# -*- coding: utf-8 -*-
"""
웹큐.py — 딥시크 '웹 채팅'(chat.deepseek.com)을 크롬 확장으로 조종할 때 쓰는 작업 큐 (API 비용 0원)

  공통_api.AI (AI="deepseek-web") → ask()  → 큐에 넣고 결과가 올 때까지 기다림
  대본선택.py                        → /api/web/next, /api/web/result 로 확장과 주고받음
  딥시크_확장/content.js             → chat.deepseek.com 탭에서 큐를 가져다 입력·전송·답변 수집
"""
import secrets, threading, time, uuid, os

_lock = threading.Lock()
_jobs: dict[str, dict] = {}          # id → {text, status: pending|taken|done|fail, result, error, created, taken_at, beat}
_event = threading.Condition(_lock)
LEASE_SEC = 240                      # 확장이 이 시간 동안 소식이 없으면 다른 탭이 다시 가져감
KEEP_SEC = 3600


def remote(operation, **data):
    import requests
    try:
        response = requests.post(os.environ["YOUTOO_WEB_BROKER"] + "/api/worker-web",
                                 json=dict(op=operation, **data),
                                 headers={"Authorization": "Bearer " + os.environ.get("YOUTOO_WEB_TOKEN", "")}, timeout=10)
        if response.status_code == 404:
            raise RuntimeError("기본 창의 제작이 끝난 뒤 [다시 시작]을 눌러 주세요. 동시 작업용 딥시크 연결을 적용해야 합니다.")
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        raise RuntimeError("기본 프로그램 창의 딥시크 연결에 응답이 없습니다. 기본 창을 켜 두세요.") from None


def broker_operation(body):
    """Only called after the server authenticates a local worker."""
    op, jid = body.get("op"), body.get("id")
    if op == "submit":
        return {"id": submit(str(body["text"]), body.get("meta"))}
    if op == "cancel":
        cancel(jid)
        return {"ok": True}
    if op == "alive":
        return {"alive": extension_alive(), **status()}
    if op == "poll":
        with _event:
            j = _jobs.get(jid)
            return {k: j.get(k) for k in ("status", "result", "error")} if j else {"status": "missing"}
    raise ValueError("알 수 없는 웹 연결 요청입니다.")


def _gc():
    now = time.time()
    for k in [k for k, j in _jobs.items() if now - j["created"] > KEEP_SEC]:
        _jobs.pop(k, None)


def submit(text: str, meta: dict | None = None) -> str:
    if os.environ.get("YOUTOO_WEB_BROKER"):
        return remote("submit", text=text, meta=meta)["id"]
    jid = uuid.uuid4().hex[:10]
    with _event:
        _gc()
        _jobs[jid] = dict(id=jid, text=text, meta=meta or {}, status="pending", result="", error="",
                          created=time.time(), taken_at=0.0, beat=0.0, progress="", claim="", lease_no=0)
        _event.notify_all()
    return jid


def cancel(jid: str) -> None:
    if os.environ.get("YOUTOO_WEB_BROKER"):
        remote("cancel", id=jid)
        return
    with _event:
        _jobs.pop(jid, None)


def wait(jid: str, timeout: float = 1800, cancel_check=None) -> str:
    """결과 텍스트를 돌려준다. 실패면 RuntimeError."""
    if os.environ.get("YOUTOO_WEB_BROKER"):
        end = time.monotonic() + timeout
        try:
            while time.monotonic() < end:
                if cancel_check and cancel_check():
                    raise RuntimeError("취소됨")
                value = remote("poll", id=jid)
                if value["status"] == "done":
                    return value["result"]
                if value["status"] in ("fail", "missing"):
                    raise RuntimeError(value.get("error") or "딥시크 작업이 종료되었습니다. 기본 창 연결을 확인하세요.")
                time.sleep(1)
            raise RuntimeError("딥시크 웹 응답 대기 시간 초과")
        finally:
            try:
                cancel(jid)
            except RuntimeError:
                pass
    end = time.time() + timeout
    while time.time() < end:
        with _event:
            j = _jobs.get(jid)
            if not j:
                raise RuntimeError("웹 작업이 사라졌습니다.")
            if j["status"] == "done":
                _jobs.pop(jid, None)
                return j["result"]
            if j["status"] == "fail":
                _jobs.pop(jid, None)
                raise RuntimeError("딥시크 웹 응답 실패: " + (j["error"] or "알 수 없음"))
            _event.wait(2)
        if cancel_check and cancel_check():
            cancel(jid)
            raise RuntimeError("취소됨")
    cancel(jid)
    raise RuntimeError("딥시크 웹 응답 대기 시간 초과 (30분). 확장 프로그램과 chat.deepseek.com 탭이 켜져 있는지 확인하세요.")


def next_job(wait_sec: float = 20) -> dict | None:
    """확장이 가져갈 다음 작업 (대기 중인 것 우선, 임대 만료된 것 재배정). 최대 wait_sec 동안 기다린다."""
    end = time.time() + wait_sec
    while True:
        with _event:
            now = time.time()
            for j in sorted(_jobs.values(), key=lambda x: x["created"]):
                if j["status"] == "pending" or (j["status"] == "taken" and now - max(j["taken_at"], j["beat"]) > LEASE_SEC):
                    claim = uuid.uuid4().hex
                    j["lease_no"] = int(j.get("lease_no") or 0) + 1
                    j["status"], j["taken_at"], j["beat"], j["claim"] = "taken", now, now, claim
                    return dict(id=j["id"], text=j["text"], meta=j["meta"], claim=claim)
            remain = end - now
            if remain <= 0:
                return None
            _event.wait(min(remain, 5))


def heartbeat(jid: str, progress: str = "", claim: str = "") -> bool:
    with _event:
        j = _jobs.get(jid)
        if not j or not _owns_lease(j, claim):
            return False
        j["beat"] = time.time()
        if progress:
            j["progress"] = progress
        return True


def finish(jid: str, result: str = "", error: str = "", claim: str = "") -> bool:
    with _event:
        j = _jobs.get(jid)
        if not j or not _owns_lease(j, claim):
            return False
        if error:
            j["status"], j["error"] = "fail", error
        else:
            j["status"], j["result"] = "done", result
        _event.notify_all()
        return True


def _owns_lease(job: dict, claim: str) -> bool:
    """새 확장은 토큰을 검사하고, 구 확장은 최초 임대에서만 호환한다."""
    if claim:
        return secrets.compare_digest(str(job.get("claim") or ""), str(claim))
    return int(job.get("lease_no") or 0) == 1


def status() -> dict:
    if os.environ.get("YOUTOO_WEB_BROKER"):
        try:
            value = remote("alive")
            return {"pending": value.get("pending", 0), "taken": value.get("taken", [])}
        except RuntimeError:
            return {"pending": 0, "taken": []}
    with _event:
        return dict(pending=sum(1 for j in _jobs.values() if j["status"] == "pending"),
                    taken=[dict(id=j["id"], progress=j["progress"], since=round(time.time() - j["taken_at"])) for j in _jobs.values() if j["status"] == "taken"])


_extension_seen = {"at": 0.0, "info": ""}

def extension_ping(info: str = "") -> None:
    _extension_seen["at"] = time.time(); _extension_seen["info"] = info

def extension_alive(sec: float = 40) -> bool:
    if os.environ.get("YOUTOO_WEB_BROKER"):
        try:
            return bool(remote("alive").get("alive"))
        except RuntimeError:
            return False
    return time.time() - _extension_seen["at"] < sec
