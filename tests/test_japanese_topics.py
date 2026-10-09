import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import 주제_추천 as topics


class JapaneseTopicsTest(unittest.TestCase):
    def test_japanese_and_korean_recommendations_use_separate_storage(self):
        with tempfile.TemporaryDirectory() as td:
            korean=str(Path(td)/"ko.json")
            japanese=str(Path(td)/"jp.json")
            with patch.object(topics,"storage_file",return_value=korean):
                topics.save({"person":[{"제목":"한국어 추천"}]})
            with patch.object(topics,"storage_file",return_value=japanese):
                self.assertEqual(topics.load()["person"],[])
                topics.save({"person":[{"제목":"日本語のテーマ"}]})
            with patch.object(topics,"storage_file",return_value=korean):
                self.assertEqual(topics.load()["person"][0]["제목"],"한국어 추천")
            with patch.object(topics,"storage_file",return_value=japanese):
                self.assertEqual(topics.load()["person"][0]["제목"],"日本語のテーマ")

    def test_language_selects_japanese_file(self):
        import 채널_프로필 as profiles
        with patch.object(profiles,"language_code",return_value="ja"):
            self.assertEqual(topics.storage_file(),"추천_추가_일본.json")
        with patch.object(profiles,"language_code",return_value="ko"):
            self.assertEqual(topics.storage_file(),topics.FILE)
