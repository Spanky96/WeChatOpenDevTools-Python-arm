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
from collections import OrderedDict
import json
import os
import re
import sys
import time

import websockets
try:
    from .event_store import read_events
    from .cdp_storage import WxStorageAdapter
except ImportError:
    from event_store import read_events
    from cdp_storage import WxStorageAdapter

GW_PORT = 9333
LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
AGENT_EVENTS = "/tmp/wechat_rea/service_events.jsonl"
GW_LOG = "/tmp/wechat_rea/gateway.log"

TARGET_CACHE = []


def _log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(GW_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def find_bridge_port():
    if os.environ.get("WECHAT_DEBUG_MODE") == "direct":
        return 9422
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
    want = 10_000 + bridge_cmd.n
    bridge_cmd.n += 1
    await ws.send(json.dumps({"id": want, "method": method,
                              **({"params": params} if params else {})}))
    while True:
        d = json.loads(await ws.recv())
        if d.get("id") == want:
            return d.get("result", {})


bridge_cmd.n = 0


async def refresh_targets(bridge_port):
    global TARGET_CACHE
    targets = []
    try:
        async with websockets.connect(f"ws://127.0.0.1:{bridge_port}",
                                      open_timeout=2, max_size=2**22) as ws:
            res = await asyncio.wait_for(bridge_cmd(ws, "Target.getTargets"), 3)
            targets = res.get("targetInfos", [])
    except Exception:
        targets = []

    if targets:
        TARGET_CACHE = targets
        return

    try:
        import urllib.request
        def status_targets():
            with urllib.request.urlopen("http://127.0.0.1:8778/status",
                                        timeout=1) as r:
                status = json.loads(r.read())
            if status.get("bridge_state") in ("dead", "stopped", "disconnected"):
                return []
            try:
                if int(status.get("bridge_port")) != int(bridge_port):
                    return []
            except (TypeError, ValueError):
                return []
            return status.get("targetInfos", status.get("targets", []))
        targets = await asyncio.to_thread(status_targets)
    except Exception:
        targets = []

    TARGET_CACHE = [{"type": "page", **target} for target in targets]


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
                f"ws://127.0.0.1:{GW_PORT}/devtools/page/{t['targetId']}" +
                (f"?appid={m.group(1)}" if m else ""),
        })
    return out


def appid_of_target(target_id):
    for t in TARGET_CACHE:
        if t.get("targetId") == target_id:
            m = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
            return m.group(1) if m else "web:" + str(target_id)
    return None


