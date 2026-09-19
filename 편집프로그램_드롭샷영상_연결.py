# -*- coding: utf-8 -*-
"""드롭샷 AI 화면을 좌표 클릭으로 조작해 이미지를 움직이는 영상(NNN.mp4)으로 바꾸는 기능을 편집프로그램에 붙인다.
KIE 크레딧이 없을 때 대신 쓴다. core/videogen.py 를 새로 만들고 app.py 에 /api/vgen/start·status·stop 을 더한다."""
from pathlib import Path

VIDEOGEN = r'''"""이미지 → 움직이는 영상 자동 변환 — 드롭샷 AI '영상 생성' 화면을 좌표 클릭으로 조작한다 (KIE 크레딧이 없을 때).

흐름 (장면마다): [이미지 업로드] 클릭 → 파일 대화상자에 이미지 경로 붙여넣기 + Enter → 프롬프트 입력창 클릭 → 전체 선택 → 움직임 프롬프트 붙여넣기
              → [생성] 클릭 → 최소 대기 뒤 [다운로드]를 주기적으로 눌러 다운로드 폴더에 새 영상이 생기면 출력 폴더로 NNN.mp4 로 옮김
좌표 4개(업로드·입력창·생성·다운로드)는 imagegen 과 같은 방식(6초 좌표 잡기)으로 잡는다.
"""
from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyautogui
import pyperclip

from core.imagegen import IMAGE_EXTS, _md5

VIDEO_EXTS = {".mp4", ".webm", ".mov"}


@dataclass
class VideoSettings:
    images_dir: str
    download_dir: str
    scenes: list[int]
    prompts: dict[int, str]              # 장면 번호 → 움직임 프롬프트 (없으면 motion_prompt 만)
    upload_xy: tuple[int, int]
    prompt_xy: tuple[int, int]
    generate_xy: tuple[int, int]
    download_xy: tuple[int, int]
    output_dir: str = ""                 # 비우면 images_dir
    motion_prompt: str = "Cinematic slow camera movement, subtle natural motion, keep the same style and composition."
    wait_upload: float = 6.0             # 업로드 뒤 기다리는 초
    wait_min: float = 60.0               # 생성 클릭 뒤 다운로드를 처음 눌러 보기까지 최소 초
    wait_max: float = 360.0              # 이보다 오래 걸리면 실패
    poll_every: float = 20.0             # 다운로드를 다시 눌러 보는 간격
    wait_download: float = 25.0          # 다운로드 파일이 나타날 때까지 최대 초
    wait_next: float = 2.0
    window_keyword: str = "영상"           # 영상 생성 창 제목의 일부 (이미지 창과 구분되도록 '드롭샷' 대신 '영상')
    clear_key: str = "ctrl+a"
    skip_existing: bool = True


class WrongPage(Exception):
    """다운로드 버튼이 영상이 아니라 이미지를 받아 왔다 → 좌표가 이미지 화면을 가리키고 있다."""


@dataclass
class VideoState:
    status: str = "idle"                 # idle | running | done | error | stopped
    current: int = 0
    done: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    total: int = 0
    log: list[str] = field(default_factory=list)
    error: str = ""
    files: dict[int, str] = field(default_factory=dict)
    output_dir: str = ""

    def add(self, s: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + s)
        if len(self.log) > 300:
            del self.log[:-300]

    def to_dict(self) -> dict:
        return {"status": self.status, "current": self.current, "done": self.done, "failed": self.failed, "total": self.total,
                "log": self.log[-60:], "error": self.error, "files": self.files, "output_dir": self.output_dir}


class VideoRunner:
    def __init__(self) -> None:
        self.state = VideoState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, s: VideoSettings) -> None:
        if self.busy():
            raise RuntimeError("이미 영상 변환이 실행 중입니다.")
        self.state = VideoState(output_dir=s.output_dir or s.images_dir)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(s,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self.state.add("중단 요청")

    def _wait(self, sec: float) -> bool:
        end = time.time() + sec
        while time.time() < end:
            if self._stop.is_set():
                return False
            time.sleep(0.2)
        return not self._stop.is_set()

    # -- 화면 조작 (테스트에서 바꿔 끼울 수 있게 메서드로 둔다)
    def _focus_window(self, keyword: str) -> bool:
        if not keyword:
            return True
        try:
            import pygetwindow as gw
            wins = [w for w in gw.getAllWindows() if keyword.lower() in (w.title or "").lower()]
            if not wins:
                return False
            w = wins[0]
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _click(self, xy) -> None:
        pyautogui.click(*xy)

    def _paste(self, text: str) -> None:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")

    def _press(self, key: str) -> None:
        pyautogui.press(key)

    def _hotkey(self, combo: str) -> None:
        pyautogui.hotkey(*combo.split("+"))

    @staticmethod
    def _snapshot(folder: Path) -> set[str]:
        return {p.name for p in folder.iterdir() if p.is_file()}

    # -- 파일 선택 창 (윈도우 '열기' 대화상자)
    DIALOG_TITLES = ("열기", "open", "파일 열기", "파일 업로드", "file upload", "업로드할 파일 선택")

    def _dialog_window(self):
        try:
            import pygetwindow as gw
            for w in gw.getAllWindows():
                if (w.title or "").strip().lower() in self.DIALOG_TITLES and w.width > 200:
                    return w
        except Exception:  # noqa: BLE001
            pass
        return None

    def _upload_image(self, s: VideoSettings, image: Path) -> bool:
        """[이미지 업로드] 클릭 → 파일 선택 창이 뜨면 파일 이름 칸에 경로를 넣고 Enter → 창이 닫히면 성공."""
        self._click(s.upload_xy)
        dlg = None
        for _ in range(40):                          # 최대 8초 동안 파일 선택 창을 기다린다
            time.sleep(0.2)
            dlg = self._dialog_window()
            if dlg or self._stop.is_set():
                break
        if not dlg:
            return False
        try:
            dlg.activate()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
        self._hotkey("alt+n")                        # '파일 이름' 칸으로
        time.sleep(0.2)
        self._hotkey(s.clear_key)
        self._paste(str(image))
        time.sleep(0.4)
        self._press("enter")
        for _ in range(25):                          # 창이 닫히면 파일이 선택된 것
            time.sleep(0.2)
            if not self._dialog_window():
                return True
        self._press("escape")
        return False

    def _wait_new_video(self, folder: Path, before: set[str], timeout: float) -> Path | None:
        end = time.time() + timeout
        while time.time() < end:
            if self._stop.is_set():
                return None
            for p in folder.iterdir():
                if not p.is_file() or p.name in before:
                    continue
                if p.suffix.lower() in IMAGE_EXTS:   # 영상 대신 이미지가 받아짐 → 이미지 생성 화면을 누르고 있다
                    time.sleep(1.0)
                    try:
                        p.unlink()                   # 우리가 눌러서 받은 중복 이미지는 지운다
                    except OSError:
                        pass
                    raise WrongPage("다운로드 버튼이 영상이 아니라 이미지를 받아 왔습니다. 드롭샷 '영상 생성' 화면이 앞에 보이는지, 영상 변환 좌표 4개를 그 화면에서 잡았는지 확인하세요.")
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                size, same = -1, 0
                for _ in range(300):                 # 받는 도중에 옮기지 않도록 크기가 멈출 때까지
                    try:
                        cur = p.stat().st_size
                    except OSError:
                        break
                    same = same + 1 if (cur > 0 and cur == size) else 0
                    if same >= 3:
                        return p
                    size = cur
                    time.sleep(0.4)
                return p if p.exists() else None
            time.sleep(0.5)
        return None

    # -- 한 장면
    def _do_scene(self, s: VideoSettings, no: int, image: Path, out: Path, dl: Path) -> Path | None:
        st = self.state
        self._focus_window(s.window_keyword)
        # 1) 이미지 업로드: 버튼 → 파일 선택 창 → 파일 이름 칸에 경로 → Enter (창이 안 뜨거나 안 닫히면 좌표가 틀린 것)
        if not self._upload_image(s, image):
            raise WrongPage("[이미지 업로드] 좌표를 눌렀는데 파일 선택 창이 뜨지 않았거나 파일을 받지 않았습니다. "
                            "영상 생성 화면의 '시작 프레임(이미지) 업로드' 자리를 눌렀을 때 윈도우 '열기' 창이 바로 뜨는 위치로 좌표를 다시 잡으세요.")
        st.add(f"{no:03d} 이미지 업로드 완료 → {s.wait_upload:.0f}초 대기")
        if not self._wait(max(float(s.wait_upload), 2.0)):
            return None
        # 2) 움직임 프롬프트
        prompt = (s.prompts.get(no) or "").strip()
        prompt = (prompt + " " if prompt else "") + s.motion_prompt.strip()
        self._focus_window(s.window_keyword)
        self._click(s.prompt_xy)
        time.sleep(0.3)
        self._hotkey(s.clear_key)
        self._press("backspace")
        time.sleep(0.2)
        self._paste(prompt)
        time.sleep(0.6)
        # 3) 생성 → 최소 대기 → 다운로드를 주기적으로 시도
        before = self._snapshot(dl)
        self._click(s.generate_xy)
        st.add(f"{no:03d} 생성 클릭 → {s.wait_min:.0f}초 뒤부터 다운로드를 시도 (최대 {s.wait_max:.0f}초)")
        if not self._wait(max(float(s.wait_min), 5.0)):
            return None
        deadline = time.time() + max(float(s.wait_max), float(s.wait_min) + 30)
        f = None
        attempt = 0
        while time.time() < deadline and not self._stop.is_set():
            attempt += 1
            self._focus_window(s.window_keyword)
            before = self._snapshot(dl)
            self._click(s.download_xy)
            f = self._wait_new_video(dl, before, max(float(s.wait_download), 10.0))
            if f:
                h = _md5(f)
                if h and h == getattr(self, "_last_hash", None):      # 직전 장면 영상을 또 받음 → 아직 안 됨
                    st.add(f"{no:03d} 받은 영상이 직전 장면과 같음 → 더 기다림 ({attempt})")
                    try:
                        f.unlink()
                    except OSError:
                        pass
                    f = None
                else:
                    self._last_hash = h
                    break
            else:
                st.add(f"{no:03d} 아직 영상이 안 나옴 → {s.poll_every:.0f}초 뒤 다시 ({attempt})")
            if not self._wait(max(float(s.poll_every), 5.0)):
                return None
        if not f:
            return None
        dest = out / f"{no:03d}.mp4"
        for _ in range(10):
            try:
                shutil.move(str(f), str(dest)); break
            except PermissionError:
                time.sleep(0.5)
        return dest if dest.exists() else None

    def _run(self, s: VideoSettings) -> None:
        st = self.state
        try:
            images = Path(s.images_dir); out = Path(s.output_dir or s.images_dir); out.mkdir(parents=True, exist_ok=True)
            dl = Path(s.download_dir)
            if not dl.is_dir():
                raise FileNotFoundError(f"다운로드 폴더가 없습니다: {dl}")
            st.total = len(s.scenes); st.status = "running"
            prev = sorted((p for p in out.glob("*.mp4") if p.is_file()), key=lambda p: p.stat().st_mtime)
            self._last_hash = _md5(prev[-1]) if prev else None      # 마지막 영상을 또 받으면 '아직 안 됨'으로 안다
            if s.window_keyword and not self._focus_window(s.window_keyword):      # 영상 창을 못 찾으면 엉뚱한 화면을 누르지 않는다
                raise WrongPage(f"제목에 '{s.window_keyword}'이(가) 들어간 창을 찾지 못했습니다. 드롭샷 [영상 생성] 화면을 별도 창으로 열어 두세요 (제목 'AI 영상 만들기 | 드롭샷 AI').")
            st.add(f"장면 {len(s.scenes)}개 영상 변환 · 3초 뒤 시작 (드롭샷 창을 가리지 마세요)")
            if not self._wait(3):
                st.status = "stopped"; return
            for no in s.scenes:
                if self._stop.is_set():
                    break
                st.current = no
                dest = out / f"{no:03d}.mp4"
                if s.skip_existing and dest.exists():
                    st.done.append(no); st.files[no] = str(dest); st.add(f"{no:03d} 이미 있음 → 건너뜀"); continue
                cands = [p for p in images.glob(f"{no:03d}.*") if p.suffix.lower() in IMAGE_EXTS]
                if not cands:
                    st.failed.append(no); st.add(f"{no:03d} 이미지 없음 → 건너뜀"); continue
                got = self._do_scene(s, no, cands[0], out, dl)
                if got:
                    st.done.append(no); st.files[no] = str(got); st.add(f"{no:03d} 저장: {got.name}")
                elif not self._stop.is_set():
                    st.failed.append(no); st.add(f"{no:03d} 실패 (영상을 받지 못함)")
                if not self._wait(s.wait_next):
                    break
            st.status = "stopped" if self._stop.is_set() else "done"
            st.add(f"끝 · 완료 {len(st.done)} / 실패 {len(st.failed)}")
        except pyautogui.FailSafeException:
            st.status = "stopped"; st.error = "마우스가 화면 모서리로 이동해 중단됨 (안전장치)"; st.add(st.error)
        except WrongPage as e:
            st.status = "error"; st.error = str(e); st.add("! " + st.error)
        except Exception as e:  # noqa: BLE001
            st.status = "error"; st.error = f"{e.__class__.__name__}: {e}"; st.add(st.error)


runner = VideoRunner()
'''

