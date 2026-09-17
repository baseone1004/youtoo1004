# -*- coding: utf-8 -*-
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class WorkspaceEditorTest(unittest.TestCase):
    def test_thumbnail_seo_context_uses_each_videos_metadata(self):
        optimized = """[추천 제목]\n1번 — 검색 의도와 궁금증\n[설명글]\n나이 들수록 친구가 줄어드는 이유\n관계 심리를 이야기합니다\n[태그]\n인간관계, 친구관계, 중년심리\n"""
        script = "[제목]\n친구가 줄어드는 진짜 이유\n[대본]\n본문"
        context = app.thumbnail_seo_context(optimized, script, False)
        self.assertIn("친구가 줄어드는 진짜 이유", context)
        self.assertIn("인간관계, 친구관계, 중년심리", context)
        self.assertIn("40~60대", context)

    def test_upload_package_collects_metadata_thumbnail_and_video(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "작업.txt"
            script.write_text("[제목]\n원래 제목\n[대본]\n본문", encoding="utf-8")
            assets = root / "작업_자료"
            thumbs = assets / "썸네일"
            thumbs.mkdir(parents=True)
            (thumbs / "썸네일_1.jpg").write_bytes(b"image")
            video = assets / "최종.mp4"
            video.write_bytes(b"video")
            (root / "작업_유튜브최적화.txt").write_text(
                "[최종 추천]\n검색에 맞춘 제목\n[설명글]\n영상 설명\n[태그]\n인생, 심리", encoding="utf-8")
            with patch.object(app, "BASE", str(root)):
                package = Path(app.make_upload_package(str(script), {"video": str(video)}))
            self.assertEqual((package / "제목.txt").read_text(encoding="utf-8"), "검색에 맞춘 제목")
            self.assertTrue((package / "설명.txt").is_file())
            self.assertTrue((package / "태그.txt").is_file())
            self.assertTrue((package / "썸네일_1.jpg").is_file())
            self.assertTrue((package / "최종.mp4").is_file())

    def test_load_and_save_script_subtitle_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "작업.txt"
            script.write_text("[제목]\n처음 제목\n[설명글]\n처음 설명\n[출처]\n출처 A\n[태그]\n#태그\n[대본]\n본문", encoding="utf-8")
            assets = root / "작업_자료"
            assets.mkdir()
            (assets / "나레이션.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n자막", encoding="utf-8")
            with patch.object(app, "대본_폴더", str(root)):
                data = app.workspace_data(str(script))
                self.assertEqual(data["title"], "처음 제목")
                self.assertEqual(data["sources"], "출처 A")
                app.save_workspace({"script_file": str(script), "kind": "srt", "text": "수정 자막"})
                app.save_workspace({"script_file": str(script), "kind": "metadata", "title": "새 제목", "description": "새 설명", "sources": "새 출처", "tags": "새 태그"})
                self.assertEqual((assets / "나레이션.srt").read_text(encoding="utf-8"), "수정 자막")
                saved = json.loads((assets / "업로드_정보.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["title"], "새 제목")


if __name__ == "__main__":
    unittest.main()


class ResetEverythingTest(unittest.TestCase):
    def test_moves_all_work_to_trash_and_keeps_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "대본").mkdir(); (root / "대본" / "민담" / "2026-09-01_옛이야기").mkdir(parents=True)
            (root / "대본" / "2026-09-01_주제.txt").write_text("x", encoding="utf-8")
            (root / "대본" / "2026-09-01_주제_자료" / "images").mkdir(parents=True)
            (root / "업로드" / "사람의 이유" / "2026-09-01_주제").mkdir(parents=True)
            (root / "사용한_주제.txt").write_text("주제\n", encoding="utf-8")
            (root / "설정.json").write_text("{}", encoding="utf-8")
            cwd = os.getcwd(); os.chdir(root)
            try:
                store = app.QueueStore(str(root / "대본" / "_상태" / "q.json"))
                store.data["items"] = [{"id": "a", "status": "pending"}]; store.data["status"] = "running"
                with patch.object(app, "BASE", str(root)), patch.object(app, "QUEUE", store):
                    result = app.reset_everything()
            finally:
                os.chdir(cwd)
            self.assertGreaterEqual(result["count"], 4)
            self.assertFalse((root / "대본" / "2026-09-01_주제.txt").exists())
            self.assertFalse(list((root / "업로드" / "사람의 이유").iterdir()))
            self.assertFalse((root / "사용한_주제.txt").exists())
            self.assertTrue((root / "설정.json").exists())
            self.assertTrue(any(p.name == "2026-09-01_주제.txt" for p in Path(result["trash"]).iterdir()))
            self.assertEqual(store.data["status"], "idle")
