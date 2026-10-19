#!/usr/bin/env python3
"""CDP gateway v2: WeChat remote-debug bridge + synthetic Network events.

Standard Chrome DevTools discovery + WebSocket proxy, PLUS: the agent-
captured wx.request traffic (from service_session.py) is injected into
the DevTools frontend as native Network.* protocol events — the DevTools
Network panel renders wx.request with full request/response bodies.

  Chrome -> chrome://inspect/#devices -> Configure -> localhost:9333
  -> WeChat pages appear -> click "inspect" = full Chrome DevTools
     (Sources/Console from the bridge + Network from the agent feed)
"""
import asyncio
import base64
import json
import os
import re
import sys
import time

import websockets

GW_PORT = 9333
LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
AGENT_EVENTS = "/tmp/wechat_rea/service_events.jsonl"

TARGET_CACHE = []


def find_bridge_port():
    try:
        files = sorted((f for f in os.listdir(LOGDIR) if f.endswith(".log")),
                       key=lambda f: os.path.getmtime(os.path.join(LOGDIR, f)))
    except FileNotFoundError:
        return None
    ports = []
    for name in files[-6:]:
        try:
            with open(os.path.join(LOGDIR, name), encoding="utf-8",
                      errors="replace") as f:
                ports += [int(m) for m in
                          re.findall(r"ws=127\.0\.0\.1:(\d+)", f.read())]
        except OSError:
            continue
    return ports[-1] if ports else None


async def bridge_cmd(ws, method, params=None):
    await ws.send(json.dumps({"id": 10_000 + bridge_cmd.n, "method": method,
                              **({"params": params} if params else {})}))
    bridge_cmd.n += 1
    want = 10_000 + bridge_cmd.n - 1
    while True:
        d = json.loads(await ws.recv())
        if d.get("id") == want:
            return d.get("result", {})


bridge_cmd.n = 0


async def refresh_targets(bridge_port):
    global TARGET_CACHE
    try:
        async with websockets.connect(f"ws://127.0.0.1:{bridge_port}",
                                      open_timeout=2, max_size=2**22) as ws:
            res = await bridge_cmd(ws, "Target.getTargets")
            TARGET_CACHE = res.get("targetInfos", [])
    except Exception:
        pass


def discovery_payload():
    out = []
    for t in TARGET_CACHE:
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        if not url.startswith("http"):
            continue
        m = re.search(r"(wx[0-9a-f]{16})", url)
        title = ((m.group(1) + " ") if m else "") + (t.get("title") or url[:44])
        out.append({
            "description": "wechat",
            "title": title.strip(),
            "type": "page",
            "url": url,
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{GW_PORT}/devtools/page/{t['targetId']}",
        })
    return out


def appid_of_target(target_id):
    for t in TARGET_CACHE:
        if t.get("targetId") == target_id:
            m = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
            return m.group(1) if m else None
    return None


def parse_headers(h):
    if isinstance(h, str):
        try:
            return json.loads(h)
        except ValueError:
            return {}
    return h if isinstance(h, dict) else {}


def guess_mime(body):
    s = (body or "").lstrip()
    if s.startswith("{") or s.startswith("["):
        return "application/json"
    if s.startswith("<"):
        return "text/html"
    return "text/plain"


