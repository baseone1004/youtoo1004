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
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


def friendly_error(code, message=""):
    text = str(message).lower()
    if str(code) in {"401", "403"} or "unauthor" in text or "api key" in text:
        return "KIE API 키를 확인하세요. 설정에서 새 키를 저장한 뒤 다시 시도하세요."
    if str(code) == "402" or any(x in text for x in ("credit", "balance", "insufficient")):
        return "KIE 크레딧이 부족합니다. 잔액을 충전한 뒤 이어서 만들기를 누르세요."
    if str(code) == "429":
        return "KIE 요청이 몰리고 있습니다. 잠시 뒤 이어서 만들기를 누르세요."
    if "prompt" in text and any(x in text for x in ("length", "long", "1000", "maximum", "max length")):
        return "이미지 설명이 KIE 모델의 글자 수 제한을 넘었습니다. 설명 길이를 줄인 뒤 다시 시도하세요."
    if "aspect" in text or "ratio" in text:
        return "이미지 비율을 확인하세요. Z-Image는 1:1, 4:3, 3:4, 16:9, 9:16을 지원합니다."
    if any(x in text for x in ("nsfw", "safety", "moderation", "sensitive")):
        return "KIE에서 이미지 내용을 제한했습니다. 해당 장면의 설명을 확인해 주세요."
    if str(code).startswith("5"):
        return "KIE 서버에서 오류가 발생했습니다. 접수 여부가 불확실한 요청은 재결제하지 않고 확인을 위해 멈춥니다."
    safe_code = str(code) if re.fullmatch(r"[\w.-]{1,30}", str(code)) else "확인 필요"
    return f"KIE 이미지 요청을 처리하지 못했습니다 (응답 코드 {safe_code}). KIE 작업 내역을 확인해 주세요."


def prompt_size(text):
    return len(text.encode("utf-16-le")) // 2


def _fit_words(text, budget):
    if prompt_size(text) <= budget:
        return text
    clipped = text.encode("utf-16-le")[:budget * 2].decode("utf-16-le", errors="ignore")
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:.")


