# -*- coding: utf-8 -*-
"""썸네일 문구 합성 — 글자 없는 원본(raw) 위에 채널별 레이아웃으로 문구를 얹는다.

  A 키워드 강조형 (심리해독소 기본): 흰 글씨 두 줄 → 마지막 핵심어만 크고 노랗게, 검정 테두리, 그림은 밝게 유지 (채널 기존 썸네일 스타일)
  C 숫자 배지형   (문구에 "3가지" 같은 숫자가 있으면): 빨간 원 배지 + 문구
  B 하단 띠형     (민담·야담): 아래쪽 어두운 띠 위에 큰 자막, 왼쪽 위 채널 태그
글꼴: 심리해독소는 Black Han Sans(굵은 고딕), 민담은 Nanum Brush Script(붓글씨) — 둘 다 assets/fonts/ 에 있다."""
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

W, H = 1280, 720
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
FONT = os.path.join(FONT_DIR, "BlackHanSans-Regular.ttf")
BRUSH = os.path.join(FONT_DIR, "NanumBrushScript-Regular.ttf")
YELLOW, WHITE, RED, GOLD, BLACK = "#FFE45C", "#FFFFFF", "#FF3B30", "#FFD54A", "#000000"
ATTACH = {"진짜", "가장", "그", "이", "숨은", "놀라운", "무서운", "결정적", "진정한", "최고의", "마지막", "단", "첫"}
NUMBER = re.compile(r"(\d+)\s*(가지|개|번|년|살|초|분|일|명|배|단계|시간)")


def _font(size, path=FONT):
    return ImageFont.truetype(path, max(20, int(size)))


def _fit(draw, text, size, max_w, min_size=48, path=FONT):
    """글자가 max_w 를 넘지 않는 가장 큰 크기의 글꼴."""
    size = int(size)
    while size > min_size and draw.textlength(text, font=_font(size, path)) > max_w:
        size -= 4
    return _font(size, path)


def _wrap(text, max_chars):
    """단어 단위로 최대 max_chars 글자씩 두 줄까지 나눈다."""
    words = text.split()
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best, best_diff = None, 10 ** 9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if diff < best_diff:
            best, best_diff = [a, b], diff
    return best


def _outlined(draw, xy, text, font, fill, stroke=None):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke if stroke is not None else max(6, font.size // 12), stroke_fill=BLACK)


def _load(image, center):
    im = Image.open(image).convert("RGB")
    return ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(center, 0.45))


