// WeChatAppEx 4.1.13 (WeChat 4.1.13.98, AppEx 2.5.6.25561) devtools enabler
//
// Mechanism (reverse-engineered, offsets are file offsets == vmaddr, base 0):
//  - WeChatAppEx is Electron-based ("flue" Chromium fork)
//  - Debug features are Chromium command-line switches:
//      enable-chrome-inspector, enable-ui-devtools, enable-vconsole,
//      auto-launch-devtools, xweb-enable-inspect, use-debug-wxpkg,
//      export-wxpkg-content
//  - Child processes do NOT inherit parent argv; a builder function
//    @ 0x525721C constructs each child's CommandLine and appends the
//    wmpf-* switches via AppendSwitchNative @ 0x1928DB4.
//  - Switch-propagation routine (~0x548D4C0) mirrors parent switches /
//    global KV (global_enable_vconsole, global_auto_launch_devtools) into
//    children via Chromium AppendSwitch @ 0x93016E0.
//
// Strategy:
//  1. Hook builder @ 0x525721C: onLeave, append debug switches onto the
//     freshly built child CommandLine -> every new renderer inherits them.
//  2. Hook CommandLine::HasSwitch @ 0x8C2730 -> force true for debug switches
//     (covers main-process consumers and the propagation routine).
//  3. Hook JSON bool getter @ 0xF1ADB8 -> force true for vconsole keys.

var DEBUG_SWITCHES = [
    "enable-chrome-inspector",
    "enable-ui-devtools",
    "enable-vconsole",
    "auto-launch-devtools",
    "xweb-enable-inspect",
    "use-debug-wxpkg",
    "export-wxpkg-content"
];

var JSON_BOOL_KEYS = [
    "enable_vconsole",
    "global_enable_vconsole",
    "is_game_debug"
];

function readCStringSafe(p) {
    try { return p.readCString(); } catch (e) { return null; }
}

// libc++ std::string at p: SSO when byte[23]&1==0 (size=byte[23]>>1),
// otherwise long: [0]=char* [16]=size
function readLibcxxString(p) {
    try {
        var last = p.add(23).readU8();
        if ((last & 1) === 0) {
            var len = last >> 1;
            if (len === 0 || len > 22) return null;
            return p.readUtf8String(len);
        }
        var data = p.readPointer();
        var size = p.add(16).readULong();
        if (size === 0 || size > 4096) return null;
        return data.readUtf8String(size);
    } catch (e) { return null; }
}

function appendSwitchesTo(cmdline, appendSwitch) {
    DEBUG_SWITCHES.forEach(function (s) {
        var buf = Memory.allocUtf8String(s);
        appendSwitch(cmdline, buf, s.length, 0, ptr(0));
    });
}

