#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制启用工具 - 通过多种策略尝试启用调试功能
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys

class ForceEnabler:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()

    def onMessage(self, message, data):
        if message['type'] == 'send':
            print(Color.GREEN + str(message['payload']) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def run(self):
        """运行强制启用脚本"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + "  强制启用调试功能工具" + Color.END)
        print(Color.GREEN + "  微信 4.1.4 版本" + Color.END)
        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + f"[+] 微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)
        print()

        # 读取强制启用脚本
        try:
            with open("scripts/force_enable.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/force_enable.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入强制启用脚本..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print()
            print(Color.YELLOW + "="*70 + Color.END)
            print(Color.YELLOW + "  脚本已注入！" + Color.END)
            print(Color.YELLOW + "" + Color.END)
            print(Color.YELLOW + "  现在请执行以下操作：" + Color.END)
            print(Color.YELLOW + "  1. 在微信中打开任意一个小程序" + Color.END)
            print(Color.YELLOW + "  2. 查看小程序界面是否出现调试按钮" + Color.END)
            print(Color.YELLOW + "  3. 尝试右键点击查看是否有开发者工具选项" + Color.END)
            print(Color.YELLOW + "  4. 观察控制台输出的 Hook 信息" + Color.END)
            print(Color.YELLOW + "" + Color.END)
            print(Color.YELLOW + "  按 Ctrl+C 退出" + Color.END)
            print(Color.YELLOW + "="*70 + Color.END)
            print()

            # 保持脚本运行
            try:
                sys.stdin.read()
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
    print(Color.GREEN + "提示：此工具会尝试多种策略强制启用调试功能" + Color.END)
    print(Color.GREEN + "如果想要详细的函数调用分析，请使用 smart_analysis.py" + Color.END)
    print()

    enabler = ForceEnabler()
    enabler.run()

if __name__ == "__main__":
    main()
