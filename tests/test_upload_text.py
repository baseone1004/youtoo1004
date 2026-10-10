import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class UploadTextTests(unittest.TestCase):
    def test_korean_and_japanese_texts_preserve_language_and_hashtag_spacing(self):
        for title, description, tags in (("마음의 거리", "한국어 설명", "마음, 관계"),
                                          ("心の距離", "日本語の説明", "心理, 人間関係")):
            with self.subTest(title=title), tempfile.TemporaryDirectory() as td:
                data = dict(title=title, description=description, tags=tags, sources="資料・출처")
                path = Path(app.write_upload_texts(td, data, separate=True))
                text = path.read_text(encoding="utf-8")
                self.assertIn(title, text)
                self.assertIn(description + "\n\n\n\n#", text)
                self.assertIn("[태그]\n" + tags, text)
                self.assertIn("資料・출처", text)
                self.assertEqual((path.parent / "제목.txt").read_text(encoding="utf-8"), title)

    def test_edit_only_updates_packages_for_same_source_script(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = {}
            packages = {}
            for language in ("한국어", "일본어"):
                script = root / "대본" / language / "같은이름.txt"
                script.parent.mkdir(parents=True)
                script.write_text("[제목]\n원래 제목\n[대본]\n본문", encoding="utf-8")
                scripts[language] = script
                package = root / "업로드" / language / "완성본"
                package.mkdir(parents=True)
                (package / "제작원본.json").write_text(json.dumps(dict(script=str(script.relative_to(root)))), encoding="utf-8")
                (package / "업로드정보.txt").write_text("previous", encoding="utf-8")
                packages[language] = package
            with patch.object(app, "BASE", str(root)), patch.object(app, "대본_폴더", str(root / "대본")):
                app.save_workspace(dict(script_file=str(scripts["일본어"]), kind="metadata", title="新しい題名", description="説明", tags="心理", sources="資料"))
            assets = scripts["일본어"].with_name("같은이름_자료")
            self.assertIn("新しい題名", (assets / "업로드정보.txt").read_text(encoding="utf-8"))
            self.assertEqual((packages["일본어"] / "제목.txt").read_text(encoding="utf-8"), "新しい題名")
            self.assertEqual((packages["한국어"] / "업로드정보.txt").read_text(encoding="utf-8"), "previous")


if __name__ == "__main__":
    unittest.main()
