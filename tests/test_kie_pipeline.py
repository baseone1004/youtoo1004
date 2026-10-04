import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class KiePipelineTest(unittest.TestCase):
    def pipeline_fixture(self, td):
        root = Path(td)
        script = root / "final.txt"
        script.write_text("[대본]\n이야기입니다.", encoding="utf-8")
        (root / "이미지프롬프트.txt").write_text("===001===\n유형: C\n프롬프트: medium shot, standing", encoding="utf-8")
        (root / "images").mkdir()
        return script

    def test_old_reference_setting_does_not_call_removed_automation(self):
        with tempfile.TemporaryDirectory() as td:
            script = self.pipeline_fixture(td)
            with patch.object(app, "assets_dir", return_value=td), \
                    patch.object(app, "channel_of", return_value="mindam"), \
                    patch.object(app.대본생성, "load_cfg", return_value={"레퍼런스_자동관리": True}), \
                    patch.object(app, "make_upload_package", return_value=td):
                result = app.make_pipeline(app.Job("pipeline"), {"script_file": str(script), "steps":
                    {"prompts": False, "tts": False, "images": False, "hook": 0, "thumbnail": False, "render": False}})
            self.assertEqual(result["upload_dir"], td)

    def test_insufficient_video_credit_keeps_static_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            script = self.pipeline_fixture(td)
            job = app.Job("pipeline")
            with patch.object(app, "assets_dir", return_value=td), \
                    patch.object(app, "aip", return_value={"kie_key_saved": True}), \
                    patch.object(app, "kie_usable", return_value=(False, "크레딧 부족")), \
                    patch.object(app, "run_hook_videos") as hook, \
                    patch.object(app, "make_upload_package", return_value=td):
                result = app.make_pipeline(job, {"script_file": str(script), "steps":
                    {"prompts": False, "tts": False, "images": False, "hook": 1, "thumbnail": False, "render": False}})
            hook.assert_not_called()
            self.assertEqual(result["upload_dir"], td)
            self.assertIn("정지 이미지", str(job.log))

    def test_motion_scene_selection_still_prefers_people_over_diagrams(self):
        with tempfile.TemporaryDirectory() as td:
            prompts = Path(td) / "prompts.txt"
            prompts.write_text("===001===\n유형: A\n프롬프트: empty diagram chart\n"
                               "===002===\n유형: C\n프롬프트: medium shot, standing, talking", encoding="utf-8")
            self.assertEqual(app.pick_hook_scenes(str(prompts), 1), [2])

    def test_channel_reference_is_sent_to_image_runner(self):
        import reference_images
        with tempfile.TemporaryDirectory() as td:
            prompts = Path(td) / "prompts.txt"
            prompts.write_text("1. a new scene", encoding="utf-8")
            calls = []
            def api(path, body=None):
                calls.append((path, body))
                if path == "/api/gen/status":
                    return {"status": "idle"} if len(calls) == 1 else {"status": "done", "done": [1], "failed": [], "total": 1}
                return {"ok": True}
            with patch.object(app, "aip_wait", return_value={"kie_key_saved": True}), patch.object(app, "aip", side_effect=api), patch.object(reference_images, "reference_options", return_value={"reference_image": "reference.png"}) as options:
                app.run_image_generation(app.Job("pipeline"), str(prompts), str(Path(td) / "images"), reference_slot="mindam")
                options.assert_called_once_with("mindam")
            self.assertEqual(next(body for path, body in calls if path == "/api/gen/start")["reference_image"], "reference.png")

    def test_pipeline_generation_uses_api_without_coordinates(self):
        with tempfile.TemporaryDirectory() as td:
            prompts = Path(td) / "prompts.txt"
            prompts.write_text("===001===\n프롬프트: a bear", encoding="utf-8")
            calls = []
            def api(path, body=None):
                calls.append((path, body))
                if path == "/api/gen/status":
                    return ({"status": "idle"} if len(calls) == 1 else
                            {"status": "done", "done": [1], "failed": [], "total": 1})
                return {"ok": True}
            job = app.Job("pipeline")
            with patch.object(app, "aip_wait", return_value={"kie_key_saved": True, "image_model": "z-image"}), \
                    patch.object(app, "aip", side_effect=api):
                result = app.run_image_generation(job, str(prompts), str(Path(td) / "images"))
            self.assertEqual(result["done"], [1])
            request = next(body for path, body in calls if path == "/api/gen/start")
            self.assertEqual(request["aspect_ratio"], "16:9")
            self.assertNotIn("prompt_xy", request)
            self.assertTrue(request["skip_existing"])


if __name__ == "__main__":
    unittest.main()
