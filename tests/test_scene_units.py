# -*- coding: utf-8 -*-
"""그림 단위: 정보형은 문장 하나 = 장면 하나, 이야기형(민담)은 문장을 약 110자씩 묶는다."""
import os
import tempfile
import unittest

import 대본선택 as app


class SceneUnitsTest(unittest.TestCase):
    def _script(self, tmp, sub, n):
        d = os.path.join(tmp, "대본", sub) if sub else os.path.join(tmp, "대본")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "t.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("[대본]\n" + " ".join(f"{i}번째 문장은 스물다섯 자 남짓으로 적당히 길게 씁니다." for i in range(1, n + 1)))
        return p

    def test_person_one_sentence_one_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            units = app.scene_units(self._script(tmp, "", 12))
            self.assertEqual([(i, i) for i in range(1, 13)], [(a, b) for a, b, _ in units])

    def test_mindam_groups_sentences(self):
        with tempfile.TemporaryDirectory() as tmp:
            units = app.scene_units(self._script(tmp, "민담", 40))
            self.assertLess(len(units), 40)
            self.assertEqual(units[0][0], 1)
            self.assertEqual(units[-1][1], 40)
            for (a, b, t), (a2, _b2, _t2) in zip(units, units[1:]):
                self.assertEqual(b + 1, a2)                      # 빈틈 없이 이어진다
                self.assertGreaterEqual(len(t), app.민담_장면_글자수)
            self.assertTrue(all("문장" in t for _a, _b, t in units))


if __name__ == "__main__":
    unittest.main()
