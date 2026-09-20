# -*- coding: utf-8 -*-
"""이야기형: 따옴표 대사는 한 문장, 자막에 따옴표 없음 · 화풍 바꾸기 · 나이·화면 가득 고정."""
import os
import tempfile
import unittest

import 나레이션
import 대본선택 as app


class QuoteSplitTest(unittest.TestCase):
    def test_quoted_dialogue_is_one_sentence_without_quotes(self):
        got = 나레이션.split_sentences('"그 칼자국, 네 놈이 낸 것이지. 감히 건드려?" 비 오는 밤이었습니다. 그는 ‘머슴’이라 불렸습니다.')
        self.assertEqual(got, ["그 칼자국, 네 놈이 낸 것이지. 감히 건드려.", "비 오는 밤이었습니다.", "그는 머슴이라 불렸습니다."])
        self.assertFalse(any(ch in " ".join(got) for ch in '"“”‘’'))

    def test_plain_text_unchanged(self):
        self.assertEqual(나레이션.split_sentences("하나입니다. 둘입니다."), ["하나입니다.", "둘입니다."])


class StyleTest(unittest.TestCase):
    def test_folktale_lock_has_age_and_frame_and_alias(self):
        lock = app.image_style_lock("민화", "mindam")                      # 예전 이름 → 수채 사극
        self.assertIn("수채 사극", lock); self.assertIn("AGE LOCK", lock); self.assertIn("edge to edge", lock)
        self.assertNotIn("AGE LOCK", app.image_style_lock("2D 일러스트", "person"))
        self.assertNotIn("aged sepia", lock)

    def test_restyle_prompts_replaces_old_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "이미지프롬프트.txt")
            old = "STRICT STYLE LOCK: every image must be 민화 style. Korean minhwa folk painting, aged sepia ink-wash tone, consistent channel look. Medium shot of a man, 16:9 aspect ratio"
            open(p, "w", encoding="utf-8").write("===001===\n유형: C\n대사: 문장.\n프롬프트: " + old + "\n\n===002===\n유형: A\n대사: 둘.\n프롬프트: a village, 16:9 aspect ratio\n")
            n = app.restyle_prompts(p, "수채 사극", "mindam")
            text = open(p, encoding="utf-8").read()
            self.assertEqual(n, 2)
            self.assertNotIn("minhwa", text)
            self.assertIn("Medium shot of a man, 16:9 aspect ratio", text)
            self.assertEqual(text.count("STRICT STYLE LOCK"), 2)
            self.assertEqual(app.restyle_prompts(p, "수채 사극", "mindam"), 0)          # 이미 맞춰져 있으면 손대지 않는다



class LegacySplitTest(unittest.TestCase):
    def test_episode_made_with_old_split_keeps_old_numbering(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "대본", "민담", "2026-01-01_옛 편"); os.makedirs(d)
            f = os.path.join(d, "final.txt")
            open(f, "w", encoding="utf-8").write('"하나. 둘. 셋." 넷입니다.')            # 예전 방식 3문장, 새 방식 2문장
            body = app.fix_script_sentences(app.script_body(open(f, encoding="utf-8").read()))
            old, new = 나레이션.split_sentences(body, quotes=False), 나레이션.split_sentences(body, quotes=True)
            self.assertNotEqual(len(old), len(new))
            open(os.path.join(d, "플로우.txt"), "w", encoding="utf-8").write("".join(f"{i}: {i}\n" for i in range(1, len(old) + 1)))
            self.assertTrue(app.legacy_split(f))                                   # 예전 방식으로 나레이션을 만든 편
            self.assertTrue(os.path.isfile(os.path.join(d, app.예전_나누기_표시)))
            self.assertEqual(app.split_sentences(body, f), old)
            self.assertTrue(app.narration_matches(os.path.join(d, "플로우.txt"), f))
            g = os.path.join(tmp, "대본", "민담", "2026-01-02_새 편"); os.makedirs(g)
            f2 = os.path.join(g, "final.txt"); open(f2, "w", encoding="utf-8").write('"하나. 둘. 셋." 넷입니다.')
            self.assertFalse(app.legacy_split(f2))                                 # 새 편은 새 방식
            self.assertEqual(app.split_sentences(body, f2), new)


if __name__ == "__main__":
    unittest.main()
