#!/usr/bin/env python3
"""Mini-program service-layer debug session (v3.1).

One WebSocket to the WeChat remote-debug bridge. Per mini-program page
target: finds the SERVICE context (`typeof wx === 'object'`), injects
scripts/rea_agent.js, enables the Debugger domain, fetches runtime
sources (exact line numbers). Serves an HTTP control API on :8778:

  GET  /status                       appids / scripts / paused
  POST /breakpoint {appid,file,line,remove}
  POST /control    {appid,action}    resume|stepOver|stepInto|stepOut
  POST /eval       {appid,code}      evaluate (call frame when paused)

Events are appended to /tmp/wechat_rea/service_events.jsonl, which is
rotated at startup (each debug session starts clean).
"""
import asyncio
import fcntl
import glob
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
EVENTS = "/tmp/wechat_rea/service_events.jsonl"
EVAL_FILE = "/tmp/wechat_rea/service_eval"
STORAGE_REQ = "/tmp/wechat_rea/service_storage_req"
STORAGE_RESP = "/tmp/wechat_rea/service_storage_resp"
ROOT = os.path.dirname(os.path.abspath(__file__))
CTRL_PORT = 8778

MP = {}            # appid -> {sid, ctx, scripts, paused, topFrame, bps, fails}
TARGETS_CACHE = []
ONLINE = {}        # appid -> last heartbeat/event ts (agent hub, no bridge)
FAILED_CTX = {}    # appid -> ts of last ctx-discovery failure (cooldown)
ws_ref = {}
_loop = None
_ctrl_queue = None  # asyncio.Queue of (req, done_event, result_box)


def emit(rec):
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def find_port():
    """Bridge port: DevTools log first; lsof fallback (watch listeners,
    never touches the relay — probing the relay kills its client slot)."""
    ports = []
    for f in sorted(glob.glob(os.path.join(LOGDIR, "*.log")),
                    key=os.path.getmtime)[-4:]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                ports += re.findall(r"ws=127\.0\.0\.1:(\d+)", fh.read())
        except OSError:
            pass
    if ports:
        return int(ports[-1])
    # lsof: ports the DevTools app listens on (candidate bridge relays)
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                             capture_output=True, text=True,
                             timeout=5).stdout
        cands = []
        for line in out.splitlines():
            if "wechatweb" not in line:
                continue
            m = re.search(r"127\.0\.0\.1:(\d+)|\*:(\d+)", line)
            if m:
                p = int(m.group(1) or m.group(2))
                if p not in (58001, 62586):   # known non-CDP services
                    cands.append(p)
        return cands[0] if cands else None
    except Exception:
        return None


def find_port_candidates():
    """All plausible bridge ports (log + lsof), newest first."""
    cands = []
    p = find_port()
    if p:
        cands.append(p)
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                             capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            if "wechatweb" not in line:
                continue
            m = re.search(r"127\.0\.0\.1:(\d+)|\*:(\d+)", line)
            if m:
                q = int(m.group(1) or m.group(2))
                if q not in cands and q not in (58001, 62586):
                    cands.append(q)
    except Exception:
        pass
    return cands


class Br:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.wait = {}
        self.buf = []

    async def start(self):
        asyncio.create_task(self._rd())

    async def _rd(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if m.get("id") in self.wait:
                    fut = self.wait.pop(m["id"])
                    if not fut.done():
                        fut.set_result(m)
                elif m.get("method", "").startswith("Debugger."):
                    await self._on_event(m)
                else:
                    self.buf.append(m)
        except Exception:
            pass

    async def _on_event(self, m):
        sid = m.get("sessionId")
        appid = next((a for a, v in MP.items() if v["sid"] == sid), None)
        if not appid:
            return
        p = m.get("params", {})
        method = m.get("method", "")
        if method == "Debugger.scriptParsed":
            url = p.get("url", "")
            if url:
                MP[appid]["scripts"][url] = p.get("scriptId")
        elif method == "Debugger.paused":
            url_by_id = {sid_: u for u, sid_ in MP[appid]["scripts"].items()}
            frames = []
            for fr in p.get("callFrames", [])[:12]:
                loc = fr.get("location", {})
                frames.append({
                    "fn": fr.get("functionName") or "(anonymous)",
                    "url": url_by_id.get(loc.get("scriptId"), ""),
                    "line": loc.get("lineNumber", -1) + 1,
                    "col": loc.get("columnNumber", 0),
                    "cf": fr.get("callFrameId", ""),
                })
            MP[appid]["paused"] = {"frames": frames,
                                   "reason": p.get("reason", "")}
            MP[appid]["topFrame"] = frames[0]["cf"] if frames else None
            emit({"t": time.time(), "appid": appid, "type": "paused",
                  "data": MP[appid]["paused"]})
        elif method == "Debugger.resumed":
            MP[appid]["paused"] = None
            emit({"t": time.time(), "appid": appid, "type": "resumed",
                  "data": {}})

    async def cmd(self, method, sid=None, timeout=8, **params):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self.wait[mid] = fut
        m = {"id": mid, "method": method, "params": params}
        if sid:
            m["sessionId"] = sid
        await self.ws.send(json.dumps(m))
        try:
            r = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"_err": "timeout"}
        return r.get("result", r.get("error"))

    async def drain(self, secs):
        await asyncio.sleep(secs)
        out, self.buf = self.buf, []
        return out


