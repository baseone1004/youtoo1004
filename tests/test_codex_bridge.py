import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
import codex_bridge as bridge
import 공통_api
import kie_imagegen as gen


class CodexBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / '대본' / '_상태' / '.codex-requests'

    def start(self, kind, payload, cancel=None):
        self.output, self.errors = [], []
        def work():
            try:
                self.output.append(bridge.wait_result(self.folder, kind, payload, cancel or (lambda: False), interval=.01, timeout=5))
            except Exception as e:
                self.errors.append(e)
        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=6))
        return thread

    def request(self):
        for _ in range(200):
            rows = bridge.pending(self.root)
            if rows:
                return rows[0][1]
            time.sleep(.01)
        self.fail('No pending request')

    def test_text_result_resume_and_no_paid_fallback(self):
        thread = self.start('text', {'system':'日本語', 'user':'write'})
        request = self.request()
        source = self.root / 'result.txt'
        source.write_text('日本語の大本。', encoding='utf-8')
        bridge.complete(self.root, request['id'], request['token'], source)
        thread.join(2)
        self.assertEqual(self.output, ['日本語の大本。'])
        self.assertEqual(bridge.wait_result(self.folder,'text',request['payload']),self.output[0])
        ai = 공통_api.AI({'AI':'codex','API_키_gemini':'private-key'})
        with patch.object(bridge,'wait_result',side_effect=RuntimeError('Codex limit')), patch.object(ai,'_make_fallback') as fallback:
            with self.assertRaisesRegex(RuntimeError,'Codex limit'):
                ai.ask('system','user')
        fallback.assert_not_called()

    def test_cancel_resume_rejects_old_token(self):
        cancel = threading.Event()
        thread = self.start('text', {'user':'same'}, cancel.is_set)
        old = self.request()
        cancel.set(); thread.join(2)
        self.assertIn('중단', str(self.errors[0]))
        thread = self.start('text', {'user':'same'})
        current = self.request()
        source=self.root/'result.txt'; source.write_text('ok',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'이전 요청'):
            bridge.complete(self.root,old['id'],old['token'],source)
        bridge.complete(self.root,current['id'],current['token'],source)
        thread.join(2)
        self.assertEqual(self.output,['ok'])

    def test_failed_tool_and_reset_reject_registration(self):
        thread=self.start('text',{'user':'failure'})
        request=self.request()
        bridge.complete(self.root,request['id'],request['token'],failed=True)
        thread.join(2)
        self.assertIn('한도',str(self.errors[0]))
        with self.assertRaises(ValueError):
            bridge.complete(self.root,request['id'],request['token'],failed=True)

    def test_program_restart_invalidates_waiting_result(self):
        thread=self.start('text',{'user':'restart'})
        request=self.request()
        bridge.cancel_all(self.root)
        thread.join(2)
        self.assertTrue(self.errors)
        with self.assertRaises(ValueError):
            bridge.complete(self.root,request['id'],request['token'],failed=True)

    def test_image_validation(self):
        thread=self.start('image',{'prompt':'mascot','reference_image':'ref.png'})
        request=self.request()
        source=self.root/'image.png'
        source.write_text('not an image')
        with self.assertRaises(OSError):
            bridge.complete(self.root,request['id'],request['token'],source)
        self.assertEqual(len(bridge.pending(self.root)),1)
        Image.new('RGB',(128,72),'blue').save(source)
        bridge.complete(self.root,request['id'],request['token'],source)
        thread.join(2)
        self.assertTrue(self.output[0].is_file())

    def test_image_runner_uses_codex_with_reference_no_api_and_resumes(self):
        out=self.root/'대본'/'work'/'images'; out.mkdir(parents=True)
        prompts=out/'prompts.txt';prompts.write_text('===001===\n프롬프트: a new cafe',encoding='utf-8')
        reference=out/'reference.png';Image.new('RGB',(128,72),'red').save(reference)
        result=self.root/'result.png';Image.new('RGB',(128,72),'blue').save(result)
        settings=gen.GenSettings(str(prompts),str(out),reference_model='codex',reference_image=str(reference))
        runner=gen.Runner();runner.settings=settings;runner.state.status='running'
        with patch.object(bridge,'wait_result',return_value=result) as wait, patch.object(gen.requests,'request') as api, patch.object(gen.requests,'post') as upload:
            runner._run(settings)
            self.assertEqual(runner.state.status,'done')
            self.assertEqual(wait.call_args.args[2]['reference_image'],str(reference))
            runner._run(settings)
            self.assertEqual(wait.call_count,1)
            api.assert_not_called(); upload.assert_not_called()
        self.assertEqual(runner.state.estimated_submitted_credits,0)
        settings.skip_existing=False
        with patch.object(bridge,'wait_result',return_value=result) as wait:
            runner._run(settings)
            self.assertEqual(wait.call_count,1)


if __name__ == '__main__':
    unittest.main()
