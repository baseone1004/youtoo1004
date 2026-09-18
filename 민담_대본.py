# -*- coding: utf-8 -*-
"""
민담_대본.py — 야담·민담·옛이야기 대본 자동 생성 (기획 한 번 → 챕터별 본문 작성)

흐름: 기획(제목·인트로·인물·반전·팩트시트·챕터 계획) → 챕터별 본문 → 합본·검수 → 파일 저장
지침: 지침/민담/01_기획_지침.txt, 02_챕터_지침.txt (수정하면 그대로 반영)
결과: 대본/민담/<날짜>_<제목>/final.txt · 기획.txt · story_facts.md · thumbnail_brief.md · chapter_N.txt

python 민담_대본.py "제목 또는 장르" [--길이 1|2|3]
"""
import sys, os, re, json, random, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)
from 공통_api import AI, strip_next_teaser

지침_폴더 = os.path.join("지침", "민담")
출력_폴더 = os.path.join("대본", "민담")
사용이름_파일 = os.path.join(지침_폴더, "최근_사용_이름.txt")
한_번에_최대 = 4500          # DeepSeek 출력 한도(8천 토큰)를 넘지 않게 한 번에 쓰는 글자수 상한

# 단계(비트)별 분량 비율(%) — 15단계 이야기 골격 (지침/민담/02_챕터_지침.txt 와 같은 이름)
비트 = [("인트로", 1), ("①", 3), ("②", 4), ("③", 11), ("④", 4), ("⑤", 8), ("⑥", 3), ("⑦", 6), ("⑧", 18),
       ("⑨", 4), ("⑩", 12), ("⑪", 3), ("⑫", 4), ("⑬", 2), ("⑭", 13), ("⑮", 2), ("엔딩", 2)]
비트_이름 = {"①": "첫 장면", "②": "씨앗 한 마디", "③": "일상과 사람들", "④": "사건의 문", "⑤": "망설임", "⑥": "첫 결단", "⑦": "곁사람",
          "⑧": "활약", "⑨": "갈림길", "⑩": "되치기", "⑪": "바닥", "⑫": "긴 밤", "⑬": "다시 일어섬",
          "⑭": "결판", "⑮": "끝 장면", "엔딩": "엔딩"}
# 챕터 분할: (비트, 비중) — 0.5 는 그 비트의 전반/후반
길이 = {
    "1": dict(이름="1시간", 총글자=25000, 챕터=[
        [("①", 1), ("②", 1)], [("③", 1), ("④", 1)], [("⑤", 1), ("⑥", 1), ("⑦", 1), ("⑧", .5)],
        [("⑧", .5), ("⑨", 1), ("⑩", 1)], [("⑪", 1), ("⑫", 1), ("⑬", 1), ("⑭", 1), ("⑮", 1), ("엔딩", 1)]]),
    "2": dict(이름="1시간 30분", 총글자=35000, 챕터=[
        [("①", 1), ("②", 1)], [("③", 1)], [("④", 1), ("⑤", 1), ("⑥", 1)], [("⑦", 1), ("⑧", .5)],
        [("⑧", .5), ("⑨", 1)], [("⑩", 1), ("⑪", 1)], [("⑫", 1), ("⑬", 1), ("⑭", 1), ("⑮", 1), ("엔딩", 1)]]),
    "3": dict(이름="2시간", 총글자=46000, 챕터=[
        [("①", 1), ("②", 1)], [("③", .5)], [("③", .5), ("④", 1)], [("⑤", 1), ("⑥", 1)], [("⑦", 1), ("⑧", .5)],
        [("⑧", .5)], [("⑨", 1), ("⑩", .5)], [("⑩", .5), ("⑪", 1), ("⑫", 1), ("⑬", 1)], [("⑭", 1), ("⑮", 1), ("엔딩", 1)]]),
}
고정_마무리 = ("다음 영상을 빠르게 만나보시려면 좋아요와 구독을 눌러주세요.\n"
           "지금 화면에 나오는 더 재미있는 영상들도 함께 해주세요.\n"
           "그럼 모두 행복한 하루 보내세요. 감사합니다.")
