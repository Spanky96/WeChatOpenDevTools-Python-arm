// SNIPER MODE - single surgical hook on the versionType READ path.
// 0x6242EB0 does: value = JsonGetInt(launchCfg, "versionType", 1)
// and that value flows into every renderer's launch config (__wxConfig).
// Forcing the read to 1 (develop) makes release mini-programs run in
// dev mode: vConsole + the official debug menu entry.
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
    send("[+] sniper armed on JsonGetInt(versionType) -> 1");
}
