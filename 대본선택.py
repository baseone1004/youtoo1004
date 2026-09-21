# -*- coding: utf-8 -*-
"""
대본선택.py — 주제를 고르면 DeepSeek(설정.json 의 AI)으로 대본을 뽑아 주는 로컬 웹 화면

  대본선택.bat  →  http://127.0.0.1:8766  이 열립니다.
  · 심리해독소: 주제_리포트(계획.json·후보.json)의 주제 또는 직접 입력 → 대본생성.py 의 9구간 대본
  · 민담·야담:   제목이나 장르 입력 → 민담_대본.py 의 기획→챕터→합본 대본
  · 이미지 프롬프트: 만든 대본을 문장별 이미지 프롬프트(===001=== 형식)로 변환
  지침은 지침/ 폴더의 txt 를 골라 쓰고, 화면에서 바로 고쳐 저장할 수 있습니다.
"""
import sys, os, re, io, json, glob, time, threading, datetime, webbrowser, urllib.parse, subprocess, shutil, uuid, mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)

from 공통_api import AI, fix_script_sentences, find_issues
import 대본생성
import 민담_대본
import 최적화
import 나레이션
import 웹큐
import 채널_연동
import 채널_프로필
import 주제_추천
import 유튜브_API
import 썸네일_합성
import requests as _rq
from 제작대기열 import QueueStore, recent_chats, send_telegram

PORT = int(os.environ.get("SCRIPT_UI_PORT") or 8766)      # 확인용으로 다른 포트에서 띄울 때만 바꾼다
VERSION = str(int(os.path.getmtime(os.path.abspath(__file__))))      # 파일이 바뀌면 값이 달라진다 → 화면이 "다시 시작 필요"를 안다
지침_폴더 = "지침"
대본_폴더 = "대본"
ADDR = f"http://127.0.0.1:{PORT}"


# ── 작업 상태 (한 번에 하나) ─────────────────────────────────────
class Job:
    def __init__(self, kind):
        self.kind, self.status, self.log, self.result, self.error = kind, "running", [], {}, ""
        self.started = datetime.datetime.now().strftime("%H:%M:%S")
        self._buf = ""
        self.stage, self.progress, self.cancel_requested = "", 0.0, False
    def write(self, s):           # sys.stdout 대체 — 공통_api 의 진행 점(.)도 받는다
        REAL.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.log.append(line.rstrip())
        if len(self._buf) > 200:                 # 줄바꿈 없이 점만 올 때
            self.log.append(self._buf); self._buf = ""
    def flush(self): REAL.flush()
    def add(self, s): self.write(s + "\n")
    def to_dict(self):
        return dict(kind=self.kind, status=self.status, log=self.log[-3000:], result=self.result, error=self.error,
                    started=self.started, partial=self._buf[-60:], stage=self.stage, progress=round(self.progress, 3),
                    cancel_requested=self.cancel_requested)

REAL = sys.stdout
STATE = {"job": None}
LOCK = threading.Lock()
import 공통_api as _공통
_공통.cancel_hook = lambda: bool(STATE.get("job") and STATE["job"].cancel_requested)   # AI 응답을 기다리는 중에도 [■ 중단] 이 바로 먹는다
QUEUE = QueueStore(os.path.join(대본_폴더, "_상태", "제작대기열.json"))
QUEUE_THREAD = None


RESTART = {"requested": False}


def restart_program(server):
    """서버를 멈추게만 하고, 새 프로세스는 main() 이 serve_forever 를 빠져나온 뒤 주 스레드에서 띄운다 (화면의 [다시 시작] 버튼).
    (요청 스레드는 데몬이라 serve_forever 가 끝나는 순간 인터프리터가 정리되면서 죽을 수 있어 여기서 Popen 을 하면 새 프로세스가 안 뜬다.)"""
    time.sleep(0.5)
    RESTART["requested"] = True
    try:
        server.shutdown()                       # serve_forever 종료 (응답은 이미 보냈다) → main() 이 spawn_restart() 를 부른다
    except Exception:  # noqa: BLE001
        pass


