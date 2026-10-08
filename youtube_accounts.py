"""Local YouTube channel bookmarks; secrets stay in ignored settings.json."""
import uuid
from urllib.parse import urlparse


def public_accounts(cfg):
    accounts = cfg.get("유튜브_계정", [])
    if not accounts:
        accounts = [{"id": slot, "name": name, "url": cfg.get(key, ""), "api_key": cfg.get("유튜브_API_키", "")} for slot, name, key in [("person", "기존 정보형", "내_채널"), ("mindam", "기존 이야기형", "민담_채널")] if cfg.get(key)]
    return [{"id": a["id"], "name": a["name"], "url": a["url"], "key_saved": bool(a.get("api_key"))} for a in accounts]


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
    migrate(cfg)
    key = str(body.get("api_key", "")).strip() or cfg.get("유튜브_API_키", "")
    found = next((a for a in cfg["유튜브_계정"] if a["url"] == url), None)
    if found:
        found.update(name=name, api_key=key or found.get("api_key", ""))
    else:
        cfg["유튜브_계정"].append({"id": uuid.uuid4().hex, "name": name, "url": url, "api_key": key})
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
