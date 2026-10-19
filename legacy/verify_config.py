#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置验证工具 - 验证旧配置地址是否仍然有效
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys
import time

class ConfigVerifier:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()

    def onMessage(self, message, data):
        if message['type'] == 'send':
            print(Color.GREEN + str(message['payload']) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def verify(self):
        """验证配置地址"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + f"[+] 找到微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)

        # 读取验证脚本
        try:
            with open("scripts/verify_addresses.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/verify_addresses.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入验证脚本..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print(Color.YELLOW + "[*] 验证中，请稍候..." + Color.END)
            time.sleep(3)

            session.detach()

        except Exception as e:
            print(Color.RED + f"[-] 错误: {e}" + Color.END)

def main():
    print(Color.GREEN + "="*60 + Color.END)
    print(Color.GREEN + "  配置地址验证工具" + Color.END)
    print(Color.GREEN + "="*60 + Color.END)

    verifier = ConfigVerifier()
    verifier.verify()

if __name__ == "__main__":
    main()