async def eval_js(br, sid, ctx, expr, timeout=8):
    ev = await br.cmd("Runtime.evaluate", sid, timeout=timeout,
                      expression=expr, contextId=ctx,
                      returnByValue=True, silent=True)
    if isinstance(ev, dict) and "_err" in ev:
        return None
    if isinstance(ev, dict) and "result" in ev:
        return ev["result"].get("value")
    return None


DUMP_STORAGE_JS = (
    "(function(){try{var i=wx.getStorageInfoSync();var kv={};"
    "(i.keys||[]).slice(0,120).forEach(function(k){var v;"
    "try{v=JSON.stringify(wx.getStorageSync(k))}catch(e){v=String(v)}"
    "kv[k]=(v||'').slice(0,2000)});"
    "return JSON.stringify({keys:i.keys||[],sz:i.currentSize,"
    "limit:i.limitSize,kv:kv})}catch(e){"
    "return JSON.stringify({err:String(e)})}})()")


async def setup_targets(br):
    r = await br.cmd("Target.getTargets", timeout=6)
    if not isinstance(r, dict) or "_err" in r or "targetInfos" not in r:
        raise ConnectionError("bridge getTargets failed — reconnect")
    global TARGETS_CACHE
    TARGETS_CACHE = r.get("targetInfos", [])
    pages = [t for t in TARGETS_CACHE
             if t["type"] == "page" and "servicewechat.com/wx" in
             t.get("url", "")]
    seen = set()
    for t in pages:
        am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
        appid = am.group(1) if am else "?"
        seen.add(appid)
        if appid in MP:
            continue
        if time.time() - FAILED_CTX.get(appid, 0) < 60:
            continue   # recently failed — don't churn attach on dead targets
        a = await br.cmd("Target.attachToTarget", targetId=t["targetId"],
                         flatten=True, timeout=6)
        sid = a.get("sessionId") if isinstance(a, dict) else None
        if not sid:
            continue
        await br.cmd("Runtime.enable", sid)
        evs = await br.drain(0.8)
        ctxs = [e["params"]["context"] for e in evs
                if e.get("method") == "Runtime.executionContextCreated"]
        svc = None
        for c in ctxs:
            if await eval_js(br, sid, c.get("id"), "typeof wx",
                             timeout=3) == "object":
                svc = c.get("id")
                break
        if svc is None:
            print(f"[sess] {appid}: no service ctx", flush=True)
            FAILED_CTX[appid] = time.time()
            continue
        has = await eval_js(br, sid, svc,
                            "typeof __REA__+'/'+(__REA__?__REA__.version:0)",
                            timeout=4)
        if has != "object/4":
            agent_src = open(os.path.join(
                ROOT, "scripts", "rea_agent.js")).read()
            await eval_js(br, sid, svc, agent_src, timeout=9)
            print(f"[sess] agent injected -> {appid}", flush=True)
        # NOTE: Debugger.enable stays OFF by default — enabling it on the
        # service context stalls live event capture (observed 2026-10-19).
        # It is enabled on demand by the first /breakpoint request.
        MP[appid] = {"sid": sid, "ctx": svc, "scripts": {}, "paused": None,
                     "topFrame": None, "bps": {}, "fails": 0,
                     "dbg": False}
        emit({"t": time.time(), "appid": appid, "type": "attached",
              "data": {"debugger": False}})
    for a in [a for a in MP if a not in seen]:
        print(f"[sess] {a} gone", flush=True)
        del MP[a]


