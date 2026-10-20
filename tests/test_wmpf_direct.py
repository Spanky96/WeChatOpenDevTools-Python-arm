import asyncio
import json
import socket
import unittest
import urllib.request
import zlib

from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import connect

try:
    from google.protobuf.message import DecodeError
    from services import wmpf_direct as direct
except ModuleNotFoundError as exc:
    if not exc.name.startswith("google"):
        raise
    direct = None


@unittest.skipIf(direct is None, "optional requirements-direct.txt dependencies are not installed")
class ProtocolTests(unittest.TestCase):
    def test_json_roundtrip_and_zlib(self):
        payload = {"id": 7, "sessionId": "native-session", "result": {"value": "test"}}
        for compressed in (False, True):
            raw = direct.encode_cdp(payload, 8, "chromeDevtoolsResult", compressed)
            self.assertEqual(direct.decode_frame(raw), ("chromeDevtoolsResult", payload))
            frame = direct.DebugMessage.FromString(raw)
            self.assertEqual(frame.seq, 8)
            self.assertEqual(frame.compressAlgo, int(compressed))

    def test_invalid_compression_and_size_are_rejected(self):
        for frame in (
            direct.DebugMessage(category="setupContext", compressAlgo=2),
            direct.DebugMessage(category="setupContext", originalSize=direct.MAX_MESSAGE + 1),
            direct.DebugMessage(category="setupContext", compressAlgo=1, data=b"bad"),
            direct.DebugMessage(category="setupContext", compressAlgo=1,
                                data=zlib.compress(b"x" * (direct.MAX_MESSAGE + 1))),
        ):
            with self.assertRaises((ValueError, zlib.error)):
                direct.decode_frame(frame.SerializeToString())
        with self.assertRaises((DecodeError, ValueError)):
            direct.decode_frame(b"\xff")


