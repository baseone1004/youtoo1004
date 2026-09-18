# -*- coding: utf-8 -*-
"""생성 중 [다시 만들기] 예약 패치: 두 번 적용해도 같고, 패치된 파일이 컴파일되며, 예약 장면은 남은 장면 뒤에 만들어진다."""
import py_compile
import tempfile
import unittest
from pathlib import Path

from 편집프로그램_다시만들기_예약_연결 import apply

IMAGEGEN = '''import time
from dataclasses import dataclass, field
from pathlib import Path
import threading

IMAGE_EXTS = {".png", ".jpg"}

@dataclass
class Scene:
    no: int
    prompt: str

def parse_prompts(path):
    return [Scene(int(l.split(".")[0]), l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]

@dataclass
class GenState:
    status: str = "idle"
    current: int = 0
    done: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    total: int = 0
    log: list[str] = field(default_factory=list)
    error: str = ""
    files: dict[int, str] = field(default_factory=dict)

    def add(self, s):
        self.log.append(s)

    def to_dict(self):
        return {"status": self.status, "current": self.current, "done": self.done[-500:], "failed": self.failed,
                "total": self.total, "log": self.log[-60:], "error": self.error, "files": self.files}

class Runner:
    def __init__(self):
        self.state = GenState()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self, s):
        self.state = GenState()
        self._pause.clear(); self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(s,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _wait(self, sec):
        return not self._stop.is_set()

    def _do_scene(self, s, sc, out, dl):
        time.sleep(0.03)
        dest = out / f"{sc.no:03d}.jpg"; dest.write_text("new"); return dest

    def _run(self, s):
        st = self.state
        try:
            scenes = parse_prompts(s.prompts_file)
            if s.end_no:
                scenes = [x for x in scenes if x.no <= s.end_no]
            scenes = [x for x in scenes if x.no >= s.start_no]
            out = Path(s.output_dir); out.mkdir(parents=True, exist_ok=True)
            dl = Path(s.download_dir)
            st.total = len(scenes); st.status = "running"
            for sc in scenes:
                if self._stop.is_set():
                    break
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.3)
                st.current = sc.no
                exists = [p for p in out.glob(f"{sc.no:03d}.*") if p.suffix.lower() in IMAGE_EXTS]
                if s.skip_existing and exists:
                    st.done.append(sc.no); st.files[sc.no] = str(exists[0]); continue
                dest = self._do_scene(s, sc, out, dl)
                if dest:
                    st.done.append(sc.no); st.files[sc.no] = str(dest)
                else:
                    st.failed.append(sc.no)
                if not self._wait(s.wait_next):
                    break
            st.status = "stopped" if self._stop.is_set() else "done"
        except Exception as e:
            st.status = "error"; st.error = str(e)

@dataclass
class GenSettings:
    prompts_file: str
    output_dir: str
    download_dir: str
    wait_next: float = 0
    start_no: int = 1
    end_no: int = 0
    skip_existing: bool = True

runner = Runner()
'''

APP = '''from pathlib import Path
import time
from core import imagegen

class HTTPException(Exception):
    def __init__(self, code, detail):
        super().__init__(detail); self.code = code; self.detail = detail

def api_gen_regen(req):
    st = imagegen.runner.state
    if st.status in ("running", "paused"):
        raise HTTPException(400, "지금 생성 중입니다. 끝나거나 중단한 뒤 다시 누르세요.")
    out = Path(req.output_dir)
    return {"ok": True, "moved": []}
'''


class RegenQueuePatchTest(unittest.TestCase):
    def make_editor(self, root: Path) -> None:
        (root / "core").mkdir()
        (root / "core" / "__init__.py").write_text("", encoding="utf-8")
        (root / "core" / "imagegen.py").write_text(IMAGEGEN, encoding="utf-8")
        (root / "app.py").write_text(APP, encoding="utf-8")

    def test_apply_twice_and_queue_order(self):
        import importlib, sys, time, types
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_editor(root)
            self.assertTrue(apply(root))
            self.assertFalse(apply(root))                     # 두 번째는 바꿀 것이 없다
            py_compile.compile(str(root / "core" / "imagegen.py"), doraise=True)
            py_compile.compile(str(root / "app.py"), doraise=True)

            sys.path.insert(0, str(root))
            for name in ("core", "core.imagegen", "app"):
                sys.modules.pop(name, None)
            try:
                imagegen = importlib.import_module("core.imagegen")
                app = importlib.import_module("app")
                prompts = root / "p.txt"; prompts.write_text("1. a\n2. b\n3. c\n", encoding="utf-8")
                out = root / "img"; out.mkdir(); (out / "001.jpg").write_text("old")
                order = []
                real = imagegen.Runner._do_scene
                imagegen.Runner._do_scene = lambda self, s, sc, o, d: (order.append(sc.no), real(self, s, sc, o, d))[1]
                s = imagegen.GenSettings(prompts_file=str(prompts), output_dir=str(out), download_dir=str(root))
                imagegen.runner.start(s)
                while imagegen.runner.state.status != "running":
                    time.sleep(0.005)
                req = types.SimpleNamespace(scene=1, output_dir=str(out))
                self.assertEqual(app.api_gen_regen(req), {"ok": True, "queued": True, "moved": []})
                with self.assertRaises(app.HTTPException):    # 같은 장면 두 번 예약은 거절
                    app.api_gen_regen(req)
                with self.assertRaises(app.HTTPException):    # 다른 폴더는 거절
                    app.api_gen_regen(types.SimpleNamespace(scene=2, output_dir=str(root / "other")))
                imagegen.runner._thread.join(5)
                st = imagegen.runner.state
                self.assertEqual(st.status, "done")
                self.assertEqual(order, [2, 3, 1])                # 남은 장면(2,3) 뒤에 예약된 1
                self.assertEqual(sorted(st.done), [1, 2, 3])
                self.assertEqual(st.to_dict()["queued"], [])
                self.assertEqual((out / "001.jpg").read_text(), "new")
                self.assertTrue(list((out / "이전").glob("001_*.jpg")))
                # 생성이 끝난 뒤에는 예전처럼 바로 다시 만든다
                self.assertEqual(app.api_gen_regen(req)["ok"], True)
            finally:
                sys.path.remove(str(root))
                for name in ("core", "core.imagegen", "app"):
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
