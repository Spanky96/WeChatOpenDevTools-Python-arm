#!/usr/bin/env python3
"""Local WMPF protobuf/CDP transport, including native flattened Target sessions."""
import argparse
import asyncio
from collections import Counter
import json
import urllib.request
import zlib

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError
from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import serve

MAX_MESSAGE = 32 * 1024 * 1024


def _messages():
    file = descriptor_pb2.FileDescriptorProto(name="wmpf_direct.proto", syntax="proto2")
    definitions = {
        "DebugMessage": [("seq", 13), ("after", 13), ("category", 9),
                         ("data", 12), ("compressAlgo", 13), ("originalSize", 13)],
        "ChromeDevtools": [("opId", 4), ("payload", 9), ("jscontextId", 9)],
        "ChromeDevtoolsResult": [("opId", 4), ("payload", 9), ("jscontextId", 9)],
    }
    for name, fields in definitions.items():
        message = file.message_type.add(name=name)
        for number, (field, kind) in enumerate(fields, 1):
            message.field.add(name=field, number=number, type=kind, label=1)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return [message_factory.GetMessageClass(pool.FindMessageTypeByName(name))
            for name in definitions]


DebugMessage, ChromeDevtools, ChromeDevtoolsResult = _messages()


def encode_cdp(payload, seq=1, category="chromeDevtools", compressed=False):
    inner = ChromeDevtools(opId=seq, payload=json.dumps(payload), jscontextId="")
    data = inner.SerializeToString()
    return DebugMessage(seq=seq, category=category, originalSize=len(data),
                        compressAlgo=int(compressed),
                        data=zlib.compress(data) if compressed else data).SerializeToString()


