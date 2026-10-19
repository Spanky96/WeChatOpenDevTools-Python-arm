#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验性 Hook - 基于已知字符串地址搜索函数引用
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys
import time

class ExperimentalHook:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()

    def onMessage(self, message, data):
        if message['type'] == 'send':
            print(Color.GREEN + str(message['payload']) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def run(self):
        """运行实验性 Hook"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + "  实验性 Hook 工具" + Color.END)
        print(Color.GREEN + "  基于已知地址搜索函数引用" + Color.END)
        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + f"[+] 微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)
        print()

        # 读取实验性 hook 脚本
        try:
            with open("scripts/experimental_hook.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/experimental_hook.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入实验性 Hook 脚本..." + Color.END)
            print(Color.YELLOW + "[*] 这可能需要一些时间，请稍候..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print()
            print(Color.YELLOW + "="*70 + Color.END)
            print(Color.YELLOW + "  脚本已注入并持续运行！" + Color.END)
            print(Color.YELLOW + "" + Color.END)
            print(Color.YELLOW + "  现在请执行以下操作：" + Color.END)
            print(Color.YELLOW + "  1. 关闭当前打开的小程序" + Color.END)
            print(Color.YELLOW + "  2. 重新打开小程序（这很重要！）" + Color.END)
            print(Color.YELLOW + "  3. 在小程序中点击、操作、切换页面" + Color.END)
            print(Color.YELLOW + "  4. 观察控制台输出的函数调用信息" + Color.END)
            print(Color.YELLOW + "  5. 查看是否出现调试按钮" + Color.END)
            print(Color.YELLOW + "" + Color.END)
            print(Color.YELLOW + "  按 Ctrl+C 退出" + Color.END)
            print(Color.YELLOW + "="*70 + Color.END)
            print()

            # 保持脚本运行
            print(Color.GREEN + "[*] 脚本持续运行中，等待小程序操作..." + Color.END)
            print()

            try:
                # 使用循环而不是 stdin.read() 来保持运行
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print(Color.YELLOW + "\n[*] 正在退出..." + Color.END)

            session.detach()
            print(Color.GREEN + "[+] 已断开连接" + Color.END)

        except KeyboardInterrupt:
            print(Color.YELLOW + "\n[*] 已中断" + Color.END)
        except Exception as e:
            print(Color.RED + f"[-] 错误: {e}" + Color.END)
            import traceback
            traceback.print_exc()

def main():
    print(Color.GREEN + "="*70 + Color.END)
    print(Color.GREEN + "  这是实验性的深度 Hook 工具" + Color.END)
    print(Color.GREEN + "  会搜索并 Hook 大量可能的函数" + Color.END)
    print(Color.GREEN + "  可能会影响微信性能，仅用于调试" + Color.END)
    print(Color.GREEN + "="*70 + Color.END)
    print()

    hooker = ExperimentalHook()
    hooker.run()

if __name__ == "__main__":
    main()
