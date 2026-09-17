# -*- coding: utf-8 -*-
"""
최적화.py — 완성 대본 → 유튜브 알고리즘용 메타데이터 (제목 후보·썸네일 문구·설명글·태그·고정댓글·첫 30초 점검·업로드 시각)

  지침: 지침/알고리즘_최적화_지침.txt
  트렌드: 트렌드.json (심리해독소) / 민담_트렌드.json (민담) — 주제뽑기가 저장한 "요즘 터지는 단어"
  결과: 대본 옆에 *_유튜브최적화.txt (심리해독소) / 유튜브_최적화.txt (민담)

python 최적화.py "대본 파일" [--채널 person|mindam]
"""
import sys, os, re, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)

지침_파일 = os.path.join("지침", "알고리즘_최적화_지침.txt")
def 채널_설명(channel):
    """채널 프로필에서 최적화 프롬프트용 채널 소개를 만든다."""
    import 채널_프로필
    p = 채널_프로필.get(channel)
    tail = " 제목 끝에 ' | 야담 옛날이야기 민담 전설 설화'." if channel == "mindam" else ""
    return (f"채널 '{p['이름']}' — {p['설명']} 시청자: {p['대상_시청자']}. 영상 길이 {p.get('영상_길이', '')}. "
            f"기본 태그: {', '.join(p.get('기본_태그') or [])}.{tail}")


def load_trends(channel):
    name = "민담_트렌드.json" if channel == "mindam" else "트렌드.json"
    try:
        with open(name, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("단어") if isinstance(d, dict) else d
        return [t if isinstance(t, str) else t[0] for t in (items or [])][:25]
    except Exception:
        return []


def load_bench_hits(channel, limit=15):
    if channel == "mindam":
        return []
    try:
        with open("벤치_히트.json", encoding="utf-8") as f:
            return (json.load(f).get("히트") or [])[:limit]
    except Exception:
        return []


def block_of(text, name):
    m = re.search(r"^\[" + re.escape(name) + r"\]\s*$(.*?)(?=^\[[^\]\n]+\]\s*$|\Z)", text, flags=re.S | re.M)
    return m.group(1).strip() if m else ""


# ── 로컬 제목 점수 (AI 점수와 별개로 형식 검사) ────────────────────────
def score_title(t):
    core = re.sub(r"\s*\|.*$", "", t).strip()
    n = len(core)
    s = 50
    s += 15 if 24 <= n <= 42 else (-10 if n > 50 else -5)
    if re.search(r"\d|하나|둘|셋|세 가지|[일이삼사오육칠팔구십]\s*(가지|년|살|번)", core): s += 8      # 숫자·기간
    if re.search(r"이유|정체|비밀|벌어진 일|알고 보니|그런데|었는데|는데\.\.|\.\.$|\?$", core): s += 12   # 궁금증 갭
    if re.search(r"나이 들수록|사람|며느리|과부|아씨|머슴|부모|자식|친구|부부", core): s += 6            # 시청자 지목/인물
    if re.search(r"죽|살해|피|시체", core): s -= 8
    if re.search(r"^(오늘은|여러분|안녕)", core): s -= 15
    return max(0, min(100, s))


def parse_titles(text):
    out = []
    for line in block_of(text, "제목 후보").splitlines():
        m = re.match(r"\s*(\d+)\s*[.)]\s*(.+?)\s*(?:\(점수\s*(\d+)\))?\s*(?:[—-]\s*(.*))?$", line)
        if m:
            title = m.group(2).strip()
            title = re.sub(r"\s*\(점수\s*\d+\)\s*$", "", title).strip()
            out.append(dict(no=int(m.group(1)), title=title, ai=int(m.group(3) or 0), local=score_title(title), why=(m.group(4) or "").strip()))
    return out


def optimize(ai, channel, script_text, extra="", log=print):
    """대본 전문(또는 앞부분)과 기획 정보를 넣어 최적화 메타를 받는다. 반환: (텍스트, 제목목록)"""
    with open(지침_파일, encoding="utf-8-sig") as f:
        system = f.read()
    trends = load_trends(channel)
    bench = load_bench_hits(channel)
    body = script_text.strip()
    head = body[:2500]
    tail = body[-1200:] if len(body) > 4000 else ""
    mid = body[len(body) // 2: len(body) // 2 + 800] if len(body) > 6000 else ""
    user = (f"[채널]\n{채널_설명(channel)}\n\n"
            + (f"[요즘 터지는 단어 — 비슷한 채널 히트 제목에서 자주 나온 말]\n{', '.join(trends)}\n\n" if trends else "")
            + (("[벤치마킹 — 비슷한 심리 채널에서 평소보다 몇 배 터진 제목. 제목의 구조·후킹 방식·썸네일 문구 길이를 참고하되 문장을 베끼지 않는다]\n"
                + "\n".join(f"- {v['title']} ({v['channel']} · {v['ratio']}배)" for v in bench) + "\n\n") if bench else "")
            + (f"[기획·현재 메타]\n{extra.strip()}\n\n" if extra.strip() else "")
            + f"[대본 시작 부분]\n{head}\n\n"
            + (f"[대본 중간 일부]\n{mid}\n\n" if mid else "")
            + (f"[대본 끝 부분]\n{tail}\n\n" if tail else "")
            + f"[대본 전체 글자수] {len(body):,}자\n\n위 대본으로 [출력 형식] 대로 작성한다.")
    log("   알고리즘 최적화 (제목·썸네일·설명·태그·첫 30초 점검) ")
    text = ai.ask(system, user).replace("```", "").strip()
    titles = parse_titles(text)
    if titles:
        best = max(titles, key=lambda x: x["ai"] * 0.6 + x["local"] * 0.4)
        text += f"\n\n[형식 점수]\n" + "\n".join(f"{t['no']}. AI {t['ai']} / 형식 {t['local']} — {t['title']}" for t in titles)
        text += f"\n\n[최종 추천]\n{best['title']}\n"
    return text, titles


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    channel = "mindam"
    for i, a in enumerate(sys.argv):
        if a == "--채널" and i + 1 < len(sys.argv):
            channel = sys.argv[i + 1]
    if not args:
        raise SystemExit('사용법: python 최적화.py "대본 파일" [--채널 person|mindam]')
    from 공통_api import AI
    with open("설정.json", encoding="utf-8") as f:
        cfg = json.load(f)
    ai = AI(cfg)
    with open(args[0], encoding="utf-8-sig") as f:
        raw = f.read()
    body = raw.split("[대본]", 1)[1] if "[대본]" in raw else raw
    body = body.split("===sum===", 1)[0]
    text, _ = optimize(ai, channel, body, extra=raw[:1500] if "[대본]" in raw else "")
    out = (os.path.join(os.path.dirname(args[0]), "유튜브_최적화.txt") if os.path.basename(args[0]) == "final.txt"
           else re.sub(r"\.txt$", "", args[0]) + "_유튜브최적화.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text); print("\n저장:", out); print("비용:", ai.cost_text())


if __name__ == "__main__":
    main()
