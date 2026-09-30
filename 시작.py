# -*- coding: utf-8 -*-
"""초보자용 실행기: 필요한 구성만 설치하고 두 로컬 서버를 연다."""
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
PACKAGES = {
    "yt_dlp": ("yt-dlp", "2026.8.19"), "openai": ("openai", "3.13.0"), "ddgs": ("ddgs", "9.16.0"),
    "anthropic": ("anthropic", "1.5.0"), "requests": ("requests", "2.34.2"), "fastapi": ("fastapi", "0.141.1"),
    "uvicorn": ("uvicorn", "0.52.4"), "PIL": ("pillow", "12.3.0"), "pyautogui": ("pyautogui", "0.9.54"),
    "pyperclip": ("pyperclip", "1.11.0"),
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
    """로그는 이어 쓴다 (다시 켤 때 지난 오류가 사라지지 않도록). 3MB 를 넘으면 앞부분을 버리고 뒤 1MB 만 남긴다."""
    path = HERE / name
    try:
        if path.is_file() and path.stat().st_size > 3_000_000:
            tail = path.read_bytes()[-1_000_000:]
            path.write_bytes(tail[tail.find(b"\n") + 1:])
    except OSError:
        pass
    try:
        f = path.open("a", encoding="utf-8")
    except PermissionError:
        path = HERE / f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}"
        f = path.open("a", encoding="utf-8")
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 시작 =====\n"); f.flush()
    return f


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


def apply_editor_integrations(editor):
    """편집기 연결 패치를 전부 적용·검증하고, 하나라도 실패하면 원상 복구한다."""
    from 편집프로그램_UI_연결 import apply as apply_editor_ui
    from 편집프로그램_KIE_연결 import apply as apply_editor_kie
    from 편집프로그램_영상_연결 import apply as apply_editor_video
    from 편집프로그램_글꼴_연결 import apply as apply_editor_fonts
    from 편집프로그램_렌더_보호 import apply as apply_editor_render_guard
    from 편집프로그램_AI표시_연결 import apply as apply_editor_ai_notice
    from 편집프로그램_드롭샷자동좌표_연결 import apply as apply_dropshot_autoxy
    from 편집프로그램_다시만들기_예약_연결 import apply as apply_regen_queue
    from 편집프로그램_드롭샷영상_연결 import apply as apply_dropshot_video
    from 편집프로그램_자막두께_연결 import apply as apply_subtitle_weight

    editor = Path(editor)
    patchers = (apply_editor_ui, apply_editor_kie, apply_editor_video, apply_editor_fonts,
                apply_editor_render_guard, apply_editor_ai_notice, apply_dropshot_autoxy,
                apply_regen_queue, apply_dropshot_video, apply_subtitle_weight)
    suffixes = {".py", ".html", ".js", ".css"}
    originals = {p.relative_to(editor) for p in editor.rglob("*") if p.is_file() and p.suffix.lower() in suffixes}
    import tempfile
    with tempfile.TemporaryDirectory(prefix="editor_patch_backup_") as td:
        backup = Path(td)
        for rel in originals:
            target = backup / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(editor / rel, target)
        try:
            for patcher in patchers:
                patcher(editor)
            for source in editor.rglob("*.py"):
                py_compile.compile(str(source), doraise=True)
        except Exception:
            current = {p.relative_to(editor) for p in editor.rglob("*") if p.is_file() and p.suffix.lower() in suffixes}
            for rel in current - originals:
                (editor / rel).unlink(missing_ok=True)
            for rel in originals:
                target = editor / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup / rel, target)
            raise


DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def main():
    background = "--background" in sys.argv
    if background:
        output = (HERE / "로그_시작.txt").open("a", encoding="utf-8")
        sys.stdout = sys.stderr = output
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome을 찾지 못했습니다. Chrome 설치 후 다시 실행하세요.")
    missing = []
    for module, (package, version) in PACKAGES.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = ""
        if importlib.util.find_spec(module) is None or installed != version:
            missing.append(f"{package}=={version}")
    if missing:
        print("필요한 프로그램을 처음 한 번 설치합니다:", ", ".join(missing), flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)

    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    children, logs = [], []
    editor = find_editor()
    editor_integrations_ok = True
    if editor:
        try:
            apply_editor_integrations(editor)
        except Exception as exc:  # noqa: BLE001
            editor_integrations_ok = False
            print("편집프로그램 연결 적용에 실패해 원상 복구했습니다:", exc)
    if editor and editor_integrations_ok and not ready(8765):
        log = open_log("로그_편집프로그램.txt")
        logs.append(log)
        children.append(subprocess.Popen([sys.executable, "app.py", "--no-browser"],
                                         cwd=editor, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=DETACHED))
    elif not editor:
        print("편집프로그램 폴더를 찾지 못했습니다. 영상 편집은 사용할 수 없습니다.")
        print("다른 위치에 있다면 YOUTUBE_EDITOR_DIR 환경 변수에 폴더 경로를 지정하세요.")

    script = None
    if not ready(8766):
        log = open_log("로그_대본선택.txt")
        logs.append(log)
        script = subprocess.Popen([sys.executable, "대본선택.py", "--no-browser"],
                                  cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=DETACHED)
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
        # 바로가기를 다시 눌렀을 때 이미 열린 예전 탭 대신 최신 화면을 확실히 요청한다.
        open_chrome(chrome, f"http://127.0.0.1:8766/?v={int((HERE / '대본선택.py').stat().st_mtime)}")
        try:
            config = json.loads((HERE / "설정.json").read_text(encoding="utf-8"))
            if config.get("AI") == "deepseek-web":
                open_chrome(chrome, "https://chat.deepseek.com/")
        except (OSError, ValueError):
            pass
        print("대본 만들기 화면이 열렸습니다: http://127.0.0.1:8766/")
        if editor and editor_integrations_ok:
            print("편집프로그램 준비:", "완료" if ready(8765) else "시작 중")
        if not background:
            print("이 창은 닫아도 됩니다 — 프로그램은 계속 돌아갑니다. 완전히 끝내려면 화면의 [종료] 버튼을 누르세요. (Enter: 이 창만 닫기)")
            try:
                input()
            except EOFError:
                pass
    finally:
        # 이 창을 닫아도 편집프로그램·대본선택은 살아 있어야 한다 (렌더·이미지 생성 중에 창을 닫아 죽는 일이 잦았다). 끝내는 건 화면의 [종료] 버튼.
        for handle in logs:
            handle.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print("실행 실패:", exc, file=sys.stderr)
        sys.exit(1)
