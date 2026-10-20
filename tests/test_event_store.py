import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services import event_store
from services import capture_config
from services import service_session as service


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "events.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, number, max_bytes=1000000):
        event_store.append_event({"t": 100, "n": number}, self.path, max_bytes=max_bytes)

    def test_dense_events_with_equal_timestamps_paginate_without_loss(self):
        for number in range(751):
            self.write(number)
        result, cursor = [], None
        while True:
            page = event_store.read_events(self.path, cursor=cursor, since=100, limit=200)
            result.extend(event["n"] for event in page["events"])
            cursor = page["cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(result, list(range(751)))
        self.assertEqual(event_store.read_events(self.path, cursor=cursor)["events"], [])

    def test_cursor_finishes_previous_file_before_reading_rotated_file(self):
        for number in range(3):
            self.write(number)
        page = event_store.read_events(self.path, limit=1)
        self.write(3, max_bytes=1)
        following = event_store.read_events(self.path, cursor=page["cursor"])
        self.assertEqual([record["n"] for record in following["events"]], [1, 2, 3])
        self.assertFalse(following["reset"])
        initial = event_store.read_events(self.path)
        self.assertEqual([record["n"] for record in initial["events"]], [0, 1, 2, 3])

    def test_partial_line_is_retried_and_expired_cursor_is_reported(self):
        self.write(0)
        page = event_store.read_events(self.path)
        with open(self.path, "ab") as stream:
            stream.write(b'{"t":100,"n":1')
        partial = event_store.read_events(self.path, cursor=page["cursor"])
        self.assertEqual(partial["events"], [])
        with open(self.path, "ab") as stream:
            stream.write(b'}\n')
        complete = event_store.read_events(self.path, cursor=partial["cursor"])
        self.assertEqual(complete["events"][0]["n"], 1)
        for number in range(2, 7):
            self.write(number, max_bytes=1)
        reset = event_store.read_events(self.path, cursor=complete["cursor"])
        self.assertTrue(reset["reset"])
        self.assertLessEqual(len(list(Path(self.tmp.name).glob("events.jsonl*"))), 4)

    def test_invalid_cursor_is_rejected(self):
        self.write(0)
        for cursor in ("invalid", "a", event_store.encode_cursor(1, -1)):
            with self.assertRaises(ValueError):
                event_store.read_events(self.path, cursor=cursor)


class ConfigTests(unittest.TestCase):
    def test_config_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "capture.json")
            with patch.object(capture_config, "CONFIG_FILE", path):
                self.assertEqual(capture_config.load_config(), capture_config.DEFAULTS)
                saved = capture_config.save_config({"response_body": 65536})
                self.assertEqual(capture_config.load_config(), saved)
                for values in ({"response_body": 0}, {"headers": True}, {"arbitrary": 999}):
                    with self.assertRaises(ValueError):
                        capture_config.save_config(values)
                self.assertEqual(capture_config.load_config(), saved)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.failed = patch.object(service, "FAILED_CTX", {})
        self.diag = patch.object(service, "DIAGNOSTICS", {})
        self.failed.start()
        self.diag.start()

    def tearDown(self):
        self.failed.stop()
        self.diag.stop()

    def test_retry_backoff_is_bounded_and_new_context_skips_wait(self):
        with patch.object(service.time, "time", return_value=100):
            for delay in (2, 4, 8, 16, 30, 30):
                service.retry_later("app", "target", "session", "waiting")
                self.assertEqual(service.FAILED_CTX["target"]["next_retry"], 100 + delay)
        bridge = service.Br(None)
        bridge._runtime_event({"method": "Runtime.executionContextCreated", "sessionId": "session",
                               "params": {"context": {"id": 88}}})
        self.assertEqual(service.FAILED_CTX["target"]["next_retry"], 0)
        self.assertEqual(bridge.contexts["session"][88], {"id": 88})


if __name__ == "__main__":
    unittest.main()
