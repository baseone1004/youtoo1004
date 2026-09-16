# -*- coding: utf-8 -*-
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import 대본선택 as app
from 제작대기열 import QueueStore


class ContinuousPipelineTest(unittest.TestCase):
    def make_store(self, td):
        store = QueueStore(Path(td) / "queue.json")
        store.data = {"status": "running", "current_id": "", "updated": "", "options": {"steps": {"render": True}},
                      "items": [{"id": "1", "channel": "person", "title": "첫째", "topic": {}, "status": "pending", "result": {}},
                                {"id": "2", "channel": "person", "title": "둘째", "topic": {}, "status": "pending", "result": {}}]}
        store.save()
        return store

    def wait_done(self, store):
        for _ in range(100):
            if store.data["status"] == "done":
                return
            time.sleep(0.01)
        self.fail("대기열 작업이 끝나지 않았습니다.")

    def test_runs_selected_topics_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(td); order = []
            def fake_run(_kind, _fn):
                current = next(x for x in store.data["items"] if x["id"] == store.data["current_id"])
                order.append(current["title"])
                return SimpleNamespace(status="done", result={"video": "ok.mp4", "assets": current["title"]}, error="", stage="완료", progress=1.0)
            with patch.object(app, "QUEUE", store), patch.object(app, "QUEUE_THREAD", None), \
                 patch.object(app, "run_job", side_effect=fake_run), patch.object(app, "verify_final_video", return_value=60.0), \
                 patch.object(app, "telegram_notice"):
                app.start_queue_worker(); self.wait_done(store)
            self.assertEqual(order, ["첫째", "둘째"])
            self.assertTrue(all(x["status"] == "done" for x in store.data["items"]))

    def test_item_failure_continues_to_next_topic(self):
        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(td); calls = [0]
            def fake_run(_kind, _fn):
                calls[0] += 1
                if calls[0] == 1:
                    return SimpleNamespace(status="error", result={}, error="개별 장면 실패", stage="실패", progress=1.0)
                return SimpleNamespace(status="done", result={"video": "ok.mp4"}, error="", stage="완료", progress=1.0)
            with patch.object(app, "QUEUE", store), patch.object(app, "QUEUE_THREAD", None), \
                 patch.object(app, "run_job", side_effect=fake_run), patch.object(app, "verify_final_video", return_value=60.0), \
                 patch.object(app, "telegram_notice"):
                app.start_queue_worker(); self.wait_done(store)
            self.assertEqual([x["status"] for x in store.data["items"]], ["error", "done"])


if __name__ == "__main__":
    unittest.main()