def z_image_prompt(prompt, prefix=""):
    """Fit the provider's 1,000-character limit locally; never pay an LLM to trim.

    Strip known repeated boilerplate, reserve most space for the actual scene,
    and retain the original prompt file for editing or other image models.
    """
    prompt, prefix = (re.sub(r"\s+", " ", x).strip() for x in (prompt, prefix))
    combined = prompt if not prefix or prompt.startswith(prefix) else prefix + " " + prompt
    if prompt_size(combined) <= 1000:
        return combined
    combined = re.sub(r"No subtitle, no caption, no narration box,.*?Do not put the narration text inside the image\.",
                      "No text, subtitles or logos.", combined, flags=re.I)
    style, scene = "", combined
    if combined.startswith("STRICT STYLE LOCK:"):
        boundary = re.search(r"(?:This style takes priority over conflicting scene descriptions\.|mixed-media image[.,]|consistent channel look\.)", combined, re.I)
        if boundary:
            style, scene = combined[:boundary.end()], combined[boundary.end():].strip()
    if not style and prefix:
        style, scene = prefix, prompt.removeprefix(prefix).strip()
    if not scene:
        scene, style = combined, ""
    style = re.sub(r"STRICT STYLE LOCK:\s*", "", style, flags=re.I)
    style = re.sub(r"(?:Maintain consistent character proportions and expressions across scenes|Fill the frame edge to edge|This style takes priority over conflicting scene descriptions)\.?", "", style, flags=re.I)
    # Drop duplicate clauses without changing their first occurrence/order.
    style = ", ".join(dict.fromkeys(x.strip() for x in style.split(",") if x.strip()))
    if style:
        style_budget = min(prompt_size(style), max(200, 995 - prompt_size(scene)), 350)
        shortened = _fit_words(style, style_budget)
        if shortened != style and shortened.rfind(",") > len(shortened) // 2:
            shortened = shortened.rsplit(",", 1)[0]
        style = shortened
        scene = _fit_words(scene, 998 - prompt_size(style))
        return style + ". " + scene
    return _fit_words(scene, 1000)


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
    reference_model: str = "qwen/image-edit"
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
    duplicate_pairs: list = field(default_factory=list)
    image_model: str = ""
    credits_per_image: float = 0
    estimated_submitted_credits: float = 0

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

    def recover_submission(self, output_dir, scene, task_id="", confirmed_not_created=False, confirmed_failed=False):
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
            elif confirmed_not_created or confirmed_failed:
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
        if not isinstance(payload, dict):
            raise KieImageError("KIE 응답을 읽지 못했습니다. 접수 여부를 확인한 뒤 이어서 진행하세요.")
        if not response.ok or str(payload.get("code")) != "200":
            code = payload.get("code", response.status_code)
            rejected = str(code) in {"400", "401", "402", "403", "404", "422", "429"} and response.status_code < 500
            raise KieImageError(friendly_error(code, payload.get("msg")), rejected=rejected)
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

    def _check_similarity(self, dest):
        # A warning never triggers another paid request. Compare recent scenes only.
        def thumbnail(path):
            with Image.open(path) as im:
                im.load()
                return list(im.convert("RGB").resize((32, 18)).getdata())
        try:
            current = thumbnail(dest)
            previous = sorted((p for p in dest.parent.glob("[0-9]*.jpg") if p != dest), reverse=True)[:6]
            for path in previous:
                other = thumbnail(path)
                difference = sum(abs(a-b) for px, py in zip(current, other) for a,b in zip(px,py)) / (len(current)*3)
                if difference <= 3:
                    pair = [int(path.stem), int(dest.stem)]
                    self.state.duplicate_pairs.append(pair)
                    self.state.add(f"이미지 확인 권장 · {pair[0]:03d}번과 {pair[1]:03d}번이 매우 비슷합니다. 자동 재생성 비용은 발생하지 않습니다.")
                    self._save(dest.parent / ".kie-image-review.json", {"similar_scenes": self.state.duplicate_pairs})
                    break
        except (OSError, ValueError):
            pass

    def _generate(self, scene, s, out, records, journal, regen=False):
        # Legacy @image references are unsupported; character descriptions come from the prompt/profile.
        prompt = re.sub(r"@image\s*\d+", "the described character", s.style_prefix + " " + scene.prompt).strip()
        model = s.reference_model if s.reference_image else "z-image"
        if model == "z-image":
            compact = z_image_prompt(re.sub(r"@image\s*\d+", "the described character", scene.prompt), s.style_prefix)
            if compact != prompt:
                self.state.add(f"{scene.no:03d} 이미지 설명 길이 자동 정리 · {prompt_size(prompt)} → {prompt_size(compact)}자 (원문 보존)")
            prompt = compact
            if not prompt or prompt_size(prompt) > 1000:
                raise KieImageError("Z-Image 설명은 1~1,000자로 작성해 주세요.")
            if s.aspect_ratio not in {"1:1", "4:3", "3:4", "16:9", "9:16"}:
                raise KieImageError(friendly_error(422, "aspect_ratio"))
        self.state.image_model = model
        self.state.credits_per_image = {"z-image": 0.8, "bytedance/seedream-v4-edit": 5, "seedream/4.5-edit": 6.5, "qwen/image-edit": 2}.get(model, 0)
        if model not in {"z-image", "seedream/4.5-edit", "bytedance/seedream-v4-edit", "qwen/image-edit"}:
            raise KieImageError("지원하지 않는 이미지 모델입니다.")
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
            if model == "bytedance/seedream-v4-edit":
                input_data.pop("quality", None)
                input_data.pop("aspect_ratio", None)
                input_data.update(image_size="landscape_16_9", image_resolution="2K", max_images=1)
            if model == "qwen/image-edit":
                if s.aspect_ratio != "16:9":
                    raise KieImageError("레퍼런스 절약 모델은 16:9로 설정해 주세요.")
                ref_url = input_data["image_urls"][0]
                instruction = "Use the reference only for character identity, colors and drawing style. Replace its background, pose and composition with the specific new scene below. No text or logos. "
                input_data = {"prompt": instruction + _fit_words(prompt, 2000 - prompt_size(instruction)),
                              "image_url": ref_url, "image_size": "landscape_16_9",
                              "num_images": "1", "num_inference_steps": 30, "guidance_scale": 4,
                              "acceleration": "none", "output_format": "png", "enable_safety_checker": True}
            # A lost POST response cannot safely be retried without provider idempotency.
            self.state.estimated_submitted_credits += self.state.credits_per_image
            records[slot] = {"status": "submitting", "fingerprint": fingerprint, "model": model, "estimated_credits": self.state.credits_per_image}
            self._save(journal, records)
            try:
                data = self._request("POST", "/createTask", s.api_key,
                                     json={"model": model, "input": input_data})
            except KieImageError as exc:
                if exc.rejected:
                    records.pop(slot, None)
                    self._save(journal, records)
                    self.state.estimated_submitted_credits -= self.state.credits_per_image
                raise
            task = data.get("taskId")
            if not task:
                raise KieImageError("KIE 작업 번호를 받지 못했습니다. KIE 작업 내역을 확인해 주세요.")
            record = records[slot] = {"task_id": task, "fingerprint": fingerprint, "status": "pending"}
            self._save(journal, records)
        if not record.get("task_id"):
            raise KieImageError(f"{scene.no:03d}번 장면의 이전 요청 접수 여부를 확인할 수 없습니다. KIE 작업 내역이 진행 중·완료라면 작업 번호를 연결하고, 실패·미접수로 확인되면 작업 기록 복구에서 정리하세요.")
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
                self._check_similarity(dest)
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
            seen_prompts = {}
            for sc in by_no.values():
                normalized = re.sub(r"\s+", " ", sc.prompt).strip().lower()
                if normalized in seen_prompts:
                    raise KieImageError(f"{seen_prompts[normalized]:03d}번과 {sc.no:03d}번의 이미지 설명이 같습니다. 장면 설명을 다르게 수정해 주세요. 유료 요청은 보내지 않았습니다.")
                seen_prompts[normalized] = sc.no
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
                    st.add(f"{sc.no:03d} KIE {({'qwen/image-edit':'Qwen Image Edit 레퍼런스', 'bytedance/seedream-v4-edit':'Seedream 4.0 레퍼런스'}.get(s.reference_model, 'Seedream 4.5 레퍼런스')) if s.reference_image else 'Z-Image'} 생성 중")
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
