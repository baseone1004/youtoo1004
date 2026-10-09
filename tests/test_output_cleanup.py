import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import production_library as lib
import 대본선택 as app


class CleanupTests(unittest.TestCase):
    def make_work(self, root):
        script = root / "대본" / "작업.txt"
        script.parent.mkdir()
        script.write_text("본문", encoding="utf-8")
        assets = script.with_name("작업_자료")
        assets.mkdir()
        (assets / "나레이션.mp3").write_bytes(b"old")
        (assets / "최종.mp4").write_bytes(b"old video")
        package = root / "업로드" / "채널" / "작업"
        package.mkdir(parents=True)
        (package / "최종.mp4").write_bytes(b"old video")
        (package / "제작원본.json").write_text(json.dumps({"script": "대본/작업.txt"}), encoding="utf-8")
        return script, assets, package

    def test_preview_and_delete_only_selected_work_including_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script, assets, package = self.make_work(root)
            other = script.with_name("작업_다른편.txt")
            other.write_text("보존", encoding="utf-8")
            config = root / "설정.json"
            config.write_text("private", encoding="utf-8")
            lib.snapshot(root)
            preview = lib.upload_cleanup(root, "작업.txt")
            self.assertEqual(preview["count"], 3)
            self.assertTrue(script.exists())
            lib.upload_cleanup(root, "작업.txt", execute=True)
            self.assertFalse(script.exists())
            self.assertFalse(assets.exists())
            self.assertFalse(package.exists())
            self.assertTrue(other.exists())
            self.assertIn("작업_다른편.txt", lib._index(root))
            self.assertEqual(config.read_text(), "private")
            self.assertEqual(lib.restore_missing(root, "작업.txt")["count"], 0)

    def test_package_without_source_only_deletes_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script, assets, package = self.make_work(root)
            (package / "제작원본.json").unlink()
            result = lib.upload_cleanup(root, "upload/채널/작업", execute=True)
            self.assertFalse(result["source_linked"])
            self.assertTrue(script.exists())
            self.assertTrue(assets.exists())
            self.assertFalse(package.exists())

    def test_legacy_queue_links_package_to_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script, assets, package = self.make_work(root)
            (package / "제작원본.json").unlink()
            queue = [{"result": {"script": str(script), "upload_dir": str(package)}}]
            result = lib.upload_cleanup(root, "upload/채널/작업", queue, True)
            self.assertTrue(result["source_linked"])
            self.assertFalse(assets.exists())

    def test_escape_source_rejected_before_deleting_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script, assets, package = self.make_work(root)
            (package / "제작원본.json").write_text(json.dumps({"script": "../outside.txt"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                lib.upload_cleanup(root, "upload/채널/작업", execute=True)
            self.assertTrue(package.exists())
            self.assertTrue(script.exists())

    def test_voice_failure_preserves_old_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "나레이션.mp3").write_bytes(b"original")
            job = app.Job("tts")
            with patch.object(app, "_make_tts", side_effect=RuntimeError("실패")):
                with self.assertRaises(RuntimeError):
                    app.make_tts(job, {"force": True, "out_dir": td})
            self.assertEqual((out / "나레이션.mp3").read_bytes(), b"original")
            self.assertEqual(len(list(out.iterdir())), 1)

    def test_voice_success_replaces_old_parts_and_returns_final_paths(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "tts_parts").mkdir()
            (out / "tts_parts" / "old.mp3").write_bytes(b"old")
            def synth(job, req):
                stage = Path(req["out_dir"])
                for name in ("나레이션.mp3", "나레이션.srt", "플로우.txt"):
                    (stage / name).write_bytes(b"new")
                (stage / "tts_parts").mkdir()
                (stage / "tts_parts" / "new.mp3").write_bytes(b"new")
                return {"mp3": str(stage / "나레이션.mp3"), "srt": str(stage / "나레이션.srt"), "flow": str(stage / "플로우.txt")}
            with patch.object(app, "_make_tts", side_effect=synth):
                result = app.make_tts(app.Job("tts"), {"force": True, "out_dir": td})
            self.assertEqual(result["mp3"], str(out / "나레이션.mp3"))
            self.assertFalse((out / "tts_parts" / "old.mp3").exists())
            self.assertTrue((out / "tts_parts" / "new.mp3").exists())

    def test_render_success_and_failure_preserve_correct_version(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "최종.mp4"
            target.write_bytes(b"old")
            with patch.object(app, "_run_render", side_effect=RuntimeError("fail")):
                with self.assertRaises(RuntimeError):
                    app.run_render(app.Job("pipeline"), "", "", "", "", str(target))
            self.assertEqual(target.read_bytes(), b"old")
            def render(job, srt, flow, images, narration, output, kb):
                Path(output).write_bytes(b"new")
                return {"output": output}
            with patch.object(app, "_run_render", side_effect=render):
                result = app.run_render(app.Job("pipeline"), "", "", "", "", str(target))
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(result["output"], str(target))
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_cancelled_voice_and_render_keep_originals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "나레이션.mp3"
            video = root / "최종.mp4"
            audio.write_bytes(b"old audio")
            video.write_bytes(b"old video")
            job = app.Job("tts")
            job.cancel_requested = True
            with patch.object(app, "_make_tts", return_value={}):
                with self.assertRaisesRegex(RuntimeError, "취소"):
                    app.make_tts(job, {"force": True, "out_dir": td})
            with patch.object(app, "_run_render", return_value={}):
                with self.assertRaisesRegex(RuntimeError, "취소"):
                    app.run_render(job, "", "", "", "", str(video))
            self.assertEqual(audio.read_bytes(), b"old audio")
            self.assertEqual(video.read_bytes(), b"old video")
            self.assertEqual(len(list(root.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
