"""편집기 업그레이드의 API 계약과 재적용을 네트워크 없이 검증한다."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

import kie_imagegen
from 편집프로그램_ZImage_연결 import apply

EDITOR = '''from fastapi.responses import FileResponse, JSONResponse

@app.get("/api/info")
def info():
    return {"config": {k: v for k, v in load_config().items() if k != "kie_api_key"},
            "kie_key_saved": bool(load_config().get("kie_api_key"))}

class GenStart(BaseModel):
    prompts_file: str
    output_dir: str
    prompt_xy: list[int]

class HookRequest(BaseModel):
    images_dir: str

@app.get("/imagegen")
def imagegen_page():
    return FileResponse(STATIC / "imagegen.html")

@app.post("/api/gen/capture")
def api_gen_capture():
    return {}

@app.post("/api/gen/start")
def api_gen_start(req: GenStart):
    s = imagegen.GenSettings(prompts_file=req.prompts_file, output_dir=req.output_dir)
    try:
        imagegen.runner.start(s)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    cfg = load_config(); cfg["gen"] = req.model_dump(); save_config(cfg)
    return {"ok": True}

class RegenRequest(GenStart):
    scene: int

@app.post("/api/gen/regen")
def api_gen_regen(req: RegenRequest):
    return {}

@app.post("/api/hook/start")
def api_hook_start(req: HookRequest):
    return {"ok": True}

# Legacy coordinate integration
from core import videogen

@app.get("/api/vgen/status")
def old_video_status():
    return {}

if __name__ == "__main__":
    pass
'''


class EditorIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "core").mkdir()
        (self.root / "static").mkdir()
        (self.root / "app.py").write_text(EDITOR, encoding="utf-8")
        (self.root / "core" / "imagegen.py").write_text("# legacy", encoding="utf-8")
        (self.root / "static" / "imagegen.html").write_text("coordinate UI", encoding="utf-8")
        apply(self.root)
        self.source = (self.root / "app.py").read_text(encoding="utf-8")
        self.config = {"kie_api_key": "test-key", "hook": {"api_key": "nested-key", "model": "veo"}}
        self.namespace = {"app": FastAPI(), "imagegen": kie_imagegen, "Path": Path,
                          "BaseModel": BaseModel, "HTTPException": HTTPException,
                          "load_config": lambda: dict(self.config), "save_config": self.config.update,
                          "os": __import__("os"), "STATIC": self.root / "static"}
        exec(compile(self.source, "editor.py", "exec"), self.namespace)
        self.client = TestClient(self.namespace["app"])
        self.prompts = self.root / "prompts.txt"
        self.prompts.write_text("===001===\n프롬프트: a bear", encoding="utf-8")
        self.body = {"prompts_file": str(self.prompts), "output_dir": str(self.root / "images")}

    def test_reapply_is_stable_and_old_routes_are_removed(self):
        self.assertFalse(apply(self.root))
        self.assertEqual(self.source, (self.root / "app.py").read_text(encoding="utf-8"))
        self.assertEqual(self.client.post("/api/gen/capture").status_code, 404)
        self.assertEqual(self.client.get("/api/vgen/status").status_code, 404)
        self.assertEqual((self.root / "core" / "imagegen.py").read_text(encoding="utf-8"),
                         Path(kie_imagegen.__file__).read_text(encoding="utf-8"))

    def test_start_uses_saved_key_and_requires_no_coordinates(self):
        with patch.object(kie_imagegen.runner, "start") as start:
            self.assertEqual(self.client.post("/api/gen/start", json=self.body).status_code, 200)
        settings = start.call_args.args[0]
        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.aspect_ratio, "16:9")
        self.assertNotIn("api_key", self.config["gen"])

    def test_public_info_hides_nested_credentials(self):
        result = self.client.get("/api/info")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("test-key", result.text)
        self.assertNotIn("nested-key", result.text)
        self.assertEqual(result.json()["config"]["hook"]["model"], "veo")

    def test_regeneration_uses_common_runner(self):
        with patch.object(kie_imagegen.runner, "start") as start:
            result = self.client.post("/api/gen/regen", json={**self.body, "scene": 1})
        self.assertEqual(result.status_code, 200)
        settings = start.call_args.args[0]
        self.assertEqual((settings.start_no, settings.end_no, settings.skip_existing), (1, 1, False))
        with patch.object(kie_imagegen.runner, "start") as start:
            self.assertEqual(self.client.post("/api/gen/regen", json={**self.body, "scene": 99}).status_code, 400)
            start.assert_not_called()

    def test_recovery_only_clears_uncertain_submission_after_confirmation(self):
        out = Path(self.body["output_dir"])
        out.mkdir()
        journal = out / ".kie-image-tasks.json"
        journal.write_text(json.dumps({"1": {"status": "submitting", "fingerprint": "abc"}}))
        request = {"output_dir": str(out), "scene": 1}
        self.assertEqual(self.client.post("/api/gen/recover", json=request).status_code, 400)
        self.assertEqual(self.client.post("/api/gen/recover", json={**request, "task_id": "task-123"}).status_code, 200)
        self.assertEqual(json.loads(journal.read_text())["1"]["task_id"], "task-123")
        self.assertEqual(self.client.post("/api/gen/recover", json={**request, "confirmed_not_created": True}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
