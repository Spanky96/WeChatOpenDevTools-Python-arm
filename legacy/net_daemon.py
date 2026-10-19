#!/usr/bin/env python3
"""Persistent network capture daemon for WeChat mini-programs.

Architecture discovery (2026-10-17):
  wx.request → renderer → IPC → WeChatAppEx main → IPC → WeChat main
    → custom TLS (NOT boringssl SSL_write) → write()/send() → TCP

This daemon hooks write() and send() in the WeChat main process to capture
pre-encrypted network data. HTTP headers may be visible in plaintext
before the custom TLS layer encrypts them.

Logs to /tmp/wechat_rea/net_capture.log
"""
import frida
import os
import re
import subprocess
import sys
import time

LOGFILE = "/tmp/wechat_rea/net_capture.log"

HOOK = r"""
var count = 0;

// write() captures all outbound data
var write_addr = null;
try { write_addr = Module.findGlobalExportByName('write'); } catch(e) {}
if (write_addr) {
    Interceptor.attach(write_addr, {
        onEnter: function(args) {
            var fd = args[0].toInt32();
            var len = args[2].toInt32();
            if (len > 10) {
                count++;
                try {
                    var buf = args[1];
                    var str = '';
                    try {
                        str = buf.readCString(Math.min(len, 500));
                    } catch(e) {}
                    if (str) {
                        str = str.replace(/[^\x20-\x7e\n\r]/g, '.');
                        if (str.indexOf('GET ') === 0 || str.indexOf('POST ') === 0 ||
                            str.indexOf('PUT ') === 0 || str.indexOf('DELETE ') === 0) {
                            // HTTP request in plaintext!
                            var lines = str.split('\r\n');
                            send('[HTTP-REQ] ' + lines[0].substring(0, 150));
                            for (var i = 1; i < lines.length && i < 20; i++) {
                                if (lines[i].indexOf('Host:') === 0) {
                                    send('  ' + lines[i].trim());
                                    break;
                                }
                            }
                        } else if (str.indexOf('HTTP/') === 0) {
                            send('[HTTP-RSP] ' + str.substring(0, 100));
                        } else if (count <= 30) {
                            // non-HTTP data (possibly encrypted)
                            var preview = str.substring(0, 60);
                            if (preview.length > 5) {
                                send('[DATA] fd=' + fd + ' len=' + len + ' ' + preview);
                            }
                        }
                    }
                } catch(e) {}
            }
        }
    });
    send('[+] write() hooked');
}

// sendmsg() for TCP
var sendmsg_addr = null;
try { sendmsg_addr = Module.findGlobalExportByName('sendmsg'); } catch(e) {}
if (sendmsg_addr) {
    Interceptor.attach(sendmsg_addr, {
        onEnter: function(args) {
            count++;
            var fd = args[0].toInt32();
            if (count <= 50) send('[SENDMSG] fd=' + fd);
        }
    });
}

// connect() for new connections
var connect_addr = null;
try { connect_addr = Module.findGlobalExportByName('connect'); } catch(e) {}
if (connect_addr) {
    Interceptor.attach(connect_addr, {
        onEnter: function(args) {
            try {
                var addr = args[1];
                if (addr.readU16() === 2) {
                    var port = (addr.add(2).readU8() << 8) | addr.add(3).readU8();
                    var ip = addr.add(4).readU8() + '.' + addr.add(5).readU8() + '.' +
                             addr.add(6).readU8() + '.' + addr.add(7).readU8();
                    if (ip !== '127.0.0.1' && ip !== '0.0.0.0') {
                        send('[CONNECT] ' + ip + ':' + port);
                    }
                }
            } catch(e) {}
        }
    });
}

send('[READY] all hooks installed');
"""


def find_wechat_main():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "PID" in line or not line.strip():
            continue
        if "MacOS/WeChat" in line and "AppEx" not in line and "Helper" not in line:
            if "grep" not in line and "net_daemon" not in line:
                try:
                    return int(line.strip().split()[0])
                except ValueError:
                    continue
    return None


def main():
    log = open(LOGFILE, "a", buffering=1)
    print(f"[daemon] logging to {LOGFILE}", flush=True)

    current_pid = None
    session = None

    def on_message(msg, data):
        payload = msg.get("payload", "")
        if payload:
            stamp = time.strftime("%H:%M:%S")
            line = f"[{stamp}] {payload}"
            print(line, flush=True)
            log.write(line + "\n")

    while True:
        try:
            pid = find_wechat_main()
            if pid != current_pid:
                if session:
                    try:
                        session.detach()
                    except:
                        pass
                    session = None
                if pid:
                    try:
                        session = frida.attach(pid)
                        script = session.create_script(HOOK)
                        script.on("message", on_message)
                        script.load()
                        current_pid = pid
                        line = f"[{time.strftime('%H:%M:%S')}] hooked WeChat main:{pid}"
                        print(line, flush=True)
                        log.write(line + "\n")
                    except Exception as e:
                        line = f"[error] attach {pid}: {e}"
                        print(line, flush=True)
                        log.write(line + "\n")
        except Exception as e:
            log.write(f"[error] {e}\n")

        time.sleep(3)


if __name__ == "__main__":
    main()
