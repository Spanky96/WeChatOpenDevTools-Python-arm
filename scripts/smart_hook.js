// 智能 Hook 脚本 - 动态分析并寻找正确的函数
// 用于微信 4.1.4 版本

console.log("[*] ============================================");
console.log("[*] 智能 Hook 分析脚本启动");
console.log("[*] ============================================");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send({ type: "error", message: "模块未找到" });
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);
    console.log("[+] 模块大小:", ptr(module.size).toString());

    var candidateFunctions = [];
    var hookedFunctions = new Set();

    // 已知的字符串地址（从之前的搜索中获得）
    var knownStrings = {
        enable_vconsole: "0x9293A11",
        dev_tools: "0x8AFCBD1",
        xweb_inspect: "0x8AFC6D5"
    };

    console.log("\n[*] ========== 阶段 1: Hook 字符串比较函数 ==========");

    // Hook strcmp, strncmp, strstr 等字符串比较函数
    var stringFuncs = ['strcmp', 'strncmp', 'strstr', 'strcasecmp', 'strncasecmp'];
    stringFuncs.forEach(function(funcName) {
        try {
            var funcAddr = Module.findExportByName(null, funcName);
            if (funcAddr) {
                Interceptor.attach(funcAddr, {
                    onEnter: function(args) {
                        try {
                            var str0 = args[0].readCString();
                            var str1 = args[1].readCString();

                            if ((str0 && (str0.includes('vconsole') || str0.includes('devtool') || str0.includes('debug'))) ||
                                (str1 && (str1.includes('vconsole') || str1.includes('devtool') || str1.includes('debug')))) {
                                console.log(`\n[${funcName}] 检测到相关字符串比较:`);
                                console.log(`  str1: "${str0}"`);
                                console.log(`  str2: "${str1}"`);
                                console.log(`  调用来自: ${Thread.backtrace(this.context, Backtracer.ACCURATE).map(DebugSymbol.fromAddress).join('\\n  ')}`);
                            }
                        } catch(e) {
                            // 忽略读取错误
                        }
                    }
                });
                console.log(`[+] 已 hook ${funcName}`);
                hookedFunctions.add(funcName);
            }
        } catch(e) {
            console.log(`[-] Hook ${funcName} 失败: ${e.message}`);
        }
    });

    console.log("\n[*] ========== 阶段 2: 扫描并 Hook 可疑函数 ==========");

    // 扫描符号表寻找可疑函数
    var symbols = module.enumerateSymbols();
    var relevantSymbols = [];

    symbols.forEach(function(sym) {
        var name = sym.name.toLowerCase();

        // 寻找可能的配置读取函数
        if ((name.includes('json') && (name.includes('bool') || name.includes('get') || name.includes('value'))) ||
            name.includes('config') && (name.includes('get') || name.includes('read')) ||
            name.includes('vconsole') ||
            (name.includes('dev') && name.includes('tool'))) {

            relevantSymbols.push(sym);
            console.log(`[+] 发现可疑符号: ${sym.name}`);

            try {
                Interceptor.attach(sym.address, {
                    onEnter: function(args) {
                        this.funcName = sym.name;
                        this.args = [];

                        // 尝试读取参数（假设最多 4 个参数）
                        for (var i = 0; i < 4; i++) {
                            try {
                                var str = args[i].readCString();
                                if (str && str.length < 100) {
                                    this.args.push(`"${str}"`);
                                } else {
                                    this.args.push(`0x${args[i].toString()}`);
                                }
                            } catch(e) {
                                this.args.push(`0x${args[i].toString()}`);
                            }
                        }

                        // 只记录与关键字相关的调用
                        var argsStr = this.args.join(', ');
                        if (argsStr.includes('vconsole') || argsStr.includes('devtool') || argsStr.includes('debug')) {
                            console.log(`\n[CALL] ${this.funcName}(${argsStr})`);
                        }
                    },
                    onLeave: function(retval) {
                        if (this.args && (this.args.join(',').includes('vconsole') ||
                            this.args.join(',').includes('devtool'))) {
                            console.log(`[RET]  ${this.funcName} => ${retval}`);

                            // 记录这个函数作为候选
                            candidateFunctions.push({
                                name: this.funcName,
                                address: sym.address.sub(base).toString(16),
                                args: this.args,
                                returnValue: retval.toInt32()
                            });
                        }
                    }
                });
                hookedFunctions.add(sym.name);
            } catch(e) {
                // Hook 失败，可能不是函数
            }
        }
    });

    console.log(`[+] 已 hook ${hookedFunctions.size} 个函数`);

    console.log("\n[*] ========== 阶段 3: 监控内存访问 ==========");

    // 监控 enable_vconsole 字符串的访问
    var vconsoleAddr = base.add(knownStrings.enable_vconsole);
    console.log(`[+] enable_vconsole 字符串地址: ${vconsoleAddr}`);
    console.log(`[+] 内容: "${vconsoleAddr.readCString()}"`);

    // 尝试在该地址设置内存访问监控
    try {
        Memory.protect(vconsoleAddr, 16, 'rwx');
        console.log("[+] 已设置内存访问监控（如果支持）");
    } catch(e) {
        console.log("[-] 无法设置内存监控（可能需要更高权限）");
    }

    console.log("\n[*] ========== 阶段 4: 实验性 Hook 策略 ==========");

    // 策略：Hook 所有返回布尔值的函数，如果参数中包含关键字符串，强制返回 true
    console.log("[+] 启用实验性 Hook - 强制启用所有调试相关配置");

    // 定义一个通用的布尔返回值修改器
    function createBoolModifier(targetStr, returnValue) {
        return {
            onEnter: function(args) {
                this.shouldModify = false;
                for (var i = 0; i < 4; i++) {
                    try {
                        var str = args[i].readCString();
                        if (str && str.includes(targetStr)) {
                            this.shouldModify = true;
                            console.log(`[INTERCEPT] 检测到 "${targetStr}" 参数，将强制返回 ${returnValue}`);
                            break;
                        }
                    } catch(e) {}
                }
            },
            onLeave: function(retval) {
                if (this.shouldModify) {
                    console.log(`[MODIFY] 原返回值: ${retval}, 修改为: ${returnValue}`);
                    retval.replace(returnValue);
                }
            }
        };
    }

    // 尝试枚举所有导出函数并选择性 hook
    var exports = module.enumerateExports();
    var hookedCount = 0;

    exports.forEach(function(exp) {
        var name = exp.name.toLowerCase();

        // 只 hook 可能与配置相关的导出函数
        if ((name.includes('get') && (name.includes('bool') || name.includes('config') || name.includes('option'))) ||
            name.includes('enabled') || name.includes('isdebug')) {

            try {
                Interceptor.attach(exp.address, createBoolModifier('vconsole', 1));
                hookedCount++;
                if (hookedCount <= 5) {  // 只显示前几个
                    console.log(`[+] Hook 导出函数: ${exp.name}`);
                }
            } catch(e) {
                // Hook 失败，跳过
            }
        }
    });

    console.log(`[+] 共 hook ${hookedCount} 个导出函数`);

    console.log("\n[*] ========== 准备就绪 ==========");
    console.log("[!] 请在微信中执行以下操作：");
    console.log("[!]   1. 打开任意一个小程序");
    console.log("[!]   2. 查看是否出现调试按钮");
    console.log("[!]   3. 观察控制台输出，寻找被调用的函数");
    console.log("[*] ========================================");

    // 定时报告候选函数
    setInterval(function() {
        if (candidateFunctions.length > 0) {
            console.log(`\n[*] 当前发现 ${candidateFunctions.length} 个候选函数：`);
            candidateFunctions.slice(-5).forEach(function(func) {
                console.log(`  - ${func.name} at 0x${func.address}`);
                console.log(`    参数: ${func.args.join(', ')}`);
                console.log(`    返回: ${func.returnValue}`);
            });
        }
    }, 10000);  // 每 10 秒报告一次
}