@unittest.skipIf(direct is None, "optional requirements-direct.txt dependencies are not installed")
class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = direct.DirectBridge()
        await self.bridge.start(0, 0)
        self.runtime_url = f"ws://127.0.0.1:{self.bridge.debug_port}"
        self.cdp_url = f"ws://127.0.0.1:{self.bridge.cdp_port}/"

    async def asyncTearDown(self):
        await self.bridge.stop()

    async def receive(self, websocket):
        return await asyncio.wait_for(websocket.recv(), 2)

    async def reply(self, runtime, command, result):
        await runtime.send(direct.encode_cdp({"id": command["id"], "result": result,
            **({"sessionId": command["sessionId"]} if command.get("sessionId") else {})},
            category="chromeDevtoolsResult", compressed=True))

    async def attach(self, client, runtime, session):
        await client.send(json.dumps({"id": 1, "method": "Target.attachToTarget",
                                      "params": {"targetId": "REAL_TARGET", "flatten": True}}))
        command = direct.decode_frame(await self.receive(runtime))[1]
        self.assertEqual(command["params"]["targetId"], "REAL_TARGET")
        await self.reply(runtime, command, {"sessionId": session})
        self.assertEqual(json.loads(await self.receive(client))["result"]["sessionId"], session)

    async def expect_cleanup(self, runtime, session):
        disable = direct.decode_frame(await self.receive(runtime))[1]
        self.assertEqual(disable["method"], "Debugger.disable")
        self.assertEqual(disable["sessionId"], session)
        await self.reply(runtime, disable, {})
        detach = direct.decode_frame(await self.receive(runtime))[1]
        self.assertEqual(detach["method"], "Target.detachFromTarget")
        self.assertEqual(detach["params"]["sessionId"], session)
        await self.reply(runtime, detach, {})

    async def test_client_ids_isolated_responses_routed_and_root_events_broadcast(self):
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as first, connect(self.cdp_url) as second:
            await first.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            first_command = direct.decode_frame(await self.receive(runtime))[1]
            await second.send(json.dumps({"id": 1, "method": "Page.enable"}))
            second_command = direct.decode_frame(await self.receive(runtime))[1]
            self.assertNotEqual(first_command["id"], second_command["id"])
            for client, command in ((second, second_command), (first, first_command)):
                await self.reply(runtime, command, {"method": command["method"]})
                self.assertEqual(json.loads(await self.receive(client)),
                                 {"id": 1, "result": {"method": command["method"]}})
            await runtime.send(direct.encode_cdp({}, category="chromeDevtoolsResult"))
            event = {"method": "Runtime.executionContextCreated", "params": {"context": {"id": 2}}}
            await runtime.send(direct.encode_cdp(event, category="chromeDevtoolsResult"))
            self.assertEqual(json.loads(await self.receive(first)), event)
            self.assertEqual(json.loads(await self.receive(second)), event)
            self.assertEqual(self.bridge.pending, {})

    async def test_native_flat_sessions_are_preserved_and_isolated(self):
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as first, connect(self.cdp_url) as second:
            await self.attach(first, runtime, "session-a")
            await self.attach(second, runtime, "session-b")
            await first.send(json.dumps({"id": 2, "sessionId": "session-b", "method": "Runtime.enable"}))
            self.assertIn("error", json.loads(await self.receive(first)))
            for client, session in ((first, "session-a"), (second, "session-b")):
                await client.send(json.dumps({"id": 3, "sessionId": session, "method": "Runtime.enable"}))
                command = direct.decode_frame(await self.receive(runtime))[1]
                self.assertEqual(command["sessionId"], session)
                await runtime.send(direct.encode_cdp({"sessionId": session,
                    "method": "Runtime.executionContextCreated", "params": {"context": {"id": 3}}},
                    category="chromeDevtoolsResult"))
                await self.reply(runtime, command, {})
                self.assertEqual(json.loads(await self.receive(client))["sessionId"], session)
                self.assertEqual(json.loads(await self.receive(client)), {"id": 3, "sessionId": session, "result": {}})
            await first.close()
            await self.expect_cleanup(runtime, "session-a")
            await second.send(json.dumps({"id": 4, "sessionId": "session-b", "method": "Runtime.evaluate"}))
            command = direct.decode_frame(await self.receive(runtime))[1]
            self.assertEqual(command["sessionId"], "session-b")
            await self.reply(runtime, command, {"result": {"value": 1}})
            self.assertEqual(json.loads(await self.receive(second))["result"]["result"]["value"], 1)

    async def test_large_script_source_exceeds_old_four_megabyte_limit(self):
        async with connect(self.runtime_url, max_size=direct.MAX_MESSAGE) as runtime, connect(
                self.cdp_url, max_size=direct.MAX_MESSAGE) as client:
            await client.send(json.dumps({"id": 1, "method": "Debugger.getScriptSource",
                                          "params": {"scriptId": "77"}}))
            command = direct.decode_frame(await self.receive(runtime))[1]
            source = "/*" + "x" * (5 * 1024 * 1024) + "*/"
            await self.reply(runtime, command, {"scriptSource": source})
            response = json.loads(await self.receive(client))
            self.assertEqual(response, {"id": 1, "result": {"scriptSource": source}})
            self.assertFalse(client.closed)

    async def test_attach_response_after_client_disconnect_is_detached(self):
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as client:
            await client.send(json.dumps({"id": 1, "method": "Target.attachToTarget",
                                          "params": {"targetId": "REAL_TARGET", "flatten": True}}))
            attach = direct.decode_frame(await self.receive(runtime))[1]
            await client.close()
            await self.reply(runtime, attach, {"sessionId": "late-session"})
            await self.expect_cleanup(runtime, "late-session")

    async def test_explicit_detach_disables_debugger_and_releases_owned_session(self):
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as client:
            await self.attach(client, runtime, "session-a")
            await client.send(json.dumps({"id": 2, "method": "Target.detachFromTarget",
                                          "params": {"sessionId": "session-a"}}))
            await self.expect_cleanup(runtime, "session-a")
            self.assertEqual(json.loads(await self.receive(client)), {"id": 2, "result": {}})
            self.assertEqual(self.bridge.clients[next(iter(self.bridge.clients))]["sessions"], set())
            self.assertEqual(self.bridge.orphan_sessions, set())
            await client.send(json.dumps({"id": 3, "sessionId": "session-a", "method": "Runtime.enable"}))
            self.assertEqual(json.loads(await self.receive(client))["error"]["code"], -32001)

    async def test_reconnected_runtime_cleans_old_debugger_sessions(self):
        async with connect(self.runtime_url) as first, connect(self.cdp_url) as client:
            await self.attach(client, first, "old-session")
            await first.close()
            await client.wait_closed()
            self.assertIn("old-session", self.bridge.orphan_sessions)
            async with connect(self.runtime_url) as replacement:
                await self.expect_cleanup(replacement, "old-session")
                for _ in range(20):
                    if not self.bridge.orphan_sessions:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(self.bridge.orphan_sessions, set())

    async def test_disconnect_closes_stale_sessions_and_unavailable_commands_fail(self):
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as client:
            await client.send(json.dumps({"id": 12, "method": "Page.getResourceTree"}))
            await self.receive(runtime)
            await runtime.close()
            try:
                result = json.loads(await self.receive(client))
                self.assertEqual(result["id"], 12)
                self.assertEqual(result["error"]["code"], -32000)
            except ConnectionClosed:
                pass
            await client.wait_closed()
            self.assertEqual(client.close_code, 1012)
        async with connect(self.cdp_url) as client:
            await client.send(json.dumps({"id": 13, "method": "Runtime.enable"}))
            self.assertIn("error", json.loads(await self.receive(client)))
        async with connect(self.runtime_url) as runtime, connect(self.cdp_url) as client:
            await client.send(json.dumps({"id": 14, "method": "Runtime.enable"}))
            await self.receive(runtime)
            await client.close()
            for _ in range(20):
                if not self.bridge.pending:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(self.bridge.pending, {})

    async def test_discovery_status_and_standby_browser_transport(self):
        def fetch(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.bridge.cdp_port}{path}", timeout=2) as response:
                return json.load(response)
        self.assertEqual(await asyncio.to_thread(fetch, "/json/list"), [])
        async with connect(self.runtime_url) as runtime:
            await runtime.send(b"\xff")
            await runtime.send(direct.DebugMessage(category="setupContext", data=b"opaque").SerializeToString())
            targets = await asyncio.to_thread(fetch, "/json/list")
            self.assertTrue(targets[0]["webSocketDebuggerUrl"].endswith("/devtools/page/current"))
            async with connect(self.runtime_url):
                self.assertEqual(len(self.bridge.runtimes), 2)
            status = await asyncio.to_thread(fetch, "/status")
            self.assertTrue(status["runtime_connected"])
            self.assertEqual(status["categories"], {"setupContext": 1})

    async def test_active_runtime_disconnect_promotes_standby(self):
        async with connect(self.runtime_url) as first, connect(self.runtime_url) as second, connect(self.cdp_url) as client:
            await first.close()
            await client.wait_closed()
            self.assertEqual(client.close_code, 1012)
            self.assertTrue(self.bridge.status()["runtime_connected"])
            async with connect(self.cdp_url) as replacement:
                await replacement.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
                command = direct.decode_frame(await self.receive(second))[1]
                await self.reply(second, command, {"targetInfos": []})
                self.assertEqual(json.loads(await self.receive(replacement))["result"], {"targetInfos": []})

    @unittest.skipUnless(socket.has_ipv6, "IPv6 is unavailable")
    async def test_fixed_runtime_port_accepts_both_loopback_families(self):
        port = self.bridge.debug_port
        await self.bridge.stop()
        await self.bridge.start(port, 0)
        self.assertEqual(len(self.bridge.servers[0].sockets), 2)
        async with connect(f"ws://[::1]:{port}"):
            self.assertTrue(self.bridge.status()["runtime_connected"])


if __name__ == "__main__":
    unittest.main()
