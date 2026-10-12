import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import wave
import local_voice
import video_export

def recording(path, seconds=4):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b'\0\0' * (24000 * seconds))

class LocalVoiceTest(unittest.TestCase):
    def test_cancellation_stops_windows_python_process_tree(self):
        with tempfile.TemporaryDirectory() as td:
            proc=Mock(); proc.poll.return_value=None; proc.pid=12345
            voice={'revision':'r','audio_path':'reference.wav','text':'녹음'}
            with patch.object(local_voice,'profile',return_value=voice), patch.object(local_voice,'status',return_value={'runtime_ready':True}), \
                    patch.object(local_voice.subprocess,'Popen',return_value=proc), patch.object(local_voice.subprocess,'run') as run, patch.object(local_voice.os,'name','nt'):
                with self.assertRaisesRegex(RuntimeError,'중단'):
                    local_voice.synthesize(td,['안녕하세요.'],td,'ko',log=lambda _:None,cancel=lambda:True)
            run.assert_called_once_with(['taskkill','/PID','12345','/T','/F'],capture_output=True,timeout=15)
            proc.wait.assert_called_once_with(timeout=15)
    def test_resume_reuses_existing_audio_without_importing_model(self):
        import local_voice_worker
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td); wav=folder/'cached.wav';recording(wav,1)
            request=folder/'request.json';request.write_text(json.dumps({'cues':[{'file':str(wav)}]}),encoding='utf-8')
            original=wav.read_bytes()
            with patch('sys.argv',['worker',str(request),str(folder/'progress.json')]):
                local_voice_worker.main()  # No torch/qwen packages are needed for cached audio.
            self.assertEqual(wav.read_bytes(),original)
    def test_profiles_are_separate_and_revision_changes_with_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'sample.wav';recording(p)
            encoded=base64.b64encode(p.read_bytes()).decode()
            local_voice.save_profile(td,'ko',encoded,'한국어 녹음입니다.')
            self.assertIsNone(local_voice.profile(td,'ja'))
            first=local_voice.profile(td,'ko')['revision']
            local_voice.save_profile(td,'ja',encoded,'日本語の録音です。')
            local_voice.save_profile(td,'ko',encoded,'수정한 녹음입니다.')
            self.assertNotEqual(first,local_voice.profile(td,'ko')['revision'])
            self.assertEqual(local_voice.profile(td,'ja')['text'],'日本語の録音です。')
    def test_missing_profile_and_runtime_do_not_call_cloud(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,'녹음'):
                local_voice.synthesize(td,['test'],td,'ko')
            with patch.object(local_voice,'profile',return_value={'revision':'a'}):
                with self.assertRaisesRegex(ValueError,'설치'):
                    local_voice.synthesize(td,['test'],td,'ko')
    def test_measured_caption_bounds_and_sentence_flow(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td);parts=out/'로컬_음성조각';parts.mkdir()
            a,b=parts/'a.wav',parts/'b.wav';recording(a,1);recording(b,2)
            r=local_voice.assemble([dict(sentence=1,text='こんにちは。',file=str(a)),dict(sentence=1,text='お元気ですか。',file=str(b))],out,log=lambda _:None)
            self.assertTrue(r['sync']['ok']);self.assertEqual(r['sync']['method'],'audio-cue')
            self.assertIn('00:00:01,000 --> 00:00:03,000',Path(r['srt']).read_text(encoding='utf-8'))
            self.assertEqual(Path(r['flow']).read_text(encoding='utf-8').strip(),'1: 1-2')
            with self.assertRaisesRegex(RuntimeError,'취소'):
                local_voice.assemble([dict(sentence=1,text='x',file=str(a))],out,cancel=lambda:True)
    def test_desktop_export_keeps_channel_folders_and_registers_only_exports(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);assets=root/'대본'/'작업_자료';assets.mkdir(parents=True)
            source=assets/'최종.mp4';source.write_bytes(b'video')
            with patch.object(video_export,'desktop',return_value=root/'Desktop'):
                output=video_export.export(source,root/'대본'/'작업.txt','일본 채널',assets)
                self.assertEqual(Path(output).parent,root/'Desktop'/'영상'/'일본 채널')
                self.assertTrue(video_export.is_registered(root,output))
                self.assertFalse(video_export.is_registered(root,root/'Desktop'/'unrelated.mp4'))
                self.assertEqual(video_export.saved(assets),output)

if __name__=='__main__':unittest.main()
