# -*- coding: utf-8 -*-
"""
대본생성.py — 사람의 이유 · 오늘 올릴 영상의 대본을 만든다

  대본만들기.bat  → 계획.json 에서 오늘 날짜까지의 주제 중 아직 대본이 없는 것을 하루 2편까지 생성
  python 대본생성.py --전부   → 계획에 있는 14편 전부
  python 대본생성.py --테스트 → 2,000자 짜리 짧은 대본 1편 (키·설정 확인용)

결과: 대본/YYYY-MM-DD_N편_제목.txt  (DINO 7.5 형식 그대로: [제목]…[대본] ===sum===)
설정: 설정.json 의 AI / API_키 / 대본_글자수 / 하루_대본_편수
"""
import sys, os, re, json, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)
from 공통_api import AI, web_search, format_sources, fix_script_sentences, find_issues, strip_next_teaser

지침_파일 = os.path.join("지침", "사람의이유_대본지침.txt")
구간_이름 = ["질문", "공감", "첫 번째 이유", "두 번째 이유", "세 번째 이유 또는 반전", "사례 확장", "이해의 순간", "오늘 할 수 있는 한 가지", "여운"]

def load_cfg():
    with open("설정.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("AI", "deepseek"); cfg.setdefault("API_키", ""); cfg.setdefault("모델", "")
    cfg.setdefault("대본_글자수", 7000); cfg.setdefault("하루_대본_편수", 2)     # 7,000자 ≈ 25분 (분당 270자 기준)
    return cfg

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()[:40]

def topic_card(t, target):
    lines = [f"[주제] {t['제목']}",
             f"[카테고리] {t.get('카테고리','')}",
             f"[오프닝 한 줄] {t.get('오프닝','')}",
             "[이 영상에서 다룰 내용]"] + [f"- {p}" for p in t.get("다룰내용", [])]
    if t.get("출처후보"):
        lines += ["[참고할 만한 저서·연구]"] + [f"- {s}" for s in t["출처후보"]]
    lines += [f"[작성 목표] 공백 포함 약 {target:,}자, 9개 구간"]
    return "\n".join(lines)

def split_groups(n_parts):
    """9개 구간을 n_parts 묶음으로 나눈다 → [(시작,끝), ...] (1부터)"""
    per = 9 / n_parts; out, s = [], 1
    for i in range(n_parts):
        e = 9 if i == n_parts - 1 else int(round(per * (i + 1)))
        out.append((s, e)); s = e + 1
    return out

def generate(ai, system, t, target, n_parts):
    card = topic_card(t, target)
    kw = " ".join(t.get("태그", [])[:2])
    src = web_search([t["제목"], f"{kw} 심리학 연구 결과", f"{kw} 연구 논문", f"{kw} 통계 조사"], per_query=5, max_total=8)
    print(f"   참고 자료 {len(src)}건 검색")
    src_text = format_sources(src)
    groups = split_groups(n_parts)
    per_chars = target // n_parts

    # 1) 메타 블록 + 구간 계획 + 첫 묶음
    s, e = groups[0]
    user = (f"{card}\n\n{src_text}\n\n"
            f"먼저 [제목]부터 [고정댓글]까지 출력 형식의 블록을 쓴다.\n"
            f"그다음 '[구간 계획]' 이라는 줄 아래에 9개 구간 각각의 내용을 한 줄씩 쓴다 (구간 이름: {', '.join(구간_이름)}).\n"
            f"그다음 '[대본]' 줄을 쓰고 구간 {s}~{e}만 약 {per_chars:,}자로 쓴다. 구간 {e} 끝에서 멈추고 마지막 줄에 '===계속===' 이라고만 쓴다.\n"
            f"구간 제목이나 번호를 본문에 쓰지 않는다.")
    print(f"   1/{n_parts} 묶음 (구간 {s}~{e}) ", end="", flush=True)
    first = ai.ask(system, user)
    head, plan, body = parse_first(first)
    parts = [body]

    # 2) 이어 쓰기
    for i, (s, e) in enumerate(groups[1:], 2):
        last = e == 9
        tail = "".join(parts)[-700:]
        user = (f"{card}\n\n{src_text}\n\n[구간 계획]\n{plan}\n\n[지금까지 쓴 대본의 마지막 부분]\n…{tail}\n\n"
                f"위에 이어서 구간 {s}~{e}를 약 {per_chars:,}자로 쓴다. 앞 내용을 다시 요약하거나 반복하지 않는다. "
                f"[대본] 같은 블록 제목, 구간 제목, 설명 없이 낭독할 본문만 쓴다.")
        if last:
            user += ("\n마지막 구간을 끝낸 뒤 줄을 바꿔 '===sum===' 을 쓰고, 그 다음 줄에 [상단 제목]과 [하단 제목]의 한글 두 줄을 정확히 포함하는 "
                     "16:9 썸네일 영어 프롬프트 한 문단을 쓴다.")
        else:
            user += f"\n구간 {e} 끝에서 멈추고 마지막 줄에 '===계속===' 이라고만 쓴다."
        print(f"   {i}/{n_parts} 묶음 (구간 {s}~{e}) ", end="", flush=True)
        parts.append(clean_part(ai.ask(system, user)))

    script = "\n\n".join(p.strip() for p in parts)
    if "===sum===" in script:
        body, thumb = script.split("===sum===", 1)
    else:
        body, thumb = script, ""
    body = strip_next_teaser(fix_script_sentences(body)).strip()
    head = compose_description(head)
    full = head.strip() + "\n[대본]\n" + body + "\n\n===sum===\n" + thumb.strip() + "\n"
    return full, body

면책 = ("※ 본 영상은 사람과 관계, 심리 현상을 이해하기 위한 정보 제공을 목적으로 제작되었습니다. 특정 개인을 진단하거나 모든 경우에 동일하게 적용하기 위한 내용은 아닙니다.\n"
       "※ 본 영상의 대본·이미지·음성 제작에는 AI 기술이 사용되었습니다.")
구분선 = "────────────────────"

def blocks_of(head):
    """[제목] … [고정댓글] 머리 부분을 {블록이름: 내용} 으로."""
    out, cur = {}, None
    for line in head.split("\n"):
        m = re.fullmatch(r"\s*\[(제목|상단 제목|하단 제목|설명글|출처|태그|고정댓글)\]\s*", line)
        if m:
            cur = m.group(1); out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}

