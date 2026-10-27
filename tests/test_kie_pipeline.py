import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class KiePipelineTest(unittest.TestCase):
    def test_rendered_long_videos_always_require_two_shorts(self):
        self.assertEqual(app.pipeline_shorts_count({"render": True, "shorts": 0}), 2)
        self.assertEqual(app.pipeline_shorts_count({"render": True, "shorts": 1}), 2)
        self.assertEqual(app.pipeline_shorts_count({"render": False, "shorts": 0}), 0)

    def test_rendered_pipeline_is_not_complete_until_two_shorts_are_packaged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, package = root / "generated", root / "업로드"
            source.mkdir(); package.mkdir()
            source_files = [source / f"short{i}.mp4" for i in (1, 2)]
            package_shorts = package / "쇼츠"; package_shorts.mkdir()
            for file in source_files:
                file.write_bytes(b"video")
                (package_shorts / file.name).write_bytes(b"video")
            generated = [{"path": str(file)} for file in source_files]
            app.verify_shorts_package(generated, package)
            (package_shorts / "short2.mp4").unlink()
            with self.assertRaisesRegex(RuntimeError, "롱폼당 필요한 쇼츠 2편"):
                app.verify_shorts_package(generated, package)

    def test_short_retry_replaces_active_package_and_archives_previous_clips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "업로드"
            active = package / "쇼츠"; active.mkdir(parents=True)
            (active / "old.mp4").write_bytes(b"old")
            source = root / "생성"; source.mkdir()
            generated = []
            for index in (1, 2):
                video = source / f"short{index}.mp4"; video.write_bytes(b"new")
                generated.append({"path": str(video)})

            app.sync_package_shorts(package, generated)
            self.assertEqual(sorted(p.name for p in (package / "쇼츠").glob("*.mp4")), ["short1.mp4", "short2.mp4"])
            archived = list((package / "이전_쇼츠").glob("*/old.mp4"))
            self.assertEqual(len(archived), 1)

            app.sync_package_shorts(package, [])
            self.assertFalse((package / "쇼츠").exists())
            self.assertEqual(len(list((package / "이전_쇼츠").glob("*/old.mp4"))), 1)

    def test_full_render_does_not_complete_when_two_shorts_fail(self):
        import shorts
        import video_export
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "script.txt"; script.write_text("[대본]\n본문입니다.", encoding="utf-8")
            (root / "나레이션.mp3").write_bytes(b"audio")
            (root / "나레이션.srt").write_text("1\n00:00:00,000 --> 00:00:03,000\n본문입니다.\n", encoding="utf-8")
            (root / "플로우.txt").write_text("1: 1\n", encoding="utf-8")
            (root / "script_이미지프롬프트.txt").write_text("===001===\n프롬프트: 장면", encoding="utf-8")
            (root / "images").mkdir()
            (root / "images" / "001.jpg").write_bytes(b"image")
            (root / "최종.mp4").write_bytes(b"video")
            package = root / "업로드"; package.mkdir()
            job = app.Job("pipeline")
            with patch.object(app, "ensure_script_language"), \
                    patch.object(app.대본생성, "load_cfg", return_value={}), \
                    patch.object(app, "assets_dir", return_value=str(root)), \
                    patch.object(app, "channel_of", return_value="person"), \
                    patch.object(app, "narration_matches", return_value=True), \
                    patch.object(app, "narration_cache_matches", return_value=True), \
                    patch.object(app.나레이션, "probe_duration", return_value=600), \
                    patch.object(app, "ensure_longform_narration", return_value=False), \
                    patch.object(app, "refresh_image_flow"), \
                    patch.object(app, "final_video_is_current", return_value=True), \
                    patch.object(app, "minimum_video_seconds", return_value=False), \
                    patch.object(video_export, "export", return_value="desktop.mp4"), \
                    patch.object(app, "AI", side_effect=RuntimeError("no AI needed")), \
                    patch.object(shorts, "create", side_effect=ValueError("테스트 실패")) as create, \
                    patch.object(app, "make_upload_package", return_value=str(package)):
                with self.assertRaisesRegex(RuntimeError, "쇼츠 2편"):
                    app.make_pipeline(job, {"script_file": str(script), "reuse_prompts": True, "steps":
                        {"tts": True, "prompts": False, "images": False, "hook": 0,
                         "thumbnail": False, "render": True, "shorts": 0}})
            self.assertEqual(create.call_args.kwargs["count"], 2)
            self.assertEqual(job.result["upload_dir"], str(package))
            self.assertEqual(job.result["shorts_error"], "테스트 실패")

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

    def test_automatic_video_uses_first_five_in_script_order(self):
        with tempfile.TemporaryDirectory() as td:
            prompts = Path(td) / "prompts.txt"
            prompts.write_text("\n".join(f"==={i:03d}===\n유형: A\n프롬프트: scene {i}" for i in range(10,0,-1)),encoding="utf-8")
            self.assertEqual(app.first_hook_scenes(str(prompts),5),list(range(1,6)))
            self.assertEqual(app.first_hook_scenes(str(prompts),0),[])
            prompts.write_text("===001===\n프롬프트: one\n===002===\n프롬프트: two",encoding="utf-8")
            self.assertEqual(app.first_hook_scenes(str(prompts),5),[1,2])

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