async def refresh_ctx(br, v):
    """Re-discover the service context (contexts change on page
    recreation; stale contextId makes every evaluate silently fail)."""
    await br.cmd("Runtime.enable", v["sid"], timeout=5)
    evs = await br.drain(1.0)
    ctxs = [e["params"]["context"] for e in evs
            if e.get("method") == "Runtime.executionContextCreated"]
    for c in ctxs:
        if await eval_js(br, v["sid"], c.get("id"), "typeof wx",
                         timeout=4) == "object":
            if c.get("id") != v["ctx"]:
                print(f"[sess] ctx refreshed -> {c.get('id')}", flush=True)
            v["ctx"] = c.get("id")
            return True
    return False


async def poll_agent(br, appid):
    """True=ok, False=should drop (after 3 consecutive failures)."""
    v = MP[appid]
    # primary path: agent pushes via hub — no bridge traffic needed
    if ONLINE.get(appid, 0) > time.time() - 30:
        v["fails"] = 0
        return True
    try:
        raw = await eval_js(
            br, v["sid"], v["ctx"],
            "(function(){if(typeof __REA__!=='object')return 'DEAD';"
            "if(!__REA__.drain)return 'V1';"
            "return JSON.stringify(__REA__.drain(200))})()", timeout=5)
        if raw is None:
            # stale context (page recreated) — rediscover and retry once
            if await refresh_ctx(br, v):
                raw = await eval_js(
                    br, v["sid"], v["ctx"],
                    "(function(){if(typeof __REA__!=='object')return 'DEAD';"
                    "if(!__REA__.drain)return 'V1';"
                    "return JSON.stringify(__REA__.drain(200))})()",
                    timeout=6)
        if raw is None:
            v["fails"] += 1
            print(f"[sess] poll {appid}: eval dead ({v['fails']})",
                  flush=True)
            if v["fails"] >= 2:
                raise ConnectionError("bridge degraded — reconnect")
            return True
        if raw == "DEAD":
            v["fails"] += 1
            if v["fails"] >= 3:
                return False
            return True
        v["fails"] = 0
        if raw == "V1":
            raw = await eval_js(
                br, v["sid"], v["ctx"],
                "JSON.stringify(__REA__.events.splice(0,200))", timeout=5)
        events = json.loads(raw) if isinstance(raw, str) else []
        for e in events if isinstance(events, list) else []:
            if isinstance(e, dict):
                # e carries the agent's own "t" (ms) — ours must win (sec)
                emit({**e, "t": time.time(), "appid": appid})
                d = e.get("data") if isinstance(e.get("data"), dict) else {}
                head = (d.get("url") or d.get("route") or d.get("level") or
                        d.get("msg") or d.get("op") or "")
                print(f"[{appid[:10]}] {str(e.get('type')):9} "
                      f"{str(head)[:70]}", flush=True)
    except Exception as exc:
        v["fails"] += 1
        print(f"[sess] poll {appid}: {type(exc).__name__} "
              f"({v['fails']})", flush=True)
        if v["fails"] >= 3:
            return False
    return True


# ---------------- control processing (fast queue) ----------------
async def ensure_debugger(br, v):
    """Enable Debugger on demand (first breakpoint). Keeps scriptParsed
    registry; does NOT pull sources (the 5MB transfer is hard on the
    bridge — cached files from earlier sessions serve source viewing)."""
    if v["dbg"]:
        return True
    r = await br.cmd("Debugger.enable", v["sid"], timeout=6)
    ok = isinstance(r, dict) and "debuggerId" in r
    if ok:
        v["dbg"] = True
        await asyncio.sleep(0.6)   # let scriptParsed arrive
        print(f"[sess] debugger enabled on demand "
              f"({len(v['scripts'])} scripts)", flush=True)
    return ok


