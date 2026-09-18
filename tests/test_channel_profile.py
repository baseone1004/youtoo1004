# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import 채널_프로필 as P


class ChannelProfileTest(unittest.TestCase):
    def setUp(self):
        self._file = P.FILE
        self._tmp = tempfile.mkdtemp()
        P.FILE = os.path.join(self._tmp, "프로필.json")
        P._cache = None

    def tearDown(self):
        P.FILE = self._file
        P._cache = None

    def test_defaults_when_no_file(self):
        data = P.load(force=True)
        self.assertEqual(data["person"]["이름"], "심리해독소")
        self.assertEqual(data["mindam"]["유형"], "이야기형")
        self.assertEqual(data["mindam"]["썸네일"]["레이아웃"], "hanji_seal")

    def test_save_merges_and_keeps_type(self):
        saved = P.save("mindam", {"이름": "새 채널", "유형": "정보형", "검색어": "전설, 설화 ,", "마스코트": {"이름": "복이"}})
        self.assertEqual(saved["이름"], "새 채널")
        self.assertEqual(saved["유형"], "이야기형")            # 자리의 제작 방식은 바뀌지 않는다
        self.assertEqual(saved["검색어"], ["전설", "설화"])
        self.assertEqual(saved["마스코트"]["이름"], "복이")
        self.assertEqual(saved["마스코트"]["프롬프트"], "")     # 나머지 필드는 기본값 유지
        with open(P.FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["mindam"]["이름"], "새 채널")
        self.assertEqual(P.name("mindam"), "새 채널")
        self.assertEqual(P.name("person"), "심리해독소")

    def test_fill_placeholders(self):
        P.save("person", {"이름": "건강백서", "마스코트": {"이름": "콩", "프롬프트": "the mascot Kong"}})
        text = "채널 {{채널명}} / {{마스코트_이름}} / {{ 마스코트_프롬프트 }} / {{모르는것}}"
        self.assertEqual(P.fill(text, "person"), "채널 건강백서 / 콩 / the mascot Kong / {{모르는것}}")
        self.assertIn("'콩'", P.mascot_reference_note("person"))
        self.assertEqual(P.mascot_reference_note("mindam"), "")


if __name__ == "__main__":
    unittest.main()
