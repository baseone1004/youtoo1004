"""Independent, persistent channel workspaces and local server processes.

Only code/resources and initial settings are copied. Existing productions remain
where they are; queues and generated media are never copied or auto-started.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from process_lock import file_lock

HOME = Path(os.environ.get("YOUTOO_HOME") or Path(__file__).resolve().parent).resolve()
STORE_NAME = "채널별_작업공간"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def identity(account_id):
    return hashlib.sha256(str(account_id).encode()).hexdigest()[:20]


def workspace_dir(account_id):
    return HOME / STORE_NAME / identity(account_id)


def fetch(port, path, timeout=1):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def own_server(record):
    data = fetch(record.get("port", 0), "/api/workspace/identity")
    return bool(data and data.get("id") == record.get("id") and data.get("token") == record.get("instance"))


def own_editor(record):
    data = fetch(record.get("editor_port", 0), "/api/channel-workspace")
    return bool(data and data.get("instance") == record.get("instance"))


def python_script_args(script, *args):
    # Embedded Python's ._pth does not automatically include the worker folder.
    return ["-c", "import os,sys,runpy; sys.path.insert(0,os.getcwd()); runpy.run_path(sys.argv[1],run_name='__main__')", script, *args]


def current_identity():
    return {"id": os.environ.get("YOUTOO_ACCOUNT", ""),
            "token": os.environ.get("YOUTOO_INSTANCE", "")}


def accounts():
    import youtube_accounts
    cfg = read_json(HOME / "설정.json")
    result = youtube_accounts.public_accounts(cfg)
    active = cfg.get("유튜브_선택_person")
    for account in result:
        record = read_json(workspace_dir(account["id"]) / ".workspace.json")
        account.update(main=account["id"] == active,
                       running=own_server(record) if record else False,
                       url_local=f"http://127.0.0.1:{record['port']}/" if record else "",
                       folder=str(workspace_dir(account["id"])))
    return result


def broker_key():
    folder = HOME / STORE_NAME
    with file_lock(folder / ".broker.lock", timeout=5):
        path = folder / ".broker.json"
        value = read_json(path).get("key")
        if not value:
            value = secrets.token_hex(32)
            write_json(path, {"key": value})
        return value


def free_port(excluded):
    for port in range(18800, 18900):
        if port in excluded:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise RuntimeError("동시 작업용 빈 연결을 찾지 못했습니다.")


def copy_resources(source, target, names):
    for name in names:
        src, dst = source / name, target / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"))
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def initialize(target, account_id, editor):
    """Snapshot settings once. Never reset saved keys, references or work on reopen."""
    cfg = copy.deepcopy(read_json(HOME / "설정.json"))
    account = next((a for a in cfg.get("유튜브_계정", []) if a["id"] == account_id), None)
    if account is None:
        raise ValueError("저장된 채널을 선택하세요.")
    profiles = copy.deepcopy(read_json(HOME / "채널_프로필.json"))
    if cfg.get("유튜브_선택_person") == account_id:
        profile = profiles.get("person") or account.get("profile")
    else:
        profile = account.get("profile")
    if not profile:
        raise ValueError("먼저 기본 창에서 이 채널의 프로필·목소리·레퍼런스를 설정해 주세요.")
    profiles["person"] = copy.deepcopy(profile)
    profiles["person"]["언어"] = account.get("language") or profile.get("언어", "ko")
    account["profile"] = copy.deepcopy(profiles["person"])
    cfg.update(유튜브_계정=[account], 유튜브_선택_person=account_id,
               내_채널=account["url"], 유튜브_API_키=account.get("api_key", ""), 이야기형_숨김=True)
    target.mkdir(parents=True, exist_ok=True)
    copy_resources(HOME, target, ["지침", "assets", "레퍼런스", "bin"])
    # An older profile may contain an absolute local reference path.
    mascot = profiles["person"].get("마스코트") or {}
    reference = Path(mascot.get("이미지") or "")
    if reference.is_absolute() and reference.is_file():
        dest = target / "레퍼런스" / reference.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference, dest)
        mascot["이미지"] = str(dest.relative_to(target))
        account["profile"] = copy.deepcopy(profiles["person"])
    write_json(target / "설정.json", cfg)
    write_json(target / "채널_프로필.json", profiles)
    # Editor settings are independent, including last-used paths and image state.
    editor_cfg = read_json(editor / "config.json")
    for key in ("last", "gen", "hook"):
        editor_cfg.pop(key, None)
    write_json(target / "편집프로그램" / "config.json", editor_cfg)


def sync_code(target, editor, include_editor=True):
    copy_resources(HOME, target, [p.name for p in HOME.glob("*.py")] + ["화면", "딥시크_확장"])
    if not include_editor:
        return
    copy_resources(editor, target / "편집프로그램",
                   ["app.py", "core", "static", "capcut_template", "user_fonts", "bin"])
    from 시작 import apply_editor_integrations
    apply_editor_integrations(target / "편집프로그램")


def launch_process(args, cwd, env, log_name):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with (cwd / log_name).open("a", encoding="utf-8") as log:
        return subprocess.Popen([sys.executable, *python_script_args(*args)], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=flags)


def launch(account_id):
    """Channel selection now stays in one app; never spawn parallel paid workers."""
    raise ValueError("별도 채널 창 기능이 제거되었습니다. 기본 프로그램 맨 위에서 채널을 선택하고 한 편씩 제작하세요.")
