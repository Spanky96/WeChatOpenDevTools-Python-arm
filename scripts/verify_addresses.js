// 验证配置文件中的地址是否仍然有效

console.log("[*] 验证旧配置地址...");

var module = Process.findModuleByName('WeChatAppEx Framework');
if (!module) {
    console.log("[-] 未找到 WeChatAppEx Framework");
    send("[-] 模块未找到");
} else {
    var base = module.base;
    console.log("[+] 模块基址:", base);
    console.log("[+] 模块大小:", ptr(module.size).toString());

    // 测试不同版本的配置
    var configs = {
        "13080712": {
            "DevToolStringAddr": "0x6C219F8",
            "WechatWebStringAddr": "0x7258F58",
            "JsonGetBoolFunc": "0x25F4E3C"
        },
        "13080a11": {
            "xwebadress": "0x23F07F4",
            "DevToolStringAddr": "0x6C218F8",
            "WechatWebStringAddr": "0x7258D58",
            "JsonGetBoolFunc": "0x25F4C88"
        }
    };

    console.log("\n" + "=".repeat(70));
    for (var version in configs) {
        console.log(`\n[*] 测试版本 ${version} 的配置:`);
        var config = configs[version];

        for (var key in config) {
            var offset = parseInt(config[key], 16);
            var addr = base.add(offset);

            try {
                console.log(`\n[*] 检查 ${key}:`);
                console.log(`    偏移: ${config[key]}`);
                console.log(`    绝对地址: ${addr}`);

                // 尝试读取字符串
                try {
                    var str = addr.readCString();
                    if (str) {
                        console.log(`    [+] 读取到字符串: "${str}"`);
                    } else {
                        console.log(`    [-] 无法读取字符串`);
                    }
                } catch(e) {
                    console.log(`    [-] 读取字符串失败: ${e.message}`);
                }

                // 尝试读取前 64 字节
                try {
                    var bytes = addr.readByteArray(64);
                    var hexStr = "";
                    var asciiStr = "";
                    var view = new Uint8Array(bytes);
                    for (var i = 0; i < Math.min(32, view.length); i++) {
                        hexStr += ("0" + view[i].toString(16)).slice(-2) + " ";
                        var char = view[i] >= 32 && view[i] < 127 ? String.fromCharCode(view[i]) : ".";
                        asciiStr += char;
                    }
                    console.log(`    十六进制: ${hexStr}`);
                    console.log(`    ASCII: ${asciiStr}`);
                } catch(e) {
                    console.log(`    [-] 读取字节失败: ${e.message}`);
                }

            } catch(e) {
                console.log(`    [-] 检查失败: ${e.message}`);
            }
        }
        console.log("\n" + "-".repeat(70));
    }

    console.log("\n[*] 验证完成");
    send("done");
}
