import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import korean_numbers as numbers
import 나레이션 as narration


class KoreanNumbersTest(unittest.TestCase):
    def test_counters_and_sino_units(self):
        pairs = {
            "5가지를": "다섯 가지를", "3명": "세 명", "20살": "스무 살", "21개": "스물한 개",
            "3시간": "세 시간", "3시 30분": "세 시 삼십 분", "100명": "백 명",
            "1번째": "첫 번째", "2번째": "두 번째", "30%": "삼십 퍼센트",
            "2.5배": "이 점 오 배", "1,234원": "천이백삼십사 원", "-2.5도": "마이너스 이 점 오 도",
            "3~5명": "세 명에서 다섯 명", "오 가지를": "다섯 가지를", "삼 명": "세 명",
            "2kg": "이 킬로그램", "0.05": "영 점 영오",
        }
        for original, expected in pairs.items():
            with self.subTest(original=original):
                self.assertEqual(numbers.normalize(original), expected)

    def test_dates_and_clock(self):
        self.assertEqual(numbers.normalize("2026-10-05"), "이천이십육 년 시월 오 일")
        self.assertEqual(numbers.normalize("3:15"), "세 시 십오 분")
        self.assertEqual(narration.split_sentences("2.5배입니다. 30.5퍼센트입니다."), ["2.5배입니다.", "30.5퍼센트입니다."])

    def test_preserves_urls_identifiers_and_correct_korean(self):
        text = "다섯 가지를 일명 마음 공부라고 합니다. https://example.com/2026/5 voice-8471"
        self.assertEqual(numbers.normalize(text), text)

    def test_normalization_is_idempotent(self):
        text = "5가지, 3명, 2.5배, 2026-10-05, 3~5명"
        result = numbers.normalize(text)
        self.assertEqual(numbers.normalize(result), result)

    def test_tts_and_subtitles_use_same_readable_text_and_force_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as td:
            part_dir = Path(td, "tts_parts"); part_dir.mkdir()
            part = part_dir / "0001.mp3"
            part.write_bytes(b"audio" * 200)
            part.with_suffix(".txt").write_text("다섯 가지를 봅니다.", encoding="utf-8")
            (part_dir / "_silence.mp3").write_bytes(b"silence")
            with patch.object(narration, "find_ffmpeg", side_effect=lambda x: x), patch.object(narration, "probe_duration", return_value=5.0), patch.object(narration.subprocess, "run"), patch.object(narration.Inworld, "synth") as synth:
                result = narration.synthesize(["5가지를 봅니다."], td, "mock", "voice", normalize_numbers=True, log=lambda _: None)
                synth.assert_not_called()
                self.assertIn("다섯 가지를 봅니다.", Path(result["srt"]).read_text(encoding="utf-8"))
                narration.synthesize(["5가지를 봅니다."], td, "mock", "voice", normalize_numbers=True, force=True, log=lambda _: None)
                synth.assert_called_once_with("다섯 가지를 봅니다.", str(part))


if __name__ == "__main__":
    unittest.main()
