# -*- coding: utf-8 -*-
"""
대본선택.py — 주제를 고르면 DeepSeek(설정.json 의 AI)으로 대본을 뽑아 주는 로컬 웹 화면

  대본선택.bat  →  http://127.0.0.1:8766  이 열립니다.
  · 사람의 이유: 주제_리포트(계획.json·후보.json)의 주제 또는 직접 입력 → 대본생성.py 의 9구간 대본
  · 민담·야담:   제목이나 장르 입력 → 민담_대본.py 의 기획→챕터→합본 대본
  · 이미지 프롬프트: 만든 대본을 문장별 이미지 프롬프트(===001=== 형식)로 변환
  지침은 지침/ 폴더의 txt 를 골라 쓰고, 화면에서 바로 고쳐 저장할 수 있습니다.
"""
import sys, os, re, io, json, glob, time, threading, datetime, webbrowser, urllib.parse, subprocess, shutil, uuid
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
import requests as _rq
from 제작대기열 import QueueStore, recent_chats, send_telegram

PORT = 8766
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
                    started=self.started, partial=self._buf[-60:], stage=self.stage, progress=round(self.progress, 3))

REAL = sys.stdout
STATE = {"job": None}
LOCK = threading.Lock()
QUEUE = QueueStore(os.path.join(대본_폴더, "_상태", "제작대기열.json"))
QUEUE_THREAD = None


def shutdown_program(server):
    """Stop the editor's image runner, then close both local servers."""
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


def run_job(kind, fn):
    with LOCK:
        if STATE["job"] and STATE["job"].status == "running":
            raise RuntimeError("이미 진행 중인 작업이 있습니다. 끝난 뒤 다시 시도하세요.")
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
    script = [os.path.basename(f) for f in files if "이미지" not in os.path.basename(f)]
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
    cands = [t for t in cands if t.get("제목", "").strip() not in used_person]
    mindam = [t for t in load_json("민담_후보.json", []) if t.get("제목", "").strip() not in used_mindam]
    return dict(plan=plan, candidates=cands, mindam=mindam)

def read_guideline(name):
    p = os.path.join(지침_폴더, name)
    if not os.path.abspath(p).startswith(os.path.abspath(지침_폴더)) or not os.path.exists(p):
        raise FileNotFoundError(name)
    with open(p, encoding="utf-8-sig") as f:
        return f.read()

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


# ── 작업 1: 사람의 이유 대본 ───────────────────────────────────
def make_person_script(job, req):
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    topic = req.get("topic") or {}
    t = {"제목": topic.get("제목") or req.get("title", "").strip(), "카테고리": topic.get("카테고리", ""), "오프닝": topic.get("오프닝", ""),
         "다룰내용": topic.get("다룰내용", []), "출처후보": topic.get("출처후보", []), "태그": topic.get("태그", [])}
    if not t["제목"]:
        raise SystemExit("주제(제목)를 입력하거나 목록에서 고르세요.")
    system = read_guideline(req.get("guideline") or "사람의이유_대본지침.txt")
    target = int(req.get("target") or cfg["대본_글자수"])
    n_parts = max(1, -(-target // 4500))          # 한 번에 4,500자 이하로 나눠 요청
    cpm = int(cfg.get("분당_글자수", 270) or 270)
    job.add(f"AI: {ai.name} ({ai.model}) · 목표 {target:,}자 (약 {target / cpm:.0f}분) · 지침 {req.get('guideline')}")
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
    sys.path.insert(0, r"C:\Users\baseo\Downloads\편집프로그램")
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
    """검증된 제목 → 베리에이션 A~D (v11.3 1-B). 슬롯 분해 후 알맹이를 바꾼 4개."""
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
    # ── 야담·민담·옛이야기용 (조선 배경 고정, 아동풍 금지)
    "사극 웹툰": "Korean historical manhwa webtoon illustration, Joseon dynasty setting, adults in period-accurate hanbok and gat, "
              "clear bold line art, flat cel-shaded coloring, refined detailed eyes, dramatic composition, no childish or cute style, 16:9 aspect ratio",
    "사극 실사": "photorealistic cinematic still from a Korean Joseon-era period drama film, adults in authentic hanbok, hanok and old village sets, "
              "warm oil-lamp and daylight lighting, shallow depth of field, film grain, 16:9 aspect ratio",
    "민화": "Korean minhwa folk painting style, flat vivid mineral pigments, bold outlines, decorative flattened perspective, "
           "tigers magpies peonies motifs where fitting, Joseon-era figures in hanbok, hanji paper texture, 16:9 aspect ratio",
    "풍속화": "Korean genre painting style in the manner of Joseon-era pungsokdo, fine ink brush lines with light color washes, "
            "everyday village and market life, expressive posture, aged hanji paper, 16:9 aspect ratio",
    "수묵담채": "Korean sumukdamchae ink and light-color wash painting, expressive brushwork, misty mountains and hanok, "
             "Joseon-era figures, generous negative space, hanji texture, 16:9 aspect ratio",
    "한지 동화": "warm storybook illustration for adults printed on textured hanji paper, soft gouache colors, gentle rounded shapes, "
              "Joseon-era village and hanbok, cozy oil-lamp glow at night, refined faces not childish, 16:9 aspect ratio",
    "목판화": "Korean woodblock print style, thick carved black lines, limited earthy color palette, rough paper texture, "
            "Joseon-era scene, strong contrast and silhouette, 16:9 aspect ratio",
    "괴담 극화": "dark dramatic Korean gekiga-style illustration for ghost and folklore tales, heavy ink shadows, cold moonlight with a single warm lantern, "
              "Joseon-era hanok and forest, eerie but non-graphic, faces clearly visible, 16:9 aspect ratio",
}
화풍_설명 = {
    "실사": "사진 같은 시네마틱", "애니": "웹툰·셀 채색", "2D 일러스트": "선명한 성인용 2D·레퍼런스 유지", "파스텔": "부드러운 수채", "수묵": "한지·먹 느낌",
    "사극 웹툰": "조선 배경 웹툰 극화 (인트로 지침 스타일)", "사극 실사": "조선 사극 영화 스틸", "민화": "호랑이·까치 민화풍 평면 채색",
    "풍속화": "김홍도·신윤복 풍속화 붓선", "수묵담채": "먹 + 옅은 채색, 여백", "한지 동화": "따뜻한 한지 그림책 (어른용)",
    "목판화": "굵은 목판 선, 흙빛 팔레트", "괴담 극화": "귀신·도깨비 이야기용 어둡고 극적",
}
화풍_그룹 = {"공통": ["실사", "애니", "2D 일러스트", "파스텔", "수묵"],
          "야담·민담·옛이야기": ["사극 웹툰", "사극 실사", "민화", "풍속화", "수묵담채", "한지 동화", "목판화", "괴담 극화"]}

def image_style_lock(style):
    """선택한 화풍이 뒤의 장면 설명과 충돌해도 사진풍으로 바뀌지 않게 고정한다."""
    base = 화풍.get(style, 화풍["2D 일러스트"])
    if style in ("실사", "사극 실사"):
        return base
    return (f"STRICT STYLE LOCK: every image must be {style} style. {base} "
            "Keep the same linework, character design, proportions and color palette as the uploaded Dropshot reference image. "
            "If a later scene description conflicts, this style lock takes priority. "
            "Never generate a photo, photorealistic face, live-action still, realistic skin texture, 3D render or mixed-media image.")

def split_sentences(body):
    return 나레이션.split_sentences(body)      # 이미지 프롬프트·나레이션·자막이 같은 문장 번호를 쓰도록 한 곳에서 나눔

def script_body(text):
    if "[대본]" in text:
        text = text.split("[대본]", 1)[1]
    if "===sum===" in text:
        text = text.split("===sum===", 1)[0]
    return text.strip()

def make_image_prompts(job, req):
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    path = req.get("script_file", "")
    if not path or not os.path.exists(path):
        raise SystemExit("대본 파일을 고르세요.")
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    body = fix_script_sentences(script_body(raw))
    sents = split_sentences(body)
    if not sents:
        raise SystemExit("대본에서 문장을 찾지 못했습니다.")
    system = read_guideline(req.get("guideline") or "이미지프롬프트_변환지침(DeepSeek).txt")
    style = image_style_lock(req.get("style", "2D 일러스트"))
    chunk = int(req.get("chunk") or 25)
    job.add(f"AI: {ai.name} ({ai.model}) · 문장 {len(sents)}개 · {chunk}문장씩 · 화풍 {req.get('style', '실사')}")
    outs = []
    for s in range(0, len(sents), chunk):
        e = min(s + chunk, len(sents))
        lines = "\n".join(f"{i+1:03d}. {sents[i]}" for i in range(s, e))
        user = (f"[화풍·화면 비율] {style}\n"
                + ("[레퍼런스] 드롭샷 References 패널에 업로드된 이미지를 반드시 참조한다. "
                   "C형의 동일 인물은 얼굴형·눈·머리·체형·의상을 유지하고, "
                   "A형은 인물을 억지로 추가하지 말고 선화·색감만 일치시킨다. "
                   "각 영어 프롬프트에 이 레퍼런스 지시를 명시한다. 수채화·사진·3D 표현은 금지한다.\n"
                   if req.get("style") == "2D 일러스트" else "")
                +
                f"[이번 범위] {s+1:03d} ~ {e:03d} (총 {e-s}개 장면)\n\n[이번 범위 원문]\n{lines}\n\n"
                f"위 {e-s}개 문장 각각에 대해 ===NNN=== 장면 블록을 {s+1:03d}부터 {e:03d}까지 순서대로 출력한다.")
        job.add(f"   {s+1:03d}~{e:03d} 변환 ")
        out = ai.ask(system, user).replace("```", "").strip()
        got = re.findall(r"===(\d{3})===", out)
        if len(got) != e - s:
            job.add(f"   ! 장면 수 {len(got)}개 (기대 {e-s}개) — 그대로 저장하되 확인 필요")
        outs.append(out)
    text = "\n\n".join(outs) + "\n"
    if path.endswith("final.txt"):
        out_path = os.path.join(os.path.dirname(path), "이미지프롬프트.txt")
    else:
        out_path = re.sub(r"\.txt$", "", path) + "_이미지프롬프트.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    # 플로우 txt (Auto-Image Placer 용: N번 이미지 = N번 문장)
    flow_path = re.sub(r"\.txt$", "", out_path) + "_플로우.txt"
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write("# 이미지번호: 자막번호 (문장 1개 = 이미지 1장 기준)\n")
        for i in range(len(sents)):
            f.write(f"{i+1}: {i+1}\n")
    job.add(f"✓ 저장: {out_path}")
    job.add("비용: " + ai.cost_text())
    return dict(file=out_path, flow=flow_path, scenes=len(sents), cost=ai.cost_text())


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
    """대본 파일 → 자료 폴더 (민담은 대본 폴더 그대로, 사람의 이유는 옆에 '<이름>_자료')."""
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
    title = saved.get("title") or _block(script_text, "제목")
    return dict(script_file=os.path.abspath(script_file), assets=os.path.abspath(assets), script=script_text,
                prompts=Path(prompts).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(prompts) else "",
                prompts_file=os.path.abspath(prompts),
                srt=Path(srt).read_text(encoding="utf-8-sig", errors="replace") if os.path.isfile(srt) else "",
                srt_file=os.path.abspath(srt), narration=os.path.abspath(os.path.join(assets, "나레이션.mp3")),
                images=os.path.abspath(os.path.join(assets, "images")), thumbnails=thumbnails,
                thumbnail_dir=os.path.abspath(thumb_dir), title=title,
                description=saved.get("description") or _block(script_text, "설명글") or _block(opt_text, "설명글"),
                sources=saved.get("sources") or _block(script_text, "출처"),
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
    sents = split_sentences(body)
    out = req.get("out_dir") or assets_dir(path)
    channel = req.get("channel") or channel_of(path)
    voice, speed = voice_for(cfg, channel)
    if req.get("voice_id"):
        voice = req["voice_id"]
    if req.get("speed"):
        speed = float(req["speed"])
    if not voice:
        raise SystemExit(f"{'민담·야담' if channel == 'mindam' else '사람의 이유'} 채널 목소리 ID 가 없습니다. [설정] 탭의 인월드 목소리에 넣어주세요.")
    job.add(f"   목소리: {'민담·야담' if channel == 'mindam' else '사람의 이유'} 채널 → {voice} · 속도 {speed}")
    job.stage = "나레이션 합성"
    r = 나레이션.synthesize(sents, out, req.get("api_key") or cfg.get("인월드_API_키", ""), voice,
                         req.get("model") or cfg.get("인월드_모델", "inworld-tts-1.5-max"), speed,
                         log=job.add, cancel=lambda: job.cancel_requested,
                         subtitle_lines=2 if channel == "mindam" else 1)
    return r


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


def run_image_generation(job, prompts_file, images_dir, style_prefix=""):
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
    body = dict(prompts_file=os.path.abspath(prompts_file), output_dir=os.path.abspath(images_dir), download_dir=dl,
                prompt_xy=xy["prompt"], generate_xy=xy.get("generate") or xy["prompt"], download_xy=xy["download"],
                wait_generate=float(ui.get("wait_generate") or 60), wait_download=float(ui.get("wait_download") or 120), window_keyword=ui.get("window_keyword") or "드롭샷", auto_generate=ui.get("auto_generate", True) is not False,
                wait_next=0.5, start_no=1, end_no=0, skip_existing=True,
                style_prefix=style_prefix or ui.get("style_prefix") or "", retries=1)
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
            job.add(f"   ✓ 이미지 {len(st.get('done', []))}장 · 실패 {len(st.get('failed', []))}장")
            return st
        time.sleep(4)


def run_hook_videos(job, images_dir, prompts_file, scenes, out_dir=None):
    body = dict(images_dir=os.path.abspath(images_dir), prompts_file=os.path.abspath(prompts_file), scenes=scenes,
                output_dir=os.path.abspath(out_dir) if out_dir else "")
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
                               "srt_font", "srt_font_size", "srt_margin_v", "srt_color", "srt_outline_color", "srt_outline", "srt_bold",
                               "srt_box", "srt_box_color", "srt_box_alpha", "srt_align", "srt_fonts_dir",
                               "bgm", "bgm_volume", "bgm_duck", "bgm_fade") if k in ui}
    body = dict(srt=os.path.abspath(srt), flow=os.path.abspath(flow), images=os.path.abspath(images_dir), narration=os.path.abspath(narration),
                subtitle_mov="", match_mode="manual", output_mode="mp4", output=os.path.abspath(output), width=1920, height=1080, fps=30,
                fit="cover", ken_burns=ken_burns, kb_zoom=0.12, transition="none", transition_duration=0.5, burn_srt=True,
                srt_font="Malgun Gothic", srt_font_size=22, srt_bold=True, srt_outline=3.5, crf=18, preset="medium")
    body.update(keep); body["burn_srt"] = True
    j = aip("/api/render", body, timeout=120)
    while True:
        if job.cancel_requested:
            aip(f"/api/jobs/{j['job_id']}/cancel", {}); raise RuntimeError("취소됨")
        st = aip(f"/api/jobs/{j['job_id']}")
        job.stage = "최종 렌더링: " + (st.get("stage") or st.get("status")); job.progress = st.get("progress", 0)
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
        m = re.match(r"\s*\d+\s*[.)]\s*상단\s*[:：]\s*(.+?)\s*/\s*하단\s*[:：]\s*(.+?)(?:\s*/\s*이미지\s*[:：]\s*(.+))?\s*$", line)
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


def make_thumbnails(job, req):
    """대본 폴더 → 썸네일 프롬프트 3개(딥시크) → 이미지 생성(편집프로그램 좌표 클릭) → 문구 합성 → 썸네일_1~3.jpg"""
    cfg = 대본생성.load_cfg()
    ai = AI(cfg)
    script = req.get("script_file", "")
    if not script or not os.path.exists(script):
        raise SystemExit("대본 파일을 고르세요.")
    assets = assets_dir(script)
    is_mindam = os.path.basename(script) == "final.txt"
    opt_path = os.path.join(assets, "유튜브_최적화.txt") if is_mindam else re.sub(r"\.txt$", "", script) + "_유튜브최적화.txt"
    opt_text = open(opt_path, encoding="utf-8").read() if os.path.exists(opt_path) else ""
    copies = parse_thumb_copies(opt_text)
    if not copies:                                    # 최적화 파일이 없으면 제목에서 대충 두 줄
        title = ""
        raw = open(script, encoding="utf-8-sig").read()
        m = re.search(r"\[제목\]\s*\n(.+)", raw)
        if m:
            title = re.sub(r"\s*\|.*$", "", m.group(1)).strip()
        elif is_mindam:
            title = re.sub(r"^\d{4}-\d\d-\d\d_", "", os.path.basename(assets))
        copies = fallback_thumb_copies(title)
        job.add("   최적화 파일이 없어 제목으로 문구를 만듦")
    brief = ""
    bp = os.path.join(assets, "thumbnail_brief.md")
    if os.path.exists(bp):
        brief = open(bp, encoding="utf-8").read()[:1500]
    style = 화풍.get(req.get("style", "실사"), 화풍["실사"])
    position = "bottom" if is_mindam else "left"
    layout = ("조선 시대 인물 2~3명과 사건 장소가 함께 보이는 넓은 이야기 장면, 문구가 들어갈 화면 아래쪽은 어둡고 단순하게"
              if is_mindam else
              "감정이 선명한 현대 한국인 한 명을 화면 오른쪽에 크게, 문구가 들어갈 왼쪽 55%는 어둡고 단순하게")
    user = (f"[화풍] {style}\n[문구 위치] {'하단' if position == 'bottom' else ('상단' if position == 'top' else '좌측')}\n"
            f"[채널] {'민담·야담' if is_mindam else '사람의 이유'}\n[구도] {layout}. 유튜브 썸네일용 강한 명암과 스마트폰에서도 즉시 읽히는 단순한 장면\n\n[썸네일 문구]\n"
            + "\n".join(f"{i}. 상단: {t} / 하단: {b}" + (f" / 이미지: {d}" if d else "") for i, (t, b, d) in enumerate(copies, 1))
            + (f"\n\n[브리프]\n{brief}" if brief else ""))
    job.stage = "썸네일 프롬프트"
    job.add("   썸네일 프롬프트 3개 ")
    text = ai.ask(read_guideline("썸네일_지침.txt"), user).replace("```", "")
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
    # 합성
    ui = ((aip("/api/info").get("config") or {}).get("ui") or {})
    font = req.get("font") or ui.get("srt_font") or "Malgun Gothic"
    outs = []
    for i, p in enumerate(prompts, 1):
        cands = [f for f in os.listdir(raw_dir) if f.startswith(f"{i:03d}.")] if os.path.isdir(raw_dir) else []
        if not cands:
            job.add(f"   ! 썸네일 {i} 이미지 없음"); continue
        top, bottom, _ = copies[(i - 1) % len(copies)]
        out = os.path.join(tdir, f"썸네일_{i}.jpg")
        aip("/api/thumbnail/compose", dict(image=os.path.join(raw_dir, cands[0]), out=out, top=top, bottom=bottom,
                                            font="Malgun Gothic Bold",
                                            top_color="#FF3B30" if is_mindam else "#FFE45C",
                                            bottom_color="#63FF66" if is_mindam else "#FF3B30",
                                            position=position, size=106, box=True))
        outs.append(out)
        job.add(f"   ✓ {out}  ({top} / {bottom})")
    job.add("비용: " + ai.cost_text())
    return dict(thumbnails=outs, dir=tdir, cost=ai.cost_text())


