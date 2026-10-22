import os
import subprocess
import unittest
from unittest.mock import ANY, Mock, call, patch
from urllib.parse import parse_qs, urlsplit

import main


class LauncherTests(unittest.TestCase):
    def test_devtools_nested_websocket_query_is_encoded(self):
        handler = object.__new__(main.Handler)
        handler.path = "/api/open_devtools?target_id=ABC123&appid=wx0000000000000001"
        handler._json = Mock()
        with patch.object(main.subprocess, "Popen") as launch:
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
        with patch.dict(main._children, {"sniper": native, "other": stuck}, clear=True):
            main.stop_children()
        native.terminate.assert_called_once()
        native.wait.assert_called_once()
        native.kill.assert_not_called()
        stuck.kill.assert_called_once()
        self.assertLess(order.mock_calls.index(call.native.terminate()),
                        order.mock_calls.index(call.stuck.kill()))


if __name__ == "__main__":
    unittest.main()


class DispatchTests(unittest.TestCase):
    """启动分流：微信未启动时等待；legacy 前台注入；new 起 Dashboard。"""

    def test_legacy_flow_runs_inline_without_dashboard(self):
        detect = {"flow": "legacy", "version": "13080911", "pid": 999}
        with patch.dict(os.environ), \
             patch.object(main, "wait_for_wechat", return_value=detect), \
             patch.object(main, "stop_previous_manager"), \
             patch.object(main, "cleanup_stale_services"), \
             patch.object(main, "run_legacy_inline") as legacy, \
             patch.object(main, "run_dashboard") as dashboard:
            main.main([])
        legacy.assert_called_once_with(999, "13080911", ANY)
        dashboard.assert_not_called()

    def test_new_flow_runs_dashboard(self):
        detect = {"flow": "new", "version": "f2641d62", "pid": 7952}
        with patch.dict(os.environ), \
             patch.object(main, "wait_for_wechat", return_value=detect), \
             patch.object(main.wechat_detect, "command_for_pid",
                          return_value=""), \
             patch.object(main, "run_dashboard") as dashboard, \
             patch.object(main, "run_legacy_inline") as legacy:
            main.main([])
        dashboard.assert_called_once_with(detect)
        legacy.assert_not_called()

    def test_pid_flag_runs_one_shot_legacy(self):
        with patch.dict(os.environ), \
             patch.object(main, "_resolve_mode", return_value="direct"), \
             patch.object(main.wechat_detect, "command_for_pid",
                          return_value="--client_version=319293713"), \
             patch.object(main, "stop_previous_manager"), \
             patch.object(main, "cleanup_stale_services"), \
             patch.object(main, "run_legacy_inline") as legacy:
            main.main(["-p", "62242"])
        legacy.assert_called_once_with(62242, "13080911")

    def test_pretty_version_prefers_ua_string(self):
        self.assertEqual(
            main._pretty_wechat_version(
                "13080911", "x --user-agent=MacWechat/3.8.9(0x13080911)"),
            "3.8.9")

    def test_pretty_version_falls_back_to_config(self):
        self.assertEqual(main._pretty_wechat_version("f2641d62", ""),
                         "4.1.13.98")

    def test_pretty_version_ignores_frozen_compat_ua(self):
        # 4.x 挂着永不更新的 MacWechat/3.8.7(0x13080712) 兼容 UA，不能采信
        cmd = ("WeChatAppEx --wechat-sub-user-agent=MicroMessenger/7.0.20.1781"
               "(0x6700143B) MacWechat/3.8.7(0x13080712) "
               "UnifiedPCMacWechat(0xf2641d62) --client_version=4066647394")
        self.assertEqual(main._pretty_wechat_version("f2641d62", cmd),
                         "4.1.13.98")


if __name__ == "__main__":
    unittest.main()
