"""Serialize expensive final renders across otherwise independent editors."""
from pathlib import Path
import shutil


def apply(editor):
    editor = Path(editor)
    shutil.copy2(Path(__file__).with_name("process_lock.py"), editor / "core" / "process_lock.py")
    path = editor / "app.py"
    source = path.read_text(encoding="utf-8")
    marker = "# channel-render-lock"
    identity = '''
@app.get("/api/channel-workspace")
def channel_workspace_identity():
    import os
    return {"instance": os.environ.get("YOUTOO_INSTANCE", "")}

'''
    if 'def channel_workspace_identity()' not in source:
        source = source.replace('@app.post("/api/render")', identity + '@app.post("/api/render")', 1)
    if marker in source:
        path.write_text(source, encoding="utf-8")
        return
    old = "            render(job, tl.segments, tl.total_duration, opts)"
    if old not in source:
        raise RuntimeError("편집기 동시 작업 연결 위치를 찾지 못했습니다.")
    # Default also covers the existing launcher; workers pass the same home.
    new = '''            # channel-render-lock
            from core.process_lock import file_lock
            import os
            lock_path = Path(os.environ.get("YOUTOO_HOME") or ROOT.parent) / "채널별_작업공간" / ".render.lock"
            def waiting_for_render():
                job.stage = "다른 채널 편집 완료 대기 중"
            with file_lock(lock_path, cancelled=lambda: job.cancel_requested, waiting=waiting_for_render):
                render(job, tl.segments, tl.total_duration, opts)'''
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
