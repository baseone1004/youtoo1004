import json
from pathlib import Path
import tempfile
import unittest

import production_library as lib


class ProductionLibraryTests(unittest.TestCase):
    def test_versions_restore_missing_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "대본"
            scripts.mkdir()
            script = scripts / "작업.txt"
            prompts = scripts / "작업_이미지프롬프트.txt"
            prompts.write_text("image prompts", encoding="utf-8")
            for text in ("first", "second", "third", "fourth"):
                script.write_text(text, encoding="utf-8")
                lib.snapshot(root)
            index = lib._index(root)
            self.assertEqual(len(index["작업.txt"]), 3)
            self.assertEqual(len(list(lib._store(root).glob("*.bin"))), 4)
            self.assertEqual(lib.snapshot(root), 0)
            script.unlink()
            prompts.write_text("keep edited prompts", encoding="utf-8")
            rows = lib.library(root)
            work = next(x for x in rows if x["id"] == "작업.txt")
            self.assertTrue(work["recoverable"])
            self.assertFalse(work["script_exists"])
            self.assertEqual(lib.restore_missing(root, "작업.txt")["count"], 1)
            self.assertEqual(script.read_text(encoding="utf-8"), "fourth")
            self.assertEqual(prompts.read_text(encoding="utf-8"), "keep edited prompts")

    def test_private_settings_and_path_escape_are_not_backed_up(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "설정.json").write_text('{"api_key":"secret"}', encoding="utf-8")
            (root / "대본" / "_상태").mkdir(parents=True)
            (root / "대본" / "_상태" / "private.txt").write_text("private")
            self.assertEqual(lib.snapshot(root), 0)
            with self.assertRaises(ValueError):
                lib.restore_missing(root, "../설정.txt")
            with self.assertRaises(ValueError):
                lib.backup_file(root, root / "설정.json")

    def test_changed_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "대본" / "작업.txt"
            script.parent.mkdir()
            script.write_text("original", encoding="utf-8")
            lib.snapshot(root)
            revision = lib._index(root)["작업.txt"][-1]
            (lib._store(root) / revision["file"]).write_bytes(b"modified")
            script.unlink()
            with self.assertRaises(ValueError):
                lib.restore_missing(root, "작업.txt")
            self.assertFalse(script.exists())

    def test_video_priority_and_missing_duplicate_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / "images"
            images.mkdir()
            flow = root / "플로우.txt"
            flow.write_text("# mapping\n1: 1\n2: 2-3\n3: 4\n", encoding="utf-8")
            for name in ("001.jpg", "001.png", "001.mp4", "002.jpg", "002.png", "009.jpg"):
                (images / name).write_bytes(b"test media")
            report = lib.media_report(flow, images)
            self.assertEqual(report["selected"][0]["type"], "video")
            self.assertEqual(report["missing"], [3])
            self.assertEqual(report["duplicates"], [2])
            self.assertEqual(report["extra"], [9])
            saved = root / "매칭.json"
            with self.assertRaises(ValueError):
                lib.require_render_media(flow, images, saved)
            self.assertFalse(json.loads(saved.read_text(encoding="utf-8"))["ok"])
            (images / "002.png").unlink()
            (images / "003.jpg").write_bytes(b"image")
            self.assertTrue(lib.require_render_media(flow, images)["ok"])
            (images / "001.MP4").write_bytes(b"other video")
            # On case-insensitive Windows this replaces the same file; duplicate movie test uses padding.
            (images / "1.mp4").write_bytes(b"duplicate video")
            self.assertEqual(lib.media_report(flow, images)["duplicates"], [1])

    def test_upload_package_is_visible_without_original_script(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "업로드" / "마음읽기연구소" / "완성한 작업"
            folder.mkdir(parents=True)
            (folder / "최종.mp4").write_bytes(b"video")
            rows = lib.library(td)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["package"])
            self.assertFalse(rows[0]["recoverable"])


if __name__ == "__main__":
    unittest.main()