# ── 작업 5: 원클릭 파이프라인 ─────────────────────────────────
def make_pipeline(job, req):
    """주제 → 대본 → 최적화 → 이미지 프롬프트 → 나레이션(인월드) → 이미지 자동 생성(편집프로그램) → [후킹 영상] → [최종 렌더]"""
    def check_cancelled():
        if job.cancel_requested:
            raise RuntimeError("사용자가 연속 제작을 중단했습니다.")
    steps = req.get("steps") or {}
    if int(steps.get("hook", 0) or 0) > 0 and not aip("/api/info").get("kie_key_saved"):
        raise ValueError("KIE API 키가 없습니다. [설정]에서 KIE 키를 저장하세요.")
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
    if steps.get("prompts", True) and req.get("reuse_prompts") and os.path.exists(existing_prompts):
        job.add(f"   이미지 프롬프트가 이미 있어 재사용: {existing_prompts}")
        steps = dict(steps, prompts=False)
    if steps.get("prompts", True):
        job.stage = "② 이미지 프롬프트"
        ip = make_image_prompts(job, dict(script_file=script, guideline=req.get("img_guideline"), style=req.get("style", "실사"),
                                          chunk=req.get("chunk", 30)))
        result["prompts"] = ip["file"]
    else:
        cand = os.path.join(assets, "이미지프롬프트.txt") if os.path.basename(script) == "final.txt" else re.sub(r"\.txt$", "", script) + "_이미지프롬프트.txt"
        result["prompts"] = cand if os.path.exists(cand) else ""
    check_cancelled()
    # 3) 나레이션
    if steps.get("tts", True):
        job.stage = "③ 나레이션"
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
        selected_style = req.get("style", "실사")
        prefix = req.get("style_prefix") or image_style_lock(selected_style)
        run_image_generation(job, result["prompts"], images_dir, prefix)
        result["images"] = images_dir
    check_cancelled()
    # 5) 후킹 영상
    n_hook = int(steps.get("hook", 0) or 0)
    if n_hook > 0 and result.get("prompts"):
        job.stage = "⑤ 후킹 영상"
        result["hook"] = run_hook_videos(job, images_dir, result["prompts"], list(range(1, n_hook + 1)))
    check_cancelled()
    # 5.5) 썸네일
    if steps.get("thumbnail", True):
        try:
            job.stage = "⑤' 썸네일"
            t = make_thumbnails(job, dict(script_file=script, style=req.get("style", "실사"), position=req.get("thumb_position", "bottom")))
            result["thumbnails"] = t["thumbnails"]
        except Exception as e:  # noqa: BLE001
            job.add(f"   ! 썸네일 실패(넘어감): {e}")
    # 6) 최종 렌더
    if steps.get("render", False) and result.get("srt") and os.path.isdir(images_dir):
        job.stage = "⑥ 최종 렌더"
        out = os.path.join(assets, "최종.mp4")
        run_render(job, result["srt"], result["flow"], images_dir, result["narration"], out)
        result["video"] = out
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
            previous = item.get("result") or {}
            if previous.get("script") and os.path.isfile(previous["script"]):
                request.update(script_file=previous["script"], reuse_prompts=True)
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
                telegram_notice(f"✅ 영상 한 편이 완성됐습니다.\n주제: {item['title']}\n길이: {duration / 60:.1f}분\n저장: {result.get('video') or result.get('assets', '')}")
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                shared = is_shared_failure(message)
                with QUEUE.lock:
                    cancelled = QUEUE.data.get("status") == "cancelled"
                    item.update(status="cancelled" if cancelled else ("pending" if shared else "error"),
                                stage="취소됨" if cancelled else ("설정 확인 후 다시 대기" if shared else "실패"),
                                error=message, progress=1.0 if cancelled or not shared else 0.0)
                    QUEUE.data["current_id"] = ""
                    if shared:
                        QUEUE.data["status"] = "paused"
                    QUEUE.save()
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
    with LOCK:
        if STATE["job"] and STATE["job"].status == "running":
            raise ValueError("현재 작업이 끝난 뒤 연속 제작을 시작하세요.")
    with QUEUE.lock:
        if QUEUE.data.get("status") == "running":
            raise ValueError("이미 연속 제작이 진행 중입니다.")
        QUEUE.data = {"status": "running", "items": unique, "options": body.get("options") or {},
                      "current_id": "", "updated": ""}
        QUEUE.save()
    start_queue_worker()
    return queue_snapshot()


