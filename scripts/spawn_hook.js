// Intercept process creation in the WeChat main process by hooking the
// whole exec/spawn family, then append remote-debugging-port for WeChatAppEx.
function findExport(name) {
    try { return Module.findGlobalExportByName(name); } catch (e) { return null; }
}

var TARGET = "/Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/MacOS/WeChatAppEx";
var EXTRA = "--inspect=127.0.0.1:9223";

function readStrv(argv, max) {
    var parts = [];
    try {
        for (var i = 0; i < (max || 64); i++) {
            var p = argv.add(i * Process.pointerSize).readPointer();
            if (p.isNull()) break;
            parts.push(p.readCString());
        }
    } catch (e) { }
    return parts;
}

function armArgv(argv) {
    // returns a new argv char** with EXTRA appended (idempotent)
    var parts = readStrv(argv);
    if (parts.length === 0) { send("[arm] readStrv empty"); return null; }
    send("[arm] argv0=" + parts[0] + " argc=" + parts.length);
    if (parts.indexOf(EXTRA) >= 0) return null;
    if (parts[0].indexOf("WeChatAppEx") < 0) { send("[arm] argv0 mismatch"); return null; }
    parts.push(EXTRA);
    var n = Memory.alloc((parts.length + 1) * Process.pointerSize);
    for (var i = 0; i < parts.length; i++) {
        n.add(i * Process.pointerSize).writePointer(Memory.allocUtf8String(parts[i]));
    }
    n.add(parts.length * Process.pointerSize).writePointer(ptr(0));
    send("[arm] argv armed (" + parts.length + " args)");
    return n;
}

["posix_spawn", "posix_spawnp"].forEach(function (fn) {
    var addr = findExport(fn);
    if (!addr) return;
    Interceptor.attach(addr, {
        onEnter: function (args) {
            try {
                var path = args[1].readCString();
                if (path && path.indexOf("WeChatAppEx") >= 0) {
                    send("[" + fn + "] " + path);
                    // dump raw argv pointer memory for diagnosis
                    try {
                        var dump = [];
                        for (var k = 0; k < 6; k++) {
                            var slot = args[4].add(k * Process.pointerSize).readPointer();
                            dump.push(k + ":" + slot + (slot.isNull() ? "" : "=" + slot.readCString()));
                        }
                        send("[argvdump] " + dump.join(" | "));
                    } catch (e2) { send("[argvdump] err " + e2); }
                    var n = armArgv(args[4]);
                    if (n) args[4] = n;
                }
            } catch (e) { }
        }
    });
    send("[+] hooked " + fn);
});

["execve", "execv", "execvp", "execvP"].forEach(function (fn) {
    var addr = findExport(fn);
    if (!addr) return;
    Interceptor.attach(addr, {
        onEnter: function (args) {
            try {
                var path = args[0].readCString();
                if (path && path.indexOf("WeChatAppEx") >= 0) {
                    send("[" + fn + "] " + path);
                    var n = armArgv(args[1]);
                    if (n) args[1] = n;
                }
            } catch (e) { }
        }
    });
    send("[+] hooked " + fn);
});

var forkAddr = findExport("fork");
if (forkAddr) {
    Interceptor.attach(forkAddr, { onLeave: function (r) { } });
    send("[+] fork hooked (no-op trace)");
}
send("[+] spawn interception ready");
