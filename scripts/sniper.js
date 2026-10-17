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
    Interceptor.attach(base.add(0x8C2730), {
        onEnter: function (args) {
            this.force = false;
            try {
                var k = args[1].readCString();
                if (k === "enable-chrome-inspector" || k === "wmpf-render-type") this.force = true;
            } catch (e) { }
        },
        onLeave: function (retval) {
            if (this.force && retval.toInt32() === 0) {
                send("[sniper] HasSwitch(enable-chrome-inspector) -> true");
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
                             "auto-launch-devtools"];
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

    recv("swap", function onMessage(msg) {
        swapAppid = msg.appid || null;
        send("[swap] " + (swapAppid ? "TARGET=" + swapAppid : "OFF"));
        recv("swap", onMessage);
    });

    send("[+] sniper armed (stable hooks + appid swap ready)");
}
