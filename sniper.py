#!/usr/bin/env python3
"""Sniper daemon: inject the single-hook versionType rewriter into the
WeChatAppEx browser main process only. No renderer touching, no other hooks.

Usage: python3 sniper.py            # run in foreground
       nohup python3 sniper.py &    # background
"""
import os
import subprocess
import time

import frida

ROOT = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(ROOT, "scripts", "sniper.js")


def find_main():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "MacOS/WeChatAppEx" not in line or "grep" in line or "--type=" in line:
            continue
        if "crashpad" in line:
            continue
        return int(line.strip().split()[0])
    return None


def main():
    code = open(HOOK).read()
    session = None
    print("[sniper] watching WeChatAppEx main process...", flush=True)
    while True:
        try:
            pid = find_main()
            if pid and (session is None or session[0] != pid):
                try:
                    s = frida.attach(pid)
                    script = s.create_script(code)
                    script.on("message", lambda m, d: print(
                        f"[{time.strftime('%H:%M:%S')}][{pid}] "
                        f"{m.get('payload', m.get('description', m))}", flush=True))
                    script.load()
                    session = (pid, s, script)
                    print(f"[sniper] armed pid {pid}", flush=True)
                except Exception as exc:
                    print(f"[sniper] attach {pid} failed: {exc}", flush=True)
            elif not pid and session:
                print("[sniper] main process gone, waiting...", flush=True)
                session = None
        except Exception as exc:
            print(f"[sniper] loop: {exc}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
