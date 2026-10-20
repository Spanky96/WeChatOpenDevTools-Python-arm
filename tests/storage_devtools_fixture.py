"""Synthetic CDP backend for the real Chrome DevTools Storage smoke check."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import cdp_gateway as gateway
from services import cdp_storage

APPID = "wx0000000000000001"
FRAME = "ABC123"
ORIGIN = "https://servicewechat.com"
URL = f"{ORIGIN}/{APPID}/0/page-frame.html"
SOURCE_URL = "https://usr/app-service.js"
SOURCE = "function loadData(){return 1;}" * 40000


async def native(ws):
    async for raw in ws:
        message = json.loads(raw)
        method = message.get("method")
        result = {}
        if method == "Target.attachToTarget":
            result = {"sessionId": "native"}
        elif method == "Target.getTargetInfo":
            result = {"targetInfo": {"targetId": FRAME, "type": "page", "title": "fixture",
                                      "url": URL, "attached": True, "canAccessOpener": False}}
        elif method in ("Page.getResourceTree", "Page.getFrameTree"):
            result = {"frameTree": {"frame": {"id": FRAME, "loaderId": "loader", "url": URL,
                                               "securityOrigin": ORIGIN, "mimeType": "text/html"},
                                     "resources": []}}
        elif method == "Runtime.enable":
            await ws.send(json.dumps({"method": "Runtime.executionContextCreated", "sessionId": "native",
                                      "params": {"context": {"id": 1, "origin": ORIGIN,
                                                               "name": "service", "uniqueId": "fixture",
                                                               "auxData": {"isDefault": True, "frameId": FRAME}}}}))
        elif method == "Runtime.getIsolateId":
            result = {"id": "fixture"}
        elif method == "Debugger.enable":
            result = {"debuggerId": "fixture"}
            await ws.send(json.dumps({"method": "Debugger.scriptParsed", "sessionId": "native", "params": {
                "scriptId": "77", "url": SOURCE_URL, "startLine": 0, "startColumn": 0,
                "endLine": 0, "endColumn": len(SOURCE), "executionContextId": 1,
                "executionContextAuxData": {"isDefault": True, "frameId": FRAME},
                "hash": "fixture", "length": len(SOURCE), "scriptLanguage": "JavaScript",
                "isLiveEdit": False, "isModule": False, "hasSourceURL": True}}))
        elif method == "Debugger.getScriptSource":
            result = {"scriptSource": SOURCE}
        elif method == "Runtime.evaluate":
            result = {"result": {"type": "undefined"}}
        elif method == "DOM.getDocument":
            result = {"root": {"nodeId": 1, "backendNodeId": 1, "nodeType": 9,
                               "nodeName": "#document", "localName": "", "nodeValue": "",
                               "documentURL": URL, "baseURL": URL, "children": []}}
        elif method == "Network.getCookies":
            result = {"cookies": []}
        elif method == "Storage.getUsageAndQuota":
            result = {"usage": 0, "quota": 10000000, "usageBreakdown": []}
        elif method == "IndexedDB.requestDatabaseNames":
            result = {"databaseNames": []}
        elif method == "CacheStorage.requestCacheNames":
            result = {"caches": []}
        await ws.send(json.dumps({"id": message["id"], "sessionId": "native", "result": result}))


async def main():
    cdp_storage.read_snapshot = lambda appid: [["example", '{"enabled":true,"count":3}'],
                                              ["locale", '"zh-CN"']]
    with tempfile.TemporaryDirectory() as directory:
        gateway.AGENT_EVENTS = str(Path(directory) / "events.jsonl")
        events = [{"t": time.time(), "appid": APPID, "type": kind, "data": data} for kind, data in (
            ("request", {"id": "fixture", "url": "https://test.invalid/fixture", "stack": [{
                "functionName": "loadData", "url": SOURCE_URL, "lineNumber": 0, "columnNumber": 20}]}),
            ("response", {"id": "fixture", "url": "https://test.invalid/fixture", "status": 200,
                          "data": {"fixture": True}}))]
        Path(gateway.AGENT_EVENTS).write_text("".join(json.dumps(event) + "\n" for event in events))
        async with websockets.serve(native, "127.0.0.1", 0) as backend:
            async def proxy(ws):
                await gateway.proxy_session(ws, FRAME, backend.sockets[0].getsockname()[1], APPID)
            async with websockets.serve(proxy, "127.0.0.1", 0) as server:
                print(server.sockets[0].getsockname()[1], flush=True)
                await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
