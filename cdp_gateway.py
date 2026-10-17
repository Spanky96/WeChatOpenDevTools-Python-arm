#!/usr/bin/env python3
"""CDP gateway: expose the WeChat remote-debug bridge as a standard Chrome
DevTools discovery + WebSocket endpoint.

  Chrome -> chrome://inspect/#devices -> Configure -> localhost:9333
  -> WeChat pages appear -> click "inspect" = full Chrome DevTools.

Requires a WeChat DevTools remote-debug session running (the bridge).
"""
import asyncio
import json
import os
import re
import sys

import websockets

GW_PORT = 9333
LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")

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
                ports += [int(m) for m in re.findall(r"ws=127\.0\.0\.1:(\d+)", f.read())]
        except OSError:
            continue
    return ports[-1] if ports else None


async def bridge_cmd(ws, method, params=None):
    """Send a command on the bridge connection, return its result."""
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


async def proxy_session(front_ws, target_id, bridge_port):
    """Bridge a Chrome DevTools frontend to one target on the WeChat bridge."""
    back = await websockets.connect(f"ws://127.0.0.1:{bridge_port}",
                                    open_timeout=3, max_size=2**22)
    try:
        res = await bridge_cmd(back, "Target.attachToTarget",
                               {"targetId": target_id, "flatten": True})
        session = res.get("sessionId")
        if not session:
            await front_ws.close()
            return
        # tell the frontend it is attached (flat protocol session id)
        await front_ws.send(json.dumps(
            {"method": "Target.attachedToTarget",
             "params": {"sessionId": session, "targetInfo":
                        {"targetId": target_id, "type": "page",
                         "title": "", "url": "", "attached": True},
                        "waitingForDebugger": False}}))

        async def front_to_back():
            async for msg in front_ws:
                d = json.loads(msg)
                d["sessionId"] = session
                await back.send(json.dumps(d))

        async def back_to_front():
            async for msg in back:
                d = json.loads(msg)
                # only forward messages of our session (or session-less)
                if d.get("sessionId") in (session, None):
                    d.pop("sessionId", None)
                    await front_ws.send(json.dumps(d))

        await asyncio.gather(front_to_back(), back_to_front())
    finally:
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
    """Serve the HTTP discovery endpoints on the same port."""
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
    print(f"[+] gateway :{GW_PORT}  (http + ws)")
    print(f"[+] Chrome -> chrome://inspect/#devices -> Configure -> localhost:{GW_PORT}")
    async with websockets.serve(handler, "127.0.0.1", GW_PORT,
                                process_request=process_request,
                                max_size=2**22):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
