#!/usr/bin/env python3
"""Persistent mini-program service-layer debug session.

Connects to the WeChat remote-debug bridge, finds each mini-program's
SERVICE context (the page-frame target context where `typeof wx ===
'object'`), injects scripts/rea_agent.js, then continuously:

  - drains __REA__.events (requests / responses / console / route /
    storage changes / errors)
  - serves eval requests  (/tmp/wechat_rea/service_eval)
  - serves storage dumps  (/tmp/wechat_rea/service_storage_req)

Writes JSON lines to /tmp/wechat_rea/service_events.jsonl.
No bridge (真机调试 not active) => idles and retries with backoff.
"""
import asyncio
import glob
import json
import os
import re
import time

import websockets

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
EVENTS = "/tmp/wechat_rea/service_events.jsonl"
EVAL_FILE = "/tmp/wechat_rea/service_eval"
STORAGE_REQ = "/tmp/wechat_rea/service_storage_req"
STORAGE_RESP = "/tmp/wechat_rea/service_storage_resp"
ROOT = os.path.dirname(os.path.abspath(__file__))

_last_seen = {}   # appid -> (sessionId, ctxId)  (current connection only)
_eval_seq = 0


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
                    f = self.wait.pop(m["id"])
                    if not f.done():
                        f.set_result(m)
                else:
                    self.buf.append(m)
        except Exception:
            pass

    async def drain(self, secs):
        await asyncio.sleep(secs)
        out, self.buf = self.buf, []
        return out

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


async def eval_js(br, sid, ctx, expr, timeout=8):
    ev = await br.cmd("Runtime.evaluate", sid, timeout=timeout,
                      expression=expr, contextId=ctx,
                      returnByValue=True, silent=True)
    if isinstance(ev, dict) and "_err" in ev:
        return None
    if isinstance(ev, dict) and "result" in ev:
        return ev["result"].get("value")
    return None


async def find_service_ctx(br, sid):
    await br.cmd("Runtime.enable", sid)
    evs = await br.drain(1.2)
    ctxs = [e["params"]["context"] for e in evs
            if e.get("method") == "Runtime.executionContextCreated"]
    for c in ctxs:
        v = await eval_js(br, sid, c.get("id"), "typeof wx")
        if v == "object":
            return c.get("id")
    return None


DUMP_STORAGE_JS = (
    "(function(){try{var i=wx.getStorageInfoSync();var kv={};"
    "(i.keys||[]).slice(0,120).forEach(function(k){var v;"
    "try{v=JSON.stringify(wx.getStorageSync(k))}catch(e){v=String(v)}"
    "kv[k]=(v||'').slice(0,2000)});"
    "return JSON.stringify({keys:i.keys||[],sz:i.currentSize,"
    "limit:i.limitSize,kv:kv})}catch(e){"
    "return JSON.stringify({err:String(e)})}})()")


async def serve_file_requests(br):
    """Handle eval + storage request files against live contexts."""
    global _eval_seq
    if not _last_seen:
        return
    # eval
    if os.path.exists(EVAL_FILE):
        try:
            code = open(EVAL_FILE).read().strip()
            os.remove(EVAL_FILE)
        except OSError:
            code = None
        if code:
            _eval_seq += 1
            wrapped = ("(function(){try{var r=(" + code +
                       ");r=(r===undefined?'undefined':r);"
                       "return JSON.stringify({ok:true,r:r})}"
                       "catch(e){return JSON.stringify({ok:false,"
                       "e:String(e)})}})()")
            for appid, (sid, ctx) in list(_last_seen.items()):
                res = await eval_js(br, sid, ctx, wrapped, timeout=10)
                with open(EVENTS, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "t": time.time(), "appid": appid,
                        "type": "eval_result", "seq": _eval_seq,
                        "data": res}, ensure_ascii=False) + "\n")
                return  # first live appid answers
    # storage dump
    if os.path.exists(STORAGE_REQ):
        try:
            want = open(STORAGE_REQ).read().strip()
            os.remove(STORAGE_REQ)
        except OSError:
            want = None
        if want is not None:
            for appid, (sid, ctx) in list(_last_seen.items()):
                if want and appid != want:
                    continue
                res = await eval_js(br, sid, ctx, DUMP_STORAGE_JS,
                                    timeout=10)
                try:
                    with open(STORAGE_RESP, "w", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "appid": appid, "data": json.loads(res)
                            if isinstance(res, str) else None}))
                except (OSError, ValueError):
                    pass
                return


