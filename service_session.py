#!/usr/bin/env python3
"""Persistent mini-program service-layer debug session.

Connects to the WeChat remote-debug bridge, finds each mini-program's
SERVICE context (the page-frame target's context where `typeof wx ===
'object'`), injects scripts/rea_agent.js, then continuously:

  - polls __REA__.events (wx.request / responses / console / errors)
  - appends them to /tmp/wechat_rea/service_events.jsonl
  - exposes an eval endpoint file /tmp/wechat_rea/service_eval (write JS,
    result appears in service_events with type 'eval_result')

No bridge (真机调试 not active) => idles and retries.
"""
import asyncio
import glob
import json
import os
import re
import sys
import time

import websockets

LOGDIR = ("/Users/yangyiming/Library/Application Support/微信开发者工具/"
          "d1e8765721a6c23d43b14c95b1843e6b/WeappLog/logs")
EVENTS = "/tmp/wechat_rea/service_events.jsonl"
EVAL_FILE = "/tmp/wechat_rea/service_eval"
ROOT = os.path.dirname(os.path.abspath(__file__))

_last_seen = {}   # appid -> (targetId, ctxId, lastEventCount)
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


async def eval_js(br, sid, ctx, expr, timeout=6):
    ev = await br.cmd("Runtime.evaluate", sid, timeout=timeout,
                      expression=expr, contextId=ctx,
                      returnByValue=True, silent=True)
    if isinstance(ev, dict) and "_err" in ev:
        return None
    if isinstance(ev, dict) and "result" in ev:
        return ev["result"].get("value")
    return None


async def service_ctx(br, sid):
    await br.cmd("Runtime.enable", sid)
    evs = await br.drain(1.2)
    ctxs = [e["params"]["context"] for e in evs
            if e.get("method") == "Runtime.executionContextCreated"]
    for c in ctxs:
        v = await eval_js(br, sid, c.get("id"), "typeof wx")
        if v == "object":
            return c.get("id")
    return None


async def run_session(port, log):
    agent = open(os.path.join(ROOT, "scripts", "rea_agent.js")).read()
    async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=3,
                                  max_size=2**23) as ws:
        br = Br(ws)
        await br.start()
        r = await br.cmd("Target.getTargets")
        pages = [t for t in r.get("targetInfos", [])
                 if t["type"] == "page" and "servicewechat.com/wx" in
                 t.get("url", "")]
        for t in pages:
            am = re.search(r"(wx[0-9a-f]{16})", t.get("url", ""))
            appid = am.group(1) if am else "?"
            a = await br.cmd("Target.attachToTarget", targetId=t["targetId"],
                             flatten=True)
            sid = a.get("sessionId") if isinstance(a, dict) else None
            if not sid:
                continue
            ctx = await service_ctx(br, sid)
            if ctx is None:
                print(f"[sess] {appid}: no service ctx", flush=True)
                continue
            has = await eval_js(br, sid, ctx, "typeof __REA__")
            if has != "object":
                await eval_js(br, sid, ctx, agent, timeout=10)
                print(f"[sess] agent injected -> {appid}", flush=True)
            else:
                print(f"[sess] agent already present in {appid}", flush=True)
            _last_seen[appid] = (sid, ctx, 0)

        if not _last_seen:
            print("[sess] no service contexts found", flush=True)
            await asyncio.sleep(4)   # back off — don't hammer the bridge
            return

        last_poll = time.time()
        while True:
            await asyncio.sleep(1.0)
            # eval endpoint (wait until contexts are registered)
            global _eval_seq
            if _last_seen and os.path.exists(EVAL_FILE):
                try:
                    code = open(EVAL_FILE).read().strip()
                    os.remove(EVAL_FILE)
                except OSError:
                    code = None
                if code:
                    _eval_seq += 1
                    # wx sandbox blocks eval/Function — but direct
                    # expression execution via Runtime.evaluate works.
                    wrapped = (
                        "(function(){try{var r=(" + code +
                        ");return JSON.stringify({ok:true,"
                        "r:(r===undefined?'undefined':r)})}"
                        "catch(e){return JSON.stringify({ok:false,"
                        "e:String(e)})}})()")
                    for appid, (sid, ctx, _) in _last_seen.items():
                        res = await eval_js(br, sid, ctx, wrapped, timeout=10)
                        log.write(json.dumps({
                            "t": time.time(), "appid": appid,
                            "type": "eval_result", "seq": _eval_seq,
                            "data": res}, ensure_ascii=False) + "\n")
                        print(f"[eval#{_eval_seq}] {str(res)[:200]}", flush=True)

            if time.time() - last_poll < 2.0:
                continue
            last_poll = time.time()
            for appid in list(_last_seen):
                try:
                    sid, ctx, mark = _last_seen[appid]
                    raw = await eval_js(
                        br, sid, ctx,
                        "(typeof __REA__==='object') ? __REA__.events.length : -1")
                    if raw is None or raw == -1:
                        # context died (mini-program closed) — drop
                        del _last_seen[appid]
                        print(f"[sess] {appid} context gone", flush=True)
                        return  # rescan
                    if not isinstance(raw, int) or raw <= mark:
                        continue
                    new = await eval_js(
                        br, sid, ctx,
                        f"JSON.stringify(__REA__.events.slice({mark}))",
                        timeout=10)
                    try:
                        events = json.loads(new) if isinstance(new, str) else []
                        if not isinstance(events, list):
                            events = []
                    except ValueError:
                        events = []
                    for e in events:
                        if not isinstance(e, dict):
                            continue
                        log.write(json.dumps(
                            {"t": time.time(), "appid": appid, **e},
                            ensure_ascii=False) + "\n")
                        d = e.get("data")
                        if not isinstance(d, dict):
                            d = {}
                        head = (d.get("url") or d.get("level") or
                                d.get("msg") or d.get("err") or "")
                        tail = str(d.get("status") or d.get("method") or "")[:40]
                        print(f"[{appid[:10]}] {str(e.get('type')):9} "
                              f"{str(head)[:80]} {tail}", flush=True)
                    _last_seen[appid] = (sid, ctx, raw)
                except Exception as exc:
                    print(f"[sess] poll {appid}: {type(exc).__name__} "
                          f"{str(exc)[:60]}", flush=True)


async def main():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    log = open(EVENTS, "a", buffering=1)
    print("[sess] service session daemon started", flush=True)
    while True:
        port = find_port()
        if port:
            try:
                await run_session(port, log)
            except Exception as exc:
                print(f"[sess] session ended: {type(exc).__name__} "
                      f"{str(exc)[:80]}", flush=True)
            await asyncio.sleep(3)  # session churn backoff
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
