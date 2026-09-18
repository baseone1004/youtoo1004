# -*- coding: utf-8 -*-
"""채널 프로필 — 채널 이름·설명·시청자·마스코트·썸네일 방식 같은 '내 채널 고유 정보'를 한 곳(채널_프로필.json)에 둔다.

프로그램에는 두 개의 '자리'가 있다 (자리는 제작 방식이 달라 고정):
  person : 정보형 채널 — 주제 → 9구간 나레이션 대본 (기본값: 심리해독소)
  mindam : 이야기형 채널 — 기획 → 챕터 창작 이야기 (기본값: 옛날서재)
어느 자리든 이름·설명·마스코트 등은 화면 [설정 → 채널 프로필]에서 바꿀 수 있고, 지침 파일 안의
{{채널명}} {{채널_설명}} {{대상_시청자}} {{마스코트_설명}} {{마스코트_프롬프트}} {{해시태그}} 자리표시자는 읽을 때 채워진다."""
import copy
import json
import os
import re
import threading

FILE = "채널_프로필.json"
SLOTS = ("person", "mindam")
_lock = threading.Lock()

기본_프로필 = {
    "person": {
        "유형": "정보형",
        "이름": "심리해독소",
        "설명": ("복잡한 사람의 속마음과 관계의 해답을 찾아 주는 정보형 롱폼 채널. "
               "관계 해독(나를 이용하려는 사람 구분법·건강한 손절 기준), 감정 해독(피로감·불안·번아웃을 줄이는 마음 관리), "
               "처세 해독(만만해 보이지 않는 대화법·상처받지 않는 거리 두기)을 다룬다. "
               "예: '왜 그 사람은 나에게 그렇게 말했을까', '좋은 사람인데 만나고 오면 진이 빠지는 이유'"),
        "대상_시청자": "관계·심리·인생 이야기에 관심 있는 40~60대",
        "카테고리": "관계 해독, 감정 해독, 처세 해독",
        "검색어": ["인간관계 심리", "사람 심리 이유", "왜 사람들은", "손절해야 할 사람", "착한 사람 손해",
                 "만만하게 보이는 이유", "말투 심리", "나를 이용하는 사람", "관계 피로", "번아웃 심리",
                 "속마음 심리학", "거리두기 인간관계", "상처받지 않는 법", "감정 소모"],
        "기본_태그": ["심리학", "인간관계", "심리해독", "인간관계피로", "처세술", "감정조절", "속마음", "대화법", "마음치유"],
        "해시태그": "#심리해독소",
        "자료_검색_접미": ["심리학 연구 결과", "연구 논문", "통계 조사"],
        "면책": ("※ 본 영상은 사람과 관계, 심리 현상을 이해하기 위한 정보 제공을 목적으로 제작되었습니다. "
               "특정 개인을 진단하거나 모든 경우에 동일하게 적용하기 위한 내용은 아닙니다."),
        "마스코트": {
            "이름": "해",
            "이미지": "assets/캐릭터/해.png",
            "설명": ("크림색 아기곰, 크고 동그란 안경, 발그레한 볼과 미소, 목에 하늘색 리본과 '해' 글자가 적힌 동그란 배지, "
                   "한 손에 하트 모양 열쇠, 옆에 하트가 새겨진 자물쇠. 굵은 갈색 외곽선의 단순한 2D 캐릭터"),
            "프롬프트": ("the channel mascot Hae exactly matching the uploaded reference image: a cream-colored chibi bear with big round glasses, "
                     "rosy cheeks, a light-blue ribbon collar with a round badge, holding a heart-shaped key next to a small heart padlock, "
                     "same face, same design, consistent character design"),
        },
        "썸네일": {
            "레이아웃": "bottom_two",
            "화풍": ("bright flat 2D chibi sticker illustration for a YouTube thumbnail, thick clean dark outlines, big expressive eyes, "
                   "vivid high-contrast pastel colors, simple background, exaggerated emotion, 16:9 aspect ratio"),
            "구도": ("채널 마스코트를 화면 위쪽·가운데에 크게, 과장된 감정과 상징 하나, 밝고 단순한 배경, "
                   "문구 두 줄이 들어갈 화면 아래쪽 35%는 단순하고 조금 어둡게"),
            "띠_문구": "",
        },
        "지침": {"대본": "정보형_대본지침.txt", "이미지": "이미지지침_정보형.txt"},
        "업로드_폴더": "심리해독소",
        "영상_길이": "20~30분",
    },
    "mindam": {
        "유형": "이야기형",
        "이름": "옛날서재",
        "설명": "조선 시대 배경의 권선징악·반전·귀신·해학 이야기를 1~2시간 나레이션으로 들려주는 야담·민담·옛이야기 채널",
        "대상_시청자": "한국 야담·민담을 즐기는 50~70대",
        "카테고리": "권선징악, 귀신·도깨비, 해학·풍자, 사랑·비극, 역사인물, 미스터리·추리, 가족·성장",
        "검색어": ["야담", "민담", "옛날이야기", "전설 설화", "조선 이야기", "구전 설화", "귀신 이야기 옛날", "권선징악 이야기"],
        "기본_태그": ["야담", "옛날이야기", "민담", "전설", "설화", "조선시대", "옛이야기"],
        "해시태그": "#야담",
        "자료_검색_접미": [],
        "면책": "※ 본 영상은 옛이야기를 바탕으로 한 창작 이야기입니다.",
        "마스코트": {"이름": "", "이미지": "", "설명": "", "프롬프트": ""},
        "썸네일": {
            "레이아웃": "band",
            "화풍": "",
            "구도": ("감정이 터지는 순간 한 컷 — 조선 시대 인물 얼굴 클로즈업, 두 인물의 시선 충돌, 또는 사건의 정점 중 하나. "
                   "인물 최대 2명, 밤이어도 등잔불로 얼굴이 밝게, 문구가 들어갈 화면 아래쪽은 단순하고 조금 어둡게"),
            "띠_문구": "옛이야기",
        },
        "지침": {"대본": "", "이미지": "이미지지침_이야기형.txt"},
        "업로드_폴더": "민담",
        "영상_길이": "1~2시간",
    },
}

