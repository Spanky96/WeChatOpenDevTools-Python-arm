#!/usr/bin/env python3
"""Interactive CDP debugger for ANY mini-program running in Mac WeChat.

Prerequisites:
  1. sniper.py running (optional, adds dev-mode vConsole to all mini-programs)
  2. A WeChat DevTools remote-debug session active with YOUR project —
     this brings up the local CDP bridge (the ws= port in devtool logs).

Usage:
  python3 miniapp_debugger.py            # list targets, pick one, REPL
  python3 miniapp_debugger.py wx7a72...  # jump straight to an appid
"""
import asyncio
import json
import re
import sys

import websockets

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")


def find_bridge_port():
    import os
    try:
        files = sorted((f for f in os.listdir(LOGDIR) if f.endswith(".log")),
                       key=lambda f: os.path.getmtime(os.path.join(LOGDIR, f)))
    except FileNotFoundError:
        return None
    ports = []
    for name in files[-5:]:
        try:
            with open(os.path.join(LOGDIR, name), encoding="utf-8",
                      errors="replace") as f:
                ports += [int(m) for m in re.findall(r"ws=127\.0\.0\.1:(\d+)", f.read())]
        except OSError:
            continue
    return ports[-1] if ports else None


class Bridge:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def cmd(self, method, params=None, session=None, timeout=6):
        self._id += 1
        rid = self._id
        msg = {"id": rid, "method": method}
        if params:
            msg["params"] = params
        if session:
            msg["sessionId"] = session
        await self.ws.send(json.dumps(msg))
        while True:
            d = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            if d.get("id") == rid:
                return d.get("result", d)
            # ignore events meanwhile

    async def targets(self):
        res = await self.cmd("Target.getTargets")
        return res.get("targetInfos", [])

    async def attach(self, tid):
        res = await self.cmd("Target.attachToTarget",
                             {"targetId": tid, "flatten": True})
        return res.get("sessionId")

    async def evaluate(self, session, expr):
        res = await self.cmd("Runtime.evaluate", {"expression": expr,
                                                  "returnByValue": True},
                             session=session)
        return res.get("result", res)


async def pick_target(bridge, want_appid):
    infos = await bridge.targets()
    pages = [t for t in infos if t["type"] == "page" and "servicewechat.com" in t.get("url", "")]
    if not pages:
        print("[-] no mini-program pages found - open one in Mac WeChat first")
        return None, None
    for i, t in enumerate(pages):
        m = re.search(r"(wx[0-9a-f]{16})", t["url"])
        appid = m.group(1) if m else "?"
        print(f"  [{i}] {appid}  attached={'Y' if t['attached'] else 'N'}  {t['url'][:70]}")
    if want_appid:
        for t in pages:
            if want_appid in t["url"]:
                return t["targetId"], want_appid
        print(f"[-] appid {want_appid} not in list")
        return None, None
    if len(pages) == 1:
        return pages[0]["targetId"], pages[0]["url"]
    idx = int(input("select target index: ").strip())
    return pages[idx]["targetId"], pages[idx]["url"]


async def repl(bridge, session):
    print("\n[REPL] evaluate JS inside the mini-program. Empty line or 'q' to quit.")
    while True:
        try:
            line = input("js> ").strip()
        except EOFError:
            break
        if not line or line == "q":
            break
        res = await bridge.evaluate(session, line)
        r = res.get("result", {})
        if "value" in r:
            print(json.dumps(r["value"], ensure_ascii=False)[:1500])
        else:
            print(json.dumps(res, ensure_ascii=False)[:1500])


async def main():
    port = find_bridge_port()
    if not port:
        print("[-] no bridge port found - start a DevTools remote-debug session first")
        return 1
    print(f"[+] bridge ws://127.0.0.1:{port}")
    try:
        ws = await websockets.connect(f"ws://127.0.0.1:{port}",
                                      open_timeout=3, max_size=2**22)
    except Exception as e:
        print(f"[-] bridge not reachable: {e}")
        print("    (open WeChat DevTools -> your project -> 真机调试)")
        return 1
    bridge = Bridge(ws)
    want = sys.argv[1] if len(sys.argv) > 1 else None
    tid, label = await pick_target(bridge, want)
    if not tid:
        return 1
    print(f"[+] attaching {label[:60]}")
    session = await bridge.attach(tid)
    print(f"[+] attached, session {session[:12]}...")
    await repl(bridge, session)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
