# -*- coding: utf-8 -*-
"""버튼이 '진행 중'이라며 거절하지 않게: 제작 시작·이어서 만들기는 돌고 있는 대기열 뒤에 붙고, 워커는 단독 작업이 끝나기를 기다린다."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import 대본선택 as app
from 제작대기열 import QueueStore


def running_store(td):
    store = QueueStore(Path(td) / "queue.json")
    store.data = {"status": "running", "current_id": "1", "updated": "", "options": {"steps": {"render": True}},
                  "items": [{"id": "1", "channel": "person", "title": "첫째", "topic": {}, "status": "working", "result": {}}]}
    store.save()
    return store


class QueueAppendTest(unittest.TestCase):
    def test_start_while_running_appends(self):
        with tempfile.TemporaryDirectory() as td:
            store = running_store(td)
            fake_thread = SimpleNamespace(is_alive=lambda: True)
            with patch.object(app, "QUEUE", store), patch.object(app, "QUEUE_THREAD", fake_thread):
                snap = app.create_queue({"items": [{"channel": "person", "title": "둘째"}, {"channel": "person", "title": "첫째"}], "options": {}})
            self.assertEqual(snap["appended"], 1)                                   # 이미 있는 '첫째'는 다시 넣지 않는다
            self.assertEqual([x["title"] for x in store.data["items"]], ["첫째", "둘째"])
            self.assertEqual(store.data["status"], "running")

    def test_continue_while_busy_enqueues_script(self):
        with tempfile.TemporaryDirectory() as td:
            store = running_store(td)
            script = os.path.join(td, "2026-01-01_주제.txt")
            open(script, "w", encoding="utf-8").write("[대본]\n문장.")
            fake_thread = SimpleNamespace(is_alive=lambda: True)
            with patch.object(app, "QUEUE", store), patch.object(app, "QUEUE_THREAD", fake_thread):
                snap = app.enqueue_pipeline({"script_file": script, "channel": "person", "steps": {"optimize": False}, "style": "실사"})
                with self.assertRaises(ValueError):
                    app.enqueue_pipeline({"script_file": script, "channel": "person"})
            self.assertTrue(snap["queued"])
            item = store.data["items"][-1]
            self.assertEqual(item["title"], "2026-01-01_주제")
            self.assertEqual(item["result"]["script"], script)
            self.assertEqual(item["request"]["steps"], {"optimize": False})

    def test_worker_waits_for_standalone_job(self):
        with tempfile.TemporaryDirectory() as td:
            store = QueueStore(Path(td) / "queue.json")
            store.data = {"status": "running", "current_id": "", "updated": "", "options": {},
                          "items": [{"id": "1", "channel": "person", "title": "첫째", "topic": {}, "status": "pending", "result": {}}]}
            store.save()
            standalone = SimpleNamespace(status="running", kind="tts", stage="", cancel_requested=False)
            started = []
            def fake_run(_kind, _fn):
                started.append(time.time())
                return SimpleNamespace(status="done", result={}, error="", stage="완료", progress=1.0)
            with patch.object(app, "QUEUE", store), patch.object(app, "QUEUE_THREAD", None), patch.dict(app.STATE, {"job": standalone}), \
                 patch.object(app, "run_job", side_effect=fake_run), patch.object(app, "verify_final_video", return_value=1.0), \
                 patch.object(app, "telegram_notice"), patch.object(app, "QUEUE_WAIT_SEC", 0.01):
                app.start_queue_worker()
                time.sleep(0.1)
                self.assertEqual(started, [])                                        # 단독 작업이 도는 동안은 시작하지 않는다
                self.assertEqual(store.data["items"][0]["stage"], "앞 작업이 끝나기를 기다리는 중")
                standalone.status = "done"
                app.QUEUE_THREAD.join(timeout=3)
                self.assertFalse(app.QUEUE_THREAD.is_alive())
            self.assertEqual(len(started), 1)
            self.assertEqual(store.data["items"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
