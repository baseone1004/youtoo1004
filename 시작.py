# -*- coding: utf-8 -*-
"""초보자용 실행기: 필요한 구성만 설치하고 두 로컬 서버를 연다."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
PACKAGES = {
    "yt_dlp": "yt-dlp", "openai": "openai", "ddgs": "ddgs",
    "anthropic": "anthropic", "requests": "requests", "fastapi": "fastapi",
    "uvicorn": "uvicorn", "PIL": "pillow", "pyautogui": "pyautogui",
    "pyperclip": "pyperclip",
}


def find_editor():
    override = os.environ.get("YOUTUBE_EDITOR_DIR", "")
    roots = [Path(override)] if override else []
    roots += [HERE / "편집프로그램", HERE.parent / "편집프로그램",
              Path.home() / "Downloads" / "편집프로그램",
              Path.home() / "Desktop" / "편집프로그램"]
    return next((p.resolve() for p in roots if (p / "app.py").is_file()), None)


def ready(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
        return True
    except Exception:
        return False


def open_log(name):
    path = HERE / name
    try:
        return path.open("w", encoding="utf-8")
    except PermissionError:
        path = HERE / f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}"
        return path.open("w", encoding="utf-8")


def find_chrome():
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    found = next((p for p in candidates if p.is_file()), None)
    return str(found) if found else shutil.which("chrome.exe")


def open_chrome(chrome, url):
    subprocess.Popen([chrome, "--new-window", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    background = "--background" in sys.argv
    if background:
        output = (HERE / "로그_시작.txt").open("a", encoding="utf-8")
        sys.stdout = sys.stderr = output
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome을 찾지 못했습니다. Chrome 설치 후 다시 실행하세요.")
    missing = [package for module, package in PACKAGES.items()
               if importlib.util.find_spec(module) is None]
    if missing:
        print("필요한 프로그램을 처음 한 번 설치합니다:", ", ".join(missing), flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)

    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    children, logs = [], []
    editor = find_editor()
    if editor:
        try:
            from 편집프로그램_UI_연결 import apply as apply_editor_ui
            apply_editor_ui(editor)
            from 편집프로그램_KIE_연결 import apply as apply_editor_kie
            apply_editor_kie(editor)
            from 편집프로그램_영상_연결 import apply as apply_editor_video
            apply_editor_video(editor)
            from 편집프로그램_글꼴_연결 import apply as apply_editor_fonts
            apply_editor_fonts(editor)
            from 편집프로그램_렌더_보호 import apply as apply_editor_render_guard
            apply_editor_render_guard(editor)
            from 편집프로그램_AI표시_연결 import apply as apply_editor_ai_notice
            apply_editor_ai_notice(editor)
        except (OSError, ValueError) as exc:
            print("편집프로그램 연결 설정을 확인하세요:", exc)
    if editor and not ready(8765):
        log = open_log("로그_편집프로그램.txt")
        logs.append(log)
        children.append(subprocess.Popen([sys.executable, "app.py", "--no-browser"],
                                         cwd=editor, env=env, stdout=log, stderr=subprocess.STDOUT))
    elif not editor:
        print("편집프로그램 폴더를 찾지 못했습니다. 영상 편집은 사용할 수 없습니다.")
        print("다른 위치에 있다면 YOUTUBE_EDITOR_DIR 환경 변수에 폴더 경로를 지정하세요.")

    script = None
    if not ready(8766):
        log = open_log("로그_대본선택.txt")
        logs.append(log)
        script = subprocess.Popen([sys.executable, "대본선택.py", "--no-browser"],
                                  cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
        children.append(script)
    try:
        for _ in range(120):
            if ready(8766):
                break
            if script is not None and script.poll() is not None:
                raise RuntimeError("대본 만들기 실행에 실패했습니다. 로그_대본선택.txt를 확인하세요.")
            time.sleep(1)
        else:
            raise RuntimeError("대본 만들기가 2분 안에 준비되지 않았습니다. 로그_대본선택.txt를 확인하세요.")
        open_chrome(chrome, "http://127.0.0.1:8766/")
        try:
            config = json.loads((HERE / "설정.json").read_text(encoding="utf-8"))
            if config.get("AI") == "deepseek-web":
                open_chrome(chrome, "https://chat.deepseek.com/")
        except (OSError, ValueError):
            pass
        print("대본 만들기 화면이 열렸습니다: http://127.0.0.1:8766/")
        if editor:
            print("편집프로그램 준비:", "완료" if ready(8765) else "시작 중")
        if not background:
            print("작업 중에는 이 창을 열어 두세요. 종료하려면 Enter를 누르세요.")
            try:
                input()
            except EOFError:
                pass
    finally:
        if not background:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
        for handle in logs:
            handle.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print("실행 실패:", exc, file=sys.stderr)
        sys.exit(1)
