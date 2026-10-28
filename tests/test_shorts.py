import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shorts


class ShortsTest(unittest.TestCase):
    def _inputs(self, root, seconds, step=3):
        video = root / "source.mp4"
        video.write_bytes(b"video")
        subtitles = root / "source.srt"
        cues = []
        for index, start in enumerate(range(0, seconds, step), 1):
            end = min(start + step, seconds)
            cues.append(f"{index}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n짧은 이야기 자막 {index}\n")
        subtitles.write_text("\n".join(cues), encoding="utf-8")
        return video, subtitles

    def test_two_distinct_shorts_are_required_and_returned(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video, subtitles = self._inputs(root, 240)

            def fake_render(_ffmpeg, _video, output, _start, _end, _cancel=None):
                Path(output).write_bytes(b"x" * 60_000)

            with patch.object(shorts, "_render", side_effect=fake_render):
                result = shorts.create(video, subtitles, root / "쇼츠", "테스트 이야기", "ko",
                                       "ffmpeg", count=2)
            self.assertEqual(len(result), 2)
            self.assertEqual(len({item["path"] for item in result}), 2)
            self.assertFalse(result[0]["start_seconds"] < result[1]["end_seconds"] and
                             result[0]["end_seconds"] > result[1]["start_seconds"])
            self.assertTrue(all(25 <= item["duration_seconds"] <= 62 for item in result))

    def test_short_source_does_not_silently_claim_two_shorts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video, subtitles = self._inputs(root, 30)
            with self.assertRaisesRegex(ValueError, "쇼츠 2편"):
                shorts.create(video, subtitles, root / "쇼츠", "짧은 이야기", "ko",
                              "ffmpeg", count=2)

    def test_render_keeps_subtitles_in_frame_and_uses_cpu_friendly_filters(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "clip.rendering-123.mp4"
            output.write_bytes(b"x" * 60_000)
            (root / "clip.srt").write_text(
                "1\n00:00:00,000 --> 00:00:03,000\n자막 위치 검사\n", encoding="utf-8"
            )
            process = unittest.mock.Mock()
            process.poll.return_value = 0
            process.returncode = 0
            process.communicate.return_value = (None, b"")
            with patch.object(shorts.subprocess, "Popen", return_value=process) as popen:
                shorts._render("ffmpeg", "source.mp4", str(output), 0, 30)

            command = popen.call_args.args[0]
            graph = command[command.index("-filter_complex") + 1]
            self.assertIn("scale=270:480", graph)
            self.assertIn("scale=1080:1920", graph)
            self.assertIn("FontSize=10", graph)
            self.assertIn("MarginV=42", graph)
            self.assertIn("WrapStyle=0", graph)
            self.assertEqual(command[command.index("-preset") + 1], "veryfast")


if __name__ == "__main__":
    unittest.main()
