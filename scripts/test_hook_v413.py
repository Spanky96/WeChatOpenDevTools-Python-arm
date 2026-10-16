#!/usr/bin/env python3
"""Quick test: attach frida to main WeChatAppEx process and load hook_v413.js."""
import subprocess
import sys
import time

import frida


def find_main_appex():
    """Find the WeChatAppEx process without --type= (the browser main process)."""
    out = subprocess.run(
        ["ps", "ax", "-o", "pid,command"],
        capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        if "MacOS/WeChatAppEx " in line and "--type=" not in line:
            pid = int(line.strip().split()[0])
            return pid
    return None


def on_message(message, data):
    if message["type"] == "send":
        print(f"[frida] {message['payload']}")
    elif message["type"] == "error":
        print(f"[error] {message['stack']}")
    else:
        print(f"[?] {message}")


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_main_appex()
    if not pid:
        print("[-] main WeChatAppEx process not found; open WeChat with any mini-program first")
        return 1
    print(f"[+] target pid {pid}")

    session = frida.attach(pid)
    with open("scripts/hook_v413.js", "r", encoding="utf-8") as f:
        code = f.read()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    print("[+] script loaded; watching messages for 90s (reopen a mini-program now)")
    time.sleep(90)
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
