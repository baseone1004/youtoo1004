# -*- coding: utf-8 -*-
"""Per-channel YouTube OAuth and resumable uploads for reviewed export packages.

OAuth client configuration and user tokens are private local files. Uploads are
always started by an explicit UI action; visibility defaults to private.
"""
import datetime
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

CLIENT_FILE = "youtube_oauth_client.json"
TOKEN_DIR = "youtube_auth"
_DPAPI_PREFIX = b"YOUTOO-DPAPI1:"
_LOCAL_PREFIX = b"YOUTOO-LOCAL1:"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_lock = threading.Lock()
_connect_jobs = {}


def _safe_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("유튜브 계정 ID가 올바르지 않습니다.")
    return value


def _root(root):
    return Path(root).resolve()


def _client_path(root):
    return _root(root) / CLIENT_FILE


def _token_path(root, account_id):
    return _root(root) / TOKEN_DIR / (_safe_id(account_id) + ".json")


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _protect(raw):
    """Protect account secrets with Windows DPAPI; restrict permissions elsewhere."""
    raw = bytes(raw)
    if os.name != "nt":
        return _LOCAL_PREFIX + raw
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(raw)
    source_blob = Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(ctypes.byref(source_blob), "Youtoo YouTube OAuth", None, None, None, 1, ctypes.byref(target_blob)):
        raise OSError(ctypes.get_last_error(), "Windows가 OAuth 비밀 파일을 암호화하지 못했습니다.")
    try:
        return _DPAPI_PREFIX + ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _unprotect(raw):
    if raw.startswith(_LOCAL_PREFIX):
        return raw[len(_LOCAL_PREFIX):]
    if not raw.startswith(_DPAPI_PREFIX):
        raise ValueError("OAuth 비밀 파일이 암호화되지 않았거나 형식이 올바르지 않습니다.")
    if os.name != "nt":
        raise ValueError("이 OAuth 파일은 저장한 Windows 사용자 계정에서만 열 수 있습니다.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    payload = raw[len(_DPAPI_PREFIX):]
    source = ctypes.create_string_buffer(payload)
    source_blob = Blob(len(payload), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = Blob()
    description = ctypes.c_wchar_p()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source_blob), ctypes.byref(description), None, None, None, 1, ctypes.byref(target_blob)):
        raise OSError(ctypes.get_last_error(), "Windows가 OAuth 비밀 파일을 복호화하지 못했습니다. 저장한 Windows 사용자로 실행하세요.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)
        if description:
            kernel32.LocalFree(description)


def _write_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    protected = _protect(raw)
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        temp.write_bytes(protected)
        if os.name != "nt":
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _read_private_json(path):
    return json.loads(_unprotect(Path(path).read_bytes()).decode("utf-8"))


def save_client(root, value):
    """Store an installed/desktop OAuth client downloaded from Google Cloud."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 100_000:
            raise ValueError("OAuth 클라이언트 JSON이 너무 큽니다.")
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("Google OAuth 클라이언트 JSON을 읽지 못했습니다.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("installed"), dict):
        raise ValueError("Google Cloud에서 데스크톱 앱 유형의 OAuth JSON을 내려받아 선택하세요.")
    client = value["installed"]
    for key in ("client_id", "client_secret", "auth_uri", "token_uri"):
        if not isinstance(client.get(key), str) or not client[key].strip():
            raise ValueError("OAuth JSON에 필수 항목이 없습니다: " + key)
    if not client["auth_uri"].startswith("https://") or not client["token_uri"].startswith("https://"):
        raise ValueError("OAuth 인증 주소는 HTTPS여야 합니다.")
    _write_private_json(_client_path(root), value)
    return {"client_saved": True}


def _authorized(root, account_id):
    path = _token_path(root, account_id)
    if not path.is_file():
        raise ValueError("이 채널은 Google 로그인 전입니다. 설정에서 먼저 연결하세요.")
    try:
        saved = _read_private_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError("저장된 Google 로그인 정보가 손상되었습니다. 계정을 다시 연결하세요.") from exc
    from google.oauth2.credentials import Credentials
    credentials = Credentials.from_authorized_user_info(saved["credentials"], SCOPES)
    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
        saved["credentials"] = json.loads(credentials.to_json())
        _write_private_json(path, saved)
    if not credentials.valid:
        raise ValueError("Google 로그인이 만료되었습니다. 이 채널을 다시 연결하세요.")
    return credentials, saved.get("channel") or {}


def status(root, accounts):
    result = {"client_saved": _client_path(root).is_file(), "accounts": {}}
    for account in accounts:
        account_id = _safe_id(account["id"])
        path = _token_path(root, account_id)
        state = {"connected": False, "channel": "", "channel_id": ""}
        if path.is_file():
            try:
                saved = _read_private_json(path)
                channel = saved.get("channel") or {}
                state.update(connected=True, channel=channel.get("title", ""),
                             channel_id=channel.get("id", ""))
            except (OSError, ValueError):
                state["error"] = "로그인 파일을 읽지 못했습니다."
        with _lock:
            current = _connect_jobs.get(account_id)
            if current:
                state.update(current)
        result["accounts"][account_id] = state
    return result


def _expected_channel_id(youtube, url):
    parsed = urlparse(url if str(url).startswith("http") else "https://www.youtube.com/" + str(url).lstrip("/"))
    path = parsed.path.rstrip("/")
    if path.startswith("/channel/"):
        return path.split("/", 2)[2]
    if path.startswith("/@"):
        result = youtube.channels().list(part="id", forHandle=path[1:]).execute()
    elif path.startswith("/user/"):
        result = youtube.channels().list(part="id", forUsername=path.split("/", 2)[2]).execute()
    else:
        return ""
    rows = result.get("items") or []
    return rows[0].get("id", "") if rows else ""


def connect(root, account):
    """Begin OAuth in a worker thread; the caller polls status()."""
    account_id = _safe_id(account["id"])
    client_path = _client_path(root)
    if not client_path.is_file():
        raise ValueError("먼저 Google Cloud의 데스크톱 OAuth JSON 파일을 등록하세요.")
    with _lock:
        current = _connect_jobs.get(account_id)
        if current and current.get("status") == "running":
            return {"started": False, "message": "이미 로그인 창을 기다리고 있습니다."}
        _connect_jobs[account_id] = {"status": "running", "stage": "Google 로그인 대기", "error": ""}

    def work():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            client = _read_private_json(client_path)
            flow = InstalledAppFlow.from_client_config(client, SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1", port=0, open_browser=True,
                authorization_prompt_message="브라우저에서 YouTube 채널에 로그인하고 권한을 허용하세요.",
                success_message="YouTube 연결이 끝났습니다. 이 창을 닫고 제작 프로그램으로 돌아가세요.",
                access_type="offline", prompt="consent",
            )
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            mine = youtube.channels().list(part="id,snippet", mine=True, maxResults=50).execute().get("items") or []
            if not mine:
                raise RuntimeError("Google 로그인 계정에서 YouTube 채널을 찾지 못했습니다.")
            expected = _expected_channel_id(youtube, account.get("url", ""))
            chosen = next((item for item in mine if item.get("id") == expected), None) if expected else None
            if expected and not chosen:
                names = ", ".join((x.get("snippet") or {}).get("title", "") for x in mine)
                raise RuntimeError(f"로그인한 Google 계정의 채널이 저장한 채널 주소와 다릅니다. 주소에서 확인한 채널 ID: {expected} · 로그인 채널: {names}")
            if not chosen:
                raise RuntimeError("채널 주소를 안전하게 확인할 수 없습니다. 채널 URL을 @핸들이나 /channel/UC… 주소로 바꾼 뒤 다시 연결하세요.")
            info = {"id": chosen["id"], "title": (chosen.get("snippet") or {}).get("title", "")}
            token = {"credentials": json.loads(credentials.to_json()), "channel": info,
                     "account_id": account_id, "saved_at": datetime.datetime.now().isoformat(timespec="seconds")}
            _write_private_json(_token_path(root, account_id), token)
            with _lock:
                _connect_jobs[account_id] = {"status": "done", "stage": "연결 완료", "channel": info["title"], "channel_id": info["id"], "error": ""}
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _connect_jobs[account_id] = {"status": "error", "stage": "로그인 실패", "error": str(exc)}

    threading.Thread(target=work, daemon=True, name="youtube-oauth-" + account_id[:8]).start()
    return {"started": True}


def disconnect(root, account_id):
    path = _token_path(root, account_id)
    path.unlink(missing_ok=True)
    with _lock:
        _connect_jobs.pop(_safe_id(account_id), None)
    return {"connected": False}


def account_for_package(root, package, accounts, cfg):
    """Match an export's saved channel folder to exactly one registered channel."""
    package = Path(package).resolve()
    source = ""
    try:
        marker = json.loads((package / "제작원본.json").read_text(encoding="utf-8"))
        source = str(marker.get("script") or "").replace("\\", "/")
    except (OSError, ValueError):
        pass
    slot = "mindam" if "/민담/" in ("/" + source.lstrip("/")) or source.endswith("/민담/final.txt") else "person"
    folder_name = package.parent.name
    matches = []
    for account in accounts:
        profile = account.get("profile") or {}
        names = {re.sub(r'[\\/:*?"<>|]+', " ", str(value or "")).strip()[:40]
                 for value in (account.get("name"), profile.get("업로드_폴더"))}
        if folder_name in names:
            matches.append(account)
    if len(matches) == 1:
        return matches[0], slot
    selected_id = cfg.get("유튜브_선택_" + slot)
    selected = next((a for a in accounts if a.get("id") == selected_id), None)
    selected_names = {re.sub(r'[\\/:*?"<>|]+', " ", str(value or "")).strip()[:40]
                      for value in (selected.get("name"), (selected.get("profile") or {}).get("업로드_폴더"))} if selected else set()
    if selected:
        try:
            import 채널_프로필
            active_profile = 채널_프로필.get(slot)
            active_name = re.sub(r'[\\/:*?"<>|]+', " ", str(active_profile.get("업로드_폴더") or "")).strip()[:40]
            if active_name:
                selected_names.add(active_name)
        except Exception:  # noqa: BLE001
            pass
    if selected and folder_name in selected_names:
        return selected, slot
    if not matches:
        raise ValueError("업로드 폴더 이름과 저장된 YouTube 계정을 연결하지 못했습니다. 채널별 업로드 폴더 이름을 확인하세요.")
    raise ValueError("같은 업로드 폴더 이름을 쓰는 계정이 여러 개입니다. 계정 이름 또는 채널 프로필의 업로드 폴더 이름을 구분해 주세요.")


def reset_uncertain(root, package, account_id, confirmed_not_uploaded):
    """Allow a manual retry only after the user checked YouTube Studio."""
    if not confirmed_not_uploaded:
        raise ValueError("YouTube Studio에서 해당 영상이 업로드되지 않은 것을 확인해야 재시도할 수 있습니다.")
    package = Path(package).resolve()
    manifest_path = package / "youtube_uploads.json"
    if not package.is_relative_to((_root(root) / "업로드").resolve()) or not manifest_path.is_file():
        raise ValueError("업로드 이력이 있는 완성 패키지가 아닙니다.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("account_id") != _safe_id(account_id):
        raise ValueError("업로드 이력의 계정이 선택한 계정과 다릅니다.")
    cleared = 0
    for item in manifest.get("items", {}).values():
        if item.get("status") in {"uploading", "uncertain", "error"}:
            item.update(status="pending", error="", retry_confirmed_at=datetime.datetime.now().isoformat(timespec="seconds"))
            cleared += 1
    _write_json(manifest_path, manifest)
    return {"cleared": cleared}


def _parse_package(package):
    text_path = package / "업로드정보.txt"
    if not text_path.is_file():
        raise ValueError("업로드정보.txt가 없는 패키지입니다.")
    text = text_path.read_text(encoding="utf-8-sig", errors="replace")

    def block(name):
        match = re.search(rf"(?ms)^\[{re.escape(name)}\]\s*\n(.*?)(?=^\[[^\n]+\]|\Z)", text)
        return match.group(1).strip() if match else ""

    title = block("제목")
    description = block("설명")
    tags_text = block("태그")
    tags = [t.strip().lstrip("#") for t in re.split(r"[,，\s]+", tags_text) if t.strip()]
    if not title:
        raise ValueError("업로드 제목이 비어 있습니다.")
    if len(title) > 100:
        raise ValueError("YouTube 제목은 100자 이하여야 합니다.")
    if len(description) > 5000:
        raise ValueError("YouTube 설명은 5,000자 이하여야 합니다.")
    return {"title": title, "description": description, "tags": tags[:60]}


def _file_key(package, file):
    relative = file.relative_to(package).as_posix()
    stat = file.stat()
    return hashlib.sha256(f"{relative}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()


def preflight(root, package, account, privacy, include_shorts=True):
    """Check auth, export files, destination identity and retry ambiguity before upload."""
    package = Path(package).resolve()
    if privacy != "private":
        raise ValueError("YouTube 업로드는 비공개로만 허용됩니다. 검토 후 YouTube Studio에서 직접 공개하세요.")
    if not package.is_relative_to((_root(root) / "업로드").resolve()) or not package.is_dir():
        raise ValueError("업로드 폴더 안의 완성 패키지만 전송할 수 있습니다.")
    _credentials, channel = _authorized(root, account["id"])
    if not channel.get("id"):
        raise ValueError("저장된 YouTube 채널 정보를 찾지 못했습니다. 계정을 다시 연결하세요.")
    metadata = _parse_package(package)
    long_video = package / "최종.mp4"
    if not long_video.is_file() or long_video.stat().st_size < 1_000_000:
        raise ValueError("완성된 최종.mp4를 찾지 못했거나 파일이 너무 작습니다.")
    files = [long_video]
    shorts = package / "쇼츠"
    if include_shorts:
        short_files = sorted(p for p in shorts.glob("*.mp4") if p.is_file()) if shorts.is_dir() else []
        if len(short_files) != 2:
            raise ValueError(f"이 업로드 패키지의 쇼츠가 {len(short_files)}/2편입니다. 원본 대본을 다시 선택해 쇼츠 2편을 완성하거나, 쇼츠 업로드를 끄세요.")
        files.extend(short_files)
    manifest_path = package / "youtube_uploads.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if manifest.get("account_id") not in (None, account["id"]) and manifest.get("items"):
        raise ValueError("이 패키지는 다른 YouTube 계정으로 업로드를 시작했습니다.")
    if manifest.get("items") and manifest.get("privacy") and manifest["privacy"] != privacy:
        raise ValueError("업로드를 시작한 뒤 공개 범위를 바꿀 수 없습니다. 같은 공개 범위로 이어서 올리거나 YouTube Studio에서 직접 변경하세요.")
    uncertain = []
    for file in files:
        entry = (manifest.get("items") or {}).get(_file_key(package, file), {})
        if entry.get("status") in {"uncertain", "error", "uploading"}:
            uncertain.append(file.name)
    if uncertain:
        raise RuntimeError("이전 업로드가 실패하거나 중단된 파일이 있습니다: " + ", ".join(uncertain) +
                           ". YouTube Studio에서 업로드 여부를 확인한 뒤 다시 눌러 복구하세요.")
    return {"channel": channel.get("title", ""), "video_count": len(files), "title": metadata["title"]}


def upload_package(root, package, account, privacy, job, include_shorts=True):
    """Upload long video and optionally its generated Shorts in order.

    Completed files are remembered. An interrupted in-flight upload is marked
    uncertain and is never blindly retried, preventing duplicate public videos.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    root, package = _root(root), Path(package).resolve()
    upload_root = (root / "업로드").resolve()
    if not package.is_relative_to(upload_root) or not package.is_dir():
        raise ValueError("업로드 폴더 안의 완성 패키지만 전송할 수 있습니다.")
    if privacy != "private":
        raise ValueError("YouTube 업로드는 비공개로만 허용됩니다. 검토 후 YouTube Studio에서 직접 공개하세요.")
    account_id = _safe_id(account["id"])
    credentials, channel = _authorized(root, account_id)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    mine = youtube.channels().list(part="id", mine=True, maxResults=50).execute().get("items") or []
    if not any(item.get("id") == channel.get("id") for item in mine):
        raise ValueError("현재 Google 로그인 채널이 저장된 채널과 달라 업로드를 막았습니다. 다시 로그인하세요.")
    metadata = _parse_package(package)
    long_video = package / "최종.mp4"
    if not long_video.is_file() or long_video.stat().st_size < 1_000_000:
        raise ValueError("완성된 최종.mp4를 찾지 못했거나 파일이 너무 작습니다.")
    language = account.get("language") or "ko"
    files = [(long_video, metadata["title"])]
    if include_shorts:
        shorts_dir = package / "쇼츠"
        short_files = sorted(p for p in shorts_dir.glob("*.mp4") if p.is_file()) if shorts_dir.is_dir() else []
        if len(short_files) != 2:
            raise ValueError(f"이 업로드 패키지의 쇼츠가 {len(short_files)}/2편입니다. 원본 대본을 다시 선택해 쇼츠 2편을 완성하거나, 쇼츠 업로드를 끄세요.")
        for video in short_files:
            sidecar = video.with_suffix(".json")
            short_title = metadata["title"] + " · 쇼츠"
            if sidecar.is_file():
                try:
                    short_title = str(json.loads(sidecar.read_text(encoding="utf-8")).get("title") or short_title).strip()
                except (ValueError, OSError):
                    pass
            files.append((video, short_title[:100]))
    manifest_path = package / "youtube_uploads.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except ValueError:
        raise ValueError("업로드 이력 파일이 손상되었습니다. 파일을 확인한 뒤 다시 시도하세요.")
    if manifest.get("account_id") not in (None, account_id) and manifest.get("items"):
        raise ValueError("이 패키지는 다른 YouTube 계정으로 업로드를 시작했습니다.")
    if manifest.get("items") and manifest.get("privacy") and manifest["privacy"] != privacy:
        raise ValueError("업로드를 시작한 뒤 공개 범위를 바꿀 수 없습니다. 같은 공개 범위로 이어서 올리거나 YouTube Studio에서 직접 변경하세요.")
    manifest.update(account_id=account_id, channel_id=channel.get("id"), channel=channel.get("title"), privacy=privacy)
    items = manifest.setdefault("items", {})
    uploaded = []
    total_bytes = sum(file.stat().st_size for file, _ in files)
    completed_bytes = 0
    try:
        for index, (file, title) in enumerate(files, 1):
            if job.cancel_requested:
                raise RuntimeError("사용자가 업로드를 중단했습니다.")
            key = _file_key(package, file)
            old = items.get(key) or {}
            if old.get("status") == "uploaded":
                uploaded.append({"file": file.name, "title": title, "video_id": old.get("video_id"), "skipped": True})
                completed_bytes += file.stat().st_size
                continue
            if old.get("status") in {"uploading", "uncertain"}:
                raise RuntimeError(f"{file.name}은 이전 업로드가 중단되어 YouTube에 도착했는지 확인이 필요합니다. YouTube Studio에서 확인한 뒤 이 패키지의 업로드 이력을 정리해야 재시도할 수 있습니다.")
            if old.get("status") == "error":
                raise RuntimeError(f"{file.name} 업로드가 실패했습니다. 상세 오류를 확인한 뒤 업로드 이력에서 재시도를 선택하세요.")
            items[key] = {"file": file.relative_to(package).as_posix(), "title": title, "status": "uploading",
                          "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
            _write_json(manifest_path, manifest)
            body = {"snippet": {"title": title, "description": metadata["description"][:5000],
                                "tags": metadata["tags"], "categoryId": "22",
                                "defaultLanguage": language},
                    "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
            media = MediaFileUpload(str(file), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media,
                                              notifySubscribers=False)
            response = None
            try:
                while response is None:
                    if job.cancel_requested:
                        raise RuntimeError("사용자가 업로드를 중단했습니다.")
                    progress, response = request.next_chunk()
                    if progress is not None:
                        job.progress = min(0.99, (completed_bytes + progress.progress() * file.stat().st_size) / max(1, total_bytes))
                        job.stage = f"YouTube 업로드 {index}/{len(files)} · {file.name} · {progress.progress() * 100:.0f}%"
                video_id = response.get("id", "")
                if not video_id:
                    raise RuntimeError("YouTube 응답에 영상 ID가 없습니다.")
                items[key].update(status="uploaded", video_id=video_id,
                                  url="https://www.youtube.com/watch?v=" + video_id,
                                  completed_at=datetime.datetime.now().isoformat(timespec="seconds"))
                _write_json(manifest_path, manifest)
                uploaded.append({"file": file.name, "title": title, "video_id": video_id,
                                 "url": "https://www.youtube.com/watch?v=" + video_id})
            except Exception as exc:  # noqa: BLE001
                items[key].update(status="uncertain", error=str(exc)[:1000],
                                  interrupted_at=datetime.datetime.now().isoformat(timespec="seconds"))
                _write_json(manifest_path, manifest)
                raise
            completed_bytes += file.stat().st_size
        job.progress = 1.0
        job.stage = "YouTube 업로드 완료"
        return {"uploaded": uploaded, "channel": channel.get("title"), "privacy": privacy,
                "manifest": str(manifest_path)}
    except Exception:
        raise
