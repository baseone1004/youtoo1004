# -*- coding: utf-8 -*-
"""설치된 편집프로그램의 KIE 이미지→영상 연결을 최신 주소로 맞춘다."""
from pathlib import Path


def apply(editor_dir):
    target = Path(editor_dir) / "core" / "kie.py"
    if not target.is_file():
        return False
    source = target.read_text(encoding="utf-8")
    updated = source
    if 'UPLOAD_API = "https://kieai.redpandaai.co"' not in updated:
        anchor = 'API = "https://api.kie.ai"'
        if anchor not in updated:
            raise ValueError("KIE API 주소를 찾지 못했습니다.")
        updated = updated.replace(anchor, anchor + '\nUPLOAD_API = "https://kieai.redpandaai.co"', 1)
    old_upload = 'f"{API}/api/file-stream-upload"'
    if old_upload in updated:
        updated = updated.replace(old_upload, 'f"{UPLOAD_API}/api/file-stream-upload"', 1)
    if 'f"{UPLOAD_API}/api/file-stream-upload"' not in updated:
        raise ValueError("KIE 업로드 주소를 찾지 못했습니다.")
    old_mode = '"generation_type": "REFERENCE_2_VIDEO"'
    if old_mode in updated:
        updated = updated.replace(old_mode, '"generation_type": "FIRST_AND_LAST_FRAMES_2_VIDEO"', 1)
    if '"generation_type": "FIRST_AND_LAST_FRAMES_2_VIDEO"' not in updated:
        raise ValueError("KIE 이미지 영상화 방식을 찾지 못했습니다.")
    old_urls = '                urls = res.get("resultUrls") or []'
    new_urls = ('                payload = res.get("data") or res\n'
                '                urls = (payload.get("resultUrls") or payload.get("result_urls")\n'
                '                        or res.get("resultUrls") or res.get("result_urls") or [])')
    if old_urls in updated:
        updated = updated.replace(old_urls, new_urls, 1)
    if new_urls not in updated:
        raise ValueError("KIE 결과 주소 처리 부분을 찾지 못했습니다.")
    if updated != source:
        target.write_text(updated, encoding="utf-8")
    app_file = Path(editor_dir) / "app.py"
    if not app_file.is_file():
        return updated != source
    app_source = app_file.read_text(encoding="utf-8")
    app_updated = app_source
    if 'job.result = {"videos": results, "failed": failed,' not in app_updated:
        replacements = (
            ('    def work(job):\n        results = {}\n        for i, no in enumerate(req.scenes):',
             '    def work(job):\n        results = {}\n        failed = []\n        for i, no in enumerate(req.scenes):'),
            ('job.add_log(f"[{no:03d}] 이미지 없음 → 건너뜀"); continue',
             'job.add_log(f"[{no:03d}] 이미지 없음 → 건너뜀"); failed.append(no); continue'),
            ('            except kie.KieError as e:\n                job.add_log(f"[{no:03d}] ✗ {e}")',
             '            except kie.KieError as e:\n                job.add_log(f"[{no:03d}] ✗ {e}")\n                failed.append(no)'),
            ('        job.result = {"videos": results, "output_dir": str(out_dir)}',
             '        job.result = {"videos": results, "failed": failed, "output_dir": str(out_dir)}\n'
             '        if failed:\n            raise RuntimeError("KIE 영상 변환 실패: " + ", ".join(f"{no:03d}" for no in failed) + " (작업 로그 확인)")'),
        )
        for old, new in replacements:
            if old not in app_updated:
                raise ValueError("편집프로그램 KIE 작업 구조가 바뀌어 오류 표시를 적용하지 못했습니다.")
            app_updated = app_updated.replace(old, new, 1)
    if app_updated != app_source:
        app_file.write_text(app_updated, encoding="utf-8")
    return updated != source or app_updated != app_source
