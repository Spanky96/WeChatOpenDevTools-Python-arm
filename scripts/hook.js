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

setImmediate(() => {
    let devToolString = base.add(address.DevToolStringAddr)
    let oldDevToolString = devToolString.readCString()
    console.log("[+] oldDevToolString = " + oldDevToolString)

    if (oldDevToolString == "devTools"){
        Memory.protect(devToolString, 8, 'rw-')
        devToolString.writeUtf8String("DevTools")
        Memory.protect(devToolString, 8, 'r--')
    } else {
        console.log("失败", address.DevToolStringAddr, oldDevToolString);
        return;
    }

    let devToolsPageString = base.add(address.WechatWebStringAddr)
    let devToolsPageStringVal = devToolsPageString.readCString()
    console.log("[+] devToolsPageStringVal = ", devToolsPageStringVal);
    if(devToolsPageStringVal == "https://applet-debug.com/devtools/wechat_app.html"){
        Memory.protect(devToolsPageString, 0x20, 'rw-')
        devToolsPageString.writeUtf8String("https://applet-debug.com/devtools/wechat_web.html")
        Memory.protect(devToolsPageString, 0x20, 'r--')
    }

    Interceptor.attach(base.add(address.JsonGetBoolFunc), { // json_get_bool
        onEnter(args) {
            this.name = args[1].readCString()
        },

        onLeave(retval) {
            if(this.name == "enable_vconsole"){
                retval.replace(1)
            }
        }
    })
    if (address.xwebadress) {
        Interceptor.attach(base.add(address.xwebadress), {
            onLeave(retval) {
                retval.replace(1)
            }
        })
    }
})
