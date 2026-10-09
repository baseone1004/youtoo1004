import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
import kie_video as video


class VideoResumeTests(unittest.TestCase):
    def test_timeout_resumes_existing_task_without_new_charge(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/"005.mp4"
            with patch.object(video.Kie,"upload",return_value="https://example.test/image") as upload, \
                 patch.object(video.Kie,"create_task",return_value="task-five") as create, \
                 patch.object(video.Kie,"wait",side_effect=video.KieError("시간 초과")):
                with self.assertRaises(video.KieError):
                    video.image_to_video("mock", "005.jpg", "move", str(dest))
                self.assertEqual(create.call_count,1)
            def download(url,path):
                Path(path).write_bytes(b"video"); return Path(path)
            with patch.object(video.Kie,"upload") as upload, patch.object(video.Kie,"create_task") as create, \
                 patch.object(video.Kie,"wait",return_value=["https://example.test/video"]) as wait, \
                 patch.object(video.Kie,"download",side_effect=download):
                video.image_to_video("mock", "005.jpg", "move", str(dest))
                upload.assert_not_called(); create.assert_not_called()
                self.assertEqual(wait.call_args.args[0],"task-five")
            self.assertEqual(dest.read_bytes(),b"video")

    def test_lost_submission_response_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/"005.mp4"
            with patch.object(video.Kie,"upload",return_value="url"), patch.object(video.Kie,"create_task",side_effect=requests.Timeout):
                with self.assertRaises(video.KieError):
                    video.image_to_video("mock", "005.jpg", "move", str(dest))
            with patch.object(video.Kie,"create_task") as create:
                with self.assertRaisesRegex(video.KieError,"접수 응답"):
                    video.image_to_video("mock", "005.jpg", "move", str(dest))
                create.assert_not_called()

    def test_failed_provider_task_can_be_retried(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/"005.mp4"
            with patch.object(video.Kie,"upload",return_value="url"), patch.object(video.Kie,"create_task",return_value="failed"), \
                 patch.object(video.Kie,"wait",side_effect=video.KieError("생성 실패: rejected")):
                with self.assertRaises(video.KieError):
                    video.image_to_video("mock", "005.jpg", "move", str(dest))
            self.assertEqual(json.loads((dest.parent/".kie-video-tasks.json").read_text()),{})

    def test_cancel_does_not_submit(self):
        with tempfile.TemporaryDirectory() as td, patch.object(video.Kie,"create_task") as create:
            with self.assertRaisesRegex(video.KieError,"취소"):
                video.image_to_video("mock", "005.jpg", "move", str(Path(td)/"005.mp4"),cancel=lambda:True)
            create.assert_not_called()
