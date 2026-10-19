#!/usr/bin/env python3
"""Mini-program service-layer debug session (v3: real breakpoints).

One WebSocket to the WeChat remote-debug bridge. For each mini-program
page target, finds the SERVICE context (`typeof wx === 'object'`),
injects scripts/rea_agent.js, enables the Debugger domain, and serves an
HTTP control API on :8778:

  GET  /status                      appids, scripts, paused state
  POST /breakpoint  {appid,file,line,remove}
  POST /control     {appid,action}  resume|stepOver|stepInto|stepOut
  POST /eval        {appid,code}    evaluate (call frame when paused)
  GET  /events?since=TS             events after timestamp

Agent events + debugger pauses are appended to
/tmp/wechat_rea/service_events.jsonl.
"""
import asyncio
import glob
import json
import os
import re
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

# ---------------- shared state (single asyncio loop) ----------------
MP = {}          # appid -> {sid, ctx, scripts:{url:scriptId}, paused:{...}}
ws_ref = {}      # the live bridge websocket


def emit(rec):
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def find_port():
    ports = []
    for f in sorted(glob.glob(os.path.join(LOGDIR, "*.log")),
                    key=os.path.getmtime)[-4:]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                ports += re.findall(r"ws=127\.0\.0\.1:(\d+)", fh.read())
        except OSError:
            pass
    return int(ports[-1]) if ports else None


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
                    self.buf.append(m)   # contexts etc. for drain()
        except Exception:
            pass

    async def _on_event(self, m):
        """Background events: scriptParsed, paused, console, etc."""
        sid = m.get("sessionId")
        method = m.get("method", "")
        appid = next((a for a, v in MP.items() if v["sid"] == sid), None)
        if not appid:
            return
        p = m.get("params", {})
        if method == "Debugger.scriptParsed":
            url = p.get("url", "")
            if url:
                MP[appid]["scripts"][url] = p.get("scriptId")
        elif method == "Debugger.paused":
            frames = []
            script_url = {sid_: u for u, sid_ in
                          MP[appid]["scripts"].items()}
            for fr in p.get("callFrames", [])[:12]:
                loc = fr.get("location", {})
                frames.append({
                    "fn": fr.get("functionName") or "(anonymous)",
                    "url": script_url.get(loc.get("scriptId"), ""),
                    "line": loc.get("lineNumber", -1) + 1,
                    "col": loc.get("columnNumber", 0),
                    "cf": fr.get("callFrameId", ""),
                })
            MP[appid]["paused"] = {"frames": frames,
                                   "reason": p.get("reason", "")}
            MP[appid]["topFrame"] = frames[0]["cf"] if frames else None
            emit({"t": time.time(), "appid": appid, "type": "paused",
                  "data": {"reason": p.get("reason"), "frames": frames}})
        elif method == "Debugger.resumed":
            MP[appid]["paused"] = None
            emit({"t": time.time(), "appid": appid, "type": "resumed",
                  "data": {}})

    async def cmd(self, method, sid=None, timeout=6, **params):
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
    r = await br.cmd("Target.getTargets")
    pages = [t for t in r.get("targetInfos", [])
             if t["type"] == "page" and "servicewechat.com/wx" in
             t.get("url", "")]
    agent = open(os.path.join(ROOT, "scripts", "rea_agent.js")).read()
    for t in pages:
        am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
        appid = am.group(1) if am else "?"
        if appid in MP:
            continue
        a = await br.cmd("Target.attachToTarget", targetId=t["targetId"],
                         flatten=True)
        sid = a.get("sessionId") if isinstance(a, dict) else None
        if not sid:
            continue
        # service context
        await br.cmd("Runtime.enable", sid)
        evs = await br.drain(1.2)
        ctxs = [e["params"]["context"] for e in evs
                if e.get("method") == "Runtime.executionContextCreated"]
        svc = None
        for c in ctxs:
            if await eval_js(br, sid, c.get("id"), "typeof wx") == "object":
                svc = c.get("id")
                break
        if svc is None:
            print(f"[sess] {appid}: no service ctx", flush=True)
            continue
        has = await eval_js(br, sid, svc, "typeof __REA__")
        if has != "object":
            await eval_js(br, sid, svc, agent, timeout=12)
            print(f"[sess] agent injected -> {appid}", flush=True)
        # register BEFORE Debugger.enable so scriptParsed lands in MP
        MP[appid] = {"sid": sid, "ctx": svc, "scripts": {}, "paused": None,
                     "topFrame": None, "bps": {}}
        # debugger domain — gives scriptParsed + real breakpoints
        r2 = await br.cmd("Debugger.enable", sid, timeout=5)
        ok = isinstance(r2, dict) and "debuggerId" in r2
        await asyncio.sleep(0.5)  # let scriptParsed events arrive
        print(f"[sess] {appid}: ctx={svc} debugger={'on' if ok else 'FAIL'} "
              f"scripts={len(MP[appid]['scripts'])}", flush=True)
        emit({"t": time.time(), "appid": appid, "type": "attached",
              "data": {"debugger": bool(ok),
                       "scripts": list(MP[appid]["scripts"].keys())[:10]}})
        # fetch runtime sources — line numbers then match the script
        # EXACTLY (local unpacked packages are a different build!)
        srcdir = f"/tmp/wechat_rea/src_{appid}"
        os.makedirs(srcdir, exist_ok=True)
        for surl, sid_script in list(MP[appid]["scripts"].items())[:6]:
            fname = surl.replace("https://", "").replace("usr/", "") \
                        .replace("/", "_") or "script.js"
            try:
                r3 = await br.cmd("Debugger.getScriptSource", sid,
                                  scriptId=sid_script, timeout=25)
                src = (r3 or {}).get("scriptSource") if isinstance(r3, dict) \
                    else None
                if src:
                    with open(os.path.join(srcdir, fname), "w",
                              encoding="utf-8") as f:
                        f.write(src)
                    emit({"t": time.time(), "appid": appid,
                          "type": "source_saved",
                          "data": {"file": fname, "lines":
                                   src.count("\n") + 1}})
            except Exception as exc:
                print(f"[sess] getScriptSource {fname}: {exc}", flush=True)
    # drop dead appids
    live = {a for a in MP}
    seen = set()
    r = await br.cmd("Target.getTargets")
    for t in r.get("targetInfos", []):
        am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
        if am:
            seen.add(am.group(1))
    for a in live - seen:
        print(f"[sess] {a} gone", flush=True)
        del MP[a]


