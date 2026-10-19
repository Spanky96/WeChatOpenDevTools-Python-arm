#!/usr/bin/env python3
"""Cronet-precise network capture daemon for WeChat mini-programs.

2026-10-18 discovery: WeChat main (and WeChatAppEx) load mmcronet with the
full Cronet C API exported. wx.request flows through Cronet, so hooking
the C API captures plaintext URLs, methods, headers and response codes —
no TLS decryption needed.

Hook points (all low-frequency, request-scoped — safe, unlike write()):
  Cronet_UrlRequest_InitWithParams   → url + params join point
  Cronet_UrlRequestParams_http_method_set
  Cronet_UrlRequestParams_request_headers_add (params, name, value)
  Cronet_UrlRequest_Start
  Cronet_UrlRequestCallback_OnResponseStarted → status + url

Attaches to: WeChat main, WeChatAppEx main, AppEx NetworkService.
Logs JSON lines to /tmp/wechat_rea/net_capture.jsonl
"""
import json
import os
import subprocess
import sys
import time

import frida

LOGFILE = "/tmp/wechat_rea/net_capture.jsonl"

HOOK = r"""
'use strict';

var G = {
  params: new Map(),      // params ptr -> {method, headers[]}
  flushed: new Map(),     // request ptr -> record
  reqCounter: 0,
  hooks: 0,
};

function emit(obj) {
  obj.t = Date.now();
  send(JSON.stringify(obj));
}

function hookModule(mmcronet) {
  function ex(name) {
    try { return mmcronet.getExportByName(name); } catch (e) { return null; }
  }

  var initFn = ex('Cronet_UrlRequest_InitWithParams');
  var methodSet = ex('Cronet_UrlRequestParams_http_method_set');
  var headerAdd = ex('Cronet_UrlRequestParams_request_headers_add');
  var startFn = ex('Cronet_UrlRequest_Start');
  var onResp = ex('Cronet_UrlRequestCallback_OnResponseStarted');
  var urlGet = ex('Cronet_UrlResponseInfo_url_get');
  var statusGet = ex('Cronet_UrlResponseInfo_http_status_code_get');

  if (initFn) {
    Interceptor.attach(initFn, {
      onEnter: function (args) {
        // (self, engine, url: const char*, request: params, callback)
        try {
          var url = args[2].readCString();
          var paramsPtr = args[3].toString();
          var meta = G.params.get(paramsPtr) || {method: 'GET', headers: []};
          G.reqCounter++;
          var rec = {
            ev: 'request',
            id: G.reqCounter,
            url: url || '',
            method: meta.method,
            headers: meta.headers.slice(0, 40),
          };
          G.flushed.set(args[0].toString(), rec);
          emit(rec);
          G.params.delete(paramsPtr);
        } catch (e) {}
      }
    });
    G.hooks++;
  }

  if (methodSet) {
    Interceptor.attach(methodSet, {
      onEnter: function (args) {
        try {
          var p = G.params.get(args[0].toString()) ||
                  {method: 'GET', headers: []};
          p.method = args[1].readCString() || 'GET';
          G.params.set(args[0].toString(), p);
        } catch (e) {}
      }
    });
    G.hooks++;
  }

  if (headerAdd) {
    Interceptor.attach(headerAdd, {
      onEnter: function (args) {
        try {
          var name = args[1].readCString();
          var value = args[2].readCString();
          var p = G.params.get(args[0].toString()) ||
                  {method: 'GET', headers: []};
          p.headers.push(name + ': ' + (value || ''));
          G.params.set(args[0].toString(), p);
        } catch (e) {}
      }
    });
    G.hooks++;
  }

  if (startFn) {
    Interceptor.attach(startFn, {
      onEnter: function (args) {
        G.reqCounter++; // cheap liveness marker
      }
    });
    G.hooks++;
  }

  if (onResp && urlGet) {
    var getUrl = new NativeFunction(urlGet, 'pointer', ['pointer']);
    var getStatus = statusGet
      ? new NativeFunction(statusGet, 'int32', ['pointer']) : null;
    Interceptor.attach(onResp, {
      onEnter: function (args) {
        // (callback, request, info, redirectInfo)
        try {
          var cstr = getUrl(args[2]);
          var url = cstr.isNull() ? '' : cstr.readCString();
          var status = getStatus ? getStatus(args[2]) : 0;
          emit({ev: 'response', url: url || '', status: status});
        } catch (e) {}
      }
    });
    G.hooks++;
  }
}

Process.enumerateModules().forEach(function (m) {
  if (m.name === 'mmcronet') {
    try { hookModule(m); } catch (e) {
      emit({ev: 'error', msg: 'hook fail ' + m.path + ': ' + e.message});
    }
  }
});

emit({ev: 'ready', pid: Process.id, hooks: G.hooks});
"""


def discover():
    """PIDs of interest: WeChat main, WeChatAppEx main, NetworkService."""
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    targets = {}
    for line in out.splitlines():
        if "grep" in line or not line.strip() or "PID" in line:
            continue
        pid_str = line.strip().split()[0]
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if "MacOS/WeChat" in line and "AppEx" not in line and \
           "Helper" not in line:
            targets[pid] = "WeChat"
        elif "MacOS/WeChatAppEx" in line and "--type=" not in line and \
                "crashpad" not in line:
            targets.setdefault(pid, "WeChatAppEx-main")
        elif "WeChatAppEx" in line and "network.mojom.NetworkService" in line:
            targets[pid] = "AppEx-NetworkService"
    return targets


def main():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    log = open(LOGFILE, "a", buffering=1)
    print(f"[net-cronet] logging to {LOGFILE}", flush=True)

    sessions = {}  # pid -> session

    def make_handler(name, pid):
        def on_message(msg, data):
            payload = msg.get("payload")
            if not payload:
                return
            try:
                rec = json.loads(payload)
            except (ValueError, TypeError):
                print(f"[{name}:{pid}] {payload[:120]}", flush=True)
                return
            rec["proc"] = name
            rec["pid"] = pid
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("ev") in ("ready", "error"):
                print(f"[{name}:{pid}] {rec}", flush=True)
            elif rec.get("ev") == "request":
                print(f"[{name}:{pid}] REQ {rec.get('method')} "
                      f"{rec.get('url', '')[:100]}", flush=True)
        return on_message

    while True:
        try:
            targets = discover()
            # drop dead sessions
            for pid in list(sessions):
                if pid not in targets:
                    try:
                        sessions[pid].detach()
                    except Exception:
                        pass
                    del sessions[pid]
                    print(f"[net-cronet] pid {pid} gone", flush=True)
            # attach new
            for pid, name in targets.items():
                if pid in sessions:
                    continue
                try:
                    s = frida.attach(pid)
                    script = s.create_script(HOOK)
                    script.on("message", make_handler(name, pid))
                    script.load()
                    sessions[pid] = s
                    print(f"[net-cronet] hooked {name} pid {pid}",
                          flush=True)
                except Exception as exc:
                    print(f"[net-cronet] attach {name}:{pid} failed: {exc}",
                          flush=True)
        except Exception as exc:
            print(f"[net-cronet] loop: {exc}", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