레이아웃_이름 = {"bottom_two": "아래 두 줄 (흰색 + 노란색, 검정 테두리)", "band": "하단 띠 + 붓글씨"}

_cache = None


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load(force=False):
    """{person: {...}, mindam: {...}} — 파일이 없거나 항목이 빠져 있으면 기본값으로 채운다."""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return copy.deepcopy(_cache)
        saved = {}
        try:
            with open(FILE, encoding="utf-8") as f:
                saved = json.load(f) or {}
        except (OSError, ValueError):
            saved = {}
        data = {slot: _merge(기본_프로필[slot], saved.get(slot)) for slot in SLOTS}
        for slot in SLOTS:
            data[slot]["유형"] = 기본_프로필[slot]["유형"]      # 자리의 제작 방식은 바꿀 수 없다
        _cache = data
        return copy.deepcopy(data)


def get(slot):
    slot = slot if slot in SLOTS else "person"
    return load()[slot]


def name(slot):
    return get(slot)["이름"] or 기본_프로필[slot]["이름"]


def save(slot, patch):
    """화면에서 바꾼 항목만 덮어쓴다. 저장한 프로필을 돌려준다."""
    global _cache
    if slot not in SLOTS:
        raise ValueError("알 수 없는 채널 자리: " + str(slot))
    data = load()
    clean = {}
    for k, v in (patch or {}).items():
        if k in ("유형",):
            continue
        if k in ("검색어", "기본_태그", "자료_검색_접미"):
            if isinstance(v, str):
                v = [x.strip() for x in re.split(r"[\n,]", v) if x.strip()]
            clean[k] = [str(x).strip() for x in (v or []) if str(x).strip()]
        elif isinstance(v, dict):
            clean[k] = {kk: (vv.strip() if isinstance(vv, str) else vv) for kk, vv in v.items()}
        elif isinstance(v, str):
            clean[k] = v.strip()
        else:
            clean[k] = v
    data[slot] = _merge(data[slot], clean)
    if not data[slot]["이름"].strip():
        data[slot]["이름"] = 기본_프로필[slot]["이름"]
    with _lock:
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FILE)
        _cache = data
    return copy.deepcopy(data[slot])


def placeholders(slot):
    p = get(slot)
    m = p.get("마스코트") or {}
    return {
        "채널명": p["이름"],
        "채널_설명": p["설명"],
        "대상_시청자": p["대상_시청자"],
        "카테고리": p["카테고리"],
        "해시태그": p["해시태그"],
        "마스코트_이름": m.get("이름", ""),
        "마스코트_설명": m.get("설명", ""),
        "마스코트_프롬프트": m.get("프롬프트", ""),
    }


def fill(text, slot):
    """지침 본문의 {{자리표시자}} 를 프로필 값으로 채운다. 모르는 자리표시자는 그대로 둔다."""
    values = placeholders(slot)
    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", lambda m: values.get(m.group(1), m.group(0)), text or "")


def mascot_reference_note(slot):
    """이미지 프롬프트 요청에 붙일 [레퍼런스] 안내. 마스코트가 없는 채널은 빈 문자열."""
    m = get(slot).get("마스코트") or {}
    if not (m.get("이름") and m.get("프롬프트")):
        return ""
    return (f"[레퍼런스] 드롭샷 References 패널에 채널 마스코트 '{m['이름']}'({m.get('설명', '')[:40]}) 이미지가 올라가 있다. "
            "마스코트가 나오는 장면(C형)에는 지침의 레퍼런스 일관성 문구를 그대로 넣고, 사람 장면(D형)과 대상 장면(A형)에는 마스코트를 넣지 않는다.\n")


def summary():
    """화면용: 자리별 이름·유형·마스코트 유무·업로드 폴더."""
    out = {}
    for slot, p in load().items():
        out[slot] = dict(p, 마스코트_있음=bool((p.get("마스코트") or {}).get("이름")))
    return out
