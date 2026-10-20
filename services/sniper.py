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
import signal
import subprocess
import threading
import time
import urllib.request

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


def direct_runtime_connected():
    try:
        with urllib.request.urlopen("http://127.0.0.1:9422/status", timeout=0.4) as response:
            return json.load(response).get("runtime_connected") is True
    except (OSError, ValueError):
        return False


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
    direct_code = None
    if os.environ.get("WECHAT_DEBUG_MODE") == "direct":
        with open(os.path.join(ROOT, "scripts", "direct_debug_probe.js")) as source:
            direct_code = source.read()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    session_obj = None
    last_swap = None

    def detach(current):
        if current is None:
            return
        if current[3] is not None:
            try:
                current[3].exports_sync.dispose()
                current[3].unload()
            except frida.InvalidOperationError:
                pass
        try:
            current[1].detach()
        except frida.InvalidOperationError:
            pass

    print("[sniper] watching WeChatAppEx main process...", flush=True)
    while not stopped.is_set():
        try:
            pid = find_main()
            if session_obj and (session_obj[0] != pid or session_obj[4].is_set()):
                detach(session_obj)
                session_obj = None
            if pid and session_obj is None:
                s = None
                try:
                    s = frida.attach(pid)
                    detached = threading.Event()
                    s.on("detached", lambda *_, event=detached: event.set())
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
                    direct = None
                    if direct_code:
                        ready, rejected = threading.Event(), threading.Event()
                        def on_direct(message, data):
                            _on_msg(message, data)
                            payload = message.get("payload") or {}
                            if message.get("type") == "error":
                                rejected.set()
                                ready.set()
                            elif isinstance(payload, dict) and payload.get("hook") == "probe":
                                if payload.get("event") == "exit":
                                    rejected.set()
                                ready.set()
                        direct = s.create_script(direct_code)
                        direct.on("message", on_direct)
                        direct.load()
                        if not ready.wait(3) or rejected.is_set():
                            raise RuntimeError("direct-mode native fingerprints rejected; see probe log")
                        direct.post({"type": "enable", "enabled": True, "force_load": True,
                                     "bypass_filter": True})
                    session_obj = (pid, s, script, direct, detached)
                    last_swap = None  # re-announce swap state to fresh script
                    print(f"[sniper] armed pid {pid}", flush=True)
                except Exception as exc:
                    if s is not None:
                        s.detach()
                    print(f"[sniper] attach {pid} failed: {exc}", flush=True)
            elif not pid and session_obj:
                print("[sniper] main process gone, waiting...", flush=True)
                session_obj = None

            # propagate swap-file state changes into the script
            if session_obj and session_obj[3] is not None:
                session_obj[3].post({"type": "connection_state", "connected": direct_runtime_connected()})
            swap = read_swap()
            if session_obj and swap != last_swap:
                session_obj[2].post({"type": "swap", "appid": swap})
                last_swap = swap
        except Exception as exc:
            print(f"[sniper] loop: {exc}", flush=True)
        stopped.wait(1)
    detach(session_obj)


if __name__ == "__main__":
    main()