APP_ADDITION = '''

# ── 드롭샷 좌표로 이미지 → 움직이는 영상 (KIE 크레딧이 없을 때) ──────────────────
from core import videogen


class VideoGenStart(BaseModel):
    images_dir: str
    download_dir: str
    scenes: list[int]
    prompts: dict[int, str] = {}
    upload_xy: list[int]
    prompt_xy: list[int]
    generate_xy: list[int]
    download_xy: list[int]
    output_dir: str = ""
    motion_prompt: str = "Cinematic slow camera movement, subtle natural motion, keep the same style and composition."
    wait_upload: float = 6
    wait_min: float = 60
    wait_max: float = 360
    poll_every: float = 20
    wait_download: float = 25
    wait_next: float = 2
    window_keyword: str = "영상"
    skip_existing: bool = True


@app.post("/api/vgen/start")
def api_vgen_start(req: VideoGenStart):
    if imagegen.runner.state.status in ("running", "paused"):
        raise HTTPException(400, "이미지 생성이 돌아가는 중입니다. 끝난 뒤 영상 변환을 시작하세요.")
    if not Path(req.images_dir).is_dir():
        raise HTTPException(400, "이미지 폴더가 없습니다.")
    s = videogen.VideoSettings(
        images_dir=req.images_dir, download_dir=req.download_dir, scenes=list(req.scenes), prompts={int(k): v for k, v in req.prompts.items()},
        upload_xy=tuple(req.upload_xy), prompt_xy=tuple(req.prompt_xy), generate_xy=tuple(req.generate_xy), download_xy=tuple(req.download_xy),
        output_dir=req.output_dir, motion_prompt=req.motion_prompt, wait_upload=req.wait_upload, wait_min=req.wait_min, wait_max=req.wait_max,
        poll_every=req.poll_every, wait_download=req.wait_download, wait_next=req.wait_next, window_keyword=req.window_keyword,
        skip_existing=req.skip_existing,
    )
    try:
        videogen.runner.start(s)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    cfg = load_config(); cfg["vgen"] = req.model_dump(); save_config(cfg)
    return {"ok": True}


class VideoUploadTest(BaseModel):
    image: str
    upload_xy: list[int]
    window_keyword: str = "영상"


@app.post("/api/vgen/upload_test")
def api_vgen_upload_test(req: VideoUploadTest):
    """설정 화면용: 영상 창을 앞으로 가져와 [이미지 업로드] 좌표를 한 번 눌러 파일 선택 창이 뜨고 이미지가 들어가는지 본다."""
    if videogen.runner.busy() or imagegen.runner.state.status in ("running", "paused"):
        raise HTTPException(400, "지금 생성이 돌아가는 중입니다.")
    if not Path(req.image).is_file():
        raise HTTPException(400, "시험할 이미지 파일이 없습니다.")
    r = videogen.runner
    if req.window_keyword and not r._focus_window(req.window_keyword):
        raise HTTPException(400, f"제목에 '{req.window_keyword}'이(가) 들어간 창을 찾지 못했습니다. 드롭샷 [영상 생성] 화면을 별도 창으로 열어 두세요.")
    s = videogen.VideoSettings(images_dir="", download_dir="", scenes=[], prompts={}, upload_xy=tuple(req.upload_xy), prompt_xy=(0, 0), generate_xy=(0, 0), download_xy=(0, 0))
    ok = r._upload_image(s, Path(req.image))
    return {"ok": ok, "detail": "" if ok else "파일 선택 창이 뜨지 않았거나 닫히지 않았습니다. 업로드 좌표를 '시작 프레임' 업로드 자리로 다시 잡으세요."}


@app.get("/api/vgen/status")
def api_vgen_status():
    return videogen.runner.state.to_dict()


@app.post("/api/vgen/stop")
def api_vgen_stop():
    videogen.runner.stop()
    return {"ok": True}
'''


