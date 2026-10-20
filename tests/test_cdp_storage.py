import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import websockets

from services import cdp_gateway as gateway
from services import cdp_storage as storage
from services import service_session as service


APPID = "wx0000000000000001"


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_origin_isolation_and_read_only_behavior(self):
        adapter = storage.WxStorageAdapter(APPID, "main")
        async def call(method, **params):
            return await adapter.handle({"id": 1, "method": method, "params": params})
        key = (await call("Storage.getStorageKey", frameId="main"))["result"]["storageKey"]
        self.assertIn(APPID, key)
        self.assertIsNone(await call("Storage.getStorageKey", frameId="h5-child"))
        identity = {"storageKey": key, "isLocalStorage": True}
        entries = [["__proto__", '{"safe":true}'], ["long", "x" * 5000]]
        with patch.object(storage, "read_snapshot", return_value=entries) as reader:
            result = await call("DOMStorage.getDOMStorageItems", storageId=identity)
            self.assertEqual(result["result"]["entries"], entries)
            reader.assert_called_once_with(APPID)
            for method in ("DOMStorage.setDOMStorageItem", "DOMStorage.removeDOMStorageItem", "DOMStorage.clear"):
                self.assertIn("read-only", (await call(method, storageId=identity))["error"]["message"])
            identity["isLocalStorage"] = False
            self.assertEqual((await call("DOMStorage.getDOMStorageItems", storageId=identity))["result"], {"entries": []})
            self.assertEqual(reader.call_count, 1)
        identity["storageKey"] = "https://another.wx-storage.invalid/"
        self.assertIsNone(await call("DOMStorage.getDOMStorageItems", storageId=identity))

    async def test_context_errors_are_reported_and_frame_changes_are_tracked(self):
        adapter = storage.WxStorageAdapter(APPID, "old")
        adapter.observe({"result": {"frameTree": {"frame": {"id": "new"}}}})
        result = await adapter.handle({"id": 1, "method": "Storage.getStorageKeyForFrame", "params": {"frameId": "new"}})
        self.assertEqual(result["result"]["storageKey"], adapter.storage_key)
        with patch.object(storage, "read_snapshot", side_effect=ValueError("context unavailable")):
            result = await adapter.handle({"id": 2, "method": "DOMStorage.getDOMStorageItems", "params": {
                "storageId": {"securityOrigin": adapter.origin, "isLocalStorage": True}}})
        self.assertEqual(result["error"]["message"], "context unavailable")

    async def test_storage_commands_are_not_forwarded_to_native_local_storage(self):
        received = []
        async def native(ws):
            async for raw in ws:
                message = json.loads(raw)
                received.append(message["method"])
                result = {"sessionId": "attached"} if message["method"] == "Target.attachToTarget" else {}
                await ws.send(json.dumps({"id": message["id"], "result": result}))
        async with websockets.serve(native, "127.0.0.1", 0) as backend:
            async def proxy(ws):
                await gateway.proxy_session(ws, "ABC123", backend.sockets[0].getsockname()[1], APPID)
            async with websockets.serve(proxy, "127.0.0.1", 0) as server, \
                    websockets.connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}") as client:
                with patch.object(storage, "read_snapshot", return_value=[["typed", "123"]]):
                    await client.send(json.dumps({"id": 1, "method": "DOMStorage.getDOMStorageItems", "params": {
                        "storageId": {"storageKey": f"https://{APPID}.wx-storage.invalid/", "isLocalStorage": True}}}))
                    result = json.loads(await asyncio.wait_for(client.recv(), 1))
                    self.assertEqual(result["result"]["entries"], [["typed", "123"]])
        self.assertEqual(received, ["Target.attachToTarget"])

    async def test_service_selects_lossless_snapshot_for_cdp(self):
        with patch.object(service, "MP", {APPID: {"sid": "s", "ctx": 1}}), \
                patch.object(service, "eval_js", new_callable=AsyncMock,
                             return_value='{"entries":[["key","123"]]}') as evaluate:
            result = await service.process_ctrl(None, {"path": "/storage", "appid": APPID, "format": "cdp"})
        self.assertEqual(result["data"]["entries"], [["key", "123"]])
        self.assertEqual(evaluate.call_args.args[3], service.CDP_STORAGE_JS)


if __name__ == "__main__":
    unittest.main()
