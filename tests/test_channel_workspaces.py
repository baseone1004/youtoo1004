import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import channel_workspaces as workspaces
from process_lock import file_lock
import 웹큐
from 편집프로그램_동시작업_연결 import apply as patch_editor


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(workspaces, "HOME", self.home).start()
        patch.object(workspaces, "fetch", return_value=None).start()
        self.editor = self.home / "편집프로그램"
        self.editor.mkdir()
        (self.editor / "app.py").write_text("# fixture", encoding="utf-8")
        workspaces.write_json(self.editor / "config.json", {"kie_api_key": "test-kie", "last": {"output": "old.mp4"}, "gen": {"output": "old"}, "ui": {"srt_font": "Test"}})
        self.cfg = {"유튜브_선택_person": "ko", "인월드_API_키": "test-voice",
                    "인월드_목소리_사람": "Korean", "인월드_목소리_일본": "Japanese",
                    "유튜브_계정": [
                        {"id": "ko", "name": "마음", "url": "https://www.youtube.com/@ko", "language": "ko", "api_key": "ko-key", "profile": {"이름": "old-ko", "언어": "ko"}},
                        {"id": "ja", "name": "日本", "url": "https://www.youtube.com/@ja", "language": "ja", "api_key": "ja-key", "profile": {"이름": "日本", "언어": "ja", "썸네일": {"레이아웃": "jp_cozy"}, "마스코트": {"이미지": "레퍼런스/ja.png"}}}]}
        workspaces.write_json(self.home / "설정.json", self.cfg)
        workspaces.write_json(self.home / "채널_프로필.json", {"person": {"이름": "마음 최신", "언어": "ko", "썸네일": {"레이아웃": "jalnan_pop"}}})
        (self.home / "레퍼런스").mkdir()
        (self.home / "레퍼런스" / "ja.png").write_bytes(b"reference")
        (self.home / "대본").mkdir()
        (self.home / "대본" / "original.txt").write_text("keep")

    def test_initialization_separates_language_profile_keys_references_and_media(self):
        ko, ja = self.home / "ko", self.home / "ja"
        workspaces.initialize(ko, "ko", self.editor)
        workspaces.initialize(ja, "ja", self.editor)
        self.assertEqual(workspaces.read_json(ko / "채널_프로필.json")["person"]["이름"], "마음 최신")
        self.assertEqual(workspaces.read_json(ja / "채널_프로필.json")["person"]["썸네일"]["레이아웃"], "jp_cozy")
        cfg = workspaces.read_json(ja / "설정.json")
        self.assertEqual(cfg["유튜브_API_키"], "ja-key")
        self.assertEqual(cfg["인월드_목소리_일본"], "Japanese")
        self.assertEqual(len(cfg["유튜브_계정"]), 1)
        (ja / "레퍼런스" / "ja.png").write_bytes(b"changed")
        self.assertEqual((self.home / "레퍼런스" / "ja.png").read_bytes(), b"reference")
        self.assertFalse((ja / "대본").exists())
        self.assertEqual((self.home / "대본" / "original.txt").read_text(), "keep")
        editor_cfg = workspaces.read_json(ja / "편집프로그램" / "config.json")
        self.assertEqual(editor_cfg["kie_api_key"], "test-kie")
        self.assertNotIn("last", editor_cfg)
        self.assertNotIn("gen", editor_cfg)

    def test_running_channel_reuses_process_without_overwriting_settings(self):
        target = workspaces.workspace_dir("ja")
        record = {"id": "ja", "instance": "test", "port": 18805}
        workspaces.write_json(target / ".workspace.json", record)
        workspaces.write_json(target / "설정.json", {"saved": True})
        with patch.object(workspaces, "own_server", return_value=True), patch.object(workspaces, "launch_process") as spawn:
            result = workspaces.launch("ja")
        self.assertEqual(result["url"], "http://127.0.0.1:18805/")
        spawn.assert_not_called()
        self.assertEqual(workspaces.read_json(target / "설정.json"), {"saved": True})

    def test_main_channel_never_creates_duplicate_worker(self):
        with patch.object(workspaces, "launch_process") as spawn:
            self.assertTrue(workspaces.launch("ko")["main"])
        spawn.assert_not_called()

    def test_invalid_account_and_path_traversal(self):
        with self.assertRaises(ValueError):
            workspaces.launch("../../bad")
        self.assertEqual(workspaces.workspace_dir("../../bad").parent, self.home / workspaces.STORE_NAME)

    def test_identity_rejects_unrelated_listener(self):
        with patch.object(workspaces, "fetch", return_value={"id": "ja", "token": "other"}):
            self.assertFalse(workspaces.own_server({"id": "ja", "instance": "expected", "port": 18800}))

    def test_broker_secret_is_persistent_and_accounts_are_public(self):
        self.assertEqual(workspaces.broker_key(), workspaces.broker_key())
        with patch.object(workspaces, "own_server", return_value=False):
            data = json.dumps(workspaces.accounts())
        for key in ("test-voice", "ko-key", "ja-key", "test-kie"):
            self.assertNotIn(key, data)

    def test_code_refresh_preserves_saved_settings_and_work(self):
        target = self.home / "worker"
        target.mkdir()
        workspaces.write_json(target / "설정.json", {"custom": "keep"})
        (target / "대본").mkdir()
        (target / "대본" / "saved.txt").write_text("keep")
        (self.home / "testcode.py").write_text("value = 1")
        workspaces.sync_code(target, self.editor, include_editor=False)
        self.assertEqual(workspaces.read_json(target / "설정.json"), {"custom": "keep"})
        self.assertTrue((target / "대본" / "saved.txt").exists())
        self.assertTrue((target / "testcode.py").exists())

    def test_simultaneous_open_requests_spawn_only_one_pair(self):
        child = Mock()
        child.poll.return_value = None
        results, errors = [], []
        def open_channel():
            try:
                results.append(workspaces.launch("ja"))
            except Exception as exc:
                errors.append(exc)
        with patch.object(workspaces, "sync_code"), patch.object(workspaces, "own_server", return_value=True), patch.object(workspaces, "own_editor", return_value=True), patch.object(workspaces, "launch_process", return_value=child) as spawn:
            threads = [threading.Thread(target=open_channel) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], results[1]["url"])
        self.assertEqual(spawn.call_count, 2)

    def test_failed_start_only_terminates_new_children(self):
        failed, alive = Mock(), Mock()
        failed.poll.return_value = 1
        alive.poll.return_value = None
        with patch.object(workspaces, "sync_code"), patch.object(workspaces, "launch_process", side_effect=[failed, alive]):
            with self.assertRaisesRegex(RuntimeError, "실행하지 못했습니다"):
                workspaces.launch("ja")
        failed.terminate.assert_not_called()
        alive.terminate.assert_called_once()

    def test_backend_recovery_reuses_its_editor_and_keeps_settings(self):
        target = workspaces.workspace_dir("ja")
        record = {"id": "ja", "instance": "test", "port": 18805, "editor_port": 18806}
        workspaces.write_json(target / ".workspace.json", record)
        workspaces.write_json(target / "설정.json", {"saved": True})
        child = Mock(); child.poll.return_value = None
        with patch.object(workspaces, "sync_code") as sync, patch.object(workspaces, "own_server", side_effect=[False, True]), patch.object(workspaces, "own_editor", return_value=True), patch.object(workspaces, "launch_process", return_value=child) as spawn:
            workspaces.launch("ja")
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args.args[0][0], "대본선택.py")
        self.assertFalse(sync.call_args.kwargs["include_editor"])
        self.assertEqual(workspaces.read_json(target / "설정.json"), {"saved": True})


