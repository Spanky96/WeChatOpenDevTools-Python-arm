import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import wechat_detect

CONFIGS = str(Path(__file__).resolve().parents[1] / "configs")
MAIN_PATH = ("/Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/"
             "Contents/MacOS/WeChatAppEx")

NEW_LINE = (f" 7952 {MAIN_PATH} --client_version=4066647394 "
            "--wechat-sub-user-agent=MicroMessenger/7.0.20.1781(0x6700143B)")
LEGACY_LINE = (f" 9999 {MAIN_PATH} --client_version=319554834 "
               "--user-agent=MacWechat/3.8.7(0x13080712)")
HELPER_LINE = (" 8000 /Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/"
               "Helpers/WeApp.app/Contents/MacOS/WeApp --type=renderer")


class FindWechatAppExTests(unittest.TestCase):
    def test_finds_main_process_and_skips_helpers(self):
        pid, cmd = wechat_detect.find_wechatappex(
            NEW_LINE + "\n" + HELPER_LINE + "\n")
        self.assertEqual(pid, 7952)
        self.assertIn("client_version", cmd)

    def test_no_process_returns_none(self):
        self.assertEqual(wechat_detect.find_wechatappex(" 1 foo\n"),
                         (None, None))


class ExtractVersionTests(unittest.TestCase):
    def test_client_version_int_maps_to_hex_id(self):
        self.assertEqual(wechat_detect.extract_version_id(NEW_LINE),
                         "f2641d62")

    def test_legacy_ua_only_line(self):
        line = " 1 x --user-agent=MacWechat/3.8.7(0x13080712)"
        self.assertEqual(wechat_detect.extract_version_id(line), "13080712")

    def test_unified_ua_fallback(self):
        line = " 1 x --user-agent=UnifiedPCMacWechat(0xF2641D62)"
        self.assertEqual(wechat_detect.extract_version_id(line), "f2641d62")

    def test_known_ua_id_wins_over_unknown_client_hex(self):
        # pre-4.x: client_version int does not match the UA config id
        version = wechat_detect.extract_version_id(
            LEGACY_LINE, known_ids={"13080712"})
        self.assertEqual(version, "13080712")

    def test_no_signal_returns_none(self):
        self.assertIsNone(wechat_detect.extract_version_id(" 1 x --foo"))
        self.assertIsNone(wechat_detect.extract_version_id(""))


class DetectFlowTests(unittest.TestCase):
    def test_legacy_config_version_routes_to_legacy(self):
        result = wechat_detect.detect_flow(LEGACY_LINE, configs_dir=CONFIGS)
        self.assertEqual(result["flow"], "legacy")
        self.assertEqual(result["version"], "13080712")
        self.assertEqual(result["pid"], 9999)

    def test_new_build_routes_to_new(self):
        result = wechat_detect.detect_flow(NEW_LINE, configs_dir=CONFIGS)
        self.assertEqual(result["flow"], "new")
        self.assertEqual(result["version"], "f2641d62")

    def test_switch_based_config_is_not_legacy(self):
        self.assertNotIn("f2641d62", wechat_detect.legacy_version_ids(CONFIGS))

    def test_missing_wechat_defaults_to_new(self):
        result = wechat_detect.detect_flow(" 1 unrelated\n",
                                           configs_dir=CONFIGS)
        self.assertEqual(result["flow"], "new")
        self.assertIsNone(result["version"])

    def test_unknown_version_routes_to_new(self):
        line = f" 5 {MAIN_PATH} --client_version=123456"
        result = wechat_detect.detect_flow(line, configs_dir=CONFIGS)
        self.assertEqual(result["flow"], "new")
        self.assertEqual(result["version"], "1e240")


if __name__ == "__main__":
    unittest.main()
