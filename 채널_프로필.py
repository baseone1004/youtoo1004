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
LANGUAGES = {
    "ko": {"이름": "한국어", "지시": "자연스러운 한국어"},
    "en": {"이름": "영어", "지시": "natural American English"},
    "ja": {"이름": "일본어", "지시": "natural Japanese"},
    "es": {"이름": "스페인어", "지시": "natural neutral Spanish"},
    "zh": {"이름": "중국어", "지시": "natural Simplified Chinese"},
}
_lock = threading.Lock()

기본_프로필 = {
    "person": {
        "유형": "정보형",
        "언어": "ko",
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
            "프롬프트": ("the channel mascot Hae: a cream-colored chibi bear with big round glasses, "
                     "rosy cheeks, a light-blue ribbon collar with a round badge, holding a heart-shaped key next to a small heart padlock, "
                     "same face, same design, consistent character design"),
        },
        "브랜드": {"주색": "#0F1B3D", "강조색": "#4BE3C4", "바탕색": "#FFF4DC", "보조색": "#E6543C", "배지": "심리해독소", "사진_톤": "warm"},
        "화풍_접미": "warm soft watercolor-like tones with gentle cream highlights, calm cozy lighting, consistent channel look",
        "썸네일": {
            "레이아웃": "jalnan_pop",
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
        "언어": "ko",
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
        "브랜드": {"주색": "#1C120A", "강조색": "#FFD54A", "바탕색": "#F3E9D2", "보조색": "#B3261E", "배지": "옛날서재", "사진_톤": "sepia"},
        "화풍_접미": "soft muted natural colors, gentle warm lamplight or daylight, clean full-frame composition, consistent channel look",
        "썸네일": {
            "레이아웃": "pop_bold",
            "화풍": ("bright clean anime-style 2D illustration for a YouTube thumbnail, large clear expressive eyes, smooth cel shading, cheerful saturated colors, sunny daylight, "
                   "saturated colors (pink, green, red and jade hanbok), warm sunny daylight, expressive emotional faces with large clear eyes, "
                   "Joseon village or hanok background, full-bleed 16:9, no text, no watermark"),
            "구도": ("감정이 터지는 순간 한 컷 — 두 인물이 마주 보거나 한 사람이 손가락으로 가리키며 다그치는 장면, 또는 놀라 입을 가린 얼굴 클로즈업. "
                   "인물 최대 2~3명, 얼굴이 크고 밝게, 표정이 과장될 만큼 또렷하게, 문구가 들어갈 화면 아래쪽 40%는 단순하게"),
            "띠_문구": "옛이야기",
        },
        "지침": {"대본": "", "이미지": "이미지지침_이야기형.txt"},
        "업로드_폴더": "민담",
        "영상_길이": "1~2시간",
    },
}

import 썸네일_합성
레이아웃_이름 = 썸네일_합성.레이아웃_이름
사진_톤_이름 = {"warm": "따뜻하게 (정보형)", "sepia": "세피아 옛 사진 (이야기형)", "none": "원본 그대로"}

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
        옛_지침 = {"사람의이유_대본지침.txt": "정보형_대본지침.txt", "이미지지침_심리해독소.txt": "이미지지침_정보형.txt", "이미지지침_민담.txt": "이미지지침_이야기형.txt"}
        for slot, old in list(saved.items()):                  # 예전 지침 파일 이름이 저장돼 있으면 새 이름으로
            for k, v in list((old.get("지침") or {}).items()) if isinstance(old, dict) else []:
                old["지침"][k] = 옛_지침.get(v, v)
        for slot, old in list(saved.items()):                  # 브랜드 항목이 생기기 전에 저장된 파일: 예전 글자 배치를 새 기본으로
            if isinstance(old, dict) and "브랜드" not in old and (old.get("썸네일") or {}).get("레이아웃") in ("bottom_two", "band"):
                old.setdefault("썸네일", {})["레이아웃"] = 기본_프로필.get(slot, 기본_프로필["person"])["썸네일"]["레이아웃"]
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
        if k == "언어":
            clean[k] = str(v or "ko").lower() if str(v or "ko").lower() in LANGUAGES else "ko"
        elif k in ("검색어", "검색어_일본", "기본_태그", "자료_검색_접미"):
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
        "언어": language_name(slot),
        "채널_설명": p["설명"],
        "대상_시청자": p["대상_시청자"],
        "카테고리": p["카테고리"],
        "해시태그": p["해시태그"],
        "마스코트_이름": m.get("이름", ""),
        "마스코트_설명": m.get("설명", ""),
        "마스코트_프롬프트": m.get("프롬프트", ""),
        "인물_표현_규칙": localize_guideline(인물_표현_규칙(p), slot),
    }


