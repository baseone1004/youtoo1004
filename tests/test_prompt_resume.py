# -*- coding: utf-8 -*-
"""이미지 프롬프트: 되풀이된 질문(지침)을 답변으로 받지 않고, 만들다 만 파일은 모자란 범위만 다시 만든다."""
import os
import tempfile
import unittest
from unittest.mock import patch

import 대본선택 as app
import 공통_api

GUIDE_ECHO = """심리해독소 | 정보형 롱폼 | 이미지 프롬프트 지침

[프로그램과 맞추는 규칙 — 가장 먼저]
1. 번호를 001로 되돌리지 않는다.

[좋은 예]
===014===
유형: D
대사: 좋은 사람인데도 만나고 돌아오는 길이 이상하게 무겁습니다.
프롬프트: [화풍 문구], medium shot of a Korean woman

==============================
[요청]
[이번 범위] 061 ~ 090 (총 30개 장면)

[이번 범위 원문]
061. 새로운 길은 낯설고 위험해 보입니다.
"""


def block(no, text="scene"):
    return f"==={no:03d}===\n유형: D\n대사: 문장 {no}입니다.\n감정: 담담\n행동: 걷는다\n프롬프트: STYLE, {text} {no}, 16:9 aspect ratio\n"


class PromptBlocksTest(unittest.TestCase):
    def test_echoed_guideline_is_ignored(self):
        text = block(59) + block(60) + "\n" + GUIDE_ECHO + "\n" + GUIDE_ECHO
        got = app.prompt_blocks(text)
        self.assertEqual(sorted(got), [59, 60])
        self.assertNotIn("[프로그램과 맞추는 규칙", got[60])
        self.assertTrue(got[60].endswith("16:9 aspect ratio"))

    def test_is_echo(self):
        sent = "지침 첫머리 " * 30 + "\n\n" + "=" * 30 + "\n[요청]\n무엇을 해 주세요"
        self.assertTrue(공통_api.is_echo(sent, sent))
        self.assertTrue(공통_api.is_echo("앞 설명\n" + "=" * 30 + "\n[요청]\n...", sent))
        self.assertFalse(공통_api.is_echo(block(1), sent))
        self.assertFalse(공통_api.is_echo("", sent))


class ResumeTest(unittest.TestCase):
    def test_partial_file_only_asks_missing_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "t.txt")
            with open(script, "w", encoding="utf-8") as f:
                f.write("[대본]\n" + " ".join(f"문장 {i}입니다." for i in range(1, 7)))
            prompts = os.path.join(tmp, "t_이미지프롬프트.txt")
            with open(prompts, "w", encoding="utf-8") as f:                 # 1~3은 있고, 뒤에는 되풀이된 지침만
                f.write(block(1) + block(2) + block(3) + "\n" + GUIDE_ECHO)
            answers = [GUIDE_ECHO, block(4) + block(5) + block(6)]         # 첫 답은 되풀이 → 두 번째에 정상
            asked = []

            class FakeAI:
                name, model = "fake", "fake"
                def __init__(self, cfg): pass
                def ask(self, system, user):
                    asked.append(user); return answers.pop(0)
                def cost_text(self): return "0"

            class Job:
                log = []
                def add(self, s): self.log.append(s)

            self.assertFalse(app.prompts_complete(prompts, script))
            with patch.object(app, "AI", FakeAI), patch.object(app.대본생성, "load_cfg", return_value={}), \
                 patch.object(app, "read_guideline", return_value="지침"), patch.object(app, "image_guideline_for", return_value="x.txt"):
                r = app.make_image_prompts(Job(), dict(script_file=script, chunk=3, style="실사"))
            self.assertEqual(len(asked), 2)                                  # 1~3 묶음은 묻지 않고 4~6만 두 번
            self.assertIn("004 ~ 006", asked[0])
            self.assertTrue(app.prompts_complete(prompts, script))
            with open(r["file"], encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(sorted(app.prompt_blocks(text)), [1, 2, 3, 4, 5, 6])
            self.assertNotIn("[이번 범위 원문]", text)

    def test_all_echo_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "t.txt")
            with open(script, "w", encoding="utf-8") as f:
                f.write("[대본]\n문장 하나입니다. 문장 둘입니다.")

            class FakeAI:
                name, model = "fake", "fake"
                def __init__(self, cfg): pass
                def ask(self, system, user): return GUIDE_ECHO
                def cost_text(self): return "0"

            class Job:
                def add(self, s): pass

            with patch.object(app, "AI", FakeAI), patch.object(app.대본생성, "load_cfg", return_value={}), \
                 patch.object(app, "read_guideline", return_value="지침"), patch.object(app, "image_guideline_for", return_value="x.txt"):
                with self.assertRaises(SystemExit):
                    app.make_image_prompts(Job(), dict(script_file=script, chunk=5, style="실사"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "t_이미지프롬프트.txt")))



class TruncatedReplyTest(unittest.TestCase):
    def test_truncated_reply_shrinks_chunk_and_continues(self):
        """답이 잘려 절반만 오면 같은 크기로 세 번 되묻지 않고, 받은 만큼 저장한 뒤 묶음을 줄여 이어서 묻는다."""
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "t.txt")
            with open(script, "w", encoding="utf-8") as f:
                f.write("[대본]\n" + " ".join(f"문장 {i}입니다." for i in range(1, 21)))
            asked = []

            class FakeAI:                         # 묻는 범위의 앞 4개만 돌려준다 (출력이 잘린 딥시크 웹 흉내)
                name, model = "fake", "fake"
                def __init__(self, cfg): pass
                def ask(self, system, user):
                    import re as _re
                    a, b = map(int, _re.search(r"\[이번 범위\] (\d+) ~ (\d+)", user).groups())
                    asked.append((a, b)); return "".join(block(i) for i in range(a, min(a + 4, b + 1)))
                def cost_text(self): return "0"

            class Job:
                log = []
                def add(self, s): self.log.append(s)

            with patch.object(app, "AI", FakeAI), patch.object(app.대본생성, "load_cfg", return_value={}), \
                 patch.object(app, "read_guideline", return_value="지침"), patch.object(app, "image_guideline_for", return_value="x.txt"), \
                 patch.object(app, "프롬프트_최소_묶음", 4):
                r = app.make_image_prompts(Job(), dict(script_file=script, chunk=10, style="실사"))
            self.assertEqual(r["scenes"], 20)
            self.assertTrue(app.prompts_complete(r["file"], script))
            self.assertEqual(asked[0], (1, 10))                              # 처음 10개를 물어 4개만 받음
            self.assertEqual(asked[1][0], 5)                                 # 되묻지 않고 5번부터 이어서, 묶음은 줄어든다
            self.assertLessEqual(asked[1][1] - asked[1][0] + 1, 4)
            self.assertEqual(len(asked), 5)                                  # 4개씩 다섯 번이면 끝 (같은 범위 되묻기 없음)


if __name__ == "__main__":
    unittest.main()
