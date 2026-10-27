import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class ResumeAudioTest(unittest.TestCase):
    def run_pipeline(self, complete=False, sync_ok=False, fail=False, language='ja'):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            script = root / 'script.txt'
            script.write_text('[대본]\n今日は晴れです。', encoding='utf-8')
            if complete:
                for name in ('나레이션.mp3', '나레이션.srt', '플로우.txt'):
                    (root / name).write_text('cached', encoding='utf-8')
            stack.enter_context(patch.object(app, 'assets_dir', return_value=td))
            stack.enter_context(patch.object(app.대본생성, 'load_cfg', return_value={}))
            stack.enter_context(patch.object(app, 'narration_matches', return_value=True))
            stack.enter_context(patch.object(app, 'narration_cache_matches', return_value=True))
            stack.enter_context(patch.object(app.채널_프로필, 'language_code', return_value=language))
            stack.enter_context(patch.object(app.나레이션, 'find_ffmpeg', return_value='mock'))
            stack.enter_context(patch.object(app.나레이션, 'probe_duration', return_value=1500))
            sync = stack.enter_context(patch.object(app.나레이션, 'verify_subtitle_sync', return_value={'ok':sync_ok}))
            stack.enter_context(patch.object(app, 'ensure_longform_narration', return_value=False))
            synth = stack.enter_context(patch.object(app, 'make_tts',
                side_effect=RuntimeError('자막 싱크 오류') if fail else None,
                return_value=dict(mp3='new audio',srt='new srt',flow='new flow',duration=1500)))
            prompts = stack.enter_context(patch.object(app, 'make_image_prompts', return_value={'file':str(root/'prompts.txt')}))
            stack.enter_context(patch.object(app, 'make_upload_package', return_value=td))
            req = dict(script_file=str(script),channel='person',reuse_prompts=True,
                steps=dict(tts=True,prompts=True,images=False,hook=0,thumbnail=False,render=False))
            if fail:
                with self.assertRaisesRegex(RuntimeError, '자막 싱크 오류'):
                    app.make_pipeline(app.Job('pipeline'),req)
                prompts.assert_not_called()
            else:
                app.make_pipeline(app.Job('pipeline'),req)
                prompts.assert_called_once()
            if complete and (sync_ok or language == 'ko'):
                synth.assert_not_called()
            else:
                synth.assert_called_once()
                self.assertEqual(synth.call_args.args[1]['repair_alignment'], language == 'ja')
                self.assertFalse(synth.call_args.args[1].get('force'))
            if language == 'ko':
                sync.assert_not_called()

    def test_resume_repairs_partial_audio(self):
        self.run_pipeline()

    def test_bad_complete_subtitles_trigger_partial_repair(self):
        self.run_pipeline(complete=True)

    def test_valid_complete_audio_is_reused_without_generation(self):
        self.run_pipeline(complete=True,sync_ok=True)

    def test_failed_repair_does_not_advance_to_image_prompts(self):
        self.run_pipeline(fail=True)

    def test_korean_complete_audio_keeps_existing_reuse_behavior(self):
        self.run_pipeline(complete=True,language='ko')

    def test_korean_partial_audio_keeps_existing_resume_behavior(self):
        self.run_pipeline(language='ko')
