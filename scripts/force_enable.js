// 强制启用脚本 - 尝试通过多种方式启用调试功能
// 适用于微信 4.1.4

console.log("[*] ==========================================");
console.log("[*] 强制启用调试功能脚本");
console.log("[*] ==========================================");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);

    // 已知字符串地址
    var addresses = {
        enable_vconsole: base.add("0x9293A11"),
        dev_tools: base.add("0x8AFCBD1"),
        xweb_inspect: base.add("0x8AFC6D5")
    };

    console.log("\n[*] ===== 策略 1: 全局 Hook 布尔返回值 =====");

    // Hook 所有可能返回布尔值的系统函数
    var boolFuncs = [
        'strcmp', 'strncmp', 'strcasecmp', 'strncasecmp',
        'strstr', 'strcasestr'
    ];

    boolFuncs.forEach(function(funcName) {
        try {
            var funcAddr = Module.findExportByName(null, funcName);
            if (funcAddr) {
                Interceptor.attach(funcAddr, {
                    onEnter: function(args) {
                        this.str0 = null;
                        this.str1 = null;
                        try {
                            this.str0 = args[0].readCString();
                            this.str1 = args[1].readCString();
                        } catch(e) {}
                    },
                    onLeave: function(retval) {
                        // 如果比较的是调试相关字符串，修改返回值
                        if (this.str0 && this.str1) {
                            // enable_vconsole 相关
                            if (this.str0.includes('enable') && this.str0.includes('vconsole')) {
                                console.log(`[${funcName}] 拦截 enable_vconsole 检查，强制匹配`);
                                retval.replace(0);  // strcmp 返回 0 表示相等
                            }
                            // devtools 相关
                            else if (this.str0.toLowerCase().includes('devtool') ||
                                     this.str1.toLowerCase().includes('devtool')) {
                                console.log(`[${funcName}] 拦截 devtools 检查`);
                                if (funcName.startsWith('str')) {
                                    retval.replace(ptr(this.str0));  // strstr 返回非空表示找到
                                }
                            }
                            // debug 相关
                            else if ((this.str0.includes('debug') || this.str1.includes('debug')) &&
                                     (this.str0.includes('enable') || this.str1.includes('enable'))) {
                                console.log(`[${funcName}] 拦截 debug 检查`);
                                retval.replace(0);
                            }
                        }
                    }
                });
                console.log(`[+] Hook ${funcName} 成功`);
            }
        } catch(e) {
            console.log(`[-] Hook ${funcName} 失败: ${e.message}`);
        }
    });

    console.log("\n[*] ===== 策略 2: Hook ObjC 方法 =====");

    // 尝试 hook Objective-C 的相关方法
    if (ObjC.available) {
        console.log("[+] Objective-C 运行时可用");

        try {
            // 查找可能的调试相关类
            var classes = Object.keys(ObjC.classes);
            var debugClasses = classes.filter(function(name) {
                var lowerName = name.toLowerCase();
                return lowerName.includes('debug') ||
                       lowerName.includes('devtool') ||
                       lowerName.includes('console') ||
                       lowerName.includes('inspect');
            });

            if (debugClasses.length > 0) {
                console.log(`[+] 发现 ${debugClasses.length} 个可能的调试相关类：`);
                debugClasses.slice(0, 10).forEach(function(className) {
                    console.log(`  - ${className}`);

                    try {
                        // 尝试 hook 类的方法
                        var cls = ObjC.classes[className];
                        var methods = cls.$ownMethods;

                        methods.forEach(function(methodName) {
                            if (methodName.toLowerCase().includes('enable') ||
                                methodName.toLowerCase().includes('visible') ||
                                methodName.toLowerCase().includes('show')) {

                                try {
                                    var method = cls[methodName];
                                    Interceptor.attach(method.implementation, {
                                        onEnter: function(args) {
                                            console.log(`[ObjC] ${className}.${methodName} 被调用`);
                                        },
                                        onLeave: function(retval) {
                                            // 如果返回布尔值，强制返回 YES
                                            if (retval.toInt32() === 0) {
                                                console.log(`[ObjC] 修改返回值为 YES`);
                                                retval.replace(ptr(1));
                                            }
                                        }
                                    });
                                    console.log(`  [+] Hook 方法: ${methodName}`);
                                } catch(e) {
                                    // Hook 失败，跳过
                                }
                            }
                        });
                    } catch(e) {
                        // 跳过
                    }
                });
            }
        } catch(e) {
            console.log(`[-] ObjC Hook 失败: ${e.message}`);
        }
    } else {
        console.log("[-] Objective-C 运行时不可用");
    }

    console.log("\n[*] ===== 策略 3: 暴力搜索并 Hook =====");

    // 扫描所有符号，Hook 任何可能相关的函数
    var symbols = module.enumerateSymbols();
    var hookedCount = 0;
    var maxHooks = 50;  // 限制 hook 数量避免性能问题

    symbols.forEach(function(sym) {
        if (hookedCount >= maxHooks) return;

        var name = sym.name.toLowerCase();

        // 更宽松的匹配条件
        if ((name.includes('vconsole') ||
             (name.includes('debug') && (name.includes('enable') || name.includes('show'))) ||
             (name.includes('dev') && name.includes('tool')) ||
             (name.includes('inspect') && name.includes('enable')) ||
             name.includes('webinspector')) &&
            !name.includes('chromium') &&
            !name.includes('blink')) {

            try {
                Interceptor.attach(sym.address, {
                    onEnter: function(args) {
                        console.log(`[CALL] ${sym.name}`);
                        this.funcName = sym.name;
                    },
                    onLeave: function(retval) {
                        // 如果看起来是布尔返回值，强制返回 1
                        var val = retval.toInt32();
                        if (val === 0 || val === 1) {
                            if (val === 0) {
                                console.log(`[FORCE] ${this.funcName} 返回值从 0 改为 1`);
                                retval.replace(1);
                            }
                        }
                    }
                });
                hookedCount++;
                console.log(`[+] Hook: ${sym.name}`);
            } catch(e) {
                // Hook 失败，可能不是函数
            }
        }
    });

    console.log(`[+] 共 hook ${hookedCount} 个函数`);

    console.log("\n[*] ===== 策略 4: 内存扫描修改 =====");

    // 扫描内存中的配置值并修改
    console.log("[+] 搜索内存中的布尔配置值...");

    // 在 enable_vconsole 字符串附近查找可能的配置值
    try {
        var vconsoleStr = addresses.enable_vconsole;
        console.log(`[+] enable_vconsole 地址: ${vconsoleStr}`);
        console.log(`[+] 内容: "${vconsoleStr.readCString()}"`);

        // 在字符串后面 0x1000 字节范围内查找 0 值（可能是 false）
        for (var offset = 0; offset < 0x1000; offset += 4) {
            try {
                var addr = vconsoleStr.add(offset);
                var value = addr.readU32();

                // 如果是 0，尝试改为 1
                if (value === 0) {
                    try {
                        Memory.protect(addr, 4, 'rw-');
                        addr.writeU32(1);
                        console.log(`[+] 在 +0x${offset.toString(16)} 处修改值: 0 => 1`);
                    } catch(e) {
                        // 无法修改，可能是只读内存
                    }
                }
            } catch(e) {
                // 读取失败，跳过
            }
        }
    } catch(e) {
        console.log(`[-] 内存修改失败: ${e.message}`);
    }

    console.log("\n[*] ==========================================");
    console.log("[+] 所有强制启用策略已应用！");
    console.log("[!] 现在请在微信中打开小程序");
    console.log("[!] 查看是否出现调试按钮或开发者工具");
    console.log("[*] ==========================================");
}