def language_code(slot):
    code = str(get(slot).get("언어") or "ko").lower()
    return code if code in LANGUAGES else "ko"


def language_name(slot):
    return LANGUAGES[language_code(slot)]["이름"]


def language_instruction(slot):
    """파서용 한글 블록명은 보존하고 시청자에게 보이는 내용만 채널 언어로 만들게 한다."""
    code = language_code(slot)
    if code == "ko":
        return ""
    lang = LANGUAGES[code]
    native = " 일본어는 일본 시청자에게 말하듯 자연스러운 です・ます체로 쓴다. 한국식 직역, 과도한 당신 호칭, 번역투 문장 연결을 피한다. 공감하는 도입→일본 일상 사례→근거→부담 없는 실천 순서로 구성한다. 직장·전철·가족·이웃의 사례를 쓰되 성별·국민성 고정관념과 근거 없는 통계는 금지한다. 썸네일 문구는 각 줄 8~14자를 목표로 두 줄, 한 가지 궁금증이나 감정을 담고 대본 내용과 일치시킨다. 색은 가독성 중심의 남색·크림 바탕, 흰색·따뜻한 노랑 글자, 제한적인 주홍 강조를 기본으로 삼는다. 조회수를 보장하거나 모든 일본인의 선호라고 단정하지 않는다." if code == "ja" else ""
    return ("\n\n[출력 언어 — 최우선 규칙]\n"
            f"이 채널의 시청자 언어는 {lang['이름']}이다. 대본 본문, 제목, 설명, 태그, 고정댓글, 화면에 보이는 문구는 모두 {lang['지시']}로 작성한다. "
            "입력 주제가 한국어여도 자연스럽게 현지화한다. 직역투를 피하고 해당 언어권의 호칭·관용 표현·문장부호를 쓴다. "
            "프로그램이 읽는 [제목], [대본], [설명글], [태그], [고정댓글] 같은 대괄호 블록명과 ===001=== 같은 번호 표시는 원래 형식을 그대로 유지한다. "
            "이미지 생성 프롬프트와 고유한 영어 스타일 문구는 영어를 유지한다." + native)


def 인물_표현_규칙(p):
    """사람 장면(D형)의 인물을 어떻게 그릴지. '캐릭터': 마스코트 설명와 같은 디자인 언어의 캐릭터로 / '사람': 현대 한국 성인 그대로."""
    if (p.get("인물_표현") or "캐릭터") == "캐릭터":
        return ("사람 장면의 인물은 사람이 아니라 채널 마스코트 설명와 같은 종류의 캐릭터다: 같은 머리 모양·몸통·팔다리·얼굴 스타일·채색 그대로. "
                "프롬프트에 'a Korean man/woman' 처럼 사람을 먼저 쓰지 말고 'a mascot-like creature with the described head shape, body, limbs and face style (not a human, no human face) dressed as a Korean office worker in his forties, ...' 처럼 캐릭터를 먼저 쓴다. "
                "사람마다 색·머리 모양·옷·소품(안경, 넥타이, 앞치마, 가방, 지팡이)으로 구분하고, 같은 인물이 이어지면 그 특징을 그대로 유지한다. "
                "나이와 역할은 소품과 자세로 보여 준다(직장인은 넥타이와 서류, 어머니는 앞치마, 노년은 흰 머리와 지팡이). "
                "마스코트의 상징 소품(리본·배지·열쇠 등)은 다른 인물에게 주지 않아 마스코트와 헷갈리지 않게 한다. 배경은 현대 한국(사무실·집·카페·버스)이되 캐릭터와 어울리게 단순하게. "
                "장면마다 프롬프트 끝에 'not a human, no human face' 를 한 번 더 적는다. "
                "실존 인물 장면(B형)도 같은 디자인 언어로 그리되 그 사람의 특징(머리 모양·안경·복장)만 소품으로 살린다.")
    return "사십 대에서 육십 대 현대 한국 성인이 나온다."


