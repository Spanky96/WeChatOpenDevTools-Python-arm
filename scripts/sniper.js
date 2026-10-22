// SNIPER MODE - stable hooks + optional appid swap for remote-debug spawns.
//
// Stable (always on):
//  - JsonGetInt(versionType) -> 1 : dev-mode mini-programs (vConsole) VERIFIED
//  - HasSwitch(enable-chrome-inspector) -> true
//  - debug bools (enable_vconsole / is_game_debug / remote_debug_endpoint)
//  - child cmdline armed with inspect switches
// Optional (activated via message from python: {"cmd":"swap","appid":...}):
//  - flue AppendSwitch(wmpf-appid, value) -> value replaced by target appid,
//    so a remote-debug session launched for YOUR appid actually spawns and
//    debugs ANY mini-program you name.
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

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    send("[-] WeChatAppEx Framework not found");
} else {
    var base = module.base;
    var swapAppid = null;

    // ---- 1. versionType read force (dev mode)
    Interceptor.attach(base.add(0x564DB00), {
        onEnter: function (args) {
            this.force = false;
            try {
                if (args[1].readCString() === "versionType") this.force = true;
            } catch (e) { }
        },
        onLeave: function (retval) {
            if (this.force && retval.toInt32() !== 1) {
                send("[sniper] versionType read " + retval.toInt32() + " -> 1");
                retval.replace(ptr(1));
            }
        }
    });

    // ---- 2. chrome-inspector + render-type switches
    // HasSwitch("wmpf-render-type")==true at 0x29E992C makes the spawner
    // assign debug render type 1 to EVERY renderer -> inspector on -> the
    // remote-debug bridge can see and debug ANY mini-program.
    // enable-chrome-inspector additionally makes the spawner pass
    // --remote-debugging-port=62168 to a special child (fixed WeChat port,
    // discovered 2026-10-18 at 0x540de64 / 0x540e75c).
    // export-wxpkg-content / use-debug-wxpkg experiment: may dump decrypted
    // base-library wxpkg (V1MMWX) to disk.
    var FORCE_SWITCHES = {
        "enable-chrome-inspector": 1,
        "wmpf-render-type": 1,
        "export-wxpkg-content": 1,
        "use-debug-wxpkg": 1,
    };
    Interceptor.attach(base.add(0x8C2730), {
        onEnter: function (args) {
            this.force = false;
            try {
                var k = args[1].readCString();
                if (FORCE_SWITCHES[k]) this.force = true;
            } catch (e) { }
        },
        onLeave: function (retval) {
            if (this.force && retval.toInt32() === 0) {
                retval.replace(ptr(1));
            }
        }
    });

    // ---- 3. debug bools
    Interceptor.attach(base.add(0xF1ADB8), {
        onEnter: function (args) {
            this.force = false;
            try {
                var k = args[1].readCString();
                if (k === "remote_debug_endpoint" || k === "enable_vconsole"
                        || k === "is_game_debug") this.force = true;
            } catch (e) { }
        },
        onLeave: function (retval) {
            if (this.force && !retval.isNull()) {
                try {
                    if (retval.add(0x18).readU8() === 1) {
                        var cur = retval.readU8();
                        if ((cur & 1) === 0) retval.writeU8(cur | 1);
                    }
                } catch (e) { }
            }
        }
    });

    // ---- 4. child cmdline inspect switches
    var appendSwitchFlue = new NativeFunction(base.add(0x1928DB4), 'void',
        ['pointer', 'pointer', 'uint64', 'pointer', 'pointer']);
    var INSPECT_SWITCHES = ["xweb-enable-inspect", "enable-vconsole",
                             "auto-launch-devtools",
                             "export-wxpkg-content", "use-debug-wxpkg"];
    // render-type=1 registers the renderer with the debug bridge; appending
    // the same switch key overwrites WeChat's default 0 (Chromium switch map)
    var RENDER_TYPE_OVERRIDE = "1";
    Interceptor.attach(base.add(0x525721C), {
        onEnter: function (args) { this.builder = args[1]; },
        onLeave: function (retval) {
            if (!this.builder || this.builder.isNull()) return;
            try {
                INSPECT_SWITCHES.forEach(function (s) {
                    var buf = Memory.allocUtf8String(s);
                    appendSwitchFlue(this.builder, buf, uint64(s.length), ptr(0), ptr(0));
                }, this);
                var k = Memory.allocUtf8String("wmpf-render-type");
                var v = Memory.allocUtf8String(RENDER_TYPE_OVERRIDE);
                appendSwitchFlue(this.builder, k, uint64(15), v,
                                 v.add(RENDER_TYPE_OVERRIDE.length));
                send("[sniper] child cmdline armed (+render-type=1)");
            } catch (e) { }
        }
    });

    // ---- 5. OPTIONAL appid swap on debug spawns
    Interceptor.attach(base.add(0x1928DB4), {   // flue AppendSwitch
        onEnter: function (args) {
            if (!swapAppid) return;
            try {
                if (args[1].readCString() !== "wmpf-appid") return;
                var buf = Memory.allocUtf8String(swapAppid);
                args[3] = buf;                          // StringPiece begin
                args[4] = buf.add(swapAppid.length);    // end
                send("[swap] wmpf-appid -> " + swapAppid);
            } catch (e) { }
        }
    });


    // ---- 7. rewrite render-type VALUE as the builder writes it.
    // WeChat appends wmpf-render-type=0 for ordinary renderers; flipping the
    // value here makes every renderer start as debug type (inspector on).
    Interceptor.attach(base.add(0x1928DB4), {   // flue AppendSwitch (value form)
        onEnter: function (args) {
            try {
                if (args[1].readCString() !== "wmpf-render-type") return;
                // value StringPiece: [x3, x4)
                var len = args[4].toInt32() - args[3].toInt32();
                if (len === 1 && args[3].readU8() === 0x30) {  // "0"
                    var v = Memory.allocUtf8String("1");
                    args[3] = v; args[4] = v.add(1);
                    send("[sniper] wmpf-render-type value 0 -> 1");
                }
            } catch (e) { }
        }
    });

    // ---- 8. render-type rewrite at the Chromium AppendSwitch source.
    // Web/multitab renderers get --wmpf-render-type via the Chromium
    // AppendSwitch (0x93016E0), NOT the flue builder, and it OVERWRITES the
    // value hook #4 appended — so rewrite 0 -> 1 here to give every renderer
    // (mini-program AND web page) debug render type: inspector on, visible to
    // the debug bridge.
    Interceptor.attach(base.add(0x93016E0), {
        onEnter: function (args) {
            try {
                if (args[1].readCString() !== "wmpf-render-type") return;
                var len = args[4].toInt32() - args[3].toInt32();
                var cur = len > 0 && len < 8 ? args[3].readUtf8String(len) : "?";
                if (len === 1 && args[3].readU8() === 0x30) {  // "0"
                    var v = Memory.allocUtf8String("1");
                    args[3] = v; args[4] = v.add(1);
                    send("[sniper] chromium AppendSwitch render-type 0 -> 1");
                } else {
                    send("[sniper] chromium AppendSwitch render-type=" + cur +
                         " (left as-is)");
                }
            } catch (e) { }
        }
    });

    // ---- 9. posix_spawn argv rewrite: catch EVERY renderer spawn.
    // Web/multitab renderers (render-type=7 etc.) bypass the AppendSwitch
    // paths above — their argv is assembled elsewhere — so rewrite at the
    // exec boundary instead: any child with --type=renderer and a
    // non-debug wmpf-render-type becomes render type 1 (inspector on).
    function readStrv(argv) {
        var parts = [];
        try {
            for (var i = 0; i < 96; i++) {
                var p = argv.add(i * Process.pointerSize).readPointer();
                if (p.isNull()) break;
                parts.push(p);
            }
        } catch (e) { }
        return parts;
    }
    var spawnPins = [];   // keep rewritten argv alive across the spawn call
    ["posix_spawn", "posix_spawnp"].forEach(function (fn) {
        var addr;
        try { addr = Module.findGlobalExportByName(fn); } catch (e) { addr = null; }
        if (!addr) return;
        Interceptor.attach(addr, {
            onEnter: function (args) {
                try {
                    var path = args[1].readCString();
                    if (!path || path.indexOf("WeChatAppEx") < 0) return;
                    var parts = readStrv(args[4]);
                    var isRenderer = false, rtIdx = -1, rtVal = null;
                    for (var i = 0; i < parts.length; i++) {
                        var s = parts[i].readCString();
                        if (s === "--type=renderer") isRenderer = true;
                        if (s !== null && s.indexOf("--wmpf-render-type=") === 0) {
                            rtIdx = i;
                            rtVal = s.substring(19);
                        }
                    }
                    if (!isRenderer || rtIdx < 0 || rtVal !== "0") return;
                    // type 0 = web page renderer (multitab); 6/7 are
                    // prewarm/shell roles and must keep their own type —
                    // rewriting those crashes the first page load.
                    var one = Memory.allocUtf8String("--wmpf-render-type=1");
                    var n = Memory.alloc((parts.length + 1) * Process.pointerSize);
                    for (var j = 0; j < parts.length; j++) {
                        n.add(j * Process.pointerSize).writePointer(
                            j === rtIdx ? one : parts[j]);
                    }
                    n.add(parts.length * Process.pointerSize).writePointer(ptr(0));
                    args[4] = n;
                    spawnPins.push(n, one);
                    if (spawnPins.length > 512) spawnPins.splice(0, 256);
                    send("[sniper] spawn render-type " + rtVal + " -> 1 (" +
                         path.split("/").pop() + ")");
                } catch (e) { }
            }
        });
        send("[+] hooked " + fn + " (argv render-type gate)");
    });

    // ---- (removed) hot __params JSON-setter hook — superseded by the
    // mmcronet C-API capture daemon (net_cronet.py). Hooking the shared
    // JSON setter on every call was a hot path and risked instability.

    recv("swap", function onMessage(msg) {
        swapAppid = msg.appid || null;
        send("[swap] " + (swapAppid ? "TARGET=" + swapAppid : "OFF"));
        recv("swap", onMessage);
    });

    send("[+] sniper armed (stable hooks + appid swap ready)");
}