def spawn_restart():
    """같은 명령으로 새 프로세스를 띄우고 이 프로세스는 끝낸다. 서버 소켓은 이미 닫힌 뒤에 부른다."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    log = open(os.path.join(BASE, "로그_대본선택.txt"), "a", encoding="utf-8")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--no-browser", "--wait-port"], cwd=BASE, env=env,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
    os._exit(0)


def shutdown_program(server):
    """Stop the editor's image runner, then close both local servers.
    만들던 편은 '대기 중'으로 되돌려 두어, 다음에 켜면 하던 데까지 이어서 만든다."""
    with QUEUE.lock:
        if QUEUE.data.get("status") == "running":
            QUEUE.data["status"] = "paused"
            QUEUE.data["resume_on_start"] = True
            QUEUE.save()
    job = STATE["job"]
    if job and job.status == "running":
        job.cancel_requested = True
    try:
        _rq.post("http://127.0.0.1:8765/api/gen/stop", json={}, timeout=3)
    except _rq.RequestException:
        pass  # The editor may already be closed.
    # The editor runs as a separate pythonw process. Only stop the process
    # listening on its dedicated local port after checking its command line.
    if os.name == "nt":
        script = (
            "$conn=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 "
            "-State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if($conn){$proc=Get-CimInstance Win32_Process "
            "-Filter \"ProcessId=$($conn.OwningProcess)\"; "
            "if($proc.CommandLine -match '(^|[\\\\/ ])app\\.py( |$)'){"
            "Stop-Process -Id $conn.OwningProcess -ErrorAction Stop}}"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           timeout=10, capture_output=True, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    server.shutdown()


KIND_LABEL = {"bench": "채널 벤치마킹", "script": "대본 만들기", "mindam": "이야기 대본 만들기", "images": "이미지 프롬프트", "variations": "제목 변형",
              "optimize": "제목·설명·태그", "tts": "나레이션", "pipeline": "한 편 자동 제작", "queue_pipeline": "연속 제작", "thumbnail": "썸네일"}


def run_job(kind, fn):
    with LOCK:
        if STATE["job"] and STATE["job"].status == "running":
            raise RuntimeError(f"지금 '{KIND_LABEL.get(STATE['job'].kind, STATE['job'].kind)}' 작업이 돌고 있습니다 ({STATE['job'].stage or '준비 중'}). "
                               "끝난 뒤 다시 누르거나, 바로 하려면 [■ 중단]을 먼저 누르세요.")
        job = Job(kind); STATE["job"] = job
    def _t():
        sys.stdout = job
        try:
            job.result = fn(job) or {}
            job.status = "done"
        except SystemExit as e:
            job.status, job.error = "error", str(e)
        except Exception as e:     # noqa: BLE001
            job.status, job.error = "error", f"{e.__class__.__name__}: {e}"
        finally:
            sys.stdout = REAL
    threading.Thread(target=_t, daemon=True).start()
    return job


# ── 데이터 ───────────────────────────────────────────────────
def load_json(name, default):
    try:
        with open(name, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def guideline_files():
    files = sorted(glob.glob(os.path.join(지침_폴더, "*.txt")))
    script = [os.path.basename(f) for f in files if "대본지침" in os.path.basename(f)]     # 대본 지침만 (썸네일·최적화 지침은 자동으로 쓰인다)
    image = [os.path.basename(f) for f in files if "이미지" in os.path.basename(f) and "참고" not in os.path.basename(f)]
    mindam = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(지침_폴더, "민담", "*.txt")))
              if not os.path.basename(f).startswith("원본") and "최근_사용" not in f]
    return dict(script=script, image=image, mindam=mindam)

def script_files():
    out = []
    for p in glob.glob(os.path.join(대본_폴더, "*.txt")) + glob.glob(os.path.join(대본_폴더, "민담", "*", "final.txt")):
        base = os.path.basename(p)
        if any(base.endswith(x) for x in ("_이미지프롬프트.txt", "_플로우.txt", "_유튜브최적화.txt", "_썸네일.txt", "_메타.txt", "_테스트.txt"))                 or "프롬프트" in base or "썸네일" in base:
            continue       # 대본 본문 파일만
        out.append(dict(path=p, name=(os.path.basename(os.path.dirname(p)) + "/final.txt") if p.endswith("final.txt") else os.path.basename(p),
                        mtime=os.path.getmtime(p)))
    out.sort(key=lambda x: -x["mtime"])
    return out


def reset_items():
    """대본과 중간 생성물을 삭제 대상으로 나열한다. 폴더 밖 경로는 받지 않는다."""
    root = os.path.realpath(대본_폴더)
    out = []
    for p in glob.glob(os.path.join(root, "*.txt")):
        name = os.path.basename(p)
        if any(name.endswith(s) for s in ("_이미지프롬프트.txt", "_이미지프롬프트_플로우.txt",
                                           "_유튜브최적화.txt", "_썸네일.txt", "_메타.txt", "_테스트.txt")):
            continue
        out.append(dict(id=os.path.relpath(p, root), label=name, kind="person", partial=False))
    for p in glob.glob(os.path.join(root, "*_자료")):
        if os.path.isdir(p) and not os.path.isfile(p[:-3] + ".txt"):
            out.append(dict(id=os.path.relpath(p, root), label=os.path.basename(p) + " (대본 없는 작업 자료)",
                            kind="person", partial=True))
    folk_root = os.path.join(root, "민담")
    for p in glob.glob(os.path.join(folk_root, "*")):
        if os.path.isdir(p):
            out.append(dict(id=os.path.relpath(p, root), label=os.path.basename(p) +
                            (" (완성)" if os.path.isfile(os.path.join(p, "final.txt")) else " (작성 중)"),
                            kind="mindam", partial=not os.path.isfile(os.path.join(p, "final.txt"))))
    out.sort(key=lambda x: x["label"], reverse=True)
    return out


def reset_output(item_id, scope):
    """선택한 작업만 대본/_휴지통으로 이동한다. 실행 중인 작업은 건드리지 않는다."""
    with LOCK:
        if STATE["job"] and STATE["job"].status == "running":
            raise ValueError("작업이 진행 중입니다. 완료하거나 중단한 뒤 초기화하세요.")
        items = {item["id"]: item for item in reset_items()}
        if item_id not in items or scope not in ("all", "assets"):
            raise ValueError("삭제할 대본을 목록에서 다시 선택하세요.")
        item = items[item_id]
        root = os.path.realpath(대본_폴더)
        path = os.path.realpath(os.path.join(root, item_id))
        if os.path.commonpath((root, path)) != root:
            raise ValueError("허용되지 않은 경로입니다.")
        if item["kind"] == "mindam":
            if scope == "assets":
                raise ValueError("민담은 대본과 작업 자료를 함께 초기화할 수 있습니다.")
            targets = [path]
        else:
            base = os.path.splitext(path)[0] if path.endswith(".txt") else path[:-3]
            suffixes = ("_자료", "_이미지프롬프트.txt", "_이미지프롬프트_플로우.txt",
                        "_유튜브최적화.txt", "_썸네일.txt", "_메타.txt", "_테스트.txt")
            targets = [base + s for s in suffixes if os.path.exists(base + s)]
            if scope == "all" and os.path.isfile(base + ".txt"):
                targets.insert(0, base + ".txt")
        if not targets:
            raise ValueError("초기화할 파일이 없습니다.")
        trash = os.path.join(root, "_휴지통", datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
        os.makedirs(trash)
        for target in targets:
            shutil.move(target, os.path.join(trash, os.path.basename(target)))
        STATE["job"] = None
        return dict(count=len(targets), trash=os.path.abspath(trash))


def reset_everything():
    """모든 작업(대본·자료·민담·업로드 폴더·대기열·사용한 주제)을 대본/_휴지통/날짜_전체초기화/ 로 옮긴다. 설정은 그대로."""
    with LOCK:
        if STATE["job"] and STATE["job"].status == "running":
            raise ValueError("작업이 진행 중입니다. 중단한 뒤 초기화하세요.")
        root = os.path.realpath(대본_폴더)
        trash = os.path.join(root, "_휴지통", datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_전체초기화")
        os.makedirs(trash, exist_ok=True)
        moved = 0
        for name in os.listdir(root) if os.path.isdir(root) else []:
            if name in ("_휴지통", "_상태"):
                continue
            src = os.path.join(root, name)
            if name == "민담" and os.path.isdir(src):
                for sub in os.listdir(src):
                    shutil.move(os.path.join(src, sub), os.path.join(trash, "민담_" + sub)); moved += 1
                continue
            shutil.move(src, os.path.join(trash, name)); moved += 1
        upload_root = os.path.join(BASE, "업로드")
        if os.path.isdir(upload_root):
            for ch in os.listdir(upload_root):
                for sub in os.listdir(os.path.join(upload_root, ch)):
                    shutil.move(os.path.join(upload_root, ch, sub), os.path.join(trash, "업로드_" + sub)); moved += 1
        for used in ("사용한_주제.txt", "민담_사용한_주제.txt"):     # 이미 만든 주제 기록은 남긴다 (지우면 올린 주제가 추천에 다시 나온다)
            if os.path.isfile(used):
                shutil.copy2(used, os.path.join(trash, used))
        with QUEUE.lock:
            QUEUE.data = {"status": "idle", "items": [], "options": {}, "current_id": "", "updated": ""}
            QUEUE.save()
        STATE["job"] = None
        for s in SEEN_TOPICS.values():
            s.clear()
        return dict(count=moved, trash=os.path.abspath(trash))


TRASH_KEEP_DAYS = 30


def trash_dir():
    return os.path.join(os.path.realpath(대본_폴더), "_휴지통")


def trash_status():
    """휴지통 항목 수·용량·가장 오래된 날짜."""
    root = trash_dir()
    items, total, oldest = 0, 0, None
    if os.path.isdir(root):
        for name in os.listdir(root):
            path = os.path.join(root, name)
            items += 1
            mtime = os.path.getmtime(path)
            oldest = mtime if oldest is None or mtime < oldest else oldest
            for dp, _, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
    return dict(items=items, bytes=total, gb=round(total / 1024 ** 3, 2),
                oldest=datetime.datetime.fromtimestamp(oldest).strftime("%Y-%m-%d") if oldest else "", keep_days=TRASH_KEEP_DAYS)


def empty_trash(older_than_days=None):
    """휴지통을 비운다. older_than_days 를 주면 그보다 오래된 항목만 지운다."""
    root = trash_dir()
    removed = 0
    if not os.path.isdir(root):
        return dict(removed=0)
    cutoff = time.time() - (older_than_days or 0) * 86400
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if older_than_days is not None and os.path.getmtime(path) > cutoff:
            continue
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
        removed += 1
    return dict(removed=removed)


def delete_script(script_file):
    """화면의 대본 목록에서 선택된 파일만 삭제한다."""
    if script_file not in {item["path"] for item in script_files()}:
        raise ValueError("대본 목록에서 파일을 다시 선택하세요.")
    root = os.path.realpath(대본_폴더)
    selected = os.path.realpath(script_file)
    target = os.path.dirname(selected) if os.path.basename(selected) == "final.txt" else selected
    return reset_output(os.path.relpath(target, root), "all")

def topics():
    plan = load_json("계획.json", [])
    cands = load_json("후보.json", [])
    def used_titles(path):
        if not os.path.exists(path):
            return set()
        with open(path, encoding="utf-8-sig") as f:
            return {line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")}

    used_person = used_titles("사용한_주제.txt")
    used_mindam = used_titles("민담_사용한_주제.txt")
    for t in plan:
        t["done"] = bool(t.get("대본파일") and os.path.exists(t["대본파일"]))
    # 이미 대본을 만들었거나 사용 기록에 들어간 제목은 다시 선택하지 않도록 목록에서 숨긴다.
    plan = [t for t in plan if not t.get("done") and t.get("제목", "").strip() not in used_person]
    # 심리해독소는 계획을 우선하고, 후보를 더해 화면 전체에서 최대 6개만 추천한다.
    plan_titles = {t.get("제목", "").strip() for t in plan}
    cands = [t for t in cands if t.get("제목", "").strip() not in used_person
             and t.get("제목", "").strip() not in plan_titles]
    mindam = [t for t in load_json("민담_후보.json", []) if t.get("제목", "").strip() not in used_mindam]
    # [새 주제] 로 AI 가 만들어 둔 추천도 후보에 더한다 (사용한 주제는 제외)
    extra = 주제_추천.load()
    seen_titles = {t.get("제목", "").strip() for t in plan + cands}
    cands = [t for t in extra["person"] if t.get("제목", "").strip() not in used_person and t.get("제목", "").strip() not in seen_titles] + cands
    seen_m = {t.get("제목", "").strip() for t in mindam}
    mindam = [t for t in extra["mindam"] if t.get("제목", "").strip() not in used_mindam and t.get("제목", "").strip() not in seen_m] + mindam
    # 내 유튜브 채널에 이미 올라간 제목과 겹치는 주제는 뺀다 (설정의 내_채널 · 민담_채널). 저장본이 오래됐으면 뒤에서 다시 받는다.
    cfg = load_json("설정.json", {})
    for ch in ("person", "mindam"):
        채널_연동.fetch_in_background(cfg, ch)
    plan = 채널_연동.filter_topics(cfg, "person", plan)
    cands = 채널_연동.filter_topics(cfg, "person", cands)
    mindam = 채널_연동.filter_topics(cfg, "mindam", mindam)
    # [새 주제] 를 누르면 이미 보여 준 것은 뒤로 미룬다 (계획+후보를 한 줄로 놓고 6개씩)
    def unseen(items, channel):
        return [t for t in items if t.get("제목", "").strip() not in SEEN_TOPICS[channel]]
    fresh = sorted([t for t in cands if t.get("생성")], key=lambda t: t.get("생성", ""), reverse=True)   # [새 주제]·벤치마킹 뒤 AI 가 만든 것 — 가장 새것이 먼저
    person = unseen(fresh + plan + [t for t in cands if not t.get("생성")], "person")[:6]
    plan_titles = {t.get("제목", "").strip() for t in plan}
    plan = [t for t in person if t.get("제목", "").strip() in plan_titles]
    cands = [t for t in person if t.get("제목", "").strip() not in plan_titles]
    mindam = unseen(mindam, "mindam")[:6]
    return dict(plan=plan, candidates=cands, mindam=mindam)


SEEN_TOPICS = {"person": set(), "mindam": set()}


def run_benchmark(job, req):
    """주제뽑기.py(심리해독소) 또는 민담_주제뽑기.py(민담)를 실행해 비슷한 채널을 새로 찾고 계획·후보·트렌드·벤치_히트 를 갱신한다 (1~3분)."""
    mindam = (req or {}).get("channel") == "mindam"
    job.stage = "벤치마킹 채널 분석"
    job.add("▶ " + ("민담·야담 채널에서 터진 제목을 모읍니다" if mindam else "비슷한 심리 채널을 찾아 터진 영상을 모읍니다") + " (1~3분)")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "민담_주제뽑기.py" if mindam else "주제뽑기.py", "--no-browser"], cwd=BASE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        if line.strip():
            job.add("   " + line.rstrip())
        if job.cancel_requested:
            proc.kill(); raise RuntimeError("취소됨")
    if proc.wait() != 0:
        raise RuntimeError("주제뽑기 실행 실패 — 로그를 확인하세요.")
    for s in SEEN_TOPICS.values():
        s.clear()
    hits = 주제_추천.bench_hits(60)
    job.add(f"   ✓ 벤치마킹 완료 · 히트 영상 {len(hits)}개")
    if not mindam and hits:                          # 터진 영상을 근거로 새 주제 6개를 바로 만들어 추천 맨 위에 올린다
        try:
            job.stage = "새 주제 만들기"
            cfg = load_json("설정.json", {})
            channel = "person"
            exclude = [t.get("제목", "") for t in load_json("계획.json", []) + load_json("후보.json", [])] + list(채널_연동.titles(cfg, channel) or [])
            fresh = 주제_추천.generate(cfg, channel, 6, list(dict.fromkeys(x for x in exclude if x)), None, log=job.add)
            job.add(f"   ✓ 터진 영상을 참고한 새 주제 {len(fresh)}개")
        except Exception as e:  # noqa: BLE001
            job.add(f"   ! 새 주제 만들기 실패(추천 목록은 그대로): {e}")
    return dict(hits=len(hits), file=os.path.abspath("벤치_히트.json"))


def refresh_topics(channel, shown):
    """지금 보이는 주제를 '본 것'으로 표시하고, 남은 후보가 6개 미만이면 AI 로 새 주제를 만들어 채운다."""
    channel = "mindam" if channel == "mindam" else "person"
    SEEN_TOPICS[channel].update(str(t).strip() for t in (shown or []) if str(t).strip())
    cfg = load_json("설정.json", {})
    채널_연동.fetch_if_stale(cfg, channel, 10)         # 방금 올린 영상 제목도 걸러지도록 추천 직전에 내 채널을 다시 읽는다
    current = topics()
    have = current["mindam"] if channel == "mindam" else current["plan"] + current["candidates"]
    have = [t for t in have if t.get("제목", "").strip() not in SEEN_TOPICS[channel]]
    generated = 0
    if len(have) < 6:
        exclude = list(SEEN_TOPICS[channel]) + [t.get("제목", "") for t in have]
        for path in ("사용한_주제.txt", "민담_사용한_주제.txt"):
            if os.path.exists(path):
                with open(path, encoding="utf-8-sig") as f:
                    exclude += [l.strip() for l in f if l.strip() and not l.startswith("#")]
        exclude += 채널_연동.titles(cfg, channel)[:80]
        exclude += [s["name"] for s in script_files()]
        analysis = 채널_연동.analysis(cfg, channel)
        try:
            fresh = 주제_추천.generate(cfg, channel, 6 - len(have), list(dict.fromkeys(exclude)), analysis if analysis.get("ok") else None)
        except Exception as exc:  # noqa: BLE001      # AI 가 안 되면 본 것부터 다시 보여 준다
            if not have:
                SEEN_TOPICS[channel].clear()
                current = topics()
                have = current["mindam"] if channel == "mindam" else current["plan"] + current["candidates"]
            return dict(channel=channel, items=have[:6], generated=0, error=f"AI 추천 실패: {exc}")
        generated = len(fresh)
        have = fresh + have
        if not have:                                  # 새 것이 하나도 없으면 처음부터 다시 돌린다
            SEEN_TOPICS[channel].clear()
            current = topics()
            have = current["mindam"] if channel == "mindam" else current["plan"] + current["candidates"]
    return dict(channel=channel, items=have[:6], generated=generated)

def read_guideline(name, channel="person"):
    """지침 파일을 읽고 {{채널명}} 같은 자리표시자를 채널 프로필 값으로 채운다."""
    p = os.path.join(지침_폴더, name)
    if not os.path.abspath(p).startswith(os.path.abspath(지침_폴더)) or not os.path.exists(p):
        raise FileNotFoundError(name)
    with open(p, encoding="utf-8-sig") as f:
        return 채널_프로필.fill(f.read(), channel)

def write_guideline(name, text):
    p = os.path.join(지침_폴더, name)
    if not os.path.abspath(p).startswith(os.path.abspath(지침_폴더)):
        raise ValueError(name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)

def mask(key):
    """키를 화면에 보여 줄 때 앞 4자·뒤 4자만 남긴다."""
    key = (key or "").strip()
    if not key:
        return ""
    return key[:4] + "…" + key[-4:] if len(key) > 10 else key[:2] + "…"

def mark_used(title, path="사용한_주제.txt"):
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f]
    if title not in lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write(title + "\n")


# ── 작업 1: 심리해독소 대본 ───────────────────────────────────
def make_person_script(job, req):
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    topic = req.get("topic") or {}
    t = {"제목": topic.get("제목") or req.get("title", "").strip(), "카테고리": topic.get("카테고리", ""), "오프닝": topic.get("오프닝", ""),
         "다룰내용": topic.get("다룰내용", []), "출처후보": topic.get("출처후보", []), "태그": topic.get("태그", [])}
    if not t["제목"]:
        raise SystemExit("주제(제목)를 입력하거나 목록에서 고르세요.")
    guideline = req.get("guideline") or 채널_프로필.get("person")["지침"].get("대본") or "정보형_대본지침.txt"
    system = read_guideline(guideline, "person")
    cpm = int(cfg.get("분당_글자수", 270) or 270)
    requested = int(req.get("target") or cfg["대본_글자수"])
    minutes = max(25, min((25, 30, 35, 40), key=lambda m: abs(requested - m * cpm)))     # 롱폼만: 25분 미만은 만들지 않는다
    target = minutes * cpm
    n_parts = max(1, -(-target // 4500))          # 한 번에 4,500자 이하로 나눠 요청
    job.add(f"AI: {ai.name} ({ai.model}) · 목표 {target:,}자 ({minutes}분) · 지침 {guideline}")
    job.add(f"▶ {t['제목']}")
    full, body = 대본생성.generate(ai, system, t, target, n_parts)
    os.makedirs(대본_폴더, exist_ok=True)
    today = datetime.date.today().isoformat()
    name = f"{today}_{대본생성.safe_name(t['제목'])}.txt"
    path = os.path.join(대본_폴더, name)
    n = 2
    while os.path.exists(path):
        path = os.path.join(대본_폴더, f"{today}_{대본생성.safe_name(t['제목'])}({n}).txt"); n += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(full)
    # 계획.json 에 기록
    plan = load_json("계획.json", [])
    for p in plan:
        if p["제목"] == t["제목"]:
            p["대본파일"] = path
    with open("계획.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    if req.get("mark_used", True):
        mark_used(t["제목"])
    issues = find_issues(body)
    job.add(f"✓ 저장: {path} ({len(body):,}자)")
    for m in issues:
        job.add(f"· 확인: {m}")
    opt = ""
    if req.get("optimize", True):
        try:
            text, _ = 최적화.optimize(ai, "person", body, extra=full.split("[대본]", 1)[0][:1500], log=job.add)
            text = 대본생성.strip_english(text)
            opt = re.sub(r"\.txt$", "", path) + "_유튜브최적화.txt"
            with open(opt, "w", encoding="utf-8") as f:
                f.write(text)
            job.add(f"✓ 알고리즘 최적화: {opt}")
        except Exception as e:  # noqa: BLE001
            job.add(f"! 최적화 실패: {e}")
    job.add("비용: " + ai.cost_text())
    return dict(file=path, chars=len(body), issues=issues, cost=ai.cost_text(), title=t["제목"], opt=opt)


# ── 작업 2: 민담 대본 ─────────────────────────────────────────
def make_mindam_script(job, req):
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    topic = (req.get("title") or "").strip()
    if not topic:
        raise SystemExit("제목 또는 장르를 입력하세요.")
    job.add(f"AI: {ai.name} ({ai.model}) · {민담_대본.길이[str(req.get('length', '2'))]['이름']}")
    job.add(f"▶ {topic}")
    bench = req.get("bench") or None
    if bench:
        job.add(f"   벤치마킹 원본: {bench.get('채널', '')} · {bench.get('조회수', 0):,}회 · 평소의 {bench.get('배수', '')}배 → 슬롯 분해 후 알맹이를 바꿔 재창조")
        topic = (f"{topic}\n(벤치마킹 원본 — 다른 채널 '{bench.get('채널', '')}' 에서 조회수 {bench.get('조회수', 0):,}회, 평소의 {bench.get('배수', '')}배로 검증된 제목. "
                 "이 제목의 골격은 살리되 알맹이(주인공 수단·제약 / 관계·적대 / 반전 종류 / 소재)를 최소 하나 바꿔 새 이야기로 재창조한다. 원본 줄거리를 베끼지 않는다.)")
    variation = (req.get("variation") or "").strip()
    if variation:
        job.add("   베리에이션 확정: " + variation.split("\n")[0][:80])
    r = 민담_대본.generate(ai, topic, str(req.get("length", "2")), log=job.add, workdir=req.get("resume_dir") or None,
                        variation=variation)
    if req.get("mark_used", True):
        mark_used(re.sub(r"\s*\|\s*야담.*$", "", r["title"]).strip(), "민담_사용한_주제.txt")
        if bench:
            mark_used(bench.get("제목", ""), "민담_사용한_주제.txt")
        if req.get("reference"):
            mark_used(req["reference"], "민담_사용한_주제.txt")
    opt = ""
    if req.get("optimize", True):
        try:
            with open(r["final"], encoding="utf-8") as f:
                body = f.read()
            extra = ""
            if r.get("meta") and os.path.exists(r["meta"]):
                with open(r["meta"], encoding="utf-8") as f:
                    extra = f.read()[:1500]
            text, _ = 최적화.optimize(ai, "mindam", body, extra=extra, log=job.add)
            opt = os.path.join(r["dir"], "유튜브_최적화.txt")
            with open(opt, "w", encoding="utf-8") as f:
                f.write(text)
            job.add(f"✓ 알고리즘 최적화: {opt}")
        except Exception as e:  # noqa: BLE001
            job.add(f"! 최적화 실패: {e}")
    job.add("비용: " + ai.cost_text())
    return dict(file=r["final"], dir=r["dir"], chars=r["chars"], issues=r["issues"], cost=ai.cost_text(), title=r["title"],
                meta=r.get("meta"), opt=opt)


def make_optimize_only(job, req):
    """이미 만든 대본 파일 → 알고리즘 최적화만."""
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    path = req.get("script_file", "")
    if not path or not os.path.exists(path):
        raise SystemExit("대본 파일을 고르세요.")
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    is_final = os.path.basename(path) == "final.txt"
    channel = "mindam" if is_final else "person"
    body = script_body(raw)
    extra = raw.split("[대본]", 1)[0][:1500] if "[대본]" in raw else ""
    if is_final:
        mp = os.path.join(os.path.dirname(path), "유튜브_설명.txt")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as f:
                extra = f.read()[:1500]
    job.add(f"AI: {ai.name} ({ai.model}) · 채널 {channel} · {os.path.basename(path)}")
    text, titles = 최적화.optimize(ai, channel, body, extra=extra, log=job.add)
    if channel == "person":
        text = 대본생성.strip_english(text)
    out = os.path.join(os.path.dirname(path), "유튜브_최적화.txt") if is_final else re.sub(r"\.txt$", "", path) + "_유튜브최적화.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    job.add(f"✓ 저장: {out}")
    job.add("비용: " + ai.cost_text())
    return dict(file=out, opt=out, titles=titles, cost=ai.cost_text())


def make_timestamps(req):
    """민담 대본 폴더(chapter_N.txt) + SRT → 챕터 타임스탬프. 유튜브_설명·최적화 파일 끝에 [챕터] 로 붙인다."""
    import glob as _g
    d = req.get("dir", ""); srt = req.get("srt", "")
    if not os.path.isdir(d) or not os.path.isfile(srt):
        raise SystemExit("대본 폴더와 SRT 파일을 고르세요.")
    for cand in (os.path.join(BASE, "편집프로그램"), os.path.join(os.path.dirname(BASE), "편집프로그램"),
                 os.path.join(os.path.expanduser("~"), "Downloads", "편집프로그램")):   # 같이 배포된 편집프로그램 → 개발용 위치
        if os.path.isdir(cand):
            sys.path.insert(0, cand); break
    try:
        from core.srt_parser import parse_srt
        cues = parse_srt(srt)
    except Exception:
        cues = []
        import re as _re
        txt = open(srt, encoding="utf-8-sig", errors="replace").read()
        for m in _re.finditer(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->.*?\n(.*?)(?:\n\n|\Z)", txt, _re.S):
            cues.append(type("C", (), {"start": int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000,
                                       "text": m[5].strip()})())
    if not cues:
        raise SystemExit("SRT 에서 자막을 읽지 못했습니다.")
    def bg(t):
        t = re.sub(r"[^가-힣a-zA-Z0-9]", "", t); return {t[i:i + 2] for i in range(len(t) - 1)}
    files = sorted(_g.glob(os.path.join(d, "chapter_*.txt")),
                   key=lambda f: [int(x) for x in re.findall(r"\d+", os.path.basename(f))])
    n_ch = len({re.match(r"chapter_(\d+)", os.path.basename(f)).group(1) for f in files})
    L = {5: "1", 7: "2", 9: "3"}.get(n_ch)
    labels = {}
    if L:
        for ci, pi, n, beats, _ in 민담_대본.chapter_spec(민담_대본.길이[L]):
            labels.setdefault(ci, []).append(민담_대본.beats_text(beats))
    def fmt(sec):
        sec = int(sec); return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"
    lines = ["00:00 시작"]; pos = 0
    seen_ch = set()
    for f in files:
        ci = int(re.match(r"chapter_(\d+)", os.path.basename(f)).group(1))
        if ci in seen_ch:
            continue
        seen_ch.add(ci)
        head = open(f, encoding="utf-8").read().strip()[:80]
        hb = bg(head)
        best, best_i = 0, None
        for i in range(pos, len(cues)):
            cb = bg(cues[i].text)
            sc = len(hb & cb) / max(1, len(cb))
            if sc > best:
                best, best_i = sc, i
        if best_i is None or best < 0.3:
            continue
        pos = best_i + 1
        beats = " · ".join(labels.get(ci, [])) or f"{ci}장"
        lines.append(f"{fmt(cues[best_i].start)} {ci}장 — {beats}")
    text = "\n".join(lines)
    with open(os.path.join(d, "챕터_타임스탬프.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    for name in ("유튜브_설명.txt", "유튜브_최적화.txt"):
        mp = os.path.join(d, name)
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as f:
                cur = f.read()
            cur = re.sub(r"\n\[챕터\]\n.*?(?=\n\[|\Z)", "", cur, flags=re.S).rstrip() + "\n\n[챕터]\n" + text + "\n"
            with open(mp, "w", encoding="utf-8") as f:
                f.write(cur)
    return dict(text=text, file=os.path.join(d, "챕터_타임스탬프.txt"))


def make_variations(job, req):
    """검증된 제목 → 제목 변형 A~D. 제목을 부품으로 나눠 알맹이를 바꾼 4개."""
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    ref = (req.get("title") or "").strip()
    if not ref:
        raise SystemExit("레퍼런스 제목을 입력하세요.")
    system = read_guideline(os.path.join("민담", "00_베리에이션_지침.txt"))
    motif = 민담_대본.read("motif_bank.txt")
    recent = []
    if os.path.exists("민담_사용한_주제.txt"):
        with open("민담_사용한_주제.txt", encoding="utf-8") as f:
            recent = [l.strip() for l in f if l.strip() and not l.startswith("#")][-20:]
    bench = req.get("bench") or {}
    ref_line = ref + (f"  (다른 채널 '{bench.get('채널', '')}' 에서 {bench.get('조회수', 0):,}회, 평소의 {bench.get('배수', '')}배)" if bench else "")
    user = (f"[레퍼런스 제목]\n{ref_line}\n\n[최근 사용한 주제]\n" + ("\n".join(recent) or "(없음)") + f"\n\n[motif_bank]\n{motif}")
    job.add(f"AI: {ai.name} ({ai.model}) · 베리에이션 4개")
    job.add(f"▶ {ref}")
    text = ai.ask(system, user).replace("```", "").strip()
    options = []
    for key in "ABCD":
        blk = 민담_대본.block_of(text, key)
        if blk:
            m = re.search(r"제목\s*[:：]\s*(.+)", blk)
            options.append(dict(key=key, title=(m.group(1).strip() if m else key), text=blk))
    rec = 민담_대본.block_of(text, "추천")
    if not options:
        raise RuntimeError("베리에이션 형식을 읽지 못했습니다. 원문:\n" + text[:600])
    job.add(f"✓ {len(options)}개 · 추천: {rec[:60]}")
    job.add("비용: " + ai.cost_text())
    return dict(reference=ref, options=options, recommend=rec, raw=text, cost=ai.cost_text())


# ── 작업 3: 대본 → 이미지 프롬프트 ─────────────────────────────
전체화면 = ("full-bleed composition that fills the entire 16:9 frame edge to edge, no border, no frame, no paper margin, "
        "no rounded corners, no vignette, no picture-in-picture")

화풍 = {
    # ── 공통
    "실사": "photorealistic cinematic photography, natural lighting, 16:9 aspect ratio",
    "애니": "Korean webtoon style illustration, clean bold line art, flat cel-shaded coloring, 16:9 aspect ratio",
    "2D 일러스트": "Premium hand-drawn 2D editorial animation illustration for an adult Korean psychology channel, "
                  "clean confident ink outlines, refined expressive adult faces, flat layered shapes with subtle cel shading, "
                  "consistent character proportions, controlled warm cream and muted teal palette with restrained coral accents, "
                  "cinematic composition made with 2D color shapes, crisp readable focal point, 16:9 landscape. "
                  "Use the image already uploaded in the Dropshot References panel as the primary visual guide: "
                  "match its character design, facial features, hairstyle, linework and palette where relevant. "
                  "Do not copy its pose or background. No watercolor texture, pastel wash, photorealism, 3D render, text or watermark.",
    "파스텔": "hand-drawn 2D pastel illustration, soft colored-pencil and watercolor textures, clean illustrated faces and outlines, warm muted palette, 16:9 aspect ratio",
    "수묵": "traditional Korean ink wash painting style with subtle color, hanji paper texture, 16:9 aspect ratio",
    # ── 야담·민담·옛이야기용 (조선 배경 고정, 아동풍 금지, 테두리 없이 화면 가득)
    "밝은 애니 사극": "bright clean anime-style 2D illustration of a Joseon-era Korean folktale: large clear expressive eyes, smooth cel shading with soft gradients, "
                "cheerful saturated yet gentle colors, sunny daylight with soft lens glow, lush flowers and greenery, detailed hanok village and hanbok, "
                "youthful smooth faces, crisp clean outlines, " + 전체화면 + ", 16:9 aspect ratio",
    "웹툰 사극": "bright vivid Korean webtoon-style digital illustration of a Joseon-era folktale: clean confident linework, soft cel shading, "
              "saturated harmonious colors (pink, green, red, jade hanbok), warm sunny daylight or golden lamplight, "
              "expressive emotional faces with large clear eyes and smooth skin, detailed hanbok, gat and hanok, lively village backgrounds, "
              + 전체화면 + ", 16:9 aspect ratio",
    "수채 사극": "Korean historical drama illustration in a soft webtoon-watercolor style: delicate clean ink line art with a gentle watercolor wash, "
              "muted natural palette with soft light, refined East Asian faces drawn in a light anime-like manner (clear eyes, small nose, smooth skin), "
              "detailed Joseon hanbok, gat and hanok, painterly backgrounds with atmosphere such as snow, rain or lamplight, "
              + 전체화면 + ", 16:9 aspect ratio",
    "한지 동화": "warm storybook illustration for adults, soft gouache colors, gentle rounded shapes, "
              "Joseon-era village and hanbok, cozy oil-lamp glow at night, refined faces not childish, " + 전체화면 + ", 16:9 aspect ratio",
    "괴담 극화": "dark dramatic Korean gekiga-style illustration for ghost and folklore tales, heavy ink shadows, cold moonlight with a single warm lantern, "
              "Joseon-era hanok and forest, eerie but non-graphic, faces clearly visible, " + 전체화면 + ", 16:9 aspect ratio",
}
화풍_별칭 = {"민화": "수채 사극"}          # 예전 이름으로 저장된 설정·프롬프트를 새 화풍으로
화풍_설명 = {
    "실사": "사진 같은 시네마틱", "애니": "웹툰·셀 채색", "2D 일러스트": "선명한 성인용 2D·레퍼런스 유지", "파스텔": "부드러운 수채", "수묵": "한지·먹 느낌",
    "밝은 애니 사극": "밝고 맑은 애니풍 · 큰 눈 · 꽃과 햇살 (기본)", "웹툰 사극": "밝고 선명한 웹툰풍 (요즘 야담 채널식)", "수채 사극": "맑은 선 + 수채 웹툰풍", "한지 동화": "따뜻한 그림책 (어른용)", "괴담 극화": "귀신·도깨비 이야기용 어둡고 극적",
}
화풍_그룹 = {"공통": ["실사", "애니", "2D 일러스트", "파스텔", "수묵"],
          "야담·민담·옛이야기": ["밝은 애니 사극", "웹툰 사극", "수채 사극", "한지 동화", "괴담 극화"]}

def image_style_lock(style, channel=None):
    """선택한 화풍이 뒤의 장면 설명과 충돌해도 사진풍으로 바뀌지 않게 고정한다. 채널을 주면 그 채널의 색감 문구(화풍_접미)를 덧붙인다."""
    style = 화풍_별칭.get(style, style)
    if style not in 화풍:
        style = "밝은 애니 사극" if channel == "mindam" else "2D 일러스트"
    base = 화풍[style]
    tail = (" " + 채널_프로필.style_tail(channel) + ".") if channel and 채널_프로필.style_tail(channel) else ""
    if style == "실사":
        return base + tail + " Every person shown is Korean with East Asian facial features; never Western faces."
    age = ("AGE LOCK: draw every character at exactly the age written for them — a person in their 20s or 30s looks young "
           "(smooth skin, black hair, no wrinkles, upright posture); only a character explicitly described as old gets grey hair and wrinkles. "
           "FRAME LOCK: the picture fills the whole frame edge to edge with no border, frame, paper margin or vignette. "
           if channel == "mindam" else "")
    if channel == "person" and (채널_프로필.get("person").get("인물_표현") or "캐릭터") == "캐릭터":
        people = ("CHARACTER LOCK: every person in the scene is the same kind of creature as the uploaded reference @image 1 — identical head shape, "
                  "body type, limbs, face style and rendering — distinguished only by clothing, hair accessories, colors and props. "
                  "Never draw a human: no human faces, no human anatomy, no realistic or anime people. Modern Korean setting. ")
    else:
        people = ("PEOPLE LOCK: every person shown is Korean with East Asian facial features, dark hair and Korean clothing and setting; "
                  "never Western, Caucasian, Black, South Asian or Southeast Asian faces. ")
    return (f"STRICT STYLE LOCK: every image must be {style} style. {base}{tail} " + people + age +
            "Keep the same linework, character design, proportions and color palette as the uploaded Dropshot reference image. "
            "If a later scene description conflicts, this style lock takes priority. "
            "Never generate a photo, photorealistic face, live-action still, realistic skin texture, 3D render or mixed-media image.")


def restyle_prompts(prompts_file, style, channel):
    """이미 만든 이미지 프롬프트 파일의 화풍 고정 문구를 지금 고른 화풍으로 바꾼다 (장면 설명은 그대로). 바뀐 블록 수를 돌려준다."""
    if not os.path.isfile(prompts_file):
        return 0
    new_lock = image_style_lock(style, channel)
    with open(prompts_file, encoding="utf-8-sig") as f:
        text = f.read()
    changed = [0]

    def fix(m):
        head, body = m.group(1), m.group(2)
        if "mixed-media image." in body:              # 화풍 문구 전체(끝 표시까지, 겹쳐 있으면 마지막 것까지)를 걷어 낸다
            stripped = re.sub(r"STRICT STYLE LOCK:.*mixed-media image[.,]?\s*", "", body, count=1, flags=re.S)
        else:
            stripped = re.sub(r"STRICT STYLE LOCK:.*?consistent channel look\.?\s*", "", body, count=1, flags=re.S)
        if stripped == body and "STRICT STYLE LOCK:" in body:
            return m.group(0)                      # 어디까지가 화풍 문구인지 모르면 손대지 않는다
        rebuilt = head + new_lock + " " + stripped.strip()
        if rebuilt == m.group(0):
            return m.group(0)
        changed[0] += 1
        return rebuilt

    text2 = re.sub(r"^(프롬프트\s*[:：]\s*)(.+)$", fix, text, flags=re.M)
    if changed[0]:
        with open(prompts_file, "w", encoding="utf-8") as f:
            f.write(text2)
    return changed[0]

예전_나누기_표시 = "문장나누기_예전방식.txt"     # 자료 폴더에 이 파일이 있으면 그 편은 끝까지 예전 방식(따옴표 그대로)으로 문장을 나눈다


def legacy_split(script_file):
    """이 편을 예전 문장 나누기(따옴표 처리 없음)로 만들어야 하는지. 예전 방식으로 나레이션을 이미 만든 편은 번호가 밀리지 않도록 그대로 간다."""
    if not script_file:
        return False
    try:
        assets = assets_dir(script_file)
    except OSError:
        return False
    marker = os.path.join(assets, 예전_나누기_표시)
    if os.path.isfile(marker):
        return True
    flow = os.path.join(assets, "플로우.txt")
    if not os.path.isfile(flow):
        return False
    try:
        with open(flow, encoding="utf-8-sig") as f:
            n_flow = sum(1 for ln in f if re.match(r"^\s*\d+\s*:", ln))
        with open(script_file, encoding="utf-8-sig") as f:
            body = fix_script_sentences(script_body(f.read()))
        old, new = 나레이션.split_sentences(body, quotes=False), 나레이션.split_sentences(body, quotes=True)
        if n_flow == len(old) and n_flow != len(new):
            with open(marker, "w", encoding="utf-8") as f:
                f.write("이 편은 예전 문장 나누기(따옴표 그대로)로 나레이션을 만들었으므로 끝까지 같은 방식으로 만든다.\n")
            return True
    except OSError:
        pass
    return False


def split_sentences(body, script_file=None):
    """이미지 프롬프트·나레이션·자막이 같은 문장 번호를 쓰도록 한 곳에서 나눔. 편(script_file)을 주면 그 편의 방식을 따른다."""
    return 나레이션.split_sentences(body, quotes=not legacy_split(script_file))

def script_body(text):
    if "[대본]" in text:
        text = text.split("[대본]", 1)[1]
    if "===sum===" in text:
        text = text.split("===sum===", 1)[0]
    return text.strip()

def image_guideline_for(script_file, requested=""):
    """채널 프로필에 정한 이미지 지침. 화면에서 고른 지침이 다른 채널의 기본 지침이면 이 채널의 것으로 바꾼다."""
    channel = channel_of(script_file)
    builtin = {"person": "이미지지침_정보형.txt", "mindam": "이미지지침_이야기형.txt"}
    exists = lambda n: bool(n) and os.path.isfile(os.path.join(지침_폴더, n))
    own = 채널_프로필.get(channel)["지침"].get("이미지") or ""
    if not exists(own):
        own = builtin[channel]
    requested = (requested or "").strip()
    # 화면에서 고른 것이 채널 기본 지침들(정보형/이야기형 또는 각 프로필의 기본) 중 하나면 이 채널의 것으로 바꾼다
    channel_defaults = set(builtin.values()) | {(채널_프로필.get(slot)["지침"].get("이미지") or "") for slot in 채널_프로필.SLOTS}
    if not requested or requested in channel_defaults or not exists(requested):
        return own
    return requested                          # 사용자가 따로 만든 지침 파일이면 그대로


ECHO_MARKS = ("[이번 범위 원문]", "[프로그램과 맞추는 규칙", "[출력 형식 — 그대로]", "[내보내기 전 점검")


def prompt_blocks(text):
    """===NNN=== 장면 블록 가운데 '프롬프트:' 줄이 있는 것만 번호 → 블록 본문으로 돌려준다.
    지침·요청문이 답변 대신 섞여 들어온 경우(딥시크 웹이 보낸 질문을 그대로 돌려준 경우)의 예시 블록은 버린다."""
    out = {}
    parts = re.split(r"^===\s*(\d{3,4})\s*===\s*$", text or "", flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        no, body = int(parts[i]), parts[i + 1]
        body = re.split(r"^={10,}\s*$|^\[(?:요청|좋은 예|나쁜 예|프로그램과 맞추는 규칙|출력 형식)|^[^\n]*\|\s*이미지 프롬프트 지침\s*$", body, flags=re.M)[0].rstrip()
        if re.search(r"^\s*(?:프롬프트|prompt)\s*[:：]\s*\S", body, flags=re.M | re.I) and "[화풍 문구]" not in body and no not in out:
            body = re.sub(r"^(\s*(?:프롬프트|prompt)\s*[:：]\s*)(?:\[화풍[^\]]*\]\s*)+", r"\1", body, flags=re.M | re.I)   # 꼬리표 제거
            out[no] = body.strip()
    return out


민담_장면_글자수 = 110       # 이야기형 한 장면(그림 한 장)에 묶는 글자 수 (약 25초 분량). 정보형은 문장 하나가 한 장면
민담_프롬프트_묶음 = 12       # 이야기형 이미지 프롬프트를 한 번에 묻는 장면 수 (블록이 길어 30개를 물으면 딥시크 웹 답이 절반에서 잘린다)
프롬프트_최소_묶음 = 6        # 답이 잘려 묶음을 줄일 때의 하한


def scene_units(script_file, sents=None):
    """대본을 그림 단위로 나눈다 → [(첫 문장 번호, 끝 문장 번호, 원문)]. 번호는 1부터, 자막(srt) 번호와 같다.
    정보형: 문장 하나 = 장면 하나. 이야기형(민담): 1~2시간짜리라 연속 문장을 약 민담_장면_글자수 만큼 묶어 장면 하나로."""
    if sents is None:
        with open(script_file, encoding="utf-8-sig") as f:
            sents = split_sentences(fix_script_sentences(script_body(f.read())), script_file)
    if channel_of(script_file) != "mindam":
        return [(i + 1, i + 1, t) for i, t in enumerate(sents)]
    units, start, buf = [], 1, []
    for i, t in enumerate(sents, 1):
        buf.append(t)
        if sum(len(x) for x in buf) >= 민담_장면_글자수:
            units.append((start, i, " ".join(buf))); start, buf = i + 1, []
    if buf:
        if units and sum(len(x) for x in buf) < 민담_장면_글자수 // 3:   # 끝에 남은 짧은 꼬리는 앞 장면에 붙인다
            a, _b, text = units[-1]; units[-1] = (a, len(sents), text + " " + " ".join(buf))
        else:
            units.append((start, len(sents), " ".join(buf)))
    return units


def _block_line(body):
    m = re.search(r"^\s*대사\s*[:：]\s*(.+)$", body or "", flags=re.M)
    return re.sub(r"\s+", "", m.group(1))[:24] if m else ""


def prompts_complete(path, script_file):
    """이미지 프롬프트 파일이 대본의 모든 장면을 덮는지 (없거나 모자라면 False → 모자란 범위만 다시 만든다)."""
    if not os.path.isfile(path):
        return False
    units = scene_units(script_file)
    with open(path, encoding="utf-8-sig") as f:
        have = prompt_blocks(f.read())
    return bool(units) and all(i in have and _block_line(have[i]) == re.sub(r"\s+", "", u[2])[:24] for i, u in enumerate(units, 1))


인물표_지시 = ("너는 사극 삽화 감독이다. 아래 [등장인물] 을 읽고, 이 편의 모든 그림 프롬프트에서 똑같이 되풀이할 인물별 영어 생김새 구절을 정한다.\n"
            "형식: 한 줄에 한 명, 다른 말 없이.\n"
            "이름 / 성별·나이 / 역할 한 구절 / clothing: 영어 두에서 여섯 단어 / hair: 영어 두에서 여섯 단어 / face & build: 영어 두에서 여섯 단어\n"
            "규칙: 대본의 묘사(옷 색과 감, 머리 모양, 흉터, 체형, 키)를 그대로 옮긴다. 지어내지 않는다. 옷은 조선 시대 한복 명칭을 영어로(jeogori, chima, dopo, gat, straw sandals).\n"
            "아이(열일곱 살 아래)는 나이를 적고 beard, topknot, wrinkles 같은 어른 표현을 쓰지 않는다. 주요 인물부터 순서대로 최대 여덟 명. "
            "같은 인물의 다른 시기(거지였다가 벼슬을 얻은 경우)는 '이름 (뒤)' 로 한 줄 더 쓴다.")


def character_sheet(script_file, ai, log=print):
    """이야기형(민담) 한 편의 '인물 고정표'. 기획.txt 의 [등장인물] 을 영어 생김새 세 구절(옷·머리·얼굴)로 정리해 인물표.txt 에 저장하고,
    이미지 프롬프트·썸네일 요청마다 같은 표를 넣어 묶음(30장면)마다 인물 생김새가 달라지지 않게 한다. 정보형이거나 기획.txt 가 없으면 빈 문자열."""
    if channel_of(script_file) != "mindam":
        return ""
    workdir = os.path.dirname(script_file)
    sheet_path = os.path.join(workdir, "인물표.txt")
    if os.path.isfile(sheet_path):
        with open(sheet_path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        if text:
            log(f"   인물 고정표 재사용: {sheet_path}")
            return text
    plan_path = os.path.join(workdir, "기획.txt")
    if not os.path.isfile(plan_path):
        return ""
    with open(plan_path, encoding="utf-8-sig") as f:
        chars = 민담_대본.block_of(f.read(), "등장인물")
    if not chars.strip():
        return ""
    log("   인물 고정표 만드는 중 (한 편에 한 번) ")
    text = ai.ask(인물표_지시, f"[등장인물]\n{chars}").replace("```", "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.count("/") >= 4 and "clothing" in ln.lower()]
    if not lines:
        log("   ! 인물 고정표를 읽지 못해 이번 편은 지침의 생김새 규칙만으로 만듭니다")
        return ""
    text = "\n".join(lines)
    with open(sheet_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(f"   ✓ 인물 고정표 {len(lines)}명 → {sheet_path}")
    return text


def sheet_note(sheet):
    """요청 메시지에 붙이는 [인물 고정표] 안내."""
    if not sheet:
        return ""
    return ("[인물 고정표 — 이 편의 모든 장면에서 같은 낱말로]\n" + sheet + "\n"
            "위 인물이 나오는 프롬프트마다 clothing·hair·face & build 세 구절을 이 표의 영어 그대로 되풀이한다. 새로 정하거나 바꾸지 않는다. "
            "장소나 때가 바뀔 때만 겉옷이나 소품을 더한다.\n\n")


def make_image_prompts(job, req):
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    path = req.get("script_file", "")
    if not path or not os.path.exists(path):
        raise SystemExit("대본 파일을 고르세요.")
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    body = fix_script_sentences(script_body(raw))
    sents = split_sentences(body, path)
    if not sents:
        raise SystemExit("대본에서 문장을 찾지 못했습니다.")
    units = scene_units(path, sents)              # 그림 단위 (정보형: 문장 하나 / 이야기형: 문장 묶음)
    texts = [u[2] for u in units]
    guideline = image_guideline_for(path, req.get("guideline"))
    system = read_guideline(guideline, channel_of(path))
    job.add(f"   이미지 지침: {guideline}")
    style = image_style_lock(req.get("style", "2D 일러스트"), channel_of(path))
    chunk = int(req.get("chunk") or 25)
    if channel_of(path) == "mindam":               # 이야기형 블록은 정보형의 서너 배 길이 → 한 번에 적게 묻는다 (답이 잘리는 것을 막는다)
        chunk = min(chunk, 민담_프롬프트_묶음)
    job.add(f"AI: {ai.name} ({ai.model}) · 문장 {len(sents)}개 → 장면 {len(units)}개 · {chunk}장면씩 · 화풍 {req.get('style', '실사')}"
            + (f" · 이야기형은 약 {민담_장면_글자수}자마다 그림 한 장" if len(units) != len(sents) else ""))
    if path.endswith("final.txt"):
        out_path = os.path.join(os.path.dirname(path), "이미지프롬프트.txt")
    else:
        out_path = re.sub(r"\.txt$", "", path) + "_이미지프롬프트.txt"
    sheet = character_sheet(path, ai, job.add)     # 이야기형: 인물 생김새를 한 편 안에서 고정 (묶음마다 같은 표를 보낸다)
    have = {}
    if os.path.isfile(out_path):                     # 지난번에 만들다 만 파일이 있으면 올바른 장면은 그대로 쓰고 모자란 묶음만 묻는다
        with open(out_path, encoding="utf-8-sig") as f:
            have = {k: v for k, v in prompt_blocks(f.read()).items()
                    if 1 <= k <= len(units) and _block_line(v) == re.sub(r"\s+", "", texts[k - 1])[:24]}   # 대사가 지금 장면과 같은 것만
        if have:
            job.add(f"   이미 만든 장면 {len(have)}개 재사용 · 모자란 범위만 다시 만듭니다")
    def save():
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(f"==={k:03d}===\n{have[k]}" for k in sorted(have)) + "\n")
    s, stuck = 0, {}
    while s < len(units):
        if s + 1 in have:                             # 이미 있는 장면은 건너뛰고 첫 빈 장면부터 묻는다
            s += 1; continue
        e = min(s + chunk, len(units))
        lines = "\n".join(f"{i+1:03d}. {texts[i]}" for i in range(s, e))
        user = (f"[화풍·화면 비율] {style}\n"
                + sheet_note(sheet)
                + 채널_프로필.mascot_reference_note(channel_of(path))
                + ("[레퍼런스] 드롭샷 References 패널에 업로드된 이미지를 반드시 참조한다. "
                   "C형의 동일 인물은 얼굴형·눈·머리·체형·의상을 유지하고, "
                   "A형은 인물을 억지로 추가하지 말고 선화·색감만 일치시킨다. "
                   "각 영어 프롬프트에 이 레퍼런스 지시를 명시한다. 수채화·사진·3D 표현은 금지한다.\n"
                   if req.get("style") == "2D 일러스트" and channel_of(path) != "person" else "")
                +
                f"[이번 범위] {s+1:03d} ~ {e:03d} (총 {e-s}개 장면)\n\n[이번 범위 원문]\n{lines}\n\n"
                f"위 {e-s}개 문장 각각에 대해 ===NNN=== 장면 블록을 {s+1:03d}부터 {e:03d}까지 순서대로 출력한다.")
        job.add(f"   {s+1:03d}~{e:03d} 변환 ")
        best = {}
        for attempt in range(3):
            if getattr(job, "cancel_requested", False):
                raise RuntimeError("사용자가 중단했습니다. (받은 프롬프트는 저장되어 있어 [이어서 만들기]로 계속할 수 있습니다)")
            out = ai.ask(system, user).replace("```", "").strip()
            if any(m in out for m in ECHO_MARKS):    # 딥시크 웹이 보낸 질문(지침)을 답변으로 돌려준 경우
                job.add(f"   ! 답변 대신 보낸 질문이 돌아옴 → 다시 요청 ({attempt + 1}/3)")
                continue
            got = {k: v for k, v in prompt_blocks(out).items() if s + 1 <= k <= e}
            if len(got) > len(best):
                best = got
            if len(best) >= e - s or len(best) >= 프롬프트_최소_묶음 // 2:    # 다 받았거나, 잘렸어도 쓸 만큼 받았으면 같은 크기로 다시 묻지 않는다
                break
            job.add(f"   ! 장면 수 {len(got)}개 (기대 {e-s}개) → 다시 요청 ({attempt + 1}/3)")
        if not best:
            raise SystemExit(f"{s+1:03d}~{e:03d} 범위의 이미지 프롬프트를 받지 못했습니다. AI 연결(딥시크 웹 탭)을 확인하고 다시 시도하세요.")
        have.update(best)
        save()                                        # 묶음마다 저장 → 중간에 끊겨도 받은 만큼은 남는다
        got_run = 0                                   # 앞에서부터 빈틈없이 받은 장면 수 (여기까지는 넘어가고, 다음은 첫 빈 장면부터 다시 묻는다)
        while s + got_run + 1 in have and s + got_run < e:
            got_run += 1
        if len(best) < e - s:
            if chunk > 프롬프트_최소_묶음:               # 답이 잘린 것 → 다음부터는 받은 만큼만 묻는다
                chunk = max(프롬프트_최소_묶음, min(chunk - 1, len(best)))
                job.add(f"   ! {s+1:03d}~{e:03d} 장면 {len(best)}/{e-s}개만 받음 (답이 잘림) → 다음부터 {chunk}장면씩 묻습니다")
            else:
                job.add(f"   ! {s+1:03d}~{e:03d} 장면 {len(best)}/{e-s}개만 받음 — 빠진 장면은 이어서 다시 묻습니다")
        if got_run == 0:                              # 첫 장면조차 못 받음 → 한 번 더 같은 자리에서 묻고, 그래도 없으면 넘어간다 (끝에 빠진 장면으로 보고)
            stuck[s] = stuck.get(s, 0) + 1
        s += got_run if got_run else (1 if stuck.get(s, 0) >= 2 else 0)
    missing = [i for i in range(1, len(units) + 1) if i not in have]
    if missing:
        raise SystemExit("이미지 프롬프트가 모자랍니다 (빠진 장면 " + ", ".join(f"{i:03d}" for i in missing[:10]) + (" …" if len(missing) > 10 else "")
                         + "). 받은 만큼은 저장했으니 [이어서 만들기]를 다시 누르면 빠진 범위만 다시 요청합니다.")
    save()
    # 플로우 txt (Auto-Image Placer 용: N번 이미지 = 자막 a~b번)
    flow_path = re.sub(r"\.txt$", "", out_path) + "_플로우.txt"
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write("# 이미지번호: 자막번호 (정보형은 문장 1개 = 이미지 1장, 이야기형은 문장 묶음 = 이미지 1장)\n")
        for k, (a, b, _t) in enumerate(units, 1):
            f.write(f"{k}: {a}\n" if a == b else f"{k}: {a}-{b}\n")
    job.add(f"✓ 저장: {out_path}")
    job.add("비용: " + ai.cost_text())
    return dict(file=out_path, flow=flow_path, scenes=len(units), cost=ai.cost_text())


def restyle_script_prompts_2d(script_file):
    """기존 파스텔 프롬프트를 2D+레퍼런스 지시로 바꾸고 원본을 백업한다."""
    if script_file not in {item["path"] for item in script_files()}:
        raise ValueError("대본 목록에서 파일을 다시 선택하세요.")
    prompts = (os.path.join(os.path.dirname(script_file), "이미지프롬프트.txt")
               if os.path.basename(script_file) == "final.txt"
               else re.sub(r"\.txt$", "", script_file) + "_이미지프롬프트.txt")
    if not os.path.isfile(prompts):
        raise ValueError("먼저 이미지 프롬프트를 만드세요.")
    with open(prompts, encoding="utf-8-sig") as f:
        original = f.read()
    if "Premium hand-drawn 2D editorial animation illustration" in original:
        raise ValueError("이미 2D 프롬프트로 변환된 파일입니다.")
    blocks = re.split(r"(?=^===\d{3}===\s*$)", original, flags=re.M)
    count = 0
    changed = []
    for block in blocks:
        if not re.match(r"^===\d{3}===", block):
            changed.append(block); continue
        kind = re.search(r"^유형:\s*([ABC])", block, flags=re.M)
        role = ("For the principal recurring person, use the uploaded reference image as the exact character identity: "
                "preserve facial structure, eyes, hairstyle, proportions, and signature clothing across scenes. "
                if kind and kind.group(1) == "C" else
                "Use the uploaded reference image for linework, color palette, and overall 2D art direction; "
                "do not add a person who is not in this scene. ")
        def update_prompt(match):
            prompt = match.group(1).strip()
            prompt = re.sub(r"(?i)soft pastel illustration,?\s*gentle watercolor texture,?\s*warm muted palette,?\s*16:9 aspect ratio\.?", "", prompt)
            prompt = re.sub(r"(?i)soft pastel style|pastel illustration|watercolor (?:texture|effect|wash)", "clean 2D cel-shaded illustration", prompt)
            prompt = re.sub(r"(?i)\bsoftly painted\b", "cleanly drawn", prompt)
            prompt = re.sub(r"\s{2,}", " ", prompt).strip()
            return "프롬프트: " + image_style_lock("2D 일러스트") + " " + role + prompt
        block, n = re.subn(r"^프롬프트:\s*(.+)$", update_prompt, block, count=1, flags=re.M)
        if n != 1:
            raise ValueError("장면 프롬프트 형식이 올바르지 않습니다.")
        count += 1
        changed.append(block)
    if not count:
        raise ValueError("변환할 장면을 찾지 못했습니다.")
    backup_dir = os.path.join(assets_dir(script_file), "프롬프트_백업")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_파스텔.txt")
    with open(backup, "w", encoding="utf-8") as f:
        f.write(original)
    with open(prompts, "w", encoding="utf-8") as f:
        f.write("".join(changed))
    return dict(count=count, prompts=os.path.abspath(prompts), backup=os.path.abspath(backup))


# ── 작업 4: 나레이션 (인월드 TTS) ─────────────────────────────
def assets_dir(script_file):
    """대본 파일 → 자료 폴더 (민담은 대본 폴더 그대로, 심리해독소는 옆에 '<이름>_자료')."""
    if os.path.basename(script_file) == "final.txt":
        return os.path.dirname(script_file)
    d = re.sub(r"\.txt$", "", script_file) + "_자료"
    os.makedirs(d, exist_ok=True)
    return d


def channel_of(script_file):
    """대본 파일 경로로 채널을 판단한다: 대본/민담/…/final.txt → mindam, 그 외 → person"""
    p = os.path.abspath(script_file or "").replace("\\", "/")
    return "mindam" if "/민담/" in p or os.path.basename(p) == "final.txt" else "person"

def _block(text, name):
    match = re.search(rf"\[{re.escape(name)}\]\s*(.*?)(?=\n\[[^\n]+\]|\Z)", text or "", re.S)
    return match.group(1).strip() if match else ""

def upload_description(description, tags=""):
    """유튜브 설명란에 한 번에 붙여 넣을 덩어리: 링크 주소는 빼고, 해시태그가 없으면 태그를 끝에 붙인다."""
    text = (description or "").strip()
    text = re.sub(r"\s*/?\s*https?://\S+", "", text)                          # 출처 줄의 주소 제거
    text = re.sub(r"(?:^|\n)📚 참고 자료\n(?:•[^\n]*\n?)+", "\n", text)        # 예전에 만든 설명의 참고 자료 목록도 뺀다
    text = re.sub(r"(?:^|\n)\[?출처\]?\s*[:：]?\s*\n(?:[-•·][^\n]*\n?)+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if "#" not in text and (tags or "").strip():
        words = [t.strip().lstrip("#") for t in re.split(r"[,\s]+", tags) if t.strip()]
        text += "\n\n" + " ".join("#" + re.sub(r"\s+", "", w) for w in words[:10])
    return text


def workspace_data(script_file):
    """선택 대본의 편집 가능한 제작 결과와 업로드 정보를 모은다."""
    if script_file not in {item["path"] for item in script_files()}:
        raise ValueError("대본 목록에서 파일을 다시 선택하세요.")
    assets = assets_dir(script_file)
    prompts = (os.path.join(assets, "이미지프롬프트.txt") if os.path.basename(script_file) == "final.txt"
               else re.sub(r"\.txt$", "", script_file) + "_이미지프롬프트.txt")
    srt = os.path.join(assets, "나레이션.srt")
    opt = (os.path.join(assets, "유튜브_최적화.txt") if os.path.basename(script_file) == "final.txt"
           else re.sub(r"\.txt$", "", script_file) + "_유튜브최적화.txt")
    script_text = Path(script_file).read_text(encoding="utf-8-sig", errors="replace")
    opt_text = Path(opt).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(opt) else ""
    saved = load_json(os.path.join(assets, "업로드_정보.json"), {})
    thumb_dir = os.path.join(assets, "썸네일")
    thumbnails = [os.path.abspath(p) for p in sorted(glob.glob(os.path.join(thumb_dir, "썸네일_*.jpg")))]
    thumbnail_raw = [v for _, v in sorted(raw_thumbnails(thumb_dir).items())]
    if os.path.basename(script_file) == "final.txt":          # 이야기형: final.txt 는 본문뿐이라 제목·설명·태그는 유튜브_설명.txt (없으면 기획.txt) 에서
        for extra in ("기획.txt", "유튜브_설명.txt"):        # 앞에 끼워 넣으므로 뒤에 적은 유튜브_설명.txt 가 맨 앞(우선)
            ep = os.path.join(assets, extra)
            if os.path.isfile(ep):
                opt_text = Path(ep).read_text(encoding="utf-8-sig", errors="replace") + "\n\n" + opt_text
    title = saved.get("title") or _block(script_text, "제목") or _block(opt_text, "제목")
    if not title and os.path.basename(script_file) == "final.txt":
        title = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", os.path.basename(os.path.dirname(script_file)))
    return dict(script_file=os.path.abspath(script_file), assets=os.path.abspath(assets), script=script_text,
                prompts=Path(prompts).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(prompts) else "",
                prompts_file=os.path.abspath(prompts),
                srt=Path(srt).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(srt) else "",
                srt_file=os.path.abspath(srt), narration=os.path.abspath(os.path.join(assets, "나레이션.mp3")),
                images=os.path.abspath(os.path.join(assets, "images")), thumbnails=thumbnails, thumbnail_raw=thumbnail_raw,
                thumbnail_dir=os.path.abspath(thumb_dir), title=title,
                description=upload_description(saved.get("description") or _block(script_text, "설명글") or _block(opt_text, "설명글"),
                                               saved.get("tags") or _block(script_text, "태그") or _block(opt_text, "태그")),
                sources=saved.get("sources") or _block(script_text, "출처") or _block(opt_text, "출처"),
                tags=saved.get("tags") or _block(script_text, "태그") or _block(opt_text, "태그"))

def save_workspace(body):
    script = body.get("script_file", "")
    data = workspace_data(script)
    kind = body.get("kind", "")
    if kind == "script":
        Path(script).write_text(str(body.get("text", "")), encoding="utf-8")
    elif kind == "prompts":
        Path(data["prompts_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(data["prompts_file"]).write_text(str(body.get("text", "")), encoding="utf-8")
    elif kind == "srt":
        Path(data["srt_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(data["srt_file"]).write_text(str(body.get("text", "")), encoding="utf-8")
    elif kind == "metadata":
        Path(data["assets"]).mkdir(parents=True, exist_ok=True)
        target = Path(data["assets"]) / "업로드_정보.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps({k: str(body.get(k, "")) for k in ("title", "description", "sources", "tags")}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)
    else:
        raise ValueError("저장할 항목을 선택하세요.")
    return {"ok": True}

def voice_for(cfg, channel):
    """채널별 목소리·속도. 채널 전용 값이 없으면 공통(인월드_목소리/인월드_속도)을 쓴다."""
    suffix = "_민담" if channel == "mindam" else "_사람"
    voice = (cfg.get("인월드_목소리" + suffix) or cfg.get("인월드_목소리") or "").strip()
    speed = cfg.get("인월드_속도" + suffix)
    if speed in (None, "", 0):
        speed = cfg.get("인월드_속도", 1.0)
    return voice, float(speed or 1.0)

def make_tts(job, req):
    cfg = 대본생성.load_cfg()
    path = req.get("script_file", "")
    if not path or not os.path.exists(path):
        raise SystemExit("대본 파일을 고르세요.")
    with open(path, encoding="utf-8-sig") as f:
        body = fix_script_sentences(script_body(f.read()))
    sents = split_sentences(body, path)
    out = req.get("out_dir") or assets_dir(path)
    channel = req.get("channel") or channel_of(path)
    voice, speed = voice_for(cfg, channel)
    if req.get("voice_id"):
        voice = req["voice_id"]
    if req.get("speed"):
        speed = float(req["speed"])
    if not voice:
        raise SystemExit(f"{채널_프로필.name(channel)} 채널 목소리 ID 가 없습니다. [설정] 탭의 인월드 목소리에 넣어주세요.")
    job.add(f"   목소리: {채널_프로필.name(channel)} 채널 → {voice} · 속도 {speed}")
    job.stage = "나레이션 합성"
    groups = tts_groups(path, sents)             # 문장을 묶어 읽혀 억양이 이어지게 (문장 번호는 그대로라 예전 방식 편도 된다)
    r = 나레이션.synthesize(sents, out, req.get("api_key") or cfg.get("인월드_API_키", ""), voice,
                         req.get("model") or cfg.get("인월드_모델", "inworld-tts-1.5-max"), speed,
                         log=job.add, cancel=lambda: job.cancel_requested,
                         subtitle_lines=2 if channel == "mindam" else 1, groups=groups,
                         temperature=(float(cfg["인월드_온도"]) if cfg.get("인월드_온도") not in (None, "", 0) else None))   # 감정 변화폭 (비우면 인월드 기본)
    return r


정보형_읽기_묶음 = 3          # 정보형: 한 번에 읽히는 문장 수 (억양이 이어지도록), 아래 글자 수를 넘으면 그 전에 끊는다
읽기_묶음_글자수 = 220


def tts_groups(script_file, sents):
    """나레이션을 한 번에 읽힐 문장 범위 [(첫 번호, 끝 번호)]. 이야기형은 그림 한 장(장면) 단위, 정보형은 2~3문장씩."""
    if channel_of(script_file) == "mindam":
        return [(a, b) for a, b, _t in scene_units(script_file, sents)]
    groups, a, chars = [], 1, 0
    for i, t in enumerate(sents, 1):
        chars += len(t)
        if i - a + 1 >= 정보형_읽기_묶음 or chars >= 읽기_묶음_글자수:
            groups.append((a, i)); a, chars = i + 1, 0
    if a <= len(sents):
        groups.append((a, len(sents)))
    return groups


# ── 편집프로그램(8765) 연동 ─────────────────────────────────────
AIP = "http://127.0.0.1:8765"

def aip(path, body=None, method=None, timeout=90):
    try:
        r = _rq.request(method or ("POST" if body is not None else "GET"), AIP + path, json=body, timeout=timeout)
    except _rq.RequestException as e:
        raise SystemExit(f"편집프로그램(Auto-Image Placer, {AIP})에 연결할 수 없습니다. 편집프로그램\\run.bat 을 먼저 켜 주세요. ({e.__class__.__name__})")
    if r.status_code >= 400:
        try:
            raise SystemExit("편집프로그램 오류: " + r.json().get("detail", r.text[:200]))
        except ValueError:
            raise SystemExit("편집프로그램 오류: " + r.text[:200])
    return r.json()


def kie_credit():
    """KIE 남은 크레딧. 키는 편집프로그램의 config.json 에 있다 (화면에서 영상 변환 전에 보여 주고, 부족하면 미리 알린다)."""
    import 시작
    editor = 시작.find_editor()
    if not editor:
        raise ValueError("편집프로그램 폴더를 찾지 못했습니다.")
    key = str(load_json(os.path.join(editor, "config.json"), {}).get("kie_api_key", "") or os.environ.get("KIE_API_KEY", "")).strip()
    if not key:
        return dict(ok=False, credit=None, detail="KIE 키가 없습니다.")
    r = _rq.get("https://api.kie.ai/api/v1/chat/credit", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or j.get("code") not in (200, None):
        return dict(ok=False, credit=None, detail=f"KIE 응답 {r.status_code}: {j.get('msg') or r.text[:120]}")
    return dict(ok=True, credit=float(j.get("data") or 0))


def run_image_generation(job, prompts_file, images_dir, style_prefix="", retries_left=2):
    """편집프로그램의 좌표 자동화로 이미지를 전부 만들 때까지 기다린다. 좌표·다운로드 폴더는 편집프로그램에 저장된 값을 쓴다."""
    info = aip("/api/info")
    ui = (info.get("config") or {}).get("gen_ui") or {}
    xy = ui.get("XY") or {}
    if not (xy.get("prompt") and xy.get("download")):
        raise SystemExit("설정에서 드롭샷 프롬프트 입력창과 이미지 다운로드 버튼 좌표를 먼저 저장하세요.")
    dl = (ui.get("P") or {}).get("download") or info.get("downloads_dir")
    if not dl or not os.path.isdir(dl):          # 저장된 폴더가 없으면 실제 다운로드 폴더로
        dl = info.get("downloads_dir") or os.path.join(os.path.expanduser("~"), "Downloads")
        job.add(f"   브라우저 다운로드 폴더 → {dl}")
    try:
        with open(prompts_file, encoding="utf-8-sig") as f:
            if "STRICT STYLE LOCK" in f.read():
                style_prefix = ""                     # 장면 프롬프트마다 이미 화풍 고정이 들어 있다
    except OSError:
        pass
    body = dict(prompts_file=os.path.abspath(prompts_file), output_dir=os.path.abspath(images_dir), download_dir=dl,
                prompt_xy=xy["prompt"], generate_xy=xy.get("generate") or xy["prompt"], download_xy=xy["download"],
                wait_generate=float(ui.get("wait_generate") or 60), wait_download=float(ui.get("wait_download") or 120), window_keyword=ui.get("window_keyword") or "드롭샷", auto_generate=ui.get("auto_generate", True) is not False,
                wait_next=0.5, start_no=1, end_no=0, skip_existing=True,
                style_prefix=style_prefix or ui.get("style_prefix") or "", retries=1)
    current = aip("/api/gen/status")
    if current.get("status") in ("running", "paused"):
        running_dir = ((info.get("config") or {}).get("gen") or {}).get("output_dir") or ""
        if os.path.normcase(os.path.abspath(running_dir)) == os.path.normcase(os.path.abspath(images_dir)):
            job.add(f"   편집프로그램이 이미 이 폴더에 이미지를 만드는 중 ({len(current.get('done', []))}/{current.get('total')}) → 이어서 기다림")
        else:
            raise RuntimeError(f"편집프로그램이 다른 작업의 이미지를 만드는 중입니다 ({running_dir}). 끝나거나 [이미지 하나씩 수정]에서 중단한 뒤 계속을 누르세요.")
    else:
        aip("/api/gen/start", body)
        job.add(f"   편집프로그램에 이미지 생성 요청 · 저장 {images_dir}")
    last = -1
    while True:
        if job.cancel_requested:
            aip("/api/gen/stop", {}); raise RuntimeError("취소됨")
        st = aip("/api/gen/status")
        n = len(st.get("done", [])) + len(st.get("failed", []))
        if n != last:
            last = n
            job.progress = n / max(1, st.get("total") or 1)
            job.stage = f"이미지 생성 {n}/{st.get('total')}"
        if st.get("status") in ("done", "error", "stopped"):
            if st.get("status") != "done":
                raise RuntimeError("이미지 생성이 끝나지 않음: " + (st.get("error") or st.get("status")))
            failed = list(st.get("failed") or [])
            job.add(f"   ✓ 이미지 {len(st.get('done', []))}장 · 실패 {len(failed)}장")
            # 실패한 장면은 사람이 다시 누르지 않아도 되도록 빠진 것만 최대 2번 더 시도한다
            if failed and retries_left > 0:
                job.add(f"   ↻ 실패 장면 {', '.join(f'{n:03d}' for n in failed[:12])}{' …' if len(failed) > 12 else ''} 다시 시도 ({3 - retries_left}/2)")
                time.sleep(5)
                return run_image_generation(job, prompts_file, images_dir, style_prefix, retries_left - 1)
            return st
        time.sleep(4)


KIE_MIN_CREDIT = 50           # 이보다 적으면 KIE 대신 드롭샷 좌표 변환을 쓴다 (장면 하나에 수십 크레딧)


def kie_usable():
    """KIE 로 영상을 만들 수 있는지: 키가 있고 크레딧이 충분한지. (크레딧 조회가 안 되면 키만 보고 시도한다) → (가능 여부, 이유)"""
    info = aip("/api/info")
    if not info.get("kie_key_saved"):
        return False, "KIE 키 없음"
    try:
        c = kie_credit()
    except Exception:  # noqa: BLE001
        return True, ""
    if c.get("ok") and c.get("credit") is not None and c["credit"] < KIE_MIN_CREDIT:
        return False, f"KIE 크레딧 부족 ({c['credit']:.0f})"
    return True, ""


def video_engine(info=None):
    """영상 변환 방식: auto(KIE 먼저, 안 되면 드롭샷) | kie | dropshot. 설정.json 의 영상변환_방식 을 먼저 보고, 없으면 편집프로그램 설정 gen_ui.video_engine."""
    v = str(load_json("설정.json", {}).get("영상변환_방식") or "").lower()
    if v not in ("auto", "kie", "dropshot"):
        try:
            info = info or aip("/api/info")
            v = str(((info.get("config") or {}).get("gen_ui") or {}).get("video_engine") or "auto").lower()
        except Exception:  # noqa: BLE001
            v = "auto"
    return v if v in ("auto", "kie", "dropshot") else "auto"


def dropshot_video_xy(info=None):
    """설정에 저장된 드롭샷 영상 변환 좌표 4개 (업로드·입력창·생성·다운로드). 하나라도 없으면 None."""
    info = info or aip("/api/info")
    v = ((info.get("config") or {}).get("gen_ui") or {}).get("VXY") or {}
    if all(v.get(k) for k in ("upload", "prompt", "generate", "download")):
        return v
    return None


움직임_프롬프트 = ("Animate this exact illustration. Keep the input image's 2D illustrated art style, colors, linework and character design completely unchanged — "
            "do not make it photorealistic, do not add realistic skin, lighting or 3D depth. Only subtle natural motion: a slow gentle camera drift, "
            "small movements of hair, cloth, eyes and breath. Same composition, same characters, nothing new appears. "
            "Every body part stays attached and anchored to the body — no floating, detached or drifting heads, hands or objects; "
            "phone screens, pictures and text stay flat and static.")


def motion_prompt_for(prompts_file=None):
    """영상 변환에 붙일 움직임 프롬프트. 프롬프트 파일의 화풍 이름을 읽어 '그 화풍 그대로'를 앞에 못 박는다 (실사로 바뀌는 것을 막는다)."""
    style = ""
    try:
        with open(prompts_file, encoding="utf-8-sig") as f:
            m = re.search(r"every image must be (.+?) style\.\s*(.*?)(?=\s+(?:PEOPLE LOCK|AGE LOCK|FRAME LOCK|Keep the same)|$)", f.read(200000), flags=re.S)
        if m:
            style = f"STYLE: {m.group(1)} — {m.group(2).strip()[:300]} "
    except (OSError, TypeError):
        pass
    return style + 움직임_프롬프트


def scene_motion_prompts(prompts_file, scenes):
    """장면별 영어 프롬프트에서 화풍 고정 문구를 떼고 움직임 프롬프트 앞에 붙일 장면 설명을 만든다 (화풍 자체는 motion_prompt 가 다시 못 박는다)."""
    out = {}
    if not prompts_file or not os.path.isfile(prompts_file):
        return out
    with open(prompts_file, encoding="utf-8-sig") as f:
        blocks = prompt_blocks(f.read())
    for no in scenes:
        m = re.search(r"^\s*(?:프롬프트|prompt)\s*[:：]\s*(.+)$", blocks.get(no, ""), flags=re.M | re.I)
        if not m:
            continue
        text = re.sub(r"^STRICT STYLE LOCK:.*?mixed-media image[.,]?\s*", "", m.group(1).strip(), flags=re.S)
        text = re.sub(r"REFERENCE FACE: @image \d+ [^\n]*?in every scene\.\s*", "", text)
        out[no] = text.lstrip(" ,;.").strip()[:500]
    return out


def start_dropshot_videos(images_dir, prompts_file, scenes):
    """편집프로그램에 드롭샷 좌표 영상 변환을 요청한다 (앞 n장). 좌표가 없으면 안내와 함께 실패."""
    info = aip("/api/info")
    v = dropshot_video_xy(info)
    if not v:
        raise RuntimeError("드롭샷 영상 변환 좌표(업로드·입력창·생성·다운로드)가 없습니다. 설정 → 드롭샷 영상 변환 좌표에서 먼저 잡으세요.")
    ui = (info.get("config") or {}).get("gen_ui") or {}
    dl = (ui.get("P") or {}).get("download") or info.get("downloads_dir") or os.path.join(os.path.expanduser("~"), "Downloads")
    body = dict(images_dir=os.path.abspath(images_dir), download_dir=dl, scenes=list(scenes), prompts=scene_motion_prompts(prompts_file, scenes),
                upload_xy=v["upload"], prompt_xy=v["prompt"], generate_xy=v["generate"], download_xy=v["download"],
                motion_prompt=motion_prompt_for(prompts_file),
                wait_min=float(ui.get("video_wait_min") or 60), wait_max=float(ui.get("video_wait_max") or 360),
                manual_download=ui.get("video_manual_download", True) is not False,
                window_keyword=ui.get("video_window_keyword") or "드롭샷")   # 드롭샷 창 (우리 프로그램 창은 제외된다)
    aip("/api/vgen/start", body)
    return body


def run_hook_videos_dropshot(job, images_dir, prompts_file, scenes):
    """드롭샷 좌표 변환이 끝날 때까지 기다린다. 실패한 장면이 있으면 오류."""
    start_dropshot_videos(images_dir, prompts_file, scenes)
    job.add(f"   드롭샷 좌표로 {len(scenes)}장면 영상 변환 · 드롭샷 창을 가리지 마세요 (장면당 1~5분)")
    while True:
        if job.cancel_requested:
            aip("/api/vgen/stop", {}); raise RuntimeError("취소됨")
        st = aip("/api/vgen/status")
        n = len(st.get("done", [])) + len(st.get("failed", []))
        job.stage = f"후킹 영상(드롭샷): {st.get('current', 0):03d} ({n}/{st.get('total') or len(scenes)})"
        if st.get("waiting"):                          # 다운로드는 사람이 누른다 → 무엇을 해야 하는지 진행 줄에 보여 준다
            job.stage += " · ⬇ " + st["waiting"]
            if getattr(job, "_vgen_waiting_for", None) != st.get("current"):
                job._vgen_waiting_for = st.get("current"); job.add("   ⬇ " + st["waiting"])
        if st.get("status") in ("done", "error", "stopped"):
            if st.get("status") != "done":
                raise RuntimeError("드롭샷 영상 변환이 끝나지 않음: " + (st.get("error") or st.get("status")))
            failed = list(st.get("failed") or [])
            if failed:
                raise RuntimeError("드롭샷 영상 변환 실패 장면: " + ", ".join(f"{no:03d}" for no in failed))
            job.add(f"   ✓ 드롭샷 영상 {len(st.get('done', []))}개")
            return st
        time.sleep(4)


움직임_부적합 = ("phone", "screen", "split", "collage", "diagram", "chart", "arrow", "text overlay", "extreme close-up", "close-up of", "hands holding",
           "hands typing", "'s hands", "hand holding", "picture frame", "photo frame", "framed photo", "reflection", "mirror",
           "top-down", "overhead", "bird's-eye", "flat lay", "empty", "no people", "nobody")
움직임_적합 = ("medium shot", "medium close-up", "waist-up", "full shot", "wide shot", "over-the-shoulder", "sitting", "standing", "walking", "talking", "looking")


def pick_hook_scenes(prompts_file, n):
    """앞부분 장면 가운데 영상으로 바꿔도 이상하지 않은 장면 n 개를 고른다 (머리만 떠다니는 식의 실패를 줄인다).
    후보는 앞 2n+3 장면. 화면 속 얼굴·분할·도표·손 클로즈업·빈 장면은 뒤로 밀고, 인물 상반신·전신 샷을 앞으로. 그래도 모자라면 순서대로 채운다."""
    try:
        with open(prompts_file, encoding="utf-8-sig") as f:
            blocks = prompt_blocks(f.read())
    except OSError:
        return list(range(1, n + 1))
    window = [k for k in sorted(blocks) if k <= 2 * n + 3]
    scored = []
    for k in window:
        body = blocks[k]
        m = re.search(r"^\s*(?:프롬프트|prompt)\s*[:：]\s*(.+)$", body, flags=re.M | re.I)
        text = re.sub(r"^STRICT STYLE LOCK:.*?mixed-media image[.,]?\s*", "", (m.group(1) if m else ""), flags=re.S).lower()
        kind = (re.search(r"^유형:\s*([A-D])", body, flags=re.M) or [None, ""])[1]
        score = 0
        score += 2 if kind in ("C", "D") else (-3 if kind == "A" else 0)
        score += sum(1 for w in 움직임_적합 if w in text)
        score -= sum(3 for w in 움직임_부적합 if w in text)
        score += max(0, (2 * n + 3 - k)) * 0.4       # 앞 장면일수록 조금 우선 (도입부 후킹이 목적)
        scored.append((-score, k))
    scored.sort()
    chosen = sorted(k for _, k in scored[:n])
    return chosen or list(range(1, n + 1))


def run_hook_videos(job, images_dir, prompts_file, scenes, out_dir=None):
    body = dict(images_dir=os.path.abspath(images_dir), prompts_file=os.path.abspath(prompts_file), scenes=scenes,
                output_dir=os.path.abspath(out_dir) if out_dir else "", motion_prompt=motion_prompt_for(prompts_file))
    j = aip("/api/hook/start", body)
    while True:
        if job.cancel_requested:
            aip(f"/api/jobs/{j['job_id']}/cancel", {}); raise RuntimeError("취소됨")
        st = aip(f"/api/jobs/{j['job_id']}")
        job.stage = "후킹 영상: " + (st.get("stage") or st.get("status"))
        if st["status"] in ("done", "error", "cancelled"):
            if st["status"] != "done":
                raise RuntimeError("KIE 영상 변환 실패: " + (st.get("error") or st["status"]))
            result = st.get("result") or {}
            videos = result.get("videos") or {}
            missing = [no for no in scenes if str(no) not in {str(k) for k in videos}]
            if missing:
                raise RuntimeError("KIE 영상 변환이 완료되지 않았습니다. 실패한 장면: " + ", ".join(f"{no:03d}" for no in missing))
            job.add(f"   ✓ KIE 영상 {len(videos)}개")
            return result
        time.sleep(5)


def run_render(job, srt, flow, images_dir, narration, output, ken_burns=True):
    # 편집프로그램 화면에서 마지막으로 쓴 자막 글꼴·색·위치·화면 설정을 그대로 가져와 쓴다
    ui = ((aip("/api/info").get("config") or {}).get("ui") or {})
    keep = {k: ui[k] for k in ("width", "height", "fps", "fit", "ken_burns", "kb_zoom", "transition", "transition_duration", "crf", "preset",
                               "srt_font", "srt_font_size", "srt_margin_v", "srt_color", "srt_outline_color", "srt_outline", "srt_bold", "srt_weight",
                               "srt_box", "srt_box_color", "srt_box_alpha", "srt_align", "srt_fonts_dir",
                               "bgm", "bgm_volume", "bgm_duck", "bgm_fade") if k in ui}
    body = dict(srt=os.path.abspath(srt), flow=os.path.abspath(flow), images=os.path.abspath(images_dir), narration=os.path.abspath(narration),
                subtitle_mov="", match_mode="manual", output_mode="mp4", output=os.path.abspath(output), width=1920, height=1080, fps=30,
                fit="cover", ken_burns=ken_burns, kb_zoom=0.12, transition="none", transition_duration=0.5, burn_srt=True,
                srt_font="Malgun Gothic", srt_font_size=22, srt_bold=True, srt_outline=3.5, crf=18, preset="medium")
    body.update(keep); body["burn_srt"] = True
    j = aip("/api/render", body, timeout=120)
    last_progress, since = -1.0, time.time()
    while True:
        if job.cancel_requested:
            aip(f"/api/jobs/{j['job_id']}/cancel", {}); raise RuntimeError("취소됨")
        st = aip(f"/api/jobs/{j['job_id']}")
        prog = float(st.get("progress") or 0)
        if prog != last_progress:
            last_progress, since = prog, time.time()
        stalled = time.time() - since
        note = "  · 마무리 중(파일 정리) — 진행률이 멈춘 것처럼 보여도 몇 분 더 걸립니다" if prog >= 0.9 and stalled > 45 else (f"  · {int(stalled)}초째 같은 위치" if stalled > 120 else "")
        job.stage = "최종 렌더링: " + (st.get("stage") or st.get("status")) + note; job.progress = prog
        if st["status"] in ("done", "error", "cancelled"):
            if st["status"] != "done":
                raise RuntimeError("렌더 실패: " + (st.get("error") or st["status"]))
            job.add(f"   ✓ 영상 {output}")
            return st.get("result") or {}
        time.sleep(4)


# ── 썸네일 ────────────────────────────────────────────────
def parse_thumb_copies(opt_text):
    """유튜브_최적화.txt 의 [썸네일 문구] → [(상단, 하단, 이미지지시)]"""
    out = []
    for line in 민담_대본.block_of(opt_text, "썸네일 문구").splitlines():
        m = re.match(r"\s*(?:\d+\s*[.)]\s*)?상단\s*[:：]\s*(.+?)\s*/\s*하단\s*[:：]\s*(.+?)(?:\s*/\s*이미지\s*[:：]\s*(.+))?\s*$", line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()))
    return out


def fallback_thumb_copies(title):
    """최적화 문구가 없어도 단어를 자르지 않고 서로 다른 썸네일 문구 3개를 만든다."""
    title = re.sub(r"\s*\|\s*", " ", (title or "").strip())
    words = title.split()
    if not words:
        return [("이 이야기를", "끝까지 보세요", "")]
    split = max(1, min(len(words) - 1, (len(words) + 1) // 2)) if len(words) > 1 else 1
    first = " ".join(words[:split])
    second = " ".join(words[split:]) or "그 진짜 이유"
    keyword = " ".join(words[:min(3, len(words))])
    return [(first, second, ""),
            (keyword, "생각부터 달랐습니다", ""),
            ("대부분 놓치는", "그 진짜 이유", "")]


def thumbnail_seo_context(opt_text, script_text, is_mindam):
    """최적화 결과와 실제 대본에서 썸네일이 지켜야 할 검색 의도와 내용 약속을 모은다."""
    recommended = 민담_대본.block_of(opt_text, "추천 제목").strip()
    description = 민담_대본.block_of(opt_text, "설명글").strip()
    tags = 민담_대본.block_of(opt_text, "태그").strip()
    title_match = re.search(r"\[제목\]\s*\n([^\n]+)", script_text)
    script_title = title_match.group(1).strip() if title_match else ""
    if not script_title:
        script_title = next((line.strip() for line in script_text.splitlines() if line.strip()), "")
    description_first = " / ".join(line.strip() for line in description.splitlines()[:2] if line.strip())
    search_terms = [x.strip().lstrip("#") for x in re.split(r"[,#]", tags) if x.strip()][:8]
    return (f"[실제 영상 제목] {script_title[:180]}\n"
            f"[SEO 추천 제목] {recommended[:240] or script_title[:180]}\n"
            f"[설명 첫 두 줄] {description_first[:260]}\n"
            f"[핵심 검색어] {', '.join(search_terms)}\n"
            f"[대상 시청자] {채널_프로필.get('mindam' if is_mindam else 'person')['대상_시청자']}")


def brand_preview(slot):
    """프로필의 브랜드·레이아웃으로 샘플 썸네일 3장을 만든다. 원본은 최근 썸네일 raw → 최근 장면 이미지 순으로 찾는다."""
    pats = ([os.path.join(대본_폴더, "민담", "*", "썸네일", "raw", "*.jpg"), os.path.join(대본_폴더, "민담", "*", "images", "*.png")] if slot == "mindam" else [])
    pats += [os.path.join(대본_폴더, "*_자료", "썸네일", "raw", "*.jpg"), os.path.join(대본_폴더, "*_자료", "images", "*.png"),
             os.path.join(대본_폴더, "민담", "*", "썸네일", "raw", "*.jpg"), os.path.join(대본_폴더, "민담", "*", "images", "*.png")]
    srcs = []
    for pat in pats:
        srcs += sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if len(srcs) >= 3:
            break
    if not srcs:
        raise ValueError("샘플에 쓸 그림이 아직 없습니다. 영상을 한 편 만든 뒤 다시 눌러 주세요.")
    copies = ([("글 모르는 머슴이", "양반 셋을 이긴 방법"), ("첫날밤 사라진", "신랑의 행방"), ("쌀 한 바가지가", "만 냥이 된 사연")] if slot == "mindam"
              else [("당첨 뒤 조용해진 집", "돈이 바꾼 가족의 이유"), ("좋은 사람인데", "만나면 지치는 이유"), ("나이 들수록", "친구가 줄어드는 이유")])
    out_dir = os.path.join(BASE, "대본", "_상태", "브랜드_미리보기"); os.makedirs(out_dir, exist_ok=True)
    tp = 채널_프로필.get(slot).get("썸네일") or {}
    outs = []
    for i, (src, (top, bottom)) in enumerate(zip(srcs[:3], copies), 1):
        out = os.path.join(out_dir, f"{slot}_{i}.jpg")
        썸네일_합성.compose(src, out, top, bottom, slot, layout=tp.get("레이아웃") or None, tag_text=tp.get("띠_문구") or None, brand=채널_프로필.brand(slot))
        outs.append(out)
    return outs


def thumb_copies_for(script, assets, is_mindam, log=None):
    """유튜브_최적화.txt 의 썸네일 문구 3세트, 없으면 제목으로 만든다."""
    opt_path = os.path.join(assets, "유튜브_최적화.txt") if is_mindam else re.sub(r"\.txt$", "", script) + "_유튜브최적화.txt"
    opt_text = open(opt_path, encoding="utf-8").read() if os.path.exists(opt_path) else ""
    script_text = open(script, encoding="utf-8-sig").read()
    copies = parse_thumb_copies(opt_text)
    blocks = 대본생성.blocks_of(script_text.split("[대본]", 1)[0])
    up, down = blocks.get("상단 제목", "").strip(), blocks.get("하단 제목", "").strip()
    if not is_mindam and (up or down):               # 대본이 정한 상단·하단 제목 두 줄을 첫 썸네일에 그대로 쓴다
        copies = [(up, down, "")] + [c for c in copies if (c[0], c[1]) != (up, down)]
    if not copies:
        title = ""
        m = re.search(r"\[제목\]\s*\n(.+)", script_text)
        if m:
            title = re.sub(r"\s*\|.*$", "", m.group(1)).strip()
        elif is_mindam:
            title = re.sub(r"^\d{4}-\d\d-\d\d_", "", os.path.basename(assets))
        copies = fallback_thumb_copies(title)
        if log:
            log("   최적화 파일이 없어 제목으로 문구를 만듦")
    return copies, opt_text, script_text


def raw_thumbnails(tdir):
    """썸네일/raw 의 001~003 원본(글자 없는) 파일."""
    raw_dir = os.path.join(tdir, "raw")
    out = {}
    if os.path.isdir(raw_dir):
        for name in sorted(os.listdir(raw_dir)):
            m = re.match(r"^(\d{3})\.(png|jpe?g|webp|avif)$", name, re.I)
            if m and int(m.group(1)) not in out:
                out[int(m.group(1))] = os.path.abspath(os.path.join(raw_dir, name))
    return out


def compose_thumbnails(script, log=None):
    """이미 받아 둔 raw 원본에 문구만 얹어 썸네일_1~3.jpg 를 만든다 (AI·드롭샷 호출 없음)."""
    assets = assets_dir(script)
    is_mindam = channel_of(script) == "mindam"
    tdir = os.path.abspath(os.path.join(assets, "썸네일"))
    raws = raw_thumbnails(tdir)
    if not raws:
        raise ValueError("썸네일 원본(raw)이 없습니다. 먼저 썸네일 만들기를 실행하세요.")
    copies, _, _ = thumb_copies_for(script, assets, is_mindam, log)
    outs = []
    for i in sorted(raws):
        top, bottom, _ = copies[(i - 1) % len(copies)]
        out = os.path.join(tdir, f"썸네일_{i}.jpg")
        tp = 채널_프로필.get("mindam" if is_mindam else "person").get("썸네일") or {}
        layout = 썸네일_합성.compose(raws[i], out, top, bottom, "mindam" if is_mindam else "person",
                                 layout=tp.get("레이아웃") or None, tag_text=tp.get("띠_문구") or None,
                                 brand=채널_프로필.brand("mindam" if is_mindam else "person"))
        outs.append(out)
        if log:
            log(f"   ✓ {out}  ({top} / {bottom}) · {썸네일_합성.레이아웃_이름.get(layout, layout)}")
    return dict(thumbnails=outs, dir=tdir)


def fallback_thumbnails(script, images_dir, log=None):
    """드롭샷 썸네일 생성이 안 됐을 때: 장면 이미지 3장(앞·중간·뒤)을 원본으로 삼아 문구를 얹는다."""
    assets = assets_dir(script)
    tdir = os.path.abspath(os.path.join(assets, "썸네일"))
    scenes = []
    for name in sorted(os.listdir(images_dir)) if os.path.isdir(images_dir) else []:
        m = IMAGE_NAME.match(name)
        if m and m.group(2).lower() in ("png", "jpg", "jpeg", "webp"):
            scenes.append(os.path.join(images_dir, name))
    if not scenes:
        raise ValueError("대신 쓸 장면 이미지가 없습니다.")
    picks = [scenes[0], scenes[len(scenes) // 3], scenes[(len(scenes) * 2) // 3]] if len(scenes) >= 3 else scenes
    raw_dir = os.path.join(tdir, "raw"); os.makedirs(raw_dir, exist_ok=True)
    for i, src in enumerate(picks, 1):
        shutil.copy2(src, os.path.join(raw_dir, f"{i:03d}{os.path.splitext(src)[1].lower()}"))
    if log:
        log(f"   장면 이미지 {len(picks)}장으로 썸네일을 대신 만듭니다")
    return compose_thumbnails(script, log)["thumbnails"]


def make_thumbnails(job, req):
    """대본 폴더 → 썸네일 프롬프트 3개(딥시크) → 이미지 생성(편집프로그램 좌표 클릭) → 문구 합성 → 썸네일_1~3.jpg"""
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    script = req.get("script_file", "")
    if not script or not os.path.exists(script):
        raise SystemExit("대본 파일을 고르세요.")
    assets = assets_dir(script)
    is_mindam = os.path.basename(script) == "final.txt"
    copies, opt_text, script_text = thumb_copies_for(script, assets, is_mindam, job.add)
    tdir_early = os.path.abspath(os.path.join(assets, "썸네일"))
    if not req.get("regenerate") and len(raw_thumbnails(tdir_early)) >= 3:
        job.add("   썸네일 원본 3장이 이미 있어 문구만 다시 얹음")
        job.stage = "썸네일 문구 합성"
        r = compose_thumbnails(script, job.add)
        return dict(thumbnails=r["thumbnails"], dir=r["dir"], cost="0원 (원본 재사용)")
    brief = ""
    bp = os.path.join(assets, "thumbnail_brief.md")
    if os.path.exists(bp):
        brief = open(bp, encoding="utf-8").read()[:1500]
    # 썸네일 화풍·구도는 채널 프로필에서 온다 (화풍을 비워 두면 장면 화풍을 그대로 쓴다)
    profile = 채널_프로필.get("mindam" if is_mindam else "person")
    tp = profile.get("썸네일") or {}
    style = (tp.get("화풍") or "").strip() or 화풍.get(화풍_별칭.get(req.get("style", "실사"), req.get("style", "실사")), 화풍["밝은 애니 사극" if is_mindam else "실사"])
    if 채널_프로필.style_tail("mindam" if is_mindam else "person"):
        style = style.rstrip(". ") + ", " + 채널_프로필.style_tail("mindam" if is_mindam else "person")
    position = "bottom"
    layout = (tp.get("구도") or "").strip() or "핵심 인물 한 명의 감정이 즉시 읽히는 단순한 장면, 문구가 들어갈 화면 아래쪽은 단순하고 조금 어둡게"
    seo_context = thumbnail_seo_context(opt_text, script_text, is_mindam)
    user = (f"[화풍] {style}\n[문구 위치] {'하단' if position == 'bottom' else ('상단' if position == 'top' else '좌측')}\n"
            f"[채널] {profile['이름']} ({profile['유형']})\n[구도] {layout}. 유튜브 썸네일용 강한 명암과 스마트폰에서도 즉시 읽히는 단순한 장면\n\n[썸네일 문구]\n"
            + "\n".join(f"{i}. 상단: {t.replace('*', '')} / 하단: {b.replace('*', '')}" + (f" / 이미지: {d}" if d else "") for i, (t, b, d) in enumerate(copies, 1))
            + f"\n\n[영상별 SEO 정보]\n{seo_context}"
            + (f"\n\n[브리프]\n{brief}" if brief else "")
            + (("\n\n" + sheet_note(character_sheet(script, ai, job.add)).rstrip()) if is_mindam else ""))
    job.stage = "썸네일 프롬프트"
    job.add("   썸네일 프롬프트 3개 ")
    text = ai.ask(read_guideline("썸네일_지침.txt", "mindam" if is_mindam else "person"), user).replace("```", "")
    prompts = [민담_대본.block_of(text, str(i)).strip() for i in (1, 2, 3)]
    prompts = [p for p in prompts if p]
    if not prompts:
        raise RuntimeError("썸네일 프롬프트를 읽지 못했습니다:\n" + text[:300])
    tdir = os.path.abspath(os.path.join(assets, "썸네일")); os.makedirs(tdir, exist_ok=True)
    if req.get("regenerate"):
        old_files = glob.glob(os.path.join(tdir, "썸네일_*.jpg")) + glob.glob(os.path.join(tdir, "raw", "*"))
        if old_files:
            backup = os.path.join(tdir, "이전", datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            os.makedirs(backup, exist_ok=True)
            for old in old_files:
                if os.path.isfile(old):
                    shutil.move(old, os.path.join(backup, os.path.basename(old)))
            job.add(f"   기존 썸네일은 이전 폴더에 보관 → {backup}")
    pf = os.path.join(tdir, "썸네일_프롬프트.txt")
    with open(pf, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{i}. {p}" for i, p in enumerate(prompts, 1)) + "\n")
    # 이미지 생성 (편집프로그램 좌표 클릭)
    job.stage = "썸네일 이미지 생성"
    raw_dir = os.path.join(tdir, "raw")
    run_image_generation(job, pf, raw_dir, "")
    # 합성 (썸네일_합성.py: 심리해독소 = 키워드 강조형/숫자 배지형, 민담 = 하단 띠형)
    job.stage = "썸네일 문구 합성"
    if not raw_thumbnails(tdir):
        raise RuntimeError("썸네일 이미지가 한 장도 내려받히지 않았습니다.")
    outs = compose_thumbnails(script, job.add)["thumbnails"]
    job.add("비용: " + ai.cost_text())
    return dict(thumbnails=outs, dir=tdir, cost=ai.cost_text())


def make_upload_package(script_file, result):
    """완성된 한 편의 업로드 필수 파일을 프로젝트의 업로드 폴더 한곳에 모은다."""
    assets = assets_dir(script_file)
    is_mindam = channel_of(script_file) == "mindam"
    opt_path = (os.path.join(assets, "유튜브_최적화.txt") if is_mindam
                else re.sub(r"\.txt$", "", script_file) + "_유튜브최적화.txt")
    script_text = Path(script_file).read_text(encoding="utf-8-sig", errors="replace")
    opt_text = Path(opt_path).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(opt_path) else ""
    saved = load_json(os.path.join(assets, "업로드_정보.json"), {})
    title = (saved.get("title") or _block(opt_text, "최종 추천") or
             _block(script_text, "제목") or result.get("title") or Path(script_file).stem)
    title = title.splitlines()[0].strip()
    tags = saved.get("tags") or _block(opt_text, "태그") or _block(script_text, "태그")
    description = upload_description(saved.get("description") or _block(opt_text, "설명글") or _block(script_text, "설명글"), tags)
    channel_dir = 대본생성.safe_name(채널_프로필.get("mindam" if is_mindam else "person").get("업로드_폴더") or ("민담" if is_mindam else "심리해독소"))
    package = Path(BASE) / "업로드" / channel_dir / f"{datetime.date.today().isoformat()}_{대본생성.safe_name(title)}"
    package.mkdir(parents=True, exist_ok=True)
    (package / "제목.txt").write_text(title, encoding="utf-8")
    (package / "설명.txt").write_text(description.strip(), encoding="utf-8")
    (package / "태그.txt").write_text(tags.strip(), encoding="utf-8")
    (package / "업로드정보.txt").write_text(
        f"[제목]\n{title}\n\n[설명]\n{description.strip()}\n\n[태그]\n{tags.strip()}\n", encoding="utf-8")
    if not saved:                                   # 화면의 제목·설명·태그 칸에도 그대로 보이도록 자료 폴더에 저장
        info = Path(assets) / "업로드_정보.json"
        info.write_text(json.dumps(dict(title=title, description=description.strip(), sources=_block(script_text, "출처"), tags=tags.strip()),
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    video = result.get("video") or os.path.join(assets, "최종.mp4")
    if os.path.isfile(video):
        shutil.copy2(video, package / "최종.mp4")
    thumbnails = result.get("thumbnails") or sorted(glob.glob(os.path.join(assets, "썸네일", "썸네일_*.jpg")))
    for index, thumb in enumerate(thumbnails, 1):
        if os.path.isfile(thumb):
            shutil.copy2(thumb, package / f"썸네일_{index}.jpg")
    return os.path.abspath(package)


# ── 작업 5: 원클릭 파이프라인 ─────────────────────────────────
def narration_matches(flow_file, script_file):
    """나레이션 플로우(문장 → 자막 번호)의 문장 수가 지금 대본의 문장 수와 같은지 — 다르면 번호가 밀려 그림·자막이 어긋난다."""
    try:
        with open(flow_file, encoding="utf-8-sig") as f:
            n_flow = sum(1 for ln in f if re.match(r"^\s*\d+\s*:", ln))
        with open(script_file, encoding="utf-8-sig") as f:
            n_now = len(split_sentences(fix_script_sentences(script_body(f.read())), script_file))
        return n_flow == n_now
    except OSError:
        return False


def main_character_line(assets):
    """인물표.txt 의 첫 인물(주인공) 줄 → (이름, 나이·성별, 생김새 영어 구절)."""
    sheet = os.path.join(assets, "인물표.txt")
    if not os.path.isfile(sheet):
        return None
    for line in open(sheet, encoding="utf-8-sig").read().splitlines():
        if line.count("/") >= 4 and "(뒤)" not in line:
            parts = [x.strip() for x in line.split("/")]
            desc = ", ".join(x.split(":", 1)[1].strip() for x in parts[3:6] if ":" in x and "not specified" not in x)
            return parts[0], parts[1], desc
    return None


def ensure_face_reference(job, script, assets, prompts_file, style):
    """이야기형: 주인공 정면 초상(인물/주인공.jpg)을 만들어 드롭샷 레퍼런스 패널에 올리고, 프롬프트마다 '@image 1 = 주인공 얼굴' 을 적어 둔다.
    실패해도 제작은 계속한다 (얼굴 고정만 빠짐)."""
    who = main_character_line(assets)
    if not who:
        job.add("   인물표가 없어 얼굴 고정(레퍼런스)은 건너뜁니다"); return
    name, age, desc = who
    face_dir = os.path.join(assets, "인물"); os.makedirs(face_dir, exist_ok=True)
    face = os.path.join(face_dir, "주인공.jpg")
    if not os.path.isfile(face):
        job.add(f"   주인공({name}) 얼굴 기준 그림을 먼저 만듭니다")
        pf = os.path.join(face_dir, "인물_프롬프트.txt")
        lock = image_style_lock(style, "mindam")
        prompt = (f"{lock} CHARACTER DESIGN REFERENCE: front-facing bust portrait of a Korean Joseon {age} ({desc}), "
                  "calm neutral expression, looking straight at the viewer, even soft lighting, plain light background, no other people, no text, 16:9 aspect ratio")
        with open(pf, "w", encoding="utf-8") as f:
            f.write(f"===001===\n유형: C\n대사: {name} 얼굴 기준\n프롬프트: {prompt}\n")
        try:
            run_image_generation(job, pf, face_dir, "", retries_left=1)
        except Exception as e:  # noqa: BLE001
            job.add(f"   ! 얼굴 기준 그림 실패(얼굴 고정 없이 진행): {e}"); return
        made = os.path.join(face_dir, "001.jpg")
        if os.path.isfile(made):
            shutil.move(made, face)
    if not os.path.isfile(face):
        job.add("   ! 얼굴 기준 그림이 없어 얼굴 고정 없이 진행"); return
    try:
        aip("/api/ref/clear", {"window_keyword": "드롭샷"})
        r = aip("/api/ref/add", {"image": os.path.abspath(face), "window_keyword": "드롭샷"})
    except Exception as e:  # noqa: BLE001
        job.add(f"   ! 레퍼런스 올리기 실패(얼굴 고정 없이 진행): {e}"); return
    if not r.get("ok"):
        job.add("   ! 드롭샷 레퍼런스 패널에 올리지 못함 (얼굴 고정 없이 진행)"); return
    idx = int(r.get("count") or 1)                # 패널에 다른 그림이 남아 있으면 우리 그림은 마지막 번호다
    note = f"REFERENCE FACE: @image {idx} is {name}'s face and hairstyle — draw {name} with exactly this face, hair and age in every scene."
    with open(prompts_file, encoding="utf-8-sig") as f:
        text = f.read()
    text = re.sub(r"REFERENCE FACE: @image \d+ [^\n]*?in every scene\.\s*", "", text)   # 지난번 지시는 지우고 다시 쓴다
    text = re.sub(r"^(프롬프트\s*[:：]\s*)", lambda m: m.group(1) + note + " ", text, flags=re.M)
    with open(prompts_file, "w", encoding="utf-8") as f:
        f.write(text)
    job.add(f"   ✓ 주인공 얼굴 레퍼런스 올림 (@image {idx} = {name}) · 모든 장면에 같은 얼굴 지시")


def ensure_mascot_reference(job):
    """정보형: 드롭샷 레퍼런스 패널을 비우고, 채널 마스코트 그림(채널_프로필 마스코트.이미지)이 있으면 그것만 올린다.
    (이야기형 주인공 얼굴이 남아 있으면 정보형 그림에 한복·사극이 섞여 나온다.)"""
    m = (채널_프로필.get("person") or {}).get("마스코트") or {}
    img = (m.get("이미지") or "").strip()
    img = os.path.abspath(os.path.join(BASE, img)) if img and not os.path.isabs(img) else img
    try:
        r = aip("/api/ref/clear", {"window_keyword": "드롭샷"})
        if r.get("removed"):
            job.add(f"   레퍼런스 패널 비움 ({r['removed']}장 · 다른 편 것)")
        if img and os.path.isfile(img):
            r = aip("/api/ref/add", {"image": img, "window_keyword": "드롭샷"})
            job.add(f"   ✓ 마스코트 '{m.get('이름', '')}' 레퍼런스 올림" if r.get("ok") else "   ! 마스코트 레퍼런스를 올리지 못함 (마스코트 없이 진행)")
        elif img:
            job.add(f"   마스코트 그림 파일이 없음: {img}")
    except Exception as e:  # noqa: BLE001
        job.add(f"   ! 레퍼런스 패널 정리 실패(그대로 진행): {e}")


def clear_face_reference(job):
    try:
        r = aip("/api/ref/clear", {"window_keyword": "드롭샷"})
        if r.get("removed"):
            job.add(f"   레퍼런스 비움 ({r['removed']}장)")
    except Exception as e:  # noqa: BLE001
        job.add(f"   ! 레퍼런스 비우기 실패: {e}")


def make_pipeline(job, req):
    """주제 → 대본 → 최적화 → 이미지 프롬프트 → 나레이션(인월드) → 이미지 자동 생성(편집프로그램) → [후킹 영상] → [최종 렌더]"""
    def check_cancelled():
        if job.cancel_requested:
            raise RuntimeError("사용자가 연속 제작을 중단했습니다.")
    steps = dict(req.get("steps") or {})
    if int(steps.get("hook", 0) or 0) > 0:
        try:
            has_kie = bool(aip("/api/info").get("kie_key_saved"))
        except Exception:  # noqa: BLE001
            has_kie = False
        if not has_kie:
            job.add("   ! KIE 키가 없어 움직이는 영상(영상변환)은 건너뜁니다. [설정]에서 KIE 키를 저장하면 다음 편부터 만듭니다.")
            steps["hook"] = 0
    result = {}
    job.result = result          # 진행 중에도 단계별 결과(대본·프롬프트·나레이션…)를 화면에서 열 수 있게
    # 1) 대본 (이미 있는 대본 파일로 시작하면 건너뜀)
    job.stage = "① 대본"
    if req.get("script_file") and os.path.exists(req["script_file"]):
        script = req["script_file"]
        job.add(f"▶ 기존 대본으로 시작: {script}")
        r = {"title": os.path.basename(os.path.dirname(script)) if os.path.basename(script) == "final.txt" else os.path.basename(script)}
    elif req.get("channel") == "mindam":
        r = make_mindam_script(job, dict(req, optimize=steps.get("optimize", True)))
        script = r["file"]
    else:
        r = make_person_script(job, dict(req, optimize=steps.get("optimize", True)))
        script = r["file"]
    result.update(script=script, title=r.get("title"), opt=r.get("opt"), meta=r.get("meta"))
    check_cancelled()
    assets = assets_dir(script)
    # 2) 이미지 프롬프트
    existing_prompts = os.path.join(assets, "이미지프롬프트.txt") if os.path.basename(script) == "final.txt" else re.sub(r"\.txt$", "", script) + "_이미지프롬프트.txt"
    if steps.get("prompts", True) and req.get("reuse_prompts") and prompts_complete(existing_prompts, script):
        job.add(f"   이미지 프롬프트가 이미 있어 재사용: {existing_prompts}")
        n_restyled = 0 if legacy_split(script) else restyle_prompts(existing_prompts, req.get("style", ""), channel_of(script))
        if n_restyled:
            job.add(f"   화풍을 '{화풍_별칭.get(req.get('style', ''), req.get('style', ''))}' 로 맞춤 ({n_restyled}장면)")
        steps = dict(steps, prompts=False)
    if steps.get("prompts", True):
        job.stage = "② 이미지 프롬프트"
        ip = make_image_prompts(job, dict(script_file=script, guideline=req.get("img_guideline"), style=req.get("style", "실사"),
                                          chunk=req.get("chunk", 30)))
        result["prompts"] = os.path.abspath(ip["file"])      # 화면이 편집프로그램(다른 폴더에서 실행)에 그대로 넘기므로 절대 경로로
    else:
        cand = os.path.join(assets, "이미지프롬프트.txt") if os.path.basename(script) == "final.txt" else re.sub(r"\.txt$", "", script) + "_이미지프롬프트.txt"
        result["prompts"] = cand if os.path.exists(cand) else ""
    check_cancelled()
    # 3) 나레이션
    if steps.get("tts", True):
        job.stage = "③ 나레이션"
        existing_tts = {k: os.path.join(assets, n) for k, n in (("mp3", "나레이션.mp3"), ("srt", "나레이션.srt"), ("flow", "플로우.txt"))}
        if req.get("reuse_prompts") and all(os.path.isfile(v) for v in existing_tts.values()) and not narration_matches(existing_tts["flow"], script):
            job.add("   문장 나누기가 달라져 나레이션·자막을 다시 만듭니다 (같은 문장의 음성은 재사용)")
            for v in existing_tts.values():
                try: os.remove(v)
                except OSError: pass
        if req.get("reuse_prompts") and all(os.path.isfile(v) for v in existing_tts.values()):
            try:
                duration = 나레이션.probe_duration(나레이션.find_ffmpeg("ffprobe"), existing_tts["mp3"])
            except (FileNotFoundError, OSError):
                duration = 0.0
            job.add(f"   나레이션이 이미 있어 재사용: {existing_tts['mp3']}")
            result.update(narration=existing_tts["mp3"], srt=existing_tts["srt"], flow=existing_tts["flow"], duration=duration)
        else:
            t = make_tts(job, dict(script_file=script, out_dir=assets, channel=req.get("channel") or channel_of(script)))
            result.update(narration=t["mp3"], srt=t["srt"], flow=t["flow"], duration=t["duration"])
    check_cancelled()
    # 4) 이미지 생성
    images_dir = os.path.join(assets, "images")
    if steps.get("images", True):
        if not result.get("prompts"):
            raise SystemExit("이미지 프롬프트 파일이 없어 이미지 생성을 할 수 없습니다.")
        job.stage = "④ 이미지 자동 생성"
        os.makedirs(images_dir, exist_ok=True)
        result["images"] = os.path.abspath(images_dir)
        selected_style = req.get("style", "실사")
        prefix = req.get("style_prefix") or image_style_lock(selected_style, channel_of(script))
        if legacy_split(script):                    # 예전 방식으로 시작한 편: 프롬프트 안의 화풍 문구를 그대로 쓴다 (새 화풍을 덧붙이지 않음)
            prefix = ""
            job.add("   예전 방식으로 시작한 편이라 화풍·문장 나누기를 그대로 둡니다 (새 규칙은 다음 편부터)")
        if 대본생성.load_cfg().get("레퍼런스_자동관리"):      # 기본 꺼짐: 드롭샷 레퍼런스 패널은 사용자가 직접 넣은 그림을 그대로 쓴다 (바꾸면 바뀐 대로)
            if channel_of(script) == "mindam" and not legacy_split(script):
                ensure_face_reference(job, script, assets, result["prompts"], selected_style)
            else:
                ensure_mascot_reference(job)
        else:
            try:
                n = aip("/api/ref/count").get("count")
                job.add(f"   드롭샷 레퍼런스: 패널에 있는 그림 {n}장을 그대로 참고합니다 (직접 바꾸면 바뀐 대로 씁니다)" if n else
                        "   드롭샷 레퍼런스 패널이 비어 있습니다 — 캐릭터를 쓰려면 드롭샷 [추가하기]로 그림을 올려 두세요")
            except Exception:  # noqa: BLE001
                pass
        run_image_generation(job, result["prompts"], images_dir, prefix)
    check_cancelled()
    # 5) 후킹 영상
    n_hook = int(steps.get("hook", 0) or 0)
    if n_hook > 0 and result.get("prompts"):
        job.stage = "⑤ 후킹 영상"
        have = {int(m.group(1)) for m in (re.match(r"^(\d{1,4})\.mp4$", f, re.I) for f in os.listdir(images_dir)) if m}
        picked = pick_hook_scenes(result["prompts"], n_hook)        # 앞쪽 장면 중 움직여도 무리 없는 장면(인물 상반신·단일 피사체)만 고른다
        todo = [n for n in picked if n not in have]
        if not todo:
            job.add(f"   앞 {n_hook}장 움직이는 영상이 이미 있어 건너뜀")
            result["hook"] = images_dir
        else:
            engine = video_engine()                 # auto: KIE 먼저 → 안 되면 드롭샷 / kie: KIE 만 / dropshot: 드롭샷만
            ok, why = (False, "드롭샷 AI 로 만들도록 설정됨") if engine == "dropshot" else kie_usable()
            if engine == "kie" and not ok:
                job.add(f"   ! {why} — KIE 만 쓰도록 설정되어 있어 그래도 KIE 로 시도합니다")
                ok = True
            done_hook = False
            if ok:
                try:
                    run_hook_videos(job, images_dir, result["prompts"], todo)
                    result["hook"] = images_dir; done_hook = True
                except Exception as e:  # noqa: BLE001
                    why = f"KIE 실패: {e}"
            if not done_hook and engine == "kie":
                job.add(f"   ! 움직이는 영상 건너뜀(정지 이미지로 편집): {why}")
            elif not done_hook:                     # KIE 가 안 되면(또는 드롭샷 설정이면) 드롭샷 좌표 변환으로 (좌표가 있을 때만)
                if dropshot_video_xy():
                    job.add(f"   {why} → 드롭샷 좌표로 영상을 만듭니다")
                    try:
                        todo = [n for n in todo if not os.path.exists(os.path.join(images_dir, f"{n:03d}.mp4"))]
                        if todo:
                            run_hook_videos_dropshot(job, images_dir, result["prompts"], todo)
                        result["hook"] = images_dir
                    except Exception as e:  # noqa: BLE001
                        job.add(f"   ! 드롭샷 영상 변환 실패(넘어감, 정지 이미지로 편집): {e}")
                else:
                    job.add(f"   ! 움직이는 영상 건너뜀(정지 이미지로 편집): {why} · 드롭샷 영상 좌표도 없음")
    check_cancelled()
    # 5.5) 썸네일
    if steps.get("thumbnail", True):
        job.stage = "⑤' 썸네일"
        try:
            t = make_thumbnails(job, dict(script_file=script, style=req.get("style", "실사"), position=req.get("thumb_position", "bottom")))
            result["thumbnails"] = t["thumbnails"]
        except Exception as e:  # noqa: BLE001
            job.add(f"   ! 썸네일 이미지 생성 실패: {e}")
            try:                                          # 그래도 썸네일은 반드시 남긴다: 장면 이미지로 대신 만든다
                result["thumbnails"] = fallback_thumbnails(script, images_dir, job.add)
            except Exception as e2:  # noqa: BLE001
                job.add(f"   ! 썸네일 대체 생성도 실패(넘어감): {e2}")
        check_cancelled()
    # 6) 최종 렌더
    render_error = None
    if steps.get("render", False) and result.get("srt") and os.path.isdir(images_dir):
        job.stage = "⑥ 최종 렌더"
        out = os.path.join(assets, "최종.mp4")
        try:
            run_render(job, result["srt"], result["flow"], images_dir, result["narration"], out)
            result["video"] = out
        except Exception as e:  # noqa: BLE001
            render_error = e
            job.add(f"   ! 최종 영상 합치기 실패: {e}")
    if channel_of(script) == "mindam" and 대본생성.load_cfg().get("레퍼런스_자동관리"):
        clear_face_reference(job)                 # 다음 편은 다른 주인공이므로 레퍼런스를 비운다
    job.stage = "⑦ 업로드 폴더 정리"
    result["upload_dir"] = make_upload_package(script, result)
    job.add("   ✓ 업로드 폴더: " + result["upload_dir"])
    if render_error is not None:
        raise RuntimeError(f"최종 영상 합치기 실패 (대본·썸네일·제목은 저장됨): {render_error}")
    job.stage = "완료"; job.progress = 1.0
    job.add("✅ 파이프라인 완료 · " + assets)
    result["assets"] = assets
    return result


def verify_final_video(path):
    """최종 파일에 재생 가능한 영상·음성과 유효한 길이가 있는지 확인한다."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) < 1024:
        raise RuntimeError("최종 영상 파일이 만들어지지 않았습니다.")
    ffprobe = 나레이션.find_ffmpeg("ffprobe")
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration",
                           "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError("최종 영상을 검사하지 못했습니다: " + proc.stderr.strip()[-300:])
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    types = {s.get("codec_type") for s in streams}
    duration = float((info.get("format") or {}).get("duration") or 0)
    if "video" not in types or "audio" not in types or duration <= 0:
        raise RuntimeError("최종 영상에 영상 또는 음성 트랙이 없습니다.")
    stream_lengths = [float(s.get("duration") or duration) for s in streams if s.get("codec_type") in ("video", "audio")]
    if stream_lengths and max(stream_lengths) - min(stream_lengths) > 1.0:
        raise RuntimeError("최종 영상과 음성의 길이가 1초 이상 차이 납니다.")
    return duration