def appid_from_session_status(target_id):
    """Resolve target ids without opening another bridge WebSocket."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8778/status",
                                    timeout=1.5) as r:
            st = json.loads(r.read())
        for t in st.get("targets", []):
            if t.get("targetId") == target_id:
                return t.get("appid")
    except Exception:
        pass
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
        self.bodies = OrderedDict()
        self.body_bytes = 0
        self.offsets = {"Runtime": None, "Network": None}
        self.initial_offsets = dict(self.offsets)
        self.enabled = {"Runtime": False, "Network": False}
        self.seen = OrderedDict()
        self.active = True
        self.context_id = 2_147_483_647
        self.context_sent = False
        self.ready = True
        self.script_ids = {}

    def observe(self, message):
        if message.get("method") == "Debugger.globalObjectCleared":
            self.script_ids.clear()
        if message.get("method") == "Debugger.scriptParsed":
            params = message.get("params") or {}
            if params.get("url") and params.get("scriptId") and len(self.script_ids) < 4096:
                # The same URL can belong to several isolated V8 contexts.
                previous = self.script_ids.get(params["url"], params["scriptId"])
                self.script_ids[params["url"]] = params["scriptId"] if previous == params["scriptId"] else ""

    def initiator(self, data):
        frames = []
        stack = data.get("stack")
        for frame in (stack[:24] if isinstance(stack, list) else []):
            if not isinstance(frame, dict) or not isinstance(frame.get("url"), str):
                continue
            try:
                line = max(0, int(frame.get("lineNumber", 0)))
                column = max(0, int(frame.get("columnNumber", 0)))
            except (ValueError, TypeError, OverflowError):
                continue
            url = frame["url"]
            frames.append({"functionName": str(frame.get("functionName") or ""),
                           "scriptId": self.script_ids.get(url, ""), "url": url,
                           "lineNumber": line, "columnNumber": column})
        return {"type": "script", "stack": {"callFrames": frames}} if frames else {"type": "other"}

    async def enable(self, domain):
        if domain == "Runtime" and not self.enabled[domain]:
            self.context_sent = False
        self.enabled[domain] = True

    async def ensure_context(self):
        if self.enabled["Runtime"] and not self.context_sent:
            # Captured service logs need a registered context in the frontend,
            # even when the bridge has not delivered native contexts yet.
            await self.send_event("Runtime.executionContextCreated", {
                "context": {"id": self.context_id,
                            "origin": "https://servicewechat.com/",
                            "name": "WeChat captured logs",
                            "uniqueId": "rea-" + self.appid,
                            "auxData": {"isDefault": False}}})
            self.context_sent = True

    def trim_bodies(self):
        while len(self.bodies) > 2000 or self.body_bytes > 32 * 1024 * 1024:
            _, body = self.bodies.popitem(last=False)
            self.body_bytes -= body.get("size", 0)

    async def send_event(self, method, params):
        try:
            await self.ws.send(json.dumps(
                {"method": method, "params": params}))
        except Exception:
            self.active = False

    async def _new_events(self, domain):
        """Read agent events appended since our last read."""
        page = read_events(path=AGENT_EVENTS, cursor=self.offsets[domain], limit=1000)
        self.offsets[domain] = page["cursor"]
        kinds = ("console", "error") if domain == "Runtime" else ("request", "response", "fail")
        return [event for event in page["events"] if event.get("type") in kinds]

    async def feed(self):
        if not self.active or not self.ready:
            return
        await self.ensure_context()
        records = []
        for domain, enabled in self.enabled.items():
            if enabled:
                records.extend(await self._new_events(domain))
        for e in records:
            if e.get("appid") != self.appid:
                continue
            t = (e["agent_t"] / 1000 if isinstance(e.get("agent_t"), (int, float))
                 else e.get("t", time.time()))
            d = e.get("data") or {}
            rid = "rea:" + str(d.get("id") or f"{t}-{e.get('type')}")
            if e.get("type") in ("request", "response", "fail"):
                key = (rid, e["type"])
                if key in self.seen:
                    continue
                self.seen[key] = True
                if len(self.seen) > 6000:
                    self.seen.popitem(last=False)
            if e.get("type") in ("console", "error"):
                # synthesize V8 console events (WeChat's service console
                # is custom — no native consoleAPICalled reaches DevTools)
                level = d.get("level") or ("error" if e["type"] == "error"
                                           else "log")
                cdptype = {"log": "log", "info": "info",
                           "warn": "warning", "error": "error"}.get(level,
                                                                    "log")
                args = d.get("args")
                if not isinstance(args, list):
                    args = [d.get("msg") or str(d)]
                payload = [{"type": "string", "value": str(a)}
                           for a in args[:8]]
                await self.send_event("Runtime.consoleAPICalled", {
                    "type": cdptype,
                    "args": payload,
                    "executionContextId": self.context_id,
                    "timestamp": t * 1000,
                })
            elif e.get("type") == "request":
                body = d.get("data")
                if body is not None and not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False)
                capture = (d.get("capture") or {}).get("body") or {}
                if capture.get("truncated"):
                    body = (body or "") + "\n[REA: request body truncated at " + str(capture.get("limit")) + " characters]"
                size = len((body or "").encode("utf-8"))
                self.bodies[rid] = {"req": body, "resp": None,
                                    "mime": guess_mime(body), "size": size}
                self.body_bytes += size
                self.trim_bodies()
                await self.send_event("Network.requestWillBeSent", {
                    "requestId": rid,
                    "loaderId": "rea",
                    "documentURL": "https://servicewechat.com/",
                    "request": {
                        "url": d.get("url", ""),
                        "method": d.get("method", "GET"),
                        "headers": parse_headers(d.get("header")),
                        **({"postData": body, "hasPostData": True}
                           if body else {}),
                        "initialPriority": "High",
                    },
                    "timestamp": t,
                    "wallTime": t,
                    "redirectHasExtraInfo": False,
                    "type": "XHR",
                    "initiator": self.initiator(d),
                })
            elif e.get("type") == "response":
                b = self.bodies.get(rid)
                if b is None:
                    continue  # request fell outside the replay window
                body = d.get("data")
                if body is not None and not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False)
                capture = (d.get("capture") or {}).get("body") or {}
                if capture.get("truncated"):
                    body = (body or "") + "\n[REA: response body truncated at " + str(capture.get("limit")) + " characters]"
                b["resp"] = body
                b["mime"] = guess_mime(body)
                length = len((body or "").encode("utf-8"))
                b["size"] += length
                self.body_bytes += length
                self.trim_bodies()
                await self.send_event("Network.responseReceived", {
                    "requestId": rid,
                    "loaderId": "rea",
                    "timestamp": t,
                    "type": "XHR",
                    "hasExtraInfo": False,
                    "response": {
                        "url": d.get("url", ""),
                        "status": d.get("status") or 200,
                        "statusText": "",
                        "headers": parse_headers(d.get("header")),
                        "mimeType": b["mime"],
                        "connectionReused": False,
                        "connectionId": 0,
                        "fromDiskCache": False,
                        "fromServiceWorker": False,
                        "encodedDataLength": length,
                        "securityState": "unknown",
                    },
                })
                await self.send_event("Network.loadingFinished", {
                    "requestId": rid,
                    "timestamp": t,
                    "encodedDataLength": length,
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
            except Exception as exc:
                _log(f"event feed failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.2)


async def proxy_session(front_ws, target_id, bridge_port, appid=None):
    back = await websockets.connect(f"ws://127.0.0.1:{bridge_port}",
                                    open_timeout=3, max_size=2**25)
    injector = None
    feeder = None
    tasks = []
    local_replies = set()
    page_requests = set()
    frontend_ready = asyncio.Event()
    storage = None
    try:
        res = await asyncio.wait_for(
            bridge_cmd(back, "Target.attachToTarget",
                       {"targetId": target_id, "flatten": True}), timeout=6)
        session = res.get("sessionId")
        if not session:
            await front_ws.close()
            return
        appid = appid or appid_of_target(target_id)
        if not appid:
            appid = await asyncio.to_thread(appid_from_session_status, target_id)
        if appid and not str(appid).startswith("web:"):
            storage = WxStorageAdapter(appid, target_id)
            injector = NetInjector(front_ws, appid)
            injector.ready = False
        async def front_to_back():
            nonlocal injector, feeder, appid
            async for msg in front_ws:
                d = json.loads(msg)
                method = d.get("method", "")
                if storage:
                    reply = await storage.handle(d)
                    if reply is not None:
                        await front_ws.send(json.dumps(reply))
                        if method in ("DOMStorage.enable", "DOMStorage.disable"):
                            local_replies.add(d.get("id"))
                        else:
                            continue
                # synthetic network: intercept body requests
                rid = str((d.get("params") or {}).get("requestId", ""))
                if (method == "Network.getResponseBody" and injector
                        and rid.startswith("rea:")):
                    r = await injector.get_response_body(
                        (d.get("params") or {}).get("requestId"))
                    await front_ws.send(json.dumps({
                        "id": d.get("id"),
                        "result": r or
                        {"body": "", "base64Encoded": False}}))
                    continue
                if (method == "Network.getRequestPostData" and injector
                        and rid.startswith("rea:")):
                    rid = str((d.get("params") or {}).get("requestId"))
                    body = (injector.bodies.get(rid) or {}).get("req")
                    await front_ws.send(json.dumps({
                        "id": d.get("id"),
                        "result": {"postData": body or ""}}))
                    continue
                if method in ("Network.enable", "Runtime.enable"):
                    # feed console on Runtime.enable (DevTools startup),
                    # network needs Network.enable too — starting on either
                    # covers both panels
                    appid = appid or appid_of_target(target_id)
                    if not appid:
                        appid = await asyncio.to_thread(
                            appid_from_session_status, target_id)
                    if not appid:
                        await refresh_targets(bridge_port)
                        appid = appid_of_target(target_id)
                    if not appid:
                        _log(f"network injection pending: target {target_id}")
                    if appid and not injector:
                        injector = NetInjector(front_ws, appid)
                        injector.ready = False
                    if injector and feeder is None:
                        async def feed_after_init():
                            try:
                                await asyncio.wait_for(frontend_ready.wait(), 2)
                            except asyncio.TimeoutError:
                                pass  # non-Page CDP clients need no frame tree
                            injector.ready = True
                            await injector.loop()
                        feeder = asyncio.create_task(feed_after_init())
                        _log(f"network injection ON for {appid}")
                if method in ("Network.enable", "Network.disable",
                              "Runtime.enable", "Runtime.disable"):
                    # Captured events remain usable while a native enable is
                    # slow/unsupported. Still forward it for native contexts
                    # and H5 traffic, suppressing its duplicate response.
                    await front_ws.send(json.dumps({
                        "id": d.get("id"), "result": {}}))
                    local_replies.add(d.get("id"))
                    if injector:
                        domain, action = method.split(".")
                        if action == "enable":
                            await injector.enable(domain)
                        else:
                            injector.enabled[domain] = False
                params = d.get("params") or {}
                if injector and params.get("contextId") == injector.context_id:
                    params.pop("contextId")
                d["sessionId"] = session
                if method == "Page.getResourceTree":
                    page_requests.add(d.get("id"))
                await back.send(json.dumps(d))

        async def back_to_front():
            async for msg in back:
                d = json.loads(msg)
                if d.get("id") in local_replies:
                    local_replies.discard(d["id"])
                    continue
                if d.get("sessionId") in (session, None):
                    d.pop("sessionId", None)
                    if storage:
                        storage.observe(d)
                    if injector:
                        injector.observe(d)
                    await front_ws.send(json.dumps(d))
                    if d.get("id") in page_requests:
                        page_requests.discard(d["id"])
                        if (injector and injector.ready and
                                not frontend_ready.is_set() and
                                "frameTree" in (d.get("result") or {})):
                            # A slow initial frame tree can arrive after the
                            # fallback replay and clear the frontend Console.
                            injector.offsets["Runtime"] = injector.initial_offsets["Runtime"]
                            injector.context_sent = False
                        frontend_ready.set()

        tasks = [asyncio.create_task(front_to_back()),
                 asyncio.create_task(back_to_front())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        if injector:
            injector.active = False
        if feeder:
            feeder.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *([feeder] if feeder else []),
                             return_exceptions=True)
        await back.close()


async def handler(ws):
    from urllib.parse import parse_qs, urlsplit
    m = re.match(r"/devtools/page/([A-Za-z0-9_.:-]+)", ws.path)
    if not m:
        await ws.close()
        return
    port = find_bridge_port()
    if not port:
        await ws.close()
        return
    appid = parse_qs(urlsplit(ws.path).query).get("appid", [None])[0]
    if appid and not (re.fullmatch(r"wx[0-9a-f]{16}", appid) or
                      re.fullmatch(r"web:[A-Za-z0-9_.:-]+", appid)):
        appid = None
    try:
        await proxy_session(ws, m.group(1), port, appid)
    except websockets.exceptions.ConnectionClosed as exc:
        await ws.close(1012 if exc.code == 1012 else 1011, "debug transport disconnected")


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