def compose_description(head):
    """[설명글] 뒤에 출처 목록·면책·해시태그를 붙여 유튜브에 그대로 넣을 설명글로 만든다."""
    b = blocks_of(head)
    if "설명글" not in b:
        return head
    src_lines = []
    for line in b.get("출처", "").split("\n"):
        line = line.strip().lstrip("-•· ").strip()
        if not line:
            continue
        m = re.search(r"(https?://\S+)", line)
        url = m.group(1) if m else ""
        label = line.replace(url, "").strip(" /·-")
        src_lines.append(f"• {label}  \n{url}" if url else f"• {label}")
    tags = re.findall(r"#\S+", b.get("태그", ""))
    parts = [b["설명글"].strip(), "", 구분선, ""]
    if src_lines:
        parts += ["📚 참고 자료 및 출처", ""] + [l + "\n" for l in src_lines]
    parts += [면책, "", 구분선, ""]
    if tags:
        parts.append(" ".join(tags))
    b["설명글"] = "\n".join(parts).strip()
    order = ["제목", "상단 제목", "하단 제목", "설명글", "출처", "태그", "고정댓글"]
    return "\n".join(f"[{k}]\n{b[k]}" for k in order if k in b) + "\n"

def parse_first(text):
    """첫 응답에서 메타 블록 / 구간 계획 / 대본 첫 부분을 나눈다."""
    text = text.replace("```", "")
    i_plan = text.find("[구간 계획]"); i_body = text.find("[대본]")
    if i_body < 0:
        # 형식이 어긋나면 전체를 본문으로 두고 메타는 비워 둔다 (파일에는 남김)
        return "[제목]\n(형식 오류 — 확인 필요)\n", "", clean_part(text)
    head = text[:i_plan if i_plan >= 0 else i_body]
    plan = text[i_plan + len("[구간 계획]"):i_body].strip() if i_plan >= 0 else ""
    body = clean_part(text[i_body + len("[대본]"):])
    return head, plan, body

def clean_part(t):
    t = t.replace("```", "")
    t = re.sub(r"===계속===.*$", "", t, flags=re.S)
    t = re.sub(r"^\s*\[대본\]\s*", "", t)
    t = re.sub(r"^\s*(구간|\d+\s*구간|【.*?】|\[.*?구간.*?\]).*$", "", t, flags=re.M)
    return t.strip()

def main():
    cfg = load_cfg()
    args = sys.argv[1:]
    test = "--테스트" in args
    everything = "--전부" in args
    if not os.path.exists(지침_파일):
        raise SystemExit(f"{지침_파일} 이 없습니다.")
    with open(지침_파일, encoding="utf-8") as f:
        system = f.read()
    if not os.path.exists("계획.json"):
        raise SystemExit("계획.json 이 없습니다. 먼저 실행.bat 으로 주제를 뽑으세요.")
    with open("계획.json", encoding="utf-8") as f:
        plan = json.load(f)

    os.makedirs("대본", exist_ok=True)
    today = datetime.date.today().isoformat()
    todo = []
    for t in plan:
        if t.get("대본파일") and os.path.exists(t["대본파일"]):
            continue
        if everything or test or t["날짜"] <= today:
            todo.append(t)
    if not test and not everything:
        todo = todo[: cfg["하루_대본_편수"]]
    if test:
        todo = todo[:1]
    if not todo:
        print("오늘 만들 대본이 없습니다. (이미 다 만들었거나 계획 날짜가 아직 안 됨)")
        return

    ai = AI(cfg)
    target = 2000 if test else int(cfg["대본_글자수"])
    n_parts = 1 if test else max(1, -(-target // 4500))
    print(f"AI: {ai.name} ({ai.model}) · 목표 {target:,}자 · {len(todo)}편\n")

    for t in todo:
        print(f"▶ {t['날짜']} {t['순번']}편째 · {t['제목']}")
        try:
            full, body = generate(ai, system, t, target, n_parts)
        except SystemExit:
            raise
        except Exception as e:
            print(f"   ✗ 실패: {e}\n"); continue
        name = f"{t['날짜']}_{t['순번']}편_{safe_name(t['제목'])}{'_테스트' if test else ''}.txt"
        path = os.path.join("대본", name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(full)
        t["대본파일"] = path
        print(f"   ✓ 저장: {path}  ({len(body):,}자)")
        for msg in find_issues(body):
            print(f"   · 확인: {msg}")
        print()
        with open("계획.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
    print("비용:", ai.cost_text())

if __name__ == "__main__":
    main()
