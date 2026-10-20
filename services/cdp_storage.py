"""Expose wx Storage as an isolated, read-only CDP Local storage view."""
import asyncio
import json
import urllib.request


def read_snapshot(appid):
    request = urllib.request.Request(
        "http://127.0.0.1:8778/storage",
        data=json.dumps({"appid": appid, "format": "cdp"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=9) as response:
        result = json.load(response)
    data = result.get("data") or {}
    if result.get("error") or data.get("err"):
        raise ValueError(result.get("error") or data["err"])
    entries = data.get("entries")
    if not isinstance(entries, list) or any(
            not isinstance(item, list) or len(item) != 2 or
            not all(isinstance(value, str) for value in item) for item in entries):
        raise ValueError("Invalid wx Storage snapshot")
    return entries


class WxStorageAdapter:
    def __init__(self, appid, target_id):
        self.appid = appid
        self.frame_id = target_id
        self.origin = f"https://{appid}.wx-storage.invalid"
        self.storage_key = self.origin + "/"

    def observe(self, message):
        frame = ((message.get("result") or {}).get("frameTree") or {}).get("frame")
        if message.get("method") == "Page.frameNavigated":
            frame = (message.get("params") or {}).get("frame")
        if frame and not frame.get("parentId") and frame.get("id"):
            self.frame_id = frame["id"]

    async def handle(self, message):
        method = message.get("method", "")
        params = message.get("params") or {}
        result = None
        if method in ("Storage.getStorageKey", "Storage.getStorageKeyForFrame"):
            if params.get("frameId") == self.frame_id:
                result = {"storageKey": self.storage_key}
        elif method in ("DOMStorage.enable", "DOMStorage.disable"):
            result = {}
        elif method.startswith("DOMStorage."):
            identity = params.get("storageId") or {}
            key = identity.get("storageKey", "").rstrip("/")
            origin = identity.get("securityOrigin", "").rstrip("/")
            if key != self.origin and origin != self.origin:
                return None
            if (key and key != self.origin) or (origin and origin != self.origin):
                return self.error(message, "Storage identity does not match this mini-program")
            if method == "DOMStorage.getDOMStorageItems":
                if identity.get("isLocalStorage") is not True:
                    result = {"entries": []}
                else:
                    try:
                        entries = await asyncio.to_thread(read_snapshot, self.appid)
                        result = {"entries": entries}
                    except Exception as exc:
                        return self.error(message, str(exc))
            else:
                return self.error(message, "wx Storage view is read-only; use wx APIs to change values")
        if result is None:
            return None
        return {"id": message["id"], "result": result}

    @staticmethod
    def error(message, reason):
        return {"id": message["id"], "error": {"code": -32000, "message": reason}}
