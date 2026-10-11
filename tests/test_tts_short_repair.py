import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import 나레이션 as tts
from test_japanese_narration import alignment


class ShortRepairTest(unittest.TestCase):
    def setUp(self):
        self.ffmpeg = tts.find_ffmpeg()
        self.ffprobe = tts.find_ffmpeg('ffprobe')
        self.client = tts.Inworld('mock', 'jp', 'inworld-tts-2', language='ja-JP', timestamps=True)
        self.calls = []

    def generate(self, text, path):
        self.calls.append(text)
        self.assertTrue(self.client.repair_mode)
        subprocess.run([self.ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=24000', '-t', str(len(text) * .1 + .2),
                        '-c:a', 'libmp3lame', path], check=True)
        Path(path + '.alignment.json').write_text(json.dumps(alignment(text)), encoding='utf-8')

    def repair(self, text, path, cancel=None):
        tts.repair_speech_part(self.client, text, path, self.ffmpeg, self.ffprobe, cancel, lambda _: None)

    def test_short_chunks_join_exact_text_and_cached_repair_never_resubmits(self):
        text = '返信のタイミングを考えます。 ' + 'あ' * 45 + '、相手を思います。'
        with tempfile.TemporaryDirectory() as td, patch.object(self.client, 'synth', self.generate):
            p = str(Path(td, 'group.mp3'))
            self.repair(text, p)
            self.assertGreater(len(self.calls), 2)
            self.assertTrue(all(len(c) <= 40 for c in self.calls))
            result = json.loads(Path(p + '.alignment.json').read_text(encoding='utf-8'))
            times = tts.character_timings(text, result)
            self.assertTrue(all(b[0] >= a[1] for a, b in zip(times, times[1:])))
            self.assertLessEqual(times[-1][1], tts.probe_duration(self.ffprobe, p))
            count = len(self.calls)
            self.repair(text, p)
            self.assertEqual(len(self.calls), count)

    def test_lost_response_is_journaled_and_does_not_overwrite_original(self):
        with tempfile.TemporaryDirectory() as td, patch.object(self.client, 'synth', side_effect=requests.ReadTimeout()) as synth:
            p = Path(td, 'group.mp3'); p.write_bytes(b'original')
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, '음성 복구 중단'):
                    self.repair('返信を考えます。', str(p))
            self.assertEqual(synth.call_count, 1)
            self.assertEqual(p.read_bytes(), b'original')

    def test_bad_new_timestamps_are_not_paid_for_again(self):
        def bad(text, path):
            self.generate(text, path)
            data = alignment(text)
            data['characterAlignment']['characterEndTimeSeconds'] = [0] * len(text)
            data['characterAlignment']['characterStartTimeSeconds'] = [0] * len(text)
            Path(path + '.alignment.json').write_text(json.dumps(data), encoding='utf-8')
        with tempfile.TemporaryDirectory() as td, patch.object(self.client, 'synth', bad):
            p = str(Path(td, 'group.mp3'))
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, '음성 복구 중단'):
                    self.repair('返信を考えます。次の文。', p)
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(Path(p).exists())

    def test_cancel_after_success_resumes_only_remaining_chunks(self):
        with tempfile.TemporaryDirectory() as td, patch.object(self.client, 'synth', self.generate):
            p = str(Path(td, 'group.mp3'))
            with self.assertRaisesRegex(RuntimeError, '취소됨'):
                self.repair('最初の文。 次の文。', p, lambda: bool(self.calls))
            self.repair('最初の文。 次の文。', p)
            self.assertEqual(self.calls, ['最初の文。', '次の文。'])

    def test_repair_request_changes_normalization_without_changing_voice(self):
        self.client.repair_mode = True
        with patch.object(tts.requests, 'post', side_effect=requests.ReadTimeout()) as post:
            with self.assertRaises(RuntimeError):
                self.client.synth('日本語。', 'unused.mp3')
            body = post.call_args.kwargs['json']
            self.assertEqual(body['applyTextNormalization'], 'OFF')
            self.assertEqual(body['voiceId'], 'jp')
            self.assertEqual(body['timestampType'], 'CHARACTER')


if __name__ == '__main__':
    unittest.main()
