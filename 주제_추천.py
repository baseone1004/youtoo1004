# -*- coding: utf-8 -*-
"""새 주제 추천 — [새 주제] 버튼을 누를 때마다 AI 가 내 채널 분석 결과를 참고해 새 제목을 만든다.

만든 주제는 추천_추가.json 에 쌓아 두고(다음 실행에도 남음), 이미 쓴 주제·내 채널 영상과 겹치는 제목은 제외한다."""
import datetime
import json
import os
import re

from 공통_api import AI

FILE = "추천_추가.json"
CHANNEL_DESC = {
    "person": "50~70대 시청자를 위한 '심리해독소' 채널 — 인간관계·심리·노후·가족·돈에 대한 '왜 그런가'를 20~30분 나레이션으로 풀어 주는 영상",
    "mindam": "50~70대 시청자를 위한 민담·야담·옛이야기 채널 — 조선 시대 배경의 권선징악·반전·귀신·해학 이야기를 1~2시간 나레이션으로 들려주는 영상",
}
CATEGORIES = {"person": "관계, 나이, 돈, 가족, 심리, 건강, 노후, 말투 중 하나", "mindam": "권선징악, 귀신·도깨비, 해학·풍자, 사랑·비극, 역사인물, 미스터리·추리, 가족·성장 중 하나"}


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
    system = (f"너는 {CHANNEL_DESC[channel]}의 기획자다. 새 영상 제목 {count}개를 JSON 배열로만 답한다. "
              f'형식: [{{"제목": "...", "{key}": "{CATEGORIES[channel]}", "한줄": "왜 이 주제가 클릭될지 한 문장"}}]. '
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