def fill(text, slot):
    """지침 본문의 {{자리표시자}} 를 프로필 값으로 채운다. 모르는 자리표시자는 그대로 둔다."""
    values = placeholders(slot)
    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", lambda m: values.get(m.group(1), m.group(0)), text or "")


def mascot_reference_note(slot):
    """공통 외형 설명은 이미지 모델과 관계없이 프롬프트에 반복한다."""
    m = get(slot).get("마스코트") or {}
    if not m.get("이름"):
        return ""
    description = (m.get("설명") or m.get("프롬프트") or "").strip()
    if not description:
        return ""
    return (f"[캐릭터 설명] 채널 마스코트 '{m['이름']}': {description}. "
            "마스코트 장면(C형)마다 이 외형·색상·소품을 글로 구체적으로 반복한다. "
            "참조 이미지나 @image 표기를 사용하지 않는다. 대상 장면(A형)에는 마스코트를 넣지 않는다.\n")


def brand(slot):
    """썸네일 합성에 넘길 브랜드 값 (배지가 비어 있으면 채널 이름)."""
    p = get(slot)
    b = dict(p.get("브랜드") or {})
    if language_code(slot) == "ja":
        b = dict({"주색":"#18344A", "강조색":"#FFE08A", "바탕색":"#FFF8ED", "보조색":"#D65745", "사진_톤":"warm", "배지":"心の話"}, **(p.get("브랜드_일본") or {}))
    if not (b.get("배지") or "").strip():
        b["배지"] = p["이름"]
    b["언어"] = language_code(slot)
    if b["언어"] == "ja" and re.search(r"[가-힣]", b.get("배지", "")):
        b["배지"] = p.get("일본어_채널명") or "心の話"
    return b


def style_tail(slot):
    """이미지·썸네일 프롬프트 끝에 붙는 채널 고유 색감 문구."""
    return (get(slot).get("화풍_접미") or "").strip()


def summary():
    """화면용: 자리별 이름·유형·마스코트 유무·업로드 폴더."""
    out = {}
    for slot, p in load().items():
        out[slot] = dict(p, 적용_브랜드=brand(slot), 적용_검색어=benchmark_queries(slot), 마스코트_있음=bool((p.get("마스코트") or {}).get("이름")))
    return out


def localize_guideline(text, slot):
    if language_code(slot) != "ja":
        return text
    for old, new in [("a Korean", "a Japanese"), ("Korean office worker", "Japanese office worker"), ("한국 유튜브", "일본 유튜브"), ("한국인", "일본인"), ("현대 한국", "현대 일본"), ("중국·일본풍은 쓰지 않는다", "주제의 실제 시대·지역을 따르며, 현대 일상은 일본 배경을 쓴다")]:
        text = text.replace(old, new)
    return text + "\n[일본 시청자 현지화 — 기존 한국 중심 예시보다 우선]\n일본 시청자가 일상에서 접하는 관계·직장·가족의 구체적 상황을 쓴다. 현대 장면은 일본의 집·동네·전철·사무실과 자연스러운 복장으로 그린다. 한국어 간판·한복·한국 배경을 자동으로 넣지 않는다. 역사나 해외 사건은 사실에 맞는 실제 지역·시대를 유지한다. 일본인을 획일적 외모나 고정관념으로 표현하지 않는다. 제목·썸네일 문구는 자연스러운 일본어로, 직역이나 한국어 조사·한글을 섞지 않는다. 썸네일은 짧은 핵심 문구 두 줄과 한 가지 감정·상징, 작은 화면에서도 읽기 쉬운 대비를 쓴다. 검증되지 않은 유행·조회수 보장은 주장하지 않는다. 이미지 생성 프롬프트는 영어로 쓰고 이미지 자체에는 글자를 그리지 않는다."


def benchmark_file(slot="person"):
    return "벤치_히트_일본.json" if language_code(slot) == "ja" else "벤치_히트.json"


def benchmark_queries(slot="person"):
    p = get(slot)
    if language_code(slot) == "ja":
        return p.get("검색어_일본") or ["人間関係 心理学", "人の心理 なぜ", "心が疲れる 人間関係", "言葉 表情 本音", "大人の人間関係", "家族 夫婦 心理"]
    return p.get("검색어") or ["인간관계 심리", "사람 심리 이유"]


def benchmark_channels_key(slot="person"):
    return "벤치_채널_추가_일본" if language_code(slot) == "ja" else "벤치_채널_추가"
