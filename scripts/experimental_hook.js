// 实验性 Hook - 基于已知字符串地址搜索函数引用
// 适用于微信 4.1.4

console.log("[*] ==========================================");
console.log("[*] 实验性 Hook 脚本");
console.log("[*] 搜索字符串引用并 Hook 相关函数");
console.log("[*] ==========================================");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);

    // 已知的字符串偏移
    var addresses = {
        enable_vconsole_offset: 0x9293A11,
        dev_tools_offset: 0x8AFCBD1,
        xweb_inspect_offset: 0x8AFC6D5
    };

    // 计算绝对地址
    var vconsoleAddr = base.add(addresses.enable_vconsole_offset);
    var devToolsAddr = base.add(addresses.dev_tools_offset);
    var xwebAddr = base.add(addresses.xweb_inspect_offset);

    console.log("[+] enable_vconsole:", vconsoleAddr, "=>", vconsoleAddr.readCString());
    console.log("[+] dev_tools:", devToolsAddr, "=>", devToolsAddr.readCString());
    console.log("[+] xweb_inspect:", xwebAddr, "=>", xwebAddr.readCString());

    console.log("\n[*] ===== 搜索引用这些字符串的代码 =====");

    // 在代码段搜索引用这些地址的指令
    // ARM64 通常使用 ADRP + ADD 指令对来加载地址

    function findStringReferences(targetAddr, name) {
        console.log(`\n[*] 搜索 ${name} 的引用...`);

        var found = [];
        var scanStart = base;
        var scanSize = module.size;

        // 搜索模式：寻找可能引用该地址的代码
        // 这是一个简化的方法，在实际中需要更复杂的分析

        // 计算相对偏移（用于 ADRP 指令）
        var offset = targetAddr.sub(base);
        var offsetInt = offset.toInt32();

        console.log(`[+] 目标偏移: 0x${offsetInt.toString(16)}`);

        // 尝试搜索附近的函数
        // 在目标地址前后搜索可能的函数起始点
        var searchRange = 0x10000;  // 搜索 64KB 范围

        for (var i = -searchRange; i < searchRange; i += 4) {
            try {
                var addr = targetAddr.add(i);

                // 读取指令（4字节）
                var instr = addr.readU32();

                // 检查是否是函数序言（SUB SP, SP, #xxx 或 STP X29, X30, [SP, #xxx]）
                // SUB SP, SP, #xxx: 0xD10xxxFF (xx 是立即数)
                // STP X29, X30, [SP, #-xxx]!: 0xA9Bxxxfd

                if ((instr & 0xFFC003FF) === 0xD10003FF ||  // SUB SP
                    (instr & 0xFFC003E0) === 0xA9800000) {  // STP

                    console.log(`[+] 可能的函数起始: ${addr} (偏移: ${i >= 0 ? '+' : ''}0x${i.toString(16)})`);
                    found.push({
                        address: addr,
                        offset: addr.sub(base),
                        relativeToString: i
                    });
                }
            } catch(e) {
                // 读取失败，跳过
            }
        }

        return found;
    }

    var vconsoleRefs = findStringReferences(vconsoleAddr, "enable_vconsole");

    console.log(`\n[*] 找到 ${vconsoleRefs.length} 个可能的函数`);

    // Hook 前几个候选函数
    console.log("\n[*] ===== Hook 候选函数 =====");

    var maxHooks = 10;
    vconsoleRefs.slice(0, maxHooks).forEach(function(ref, index) {
        try {
            Interceptor.attach(ref.address, {
                onEnter: function(args) {
                    console.log(`\n[HOOK-${index}] 函数被调用 at ${ref.address}`);
                    console.log(`  偏移: 0x${ref.offset.toString(16)}`);

                    // 尝试读取参数
                    for (var i = 0; i < 4; i++) {
                        try {
                            var str = args[i].readCString();
                            if (str && str.length < 100) {
                                console.log(`  arg[${i}]: "${str}"`);
                            }
                        } catch(e) {}
                    }

                    this.hookIndex = index;
                },
                onLeave: function(retval) {
                    console.log(`[HOOK-${this.hookIndex}] 返回: ${retval}`);

                    // 如果返回布尔值 0，尝试改为 1
                    var val = retval.toInt32();
                    if (val === 0) {
                        console.log(`[!] 强制修改返回值: 0 => 1`);
                        retval.replace(1);
                    }
                }
            });
            console.log(`[+] Hook 函数 #${index}: ${ref.address} (0x${ref.offset.toString(16)})`);
        } catch(e) {
            console.log(`[-] Hook 失败 #${index}: ${e.message}`);
        }
    });

    console.log("\n[*] ===== 尝试暴力搜索所有函数 =====");

    // 扫描整个代码段，找到所有可能的函数
    var allFunctions = [];
    var step = 0x1000;  // 每 4KB 扫描一次
    var funcCount = 0;

    console.log("[*] 扫描代码段（这可能需要一些时间）...");

    for (var offset = 0; offset < module.size && funcCount < 100; offset += step) {
        try {
            var addr = base.add(offset);
            var instr = addr.readU32();

            // 检查函数序言
            if ((instr & 0xFFC003FF) === 0xD10003FF ||
                (instr & 0xFFC003E0) === 0xA9800000) {

                funcCount++;

                // 尝试hook这个函数
                try {
                    Interceptor.attach(addr, {
                        onEnter: function(args) {
                            // 检查参数中是否有我们关心的字符串
                            for (var i = 0; i < 4; i++) {
                                try {
                                    var str = args[i].readCString();
                                    if (str && (str.includes('vconsole') ||
                                                str.includes('devtool') ||
                                                str.includes('debug') ||
                                                str.includes('xweb'))) {
                                        console.log(`\n[SCAN] 发现相关函数调用!`);
                                        console.log(`  地址: ${addr}`);
                                        console.log(`  偏移: 0x${addr.sub(base).toString(16)}`);
                                        console.log(`  参数: "${str}"`);
                                        this.shouldModify = true;
                                    }
                                } catch(e) {}
                            }
                        },
                        onLeave: function(retval) {
                            if (this.shouldModify) {
                                var val = retval.toInt32();
                                if (val === 0) {
                                    console.log(`  [!] 强制返回 1`);
                                    retval.replace(1);
                                }
                            }
                        }
                    });
                } catch(e) {
                    // Hook失败
                }
            }
        } catch(e) {
            // 读取失败
        }
    }

    console.log(`[+] 扫描完成，hook了 ${funcCount} 个函数`);

    console.log("\n[*] ==========================================");
    console.log("[+] 实验性 Hook 已就绪");
    console.log("[!] 现在请：");
    console.log("[!]   1. 关闭并重新打开小程序");
    console.log("[!]   2. 在小程序中执行各种操作");
    console.log("[!]   3. 观察控制台输出");
    console.log("[*] ==========================================");
}
