#!/usr/bin/env python3
"""Trace WeApp process startup to find the vConsole/appservice injection path.

Watches for new WeApp.app processes (real mini-program ones), attaches
immediately, and hooks the config readers (JsonGetInt / JsonGetBool /
HasSwitch) with backtraces so we can see which native code drives the
service-context JS injection.

Usage:
  1. run this script
  2. fully close a mini-program window, then reopen it
"""
import re
import subprocess
import sys
import time

import frida

LOG = "/tmp/wechat_rea/weapp_trace.log"

HOOK = r"""
'use strict';
var seen = {};
function bt(ctx, n) {
  try {
    return Thread.backtrace(ctx, Backtracer.ACCURATE).slice(0, n)
      .map(function (a) {
        var m = Process.findModuleByAddress(a);
        return m ? m.name + '+' + a.sub(m.base) : '' + a;
      }).join(' | ');
  } catch (e) { return 'bt-fail'; }
}
function log(tag, key, ctx) {
  if (seen[tag + key]) return;
  seen[tag + key] = 1;
  send('[' + tag + '] ' + key + '  BT: ' + bt(ctx, 9));
}

var fw = Process.findModuleByName('WeChatAppEx Framework');
if (!fw) { send('[-] framework module not found'); }
else {
  var base = fw.base;
  // JsonGetInt(json, key, default)
  Interceptor.attach(base.add(0x564DB00), {
    onEnter: function (a) {
      try { log('int', a[1].readCString(), this.context); } catch (e) {}
    }
  });
  // JsonGetBool(json, key, len)
  Interceptor.attach(base.add(0xF1ADB8), {
    onEnter: function (a) {
      try { log('bool', a[1].readCString(), this.context); } catch (e) {}
    }
  });
  // HasSwitch(cmdline, key)
  Interceptor.attach(base.add(0x8C2730), {
    onEnter: function (a) {
      try { log('switch', a[1].readCString(), this.context); } catch (e) {}
    }
  });
  send('[+] weapp tracer armed in pid ' + Process.id);
}
"""


def find_weapp_targets():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        if "WeApp.app" not in line or "grep" in line or "python" in line:
            continue
        m = re.match(r"\s*(\d+)\s+", line)
        if not m:
            continue
        pid = int(m.group(1))
        am = re.search(r"wmpf-appid=(\S+)", line)
        appid = am.group(1) if am else "?"
        if appid.startswith("wx"):
            found[pid] = appid
    return found


def main():
    logf = open(LOG, "a", buffering=1)
    print("[tracer] waiting for a NEW mini-program WeApp process...",
          flush=True)
    print("[tracer] close the mini-program window, then reopen it",
          flush=True)
    known = set(find_weapp_targets().keys())
    attached = {}

    def on_message_factory(pid, appid):
        def on_message(msg, data):
            payload = msg.get("payload")
            if payload:
                line = f"[{time.strftime('%H:%M:%S')}][{appid}:{pid}] {payload}"
                print(line[:250], flush=True)
                logf.write(line + "\n")
        return on_message

    while True:
        try:
            targets = find_weapp_targets()
            for pid, appid in targets.items():
                if pid in known or pid in attached:
                    continue
                known.add(pid)
                try:
                    s = frida.attach(pid)
                    script = s.create_script(HOOK)
                    script.on("message", on_message_factory(pid, appid))
                    script.load()
                    attached[pid] = (s, script, appid)
                    print(f"[tracer] ATTACHED to {appid} pid {pid}", flush=True)
                except Exception as exc:
                    print(f"[tracer] attach {appid}:{pid} failed: "
                          f"{str(exc)[:60]}", flush=True)
            for pid in list(attached):
                if pid not in targets:
                    try:
                        attached[pid][0].detach()
                    except Exception:
                        pass
                    del attached[pid]
                    print(f"[tracer] {pid} exited", flush=True)
        except Exception as exc:
            print(f"[tracer] loop: {exc}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