class NetInjector:
    """Feeds agent-captured wx.request traffic as Network.* events."""

    def __init__(self, front_ws, appid):
        self.ws = front_ws
        self.appid = appid
        self.bodies = {}          # requestId -> {req, resp, mime}
        self.offset = self._eof()
        self.active = False

    @staticmethod
    def _eof():
        try:
            return os.path.getsize(AGENT_EVENTS)
        except OSError:
            return 0

    async def send_event(self, method, params):
        try:
            await self.ws.send(json.dumps(
                {"method": method, "params": params}))
        except Exception:
            self.active = False

    async def _new_events(self):
        """Read agent events appended since our last read."""
        out = []
        try:
            with open(AGENT_EVENTS, encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
        except OSError:
            return out
        for line in chunk.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    async def feed(self):
        if not self.active:
            return
        for e in await self._new_events():
            if e.get("appid") != self.appid:
                continue
            t = e.get("t", time.time())
            d = e.get("data") or {}
            rid = str(d.get("id") or f"{t}-{e.get('type')}")
            if e.get("type") == "request":
                body = d.get("data")
                self.bodies[rid] = {"req": body, "resp": None,
                                    "mime": guess_mime(body)}
                await self.send_event("Network.requestWillBeSent", {
                    "requestId": rid,
                    "loaderId": "rea",
                    "documentURL": "https://servicewechat.com/",
                    "request": {
                        "url": d.get("url", ""),
                        "method": d.get("method", "GET"),
                        "headers": parse_headers(d.get("header")),
                        "postData": body if body else None,
                        "initialPriority": "High",
                    },
                    "timestamp": t,
                    "wallTime": t,
                    "type": "XHR",
                    "initiator": {"type": "script",
                                  "stack": {"callFrames": [{
                                      "functionName": "wx.request",
                                      "url": "app-service.js"}]}},
                })
            elif e.get("type") == "response":
                b = self.bodies.setdefault(rid, {"req": None, "resp": None,
                                                 "mime": "text/plain"})
                b["resp"] = d.get("data")
                b["mime"] = guess_mime(d.get("data"))
                await self.send_event("Network.responseReceived", {
                    "requestId": rid,
                    "loaderId": "rea",
                    "timestamp": t,
                    "type": "XHR",
                    "response": {
                        "url": d.get("url", ""),
                        "status": d.get("status", 200),
                        "statusText": "",
                        "headers": parse_headers(d.get("header")),
                        "mimeType": b["mime"],
                        "connectionReused": False,
                        "connectionId": 0,
                        "fromDiskCache": False,
                        "fromServiceWorker": False,
                        "encodedDataLength":
                            len(str(d.get("data") or "")),
                    },
                })
                await self.send_event("Network.loadingFinished", {
                    "requestId": rid,
                    "timestamp": t,
                    "encodedDataLength": len(str(d.get("data") or "")),
                })
            elif e.get("type") == "fail":
                await self.send_event("Network.loadingFailed", {
                    "requestId": rid,
                    "timestamp": t,
                    "type": "XHR",
                    "errorText": str(d.get("err", "failed")),
                    "canceled": False,
                })

    async def get_response_body(self, rid):
        b = self.bodies.get(str(rid)) or {}
        body = b.get("resp")
        if body is None:
            return None
        return {"body": body[:100_000], "base64Encoded": False}

    async def loop(self):
        while self.active:
            try:
                await self.feed()
            except Exception:
                pass
            await asyncio.sleep(1.0)


# commands the gateway answers itself (never forwarded to the bridge)
LOCAL_METHODS = {"Network.getResponseBody", "Network.getRequestPostData"}


async def proxy_session(front_ws, target_id, bridge_port):
    back = await websockets.connect(f"ws://127.0.0.1:{bridge_port}",
                                    open_timeout=3, max_size=2**22)
    injector = None
    feeder = None
    try:
        res = await bridge_cmd(back, "Target.attachToTarget",
                               {"targetId": target_id, "flatten": True})
        session = res.get("sessionId")
        if not session:
            await front_ws.close()
            return
        await front_ws.send(json.dumps(
            {"method": "Target.attachedToTarget",
             "params": {"sessionId": session, "targetInfo":
                        {"targetId": target_id, "type": "page",
                         "title": "", "url": "", "attached": True},
                        "waitingForDebugger": False}}))

        async def front_to_back():
            nonlocal injector, feeder
            async for msg in front_ws:
                d = json.loads(msg)
                method = d.get("method", "")
                # synthetic network: intercept body requests
                if method == "Network.getResponseBody" and injector:
                    r = await injector.get_response_body(
                        (d.get("params") or {}).get("requestId"))
                    await front_ws.send(json.dumps({
                        "id": d.get("id"),
                        "result": r or
                        {"body": "", "base64Encoded": False}}))
                    continue
                if method == "Network.getRequestPostData" and injector:
                    rid = str((d.get("params") or {}).get("requestId"))
                    body = (injector.bodies.get(rid) or {}).get("req")
                    await front_ws.send(json.dumps({
                        "id": d.get("id"),
                        "result": {"postData": body or ""}}))
                    continue
                if method == "Network.enable":
                    appid = appid_of_target(target_id)
                    if appid and not injector:
                        injector = NetInjector(front_ws, appid)
                        feeder = asyncio.create_task(injector.loop())
                        print(f"[gw] network injection ON for {appid}",
                              flush=True)
                d["sessionId"] = session
                await back.send(json.dumps(d))

        async def back_to_front():
            async for msg in back:
                d = json.loads(msg)
                if d.get("sessionId") in (session, None):
                    d.pop("sessionId", None)
                    await front_ws.send(json.dumps(d))

        await asyncio.gather(front_to_back(), back_to_front())
    finally:
        if feeder:
            feeder.cancel()
        await back.close()


async def handler(ws):
    m = re.match(r"/devtools/page/([0-9A-F]+)", ws.path)
    if not m:
        await ws.close()
        return
    port = find_bridge_port()
    if not port:
        await ws.close()
        return
    await proxy_session(ws, m.group(1), port)


async def process_request(path, headers):
    port = find_bridge_port()
    if path in ("/json", "/json/list"):
        if port:
            await refresh_targets(port)
        body = json.dumps(discovery_payload()).encode()
        return (200, [("Content-Type", "application/json"),
                      ("Content-Length", str(len(body)))], body)
    if path == "/json/version":
        body = json.dumps({"Browser": "WeChat/4.1.13 CDP gateway",
                           "Protocol-Version": "1.3"}).encode()
        return (200, [("Content-Type", "application/json"),
                      ("Content-Length", str(len(body)))], body)
    return None


async def main():
    port = find_bridge_port()
    if not port:
        print("[-] no bridge - start WeChat DevTools remote-debug first")
        sys.exit(1)
    print(f"[+] bridge ws://127.0.0.1:{port}")
    print(f"[+] gateway :{GW_PORT}  (http + ws + network injection)")
    print(f"[+] Chrome -> chrome://inspect/#devices -> Configure -> localhost:{GW_PORT}")
    async with websockets.serve(handler, "127.0.0.1", GW_PORT,
                                process_request=process_request,
                                max_size=2**22):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
