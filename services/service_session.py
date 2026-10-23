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

Events are appended to bounded, rotating JSONL files and survive restarts.
"""
import asyncio
from collections import OrderedDict
import fcntl
import glob
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets
try:
    from .event_store import append_event
    from .capture_config import load_config, save_config
except ImportError:
    from event_store import append_event
    from capture_config import load_config, save_config

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
EVENTS = "/tmp/wechat_rea/service_events.jsonl"
EVAL_FILE = "/tmp/wechat_rea/service_eval"
STORAGE_REQ = "/tmp/wechat_rea/service_storage_req"
STORAGE_RESP = "/tmp/wechat_rea/service_storage_resp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTRL_PORT = 8778

MP = {}            # appid -> {sid, ctx, scripts, paused, topFrame, bps, fails}
SESSION_APPS = {}  # sessionId -> appid, available before Runtime.enable completes
TARGETS_CACHE = []
ONLINE = {}        # appid -> last heartbeat/event ts (agent hub, no bridge)
HUB_VERSION = {}   # appid -> newest injected agent generation
HUB_SOCKET = {}    # appid -> current agent WebSocket
BRIDGE = {"state": "connecting", "port": None}   # connecting|up|dead
FAILED_CTX = {}    # targetId -> retry state (attempts, deadline, attached sid)
DIAGNOSTICS = {}
LAST_EVENT = {}
WAKE = None
SEEN_EVENTS = OrderedDict()
ws_ref = {}
_loop = None
_ctrl_queue = None  # asyncio.Queue of (req, done_event, result_box)


def emit(rec):
    append_event(rec, path=EVENTS)


def emit_agent(appid, event):
    """Deduplicate legacy live-push/flush copies before persisting them."""
    if event.get("seq") is not None:
        key = (appid, str(event["seq"]))
    else:
        raw = json.dumps(event, sort_keys=True, ensure_ascii=False).encode()
        key = (appid, hashlib.sha256(raw).digest())
    if key in SEEN_EVENTS:
        return
    SEEN_EVENTS[key] = True
    if len(SEEN_EVENTS) > 4096:
        SEEN_EVENTS.popitem(last=False)
    LAST_EVENT[appid] = time.time()
    emit({**event, "agent_t": event.get("t"),
          "t": time.time(), "appid": appid})


def find_port():
    """Bridge port: DevTools log first; lsof fallback (watch listeners,
    never touches the relay — probing the relay kills its client slot)."""
    if os.environ.get("WECHAT_DEBUG_MODE") == "direct":
        return 9422
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
    """Other wechatweb listeners are HTTP services, not backup CDP bridges."""
    p = find_port()
    return [p] if p else []


class Br:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.wait = {}
        self.buf = []
        self.reader = None
        self.contexts = {}

    async def start(self):
        self.reader = asyncio.create_task(self._rd())

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
                    self._runtime_event(m)
                    self.buf.append(m)
                    if len(self.buf) > 2000:
                        del self.buf[:len(self.buf) - 2000]
        except Exception:
            pass
        finally:
            for fut in self.wait.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("bridge disconnected"))
            self.wait.clear()

    def _runtime_event(self, message):
        method, sid = message.get("method"), message.get("sessionId")
        params = message.get("params") or {}
        web_appid = SESSION_APPS.get(sid) or next((a for a, v in MP.items()
                          if v.get("kind") == "web" and v.get("sid") == sid), None)
        if web_appid:
            self._emit_web_event(web_appid, method, params)
        if method == "Runtime.executionContextCreated":
            context = params.get("context") or {}
            if context.get("id") is not None:
                self.contexts.setdefault(sid, {})[context["id"]] = context
                for retry in FAILED_CTX.values():
                    if retry.get("sid") == sid:
                        retry["next_retry"] = 0
                if WAKE:
                    WAKE.set()
        elif method in ("Runtime.executionContextDestroyed", "Runtime.executionContextsCleared"):
            destroyed = params.get("executionContextId")
            if method.endswith("Cleared"):
                self.contexts.pop(sid, None)
            else:
                self.contexts.get(sid, {}).pop(destroyed, None)
            for appid, value in list(MP.items()):
                if value["sid"] == sid and (destroyed is None or value["ctx"] == destroyed):
                    MP.pop(appid, None)
                    FAILED_CTX[value["targetId"]] = {"sid": sid, "attempts": 0, "next_retry": 0}
                    DIAGNOSTICS[appid] = {"phase": "waiting_context", "since": time.time()}
            if WAKE:
                WAKE.set()
        elif method == "Target.detachedFromTarget":
            detached = params.get("sessionId")
            SESSION_APPS.pop(detached, None)
            self.contexts.pop(detached, None)
            for retry in FAILED_CTX.values():
                if retry.get("sid") == detached:
                    retry["sid"] = None
                    retry["next_retry"] = 0
            for appid, value in list(MP.items()):
                if value["sid"] == detached:
                    MP.pop(appid, None)
            if WAKE:
                WAKE.set()

    @staticmethod
    def _remote_value(value):
        if not isinstance(value, dict):
            return value
        if "value" in value:
            return value["value"]
        if "description" in value:
            return value["description"]
        return value.get("type", "")

    @classmethod
    def _emit_web_event(cls, appid, method, params):
        """Persist native web CDP events in the same bounded event stream."""
        now = time.time()
        if method == "Runtime.consoleAPICalled":
            args = [cls._remote_value(arg) for arg in params.get("args", [])[:16]]
            emit({"t": now, "appid": appid, "type": "console",
                  "data": {"level": params.get("type", "log"), "args": args}})
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            exception = details.get("exception") or {}
            message = (exception.get("description") or exception.get("value") or
                       details.get("text") or "Uncaught exception")
            location = details.get("url")
            if details.get("lineNumber") is not None:
                location = f"{location or ''}:{details['lineNumber'] + 1}"
            emit({"t": now, "appid": appid, "type": "console",
                  "data": {"level": "error", "args": [message],
                           "location": location}})
        elif method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            emit({"t": now, "appid": appid, "type": "request",
                  "data": {"id": params.get("requestId"), "url": request.get("url", ""),
                           "method": request.get("method", "GET"),
                           "header": request.get("headers") or {},
                           "data": request.get("postData")}})
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            emit({"t": now, "appid": appid, "type": "response",
                  "data": {"id": params.get("requestId"), "url": response.get("url", ""),
                           "status": response.get("status"),
                           "header": response.get("headers") or {}}})
        elif method == "Network.loadingFailed":
            emit({"t": now, "appid": appid, "type": "fail",
                  "data": {"id": params.get("requestId"),
                           "err": params.get("errorText", "network request failed")}})
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
        try:
            await self.ws.send(json.dumps(m))
            r = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"_err": "timeout"}
        finally:
            self.wait.pop(mid, None)
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


async def is_service_context(br, sid, context_id, appid):
    # A web page has no mini-program `wx` service API. Its first runtime
    # context is still a valid CDP target for evaluate and debugger calls.
    if str(appid).startswith("web:"):
        return True
    if os.environ.get("WECHAT_DEBUG_MODE") != "direct":
        return await eval_js(br, sid, context_id, "typeof wx", timeout=3) == "object"
    expression = ("(function(){try{return typeof wx==='object' && typeof getApp==='function' && "
                  "wx.getAccountInfoSync().miniProgram.appId===" + json.dumps(appid) +
                  "}catch(e){return false}})()")
    return await eval_js(br, sid, context_id, expression, timeout=3) is True


DUMP_STORAGE_JS = (
    "(function(){try{var i=wx.getStorageInfoSync();var kv={};"
    "(i.keys||[]).slice(0,120).forEach(function(k){var v;"
    "try{v=JSON.stringify(wx.getStorageSync(k))}catch(e){v=String(v)}"
    "kv[k]=(v||'').slice(0,2000)});"
    "return JSON.stringify({keys:i.keys||[],sz:i.currentSize,"
    "limit:i.limitSize,kv:kv})}catch(e){"
    "return JSON.stringify({err:String(e)})}})()")
with open(os.path.join(ROOT, "scripts", "wx_storage_snapshot.js"), encoding="utf-8") as _snapshot:
    CDP_STORAGE_JS = _snapshot.read()


def retry_later(appid, target_id, sid, reason):
    attempts = FAILED_CTX.get(target_id, {}).get("attempts", 0) + 1
    delay = min(2 ** min(attempts, 5), 30)
    FAILED_CTX[target_id] = {"attempts": attempts, "next_retry": time.time() + delay,
                             "sid": sid, "reason": reason}
    DIAGNOSTICS[appid] = {"phase": "retrying", "since": time.time(), "reason": reason}


def app_status(appid, target_id=None):
    now = time.time()
    value = MP.get(appid, {})
    diag = DIAGNOSTICS.get(appid, {})
    retry = FAILED_CTX.get(target_id, {})
    is_web = str(appid).startswith("web:")
    online = (is_web and bool(value)) or ONLINE.get(appid, 0) > now - 30
    phase = "online" if online else diag.get("phase", "discovered")
    if value and not online:
        phase = "connecting" if now - diag.get("since", now) < 15 else "offline"
    return {**diag, "phase": phase, "agent_online": online,
            "kind": "web" if is_web else "mini",
            "context_id": value.get("ctx"),
            "agent_version": HUB_VERSION.get(appid) or value.get("agent_version"),
            "last_event_at": LAST_EVENT.get(appid), "heartbeat_at": ONLINE.get(appid),
            "retry_in": max(0, round(retry.get("next_retry", 0) - now, 1)),
            "attempts": retry.get("attempts", 0),
            "capture_legacy": value.get("capture_legacy", []),
            "debugger": value.get("dbg", False), "scripts": list(value.get("scripts", {}))[:20],
            "paused": value.get("paused"), "bps": list(value.get("bps", {}))}


def target_key(target):
    """Return the dashboard/session key for a target without guessing H5 appids."""
    match = re.search(r"(wx[0-9a-f]{16})", target.get("url", ""))
    return match.group(1) if match else "web:" + str(target.get("targetId", ""))


def target_descriptor(target):
    key = target_key(target)
    is_web = key.startswith("web:")
    return {"appid": key, "kind": "web" if is_web else "mini",
            "title": target.get("title") or ("网页" if is_web else key),
            "url": target.get("url", "")[:240],
            "targetId": target.get("targetId")}


async def setup_targets(br):
    r = await br.cmd("Target.getTargets", timeout=6)
    if not isinstance(r, dict) or "_err" in r or "targetInfos" not in r:
        raise ConnectionError("bridge getTargets failed — reconnect")
    global TARGETS_CACHE
    TARGETS_CACHE = r.get("targetInfos", [])
    pages = [t for t in TARGETS_CACHE
             if t.get("type") == "page" and t.get("url", "").startswith("http")]
    seen = set()
    for t in pages:
        am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
        appid = am.group(1) if am else "web:" + str(t.get("targetId", ""))
        is_web = not bool(am)
        seen.add(appid)
        if appid in MP:
            if MP[appid].get("targetId") == t["targetId"]:
                value = MP[appid]
                if not value.pop("reconnect_requested", False):
                    continue
                if is_web:
                    MP.pop(appid, None)
                    SESSION_APPS.pop(value["sid"], None)
                    await br.cmd("Target.detachFromTarget",
                                 sessionId=value["sid"], timeout=2)
                    FAILED_CTX.pop(t["targetId"], None)
                else:
                    connected = await eval_js(
                        br, value["sid"], value["ctx"],
                        "(function(){if(typeof __REA__==='object' && __REA__.reconnect){"
                        "__REA__.reconnect();return true}return false})()", timeout=4)
                    if connected:
                        DIAGNOSTICS[appid] = {"phase": "connecting", "since": time.time()}
                        continue
                    MP.pop(appid, None)
                    FAILED_CTX[t["targetId"]] = {"sid": value["sid"], "next_retry": 0,
                                                 "attempts": 0}
            else:
                previous = MP.pop(appid)
                SESSION_APPS.pop(previous["sid"], None)
                await br.cmd("Target.detachFromTarget", sessionId=previous["sid"], timeout=2)
        retry = FAILED_CTX.get(t["targetId"], {})
        if time.time() < retry.get("next_retry", 0):
            continue
        DIAGNOSTICS[appid] = {"phase": "attaching", "since": time.time()}
        sid = retry.get("sid")
        if not sid:
            a = await br.cmd("Target.attachToTarget", targetId=t["targetId"],
                             flatten=True, timeout=6)
            sid = a.get("sessionId") if isinstance(a, dict) else None
        if not sid:
            retry_later(appid, t["targetId"], None, "无法附加目标")
            continue
        if is_web:
            SESSION_APPS[sid] = appid
        else:
            SESSION_APPS.pop(sid, None)
        DIAGNOSTICS[appid] = {"phase": "waiting_context", "since": time.time()}
        svc = None
        invalid_session = False
        # Context-created notifications can arrive after the enable response
        # on 4.1.13. Give the target a second short window before marking it
        # failed for the full cooldown period.
        for attempt in range(2):
            if attempt and os.environ.get("WECHAT_DEBUG_MODE") == "direct":
                await br.cmd("Runtime.disable", sid, timeout=3)
            result = await br.cmd("Runtime.enable", sid, timeout=5)
            if isinstance(result, dict) and ("code" in result or "_err" in result):
                invalid_session = True
                break
            cached = br.contexts.get(sid, {}) if isinstance(br.contexts, dict) else {}
            evs = [] if cached else await br.drain(1.0 if attempt == 0 else 0.8)
            ctxs = [e["params"]["context"] for e in evs
                    if e.get("method") == "Runtime.executionContextCreated"
                    and e.get("sessionId") == sid
                    and e.get("params", {}).get("context")]
            ctxs += [context for context in cached.values() if context not in ctxs]
            for c in ctxs:
                if await is_service_context(br, sid, c.get("id"), appid):
                    svc = c.get("id")
                    break
            if svc is not None:
                break
        if svc is None:
            if invalid_session:
                SESSION_APPS.pop(sid, None)
            print(f"[sess] {appid}: no runtime ctx", flush=True)
            retry_later(appid, t["targetId"], None if invalid_session else sid,
                        "附加会话已失效" if invalid_session else "等待 runtime 上下文")
            continue
        if is_web:
            MP[appid] = {"appid": appid, "kind": "web", "sid": sid,
                         "ctx": svc, "targetId": t["targetId"], "scripts": {},
                         "paused": None, "topFrame": None, "bps": {}, "fails": 0,
                         "dbg": False, "agent_version": None, "capture_legacy": [],
                         "title": t.get("title") or "网页", "url": t.get("url", "")}
            FAILED_CTX.pop(t["targetId"], None)
            network = await br.cmd("Network.enable", sid, timeout=5)
            network_error = None
            if isinstance(network, dict) and any(
                    key in network for key in ("_err", "code", "error")):
                network_error = str(network.get("message") or
                                     network.get("_err") or
                                     network.get("error") or network)
            DIAGNOSTICS[appid] = {"phase": "online", "since": time.time(),
                                  **({"reason": "Network unavailable: " + network_error}
                                     if network_error else {})}
            emit({"t": time.time(), "appid": appid, "type": "attached",
                  "data": {"debugger": False, "kind": "web"}})
            print(f"[sess] web target attached -> {appid}", flush=True)
            continue
        # Re-evaluate the agent (idempotent: its IIFE early-returns
        # if present). Page reloads after bridge restarts create fresh
        # contexts with no instrumentation — this guarantees coverage.
        with open(os.path.join(ROOT, "scripts", "rea_agent.js")) as f:
            agent_src = f.read()
        chk = await eval_js(br, sid, svc, "typeof __REA__", timeout=4)
        DIAGNOSTICS[appid] = {"phase": "injecting", "since": time.time()}
        agent_src = "globalThis.__REA_CAPTURE__=" + json.dumps(load_config()) + ";\n" + agent_src
        version = await eval_js(br, sid, svc,
                               agent_src + "\n;globalThis.__REA__.version\n//# sourceURL=rea://agent.js", timeout=9)
        if not isinstance(version, (int, float)) or version < 10:
            print(f"[sess] agent injection unconfirmed -> {appid}", flush=True)
            retry_later(appid, t["targetId"], sid, "agent 注入尚未确认")
            continue
        legacy = await eval_js(br, sid, svc, "globalThis.__REA__.legacyHooks || []", timeout=3)
        print(f"[sess] agent ensured ({chk}) -> {appid}", flush=True)
        # NOTE: Debugger.enable stays OFF by default — enabling it on the
        # service context stalls live event capture (observed 2026-10-19).
        # It is enabled on demand by the first /breakpoint request.
        MP[appid] = {"appid": appid, "sid": sid, "ctx": svc, "targetId": t["targetId"],
                     "scripts": {}, "paused": None,
                     "topFrame": None, "bps": {}, "fails": 0,
                     "dbg": False, "agent_version": version,
                     "capture_legacy": legacy if isinstance(legacy, list) else []}
        FAILED_CTX.pop(t["targetId"], None)
        DIAGNOSTICS[appid] = {"phase": "connecting", "since": time.time()}
        emit({"t": time.time(), "appid": appid, "type": "attached",
              "data": {"debugger": False}})
    for a in [a for a in MP if a not in seen]:
        print(f"[sess] {a} gone", flush=True)
        previous = MP.pop(a)
        SESSION_APPS.pop(previous["sid"], None)
        await br.cmd("Target.detachFromTarget", sessionId=previous["sid"], timeout=2)
    active_ids = {t["targetId"] for t in pages}
    for target_id in list(FAILED_CTX):
        if target_id not in active_ids:
            sid = FAILED_CTX.pop(target_id).get("sid")
            if sid:
                SESSION_APPS.pop(sid, None)
                await br.cmd("Target.detachFromTarget", sessionId=sid, timeout=2)


async def refresh_ctx(br, v):
    """Re-discover the service context (contexts change on page
    recreation; stale contextId makes every evaluate silently fail)."""
    if os.environ.get("WECHAT_DEBUG_MODE") == "direct":
        await br.cmd("Runtime.disable", v["sid"], timeout=3)
    await br.cmd("Runtime.enable", v["sid"], timeout=5)
    evs = await br.drain(1.0)
    ctxs = [e["params"]["context"] for e in evs
            if e.get("method") == "Runtime.executionContextCreated"
            and e.get("sessionId") == v["sid"]]
    cached = br.contexts.get(v["sid"], {}) if isinstance(br.contexts, dict) else {}
    ctxs += [context for context in cached.values() if context not in ctxs]
    for c in ctxs:
        if await is_service_context(br, v["sid"], c.get("id"), v.get("appid")):
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
                emit_agent(appid, e)
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
        for _ in range(5):          # scriptParsed can take seconds
            if v["scripts"]:
                break
            await asyncio.sleep(0.8)
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
        if req.get("path") == "/storage":
            expression = CDP_STORAGE_JS if req.get("format") == "cdp" else DUMP_STORAGE_JS
            raw = await eval_js(br, v["sid"], v["ctx"], expression, timeout=6)
            data = json.loads(raw) if isinstance(raw, str) else {"err": "上下文不可用"}
            return {"appid": appid, "data": data}
        if req.get("path") == "/breakpoint":
            line = int(req.get("line", 0))
            f = req.get("file", "app-service.js")
            url = ("https://usr/" + f) if "/" not in f else f
            v.setdefault("bps", {})
            key = f"{f}:{line}"
            if req.get("remove"):
                bpid = v["bps"].get(key)
                if not bpid:
                    return {"ok": False, "error": "no such breakpoint"}
                r = await br.cmd("Debugger.removeBreakpoint", v["sid"],
                                 breakpointId=bpid, timeout=8)
                if not isinstance(r, dict) or any(k in r for k in ("_err", "code", "error")):
                    return {"ok": False, "error": "移除断点未确认，请重试", "raw": r}
                v["bps"].pop(key, None)
                return {"ok": True, "removed": bpid}
            if key in v["bps"]:
                return {"ok": True, "breakpointId": v["bps"][key]}
            if not await ensure_debugger(br, v):
                return {"ok": False,
                        "error": "Debugger.enable failed (bridge)"}
            r = await br.cmd("Debugger.setBreakpointByUrl", v["sid"],
                             lineNumber=max(0, line - 1), url=url,
                             columnNumber=0, timeout=8)
            bpid = (r or {}).get("breakpointId")
            if bpid:
                v["bps"][key] = bpid
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
        if req.get("path") == "/fetch_source":
            if not await ensure_debugger(br, v):
                return {"error": "Debugger.enable failed"}
            for _ in range(6):
                if v["scripts"]:
                    break
                await asyncio.sleep(0.8)
            srcdir = f"/tmp/wechat_rea/src_{appid}"
            os.makedirs(srcdir, exist_ok=True)
            saved = []
            for surl, sid_script in list(v["scripts"].items())[:8]:
                fname = surl.replace("https://", "").replace("usr/", "") \
                            .replace("/", "_") or "script.js"
                try:
                    r3 = await br.cmd("Debugger.getScriptSource", v["sid"],
                                      scriptId=sid_script, timeout=25)
                    body = (r3 or {}).get("scriptSource") \
                        if isinstance(r3, dict) else None
                    if body:
                        with open(os.path.join(srcdir, fname), "w",
                                  encoding="utf-8") as f:
                            f.write(body)
                        saved.append(fname)
                except Exception:
                    pass
            return {"ok": True, "saved": saved}

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
    """main.py /api/eval and /api/storage file channels."""
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
        if req["path"] in ("/capture_settings", "/retry"):
            future = asyncio.run_coroutine_threadsafe(local_control(req), _loop)
            try:
                self._json(future.result(timeout=5))
            except Exception as exc:
                self._json({"error": str(exc)})
            return
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
            page_targets = [t for t in TARGETS_CACHE if t.get("type") == "page"
                            and t.get("url", "").startswith("http")]
            descriptors = [target_descriptor(t) for t in page_targets]
            self._json({"bridge_state": BRIDGE["state"],
                        "bridge_port": BRIDGE["port"],
                        "debug_mode": os.environ.get("WECHAT_DEBUG_MODE", "bridge"),
                        "targetInfos": list(TARGETS_CACHE),
                        "targets": descriptors,
                        "mps": {d["appid"]: app_status(d["appid"], d["targetId"])
                                for d in descriptors},
                        "capture_settings": load_config()})
        else:
            self._json({})


async def local_control(req):
    if req["path"] == "/retry":
        appid = req.get("appid")
        if not (re.fullmatch(r"wx[0-9a-f]{16}", appid or "") or
                re.fullmatch(r"web:[A-Za-z0-9_.:-]+", appid or "")):
            return {"error": "无效 target"}
        if appid in MP:
            MP[appid]["reconnect_requested"] = True
        for target in TARGETS_CACHE:
            if target_key(target) == appid:
                retry = FAILED_CTX.get(target.get("targetId"))
                if retry:
                    retry["next_retry"] = 0
        if WAKE:
            WAKE.set()
        return {"ok": True}
    try:
        config = save_config(req.get("settings"))
    except ValueError as exc:
        return {"error": str(exc)}
    sockets = list(HUB_SOCKET.values())
    await asyncio.gather(*(ws.send(json.dumps({"config": config})) for ws in sockets),
                         return_exceptions=True)
    return {"ok": True, "settings": config}


async def agent_hub(ws):
    """Direct agent connections (wx.connectSocket) — bridge-independent
    event push. This is the primary capture path; bridge polling is only
    a fallback."""
    m = re.match(r"/agent\?appid=(wx[0-9a-f]{16})(?:&v=(\d+))?",
                 ws.path or "")
    if not m:
        await ws.close()
        return
    appid = m.group(1)
    version = int(m.group(2) or 0)
    # Older agents can keep reconnect timers alive after an upgrade. Drain
    # their sockets quietly; closing them would trigger another retry loop.
    if version < HUB_VERSION.get(appid, 0):
        async for _ in ws:
            pass
        return
    HUB_VERSION[appid] = version
    HUB_SOCKET[appid] = ws
    ONLINE[appid] = time.time()
    print(f"[hub] agent online: {appid}", flush=True)
    emit({"t": time.time(), "appid": appid, "type": "agent_online",
          "data": {}})
    try:
        await ws.send(json.dumps({"config": load_config()}))
        async for raw in ws:
            if HUB_SOCKET.get(appid) is not ws:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            ONLINE[appid] = time.time()
            if msg.get("hb"):
                continue
            for e in msg.get("events", [])[:500]:
                if isinstance(e, dict):
                    emit_agent(appid, e)
    finally:
        if HUB_SOCKET.get(appid) is ws:
            HUB_SOCKET.pop(appid, None)
            print(f"[hub] agent offline: {appid}", flush=True)
            ONLINE[appid] = 0


async def run_session(port):
    global WAKE
    WAKE = asyncio.Event()
    BRIDGE["state"], BRIDGE["port"] = "connecting", port
    async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=3,
                                  max_size=2**25) as ws:
        br = Br(ws)
        await br.start()
        ctrl_task = asyncio.create_task(ctrl_loop(br))
        last_poll = 0.0
        try:
            while True:
                await setup_targets(br)
                BRIDGE["state"] = "up"
                if not MP:
                    await asyncio.sleep(1.5)
                    continue
                # Discovery uses this persistent connection. Keep fallback
                # Runtime.evaluate polling at eight seconds independently.
                try:
                    await asyncio.wait_for(WAKE.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                WAKE.clear()
                await legacy_file_requests(br)
                if time.monotonic() - last_poll < 8:
                    continue
                last_poll = time.monotonic()
                for appid in list(MP):
                    if MP[appid].get("kind") == "web":
                        continue
                    if ONLINE.get(appid, 0) > time.time() - 30:
                        continue
                    ok = await poll_agent(br, appid)
                    if not ok:
                        print(f"[sess] drop {appid}", flush=True)
                        del MP[appid]
        finally:
            ctrl_task.cancel()
            BRIDGE["state"] = "dead"
            # Session ids belong to this bridge WebSocket. Never carry stale
            # ids into the next connection, or reinjection is suppressed.
            MP.clear()
            SESSION_APPS.clear()
            TARGETS_CACHE.clear()
            FAILED_CTX.clear()


async def main():
    global _loop, _ctrl_queue
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    # single-instance lock (main.py respawns; duplicates fight for :8778)
    lock = open("/tmp/wechat_rea/sess.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[sess] another instance holds the lock, exiting", flush=True)
        return
    # Retain bounded history across daemon restarts; append_event rotates it.
    open(EVENTS, "a").close()
    print("[sess] service session daemon v3.1 started", flush=True)

    _loop = asyncio.get_event_loop()
    _ctrl_queue = asyncio.Queue()

    server = ThreadingHTTPServer(("127.0.0.1", CTRL_PORT), CtrlHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    await websockets.serve(agent_hub, "127.0.0.1", 8779,
                           max_size=2**24)

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
                # rotate on ANY failure type after 2 tries — wrong ports
                # refuse (RefusedError), reject (500), or hang (timeout)
                refused += 1
                if refused >= 2 and len(cands) > 1:
                    cand_idx += 1
                    refused = 0
                    print(f"[sess] rotating to port "
                          f"{cands[cand_idx % len(cands)]} "
                          f"(after {type(exc).__name__})", flush=True)
                print(f"[sess] session ended: {type(exc).__name__} "
                      f"{str(exc)[:80]}", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