def telegram_notice(text):
    cfg = load_json("설정.json", {})
    if not cfg.get("텔레그램_알림", True):
        return False
    try:
        return send_telegram(cfg.get("텔레그램_봇_토큰", ""), cfg.get("텔레그램_채팅_ID", ""), text)
    except Exception as exc:  # 알림 실패가 영상 제작을 중단시키지는 않는다.
        REAL.write(f"텔레그램 알림 실패: {exc}\n")
        return False


def is_shared_failure(message):
    return bool(re.search(r"API.?키|401|403|인증|목소리|voice|8765|편집프로그램|연결.*거부|좌표|확장.*연결|KIE API", message or "", re.I))


def queue_snapshot():
    data = QUEUE.public()
    job = STATE.get("job")
    if job and data.get("current_id"):
        for item in data.get("items", []):
            if item.get("id") == data["current_id"] and item.get("status") == "working":
                item["stage"] = job.stage or "준비 중"
                item["progress"] = round(job.progress, 3)
                break
    return data


QUEUE_WAIT_SEC = 2          # 대기열 워커가 단독 작업이 끝나기를 기다리며 확인하는 간격(초)


def start_queue_worker():
    global QUEUE_THREAD
    if QUEUE_THREAD and QUEUE_THREAD.is_alive():
        return

    def worker():
        while True:
            with QUEUE.lock:
                if QUEUE.data.get("status") != "running":
                    QUEUE.save(); return
                item = next((x for x in QUEUE.data.get("items", []) if x.get("status") == "pending"), None)
                if not item:
                    QUEUE.data.update(status="done", current_id="")
                    QUEUE.save()
                    done = sum(x.get("status") == "done" for x in QUEUE.data.get("items", []))
                    failed = sum(x.get("status") == "error" for x in QUEUE.data.get("items", []))
                    telegram_notice(f"✅ 연속 제작이 모두 끝났습니다.\n완료 {done}편 · 실패 {failed}편")
                    return
                item.update(status="working", status_text="제작 중", stage="준비 중", progress=0.0,
                            attempts=int(item.get("attempts", 0)) + 1, error="")
                QUEUE.data["current_id"] = item["id"]
                QUEUE.save()

            options = dict(QUEUE.data.get("options") or {})
            request = dict(options, channel=item["channel"], title=item["title"], topic=item.get("topic") or {})
            if item["channel"] == "mindam":
                request["bench"] = item.get("topic") or {}
            request.update(item.get("request") or {})          # [이어서 만들기] 로 붙인 항목은 그때의 옵션을 그대로 쓴다
            previous = item.get("result") or {}
            if previous.get("script") and os.path.isfile(previous["script"]):
                request.update(script_file=previous["script"], reuse_prompts=True)
            # 대기열 밖에서 시작한 작업(나레이션 다시 만들기 등)이 돌고 있으면 실패시키지 않고 끝나기를 기다린다
            stopped_while_waiting = False
            while True:
                with LOCK:
                    busy = bool(STATE["job"] and STATE["job"].status == "running")
                if not busy:
                    break
                with QUEUE.lock:
                    if QUEUE.data.get("status") != "running":                     # 기다리는 동안 중단·일시정지됨
                        paused = QUEUE.data.get("status") == "paused"
                        item.update(status="pending" if paused else "cancelled", stage="대기 중" if paused else "취소됨", progress=0.0)
                        QUEUE.data["current_id"] = ""; QUEUE.save(); stopped_while_waiting = True
                    else:
                        item["stage"] = "앞 작업이 끝나기를 기다리는 중"; QUEUE.save()
                if stopped_while_waiting:
                    break
                time.sleep(QUEUE_WAIT_SEC)
            if stopped_while_waiting:
                continue
            try:
                job = run_job("queue_pipeline", lambda active: make_pipeline(active, request))
                while job.status == "running":
                    time.sleep(2)
                    with QUEUE.lock:
                        item["stage"], item["progress"], item["result"] = job.stage or "준비 중", job.progress, dict(job.result)
                        if QUEUE.data.get("status") == "cancelled":
                            job.cancel_requested = True
                        QUEUE.save()
                if job.status != "done":
                    raise RuntimeError(job.error or "제작 작업이 중단됐습니다.")
                result = dict(job.result)
                duration = verify_final_video(result.get("video")) if (request.get("steps") or {}).get("render") else float(result.get("duration") or 0)
                with QUEUE.lock:
                    item.update(status="done", stage="완료", progress=1.0, result=result, duration=duration, error="")
                    QUEUE.data["current_id"] = ""
                    QUEUE.save()
                telegram_notice(f"✅ 영상 한 편이 완성됐습니다.\n주제: {item['title']}\n길이: {duration / 60:.1f}분\n업로드 폴더: {result.get('upload_dir') or result.get('assets', '')}\n다음 주제를 자동으로 시작합니다.")
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                shared = is_shared_failure(message)
                with QUEUE.lock:
                    cancelled = QUEUE.data.get("status") == "cancelled"
                    stopping = QUEUE.data.get("resume_on_start") and job.cancel_requested     # 프로그램 종료로 멈춘 것
                    if stopping:
                        item.update(status="pending", stage="이어서 만들 예정", error="", progress=0.0)
                    else:
                        item.update(status="cancelled" if cancelled else ("pending" if shared else "error"),
                                    stage="취소됨" if cancelled else ("설정 확인 후 다시 대기" if shared else "실패"),
                                    error=message, progress=1.0 if cancelled or not shared else 0.0)
                    QUEUE.data["current_id"] = ""
                    if shared:
                        QUEUE.data["status"] = "paused"
                    QUEUE.save()
                if stopping:
                    return
                if not cancelled:
                    telegram_notice(f"❌ 영상 제작에 실패했습니다.\n주제: {item['title']}\n오류: {message}" +
                                    ("\n공통 설정 문제로 대기열을 일시정지했습니다." if shared else "\n다음 주제를 계속 제작합니다."))
                if shared:
                    return

    QUEUE_THREAD = threading.Thread(target=worker, daemon=True, name="continuous-production")
    QUEUE_THREAD.start()


