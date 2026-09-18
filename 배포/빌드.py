# -*- coding: utf-8 -*-
"""배포판 만들기 — 파이썬 설치 없이 압축만 풀면 되는 폴더(+zip)를 만든다.

    python 배포/빌드.py                # 배포판/유튜브영상자동제작/ + zip
    python 배포/빌드.py --no-zip       # 폴더만
    python 배포/빌드.py --editor "D:\\편집프로그램"   # 편집프로그램 위치를 직접 지정

만들어지는 구조:
    유튜브영상자동제작/
      시작.bat                 ← 더블클릭 (검은 창이 열려 있는 동안 동작)
      시작(창없이).vbs          ← 창 없이 실행
      설치_안내.txt
      python/                  ← 내장 파이썬 (python.org 임베디드판 + 필요한 패키지)
      app/                     ← 이 프로그램 + 편집프로그램 + ffmpeg
        시작.py 대본선택.py … 화면/ 지침/ assets/ 딥시크_확장/
        편집프로그램/ (app.py core/ static/ … bin/ffmpeg.exe)
        설정.json (빈 값)  채널_프로필.json 없음 → 기본값
개인 자료(설정.json 의 키, 대본/, 업로드/, 채널_프로필.json, 계획·후보 json, 로그)는 넣지 않는다."""
import argparse
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "배포" / "_cache"
PY_VER = "3.14.7"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# 프로그램 파일: 이것만 복사한다 (개인 자료·로그·테스트는 제외)
APP_DIRS = ["화면", "지침", "assets", "딥시크_확장"]
APP_SKIP_FILES = {"설정.json", "채널_프로필.json", "계획.json", "후보.json", "민담_후보.json", "트렌드.json", "민담_트렌드.json",
                  "벤치_히트.json", "추천_추가.json", "사용한_주제.txt", "민담_사용한_주제.txt", "현상_추가.txt",
                  "유튜브_자동화_시작.bat", "유튜브_자동화_시작.vbs"}
EDITOR_DIRS = ["core", "static", "capcut_template", "user_fonts"]
EDITOR_FILES = ["app.py", "README.md", "requirements.txt"]

기본_설정 = {
    "AI": "deepseek-web", "API_키": "", "모델": "", "대본_글자수": 6750, "하루_대본_편수": 2,
    "내_채널": "", "민담_채널": "", "벤치_채널_추가": [], "민담_벤치_채널_추가": [],
    "인월드_API_키": "", "인월드_목소리": "", "인월드_목소리_사람": "", "인월드_목소리_민담": "",
    "인월드_모델": "inworld-tts-1.5-max", "인월드_속도": 1.0, "분당_글자수": 270, "화풍": "파스텔", "후킹_장면수": 7,
    "API_키_deepseek": "", "API_키_gemini": "", "API_키_claude": "", "유튜브_API_키": "",
    "텔레그램_봇_토큰": "", "텔레그램_채팅_ID": "", "텔레그램_알림": True, "온보딩_완료": False,
}


def log(msg):
    print(msg, flush=True)


def download(url, name):
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    if dest.exists() and dest.stat().st_size > 0:
        log(f"   (캐시) {name}")
        return dest
    log(f"   내려받는 중: {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def find_editor(explicit):
    cands = [Path(explicit)] if explicit else []
    cands += [ROOT / "편집프로그램", ROOT.parent / "편집프로그램", Path.home() / "Downloads" / "편집프로그램", Path.home() / "Desktop" / "편집프로그램"]
    for c in cands:
        if (c / "app.py").is_file():
            return c.resolve()
    raise SystemExit("편집프로그램 폴더를 찾지 못했습니다. --editor 로 지정하세요.")


def copy_tree(src, dst, skip_dirs=("__pycache__",), skip_suffix=(".pyc", ".log", ".mp4")):
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in skip_dirs for part in rel.parts) or p.suffix.lower() in skip_suffix:
            continue
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst / rel)


