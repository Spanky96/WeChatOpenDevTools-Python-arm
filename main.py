#!/usr/bin/env python3
"""WeChat mini-program and H5 DevTools launcher with one dashboard.

Detects the running WeChat build and picks the flow automatically:
  4.x (WeChatAppEx Electron)  -> sniper + direct transport (default mode,
                                 no WeChat DevTools IDE needed) + H5 + dashboard
  pre-4.x (3.8.x)             -> legacy address-table hook (the old flow)

Auto-manages the matching daemons. Open http://127.0.0.1:8777/ after starting.

Usage:
  python3 main.py                 # 先启动微信；自动检测版本，4.x 默认 direct 模式
  python3 main.py --mode bridge   # 4.x 走微信开发者工具真机调试桥
  python3 main.py -p <pid>        # 对指定 WeChatAppEx pid 一次性老版注入
"""
import argparse
import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

from services import wechat_detect
from services import h5_dashboard
from services.version_config import get_version

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DASHBOARD_PORT = 8777
GATEWAY_PORT = 9333
H5_PORT = 8780

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
SNIPER_LOCK = "/tmp/wechat_rea/sniper.lock"
SESS_LOCK = "/tmp/wechat_rea/sess.lock"
SESSION_PORT = 8778
SERVICE_SCRIPTS = ("sniper.py", "cdp_gateway.py", "service_session.py",
                   "wmpf_direct.py", "legacy_inject.py", "h5_debug.py")
MANAGER_PID_FILE = "/tmp/wechat_rea/main.pid"

# services per WeChat flow — the other flow's children are retired on switch
FLOW_CHILDREN = {"new": ("sniper", "gateway", "sess", "direct", "h5"),
                 "legacy": ("legacy",)}
FLOW_STATE = {"flow": "new", "version": None, "pid": None}

# --- process management ---
_children = {}
_manage_stop = threading.Event()
_h5_launch_lock = threading.Lock()
_h5_retry_at = 0


def spawn(name, script, *args):
    """Start a child process if not already running (logs to files)."""
    if name in _children and _children[name].poll() is None:
        return False
    cmd = [sys.executable, "-u", os.path.join(ROOT, script)] + list(args)
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    with open(f"/tmp/wechat_rea/managed_{name}.log", "a", buffering=1) as logf:
        _children[name] = subprocess.Popen(
            cmd, stdout=logf, stderr=subprocess.STDOUT,
            cwd=ROOT, start_new_session=True)
    return True


def ensure_h5_service(force=False):
    global _h5_retry_at
    with _h5_launch_lock:
        if force:
            h5_dashboard.launch_config.cache_clear()
        if h5_dashboard.snapshot(H5_PORT)['online']:
            return {'ok': True, 'started': False}
        if not force and time.monotonic() < _h5_retry_at:
            return {'ok': False, 'error': '网页服务等待重试'}
        python, reason = h5_dashboard.launch_config()
        if not python:
            return {'ok': False, 'error': reason}
        _h5_retry_at = time.monotonic() + 30
        started = spawn('h5', 'services/h5_debug.py', '--port', str(H5_PORT))
        return {'ok': True, 'started': started}


def dashboard_status():
    result = asyncio.run(get_targets_cached())
    h5 = h5_dashboard.snapshot(H5_PORT)
    _, install_reason = h5_dashboard.launch_config()
    h5['available'] = h5['online'] or not install_reason
    if not h5['online'] and install_reason:
        h5['reason'] = install_reason
    mps = dict(result.get('mps', {}))
    for target in h5['targets']:
        mps[target['appid']] = {'phase': 'online', 'agent_online': True, 'transport': 'chii'}

    def alive(name, port=None):
        child = _children.get(name)
        if child:
            return child.poll() is None and (port is None or bool(
                _lsof_pids('-nP', f'-iTCP:{port}', '-sTCP:LISTEN')))
        return False

    return {**result, 'targets': [*result.get('targets', []), *h5['targets']], 'mps': mps,
            'h5': h5, 'flow': FLOW_STATE['flow'], 'wechat_version': FLOW_STATE.get('version'),
            'sniper': alive('sniper'), 'gateway': alive('gateway', GATEWAY_PORT),
            'sess': alive('sess', SESSION_PORT), 'gateway_port': GATEWAY_PORT}


