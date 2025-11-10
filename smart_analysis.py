#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能分析工具 - 动态寻找正确的函数地址
"""

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida
import sys
import json
import time

class SmartAnalyzer:
    def __init__(self):
        self.wechatutils = WechatUtils()
        self.device = frida.get_local_device()
        self.candidates = []

    def onMessage(self, message, data):
        if message['type'] == 'send':
            payload = message['payload']

            # 如果是候选函数信息，保存下来
            if isinstance(payload, dict) and 'type' in payload:
                if payload['type'] == 'candidate':
                    self.candidates.append(payload)
            else:
                print(Color.GREEN + str(payload) + Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'] + Color.END)

    def analyze(self):
        """运行智能分析"""
        pid, version = self.wechatutils.get_wechat_pid_and_version_mac()

        if pid is None or version is None:
            print(Color.RED + "[-] 未找到微信进程或版本信息" + Color.END)
            return

        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + "  智能 Hook 分析工具" + Color.END)
        print(Color.GREEN + "  用于微信 4.1.4 动态分析" + Color.END)
        print(Color.GREEN + "="*70 + Color.END)
        print(Color.GREEN + f"[+] 微信进程 PID: {pid}" + Color.END)
        print(Color.GREEN + f"[+] WeChatAppEx 版本: {version}" + Color.END)
        print()

        # 读取智能 hook 脚本
        try:
            with open("scripts/smart_hook.js", "r", encoding="utf-8") as f:
                script_code = f.read()
        except FileNotFoundError:
            print(Color.RED + "[-] 未找到 scripts/smart_hook.js" + Color.END)
            return

        # 注入脚本
        try:
            print(Color.GREEN + "[+] 正在注入智能 Hook 脚本..." + Color.END)
            session = frida.attach(pid)
            script = session.create_script(script_code)
            script.on("message", self.onMessage)
            script.load()

            print(Color.GREEN + "[+] Hook 已就绪！" + Color.END)
            print()
            print(Color.YELLOW + "="*70 + Color.END)
            print(Color.YELLOW + "  重要提示：" + Color.END)
            print(Color.YELLOW + "  1. 现在请在微信中打开任意一个小程序" + Color.END)
            print(Color.YELLOW + "  2. 观察控制台输出的函数调用信息" + Color.END)
            print(Color.YELLOW + "  3. 脚本会自动尝试启用调试功能" + Color.END)
            print(Color.YELLOW + "  4. 按 Ctrl+C 结束分析并保存结果" + Color.END)
            print(Color.YELLOW + "="*70 + Color.END)
            print()

            # 保持脚本运行
            try:
                sys.stdin.read()
            except KeyboardInterrupt:
                print(Color.YELLOW + "\n[*] 收到中断信号，正在保存分析结果..." + Color.END)

            # 保存候选函数
            if self.candidates:
                print(Color.GREEN + f"\n[+] 发现 {len(self.candidates)} 个候选函数" + Color.END)
                with open("analysis_results.json", "w", encoding="utf-8") as f:
                    json.dump(self.candidates, f, indent=2, ensure_ascii=False)
                print(Color.GREEN + "[+] 结果已保存到 analysis_results.json" + Color.END)
            else:
                print(Color.YELLOW + "\n[*] 未发现候选函数" + Color.END)
                print(Color.YELLOW + "[*] 可能需要在微信中执行更多操作来触发相关代码" + Color.END)

            session.detach()
            print(Color.GREEN + "\n[+] 分析完成！" + Color.END)

        except KeyboardInterrupt:
            print(Color.YELLOW + "\n[*] 分析已中断" + Color.END)
        except Exception as e:
            print(Color.RED + f"[-] 错误: {e}" + Color.END)
            import traceback
            traceback.print_exc()

def main():
    analyzer = SmartAnalyzer()
    analyzer.analyze()

if __name__ == "__main__":
    main()
