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
    """Open/reuse one dedicated app+editor pair; no generation calls here."""
    with file_lock(HOME / STORE_NAME / ".launch.lock", timeout=90):
        cfg = read_json(HOME / "설정.json")
        if not any(a["id"] == account_id for a in cfg.get("유튜브_계정", [])):
            raise ValueError("저장된 채널을 선택하세요.")
        if cfg.get("유튜브_선택_person") == account_id:
            return {"url": "http://127.0.0.1:8766/", "main": True}
        # The existing editor must participate in the same final-render lock.
        # Do not restart it here: it may be completing the user's current work.
        main_editor = read_json(HOME / ".editor-port.json").get("port", 8765)
        if fetch(main_editor, "/api/info") and fetch(main_editor, "/api/channel-workspace") is None:
            raise ValueError("현재 제작을 끝낸 뒤 기본 프로그램을 [종료]하고 시작 파일로 다시 실행해 주세요. 편집기에 동시 작업 연결을 적용해야 합니다.")
        target = workspace_dir(account_id)
        record = read_json(target / ".workspace.json")
        if record and own_server(record):
            return {"url": f"http://127.0.0.1:{record['port']}/", "folder": str(target)}
        candidates = [HOME / "편집프로그램", HOME.parent / "편집프로그램",
                      Path.home() / "Downloads" / "편집프로그램", Path.home() / "Desktop" / "편집프로그램"]
        custom = os.environ.get("YOUTOO_SOURCE_EDITOR") or (os.environ.get("YOUTUBE_EDITOR_DIR") if not os.environ.get("YOUTOO_ACCOUNT") else "")
        if custom:
            candidates.insert(0, Path(custom))
        editor = next((p for p in candidates if (p / "app.py").is_file()), None)
        if not editor:
            raise ValueError("편집프로그램 폴더를 찾지 못했습니다.")
        if not (target / "설정.json").exists():
            staging = Path(tempfile.mkdtemp(prefix=".준비_", dir=target.parent))
            try:
                initialize(staging, account_id, editor)
                staging.rename(target)
            finally:
                if staging.exists():
                    if staging.resolve().parent != target.parent.resolve():
                        raise RuntimeError("임시 작업 공간 경로를 확인할 수 없습니다.")
                    shutil.rmtree(staging)
        # Reconnect a surviving owned editor after a backend crash. Never copy
        # code/config onto a live editor or mistake another listener for it.
        reuse_editor = bool(record and own_editor(record))
        sync_code(target, editor, include_editor=not reuse_editor)
        port = free_port(set())
        editor_port = record["editor_port"] if reuse_editor else free_port({port})
        record = {"id": account_id, "instance": record["instance"] if reuse_editor else secrets.token_hex(16), "port": port, "editor_port": editor_port}
        write_json(target / ".workspace.json", record)
        write_json(target / ".editor-port.json", {"port": editor_port})
        env = dict(os.environ, YOUTOO_HOME=str(HOME), YOUTOO_ACCOUNT=account_id,
                   YOUTOO_INSTANCE=record["instance"], SCRIPT_UI_PORT=str(port),
                   YOUTOO_SOURCE_EDITOR=str(editor),
                   YOUTUBE_EDITOR_PORT=str(editor_port), YOUTUBE_EDITOR_DIR=str(target / "편집프로그램"),
                   YOUTOO_WEB_BROKER="http://127.0.0.1:8766", YOUTOO_WEB_TOKEN=broker_key(),
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        children = []
        try:
            if not reuse_editor:
                children.append(launch_process(["app.py", "--no-browser"], target / "편집프로그램", env, "로그_편집.txt"))
            children.append(launch_process(["대본선택.py", "--no-browser"], target, env, "로그_대본선택.txt"))
            for _ in range(90):
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("채널 창을 실행하지 못했습니다. 채널별 작업 공간의 로그를 확인하세요.")
                if own_server(record) and own_editor(record):
                    return {"url": f"http://127.0.0.1:{port}/", "folder": str(target)}
                time.sleep(1)
            raise RuntimeError("채널 창 준비 시간이 초과되었습니다.")
        except Exception:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
            raise
