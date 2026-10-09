"""Local YouTube channel bookmarks; secrets stay in ignored settings.json."""
import uuid
import copy
from urllib.parse import urlparse


def public_accounts(cfg):
    accounts = cfg.get("유튜브_계정", [])
    if not accounts:
        accounts = [{"id": slot, "name": name, "url": cfg.get(key, ""), "api_key": cfg.get("유튜브_API_키", "")} for slot, name, key in [("person", "기존 정보형", "내_채널"), ("mindam", "기존 이야기형", "민담_채널")] if cfg.get(key)]
    return [{"id": a["id"], "name": a["name"], "url": a["url"], "key_saved": bool(a.get("api_key")), "language": a.get("language", ""),
             "selected_slots": [slot for slot in ("person", "mindam") if cfg.get("유튜브_선택_" + slot) == a["id"]]} for a in accounts]


def migrate(cfg):
    if not cfg.get("유튜브_계정"):
        cfg["유튜브_계정"] = [{**a, "api_key": cfg.get("유튜브_API_키", "")} for a in public_accounts(cfg)]


def save_account(cfg, body):
    name, url = str(body.get("name", "")).strip(), str(body.get("url", "")).strip()
    parsed = urlparse(url)
    if not name or len(name) > 100:
        raise ValueError("계정 이름을 입력하세요 (100자 이하).")
    if parsed.scheme != "https" or parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or not parsed.path.startswith(("/@", "/channel/", "/c/", "/user/")):
        raise ValueError("https://www.youtube.com/@이름 형식의 채널 주소를 입력하세요.")
    language = str(body.get("language", "ko"))
    if language not in {"ko", "ja", "en", "es", "zh"}:
        raise ValueError("지원하는 제작 언어를 선택하세요.")
    migrate(cfg)
    key = str(body.get("api_key", "")).strip() or cfg.get("유튜브_API_키", "")
    found = next((a for a in cfg["유튜브_계정"] if a["url"] == url), None)
    if found:
        found.update(name=name, api_key=key or found.get("api_key", ""), language=language)
    else:
        cfg["유튜브_계정"].append({"id": uuid.uuid4().hex, "name": name, "url": url, "api_key": key, "language": language})
    return public_accounts(cfg)


def select_account(cfg, account_id, slot):
    if slot not in {"person", "mindam"}:
        raise ValueError("적용할 채널을 선택하세요.")
    migrate(cfg)
    account = next((a for a in cfg["유튜브_계정"] if a["id"] == account_id), None)
    if not account:
        raise ValueError("저장된 유튜브 계정을 선택하세요.")
    cfg["내_채널" if slot == "person" else "민담_채널"] = account["url"]
    cfg["유튜브_API_키"] = account.get("api_key", "")
    cfg["유튜브_선택_" + slot] = account_id

    return account.get("language")


def save_selected_api_key(cfg, key, slot="person"):
    """처음 설정에서 바꾼 키도 현재 저장 계정에 보관한다."""
    key = str(key or "").strip()
    if not key:
        return
    cfg["유튜브_API_키"] = key
    selected = cfg.get("유튜브_선택_" + slot)
    for account in cfg.get("유튜브_계정", []):
        if account["id"] == selected:
            account["api_key"] = key
            break


def switch_profile(cfg, account_id, slot, current):
    """Keep each account's reference/model/branding separate when sharing a production slot."""
    migrate(cfg)
    account = next((a for a in cfg["유튜브_계정"] if a["id"] == account_id), None)
    if not account:
        raise ValueError("저장된 유튜브 계정을 선택하세요.")
    previous = next((a for a in cfg["유튜브_계정"] if a["id"] == cfg.get("유튜브_선택_" + slot)), None)
    if previous is None:
        previous = next((a for a in cfg["유튜브_계정"] if a["url"] == cfg.get("내_채널" if slot == "person" else "민담_채널")), None)
    if previous:
        previous["profile"] = copy.deepcopy(current)
    profile = copy.deepcopy(account.get("profile") or current)
    if not account.get("profile") and (previous is None or previous["id"] != account_id):
        profile.update(이름=account["name"], 업로드_폴더=account["name"])
    profile["언어"] = account.get("language") or profile.get("언어", "ko")
    if profile["언어"] == "ja":
        profile.setdefault("일본어_채널명", account["name"])
    select_account(cfg, account_id, slot)
    account["profile"] = copy.deepcopy(profile)
    return profile


def delete_account(cfg, account_id):
    migrate(cfg)
    if not any(a["id"] == account_id for a in cfg["유튜브_계정"]):
        raise ValueError("삭제할 유튜브 계정을 선택하세요.")
    removed = next(a for a in cfg["유튜브_계정"] if a["id"] == account_id)
    cfg["유튜브_계정"] = [a for a in cfg["유튜브_계정"] if a["id"] != account_id]
    for slot in ("person", "mindam"):
        if cfg.get("유튜브_선택_" + slot) == account_id or cfg.get("내_채널" if slot == "person" else "민담_채널") == removed["url"]:
            cfg.pop("유튜브_선택_" + slot, None)
            cfg["내_채널" if slot == "person" else "민담_채널"] = ""
    return public_accounts(cfg)