def _subject_on_left(im):
    edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
    left = sum(ImageStat.Stat(edges.crop((0, 0, W // 2, H))).mean)
    right = sum(ImageStat.Stat(edges.crop((W // 2, 0, W, H))).mean)
    return left > right * 1.12


def _shade_left(im, width=0.62, strength=0.9):
    grad = Image.new("L", (W, 1))
    for x in range(W):
        t = x / (W * width)
        grad.putpixel((x, 0), int(255 * strength * max(0.0, 1 - t ** 1.6)) if t < 1 else 0)
    return Image.composite(Image.new("RGB", (W, H), (8, 8, 10)), im, grad.resize((W, H)))


def _shade_bottom(im, start=0.45, strength=0.92):
    grad = Image.new("L", (1, H))
    for y in range(H):
        t = (y / H - start) / (1 - start)
        grad.putpixel((0, y), int(255 * strength * min(1.0, max(0.0, t)) ** 0.8))
    return Image.composite(Image.new("RGB", (W, H), (6, 6, 8)), im, grad.resize((W, H)))


def _tiers(top, bottom):
    """[(문구, 역할)] — 앞말(lead) / 핵심어(key) / 마무리(tail)."""
    top, bottom = (top or "").strip(), (bottom or "").strip()
    if not bottom:
        top, bottom = "", top
    words = bottom.split()
    tail = ""
    if len(words) >= 2:
        tail = words[-1]                      # 마지막 낱말(습관·이유·비밀…)이 강조어
        if len(words) >= 3 and (len(tail) <= 1 or words[-2] in ATTACH):
            tail = " ".join(words[-2:])       # '진짜 이유', '그 정체' 처럼 꾸밈말은 붙여서 강조
        bottom = bottom[: -len(tail)].strip()
    tiers = []
    if top:
        tiers.append((top, "lead"))
    tiers.append((bottom, "key"))
    if tail:
        tiers.append((tail, "tail"))
    return tiers


def layout_keyword(im, top, bottom, badge=""):
    """A (badge 가 있으면 C)"""
    if _subject_on_left(im):
        im = ImageOps.mirror(im)
    im = _shade_left(im, 0.58, 0.45)          # 그림은 밝게 두고 글자 뒤만 살짝 어둡게
    draw = ImageDraw.Draw(im)
    x, max_w = 88, int(W * 0.56)              # 글자를 왼쪽 끝에 붙이지 않고 살짝 안쪽에
    y = 64
    if badge:
        cx, cy, r = 204, 176, 112
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline=WHITE, width=9)
        bf = _fit(draw, badge, 104, r * 2 - 36, 56)
        bw, bh = draw.textlength(badge, font=bf), bf.size
        draw.text((cx - bw / 2, cy - bh * 0.62), badge, font=bf, fill=WHITE)
        y = 318
    # 모든 줄의 글꼴을 먼저 정하고, 세로가 넘치면 전체를 같은 비율로 줄인다.
    tiers = _tiers(top, bottom)
    has_tail = any(role == "tail" for _, role in tiers)
    rows = []
    for text, role in tiers:
        # 마지막 핵심어(tail)만 크고 노랗게, 나머지는 흰색. tail 이 없으면 key 를 강조한다.
        strong = role == "tail" or (role == "key" and not has_tail)
        lines = _wrap(text, 8) if strong else [text]
        base = (150 if len(lines) == 1 else 128) if strong else 92
        if badge:
            base = int(base * 0.85)
        for ln in lines:
            rows.append([ln, strong, _fit(draw, ln, base, max_w)])
    avail = H - y - 48
    block = sum(f.size * 1.1 for _, _, f in rows) + 10 * (len(rows) - 1)
    if block > avail:
        scale = max(0.5, avail / block)
        for row in rows:
            row[2] = _font(row[2].size * scale)
    for ln, strong, f in rows:
        _outlined(draw, (x - 4 if strong else x, y), ln, f, YELLOW if strong else WHITE, max(7, f.size // 10))
        y += int(f.size * 1.1) + 10
    return im


def _brush_text(draw, xy, text, font, fill):
    """붓글씨는 획이 가늘어서 굵은 검정 테두리 위에 획을 몇 번 겹쳐 그려 두껍게 만든다."""
    x, y = xy
    sw = max(10, font.size // 9)
    draw.text((x, y), text, font=font, fill=BLACK, stroke_width=sw, stroke_fill=BLACK)
    for dx, dy in ((0, 0), (2, 0), (0, 2), (2, 2), (-2, 0), (0, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=fill)


def layout_band(im, top, bottom, tag_text="옛이야기"):
    """B — 하단 띠 + 붓글씨"""
    im = _shade_bottom(im)
    draw = ImageDraw.Draw(im)
    tf = _font(40)
    tw = draw.textlength(tag_text, font=tf)
    draw.rounded_rectangle((36, 34, 36 + tw + 44, 34 + tf.size + 20), radius=14, fill=RED)
    draw.text((58, 40), tag_text, font=tf, fill=WHITE)
    lines = []
    if top:
        lines.append((top, WHITE, 120))
    wrapped = _wrap(bottom, 10)
    for ln in wrapped:
        lines.append((ln, GOLD, 176 if len(wrapped) == 1 else 148))
    fonts = [_fit(draw, t, s, W - 110, 64, BRUSH) for t, _, s in lines]
    y = H - 56 - sum(int(f.size * 0.98) for f in fonts)
    for (t, color, _), f in zip(lines, fonts):
        lw = draw.textlength(t, font=f)
        _brush_text(draw, ((W - lw) / 2, y), t, f, color)
        y += int(f.size * 0.98)
    return im


def pick_layout(top, bottom, channel):
    if channel == "mindam":
        return "band", ""
    m = NUMBER.search(top or "") or NUMBER.search(bottom or "")
    if m:
        return "badge", m.group(0).replace(" ", "")
    return "keyword", ""


def compose(image, out, top, bottom, channel="person"):
    """원본 이미지 + 문구 → out (1280×720 JPG). 쓴 레이아웃 이름을 돌려준다."""
    layout, badge = pick_layout(top, bottom, channel)
    if layout == "band":
        im = layout_band(_load(image, 0.55), top, bottom)
    else:
        if badge:
            strip = lambda s: re.sub(r"\s{2,}", " ", NUMBER.sub("", s or "", count=1)).strip()
            top, bottom = strip(top), strip(bottom)
            if not bottom:
                top, bottom = "", top or badge
        im = layout_keyword(_load(image, 0.75), top, bottom, badge)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im.save(out, quality=92)
    return layout
