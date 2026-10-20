import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import cdp_gateway as gateway
from services import service_session as service


APPID = "wx0000000000000001"
OTHER = "wx0000000000000002"
TARGET = "ABC123"


class Front:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


class BridgeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_connections_keep_their_command_ids(self):
        sent = []
        both_sending = asyncio.Event()

        class Socket:
            def __init__(self):
                self.replies = asyncio.Queue()

            async def send(self, raw):
                msg = json.loads(raw)
                sent.append(msg)
                self.replies.put_nowait(json.dumps({
                    "id": msg["id"], "result": {"method": msg["method"]}}))
                if len(sent) == 2:
                    both_sending.set()
                await both_sending.wait()

            async def recv(self):
                return await self.replies.get()

        results = await asyncio.wait_for(asyncio.gather(
            gateway.bridge_cmd(Socket(), "Target.attachToTarget"),
            gateway.bridge_cmd(Socket(), "Target.getTargets")), 0.5)
        self.assertNotEqual(sent[0]["id"], sent[1]["id"])
        self.assertEqual(results, [{"method": "Target.attachToTarget"},
                                   {"method": "Target.getTargets"}])


class EventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "events.jsonl")
        Path(self.path).touch()
        self.events_patch = patch.object(gateway, "AGENT_EVENTS", self.path)
        self.events_patch.start()
        self.front = Front()

    def tearDown(self):
        self.events_patch.stop()
        self.tmp.cleanup()

    def append(self, kind, data, appid=APPID, timestamp=1_700_000_000.5):
        with open(self.path, "a") as f:
            f.write(json.dumps({"t": timestamp, "appid": appid,
                                "type": kind, "data": data}) + "\n")

    async def test_request_initiator_uses_captured_stack_and_observed_script(self):
        frame = {"functionName": "loadData", "url": "https://usr/pages/home.js",
                 "lineNumber": 12, "columnNumber": 34}
        self.append("request", {"id": "stack", "url": "https://test.invalid/", "stack": [frame]})
        injector = gateway.NetInjector(self.front, APPID)
        injector.observe({"method": "Debugger.scriptParsed", "params": {
            "url": frame["url"], "scriptId": "77", "executionContextId": 3}})
        await injector.enable("Network")
        await injector.feed()
        self.assertEqual(self.front.messages[0]["params"]["initiator"], {
            "type": "script", "stack": {"callFrames": [{**frame, "scriptId": "77"}]}})
        injector.observe({"method": "Debugger.scriptParsed", "params": {
            "url": frame["url"], "scriptId": "88", "executionContextId": 4}})
        self.assertEqual(injector.initiator({"stack": [frame]})["stack"]["callFrames"][0]["scriptId"], "")
        for stack in (None, {}, "bad", [None, {"url": "x", "lineNumber": "bad"}]):
            self.assertEqual(injector.initiator({"stack": stack}), {"type": "other"})
        self.assertEqual(injector.initiator({}), {"type": "other"})

    async def test_console_first_does_not_consume_network_history(self):
        self.append("request", {"id": "one", "url": "https://test.invalid/",
                                "method": "POST", "data": {"a": 1}})
        self.append("console", {"level": "log", "args": ["already captured"]})
        self.append("response", {"id": "one", "url": "https://test.invalid/",
                                 "status": 201, "data": {"ok": True}})
        self.append("console", {"args": ["other app"]}, OTHER)
        injector = gateway.NetInjector(self.front, APPID)
        await injector.enable("Runtime")
        await injector.feed()
        methods = [m["method"] for m in self.front.messages]
        self.assertEqual(methods, ["Runtime.executionContextCreated",
                                   "Runtime.consoleAPICalled"])
        console = self.front.messages[-1]["params"]
        self.assertEqual(console["timestamp"], 1_700_000_000_500)
        self.assertEqual(console["executionContextId"], injector.context_id)

        await injector.enable("Network")
        await injector.feed()
        self.assertEqual([m["method"] for m in self.front.messages][-3:], [
            "Network.requestWillBeSent", "Network.responseReceived",
            "Network.loadingFinished"])
        self.assertEqual(await injector.get_response_body("rea:one"),
                         {"body": '{"ok": true}', "base64Encoded": False})
        count = len(self.front.messages)
        await injector.feed()
        self.assertEqual(len(self.front.messages), count)

        injector.enabled["Network"] = False
        self.append("request", {"id": "two", "url": "https://test.invalid/2"})
        await injector.feed()
        self.assertEqual(len(self.front.messages), count)
        await injector.enable("Network")
        await injector.feed()
        self.assertEqual(self.front.messages[-1]["params"]["requestId"], "rea:two")

    async def test_partial_line_and_file_rotation_do_not_lose_events(self):
        injector = gateway.NetInjector(self.front, APPID)
        await injector.enable("Network")
        line = json.dumps({"t": 2, "appid": APPID, "type": "request",
                           "data": {"id": "partial", "url": "https://test.invalid"}})
        Path(self.path).write_text(line[:30])
        await injector.feed()
        self.assertEqual(self.front.messages, [])
        with open(self.path, "a") as f:
            f.write(line[30:] + "\n")
        await injector.feed()
        self.assertEqual(len(self.front.messages), 1)
        os.rename(self.path, self.path + ".old")
        self.append("request", {"id": "rotated", "url": "https://test.invalid/new"})
        await injector.feed()
        self.assertEqual(self.front.messages[-1]["params"]["requestId"], "rea:rotated")

    async def test_legacy_duplicate_request_and_response_are_replayed_once(self):
        for _ in range(2):
            self.append("request", {"id": "legacy", "url": "https://test.invalid"})
            self.append("response", {"id": "legacy", "data": "response", "status": 200})
        injector = gateway.NetInjector(self.front, APPID)
        await injector.enable("Network")
        await injector.feed()
        self.assertEqual(len(self.front.messages), 3)

    async def test_enable_errors_from_bridge_do_not_hide_captured_traffic(self):
        self.append("request", {"id": "one", "url": "https://test.invalid"})
        received = []
        async def bridge(ws):
            async for raw in ws:
                msg = json.loads(raw)
                received.append(msg)
                if msg["method"] == "Target.attachToTarget":
                    result = {"id": msg["id"], "result": {"sessionId": "native"}}
                else:
                    result = {"id": msg["id"], "sessionId": "native",
                              "error": {"code": -32601, "message": "unsupported"}}
                await ws.send(json.dumps(result))

        async with websockets.serve(bridge, "127.0.0.1", 0) as backend:
            port = backend.sockets[0].getsockname()[1]
            async def frontend(ws):
                await gateway.proxy_session(ws, TARGET, port, APPID)
            async with websockets.serve(frontend, "127.0.0.1", 0) as proxy:
                proxy_port = proxy.sockets[0].getsockname()[1]
                async with websockets.connect(f"ws://127.0.0.1:{proxy_port}") as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(reply, {"id": 1, "result": {}})
                    with self.assertRaises(asyncio.TimeoutError):
                        await asyncio.wait_for(ws.recv(), 0.05)
                    await ws.send(json.dumps({"id": 99, "method": "Page.getResourceTree"}))
                    page_reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(page_reply["id"], 99)
                    event = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(event["method"], "Network.requestWillBeSent")
                    await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(reply, {"id": 2, "result": {}})
                    context = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(context["method"], "Runtime.executionContextCreated")
        self.assertTrue(any(m["method"] == "Network.enable" for m in received))

    async def test_late_frame_tree_restores_console_after_fallback_replay(self):
        self.append("console", {"args": ["captured before DevTools opened"]})
        release_tree = asyncio.Event()

        async def bridge(ws):
            async for raw in ws:
                msg = json.loads(raw)
                result = {}
                if msg["method"] == "Target.attachToTarget":
                    result = {"sessionId": "native"}
                elif msg["method"] == "Page.getResourceTree":
                    await release_tree.wait()
                    result = {"frameTree": {"frame": {"id": "main"}}}
                await ws.send(json.dumps({"id": msg["id"], "result": result}))

        async with websockets.serve(bridge, "127.0.0.1", 0) as backend:
            port = backend.sockets[0].getsockname()[1]

            async def frontend(ws):
                await gateway.proxy_session(ws, TARGET, port, APPID)

            async with websockets.serve(frontend, "127.0.0.1", 0) as proxy:
                proxy_port = proxy.sockets[0].getsockname()[1]
                async with websockets.connect(f"ws://127.0.0.1:{proxy_port}") as ws:
                    try:
                        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
                        reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertEqual(reply, {"id": 1, "result": {}})
                        await ws.send(json.dumps({"id": 2, "method": "Page.getResourceTree"}))
                        context = json.loads(await asyncio.wait_for(ws.recv(), 4))
                        self.assertEqual(context["method"], "Runtime.executionContextCreated")
                        first = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertEqual(first["method"], "Runtime.consoleAPICalled")
                        release_tree.set()
                        page_reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertEqual(page_reply["id"], 2)
                        context = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertEqual(context["method"], "Runtime.executionContextCreated")
                        restored = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertEqual(restored, first)
                    finally:
                        release_tree.set()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        service.MP.clear()
        service.FAILED_CTX.clear()
        service.SEEN_EVENTS.clear()

    def tearDown(self):
        service.MP.clear()
        service.FAILED_CTX.clear()

    async def test_context_events_are_scoped_to_attached_session(self):
        br = AsyncMock()
        br.cmd.side_effect = lambda method, *args, **kwargs: {
            "Target.getTargets": {"targetInfos": [{"type": "page", "targetId": TARGET,
                "url": f"https://servicewechat.com/{APPID}/1/page-frame.html"}]},
            "Target.attachToTarget": {"sessionId": "wanted"},
        }.get(method, {})
        br.drain.side_effect = [
            [{"method": "Runtime.executionContextCreated", "sessionId": "other",
              "params": {"context": {"id": 77}}}],
            [{"method": "Runtime.executionContextCreated", "sessionId": "wanted",
              "params": {"context": {"id": 88}}}],
        ]
        async def evaluate(br, sid, ctx, expression, **kwargs):
            self.assertEqual(ctx, 88)
            if expression == "typeof wx":
                return "object"
            return 10 if ";globalThis.__REA__.version\n" in expression else "undefined"
        with patch.object(service, "eval_js", side_effect=evaluate), patch.object(service, "emit"):
            await service.setup_targets(br)
        self.assertEqual(service.MP[APPID]["ctx"], 88)
        self.assertEqual(service.MP[APPID]["targetId"], TARGET)

    async def test_closed_bridge_fails_pending_commands_immediately(self):
        queue = asyncio.Queue()
        class Socket:
            async def send(self, raw):
                queue.put_nowait(raw)
            async def __aiter__(self):
                await queue.get()
                if False:
                    yield
        br = service.Br(Socket())
        await br.start()
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(br.cmd("Target.getTargets"), 0.5)
        self.assertEqual(br.wait, {})

    async def test_direct_context_requires_matching_service_appid(self):
        br = AsyncMock()
        with patch.dict(os.environ, {"WECHAT_DEBUG_MODE": "direct"}), \
                patch.object(service, "eval_js", new_callable=AsyncMock) as evaluate:
            for returned in (False, None, "object", "true"):
                evaluate.return_value = returned
                self.assertFalse(await service.is_service_context(br, "session", 3, APPID))
            evaluate.return_value = True
            self.assertTrue(await service.is_service_context(br, "session", 3, APPID))
            expression = evaluate.call_args.args[3]
            self.assertIn("typeof getApp==='function'", expression)
            self.assertIn("miniProgram.appId===" + json.dumps(APPID), expression)

    async def test_breakpoint_removal_keeps_record_until_native_acknowledgement(self):
        value = service.MP[APPID] = {"sid": "session", "bps": {"app-service.js:12": "bp-one"}}
        br = AsyncMock()
        request = {"path": "/breakpoint", "appid": APPID, "file": "app-service.js", "line": 12, "remove": True}
        for response in ({"_err": "timeout"}, {"code": -32000, "message": "disconnected"}, None):
            br.cmd.return_value = response
            result = await service.process_ctrl(br, request)
            self.assertFalse(result["ok"])
            self.assertEqual(value["bps"], {"app-service.js:12": "bp-one"})
        br.cmd.return_value = {}
        self.assertTrue((await service.process_ctrl(br, request))["ok"])
        self.assertEqual(value["bps"], {})

    async def test_duplicate_breakpoint_setting_preserves_the_owned_id(self):
        service.MP[APPID] = {"sid": "session", "bps": {"app-service.js:12": "bp-one"}}
        br = AsyncMock()
        result = await service.process_ctrl(br, {"path": "/breakpoint", "appid": APPID,
                                                "file": "app-service.js", "line": 12})
        self.assertEqual(result, {"ok": True, "breakpointId": "bp-one"})
        br.cmd.assert_not_called()

    async def test_manual_retry_reconnects_an_injected_agent(self):
        target = {"type": "page", "targetId": TARGET,
                  "url": f"https://servicewechat.com/{APPID}/1/page-frame.html"}
        service.MP[APPID] = {"sid": "session", "ctx": 88, "targetId": TARGET}
        bridge = AsyncMock()
        bridge.cmd.return_value = {"targetInfos": [target]}
        with patch.object(service, "TARGETS_CACHE", [target]), \
                patch.object(service, "WAKE", asyncio.Event()), \
                patch.object(service, "eval_js", new_callable=AsyncMock, return_value=True) as evaluate:
            self.assertTrue((await service.local_control({"path": "/retry", "appid": APPID}))["ok"])
            self.assertTrue(service.WAKE.is_set())
            await service.setup_targets(bridge)
        self.assertIn("__REA__.reconnect()", evaluate.call_args.args[3])
        self.assertNotIn("reconnect_requested", service.MP[APPID])
        self.assertEqual(bridge.cmd.await_count, 1, "retry must preserve the attached debugger session")

    async def test_hub_and_fallback_do_not_persist_duplicate_agent_events(self):
        event = {"t": 12345, "type": "request", "data": {"id": "same"}}
        with patch.object(service, "emit") as emit:
            service.emit_agent(APPID, event)
            service.emit_agent(APPID, dict(event))
            service.emit_agent(OTHER, event)
        self.assertEqual(emit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
