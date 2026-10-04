"""별도 편집기에 저장소의 Z-Image 실행기와 API 연결을 설치한다."""
from pathlib import Path

REQUEST = '''class GenStart(BaseModel):
    prompts_file: str
    output_dir: str
    start_no: int = 1
    end_no: int = 0
    skip_existing: bool = True
    style_prefix: str = ""
    aspect_ratio: str = "16:9"


'''
SETTINGS = '''    cfg = load_config()
    s = imagegen.GenSettings(
        prompts_file=req.prompts_file, output_dir=req.output_dir,
        api_key=cfg.get("kie_api_key") or os.environ.get("KIE_API_KEY", ""),
        start_no=req.start_no, end_no=req.end_no, skip_existing=req.skip_existing,
        style_prefix=req.style_prefix, aspect_ratio=req.aspect_ratio,
    )
'''
REGEN = '''@app.post("/api/gen/regen")
def api_gen_regen(req: RegenRequest):
    st = imagegen.runner.state
    if req.scene not in {sc.no for sc in imagegen.parse_prompts(req.prompts_file)}:
        raise HTTPException(400, "해당 장면의 프롬프트가 없습니다.")
    if st.status in ("running", "paused"):
        running = imagegen.runner.settings
        if (Path(running.output_dir).resolve() != Path(req.output_dir).resolve()
                or Path(running.prompts_file).resolve() != Path(req.prompts_file).resolve()):
            raise HTTPException(400, "다른 작업의 이미지를 만드는 중입니다.")
        if not imagegen.runner.queue_regen(req.scene):
            raise HTTPException(400, "이미 다시 만들기가 예약되어 있습니다.")
        return {"ok": True, "queued": True, "moved": []}
    cfg = load_config()
    s = imagegen.GenSettings(
        prompts_file=req.prompts_file, output_dir=req.output_dir,
        api_key=cfg.get("kie_api_key") or os.environ.get("KIE_API_KEY", ""),
        start_no=req.scene, end_no=req.scene, skip_existing=False,
        style_prefix=req.style_prefix, aspect_ratio=req.aspect_ratio,
    )
    try:
        imagegen.runner.start(s)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    cfg["gen"] = req.model_dump(exclude={"scene"})
    save_config(cfg)
    return {"ok": True, "moved": []}


'''

RECOVERY = '''class RecoverImageTask(BaseModel):
    output_dir: str
    scene: int
    task_id: str = ""
    confirmed_not_created: bool = False


@app.post("/api/gen/recover")
def recover_image_task(req: RecoverImageTask):
    try:
        imagegen.runner.recover_submission(req.output_dir, req.scene, req.task_id, req.confirmed_not_created)
    except (OSError, ValueError):
        raise HTTPException(400, "작업 기록을 읽지 못했습니다. 이미지 폴더를 확인하세요.")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


'''


def apply(editor_dir):
    editor = Path(editor_dir)
    target = editor / "app.py"
    source = target.read_text(encoding="utf-8")
    updated = source
    marker = "# KIE_ZIMAGE_INTEGRATION_V1"
    if marker not in updated:
        start = updated.index("class GenStart(BaseModel):")
        end = updated.index("class HookRequest(BaseModel):", start)
        updated = updated[:start] + REQUEST + updated[end:]
        start = updated.index('@app.post("/api/gen/capture")')
        end = updated.index('@app.post("/api/gen/start")', start)
        updated = updated[:start] + updated[end:]
        start = updated.index("    s = imagegen.GenSettings(", updated.index("def api_gen_start("))
        end = updated.index("    try:", start)
        updated = updated[:start] + SETTINGS + updated[end:]
        start = updated.index('@app.post("/api/gen/regen")')
        end = updated.index('@app.post("/api/hook/start")', start)
        updated = updated[:start] + REGEN + RECOVERY + updated[end:]
        updated = updated.replace('return FileResponse(STATIC / "imagegen.html")',
                                  'return RedirectResponse("http://127.0.0.1:8766/", status_code=302)')
        updated = updated.replace("from fastapi.responses import FileResponse, JSONResponse",
                                  "from fastapi.responses import FileResponse, JSONResponse, RedirectResponse")
        # Remove integrations installed by older versions, including all reference/coordinate routes.
        if "from core import videogen" in updated:
            start = updated.rfind("\n#", 0, updated.index("from core import videogen"))
            end = updated.index('if __name__ == "__main__":', start)
            updated = updated[:start] + "\n\n" + updated[end:]
        updated = updated.replace('{k: v for k, v in load_config().items() if k != "kie_api_key"}',
                                  'public_config(load_config())')
        updated = updated.replace('@app.get("/api/info")\ndef info():', '''def public_config(value):
    if isinstance(value, dict):
        return {k: public_config(v) for k, v in value.items()
                if not any(x in k.lower() for x in ("key", "token", "secret"))}
    if isinstance(value, list):
        return [public_config(v) for v in value]
    return value


@app.get("/api/info")
def info():''')
        updated = updated.replace('"kie_key_saved": bool(', '"image_model": "z-image",\n        "kie_key_saved": bool(')
        updated += "\n" + marker + "\n"
    compile(updated, str(target), "exec")
    runner = Path(__file__).with_name("kie_imagegen.py").read_text(encoding="utf-8")
    gen = editor / "core" / "imagegen.py"
    changed = updated != source or gen.read_text(encoding="utf-8") != runner
    target.write_text(updated, encoding="utf-8")
    gen.write_text(runner, encoding="utf-8")
    legacy_video = editor / "core" / "videogen.py"
    if legacy_video.exists():
        legacy_video.write_text('"""화면 자동 클릭 영상 변환은 제거되었습니다. 영상은 KIE API를 사용합니다."""\n', encoding="utf-8")
    # Old direct editor page must not expose coordinate automation after upgrading.
    legacy_page = editor / "static" / "imagegen.html"
    if legacy_page.exists():
        legacy_page.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" '
                               'content="0;url=http://127.0.0.1:8766/"><a href="http://127.0.0.1:8766/">영상 만들기 열기</a>', encoding="utf-8")
    return changed
