# -*- coding: utf-8 -*-
"""새 주제 추천 — [새 주제] 버튼을 누를 때마다 AI 가 내 채널 분석 결과를 참고해 새 제목을 만든다.

만든 주제는 추천_추가.json 에 쌓아 두고(다음 실행에도 남음), 이미 쓴 주제·내 채널 영상과 겹치는 제목은 제외한다."""
import datetime
import json
import os
import re

from 공통_api import AI

FILE = "추천_추가.json"
def channel_desc(channel):
    import 채널_프로필
    p = 채널_프로필.get(channel)
    return f"{p['대상_시청자']}를 위한 '{p['이름']}' 채널 — {p['설명']} (영상 길이 {p.get('영상_길이', '')})"


def categories(channel):
    import 채널_프로필
    return (채널_프로필.get(channel).get("카테고리") or "").strip() + " 중 하나"


def bench_hits(limit=20):
    """주제뽑기가 저장한 비슷한 채널의 히트 제목."""
    try:
        with open("벤치_히트.json", encoding="utf-8") as f:
            return (json.load(f).get("히트") or [])[:limit]
    except (OSError, ValueError):
        return []


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"person": list(data.get("person") or []), "mindam": list(data.get("mindam") or [])}


def save(data):
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def _parse(text):
    """AI 답변에서 JSON 배열만 뽑는다."""
    text = text.replace("```json", "").replace("```", "")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("추천 결과를 읽지 못했습니다: " + text[:200])
    items = json.loads(text[start:end + 1])
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = re.sub(r"\s+", " ", str(it.get("제목") or it.get("title") or "")).strip()
        if len(title) < 4:
            continue
        out.append({k: str(v).strip() for k, v in it.items() if isinstance(v, (str, int, float))} | {"제목": title})
    return out


def generate(cfg, channel, count, exclude, analysis=None, log=None):
    """새 주제 count 개를 만들어 추천_추가.json 에 추가하고 새 항목만 돌려준다."""
    ai = AI(cfg)
    key = "카테고리" if channel == "person" else "장르"
    hits = ""
    if analysis and analysis.get("top"):
        hits = "\n[내 채널에서 잘 된 영상 (조회수 순)]\n" + "\n".join(f"- {v['title']} ({v['views']:,}회)" for v in analysis["top"][:8])
        if analysis.get("keywords"):
            hits += "\n[잘 되는 키워드] " + ", ".join(k["word"] for k in analysis["keywords"][:10])
        if analysis.get("weak"):
            hits += "\n[반응이 약한 키워드] " + ", ".join(k["word"] for k in analysis["weak"][:6])
    hits_bench = bench_hits(20) if channel == "person" else []
    if hits_bench:
        hits += "\n[비슷한 심리 채널에서 평소보다 몇 배 터진 제목 — 소재·제목 형태를 벤치마킹하되 베끼지 않는다]\n" + "\n".join(
            f"- {v['title']} ({v['channel']} · 평소의 {v['ratio']}배)" for v in hits_bench)
    system = (f"너는 {channel_desc(channel)}의 기획자다. 새 영상 제목 {count}개를 JSON 배열로만 답한다. "
              f'형식: [{{"제목": "...", "{key}": "{categories(channel)}", "한줄": "왜 이 주제가 클릭될지 한 문장"}}]. '
              "설명·표·코드블록 없이 JSON 만 쓴다.\n"
              "규칙: 제목은 15~30자, 궁금증을 남기는 구체적인 문장(‘~하는 진짜 이유’, ‘~하면 생기는 일’, 숫자 활용). "
              "제외 목록과 같은 소재·비슷한 제목은 절대 내지 않는다. 잘 된 영상의 소재·말투를 참고하되 그대로 반복하지 않는다. 서로 다른 소재로 고르게 뽑는다.")
    user = (f"[제외 목록 — 이미 만들었거나 올린 제목]\n" + ("\n".join(f"- {t}" for t in exclude[:120]) or "(없음)") + hits
            + f"\n\n오늘: {datetime.date.today().isoformat()}. 새 제목 {count}개를 만들어라.")
    if log:
        log(f"   새 주제 {count}개 요청 · AI {ai.name}")
    text = ai.ask(system, user)
    items = _parse(text)
    data = load()
    known = {x.get("제목", "") for x in data[channel]} | set(exclude)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    fresh = []
    for it in items:
        if it["제목"] in known:
            continue
        known.add(it["제목"])
        it["생성"] = stamp
        it.setdefault(key, "")
        fresh.append(it)
    data[channel] = fresh + data[channel]
    save(data)
    return fresh[:count]
