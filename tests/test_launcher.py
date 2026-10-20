import subprocess
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit

import start


class LauncherTests(unittest.TestCase):
    def test_devtools_nested_websocket_query_is_encoded(self):
        handler = object.__new__(start.Handler)
        handler.path = "/api/open_devtools?target_id=ABC123&appid=wx0000000000000001"
        handler._json = Mock()
        with patch.object(start.subprocess, "Popen") as launch:
            handler.do_GET()
        result = handler._json.call_args.args[0]
        self.assertTrue(result["ok"])
        url = result["url"]
        self.assertEqual(url.count("?"), 1)
        self.assertEqual(parse_qs(urlsplit(url).query), {
            "ws": ["127.0.0.1:9333/devtools/page/ABC123?appid=wx0000000000000001"]})
        self.assertIn(url, launch.call_args.args[0][-1])

    def test_shutdown_waits_for_native_hook_cleanup_before_kill(self):
        native, stuck = Mock(), Mock()
        native.poll.return_value = stuck.poll.return_value = None
        stuck.wait.side_effect = [subprocess.TimeoutExpired("stuck", 4), 0]
        order = Mock()
        order.attach_mock(native, "native")
        order.attach_mock(stuck, "stuck")
        with patch.dict(start._children, {"sniper": native, "other": stuck}, clear=True):
            start.stop_children()
        native.terminate.assert_called_once()
        native.wait.assert_called_once()
        native.kill.assert_not_called()
        stuck.kill.assert_called_once()
        self.assertLess(order.mock_calls.index(call.native.terminate()),
                        order.mock_calls.index(call.stuck.kill()))


if __name__ == "__main__":
    unittest.main()
