# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from 제작대기열 import QueueStore, recent_chats, send_telegram


class QueueStoreTest(unittest.TestCase):
    def test_save_and_restore_running_item_as_pending(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "queue.json"
            store = QueueStore(path)
            store.data = {"status": "running", "current_id": "a", "options": {},
                          "items": [{"id": "a", "status": "working", "title": "주제"}], "updated": ""}
            store.save()
            restored = QueueStore(path)
            self.assertEqual(restored.data["status"], "running")
            self.assertEqual(restored.data["items"][0]["status"], "pending")
            self.assertEqual(restored.public()["items"][0]["status_text"], "대기 중")

    def test_completed_queue_is_normalized_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "queue.json"
            path.write_text(json.dumps({"status": "running", "current_id": "a", "options": {},
                                        "items": [{"id": "a", "status": "done"}]}), encoding="utf-8")
            self.assertEqual(QueueStore(path).data["status"], "done")


class TelegramTest(unittest.TestCase):
    @patch("제작대기열.telegram_call")
    def test_recent_chats_are_deduplicated(self, call):
        call.return_value = [{"message": {"chat": {"id": 1, "first_name": "사용자", "type": "private"}}},
                             {"message": {"chat": {"id": 1, "first_name": "사용자", "type": "private"}}}]
        self.assertEqual(recent_chats("token"), [{"id": "1", "name": "사용자", "type": "private"}])

    @patch("제작대기열.telegram_call")
    def test_send_message_uses_selected_chat(self, call):
        self.assertTrue(send_telegram("token", "123", "완료"))
        call.assert_called_once_with("token", "sendMessage", {"chat_id": "123", "text": "완료"})
