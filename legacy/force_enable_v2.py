#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制启用工具 V2 - 改进版，避免不兼容的 Hook 方法
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys

class ForceEnablerV2:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()

    def onMessage(self, message, data):
        if message['type'] == 'send':
            payload = message['payload']
            if isinstance(payload, dict) and 'type' in payload:
                if payload['type'] == 'status':
                    print(Color.GREEN + f"\n[统计] 共 Hook {payload['totalHooks']} 个函数" + Color.END)
                    print(Color.GREEN + f"  - 符号函数: {payload['hookedSymbols']}" + Color.END)
                    print(Color.GREEN + f"  - 导出函数: {payload['hookedExports']}" + Color.END)
                else:
                    print(Color.GREEN + str(payload) + Color.END)
            else:
                print(Color.GREEN + str(payload) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def run(self):
        """运行强制启用脚本 V2"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + "  强制启用调试功能工具 V2" + Color.END)
        print(Color.GREEN + "  微信 4.1.4 版本 - 改进版" + Color.END)
        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + f"[+] 微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)
        print()

        # 读取强制启用脚本 V2
        try:
            with open("scripts/force_enable_v2.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/force_enable_v2.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入强制启用脚本 V2..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print()
            print(Color.YELLOW + "="*70 + Color.END)
            print(Color.YELLOW + "  脚本已注入！" + Color.END)
            print(Color.YELLOW + "" + Color.END)
            print(Color.YELLOW + "  现在请执行以下操作：" + Color.END)
            print(Color.YELLOW + "  1. 在已打开的小程序中点击按钮、切换页面" + Color.END)
            print(Color.YELLOW + "  2. 或者关闭小程序重新打开一次" + Color.END)
            print(Color.YELLOW + "  3. 查看小程序界面是否出现调试按钮" + Color.END)
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
    print(Color.GREEN + "这是改进版强制启用工具，修复了兼容性问题" + Color.END)
    print(Color.GREEN + "Hook 范围更精确，避免不必要的系统函数 Hook" + Color.END)
    print()

    enabler = ForceEnablerV2()
    enabler.run()

if __name__ == "__main__":
    main()
