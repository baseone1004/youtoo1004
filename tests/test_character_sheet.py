# -*- coding: utf-8 -*-
"""이야기형 인물 고정표: 기획.txt [등장인물] 을 영어 생김새 구절로 한 번 만들어 저장하고, 묶음마다 같은 표를 보낸다."""
import os
import tempfile
import unittest

import 대본선택 as app

PLAN = """[제목]
몰락한 아씨가 거둔 벙어리 머슴

[등장인물]

청송댁 / 여성·서른여섯 / 몰락한 양반가 안주인 / 마른 얼굴에 눈이 크고 광대뼈가 도드라짐 / 빛바랜 옥색 명주 저고리에 남색 치마 / 쪽진 머리에 은비녀 하나

바우 / 남성·서른두 살 / 머슴, 실은 왕실 검사 / 장신에 어깨가 넓고 이마에 흉터 / 거친 무명 저고리에 짚신 / 상투가 흐트러짐

[반전 6개]
- 하나
"""

SHEET = """청송댁 / 여성·36 / 몰락한 양반가 안주인 / clothing: faded jade silk jeogori, navy chima / hair: low chignon with one silver binyeo / face & build: thin face, large eyes, high cheekbones, small
바우 / 남성·32 / 머슴, 실은 왕실 검사 / clothing: rough hemp jeogori, straw sandals / hair: loose messy topknot / face & build: tall, broad shoulders, forehead scar
"""


class FakeAI:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0
    def ask(self, system, user):
        self.calls += 1; self.last = (system, user); return self.answer


class CharacterSheetTest(unittest.TestCase):
    def _workdir(self, tmp):
        d = os.path.join(tmp, "대본", "민담", "2026-01-01_몰락한 아씨"); os.makedirs(d)
        open(os.path.join(d, "기획.txt"), "w", encoding="utf-8").write(PLAN)
        open(os.path.join(d, "final.txt"), "w", encoding="utf-8").write("이야기 본문입니다.")
        return os.path.join(d, "final.txt")

    def test_made_once_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._workdir(tmp); ai = FakeAI(SHEET); logs = []
            sheet = app.character_sheet(script, ai, logs.append)
            self.assertIn("청송댁", sheet); self.assertIn("clothing:", sheet)
            self.assertIn("[등장인물]", ai.last[1]); self.assertIn("빛바랜 옥색 명주 저고리", ai.last[1])
            self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(script), "인물표.txt")))
            again = app.character_sheet(script, ai, logs.append)
            self.assertEqual(again, sheet); self.assertEqual(ai.calls, 1)              # 두 번째는 파일 재사용
            note = app.sheet_note(sheet)
            self.assertIn("[인물 고정표", note); self.assertIn("그대로 되풀이", note)

    def test_person_or_no_plan_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            person = os.path.join(tmp, "대본", "2026-01-01_정보형.txt"); os.makedirs(os.path.dirname(person))
            open(person, "w", encoding="utf-8").write("[대본]\n문장.")
            self.assertEqual(app.character_sheet(person, FakeAI(SHEET)), "")
            d = os.path.join(tmp, "대본", "민담", "기획없음"); os.makedirs(d)
            f = os.path.join(d, "final.txt"); open(f, "w", encoding="utf-8").write("본문")
            self.assertEqual(app.character_sheet(f, FakeAI(SHEET)), "")
            self.assertEqual(app.sheet_note(""), "")

    def test_unreadable_answer_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._workdir(tmp)
            self.assertEqual(app.character_sheet(script, FakeAI("알 수 없는 답"), lambda *_: None), "")
            self.assertFalse(os.path.isfile(os.path.join(os.path.dirname(script), "인물표.txt")))


if __name__ == "__main__":
    unittest.main()
