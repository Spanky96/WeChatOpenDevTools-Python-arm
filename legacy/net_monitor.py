#!/usr/bin/env python3
"""Persistent network monitor for WeChat main process.

Hooks SSL_write, mmcronet, NSURLSession to capture ALL outbound HTTP.
Logs to /tmp/wechat_rea/net_capture.log — check anytime.
"""
import frida
import sys
import time

HOOK_CODE = r"""
var count = 0;
var LOG = [];

// 1. BoringSSL SSL_write — captures HTTPS request text
var bssl = Process.findModuleByName('libboringssl.dylib');
if (bssl) {
    var sslW = bssl.getExportByName('SSL_write');
    Interceptor.attach(sslW, {
        onEnter: function(args) {
            try {
                var data = args[1].readCString(500);
                if (data && data.length > 10) {
                    count++;
                    var first = data.split(String.fromCharCode(13))[0];
                    if (/^(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH) /.test(first)) {
                        send('[HTTP] ' + first.substring(0, 150));
                    }
                }
            } catch(e) {}
        }
    });
    send('[+] boringssl SSL_write hooked');
}

// 2. mmcronet Cronet — captures URL at request creation
var cronet = Process.findModuleByName('mmcronet');
if (cronet) {
    try {
        var initFn = cronet.getExportByName('Cronet_UrlRequest_InitWithParams');
        Interceptor.attach(initFn, {
            onEnter: function(args) {
                try {
                    var url = args[2].readCString();
                    if (url) { count++; send('[CRONET] ' + url.substring(0, 150)); }
                } catch(e) {}
            }
        });
        send('[+] mmcronet hooked');
    } catch(e) { send('[-] mmcronet: ' + e.message); }
}

// 3. NSURLSession — macOS native networking
try {
    var NSURLSession = ObjC.classes.NSURLSession;
    if (NSURLSession) {
        var dataTaskWithURL = NSURLSession['- dataTaskWithURL:completionHandler:'];
        if (dataTaskWithURL) {
            Interceptor.attach(dataTaskWithURL.implementation, {
                onEnter: function(args) {
                    try {
                        var url = ObjC.Object(args[2]).toString();
                        count++;
                        send('[NSURL] ' + url.substring(0, 150));
                    } catch(e) {}
                }
            });
            send('[+] NSURLSession hooked');
        }
    }
} catch(e) { send('[-] NSURLSession: ' + e.message); }

// 4. CFNetwork — lower level
try {
    var CFURLConnectionSendRequest = Module.findGlobalExportByName('CFURLConnectionSendRequest');
    if (CFURLConnectionSendRequest) {
        Interceptor.attach(CFURLConnectionSendRequest, {
            onEnter: function(args) {
                count++;
                send('[CFNetwork] request sent');
            }
        });
        send('[+] CFNetwork hooked');
    }
} catch(e) {}

// Report count every 30s
setInterval(function() {
    if (count > 0) send('[stats] ' + count + ' total captured');
}, 30000);
"""

def main():
    # find WeChat main process
    import subprocess
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    pid = None
    for line in out.splitlines():
        if "MacOS/WeChat" in line and "--type=" not in line and "grep" not in line:
            if "AppEx" not in line and "Helper" not in line:
                pid = int(line.strip().split()[0])
                break
    if not pid:
        print("[-] WeChat main process not found")
        sys.exit(1)

    print(f"[monitor] attaching to WeChat main ({pid})")
    print(f"[monitor] logging to stdout — interact with mini-programs freely")

    session = frida.attach(pid)
    script = session.create_script(HOOK_CODE)

    def on_message(msg, data):
        payload = msg.get("payload", str(msg)[:150])
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {payload}", flush=True)

    script.on("message", on_message)
    script.load()
    print("[monitor] running... Ctrl+C to stop\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[monitor] stopped")


if __name__ == "__main__":
    main()
