import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import 나레이션 as tts
import 대본선택 as app
from test_japanese_narration import alignment


class TtsFailuresTest(unittest.TestCase):
    def test_billable_post_is_not_repeated_after_lost_response(self):
        for error in (requests.exceptions.SSLError(), requests.exceptions.ReadTimeout()):
            with self.subTest(error=type(error).__name__), patch.object(tts.requests, 'post', side_effect=error) as post:
                with self.assertRaisesRegex(RuntimeError, '인월드 통신 오류'):
                    tts.Inworld('fake', 'fake').synth('test', 'unused.mp3', retries=3)
                self.assertEqual(post.call_count, 1)

    def test_collapsed_japanese_tail_is_rejected(self):
        data = alignment('今日は晴れです。')
        info = data['characterAlignment']
        info['characterStartTimeSeconds'][3:] = [.3] * 5
        info['characterEndTimeSeconds'][3:] = [.3] * 5
        with self.assertRaisesRegex(ValueError, '0초'):
            tts.character_timings('今日は晴れです。', data)

    def test_failure_stops_remaining_requests(self):
        with tempfile.TemporaryDirectory() as td, patch.object(tts, 'find_ffmpeg', return_value='mock'), \
                patch.object(tts, '동시_요청', 1), patch.object(tts.Inworld, 'synth', side_effect=RuntimeError('인월드 통신 오류')) as synth:
            with self.assertRaises(SystemExit):
                tts.synthesize(['하나.', '둘.', '셋.'], td, 'fake', 'voice', log=lambda _: None)
            self.assertEqual(synth.call_count, 1)

    def test_bad_cached_alignment_blocks_missing_audio_before_spending(self):
        with tempfile.TemporaryDirectory() as td, patch.object(tts, 'find_ffmpeg', return_value='mock'), \
                patch.object(tts.Inworld, 'synth') as synth:
            parts = Path(td, 'tts_parts'); parts.mkdir()
            audio = parts / '0002.mp3'
            audio.write_bytes(b'a' * 600)
            audio.with_suffix('.txt').write_text('今日は晴れです。', encoding='utf-8')
            identity = hashlib.sha256(json.dumps(dict(voice='voice', model='inworld-tts-2', speed=1.0,
                temperature=None, language='ja-JP', timestamps=True), sort_keys=True).encode()).hexdigest()
            Path(str(audio) + '.identity').write_text(identity, encoding='utf-8')
            Path(str(audio) + '.alignment.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '2~2'):
                tts.synthesize(['前の文。', '今日は晴れです。'], td, 'fake', 'voice', model='inworld-tts-2', language='ja-JP', timestamps=True, log=lambda _: None)
            synth.assert_not_called()
            self.assertEqual(audio.read_bytes(), b'a' * 600)
            with self.assertRaisesRegex(RuntimeError, '기존 음성과 달라'):
                tts.synthesize(['前の文。', '今日は晴れです。'], td, 'fake', 'changed-voice', model='inworld-tts-2',
                    language='ja-JP', timestamps=True, repair_alignment=True, log=lambda _: None)
            synth.assert_not_called()

    def test_sync_and_provider_errors_pause_batch(self):
        for message in ('자막 싱크 검사 실패', '인월드 통신 오류', 'KIE 크레딧이 부족합니다.'):
            self.assertTrue(app.is_shared_failure(message), message)
        self.assertFalse(app.is_shared_failure('개별 장면 실패'))


if __name__ == '__main__':
    unittest.main()
