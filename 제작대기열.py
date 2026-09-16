# -*- coding: utf-8 -*-
"""연속 제작 대기열의 안전한 저장과 텔레그램 알림."""
from __future__ import annotations

import datetime
import json
import os
import tempfile
import threading
from pathlib import Path

import requests

상태_한글 = {
    "idle": "대기 없음", "running": "연속 제작 중", "paused": "일시정지",
    "done": "전체 완료", "cancelled": "취소됨", "pending": "대기 중",
    "working": "제작 중", "error": "실패", "skipped": "건너뜀",
}


class QueueStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = self._load()

    def _blank(self):
        return {"status": "idle", "items": [], "options": {}, "current_id": "", "updated": ""}

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                return self._blank()
            for item in data["items"]:
                if item.get("status") == "working":
                    item["status"] = "pending"
            if data.get("status") == "running" and any(i.get("status") == "pending" for i in data["items"]):
                data["status"] = "running"
            elif data.get("status") == "running":
                data["status"] = "done"
                data["current_id"] = ""
            return data
        except (OSError, ValueError, TypeError):
            return self._blank()

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            fd, temp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                    f.flush(); os.fsync(f.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)

    def public(self):
        with self.lock:
            value = json.loads(json.dumps(self.data, ensure_ascii=False))
        value["status_text"] = 상태_한글.get(value.get("status"), value.get("status", ""))
        for item in value.get("items", []):
            item["status_text"] = 상태_한글.get(item.get("status"), item.get("status", ""))
        return value


def telegram_call(token, method, payload=None, timeout=15):
    token = (token or "").strip()
    if not token:
        raise ValueError("텔레그램 봇 토큰을 먼저 저장하세요.")
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {}, timeout=timeout)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("텔레그램 서버 응답을 읽지 못했습니다.") from exc
    if not response.ok or not data.get("ok"):
        raise RuntimeError("텔레그램 오류: " + str(data.get("description") or response.status_code))
    return data.get("result")


def recent_chats(token):
    updates = telegram_call(token, "getUpdates", {"limit": 100, "timeout": 0}) or []
    found = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            continue
        title = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or str(chat["id"])
        found[str(chat["id"])] = {"id": str(chat["id"]), "name": title, "type": chat.get("type", "")}
    return list(found.values())[::-1]


def send_telegram(token, chat_id, text):
    if not (token and chat_id):
        return False
    telegram_call(token, "sendMessage", {"chat_id": str(chat_id), "text": text})
    return True
