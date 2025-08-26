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

// 安全读取函数
function isReadable(ptr, size = 8) {
    try {
        Memory.readByteArray(ptr, size);
        return true;
    } catch (e) {
        return false;
    }
}

function tryReadCString(ptr, maxLength = 32) {
    try {
        const arr = Memory.readByteArray(ptr, maxLength);
        if (arr === null) return "<null>";

        let str = '';
        for (let i = 0; i < arr.length; i++) {
            if (arr[i] === 0) break;
            str += String.fromCharCode(arr[i]);
        }
        return str;
    } catch (e) {
        return "<read-error>";
    }
}

// ======== 开始扫描 0x6CXXXF8 模式 ========
setTimeout(() => {
    console.log("[*] 开始扫描 0x6CXXXF8 模式，寻找 devTools...");

    // 缩小范围：0x6C200F8 ~ 0x6C3FF00
    for (let hi = 0x200; hi <= 0x3FF; hi++) {
        let offset = ptr(`0x6C${hi.toString(16).padStart(3, '0')}F8`);
        let addr = base.add(offset);

        // 跳过已知无效地址
        if (addr.compare(ptr("0x11aeb99f8")) === 0) continue; // 就是之前那个坏地址

        if (!isReadable(addr, 8)) continue;

        let strPtr;
        try {
            strPtr = addr.readPointer();
        } catch (e) {
            continue;
        }

        if (strPtr.isNull()) continue;

        // 验证指针范围：必须是 0x1... 或 0x7... 开头
        if (!strPtr.toString().startsWith("0x1") && 
            !strPtr.toString().startsWith("0x7")) {
            continue;
        }

        if (!isReadable(strPtr, 16)) continue;

        let str = tryReadCString(strPtr);
        if (str === "devTools") {
            console.log(`[+] 找到 devTools！`);
            console.log(`    偏移: ${offset}`);
            console.log(`    指针地址: ${addr}`);
            console.log(`    字符串地址: ${strPtr}`);
            console.log(`    内容: ${str}`);

            // 尝试绕过
            try {
                Memory.protect(strPtr, 32, 'rw-');
                strPtr.writeUtf8String("DevTools");
                Memory.protect(strPtr, 32, 'r--');
                console.log("[+] 已修改为 DevTools，绕过成功！");
                send("bypass_success");
            } catch (e) {
                console.log("[-] 修改失败:", e.message);
            }

            return; // 找到就退出
        }
    }

    console.log("[-] 扫描完成，未找到 devTools 字符串。");
    console.log("[-] 微信 4.0.6 可能已加密或更改结构。");

}, 4000);

// /Users/yangyiming/Desktop/微信4.0.6.app
// frida -F -n /Users/yangyiming/Desktop/微信4.0.6.app -l wechat_scan.js