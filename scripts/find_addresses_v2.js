// 增强版地址查找脚本 - 搜索更多可能的变体

console.log("[*] 开始增强搜索...");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send("[-] 模块未找到");
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);
    console.log("[+] 模块大小:", ptr(module.size).toString());

    var results = {};

    // 搜索所有可能的 devtools 相关字符串
    var devToolsVariants = [
        { name: "devTools", pattern: "64 65 76 54 6F 6F 6C 73" },
        { name: "DevTools", pattern: "44 65 76 54 6F 6F 6C 73" },
        { name: "dev_tools", pattern: "64 65 76 5F 74 6F 6F 6C 73" },
        { name: "devtool", pattern: "64 65 76 74 6F 6F 6C" },
        { name: "developer", pattern: "64 65 76 65 6C 6F 70 65 72" },
        { name: "debug", pattern: "64 65 62 75 67" }
    ];

    console.log("\n[*] 搜索 DevTools 相关字符串变体...");
    devToolsVariants.forEach(function(variant) {
        Memory.scan(base, module.size, variant.pattern, {
            onMatch: function(address, size) {
                var offset = address.sub(base);
                var str = address.readCString();
                console.log(`[+] 找到 '${variant.name}': "${str}" at offset: 0x${offset.toString(16).toUpperCase()}`);
                if (!results[variant.name]) {
                    results[variant.name] = "0x" + offset.toString(16).toUpperCase();
                }
            },
            onComplete: function() {}
        });
    });

    // 搜索调试相关 URL
    var urlPatterns = [
        { name: "wechat_app.html", pattern: "77 65 63 68 61 74 5F 61 70 70 2E 68 74 6D 6C" },
        { name: "wechat_web.html", pattern: "77 65 63 68 61 74 5F 77 65 62 2E 68 74 6D 6C" },
        { name: "applet-debug.com", pattern: "61 70 70 6C 65 74 2D 64 65 62 75 67 2E 63 6F 6D" },
        { name: "devtools", pattern: "64 65 76 74 6F 6F 6C 73" }
    ];

    console.log("\n[*] 搜索调试 URL 相关字符串...");
    urlPatterns.forEach(function(pattern) {
        Memory.scan(base, module.size, pattern.pattern, {
            onMatch: function(address, size) {
                var offset = address.sub(base);
                // 尝试读取更长的字符串
                try {
                    var str = address.readCString(100);
                    console.log(`[+] 找到 '${pattern.name}': "${str}" at offset: 0x${offset.toString(16).toUpperCase()}`);
                } catch(e) {
                    console.log(`[+] 找到 '${pattern.name}' at offset: 0x${offset.toString(16).toUpperCase()}`);
                }
            },
            onComplete: function() {}
        });
    });

    // 搜索配置相关字符串
    var configPatterns = [
        { name: "enable_vconsole", pattern: "65 6E 61 62 6C 65 5F 76 63 6F 6E 73 6F 6C 65" },
        { name: "show_dev_tools", pattern: "73 68 6F 77 5F 64 65 76 5F 74 6F 6F 6C 73" },
        { name: "enable_debug", pattern: "65 6E 61 62 6C 65 5F 64 65 62 75 67" },
        { name: "xweb-enable-inspect", pattern: "78 77 65 62 2D 65 6E 61 62 6C 65 2D 69 6E 73 70 65 63 74" },
        { name: "closeNetLog", pattern: "63 6C 6F 73 65 4E 65 74 4C 6F 67" },
        { name: "openNetLog", pattern: "6F 70 65 6E 4E 65 74 4C 6F 67" }
    ];

    console.log("\n[*] 搜索配置相关字符串...");
    configPatterns.forEach(function(pattern) {
        Memory.scan(base, module.size, pattern.pattern, {
            onMatch: function(address, size) {
                var offset = address.sub(base);
                var str = address.readCString();
                console.log(`[+] 找到 '${pattern.name}': "${str}" at offset: 0x${offset.toString(16).toUpperCase()}`);
                if (!results[pattern.name]) {
                    results[pattern.name] = "0x" + offset.toString(16).toUpperCase();
                }
            },
            onComplete: function() {}
        });
    });

    // 等待扫描完成
    setTimeout(function() {
        console.log("\n" + "=".repeat(70));
        console.log("搜索结果汇总:");
        console.log("=".repeat(70));
        for (var key in results) {
            console.log(`${key}: ${results[key]}`);
        }
        console.log("=".repeat(70));
        send(JSON.stringify(results, null, 2));
    }, 4000);
}
