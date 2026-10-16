#!/usr/bin/env python3
"""Probe localhost for Chromium DevTools HTTP endpoints (CDP).

Scans ports owned by WeChatAppEx processes first, then falls back to the
common remote-debugging ports. Prints /json/version for any hit.
"""
import json
import socket
import subprocess
import sys
import urllib.request


def appex_ports():
    out = subprocess.run(["ps", "ax", "-o", "pid,command"],
                         capture_output=True, text=True).stdout
    pids = [l.strip().split()[0] for l in out.splitlines()
            if "MacOS/WeChatAppEx" in l and "grep" not in l]
    ports = set()
    if not pids:
        return ports
    lsof = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN",
                           "-a", "-p", ",".join(pids)],
                          capture_output=True, text=True).stdout
    for line in lsof.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 9 and ":" in parts[8]:
            try:
                ports.add(int(parts[8].rsplit(":", 1)[1]))
            except ValueError:
                pass
    return ports


def probe(port):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.5) as r:
            return r.read().decode()
    except Exception:
        return None


def main():
    candidates = appex_ports() | {9222, 9229, 9230, 5858, 9333, 8080}
    hits = 0
    for port in sorted(candidates):
        body = probe(port)
        if body:
            hits += 1
            print(f"[HIT] port {port}:")
            try:
                print(json.dumps(json.loads(body), indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(body[:400])
    if not hits:
        print("no CDP endpoint found")
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
