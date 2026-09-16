# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import 대본생성


class FakeAI:
    def __init__(self):
        self.calls = 0

    def ask(self, _system, _user):
        self.calls += 1
        if self.calls == 1:
            return "[제목]\n테스트\n[대본]\n" + ("짧은 문장입니다. " * 8)
        return "보충 장면과 구체적인 사례입니다. " * 80


class ScriptLengthTest(unittest.TestCase):
    def test_short_person_script_is_automatically_extended(self):
        ai = FakeAI()
        topic = {"제목": "테스트", "태그": [], "다룰내용": []}
        with patch.object(대본생성, "web_search", return_value=[]):
            _full, body = 대본생성.generate(ai, "지침", topic, 1000, 1)
        self.assertGreaterEqual(len(body), 900)
        self.assertGreater(ai.calls, 1)


if __name__ == "__main__":
    unittest.main()
