// SNIPER MODE - two surgical hooks, main process only.
//
// 1. JsonGetInt(versionType) -> 1 : mini-programs run in dev mode
//    (vConsole + official debug menu) - VERIFIED WORKING.
// 2. HasSwitch("enable-chrome-inspector") -> true : activates WeChat's
//    built-in CDP infrastructure (endpoint generator 0x221DB58 + HTTP
//    handler 0x5325xxx) for webview pages - under test.
var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    send("[-] WeChatAppEx Framework not found");
} else {
    var base = module.base;

    Interceptor.attach(base.add(0x564DB00), {   // JsonGetInt(json, key, default)
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

    Interceptor.attach(base.add(0x8C2730), {    // CommandLine::HasSwitch
        onEnter: function (args) {
            this.force = false;
            try {
                if (args[1].readCString() === "enable-chrome-inspector") this.force = true;
            } catch (e) { }
        },
        onLeave: function (retval) {
            if (this.force && retval.toInt32() === 0) {
                send("[sniper] HasSwitch(enable-chrome-inspector) -> true");
                retval.replace(ptr(1));
            }
        }
    });

    // 3. Arm every spawned child cmdline with the xweb inspect switches so
    //    renderers believe remote debugging is on and dial the xweb server.
    var appendSwitchFlue = new NativeFunction(base.add(0x1928DB4), 'void',
        ['pointer', 'pointer', 'uint64', 'pointer', 'pointer']);
    var INSPECT_SWITCHES = ["xweb-enable-inspect", "enable-vconsole",
                             "auto-launch-devtools"];
    Interceptor.attach(base.add(0x525721C), {   // child cmdline builder
        onEnter: function (args) {
            this.builder = args[1];
        },
        onLeave: function (retval) {
            if (!this.builder || this.builder.isNull()) return;
            try {
                INSPECT_SWITCHES.forEach(function (s) {
                    var buf = Memory.allocUtf8String(s);
                    appendSwitchFlue(this.builder, buf, uint64(s.length), ptr(0), ptr(0));
                }, this);
                send("[sniper] child cmdline armed with inspect switches");
            } catch (e) {
                send("[sniper] child arm failed: " + e);
            }
        }
    });

    // 4. JSON bool getter: the launch sequence queries remote_debug_endpoint
    //    via JsonGetBool - forcing true may kick off the xweb debug session.
    Interceptor.attach(base.add(0xF1ADB8), {
        onEnter: function (args) {
            this.force = false;
            try {
                var k = args[1].readCString();
                if (k === "remote_debug_endpoint" || k === "enable_vconsole"
                        || k === "is_game_debug") {
                    this.force = true;
                }
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

send("[+] sniper armed: versionType=1 + chrome-inspector + inspect switches + debug bools");
}