class CrossProcessLockTest(unittest.TestCase):
    def test_other_process_waits_and_crash_releases_lock(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lock"
            code = "import sys,time; from process_lock import file_lock\nwith file_lock(sys.argv[1]):\n print('locked',flush=True)\n time.sleep(30)"
            proc = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), "locked")
                with self.assertRaises(RuntimeError):
                    with file_lock(path, timeout=0.1):
                        self.fail("lock was shared")
            finally:
                proc.terminate(); proc.wait(timeout=5); proc.stdout.close()
            with file_lock(path, timeout=1):
                pass

    def test_waiting_can_cancel_without_entering_render(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "취소됨"):
                with file_lock(Path(td) / "lock", cancelled=lambda: True):
                    self.fail("cancelled render started")


class BrokerTest(unittest.TestCase):
    def tearDown(self):
        with 웹큐._event:
            웹큐._jobs.clear()

    def test_two_channels_return_correct_results_and_cancel_is_scoped(self):
        a = 웹큐.broker_operation({"op": "submit", "text": "한국어"})["id"]
        b = 웹큐.broker_operation({"op": "submit", "text": "日本語"})["id"]
        claim = 웹큐.next_job(0)
        self.assertEqual(claim["id"], a)
        웹큐.finish(a, "한국 결과", claim=claim["claim"])
        claim = 웹큐.next_job(0)
        self.assertEqual(claim["id"], b)
        웹큐.finish(b, "日本の結果", claim=claim["claim"])
        self.assertEqual(웹큐.broker_operation({"op": "poll", "id": a})["result"], "한국 결과")
        웹큐.broker_operation({"op": "cancel", "id": a})
        self.assertEqual(웹큐.broker_operation({"op": "poll", "id": b})["result"], "日本の結果")

    def test_remote_wait_cancels_only_own_request(self):
        with patch.dict(os.environ, {"YOUTOO_WEB_BROKER": "http://127.0.0.1:8766"}), patch.object(웹큐, "remote") as remote:
            with self.assertRaisesRegex(RuntimeError, "취소됨"):
                웹큐.wait("my-id", cancel_check=lambda: True)
            remote.assert_called_once_with("cancel", id="my-id")

    def test_remote_wait_keeps_success_even_if_cleanup_connection_fails(self):
        with patch.dict(os.environ, {"YOUTOO_WEB_BROKER": "http://127.0.0.1:8766"}), patch.object(웹큐, "remote", side_effect=[{"status": "done", "result": "text"}, RuntimeError("offline")]):
            self.assertEqual(웹큐.wait("my-id"), "text")

    def test_remote_status_does_not_duplicate_alive_field(self):
        with patch.dict(os.environ, {"YOUTOO_WEB_BROKER": "http://127.0.0.1:8766"}), patch.object(웹큐, "remote", return_value={"alive": True, "pending": 1, "taken": []}):
            self.assertEqual(dict(alive=웹큐.extension_alive(), **웹큐.status()), {"alive": True, "pending": 1, "taken": []})


class EditorPatchTest(unittest.TestCase):
    def test_patch_compiles_is_idempotent_and_checks_real_cancel_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "core").mkdir()
            (root / "app.py").write_text('@app.post("/api/render")\ndef api_render(req):\n    def work(job):\n        if True:\n            render(job, tl.segments, tl.total_duration, opts)\n', encoding="utf-8")
            patch_editor(root)
            once = (root / "app.py").read_text(encoding="utf-8")
            patch_editor(root)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), once)
            compile(once, "editor", "exec")
            self.assertIn("job.cancel_requested", once)
            self.assertIn('"/api/channel-workspace"', once)
            self.assertTrue((root / "core" / "process_lock.py").is_file())


if __name__ == "__main__":
    unittest.main()
