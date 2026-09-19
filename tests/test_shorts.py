# -*- coding: utf-8 -*-
"""쇼츠(세로 짧은 영상) 대본: 파일 이름에 [쇼츠], 목표 글자 수는 초 단위, 이미지 프롬프트는 9:16, 롱폼 최소 25분."""
import os
import tempfile
import unittest
from unittest.mock import patch

import 대본선택 as app
import 대본생성

SHORT_ANSWER = """[제목]
만나고 나면 지치는 사람의 정체
[설명글]
같이 있으면 힘이 빠지는 사람, 이유가 있습니다.
[태그]
#쇼츠 #심리 #관계 #피로 #정서전염 #인간관계 #마음 #휴식
[대본]
만나고 나면 이상하게 지치는 사람이 있습니다. 나쁜 사람은 아닌데 하루가 통째로 사라집니다. 심리학에서는 이것을 정서 전염이라고 부릅니다. 옆 사람의 기분이 나에게 옮아오는 겁니다. 오늘은 그 사람과 헤어진 뒤 십 분만 혼자 걸어 보세요. 그 십 분이 당신의 저녁을 돌려줍니다.
"""


class FakeAI:
    name, model = "fake", "fake"
    def __init__(self, cfg): self.asked = []
    def ask(self, system, user, **kw): self.asked.append((system, user)); return SHORT_ANSWER
    def cost_text(self): return "0"


class Job:
    def __init__(self): self.log = []; self.stage = ""; self.progress = 0; self.result = {}; self.cancel_requested = False
    def add(self, s): self.log.append(s)


class ShortsTest(unittest.TestCase):
    def test_shorts_script_and_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd(); os.chdir(tmp); os.makedirs("대본")
            try:
                with patch.object(app, "AI", FakeAI), patch.object(app.대본생성, "load_cfg", return_value={"분당_글자수": 270, "대본_글자수": 6750}), \
                     patch.object(대본생성, "web_search", return_value=[]), patch.object(app, "read_guideline", return_value="쇼츠 지침"), \
                     patch.object(app, "load_json", return_value=[]):
                    job = Job()
                    r = app.make_person_script(job, dict(title="만나고 나면 지치는 사람", format="shorts", shorts_seconds=60, mark_used=False, optimize=True))
                    self.assertIn("[쇼츠]", os.path.basename(r["file"]))
                    self.assertTrue(app.is_shorts(r["file"]))
                    self.assertTrue(any("쇼츠 목표 270자 (60초)" in l for l in job.log), job.log)
                    self.assertEqual(r["opt"], "")                                   # 쇼츠는 따로 최적화하지 않는다
                    with open(r["file"], encoding="utf-8") as f: text = f.read()
                    self.assertIn("[대본]", text); self.assertNotIn("===sum===", text)
                    self.assertTrue(text.split("[대본]", 1)[1].strip().startswith("만나고 나면 이상하게 지치는 사람이 있습니다."))
                    # 이미지 프롬프트: 9:16 로 바뀐 지침·화풍이 AI 로 간다
                    with patch.object(app, "read_guideline", return_value="이미지는 16:9 장면이다."), patch.object(app, "image_guideline_for", return_value="x.txt"):
                        job2 = Job()
                        class PromptAI(FakeAI):
                            def ask(self, system, user, **kw):
                                self.asked.append((system, user)); n = user.count("\n0") + 1
                                return "\n".join(f"==={i:03d}===\n유형: D\n대사: x\n감정: y\n행동: z\n프롬프트: STYLE scene {i}, 9:16 vertical aspect ratio" for i in range(1, 7))
                        with patch.object(app, "AI", PromptAI):
                            out = app.make_image_prompts(job2, dict(script_file=r["file"], style="파스텔", chunk=30))
                        self.assertTrue(any("쇼츠: 세로 9:16" in l for l in job2.log))
                        self.assertTrue(os.path.exists(out["file"]))
                    self.assertFalse(app.is_shorts(r"대본\2026-09-19_보통 대본.txt"))
            finally:
                os.chdir(cwd)

    def test_longform_minimum_is_25_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd(); os.chdir(tmp); os.makedirs("대본")
            try:
                seen = {}
                def fake_generate(ai, system, t, target, n_parts):
                    seen["target"] = target; return "[제목]\nx\n[대본]\n본문입니다.\n", "본문입니다."
                with patch.object(app, "AI", FakeAI), patch.object(app.대본생성, "load_cfg", return_value={"분당_글자수": 270, "대본_글자수": 5400}), \
                     patch.object(app.대본생성, "generate", side_effect=fake_generate), patch.object(app, "read_guideline", return_value="지침"), \
                     patch.object(app, "load_json", return_value=[]):
                    app.make_person_script(Job(), dict(title="주제", target=5400, mark_used=False, optimize=False))   # 20분 요청 → 25분으로 올린다
                self.assertEqual(seen["target"], 25 * 270)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