def apply(editor_dir):
    editor = Path(editor_dir)
    changed = False
    target = editor / "core" / "videogen.py"
    if not (editor / "core" / "imagegen.py").is_file() or not (editor / "app.py").is_file():
        return False
    if not target.is_file() or target.read_text(encoding="utf-8") != VIDEOGEN:
        target.write_text(VIDEOGEN, encoding="utf-8"); changed = True
    app_file = editor / "app.py"
    text = app_file.read_text(encoding="utf-8")
    if "/api/vgen/start" in text and "/api/vgen/upload_test" not in text:      # 먼저 붙인 버전에 업로드 시험 경로만 더한다
        a = APP_ADDITION.index("class VideoUploadTest"); b = APP_ADDITION.index('@app.get("/api/vgen/status")')
        text = text.replace('@app.get("/api/vgen/status")', APP_ADDITION[a:b] + '@app.get("/api/vgen/status")', 1)
        app_file.write_text(text, encoding="utf-8"); changed = True
    if "/api/vgen/start" not in text:
        if "@app.post(\"/api/hook/start\")" not in text:
            raise ValueError("편집프로그램 app.py 에서 KIE 영상 변환(/api/hook/start)을 찾지 못했습니다.")
        if "from core import imagegen" not in text and "import imagegen" not in text:
            raise ValueError("편집프로그램 app.py 에서 imagegen 을 찾지 못했습니다.")
        marker = 'if __name__ == "__main__":'
        if marker in text:                         # main() 이 서버를 띄우고 멈추므로 그 앞에 넣어야 경로가 등록된다
            i = text.index(marker)
            text = text[:i].rstrip("\n") + "\n" + APP_ADDITION + "\n\n" + text[i:]
        else:
            text = text.rstrip("\n") + "\n" + APP_ADDITION
        app_file.write_text(text, encoding="utf-8"); changed = True
    return changed
