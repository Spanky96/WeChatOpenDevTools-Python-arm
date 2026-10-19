#!/usr/bin/env python3
"""All-spectrum persistent network watch.

Attaches to WeChat main + every WeChatAppEx process and hooks whatever
network libraries each has loaded:
  - mmcronet (Cronet C API: URL/method/headers/status)
  - libboringssl (SSL_write plaintext HTTP)
  - ilink2 (NetworkManager::SendRequestAsync — mini-program CGI funnel)

Logs JSON lines to /tmp/wechat_rea/net_capture.jsonl
"""
import json
import os
import re
import subprocess
import time

import frida

LOGFILE = "/tmp/wechat_rea/net_capture.jsonl"

HOOK = r"""
'use strict';
var H = {n: 0};
function emit(o) { o.t = Date.now(); send(JSON.stringify(o)); }

function readStdString(p) {
  try {
    var last = p.add(23).readU8();
    if ((last & 1) === 0) { var len = last >> 1; return p.readUtf8String(len); }
    var d = p.readPointer(); var sz = p.add(16).readULong();
    if (sz > 200000) sz = 200000;
    return d.readUtf8String(sz);
  } catch (e) { return null; }
}
function render(b, n) {
  var s = '';
  for (var i = 0; i < Math.min(b.length, n); i++) {
    var c = b[i];
    s += (c >= 32 && c < 127) ? String.fromCharCode(c) : '.';
  }
  return s;
}

// ---- mmcronet ----
function hookCronet(m) {
  function ex(n) { try { return m.getExportByName(n); } catch (e) { return null; } }
  var params = new Map();
  var initFn = ex('Cronet_UrlRequest_InitWithParams');
  var ms = ex('Cronet_UrlRequestParams_http_method_set');
  var ha = ex('Cronet_UrlRequestParams_request_headers_add');
  var onResp = ex('Cronet_UrlRequestCallback_OnResponseStarted');
  var urlGet = ex('Cronet_UrlResponseInfo_url_get');
  var stGet = ex('Cronet_UrlResponseInfo_http_status_code_get');
  if (initFn) Interceptor.attach(initFn, { onEnter: function (a) {
    try {
      var url = a[2].readCString(); var p = a[3].toString();
      var meta = params.get(p) || { method: 'GET', headers: [] }; H.n++;
      emit({ ev: 'cronet_request', url: url || '', method: meta.method,
             headers: meta.headers.slice(0, 30) });
      params.delete(p);
    } catch (e) {} } });
  if (ms) Interceptor.attach(ms, { onEnter: function (a) {
    try {
      var p = params.get(a[0].toString()) || { method: 'GET', headers: [] };
      p.method = a[1].readCString() || 'GET';
      params.set(a[0].toString(), p);
    } catch (e) {} } });
  if (ha) Interceptor.attach(ha, { onEnter: function (a) {
    try {
      var p = params.get(a[0].toString()) || { method: 'GET', headers: [] };
      p.headers.push(a[1].readCString() + ': ' + (a[2].readCString() || ''));
      params.set(a[0].toString(), p);
    } catch (e) {} } });
  if (onResp && urlGet) {
    var gu = new NativeFunction(urlGet, 'pointer', ['pointer']);
    var gs = stGet ? new NativeFunction(stGet, 'int32', ['pointer']) : null;
    Interceptor.attach(onResp, { onEnter: function (a) {
      try {
        var c = gu(a[2]);
        emit({ ev: 'cronet_response', url: c.isNull() ? '' : c.readCString(),
               status: gs ? gs(a[2]) : 0 });
      } catch (e) {} } });
  }
  return !!initFn;
}

// ---- boringssl ----
function hookSsl(m) {
  var sw;
  try { sw = m.getExportByName('SSL_write'); } catch (e) { return false; }
  Interceptor.attach(sw, { onEnter: function (a) {
    var len = a[2].toInt32();
    if (len < 8 || len > 131072) return;
    try {
      var str = a[1].readCString(Math.min(len, 700));
      if (str && (str.indexOf('GET ') === 0 || str.indexOf('POST ') === 0 ||
          str.indexOf('PUT ') === 0 || str.indexOf('DELETE ') === 0 ||
          str.indexOf('HEAD ') === 0 || str.indexOf('OPTIONS ') === 0 ||
          str.indexOf('PATCH ') === 0)) {
        var lines = str.split('\r\n');
        var host = '';
        for (var i = 1; i < lines.length && i < 15; i++) {
          if (lines[i].toLowerCase().indexOf('host:') === 0) {
            host = lines[i].trim(); break;
          }
        }
        emit({ ev: 'ssl_request', url: lines[0].substring(0, 250), host: host });
      }
    } catch (e) {} } });
  return true;
}

// ---- ilink2 ----
var ILINK_SYMS = [
  '_ZN5ilink11zidl_export14NetworkManager16SendRequestAsyncERKNS_9pb_export16IlinkRequestInfoE',
  '_ZN5ilink11zidl_export14NetworkManager16SendRequestAsyncERKNS_9pb_export16IlinkRequestInfoENSt3__18functionIFviRKNS6_12basic_stringIcNS6_11char_traitsIcEENS6_9allocatorIcEEEEEEE',
  '_ZN5ilink11zidl_export18NetworkManagerNoPB16SendRequestAsyncERKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE',
  '_ZN5ilink11zidl_export18NetworkManagerNoPB16SendRequestAsyncERKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEENS2_8functionIFviSA_EEE',
];
function hookIlink(m) {
  var placed = 0;
  ILINK_SYMS.forEach(function (sym) {
    var addr;
    try { addr = m.getExportByName(sym); } catch (e) { return; }
    Interceptor.attach(addr, { onEnter: function (a) {
      H.n++;
      var rec = { ev: 'ilink_request',
                  kind: sym.indexOf('NoPB') >= 0 ? 'nopb' : 'pb' };
      try {
        var s1 = readStdString(a[1]);
        if (s1 && s1.length > 2) rec.str = s1.slice(0, 600);
        else {
          var raw = new Uint8Array(a[1].readByteArray(2048));
          rec.raw = render(raw, 2048);
          rec.hex = Array.prototype.map.call(raw.slice(0, 2048),
            function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        }
      } catch (e) {}
      emit(rec);
    } });
    placed++;
  });
  // Cloud CGI event handlers (requests + responses as std::string)
  var CGI_SYMS = {
    req: '_ZN5ilink11zidl_export18CloudSessionEvents26OnSendCloudCgiRequestEventERKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE',
    req_nopb: '_ZN5ilink11zidl_export16CloudSessionNoPB21OnSendCloudCgiRequestEv',
    resp: '_ZN5ilink11zidl_export12CloudSession23ReceiveCloudCgiResponseEjiRKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE',
    resp_nopb: '_ZN5ilink11zidl_export16CloudSessionNoPB23ReceiveCloudCgiResponseEjiRKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE',
  };
  Object.keys(CGI_SYMS).forEach(function (kind) {
    var addr;
    try { addr = m.getExportByName(CGI_SYMS[kind]); } catch (e) { return; }
    Interceptor.attach(addr, { onEnter: function (a) {
      H.n++;
      var rec = { ev: 'cloudcgi_' + (kind.indexOf('resp') >= 0 ? 'response' : 'request') };
      try {
        var sp = (kind === 'req') ? a[1] : a[3];  // std::string arg position
        if (kind === 'req_nopb') {
          rec.raw = render(new Uint8Array(a[0].readByteArray(256)), 256);
        } else {
          var s = readStdString(sp);
          if (s && s.length > 2) rec.str = s.slice(0, 3000);
          else rec.raw = render(new Uint8Array(sp.readByteArray(512)), 512);
        }
        if (kind.indexOf('resp') >= 0 && kind !== 'req_nopb') {
          rec.code = a[1].toInt32(); rec.status = a[2].toInt32();
        }
      } catch (e) {}
      emit(rec);
    } });
    placed++;
  });
  return placed;
}

var summary = { cronet: 0, ssl: 0, ilink: 0 };
Process.enumerateModules().forEach(function (m) {
  var n = m.name.toLowerCase();
  try {
    if (n === 'mmcronet' && hookCronet(m)) summary.cronet++;
    else if (n === 'libboringssl.dylib' && hookSsl(m)) summary.ssl++;
    else if (n === 'ilink2' && hookIlink(m)) summary.ilink += 1;
  } catch (e) {}
});
emit({ ev: 'ready', summary: summary });
"""


