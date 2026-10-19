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


_targets_cache = {"ts": 0.0, "data": None}


async def get_targets_cached():
    """Bridge lookups are expensive and the bridge is fragile — cache 4s."""
    now = time.time()
    if _targets_cache["data"] is not None and now - _targets_cache["ts"] < 4:
        return _targets_cache["data"]
    data = await get_targets()
    _targets_cache["ts"], _targets_cache["data"] = now, data
    return data


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
        path = self.path.split("?")[0]
        if path == "/api/status":
            result = asyncio.run(get_targets_cached())
            sniper_alive = _children.get("sniper") and _children["sniper"].poll() is None
            gateway_alive = _children.get("gateway") and _children["gateway"].poll() is None
            sess_alive = _children.get("sess") and _children["sess"].poll() is None
            self._json({
                **result,
                "sniper": bool(sniper_alive),
                "gateway": bool(gateway_alive),
                "sess": bool(sess_alive),
                "gateway_port": GATEWAY_PORT,
            })
        elif path == "/api/events":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            since = float((qs.get("since") or ["0"])[0])
            self._json(recent_events(since=since))
        elif path == "/api/eval":
            code = self.path.split("code=", 1)[1] if "code=" in self.path else ""
            import urllib.parse
            code = urllib.parse.unquote(code)
            if code:
                with open("/tmp/wechat_rea/service_eval", "w") as f:
                    f.write(code)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "no code"})
        elif path == "/api/storage":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            appid = (qs.get("appid") or [""])[0]
            resp_file = "/tmp/wechat_rea/service_storage_resp"
            try:
                os.remove(resp_file)
            except OSError:
                pass
            with open("/tmp/wechat_rea/service_storage_req", "w") as f:
                f.write(appid)
            deadline = time.time() + 8
            result = None
            while time.time() < deadline:
                time.sleep(0.4)
                try:
                    with open(resp_file, encoding="utf-8") as f:
                        result = json.loads(f.read())
                    break
                except (OSError, ValueError):
                    continue
            self._json(result or {"error": "timeout — service context "
                               "unavailable (check agent injection)"})
        elif path == "/api/files":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            appid = (qs.get("appid") or [""])[0]
            outdir = ensure_unpacked(appid)
            files = []
            for dirpath, _dirnames, filenames in os.walk(outdir):
                for fn in filenames:
                    if fn.endswith((".js", ".json")):
                        rel = os.path.relpath(os.path.join(dirpath, fn),
                                              outdir)
                        files.append(rel)
            files.sort()
            self._json({"appid": appid, "files": files[:400]})
        elif path == "/api/source":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            appid = (qs.get("appid") or [""])[0]
            fname = (qs.get("file") or ["app-service.js"])[0]
            content = read_source(appid, fname)
            if content is None:
                self._json({"error": f"file not found: {fname} "
                                     f"(needs unpacked package)"})
            else:
                self._json({"appid": appid, "file": fname,
                            "lines": content.splitlines()})
        elif path == "/api/open_devtools":
            # target_id passed as query param
            tid = self.path.split("target_id=")[1].split("&")[0] if "target_id=" in self.path else ""
            if tid:
                url = (f"devtools://devtools/bundled/devtools_app.html"
                       f"?ws=127.0.0.1:{GATEWAY_PORT}/devtools/page/{tid}")
                # 'open' cmd doesn't support devtools:// — use AppleScript
                apple = f'tell application "Google Chrome" to open location "{url}"'
                subprocess.Popen(["osascript", "-e", apple],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True, "url": url[:80]})
            else:
                self._json({"ok": False, "error": "no target_id"})
        elif path == "/api/start_sniper":
            ok = spawn("sniper", "sniper.py")
            self._json({"ok": True, "started": ok})
        elif path == "/api/start_gateway":
            ok = spawn("gateway", "cdp_gateway.py")
            self._json({"ok": True, "started": ok})
        elif path == "/":
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


def recent_events(limit=200, since=0.0):
    """Recent service-layer events newer than `since` (unix seconds)."""
    try:
        with open("/tmp/wechat_rea/service_events.jsonl",
                  encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-limit * 3:]
        out = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("t", 0) > since:
                out.append(rec)
        return out[-limit:]
    except FileNotFoundError:
        return []


def ensure_unpacked(appid):
    """Auto-unpack a mini-program's plaintext wxapkg for the source tab."""
    outdir = f"/tmp/wechat_rea/mp_{appid}"
    if os.path.isdir(outdir) and \
            os.path.exists(os.path.join(outdir, "app-service.js")):
        return outdir
    try:
        subprocess.run([sys.executable, "-u", os.path.join(ROOT, "unpack_mp.py"),
                        appid, outdir],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       cwd=ROOT, timeout=60)
    except Exception:
        pass
    return outdir


def read_source(appid, fname):
    """Return source file content for a mini-program (path-safe)."""
    outdir = ensure_unpacked(appid)
    safe = os.path.normpath(fname).lstrip("/")
    if ".." in safe.split(os.sep):
        return None
    path = os.path.join(outdir, safe)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(4 * 1024 * 1024)
    except OSError:
        return None


def auto_manage():
    """Background thread: keep sniper, gateway and service session running."""
    while True:
        spawn("sniper", "sniper.py")
        spawn("gateway", "cdp_gateway.py")
        spawn("sess", "service_session.py")
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