def decode_frame(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE:
        raise ValueError("invalid frame size or type")
    frame = DebugMessage.FromString(raw)
    data = frame.data
    if frame.originalSize > MAX_MESSAGE or frame.compressAlgo not in (0, 1):
        raise ValueError("unsupported compression or oversized payload")
    if frame.compressAlgo:
        inflater = zlib.decompressobj()
        data = inflater.decompress(data, MAX_MESSAGE + 1)
        if len(data) > MAX_MESSAGE or not inflater.eof or inflater.unused_data:
            raise ValueError("invalid or oversized compressed payload")
    if frame.originalSize and len(data) != frame.originalSize:
        raise ValueError("payload size mismatch")
    payload = None
    if frame.category in ("chromeDevtools", "chromeDevtoolsResult"):
        payload = json.loads(ChromeDevtoolsResult.FromString(data).payload)
        if not isinstance(payload, dict):
            raise ValueError("CDP payload must be an object")
        if "method" in payload and not isinstance(payload["method"], str):
            raise ValueError("invalid CDP method")
        if "params" in payload and not isinstance(payload["params"], dict):
            raise ValueError("invalid CDP event params")
    return frame.category, payload


class DirectBridge:
    def __init__(self):
        self.runtime = None
        self.runtimes = {}
        self.clients, self.pending = {}, {}
        self.categories = Counter()
        self.rejected = Counter()
        self.orphan_sessions = set()
        self.sequence = 0
        self.send_lock = asyncio.Lock()
        self.servers = []
        self.cdp_port = 9422

    async def deliver(self, client, payload):
        state = self.clients.get(client)
        if state is not None:
            try:
                raw = json.dumps(payload)
                size = len(raw.encode("utf-8"))
                if state["queued_bytes"] + size > 2 * MAX_MESSAGE:
                    raise asyncio.QueueFull
                state["queue"].put_nowait((raw, size))
                state["queued_bytes"] += size
            except asyncio.QueueFull:
                await client.close(1013, "CDP consumer is too slow")

    async def send_command(self, client, command):
        async with self.send_lock:
            if self.runtime is None or len(self.pending) >= 1024:
                return False
            if client is not None:
                state = self.clients.get(client)
                if state is None or state["runtime"] not in (None, self.runtime):
                    return False
                state["runtime"] = self.runtime
            self.sequence += 1
            self.pending[self.sequence] = (client, command)
            try:
                await self.runtime.send(encode_cdp({**command, "id": self.sequence}, self.sequence))
            except ConnectionClosed:
                self.pending.pop(self.sequence, None)
                return False
            return True

    async def runtime_handler(self, websocket):
        self.runtimes[websocket] = True
        if self.runtime is None:
            self.runtime = websocket
            await self.cleanup_orphans()
        print(f"[direct] runtime connected; connections={len(self.runtimes)}", flush=True)
        try:
            async for raw in websocket:
                try:
                    category, payload = decode_frame(raw)
                    self.categories[category] += 1
                    if self.categories[category] == 1:
                        print(f"[direct] category={category[:80]!r} bytes={len(raw)}", flush=True)
                    # The native filter emits {} for hidden internal events.
                    if websocket is not self.runtime or not payload:
                        continue
                    if "id" in payload:
                        owner = self.pending.pop(payload["id"], None)
                        if owner:
                            client, command = owner
                            state = self.clients.get(client)
                            if state is not None and command["method"] == "Target.attachToTarget":
                                session_id = (payload.get("result") or {}).get("sessionId")
                                if session_id:
                                    state["sessions"].add(session_id)
                            elif state is not None and command["method"] == "Target.detachFromTarget" and "result" in payload:
                                state["sessions"].discard((command.get("params") or {}).get("sessionId"))
                            if client is not None:
                                await self.deliver(client, {**payload, "id": command["id"]})
                            elif command["method"] == "Target.attachToTarget":
                                # A client can close before its attach response arrives.
                                session_id = (payload.get("result") or {}).get("sessionId")
                                if session_id:
                                    await self.detach_session(session_id)
                            elif client is None and command["method"] == "Target.detachFromTarget":
                                self.orphan_sessions.discard((command.get("params") or {}).get("sessionId"))
                    elif payload.get("method"):
                        session_id = payload.get("sessionId")
                        for client, state in list(self.clients.items()):
                            if session_id is None or session_id in state["sessions"]:
                                await self.deliver(client, payload)
                            if payload["method"] == "Target.detachedFromTarget":
                                state["sessions"].discard((payload.get("params") or {}).get("sessionId"))
                except (DecodeError, ValueError, TypeError, zlib.error) as error:
                    kind = type(error).__name__
                    self.rejected[kind] += 1
                    if self.rejected[kind] == 1 or self.rejected[kind] % 100 == 0:
                        print(f"[direct] rejected frame: {kind} count={self.rejected[kind]} bytes={len(raw)}", flush=True)
        except ConnectionClosed as error:
            print(f"[direct] native websocket closed code={error.code}", flush=True)
        finally:
            self.runtimes.pop(websocket, None)
            if self.runtime is websocket:
                for state in self.clients.values():
                    if state["runtime"] is websocket:
                        self.orphan_sessions.update(state["sessions"])
                self.runtime = next(reversed(self.runtimes), None)
                pending, self.pending = self.pending, {}
                for client, command in pending.values():
                    await self.deliver(client, {"id": command["id"],
                        **({"sessionId": command["sessionId"]} if command.get("sessionId") else {}),
                        "error": {"code": -32000, "message": "WMPF runtime disconnected"}})
                await self.cleanup_orphans()
                # A new native connection owns different Target session ids.
                await asyncio.gather(*(client.close(1012, "WMPF runtime disconnected")
                    for client, state in list(self.clients.items()) if state["runtime"] is websocket),
                    return_exceptions=True)
            print(f"[direct] runtime disconnected; connections={len(self.runtimes)}", flush=True)

    async def detach_session(self, session_id):
        self.orphan_sessions.add(session_id)
        await self.send_command(None, {"id": 0, "method": "Debugger.disable", "sessionId": session_id})
        await self.send_command(None, {"id": 0, "method": "Target.detachFromTarget",
                                       "params": {"sessionId": session_id}})

    async def cleanup_orphans(self):
        if self.runtime is not None:
            for session_id in list(self.orphan_sessions):
                await self.detach_session(session_id)

    async def cdp_handler(self, websocket):
        if websocket.path not in ("/", "/devtools/page/current"):
            await websocket.close(1008, "unknown target; use gateway :9333 for page targets")
            return
        queue = asyncio.Queue(maxsize=256)
        state = self.clients[websocket] = {"queue": queue, "queued_bytes": 0,
                                           "sessions": set(), "runtime": self.runtime}

        async def writer():
            while True:
                raw, size = await queue.get()
                state["queued_bytes"] -= size
                await websocket.send(raw)

        writer_task = asyncio.create_task(writer())
        try:
            async for raw in websocket:
                try:
                    command = json.loads(raw)
                    if (not isinstance(command, dict) or type(command.get("id")) is not int or
                            not isinstance(command.get("method"), str) or
                            not isinstance(command.get("params", {}), dict) or
                            not isinstance(command.get("sessionId", ""), str)):
                        raise ValueError("invalid CDP command")
                    session_id = command.get("sessionId")
                    detached = (command.get("params") or {}).get("sessionId") if command["method"] == "Target.detachFromTarget" else None
                    if ((session_id and session_id not in state["sessions"]) or
                            (detached and detached not in state["sessions"])):
                        await self.deliver(websocket, {"id": command["id"],
                            **({"sessionId": session_id} if session_id else {}),
                            "error": {"code": -32001, "message": "session not attached by this client"}})
                        continue
                    if detached:
                        await self.send_command(None, {"id": 0, "method": "Debugger.disable", "sessionId": detached})
                    if not await self.send_command(websocket, command):
                        await self.deliver(websocket, {"id": command["id"],
                            **({"sessionId": session_id} if session_id else {}),
                            "error": {"code": -32000, "message": "runtime unavailable or busy"}})
                except (ValueError, TypeError):
                    await websocket.close(1007, "invalid CDP JSON command")
        except ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)
            for wire_id, (client, command) in list(self.pending.items()):
                if client is websocket:
                    if command["method"] == "Target.attachToTarget":
                        self.pending[wire_id] = (None, command)
                    else:
                        self.pending.pop(wire_id, None)
            if state["runtime"] is self.runtime:
                for session_id in list(state["sessions"]):
                    await self.detach_session(session_id)
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)

    def status(self):
        return {"experimental": True, "runtime_connected": self.runtime is not None,
                "runtime_connections": len(self.runtimes),
                "clients": len(self.clients), "pending": len(self.pending),
                "orphan_sessions": len(self.orphan_sessions), "rejected_frames": dict(self.rejected),
                "categories": dict(self.categories)}

    async def http_request(self, path, headers):
        endpoint = f"127.0.0.1:{self.cdp_port}/devtools/page/current"
        target = {"id": "current", "type": "page", "title": "WMPF browser (experimental)",
                  "url": "", "webSocketDebuggerUrl": f"ws://{endpoint}",
                  "devtoolsFrontendUrl": f"devtools://devtools/bundled/inspector.html?ws={endpoint}"}
        if path in ("/json", "/json/list"):
            body = [target] if self.runtime is not None else []
        elif path == "/json/version":
            body = {"Browser": "WMPF experimental transport", "Protocol-Version": "1.3"}
        elif path == "/status":
            body = self.status()
        elif headers.get("Upgrade", "").lower() == "websocket":
            return None
        else:
            return 404, [], b"Not found"
        data = json.dumps(body).encode()
        return 200, [("Content-Type", "application/json"),
                     ("Content-Length", str(len(data)))], data

    async def start(self, debug_port=9421, cdp_port=9422):
        try:
            runtime_hosts = ["127.0.0.1", "::1"] if debug_port else "127.0.0.1"
            self.servers.append(await serve(self.runtime_handler, runtime_hosts, debug_port,
                                             max_size=MAX_MESSAGE, close_timeout=1, ping_interval=None))
            self.servers.append(await serve(self.cdp_handler, "127.0.0.1", cdp_port,
                                             process_request=self.http_request,
                                             max_size=MAX_MESSAGE, close_timeout=1))
            self.debug_port = self.servers[0].sockets[0].getsockname()[1]
            self.cdp_port = self.servers[1].sockets[0].getsockname()[1]
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        for server in self.servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self.servers))
        self.servers.clear()


async def main(args):
    bridge = DirectBridge()
    await bridge.start(args.debug_port, args.cdp_port)
    print(f"[direct] runtime ws://127.0.0.1:{bridge.debug_port}; "
          f"browser CDP ws://127.0.0.1:{bridge.cdp_port}/", flush=True)
    try:
        await asyncio.Future()
    finally:
        await bridge.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-port", type=int, default=9421)
    parser.add_argument("--cdp-port", type=int, default=9422)
    parser.add_argument("--status", action="store_true", help="query an existing transport")
    options = parser.parse_args()
    if options.status:
        with urllib.request.urlopen(f"http://127.0.0.1:{options.cdp_port}/status", timeout=2) as response:
            print(response.read().decode())
    else:
        try:
            asyncio.run(main(options))
        except KeyboardInterrupt:
            pass