async def process_ctrl(br, req):
    appid = req.get("appid")
    v = MP.get(appid)
    if v is None:
        return {"error": f"appid 未连接 (当前: {list(MP) or '无'}) — "
                         "等小程序出现在列表中再试"}
    try:
        if req.get("path") == "/breakpoint":
            line = int(req.get("line", 0))
            f = req.get("file", "app-service.js")
            url = ("https://usr/" + f) if "/" not in f else f
            v.setdefault("bps", {})
            if req.get("remove"):
                bpid = v["bps"].pop(f"{f}:{line}", None)
                if not bpid:
                    return {"ok": False, "error": "no such breakpoint"}
                r = await br.cmd("Debugger.removeBreakpoint", v["sid"],
                                 breakpointId=bpid, timeout=8)
                return {"ok": True, "removed": bpid}
            if not await ensure_debugger(br, v):
                return {"ok": False,
                        "error": "Debugger.enable failed (bridge)"}
            r = await br.cmd("Debugger.setBreakpointByUrl", v["sid"],
                             lineNumber=max(0, line - 1), url=url,
                             columnNumber=0, timeout=8)
            bpid = (r or {}).get("breakpointId")
            if bpid:
                v["bps"][f"{f}:{line}"] = bpid
            return {"ok": bool(bpid), "breakpointId": bpid,
                    "locations": (r or {}).get("locations", []),
                    "raw": r}
        if req.get("path") == "/control":
            action = req.get("action", "resume")
            method = {"resume": "Debugger.resume",
                      "stepOver": "Debugger.stepOver",
                      "stepInto": "Debugger.stepInto",
                      "stepOut": "Debugger.stepOut"}.get(action)
            if not method:
                return {"error": "bad action"}
            r = await br.cmd(method, v["sid"], timeout=6)
            return {"ok": isinstance(r, dict) and "_err" not in r and
                          "error" not in r, "raw": r}
        if req.get("path") == "/eval":
            code = req.get("code", "")
            if not code:
                return {"error": "no code"}
            if v.get("paused") and v.get("topFrame"):
                r = await br.cmd("Debugger.evaluateOnCallFrame", v["sid"],
                                 callFrameId=v["topFrame"],
                                 expression=code, returnByValue=True,
                                 silent=True, timeout=6)
                val = ((r or {}).get("result") or {}).get("value")
                if isinstance(r, dict) and "_err" in r:
                    return {"ok": False, "frame": True,
                            "value": str(r.get("_err"))}
                return {"ok": True, "frame": True, "value": val}
            wrapped = ("(function(){try{var r=(" + code +
                       ");r=(r===undefined?'undefined':r);"
                       "return JSON.stringify({ok:true,r:r})}"
                       "catch(e){return JSON.stringify("
                       "{ok:false,e:String(e)})}})()")
            val = await eval_js(br, v["sid"], v["ctx"], wrapped, timeout=5)
            if val is None and await refresh_ctx(br, v):
                val = await eval_js(br, v["sid"], v["ctx"], wrapped,
                                    timeout=5)
            if val is None:
                return {"ok": False, "frame": False, "value": None,
                        "debug": "ctx dead after refresh"}
            return {"ok": True, "frame": False, "value": val}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": "unknown path"}


async def ctrl_loop(br):
    """High-priority consumer of the control queue."""
    while True:
        try:
            item = await asyncio.wait_for(_ctrl_queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            continue
        req, ev, box = item
        try:
            box["result"] = await process_ctrl(br, req)
        except Exception as exc:
            box["result"] = {"error": str(exc)}
        ev.set()


async def legacy_file_requests(br):
    """start.py /api/eval and /api/storage file channels."""
    if not MP:
        return
    if os.path.exists(EVAL_FILE):
        try:
            code = open(EVAL_FILE).read().strip()
            os.remove(EVAL_FILE)
        except OSError:
            code = None
        if code:
            appid, v = next(iter(MP.items()))
            val = await eval_js(
                br, v["sid"], v["ctx"],
                "(function(){try{var r=(" + code +
                ");r=(r===undefined?'undefined':r);"
                "return JSON.stringify({ok:true,r:r})}catch(e){"
                "return JSON.stringify({ok:false,e:String(e)})}})()")
            emit({"t": time.time(), "appid": appid, "type": "eval_result",
                  "data": val})
    if os.path.exists(STORAGE_REQ):
        try:
            want = open(STORAGE_REQ).read().strip()
            os.remove(STORAGE_REQ)
        except OSError:
            want = None
        if want is not None:
            for appid, v in list(MP.items()):
                if want and appid != want:
                    continue
                res = await eval_js(br, v["sid"], v["ctx"],
                                    DUMP_STORAGE_JS, timeout=8)
                try:
                    with open(STORAGE_RESP, "w", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "appid": appid,
                            "data": json.loads(res)
                            if isinstance(res, str) else None}))
                except (OSError, ValueError):
                    pass
                break