async def run_session(port, log=None):
    agent = open(os.path.join(ROOT, "scripts", "rea_agent.js")).read()
    async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=3,
                                  max_size=2**23) as ws:
        br = Br(ws)
        await br.start()
        r = await br.cmd("Target.getTargets")
        pages = [t for t in r.get("targetInfos", [])
                 if t["type"] == "page" and "servicewechat.com/wx" in
                 t.get("url", "")]
        _last_seen.clear()
        for t in pages:
            am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
            appid = am.group(1) if am else "?"
            a = await br.cmd("Target.attachToTarget", targetId=t["targetId"],
                             flatten=True)
            sid = a.get("sessionId") if isinstance(a, dict) else None
            if not sid:
                continue
            ctx = await find_service_ctx(br, sid)
            if ctx is None:
                print(f"[sess] {appid}: no service ctx", flush=True)
                continue
            has = await eval_js(br, sid, ctx, "typeof __REA__")
            if has != "object":
                await eval_js(br, sid, ctx, agent, timeout=12)
                print(f"[sess] agent injected -> {appid}", flush=True)
            _last_seen[appid] = (sid, ctx)

        if not _last_seen:
            print("[sess] no service contexts found", flush=True)
            await asyncio.sleep(4)   # back off — don't hammer the bridge
            return

        while True:
            await asyncio.sleep(1.0)
            await serve_file_requests(br)
            dead = []
            for appid, (sid, ctx) in _last_seen.items():
                try:
                    raw = await eval_js(
                        br, sid, ctx,
                        "(function(){if(typeof __REA__!=='object')"
                        "return 'DEAD';if(!__REA__.drain)return 'V1';"
                        "return JSON.stringify(__REA__.drain(200))})()")
                    if raw == "DEAD":
                        dead.append(appid)
                        continue
                    if raw == "V1":
                        # legacy v1 agent (no drain) — poll by slice
                        raw = await eval_js(
                            br, sid, ctx,
                            "JSON.stringify(__REA__.events.splice(0,200))")
                    events = json.loads(raw) if isinstance(raw, str) else []
                    if not isinstance(events, list):
                        events = []
                    for e in events:
                        if not isinstance(e, dict):
                            continue
                        with open(EVENTS, "a", encoding="utf-8") as f:
                            f.write(json.dumps(
                                {"t": time.time(), "appid": appid, **e},
                                ensure_ascii=False) + "\n")
                        d = e.get("data") if isinstance(e.get("data"), dict) \
                            else {}
                        head = (d.get("url") or d.get("route") or
                                d.get("level") or d.get("msg") or
                                d.get("op") or d.get("err") or "")
                        tail = str(d.get("status") or d.get("method") or
                                   d.get("key") or "")[:40]
                        print(f"[{appid[:10]}] {str(e.get('type')):9} "
                              f"{str(head)[:80]} {tail}", flush=True)
                except Exception as exc:
                    print(f"[sess] poll {appid}: {type(exc).__name__} "
                          f"{str(exc)[:60]}", flush=True)
            for appid in dead:
                print(f"[sess] {appid} context gone", flush=True)
                del _last_seen[appid]
            if not _last_seen:
                return  # rescan


async def main():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    print("[sess] service session daemon v2 started", flush=True)
    while True:
        port = find_port()
        if port:
            try:
                await run_session(port)
            except Exception as exc:
                print(f"[sess] session ended: {type(exc).__name__} "
                      f"{str(exc)[:80]}", flush=True)
        await asyncio.sleep(3)  # churn backoff


if __name__ == "__main__":
    asyncio.run(main())
