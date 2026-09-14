# -*- coding: utf-8 -*-
"""중복 렌더를 막고 완성 MP4만 최종 파일명으로 저장한다."""
from pathlib import Path


def _replace(text, old, new, what):
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"편집프로그램 {what} 구조가 바뀌었습니다.")
    return text.replace(old, new, 1)


def apply(editor_dir):
    editor = Path(editor_dir)
    changed = False
    jobs_file = editor / "core" / "jobs.py"
    source = jobs_file.read_text(encoding="utf-8")
    anchor = "    def get(self, job_id: str) -> Job | None:"
    addition = '''    def create_for_output(self, output: str) -> Job | None:
        """같은 MP4에 두 렌더가 동시에 쓰지 못하도록 작업을 원자적으로 만든다."""
        with self._lock:
            if any(j.status in ("pending", "running") and j.result.get("output") == output
                   for j in self._jobs.values()):
                return None
            job = Job(id=uuid.uuid4().hex[:12], result={"output": output})
            self._jobs[job.id] = job
            return job

'''
    if "def create_for_output" not in source:
        source = _replace(source, anchor, addition + anchor, "작업 저장소")
        jobs_file.write_text(source, encoding="utf-8")
        changed = True

    app_file = editor / "app.py"
    source = app_file.read_text(encoding="utf-8")
    old = '    job = jobs.create()\n    job.result = {"warnings": prep["warnings"]}'
    new = ('    job = jobs.create_for_output(str(Path(req.output).resolve())) if do_mp4 else jobs.create()\n'
           '    if job is None:\n'
           '        raise HTTPException(409, "같은 파일로 영상을 만드는 작업이 이미 진행 중입니다. 완료될 때까지 기다려 주세요.")\n'
           '    job.result["warnings"] = prep["warnings"]')
    updated = _replace(source, old, new, "렌더 요청")
    if updated != source:
        app_file.write_text(updated, encoding="utf-8")
        changed = True

    renderer_file = editor / "core" / "renderer.py"
    source = renderer_file.read_text(encoding="utf-8")
    updated = source
    if "import os\n" not in updated:
        updated = _replace(updated, "import random\n", "import os\nimport random\n", "렌더러 import")
    updated = _replace(updated,
        '    tmp = Path(tempfile.mkdtemp(prefix="aip_"))',
        '    tmp = Path(tempfile.mkdtemp(prefix="aip_"))\n'
        '    target = Path(opts.output)\n'
        '    partial = target.with_name(f"{target.stem}.{job.id}.rendering{target.suffix}")', "임시 MP4")
    updated = _replace(updated,
        '"-r", str(fps), "-movflags", "+faststart", "-t", f"{total_duration:.4f}", opts.output]',
        '"-r", str(fps), "-movflags", "+faststart", "-t", f"{total_duration:.4f}", str(partial)]', "출력 경로")
    updated = _replace(updated,
        '        Path(opts.output).parent.mkdir(parents=True, exist_ok=True)',
        '        target.parent.mkdir(parents=True, exist_ok=True)', "저장 폴더")
    updated = _replace(updated,
        '        job.progress = 1.0\n        job.stage = "완료"',
        '        if media_duration(str(partial)) <= 0:\n'
        '            raise RuntimeError("완성 영상 파일을 읽을 수 없습니다.")\n'
        '        os.replace(partial, target)\n'
        '        job.progress = 1.0\n        job.stage = "완료"', "완성 검증")
    updated = _replace(updated,
        '    finally:\n        shutil.rmtree(tmp, ignore_errors=True)',
        '    finally:\n        partial.unlink(missing_ok=True)\n        shutil.rmtree(tmp, ignore_errors=True)', "임시 파일 정리")
    if updated != source:
        renderer_file.write_text(updated, encoding="utf-8")
        changed = True
    return changed