def create_queue(body):
    raw = body.get("items") or []
    unique, seen = [], set()
    for source in raw:
        title = str(source.get("title") or source.get("제목") or "").strip()
        channel = "mindam" if source.get("channel") == "mindam" else "person"
        key = (channel, title)
        if title and key not in seen:
            seen.add(key)
            unique.append({"id": uuid.uuid4().hex[:12], "channel": channel, "title": title,
                           "topic": source.get("topic") or source, "status": "pending", "stage": "대기 중",
                           "progress": 0.0, "attempts": 0, "result": {}, "error": ""})
    if not unique:
        raise ValueError("연속 제작할 주제를 하나 이상 선택하세요.")
    with QUEUE.lock:
        if QUEUE.data.get("status") in ("running", "paused"):      # 이미 만드는 중 → 거절하지 않고 뒤에 붙인다
            have = {(x.get("channel"), x.get("title")) for x in QUEUE.data.get("items", []) if x.get("status") in ("pending", "working")}
            added = [x for x in unique if (x["channel"], x["title"]) not in have]
            QUEUE.data["items"].extend(added)
            if not QUEUE.data.get("options"):
                QUEUE.data["options"] = body.get("options") or {}
            QUEUE.save()
            paused = QUEUE.data.get("status") == "paused"
        else:
            added, paused = unique, False
            QUEUE.data = {"status": "running", "items": unique, "options": body.get("options") or {},
                          "current_id": "", "updated": ""}
            QUEUE.save()
    start_queue_worker()                          # 단독 작업이 돌고 있으면 워커가 끝나기를 기다렸다가 시작한다
    snap = queue_snapshot()
    snap.update(appended=len(added), paused=paused)
    return snap