def build_app(out_app, editor):
    log("1) 프로그램 파일 복사")
    out_app.mkdir(parents=True)
    for p in ROOT.iterdir():
        if p.is_file() and p.suffix == ".py" and p.name not in APP_SKIP_FILES:
            shutil.copy2(p, out_app / p.name)
    shutil.copy2(ROOT / "README.md", out_app / "README.md")
    for d in APP_DIRS:
        copy_tree(ROOT / d, out_app / d)
    (out_app / "지침" / "민담" / "최근_사용_이름.txt").write_text("", encoding="utf-8")
    (out_app / "설정.json").write_text(json.dumps(기본_설정, ensure_ascii=False, indent=2), encoding="utf-8")
    for d in ("대본", "업로드", "대본/민담", "대본/_상태"):
        (out_app / d).mkdir(parents=True, exist_ok=True)
    log(f"2) 편집프로그램 복사: {editor}")
    out_ed = out_app / "편집프로그램"
    out_ed.mkdir()
    for f in EDITOR_FILES:
        if (editor / f).is_file():
            shutil.copy2(editor / f, out_ed / f)
    for d in EDITOR_DIRS:
        if (editor / d).is_dir():
            copy_tree(editor / d, out_ed / d)
    (out_ed / "config.json").write_text("{}", encoding="utf-8")


