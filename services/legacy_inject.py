#!/usr/bin/env python3
"""Legacy daemon: address-table devtools hook for pre-4.x WeChat (3.8.x).

main.py spawns this instead of the sniper stack when the detected WeChat
build has an address_*_arm.json config. Same injection as the original
main.py flow — "var address=<address_<id>_arm.json>" + scripts/hook.js —
but re-arms automatically after WeChat restarts.

One-shot attach (old `main.py -p`): python3 services/legacy_inject.py -p 123
"""
import argparse
import fcntl
import os
import signal
import sys
import threading
import time

import frida

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import wechat_detect

LOCK = "/tmp/wechat_rea/legacy.lock"


def build_hookcode(version):
    with open(os.path.join(ROOT, "configs",
                           f"address_{version}_arm.json")) as f:
        addresses = f.read()
    with open(os.path.join(ROOT, "scripts", "hook.js"),
              encoding="utf-8") as f:
        hookcode = f.read()
    return "var address=" + addresses + hookcode


def inject(pid, version):
    """Attach + load the legacy hook; returns (pid, session, detached)."""
    session = frida.attach(pid)
    detached = threading.Event()
    session.on("detached", lambda *_: detached.set())

    def on_message(message, _data):
        payload = (message.get("payload") if message.get("type") == "send"
                   else message.get("description") or message)
        print(f"[{time.strftime('%H:%M:%S')}][{pid}] {payload}", flush=True)

    script = session.create_script(build_hookcode(version))
    script.on("message", on_message)
    script.load()
    print(f"[legacy] armed pid {pid} (version {version})", flush=True)
    return pid, session, detached


def detach(armed):
    _pid, session, _detached = armed
    try:
        session.detach()
    except frida.InvalidOperationError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--pid", type=int,
                        help="one-shot: inject into this WeChatAppEx pid")
    parser.add_argument("--version",
                        help="version id override for --pid (e.g. 13080712)")
    args = parser.parse_args()

    os.makedirs("/tmp/wechat_rea", exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[legacy] another instance holds the lock, exiting", flush=True)
        return

    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())

    if args.pid:
        version = args.version or wechat_detect.extract_version_id(
            wechat_detect.command_for_pid(args.pid))
        if not version:
            print("[legacy] cannot determine version for pid; "
                  "pass --version", flush=True)
            return
        try:
            armed = inject(args.pid, version)
        except Exception as exc:
            print(f"[legacy] attach {args.pid} failed: {exc}", flush=True)
            return
        while not stopped.is_set() and not armed[2].is_set():
            stopped.wait(1)
        detach(armed)
        return

    print("[legacy] watching for pre-4.x WeChatAppEx...", flush=True)
    armed = None
    while not stopped.is_set():
        try:
            pid, cmd = wechat_detect.find_wechatappex()
            version = wechat_detect.extract_version_id(cmd)
            if armed and (pid != armed[0] or armed[2].is_set()):
                print("[legacy] main process gone, re-arming...", flush=True)
                detach(armed)
                armed = None
            if pid and armed is None and version \
                    in wechat_detect.legacy_version_ids():
                armed = inject(pid, version)
        except Exception as exc:
            print(f"[legacy] loop: {exc}", flush=True)
        stopped.wait(1)
    detach(armed)


if __name__ == "__main__":
    main()
