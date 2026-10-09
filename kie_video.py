"""KIE AI (kie.ai) — 이미지 → 영상 변환 (후킹 구간용).

문서: https://docs.kie.ai/  ·  파일 업로드 → createTask → recordInfo 폴링 → mp4 다운로드
모델: veo-3-1 (Google Veo 3.1) / kling-3.0/video (Kling 3.0)
"""
from __future__ import annotations

import json
import mimetypes
import time
import os
import threading
import uuid
from pathlib import Path

import requests

API = "https://api.kie.ai"
UPLOAD_API = "https://kieai.redpandaai.co"

MODELS = {
    "veo-3-1": {"label": "Veo 3.1 (8초, 소리 포함)", "durations": [4, 6, 8], "default_duration": 8},
    "kling-3.0/video": {"label": "Kling 3.0 (5·10초)", "durations": [5, 10], "default_duration": 5},
}

_LOCK = threading.RLock()

def _save(path, records):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class KieError(RuntimeError):
    pass


class Kie:
    def __init__(self, api_key: str, log=None):
        if not api_key:
            raise KieError("KIE API 키가 없습니다. https://kie.ai/api-key 에서 발급받아 설정에 넣으세요.")
        self.h = {"Authorization": f"Bearer {api_key.strip()}"}
        self.log = log or (lambda s: None)

    # -- 파일 업로드 (공개 URL 필요) ---------------------------------------
    def upload(self, path: str | Path, upload_path: str = "auto-image-placer") -> str:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        with open(p, "rb") as f:
            r = requests.post(f"{UPLOAD_API}/api/file-stream-upload", headers=self.h,
                              files={"file": (p.name, f, mime)}, data={"uploadPath": upload_path, "fileName": p.name},
                              timeout=120)
        j = self._json(r)
        url = (j.get("data") or {}).get("downloadUrl") or (j.get("data") or {}).get("fileUrl")
        if not url:
            raise KieError(f"업로드 응답에 URL 이 없습니다: {j}")
        return url

    # -- 작업 생성 ----------------------------------------------------------
    def create_task(self, model: str, prompt: str, image_url: str, aspect_ratio: str = "16:9",
                    duration: int | None = None, resolution: str = "1080p") -> str:
        if model == "veo-3-1":
            inp = {"prompt": prompt, "image_urls": [image_url], "generation_type": "FIRST_AND_LAST_FRAMES_2_VIDEO",
                   "aspect_ratio": aspect_ratio, "resolution": resolution, "enable_translation": True}
            if duration:
                inp["duration"] = int(duration)
        elif model.startswith("kling"):
            inp = {"prompt": prompt, "image_urls": [image_url], "duration": str(duration or 5),
                   "aspect_ratio": aspect_ratio, "mode": "std", "sound": False}
        else:
            inp = {"prompt": prompt, "image_urls": [image_url], "aspect_ratio": aspect_ratio}
            if duration:
                inp["duration"] = duration
        r = requests.post(f"{API}/api/v1/jobs/createTask", headers={**self.h, "Content-Type": "application/json"},
                          json={"model": model, "input": inp}, timeout=60)
        j = self._json(r)
        task = (j.get("data") or {}).get("taskId")
        if not task:
            raise KieError(f"taskId 를 받지 못했습니다: {j}")
        return task

    # -- 폴링 ---------------------------------------------------------------
    def wait(self, task_id: str, timeout: float = 900, interval: float = 8, cancel=None) -> list[str]:
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            if cancel and cancel():
                raise KieError("취소됨")
            r = requests.get(f"{API}/api/v1/jobs/recordInfo", headers=self.h, params={"taskId": task_id}, timeout=60)
            j = self._json(r)
            d = j.get("data") or {}
            state = d.get("state") or ""
            if state != last:
                self.log(f"      상태: {state}" + (f" ({d.get('progress')}%)" if d.get("progress") else ""))
                last = state
            if state == "success":
                res = d.get("resultJson") or "{}"
                if isinstance(res, str):
                    res = json.loads(res)
                payload = res.get("data") or res
                urls = (payload.get("resultUrls") or payload.get("result_urls")
                        or res.get("resultUrls") or res.get("result_urls") or [])
                if not urls:
                    raise KieError(f"결과 URL 이 비어 있습니다: {res}")
                return urls
            if state == "fail":
                raise KieError(f"생성 실패: {d.get('failMsg') or d.get('failCode') or d}")
            time.sleep(interval)
        raise KieError("시간 초과 (15분)")

    @staticmethod
    def download(url: str, dest: str | Path) -> Path:
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".download")
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            if not tmp.stat().st_size:
                raise KieError("다운로드한 영상이 비어 있습니다. 이어서 변환하면 같은 작업을 다시 다운로드합니다.")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest

    @staticmethod
    def _json(r: requests.Response) -> dict:
        try:
            j = r.json()
        except ValueError:
            raise KieError(f"HTTP {r.status_code}: {r.text[:200]}")
        code = j.get("code", r.status_code)
        if r.status_code != 200 or (code not in (200, 0)):
            msg = j.get("msg") or j.get("message") or r.text[:200]
            hint = {401: " (API 키 확인)", 402: " (크레딧 부족)", 429: " (요청 과다, 잠시 후)"}.get(code, "")
            raise KieError(f"KIE 오류 {code}: {msg}{hint}")
        return j


def image_to_video(api_key: str, image: str, prompt: str, out_path: str, model: str = "veo-3-1",
                   aspect_ratio: str = "16:9", duration: int | None = None, log=None, cancel=None) -> Path:
    log = log or (lambda s: None)
    with _LOCK:
        k = Kie(api_key, log)
        dest = Path(out_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        journal = dest.parent / ".kie-video-tasks.json"
        records = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {}
        key = dest.name
        record = records.get(key, {})
        try:
            if cancel and cancel():
                raise KieError("취소됨")
            if record.get("status") == "submitting" and not record.get("task_id"):
                raise KieError(f"{dest.stem}번 영상의 접수 응답을 받지 못했습니다. KIE 작업 내역의 작업 번호를 확인해야 중복 결제 없이 이어갈 수 있습니다.")
            if not record.get("task_id"):
                log(f"   업로드: {Path(image).name}")
                url = k.upload(image)
                if cancel and cancel():
                    raise KieError("취소됨")
                records[key] = {"status": "submitting", "model": model}
                _save(journal, records)
                try:
                    task = k.create_task(model, prompt, url, aspect_ratio, duration)
                except KieError as exc:
                    if "taskId" not in str(exc):
                        records.pop(key, None)
                        _save(journal, records)
                    raise
                record = records[key] = {"status": "pending", "task_id": task, "model": model}
                _save(journal, records)
                log(f"   taskId {task} · 작업 번호 저장됨")
            else:
                log(f"   기존 영상 작업 조회: {record['task_id']} (새 생성 요청 없음)")
            try:
                urls = k.wait(record["task_id"], cancel=cancel)
            except KieError as exc:
                if str(exc).startswith("생성 실패:"):
                    records.pop(key, None)
                    _save(journal, records)
                raise
            if cancel and cancel():
                raise KieError("취소됨")
            result = k.download(urls[0], dest)
            record["status"] = "saved"
            _save(journal, records)
            return result
        except requests.RequestException:
            raise KieError("KIE 통신 또는 다운로드 연결이 끊겼습니다. 이어서 변환하면 저장된 작업 번호로 다시 확인합니다.") from None
        except (OSError, ValueError) as exc:
            raise KieError("영상 작업 기록·파일 처리 오류: " + type(exc).__name__) from None
