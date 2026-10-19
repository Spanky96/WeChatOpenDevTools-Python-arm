#!/usr/bin/env python3
"""Unpack any mini-program's local wxapkg into readable source.

Packages live (plaintext!) under:
  ~/Library/Containers/com.tencent.xinWeChat/Data/.wxapplet/packages/
    <appid>/<version>/__APP__.wxapkg (+ subpackages)

Usage:
  python3 unpack_mp.py <appid>            # unpack latest version
  python3 unpack_mp.py --list             # list all cached appids
"""
import glob
import json
import os
import struct
import sys

BASE = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/.wxapplet/packages")


def list_appids():
    out = []
    for d in sorted(glob.glob(os.path.join(BASE, "wx*"))):
        appid = os.path.basename(d)
        versions = [os.path.basename(v) for v in glob.glob(os.path.join(d, "*"))
                    if os.path.isdir(v)]
        out.append((appid, versions))
    return out


def unpack(pkg_path, outdir):
    data = open(pkg_path, "rb").read()
    if data[0] != 0xBE or data[13] != 0xED:
        raise ValueError("not a standard (plaintext) wxapkg — "
                         f"firstMark={data[0]:02x} lastMark={data[13]:02x}")
    file_count = struct.unpack(">I", data[14:18])[0]
    pos = 18
    entries = []
    for _ in range(file_count):
        name_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        name = data[pos:pos + name_len].decode("utf-8", "replace")
        pos += name_len
        off, size = struct.unpack(">II", data[pos:pos + 8])
        pos += 8
        entries.append((name, off, size))
    os.makedirs(outdir, exist_ok=True)
    for name, off, size in entries:
        dest = os.path.join(outdir, name.lstrip("/"))
        d = os.path.dirname(dest)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(dest, "wb") as w:
            w.write(data[off:off + size])
    return entries


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--list":
        for appid, versions in list_appids():
            print(appid, versions)
        return
    appid = sys.argv[1]
    pkgs = sorted(glob.glob(os.path.join(BASE, appid, "*")))
    if not pkgs:
        print(f"[-] no packages for {appid}")
        sys.exit(1)
    ver = os.path.basename(pkgs[-1])
    outroot = sys.argv[2] if len(sys.argv) > 2 else \
        f"/tmp/wechat_rea/mp_{appid}"
    total = 0
    for pkg in glob.glob(os.path.join(BASE, appid, ver, "*.wxapkg")):
        name = os.path.basename(pkg).replace(".wxapkg", "")
        sub = outroot if name == "__APP__" else os.path.join(outroot, name)
        try:
            entries = unpack(pkg, sub)
            total += len(entries)
            print(f"[+] {os.path.basename(pkg)}: {len(entries)} files -> {sub}")
        except ValueError as e:
            print(f"[-] {pkg}: {e}")
    print(f"\n[*] {total} files total -> {outroot}")
    print(f"[*] service source: {outroot}/app-service.js")


if __name__ == "__main__":
    main()
