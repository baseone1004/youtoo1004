import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import 대본선택 as app
from 제작대기열 import QueueStore

class StartupSafetyTest(unittest.TestCase):
    def test_restart_restores_jobs_without_starting_paid_work(self):
        with tempfile.TemporaryDirectory() as td:
            store = QueueStore(Path(td) / "queue.json")
            store.data.update(status="running", resume_on_start=True, current_id="a", items=[
                {"id": "a", "status": "working"}, {"id": "b", "status": "done"}])
            with patch.object(app, "QUEUE", store), patch.object(app, "start_queue_worker") as worker, patch.object(app, "aip") as network:
                app.auto_resume_queue()
                worker.assert_not_called()
                network.assert_not_called()
            self.assertEqual(store.data["status"], "paused")
            self.assertFalse(store.data["resume_on_start"])
            self.assertEqual([x["status"] for x in store.data["items"]], ["pending", "done"])
            self.assertEqual(QueueStore(store.path).data["status"], "paused")

    def test_cancelled_queue_stays_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            store = QueueStore(Path(td) / "queue.json")
            store.data.update(status="cancelled", items=[{"status": "pending"}])
            with patch.object(app, "QUEUE", store):
                app.auto_resume_queue()
            self.assertEqual(store.data["status"], "cancelled")
