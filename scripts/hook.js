;
var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send("[-] 模块未找到");
    exit();
}

var base = module.base;
console.log("模块名称:", module.name);
console.log("模块地址:", module.base);
console.log("大小:", module.size);

send("[+] WeChatAppEx 注入成功!");
send("[+] 等待小程序加载...");


// setImmediate(() => {
//     console.log("[+] 启动综合解决方案...");
    
//     // 1. 首先确保能Hook到配置读取
//     let configHooked = false;
    
//     // 2. 搜索show_dev_tools
//     let showDevToolsPattern = '73 68 6F 77 5F 64 65 76 5F 74 6F 6F 6C 73';
    
//     Memory.scan(base, module.size, showDevToolsPattern, {
//         onMatch: function(address, size) {
//             console.log(`[+] 找到配置项: show_dev_tools at ${address}`);
            
//             // 尝试Hook相关的读取函数
//             hookConfigReader(address);
            
//             // 尝试直接修改附近的值
//             modifyNearbyValues(address);
//         },
//         onComplete: function() {
//             console.log("[+] 配置项搜索完成");
            
//             // 如果没有Hook成功，尝试通用方法
//             if (!configHooked) {
//                 console.log("[+] 使用通用Hook方法...");
//                 useGenericHooks();
//             }
//         }
//     });
// });

// function hookConfigReader(configAddress) {
//     console.log("[+] 尝试Hook配置读取器...");
    
//     // 查找引用该配置的代码
//     // 这里使用简单的模式匹配
//     let patterns = ['48 8D 0D', '48 8D 15', '48 8D 35', '48 8D 3D'];
    
//     patterns.forEach(pattern => {
//         Memory.scan(base, module.size, pattern, {
//             onMatch: function(addr, size) {
//                 try {
//                     let offset = addr.add(3).readS32();
//                     let target = addr.add(7).add(offset);
                    
//                     if (target.equals(configAddress)) {
//                         console.log(`[+] 找到配置读取点: ${addr}`);
                        
//                         Interceptor.attach(addr, {
//                             onLeave: function(retval) {
//                                 console.log(`[CONFIG] show_dev_tools读取, 返回值: ${retval}`);
//                                 retval.replace(1);
//                                 console.log(`[CONFIG] 已强制返回true`);
//                                 configHooked = true;
//                             }
//                         });
//                     }
//                 } catch (e) {
//                     // 忽略错误
//                 }
//             },
//             onComplete: function() {}
//         });
//     });
// }

// function modifyNearbyValues(address) {
//     console.log("[+] 尝试修改附近的配置值...");
    
//     // 在配置项后面搜索可能的值
//     for (let i = 0x10; i < 0x50; i++) {
//         try {
//             let valueAddr = address.add(i);
//             let value = valueAddr.readU8();
            
//             if (value === 0) { // 如果是false
//                 console.log(`[+] 在偏移${i}处找到可能的布尔值: ${value}`);
                
//                 Memory.protect(valueAddr, 1, 'rw-');
//                 valueAddr.writeU8(1);
//                 Memory.protect(valueAddr, 1, 'r--');
                
//                 console.log(`[+] 已修改为true`);
//             }
//         } catch (e) {
//             // 忽略错误
//         }
//     }
// }

// function useGenericHooks() {
//     console.log("[+] 使用通用Hook...");
    
//     // Hook常见的配置读取函数
//     let commonFunctions = [
//         'strcmp', 'strncmp', 'strstr',
//         'json_get_bool', 'json_get_value'
//     ];
    
//     commonFunctions.forEach(funcName => {
//         try {
//             let funcAddr = Module.findExportByName(null, funcName);
//             if (funcAddr) {
//                 Interceptor.attach(funcAddr, {
//                     onEnter: function(args) {
//                         this.funcName = funcName;
//                         try {
//                             if (funcName.includes('str')) {
//                                 let argStr = args[0].readCString() || args[1].readCString();
//                                 if (argStr && (argStr.includes('dev') || argStr.includes('tool'))) {
//                                     this.isRelevant = true;
//                                     console.log(`[GENERIC] ${funcName} 处理相关字符串: ${argStr}`);
//                                 }
//                             }
//                         } catch (e) {
//                             // 忽略错误
//                         }
//                     },
//                     onLeave: function(retval) {
//                         if (this.isRelevant && this.funcName.includes('bool')) {
//                             console.log(`[GENERIC] 修改返回值从 ${retval} 到 1`);
//                             retval.replace(1);
//                         }
//                     }
//                 });
//             }
//         } catch (e) {
//             // 忽略错误
//         }
//     });
// }

setImmediate(() => {
    let devToolString = base.add(address.DevToolStringAddr)
    // console.log("[+] devToolString = ", devToolString)
    let oldDevToolString = devToolString.readCString()
    console.log("[+] oldDevToolString = " + oldDevToolString)

    if (oldDevToolString == "devTools"){
        Memory.protect(devToolString, 8, 'rw-')
        devToolString.writeUtf8String("DevTools")
        Memory.protect(devToolString, 8, 'r--')
        // console.log("[+] newDevToolString = ", devToolString.readCString())
    } else {
        console.log("失败", address.DevToolStringAddr, oldDevToolString);
        return;
    }

    let devToolsPageString = base.add(address.WechatWebStringAddr)
    let devToolsPageStringVal = devToolsPageString.readCString()
    console.log("[+] devToolsPageStringVal = ", devToolsPageStringVal);
    // console.log("[+] oldDevToolsPageStringVal = ", devToolsPageStringVal)
    if(devToolsPageStringVal == "https://applet-debug.com/devtools/wechat_app.html"){
        Memory.protect(devToolsPageString, 0x20, 'rw-')
        devToolsPageString.writeUtf8String("https://applet-debug.com/devtools/wechat_web.html")
        // console.log("[+] newDevToolsPageStringVal = ", devToolString.readCString())
        Memory.protect(devToolsPageString, 0x20, 'r--')
    }

    Interceptor.attach(base.add(address.JsonGetBoolFunc), { // json_get_bool
        onEnter(args) {
            this.name = args[1].readCString()
        },

        onLeave(retval) {
            if(this.name == "enable_vconsole"){
                // console.log("[+] enable_vconsole detected, replace retval to true")
                retval.replace(1)
            }
        }
    })
    Interceptor.attach(base.add(address.xwebadress), { // json_get_bool
        onLeave(retval) {
                // console.log("[+] enable_vconsole detected, replace retval to true")
            retval.replace(1)
        }
    }) 
})
