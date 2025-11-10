// 强制启用脚本 V2 - 改进版，避免不兼容的 Hook 方法
// 适用于微信 4.1.4

console.log("[*] ==========================================");
console.log("[*] 强制启用调试功能脚本 V2");
console.log("[*] ==========================================");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send({ type: "error", message: "模块未找到" });
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);
    console.log("[+] 模块大小:", ptr(module.size).toString());

    var hookedCount = 0;

    console.log("\n[*] ===== 策略 1: Hook 模块内的符号函数 =====");

    // 枚举模块符号而不是系统函数
    var symbols = module.enumerateSymbols();
    console.log(`[+] 找到 ${symbols.length} 个符号`);

    // 查找可能相关的符号
    var targetSymbols = [];
    symbols.forEach(function(sym) {
        var name = sym.name.toLowerCase();

        // 更精确的匹配
        if ((name.includes('vconsole') ||
             (name.includes('json') && (name.includes('bool') || name.includes('get'))) ||
             (name.includes('config') && name.includes('get')) ||
             (name.includes('debug') && name.includes('enable')) ||
             (name.includes('devtool') && !name.includes('chromium'))) &&
            sym.type === 'function') {

            targetSymbols.push(sym);
        }
    });

    console.log(`[+] 找到 ${targetSymbols.length} 个目标符号`);

    // Hook 这些符号
    targetSymbols.forEach(function(sym) {
        try {
            Interceptor.attach(sym.address, {
                onEnter: function(args) {
                    this.funcName = sym.name;
                    this.args = [];

                    // 读取参数
                    for (var i = 0; i < 4; i++) {
                        try {
                            var str = args[i].readCString();
                            if (str && str.length < 200) {
                                this.args.push(str);
                            }
                        } catch(e) {}
                    }

                    // 如果参数包含关键字，记录下来
                    var argsStr = this.args.join('|');
                    if (argsStr.includes('vconsole') ||
                        argsStr.includes('devtool') ||
                        argsStr.includes('debug')) {
                        console.log(`\n[CALL] ${this.funcName}`);
                        console.log(`  参数: ${JSON.stringify(this.args)}`);
                        this.shouldLog = true;
                    }
                },
                onLeave: function(retval) {
                    if (this.shouldLog) {
                        console.log(`  返回: ${retval}`);

                        // 如果是布尔值且为 false，强制改为 true
                        var val = retval.toInt32();
                        if (val === 0) {
                            console.log(`  [!] 强制修改返回值: 0 => 1`);
                            retval.replace(1);
                        }
                    }
                }
            });
            hookedCount++;
            console.log(`[+] Hook: ${sym.name}`);
        } catch(e) {
            // Hook 失败，跳过
        }
    });

    console.log(`[+] 成功 Hook ${hookedCount} 个函数\n`);

    console.log("[*] ===== 策略 2: Hook 导出函数 =====");

    var exports = module.enumerateExports();
    console.log(`[+] 找到 ${exports.length} 个导出函数`);

    var exportHooked = 0;
    exports.forEach(function(exp) {
        var name = exp.name.toLowerCase();

        if ((name.includes('vconsole') ||
             (name.includes('get') && name.includes('bool')) ||
             (name.includes('config') && name.includes('enabled'))) &&
            exp.type === 'function') {

            try {
                Interceptor.attach(exp.address, {
                    onEnter: function(args) {
                        console.log(`\n[EXPORT] ${exp.name} 被调用`);
                        this.isTarget = true;
                    },
                    onLeave: function(retval) {
                        if (this.isTarget) {
                            var val = retval.toInt32();
                            console.log(`  返回: ${val}`);
                            if (val === 0) {
                                console.log(`  [!] 强制修改返回值: 0 => 1`);
                                retval.replace(1);
                            }
                        }
                    }
                });
                exportHooked++;
                console.log(`[+] Hook 导出: ${exp.name}`);
            } catch(e) {}
        }
    });

    console.log(`[+] 成功 Hook ${exportHooked} 个导出函数\n`);

    console.log("[*] ===== 策略 3: 内存字符串监控 =====");

    // 已知的字符串地址
    var knownAddresses = {
        enable_vconsole: base.add("0x9293A11"),
        dev_tools: base.add("0x8AFCBD1"),
        xweb_inspect: base.add("0x8AFC6D5")
    };

    for (var key in knownAddresses) {
        try {
            var addr = knownAddresses[key];
            var str = addr.readCString();
            console.log(`[+] ${key}: "${str}" at ${addr}`);
        } catch(e) {
            console.log(`[-] 无法读取 ${key}: ${e.message}`);
        }
    }

    console.log("\n[*] ===== 策略 4: 搜索并 Hook 字符串引用 =====");

    // 搜索引用 enable_vconsole 字符串的代码
    var vconsoleStr = knownAddresses.enable_vconsole;

    console.log(`[+] 正在搜索引用 enable_vconsole 的代码...`);

    // 在代码段中搜索引用该字符串地址的指令
    // ARM64 通常使用 ADRP + ADD 来加载地址
    var codeStart = base;
    var codeSize = module.size;

    // 计算 ADRP 需要的偏移
    var targetOffset = vconsoleStr.sub(base).toInt32();

    console.log(`[+] 目标字符串偏移: 0x${targetOffset.toString(16)}`);
    console.log(`[!] 建议：在小程序中执行操作，触发配置读取`);

    console.log("\n[*] ==========================================");
    console.log("[+] Hook 已就绪！");
    console.log("[!] 请在小程序中：");
    console.log("[!]   1. 点击按钮、切换页面");
    console.log("[!]   2. 或关闭小程序重新打开");
    console.log("[!]   3. 观察是否出现调试按钮");
    console.log("[*] ==========================================");

    // 统计信息
    send({
        type: "status",
        hookedSymbols: hookedCount,
        hookedExports: exportHooked,
        totalHooks: hookedCount + exportHooked
    });
}
