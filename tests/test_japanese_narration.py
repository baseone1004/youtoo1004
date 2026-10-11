import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import 나레이션 as narration
import 대본선택 as app


def alignment(text):
    return {"characterAlignment": {"characters": list(text),
        "characterStartTimeSeconds": [i * .1 for i in range(len(text))],
        "characterEndTimeSeconds": [(i + 1) * .1 for i in range(len(text))]}}


class JapaneseNarrationTest(unittest.TestCase):
    def test_production_tts_uses_japanese_voice_and_records_settings(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td, "script.txt"); script.write_text("[대본]\n今日は晴れです。", encoding="utf-8")
            cfg = {"인월드_API_키": "mock-key", "인월드_목소리_일본": "jp", "인월드_모델": "inworld-tts-1.5-max"}
            job = SimpleNamespace(add=lambda _: None, cancel_requested=False)
            with patch.object(app.대본생성, "load_cfg", return_value=cfg), patch.object(app.채널_프로필, "language_code", return_value="ja"), patch.object(narration, "synthesize", return_value={"sync": {"ok": True}}) as synth:
                app.make_tts(job, dict(script_file=str(script), out_dir=td, channel="person"))
                self.assertEqual(synth.call_args.args[3], "jp")
                self.assertEqual(synth.call_args.args[4], "inworld-tts-2")
                self.assertEqual(synth.call_args.kwargs["language"], "ja-JP")
                self.assertTrue(synth.call_args.kwargs["timestamps"])
                self.assertTrue(app.narration_cache_matches(td, cfg, "person"))

    def test_sync_check_rejects_paths_outside_script_list(self):
        with patch.object(app, "script_files", return_value=[]):
            with self.assertRaises(ValueError):
                app.check_narration_sync("outside.txt")

    def test_request_uses_japanese_stable_model_and_character_timestamps(self):
        response = SimpleNamespace(status_code=200, json=lambda: {
            "audioContent": base64.b64encode(b"mock audio").decode(), "timestampInfo": alignment("晴れ。")})
        with tempfile.TemporaryDirectory() as td, patch("나레이션.requests.post", return_value=response) as post:
            path = str(Path(td, "voice.mp3"))
            narration.Inworld("private", "japanese-voice", "inworld-tts-2", language="ja-JP", timestamps=True).synth("晴れ。", path)
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["language"], "ja-JP")
            self.assertEqual(payload["timestampType"], "CHARACTER")
            self.assertEqual(payload["deliveryMode"], "STABLE")
            self.assertEqual(json.loads(Path(path + ".alignment.json").read_text(encoding="utf-8")), alignment("晴れ。"))

    def test_japanese_does_not_fall_back_to_korean_voice(self):
        with patch.object(app.채널_프로필, "language_code", return_value="ja"):
            cfg = {"인월드_목소리": "korean"}
            self.assertEqual(app.voice_for(cfg, "person")[0], "")
            cfg.update(인월드_목소리_일본="japanese", 인월드_속도_일본=.9)
            self.assertEqual(app.narration_settings(cfg, "person"), dict(voice="japanese", speed=.9, model="inworld-tts-2", language="ja-JP", timestamps=True))

    def test_voice_change_invalidates_complete_audio_cache(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app.채널_프로필, "language_code", return_value="ja"):
            cfg = {"인월드_목소리_일본": "one"}
            self.assertFalse(app.narration_cache_matches(td, cfg, "person"))
            Path(td, "나레이션_설정.json").write_text(json.dumps(app.narration_settings(cfg, "person")), encoding="utf-8")
            self.assertTrue(app.narration_cache_matches(td, cfg, "person"))
            cfg["인월드_목소리_일본"] = "two"
            self.assertFalse(app.narration_cache_matches(td, cfg, "person"))

    def test_rejects_wrong_text_and_reversed_times(self):
        with self.assertRaises(ValueError):
            narration.character_timings("雨。", alignment("晴れ。"))
        bad = alignment("晴れ。")
        bad["characterAlignment"]["characterStartTimeSeconds"][2] = -1
        with self.assertRaises(ValueError):
            narration.character_timings("晴れ。", bad)

    def test_sync_detects_overlap_and_audio_overrun(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "captions.srt")
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\n晴れ。\n\n2\n00:00:00,500 --> 00:00:03,000\n散歩。", encoding="utf-8")
            result = narration.verify_subtitle_sync(path, 2.0, "character")
            self.assertFalse(result["ok"])
            self.assertEqual(len(result["issues"]), 2)

    def test_real_ffmpeg_concat_preserves_character_sync_and_reuses_parts(self):
        ffmpeg = narration.find_ffmpeg()
        calls = []
        def fake_synth(_self, text, path):
            calls.append(text)
            subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "2", "-c:a", "libmp3lame", path], check=True)
            Path(path + ".alignment.json").write_text(json.dumps(alignment(text)), encoding="utf-8")
        sentences = ["今日は晴れです。", "散歩に行きます。", "花がきれいです。"]
        with tempfile.TemporaryDirectory() as td, patch.object(narration.Inworld, "synth", fake_synth):
            kwargs = dict(language="ja-JP", timestamps=True, model="inworld-tts-2", groups=[(1, 2), (3, 3)], log=lambda _: None)
            result = narration.synthesize(sentences, td, "mock-key", "jp", **kwargs)
            self.assertTrue(result["sync"]["ok"])
            self.assertEqual(result["sync"]["cues"], 3)
            srt = Path(result["srt"]).read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:00,800", srt)
            self.assertIn("00:00:02,280 --> 00:00:03,080", srt)
            self.assertEqual(len(calls), 2)
            narration.synthesize(sentences, td, "mock-key", "jp", **kwargs)
            self.assertEqual(len(calls), 2)
            # A corrupt cached part blocks a free retry before any POST, then
            # an explicit repair replaces only that group and keeps numbering.
            bad_path = Path(td, 'tts_parts', '0003.mp3.alignment.json')
            bad_path.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '자막 싱크 오류'):
                narration.synthesize(sentences, td, "mock-key", "jp", **kwargs)
            self.assertEqual(len(calls), 2)
            result = narration.synthesize(sentences, td, "mock-key", "jp", repair_alignment=True, **kwargs)
            self.assertTrue(result['sync']['ok'])
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[-1], sentences[-1])
            narration.synthesize(sentences, td, "mock-key", "jp-new", **kwargs)
            self.assertEqual(len(calls), 5)


if __name__ == "__main__":
    unittest.main()