인트로_고정 = "구독과 좋아요는 더 좋은 이야기를 만드는 힘이 됩니다. 그럼 지금부터 이야기를 시작하겠습니다."


def read(name):
    p = os.path.join(지침_폴더, name)
    with open(p, encoding="utf-8-sig") as f:
        return f.read()

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()[:40]

# ── 이름 은행 (name_picker 이식) ─────────────────────────────────
차단 = {"연이", "도화", "분이", "무휼", "막동", "막둥", "삼월", "개똥", "개똥이", "언년", "언년이", "돌쇠", "마당쇠", "강쇠", "갑돌", "갑돌이",
      "갑순", "갑순이", "춘향", "향단", "방자", "길동", "칠복", "봉이", "똘이", "순이", "영이", "덕이", "막쇠", "끝단", "보슬", "곰배"}
편향_어근 = ("곰", "검", "막", "돌", "쇠")
구역 = [("천민 여성", "## 1.", "### 여성"), ("천민 남성", "## 1.", "### 남성"), ("평민 여성", "## 2.", "### 여성"),
      ("평민 남성", "## 2.", "### 남성"), ("중인 남성", "## 3.", None), ("사대부 남성", "## 4.", "### 남성"),
      ("사대부 여성(본명·필요시)", "## 4.", "### 여성"), ("기생 예명", "## 5.", None), ("승려 법명", "## 6.", None)]

def _section(text, h2, h3):
    i = text.find(h2)
    if i < 0:
        return ""
    m = re.search(r"\n## ", text[i + len(h2):])
    block = text[i:i + len(h2) + m.start()] if m else text[i:]
    if h3:
        j = block.find(h3)
        if j < 0:
            return ""
        m2 = re.search(r"\n### ", block[j + len(h3):])
        block = block[j:j + len(h3) + m2.start()] if m2 else block[j:]
    return block

def _names_in(block):
    out = []
    for line in block.split("\n"):
        line = line.strip()
        if not line or line[0] in "#>" or line.startswith("**") or "작명 규칙" in line:
            continue
        line = re.sub(r"^-\s*[^:：]+[:：]", "", line)     # "- 본명 (필요시): ..." 앞부분 제거
        for tok in line.split(","):
            tok = re.sub(r"\(.*?\)", "", tok).strip()
            if re.fullmatch(r"[가-힣]{1,4}", tok):
                out.append(tok)
    return out

def load_used_names():
    if not os.path.exists(사용이름_파일):
        return []
    with open(사용이름_파일, encoding="utf-8") as f:
        return [x.strip() for x in f.read().replace("\n", ",").split(",") if x.strip()]

def pick_names(n=6, seed=None):
    """구역별로 n개씩 뽑아 '[사용 가능한 이름]' 텍스트를 만든다. 최근 40편 안에 쓴 이름은 뺀다."""
    bank = read("name_bank.txt")
    used = set(load_used_names()[-200:])
    rng = random.Random(seed)
    lines = []
    for label, h2, h3 in 구역:
        pool = [x for x in dict.fromkeys(_names_in(_section(bank, h2, h3))) if x not in 차단 and x not in used]
        clean = [x for x in pool if not x.startswith(편향_어근)]; biased = [x for x in pool if x.startswith(편향_어근)]
        rng.shuffle(clean); rng.shuffle(biased)
        picked = (clean + biased)[:n]
        if picked:
            lines.append(f"- {label}: {', '.join(picked)}")
    lines.append("- 사대부 여성 택호·호칭: 아씨, 소저, 부인, 마님 / 안동댁, 청송댁, 해주댁, 나주댁, 동래댁, 광주댁, 전주댁, 진주댁")
    lines.append("- 왕실: 임금·주상·전하 / 중전·마마 / 세자·동궁 (이름을 부르지 않는다). 상궁·나인: 자선, 옥교, 난정, 보연 / 내관: 처선, 득남")
    surnames = ["김", "이", "박", "한", "윤", "서", "황", "명", "방", "도", "구", "추", "표", "봉"]
    rng.shuffle(surnames)
    lines.append(f"- 성씨(주요 인물은 앞쪽 우선, 김·이·박 연속 금지): {', '.join(surnames[:8])} / 조연·단역용 드문 성씨: 견, 빈, 어, 변, 호, 갈, 옥")
    return "\n".join(lines)