def discover():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    targets = {}
    for line in out.splitlines():
        if ("grep" in line or not line.strip() or "python" in line or
                "frida" in line):
            continue
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, cmd = int(m.group(1)), m.group(2)
        if "MacOS/WeChat" in cmd and "AppEx" not in cmd and \
                "Helper" not in cmd:
            targets[pid] = "WeChat"
        elif "MacOS/WeChatAppEx" in cmd and "crashpad" not in cmd:
            if "--type=" not in cmd:
                targets[pid] = "AppEx-main"
            elif "network.mojom.NetworkService" in cmd:
                targets[pid] = "AppEx-NetSvc"
            elif "ILinkServiceHost" in cmd:
                targets[pid] = "AppEx-ILink"
            elif "WeApp.app" in cmd:
                am = re.search(r"wmpf-appid=(\S+)", cmd)
                targets[pid] = f"WeApp-{am.group(1)[:16] if am else '?'}"
            elif "wmpf-render-type=7" in cmd:
                targets[pid] = "Helper-rt7"
    return targets


def main():
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    log = open(LOGFILE, "a", buffering=1)
    print(f"[net-watch] logging to {LOGFILE}", flush=True)
    sessions = {}

    def handler(name, pid):
        def on_message(msg, data):
            payload = msg.get("payload")
            if not payload:
                return
            try:
                rec = json.loads(payload)
            except (ValueError, TypeError):
                return
            rec["proc"], rec["pid"] = name, pid
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("ev") == "ready":
                print(f"[net-watch] {name}:{pid} {rec.get('summary')}",
                      flush=True)
            elif rec.get("ev") != "cronet_response":
                print(f"[CAPTURE] {name}:{pid} {rec.get('ev')} "
                      f"{str(rec.get('url') or rec.get('str') or rec.get('raw') or '')[:130]}",
                      flush=True)
        return on_message

    while True:
        try:
            targets = discover()
            for pid in list(sessions):
                if pid not in targets:
                    try:
                        sessions[pid].detach()
                    except Exception:
                        pass
                    del sessions[pid]
                    print(f"[net-watch] {pid} gone", flush=True)
            for pid, name in targets.items():
                if pid in sessions:
                    continue
                try:
                    s = frida.attach(pid)
                    script = s.create_script(HOOK)
                    script.on("message", handler(name, pid))
                    script.load()
                    sessions[pid] = s
                except Exception as exc:
                    print(f"[net-watch] {name}:{pid} attach-fail "
                          f"{str(exc)[:60]}", flush=True)
        except Exception as exc:
            print(f"[net-watch] loop: {exc}", flush=True)
        time.sleep(4)


if __name__ == "__main__":
    main()
