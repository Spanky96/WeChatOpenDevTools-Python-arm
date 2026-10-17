#!/usr/bin/env python3
"""Persistent network capture daemon for WeChat mini-programs.

Runs indefinitely. Hooks connect(), SSL_write, and mmcronet in:
- WeChat main process
- WeChatAppEx main process
- All Helper renderer processes (auto-discovers new ones)

Logs to /tmp/wechat_rea/net_daemon.log
"""
import frida
import os
import re
import subprocess
import sys
import time

LOGFILE = "/tmp/wechat_rea/net_daemon.log"

HOOK = r"""
var PROC = 'unknown';
var bssl = Process.findModuleByName('libboringssl.dylib');

// connect() — any outgoing TCP
try {
    var connect_fn = Module.findGlobalExportByName('connect');
    if (connect_fn) {
        Interceptor.attach(connect_fn, {
            onEnter: function(args) {
                try {
                    var addr = args[1];
                    if (addr.readU16() === 2) {
                        var port = (addr.add(2).readU8() << 8) | addr.add(3).readU8();
                        var ip = addr.add(4).readU8() + '.' + addr.add(5).readU8() +
                                 '.' + addr.add(6).readU8() + '.' + addr.add(7).readU8();
                        if (ip !== '127.0.0.1' && ip !== '0.0.0.0') {
                            send('[CONNECT] ' + ip + ':' + port);
                        }
                    }
                } catch(e) {}
            }
        });
    }
} catch(e) {}

// SSL_write — HTTPS request text
if (bssl) {
    try {
        var sslW = bssl.getExportByName('SSL_write');
        Interceptor.attach(sslW, {
            onEnter: function(args) {
                try {
                    var data = args[1].readCString(400);
                    if (data && /^(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH) /.test(data)) {
                        var lines = data.split(String.fromCharCode(13, 10));
                        var url = lines[0].substring(0, 150);
                        var host = '';
                        for (var i = 1; i < lines.length && i < 15; i++) {
                            if (lines[i].indexOf('Host:') === 0) {
                                host = lines[i].trim();
                                break;
                            }
                        }
                        send('[HTTPS] ' + url + (host ? ' | ' + host : ''));
                    }
                } catch(e) {}
            }
        });
    } catch(e) {}
}

// mmcronet — URL at request creation
var cronet = Process.findModuleByName('mmcronet');
if (cronet) {
    try {
        var initFn = cronet.getExportByName('Cronet_UrlRequest_InitWithParams');
        if (initFn) {
            Interceptor.attach(initFn, {
                onEnter: function(args) {
                    try {
                        var url = args[2].readCString();
                        if (url) send('[CRONET] ' + url.substring(0, 150));
                    } catch(e) {}
                }
            });
        }
    } catch(e) {}
}

send('[READY] hooks installed');
"""


def find_wechat_processes():
    """Find all WeChat-related processes worth hooking."""
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    targets = {}
    for line in out.splitlines():
        if "grep" in line or "crashpad" in line or "net_daemon" in line:
            continue
        if "PID" in line or not line.strip():
            continue
        try:
            pid = int(line.strip().split()[0])
        except ValueError:
            continue
        if "MacOS/WeChat" in line and "AppEx" not in line and "Helper" not in line:
            targets[pid] = "wechat-main"
        elif "MacOS/WeChatAppEx " in line and "--type=" not in line:
            targets[pid] = "appex-main"
        elif "Helper (Renderer)" in line:
            targets[pid] = "renderer"
        elif "WeApp" in line and "--type=renderer" in line:
            targets[pid] = "weapp"
    return targets


def main():
    log = open(LOGFILE, "a", buffering=1)
    print(f"[daemon] logging to {LOGFILE}", flush=True)
    log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] daemon started\n")

    sessions = {}  # pid -> frida session

    def on_message_factory(pid, name):
        def handler(msg, data):
            payload = msg.get("payload", "")
            if payload and ("[" in str(payload)):
                stamp = time.strftime("%H:%M:%S")
                line = f"[{stamp}][{name}:{pid}] {payload}"
                print(line, flush=True)
                log.write(line + "\n")
        return handler

    while True:
        try:
            targets = find_wechat_processes()
            # hook new processes
            for pid, name in targets.items():
                if pid in sessions:
                    continue
                try:
                    s = frida.attach(pid)
                    script = s.create_script(HOOK)
                    script.on("message", on_message_factory(pid, name))
                    script.load()
                    sessions[pid] = s
                    line = f"[{time.strftime('%H:%M:%S')}] hooked {name}:{pid}"
                    print(line, flush=True)
                    log.write(line + "\n")
                except Exception as e:
                    pass  # silently skip failed attaches

            # clean dead sessions
            for pid in list(sessions):
                if pid not in targets:
                    del sessions[pid]
        except Exception as e:
            log.write(f"[error] {e}\n")

        time.sleep(3)


if __name__ == "__main__":
    main()