function main() {
    var module = Process.findModuleByName('WeChatAppEx Framework');
    if (!module) {
        send("[-] WeChatAppEx Framework not found in this process");
        return false;
    }
    var base = module.base;
    send("[+] module " + module.name + " @ " + base);

    var switchSet = {};
    DEBUG_SWITCHES.forEach(function (s) { switchSet[s] = true; });
    var jsonKeySet = {};
    JSON_BOOL_KEYS.forEach(function (k) { jsonKeySet[k] = true; });

    // flue's own AppendSwitch: f(builder, key_data, key_len, value_begin, value_end)
    var appendSwitchFlue = new NativeFunction(base.add(0x1928DB4), 'void',
        ['pointer', 'pointer', 'uint64', 'pointer', 'pointer']);
    // Chromium CommandLine::AppendSwitchNative — takes the CommandLine object
    // obtained by dereferencing wrapper+0x18 (main process path).
    var appendSwitchMain = new NativeFunction(base.add(0x93016E0), 'void',
        ['pointer', 'pointer', 'uint32', 'uint32', 'pointer']);

    // ---- Hook A: child CommandLine builder @ 0x525721C
    // x1 (-> x19) is the flue builder object the wmpf-* switches go through.
    Interceptor.attach(base.add(0x525721C), {
        onEnter: function (args) {
            this.builder = args[1];
        },
        onLeave: function (retval) {
            if (this.builder && !this.builder.isNull()) {
                try {
                    DEBUG_SWITCHES.forEach(function (s) {
                        var buf = Memory.allocUtf8String(s);
                        appendSwitchFlue(this.builder, buf, uint64(s.length), ptr(0), ptr(0));
                    }, this);
                    send("[+] child cmdline armed with debug switches");
                } catch (e) {
                    send("[-] child arm failed: " + e);
                }
            }
        }
    });
    send("[+] hooked child-cmdline builder @ +0x525721C");

    // ---- Hook B: CommandLine::HasSwitch @ 0x8C2730
    Interceptor.attach(base.add(0x8C2730), {
        onEnter: function (args) {
            this.force = false;
            var name = readCStringSafe(args[1]);
            if (name && switchSet[name]) {
                this.force = true;
                send("[hasswitch] " + name + " -> forcing true");
            }
        },
        onLeave: function (retval) {
            if (this.force) retval.replace(ptr(1));
        }
    });
    send("[+] hooked HasSwitch @ +0x8C2730");

    // ---- Hook C2: JSON int getter @ 0x564DB00
    // Trace only launch/debug-related keys to diff mini-program launches.
    var TRACE_PATTERN = /^(debug|version|env|scene|launch|app_|pkg|wxpkg|host_)/i;
    var DEBUG_TYPE_VALUE = 1;
    var FORCE_INTS = { "debugType": 1, "versionType": 1, "envVersion": 1 };
    Interceptor.attach(base.add(0x564DB00), {
        onEnter: function (args) {
            this.force = 0;
            this.keyName = null;
            var key = readCStringSafe(args[1]);
            if (key && FORCE_INTS[key] !== undefined) {
                this.force = FORCE_INTS[key];
                this.keyName = key;
            }
            if (key && TRACE_PATTERN.test(key)) {
                send("[getint] " + key + " (default=" + args[2].toInt32() + ")");
            }
        },
        onLeave: function (retval) {
            if (this.force) {
                send("[forceint] " + this.keyName + " " + retval.toInt32()
                     + " -> " + this.force);
                retval.replace(ptr(this.force));
            }
        }
    });
    send("[+] hooked JsonGetInt @ +0x564DB00");

    // ---- Hook C3: JSON set @ 0x6BEFBC — trace launch/debug keys written
    var LOCAL_DEBUG_ENDPOINT = "ws://127.0.0.1:9999/x";
    Interceptor.attach(base.add(0x6BEFBC), {
        onEnter: function (args) {
            var key = readCStringSafe(args[1]);
            if (!key) return;
            var tag = "?", val = "";
            try {
                tag = args[3].add(0x18).readU8();
                if (key === "app_id" && tag === 4) {
                    var s = readLibcxxString(args[3]);
                    if (s) send("[appid] " + s);
                }
                if (key === "remote_debug_endpoint" && tag === 4) {
                    // rewrite endpoint to the local bridge (SSO std::string)
                    var old = readLibcxxString(args[3]);
                    var nv = Memory.alloc(0x20);
                    nv.writeUtf8String(LOCAL_DEBUG_ENDPOINT);
                    nv.add(23).writeU8(LOCAL_DEBUG_ENDPOINT.length << 1);
                    nv.add(0x18).writeU8(4);
                    args[3] = nv;
                    send("[endpoint] " + (old || "<empty>") + " -> " + LOCAL_DEBUG_ENDPOINT);
                    return;
                }
                if (!TRACE_PATTERN.test(key)) return;
                if (tag === 2) val = " int=" + args[3].readU32();
                else if (tag === 1) val = " bool=" + (args[3].readU8() & 1);
            } catch (e) { }
            send("[jsonset] " + key + " tag=" + tag + val);
        }
    });
    send("[+] hooked JsonSet @ +0x6BEFBC");

    // ---- Hook C: JSON bool getter @ 0xF1ADB8
    Interceptor.attach(base.add(0xF1ADB8), {
        onEnter: function (args) {
            this.force = false;
            var key = readCStringSafe(args[1]);
            if (key && jsonKeySet[key]) {
                this.force = true;
                send("[jsonget] " + key + " -> forcing true");
            } else if (key && (key.indexOf("debug") >= 0 || key.indexOf("console") >= 0
                        || key.indexOf("inspect") >= 0 || key.indexOf("devtool") >= 0)) {
                send("[jsonquery] " + key);
            }
        },
        onLeave: function (retval) {
            if (this.force && !retval.isNull()) {
                try {
                    if (retval.add(0x18).readU8() === 1) {
                        var cur = retval.readU8();
                        if ((cur & 1) === 0) retval.writeU8(cur | 1);
                    } else {
                        send("[jsonget] " + "type tag=" + retval.add(0x18).readU8() + " (not bool=1)");
                    }
                } catch (e) { }
            }
        }
    });
    send("[+] hooked JsonGetBool @ +0xF1ADB8");

    // ---- Also bake switches into the main process CommandLine
    try {
        var getMutableCmdline = new NativeFunction(base.add(0x525FAF0), 'pointer', []);
        var wrapper = getMutableCmdline();
        if (!wrapper.isNull()) {
            var cmdline = wrapper.add(0x18).readPointer();
            if (!cmdline.isNull()) {
                appendSwitchesTo(cmdline, appendSwitchMain);
                send("[+] main cmdline armed");
            }
        }
    } catch (e) {
        send("[-] main arm failed: " + e);
    }

    // ---- Probe: chrome-inspector consumer & endpoint generator timing
    Interceptor.attach(base.add(0x5628EAC), {
        onEnter: function () { send("[probe] inspector-consumer called"); }
    });
    Interceptor.attach(base.add(0x221DB58), {
        onEnter: function (args) { send("[probe] endpoint-gen called"); }
    });
    send("[+] probes armed");
    send("[+] ready - open a mini-program; the new renderer will carry debug switches");
    return true;
}


if (!main()) {
    send("[-] injection failed");
}