async def poll_agent(br, appid):
    v = MP[appid]
    try:
        raw = await eval_js(
            br, v["sid"], v["ctx"],
            "(function(){if(typeof __REA__!=='object')return 'DEAD';"
            "if(!__REA__.drain)return 'V1';"
            "return JSON.stringify(__REA__.drain(200))})()")
        if raw == "DEAD":
            return False
        if raw == "V1":
            raw = await eval_js(
                br, v["sid"], v["ctx"],
                "JSON.stringify(__REA__.events.splice(0,200))")
        events = json.loads(raw) if isinstance(raw, str) else []
        for e in events if isinstance(events, list) else []:
            if isinstance(e, dict):
                emit({"t": time.time(), "appid": appid, **e})
                d = e.get("data") if isinstance(e.get("data"), dict) else {}
                head = (d.get("url") or d.get("route") or d.get("level") or
                        d.get("msg") or d.get("op") or "")
                print(f"[{appid[:10]}] {str(e.get('type')):9} "
                      f"{str(head)[:70]}", flush=True)
    except Exception as exc:
        print(f"[sess] poll {appid}: {type(exc).__name__} "
              f"{str(exc)[:50]}", flush=True)
        return False
    return True


# ---------------- HTTP control API ----------------
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
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            req = {}
        # hand off into the asyncio loop via a pending-command file
        cmdfile = "/tmp/wechat_rea/sess_ctrl_req"
        with open(cmdfile, "w") as f:
            json.dump({"path": path, **req}, f)
        deadline = time.time() + 6
        result = None
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                result = json.loads(
                    open("/tmp/wechat_rea/sess_ctrl_resp").read())
                os.remove("/tmp/wechat_rea/sess_ctrl_resp")
                break
            except (OSError, ValueError):
                continue
        self._json(result or {"error": "session busy / not connected"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/status":
            self._json({
                a: {"debugger": bool(v["scripts"] is not None),
                    "scripts": list(v["scripts"].keys())[:20],
                    "paused": v["paused"]}
                for a, v in MP.items()})
        else:
            self._json({})


async def serve_ctrl_requests(br):
    """Poll control/eval/storage request files."""
    req_file = "/tmp/wechat_rea/sess_ctrl_req"
    resp_file = "/tmp/wechat_rea/sess_ctrl_resp"

    # legacy eval file (start.py /api/eval)
    if os.path.exists(EVAL_FILE) and MP:
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

    # storage dump (start.py /api/storage)
    if os.path.exists(STORAGE_REQ) and MP:
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
                                    DUMP_STORAGE_JS, timeout=10)
                try:
                    with open(STORAGE_RESP, "w", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "appid": appid,
                            "data": json.loads(res)
                            if isinstance(res, str) else None}))
                except (OSError, ValueError):
                    pass
                break

    if not os.path.exists(req_file):
        return
    try:
        req = json.loads(open(req_file).read())
        os.remove(req_file)
    except (OSError, ValueError):
        return
    appid = req.get("appid")
    v = MP.get(appid)
    result = {"error": "unknown appid"}
    try:
        if req.get("path") == "/breakpoint" and v:
            line = int(req.get("line", 0))
            f = req.get("file", "app-service.js")
            url = ("https://usr/" + f) if "/" not in f else f
            v.setdefault("bps", {})
            if req.get("remove"):
                bpid = v["bps"].pop(f"{f}:{line}", None)
                if bpid:
                    r = await br.cmd("Debugger.removeBreakpoint", v["sid"],
                                     breakpointId=bpid)
                    result = {"ok": True, "removed": bpid}
                else:
                    result = {"ok": False, "error": "no such breakpoint"}
            else:
                r = await br.cmd(
                    "Debugger.setBreakpointByUrl", v["sid"],
                    lineNumber=max(0, line - 1), url=url,
                    columnNumber=0)
                bpid = (r or {}).get("breakpointId")
                # fallback: scriptId-based when by-url yields no location
                if bpid and not (r or {}).get("locations"):
                    for surl, sid_script in v["scripts"].items():
                        if surl.endswith(f):
                            r2 = await br.cmd(
                                "Debugger.setBreakpoint", v["sid"],
                                location={"scriptId": sid_script,
                                          "lineNumber": max(0, line - 1)},
                                condition="")
                            if r2 and r2.get("breakpointId"):
                                r, bpid = r2, r2["breakpointId"]
                            break
                if bpid:
                    v["bps"][f"{f}:{line}"] = bpid
                result = {"ok": bool(bpid), "breakpointId": bpid, "raw": r}
        elif req.get("path") == "/control" and v:
            action = req.get("action", "resume")
            method = {"resume": "Debugger.resume",
                      "stepOver": "Debugger.stepOver",
                      "stepInto": "Debugger.stepInto",
                      "stepOut": "Debugger.stepOut"}.get(action)
            if method:
                r = await br.cmd(method, v["sid"], timeout=5)
                result = {"ok": isinstance(r, dict) and
                          "_err" not in r and "error" not in r, "raw": r}
            else:
                result = {"error": "bad action"}
        elif req.get("path") == "/eval" and v:
            code = req.get("code", "")
            if v.get("paused") and v.get("topFrame"):
                r = await br.cmd(
                    "Debugger.evaluateOnCallFrame", v["sid"],
                    callFrameId=v["topFrame"], expression=code,
                    returnByValue=True, silent=True)
                val = ((r or {}).get("result") or {}).get("value")
                result = {"ok": True, "frame": True, "value": val}
            else:
                val = await eval_js(br, v["sid"], v["ctx"],
                                    "(function(){try{var r=(" + code +
                                    ");r=(r===undefined?'undefined':r);"
                                    "return JSON.stringify({ok:true,"
                                    "r:r})}catch(e){return "
                                    "JSON.stringify({ok:false,e:"
                                    "String(e)})}})()")
                result = {"ok": True, "frame": False, "value": val}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        with open(resp_file, "w") as f:
            f.write(json.dumps(result, ensure_ascii=False))
    except OSError:
        pass


async def run_session(port):
    agent = open(os.path.join(ROOT, "scripts", "rea_agent.js")).read()
    async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=3,
                                  max_size=2**23) as ws:
        br = Br(ws)
        await br.start()
        ws_ref["br"] = br
        await setup_targets(br)
        if not MP:
            print("[sess] no service contexts found", flush=True)
            await asyncio.sleep(4)
            return
        while True:
            await asyncio.sleep(1.0)
            await serve_ctrl_requests(br)
            for appid in list(MP):
                if not await poll_agent(br, appid):
                    del MP[appid]
            if not MP:
                return  # rescan


async def main():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    print("[sess] service session daemon v3 started", flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", CTRL_PORT), CtrlHandler)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()

    while True:
        port = find_port()
        if port:
            try:
                await run_session(port)
            except Exception as exc:
                print(f"[sess] session ended: {type(exc).__name__} "
                      f"{str(exc)[:80]}", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
