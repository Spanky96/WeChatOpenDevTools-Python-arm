#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地址查找工具 - 用于查找新版本微信的关键内存地址
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys
import time

class AddressFinder:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()
        self.pid = -1

    def onMessage(self, message, data):
        if message['type'] == 'send':
            print(Color.GREEN + str(message['payload']) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def find_addresses(self):
        """查找 WeChatAppEx 的关键内存地址"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + f"[+] 找到微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)

        # 读取查找地址的脚本
        try:
            with open("scripts/find_addresses_v2.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/find_addresses_v2.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入 Frida 脚本..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print(Color.GREEN + "[+] 脚本已注入，正在搜索地址..." + Color.END)
            print(Color.YELLOW + "[*] 搜索过程可能需要几秒钟..." + Color.END)

            # 等待脚本执行完成
            time.sleep(5)

            print(Color.GREEN + "\n[+] 搜索完成！" + Color.END)
            print(Color.YELLOW + "[*] 请查看上面的输出结果" + Color.END)
            print(Color.YELLOW + "[*] 部分地址（如 JsonGetBoolFunc 和 xwebadress）需要使用 IDA Pro 分析" + Color.END)

            session.detach()

        except Exception as e:
            print(Color.RED + f"[-] 错误: {e}" + Color.END)

def main():
    print(Color.GREEN + "="*60 + Color.END)
    print(Color.GREEN + "  WeChatAppEx 地址查找工具" + Color.END)
    print(Color.GREEN + "  用于支持新版本微信" + Color.END)
    print(Color.GREEN + "="*60 + Color.END)

    finder = AddressFinder()
    finder.find_addresses()

if __name__ == "__main__":
    main()
