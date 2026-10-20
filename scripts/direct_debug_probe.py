#!/usr/bin/env python3
"""Run a bounded native transport experiment; normal start.py does not use it."""
import argparse
import json
from pathlib import Path
import sys
import threading

import frida

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.sniper import find_main


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-scene", action="store_true",
                        help="temporarily allow listed normal-launch scenes through the local debug gate")
    parser.add_argument("--force-load", action="store_true",
                        help="also enter the connection branch for a listed normal-launch scene")
    parser.add_argument("--bypass-filter", action="store_true",
                        help="temporarily bypass the guarded outgoing CDP dictionary filter")
    parser.add_argument("--duration", type=int, default=60,
                        help="automatically detach after this many seconds (1-600)")
    args = parser.parse_args()
    if not 1 <= args.duration <= 600:
        parser.error("duration must be 1-600")
    if (args.force_load or args.bypass_filter) and not args.enable_scene:
        parser.error("--force-load and --bypass-filter require --enable-scene")
    pid = find_main()
    if not pid:
        parser.error("WeChatAppEx main process is not running")
    ready, rejected = threading.Event(), threading.Event()

    def on_message(message, data):
        payload = message.get("payload")
        if message.get("type") == "error":
            print("[probe] script error: " + str(message.get("description")), flush=True)
            rejected.set()
            ready.set()
        elif isinstance(payload, dict):
            print(json.dumps(payload, ensure_ascii=True), flush=True)
            if payload.get("hook") == "probe":
                if payload.get("event") == "exit":
                    rejected.set()
                ready.set()

    session = frida.attach(pid)
    script = None
    try:
        script = session.create_script((ROOT / "scripts/direct_debug_probe.js").read_text())
        script.on("message", on_message)
        script.load()
        if not ready.wait(3) or rejected.is_set():
            return 2
        if args.enable_scene:
            script.post({"type": "enable", "enabled": True, "force_load": args.force_load,
                         "bypass_filter": args.bypass_filter})
        print(f"[probe] attached pid={pid}; scene experiment={args.enable_scene}; "
              f"force load={args.force_load}; "
              f"bypass filter={args.bypass_filter}; "
              f"detaching in {args.duration}s", flush=True)
        try:
            rejected.wait(args.duration)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if script is not None:
            try:
                script.exports_sync.dispose()
            except frida.InvalidOperationError:
                pass
            script.unload()
        session.detach()
        print("[probe] detached", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