# ── HTTP ─────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

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
            if u.path == "/":
                data = PAGE.encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            elif u.path == "/api/state":
                cfg = 대본생성.load_cfg()
                job = STATE["job"]
                self._json(dict(topics=topics(), guidelines=guideline_files(), scripts=script_files(),
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
                                            분당_글자수=cfg.get("분당_글자수", 270), 화풍=cfg.get("화풍", "실사"),
                                            후킹_장면수=cfg.get("후킹_장면수", 7), 프롬프트_묶음=cfg.get("프롬프트_묶음", 30),
                                            텔레그램_토큰=mask(cfg.get("텔레그램_봇_토큰", "")),
                                            텔레그램_채팅_ID=str(cfg.get("텔레그램_채팅_ID", "")),
                                            텔레그램_알림=cfg.get("텔레그램_알림", True)),
                                web_alive=웹큐.extension_alive(), web_hidden=(웹큐._extension_seen["info"] == "hidden"),
                                lengths={k: v["이름"] for k, v in 민담_대본.길이.items() if str(k) != "0"}, styles=list(화풍), style_info=화풍_설명, style_groups=화풍_그룹,
                                style_prefixes={k: image_style_lock(k) for k in 화풍},
                                job=job.to_dict() if job else None, queue=queue_snapshot(), reset_items=reset_items()))
            elif u.path == "/api/job":
                job = STATE["job"]; self._json(job.to_dict() if job else {"status": "none"})
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
            elif u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=shutdown_program, args=(self.server,), daemon=True).start()
            elif u.path == "/api/reset":
                self._json(reset_output(body.get("id", ""), body.get("scope", "")))
            elif u.path == "/api/delete-script":
                self._json(delete_script(body.get("script_file", "")))
            elif u.path == "/api/thumbnail":
                run_job("thumbnail", lambda job: make_thumbnails(job, body)); self._json({"ok": True})
            elif u.path == "/api/optimize":
                run_job("optimize", lambda job: make_optimize_only(job, body)); self._json({"ok": True})
            elif u.path == "/api/timestamps":
                self._json(make_timestamps(body))
            elif u.path == "/api/images":
                run_job("images", lambda job: make_image_prompts(job, body)); self._json({"ok": True})
            elif u.path == "/api/restyle-2d":
                if STATE["job"] and STATE["job"].status == "running":
                    raise ValueError("진행 중인 작업을 마친 뒤 변환하세요.")
                self._json(restyle_script_prompts_2d(body.get("script_file", "")))
            elif u.path == "/api/guideline":
                write_guideline(body["name"], body["text"]); self._json({"ok": True})
            elif u.path == "/api/config":
                cfg = load_json("설정.json", {})
                for k in ("AI", "API_키", "모델", "대본_글자수", "인월드_API_키", "인월드_목소리", "인월드_모델", "인월드_속도",
                          "인월드_목소리_사람", "인월드_목소리_민담", "인월드_속도_사람", "인월드_속도_민담", "분당_글자수", "화풍", "후킹_장면수",
                          "API_키_deepseek", "API_키_gemini", "API_키_claude", "프롬프트_묶음",
                          "텔레그램_봇_토큰", "텔레그램_채팅_ID", "텔레그램_알림"):
                    if k in body and body[k] != "":
                        cfg[k] = str(body[k]).strip() if isinstance(body[k], str) else body[k]
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
                self._json({"ok": True})
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


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>대본 만들기 · 주제 선택</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=Noto+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
:root{--bg:#F4F1F5;--surface:#FFF;--ink:#241B27;--muted:#7A6E7E;--line:#E3DCE5;--accent:#6B2D5C;--accent-soft:#F1E4EE;--gold:#D9A33A;--warn:#A85A1F;--warn-soft:#FBEBD9;--box:#FAF7FB;--ok:#2F7A4A;
--serif:'Gowun Batang',serif;--sans:'Noto Sans KR',system-ui,sans-serif;--mono:'IBM Plex Mono',monospace}
@media (prefers-color-scheme:dark){:root{--bg:#17121A;--surface:#221B26;--ink:#EEE6EF;--muted:#A395A8;--line:#33293A;--accent:#D48CC2;--accent-soft:#33223A;--gold:#F0B850;--warn:#E9A466;--warn-soft:#3A2A18;--box:#1B151F;--ok:#7CC98F}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14.5px;line-height:1.6}
.wrap{max-width:1040px;margin:0 auto;padding:32px 22px 80px}
h1{font-family:var(--serif);font-size:30px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 18px}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.12em;color:var(--accent)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:14px}
h2{font-family:var(--serif);font-size:18px;margin:0 0 12px;padding-bottom:8px;border-bottom:2px solid var(--accent);display:flex;gap:10px;align-items:baseline}
h2 small{font-family:var(--sans);font-size:12.5px;font-weight:400;color:var(--muted)}
.tabs{display:flex;gap:6px;margin-bottom:14px}.tabs button{border:1px solid var(--line);background:var(--box);padding:8px 16px;border-radius:8px;font:600 14px var(--sans);color:var(--ink);cursor:pointer}
.tabs button.on{background:var(--accent);color:#fff;border-color:transparent}
.tabs.steps{gap:8px;margin-bottom:6px;flex-wrap:wrap}.tabs.steps button{flex:1 1 210px;min-width:210px;text-align:left;padding:10px 14px;line-height:1.25;display:flex;align-items:center;gap:10px;white-space:nowrap}
.tabs.tools{flex-wrap:wrap}
.tabs.steps .no{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font-weight:800;font-size:13px;flex:none}
.tabs.steps button.on .no{background:#fff;color:var(--accent)}.tabs.steps small{display:block;font-weight:400;font-size:11.5px;color:var(--muted)}.tabs.steps button.on small{color:#fff;opacity:.85}
.tabs.tools{margin-bottom:14px}.tabs.tools button{font-weight:500;font-size:13px;padding:5px 12px}
.ready{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}.ready button{border-radius:999px;padding:5px 12px;font-size:12.5px}.ready button.ok{border-color:var(--ok);color:var(--ok);cursor:default}.ready button.bad{border-color:var(--warn);color:var(--warn);background:var(--warn-soft)}
.stepline{display:flex;align-items:flex-start;gap:12px;margin:10px 0}.stepline .no{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;background:var(--accent);color:#fff;font-weight:800;flex:none;margin-top:2px}.stepline>div{flex:1}
.gonext{margin-top:12px;padding:10px 14px;background:var(--accent-soft);border-radius:10px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
label{font-size:12.5px;color:var(--muted)}
input[type=text],input[type=number],input[type=password],select,textarea{font:inherit;color:var(--ink);background:var(--box);border:1px solid var(--line);border-radius:7px;padding:7px 10px}
input[type=text]{flex:1;min-width:260px}textarea{width:100%;min-height:260px;font:12.5px/1.55 var(--mono)}
button{font:500 13.5px var(--sans);border:1px solid var(--line);background:transparent;color:var(--ink);padding:7px 13px;border-radius:7px;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}button.primary{background:var(--accent);color:#fff;border-color:transparent;font-weight:700;padding:11px 20px;font-size:15px}
button.primary:disabled{opacity:.5;cursor:not-allowed}button.mini{padding:2px 9px;font-size:12px}
.list{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:8px;max-height:380px;overflow:auto}
.list li{display:flex;gap:10px;align-items:center;padding:9px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.list li:last-child{border-bottom:0}.list li:hover{background:var(--box)}.list li.sel{background:var(--accent-soft);outline:2px solid var(--accent)}
.list .d{font-family:var(--mono);font-size:11px;color:var(--gold);width:88px;flex:none}.list .c{font-size:11.5px;color:var(--accent);font-weight:700;width:30px;flex:none}
.list .t{flex:1;font-family:var(--serif);font-size:15px}.list .done{font-size:11px;color:var(--ok);white-space:nowrap}
.hint{font-size:12.5px;color:var(--muted)}
.warn{font-size:13px;color:var(--warn);background:var(--warn-soft);padding:7px 11px;border-radius:6px;margin:8px 0;white-space:pre-wrap}
.bar{height:10px;border-radius:999px;background:var(--box);border:1px solid var(--line);overflow:hidden}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--gold));transition:width .4s}
.log{margin-top:10px;background:#0f0a12;color:#d7cfe0;border-radius:8px;padding:10px 12px;font:12.5px/1.6 var(--mono);max-height:60vh;min-height:160px;overflow:auto;white-space:pre-wrap}.log.tall{max-height:none}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:10px;margin-top:8px}
.gal .g{border-radius:9px;background:var(--box);border:1px solid var(--line);padding:6px;text-align:center;font-size:11.5px;color:var(--muted);display:flex;flex-direction:column;gap:5px;align-items:center}
.gal .g .no{font:700 11.5px var(--mono);color:var(--ink)}.gal .g .pic{width:100%;aspect-ratio:16/9;border-radius:6px;overflow:hidden;background:var(--surface);border:1px solid var(--line);display:grid;place-items:center;position:relative}
.gal .g .pic img{width:100%;height:100%;object-fit:cover;display:block;cursor:zoom-in}
.gal .g .st{font-size:11px;white-space:nowrap}.gal .g.done{border-color:var(--ok)}.gal .g.done .st{color:var(--ok)}
.gal .g.now{border-color:var(--gold);box-shadow:0 0 0 2px var(--gold)}.gal .g.now .st{color:var(--gold);font-weight:700}.gal .g.now .pic::after{content:'지금 차례';position:absolute;inset:auto 0 0 0;background:rgba(0,0,0,.6);color:#fde68a;font-size:11px;padding:2px}
.gal .g.fail{border-color:var(--warn)}.gal .g.fail .pic{background:var(--warn-soft);color:var(--warn);font-weight:700}.gal .g.fail .st{color:var(--warn)}
.gal .g .bt{display:flex;gap:4px;flex-wrap:wrap;justify-content:center}.gal .g .bt button{font-size:10.5px;padding:2px 7px;border-radius:5px}
.gal .g .bt .re{background:var(--accent);color:#fff;border-color:transparent}.gal .g .bt .vi{background:#0f766e;color:#fff;border-color:transparent}.gal .g .bt .ge{background:#1d4ed8;color:#fff;border-color:transparent}
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,.85);display:grid;place-items:center;z-index:30;cursor:zoom-out}.lightbox img{max-width:92vw;max-height:88vh;border-radius:8px}
.stepbar{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 10px}.stepbar span{font-size:12.5px;border:1px solid var(--line);border-radius:999px;padding:3px 10px;color:var(--muted)}
.stepbar span.done{border-color:var(--ok);color:var(--ok)}.stepbar span.has{cursor:pointer;text-decoration:underline dotted}.stepbar span.has:hover{background:var(--accent-soft)}.stepbar span.now{background:var(--accent);color:#fff;border-color:transparent;font-weight:700}
.result{margin-top:12px;padding:12px 14px;background:var(--accent-soft);border-radius:8px}
.result code{font-family:var(--mono);font-size:12px}
.preview{white-space:pre-wrap;font:14px/1.7 var(--serif);max-height:420px;overflow:auto;background:var(--box);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-top:10px}
details summary{cursor:pointer;color:var(--muted);font-size:13px}
.hidden{display:none!important}
.page-nav{position:sticky;top:0;z-index:20;display:flex;gap:7px;flex-wrap:wrap;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(10px);padding:10px 0 12px;margin-bottom:10px}.page-nav button{background:var(--surface);font-weight:700}.page-nav button.settings{margin-left:auto;background:var(--accent);color:#fff}
.settings-hidden{display:none!important}.main-section{scroll-margin-top:82px}
.xy-countdown{display:inline-flex;align-items:center;justify-content:center;min-width:250px;padding:9px 14px;border-radius:12px;background:var(--accent-soft);color:var(--accent);font-size:20px;font-weight:900;letter-spacing:3px}.xy-countdown.active{background:var(--accent);color:#fff;animation:pulse .8s infinite alternate}@keyframes pulse{to{transform:scale(1.03)}}
.view-switch{display:flex;align-items:center;gap:8px;margin-bottom:12px;padding:10px 13px;border:1px solid var(--line);border-radius:13px;background:var(--surface)}.view-switch button.on{background:var(--accent);color:#fff}.simple-mode .advanced-section:not(.focused-section),.simple-mode .advanced-nav{display:none}.beginner-note{margin-bottom:14px;padding:14px 18px;border-radius:14px;background:var(--accent-soft);color:var(--ink);line-height:1.65}.beginner-note b{color:var(--accent)}.advanced-options{margin:10px 0;border:1px solid var(--line);border-radius:12px;background:var(--box)}.advanced-options summary{padding:11px 14px;cursor:pointer;font-weight:700;color:var(--muted)}.advanced-options[open] summary{border-bottom:1px solid var(--line)}.advanced-options-body{padding:12px 14px}
.styles{display:flex;gap:8px;flex-wrap:wrap}.styles button{border:1px solid var(--line);background:var(--box);border-radius:9px;padding:8px 14px;font-weight:600;cursor:pointer}
.styles button.on{background:var(--accent);color:#fff;border-color:transparent}.styles button small{display:block;font-weight:400;font-size:11px;color:var(--muted)}.styles button.on small{color:#fff;opacity:.85}
.keyrow{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}.keyrow b{width:90px}.keyrow input{flex:1;min-width:240px;max-width:420px}
.keystat{font-family:var(--mono);font-size:12px;color:var(--muted);min-width:120px;white-space:nowrap}
.keyrow label.grow{flex:1 1 220px;display:flex;align-items:center;gap:6px;min-width:220px}.keyrow label.grow input{flex:1;min-width:0;max-width:none}
.keyrow .nowrap{display:inline-flex;align-items:center;gap:8px;white-space:nowrap;flex:none}.keyrow .nowrap label{display:inline-flex;align-items:center;gap:6px}.keystat.ok{color:var(--ok)}
.stylegrp{width:100%;margin-bottom:6px}.stylegrp-t{font-size:11.5px;color:var(--muted);margin:2px 0 4px;letter-spacing:.04em}
.lenbtns button{border:1px solid var(--line);background:var(--box);border-radius:6px;padding:3px 10px;font-size:12.5px;cursor:pointer;margin-right:3px}.lenbtns button.on{background:var(--accent);color:#fff;border-color:transparent}
.pill{font-family:var(--mono);font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
/* Calm studio theme */
:root{--bg:#f3f7f6;--surface:#fff;--ink:#162b31;--muted:#62757a;--line:#dce7e5;--accent:#087e79;--accent-soft:#e6f5f2;--gold:#c18429;--warn:#ad483e;--warn-soft:#fff0ed;--box:#f7faf9;--ok:#187c5e}
@media (prefers-color-scheme:dark){:root{--bg:#101b20;--surface:#19272b;--ink:#eaf4f2;--muted:#a3bbb9;--line:#34474a;--accent:#6ad7c9;--accent-soft:#1c3c39;--gold:#edba65;--warn:#f3a096;--warn-soft:#482b2b;--box:#152226;--ok:#7be2aa}}
body{background:radial-gradient(circle at 8% 0%,rgba(104,210,191,.18),transparent 31%),radial-gradient(circle at 96% 16%,rgba(226,187,108,.11),transparent 29%),var(--bg);letter-spacing:-.012em}
.wrap{max-width:1140px;padding:28px 24px 92px}
.hero{position:relative;overflow:hidden;background:linear-gradient(125deg,#102e37 0%,#124e50 56%,#0a7770 100%);color:#fff;border-radius:24px;padding:34px 38px 36px;margin-bottom:20px;box-shadow:0 18px 45px rgba(19,58,64,.2)}
.hero:after{content:'';position:absolute;width:350px;height:350px;border:1px solid rgba(255,255,255,.16);border-radius:50%;right:-70px;top:-210px;box-shadow:0 0 0 48px rgba(255,255,255,.04),0 0 0 100px rgba(255,255,255,.025);pointer-events:none}
.hero .eyebrow{color:#a9e9dc;font-weight:700;letter-spacing:.18em}.hero h1{font-size:clamp(28px,4vw,42px);line-height:1.25;letter-spacing:-.045em;margin:8px 0 10px}.hero .sub{color:#d1e7e3;margin:0;max-width:670px;font-size:14px}
.card{border-radius:18px;border-color:var(--line);padding:23px 26px;box-shadow:0 8px 30px rgba(21,55,60,.045)}
h2{font-family:var(--sans);font-size:20px;font-weight:800;letter-spacing:-.035em;border-bottom:1px solid var(--line);padding-bottom:13px;margin-bottom:17px}h2 small{font-size:12.5px;letter-spacing:0}
.tabs.steps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:13px}.tabs.steps button{min-width:0;border-radius:15px;background:var(--surface);border-color:var(--line);box-shadow:0 5px 18px rgba(21,55,60,.04);padding:13px 15px;text-align:left;transition:transform .18s,box-shadow .18s,border-color .18s}.tabs.steps button:hover{transform:translateY(-2px);box-shadow:0 9px 22px rgba(21,55,60,.1)}.tabs.steps button.on{background:var(--accent);color:#fff;box-shadow:0 9px 25px rgba(8,126,121,.22)}.tabs.steps .no{width:30px;height:30px;border-radius:9px}.tabs.steps button small{line-height:1.35;margin-top:3px}
.tabs.steps .tab-title{display:block;min-width:0;white-space:normal;line-height:1.3}.tabs.steps .tab-title small{display:block}
.tabs.tools{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:7px;margin-bottom:20px;align-items:center}.tabs.tools button{border-radius:9px;padding:8px 12px}.tabs.tools button.on{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.toolbox{margin:0 0 20px;border:1px solid var(--line);border-radius:13px;background:var(--surface)}.toolbox summary{padding:12px 16px;font-weight:700;cursor:pointer}.toolbox .tabs.tools{margin:0;border:0;border-top:1px solid var(--line);border-radius:0}.simple-guide{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}.simple-guide span{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:8px 13px;font-size:13px;color:var(--muted)}
button{transition:background .16s,border-color .16s,transform .16s,box-shadow .16s}button:hover:not(:disabled){transform:translateY(-1px)}button.primary{border-radius:10px;box-shadow:0 6px 17px rgba(8,126,121,.18)}button.primary:hover{background:#076a66;color:white}.danger{background:var(--warn-soft);color:var(--warn);border-color:rgba(173,72,62,.25);font-weight:700}.danger:hover{background:var(--warn);color:white;border-color:var(--warn)}
input[type=text],input[type=number],input[type=password],select,textarea{background:var(--surface);border-radius:10px;padding:9px 12px;outline:none}input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(8,126,121,.13)}
.stepline{gap:15px;padding:8px 0}.stepline .no{width:32px;height:32px;border-radius:10px}.gonext{padding:16px 18px;border:1px solid rgba(8,126,121,.15);border-radius:14px}.list{border-radius:12px}.list li{padding:12px 15px}.list li.sel{outline:0;box-shadow:inset 3px 0 var(--accent)}.ready button{padding:7px 12px}.log{border-radius:12px}.result{border-radius:12px}
@media(max-width:850px){.tabs.steps{grid-template-columns:repeat(2,minmax(0,1fr))}.wrap{padding:16px 14px 60px}.hero{padding:27px 25px;border-radius:18px}.card{padding:18px}}
@media(max-width:520px){.tabs.steps{grid-template-columns:1fr 1fr;gap:7px}.tabs.steps button{padding:10px;gap:7px;font-size:12px}.tabs.steps button small{display:none}.tabs.steps .no{width:24px;height:24px;font-size:11px}.hero{padding:24px 20px}.hero .sub{font-size:12px}.tabs.tools .hint{display:none}.tabs.tools button{flex:1}.keyrow input{min-width:0;width:100%}}
/* DINO 스타일의 한눈에 보는 제작 화면 */
:root{--bg:#090b20;--surface:#171a36;--ink:#f3f4ff;--muted:#a9aecb;--line:#34395d;--accent:#16d5ca;--accent-soft:#153d43;--gold:#ffbe55;--warn:#ff6874;--warn-soft:#43232d;--box:#11152d;--ok:#65e6ad}
body{background:linear-gradient(180deg,#090b20,#0c1027 55%,#090b20);font-size:14px}.wrap{max-width:980px;padding-top:18px}.hero{padding:22px 27px;border-radius:14px;margin-bottom:12px;background:linear-gradient(120deg,#173a47,#126e69);box-shadow:none}.hero h1{font-size:27px}.hero .sub{font-size:13px}.card{padding:18px 20px;border-radius:13px;background:var(--surface);box-shadow:none;margin-bottom:12px}h2{font-size:17px;padding-bottom:10px;margin-bottom:12px}.simple-guide{margin-bottom:9px}.simple-guide span{padding:5px 10px;background:#11152d}.view-switch{padding:7px 9px;margin-bottom:9px}.beginner-note{padding:10px 13px;margin-bottom:9px}.page-nav{padding:7px 0 9px;background:#090b20}.page-nav button{padding:7px 11px}.stepline{padding:6px 0}.styles{gap:6px}.styles button{min-height:43px;padding:7px 10px}.gonext{padding:11px 13px}.pipeline-overview{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-bottom:12px;padding:8px;border:1px solid var(--line);border-radius:12px;background:#0c1024}.pipeline-step{padding:11px 5px;border:1px solid var(--line);border-radius:9px;text-align:center;background:#080c18}.pipeline-step b{display:block;font-size:12px}.pipeline-step small{display:block;margin-top:4px;color:var(--muted)}.pipeline-step.now{border-color:var(--accent);background:#15353d}.pipeline-step.done{border-color:var(--ok);color:var(--ok)}#tab-auto textarea#a_title{width:min(100%,620px);min-height:66px;resize:vertical;font-size:16px;line-height:1.5}.simple-mode #tab-auto{border-color:#4a527c}.simple-mode #tab-auto h2{color:var(--accent)}#tab-settings{background:transparent;border:0;padding:0}#tab-settings>h2,#tab-settings>p,#tab-settings>.row,#tab-settings>.ready,#tab-settings>.keyrow,#tab-settings>details{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:12px 15px;margin:8px 0}#tab-settings>h2{margin-top:13px;color:var(--accent)}@media(max-width:760px){.pipeline-overview{grid-template-columns:repeat(3,1fr)}}
.work-editor textarea{width:100%;min-height:240px;font-family:var(--mono);font-size:13px;line-height:1.55}.work-editor .meta-field textarea{min-height:90px}.work-editor input[type=text]{flex:1;min-width:280px}.work-editor details{margin:8px 0;border:1px solid var(--line);border-radius:10px;background:var(--box)}.work-editor summary{padding:11px 13px;cursor:pointer;font-weight:800}.work-editor details>div{padding:0 13px 13px}.copyrow{display:flex;gap:7px;align-items:flex-start;margin:8px 0}.copyrow label{min-width:65px;padding-top:9px;font-weight:800}
.studio-sidebar{position:fixed;left:0;top:0;bottom:0;width:220px;z-index:40;background:#0f0f0f;border-right:1px solid #303030;padding:18px 12px;overflow-y:auto}.studio-brand{display:flex;align-items:center;gap:10px;padding:5px 10px 22px;font-size:17px;font-weight:900}.studio-logo{display:grid;place-items:center;width:34px;height:24px;border-radius:7px;background:#ff0033;color:#fff;font-size:13px}.studio-sidebar button{display:flex;width:100%;align-items:center;gap:12px;margin:3px 0;padding:11px 13px;border:0;border-radius:9px;text-align:left;background:transparent;color:#e5e5e5}.studio-sidebar button:hover,.studio-sidebar button.on{background:#272727;color:#fff}.studio-sidebar button.on{font-weight:800}.studio-sidebar .nav-icon{width:22px;text-align:center;font-size:17px}.studio-divider{height:1px;background:#303030;margin:13px 6px}.studio-sidebar small{display:block;padding:8px 13px;color:#888}.page-nav,.simple-guide,.view-switch,.beginner-note{display:none!important}.studio-panel-mode .main-section:not(.studio-selected){display:none!important}.wrap{max-width:1180px;width:calc(100% - 260px);margin:0 auto 0 240px;padding:18px 22px 80px}.hero{background:#212121;border:1px solid #343434;border-radius:12px;padding:19px 24px}.hero:after{display:none}.hero .eyebrow{color:#aaa;letter-spacing:.08em}.hero h1{font-size:25px}.card{background:#212121;border-color:#383838}.pipeline-overview{background:#181818;border-color:#383838}.pipeline-step{background:#121212;border-color:#383838}:root{--bg:#0f0f0f;--surface:#212121;--ink:#f1f1f1;--muted:#aaa;--line:#3f3f3f;--accent:#3ea6ff;--accent-soft:#263850;--box:#181818;--ok:#2ed39a;--warn:#ff6673;--warn-soft:#47242b}.simple-mode #tab-auto{border-color:#3f3f3f}.simple-mode #tab-auto h2,#tab-settings>h2{color:#f1f1f1}@media(max-width:820px){.studio-sidebar{position:sticky;top:0;width:auto;height:auto;display:flex;gap:4px;padding:7px;overflow-x:auto;border-right:0;border-bottom:1px solid #303030}.studio-brand,.studio-divider,.studio-sidebar small{display:none}.studio-sidebar button{width:auto;min-width:max-content;margin:0;padding:8px 11px}.wrap{width:auto;margin:0;padding:12px}.studio-sidebar .nav-label{font-size:12px}}
</style></head><body><div class="wrap">
<aside class="studio-sidebar">
  <div class="studio-brand"><span class="studio-logo">▶</span><span>사람의 이유 Studio</span></div>
  <button data-nav="auto" class="on" onclick="goTab('auto')"><span class="nav-icon">🏠</span><span class="nav-label">자동 제작</span></button>
  <button data-nav="person" onclick="goTab('person')"><span class="nav-icon">💡</span><span class="nav-label">주제 고르기</span></button>
  <button data-nav="work" onclick="goTab('work')"><span class="nav-icon">📝</span><span class="nav-label">완성 자료</span></button>
  <button data-nav="gallery" onclick="goTab('gallery')"><span class="nav-icon">🖼</span><span class="nav-label">이미지</span></button>
  <button data-nav="video" onclick="goTab('video')"><span class="nav-icon">🎬</span><span class="nav-label">영상·자막 설정</span></button>
  <div class="studio-divider"></div>
  <button data-nav="settings" onclick="goTab('settings')"><span class="nav-icon">⚙</span><span class="nav-label">설정</span></button>
  <small>필요한 메뉴만 눌러 작업하세요.</small>
</aside>
<header class="hero"><div class="eyebrow">CREATOR STUDIO · 사람의 이유 / 민담·야담</div>
<div style="float:right;display:flex;gap:8px"><button class="danger" onclick="goTab('reset')">🗑 삭제·초기화</button><button onclick="exitProgram()">■ 프로그램 종료</button></div>
<h1>이야기를 영상으로 만드는 공간</h1>
<p class="sub">주제 선택부터 대본, 나레이션, 이미지와 영상까지. 필요한 단계를 차례로 진행하세요.</p></header>
<div id="envwarn" class="warn hidden"></div>

<div class="simple-guide"><span>① 여러 주제 선택</span><span>② 연속 제작</span><span>③ 이미지 확인</span><span>④ 영상 확인</span><span>⑤ 완료 작업 정리</span></div>
<div class="view-switch"><b>화면 선택</b><button id="simple_mode_btn" onclick="setViewMode('simple')">초보자 간단 화면</button><button id="detail_mode_btn" onclick="setViewMode('detail')">상세 기능 모두 보기</button><span class="hint">처음에는 간단 화면을 권장합니다.</span></div>
<div class="beginner-note" id="beginner_note"><b>처음 사용 순서:</b> 아래에서 채널과 주제, 그림체를 고른 뒤 <b>대본부터 영상까지 자동 실행</b>만 누르세요. 이미지 수정이나 민담 세부 설정은 위 메뉴에서 필요한 항목만 열면 됩니다.</div>
<div class="page-nav"><button onclick="goTab('person')">주제 고르기</button><button onclick="goTab('auto')">자동 제작</button><button class="advanced-nav" onclick="goTab('gallery')">이미지 수정</button><button class="advanced-nav" onclick="goTab('video')">영상 편집</button><button class="settings" onclick="goTab('settings')">⚙ 설정</button></div>
<div class="pipeline-overview" id="pipeline_overview">
  <div class="pipeline-step" data-stage="script"><b>① 대본</b><small>대기</small></div><div class="pipeline-step" data-stage="prompt"><b>② 프롬프트</b><small>대기</small></div><div class="pipeline-step" data-stage="image"><b>③ 이미지</b><small>대기</small></div><div class="pipeline-step" data-stage="motion"><b>④ 영상변환</b><small>대기</small></div><div class="pipeline-step" data-stage="audio"><b>⑤ 음성/SRT</b><small>대기</small></div><div class="pipeline-step" data-stage="render"><b>⑥ 최종편집</b><small>대기</small></div>
</div>

<!-- 원클릭 -->
<div class="card main-section" id="tab-auto">
  <h2>③ 만들기 <small>주제 하나 → 대본 → 나레이션 → 이미지 → 완성 영상까지 한 번에</small></h2>
  <div id="readyBox" class="ready"></div>
  <div class="stepline"><span class="no">1</span><div><b>어느 채널?</b>
  <div class="radios" style="margin-top:6px">
    <label><input type="radio" name="a_channel" value="person" checked>사람의 이유<small>20~30분 · 심리·관계 이야기</small></label>
    <label><input type="radio" name="a_channel" value="mindam">민담·야담·옛이야기<small>1~2시간 · 옛이야기</small></label>
  </div></div></div>
  <div class="stepline"><span class="no">2</span><div><b>주제</b> <span class="hint">직접 적거나, 위 [2 주제 고르기]에서 클릭하면 여기로 들어옵니다</span>
  <div class="row" style="margin-top:6px"><textarea id="a_title" rows="2" placeholder="예) 나이 들수록 친구가 줄어드는 진짜 이유"></textarea><button class="primary" onclick="pickAnotherTopic()">🎲 다른 주제</button><button class="mini" onclick="goTab(pipelineTopic()==='mindam'?'mindam':'person')">목록 열기</button></div></div></div>
  <div class="stepline"><span class="no">3</span><div><b>그림체 선택</b> <span class="hint">애니·파스텔·실사 중 원하는 그림체를 누르면 바로 저장됩니다</span>
  <div class="row" style="margin-top:6px"><input type="hidden" id="a_style"><div class="styles" id="a_styles"></div></div>
  <div class="row"><label>민담 영상 길이 <select id="a_length"></select></label></div>
  <div class="row" style="margin-top:8px"><b>썸네일</b><label><input type="checkbox" id="a_thumb" checked> 영상마다 썸네일 3장 자동 제작</label><input type="hidden" id="a_thumb_pos" value="auto"><span class="hint">사람의 이유는 오른쪽 인물+왼쪽 문구, 민담은 이야기 장면+아래쪽 문구로 자동 구분합니다.</span></div>
  </div></div>
  <div class="stepline"><span class="no">4</span><div>
  <div class="row"><button class="primary" id="a_go" onclick="startPipeline()" style="font-size:17px;padding:14px 26px">🚀 대본부터 영상까지 자동 실행</button><button class="mini hidden" id="a_cancel" onclick="api('/api/cancel',{})">현재 작업 취소</button>
  <span class="hint">끝나면 아래 진행 칸에 결과 폴더가 나옵니다 (사람의 이유 30~60분 · 민담 1~2시간)</span></div></div></div>
  <details class="advanced-options"><summary>이미 만든 대본 이어서 만들기</summary><div class="advanced-options-body"><div class="gonext"><b>이미 만든 대본이 있으면 →</b>
    <select id="c_file" style="min-width:380px"></select>
    <button class="primary" onclick="continuePipeline($('c_file').value)">🎬 이 대본으로 나레이션 → 이미지 → 영상까지 이어서 만들기</button>
    <span class="hint">이미 있는 나레이션·이미지 프롬프트·그림은 건너뛰고 없는 것부터 만듭니다</span></div></div></details>
</div>

<div class="card main-section work-editor" id="workCard">
  <h2>📝 완성 자료 확인·수정 <small>대본과 프롬프트가 작성되면 여기에서 바로 보입니다</small></h2>
  <div class="row"><label>작업 선택 <select id="work_file" style="min-width:390px" onchange="loadWorkspace(true)"></select></label><button onclick="loadWorkspace(true)">새로고침</button><button onclick="goTab('gallery')">생성 이미지 보기</button></div>
  <div id="work_empty" class="hint">완성된 대본을 선택하면 편집 도구가 나타납니다.</div>
  <div id="work_body" class="hidden">
    <details open><summary>유튜브 썸네일 보기</summary><div><p class="hint">드롭샷 화면의 그림은 글씨 없는 원본입니다. 3장 다운로드가 끝나면 프로그램이 글씨를 합성하며, 아래에 표시되는 이미지가 유튜브용 최종 썸네일입니다.</p><div id="work_thumbnails" class="gal"></div><div class="row"><button class="primary" onclick="makeWorkspaceThumbnails()">🖼 썸네일 3장 만들기·다시 만들기</button><button onclick="openWorkspaceThumbnailFolder()">📁 썸네일 폴더 열기</button><span class="hint">그림을 클릭하면 크게 볼 수 있습니다.</span></div></div></details>
    <details open><summary>대본 보기·수정</summary><div><textarea id="work_script"></textarea><div class="row"><button class="primary" onclick="saveWorkspaceText('script')">대본 저장</button><button onclick="copyField('work_script')">대본 복사</button><button onclick="rerunTTS()">🎙 수정한 대본으로 TTS 다시 만들기</button></div></div></details>
    <details><summary>이미지 프롬프트 보기·수정</summary><div><textarea id="work_prompts"></textarea><div class="row"><button class="primary" onclick="saveWorkspaceText('prompts')">프롬프트 저장</button><button onclick="copyField('work_prompts')">프롬프트 복사</button></div></div></details>
    <details><summary>TTS 자막 보기·수정</summary><div><textarea id="work_srt" placeholder="TTS가 완성되면 SRT 자막이 표시됩니다."></textarea><div class="row"><button class="primary" onclick="saveWorkspaceText('srt')">자막 저장</button><button onclick="copyField('work_srt')">자막 복사</button><button onclick="openPath(WORK.narration)">TTS 파일 열기</button></div></div></details>
    <details open><summary>유튜브 제목·설명·출처·태그 복사/붙여넣기</summary><div>
      <div class="copyrow"><label>제목</label><input type="text" id="work_title"><button onclick="copyField('work_title')">복사</button></div>
      <div class="copyrow meta-field"><label>설명</label><textarea id="work_description"></textarea><button onclick="copyField('work_description')">복사</button></div>
      <div class="copyrow meta-field"><label>출처</label><textarea id="work_sources"></textarea><button onclick="copyField('work_sources')">복사</button></div>
      <div class="copyrow meta-field"><label>태그</label><textarea id="work_tags"></textarea><button onclick="copyField('work_tags')">복사</button></div>
      <div class="row"><button class="primary" onclick="saveWorkspaceMeta()">제목·설명·출처·태그 저장</button><span class="hint">각 칸에 직접 붙여넣어 수정한 뒤 저장할 수 있습니다.</span></div>
    </div></details>
  </div>
</div>

<div class="card main-section" id="queueCard">
  <h2>📚 연속 제작 대기열 <small>선택한 주제를 한 편씩 끝까지 만든 뒤 다음 주제로 넘어갑니다</small></h2>
  <div class="row"><button class="primary" onclick="startSelectedQueue()">▶ 선택한 주제 연속 제작</button><span class="hidden row" id="queue_manage"><button onclick="queueControl('pause')">일시정지</button><button onclick="queueControl('resume')">계속</button><button class="danger" onclick="queueControl('cancel')">전체 중단</button></span><span id="queue_summary" class="hint">대기열 없음</span></div>
  <div id="queue_list" style="margin-top:10px"></div>
</div>

<div class="card main-section advanced-section" id="galleryCard">
  <h2>🖼 이미지 <small id="pg_imgs_t">장면별 생성 현황 — 위에서 고른 대본 기준</small></h2>
  <div class="row"><label>확인할 대본 <select id="g_file" style="min-width:380px"></select></label><span class="hint">그림 아래의 재생성을 누르면 해당 장면만 다시 만듭니다.</span></div>
  <div class="gonext" style="margin:12px 0"><b>🎬 앞 7장 KIE 영상화</b>
    <div class="row" style="margin-top:8px"><span id="g_kie_status" class="hint">KIE 키 확인 중…</span><button class="mini" onclick="goTab('settings')">API 키·좌표 설정 →</button></div>
    <div class="row"><button class="primary" onclick="startFirstSevenVideos()">▶ 앞 7장 KIE AI로 영상 변환</button><button onclick="cancelKieVideos()">■ 영상화 중단</button><span class="hint">이미지→영상 변환은 KIE AI만 사용 · 이미 만든 영상은 건너뜀 · KIE 크레딧 사용</span></div>
    <div id="g_kie_progress" role="status" aria-live="polite" style="font-weight:700;margin:10px 0">영상 파일 확인 중…</div>
    <div class="row"><span class="hint" id="g_kie_scenes">1~7번 영상 확인 중…</span><button class="mini" onclick="refreshKieFiles()">영상 상태 새로고침</button><button class="mini" onclick="openPath(galDir)">영상 저장 폴더 열기</button></div>
  </div>
  <div class="row" style="gap:8px"><button class="primary" onclick="galStart(false)">▶ 빠진 장면 이어서 만들기</button><button onclick="galCtl('pause')">❚❚ 일시정지</button><button onclick="galCtl('resume')">▶ 재개</button><button onclick="galCtl('stop')" style="border-color:var(--warn);color:var(--warn)">■ 중단</button><button onclick="galStart(true)" style="border-color:var(--warn);color:var(--warn)">🔄 처음부터 다시 만들기</button>
    <span class="hint" id="gal_ctl"></span></div>
  <div class="row"><span class="hint" id="gal_src"></span><button class="mini" onclick="refreshGallery(true)">새로고침</button><button class="mini" onclick="openPath(galDir)" id="gal_open">이미지 폴더 열기</button></div>
  <div class="gal" id="pg_gal"></div>
  <p class="hint" style="margin-top:8px">완료 = 그림 클릭하면 크게 보기 · <b>재생성</b> = 그 장면만 다시 (기존 그림은 이전/ 폴더로) · <b>영상화</b> = KIE로 그 장면을 영상으로 · <b>생성/재시도</b> = 없는 장면 하나만</p>
  <details style="margin-top:8px"><summary class="hint">세부 옵션 (보통은 안 건드려도 됩니다)</summary>
  <div class="row" style="margin-top:8px">
    <label>사람의 이유 길이 <span class="lenbtns" data-for="a_target"></span> <input type="number" id="a_target" value="7000" step="500" style="width:90px"> 자</label>
    <label><input type="checkbox" id="a_optimize" checked>알고리즘 최적화</label>
    <label><input type="checkbox" id="a_prompts" checked>이미지 프롬프트</label>
    <label><input type="checkbox" id="a_tts" checked>나레이션(인월드)</label>
    <label><input type="checkbox" id="a_images" checked>이미지 자동 생성(좌표 클릭)</label>
    <label>후킹 영상 앞 <input type="number" id="a_hook" value="7" min="0" max="30" style="width:70px" onchange="api('/api/config',{후킹_장면수:+this.value})"> 장면 (KIE, 0=안 함)</label>
    <label><input type="checkbox" id="a_render" checked>최종 MP4 편집·저장(자막·음성 합치기)</label>
  </div>
  <p class="hint">자막 글꼴·색·화면 비율은 [🎬 영상 만들기] 탭에서 정한 것을 그대로 씁니다. 이미지 생성 중에는 마우스·키보드를 쓰지 마세요.</p>
  </details>
  <div class="row" style="margin-top:6px">
    <span class="hint">결과: 사람의 이유 → <code>대본/날짜_제목_자료/</code> · 민담 → <code>대본/민담/날짜_제목/</code> (images/, 나레이션.mp3, 나레이션.srt, 플로우.txt, 이미지프롬프트.txt)</span></div>
</div>

<!-- 사람의 이유 -->
<div class="card main-section advanced-section" id="tab-person">
  <h2>① 주제 고르기 <small>계획.json(이번 주 14편) + 후보 · 또는 직접 입력</small></h2>
  <div class="row"><button class="mini" onclick="selectAllQueue('person',true)">모두 선택</button><button class="mini" onclick="selectAllQueue('person',false)">선택 해제</button><span class="hint">체크한 주제는 아래 연속 제작 버튼으로 차례대로 만듭니다.</span></div>
  <ul class="list" id="topicList"></ul>
  <div class="row" style="margin-top:10px"><input type="text" id="p_title" placeholder="직접 입력: 예) 나이 들수록 친구가 줄어드는 진짜 이유"><button class="mini" onclick="selectTopic(null)">목록 선택 해제</button></div>
  <h2 style="margin-top:18px">② 지침·분량</h2>
  <div class="row">
    <label>대본 지침 <select id="p_guide"></select></label>
    <label>영상 길이 <span class="lenbtns" data-for="p_target"></span> <input type="number" id="p_target" value="7000" min="200" max="40000" step="10" style="width:90px"> 자</label>
    <label><input type="checkbox" id="p_used" checked> 사용한_주제.txt 에 기록</label>
    <label><input type="checkbox" id="p_opt" checked> 알고리즘 최적화(제목 5개·썸네일·설명·태그·첫30초 점검)</label>
    <button class="mini" onclick="editGuide('p_guide')">지침 열어 수정</button>
  </div>
  <div class="gonext"><b>고른 주제:</b> <span id="p_chosen" class="hint">(아직 없음)</span>
    <button class="primary" onclick="sendToAuto('person')">③ 만들기로 보내기 →</button>
    <button id="p_go" onclick="startPerson()">대본만 만들기</button><span class="hint">대본만: 2~5분</span></div>
</div>

<!-- 민담 -->
<div class="card main-section advanced-section" id="tab-mindam">
  <h2>① 주제 <small>민담_주제뽑기.bat 으로 모은 "터진 제목"을 고르거나 제목·장르를 직접 입력. 고른 제목은 슬롯 분해 → 알맹이 교체로 재창조</small></h2>
  <div class="row"><button class="mini" onclick="selectAllQueue('mindam',true)">모두 선택</button><button class="mini" onclick="selectAllQueue('mindam',false)">선택 해제</button><span class="hint">사람의 이유와 민담을 함께 선택해도 선택 순서대로 제작됩니다.</span></div>
  <ul class="list" id="mindamList" style="margin-bottom:10px"></ul>
  <div class="row"><input type="text" id="m_title" placeholder="예) 장터에서 아기를 백 냥에 사온 과부, 그 아이의 정체는  /  또는  권선징악  /  귀신·도깨비"><button class="mini" onclick="selectBench(null)">목록 선택 해제</button></div>
  <p class="hint" id="m_benchInfo"></p>
  <div class="row"><span class="hint">장르만 쓸 때:</span>
    <button class="mini" onclick="$('m_title').value='권선징악'">권선징악</button><button class="mini" onclick="$('m_title').value='귀신·도깨비'">귀신·도깨비</button><button class="mini" onclick="$('m_title').value='해학·풍자'">해학·풍자</button><button class="mini" onclick="$('m_title').value='사랑·비극'">사랑·비극</button><button class="mini" onclick="$('m_title').value='역사인물'">역사인물</button><button class="mini" onclick="$('m_title').value='미스터리·추리'">미스터리·추리</button><button class="mini" onclick="$('m_title').value='가족·성장'">가족·성장</button></div>
  <h2 style="margin-top:18px">② 길이·지침</h2>
  <div class="row">
    <label>영상 길이 <select id="m_length"></select></label>
    <label>지침 파일 <select id="m_guide"></select></label>
    <button class="mini" onclick="editGuide('m_guide')">지침 열어 수정</button>
    <label><input type="checkbox" id="m_used" checked> 민담_사용한_주제.txt 에 기록</label>
    <label><input type="checkbox" id="m_opt" checked> 알고리즘 최적화</label>
  </div>
  <p class="hint">흐름: 기획(제목·인트로·인물·반전 6개·팩트시트·챕터 계획) → 챕터별 본문 → 합본·검수. 결과는 <code>대본/민담/날짜_제목/</code> 에 final.txt · 기획.txt · story_facts.md · thumbnail_brief.md 로 저장. 도중에 끊기면 같은 제목으로 다시 눌러 이어서 씁니다.</p>
  <div class="row"><label>이어쓰기 폴더(선택) <input type="text" id="m_resume" placeholder="대본\민담\2026-09-12_제목  (비우면 새로 시작)" style="min-width:380px"></label></div>
  <h2 style="margin-top:18px">③ 재창조 <small>제목 골격은 살리고 알맹이(주인공·관계·반전)를 바꾼 A~D 중 하나를 고릅니다 (v11.3 1-B)</small></h2>
  <div class="row"><button id="v_go" onclick="startVariations()">베리에이션 A~D 만들기 (30초~1분)</button><span class="hint">장르만 넣었거나 바로 쓰려면 건너뛰고 아래 버튼 — 그래도 기획 단계에서 알맹이를 바꿔 재창조합니다</span></div>
  <div id="varBox" class="hidden"></div>
  <div class="gonext"><b>고른 제목:</b> <span id="m_chosen" class="hint">(아직 없음)</span>
    <button class="primary" onclick="sendToAuto('mindam')">③ 만들기로 보내기 →</button>
    <button id="m_go" onclick="startMindam()">대본만 만들기</button><span class="hint" id="m_hint">1시간 30분 기준 API 호출 12회, 10~20분</span></div>
</div>

<!-- 이미지 프롬프트 -->
<div class="card main-section advanced-section" id="tab-images">
  <h2>대본 → 문장별 이미지 프롬프트 <small>DINO 형식 ===001=== 블록</small></h2>
  <div class="row"><label>대본 파일 <select id="i_file" style="min-width:420px"></select></label><button class="mini" onclick="refresh()">새로고침</button></div>
  <div class="row"><label>변환 지침 <select id="i_guide"></select></label><label>화풍 (클릭)</label><input type="hidden" id="i_style"><div class="styles" id="i_styles"></div><label>한 번에 <input type="number" id="i_chunk" value="30" min="5" max="60" style="width:70px" onchange="api('/api/config',{프롬프트_묶음:+this.value});toast('한 번에 '+this.value+'문장씩 저장됨')"> 문장</label><button class="mini" onclick="editGuide('i_guide')">지침 열어 수정</button></div>
  <p class="hint">문장은 마침표 기준으로 나눕니다(대본 1문장 = 이미지 1장). 결과는 대본 옆에 <code>…_이미지프롬프트.txt</code> 와 Auto-Image Placer 용 <code>…_플로우.txt</code> 로 저장됩니다.</p>
  <div class="row" style="margin-top:14px"><button class="primary" id="i_go" onclick="startImages()">▶ 이미지 프롬프트 만들기</button></div>
  <div class="row"><button onclick="restyleTo2D()">기존 파스텔 프롬프트를 2D·레퍼런스로 바꾸기</button><span class="hint">원본은 대본 자료 폴더에 백업됩니다.</span></div>
  <p class="hint">레퍼런스는 드롭샷 생성창의 References에서 첨부 상태를 확인하세요. 인물 일관성이 목적이면 Subject로 지정하세요. 프롬프트 문구만으로는 이미지 첨부가 유지되지 않습니다.</p>
  <h2 style="margin-top:18px">알고리즘 최적화 · 챕터 타임스탬프 <small>이미 만든 대본에 따로 실행</small></h2>
  <div class="row"><button onclick="startThumb()">🖼 썸네일 3장 만들기 (위에서 고른 대본으로)</button><span class="hint">최적화 파일의 썸네일 문구 3세트 → 이미지 3장 생성(좌표 클릭) → 문구 합성 → 자료폴더/썸네일/썸네일_1~3.jpg</span></div>
  <div class="row"><button onclick="startOptimize()">▶ 위에서 고른 대본 파일 알고리즘 최적화만 실행</button><span class="hint">제목 후보 5개(점수) · 썸네일 문구 3세트 · 설명글 · 태그 · 고정댓글 · 첫 30초 점검 · 업로드 시각 → *_유튜브최적화.txt</span></div>
  <div class="row"><label>민담 대본 폴더 <input type="text" id="ts_dir" placeholder="대본\민담\2026-09-12_제목" style="min-width:320px"></label>
    <label>SRT <input type="text" id="ts_srt" placeholder="TTS 뒤 만든 자막 .srt 경로" style="min-width:320px"></label>
    <button onclick="makeTimestamps()">챕터 타임스탬프 만들기</button></div>
  <pre id="ts_out" class="hint" style="white-space:pre-wrap"></pre>
</div>

<!-- 영상 만들기 (편집프로그램 8765 를 안에 띄움) -->
<div class="card main-section advanced-section" id="tab-video" style="padding:0;overflow:hidden">
  <div id="video_source_status" class="gonext" style="margin:12px">아래에서 자막 글꼴·크기·굵기·색상과 영상 설정을 바로 바꿀 수 있습니다.</div>
  <div class="row" style="margin:0 12px 12px"><button class="primary" onclick="prepareVideoEditor()">🎬 선택한 대본 연결·새로고침</button><span class="hint">자막 설정은 대본이 없어도 저장할 수 있습니다.</span></div>
  <iframe id="fr_video" src="about:blank" data-src="http://127.0.0.1:8765/" style="width:100%;height:950px;border:0;background:#fff"></iframe>
</div>
<div class="card main-section" id="tab-reset">
  <h2>🗑 작업 전체 삭제 및 초기화 <small>오른쪽 상단 버튼으로만 들어오는 정리 화면</small></h2>
  <p class="hint">선택한 작업의 대본과 저장 폴더에 있는 이미지·KIE 영상·최종 영상·음성·자막·썸네일까지 모두 <code>대본/_휴지통</code>으로 옮깁니다. 다른 작업과 프로그램 설정은 유지됩니다.</p>
  <div class="row"><label>초기화할 작업 <select id="reset_file" style="min-width:min(100%,520px)"></select></label><button class="mini" onclick="refresh()">목록 새로고침</button></div>
  <div class="row"><button class="danger" onclick="resetSelected('all')">🗑 선택한 작업 전체 초기화</button></div>
  <p class="hint">실행 중인 작업은 중단한 뒤 초기화할 수 있습니다. 삭제 파일은 바로 지우지 않고 _휴지통에 보관합니다.</p>
</div>

<!-- 설정 -->
<div class="card tab hidden" id="tab-settings">
  <div class="row" style="justify-content:space-between"><h2 style="flex:1">⚙ 설정</h2><button class="primary" onclick="goTab('auto')">← 제작 화면으로 돌아가기</button></div>
  <h2>처음 사용 순서</h2>
  <p class="hint">① API 키를 저장하세요. ② 드롭샷 입력·생성·다운로드 좌표를 확인하세요. ③ [영상 만들기]에서 주제를 입력하면 됩니다.</p>
  <div id="setupReady" class="ready"></div>
  <h2 style="margin-top:14px">텔레그램 완성 알림</h2>
  <p class="hint">① BotFather에서 받은 봇 토큰을 저장합니다. ② 텔레그램에서 그 봇에게 아무 메시지나 보냅니다. ③ 채팅 자동 찾기 → 선택 저장 → 테스트를 누릅니다.</p>
  <div class="keyrow"><b>봇 토큰</b><span class="keystat" id="tg_token_status"></span><input type="password" id="tg_token" placeholder="123456:ABC…"><button class="mini" onclick="saveTelegramToken()">토큰 저장</button></div>
  <div class="row"><button onclick="findTelegramChats()">🔎 채팅 자동 찾기</button><select id="tg_chats" style="min-width:260px"><option value="">먼저 채팅을 찾으세요</option></select><button onclick="saveTelegramChat()">선택한 채팅 저장</button><button class="primary" onclick="testTelegram()">테스트 메시지</button><label><input type="checkbox" id="tg_enabled" onchange="saveTelegramEnabled()"> 알림 사용</label><span id="tg_chat_status" class="hint"></span></div>
  <div class="row"><button class="primary" onclick="goTab('person')">사람의 이유 주제 고르기 →</button><button onclick="goTab('mindam')">민담·야담 주제 고르기 →</button></div>
  <h2>대본 AI 선택</h2>
  <div class="row"><label>대본 쓰는 AI <select id="s_ai" onchange="saveAI()"><option value="deepseek-web">deepseek-web (웹 채팅 · 무료 · 확장 필요)</option><option>deepseek</option><option>gemini</option><option>claude</option></select></label>
    <label>모델 <input type="text" id="s_model" placeholder="비우면 기본" style="min-width:160px" onchange="saveAI()"></label></div>
  <p class="hint" id="s_status"></p>
  <h2 style="margin-top:14px">API 키 <small>한 번 저장하면 다시 안 넣어도 됩니다 · 바꿀 때만 새 키 입력 → 저장</small></h2>
  <div class="keyrow"><b>딥시크</b><span class="keystat" id="k_deepseek"></span><input type="password" id="key_deepseek" placeholder="sk-…  (platform.deepseek.com)"><button class="mini" onclick="saveKey('deepseek')">저장</button></div>
  <div class="keyrow"><b>제미나이</b><span class="keystat" id="k_gemini"></span><input type="password" id="key_gemini" placeholder="AIza…  (aistudio.google.com/apikey)"><button class="mini" onclick="saveKey('gemini')">저장</button></div>
  <div class="keyrow"><b>클로드</b><span class="keystat" id="k_claude"></span><input type="password" id="key_claude" placeholder="sk-ant-…  (console.anthropic.com)"><button class="mini" onclick="saveKey('claude')">저장</button></div>
  <div class="keyrow"><b>인월드 TTS</b><span class="keystat" id="k_inworld"></span><input type="password" id="key_inworld" placeholder="Basic 키  (platform.inworld.ai)"><button class="mini" onclick="saveKey('inworld')">저장</button></div>
  <div class="keyrow"><b>KIE 영상화</b><span class="keystat" id="s_kie_status">확인 중…</span><input type="password" id="s_kie_key" placeholder="KIE AI API 키" autocomplete="off"><button class="mini" onclick="saveKieKey()">저장</button></div>
  <div class="hint" id="s_web" style="margin:-4px 0 8px"></div>
  <details><summary class="hint">deepseek-web 쓰는 법 (API 비용 0원)</summary><ol class="hint">
    <li>크롬 주소창에 <code>chrome://extensions</code> → 오른쪽 위 <b>개발자 모드</b> 켜기 → <b>압축해제된 확장 프로그램을 로드</b> → 이 폴더의 <code>딥시크_확장</code> 선택</li>
    <li>크롬에서 <code>https://chat.deepseek.com</code> 을 열고 로그인 (탭을 닫지 않고 둡니다 — 최소화는 괜찮음)</li>
    <li>여기 AI 를 <b>deepseek-web</b> 으로 저장. 아래 상태가 "확장 연결됨"이면 끝. 대본을 만들면 그 탭에서 자동으로 새 대화 → 지침+요청 입력 → 답변 수집을 반복합니다.</li>
    <li>딥시크 웹은 한 번에 쓸 수 있는 답변 길이가 API 보다 짧을 수 있어, 글자수를 5,000자 단위로 나눠 요청합니다. 서버 혼잡 시 자동 재시도.</li></ol></details>
  <h2 style="margin-top:14px">드롭샷 좌표 설정 <small>프롬프트 입력창과 다운로드 버튼은 직접 지정 · 생성하기 버튼만 자동 탐색</small></h2>
  <div class="row"><button type="button" class="primary" onclick="window.open('https://aistudio.dropshot.io/ko/workspace/board', '_blank', 'noopener')">↗ 드롭샷 AI 열기</button></div>
  <div class="row"><b>좌표 잡기 순서</b><span class="xy-countdown" id="xy_countdown">6 → 5 → 4 → 3 → 2 → 1</span></div>
  <p class="hint">[6초 좌표]를 누르면 위 숫자가 6부터 1까지 줄어듭니다. 1이 끝날 때까지 드롭샷 창의 해당 위치에 마우스를 올려 두세요. 잡힌 좌표는 자동 저장됩니다.</p>
  <div class="row"><label>드롭샷 창 제목 <input type="text" id="s_window_keyword" value="드롭샷" style="width:150px"></label><button class="mini" onclick="saveEditorXY()">제목·좌표 저장</button><button class="mini" onclick="loadEditorSettings()">저장값 다시 읽기</button></div>
  <div class="row"><b style="min-width:130px">프롬프트 입력창</b><span class="pill">필수 좌표</span><label>X <input type="number" id="s_prompt_x" style="width:90px"></label><label>Y <input type="number" id="s_prompt_y" style="width:90px"></label><button class="mini primary" onclick="captureEditorXY('prompt')">6초 좌표 잡기</button><button class="mini" onclick="testEditorXY('prompt')">위치 확인</button></div>
  <div class="row"><b style="min-width:130px">생성하기 버튼</b><span class="pill">자동으로 찾음</span><label>예비 X <input type="number" id="s_generate_x" style="width:90px"></label><label>예비 Y <input type="number" id="s_generate_y" style="width:90px"></label><button class="mini primary" onclick="detectGenerateButton()">지금 자동 찾기</button><button class="mini" onclick="captureEditorXY('generate')">예비 좌표 잡기</button></div>
  <div class="row"><b style="min-width:130px">이미지 다운로드</b><span class="pill">필수 좌표</span><label>X <input type="number" id="s_download_x" style="width:90px"></label><label>Y <input type="number" id="s_download_y" style="width:90px"></label><button class="mini primary" onclick="captureEditorXY('download')">6초 좌표 잡기</button><button class="mini" onclick="testEditorXY('download')">위치 확인</button></div>
  <p class="hint" id="s_xy_status">저장된 좌표를 읽는 중…</p>
  <h2 style="margin-top:14px">인월드(Inworld) 목소리 <small>채널마다 다른 목소리로 저장됩니다</small></h2>
  <div class="row"><label>모델 <select id="s_inworld_model"><option>inworld-tts-1.5-max</option><option>inworld-tts-1-max</option><option>inworld-tts-1</option><option>inworld-tts-2</option><option>inworld-tts-2-flash</option></select></label></div>
  <div class="keyrow"><b>사람의 이유</b><span class="keystat" id="v_person"></span>
    <label class="grow">목소리 ID <input type="text" id="s_inworld_voice" placeholder="예) Sarah 또는 복제한 voice id"></label>
    <span class="nowrap"><label>속도 <input type="number" id="s_inworld_speed" value="0.9" step="0.05" min="0.5" max="1.5" style="width:70px"></label>
    <button class="mini" onclick="saveVoice('person')">저장</button></span></div>
  <div class="keyrow"><b>민담·야담</b><span class="keystat" id="v_mindam"></span>
    <label class="grow">목소리 ID <input type="text" id="s_inworld_voice_m" placeholder="옛이야기용 voice id (비우면 사람의 이유 목소리 사용)"></label>
    <span class="nowrap"><label>속도 <input type="number" id="s_inworld_speed_m" value="0.9" step="0.05" min="0.5" max="1.5" style="width:70px"></label>
    <button class="mini" onclick="saveVoice('mindam')">저장</button></span></div>
  <div class="keyrow"><b>분당 글자수</b><span class="keystat" id="v_cpm"></span><label>영상 길이 계산용 <input type="number" id="s_cpm" value="270" min="150" max="400" step="10" style="width:80px"></label>
    <button class="mini" onclick="saveConfig()">저장</button></div>
  <p class="hint" id="s_inworld_status">목소리 ID·속도는 줄마다 [저장]. 저장된 값은 그대로 남아 있으니 바꿀 때만 고치면 됩니다. (인월드 키는 위 API 키 칸)</p>
  <div class="row"><label>나레이션만 따로 만들기 — 이미지 프롬프트 탭에서 고른 대본 파일로</label><button onclick="startTTS()">▶ 나레이션 만들기</button></div>
</div>

<!-- 지침 편집 -->
<div class="card hidden" id="guideCard">
  <h2>지침 수정 <small id="g_name"></small></h2>
  <textarea id="g_text"></textarea>
  <div class="row" style="margin-top:8px"><button class="primary" onclick="saveGuide()">저장</button><button onclick="$('guideCard').classList.add('hidden')">닫기</button></div>
</div>

<!-- 진행 -->
<div class="card main-section hidden" id="progressCard">
  <h2>진행 <small id="pg_kind"></small></h2>
  <div class="stepbar" id="pg_steps"></div>
  <div class="bar"><i id="pg_bar"></i></div>
  <div class="row" style="justify-content:space-between;margin-top:6px"><span id="pg_stage" class="hint"></span><span class="hint"><label><input type="checkbox" id="pg_follow" checked> 새 로그 따라가기</label> · <span id="pg_lines"></span> · <button class="mini" onclick="$('pg_log').classList.toggle('tall')">로그 크게/작게</button></span></div>
  <div id="pg_web" class="hint hidden" style="margin-top:6px"></div>
  <div class="log" id="pg_log"></div>
  <div id="pg_result" class="result hidden"></div>
  <div id="pg_err" class="warn hidden"></div>
  <div id="pg_preview" class="preview hidden"></div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
let STATE=null, selected=null, bench=null, editing=null, pollTimer=null, chosenVar=null, varRef='', queueSelected=new Map(), WORK=null;
async function api(p,body,method){const r=await fetch(p,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||r.statusText);return j;}
async function exitProgram(){
  if(!confirm('이미지 생성과 다운로드를 중단하고 프로그램을 종료할까요?'))return;
  try{await api('/api/shutdown',{});document.body.innerHTML='<main style="max-width:560px;margin:12vh auto;padding:36px;font:18px/1.7 sans-serif;text-align:center"><h1>프로그램을 종료했습니다</h1><p>이미지 생성·다운로드를 중단하고 서버를 닫는 중입니다. 이 창은 닫아도 됩니다.</p></main>';}
  catch(e){toast('종료 요청 실패: '+e.message,true);}
}
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function setViewMode(mode){
  const simple=mode!=='detail';document.body.classList.toggle('simple-mode',simple);
  document.querySelectorAll('.advanced-section').forEach(x=>x.classList.remove('focused-section'));
  $('simple_mode_btn').classList.toggle('on',simple);$('detail_mode_btn').classList.toggle('on',!simple);
  $('beginner_note').classList.toggle('hidden',!simple);localStorage.setItem('creatorViewMode',simple?'simple':'detail');
}
function showMainScreen(){ $('tab-settings').classList.add('hidden');document.querySelectorAll('.main-section').forEach(x=>x.classList.remove('settings-hidden'));window.scrollTo({top:0,behavior:'smooth'}); }
function showSettings(){document.querySelectorAll('.main-section').forEach(x=>x.classList.add('settings-hidden'));$('tab-settings').classList.remove('hidden');loadEditorSettings();window.scrollTo({top:0,behavior:'smooth'});}
function markStudioNav(name){document.querySelectorAll('.studio-sidebar button[data-nav]').forEach(b=>b.classList.toggle('on',b.dataset.nav===(name==='mindam'?'person':name)));}
function goTab(name){
  markStudioNav(name);
  document.body.classList.add('studio-panel-mode');document.querySelectorAll('.main-section').forEach(x=>x.classList.remove('studio-selected'));
  if(name==='settings'){showSettings();return;}
  showMainScreen();const ids={auto:'tab-auto',person:'tab-person',mindam:'tab-mindam',work:'workCard',images:'tab-images',gallery:'galleryCard',video:'tab-video',reset:'tab-reset'};
  const target=$(ids[name]||'tab-auto');if(target){target.classList.add('studio-selected');if(name==='auto'){ $('queueCard').classList.add('studio-selected');$('progressCard').classList.add('studio-selected'); }document.querySelectorAll('.advanced-section').forEach(x=>x.classList.remove('focused-section'));if(document.body.classList.contains('simple-mode')&&target.classList.contains('advanced-section'))target.classList.add('focused-section');setTimeout(()=>target.scrollIntoView({behavior:'smooth',block:'start'}),50);}
  if(name==='gallery')refreshGallery(true);if(name==='video')prepareVideoEditor();
}
function sendToAuto(ch){const t=(ch==='mindam'?$('m_title'):$('p_title')).value.trim();if(!t)return toast('먼저 주제를 고르거나 입력하세요',true);document.querySelector(`input[name=a_channel][value=${ch}]`).checked=true;$('a_title').value=t;goTab('auto');toast('③ 만들기에 주제를 넣었습니다. 그림체를 확인하고 실행하세요');}
async function prepareVideoEditor(){
  const fr=$('fr_video'),status=$('video_source_status'),script=$('g_file').value||$('c_file').value;
  fr.src=fr.dataset.src+'?settings='+Date.now();
  if(!script){status.textContent='자막 글꼴·크기·굵기·색상을 설정할 수 있습니다. 완성 대본을 선택하면 이미지·음성·자막 경로도 자동 연결됩니다.';return;}
  status.textContent='선택한 대본의 나레이션·자막·이미지를 연결하는 중…';
  try{
    const a=await api('/api/assets?script='+encodeURIComponent(script));
    const scan=await post8765('/api/scan_folder',{path:a.assets});
    if(!scan.srt||!scan.narration||!scan.images)throw new Error('이 대본의 자막(SRT), 나레이션 또는 이미지가 없습니다. 먼저 제작을 완료하세요.');
    const media=await fetch('http://127.0.0.1:8765/api/gen/images?dir='+encodeURIComponent(scan.images)).then(r=>r.json());
    const kieCount=(media.images||[]).filter(x=>x.video).length;
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json());
    const ui={...((info.config||{}).ui||{}),easy:a.assets,srt:scan.srt,flow:scan.flow||'',images:scan.images,narration:scan.narration,
      subtitle_mov:scan.subtitle_mov||'',output:a.assets+'\\최종.mp4'};
    await post8765('/api/config',{ui});
    status.textContent='✓ 선택한 대본: '+script.split(/[\\/]/).pop()+' · 이미지 '+scan.image_count+'장 · KIE 영상 '+kieCount+'개 · 결과는 이 대본의 자료 폴더에 저장됩니다.';
    fr.src=fr.dataset.src+'?selected='+Date.now();
  }catch(e){status.textContent='편집 자료 연결 실패: '+e.message;toast(e.message,true);}
}
window.addEventListener('message',e=>{ if(e.data&&e.data.aipHeight){ const f=$('fr_video');if(f&&f.contentWindow===e.source)f.style.height=(e.data.aipHeight+40)+'px'; } });
async function checkReady(){
  const box=$('readyBox'); const items=[];   // [ok, 라벨, 고칠 탭]
  try{ const info=await api('/api/state');
    const v=info.config.인월드키있음&&(info.config.인월드_목소리_사람||info.config.인월드_목소리);
    items.push([!!v, v?'인월드 목소리 ✓':'인월드 키·목소리 없음', 'settings']);
    if(info.config.AI==='deepseek-web') items.push([!!info.web_alive, info.web_alive?'딥시크 웹 연결 ✓':'딥시크 창 안 보임 (chat.deepseek.com 열기)', 'settings']);
    else items.push([!!info.config.키있음, info.config.키있음?(info.config.AI+' 키 ✓'):(info.config.AI+' 키 없음'), 'settings']);
  }catch(e){}
  try{ const r=await fetch('http://127.0.0.1:8765/api/info'); const j=await r.json();const xy=((j.config||{}).gen_ui||{}).XY||{};const ok=!!(xy.prompt&&xy.download);
    items.push([ok, ok?'드롭샷 입력·다운로드 좌표 ✓':'드롭샷 필수 좌표 2개 없음', 'settings']); }
  catch(e){ items.push([false,'편집프로그램(8765) 꺼짐 — 유튜브_자동화_시작.bat 다시 실행', null]); }
  const markup='<span class="hint" style="align-self:center">준비 상태:</span>'+items.map(([ok,label,tab])=>`<button type="button" class="${ok?'ok':'bad'}" ${tab&&!ok?`onclick="goTab('${tab}')"`:''}>${ok?'🟢':'🔴'} ${esc(label)}${(!ok&&tab)?' → 고치기':''}</button>`).join('');
  box.innerHTML=markup; $('setupReady').innerHTML=markup;
}
setTimeout(checkReady,800); setInterval(checkReady,15000);

function fill(sel,items,val){sel.innerHTML=items.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');if(val&&items.includes(val))sel.value=val;}
async function refresh(){
  STATE=await api('/api/state');
  renderOverview(STATE.job);
  const g=STATE.guidelines;
  fill($('p_guide'),g.script,'사람의이유_대본지침.txt');
  fill($('m_guide'),g.mindam,'01_기획_지침.txt');
  fill($('i_guide'),g.image,'이미지프롬프트_변환지침(DeepSeek).txt');
  $('m_length').innerHTML=Object.entries(STATE.lengths).map(([k,v])=>`<option value="${k}" ${k==='2'?'selected':''}>${v}</option>`).join('');
  renderStyles(STATE.styles, STATE.config.화풍||'실사'); renderLenButtons(STATE.config.분당_글자수||270);
  $('a_length').innerHTML=Object.entries(STATE.lengths).map(([k,v])=>`<option value="${k}" ${k==='2'?'selected':''}>${v}</option>`).join('');
  $('s_inworld_voice').value=STATE.config.인월드_목소리_사람||''; $('s_inworld_model').value=STATE.config.인월드_모델||'inworld-tts-1.5-max'; $('s_inworld_speed').value=STATE.config.인월드_속도_사람||1.0;
  $('s_inworld_voice_m').value=STATE.config.인월드_목소리_민담||''; $('s_inworld_speed_m').value=STATE.config.인월드_속도_민담||1.0; $('s_cpm').value=STATE.config.분당_글자수||270;
  $('tg_token_status').textContent=STATE.config.텔레그램_토큰?'✔ 저장됨 '+STATE.config.텔레그램_토큰:'없음';
  $('tg_chat_status').textContent=STATE.config.텔레그램_채팅_ID?'연결된 채팅: '+STATE.config.텔레그램_채팅_ID:'연결된 채팅 없음';
  $('tg_enabled').checked=STATE.config.텔레그램_알림!==false;
  $('a_hook').value=STATE.config.후킹_장면수??7; $('i_chunk').value=STATE.config.프롬프트_묶음||30;
  const c=STATE.config; $('v_person').textContent=c.인월드_목소리_사람?'✔ 저장됨 · 속도 '+(c.인월드_속도_사람||1):'없음'; $('v_person').classList.toggle('ok',!!c.인월드_목소리_사람);
  $('v_mindam').textContent=c.인월드_목소리_민담?'✔ 저장됨 · 속도 '+(c.인월드_속도_민담||1):'없음 → 공용'; $('v_mindam').classList.toggle('ok',!!c.인월드_목소리_민담);
  $('v_cpm').textContent='✔ '+(c.분당_글자수||270)+'자/분'; $('v_cpm').classList.add('ok');
  for(const [k,v] of Object.entries(STATE.keys||{})){const el=$('k_'+k);if(!el)continue;el.textContent=v?'✔ 저장됨 '+v:'없음';el.classList.toggle('ok',!!v);}
  const selected=$('g_file').value||$('c_file').value||localStorage.getItem('selectedScript')||'';
  $('i_file').innerHTML=STATE.scripts.map(s=>`<option value="${esc(s.path)}">${esc(s.name)}</option>`).join('')||'<option value="">(대본 폴더에 파일 없음)</option>';
  const previousWork=$('work_file').value||localStorage.getItem('workScript')||'';
  $('work_file').innerHTML=STATE.scripts.map(s=>`<option value="${esc(s.path)}">${esc(s.name)}</option>`).join('')||'<option value="">완성된 대본 없음</option>';
  if(STATE.scripts.some(s=>s.path===previousWork))$('work_file').value=previousWork;
  if($('work_file').value&&(!WORK||WORK.script_file!==$('work_file').value))loadWorkspace(false);
  $('c_file').innerHTML=$('g_file').innerHTML=$('i_file').innerHTML;
  if(STATE.scripts.some(s=>s.path===selected))$('c_file').value=$('g_file').value=selected;
  else $('g_file').value=$('c_file').value;
  refreshGallery(true);
  const oldReset=$('reset_file').value;
  $('reset_file').innerHTML=(STATE.reset_items||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.kind==='mindam'?'민담 · ':'대본 · ')}${esc(x.label)}</option>`).join('')||'<option value="">초기화할 작업 없음</option>';
  if((STATE.reset_items||[]).some(x=>x.id===oldReset))$('reset_file').value=oldReset;
  $('p_target').value=STATE.config.대본_글자수; $('a_target').value=STATE.config.대본_글자수; markLen('p_target'); markLen('a_target');
  $('s_ai').value=STATE.config.AI; $('s_model').value=STATE.config.모델||'';
  const isWeb=STATE.config.AI==='deepseek-web';
  $('s_status').textContent=isWeb?'현재 deepseek-web (웹 채팅, 키 불필요)':(STATE.config.키있음?`현재 ${STATE.config.AI} · 키 저장됨`:`현재 ${STATE.config.AI} · API 키 없음`);
  $('s_web').textContent=isWeb?(STATE.web_alive?'🟢 딥시크 확장 연결됨 (chat.deepseek.com 탭 감지)':'🔴 확장이 연결되지 않음 — 크롬에 딥시크_확장 을 설치하고 chat.deepseek.com 탭을 열어 두세요'):'';
  $('envwarn').classList.toggle('hidden',STATE.config.키있음||isWeb);
  $('envwarn').textContent='설정.json 에 API 키가 없습니다. [설정] 탭에서 딥시크 키를 넣거나 AI 를 deepseek-web(무료) 로 바꾸세요.';
  if(isWeb&&!STATE.web_alive){$('envwarn').classList.remove('hidden');$('envwarn').textContent='deepseek-web 모드: 크롬 확장이 연결되지 않았습니다. [설정] 탭의 "deepseek-web 쓰는 법"을 보세요.';}
  refreshKieStatus();
  renderAfter(); renderQueue(STATE.queue);
}
function renderAfter(){
  renderTopics(); renderMindam();
  if(STATE.job&&STATE.job.status==='running')startPolling();
  const sp=new URLSearchParams(location.search);const q=sp.get('t');
  if(q){
    if(sp.get('ch')==='mindam'){goTab('mindam');const b=(STATE.topics.mindam||[]).find(x=>x.제목===q);if(b)selectBench(b);else $('m_title').value=q;}
    else{const all=[...STATE.topics.plan,...STATE.topics.candidates];const t=all.find(x=>x.제목===q);if(t)selectTopic(t);else $('p_title').value=q;}
    history.replaceState(null,'',location.pathname);
  }
}
function renderTopics(){
  const {plan,candidates}=STATE.topics;
  const li=(t,d,i)=>`<li data-title="${esc(t.제목)}" onclick='selectTopic(${JSON.stringify(t).replace(/'/g,"&#39;")})'><input type="checkbox" ${queueSelected.has('person|'+t.제목)?'checked':''} onclick="toggleQueueTopic(event,'person',${i})"><span class="d">${esc(d)}</span><span class="c">${esc(t.카테고리||'')}</span><span class="t">${esc(t.제목)}</span>${t.done?'<span class="done">✓ 대본 있음</span>':''}</li>`;
  const all=[...plan,...candidates]; $('topicList').innerHTML=all.map((t,i)=>li(t,i<plan.length?`${t.날짜.slice(5)} ${t.순번}편`:'후보',i)).join('')||'<li><span class="hint">계획.json 이 없습니다. 실행.bat 으로 주제를 먼저 뽑거나 아래에 직접 입력하세요.</span></li>';
  highlight();
}
function renderMindam(){
  const list=STATE.topics.mindam||[];
  $('mindamList').innerHTML=list.map((b,i)=>`<li data-title="${esc(b.제목)}" onclick="selectBench(STATE.topics.mindam[${i}])"><input type="checkbox" ${queueSelected.has('mindam|'+b.제목)?'checked':''} onclick="toggleQueueTopic(event,'mindam',${i})"><span class="d">${b.배수}배 · ${(b.조회수/10000).toFixed(1)}만</span><span class="c" style="width:auto">${esc(b.장르)}</span><span class="t">${esc(b.제목)}</span><span class="hint" style="white-space:nowrap">${esc(b.채널)}</span></li>`).join('')
    ||'<li><span class="hint">민담_후보.json 이 없습니다. 민담_주제뽑기.bat 으로 터진 제목을 먼저 모으거나 아래에 직접 입력하세요.</span></li>';
  highlightBench();
}
function highlightBench(){document.querySelectorAll('#mindamList li').forEach(l=>l.classList.toggle('sel',bench&&l.dataset.title===bench.제목));}
function selectBench(b){bench=b;$('m_chosen').textContent=(b?b.제목:$('m_title').value)||'(아직 없음)';if(b){$('m_title').value=b.제목;$('m_benchInfo').textContent=`벤치마킹 원본: ${b.채널} · ${b.조회수.toLocaleString()}회 · 평소의 ${b.배수}배 — 제목 골격은 살리고 알맹이(수단·관계·반전)를 바꿔 새 이야기로 만듭니다.`;}else{$('m_benchInfo').textContent='';}highlightBench();}
$('m_title').addEventListener('input',()=>{$('m_chosen').textContent=$('m_title').value||'(아직 없음)';if(bench&&$('m_title').value!==bench.제목){bench=null;$('m_benchInfo').textContent='';highlightBench();}chosenVar=null;$('varBox').classList.add('hidden');});
async function startVariations(){
  try{await api('/api/variations',{title:$('m_title').value,bench});$('varBox')._shown=null;startPolling();}catch(e){toast(e.message,true);}
}
function renderVariations(r){
  varRef=r.reference; chosenVar=null;
  const box=$('varBox');box.classList.remove('hidden');
  box.innerHTML=`<p class="hint">레퍼런스: ${esc(r.reference)} · AI 추천: ${esc(r.recommend||'')}</p>`+r.options.map(o=>`
    <label class="row" style="align-items:flex-start;border:1px solid var(--line);border-radius:8px;padding:10px 12px;cursor:pointer" onclick="chooseVar('${o.key}')">
      <input type="radio" name="var" value="${o.key}" style="margin-top:6px"><div><b>[${o.key}] ${esc(o.title)}</b><pre style="margin:4px 0 0;white-space:pre-wrap;font:12.5px/1.5 var(--sans);color:var(--muted)">${esc(o.text.replace(/^-\s*제목.*\n?/m,''))}</pre></div></label>`).join('');
  box._options=r.options;
  goTab('mindam'); setTimeout(()=>box.scrollIntoView({behavior:'smooth'}),180);
}
function chooseVar(k){const o=$('varBox')._options.find(x=>x.key===k);chosenVar=o;document.querySelector(`input[name=var][value=${k}]`).checked=true;$('m_hint').textContent=`선택: [${k}] ${o.title}`;}
function highlight(){document.querySelectorAll('#topicList li').forEach(l=>l.classList.toggle('sel',selected&&l.dataset.title===selected.제목));}
function selectTopic(t){selected=t;if(t){$('p_title').value=t.제목;}highlight();$('p_chosen').textContent=$('p_title').value||'(아직 없음)';}
$('p_title').addEventListener('input',()=>{$('p_chosen').textContent=$('p_title').value||'(아직 없음)';});
$('p_title').addEventListener('input',()=>{if(selected&&$('p_title').value!==selected.제목){selected=null;highlight();}});

function queueSource(ch){return ch==='mindam'?(STATE.topics.mindam||[]):[...(STATE.topics.plan||[]),...(STATE.topics.candidates||[])];}
function toggleQueueTopic(event,ch,index){event.stopPropagation();const topic=queueSource(ch)[index],key=ch+'|'+topic.제목;if(event.target.checked)queueSelected.set(key,{channel:ch,title:topic.제목,topic});else queueSelected.delete(key);renderQueueSelectionCount();}
function selectAllQueue(ch,on){for(const topic of queueSource(ch)){const key=ch+'|'+topic.제목;if(on)queueSelected.set(key,{channel:ch,title:topic.제목,topic});else queueSelected.delete(key);}renderTopics();renderMindam();renderQueueSelectionCount();}
function renderQueueSelectionCount(){const el=$('queue_summary');if(queueSelected.size)el.textContent=`선택 ${queueSelected.size}개 · 연속 제작 버튼을 누르세요`;}
function queueOptions(){return {guideline:$('p_guide').value,target:+$('a_target').value,mark_used:true,length:$('m_length').value,img_guideline:$('i_guide').value,style:$('a_style').value,chunk:+$('i_chunk').value,thumb_position:$('a_thumb_pos').value,
  steps:{optimize:$('a_optimize').checked,prompts:true,tts:true,images:true,hook:+$('a_hook').value,render:true,thumbnail:$('a_thumb').checked}};}
async function startSelectedQueue(){
  if(!queueSelected.size)return toast('주제 목록에서 연속 제작할 주제를 체크하세요.',true);
  const options=queueOptions();if(options.steps.hook>0&&!await ensureKieReady())return;
  if(!confirm(`선택한 ${queueSelected.size}개 주제를 한 편씩 연속 제작할까요?`))return;
  try{const q=await api('/api/queue/start',{items:[...queueSelected.values()],options});queueSelected.clear();renderQueue(q);startPolling();toast('연속 제작을 시작했습니다.');}catch(e){toast(e.message,true);}
}
async function queueControl(action){
  const labels={pause:'현재 편이 끝난 뒤 일시정지합니다.',resume:'연속 제작을 계속합니다.',cancel:'현재 작업과 남은 대기열을 모두 중단할까요?'};
  if(action==='cancel'&&!confirm(labels.cancel))return;
  try{const q=await api('/api/queue/'+action,{});renderQueue(q);toast(labels[action]);}catch(e){toast(e.message,true);}
}
async function removeQueueItem(id){try{renderQueue(await api('/api/queue/remove',{id}));}catch(e){toast(e.message,true);}}
function renderQueue(q){
  if(!q)return;const items=q.items||[],done=items.filter(x=>x.status==='done').length,failed=items.filter(x=>x.status==='error').length;
  $('queue_manage').classList.toggle('hidden',!items.length);
  $('queue_summary').textContent=`상태: ${q.status_text||'대기 없음'} · 전체 ${items.length}편 · 완료 ${done}편 · 실패 ${failed}편`;
  $('queue_list').innerHTML=items.length?items.map((x,i)=>`<div class="row" style="border-top:1px solid var(--line);padding:8px 0"><b style="min-width:42px">${i+1}편</b><span style="flex:1">${esc(x.title)}</span><span class="hint" style="min-width:120px">${esc(x.status_text||'대기 중')} · ${esc(x.stage||'')}</span>${x.status==='working'?`<progress max="1" value="${x.progress||0}" style="width:120px"></progress>`:''}${x.result&&x.result.assets?`<button class="mini" onclick="openPath('${js(x.result.assets)}')">결과 폴더</button>`:''}${x.status==='pending'?`<button class="mini" onclick="removeQueueItem('${x.id}')">목록에서 빼기</button>`:''}${x.error?`<small style="color:var(--warn)">${esc(x.error)}</small>`:''}</div>`).join(''):'<span class="hint">대기열이 없습니다. 주제 목록에서 여러 개를 체크하세요.</span>';
}
async function refreshQueue(){try{renderQueue(await api('/api/queue'));}catch(e){}}

let topicDeck=[], topicDeckChannel='';
function pickAnotherTopic(){
  const ch=pipelineTopic();
  const source=ch==='mindam'?(STATE.topics.mindam||[]):[...(STATE.topics.plan||[]),...(STATE.topics.candidates||[])];
  const current=$('a_title').value.trim();
  const unique=[...new Map(source.filter(x=>x&&x.제목).map(x=>[x.제목,x])).values()];
  if(unique.length<2&&unique.every(x=>x.제목===current))return toast('다른 주제 후보가 없습니다. 주제 추천 목록을 먼저 추가하세요.',true);
  if(topicDeckChannel!==ch||!topicDeck.length){
    topicDeckChannel=ch;
    topicDeck=unique.filter(x=>x.제목!==current);
    for(let i=topicDeck.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[topicDeck[i],topicDeck[j]]=[topicDeck[j],topicDeck[i]];}
  }
  const next=topicDeck.pop();
  if(!next)return toast('다른 주제 후보가 없습니다.',true);
  $('a_title').value=next.제목;
  if(ch==='mindam'){bench=next;selected=null;}else{selected=next;bench=null;}
  toast('새 주제로 변경했습니다: '+next.제목);
}

async function editGuide(selId){const name=$(selId).value;if(!name)return;const sub=selId==='m_guide'?'민담/':'';editing=sub+name;const j=await api('/api/guideline?name='+encodeURIComponent(editing));$('g_name').textContent=editing;$('g_text').value=j.text;$('guideCard').classList.remove('hidden');$('guideCard').scrollIntoView({behavior:'smooth'});}
async function saveGuide(){await api('/api/guideline',{name:editing,text:$('g_text').value});toast('지침 저장됨: '+editing);}
async function resetSelected(scope){
  const id=$('reset_file').value, item=(STATE.reset_items||[]).find(x=>x.id===id);
  if(!item)return toast('초기화할 작업을 선택하세요',true);
  if(scope==='assets'&&item.kind==='mindam')return toast('민담은 대본과 자료를 함께 초기화하세요',true);
  const what=scope==='all'?'대본과 생성 자료 모두':'생성 자료만';
  if(!confirm(`「${item.label}」의 ${what} 대본/_휴지통으로 옮길까요?`))return;
  try{const result=await api('/api/reset',{id,scope});$('progressCard').classList.add('hidden');await refresh();toast(`${result.count}개 항목을 _휴지통으로 옮겼습니다`);}
  catch(e){toast(e.message,true);}
}
async function restyleTo2D(){
  const file=$('i_file').value;if(!file)return toast('대본을 먼저 고르세요',true);
  if(!confirm('이 대본의 이미지 프롬프트를 2D·레퍼런스 지시로 바꿀까요? 기존 파일은 백업됩니다.'))return;
  try{const r=await api('/api/restyle-2d',{script_file:file});pickStyle('2D 일러스트');toast(`${r.count}개 장면을 2D로 전환했습니다`);}
  catch(e){toast(e.message,true);}
}
async function deleteSelectedScript(selectId){
  const select=$(selectId), file=select.value;
  if(!file)return toast('삭제할 대본을 선택하세요',true);
  const name=select.options[select.selectedIndex]?.textContent||file;
  if(!confirm(`「${name}」 대본과 연결된 자료를 _휴지통으로 옮길까요?`))return;
  try{const result=await api('/api/delete-script',{script_file:file});$('progressCard').classList.add('hidden');await refresh();toast(`${result.count}개 항목을 _휴지통으로 옮겼습니다`);}
  catch(e){toast(e.message,true);}
}
async function deleteCurrentCompletedWork(){
  const file=$('g_file').value||$('c_file').value||localStorage.getItem('selectedScript')||'';
  if(!file)return toast('삭제할 완료 작업을 먼저 선택하세요.',true);
  const item=(STATE.scripts||[]).find(x=>x.path===file), name=item?item.name:file;
  if(!confirm(`「${name}」의 대본·이미지·영상·음성·자막을 모두 _휴지통으로 옮길까요?`))return;
  try{
    const result=await api('/api/delete-script',{script_file:file});
    localStorage.removeItem('selectedScript'); $('fr_video').src='about:blank';
    $('progressCard').classList.add('hidden'); await refresh();
    toast(`${result.count}개 항목을 _휴지통으로 옮겼습니다.`);
  }catch(e){toast(e.message,true);}
}
async function saveKey(k){
  const v=$('key_'+k).value.trim(); if(!v) return toast('새 키를 입력한 뒤 저장을 누르세요. (이미 저장된 키는 그대로 유지됩니다)',true);
  const body=k==='inworld'?{인월드_API_키:v}:{['API_키_'+k]:v};
  const ai=(STATE.config.AI==='deepseek-web'?'deepseek':STATE.config.AI); if(k===ai) body.API_키=v;
  await api('/api/config',body); $('key_'+k).value=''; toast({deepseek:'딥시크',gemini:'제미나이',claude:'클로드',inworld:'인월드'}[k]+' 키 저장됨'); refresh();
}
async function saveVoice(ch){
  const body=ch==='mindam'?{인월드_목소리_민담:$('s_inworld_voice_m').value.trim(),인월드_속도_민담:+$('s_inworld_speed_m').value}
                          :{인월드_목소리_사람:$('s_inworld_voice').value.trim(),인월드_속도_사람:+$('s_inworld_speed').value,인월드_목소리:$('s_inworld_voice').value.trim(),인월드_속도:+$('s_inworld_speed').value,인월드_모델:$('s_inworld_model').value};
  if(!(ch==='mindam'?body.인월드_목소리_민담:body.인월드_목소리_사람)) return toast('목소리 ID 를 입력하세요',true);
  await api('/api/config',body); toast((ch==='mindam'?'민담·야담':'사람의 이유')+' 목소리 저장됨'); refresh();
}
async function saveAI(){await api('/api/config',{AI:$('s_ai').value,모델:$('s_model').value||''});toast('AI: '+$('s_ai').value);refresh();}
async function saveConfig(){await api('/api/config',{모델:$('s_model').value,대본_글자수:+$('p_target').value,
  인월드_모델:$('s_inworld_model').value,
  인월드_목소리_사람:$('s_inworld_voice').value,인월드_속도_사람:+$('s_inworld_speed').value,
  인월드_목소리_민담:$('s_inworld_voice_m').value,인월드_속도_민담:+$('s_inworld_speed_m').value,
  인월드_목소리:$('s_inworld_voice').value,인월드_속도:+$('s_inworld_speed').value,분당_글자수:+$('s_cpm').value,후킹_장면수:+$('a_hook').value});toast('설정 저장됨');refresh();}
// ── 화풍 클릭 선택 (두 탭 동기화, 설정.json 에 저장)
function renderStyles(list,cur){
  const desc=STATE.style_info||{}, groups=STATE.style_groups||{'화풍':list};
  const btn=s=>`<button type="button" data-val="${esc(s)}" onclick="pickStyle('${esc(s)}')">${esc(s)}<small>${esc(desc[s]||'')}</small></button>`;
  for(const id of ['a_styles','i_styles']){const box=$(id);if(!box)continue;
    box.innerHTML=Object.entries(groups).map(([g,names])=>`<div class="stylegrp"><div class="stylegrp-t">${esc(g)}</div><div class="styles">${names.map(btn).join('')}</div></div>`).join('');
    box.classList.remove('styles');}
  pickStyle(cur,true);
}
function pickStyle(v,silent){$('a_style').value=v;$('i_style').value=v;document.querySelectorAll('.styles button').forEach(b=>b.classList.toggle('on',b.dataset.val===v));if(!silent){api('/api/config',{화풍:v}).catch(()=>{});toast('화풍: '+v);}}
// ── 사람의 이유 길이 버튼 (분당 글자수 × 분)
function renderLenButtons(cpm){
  document.querySelectorAll('.lenbtns').forEach(sp=>{const id=sp.dataset.for;sp.innerHTML=[20,25,30].map(m=>`<button type="button" data-min="${m}" onclick="setLen('${id}',${m})">${m}분</button>`).join('');});
  window._cpm=cpm;
}
async function saveTelegramToken(){const token=$('tg_token').value.trim();if(!token)return toast('BotFather에서 받은 봇 토큰을 입력하세요.',true);await api('/api/config',{텔레그램_봇_토큰:token});$('tg_token').value='';toast('텔레그램 봇 토큰을 저장했습니다. 이제 봇에게 메시지를 보내고 채팅 자동 찾기를 누르세요.');refresh();}
async function findTelegramChats(){try{const r=await api('/api/telegram/chats',{});$('tg_chats').innerHTML=(r.chats||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${esc(x.id)}</option>`).join('')||'<option value="">찾은 채팅이 없습니다</option>';if(!r.chats.length)toast('텔레그램에서 봇에게 메시지를 먼저 보낸 뒤 다시 눌러주세요.',true);}catch(e){toast(e.message,true);}}
async function saveTelegramChat(){const id=$('tg_chats').value;if(!id)return toast('저장할 채팅을 선택하세요.',true);await api('/api/config',{텔레그램_채팅_ID:id});toast('텔레그램 채팅을 저장했습니다.');refresh();}
async function saveTelegramEnabled(){await api('/api/config',{텔레그램_알림:$('tg_enabled').checked});toast($('tg_enabled').checked?'텔레그램 알림을 켰습니다.':'텔레그램 알림을 껐습니다.');}
async function testTelegram(){try{await api('/api/telegram/test',{});toast('텔레그램으로 테스트 메시지를 보냈습니다.');}catch(e){toast(e.message,true);}}
function setLen(id,min){const v=Math.round(min*(window._cpm||270));$(id).value=v;if(id==='p_target')$('a_target').value=v;else $('p_target').value=v;markLen('p_target');markLen('a_target');if(min!==1)api('/api/config',{대본_글자수:v}).catch(()=>{});}
function markLen(id){const v=+$(id).value,cpm=window._cpm||270;document.querySelectorAll(`.lenbtns[data-for=${id}] button`).forEach(b=>b.classList.toggle('on',Math.abs(v-Math.round(+b.dataset.min*cpm))<50));}
document.addEventListener('input',e=>{if(e.target.id==='p_target'||e.target.id==='a_target')markLen(e.target.id);});
async function startTTS(){try{await api('/api/tts',{script_file:$('i_file').value});startPolling();}catch(e){toast(e.message,true);}}
async function loadWorkspace(showToast){
  const file=$('work_file').value;if(!file){WORK=null;$('work_body').classList.add('hidden');$('work_empty').classList.remove('hidden');return;}
  try{WORK=await api('/api/workspace?script='+encodeURIComponent(file));localStorage.setItem('workScript',file);
    $('work_script').value=WORK.script||'';$('work_prompts').value=WORK.prompts||'';$('work_srt').value=WORK.srt||'';
    $('work_title').value=WORK.title||'';$('work_description').value=WORK.description||'';$('work_sources').value=WORK.sources||'';$('work_tags').value=WORK.tags||'';
    renderWorkspaceThumbnails();
    $('work_empty').classList.add('hidden');$('work_body').classList.remove('hidden');if(showToast)toast('완성 자료를 불러왔습니다.');
  }catch(e){toast(e.message,true);}
}
function renderWorkspaceThumbnails(){
  const box=$('work_thumbnails'),items=(WORK&&WORK.thumbnails)||[];
  if(!items.length){box.innerHTML='<div class="hint">아직 생성된 썸네일이 없습니다. 아래의 썸네일 3장 만들기를 누르세요.</div>';return;}
  box.innerHTML=items.map((p,i)=>{const src=`http://127.0.0.1:8765/api/gen/image?path=${encodeURIComponent(p)}&t=${Date.now()}`;return `<div class="g done"><div class="no">썸네일 ${i+1}</div><div class="pic"><img src="${src}" loading="lazy" onclick="showBig('${src}')"></div><div class="st">완료 · 클릭해서 크게 보기</div></div>`}).join('');
}
async function makeWorkspaceThumbnails(){
  if(!WORK)return toast('완성 자료에서 작업을 먼저 선택하세요.',true);
  try{await api('/api/thumbnail',{script_file:WORK.script_file,style:$('a_style').value,position:$('a_thumb_pos').value,regenerate:true});startPolling();toast('새 이미지와 굵은 한글 문구로 썸네일 3장을 다시 만듭니다.');}catch(e){toast(e.message,true);}
}
function openWorkspaceThumbnailFolder(){if(!WORK)return toast('작업을 먼저 선택하세요.',true);openPath(WORK.thumbnail_dir);}
async function saveWorkspaceText(kind){
  if(!WORK)return toast('작업을 먼저 선택하세요.',true);const ids={script:'work_script',prompts:'work_prompts',srt:'work_srt'};
  try{await api('/api/workspace/save',{script_file:WORK.script_file,kind,text:$(ids[kind]).value});toast(({script:'대본',prompts:'이미지 프롬프트',srt:'자막'})[kind]+' 저장 완료');}
  catch(e){toast(e.message,true);}
}
async function saveWorkspaceMeta(){
  if(!WORK)return toast('작업을 먼저 선택하세요.',true);
  try{await api('/api/workspace/save',{script_file:WORK.script_file,kind:'metadata',title:$('work_title').value,description:$('work_description').value,sources:$('work_sources').value,tags:$('work_tags').value});toast('업로드 정보 저장 완료');}
  catch(e){toast(e.message,true);}
}
async function copyField(id){
  const el=$(id),text=el.value||'';if(!text)return toast('복사할 내용이 없습니다.',true);
  try{await navigator.clipboard.writeText(text);toast('클립보드에 복사했습니다.');}catch(e){el.focus();el.select();document.execCommand('copy');toast('클립보드에 복사했습니다.');}
}
async function rerunTTS(){
  if(!WORK)return toast('작업을 먼저 선택하세요.',true);
  try{await saveWorkspaceText('script');await api('/api/tts',{script_file:WORK.script_file});startPolling();toast('수정한 대본으로 TTS를 다시 만듭니다.');}catch(e){toast(e.message,true);}
}
function pipelineTopic(){const ch=document.querySelector('input[name=a_channel]:checked').value;return ch;}
async function continuePipeline(file){
  if(!file) return toast('대본 파일을 고르세요',true);
  const steps={optimize:false,prompts:true,tts:true,images:true,hook:+$('a_hook').value,render:true,thumbnail:$('a_thumb').checked};
  if(steps.hook>0&&!await ensureKieReady())return;
  try{await api('/api/pipeline',{reuse_prompts:true,script_file:file,channel:file.endsWith('final.txt')?'mindam':'person',style:$('a_style').value,img_guideline:$('i_guide').value,chunk:+$('i_chunk').value,steps,thumb_position:$('a_thumb_pos').value});startPolling();}catch(e){toast(e.message,true);}
}
async function startThumb(){
  try{await api('/api/thumbnail',{script_file:$('i_file').value,style:$('i_style').value,position:$('a_thumb_pos').value});startPolling();}catch(e){toast(e.message,true);}
}
async function startPipeline(){
  const ch=pipelineTopic(); const title=$('a_title').value.trim(); if(!title) return toast('주제를 입력하세요',true);
  const steps={optimize:$('a_optimize').checked,prompts:$('a_prompts').checked,tts:$('a_tts').checked,images:$('a_images').checked,hook:+$('a_hook').value,render:$('a_render').checked,thumbnail:$('a_thumb').checked};
  if(steps.hook>0&&!await ensureKieReady())return;
  const body={channel:ch,title,topic:(ch==='person'&&selected&&selected.제목===title)?selected:null,guideline:$('p_guide').value,target:+$('a_target').value,mark_used:true,
    length:$('m_length').value,bench:(ch==='mindam'&&bench&&bench.제목===title)?bench:null,img_guideline:$('i_guide').value,style:$('a_style').value,chunk:+$('i_chunk').value,steps,thumb_position:$('a_thumb_pos').value};
  try{await api('/api/pipeline',body);startPolling();}catch(e){toast(e.message,true);}
}
$('a_title').addEventListener('focus',()=>{if(!$('a_title').value){const ch=pipelineTopic();$('a_title').value=ch==='mindam'?$('m_title').value:$('p_title').value;}});
document.querySelectorAll('input[name=a_channel]').forEach(r=>r.addEventListener('change',()=>{$('a_title').value=pipelineTopic()==='mindam'?$('m_title').value:$('p_title').value;}));

async function startPerson(){
  const body={topic:selected,title:$('p_title').value,guideline:$('p_guide').value,target:+$('p_target').value,mark_used:$('p_used').checked,optimize:$('p_opt').checked};
  try{await api('/api/script',body);startPolling();}catch(e){toast(e.message,true);}
}
async function startMindam(){
  const body={title:chosenVar?chosenVar.title:$('m_title').value,reference:chosenVar?varRef:'',variation:chosenVar?chosenVar.text:'',
              length:$('m_length').value,mark_used:$('m_used').checked,resume_dir:$('m_resume').value,bench:bench,optimize:$('m_opt').checked};
  try{await api('/api/mindam',body);startPolling();}catch(e){toast(e.message,true);}
}
async function startOptimize(){
  try{await api('/api/optimize',{script_file:$('i_file').value});startPolling();}catch(e){toast(e.message,true);}
}
async function makeTimestamps(){
  try{const j=await api('/api/timestamps',{dir:$('ts_dir').value,srt:$('ts_srt').value});$('ts_out').textContent=j.text+'\n→ '+j.file+' (유튜브_설명·최적화 파일에도 [챕터]로 추가됨)';}catch(e){toast(e.message,true);}
}
async function startImages(){
  const body={script_file:$('i_file').value,guideline:$('i_guide').value,style:$('i_style').value,chunk:+$('i_chunk').value};
  try{await api('/api/images',body);startPolling();}catch(e){toast(e.message,true);}
}
function startPolling(){goTab('auto');clearInterval(pollTimer);$('progressCard').classList.remove('hidden');$('progressCard').classList.add('studio-selected');$('pg_result').classList.add('hidden');$('pg_err').classList.add('hidden');$('pg_preview').classList.add('hidden');['p_go','m_go','i_go'].forEach(i=>$(i).disabled=true);poll();pollTimer=setInterval(poll,1500);$('progressCard').scrollIntoView({behavior:'smooth'});}
const PIPE_STEPS=['① 대본','② 이미지 프롬프트','③ 나레이션','④ 이미지 자동 생성','⑤ 후킹 영상',"⑤' 썸네일",'⑥ 최종 렌더','완료'];
// 단계 → (결과 키, 여는 방법). 클릭하면 그 단계가 만든 파일/폴더를 연다
const STEP_OUT=[['script','file'],['prompts','file'],['narration','folder'],['images','folder'],['hook','folder'],['thumbnails','folder'],['video','folder'],['assets','folder']];
function renderOverview(job){
  const steps=[...document.querySelectorAll('#pipeline_overview .pipeline-step')];if(!steps.length)return;
  steps.forEach(x=>{x.classList.remove('now','done');x.querySelector('small').textContent='대기';});
  if(!job||job.status==='none')return;
  const r=job.result||{},done={script:!!(r.script||r.file),prompt:!!r.prompts,image:!!r.images,motion:!!r.hook,audio:!!(r.narration||r.mp3),render:!!r.video};
  for(const x of steps)if(done[x.dataset.stage]){x.classList.add('done');x.querySelector('small').textContent='완료';}
  const s=String(job.stage||'');let current=s.includes('최종')?'render':s.includes('후킹')?'motion':s.includes('이미지 자동')?'image':s.includes('나레이션')?'audio':s.includes('프롬프트')?'prompt':s.includes('대본')?'script':'';
  if(job.status==='done')steps.forEach(x=>{x.classList.add('done');x.classList.remove('now');x.querySelector('small').textContent='완료';});
  else if(current){const x=steps.find(v=>v.dataset.stage===current);if(x){x.classList.add('now');x.querySelector('small').textContent='진행 중';}}
}
function stageIndex(st){
  st=st||''; const m=st.match(/^([①②③④⑤⑥]'?)/); if(m) return PIPE_STEPS.findIndex(s=>s.startsWith(m[1]));
  if(/최종 렌더|렌더링/.test(st)) return 6; if(/썸네일/.test(st)) return 5; if(/후킹/.test(st)) return 4; if(/이미지 생성/.test(st)) return 3;
  if(/나레이션/.test(st)) return 2; if(/프롬프트|변환/.test(st)) return 1; if(/완료/.test(st)) return 7; return 0;
}
function renderSteps(j){
  const box=$('pg_steps'); if(j.kind!=='pipeline'){box.innerHTML='';return;}
  let idx=stageIndex(j.stage); if(j.status==='done')idx=PIPE_STEPS.length-1;
  const fin=j.status==='done', r=j.result||{};
  const cnt=(j.stage||'').match(/이미지 생성\s*(\d+)\/(\d+)/); const extra=cnt?` ${cnt[1]}/${cnt[2]}`:'';
  box.innerHTML=PIPE_STEPS.map((s,i)=>{const [key,how]=STEP_OUT[i]; const v=r[key]; const path=Array.isArray(v)?(v[0]||''):(v||''); s=s+(i===3?extra:'');
    const cls=((i<idx||fin)?'done':(i===idx?'now':''))+(path?' has':'');
    return `<span class="${cls}" ${path?`onclick="openStep('${js(path)}','${how}')" title="클릭: ${how==='file'?'내용 보기':'폴더 열기'}"`:''}>${(i<idx||fin)?'✓ ':''}${esc(s)}${path?' ↗':''}</span>`;}).join('');
}
// ── 이미지 생성 갤러리 (편집프로그램 8765 에서 생성 상태·파일 목록을 가져와 실시간 표시)
let galBusy=false, galKey='', galPrompts={path:'',count:0};
let galDir='', galPromptsPath='';
async function refreshGallery(force){
  // 실행 중인 파이프라인이 있으면 그 결과 폴더, 아니면 [이미 만든 대본] 에서 고른 대본의 자료 폴더
  const j=STATE.job; let dir='',pr='';
  if(j&&j.kind==='pipeline'&&j.status==='running'&&(j.result||{}).images){dir=j.result.images;pr=j.result.prompts||'';}
  else{const f=$('c_file').value; if(f){try{const a=await api('/api/assets?script='+encodeURIComponent(f));dir=a.images;pr=a.prompts;}catch(e){}}}
  galDir=dir; galPromptsPath=pr; $('gal_src').textContent=dir?dir:'대본을 고르면 그 대본의 이미지 폴더를 보여 줍니다';
  if(force)galKey='';
  await updateGallery({kind:'pipeline',result:{images:dir,prompts:pr}});
  await refreshKieFiles();
}
$('c_file').addEventListener('change',()=>{ $('g_file').value=$('c_file').value;localStorage.setItem('selectedScript',$('c_file').value);refreshGallery(true); });
$('g_file').addEventListener('change',()=>{ $('c_file').value=$('g_file').value;localStorage.setItem('selectedScript',$('g_file').value);refreshGallery(true); });
setInterval(()=>{ const tab=document.querySelector('.tabs button.on')?.dataset.tab;if(tab==='auto'||tab==='gallery')refreshGallery(false); },3000);
async function updateGallery(j){
  const dir=(j.result||{}).images; const box=$('galleryCard');
  if(!dir){$('pg_gal').innerHTML='<div class="hint">대본을 고르세요</div>';return;}
  if(galBusy)return; galBusy=true;
  try{
    const [st,im]=await Promise.all([fetch('http://127.0.0.1:8765/api/gen/status').then(r=>r.json()),fetch('http://127.0.0.1:8765/api/gen/images?dir='+encodeURIComponent(dir)).then(r=>r.json())]);
    const imgs={}; (im.images||[]).forEach(i=>{if(!i.video)imgs[i.no]=i;});
    const pr=(j.result||{}).prompts; if(pr&&galPrompts.path!==pr){try{const pj=await fetch('http://127.0.0.1:8765/api/gen/prompts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:pr})}).then(r=>r.json());galPrompts={path:pr,count:pj.count||0};}catch(e){galPrompts={path:pr,count:0};}}
    const total=Math.max(st.total||0,galPrompts.count||0,...Object.keys(imgs).map(Number),0);
    const key=JSON.stringify([st.status,st.current,Object.values(imgs).map(i=>i.mtime)]);
    $('pg_imgs_t').textContent=` ${Object.keys(imgs).length}/${total} · ${({running:'생성 중',paused:'일시정지',done:'완료',stopped:'중단',error:'오류',idle:'대기'})[st.status]||st.status}${st.current?' · 지금 '+String(st.current).padStart(3,'0')+'번':''}${st.failed&&st.failed.length?' · 실패 '+st.failed.join(','):''}`;
    if(key===galKey)return; galKey=key;
    const gal=$('pg_gal'); gal.innerHTML=''; const busy=(st.status==='running'||st.status==='paused'); const failed=new Set(st.failed||[]);
    for(let i=1;i<=total;i++){
      const it=imgs[i], now=busy&&st.current===i, fail=!it&&failed.has(i);
      const d=document.createElement('div'); d.className='g'+(it?' done':'')+(now?' now':'')+(fail?' fail':'');
      const src=it?`http://127.0.0.1:8765/api/gen/image?path=${encodeURIComponent(it.path)}&t=${it.mtime}`:'';
      const stTxt=it?'완료 · 미리보기':(now?'생성 중':(fail?'실패 · 재시도 필요':'대기'));
      const btns=it?`<button class="re" onclick="regenScene(${i})">재생성</button><button class="vi" onclick="hookScene(${i})">영상화</button>`:`<button class="ge" onclick="regenScene(${i})">${fail?'재시도':'생성'}</button>`;
      d.innerHTML=`<div class="no">${String(i).padStart(3,'0')}</div><div class="pic">${it?`<img src="${src}" loading="lazy" onclick="showBig('${src}')">`:(fail?'FAIL':(now?'…':'대기'))}</div><div class="st">${stTxt}</div><div class="bt">${btns}</div>`;
      gal.appendChild(d);}
  }catch(e){$('pg_imgs_t').textContent='편집프로그램(8765)에 연결할 수 없습니다';}finally{galBusy=false;}
}
// ── 갤러리 제어: 이어서 / 처음부터 / 일시정지 / 중단 (편집프로그램 8765 러너)
async function genBodyFromUI(){
  const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json()); const ui=(info.config||{}).gen_ui||{}; const XY=ui.XY||{};
  if(!galDir)throw new Error('대본을 먼저 고르세요'); if(!galPromptsPath)throw new Error('이미지 프롬프트 파일이 없습니다. 먼저 이미지 프롬프트를 만드세요.');
  if(!XY.prompt||!XY.download)throw new Error('설정에서 프롬프트 입력창과 이미지 다운로드 좌표를 먼저 저장하세요');
  return {prompts_file:galPromptsPath,output_dir:galDir,download_dir:(ui.P||{}).download||info.downloads_dir,prompt_xy:XY.prompt,generate_xy:XY.generate||XY.prompt,download_xy:XY.download,
    wait_generate:+(ui.wait_generate||60),wait_download:+(ui.wait_download||120),wait_next:0.5,start_no:1,end_no:0,skip_existing:true,
    style_prefix:(STATE.style_prefixes||{})[$('a_style').value]||ui.style_prefix||'',retries:1,window_keyword:ui.window_keyword||'드롭샷',auto_generate:ui.auto_generate!==false};
}
async function post8765(path,body){const r=await fetch('http://127.0.0.1:8765'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||r.statusText);return j;}
async function refreshKieStatus(){
  try{const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json());const msg=info.kie_key_saved?'✓ KIE 키 저장됨':'KIE 키가 필요합니다';$('g_kie_status').textContent=$('s_kie_status').textContent=msg;return !!info.kie_key_saved;}
  catch(e){$('g_kie_status').textContent=$('s_kie_status').textContent='편집프로그램 연결을 확인하세요';return false;}
}
async function saveKieKey(){
  const key=$('s_kie_key').value.trim();if(!key)return toast('KIE API 키를 입력하세요',true);
  try{await post8765('/api/config',{kie_api_key:key});$('s_kie_key').value='';await refreshKieStatus();toast('KIE 키 저장됨');}
  catch(e){toast('KIE 키 저장 실패: '+e.message,true);}
}
async function ensureKieReady(){
  if(await refreshKieStatus())return true;
  goTab('settings');$('s_kie_key').focus();toast('앞 7장 영상화를 위해 KIE 키를 먼저 저장하세요',true);return false;
}
async function loadEditorSettings(){
  try{
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json());if(!info.config)throw new Error('편집프로그램 응답 없음');
    const ui=info.config.gen_ui||{},xy=ui.XY||{};
    for(const name of ['prompt','generate','download']){const pair=xy[name]||[];$('s_'+name+'_x').value=pair[0]??'';$('s_'+name+'_y').value=pair[1]??'';}
    $('s_window_keyword').value=ui.window_keyword||'드롭샷';
    $('s_xy_status').textContent='저장된 좌표: '+['prompt','generate','download'].map(n=>xy[n]?`${{prompt:'입력창',generate:'생성',download:'다운로드'}[n]} (${xy[n].join(', ')})`:`${{prompt:'입력창',generate:'생성',download:'다운로드'}[n]} 없음`).join(' · ');
    await refreshKieStatus();
  }catch(e){$('s_xy_status').textContent='좌표를 불러오지 못했습니다: '+e.message;}
}
async function saveEditorXY(){
  try{
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json()),ui=(info.config||{}).gen_ui||{},XY={...(ui.XY||{})};
    for(const name of ['prompt','generate','download']){
      const x=$('s_'+name+'_x').value.trim(),y=$('s_'+name+'_y').value.trim();
      if((x&&!y)||(!x&&y))throw new Error('X와 Y를 모두 입력하세요: '+name);
      if(x&&y)XY[name]=[Number(x),Number(y)];
    }
    await post8765('/api/config',{gen_ui:{...ui,XY,window_keyword:$('s_window_keyword').value.trim()||'드롭샷'}});
    $('s_xy_status').textContent='✓ 이미지 생성 좌표가 저장됐습니다';toast('좌표 저장됨');return true;
  }catch(e){$('s_xy_status').textContent='좌표 저장 실패: '+e.message;toast(e.message,true);return false;}
}
async function captureEditorXY(name){
  const label={prompt:'프롬프트 입력창',generate:'생성 버튼',download:'다운로드 버튼'}[name];
  const counter=$('xy_countdown');
  $('s_xy_status').textContent=`마우스를 드롭샷의 ${label} 위에 올려 두세요`;
  counter.classList.add('active');
  try{
    const capture=post8765('/api/gen/capture',{seconds:6});
    for(let second=6;second>=1;second--){counter.textContent=String(second);await new Promise(resolve=>setTimeout(resolve,1000));}
    const j=await capture;$('s_'+name+'_x').value=j.x;$('s_'+name+'_y').value=j.y;await saveEditorXY();
  }catch(e){$('s_xy_status').textContent='좌표 잡기 실패: '+e.message;}
  finally{counter.classList.remove('active');counter.textContent='6 → 5 → 4 → 3 → 2 → 1';}
}
async function testEditorXY(name){
  const x=$('s_'+name+'_x').value,y=$('s_'+name+'_y').value;
  if(x===''||y==='')return toast('좌표를 먼저 입력하거나 잡으세요',true);
  try{await post8765('/api/gen/test',{x:Number(x),y:Number(y)});toast('마우스를 해당 좌표로 옮겼습니다');}
  catch(e){toast('좌표 테스트 실패: '+e.message,true);}
}
async function detectGenerateButton(){
  $('s_xy_status').textContent="드롭샷에서 파란 '이미지 생성하기' 버튼을 찾는 중…";
  try{
    const j=await post8765('/api/gen/detect',{window_keyword:$('s_window_keyword').value.trim()||'드롭샷'});
    $('s_generate_x').value=j.x;$('s_generate_y').value=j.y;await saveEditorXY();
    $('s_xy_status').textContent=`✓ 생성하기 버튼을 자동으로 찾았습니다: X=${j.x}, Y=${j.y}`;
  }catch(e){$('s_xy_status').textContent='자동 탐색 실패: 드롭샷 창을 열고 프롬프트를 입력해 파란 생성 버튼이 보이게 한 뒤 다시 누르세요. ('+e.message+')';}
}
let kieJobId=sessionStorage.getItem('kieJobId')||'', kieTimer=null;
async function refreshKieFiles(){
  if(!galDir){$('g_kie_scenes').textContent='대본을 고르면 앞 7장 영상 상태를 표시합니다';if(!kieJobId)$('g_kie_progress').textContent='대본을 먼저 선택하세요';return;}
  try{
    const r=await fetch('http://127.0.0.1:8765/api/gen/images?dir='+encodeURIComponent(galDir));
    if(!r.ok)throw new Error('파일 목록을 읽지 못했습니다');
    const data=await r.json(),done=new Set((data.images||[]).filter(x=>x.video).map(x=>x.no));
    const count=[1,2,3,4,5,6,7].filter(n=>done.has(n)).length;
    $('g_kie_scenes').textContent=[1,2,3,4,5,6,7].map(n=>`${String(n).padStart(3,'0')} ${done.has(n)?'✓ 완료':'대기'}`).join(' · ');
    if(!kieJobId)$('g_kie_progress').textContent=count===7?'✅ 앞 7장 영상 변환 완료 (7/7)':count?'🎬 영상 파일 '+count+'/7개 완료 · 나머지 확인 필요':'영상 파일 0/7개 · 아직 완료되지 않았습니다';
  }catch(e){$('g_kie_scenes').textContent='영상 파일 확인 실패: '+e.message;}
}
async function pollKieJob(){
  if(!kieJobId)return;
  try{const r=await fetch('http://127.0.0.1:8765/api/jobs/'+encodeURIComponent(kieJobId));if(!r.ok)throw new Error('작업을 찾지 못했습니다');const j=await r.json();
    $('g_kie_progress').textContent=`KIE ${j.stage||j.status||'진행 중'}${j.progress!=null?' · '+Math.round(j.progress*100)+'%':''}${j.error?' · '+j.error:''}`;
    await refreshKieFiles();
    if(['done','error','cancelled'].includes(j.status)){clearInterval(kieTimer);kieTimer=null;kieJobId='';sessionStorage.removeItem('kieJobId');await refreshGallery(true);if(j.status==='error')$('g_kie_progress').textContent='❌ 영상화 오류: '+(j.error||'작업 로그를 확인하세요');else if(j.status==='cancelled')$('g_kie_progress').textContent='■ 영상화가 중단됐습니다';}
  }catch(e){clearInterval(kieTimer);kieTimer=null;kieJobId='';sessionStorage.removeItem('kieJobId');await refreshKieFiles();if($('g_kie_scenes').textContent.includes('대기'))$('g_kie_progress').textContent+=' · 이전 작업 상태를 읽지 못했습니다';}
}
async function startFirstSevenVideos(){
  if(kieJobId)return toast('앞 7장 영상화가 이미 진행 중입니다',true);
  if(!galDir||!galPromptsPath)return toast('대본과 이미지 프롬프트를 먼저 고르세요',true);
  if(!await ensureKieReady())return;
  try{
    const im=await fetch('http://127.0.0.1:8765/api/gen/images?dir='+encodeURIComponent(galDir)).then(r=>r.json());
    const ready=new Set((im.images||[]).filter(x=>!x.video).map(x=>x.no));
    const missing=[1,2,3,4,5,6,7].filter(no=>!ready.has(no));
    if(missing.length)return toast('앞 7장 이미지가 먼저 필요합니다. 없는 장면: '+missing.map(n=>String(n).padStart(3,'0')).join(', '),true);
    if(!confirm('앞 7장 이미지를 KIE AI 영상으로 변환할까요? 장면마다 크레딧이 사용됩니다.'))return;
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json()),ui=(info.config||{}).gen_ui||{};
    const body={api_key:'',images_dir:galDir,prompts_file:galPromptsPath,scenes:[1,2,3,4,5,6,7],model:ui.kie_model||'veo-3-1',aspect_ratio:ui.kie_ratio||'16:9',duration:0,motion_prompt:ui.motion_prompt||'Subtle 2D motion, preserve characters and composition.',use_scene_prompt:true,output_dir:''};
    const j=await post8765('/api/hook/start',body);kieJobId=j.job_id;sessionStorage.setItem('kieJobId',kieJobId);clearInterval(kieTimer);kieTimer=setInterval(pollKieJob,2000);pollKieJob();toast('앞 7장 영상 변환을 시작했습니다');
  }catch(e){toast('영상화 실패: '+e.message,true);}
}
async function cancelKieVideos(){
  if(!kieJobId)return toast('진행 중인 영상화가 없습니다',true);
  try{await post8765('/api/jobs/'+encodeURIComponent(kieJobId)+'/cancel',{});$('g_kie_progress').textContent='영상화 중단 요청';}
  catch(e){toast('중단 실패: '+e.message,true);}
}
if(kieJobId){kieTimer=setInterval(pollKieJob,2000);setTimeout(pollKieJob,500);}
async function galStart(fromScratch){
  try{
    const body=await genBodyFromUI();
    if(fromScratch){ if(!confirm('지금 있는 그림을 전부 이전/ 폴더로 옮기고 1번부터 다시 만들까요?'))return; const r=await post8765('/api/gen/reset',{output_dir:galDir}); body.skip_existing=false; toast(`그림 ${r.moved}장을 이전/ 으로 옮김`); }
    await post8765('/api/gen/start',body); galKey=''; toast(fromScratch?'1번부터 다시 만듭니다 — 드롭샷 창을 가리지 마세요':'빠진 장면부터 이어서 만듭니다 — 드롭샷 창을 가리지 마세요');
  }catch(e){toast(e.message,true);}
}
async function galCtl(what){try{await post8765('/api/gen/'+what,{});toast({pause:'일시정지',resume:'재개',stop:'중단 요청'}[what]);galKey='';}catch(e){toast(e.message,true);}}
async function hookScene(no){
  if(!await ensureKieReady())return;
  if(!confirm(String(no).padStart(3,'0')+'번 장면을 KIE 로 영상으로 만들까요? (크레딧 차감, 1~4분)'))return;
  try{
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json()); const ui=(info.config||{}).gen_ui||{}; const j=STATE.job||(await api('/api/job'));
    const body={api_key:'',images_dir:galDir,prompts_file:galPromptsPath||'',scenes:[no],model:ui.kie_model||'veo-3-1',aspect_ratio:ui.kie_ratio||'16:9',duration:0,
      motion_prompt:ui.motion_prompt||'Cinematic slow camera movement, subtle natural motion, keep the same style and composition.',use_scene_prompt:true,output_dir:''};
    const r=await fetch('http://127.0.0.1:8765/api/hook/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const jj=await r.json(); if(!r.ok)throw new Error(jj.detail||r.statusText);
    toast(String(no).padStart(3,'0')+'번 영상 변환 시작 — 편집프로그램 화면에서 진행을 볼 수 있습니다');
  }catch(e){toast(e.message,true);}
}
function showBig(src){const lb=document.createElement('div');lb.className='lightbox';lb.innerHTML=`<img src="${src}">`;lb.onclick=()=>lb.remove();document.body.appendChild(lb);}
async function regenScene(no){
  try{const st=await fetch('http://127.0.0.1:8765/api/gen/status').then(r=>r.json()); if(st.status==='running'||st.status==='paused'){return toast('지금 생성이 돌아가는 중입니다. [■ 중단] 뒤에 다시 누르거나 끝날 때까지 기다리세요.',true);}}catch(e){}
  if(!confirm(String(no).padStart(3,'0')+'번 장면을 다시 만들까요? (드롭샷 창을 가리지 마세요)'))return;
  try{
    const info=await fetch('http://127.0.0.1:8765/api/info').then(r=>r.json()); const ui=(info.config||{}).gen_ui||{}; const XY=ui.XY||{}; const j=STATE.job||(await api('/api/job'));
    const dir=galDir, prompts=galPromptsPath;
    if(!prompts)throw new Error('이미지 프롬프트 파일이 없습니다. 먼저 이미지 프롬프트를 만드세요.');
    if(!XY.prompt||!XY.download)throw new Error('설정에서 프롬프트 입력창과 이미지 다운로드 좌표를 먼저 저장하세요');
    const body={scene:no,prompts_file:prompts,output_dir:dir,download_dir:(ui.P||{}).download||info.downloads_dir,prompt_xy:XY.prompt,generate_xy:XY.generate||XY.prompt,download_xy:XY.download,
      wait_generate:+(ui.wait_generate||60),wait_download:+(ui.wait_download||120),wait_next:0.5,start_no:no,end_no:no,skip_existing:false,style_prefix:(STATE.style_prefixes||{})[$('a_style').value]||ui.style_prefix||'',retries:1,window_keyword:ui.window_keyword||'드롭샷'};
    const r=await fetch('http://127.0.0.1:8765/api/gen/regen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const jj=await r.json(); if(!r.ok)throw new Error(jj.detail||r.statusText);
    toast(String(no).padStart(3,'0')+'번 다시 생성 시작'); galKey='';
  }catch(e){toast(e.message,true);}
}
function openStep(path,how){ if(how==='file') showFile(path); else openPath(path); }
async function poll(){
  const j=await api('/api/job');if(!j||j.status==='none')return; STATE.job=j;
  renderOverview(j);
  $('a_cancel').classList.toggle('hidden',j.status!=='running');
  $('pg_kind').textContent=({script:'사람의 이유 대본',mindam:'민담 대본',images:'이미지 프롬프트',variations:'주제 변형',optimize:'알고리즘 최적화',tts:'나레이션',pipeline:'🚀 한 편 자동 제작',queue_pipeline:'📚 연속 제작 중',thumbnail:'썸네일'}[j.kind]||'작업 중')+' · '+j.started+' 시작'+(j.stage?' · '+j.stage:'');
  const log=$('pg_log');const txt=j.log.join('\n')+(j.partial?'\n'+j.partial:'');if(log.textContent!==txt){log.textContent=txt;if($('pg_follow').checked)log.scrollTop=log.scrollHeight;}
  $('pg_lines').textContent=j.log.length+'줄';$('pg_stage').textContent=j.stage?('지금: '+j.stage):'';
  renderSteps(j);
  refreshGallery(false);
  if(j.status==='running'&&STATE&&STATE.config.AI==='deepseek-web'){try{const w=await api('/api/web/status');const t=(w.taken||[])[0];$('pg_web').classList.remove('hidden');$('pg_web').textContent=!w.alive?'🔴 딥시크 확장이 끊겼습니다 — chat.deepseek.com 창을 열어 두세요':(t?`🌐 딥시크 웹 응답 중 · ${t.progress||'전송 중'} · ${t.since}초`:'🌐 딥시크 웹 연결됨');$('pg_web').style.color=((t&&/가려져/.test(t.progress||''))||!w.alive)?'var(--warn)':'var(--muted)';}catch(e){}}else{$('pg_web').classList.add('hidden');}
  // 진행률: 로그의 "n/m" 또는 "챕터" 표시를 대충 읽어 표시
  let p=0.1;const m=[...j.log.join('\n').matchAll(/(\d+)\/(\d+)\s*(묶음|부분)|챕터\s*(\d+)/g)];if(m.length){const last=m[m.length-1];p=last[2]?(+last[1])/(+last[2])*0.9:Math.min(0.9,(+last[4])/9);}
  if(j.progress) p=j.progress;
  if(j.status==='running'){$('pg_err').classList.add('hidden');$('pg_result').classList.add('hidden');}
  if(j.status!=='running'){clearInterval(pollTimer);['p_go','m_go','i_go'].forEach(i=>$(i).disabled=false);p=1;}
  $('pg_bar').style.width=(p*100)+'%';
  if(j.status==='done'&&j.kind==='variations'){if($('varBox')._shown!==j.started){$('varBox')._shown=j.started;renderVariations(j.result);}$('pg_result').classList.remove('hidden');$('pg_result').innerHTML=`<b>✅ 베리에이션 ${j.result.options.length}개</b> — 위에서 하나를 고르고 [민담 대본 만들기]를 누르세요. <div class="hint">${esc(j.result.cost||'')}</div>`;return;}
  if(j.status==='done'){
    const r=j.result,box=$('pg_result');box.classList.remove('hidden');
    if(j.kind==='thumbnail'&&$('work_file').value)loadWorkspace(false);
    if(r.script||r.file){localStorage.setItem('workScript',r.script||r.file);WORK=null;}
    box.innerHTML=`<b>✅ 완료</b> ${r.title?esc(r.title):''}<br><code>${esc(r.file)}</code> ${r.chars?`(${r.chars.toLocaleString()}자)`:''} ${r.scenes?`(장면 ${r.scenes}개)`:''}<br>
      <button class="mini" onclick="openPath('${js(r.file)}')">폴더 열기</button> <button class="mini" onclick="showFile('${js(r.file)}')">내용 보기</button>
      ${r.flow?`<br><small>플로우: <code>${esc(r.flow)}</code></small>`:''}
      ${r.meta?`<br><small>유튜브 제목·설명·태그(창작물·AI 고지 포함): <code>${esc(r.meta)}</code></small> <button class="mini" onclick="showFile('${js(r.meta)}')">보기</button>`:''}
      ${r.opt?`<br><small>🎯 알고리즘 최적화(제목 후보·썸네일·설명·태그·첫30초): <code>${esc(r.opt)}</code></small> <button class="mini" onclick="showFile('${js(r.opt)}')">보기</button>`:''}
      ${r.mp3?`<br><small>🎙 나레이션: <code>${esc(r.mp3)}</code> (${((r.duration||0)/60).toFixed(1)}분, 문장 ${r.sentences}) · 자막 <code>${esc(r.srt)}</code></small>`:''}
      ${r.narration?`<br><small>🎙 나레이션: <code>${esc(r.narration)}</code> (${((r.duration||0)/60).toFixed(1)}분) · 자막 <code>${esc(r.srt)}</code></small>`:''}
      ${r.prompts?`<br><small>🖼 이미지 프롬프트: <code>${esc(r.prompts)}</code></small>`:''}
      ${r.images?`<br><small>📁 이미지: <code>${esc(r.images)}</code></small> <button class="mini" onclick="openPath('${js(r.images)}')">폴더 열기</button>`:''}
      ${r.video?`<br><small>🎬 최종 영상: <code>${esc(r.video)}</code></small> <button class="mini" onclick="openPath('${js(r.video)}')">폴더 열기</button>`:''}
      ${(r.thumbnails&&r.thumbnails.length)?`<br><small>🖼 썸네일 ${r.thumbnails.length}장: <code>${esc(r.thumbnails[0])}</code></small> <button class="mini" onclick="openPath('${js(r.thumbnails[0])}')">폴더 열기</button>`:''}
      ${r.assets?`<br><small>자료 폴더: <code>${esc(r.assets)}</code></small> <button class="mini" onclick="openPath('${js(r.assets)}')">폴더 열기</button>`:''}
      ${(j.kind==='script'||j.kind==='mindam')&&r.file?`<br><button class="primary" style="margin-top:8px" onclick="continuePipeline('${js(r.file)}')">🎬 이 대본으로 나레이션 → 이미지 → 영상까지 이어서 만들기</button>`:''}
      ${(r.issues||[]).length?`<div class="hint" style="margin-top:6px">확인: ${r.issues.map(esc).join(' · ')}</div>`:''}
      <div class="hint">${esc(r.cost||'')}</div>`;
    refresh();
  }else if(j.status==='error'){const e=$('pg_err');e.classList.remove('hidden');e.textContent=errorHelp(j.error);}
}
function errorHelp(message){
  const s=String(message||'알 수 없는 오류');
  if(/확장|chat\.deepseek|웹 대기/.test(s))return '딥시크 연결을 확인하세요: 크롬에서 chat.deepseek.com에 로그인하고 [처음 설정]의 확장 상태를 확인한 뒤 다시 실행하세요.\n상세: '+s;
  if(/8765|편집프로그램|연결할 수 없/.test(s))return '편집프로그램이 연결되지 않았습니다. 시작 배치 파일을 다시 실행하고 [처음 설정]의 준비 상태를 확인하세요.\n상세: '+s;
  if(/API_키|api.key|인증|401|authentication/i.test(s))return 'AI 또는 인월드 키를 확인하세요. [처음 설정]에서 해당 키를 저장한 뒤 다시 실행하세요.\n상세: '+s;
  if(/목소리|voice/i.test(s))return '목소리 설정을 확인하세요. [처음 설정]에서 채널별 목소리 ID를 저장한 뒤 다시 실행하세요.\n상세: '+s;
  if(/좌표|prompt|download/.test(s))return '이미지 생성 좌표를 확인하세요. [이미지 생성·좌표]에서 입력창·생성·다운로드 좌표를 저장하세요.\n상세: '+s;
  return '작업이 중단됐습니다. 아래 로그의 마지막 단계와 상세 오류를 확인한 뒤 다시 실행하세요.\n상세: '+s;
}
const js=s=>String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
async function openPath(p){await api('/api/open',{path:p});}
async function showFile(p){const j=await api('/api/file?path='+encodeURIComponent(p));const v=$('pg_preview');v.textContent=j.text;v.classList.remove('hidden');}
function toast(msg,err){const t=document.createElement('div');t.textContent=msg;t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--surface);border:1px solid '+(err?'var(--warn)':'var(--accent)')+';padding:10px 16px;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.25);z-index:9';document.body.appendChild(t);setTimeout(()=>t.remove(),4000);}
setViewMode('simple');goTab('auto');refresh(); setInterval(refreshQueue,2000);
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"대본 만들기 화면: {ADDR}  (종료: 이 창을 닫거나 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(ADDR)).start()
    if QUEUE.data.get("status") == "running" and any(x.get("status") == "pending" for x in QUEUE.data.get("items", [])):
        threading.Timer(8.0, start_queue_worker).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

if __name__ == "__main__":
    main()
