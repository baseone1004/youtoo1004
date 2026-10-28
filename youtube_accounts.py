"""Local YouTube channel bookmarks; secrets stay in ignored settings.json."""
import uuid
import copy
from urllib.parse import urlparse


def public_accounts(cfg):
    accounts = cfg.get("유튜브_계정", [])
    if not accounts:
        accounts = [{"id": slot, "name": name, "url": cfg.get(key, ""), "api_key": cfg.get("유튜브_API_키", "")} for slot, name, key in [("person", "기존 정보형", "내_채널"), ("mindam", "기존 이야기형", "민담_채널")] if cfg.get(key)]
    result = []
    for account in accounts:
        selected = [slot for slot in ("person", "mindam") if cfg.get("유튜브_선택_" + slot) == account["id"]]
        slot = account.get("slot") or (selected[0] if selected else "")
        if slot not in {"person", "mindam"}:
            slot = "mindam" if (account.get("profile") or {}).get("유형") == "이야기형" else "person"
        result.append({"id": account["id"], "name": account["name"], "url": account["url"],
                       "key_saved": bool(account.get("api_key")), "language": account.get("language", ""),
                       "slot": slot, "selected_slots": selected})
    return result


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
    slot = str(body.get("slot", "") or "")
    if slot and slot not in {"person", "mindam"}:
        raise ValueError("정보형 또는 이야기형 제작 채널을 선택하세요.")
    migrate(cfg)
    key = str(body.get("api_key", "")).strip() or cfg.get("유튜브_API_키", "")
    found = next((a for a in cfg["유튜브_계정"] if a["url"] == url), None)
    if found:
        selected = next((s for s in ("person", "mindam") if cfg.get("유튜브_선택_" + s) == found["id"]), "")
        assigned = found.get("slot") or selected or ("mindam" if (found.get("profile") or {}).get("유형") == "이야기형" else "")
        if slot and assigned and slot != assigned:
            raise ValueError("저장된 계정의 제작 유형은 바꿀 수 없습니다. 다른 채널로 쓰려면 별도 계정으로 저장하세요.")
        found.update(name=name, api_key=key or found.get("api_key", ""), language=language)
        if not assigned and slot:
            found["slot"] = slot
    else:
        cfg["유튜브_계정"].append({"id": uuid.uuid4().hex, "name": name, "url": url, "api_key": key,
                                   "language": language, **({"slot": slot} if slot else {})})
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
    import 채널_프로필

    def locked(profile):
        value = copy.deepcopy(profile)
        return 채널_프로필.lock_brand_reference(slot, value)

    migrate(cfg)
    account = next((a for a in cfg["유튜브_계정"] if a["id"] == account_id), None)
    if not account:
        raise ValueError("저장된 유튜브 계정을 선택하세요.")
    selected_slot = next((s for s in ("person", "mindam") if cfg.get("유튜브_선택_" + s) == account_id), "")
    assigned_slot = account.get("slot") or selected_slot or ("mindam" if (account.get("profile") or {}).get("유형") == "이야기형" else "")
    if assigned_slot and assigned_slot != slot:
        label = "이야기형" if assigned_slot == "mindam" else "정보형"
        raise ValueError(f"이 계정은 {label} 채널용입니다. 해당 제작 탭을 선택해 적용하세요.")
    account["slot"] = slot
    previous = next((a for a in cfg["유튜브_계정"] if a["id"] == cfg.get("유튜브_선택_" + slot)), None)
    if previous is None:
        previous = next((a for a in cfg["유튜브_계정"] if a["url"] == cfg.get("내_채널" if slot == "person" else "민담_채널")), None)
    if previous:
        previous["profile"] = locked(current)
    profile = copy.deepcopy(account.get("profile") or current)
    if not account.get("profile") and (previous is None or previous["id"] != account_id):
        profile.update(이름=account["name"], 업로드_폴더=account["name"])
    profile["언어"] = account.get("language") or profile.get("언어", "ko")
    profile = locked(profile)
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
