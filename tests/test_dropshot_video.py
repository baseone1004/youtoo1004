# -*- coding: utf-8 -*-
"""드롭샷 좌표 영상 변환 패치: 두 번 적용해도 같고, 화면 조작을 흉내 낸 상태에서 장면마다 NNN.mp4 가 만들어진다."""
import importlib
import py_compile
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from 편집프로그램_드롭샷영상_연결 import apply

IMAGEGEN_STUB = '''IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif"}
def _md5(p):
    try:
        return __import__("hashlib").md5(open(p, "rb").read()).hexdigest()
    except OSError:
        return ""
class _R:
    class state: status = "idle"
runner = _R()
'''
APP_STUB = '''from pathlib import Path
from pydantic import BaseModel
from core import imagegen
class HTTPException(Exception):
    def __init__(self, code, detail): super().__init__(detail); self.code = code; self.detail = detail
class _App:
    def post(self, *_a, **_k): return lambda f: f
    def get(self, *_a, **_k): return lambda f: f
app = _App()
def load_config(): return {}
def save_config(d): pass
@app.post("/api/hook/start")
def api_hook_start(req): pass
def main(): pass
if __name__ == "__main__":
    main()
'''


class DropshotVideoPatchTest(unittest.TestCase):
    def test_apply_and_simulated_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "core").mkdir()
            (root / "core" / "__init__.py").write_text("", encoding="utf-8")
            (root / "core" / "imagegen.py").write_text(IMAGEGEN_STUB, encoding="utf-8")
            (root / "app.py").write_text(APP_STUB, encoding="utf-8")
            self.assertTrue(apply(root)); self.assertFalse(apply(root))
            py_compile.compile(str(root / "core" / "videogen.py"), doraise=True)
            py_compile.compile(str(root / "app.py"), doraise=True)
            app_text = (root / "app.py").read_text(encoding="utf-8")
            self.assertLess(app_text.index("/api/vgen/start"), app_text.index('if __name__ == "__main__":'))   # main() 앞에 등록돼야 한다

            sys.path.insert(0, str(root))
            for name in ("core", "core.imagegen", "core.videogen", "app"):
                sys.modules.pop(name, None)
            try:
                vg = importlib.import_module("core.videogen")
                images = root / "img"; images.mkdir(); dl = root / "dl"; dl.mkdir()
                for n in (1, 2, 3):
                    (images / f"{n:03d}.jpg").write_bytes(b"img%d" % n)
                (images / "002.mp4").write_bytes(b"already")          # 2번은 이미 있음 → 건너뜀
                actions = []; downloads = {"n": 0}
                r = vg.runner

                def click(xy):
                    actions.append(("click", tuple(xy)))
                    if tuple(xy) == (4, 4):                            # 다운로드 버튼: 첫 시도는 직전 영상(중복), 그다음 새 영상
                        downloads["n"] += 1
                        body = b"already" if downloads["n"] == 1 else b"video-%d" % downloads["n"]   # 첫 시도: 직전(기존 002) 영상이 또 받힘
                        threading.Timer(0.2, lambda b=body: (dl / f"dl{downloads['n']}.mp4").write_bytes(b)).start()
                r._click = click
                r._paste = lambda text: actions.append(("paste", text))
                r._press = lambda key: actions.append(("press", key))
                r._hotkey = lambda combo: actions.append(("hotkey", combo))
                r._focus_window = lambda kw: True

                s = vg.VideoSettings(images_dir=str(images), download_dir=str(dl), scenes=[1, 2, 3, 9],
                                     prompts={1: "scene one"}, upload_xy=(1, 1), prompt_xy=(2, 2), generate_xy=(3, 3), download_xy=(4, 4),
                                     wait_upload=0.05, wait_min=0.05, wait_max=6, poll_every=0.05, wait_download=2, wait_next=0)
                r._wait = lambda sec: not r._stop.is_set()
                r.start(s); r._thread.join(20)
                st = r.state
                self.assertEqual(st.status, "done", st.log)
                self.assertEqual(sorted(st.done), [1, 2, 3])
                self.assertEqual(st.failed, [9])                                   # 이미지 없는 장면
                self.assertEqual((images / "002.mp4").read_bytes(), b"already")    # 있는 영상은 그대로
                self.assertTrue((images / "001.mp4").exists() and (images / "003.mp4").exists())
                self.assertNotEqual((images / "001.mp4").read_bytes(), b"already")      # 중복 영상은 버리고 새것을 받음
                pastes = [a[1] for a in actions if a[0] == "paste"]
                self.assertEqual(pastes[0], str(images / "001.jpg"))                # 파일 대화상자에 이미지 경로
                self.assertTrue(pastes[1].startswith("scene one ") and "Cinematic" in pastes[1])
                self.assertIn(("press", "enter"), actions)
                self.assertEqual(st.to_dict()["output_dir"], str(images))
                # 다운로드 버튼이 이미지를 받아 오면(이미지 화면을 누르고 있음) 바로 멈추고 이유를 남긴다
                (images / "004.jpg").write_bytes(b"img4")
                r._click = lambda xy: (xy == (4, 4)) and threading.Timer(0.2, lambda: (dl / "dup.jpg").write_bytes(b"image")).start()
                r.start(vg.VideoSettings(images_dir=str(images), download_dir=str(dl), scenes=[4], prompts={}, upload_xy=(1, 1), prompt_xy=(2, 2), generate_xy=(3, 3), download_xy=(4, 4),
                                         wait_upload=0.05, wait_min=0.05, wait_max=6, poll_every=0.05, wait_download=2, wait_next=0))
                r._thread.join(20)
                self.assertEqual(r.state.status, "error", r.state.log)
                self.assertIn("이미지를 받아 왔습니다", r.state.error)
                self.assertFalse((dl / "dup.jpg").exists())                        # 우리가 받은 중복 이미지는 지운다
            finally:
                sys.path.remove(str(root))
                for name in ("core", "core.imagegen", "core.videogen", "app"):
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
