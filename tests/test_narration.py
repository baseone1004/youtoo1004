# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from 나레이션 import split_subtitle_text, synthesize


class SubtitleSplitTest(unittest.TestCase):
    def test_empty_text(self) -> None:
        self.assertEqual(split_subtitle_text("  \n  "), [])

    def test_preserves_text_and_respects_limit(self) -> None:
        source = "노인은 바닷가로 걸어갔고, 황금빛 물고기를 다시 만났습니다."
        chunks = split_subtitle_text(source, max_chars=12)
        self.assertEqual("".join(chunks).replace(" ", ""), source.replace(" ", ""))
        self.assertTrue(all(len(chunk.replace(" ", "")) <= 12 for chunk in chunks))

    def test_splits_a_long_unspaced_word(self) -> None:
        source = "가나다라마바사아자차카타파하"
        chunks = split_subtitle_text(source, max_chars=5)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(chunk) <= 5 for chunk in chunks))

    def test_word_boundaries_never_break_the_hard_limit(self) -> None:
        source = "가나다라마바사아 가나다라마바사아 가나다라마바사아"
        chunks = split_subtitle_text(source, max_chars=10)
        self.assertEqual("".join(chunks).replace(" ", ""), source.replace(" ", ""))
        self.assertTrue(all(len(chunk.replace(" ", "")) <= 10 for chunk in chunks))

    def test_rejects_invalid_limit(self) -> None:
        with self.assertRaises(ValueError):
            split_subtitle_text("자막", max_chars=0)

    def test_synthesize_maps_one_image_to_all_split_cues(self) -> None:
        sentence = "가" * 50
        with tempfile.TemporaryDirectory() as td:
            parts = Path(td) / "tts_parts"
            parts.mkdir()
            (parts / "0001.mp3").write_bytes(b"audio" * 200)
            (parts / "_silence.mp3").write_bytes(b"silence")
            with patch("나레이션.find_ffmpeg", side_effect=lambda name: name), \
                 patch("나레이션.probe_duration", return_value=5.0), \
                 patch("나레이션.subprocess.run"):
                result = synthesize([sentence], td, "key", "voice", log=lambda _line: None)

            srt = Path(result["srt"]).read_text(encoding="utf-8")
            flow = Path(result["flow"]).read_text(encoding="utf-8")
            self.assertEqual(result["sentences"], 3)
            self.assertIn("1: 1-3", flow)
            self.assertIn("00:00:00,000 -->", srt)
            self.assertIn("--> 00:00:05,000", srt)

    def test_mindam_groups_short_captions_into_two_lines(self) -> None:
        sentence = "가" * 50
        with tempfile.TemporaryDirectory() as td:
            parts = Path(td) / "tts_parts"; parts.mkdir()
            (parts / "0001.mp3").write_bytes(b"audio" * 200)
            (parts / "_silence.mp3").write_bytes(b"silence")
            with patch("나레이션.find_ffmpeg", side_effect=lambda name: name), \
                 patch("나레이션.probe_duration", return_value=5.0), \
                 patch("나레이션.subprocess.run"):
                result = synthesize([sentence], td, "key", "voice", log=lambda _line: None, subtitle_lines=2)
            self.assertEqual(result["sentences"], 2)
            blocks = Path(result["srt"]).read_text(encoding="utf-8").strip().split("\n\n")
            self.assertEqual(len(blocks[0].splitlines()[2:]), 2)


if __name__ == "__main__":
    unittest.main()