class CtrlHandler(BaseHTTPRequestHandler):
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
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            req = {}
        req["path"] = self.path.split("?")[0]
        ev = threading.Event()
        box = {}
        _loop.call_soon_threadsafe(_ctrl_queue.put_nowait,
                                   (req, ev, box))
        if not ev.wait(timeout=20):
            self._json({"error": "session 未响应 (bridge 断开?)"})
            return
        self._json(box.get("result") or {"error": "no result"})

    def do_GET(self):
        if self.path.split("?")[0] == "/status":
            self._json({"targets": [{"appid": (re.search(r"(wx[0-9a-f]{16})", t.get("url", "")) or [None, None])[1],
                                     "url": t.get("url", "")[:90],
                                     "targetId": t.get("targetId")}
                                    for t in TARGETS_CACHE
                                    if t.get("type") == "page" and
                                    "servicewechat.com/wx" in t.get("url", "")],
                        "mps": {a: {"debugger": True,
                                    "scripts": list(v["scripts"].keys())[:20],
                                    "paused": v["paused"],
                                    "bps": list(v.get("bps", {}).keys())}
                                for a, v in MP.items()}})
        else:
            self._json({})


async def agent_hub(ws):
    """Direct agent connections (wx.connectSocket) — bridge-independent
    event push. This is the primary capture path; bridge polling is only
    a fallback."""
    m = re.match(r"/agent\?appid=(wx[0-9a-f]{16})", ws.path or "")
    if not m:
        await ws.close()
        return
    appid = m.group(1)
    ONLINE[appid] = time.time()
    print(f"[hub] agent online: {appid}", flush=True)
    emit({"t": time.time(), "appid": appid, "type": "agent_online",
          "data": {}})
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            ONLINE[appid] = time.time()
            if msg.get("hb"):
                continue
            for e in msg.get("events", [])[:500]:
                if isinstance(e, dict):
                    emit({**e, "t": time.time(), "appid": appid})
    finally:
        print(f"[hub] agent offline: {appid}", flush=True)
        ONLINE[appid] = 0


async def run_session(port):
    async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=3,
                                  max_size=2**24) as ws:
        br = Br(ws)
        await br.start()
        ctrl_task = asyncio.create_task(ctrl_loop(br))
        stale_cycles = 0
        try:
            while True:
                await setup_targets(br)
                if not MP:
                    stale_cycles += 1
                    if stale_cycles >= 6:
                        # zombie connection: bridge port alive but serves
                        # nothing — force reconnect
                        print("[sess] stale bridge, reconnecting",
                              flush=True)
                        return
                    await asyncio.sleep(4)
                    continue
                stale_cycles = 0
                for _ in range(2):   # ~16s rounds, 8s polls (gentler)
                    await asyncio.sleep(8.0)
                    await legacy_file_requests(br)
                    activity = False
                    for appid in list(MP):
                        ok = await poll_agent(br, appid)
                        if not ok:
                            print(f"[sess] drop {appid}", flush=True)
                            del MP[appid]
                        else:
                            activity = True
                    if not MP:
                        break
        finally:
            ctrl_task.cancel()


async def main():
    global _loop, _ctrl_queue
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    # single-instance lock (start.py respawns; duplicates fight for :8778)
    lock = open("/tmp/wechat_rea/sess.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[sess] another instance holds the lock, exiting", flush=True)
        return
    # rotate events: each session starts clean
    try:
        if os.path.exists(EVENTS):
            os.rename(EVENTS, EVENTS + ".old")
    except OSError:
        pass
    open(EVENTS, "a").close()
    print("[sess] service session daemon v3.1 started", flush=True)

    _loop = asyncio.get_event_loop()
    _ctrl_queue = asyncio.Queue()

    server = ThreadingHTTPServer(("127.0.0.1", CTRL_PORT), CtrlHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    await websockets.serve(agent_hub, "127.0.0.1", 8779,
                           max_size=2**22)

    refused = 0
    cand_idx = 0
    while True:
        cands = find_port_candidates()
        port = cands[cand_idx % len(cands)] if cands else None
        if port:
            try:
                await run_session(port)
                refused = 0
                cand_idx = 0
            except Exception as exc:
                if "RefusedError" in type(exc).__name__ or \
                        "Connect call failed" in str(exc):
                    refused += 1
                    if refused >= 3 and cands:
                        cand_idx += 1      # rotate to next candidate
                        refused = 0
                print(f"[sess] session ended: {type(exc).__name__} "
                      f"{str(exc)[:80]}", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
