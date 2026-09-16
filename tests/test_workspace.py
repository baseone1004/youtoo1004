# -*- coding: utf-8 -*-
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
