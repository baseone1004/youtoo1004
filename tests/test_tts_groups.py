# -*- coding: utf-8 -*-
"""나레이션 묶어 읽기: 문장 몇 개를 한 번에 읽혀도 자막·플로우는 문장마다 나오고, 경계는 음성의 쉼에서 되찾는다."""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import 나레이션
import 대본선택 as app


def tone_with_gaps(ffmpeg, out, n_sent, sec=0.8, gap=0.4):
    """문장 n개를 읽은 것처럼: 소리 sec초 → 무음 gap초 반복."""
    parts = []
    for k in range(n_sent):
        parts.append(f"sine=frequency=440:duration={sec}")
        if k < n_sent - 1:
            parts.append(f"anullsrc=r=44100:cl=mono:d={gap}")
    filt = ";".join(f"{p}[a{i}]" for i, p in enumerate(parts)) + ";" + "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-filter_complex", filt, "-map", "[out]", "-c:a", "libmp3lame", out], check=True)


class GroupedTTSTest(unittest.TestCase):
    def test_grouped_reading_keeps_per_sentence_cues(self):
        try:
            ffmpeg = 나레이션.find_ffmpeg("ffmpeg")
        except FileNotFoundError:
            self.skipTest("ffmpeg 없음")
        sents = ["첫째 문장입니다.", "둘째 문장입니다.", "셋째 문장입니다.", "넷째 문장입니다.", "다섯째 문장입니다."]
        calls = []

        class FakeTTS:
            def __init__(self, *a, **k): pass
            def synth(self, text, out_path, retries=3):
                calls.append(text); tone_with_gaps(ffmpeg, out_path, text.count("."))

        with tempfile.TemporaryDirectory() as tmp, patch.object(나레이션, "Inworld", FakeTTS):
            r = 나레이션.synthesize(sents, tmp, "k", "v", groups=[(1, 3), (4, 5)], log=lambda *_: None)
            self.assertEqual(len(calls), 2)                                   # 두 번만 읽힌다
            self.assertIn("첫째 문장입니다. 둘째 문장입니다. 셋째 문장입니다.", calls[0])
            flow = [ln for ln in open(r["flow"], encoding="utf-8") if ":" in ln and not ln.startswith("#")]
            self.assertEqual([ln.split(":")[0].strip() for ln in flow], ["1", "2", "3", "4", "5"])   # 문장마다 플로우
            srt = open(r["srt"], encoding="utf-8").read()
            self.assertEqual(srt.count("-->"), 5)
            first_end = srt.split("\n")[1].split("-->")[1].strip()          # 첫 문장은 약 0.8초 소리 + 0.4초 쉼의 가운데(≈1.0초)에서 끝난다
            self.assertTrue(first_end.startswith("00:00:00,9") or first_end.startswith("00:00:01,0"), first_end)
            r2 = 나레이션.synthesize(sents, tmp, "k", "v", groups=[(1, 3), (4, 5)], log=lambda *_: None)
            self.assertEqual(len(calls), 2)                                   # 같은 문장이면 다시 읽히지 않는다
            self.assertTrue(os.path.isfile(r2["mp3"]))

    def test_groups_for_person_and_mindam(self):
        with tempfile.TemporaryDirectory() as tmp:
            person = os.path.join(tmp, "대본", "t.txt"); os.makedirs(os.path.dirname(person)); open(person, "w", encoding="utf-8").write("x")
            sents = [f"{i}번째 문장은 스무 자 남짓입니다." for i in range(1, 8)]
            g = app.tts_groups(person, sents)
            self.assertEqual(g[0], (1, 3)); self.assertEqual(g[-1][1], 7)
            self.assertEqual([a for a, _ in g], [1, 4, 7])
            d = os.path.join(tmp, "대본", "민담", "e"); os.makedirs(d); m = os.path.join(d, "final.txt"); open(m, "w", encoding="utf-8").write("x")
            gm = app.tts_groups(m, sents)
            self.assertEqual(gm, [(a, b) for a, b, _ in app.scene_units(m, sents)])


if __name__ == "__main__":
    unittest.main()
