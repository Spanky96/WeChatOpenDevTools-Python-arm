#!/usr/bin/env python3
"""One-click WeChat mini-program DevTools launcher.

Auto-manages: sniper injection + CDP gateway + web dashboard.
Open http://127.0.0.1:8777/ after starting.

Usage:
  python3 start.py    # start everything
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DASHBOARD_PORT = 8777
GATEWAY_PORT = 9333

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")

# --- process management ---
_children = {}


def spawn(name, script, *args):
    """Start a child process if not already running."""
    if name in _children and _children[name].poll() is None:
        return False
    cmd = [sys.executable, "-u", os.path.join(ROOT, script)] + list(args)
    _children[name] = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=ROOT, start_new_session=True)
    return True


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


async def get_targets():
    port = find_bridge_port()
    if not port:
        return {"bridge": None, "targets": []}
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}",
                                       open_timeout=2, max_size=2**22) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
            while True:
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                if d.get("id") == 1:
                    infos = d.get("result", {}).get("targetInfos", [])
                    pages = []
                    for t in infos:
                        if t.get("type") != "page":
                            continue
                        url = t.get("url", "")
                        if "servicewechat.com" in url and "wx" in url:
                            m = re.search(r"(wx[0-9a-f]{16})", url)
                            pages.append({
                                "appid": m.group(1) if m else "?",
                                "url": url[:80],
                                "targetId": t["targetId"],
                            })
                        elif url.startswith("http") and "servicewechat" not in url:
                            pages.append({
                                "appid": "H5",
                                "url": url[:80],
                                "targetId": t["targetId"],
                            })
                    return {"bridge": port, "targets": pages}
    except Exception:
        return {"bridge": port, "targets": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            result = asyncio.run(get_targets())
            sniper_alive = _children.get("sniper") and _children["sniper"].poll() is None
            gateway_alive = _children.get("gateway") and _children["gateway"].poll() is None
            self._json({
                **result,
                "sniper": bool(sniper_alive),
                "gateway": bool(gateway_alive),
                "gateway_port": GATEWAY_PORT,
            })
        elif self.path == "/api/open_devtools":
            # target_id passed as query param
            tid = self.path.split("target_id=")[1].split("&")[0] if "target_id=" in self.path else ""
            if tid:
                url = (f"devtools://devtools/bundled/devtools_app.html"
                       f"?ws=127.0.0.1:{GATEWAY_PORT}/devtools/page/{tid}")
                subprocess.Popen(["open", "-a", "Google Chrome", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True, "url": url[:80]})
            else:
                self._json({"ok": False, "error": "no target_id"})
        elif self.path == "/api/start_sniper":
            ok = spawn("sniper", "sniper.py")
            self._json({"ok": True, "started": ok})
        elif self.path == "/api/start_gateway":
            ok = spawn("gateway", "cdp_gateway.py")
            self._json({"ok": True, "started": ok})
        elif self.path == "/":
            with open(os.path.join(WEB_DIR, "dashboard.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def auto_manage():
    """Background thread: keep sniper and gateway running."""
    while True:
        spawn("sniper", "sniper.py")
        spawn("gateway", "cdp_gateway.py")
        time.sleep(5)


def main():
    os.makedirs(WEB_DIR, exist_ok=True)
    threading.Thread(target=auto_manage, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), Handler)
    print(f"\n  🚀 Dashboard: http://127.0.0.1:{DASHBOARD_PORT}/\n")
    print(f"  Sniper + Gateway auto-managed\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
