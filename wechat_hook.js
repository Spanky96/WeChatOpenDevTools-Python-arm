// wechat_hook_all_fixed.js
console.log("[*] WeChat 终极 Hook 脚本已加载 (修复版)");

// === 1. Hook NSString isEqualToString: ===
function hookIsEqualToString() {
    try {
        var isEqualToString = ObjC.classes.NSString["- isEqualToString:"];
        if (isEqualToString && isEqualToString.implementation) {
            Interceptor.attach(isEqualToString.implementation, {
                onEnter: function(args) {
                    try {
                        var other = new ObjC.Object(args[2]);
                        var otherStr = other.toString();
                        if (otherStr === "devTools") {
                            console.log("[✅ ObjC] isEqualToString: 拦截 devTools");
                            console.log("    调用栈:\n" + 
                                Thread.backtrace(this.context, Backtracer.ACCURATE)
                                .map(Debug.symbolize)
                                .join("\n    "));
                            this.returnOverride = ptr(1);
                        } else if (otherStr.includes("dev") || otherStr.includes("tool")) {
                            console.log("[🔍 ObjC] 比较字符串: '" + otherStr + "'");
                        }
                    } catch (e) {
                        console.log("[-] 解析 ObjC 字符串失败:", e.message);
                    }
                }
            });
            console.log("[+] Hooked isEqualToString:");
        } else {
            console.log("[-] isEqualToString: 方法不存在或 implementation 为空");
        }
    } catch (e) {
        console.log("[-] Hook isEqualToString 失败:", e.message);
    }
}

// === 2. Hook strcmp ===
function hookStrcmp() {
    try {
        var strcmpPtr = Module.findExportByName(null, 'strcmp');
        if (strcmpPtr) {
            Interceptor.attach(strcmpPtr, {
                onEnter: function(args) {
                    try {
                        var s1 = args[0].readUtf8String();
                        var s2 = args[1].readUtf8String();
                        if (s1 === "devTools" || s2 === "devTools") {
                            console.log(`[✅ C] strcmp 检测到 devTools: "${s1}" vs "${s2}"`);
                            console.log("    调用栈:\n" + 
                                Thread.backtrace(this.context, Backtracer.ACCURATE)
                                .map(Debug.symbolize)
                                .join("\n    "));
                            this.returnOverride = ptr(0); // 强制相等
                        } else if ((s1 && (s1.includes("dev") || s1.includes("tool"))) ||
                                   (s2 && (s2.includes("dev") || s2.includes("tool")))) {
                            console.log(`[🔍 C] strcmp 比较: "${s1}" vs "${s2}"`);
                        }
                    } catch (e) {
                        console.log("[-] 读取 C 字符串失败:", e.message);
                    }
                }
            });
            console.log("[+] Hooked strcmp @ " + strcmpPtr);
        } else {
            console.log("[-] 未找到 strcmp 函数（可能被 strip 或未加载）");
        }
    } catch (e) {
        console.log("[-] Hook strcmp 失败:", e.message);
    }
}

// === 3. Hook strstr ===
function hookStrstr() {
    try {
        var strstrPtr = Module.findExportByName(null, 'strstr');
        if (strstrPtr) {
            Interceptor.attach(strstrPtr, {
                onEnter: function(args) {
                    try {
                        var haystack = args[0].readUtf8String();
                        var needle = args[1].readUtf8String();
                        if (needle === "devTools") {
                            console.log(`[✅ C] strstr 检测到 devTools: "${haystack}" 中搜索 "${needle}"`);
                            console.log("    调用栈:\n" + 
                                Thread.backtrace(this.context, Backtracer.ACCURATE)
                                .map(Debug.symbolize)
                                .join("\n    "));
                            this.returnOverride = args[0]; // 强制返回找到
                        } else if (needle && (needle.includes("dev") || needle.includes("tool"))) {
                            console.log(`[🔍 C] strstr 搜索: "${needle}" in "${haystack?.substr(0, 50)}..."`);
                        }
                    } catch (e) {
                        console.log("[-] 读取 strstr 参数失败:", e.message);
                    }
                }
            });
            console.log("[+] Hooked strstr @ " + strstrPtr);
        } else {
            console.log("[-] 未找到 strstr 函数");
        }
    } catch (e) {
        console.log("[-] Hook strstr 失败:", e.message);
    }
}

// === 4. Hook JSContext evaluateScript: ===
function hookEvaluateScript() {
    try {
        var evaluateScript = ObjC.classes.JSContext["- evaluateScript:"];
        if (evaluateScript && evaluateScript.implementation) {
            Interceptor.attach(evaluateScript.implementation, {
                onEnter: function(args) {
                    try {
                        var script = args[1].readUtf8String();
                        if (script && (script.includes("devTools") || script.includes("vConsole"))) {
                            console.log("[🔍 JS] 执行脚本 (含 devTools): " + script.substring(0, 100) + "...");
                        }
                    } catch (e) {
                        console.log("[-] 读取 JS 脚本失败:", e.message);
                    }
                }
            });
            console.log("[+] Hooked JSContext evaluateScript:");
        } else {
            console.log("[-] 未找到 JSContext evaluateScript 方法");
        }
    } catch (e) {
        console.log("[-] Hook evaluateScript 失败:", e.message);
    }
}

// === 启动所有 Hook ===
setTimeout(function() {
    console.log("[*] 开始安装 Hook...");
    hookIsEqualToString();
    hookStrcmp();
    hookStrstr();
    hookEvaluateScript();
    console.log("[*] 所有 Hook 安装完成，打开小程序触发...");
}, 1000);