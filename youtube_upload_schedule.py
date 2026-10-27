"""Persistent, local-time upload reservations for reviewed YouTube packages.

The scheduler only uploads packages the user explicitly reserves. It always
uses private visibility, never publishes publicly, and turns interrupted runs
into a manual-review state instead of retrying an ambiguous upload.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
import secrets

_LOCK = threading.RLock()
MAX_LONG_FORMS_PER_ACCOUNT_DAY = 2
ACTIVE = {"pending", "working", "done", "attention"}


def _path(root):
    return Path(root).resolve() / "대본" / "_상태" / "예약업로드.json"


def _read(root):
    path = _path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"items": []}
    except (OSError, ValueError) as exc:
        raise RuntimeError("예약 업로드 기록을 읽지 못했습니다. 원본 파일을 보존하고 로그를 확인하세요.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise RuntimeError("예약 업로드 기록 형식이 올바르지 않습니다. 원본 파일을 보존하고 복구하세요.")
    return value


def _write(root, value):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _parse_time(value):
    try:
        when = dt.datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("예약 날짜와 시간을 다시 선택하세요.") from exc
    if when.tzinfo is not None:
        raise ValueError("예약 시간은 이 컴퓨터의 현지 시각으로 입력하세요.")
    if when < dt.datetime.now() + dt.timedelta(minutes=1):
        raise ValueError("예약 시간은 현재 시각보다 1분 이상 뒤로 선택하세요.")
    return when.replace(second=0, microsecond=0)


def list_items(root):
    with _LOCK:
        return [dict(item) for item in _read(root)["items"]]


def add(root, *, package_id, package_name, folder, account_id, channel_name, scheduled_at, include_shorts):
    when = _parse_time(scheduled_at)
    with _LOCK:
        data = _read(root)
        items = data["items"]
        for item in items:
            if item.get("package_id") == package_id and item.get("status") in ACTIVE:
                raise ValueError("이 패키지는 이미 예약되었거나 업로드 처리 중입니다.")
        daily = [item for item in items if item.get("account_id") == account_id
                 and item.get("scheduled_at", "")[:10] == when.date().isoformat()
                 and item.get("status") in ACTIVE]
        if len(daily) >= MAX_LONG_FORMS_PER_ACCOUNT_DAY:
            raise ValueError(f"이 채널은 하루 롱폼 {MAX_LONG_FORMS_PER_ACCOUNT_DAY}편까지 예약할 수 있습니다. 날짜를 바꾸거나 기존 예약을 취소하세요.")
        row = {"id": secrets.token_hex(8), "package_id": str(package_id), "package_name": str(package_name),
               "folder": str(Path(folder).resolve()),
               "account_id": str(account_id), "channel_name": str(channel_name),
               "scheduled_at": when.isoformat(timespec="minutes"), "include_shorts": bool(include_shorts),
               "visibility": "private", "status": "pending", "created_at": dt.datetime.now().isoformat(timespec="seconds"),
               "result": {}, "error": ""}
        items.append(row)
        _write(root, data)
        return dict(row)


def cancel(root, reservation_id):
    with _LOCK:
        data = _read(root)
        item = next((x for x in data["items"] if x.get("id") == reservation_id), None)
        if not item:
            raise ValueError("예약 목록에서 항목을 찾지 못했습니다.")
        if item.get("status") != "pending":
            raise ValueError("업로드가 시작된 예약은 여기서 취소할 수 없습니다.")
        item.update(status="cancelled", cancelled_at=dt.datetime.now().isoformat(timespec="seconds"))
        _write(root, data)
        return dict(item)


def claim_due(root, now=None):
    now = now or dt.datetime.now()
    with _LOCK:
        data = _read(root)
        for item in sorted(data["items"], key=lambda x: x.get("scheduled_at", "")):
            if item.get("status") != "pending":
                continue
            try:
                when = dt.datetime.fromisoformat(item["scheduled_at"])
            except (KeyError, TypeError, ValueError):
                item.update(status="attention", error="예약 날짜 형식이 손상되었습니다.")
                _write(root, data)
                continue
            if when <= now:
                attempted_today = sum(
                    other.get("account_id") == item.get("account_id")
                    and other.get("started_at", "")[:10] == now.date().isoformat()
                    and other.get("status") in {"working", "done", "attention"}
                    for other in data["items"]
                )
                if attempted_today >= MAX_LONG_FORMS_PER_ACCOUNT_DAY:
                    continue
                item.update(status="working", started_at=now.isoformat(timespec="seconds"))
                _write(root, data)
                return dict(item)
        return None


def release(root, reservation_id):
    """Return a claimed item to pending when the app was busy before upload began."""
    with _LOCK:
        data = _read(root)
        item = next((x for x in data["items"] if x.get("id") == reservation_id), None)
        if not item or item.get("status") != "working":
            return False
        item.update(status="pending", started_at="")
        _write(root, data)
        return True


def finish(root, reservation_id, *, result=None, error=""):
    with _LOCK:
        data = _read(root)
        item = next((x for x in data["items"] if x.get("id") == reservation_id), None)
        if not item or item.get("status") != "working":
            raise ValueError("진행 중인 예약 기록이 바뀌어 결과를 연결하지 못했습니다.")
        item.update(status="done" if not error else "attention", result=result or {}, error=str(error)[:1000],
                    finished_at=dt.datetime.now().isoformat(timespec="seconds"))
        _write(root, data)
        return dict(item)


def recover_interrupted(root):
    """Never retry a reservation whose process died during upload."""
    with _LOCK:
        data = _read(root)
        changed = False
        for item in data["items"]:
            if item.get("status") == "working":
                item.update(status="attention", error="프로그램이 업로드 중 종료됐습니다. YouTube Studio에서 수신 여부를 확인하세요.",
                             recovered_at=dt.datetime.now().isoformat(timespec="seconds"))
                changed = True
        if changed:
            _write(root, data)
        return sum(x.get("status") == "attention" for x in data["items"])


def public_items(root):
    rows = list_items(root)
    result = []
    for row in sorted(rows, key=lambda x: x.get("scheduled_at", "")):
        uploaded = (row.get("result") or {}).get("uploaded") or []
        safe_result = {"uploaded": [{key: item.get(key) for key in ("file", "title", "video_id", "url", "skipped")}
                                     for item in uploaded if isinstance(item, dict)]}
        safe_result["channel"] = (row.get("result") or {}).get("channel")
        result.append({key: row.get(key) for key in ("id", "package_id", "package_name", "channel_name",
                                                       "scheduled_at", "include_shorts", "visibility", "status", "error")}
                      | {"result": safe_result})
    return result