def remember_names(plan_text):
    names = []
    block = block_of(plan_text, "등장인물")
    for line in block.split("\n"):
        m = re.match(r"\s*-\s*([^/]+)/", line)
        if m:
            nm = re.sub(r"\(.*?\)", "", m.group(1)).strip().split()[-1] if m.group(1).strip() else ""
            nm = re.sub(r"[^가-힣]", "", nm)
            if 1 <= len(nm) <= 4:
                names.append(nm)
                if len(nm) >= 3:
                    names.append(nm[1:])      # 성씨를 뗀 이름도 같이 기록
    if names:
        with open(사용이름_파일, "a", encoding="utf-8") as f:
            f.write(", ".join(names) + "\n")
    return names

# ── 기획 텍스트 블록 ────────────────────────────────────────────
def block_of(text, name):
    m = re.search(r"^\[" + re.escape(name) + r"\]\s*$(.*?)(?=^\[[^\]\n]+\]\s*$|\Z)", text, flags=re.S | re.M)
    return m.group(1).strip() if m else ""

def chapter_spec(L):
    """길이 설정 → [(챕터번호, 파트번호, 파트수, [(비트,비중)], 목표글자)]
    한 챕터가 한_번에_최대 를 넘으면 비트 단위로 잘라 여러 번 부른다 (큰 비트는 반으로 쪼갬)."""
    pct = dict(비트)
    total = L["총글자"]
    specs = []
    for ci, beats in enumerate(L["챕터"], 1):
        units = []
        for b, w in beats:
            size = pct[b] * w / 100 * total
            k = max(1, -(-int(size) // 한_번에_최대))
            units += [(b, w / k, size / k)] * k
        groups, cur, cur_size = [], [], 0
        for u in units:
            if cur and cur_size + u[2] > 한_번에_최대:
                groups.append(cur); cur, cur_size = [], 0
            cur.append(u); cur_size += u[2]
        groups.append(cur)
        # 마지막 묶음이 너무 작으면 앞 묶음에 합친다 (호출 수 절약)
        while len(groups) > 1 and sum(u[2] for u in groups[-1]) < 1600 and                 sum(u[2] for u in groups[-2] + groups[-1]) <= 한_번에_최대 * 1.15:
            last = groups.pop(); groups[-1] = groups[-1] + last
        for pi, g in enumerate(groups, 1):
            merged = {}
            for b, w, _ in g:
                merged[b] = merged.get(b, 0) + w
            sub = list(merged.items())
            specs.append((ci, pi, len(groups), sub, round(sum(pct[b] * w for b, w in sub) / 100 * total)))
    return specs

def beats_text(beats):
    out = []
    for b, w in beats:
        name = 비트_이름[b] if b != "엔딩" else ""
        out.append(f"{b} {name}".strip() + ("" if w >= 0.99 else " (일부)"))
    return ", ".join(out)

def chapter_plan_text(L):
    lines = [f"영상 길이 {L['이름']} · 전체 목표 약 {L['총글자']:,}자 · 챕터 {len(L['챕터'])}개"]
    pct = dict(비트)
    for ci, beats in enumerate(L["챕터"], 1):
        t = round(sum(pct[b] * w for b, w in beats) / 100 * L["총글자"])
        lines.append(f"챕터 {ci}: {beats_text(beats)} / 목표 약 {t:,}자")
    return "\n".join(lines)

# ── 후처리 ───────────────────────────────────────────────────
def clean_body(t):
    t = t.replace("```", "")
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    keep = []
    for line in t.split("\n"):
        s = line.strip()
        if re.match(r"^(\[.*?\]|【.*?】|챕터\s*\d+|제\s*\d+\s*장|\d+\s*장\s|---+|\*\*\*+|===+|#+\s)", s):
            continue
        keep.append(line)
    t = "\n".join(keep)
    t = re.sub(r"[一-鿿]", "", t)                       # 한자 제거
    t = re.sub(r"[?!？！]+", ".", t)
    t = re.sub(r"(\.{2,}|…+)", ".", t)
    t = re.sub(r"\.\s*\.", ".", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def check(text):
    issues = []
    if text.count("“") != text.count("”") or text.count('"') % 2:
        issues.append("따옴표 짝이 맞지 않는 곳이 있음")
    d = re.findall(r"\d+", text)
    if d:
        issues.append(f"아라비아 숫자 {len(d)}곳 (예: {', '.join(d[:4])})")
    lines = [l for l in text.split("\n") if l.strip()]
    dial = sum(len(l) for l in lines if l.strip().startswith(("“", '"')))
    ratio = dial / max(1, sum(len(l) for l in lines))
    issues.append(f"대사 비중 {ratio*100:.0f}% (목표 35% 이상)")
    seum = len(re.findall(r"(습니다\.\s*[^\n\"“]*습니다\.\s*[^\n\"“]*습니다\.)", text))
    if seum > 5:
        issues.append(f"'~습니다' 세 문장 연속 {seum}건")
    if 고정_마무리.split("\n")[0] not in text:
        issues.append("고정 마무리 멘트 없음 (자동으로 붙임)")
    for w in re.findall(r"[가-힣]+년", text):
        if w.endswith("년") and w not in ("소년", "청년", "노년", "중년", "장년", "유년", "만년", "작년", "매년", "내년", "올해년", "십년", "백년", "천년", "이십년", "삼십년"):
            pass
    return issues

# ── 메인 파이프라인 ───────────────────────────────────────────
설명_고지 = ("※ 이 영상은 전해 내려오는 야담·민담·옛이야기를 바탕으로 새롭게 창작한 이야기입니다. 등장하는 인물·사건·지명은 모두 허구이며 실제와 관련이 없습니다.\n"
         "※ 본 영상의 대본·이미지·음성 제작에는 AI 기술이 사용되었습니다.")
구분선 = "────────────────────"

def youtube_meta(plan, title):
    """기획의 [설명글]·[태그]·[고정댓글] 로 유튜브에 붙여 넣을 설명 텍스트를 만든다 (창작물·AI 고지 포함)."""
    desc = block_of(plan, "설명글") or "옛이야기 한 편, 편안히 들어 보세요."
    tags = [t.strip().lstrip("#") for t in re.split(r"[,、\n]", block_of(plan, "태그")) if t.strip()]
    for must in ("야담", "민담", "옛날이야기", "전설", "설화"):
        if must not in tags:
            tags.append(must)
    hashtags = " ".join("#" + re.sub(r"\s+", "", t) for t in tags[:6])
    body = "\n\n".join([desc.strip(), 구분선, 설명_고지, 구분선, hashtags])
    return (f"[제목]\n{title}\n\n[설명글]\n{body}\n\n[태그]\n{', '.join(tags[:15])}\n\n"
            f"[고정댓글]\n{block_of(plan, '고정댓글')}\n")


def generate(ai, topic, length_key="2", log=print, workdir=None, variation=""):
    L = 길이.get(str(length_key), 길이["2"])
    system_plan = read("01_기획_지침.txt")
    system_ch = read("02_챕터_지침.txt")
    refs = {
        "motif_bank": read("motif_bank.txt"),
        "장르 요소": read("참고_장르요소.txt"),
        "인트로·제목 가이드": read("참고_인트로제목.txt"),
        "참고_말투문체": read("참고_말투문체.txt"),
    }
    names = pick_names(6)
    today = datetime.date.today().isoformat()

    # 1) 기획
    plan_path = None
    if workdir and os.path.exists(os.path.join(workdir, "기획.txt")):
        plan_path = os.path.join(workdir, "기획.txt")
        with open(plan_path, encoding="utf-8") as f:
            plan = f.read()
        log(f"   기획.txt 가 있어 다시 쓰지 않고 이어감")
    else:
        user = (f"[주제]\n{topic}\n\n" + (f"[확정 베리에이션]\n{variation.strip()}\n\n" if variation.strip() else "")
                + f"[챕터 구성]\n{chapter_plan_text(L)}\n\n[사용 가능한 이름]\n{names}\n\n"
                f"[motif_bank]\n{refs['motif_bank']}\n\n[장르 요소]\n{refs['장르 요소']}\n\n[인트로·제목 가이드]\n{refs['인트로·제목 가이드']}")
        log("   1) 기획 (제목·인트로·인물·반전·팩트시트·챕터 계획) ")
        plan = ai.ask(system_plan, user).replace("```", "").strip()
        if "[챕터 계획]" not in plan or "[인트로]" not in plan:
            log("   ! 기획 형식이 어긋나 한 번 더 요청")
            plan = ai.ask(system_plan, user + "\n\n반드시 [제목]부터 [썸네일 브리프]까지 모든 블록을 형식 그대로 출력한다.").replace("```", "").strip()
    title = block_of(plan, "제목").split("\n")[0].strip() or topic
    short = re.sub(r"\s*\|\s*야담.*$", "", title).strip()
    if not workdir:
        workdir = os.path.join(출력_폴더, f"{today}_{safe_name(short)}")
        n = 2
        while os.path.exists(workdir):          # 같은 제목이 이미 있으면 새 폴더 (이어쓰기는 workdir 를 직접 지정)
            workdir = os.path.join(출력_폴더, f"{today}_{safe_name(short)} ({n})"); n += 1
    os.makedirs(workdir, exist_ok=True)
    if not plan_path:
        with open(os.path.join(workdir, "기획.txt"), "w", encoding="utf-8") as f:
            f.write(plan)
        remember_names(plan)
    log(f"   제목: {title}")

    intro = clean_body(block_of(plan, "인트로"))
    if 인트로_고정.split(".")[0] not in intro:
        intro = intro.rstrip() + "\n" + 인트로_고정
    facts = block_of(plan, "팩트시트")
    chars = block_of(plan, "등장인물")
    props = block_of(plan, "핵심 소품")
    theme = block_of(plan, "주제 대사")
    twists = block_of(plan, "반전 6개")
    ch_plan = block_of(plan, "챕터 계획")
    core = block_of(plan, "핵심 설정")
    plan_core = (f"[제목]\n{title}\n\n[핵심 설정]\n{core}\n\n[주제 대사]\n{theme}\n\n[등장인물]\n{chars}\n\n[반전 6개]\n{twists}\n\n"
                 f"[핵심 소품]\n{props}\n\n[팩트시트]\n{facts}\n\n[챕터 계획]\n{ch_plan}")

    # 2) 챕터
    specs = chapter_spec(L)
    bodies = {}          # (챕터, 파트) → 본문
    prev_tail = intro[-300:]
    for ci, pi, n_parts, beats, target in specs:
        fname = os.path.join(workdir, f"chapter_{ci}" + (f"_{pi}" if n_parts > 1 else "") + ".txt")
        if os.path.exists(fname):
            with open(fname, encoding="utf-8") as f:
                bodies[(ci, pi)] = f.read()
            prev_tail = bodies[(ci, pi)][-300:]
            log(f"   챕터 {ci}{'-' + str(pi) if n_parts > 1 else ''}: 이미 있음 ({len(bodies[(ci, pi)]):,}자)")
            continue
        label = f"챕터 {ci}" + (f" ({pi}/{n_parts} 부분)" if n_parts > 1 else "")
        is_last = (ci, pi) == (specs[-1][0], specs[-1][1])
        goal = round(target * 1.1)
        user = (f"[기획]\n{plan_core}\n\n[문체 참고]\n{refs['참고_말투문체']}\n\n"
                f"[직전 챕터 끝부분]\n…{prev_tail}\n\n"
                f"[이번 챕터] {label}\n쓸 비트: {beats_text(beats)}\n목표 글자수: 약 {goal:,}자 (공백 포함). 이보다 적게 쓰지 않는다.\n"
                f"챕터 계획의 '{label.split(' (')[0]}' 항목에 적힌 사건을 순서대로 모두 다룬다."
                + (" 이번이 마지막이다. ⑮ 마지막 이미지와 엔딩(주제 대사 변형 + 고정 마무리 멘트)으로 끝낸다." if is_last else
                   " 이 챕터에 배정된 비트에서 멈춘다. 다음 비트의 내용을 미리 쓰지 않는다.")
                + "\n본문만 출력한다.")
        log(f"   {label} · {beats_text(beats)} · 목표 {goal:,}자 ")
        body = clean_body(ai.ask(system_ch, user))
        attempts = 0
        while len(body) < target * 0.90 and attempts < 3:
            attempts += 1
            log(f"   ! {len(body):,}자로 짧아 목표의 90%까지 장면을 보충 ({attempts}/3)")
            more = ai.ask(system_ch, user + f"\n\n[지금까지 쓴 이번 챕터]\n{body}\n\n위 본문의 뒤에 이어질 장면을 약 {target - len(body):,}자 더 쓴다. "
                          "앞 내용을 반복하거나 요약하지 않는다. 이어지는 본문만 출력한다.")
            addition = clean_body(more)
            if not addition:
                break
            body = body + "\n\n" + addition
        bodies[(ci, pi)] = body
        with open(fname, "w", encoding="utf-8") as f:
            f.write(body)
        prev_tail = body[-300:]
        log(f"   ✓ {label} {len(body):,}자 (누적 {sum(len(b) for b in bodies.values()):,}자)")

    # 3) 합본
    script = intro + "\n\n" + "\n\n".join(bodies[k] for k in sorted(bodies))
    if 고정_마무리.split("\n")[0] not in script:
        script = script.rstrip() + "\n\n" + 고정_마무리
    script = strip_next_teaser(clean_body(script))
    # 마무리 멘트 뒤에 붙은 군더더기 제거
    i = script.find(고정_마무리.split("\n")[0])
    if i >= 0:
        script = script[:i] + 고정_마무리
    with open(os.path.join(workdir, "final.txt"), "w", encoding="utf-8") as f:
        f.write(script)
    with open(os.path.join(workdir, "story_facts.md"), "w", encoding="utf-8") as f:
        f.write(f"# story_facts.md\n\n{title}\n\n## 등장인물\n{chars}\n\n## 핵심 설정\n{core}\n\n## 반전\n{twists}\n\n## 핵심 소품\n{props}\n\n## 팩트시트\n{facts}\n")
    with open(os.path.join(workdir, "thumbnail_brief.md"), "w", encoding="utf-8") as f:
        f.write(f"# thumbnail_brief.md\n\n{block_of(plan, '썸네일 브리프')}\n")
    with open(os.path.join(workdir, "유튜브_설명.txt"), "w", encoding="utf-8") as f:
        f.write(youtube_meta(plan, title))
    issues = check(script)
    log(f"   ✓ 합본 {len(script):,}자 (목표 {L['총글자']:,}자) → {os.path.join(workdir, 'final.txt')}")
    for m in issues:
        log(f"   · 확인: {m}")
    return dict(title=title, dir=workdir, final=os.path.join(workdir, "final.txt"), chars=len(script), issues=issues,
                meta=os.path.join(workdir, "유튜브_설명.txt"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    length = "2"
    for i, a in enumerate(sys.argv):
        if a == "--길이" and i + 1 < len(sys.argv):
            length = sys.argv[i + 1]
    if not args:
        raise SystemExit('사용법: python 민담_대본.py "제목 또는 장르" [--길이 1|2|3]')
    with open("설정.json", encoding="utf-8") as f:
        cfg = json.load(f)
    ai = AI(cfg)
    print(f"AI: {ai.name} ({ai.model}) · {길이[length]['이름']}\n▶ {args[0]}")
    r = generate(ai, args[0], length)
    print(f"\n완료: {r['final']} ({r['chars']:,}자)")
    print("비용:", ai.cost_text())

if __name__ == "__main__":
    main()