def build_ffmpeg(out_app, skip_download):
    log("3) ffmpeg / ffprobe")
    bin_dir = out_app / "편집프로그램" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    got = False
    if not skip_download:
        try:
            z = download(FFMPEG_URL, "ffmpeg-release-essentials.zip")
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    base = n.rsplit("/", 1)[-1]
                    if base in ("ffmpeg.exe", "ffprobe.exe", "LICENSE", "README.txt") and ("/bin/" in n or base in ("LICENSE", "README.txt")):
                        with zf.open(n) as src, open(bin_dir / base, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            got = (bin_dir / "ffmpeg.exe").exists() and (bin_dir / "ffprobe.exe").exists()
        except Exception as exc:  # noqa: BLE001
            log(f"   ! ffmpeg 내려받기 실패 ({exc}) — 이 컴퓨터의 ffmpeg 를 복사합니다")
    if not got:
        for name in ("ffmpeg", "ffprobe"):
            p = shutil.which(name)
            if not p:
                raise SystemExit(f"{name} 을 찾지 못했습니다. ffmpeg 를 설치하거나 인터넷 연결 뒤 다시 실행하세요.")
            shutil.copy2(p, bin_dir / f"{name}.exe")
        log("   ! 이 컴퓨터의 ffmpeg 를 복사했습니다 (full 빌드라 용량이 큽니다)")


def build_python(out_py, req_file):
    log(f"4) 내장 파이썬 {PY_VER}")
    z = download(EMBED_URL, f"python-{PY_VER}-embed-amd64.zip")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(out_py)
    # ._pth: 임베디드 파이썬은 이 파일에 적힌 경로만 sys.path 에 넣는다 (스크립트 폴더도 자동으로 안 들어감)
    pth = next(out_py.glob("python*._pth"))
    zip_name = pth.name.replace("._pth", ".zip")
    pth.write_text("\n".join([zip_name, ".", "Lib", "..\\app", "..\\app\\편집프로그램", "import site", ""]), encoding="utf-8")
    exe = out_py / "python.exe"
    env = dict(os.environ, PYTHONNOUSERSITE="1")      # 이 컴퓨터의 사용자 패키지를 못 보게 해서 배포판 안에 새로 설치되게
    log("   pip 설치")
    getpip = download(GETPIP_URL, "get-pip.py")
    subprocess.run([str(exe), "-s", str(getpip), "--no-warn-script-location", "-q"], check=True, env=env)
    log("   패키지 설치 (몇 분 걸립니다)")
    subprocess.run([str(exe), "-s", "-m", "pip", "install", "-q", "--no-warn-script-location", "-r", str(req_file)], check=True, env=env)
    # tkinter (편집프로그램의 파일 선택 창) — 같은 버전의 일반 파이썬이 있으면 거기서 복사
    host = Path(sys.base_prefix)
    if sys.version_info[:2] == tuple(int(x) for x in PY_VER.split(".")[:2]) and (host / "DLLs" / "_tkinter.pyd").exists():
        for f in (host / "DLLs").glob("*"):
            if f.name.startswith(("_tkinter", "tcl", "tk", "zlib")) and f.suffix in (".pyd", ".dll"):
                shutil.copy2(f, out_py / f.name)
        (out_py / "Lib").mkdir(exist_ok=True)
        copy_tree(host / "Lib" / "tkinter", out_py / "Lib" / "tkinter")
        if (host / "tcl").is_dir():
            copy_tree(host / "tcl", out_py / "tcl")
        log("   tkinter 포함")
    else:
        log("   ! tkinter 를 넣지 못했습니다 (편집프로그램의 '찾아보기' 창만 동작하지 않습니다)")
    for p in out_py.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def write_launchers(out):
    log("5) 실행 파일·안내문")
    (out / "시작.bat").write_text('''@echo off
chcp 65001 >nul
title 유튜브 영상 자동 제작
cd /d "%~dp0app"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONNOUSERSITE=1
"%~dp0python\\python.exe" 시작.py
if errorlevel 1 pause
''', encoding="utf-8")
    (out / "시작(창없이).vbs").write_text('''Option Explicit
Dim shell, fso, folder
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = fso.BuildPath(folder, "app")
shell.Environment("PROCESS")("PYTHONNOUSERSITE") = "1"
shell.Environment("PROCESS")("PYTHONUTF8") = "1"
shell.Run Chr(34) & fso.BuildPath(folder, "python\\pythonw.exe") & Chr(34) & " " & Chr(34) & fso.BuildPath(folder, "app\\launcher.py") & Chr(34) & " --background", 0, False
''', encoding="utf-8")
    (out / "설치_안내.txt").write_text('''유튜브 영상 자동 제작 — 설치 안내

1. 이 폴더를 원하는 곳(예: 바탕화면 또는 D:\\)에 통째로 둡니다. 경로에 한글이 있어도 됩니다.
2. 크롬(Chrome)이 설치되어 있어야 합니다. 없으면 https://www.google.com/chrome 에서 설치하세요.
3. [시작.bat] 을 더블클릭합니다. 검은 창이 뜨고 잠시 뒤 크롬에 프로그램 화면이 열립니다.
   - 검은 창을 닫으면 프로그램이 꺼집니다. 창 없이 쓰려면 [시작(창없이).vbs] 를 쓰세요 (종료는 화면의 [종료] 버튼).
   - 처음 실행하면 윈도우 방화벽이 물어볼 수 있습니다. [액세스 허용]을 누르세요 (내 컴퓨터 안에서만 통신합니다).
4. 화면의 [🧭 처음 설정]을 순서대로 따라가면 됩니다: 채널 이름 → 대본 AI(딥시크 확장 또는 API 키) → 나레이션(인월드) → 드롭샷 좌표.
5. 파이썬을 따로 설치할 필요가 없습니다. python 폴더 안에 필요한 것이 모두 들어 있습니다.

폴더 설명
  app\\           프로그램 본체. 만든 대본은 app\\대본\\, 업로드용 결과는 app\\업로드\\ 에 저장됩니다.
  app\\설정.json   API 키 등 설정 (화면에서 저장하면 여기에 기록됩니다. 남에게 보내지 마세요)
  python\\        내장 파이썬 (건드리지 마세요)

문제가 생기면 app\\로그_대본선택.txt, app\\로그_편집프로그램.txt 를 확인하세요.
ffmpeg(app\\편집프로그램\\bin)은 별도 오픈소스 프로그램이며 같은 폴더의 LICENSE 를 따릅니다.
''', encoding="utf-8")


def make_zip(out):
    stamp = datetime.date.today().strftime("%Y%m%d")
    zpath = out.parent / f"{out.name}_{stamp}.zip"
    log(f"6) 압축: {zpath.name}")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in out.rglob("*"):
            if p.is_file():
                zf.write(p, str(Path(out.name) / p.relative_to(out)))
    return zpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--editor", default="")
    ap.add_argument("--out", default=str(ROOT / "배포판" / "유튜브영상자동제작"))
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--skip-download", action="store_true", help="ffmpeg 를 내려받지 않고 이 컴퓨터의 것을 복사")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    editor = find_editor(a.editor)
    build_app(out / "app", editor)
    build_ffmpeg(out / "app", a.skip_download)
    build_python(out / "python", ROOT / "배포" / "requirements.txt")
    write_launchers(out)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    log(f"✓ 배포 폴더 완성: {out} ({size:,.0f} MB)")
    if not a.no_zip:
        z = make_zip(out)
        log(f"✓ {z} ({z.stat().st_size / 1e6:,.0f} MB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
