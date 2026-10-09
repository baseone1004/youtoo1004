import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import production_duration as policy
import 대본선택 as app


class DurationPolicyTests(unittest.TestCase):
    def test_language_and_speed_budgets_include_margin(self):
        self.assertEqual(policy.script_target(6750, 270, "ko"), (8100, 25))
        self.assertEqual(policy.script_target(6750, 270, "ja"), (12000, 25))
        self.assertEqual(policy.script_target(6750, 270, "ja", 1.2), (14400, 25))
        self.assertEqual(policy.script_target(100, 270, "ko", 0.8), (8100, 25))

    def test_duration_boundary_and_invalid_metadata(self):
        policy.require_duration(1500)
        for seconds in (1499.99, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                policy.require_duration(seconds)
        self.assertEqual(policy.additional_chars(6000, 1200), 2100)

    def run_extension(self, durations, cancelled=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        script = Path(temp.name) / "script.txt"
        original = "[제목]\n주제\n[대본]\n" + "기존 본문입니다. " * 100 + "\n===sum===\nthumbnail"
        script.write_text(original, encoding="utf-8")
        result = dict(narration="audio", srt="srt", flow="flow")
        job = app.Job("pipeline")
        ai = Mock()
        def answer(*args):
            if cancelled:
                job.cancel_requested = True
            return f"さらに具体的な例{ai.ask.call_count}を見ていきましょう。" * 60
        ai.ask.side_effect = answer
        patches = [patch.object(app, "minimum_video_seconds", return_value=1500),
                   patch.object(app, "assets_dir", return_value=temp.name),
                   patch.object(app.나레이션, "find_ffmpeg", return_value="probe"),
                   patch.object(app.나레이션, "probe_duration", side_effect=durations),
                   patch.object(app.대본생성, "load_cfg", return_value={}),
                   patch.object(app, "read_guideline", return_value="Japanese"),
                   patch.object(app, "AI", return_value=ai),
                   patch.object(app, "make_tts", return_value=dict(mp3="new audio", srt="new srt", flow="new flow"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return job, script, result, ai, original

    def test_short_narration_is_extended_and_retimed(self):
        job, script, result, ai, original = self.run_extension([1200, 1520])
        self.assertTrue(app.ensure_longform_narration(job, str(script), result))
        self.assertEqual(result["duration"], 1520)
        self.assertEqual(result["srt"], "new srt")
        text = script.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original.split("===sum===")[0].rstrip()))
        self.assertTrue(text.endswith("===sum===\nthumbnail"))
        self.assertEqual(ai.ask.call_count, 1)

    def test_sufficient_narration_does_not_generate_again(self):
        job, script, result, ai, original = self.run_extension([1500])
        self.assertFalse(app.ensure_longform_narration(job, str(script), result))
        ai.ask.assert_not_called()
        self.assertEqual(script.read_text(encoding="utf-8"), original)

    def test_insufficient_after_two_additions_stops(self):
        job, script, result, ai, _ = self.run_extension([1000, 1100, 1200])
        with self.assertRaisesRegex(ValueError, "최소 25분"):
            app.ensure_longform_narration(job, str(script), result)
        self.assertEqual(ai.ask.call_count, 2)

    def test_cancel_during_extension_does_not_change_script_or_request_tts(self):
        job, script, result, ai, original = self.run_extension([1200], cancelled=True)
        with self.assertRaisesRegex(RuntimeError, "중단"):
            app.ensure_longform_narration(job, str(script), result)
        app.make_tts.assert_not_called()
        self.assertEqual(script.read_text(encoding="utf-8"), original)

    def test_short_render_does_not_replace_previous_video(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "최종.mp4"
            output.write_bytes(b"previous video")
            def render(job, srt, flow, images, audio, staged, ken_burns):
                Path(staged).write_bytes(b"new video")
                return {}
            with patch.object(app, "_run_render", side_effect=render), patch.object(app, "verify_final_video", return_value=1499):
                with self.assertRaises(ValueError):
                    app.run_render(app.Job("pipeline"), "srt", "flow", "images", "audio", str(output), minimum_duration=1500)
            self.assertEqual(output.read_bytes(), b"previous video")

    def test_narration_and_length_are_finalized_before_image_prompts(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "script.txt"
            script.write_text("[대본]\n본문", encoding="utf-8")
            events = []
            def tts(*args):
                events.append("tts")
                return dict(mp3="audio", srt="srt", flow="flow", duration=1200)
            def extend(*args):
                events.append("duration")
                script.write_text("[대본]\n보충된 본문", encoding="utf-8")
                return True
            def prompts(*args):
                events.append("prompts")
                self.assertIn("보충된", script.read_text(encoding="utf-8"))
                return dict(file=str(Path(td) / "prompts.txt"))
            with patch.object(app, "assets_dir", return_value=td), \
                 patch.object(app, "make_tts", side_effect=tts), \
                 patch.object(app, "ensure_longform_narration", side_effect=extend), \
                 patch.object(app, "make_image_prompts", side_effect=prompts), \
                 patch.object(app, "make_upload_package", return_value=td):
                app.make_pipeline(app.Job("pipeline"), dict(script_file=str(script), steps=dict(
                    tts=True, prompts=False, images=False, hook=0, thumbnail=False, render=False)))
            self.assertEqual(events, ["tts", "duration", "prompts"])


if __name__ == "__main__":
    unittest.main()