def enqueue_pipeline(body):
    """[이어서 만들기]: 다른 작업이 돌고 있으면 거절하지 않고 그 대본을 대기열 뒤에 붙인다 (지금 편이 끝나면 이어서 만든다)."""
    script = body.get("script_file") or ""
    if not os.path.isfile(script):
        raise ValueError("이어서 만들 대본 파일을 찾지 못했습니다.")
    channel = "mindam" if body.get("channel") == "mindam" or channel_of(script) == "mindam" else "person"
    title = os.path.basename(os.path.dirname(script)) if os.path.basename(script) == "final.txt" else re.sub(r"\.txt$", "", os.path.basename(script))
    request = {k: v for k, v in body.items() if k not in ("script_file", "channel", "title", "topic")}
    item = {"id": uuid.uuid4().hex[:12], "channel": channel, "title": title, "topic": {}, "status": "pending",
            "stage": "대기 중 (이어서 만들기)", "progress": 0.0, "attempts": 0, "result": {"script": script}, "error": "",
            "request": request}
    with QUEUE.lock:
        if QUEUE.data.get("status") not in ("running", "paused"):
            QUEUE.data = {"status": "running", "items": [], "options": {}, "current_id": "", "updated": ""}
        if any(x.get("status") in ("pending", "working") and (x.get("result") or {}).get("script") == script
               for x in QUEUE.data.get("items", [])):
            raise ValueError("이 대본은 이미 대기열에 있습니다. 지금 편이 끝나면 이어서 만듭니다.")
        QUEUE.data["items"].append(item); QUEUE.save()
        paused = QUEUE.data.get("status") == "paused"
    start_queue_worker()
    snap = queue_snapshot()
    snap.update(queued=True, paused=paused)
    return snap


