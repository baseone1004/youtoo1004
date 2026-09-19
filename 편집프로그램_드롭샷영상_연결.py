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
    manual_download: bool = True         # True: 다운로드 버튼은 사람이 누르고, 프로그램은 다운로드 폴더에 새 영상이 생기면 가져간다


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
    waiting: str = ""                    # 사람이 해야 할 일이 있으면 그 안내 (화면에 크게 보여 준다)

    def add(self, s: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + s)
        if len(self.log) > 300:
            del self.log[:-300]

    def to_dict(self) -> dict:
        return {"status": self.status, "current": self.current, "done": self.done, "failed": self.failed, "total": self.total,
                "log": self.log[-60:], "error": self.error, "files": self.files, "output_dir": self.output_dir, "waiting": self.waiting}


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

    # -- 창 고르기: 드롭샷 창만 (우리 프로그램 창 제목에도 '영상'이 들어 있어 키워드만으로 고르면 엉뚱한 창을 누른다)
    OWN_TITLES = ("유튜브 영상 자동 제작", "auto image placer")

    @classmethod
    def _title_ok(cls, title: str, keyword: str) -> bool:
        t = (title or "").lower()
        if not t or any(o.lower() in t for o in cls.OWN_TITLES):
            return False
        if "드롭샷" not in t and "dropshot" not in t:
            return False
        return (not keyword) or keyword.lower() in t or keyword.lower() in ("드롭샷", "dropshot", "영상")

    # -- 화면 조작 (테스트에서 바꿔 끼울 수 있게 메서드로 둔다)
    def _focus_window(self, keyword: str) -> bool:
        try:
            import pygetwindow as gw
            wins = [w for w in gw.getAllWindows() if self._title_ok(w.title, keyword)]
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

    # -- 드롭샷 영상 창의 접근성 트리 (Chrome UIA) 로 버튼 찾기
    def _video_controls(self, keyword: str):
        try:
            from pywinauto import Desktop
            wins = [w for w in Desktop(backend="uia").windows() if self._title_ok(w.window_text(), keyword)]
            if not wins:
                return None, []
            return wins[0], wins[0].descendants()
        except Exception:  # noqa: BLE001
            return None, []

    @staticmethod
    def _ctrl_info(c):
        try:
            return (c.window_text() or "").strip(), c.rectangle(), c.element_info.control_type
        except Exception:  # noqa: BLE001
            return None

    def _page_items(self, keyword: str):
        """드롭샷 창의 웹 페이지 영역 안에 있는 컨트롤 [(이름, 사각형, 종류)]. 크롬 주소창·도구막대는 뺀다."""
        sw, sh = pyautogui.size()
        _win, controls = self._video_controls(keyword)
        infos = [i for i in (self._ctrl_info(c) for c in controls) if i]
        docs = [r for (n, r, t) in infos if t == "Document" and r.width() > 300 and r.height() > 200]

        def in_page(r):
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            return 0 <= cx < sw and 0 <= cy < sh and ((not docs) or any(d.left <= cx <= d.right and d.top <= cy <= d.bottom for d in docs))

        return [(n, r, t) for (n, r, t) in infos if r.width() > 0 and in_page(r)]

    @staticmethod
    def _center(r):
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    @staticmethod
    def _nearest(rects, near):
        if near and any(near):
            return min(rects, key=lambda r: abs((r.left + r.right) // 2 - near[0]) + abs((r.top + r.bottom) // 2 - near[1]))
        return min(rects, key=lambda r: r.top)                 # 저장 좌표가 없으면 가장 위(최신 결과)

    def find_generate_button(self, s: VideoSettings):
        """'영상 생성하기' 버튼 — 입력창이 커지거나 이미지를 올리면 아래로 밀리므로 매번 찾는다."""
        items = self._page_items(s.window_keyword)
        cands = [r for (n, r, t) in items if t == "Button" and ("생성하기" in n or n.startswith("영상 생성")) and r.width() > 100]
        if cands:
            return self._center(max(cands, key=lambda r: r.width()))
        try:                                                   # 이름을 못 읽는 버전: 이미지 생성과 같은 파란 버튼 색으로 찾는다
            from core.imagegen import find_button_by_color
            import pygetwindow as gw
            wins = [w for w in gw.getAllWindows() if self._title_ok(w.title, s.window_keyword)]
            if wins:
                w = wins[0]; sw, sh = pyautogui.size()
                region = (max(0, w.left), max(0, w.top), min(sw, w.left + w.width) - max(0, w.left), min(sh, w.top + w.height) - max(0, w.top))
                found = find_button_by_color(region)
                if found:
                    return tuple(found)
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_prompt_input(self, s: VideoSettings):
        """움직임 프롬프트 입력창 — 페이지 안의 넓은 입력 칸(Edit) 중 생성 버튼 바로 위의 것."""
        items = self._page_items(s.window_keyword)
        gen = self.find_generate_button(s)
        edits = [r for (n, r, t) in items if t in ("Edit", "Document") and r.width() > 250 and 20 <= r.height() <= 600 and (not gen or r.top < gen[1])]
        if not edits:
            return None
        r = max(edits, key=lambda r: r.bottom) if gen else max(edits, key=lambda r: r.width())
        return self._center(r)

    def find_download_button(self, s: VideoSettings, near=None):
        """결과 카드의 다운로드 버튼. 드롭샷은 이름 없는 아이콘 버튼('icon')을 ♥ 즐겨찾기 버튼 바로 오른쪽에 둔다.
        이름에 '다운로드'가 있으면 그것을, 없으면 즐겨찾기 옆 아이콘을 쓴다. 여러 결과가 있으면 저장 좌표에 가까운 것(없으면 맨 위)."""
        items = self._page_items(s.window_keyword)
        if not items:
            return None
        named = [r for (n, r, t) in items if t in ("Button", "Hyperlink") and any(k in n.lower() for k in ("다운로드", "download"))]
        if named:
            return self._center(self._nearest(named, near))
        favs = [r for (n, r, t) in items if t == "Button" and "즐겨찾기" in n]
        small = [r for (n, r, t) in items if t == "Button" and 16 <= r.width() <= 60 and 16 <= r.height() <= 60 and "즐겨찾기" not in n]
        beside = []
        for f in favs:
            for r in small:
                if abs(r.top - f.top) <= 6 and 0 <= r.left - f.right <= 24:      # 같은 줄, 바로 오른쪽
                    beside.append(r)
        if beside:
            return self._center(self._nearest(beside, near))
        return None

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
        pxy = self.find_prompt_input(s) or tuple(s.prompt_xy)
        self._click(pxy)
        time.sleep(0.3)
        self._hotkey(s.clear_key)
        self._press("backspace")
        time.sleep(0.2)
        self._paste(prompt)
        time.sleep(0.8)
        # 3) 생성 → 최소 대기 → 다운로드를 주기적으로 시도
        before = self._snapshot(dl)
        gxy = self.find_generate_button(s)
        self._click(gxy or tuple(s.generate_xy))
        st.add(f"{no:03d} 생성 클릭 ({'버튼 자동 감지 ' + str(gxy) if gxy else '저장된 좌표 ' + str(tuple(s.generate_xy))}) → {s.wait_min:.0f}초 뒤부터 다운로드를 시도 (최대 {s.wait_max:.0f}초)")
        if not self._wait(max(float(s.wait_min), 5.0)):
            return None
        deadline = time.time() + max(float(s.wait_max), float(s.wait_min) + 30)
        f = None
        attempt = 0
        if s.manual_download:                          # 사람이 다운로드를 누른다 → 새 영상 파일이 생길 때까지만 기다린다
            st.waiting = f"{no:03d}번 영상이 완성되면 드롭샷에서 ⬇ 다운로드 버튼을 눌러 주세요 (최대 {int((deadline - time.time()) // 60)}분)"
            st.add(f"{no:03d} 다운로드는 직접 눌러 주세요 — 다운로드 폴더에 새 영상이 생기면 가져갑니다")
            while time.time() < deadline and not self._stop.is_set():
                f = self._wait_new_video(dl, before, 5.0)
                if f:
                    h = _md5(f)
                    if h and h == getattr(self, "_last_hash", None):
                        st.add(f"{no:03d} 받은 영상이 직전 장면과 같음 → 새 영상을 눌러 주세요")
                        try:
                            f.unlink()
                        except OSError:
                            pass
                        before = self._snapshot(dl); f = None
                        continue
                    self._last_hash = h
                    break
            st.waiting = ""
            if not f:
                return None
            dest = out / f"{no:03d}.mp4"
            for _ in range(10):
                try:
                    shutil.move(str(f), str(dest)); break
                except PermissionError:
                    time.sleep(0.5)
            return dest if dest.exists() else None
        while time.time() < deadline and not self._stop.is_set():
            attempt += 1
            self._focus_window(s.window_keyword)
            before = self._snapshot(dl)
            xy = self.find_download_button(s, tuple(s.download_xy) if any(s.download_xy) else None) or tuple(s.download_xy)
            if xy != getattr(self, "_last_dl_xy", None):
                st.add(f"{no:03d} 다운로드 버튼 {'자동 감지' if xy != tuple(s.download_xy) else '저장된 좌표'} → X={xy[0]}, Y={xy[1]}")
                self._last_dl_xy = xy
            self._click(xy)
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
    manual_download: bool = True


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
        skip_existing=req.skip_existing, manual_download=req.manual_download,
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


class VideoFindDownload(BaseModel):
    window_keyword: str = "영상"
    near: list[int] = []


@app.post("/api/vgen/find_download")
def api_vgen_find_download(req: VideoFindDownload):
    """설정 화면용: 드롭샷 영상 창에서 '다운로드' 버튼을 접근성 트리로 찾아 좌표를 돌려준다 (마우스를 결과 위로 옮길 수 있음)."""
    r = videogen.runner
    if r.busy():
        raise HTTPException(400, "영상 변환이 돌아가는 중입니다.")
    if req.window_keyword and not r._focus_window(req.window_keyword):
        raise HTTPException(400, f"제목에 '{req.window_keyword}'이(가) 들어간 창을 찾지 못했습니다. 드롭샷 [영상 생성] 화면을 별도 창으로 열어 두세요.")
    s = videogen.VideoSettings(images_dir="", download_dir="", scenes=[], prompts={}, upload_xy=(0, 0), prompt_xy=(0, 0), generate_xy=(0, 0),
                               download_xy=tuple(req.near) if len(req.near) == 2 else (0, 0), window_keyword=req.window_keyword)
    xy = r.find_download_button(s, tuple(req.near) if len(req.near) == 2 else None)
    if not xy:
        _w, controls = r._video_controls(req.window_keyword)
        names = sorted({(r._ctrl_info(c) or ("",))[0] for c in controls if (r._ctrl_info(c) or ("", None, ""))[2] == "Button"})[:40]
        raise HTTPException(400, "영상 창에서 '다운로드' 버튼을 찾지 못했습니다. 영상이 하나 이상 만들어져 있어야 합니다. (보이는 버튼: " + ", ".join(n for n in names if n)[:300] + ")")
    return {"x": xy[0], "y": xy[1]}


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
    if "/api/vgen/start" in text:                          # 먼저 붙인 버전에 빠진 경로 블록만 더한다
        for start_marker, route in (("class VideoUploadTest", "/api/vgen/upload_test"), ("class VideoFindDownload", "/api/vgen/find_download")):
            if route in text:
                continue
            a = APP_ADDITION.index(start_marker)
            ends = [APP_ADDITION.find(m, a + 1) for m in ("class VideoUploadTest", "class VideoFindDownload", '@app.get("/api/vgen/status")')]
            b = min(k for k in ends if k > 0)                  # 그 블록의 끝 = 다음 정의가 시작하는 곳
            block = APP_ADDITION[a:b]
            text = text.replace('@app.get("/api/vgen/status")', block + '@app.get("/api/vgen/status")', 1)
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
