"""KIE Z-Image 실행기. 편집기의 core/imagegen.py로 설치된다.

API 문서: https://docs.kie.ai/market/z-image/z-image
작업 ID를 이미지 폴더에 보관하여 중단/재시작 후 같은 작업을 조회한다.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image

API = "https://api.kie.ai/api/v1/jobs"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif"}


class KieImageError(RuntimeError):
    pass


def friendly_error(code, message=""):
    text = str(message).lower()
    if str(code) in {"401", "403"} or "unauthor" in text or "api key" in text:
        return "KIE API 키를 확인하세요. 설정에서 새 키를 저장한 뒤 다시 시도하세요."
    if str(code) == "402" or any(x in text for x in ("credit", "balance", "insufficient")):
        return "KIE 크레딧이 부족합니다. 잔액을 충전한 뒤 이어서 만들기를 누르세요."
    if str(code) == "429":
        return "KIE 요청이 몰리고 있습니다. 잠시 뒤 이어서 만들기를 누르세요."
    return "KIE 이미지 생성에 실패했습니다. KIE 작업 내역을 확인한 뒤 다시 시도하세요."


@dataclass
class Scene:
    no: int
    prompt: str
    line: str = ""
    kind: str = ""


def parse_prompts(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    scenes = []
    blocks = re.split(r"^===\s*(\d{1,4})\s*===\s*$", text, flags=re.M)
    if len(blocks) > 2:
        for i in range(1, len(blocks) - 1, 2):
            no, body = int(blocks[i]), blocks[i + 1]
            m = re.search(r"^\s*(?:프롬프트|prompt)\s*[:：]\s*(.+)", body, re.M | re.I | re.S)
            prompt = m.group(1) if m else body.strip()
            prompt = re.split(r"\n\s*(?:유형|대사|감정|행동)\s*[:：]", prompt)[0].strip()
            line = re.search(r"^\s*대사\s*[:：]\s*(.+)$", body, re.M)
            kind = re.search(r"^\s*유형\s*[:：]\s*([ABCD])", body, re.M)
            scenes.append(Scene(no, prompt, line.group(1).strip() if line else "", kind.group(1) if kind else ""))
        return scenes
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d{1,4})\s*[.):]\s*(.+)$", line)
        scenes.append(Scene(int(m.group(1)), m.group(2)) if m else Scene(len(scenes) + 1, line))
    return scenes


@dataclass
class GenSettings:
    prompts_file: str
    output_dir: str
    api_key: str = field(default="", repr=False)
    start_no: int = 1
    end_no: int = 0
    skip_existing: bool = True
    reference_image: str = ""
    style_prefix: str = ""
    aspect_ratio: str = "16:9"
    poll_interval: float = 3
    timeout: float = 900


@dataclass
class GenState:
    status: str = "idle"
    current: int = 0
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    total: int = 0
    log: list = field(default_factory=list)
    error: str = ""
    files: dict = field(default_factory=dict)
    queued: list = field(default_factory=list)

    def add(self, text):
        self.log.append(time.strftime("%H:%M:%S ") + text)
        self.log = self.log[-300:]

    def to_dict(self):
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in vars(self).items()}


class Runner:
    def __init__(self):
        self.state = GenState()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.RLock()
        self._thread = None

    def start(self, settings):
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("이미지 생성이 진행 중입니다. 끝나거나 중단한 뒤 다시 시도하세요.")
            if not settings.api_key:
                raise KieImageError("설정에서 KIE API 키를 먼저 저장하세요.")
            if not parse_prompts(settings.prompts_file):
                raise KieImageError("이미지 프롬프트가 없습니다. 프롬프트를 먼저 만들어 주세요.")
            if settings.reference_image and not self._valid_image(Path(settings.reference_image)):
                raise KieImageError("레퍼런스 이미지를 읽지 못했습니다. 다시 올려 주세요.")
            self.settings = settings
            self._reference_url = None
            self.state = GenState(status="running")
            self._stop.clear()
            self._pause.clear()
            self._thread = threading.Thread(target=self._run, args=(settings,), daemon=True)
            self._thread.start()

    def pause(self):
        self._pause.set()
        if self.state.status == "running":
            self.state.status = "paused"

    def resume(self):
        self._pause.clear()
        if self.state.status == "paused":
            self.state.status = "running"

    def stop(self):
        self._stop.set()
        self._pause.clear()
        self.state.add("중단 요청 · 이미 접수된 KIE 작업은 이어 만들기에서 확인합니다.")

    def queue_regen(self, no):
        with self._lock:
            if self.state.status not in {"running", "paused"} or no in self.state.queued:
                return False
            if no not in {s.no for s in parse_prompts(self.settings.prompts_file)}:
                raise KieImageError("해당 장면의 프롬프트가 없습니다.")
            self.state.queued.append(no)
            self._save_regens()
            return True

    def _save_regens(self):
        out = Path(self.settings.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        self._save(out / ".kie-image-regens.json", {
            "prompts_file": str(Path(self.settings.prompts_file).resolve()),
            "queued": self.state.queued, "active": getattr(self, "_active_regen", None)})

    @staticmethod
    def _valid_image(path):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
            return True
        except (OSError, ValueError, SyntaxError):
            return False

    def recover_submission(self, output_dir, scene, task_id="", confirmed_not_created=False):
        """접수 응답을 잃은 요청만 사람이 KIE 작업 내역으로 확인해 복구한다."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise KieImageError("이미지 생성을 중단한 뒤 작업 기록을 복구하세요.")
            journal = Path(output_dir) / ".kie-image-tasks.json"
            records = json.loads(journal.read_text(encoding="utf-8"))
            slot = str(scene)
            record = records.get(slot, {})
            if record.get("status") != "submitting":
                raise KieImageError("접수 확인이 필요한 장면만 복구할 수 있습니다.")
            if task_id:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
                    raise KieImageError("KIE 작업 번호 형식을 확인하세요.")
                record.update(task_id=task_id, status="pending")
            elif confirmed_not_created:
                records.pop(slot)
            else:
                raise KieImageError("KIE 작업 번호를 입력하거나 접수되지 않은 것을 확인하세요.")
            self._save(journal, records)

    def _wait(self, seconds):
        if self._stop.wait(seconds):
            return False
        while self._pause.is_set():
            if self._stop.wait(.2):
                return False
        return not self._stop.is_set()

    def _request(self, method, path, key, **kwargs):
        try:
            response = requests.request(method, API + path, headers={"Authorization": "Bearer " + key},
                                        timeout=30, **kwargs)
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise KieImageError("KIE 연결을 확인하지 못했습니다. 잠시 뒤 이어서 만들기를 누르세요.") from None
        if not response.ok or str(payload.get("code")) != "200":
            raise KieImageError(friendly_error(payload.get("code", response.status_code), payload.get("msg")))
        return payload.get("data") or {}

    @staticmethod
    def _save(path, records):
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _upload_reference(self, s):
        if getattr(self, "_reference_url", None) and time.monotonic() - getattr(self, "_reference_uploaded_at", 0) < 12 * 3600:
            return self._reference_url
        try:
            with Image.open(s.reference_image) as image:
                image.load()
                buffer = io.BytesIO()
                image.convert("RGBA").save(buffer, format="PNG")
            data = buffer.getvalue()
            if len(data) > 10 * 1024 * 1024:
                raise KieImageError("레퍼런스 이미지가 너무 큽니다. 10MB 이하로 다시 올려 주세요.")
            response = requests.post("https://kieai.redpandaai.co/api/file-base64-upload",
                headers={"Authorization": "Bearer " + s.api_key}, timeout=60,
                json={"base64Data": "data:image/png;base64," + base64.b64encode(data).decode(),
                      "uploadPath": "images/references"})
            payload = response.json()
            if not response.ok or str(payload.get("code")) != "200":
                raise KieImageError(friendly_error(payload.get("code", response.status_code), payload.get("msg")))
            url = (payload.get("data") or {}).get("downloadUrl")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError()
            self._reference_url = url
            self._reference_uploaded_at = time.monotonic()
            return url
        except (requests.RequestException, ValueError, OSError):
            raise KieImageError("레퍼런스 이미지 업로드에 실패했습니다. 연결을 확인하고 이어 만들기를 누르세요.") from None

    def _generate(self, scene, s, out, records, journal, regen=False):
        # Legacy @image references are unsupported; character descriptions come from the prompt/profile.
        prompt = re.sub(r"@image\s*\d+", "the described character", s.style_prefix + " " + scene.prompt).strip()
        model = "seedream/4.5-edit" if s.reference_image else "z-image"
        reference_hash = hashlib.sha256(Path(s.reference_image).read_bytes()).hexdigest() if s.reference_image else ""
        fingerprint = hashlib.sha256((s.aspect_ratio + prompt + (model + reference_hash if s.reference_image else "")).encode()).hexdigest()
        slot = str(scene.no)
        record = records.get(slot, {})
        # Pending remote work is always resolved before a changed prompt can incur another charge.
        if record and record.get("status") != "saved" and record.get("fingerprint") != fingerprint:
            self.state.add(f"{scene.no:03d} 이전 프롬프트로 접수된 작업을 먼저 확인합니다. 새 설명 적용은 완료 후 다시 만들기를 사용하세요.")
        if regen and record.get("status") == "saved":
            records.pop(slot, None)
            record = {}
        if not record:
            if not self._wait(0):
                return None
            input_data = {"prompt": prompt, "aspect_ratio": s.aspect_ratio, "nsfw_checker": True}
            if s.reference_image:
                input_data.update(image_urls=[self._upload_reference(s)], quality="basic",
                    prompt="Use the reference image to preserve the character design, colors and style. Create a NEW scene following this description, rather than copying the reference background. Only include the character when the scene calls for it. " + prompt)
                if not self._wait(0):
                    return None
            # A lost POST response cannot safely be retried without provider idempotency.
            records[slot] = {"status": "submitting", "fingerprint": fingerprint}
            self._save(journal, records)
            try:
                data = self._request("POST", "/createTask", s.api_key,
                                     json={"model": model, "input": input_data})
            except KieImageError as exc:
                if "연결을 확인" not in str(exc):
                    records.pop(slot, None)
                    self._save(journal, records)
                raise
            task = data.get("taskId")
            if not task:
                raise KieImageError("KIE 작업 번호를 받지 못했습니다. KIE 작업 내역을 확인해 주세요.")
            record = records[slot] = {"task_id": task, "fingerprint": fingerprint, "status": "pending"}
            self._save(journal, records)
        if not record.get("task_id"):
            raise KieImageError("이전 요청의 접수 여부를 확인할 수 없습니다. KIE 작업 내역 확인 후 해당 장면의 작업 기록을 정리해 주세요.")
        deadline = time.monotonic() + s.timeout
        interval = s.poll_interval
        while self._wait(0):
            if time.monotonic() >= deadline:
                raise KieImageError("이미지 생성 대기 시간이 지났습니다. 이어서 만들기를 누르면 같은 작업을 다시 확인합니다.")
            data = self._request("GET", "/recordInfo", s.api_key, params={"taskId": record["task_id"]})
            if data.get("state") == "fail":
                records.pop(slot, None)
                self._save(journal, records)
                raise KieImageError(friendly_error(data.get("failCode"), data.get("failMsg")))
            if data.get("state") == "success":
                try:
                    result = data.get("resultJson") or {}
                    result = json.loads(result) if isinstance(result, str) else result
                    url = result["resultUrls"][0]
                    response = requests.get(url, timeout=60)  # Never forward API credentials to result hosts.
                    response.raise_for_status()
                    with Image.open(io.BytesIO(response.content)) as image:
                        image.load()
                        temp = out / f"{scene.no:03d}.tmp"
                        image.convert("RGB").save(temp, format="JPEG", quality=95)
                    if not self._wait(0):
                        temp.unlink(missing_ok=True)
                        return None
                    dest = out / f"{scene.no:03d}.jpg"
                    old = out / "이전"
                    for previous in out.glob(f"{scene.no:03d}.*"):
                        if previous.suffix.lower() in IMAGE_EXTS:
                            old.mkdir(exist_ok=True)
                            previous.replace(old / f"{previous.stem}_{time.time_ns()}{previous.suffix}")
                    temp.replace(dest)
                except (requests.RequestException, ValueError, OSError, KeyError, IndexError, TypeError):
                    raise KieImageError("완성된 이미지 다운로드에 실패했습니다. 이어서 만들기를 누르면 새로 결제하지 않고 다시 받습니다.") from None
                record["status"] = "saved"
                self._save(journal, records)
                return dest
            if not self._wait(interval):
                break
            interval = min(15, interval * 1.5)
        return None

    def _run(self, s):
        st = self.state
        try:
            out = Path(s.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            journal = out / ".kie-image-tasks.json"
            records = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {}
            by_no = {sc.no: sc for sc in parse_prompts(s.prompts_file)}
            todo = [(sc, not s.skip_existing) for sc in by_no.values()
                    if sc.no >= s.start_no and (not s.end_no or sc.no <= s.end_no)]
            with self._lock:
                queue_path = out / ".kie-image-regens.json"
                saved = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
                if saved.get("prompts_file") == str(Path(s.prompts_file).resolve()):
                    self._active_regen = saved.get("active")
                    st.queued = list(dict.fromkeys(saved.get("queued", []) + st.queued))
                else:
                    self._active_regen = None
                st.queued = [no for no in st.queued if no in by_no]
                if self._active_regen and self._active_regen["scene"] in by_no:
                    no = self._active_regen["scene"]
                    record = records.get(str(no), {})
                    # A saved new task means the reservation already finished before a crash.
                    finished = record.get("status") == "saved" and record.get("task_id") != self._active_regen.get("previous_task")
                    todo = [(sc, regen) for sc, regen in todo if sc.no != no]
                    todo.insert(0, (by_no[no], not finished))
                else:
                    self._active_regen = None
                self._save_regens()
            st.total = len(todo)
            while self._wait(0):
                with self._lock:
                    if not todo and st.queued:
                        no = st.queued.pop(0)
                        self._active_regen = {"scene": no, "previous_task": records.get(str(no), {}).get("task_id")}
                        self._save_regens()
                        todo.append((by_no[no], True))
                    if not todo:
                        st.status = "done"
                        break
                    sc, regen = todo.pop(0)
                st.current = sc.no
                existing = [p for p in out.glob(f"{sc.no:03d}.*") if p.suffix.lower() in IMAGE_EXTS and self._valid_image(p)]
                if s.skip_existing and not regen and existing:
                    dest = existing[0]
                else:
                    st.add(f"{sc.no:03d} KIE {'Seedream 4.5 레퍼런스' if s.reference_image else 'Z-Image'} 생성 중")
                    dest = self._generate(sc, s, out, records, journal, regen)
                if dest is None:
                    break
                with self._lock:
                    if self._active_regen and self._active_regen["scene"] == sc.no:
                        self._active_regen = None
                        self._save_regens()
                if sc.no not in st.done:
                    st.done.append(sc.no)
                st.files[sc.no] = str(dest)
            if self._stop.is_set():
                st.status = "stopped"
        except Exception as exc:
            st.status = "error"
            st.error = str(exc) if isinstance(exc, KieImageError) else "이미지 작업 기록이나 파일을 읽지 못했습니다. 결과 폴더를 확인하세요."
            if st.current:
                st.failed.append(st.current)
            st.add(st.error)


runner = Runner()
