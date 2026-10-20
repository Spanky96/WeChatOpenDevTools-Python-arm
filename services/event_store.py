"""Bounded JSONL event storage with resumable byte cursors across rotation."""
import base64
import json
import os
import threading

EVENTS = "/tmp/wechat_rea/service_events.jsonl"
MAX_BYTES = 16 * 1024 * 1024
BACKUPS = 3
_lock = threading.Lock()


def append_event(record, path=EVENTS, max_bytes=MAX_BYTES, backups=BACKUPS):
    raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with _lock:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) + len(raw) > max_bytes:
            for index in range(backups, 0, -1):
                source = path if index == 1 else f"{path}.{index - 1}"
                if os.path.exists(source):
                    os.replace(source, f"{path}.{index}")
        with open(path, "ab") as stream:
            stream.write(raw)


def _files(path):
    result = []
    for name in [f"{path}.{i}" for i in range(BACKUPS, 0, -1)] + [path]:
        try:
            stat = os.stat(name)
            result.append((name, stat.st_ino, stat.st_size))
        except FileNotFoundError:
            pass
    return result


def encode_cursor(inode, offset):
    return base64.urlsafe_b64encode(f"{inode}:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor):
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        inode, offset = map(int, raw.split(":"))
        if inode <= 0 or offset < 0:
            raise ValueError("invalid event cursor")
        return inode, offset
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ValueError("invalid event cursor") from exc


def read_events(path=EVENTS, cursor=None, since=0, limit=200,
                initial_bytes=2 * 1024 * 1024, scan_bytes=2 * 1024 * 1024):
    files = _files(path)
    if not files:
        return {"events": [], "cursor": None, "has_more": False, "reset": False}
    reset = False
    index, remaining = len(files) - 1, initial_bytes
    while index > 0 and remaining > files[index][2]:
        remaining -= files[index][2]
        index -= 1
    offset = max(0, files[index][2] - remaining)
    partial_start = offset > 0
    if cursor:
        inode, saved = decode_cursor(cursor)
        match = next((i for i, entry in enumerate(files) if entry[1] == inode), None)
        if match is None or saved > files[match][2]:
            reset = True
            index, offset, partial_start = 0, 0, False
        else:
            index, offset, partial_start = match, saved, False
    events, scanned = [], 0
    limit = max(1, min(int(limit), 1000))
    while index < len(files):
        name, inode, size = files[index]
        try:
            with open(name, "rb") as stream:
                if os.fstat(stream.fileno()).st_ino != inode:
                    break  # rotation raced the snapshot; retry from the same cursor
                stream.seek(offset)
                if partial_start:
                    stream.readline()
                    offset = stream.tell()
                    partial_start = False
                while True:
                    pos = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        offset = pos
                        break
                    offset = stream.tell()
                    scanned += len(line)
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict) and event.get("t", 0) >= since:
                            events.append(event)
                    except (ValueError, UnicodeError, TypeError):
                        pass
                    if len(events) >= limit or scanned >= scan_bytes:
                        return {"events": events, "cursor": encode_cursor(inode, offset),
                                "has_more": offset < size or index < len(files) - 1,
                                "reset": reset}
        except FileNotFoundError:
            break
        if index == len(files) - 1:
            break
        index += 1
        offset = 0
    return {"events": events, "cursor": encode_cursor(files[index][1], offset),
            "has_more": index < len(files) - 1, "reset": reset}
