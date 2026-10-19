#!/usr/bin/env python3
"""Network-aware mini-program debugger.

Connects to the WeChat remote-debug bridge, attaches to a target,
injects a wx.request interceptor, and forwards captured requests as
standard CDP Network events to a DevTools frontend.

Usage:
  python3 net_debugger.py              # list targets, pick one
  python3 net_debugger.py wx17102e...  # by appid prefix
"""
import asyncio
import json
import os
import re
import sys
import time

import websockets

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
INJECT_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "scripts", "net_inject.js")


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


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events = []

    async def cmd(self, method, params=None, session=None, timeout=8):
        self._id += 1
        rid = self._id
        msg = {"id": rid, "method": method}
        if params:
            msg["params"] = params
        if session:
            msg["sessionId"] = session
        await self.ws.send(json.dumps(msg))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            d = json.loads(raw)
            if d.get("id") == rid:
                return d.get("result", d)
            # store events for later processing
            self.events.append(d)

    async def drain_events(self, duration=2):
        """Collect events for a short window."""
        end = time.time() + duration
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=0.5)
                self.events.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass

    def net_events(self):
        """Extract __NET__ console messages from events."""
        out = []
        for ev in self.events:
            if ev.get("method") == "Runtime.consoleAPICalled":
                args = ev.get("params", {}).get("args", [])
                for arg in args:
                    val = arg.get("value", "")
                    if isinstance(val, str) and val.startswith("__NET__"):
                        try:
                            out.append(json.loads(val[7:]))
                        except json.JSONDecodeError:
                            pass
        self.events = [e for e in self.events if e not in out]
        return out


async def main():
    port = find_bridge_port()
    if not port:
        print("[-] no bridge found - start a WeChat DevTools remote-debug session first")
        return 1
    print(f"[+] bridge ws://127.0.0.1:{port}")

    async with websockets.connect(f"ws://127.0.0.1:{port}",
                                   open_timeout=3, max_size=2**22) as ws:
        cdp = CDP(ws)
        infos = (await cdp.cmd("Target.getTargets")).get("targetInfos", [])
        pages = [t for t in infos if t["type"] == "page"
                 and "servicewechat.com" in t.get("url", "")
                 and "wx" in t.get("url", "")]
        if not pages:
            print("[-] no mini-program pages found")
            return 1
        for i, t in enumerate(pages):
            m = re.search(r"(wx[0-9a-f]{6,})", t["url"])
            print(f"  [{i}] {m.group(1) if m else '?':22} {t['url'][:65]}")

        want = sys.argv[1] if len(sys.argv) > 1 else None
        target = None
        if want:
            for t in pages:
                if want in t["url"]:
                    target = t
                    break
        if not target:
            idx = int(input("target index: ").strip()) if len(pages) > 1 else 0
            target = pages[idx]

        print(f"[+] attaching {target['url'][:60]}")
        sess = (await cdp.cmd("Target.attachToTarget",
                              {"targetId": target["targetId"], "flatten": True})).get("sessionId")
        if not sess:
            print("[-] attach failed")
            return 1

        # enable Runtime to capture console
        await cdp.cmd("Runtime.enable", session=sess)

        # inject the interceptor
        with open(INJECT_JS, "r") as f:
            inject_code = f.read()
        r = await cdp.cmd("Runtime.evaluate",
                          {"expression": inject_code, "returnByValue": True},
                          session=sess)
        print(f"[+] injected: {json.dumps(r.get('result', {}))[:200]}")

        # listen for network events
        print("\n[network] listening for requests... (interact with the mini-program)")
        request_map = {}
        while True:
            try:
                await cdp.drain_events(1)
                for ev in cdp.net_events():
                    if ev["type"] == "ready":
                        print(f"[net] interceptor ready: {ev['data']}")
                    elif ev["type"] == "request":
                        d = ev["data"]
                        request_map[d["id"]] = d
                        print(f"[req] {d['method']:6} {d['url'][:80]}")
                        if d.get("data") and d["data"] != "null":
                            print(f"       body: {str(d['data'])[:120]}")
                    elif ev["type"] == "response":
                        d = ev["data"]
                        print(f"[rsp] {d['status']:3} {d['url'][:80]}")
                        if d.get("body"):
                            print(f"       body: {str(d['body'])[:120]}")
                    elif ev["type"] == "no_wx":
                        print("[!] wx object not found in this context")
            except KeyboardInterrupt:
                break
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
