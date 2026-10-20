#!/usr/bin/env python3
"""Sniper daemon: inject the stable hook set into the WeChatAppEx browser
main process only, plus optional remote-debug appid swapping.

Swap control:
  echo wx0000000000000005 > /tmp/wechat_rea/swap_appid   # enable swap
  rm /tmp/wechat_rea/swap_appid                          # disable

Workflow for debugging ANY mini-program:
  1. enable swap with the target appid
  2. in WeChat DevTools, start remote-debug with YOUR project (permission ok)
  3. the spawned debug renderer runs the TARGET appid instead
  4. full Chrome DevTools attaches to the target mini-program
"""
import fcntl
import os
import subprocess
import time

import json

import frida

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "scripts", "sniper.js")
SWAP_FILE = "/tmp/wechat_rea/swap_appid"


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


def read_swap():
    try:
        v = open(SWAP_FILE).read().strip()
        return v if v.startswith("wx") else ""
    except FileNotFoundError:
        return ""


def main():
    # single-instance lock: multiple snipers double-hook and crash WeChatAppEx
    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    lock = open("/tmp/wechat_rea/sniper.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[sniper] another instance holds the lock, exiting", flush=True)
        return

    code = open(HOOK).read()
    session_obj = None
    last_swap = None
    print("[sniper] watching WeChatAppEx main process...", flush=True)
    while True:
        try:
            pid = find_main()
            if pid and (session_obj is None or session_obj[0] != pid):
                try:
                    s = frida.attach(pid)
                    script = s.create_script(code)
                    def _on_msg(m, d, pid=pid):
                        payload = m.get("payload")
                        if isinstance(payload, str) and payload.startswith("[netreq] "):
                            try:
                                with open("/tmp/wechat_rea/net_events.log", "a") as f:
                                    f.write(json.dumps({"t": time.time(),
                                                        "params": payload[9:]}) + "\n")
                            except OSError:
                                pass
                        print(f"[{time.strftime('%H:%M:%S')}][{pid}] "
                              f"{payload if payload else m.get('description', m)}", flush=True)
                    script.on("message", _on_msg)
                    script.load()
                    session_obj = (pid, s, script)
                    last_swap = None  # re-announce swap state to fresh script
                    print(f"[sniper] armed pid {pid}", flush=True)
                except Exception as exc:
                    print(f"[sniper] attach {pid} failed: {exc}", flush=True)
            elif not pid and session_obj:
                print("[sniper] main process gone, waiting...", flush=True)
                session_obj = None

            # propagate swap-file state changes into the script
            swap = read_swap()
            if session_obj and swap != last_swap:
                session_obj[2].post({"type": "swap", "appid": swap})
                last_swap = swap
        except Exception as exc:
            print(f"[sniper] loop: {exc}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
