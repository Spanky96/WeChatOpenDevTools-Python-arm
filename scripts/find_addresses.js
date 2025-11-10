// Frida 脚本：动态搜索 WeChatAppEx Framework 中的关键地址
// 用于支持新版本微信

console.log("[*] 开始搜索关键地址...");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send("[-] 模块未找到");
} else {
    var base = module.base;
    console.log("[+] 模块名称:", module.name);
    console.log("[+] 模块基址:", module.base);
    console.log("[+] 模块大小:", ptr(module.size).toString());

    var results = {
        DevToolStringAddr: null,
        WechatWebStringAddr: null,
        JsonGetBoolFunc: null,
        xwebadress: null
    };

    // 1. 搜索 "devTools" 字符串
    console.log("\n[*] 搜索 'devTools' 字符串...");
    var devToolsPattern = '64 65 76 54 6F 6F 6C 73 00'; // "devTools\0"
    Memory.scan(base, module.size, devToolsPattern, {
        onMatch: function(address, size) {
            var offset = address.sub(base);
            console.log("[+] 找到 'devTools' at offset: 0x" + offset.toString(16).toUpperCase());
            console.log("    绝对地址: " + address);
            if (!results.DevToolStringAddr) {
                results.DevToolStringAddr = "0x" + offset.toString(16).toUpperCase();
            }
        },
        onComplete: function() {
            console.log("[*] 'devTools' 搜索完成");
        }
    });

    // 2. 搜索 "https://applet-debug.com/devtools/wechat_app.html"
    console.log("\n[*] 搜索 wechat_app.html URL...");
    var urlPattern = '68 74 74 70 73 3A 2F 2F 61 70 70 6C 65 74 2D 64 65 62 75 67 2E 63 6F 6D 2F 64 65 76 74 6F 6F 6C 73 2F 77 65 63 68 61 74 5F 61 70 70 2E 68 74 6D 6C';
    Memory.scan(base, module.size, urlPattern, {
        onMatch: function(address, size) {
            var offset = address.sub(base);
            console.log("[+] 找到 wechat_app.html URL at offset: 0x" + offset.toString(16).toUpperCase());
            console.log("    绝对地址: " + address);
            if (!results.WechatWebStringAddr) {
                results.WechatWebStringAddr = "0x" + offset.toString(16).toUpperCase();
            }
        },
        onComplete: function() {
            console.log("[*] URL 搜索完成");
        }
    });

    // 3. 搜索 "enable_vconsole" 字符串
    console.log("\n[*] 搜索 'enable_vconsole' 字符串...");
    var vconsolePattern = '65 6E 61 62 6C 65 5F 76 63 6F 6E 73 6F 6C 65'; // "enable_vconsole"
    var vconsoleRefs = [];
    Memory.scan(base, module.size, vconsolePattern, {
        onMatch: function(address, size) {
            var offset = address.sub(base);
            console.log("[+] 找到 'enable_vconsole' at offset: 0x" + offset.toString(16).toUpperCase());
            vconsoleRefs.push(address);
        },
        onComplete: function() {
            console.log("[*] 'enable_vconsole' 搜索完成，找到", vconsoleRefs.length, "个引用");

            // 搜索引用这个字符串的代码
            if (vconsoleRefs.length > 0) {
                console.log("\n[*] 搜索 enable_vconsole 的交叉引用...");
                var stringAddr = vconsoleRefs[0];

                // 在代码段搜索引用该地址的指令 (LEA 指令等)
                // ARM64 中通常是 ADRP + ADD 指令对
                console.log("[*] 正在分析可能的函数引用...");
                console.log("[!] 建议使用 IDA Pro 查找字符串 'enable_vconsole' 的交叉引用");
                console.log("[!] 找到类似 'sub_XXXXXX(v53, \"enable_vconsole\", 0)' 的函数调用");
            }
        }
    });

    // 4. 搜索 "xweb-enable-inspect"
    console.log("\n[*] 搜索 'xweb-enable-inspect' 字符串...");
    var xwebPattern = '78 77 65 62 2D 65 6E 61 62 6C 65 2D 69 6E 73 70 65 63 74'; // "xweb-enable-inspect"
    Memory.scan(base, module.size, xwebPattern, {
        onMatch: function(address, size) {
            var offset = address.sub(base);
            console.log("[+] 找到 'xweb-enable-inspect' at offset: 0x" + offset.toString(16).toUpperCase());
            console.log("    绝对地址: " + address);
        },
        onComplete: function() {
            console.log("[*] 'xweb-enable-inspect' 搜索完成");
        }
    });

    // 5. 搜索 "closeNetLog" (devTools 附近)
    console.log("\n[*] 搜索 'closeNetLog' 字符串...");
    var closeNetLogPattern = '63 6C 6F 73 65 4E 65 74 4C 6F 67'; // "closeNetLog"
    Memory.scan(base, module.size, closeNetLogPattern, {
        onMatch: function(address, size) {
            var offset = address.sub(base);
            console.log("[+] 找到 'closeNetLog' at offset: 0x" + offset.toString(16).toUpperCase());
            console.log("    绝对地址: " + address);
        },
        onComplete: function() {
            console.log("[*] 'closeNetLog' 搜索完成");
        }
    });

    // 等待所有扫描完成后输出总结
    setTimeout(function() {
        console.log("\n" + "=".repeat(60));
        console.log("搜索结果汇总:");
        console.log("=".repeat(60));
        console.log("DevToolStringAddr:", results.DevToolStringAddr || "未找到");
        console.log("WechatWebStringAddr:", results.WechatWebStringAddr || "未找到");
        console.log("JsonGetBoolFunc:", results.JsonGetBoolFunc || "需要通过 IDA 分析");
        console.log("xwebadress:", results.xwebadress || "需要通过 IDA 分析");
        console.log("=".repeat(60));
        console.log("\n[!] 注意：JsonGetBoolFunc 和 xwebadress 需要通过代码分析获取");
        console.log("[!] 建议使用 IDA Pro 打开 WeChatAppEx Framework 进行分析");
        console.log("[!] 模块路径: /Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/WeChatAppEx Framework.framework/Versions/C/WeChatAppEx Framework");

        send(JSON.stringify(results, null, 2));
    }, 3000);
}
