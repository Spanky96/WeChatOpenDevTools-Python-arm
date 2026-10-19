#!/usr/bin/env python3
"""Daemon: watch for WeChatAppEx processes and inject devtools hooks.

- Detects the browser main process (no --type=) and attaches within
  milliseconds of it appearing, so child-cmdline building is covered.
- Skips processes already injected (tracked in-memory).
- Prints all frida messages with timestamps.
"""
import os
import subprocess
import sys
import time

import frida

HOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "scripts", "hook_v413.js")


def list_appex_processes():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    mains, children = [], []
    for line in out.splitlines():
        if "MacOS/WeChatAppEx" not in line or "grep" in line:
            continue
        pid = int(line.strip().split()[0])
        if "--type=" in line:
            # only mini-program renderers carry wmpf-render-type
            if "--type=renderer" in line and "wmpf-render-type" in line:
                children.append(pid)
        else:
            mains.append(pid)
    return mains, children


def on_message_factory(pid):
    def on_message(message, data):
        if message["type"] == "send":
            print(f"[{time.strftime('%H:%M:%S')}][{pid}] {message['payload']}",
                  flush=True)
        elif message["type"] == "error":
            print(f"[{time.strftime('%H:%M:%S')}][{pid}] ERROR "
                  f"{message.get('stack', message)}", flush=True)
    return on_message


def inject(pid, sessions, hook_code):
    try:
        session = frida.attach(pid)
        script = session.create_script(hook_code)
        script.on("message", on_message_factory(pid))
        script.load()
        sessions[pid] = session
        print(f"[{time.strftime('%H:%M:%S')}] injected pid {pid}", flush=True)
    except frida.ProcessNotFoundError:
        print(f"[{time.strftime('%H:%M:%S')}] pid {pid} gone", flush=True)
    except Exception as exc:
        print(f"[{time.strftime('%H:%M:%S')}] inject {pid} failed: {exc}",
              flush=True)


def main():
    with open(HOOK_PATH, "r", encoding="utf-8") as f:
        hook_code = f.read()

    sessions = {}
    print(f"[{time.strftime('%H:%M:%S')}] daemon watching WeChatAppEx...", flush=True)
    while True:
        try:
            mains, children = list_appex_processes()
            # drop dead sessions
            for pid in list(sessions):
                if pid not in mains + children:
                    del sessions[pid]
            # inject into the browser main and every fresh mini-program
            # renderer so launch-time switch reads are covered too
            for pid in mains + children:
                if pid not in sessions:
                    inject(pid, sessions, hook_code)
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] loop error: {exc}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
