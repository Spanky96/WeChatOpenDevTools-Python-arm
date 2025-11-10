// 测试脚本：为微信 4.1.4 尝试不同的 hook 策略

console.log("[*] 开始测试新的 hook 方法...");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send("[-] 模块未找到");
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);

    // 基于搜索结果的地址
    var addresses = {
        xwebadress: "0x8AFC6D5",
        enable_vconsole_str: "0x9293A11",
        dev_tools_str: "0x8AFCBD1"
    };

    console.log("\n[*] 测试策略 1: Hook enable_vconsole 相关函数");

    // 搜索可能调用 enable_vconsole 的函数
    // 在字符串附近搜索函数
    var vconsoleStrAddr = base.add(addresses.enable_vconsole_str);
    console.log("[+] enable_vconsole 字符串地址:", vconsoleStrAddr);
    console.log("[+] 字符串内容:", vconsoleStrAddr.readCString());

    // 策略：Hook 所有包含 "json" 和 "bool" 的导出函数
    console.log("\n[*] 测试策略 2: 枚举可能的 JSON 解析函数");
    var exports = module.enumerateExports();
    var jsonFuncs = [];
    exports.forEach(function(exp) {
        var name = exp.name.toLowerCase();
        if ((name.includes('json') && (name.includes('bool') || name.includes('get'))) ||
            name.includes('config') ||
            name.includes('vconsole')) {
            console.log(`[+] 发现可疑函数: ${exp.name} at ${exp.address}`);
            jsonFuncs.push(exp);
        }
    });

    if (jsonFuncs.length === 0) {
        console.log("[-] 未找到导出的 JSON 函数，可能是内部函数");
    }

    // 策略 3: 通过符号表查找
    console.log("\n[*] 测试策略 3: 枚举符号");
    var symbols = module.enumerateSymbols();
    var relevantSymbols = [];
    symbols.forEach(function(sym) {
        var name = sym.name.toLowerCase();
        if ((name.includes('json') && name.includes('bool')) ||
            (name.includes('vconsole')) ||
            (name.includes('devtool') && !name.includes('chromium'))) {
            console.log(`[+] 发现符号: ${sym.name} at ${sym.address}`);
            relevantSymbols.push(sym);

            // 尝试 hook 看看
            try {
                Interceptor.attach(sym.address, {
                    onEnter: function(args) {
                        console.log(`[HOOK] ${sym.name} 被调用`);
                        // 打印前几个参数
                        for (var i = 0; i < Math.min(4, args.length); i++) {
                            try {
                                var str = args[i].readCString();
                                if (str) {
                                    console.log(`  arg[${i}]: "${str}"`);
                                }
                            } catch(e) {
                                // 不是字符串
                            }
                        }
                    },
                    onLeave: function(retval) {
                        console.log(`[HOOK] ${sym.name} 返回: ${retval}`);
                    }
                });
                console.log(`[+] 已 hook: ${sym.name}`);
            } catch(e) {
                // Hook 失败，跳过
            }
        }
    });

    if (relevantSymbols.length === 0) {
        console.log("[-] 未找到相关符号");
    }

    // 策略 4: 搜索字符串引用
    console.log("\n[*] 测试策略 4: 分析 xweb-enable-inspect 的引用");
    var xwebStr = base.add(addresses.xwebadress);
    console.log("[+] xweb-enable-inspect 地址:", xwebStr);
    console.log("[+] 内容:", xwebStr.readCString());

    console.log("\n[+] 测试 hook 就绪，等待小程序触发...");
    console.log("[!] 请在微信中打开一个小程序来触发相关代码");
}
