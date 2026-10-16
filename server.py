#!/usr/bin/env python3
"""WeChat devtools dashboard server.

Serves a control panel at http://127.0.0.1:8777/ :
- live WeChatAppEx process table (main + mini-program renderers)
- hook injection status per process, one-click (re)inject
- CDP endpoint probing
- daemon start/stop
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import frida

ROOT = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.join(ROOT, "scripts", "hook_v413.js")
PORT = 8777


def ps_appex():
    out = subprocess.run(["ps", "ax", "-o", "pid,ppid,%cpu,rss,command"],
                         capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        if "MacOS/WeChatAppEx" not in line or "grep" in line:
            continue
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid, ppid, cpu, rss, cmd = parts
        if "crashpad" in cmd:
            continue
        if "--type=" not in cmd:
            kind = "main"
        elif "WeApp.app" in cmd:
            appid = ([t.split("=", 1)[1] for t in cmd.split() if t.startswith("--wmpf-appid=")] or [""])[0]
            # real mini-programs use wx-prefixed appids; preload-N is a warmup host
            kind = "miniapp" if appid.startswith("wx") else "preload"
        elif "--type=renderer" in cmd:
            kind = "prewarm"   # pre-warmed generic renderer pool
        else:
            kind = "child"
        # extract appid if present
        appid = ""
        if "wmpf-render-type" in cmd:
            for tok in cmd.split():
                if "--wmpf-appid=" in tok or tok.startswith("wx") and len(tok) > 10:
                    appid = tok.split("=")[-1]
                    break
        armed = any(s in cmd for s in
                    ("enable-chrome-inspector", "auto-launch-devtools", "enable-vconsole"))
        procs.append({
            "pid": int(pid), "ppid": int(ppid), "cpu": cpu, "rss_mb": int(rss) // 1024,
            "kind": kind, "appid": appid, "armed": armed,
            "cmd": cmd[:160],
        })
    return procs


class Injector:
    """Tracks frida sessions and injects the hook script on demand."""

    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.messages = []
        self.appids = {}  # pid -> appid observed via JsonSet("app_id")
        with open(HOOK_PATH, "r", encoding="utf-8") as f:
            self.code = f.read()

    def _on_message(self, pid):
        def handler(message, data):
            if message["type"] == "send":
                payload = str(message["payload"])
                if payload.startswith("[appid] "):
                    appid = payload[8:].strip()
                    if appid.startswith("wx"):
                        self.appids[pid] = appid
                self.messages.append(
                    {"t": time.strftime("%H:%M:%S"), "pid": pid,
                     "msg": payload[:300]})
                del self.messages[:-200]
        return handler

    def inject(self, pid):
        with self.lock:
            if pid in self.sessions:
                return {"ok": False, "error": "already injected"}
            try:
                with open(HOOK_PATH, "r", encoding="utf-8") as f:
                    code = f.read()  # always the latest hook script
                session = frida.attach(pid)
                script = session.create_script(code)
                script.on("message", self._on_message(pid))
                script.load()
                self.sessions[pid] = session
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def detach(self, pid):
        with self.lock:
            session = self.sessions.pop(pid, None)
            if session:
                try:
                    session.detach()
                except Exception:
                    pass
            return {"ok": True}

    def prune(self, alive):
        with self.lock:
            for pid in list(self.sessions):
                if pid not in alive:
                    self.sessions.pop(pid, None)

    def injected_pids(self):
        with self.lock:
            return list(self.sessions)


INJECTOR = Injector()


def probe_cdp():
    candidates = {9222, 9223, 9229}
    hits = []
    try:
        lsof = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                              capture_output=True, text=True, timeout=5).stdout
        for line in lsof.splitlines():
            parts = line.split()
            if len(parts) >= 9 and "WeChat" in " ".join(parts[:2]):
                try:
                    candidates.add(int(parts[8].rsplit(":", 1)[1]))
                except ValueError:
                    pass
    except Exception:
        pass
    import urllib.request
    for port in sorted(candidates):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1.0) as r:
                hits.append({"port": port, "body": r.read().decode()[:400]})
        except Exception:
            continue
    return hits


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            procs = ps_appex()
            INJECTOR.prune({p["pid"] for p in procs})
            appids = INJECTOR.appids
            for p in procs:
                rt = appids.get(p["pid"])
                if rt:
                    p["appid"] = rt
                    if p["kind"] in ("preload", "prewarm"):
                        p["kind"] = "miniapp"  # promoted: hosting a real applet
            self._json({
                "procs": procs,
                "injected": INJECTOR.injected_pids(),
                "messages": INJECTOR.messages[-40:],
            })
        elif self.path == "/api/probe":
            self._json({"cdp": probe_cdp()})
        elif self.path == "/":
            with open(os.path.join(ROOT, "web", "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/inject":
            pid = int(payload.get("pid", 0))
            self._json(INJECTOR.inject(pid))
        elif self.path == "/api/detach":
            pid = int(payload.get("pid", 0))
            self._json(INJECTOR.detach(pid))
        elif self.path == "/api/inject_all":
            results = []
            for p in ps_appex():
                if p["kind"] in ("main", "miniapp"):
                    results.append({"pid": p["pid"], **INJECTOR.inject(p["pid"])})
            self._json({"results": results})
        else:
            self._json({"error": "not found"}, 404)


def auto_inject_loop():
    """Background thread: inject every new AppEx main / WeApp process."""
    while True:
        try:
            for p in ps_appex():
                if p["kind"] in ("main", "miniapp", "preload", "prewarm"):
                    INJECTOR.inject(p["pid"])
        except Exception:
            pass
        time.sleep(2)


def main():
    os.makedirs(os.path.join(ROOT, "web"), exist_ok=True)
    threading.Thread(target=auto_inject_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"dashboard: http://127.0.0.1:{PORT}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