def _lsof_pids(*args):
    try:
        out = subprocess.run(["lsof", "-t"] + list(args),
                             capture_output=True, text=True, timeout=5).stdout
        return [int(p) for p in out.split() if p.isdigit()]
    except Exception:
        return []


def _process_command(pid):
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""


def _service_pids():
    """Find service processes even when lsof cannot report advisory locks."""
    try:
        out = subprocess.run(["ps", "ax", "-o", "pid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, cmd = int(parts[0]), parts[1]
        if pid != os.getpid() and _is_service_command(cmd):
            pids.append(pid)
    return pids


def _is_orphan_service(pid):
    return _is_service_command(_process_command(pid))


def _is_service_command(cmd):
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if not argv or not os.path.basename(argv[0]).lower().startswith("python"):
        return False
    paths = {os.path.join(ROOT, parent, script)
             for parent in ("", "services") for script in SERVICE_SCRIPTS}
    return any(arg in paths for arg in argv[1:])


def _process_cwd(pid):
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd"],
                             capture_output=True, text=True, timeout=5).stdout
        parts = out.strip().split()
        return parts[-1] if parts else ""
    except Exception:
        return ""


def _is_previous_manager(pid):
    if not pid or pid == os.getpid():
        return False
    cmd = _process_command(pid)
    if "main.py" not in cmd:
        return False
    return ROOT in cmd or _process_cwd(pid) == ROOT


def stop_previous_manager():
    """Stop an older main.py manager so it cannot respawn killed services."""
    try:
        with open(MANAGER_PID_FILE, encoding="ascii") as f:
            old = int(f.read().strip())
    except (OSError, ValueError):
        return
    if not _is_previous_manager(old):
        return
    try:
        os.kill(old, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(50):
        try:
            os.kill(old, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(old, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def write_manager_pid():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    with open(MANAGER_PID_FILE, "w", encoding="ascii") as f:
        f.write(str(os.getpid()))


def cleanup_stale_services():
    """Drop older service daemons before this manager binds their ports."""
    pids = set(_lsof_pids("-nP", "-iTCP:%d" % GATEWAY_PORT, "-sTCP:LISTEN"))
    pids.update(_lsof_pids("-nP", "-iTCP:%d" % SESSION_PORT, "-sTCP:LISTEN"))
    pids.update(_lsof_pids(SNIPER_LOCK, SESS_LOCK))
    pids.update(_service_pids())
    for pid in sorted(pids):
        if not _is_orphan_service(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            continue

    # Sniper does not bind a TCP port, but its lock must be released first.
    for _ in range(40):
        leftover = set(_lsof_pids(SNIPER_LOCK, SESS_LOCK))
        if not leftover:
            break
        time.sleep(0.1)
    for pid in sorted(leftover):
        if _is_orphan_service(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    # Gateway must be gone before the new process can bind :9333.
    for _ in range(40):
        if not _lsof_pids("-nP", "-iTCP:%d" % GATEWAY_PORT, "-sTCP:LISTEN"):
            return
        time.sleep(0.1)


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
                ports += [int(m) for m in re.findall(r"ws=127\.0\.0\.1:(\d+)", f.read())]
        except OSError:
            continue
    return ports[-1] if ports else None


_targets_cache = {"ts": 0.0, "data": None}


async def get_targets_cached():
    """Read targets from the session daemon's status (ZERO bridge load —
    the bridge degrades under frequent connections and self-heals only
    after quiet periods)."""
    now = time.time()
    if _targets_cache["data"] is not None and now - _targets_cache["ts"] < 0.5:
        return _targets_cache["data"]
    data = None
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8778/status",
                                    timeout=3) as r:
            st = json.loads(r.read())
        data = {"bridge": st.get("bridge_port") or find_bridge_port(),
                "bridge_state": st.get("bridge_state", "?"),
                "debug_mode": st.get("debug_mode", "bridge"),
                "targets": [t for t in st.get("targets", [])
                            if t.get("appid")],
                "mps": st.get("mps", {}),
                "capture_settings": st.get("capture_settings", {})}
    except Exception:
        data = None
    if data is None:
        # daemon not up yet — DO NOT touch the bridge from the poll path
        data = {"bridge": find_bridge_port(), "targets": []}
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
                                "appid": "web:" + str(t["targetId"]),
                                "kind": "web",
                                "title": t.get("title") or "网页",
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

    def do_POST(self):
        if self.path.split('?')[0] == '/api/h5/start':
            self._json(ensure_h5_service(force=True))
            return
        routes = {"/api/settings": "/capture_settings", "/api/retry": "/retry",
                  "/api/evaluate": "/eval", "/api/control": "/control",
                  "/api/breakpoint": "/breakpoint"}
        route = routes.get(self.path.split("?")[0])
        if not route:
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 8192:
                raise ValueError("invalid request size")
            data = json.loads(self.rfile.read(size))
            self._json(session_request(route, data))
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            self._json(dashboard_status())
        elif path == "/api/events":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            from services.event_store import read_events
            try:
                since = float((qs.get("since") or ["0"])[0])
                if qs.get("paged") == ["1"]:
                    self._json(read_events(cursor=(qs.get("cursor") or [None])[0],
                                           since=since, limit=int((qs.get("limit") or ["200"])[0])))
                else:
                    self._json(recent_events(since=since))
            except ValueError as exc:
                self._json({"error": str(exc)})
        elif path == "/api/settings":
            from services.capture_config import load_config
            self._json({"settings": load_config()})
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
            self._json(session_request("/storage", {"appid": appid}))
        elif path == "/api/files":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            appid = (qs.get("appid") or [""])[0]
            if not (re.fullmatch(r"wx[0-9a-f]{16}", appid) or
                    re.fullmatch(r"web:[A-Za-z0-9_.:-]+", appid)):
                self._json({"error": "invalid target"})
                return
            files = []
            srcdir = f"/tmp/wechat_rea/src_{appid}"
            if os.path.isdir(srcdir):
                for fn in os.listdir(srcdir):
                    if fn.endswith(".js"):
                        files.append(fn)
            if not files and not appid.startswith("web:"):
                outdir = ensure_unpacked(appid)
                for dirpath, _dirnames, filenames in os.walk(outdir):
                    for fn in filenames:
                        if fn.endswith((".js", ".json")):
                            rel = os.path.relpath(os.path.join(dirpath, fn),
                                                  outdir)
                            files.append(rel)
            if not files:
                # no local package either — pull runtime sources via daemon
                try:
                    import urllib.request
                    r = urllib.request.urlopen(urllib.request.Request(
                        "http://127.0.0.1:8778/fetch_source",
                        data=json.dumps({"appid": appid}).encode(),
                        headers={"Content-Type": "application/json"}),
                        timeout=30)
                    for fn in (json.loads(r.read()) or {}).get("saved", []):
                        files.append(fn)
                except Exception:
                    pass
            files.sort()
            self._json({"appid": appid, "files": files[:400]})
        elif path == "/api/source":
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
            appid = (qs.get("appid") or [""])[0]
            fname = (qs.get("file") or ["app-service.js"])[0]
            if not (re.fullmatch(r"wx[0-9a-f]{16}", appid) or
                    re.fullmatch(r"web:[A-Za-z0-9_.:-]+", appid)):
                content = None
            else:
                content = read_source(appid, fname)
            if content is None:
                self._json({"error": f"file not found: {fname} "
                                     f"(needs unpacked package)"})
            else:
                self._json({"appid": appid, "file": fname,
                            "lines": content.splitlines()})
        elif path == "/api/open_devtools":
            from urllib.parse import parse_qs, urlencode, urlsplit
            qs = parse_qs(urlsplit(self.path).query)
            tid = qs.get("target_id", [""])[0]
            appid = qs.get("appid", [""])[0]
            if re.fullmatch(r"[A-Za-z0-9_.:-]+", tid):
                endpoint = f"127.0.0.1:{GATEWAY_PORT}/devtools/page/{tid}"
                if re.fullmatch(r"wx[0-9a-f]{16}", appid or ""):
                    from urllib.parse import quote
                    endpoint += "?appid=" + quote(appid, safe="")
                url = "devtools://devtools/bundled/devtools_app.html?" + urlencode({"ws": endpoint})
                # 'open' cmd doesn't support devtools:// — use AppleScript
                apple = f'tell application "Google Chrome" to open location "{url}"'
                subprocess.Popen(["osascript", "-e", apple],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True, "url": url})
            else:
                self._json({"ok": False, "error": "no target_id"})
        elif path == "/api/start_sniper":
            ok = spawn("sniper", "services/sniper.py")
            self._json({"ok": True, "started": ok})
        elif path == "/api/start_gateway":
            ok = spawn("gateway", "services/cdp_gateway.py")
            self._json({"ok": True, "started": ok})
        elif path in ("/", "/dashboard.css", "/dashboard.js", "/debug_model.js",
                       "/vendor/lucide.min.js", "/vendor/beautify.min.js"):
            filename = "dashboard.html" if path == "/" else path.lstrip("/")
            with open(os.path.join(WEB_DIR, filename), "rb") as f:
                body = f.read()
            self.send_response(200)
            content_type = ("text/css" if filename.endswith(".css") else
                            "text/javascript" if filename.endswith(".js") else "text/html")
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def session_request(path, data):
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8778" + path,
                                     data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=22) as response:
            return json.loads(response.read())
    except Exception as exc:
        return {"error": f"service 请求失败: {type(exc).__name__}"}


def recent_events(limit=200, since=0.0):
    from services.event_store import read_events
    page = read_events(since=since, limit=1000)
    events = page["events"]
    while page["has_more"]:
        page = read_events(cursor=page["cursor"], since=since, limit=1000)
        events = (events + page["events"])[-limit:]
    return events[-limit:]


def ensure_unpacked(appid):
    """Auto-unpack a mini-program's plaintext wxapkg for the source tab."""
    outdir = f"/tmp/wechat_rea/mp_{appid}"
    if os.path.isdir(outdir) and \
            os.path.exists(os.path.join(outdir, "app-service.js")):
        return outdir
    try:
        subprocess.run([sys.executable, "-u", os.path.join(ROOT, "services", "unpack_mp.py"),
                        appid, outdir],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       cwd=ROOT, timeout=60)
    except Exception:
        pass
    return outdir


def runtime_src_path(appid, fname):
    """Runtime script saved by service_session (exact line mapping)."""
    srcdir = f"/tmp/wechat_rea/src_{appid}"
    cand = os.path.join(srcdir, fname)
    if os.path.isfile(cand):
        return cand
    # dash-joined runtime name e.g. usr_app-service.js / subPackages_...
    base = os.path.basename(fname)
    for alt in (f"usr_{base}", base,
                "usr_subPackages_" + base, "subPackages_" + base):
        cand = os.path.join(srcdir, alt)
        if os.path.isfile(cand):
            return cand
    return None


def read_source(appid, fname):
    """Runtime script first (exact line numbers), else unpacked package."""
    safe = os.path.normpath(fname).lstrip("/")
    if ".." in safe.split(os.sep):
        return None
    path = runtime_src_path(appid, safe)
    if path is None:
        if str(appid).startswith("web:"):
            return None
        outdir = ensure_unpacked(appid)
        path = os.path.join(outdir, safe)
        if not os.path.isfile(path):
            return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(6 * 1024 * 1024)
    except OSError:
        return None


def _spawn_flow(flow):
    """Start the daemons belonging to one WeChat flow (skip already-running)."""
    if flow == "legacy":
        spawn("legacy", "services/legacy_inject.py")
        return
    ensure_h5_service()
    if os.environ.get("WECHAT_DEBUG_MODE") == "direct":
        spawn("direct", "services/wmpf_direct.py")
    spawn("sniper", "services/sniper.py")
    spawn("gateway", "services/cdp_gateway.py")
    spawn("sess", "services/service_session.py")


def _retire_children(names):
    """Terminate children whose service belongs to a retired flow."""
    for name in names:
        child = _children.pop(name, None)
        if child is None:
            continue
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def auto_manage():
    """Background thread: run the service stack for the detected WeChat build.

    Re-detects every cycle so a WeChat upgrade/restart mid-session switches
    flows (and service sets) without a launcher restart.
    """
    global FLOW_STATE
    flow = FLOW_STATE["flow"]
    while not _manage_stop.is_set():
        detected = wechat_detect.detect_flow()
        if detected != FLOW_STATE:
            FLOW_STATE = detected
        if detected["flow"] != flow:
            print(f"[管理] 微信版本变化：流程 {flow} -> {detected['flow']}"
                  f"（版本 {detected['version']}）", flush=True)
            _retire_children(FLOW_CHILDREN[flow])
            flow = detected["flow"]
        _spawn_flow(flow)
        _manage_stop.wait(5)


def stop_children():
    for child in _children.values():
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 4
    for child in _children.values():
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def _pretty_wechat_version(version_id, cmdline=""):
    """启动提示用的人类可读版本号。

    4.x 会挂一个永不更新的 MacWechat/3.8.7 兼容 UA —— 只有当 UA 的 hex
    标识与解析出的 version_id 一致时才采信其版本串（3.8.x 场景）；
    否则用配置的 version 字段（f2641d62 -> 4.1.13.98）。
    """
    if cmdline:
        m = re.search(r"MacWechat/([\d.]+)\(0x([0-9a-fA-F]+)\)", cmdline)
        if m and m.group(2).lower() == version_id:
            return m.group(1)
    try:
        return get_version(version_id).get("version") or version_id
    except (OSError, ValueError):
        return version_id or "?"


def wait_for_wechat():
    """Wait for the desktop app; AppEx may be spawned only after navigation."""
    detect = wechat_detect.detect_flow()
    if detect["pid"] or wechat_detect.find_wechat_main()[0]:
        return detect
    print("[-] 未检测到微信。请先启动微信（并登录），本工具每 2 秒自动重试；"
          "Ctrl+C 退出。")
    while True:
        time.sleep(2)
        detect = wechat_detect.detect_flow()
        if detect["pid"] or wechat_detect.find_wechat_main()[0]:
            return detect


def run_legacy_inline(pid, version, pretty=None):
    """老版流程（3.8.x）：沿用原 main.py 的输出与交互 —— 横幅、彩色日志、
    前台常驻；不启动 Dashboard/8777，也不拉任何后台守护。"""
    try:
        from utils.banner import generate_banner
        generate_banner()
    except ImportError:
        print("=== WX DevTools ===")
    from utils.colors import Color
    import frida
    from services.legacy_inject import build_hookcode

    print(f"[+] 检测到微信 {pretty or version}（{version}）"
          f"→ 老版流程（地址补丁注入，不启动 Dashboard）")
    print(f"[+] WeChatAppEx 主进程 PID: {pid}")

    def on_message(message, _data):
        if message.get("type") == "send":
            print(Color.GREEN + str(message.get("payload")) + Color.END,
                  flush=True)
        elif message.get("type") == "error":
            print(Color.RED + str(message.get("stack")) + Color.END, flush=True)

    session = None
    try:
        session = frida.attach(pid)
        script = session.create_script(build_hookcode(version))
        script.on("message", on_message)
        script.load()
        if sys.stdin is not None and sys.stdin.isatty():
            sys.stdin.read()
        else:
            while True:
                time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
        print("[+] 已退出，注入会话已卸载")


def run_dashboard(detect):
    """新版流程（4.x）：8777 Dashboard + 自动托管服务组。"""
    global FLOW_STATE
    FLOW_STATE = detect
    os.makedirs(WEB_DIR, exist_ok=True)
    stop_previous_manager()
    cleanup_stale_services()
    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), Handler)
    write_manager_pid()
    manager = threading.Thread(target=auto_manage, daemon=True)
    def stop(*_):
        _manage_stop.set()
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    manager.start()
    try:
        print(f"\n  Dashboard: http://127.0.0.1:{DASHBOARD_PORT}/\n")
        print("  小程序 + 网页服务自动托管\n")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _manage_stop.set()
        manager.join()
        server.server_close()
        stop_children()
        try:
            with open(MANAGER_PID_FILE, encoding="ascii") as f:
                ours = f.read().strip() == str(os.getpid())
            if ours:
                os.unlink(MANAGER_PID_FILE)
        except OSError:
            pass


def _direct_available():
    try:
        from services import wmpf_direct  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_mode(parser, requested):
    if requested == "direct" and not _direct_available():
        parser.error("direct 模式需要: "
                     "python3 -m pip install -r requirements.txt")
    if requested is None:
        requested = "direct" if _direct_available() else "bridge"
        if requested == "bridge":
            print("[提示] direct 依赖缺失 (requirements.txt)，"
                  "回落 bridge 模式")
    return requested


def main(argv=None):
    global H5_PORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=int, default=8780,
                        help="网页 Chii 服务端口（默认 8780）")
    parser.add_argument("--mode", choices=("direct", "bridge"), default=None,
                        help="仅 4.x 生效：direct（默认，无需微信开发者工具）/"
                             "bridge（真机调试桥）")
    parser.add_argument("-p", "--pid", type=int, metavar="PID",
                        help="对指定 WeChatAppEx pid 一次性老版注入"
                             "（跳过自动检测）")
    parser.add_argument("--version", metavar="ID",
                        help="配合 -p 使用：指定老版配置标识（如 13080712）")
    options = parser.parse_args(argv)
    if not 1024 <= options.web_port <= 65535 or options.web_port in (8777, 8778, 9333, 9422):
        parser.error("--web-port 必须为 1024..65535，且不能占用其他调试服务端口")
    H5_PORT = options.web_port
    os.environ["WECHAT_DEBUG_MODE"] = _resolve_mode(parser, options.mode)

    if options.pid:
        version = options.version or wechat_detect.extract_version_id(
            wechat_detect.command_for_pid(options.pid),
            wechat_detect.all_known_ids())
        if not version:
            parser.error(f"无法识别 pid {options.pid} 的微信版本，"
                         "请用 --version 指定配置标识")
        if options.mode:
            print("[提示] 老版流程不使用 --mode")
        stop_previous_manager()
        cleanup_stale_services()
        run_legacy_inline(options.pid, version)
        return

    detect = wait_for_wechat()
    pretty = _pretty_wechat_version(
        detect["version"], wechat_detect.command_for_pid(detect["pid"]))
    if detect["flow"] == "legacy":
        if options.mode:
            print("[提示] 老版流程不使用 --mode，也不启动 Dashboard")
        stop_previous_manager()
        cleanup_stale_services()
        run_legacy_inline(detect["pid"], detect["version"], pretty)
        return
    print(f"[+] 检测到微信 {pretty}（{detect['version']}）→ 新版流程")
    run_dashboard(detect)


if __name__ == "__main__":
    main()
