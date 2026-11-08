"""Offline tests for the download audit history."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bot


class AuditHistoryTests(unittest.TestCase):
    def test_request_and_result_records_are_appended(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "downloads.jsonl"
            message = SimpleNamespace(chat_id=-1001234567890, message_id=42)
            url = "https://www.youtube.com/watch?v=example&foo=bar"

            with patch.object(bot, "AUDIT_LOG", path):
                bot.audit_event(message, url, "requested")
                bot.audit_event(message, url, "uploaded", quality="720p", size_bytes=123456)

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual([r["event"] for r in records], ["requested", "uploaded"])
            self.assertEqual([r["chat_id"] for r in records], [-1001234567890] * 2)
            self.assertEqual([r["message_id"] for r in records], [42] * 2)
            self.assertEqual([r["url"] for r in records], [url] * 2)
            self.assertEqual(records[1]["quality"], "720p")
            self.assertEqual(records[1]["size_bytes"], 123456)
            self.assertIn("timestamp", records[0])

    def test_url_control_characters_cannot_break_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "downloads.jsonl"
            message = SimpleNamespace(chat_id=1234567, message_id=1)
            url = "https://example.com/video?title=first\nsecond"

            with patch.object(bot, "AUDIT_LOG", path):
                bot.audit_event(message, url, "requested")

            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["url"], url)


if __name__ == "__main__":
    unittest.main()
