import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image

import kie_imagegen as gen


def response(data=None, code=200, content=b""):
    r = Mock(ok=code < 400, status_code=code, content=content)
    r.json.return_value = data
    return r


class ZImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.prompts = self.out / "prompts.txt"
        self.prompts.write_text("===001===\n유형: C\n대사: 안녕\n프롬프트: a cream bear\n"
                                "===002===\n프롬프트: a cafe", encoding="utf-8")
        self.s = gen.GenSettings(str(self.prompts), str(self.out), api_key="test-secret", poll_interval=.001)
        buf = io.BytesIO()
        Image.new("RGB", (32, 18), "blue").save(buf, format="PNG")
        self.download = response(content=buf.getvalue())
        self.created = response({"code": 200, "data": {"taskId": "task-1"}})
        self.success = response({"code": 200, "data": {"state": "success", "resultJson":
                                                     json.dumps({"resultUrls": ["https://result.test/1.png"]})}})

    def run_sync(self, runner=None):
        runner = runner or gen.Runner()
        runner.settings = self.s
        runner.state.status = "running"
        runner._run(self.s)
        return runner

    def journal(self):
        return json.loads((self.out / ".kie-image-tasks.json").read_text(encoding="utf-8"))

    def test_generation_download_and_skip_existing(self):
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success] * 2) as api, \
                patch.object(gen.requests, "get", return_value=self.download) as download:
            runner = self.run_sync()
            self.assertEqual(runner.state.status, "done")
            self.assertEqual(runner.state.done, [1, 2])
            self.assertEqual(api.call_args_list[0].kwargs["json"]["model"], "z-image")
            self.assertEqual(api.call_args_list[0].kwargs["json"]["input"]["aspect_ratio"], "16:9")
            self.assertNotIn("headers", download.call_args.kwargs)
            self.assertNotIn("test-secret", (self.out / ".kie-image-tasks.json").read_text())
            self.run_sync()
            self.assertEqual(api.call_count, 4)
            with Image.open(self.out / "001.jpg") as image:
                self.assertEqual(image.size, (32, 18))

    def test_auth_and_credit_errors_are_safe(self):
        for code, expected in [(401, "키"), (402, "크레딧")]:
            with self.subTest(code=code), patch.object(gen.requests, "request", return_value=
                    response({"code": code, "msg": "test-secret"}, code)):
                runner = self.run_sync()
                self.assertIn(expected, runner.state.error)
                self.assertNotIn("test-secret", str(runner.state.to_dict()))
                self.assertEqual(self.journal(), {})

    def test_timeout_resumes_same_remote_task(self):
        self.s.timeout = 0
        with patch.object(gen.requests, "request", return_value=self.created) as api:
            self.assertEqual(self.run_sync().state.status, "error")
            self.assertEqual(api.call_count, 1)
        self.s.timeout = 10
        self.s.end_no = 1
        with patch.object(gen.requests, "request", return_value=self.success) as api, \
                patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
            self.assertTrue(all(c.args[0] == "GET" for c in api.call_args_list))

    def test_failed_download_reuses_task(self):
        self.s.end_no = 1
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success]), \
                patch.object(gen.requests, "get", side_effect=requests.ConnectionError("test-secret")):
            runner = self.run_sync()
            self.assertIn("다운로드", runner.state.error)
            self.assertEqual(self.journal()["1"]["status"], "pending")
            self.assertFalse((self.out / "001.jpg").exists())
        with patch.object(gen.requests, "request", return_value=self.success) as api, \
                patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
            self.assertEqual(api.call_args.args[0], "GET")

    def test_stop_after_submission_preserves_id_and_blocks_next_scene(self):
        runner = gen.Runner()
        def submitted(*args, **kwargs):
            runner.stop()
            return self.created
        with patch.object(gen.requests, "request", side_effect=submitted) as api:
            self.run_sync(runner)
            self.assertEqual(runner.state.status, "stopped")
            self.assertEqual(api.call_count, 1)
            self.assertEqual(self.journal()["1"]["task_id"], "task-1")

    def test_unknown_submission_is_not_automatically_charged_again(self):
        with patch.object(gen.requests, "request", side_effect=requests.Timeout):
            self.run_sync()
        with patch.object(gen.requests, "request") as api:
            runner = self.run_sync()
            api.assert_not_called()
            self.assertIn("접수 여부", runner.state.error)

    def test_queued_regeneration_runs_after_remaining_scenes(self):
        runner = gen.Runner()
        order = []
        def generate(scene, settings, out, records, journal, regen=False):
            order.append((scene.no, regen))
            if len(order) == 1:
                self.assertTrue(runner.queue_regen(1))
                self.assertFalse(runner.queue_regen(1))
            dest = out / f"{scene.no:03d}.jpg"
            dest.write_bytes(b"image")
            return dest
        with patch.object(runner, "_generate", side_effect=generate):
            self.run_sync(runner)
        self.assertEqual(order, [(1, False), (2, False), (1, True)])
        self.assertEqual(runner.state.status, "done")

    def test_corrupt_saved_image_redownloads_without_new_charge(self):
        self.s.end_no = 1
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success]), patch.object(gen.requests, "get", return_value=self.download):
            self.run_sync()
        (self.out / "001.jpg").write_bytes(b"broken jpeg")
        with patch.object(gen.requests, "request", return_value=self.success) as api, patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
            self.assertTrue(all(c.args[0] == "GET" for c in api.call_args_list))
        self.assertTrue(gen.Runner._valid_image(self.out / "001.jpg"))

    def test_regeneration_reservation_survives_stop_and_new_runner(self):
        runner = gen.Runner()
        def generate(scene, settings, out, records, journal, regen=False):
            runner.queue_regen(1)
            runner.stop()
            return None
        with patch.object(runner, "_generate", side_effect=generate):
            self.run_sync(runner)
        queue = json.loads((self.out / ".kie-image-regens.json").read_text())
        self.assertEqual(queue["queued"], [1])
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success] * 3) as api, patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
            self.assertEqual(sum(c.args[0] == "POST" for c in api.call_args_list), 3)
        self.assertEqual(json.loads((self.out / ".kie-image-regens.json").read_text())["queued"], [])

    def test_pending_active_regeneration_resumes_same_task(self):
        self.s.end_no = 1
        gen.Runner._save(self.out / ".kie-image-tasks.json", {
            "1": {"task_id": "new-task", "status": "pending", "fingerprint": "old"}})
        gen.Runner._save(self.out / ".kie-image-regens.json", {
            "prompts_file": str(self.prompts.resolve()), "queued": [],
            "active": {"scene": 1, "previous_task": "older-task"}})
        with patch.object(gen.requests, "request", return_value=self.success) as api, patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
            self.assertTrue(all(c.args[0] == "GET" for c in api.call_args_list))
            self.assertEqual(api.call_args.kwargs["params"]["taskId"], "new-task")

    def test_completed_active_reservation_does_not_charge_again(self):
        self.s.end_no = 1
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success]), patch.object(gen.requests, "get", return_value=self.download):
            self.run_sync()
        gen.Runner._save(self.out / ".kie-image-regens.json", {
            "prompts_file": str(self.prompts.resolve()), "queued": [],
            "active": {"scene": 1, "previous_task": "older-task"}})
        with patch.object(gen.requests, "request") as api:
            self.assertEqual(self.run_sync().state.status, "done")
            api.assert_not_called()

    def test_regen_backs_up_existing_only_after_success(self):
        (self.out / "001.png").write_bytes(b"original")
        self.s.start_no = self.s.end_no = 1
        self.s.skip_existing = False
        with patch.object(gen.requests, "request", side_effect=[self.created, self.success]), \
                patch.object(gen.requests, "get", return_value=self.download):
            self.assertEqual(self.run_sync().state.status, "done")
        self.assertEqual(next((self.out / "이전").glob("*.png")).read_bytes(), b"original")
        self.assertTrue((self.out / "001.jpg").exists())

    def test_pause_blocks_generation_until_resumed(self):
        runner = gen.Runner()
        runner._pause.set()
        import threading
        result = []
        worker = threading.Thread(target=lambda: result.append(runner._wait(0)))
        worker.start()
        worker.join(.05)
        self.assertTrue(worker.is_alive())
        runner.resume()
        worker.join(1)
        self.assertEqual(result, [True])


if __name__ == "__main__":
    unittest.main()
