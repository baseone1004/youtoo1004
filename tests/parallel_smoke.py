"""Opt-in local two-process smoke check; replaces generation with a local stub.

python tests/parallel_smoke.py [--hold]
Requires the installed editor. No KIE/Inworld/AI generation is called.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import channel_workspaces as workspaces
from 시작 import find_editor


def request(url, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def wait_job(url, expected):
    for _ in range(100):
        job = request(url, "/api/job")
        if job["status"] == expected:
            return
        if job["status"] == "error" and expected != "error":
            raise AssertionError(job.get("error"))
        time.sleep(0.1)
    raise AssertionError(f"job did not reach {expected}")


def main():
    installed = find_editor()
    assert installed, "installed editor required"
    children = []
    original_launch = workspaces.launch_process
    with tempfile.TemporaryDirectory(prefix="youtoo_parallel_smoke_") as td:
        home = Path(td)
        for source in ROOT.glob("*.py"):
            shutil.copy2(source, home / source.name)
        for name in ("화면", "지침"):
            shutil.copytree(ROOT / name, home / name)
        fixture_editor = home / "편집프로그램"
        fixture_editor.mkdir()
        workspaces.copy_resources(installed, fixture_editor, ["app.py", "core", "static", "capcut_template", "user_fonts"])
        workspaces.write_json(fixture_editor / "config.json", {})
        profiles = [{"id": lang, "name": name, "url": "", "language": lang, "api_key": "",
                     "profile": {"이름": name, "언어": lang, "마스코트": {"이미지": ""}, "썸네일": {"레이아웃": layout}}}
                    for lang, name, layout in [("ko", "마음 · 동시작업 검증", "jalnan_pop"), ("ja", "日本 · 同時作業テスト", "jp_cozy")]]
        workspaces.write_json(home / "설정.json", {"AI": "deepseek-web", "유튜브_선택_person": "main-fixture", "유튜브_계정": profiles, "온보딩_완료": True})
        workspaces.write_json(home / ".editor-port.json", {"port": 18998})
        workspaces.write_json(home / "채널_프로필.json", {"person": profiles[0]["profile"]})
        source_path = home / "대본선택.py"
        source = source_path.read_text(encoding="utf-8")
        stub = '''
def smoke_script(job, body):
    job.stage = "무료 모의 제작 검증 중"
    while not Path(".finish-test").exists():
        if job.cancel_requested:
            raise RuntimeError("취소됨")
        time.sleep(0.05)
    folder = Path("대본") / ("일본어" if 채널_프로필.language_code("person") == "ja" else "한국어")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "동시검증.txt"
    output.write_text(채널_프로필.name("person"), encoding="utf-8")
    return {"script": str(output)}

make_person_script = smoke_script
채널_연동.fetch_in_background = lambda *a, **kw: False
'''
        assert 'if __name__ == "__main__":' in source
        source_path.write_text(source.replace('if __name__ == "__main__":', stub + '\nif __name__ == "__main__":'), encoding="utf-8")

        def track(*args, **kwargs):
            child = original_launch(*args, **kwargs)
            children.append(child)
            return child
        try:
            with patch.object(workspaces, "HOME", home), patch.object(workspaces, "launch_process", side_effect=track), patch.dict(os.environ, {"KIE_API_KEY": "", "DEEPSEEK_API_KEY": "", "YOUTUBE_EDITOR_DIR": str(fixture_editor)}):
                ko = workspaces.launch("ko")
                ja = workspaces.launch("ja")
                assert ko["url"] != ja["url"]
                assert workspaces.launch("ko")["url"] == ko["url"]
                assert len(children) == 4
                request(ko["url"], "/api/script", {})
                request(ja["url"], "/api/script", {})
                assert request(ko["url"], "/api/job")["status"] == "running"
                assert request(ja["url"], "/api/job")["status"] == "running"
                request(ko["url"], "/api/stop-all", {})
                wait_job(ko["url"], "error")
                assert request(ja["url"], "/api/job")["status"] == "running"
                (Path(ja["folder"]) / ".finish-test").touch()
                (Path(ko["folder"]) / ".finish-test").touch()
                request(ko["url"], "/api/script", {})
                wait_job(ko["url"], "done")
                wait_job(ja["url"], "done")
                assert (Path(ko["folder"]) / "대본" / "한국어" / "동시검증.txt").is_file()
                assert (Path(ja["folder"]) / "대본" / "일본어" / "동시검증.txt").is_file()
                assert not (Path(ko["folder"]) / "대본" / "일본어").exists()
                print(json.dumps({"ok": True, "ko": ko["url"], "ja": ja["url"], "fixture": str(home), "checks": "two concurrent jobs; scoped stop; resume; separate outputs; process reuse; no paid generation"}, ensure_ascii=False), flush=True)
                if "--hold" in sys.argv:
                    while not (home / ".close-preview").exists():
                        time.sleep(0.5)
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=15)


if __name__ == "__main__":
    main()
