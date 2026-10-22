#!/usr/bin/env python3
"""WeChat build detection for the unified launcher.

One entry point (main.py), two flows:
  legacy — pre-4.x WeChatAppEx (3.8.x): address-table hook (main.py's flow)
  new    — 4.x Electron/flue builds: sniper + gateway + session stack

Version identifiers come from the WeChatAppEx main-process command line:
  --client_version=<int>                    4.x, e.g. 4066647394 -> f2641d62
  UnifiedPCMacWechat(0x..) / MacWechat/3.8.x(0x..)   pre-4.x UA fallback

On 4.x both signals agree (client_version int == UA hex). On pre-4.x they
may not, so resolution prefers whichever signal matches a known config id.
"""
import os
import re
import subprocess

# 4.1.13 Electron build: switch-based, served by the new stack (sniper.js)
SWITCH_BASED_IDS = {"f2641d62"}


def _ps_output():
    try:
        return subprocess.run(["ps", "ax", "-o", "pid=,command="],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def find_wechatappex(ps_text=None):
    """(pid, cmdline) of the WeChatAppEx browser main process, else (None, None)."""
    text = ps_text if ps_text is not None else _ps_output()
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, cmd = int(parts[0]), parts[1]
        if "MacOS/WeChatAppEx" not in cmd or "--type=" in cmd or "crashpad" in cmd:
            continue
        return pid, cmd
    return None, None


def command_for_pid(pid):
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def version_signals(cmd):
    """(client_hex, ua_id) parsed from a WeChatAppEx command line."""
    if not cmd:
        return None, None
    client_hex = ua_id = None
    m = re.search(r"--client_version=(\d+)", cmd)
    if m:
        client_hex = format(int(m.group(1)), "x")
    for pattern in (r"UnifiedPCMacWechat\(0x([0-9a-fA-F]+)\)",
                    r"MacWechat/[\d.]+\(0x([0-9a-fA-F]+)\)"):
        m = re.search(pattern, cmd)
        if m:
            ua_id = m.group(1).lower()
            break
    return client_hex, ua_id


def extract_version_id(cmd, known_ids=frozenset()):
    """Version id as lowercase hex; prefers a signal matching known_ids.

    4.x builds: client_version int == UA hex, either works. Pre-4.x builds
    key off the UA token, which may differ from client_version.
    """
    client_hex, ua_id = version_signals(cmd)
    for candidate in (ua_id, client_hex):
        if candidate and candidate in known_ids:
            return candidate
    return client_hex or ua_id


def all_known_ids(configs_dir=None):
    """Every config id, including switch-based builds served by the new stack."""
    return legacy_version_ids(configs_dir) | SWITCH_BASED_IDS


def legacy_version_ids(configs_dir=None):
    """Config ids served by the legacy address-hook flow.

    Switch-based builds (4.1.13) have a config but belong to the new stack.
    """
    configs_dir = configs_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
    try:
        names = os.listdir(configs_dir)
    except OSError:
        return set()
    return ({name.split("_")[1] for name in names
             if name.startswith("address_")} - SWITCH_BASED_IDS)


def detect_flow(ps_text=None, configs_dir=None):
    """{'flow': 'legacy'|'new', 'version': id|None, 'pid': pid|None}.

    'new' also covers "WeChatAppEx not running yet" — the new services
    idle-wait for the process, matching the previous startup behavior.
    """
    pid, cmd = find_wechatappex(ps_text)
    version = extract_version_id(cmd, all_known_ids(configs_dir))
    legacy = version in legacy_version_ids(configs_dir) if version else False
    return {"flow": "legacy" if legacy else "new", "version": version, "pid": pid}