# ── HTTP ─────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                                   # 화면이 새로고침되거나 확장이 긴 폴링을 끊은 것 — 오류가 아니므로 로그에 남기지 않는다

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/" or u.path.startswith("/static/"):
                data, mime = static_file("index.html" if u.path == "/" else u.path[len("/static/"):])
                if data is None:
                    self._json({"detail": "not found"}, 404); return
                self.send_response(200); self.send_header("Content-Type", mime); self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            elif u.path == "/api/state":
                cfg = 대본생성.load_cfg()
                job = STATE["job"]
                hide_mindam = bool(cfg.get("이야기형_숨김"))          # 이야기형(민담) 채널을 당분간 쓰지 않을 때: 화면·목록에서 감춘다 (파일은 그대로)
                self._json(dict(topics=topics(), guidelines=guideline_files(),
                                scripts=[x for x in script_files() if not (hide_mindam and os.path.basename(x["path"]) == "final.txt")],
                                hidden_channels=(["mindam"] if hide_mindam else []),
                                keys={"deepseek": mask(cfg.get("API_키_deepseek") or (cfg.get("API_키") if cfg["AI"] in ("deepseek", "deepseek-web") else "")),
                                      "gemini": mask(cfg.get("API_키_gemini") or (cfg.get("API_키") if cfg["AI"] == "gemini" else "")),
                                      "claude": mask(cfg.get("API_키_claude") or (cfg.get("API_키") if cfg["AI"] == "claude" else "")),
                                      "inworld": mask(cfg.get("인월드_API_키", ""))},
                                config=dict(AI=cfg["AI"], 모델=cfg["모델"], 키있음=bool(cfg["API_키"] or os.environ.get("DEEPSEEK_API_KEY")),
                                            대본_글자수=cfg["대본_글자수"], 인월드키있음=bool(cfg.get("인월드_API_키")),
                                            인월드_목소리=cfg.get("인월드_목소리", ""), 인월드_모델=cfg.get("인월드_모델", "inworld-tts-1.5-max"),
                                            인월드_속도=cfg.get("인월드_속도", 1.0),
                                            인월드_목소리_사람=cfg.get("인월드_목소리_사람", cfg.get("인월드_목소리", "")),
                                            인월드_목소리_민담=cfg.get("인월드_목소리_민담", ""),
                                            인월드_속도_사람=cfg.get("인월드_속도_사람", cfg.get("인월드_속도", 1.0)),
                                            인월드_속도_민담=cfg.get("인월드_속도_민담", cfg.get("인월드_속도", 1.0)),
                                            분당_글자수=cfg.get("분당_글자수", 270), 화풍=화풍_별칭.get(cfg.get("화풍", "실사"), cfg.get("화풍", "실사")),
                                            영상변환_방식=cfg.get("영상변환_방식", ""),
                                            후킹_장면수=cfg.get("후킹_장면수", 7), 프롬프트_묶음=cfg.get("프롬프트_묶음", 30),
                                            텔레그램_토큰=mask(cfg.get("텔레그램_봇_토큰", "")), 유튜브_API_키=mask(cfg.get("유튜브_API_키", "")),
                                            텔레그램_채팅_ID=str(cfg.get("텔레그램_채팅_ID", "")),
                                            텔레그램_알림=cfg.get("텔레그램_알림", True), 온보딩_완료=bool(cfg.get("온보딩_완료", False))),
                                base_dir=BASE, extension_dir=os.path.join(BASE, "딥시크_확장"),
                                web_alive=웹큐.extension_alive(), web_hidden=(웹큐._extension_seen["info"] == "hidden"),
                                lengths={k: v["이름"] for k, v in 민담_대본.길이.items() if str(k) != "0"}, styles=list(화풍), style_info=화풍_설명, style_groups=화풍_그룹,
                                style_prefixes={k: image_style_lock(k) for k in 화풍},
                                job=job.to_dict() if job else None, queue=queue_snapshot(), reset_items=reset_items(),
                                channels=채널_연동.status(cfg), profiles=채널_프로필.summary(), layouts=채널_프로필.레이아웃_이름, tones=채널_프로필.사진_톤_이름))
            elif u.path == "/api/job":
                job = STATE["job"]; self._json(job.to_dict() if job else {"status": "none"})
            elif u.path == "/api/version":
                self._json(dict(version=VERSION, current=str(int(os.path.getmtime(os.path.abspath(__file__))))))
            elif u.path == "/api/images":
                self._json(dict(images=list_images(q.get("dir", [""])[0])))
            elif u.path == "/api/image":
                p = os.path.realpath(q.get("path", [""])[0])
                ext = os.path.splitext(p)[1].lower()
                if os.path.commonpath((os.path.realpath(BASE), p)) != os.path.realpath(BASE) or ext not in IMAGE_MIME or not os.path.isfile(p):
                    self._json({"detail": "이미지가 없습니다."}, 404); return
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200); self.send_header("Content-Type", IMAGE_MIME[ext]); self.send_header("Cache-Control", "max-age=3600")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            elif u.path == "/api/bench":
                try:
                    with open("벤치_히트.json", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = {"날짜": "", "채널": [], "히트": []}
                cfg = load_json("설정.json", {})
                data["추가_채널"] = cfg.get("벤치_채널_추가") or []
                self._json(data)
            elif u.path == "/api/channel/analysis":
                cfg = load_json("설정.json", {})
                self._json(채널_연동.analysis(cfg, q.get("channel", ["person"])[0]))
            elif u.path == "/api/trash":
                self._json(trash_status())
            elif u.path == "/api/kie/credit":
                self._json(kie_credit())
            elif u.path == "/api/channel":
                cfg = load_json("설정.json", {})
                for ch in ("person", "mindam"):
                    채널_연동.fetch_in_background(cfg, ch)
                self._json(채널_연동.status(cfg))
            elif u.path == "/api/queue":
                self._json(queue_snapshot())
            elif u.path == "/api/web/next":                       # 크롬 확장이 긴 폴링으로 작업을 가져감
                웹큐.extension_ping("hidden" if q.get("hidden", ["0"])[0] == "1" else "visible")
                j = 웹큐.next_job(float(q.get("wait", ["20"])[0]))
                self._json(j or {})
            elif u.path == "/api/web/status":
                self._json(dict(alive=웹큐.extension_alive(), **웹큐.status()))
            elif u.path == "/ext/content.js":                     # 확장 소스 확인용 (콘솔에서 직접 붙여 넣어 테스트할 때)
                data = open(os.path.join("딥시크_확장", "content.js"), "rb").read()
                self.send_response(200); self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            elif u.path == "/api/guideline":
                self._json(dict(text=read_guideline(q["name"][0])))
            elif u.path == "/api/assets":
                sf = q.get("script", [""])[0]
                if not sf or not os.path.exists(sf):
                    self._json({"detail": "대본 파일이 없습니다."}, 404); return
                a = assets_dir(sf)
                pr = os.path.join(a, "이미지프롬프트.txt") if os.path.basename(sf) == "final.txt" else re.sub(r"\.txt$", "", sf) + "_이미지프롬프트.txt"
                self._json(dict(script=os.path.abspath(sf), assets=os.path.abspath(a), images=os.path.abspath(os.path.join(a, "images")),
                                prompts=os.path.abspath(pr) if os.path.exists(pr) else "",
                                narration=os.path.abspath(os.path.join(a, "나레이션.mp3")) if os.path.exists(os.path.join(a, "나레이션.mp3")) else "",
                                video=os.path.abspath(os.path.join(a, "최종.mp4")) if os.path.exists(os.path.join(a, "최종.mp4")) else ""))
            elif u.path == "/api/workspace":
                self._json(workspace_data(q.get("script", [""])[0]))
            elif u.path == "/api/file":
                p = q["path"][0]
                if not os.path.abspath(p).startswith(os.path.abspath(BASE)):
                    raise ValueError("허용되지 않은 경로")
                with open(p, encoding="utf-8-sig") as f:
                    self._json(dict(text=f.read()))
            else:
                self._json({"detail": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"detail": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/script":
                run_job("script", lambda job: make_person_script(job, body)); self._json({"ok": True})
            elif u.path == "/api/mindam":
                run_job("mindam", lambda job: make_mindam_script(job, body)); self._json({"ok": True})
            elif u.path == "/api/variations":
                run_job("variations", lambda job: make_variations(job, body)); self._json({"ok": True})
            elif u.path == "/api/web/result":
                웹큐.extension_ping()
                ok = 웹큐.finish(body.get("id", ""), result=body.get("text", ""), error=body.get("error", ""))
                self._json({"ok": ok})
            elif u.path == "/api/web/beat":
                웹큐.extension_ping("hidden" if body.get("hidden") else "visible")
                self._json({"ok": 웹큐.heartbeat(body.get("id", ""), body.get("progress", ""))})
            elif u.path == "/api/tts":
                run_job("tts", lambda job: make_tts(job, body)); self._json({"ok": True})
            elif u.path == "/api/workspace/save":
                self._json(save_workspace(body))
            elif u.path == "/api/pipeline":
                with LOCK:
                    busy = bool(STATE["job"] and STATE["job"].status == "running")
                with QUEUE.lock:
                    busy = busy or QUEUE.data.get("status") == "running"
                if busy:
                    self._json(enqueue_pipeline(body))
                else:
                    run_job("pipeline", lambda job: make_pipeline(job, body)); self._json({"ok": True})
            elif u.path == "/api/queue/start":
                self._json(create_queue(body))
            elif u.path == "/api/queue/pause":
                with QUEUE.lock:
                    if QUEUE.data.get("status") == "running": QUEUE.data["status"] = "paused"
                    QUEUE.save()
                self._json(queue_snapshot())
            elif u.path == "/api/queue/resume":
                with QUEUE.lock:
                    if not any(x.get("status") in ("pending", "error") for x in QUEUE.data.get("items", [])):
                        raise ValueError("다시 제작할 대기 항목이 없습니다.")
                    if not any(x.get("status") == "pending" for x in QUEUE.data.get("items", [])):
                        for x in QUEUE.data.get("items", []):
                            if x.get("status") == "error": x.update(status="pending", stage="다시 대기 중", error="", progress=0.0)
                    QUEUE.data["status"] = "running"; QUEUE.save()
                start_queue_worker(); self._json(queue_snapshot())
            elif u.path == "/api/queue/cancel":
                with QUEUE.lock:
                    QUEUE.data["status"] = "cancelled"
                    for x in QUEUE.data.get("items", []):
                        if x.get("status") == "pending": x.update(status="cancelled", stage="취소됨")
                    QUEUE.save()
                if STATE.get("job") and STATE["job"].status == "running": STATE["job"].cancel_requested = True
                self._json(queue_snapshot())
            elif u.path == "/api/queue/remove":
                with QUEUE.lock:
                    target = body.get("id", "")
                    if target == QUEUE.data.get("current_id"): raise ValueError("현재 제작 중인 항목은 삭제할 수 없습니다.")
                    QUEUE.data["items"] = [x for x in QUEUE.data.get("items", []) if x.get("id") != target]
                    QUEUE.save()
                self._json(queue_snapshot())
            elif u.path == "/api/cancel":
                j = STATE["job"]
                if j and j.status == "running":
                    j.cancel_requested = True
                self._json({"ok": True})
            elif u.path == "/api/stop-all":                      # 화면의 [■ 중단] 하나로 전부: 지금 작업·남은 주제·편집프로그램의 이미지 생성·영상 변환
                stopped = []
                with QUEUE.lock:
                    if QUEUE.data.get("status") in ("running", "paused"):
                        QUEUE.data["status"] = "cancelled"; stopped.append("대기열")
                        for x in QUEUE.data.get("items", []):
                            if x.get("status") == "pending": x.update(status="cancelled", stage="취소됨")
                        QUEUE.save()
                j = STATE["job"]
                if j and j.status == "running":
                    j.cancel_requested = True; stopped.append("지금 작업")
                for path, name in (("/api/gen/stop", "이미지 생성"), ("/api/vgen/stop", "영상 변환")):
                    try:
                        _rq.post(AIP + path, json={}, timeout=5); stopped.append(name)
                    except _rq.RequestException:
                        pass
                self._json({"ok": True, "stopped": stopped})
            elif u.path == "/api/restart":
                if STATE["job"] and STATE["job"].status == "running" and not STATE["job"].cancel_requested:
                    raise ValueError("지금 만드는 편이 끝난 뒤에 다시 시작할 수 있습니다. 바로 하려면 [■ 중단]을 먼저 누르세요.")
                self._json({"ok": True})
                threading.Thread(target=restart_program, args=(self.server,), daemon=True).start()
            elif u.path == "/api/web/test":                  # 딥시크 웹이 실제로 답하는지 짧은 질문으로 확인 (최대 2분)
                if not 웹큐.extension_alive():
                    raise ValueError("딥시크 확장이 연결되어 있지 않습니다. 크롬에서 chat.deepseek.com 탭을 열어 두세요.")
                t0 = time.time()
                jid = 웹큐.submit("아래 질문에 숫자 하나로만 답하세요. 다른 말은 쓰지 마세요.\n\n1 더하기 1은?", {"new_chat": True})
                try:
                    out = 웹큐.wait(jid, timeout=120)
                except RuntimeError as exc:
                    raise ValueError(f"딥시크 웹이 {int(time.time() - t0)}초 동안 답하지 않았습니다: {exc}")
                self._json(dict(ok=True, seconds=round(time.time() - t0, 1), answer=out.strip()[:80]))
            elif u.path == "/api/profile/preview":            # 저장된 브랜드로 샘플 썸네일 (최근 raw 원본 또는 장면 이미지 사용)
                slot = body.get("slot") or "person"
                self._json(dict(ok=True, images=brand_preview(slot)))
            elif u.path == "/api/profile":
                slot = body.get("slot") or "person"
                saved = 채널_프로필.save(slot, body.get("data") or {})
                self._json(dict(ok=True, profile=saved))
            elif u.path == "/api/youtube/test":
                cfg = load_json("설정.json", {})
                key = str(cfg.get("유튜브_API_키", "") or "").strip()
                if not key:
                    raise ValueError("유튜브 API 키가 저장돼 있지 않습니다.")
                if key == str(cfg.get("API_키_gemini", "") or "").strip():
                    raise ValueError("제미나이 키와 같은 키가 들어가 있습니다. 유튜브 API 키는 따로 발급해야 합니다. " + 유튜브_API.KEY_HELP)
                bad = 유튜브_API.check_key_format(key)
                if bad:
                    raise ValueError(bad)
                url = 채널_연동.channel_url(cfg, "person") or 채널_연동.channel_url(cfg, "mindam") or "UC_x5XG1OV2P6uZZ5FSM9Ttw"
                info = 유튜브_API.fetch_channel(key, url, 5)
                self._json(dict(ok=True, name=info["name"], subs=info["subs"], sample=len(info["videos"])))
            elif u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=shutdown_program, args=(self.server,), daemon=True).start()
            elif u.path == "/api/reset":
                self._json(reset_output(body.get("id", ""), body.get("scope", "")))
            elif u.path == "/api/trash/empty":
                self._json(empty_trash(body.get("older_than_days")))
            elif u.path == "/api/reset-all":
                self._json(reset_everything())
            elif u.path == "/api/delete-script":
                self._json(delete_script(body.get("script_file", "")))
            elif u.path == "/api/hook/dropshot":               # 앞 n장을 드롭샷 좌표로 영상 변환 (KIE 대신)
                sf = body.get("script_file", "")
                if not sf or not os.path.exists(sf):
                    raise ValueError("대본을 먼저 고르세요.")
                a = assets_dir(sf)
                pr = os.path.join(a, "이미지프롬프트.txt") if os.path.basename(sf) == "final.txt" else re.sub(r"\.txt$", "", sf) + "_이미지프롬프트.txt"
                scenes = [int(n) for n in (body.get("scenes") or [1, 2, 3, 4, 5, 6, 7])]
                try:
                    start_dropshot_videos(os.path.join(a, "images"), pr, scenes)
                except SystemExit as e:
                    raise ValueError(str(e))
                self._json({"ok": True, "scenes": scenes})
            elif u.path == "/api/thumbnail":
                run_job("thumbnail", lambda job: make_thumbnails(job, body)); self._json({"ok": True})
            elif u.path == "/api/thumbnail/compose":            # raw 원본에 문구만 다시 얹기 (빠름, 비용 없음 → 다른 작업 중에도 된다)
                j = STATE["job"]
                if j and j.status == "running" and (j.kind == "thumbnail" or "썸네일" in (j.stage or "")):
                    raise ValueError("지금 썸네일을 만드는 중입니다. 잠시 뒤 다시 눌러 주세요.")
                self._json(compose_thumbnails(body.get("script_file", "")))
            elif u.path == "/api/optimize":
                run_job("optimize", lambda job: make_optimize_only(job, body)); self._json({"ok": True})
            elif u.path == "/api/timestamps":
                self._json(make_timestamps(body))
            elif u.path == "/api/images":
                run_job("images", lambda job: make_image_prompts(job, body)); self._json({"ok": True})
            elif u.path == "/api/restyle-2d":
                j = STATE["job"]
                if j and j.status == "running" and (j.kind in ("images", "pipeline", "queue_pipeline") and "프롬프트" in (j.stage or "")):
                    raise ValueError("지금 이미지 프롬프트를 만드는 중입니다. 잠시 뒤 다시 눌러 주세요.")
                self._json(restyle_script_prompts_2d(body.get("script_file", "")))
            elif u.path == "/api/guideline":
                write_guideline(body["name"], body["text"]); self._json({"ok": True})
            elif u.path == "/api/config":
                cfg = load_json("설정.json", {})
                prev_ai = (cfg.get("AI") or "").strip().lower()
                for k in ("AI", "API_키", "모델", "대본_글자수", "인월드_API_키", "인월드_목소리", "인월드_모델", "인월드_속도",
                          "인월드_목소리_사람", "인월드_목소리_민담", "인월드_속도_사람", "인월드_속도_민담", "인월드_온도", "이야기형_숨김", "레퍼런스_자동관리", "영상변환_방식", "분당_글자수", "화풍", "후킹_장면수",
                          "API_키_deepseek", "API_키_gemini", "API_키_claude", "프롬프트_묶음",
                          "텔레그램_봇_토큰", "텔레그램_채팅_ID", "텔레그램_알림", "내_채널", "민담_채널", "유튜브_API_키", "온보딩_완료"):
                    if k in body and (body[k] != "" or k in ("내_채널", "민담_채널", "인월드_목소리_민담", "모델")):     # 빈 값을 허용하는 항목: 지우면 기본으로 돌아감
                        cfg[k] = str(body[k]).strip() if isinstance(body[k], str) else body[k]
                # AI 를 바꾸면 이전 AI 의 모델 이름이 남지 않게 (예: gemini 인데 deepseek-chat) — 새 AI 의 기본 모델을 쓴다
                if "AI" in body and body["AI"] != prev_ai and "모델" not in body:
                    cfg["모델"] = ""
                # 서비스별 키 ↔ 현재 AI 의 키 동기화 (AI 를 바꾸면 그 서비스에 저장된 키가 자동으로 쓰인다)
                ai = (cfg.get("AI") or "deepseek").strip().lower()
                if ai == "deepseek-web":
                    ai = "deepseek"
                if body.get("API_키"):
                    cfg["API_키_" + ai] = cfg["API_키"]
                if cfg.get("API_키_" + ai):
                    cfg["API_키"] = cfg["API_키_" + ai]
                with open("설정.json", "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                for ch, key in 채널_연동.CONFIG_KEY.items():
                    if key in body or "유튜브_API_키" in body:
                        채널_연동.fetch_in_background(cfg, ch, force=True)
                self._json({"ok": True})
            elif u.path == "/api/bench/run":
                run_job("bench", lambda job: run_benchmark(job, body)); self._json({"ok": True})
            elif u.path == "/api/bench/channels":
                cfg = load_json("설정.json", {})
                cfg["벤치_채널_추가"] = [str(x).strip() for x in (body.get("channels") or []) if str(x).strip()]
                with open("설정.json", "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                self._json({"ok": True, "count": len(cfg["벤치_채널_추가"])})
            elif u.path == "/api/topics/hide":                    # 추천 목록의 주제를 '이미 만든 주제'로 기록해 다시 안 나오게
                title = str(body.get("title") or "").strip()
                if not title:
                    raise ValueError("숨길 주제가 없습니다.")
                ch = "mindam" if body.get("channel") == "mindam" else "person"
                mark_used(title, "민담_사용한_주제.txt" if ch == "mindam" else "사용한_주제.txt")
                SEEN_TOPICS[ch].add(title)
                self._json(dict(ok=True, topics=topics()))
            elif u.path == "/api/topics/refresh":
                self._json(refresh_topics(body.get("channel"), body.get("shown") or []))
            elif u.path == "/api/channel/refresh":
                cfg = load_json("설정.json", {})
                ch = body.get("channel") or "person"
                if not 채널_연동.channel_url(cfg, ch):
                    raise ValueError("먼저 채널 주소를 저장하세요.")
                info = 채널_연동.fetch(cfg, ch)           # 몇 초 걸리므로 화면은 기다렸다가 결과를 받는다
                if info.get("error"):
                    raise ValueError(info["error"])
                self._json(채널_연동.status(cfg)[ch])
            elif u.path == "/api/channel/check":
                cfg = load_json("설정.json", {})
                self._json(채널_연동.check(cfg, body.get("channel") or "person", str(body.get("title", ""))))
            elif u.path == "/api/telegram/chats":
                cfg = load_json("설정.json", {})
                token = body.get("token") or cfg.get("텔레그램_봇_토큰", "")
                self._json({"chats": recent_chats(token)})
            elif u.path == "/api/telegram/test":
                cfg = load_json("설정.json", {})
                send_telegram(cfg.get("텔레그램_봇_토큰", ""), cfg.get("텔레그램_채팅_ID", ""),
                              "✅ 유튜브 자동 제작 프로그램과 텔레그램이 연결되었습니다.")
                self._json({"ok": True})
            elif u.path == "/api/open":
                p = body.get("path", "")
                if os.path.isfile(p):
                    subprocess.Popen(["explorer", "/select,", os.path.abspath(p)])
                elif os.path.isdir(p):
                    os.startfile(os.path.abspath(p))
                self._json({"ok": True})
            else:
                self._json({"detail": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"detail": str(e)}, 400)


화면_폴더 = os.path.join(BASE, "화면")


IMAGE_NAME = re.compile(r"^(\d{1,4})\.(png|jpe?g|webp|avif|mp4)$", re.I)
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".avif": "image/avif"}


def list_images(folder):
    """이미지 폴더의 NNN.jpg / NNN.mp4 목록 (편집프로그램 없이도 화면에 바로 보이도록 이 서버가 직접 읽는다)."""
    root = os.path.realpath(BASE)
    path = os.path.realpath(folder or "")
    if not folder or os.path.commonpath((root, path)) != root or not os.path.isdir(path):
        return []
    out = []
    for name in sorted(os.listdir(path)):
        m = IMAGE_NAME.match(name)
        if m:
            full = os.path.join(path, name)
            out.append(dict(no=int(m.group(1)), name=name, video=name.lower().endswith(".mp4"), path=full, mtime=int(os.path.getmtime(full))))
    return out


def static_file(name):
    """화면/ 폴더의 파일을 (내용, MIME) 로 돌려준다. 폴더 밖 경로는 거부한다."""
    path = os.path.realpath(os.path.join(화면_폴더, name))
    if os.path.commonpath((os.path.realpath(화면_폴더), path)) != os.path.realpath(화면_폴더) or not os.path.isfile(path):
        return None, None
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
        mime += "; charset=utf-8"
    with open(path, "rb") as f:
        return f.read(), mime



def auto_resume_queue():
    """프로그램을 껐다 켜면, 만들다 만 대기열을 편집프로그램이 뜨는 대로 자동으로 이어서 만든다.
    (대본·프롬프트·나레이션·받아 둔 이미지·썸네일 원본은 그대로 재사용)"""
    with QUEUE.lock:
        items = QUEUE.data.get("items", [])
        for x in items:                                # 종료·강제 종료로 끊긴 편은 다시 대기로
            if x.get("status") == "error" and "취소" in str(x.get("error", "")) and int(x.get("attempts", 0)) < 5:
                x.update(status="pending", stage="이어서 만들 예정", error="")
        pending = [x for x in items if x.get("status") == "pending"]
        resumable = QUEUE.data.get("status") == "running" or QUEUE.data.get("resume_on_start") or (QUEUE.data.get("status") == "done" and pending)
        if not resumable or not pending:
            return
        QUEUE.data["status"] = "running"; QUEUE.data["resume_on_start"] = False; QUEUE.save()
    print(f"이어서 만들기: 대기열 {len(pending)}편 · 편집프로그램이 준비되면 자동으로 시작합니다")
    for _ in range(60):                               # 편집프로그램(8765)이 뜰 때까지 최대 3분
        try:
            aip("/api/info", timeout=5); break
        except Exception:  # noqa: BLE001
            time.sleep(3)
    else:
        print("편집프로그램이 켜지지 않아 자동으로 이어가지 못했습니다. 화면에서 [▶ 계속]을 누르세요.")
        return
    time.sleep(3)
    start_queue_worker()


def main():
    if "--wait-port" in sys.argv:                    # 이전 프로세스가 포트를 놓을 때까지 최대 10초 (접속이 되는 동안은 아직 살아 있는 것)
        import socket
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
                    pass
                time.sleep(0.25)
            except OSError:
                break
    cfg = load_json("설정.json", {})
    for ch in 채널_연동.CONFIG_KEY:
        채널_연동.fetch_in_background(cfg, ch)      # 내 채널 제목을 미리 받아 둔다 (없거나 오래됐을 때만)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"대본 만들기 화면: {ADDR}  (종료: 이 창을 닫거나 Ctrl+C)")
    threading.Thread(target=auto_resume_queue, daemon=True, name="auto-resume").start()
    try:
        old = empty_trash(TRASH_KEEP_DAYS)
        if old["removed"]:
            print(f"휴지통 정리: {TRASH_KEEP_DAYS}일 지난 {old['removed']}개 항목을 지웠습니다")
    except Exception as exc:  # noqa: BLE001
        print("휴지통 정리 실패:", exc)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(ADDR)).start()
    if QUEUE.data.get("status") == "running" and any(x.get("status") == "pending" for x in QUEUE.data.get("items", [])):
        threading.Timer(8.0, start_queue_worker).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()                      # 8766 포트 반환 — 새 프로세스가 같은 포트를 잡을 수 있게
    if RESTART["requested"]:
        spawn_restart()

if __name__ == "__main__":
    main()
