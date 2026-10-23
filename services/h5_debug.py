#!/usr/bin/env python3
"""Internal H5 injection service managed by main.py."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import frida
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.version_config import get_version


def load_profile():
    return get_version('f2641d62')['h5']


def arm64_digest(path):
    """Hash the ARM64 slice rather than a universal Mach-O container."""
    with open(path, 'rb') as stream:
        header = stream.read(8)
        size = os.fstat(stream.fileno()).st_size
        offset = 0
        magic, count = struct.unpack('>II', header)
        if magic in (0xcafebabe, 0xcafebabf):
            if count > 32:
                raise ValueError('Invalid universal Mach-O architecture count')
            entry = '>iiQQII' if magic == 0xcafebabf else '>iiIII'
            length = struct.calcsize(entry)
            entries = [struct.unpack(entry, stream.read(length)) for _ in range(count)]
            selected = next((e for e in entries if e[0] == 0x100000c and e[1] == 0), None)
            if not selected:
                raise ValueError('No ARM64 slice in framework')
            offset, size = selected[2:4]
        elif header[:4] != b'\xcf\xfa\xed\xfe':
            raise ValueError('Expected a Mach-O framework')
        if offset + size > os.fstat(stream.fileno()).st_size:
            raise ValueError('Mach-O slice exceeds file')
        stream.seek(offset)
        digest = hashlib.sha256()
        while size:
            block = stream.read(min(size, 1024 * 1024))
            if not block:
                raise ValueError('Truncated framework')
            digest.update(block)
            size -= len(block)
        return digest.hexdigest()


def renderer_pids(profile):
    for process in psutil.process_iter(['pid', 'cmdline']):
        try:
            args = process.info['cmdline'] or []
            if not args or 'WeChatAppEx Helper (Renderer)' not in args[0]:
                continue
            if ('--type=renderer' in args and
                    '--client_version=' + profile['client_version'] in args and
                    any('--wmpf-render-type=' + kind in args for kind in ('0', '1'))):
                yield process.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def run(port=8780, scan_interval=5):
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('H5 injection currently supports macOS ARM64 only')
    if not shutil.which('node'):
        raise RuntimeError('Node.js 18+ is required')
    if not (ROOT / 'node_modules/chii/package.json').is_file():
        raise RuntimeError('Missing Chii dependency: run npm ci --ignore-scripts')
    profile = load_profile()
    state_dir = Path('/tmp/wechat_rea')
    state_dir.mkdir(exist_ok=True)
    stop = threading.Event()
    def stop_signal(*_):
        stop.set()
    previous_signals = {sig: signal.signal(sig, stop_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    sessions = {}
    failed = {}
    digests = {}
    last_counts = {}
    errors = {}
    server = None
    server_url = f'http://127.0.0.1:{port}/'
    with open(state_dir / 'h5.lock', 'a') as lock:
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('H5 debugger is already running (see /tmp/wechat_rea/h5_status.json)') from exc
            with open(state_dir / 'h5_chii.log', 'a', buffering=1) as log:
                server = subprocess.Popen(['node', str(ROOT / 'scripts/h5_chii_server.cjs'), '--port', str(port)],
                                          cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                for _ in range(50):
                    if server.poll() is not None:
                        raise RuntimeError(f'Chii could not bind {server_url}; see {state_dir}/h5_chii.log')
                    try:
                        health = get_json(server_url + 'health')
                        if health.get('service') == 'wechat-h5-chii' and health.get('pid') == server.pid:
                            break
                    except (OSError, ValueError):
                        pass
                    if stop.wait(0.1):
                        return
                else:
                    raise RuntimeError('Chii startup timed out')
                print(f'[H5] DevTools: {server_url}', flush=True)
                print('[H5] Waiting for WeChat H5 pages; mini-program services are independent.', flush=True)
                code = (ROOT / 'scripts/h5_bootstrap.js').read_text().replace('__H5_SERVER_URL__', json.dumps(server_url))
                agent = (ROOT / 'scripts/h5_inject.js').read_text()
                while not stop.is_set():
                    if server.poll() is not None:
                        raise RuntimeError(f'Chii exited ({server.returncode}); see /tmp/wechat_rea/h5_chii.log')
                    pids = set(renderer_pids(profile))
                    for pid in set(sessions) - pids:
                        session, script = sessions.pop(pid)
                        with contextlib.suppress(frida.InvalidOperationError, frida.TransportError):
                            session.detach()
                        print(f'[H5] Renderer {pid} exited', flush=True)
                    failed = {pid: retry for pid, retry in failed.items() if pid in pids}
                    errors = {pid: error for pid, error in errors.items() if pid in pids}
                    for pid in sorted(pids - sessions.keys()):
                        if failed.get(pid, 0) > time.monotonic():
                            continue
                        session = script = None
                        try:
                            session = frida.attach(pid)
                            script = session.create_script(agent)
                            script.load()
                            identity = script.exports_sync.identity(profile['framework'])
                            path = identity['path']
                            if path not in digests:
                                digests[path] = arm64_digest(path)
                            if identity['arch'] != profile['architecture'] or digests[path] != profile['arm64_sha256']:
                                raise RuntimeError('Unsupported framework SHA-256/architecture; refusing injection')
                            script.exports_sync.initialize(profile, code)
                            sessions[pid] = (session, script)
                            errors.pop(pid, None)
                            print(f'[H5] Attached renderer {pid}; framework SHA-256 verified', flush=True)
                        except Exception as exc:
                            errors[pid] = str(exc)
                            failed[pid] = float('inf') if isinstance(exc, RuntimeError) else time.monotonic() + 10
                            print(f'[H5] Renderer {pid}: {exc}', flush=True)
                            if session:
                                with contextlib.suppress(Exception):
                                    session.detach()
                    for pid, (session, script) in list(sessions.items()):
                        try:
                            result = script.exports_sync.scan()
                            if last_counts.get(pid) != result['frames']:
                                print(f"[H5] Renderer {pid}: {result['frames']} validated frame(s)", flush=True)
                                last_counts[pid] = result['frames']
                            if result['limited']:
                                print(f'[H5] Renderer {pid}: scan/allocation limit reached', flush=True)
                        except Exception as exc:
                            errors[pid] = str(exc)
                            print(f'[H5] Renderer {pid} disconnected: {exc}', flush=True)
                            sessions.pop(pid)
                            failed[pid] = time.monotonic() + 10
                            with contextlib.suppress(Exception):
                                session.detach()
                    state = {'running': True, 'pid': os.getpid(), 'port': port,
                             'updated_at': time.time(), 'renderers': {}, 'errors': errors}
                    for pid, (_, script) in sessions.items():
                        with contextlib.suppress(Exception):
                            state['renderers'][pid] = script.exports_sync.states()
                    (state_dir / 'h5_status.json').write_text(json.dumps(state, indent=2))
                    stop.wait(scan_interval)
        finally:
            for session, script in sessions.values():
                with contextlib.suppress(Exception):
                    script.exports_sync.stop()
                    script.unload()
                    session.detach()
            if server and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
            if server:
                with contextlib.suppress(OSError):
                    (state_dir / 'h5_status.json').write_text(json.dumps({
                        'running': False, 'pid': os.getpid(), 'port': port, 'updated_at': time.time()}))
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8780)
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535:
        parser.error('--port must be 1024..65535')
    try:
        run(options.port)
    except (RuntimeError, OSError) as exc:
        print(f'[H5] {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
