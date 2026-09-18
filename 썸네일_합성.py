# -*- coding: utf-8 -*-
"""썸네일 문구 합성 — 글자 없는 원본(raw) 위에 채널 브랜드(색·배지·낙관·질감)로 문구를 얹는다.

레이아웃 (채널 프로필 → 썸네일 → 레이아웃):
  navy_mint    아래 두 줄 (흰색 + 강조색), 왼쪽 위 배지, 아래 강조선          ← 정보형 기본
  cream_card   왼쪽 바탕색 카드에 남색·코랄 글씨(테두리 없음), 오른쪽 인물
  coral_ribbon 아래 두 줄 (흰색 + 강조색, 검정 테두리), 왼쪽 위 사선 리본
  bottom_two   아래 두 줄 (흰색 + 노란색, 검정 테두리) — 예전 방식
  hanji_seal   세피아 사진, 아래 한지 띠에 먹글씨, 오른쪽 위 붉은 낙관          ← 이야기형 기본
  ink_gold     먹빛 띠 + 금색 붓글씨 + 금색 두루마리 테두리 + 낙관
  scroll       왼쪽 한지에 세로쓰기 붓글씨, 오른쪽 사진
  band         먹빛 띠 + 붓글씨, 왼쪽 위 빨간 태그 — 예전 방식
브랜드 값(주색·강조색·바탕색·배지 문구·사진 톤)은 compose(..., brand=) 로 받는다. 글꼴은 assets/fonts/."""
import os
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

W, H = 1280, 720
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
FONT = os.path.join(FONT_DIR, "BlackHanSans-Regular.ttf")
BRUSH = os.path.join(FONT_DIR, "NanumBrushScript-Regular.ttf")
LABEL = os.path.join(FONT_DIR, "DoHyeon-Regular.ttf")
YELLOW, WHITE, RED, GOLD, BLACK = "#FFE45C", "#FFFFFF", "#FF3B30", "#FFD54A", "#000000"

레이아웃_이름 = {
    "navy_mint": "아래 두 줄 · 배지 · 강조선 (정보형 추천)",
    "cream_card": "왼쪽 카드 + 오른쪽 인물 (차분한 책 느낌)",
    "coral_ribbon": "아래 두 줄 · 사선 리본",
    "bottom_two": "아래 두 줄 · 노란색 (예전 방식)",
    "hanji_seal": "한지 띠 + 먹글씨 + 낙관 (이야기형 추천)",
    "ink_gold": "먹빛 띠 + 금 글씨 + 두루마리 테두리 + 낙관",
    "scroll": "왼쪽 세로쓰기 두루마리",
    "band": "먹빛 띠 + 붓글씨 + 빨간 태그 (예전 방식)",
}
기본_브랜드 = {
    "person": {"주색": "#0F1B3D", "강조색": "#4BE3C4", "바탕색": "#FFF4DC", "보조색": "#E6543C", "배지": "", "사진_톤": "warm"},
    "mindam": {"주색": "#1C120A", "강조색": "#FFD54A", "바탕색": "#F3E9D2", "보조색": "#B3261E", "배지": "", "사진_톤": "sepia"},
}


# ── 기본 도구 ─────────────────────────────────────────
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


def _hex(c, default=(0, 0, 0)):
    c = (c or "").strip().lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else default
    except ValueError:
        return default


def _outlined(draw, xy, text, font, fill, stroke=None, stroke_fill=BLACK):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke if stroke is not None else max(6, font.size // 12), stroke_fill=stroke_fill)


def _load(image, center):
    im = Image.open(image).convert("RGB")
    return ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(center, 0.35))


def _shade_bottom(im, start=0.45, strength=0.92, color=(6, 6, 8)):
    grad = Image.new("L", (1, H))
    for y in range(H):
        t = (y / H - start) / (1 - start)
        grad.putpixel((0, y), int(255 * strength * min(1.0, max(0.0, t)) ** 0.8))
    return Image.composite(Image.new("RGB", (W, H), color), im, grad.resize((W, H)))


def _brush_text(draw, xy, text, font, fill, stroke_fill=BLACK):
    """붓글씨는 획이 가늘어서 굵은 테두리 위에 획을 몇 번 겹쳐 그려 두껍게 만든다."""
    x, y = xy
    sw = max(10, font.size // 9)
    draw.text((x, y), text, font=font, fill=stroke_fill, stroke_width=sw, stroke_fill=stroke_fill)
    for dx, dy in ((0, 0), (2, 0), (0, 2), (2, 2), (-2, 0), (0, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=fill)


def _pill(draw, xy, text, font, bg, fg, pad=(22, 10), radius=18):
    x, y = xy
    tw = draw.textlength(text, font=font)
    draw.rounded_rectangle((x, y, x + tw + pad[0] * 2, y + font.size + pad[1] * 2), radius=radius, fill=bg)
    draw.text((x + pad[0], y + pad[1] - 2), text, font=font, fill=fg)


def _hanji(w, h, base=(243, 233, 210)):
    """한지 느낌 — 옅은 섬유 노이즈."""
    rnd = random.Random(7)
    im = Image.new("RGB", (w, h), base)
    px = im.load()
    for _ in range(w * h // 18):
        x, y = rnd.randrange(w), rnd.randrange(h)
        d = rnd.randint(-14, 8)
        r, g, b = px[x, y]
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)))
    return im.filter(ImageFilter.GaussianBlur(0.6))


def _tone(im, tone):
    """사진 톤: warm(살짝 따뜻하게) / sepia(옛 사진 + 비네트) / none."""
    if tone == "sepia":
        g = ImageOps.grayscale(im)
        s = ImageOps.colorize(g, (28, 18, 10), (250, 236, 205), mid=(150, 110, 70))
        out = ImageEnhance.Contrast(Image.blend(im, s, 0.7)).enhance(1.12)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).ellipse((-W * 0.25, -H * 0.35, W * 1.25, H * 1.35), fill=255)
        return Image.composite(out, Image.new("RGB", (W, H), (18, 12, 8)), mask.filter(ImageFilter.GaussianBlur(160)))
    if tone == "warm":
        r, g, b = im.split()
        r = r.point(lambda v: min(255, int(v * 1.04 + 4)))
        b = b.point(lambda v: int(v * 0.96))
        return ImageEnhance.Color(Image.merge("RGB", (r, g, b))).enhance(1.06)
    return im


def _seal(target, xy, text, size=118, rotate=-6, color=(179, 38, 30)):
    """붉은 낙관 도장 (두 글자씩 두 줄)."""
    s = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(s)
    dark = tuple(max(0, c - 55) for c in color)
    d.rounded_rectangle((3, 3, size - 3, size - 3), radius=10, fill=color + (235,), outline=dark + (255,), width=3)
    f = _font(int(size * 0.44), BRUSH)
    lines = [text[:2], text[2:4]] if len(text) > 2 else [text]
    y = size * (0.12 if len(lines) > 1 else 0.28)
    for ln in lines:
        d.text(((size - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(255, 240, 225, 255))
        y += size * 0.4
    s = s.rotate(rotate, expand=True, resample=Image.BICUBIC)
    target.paste(s, xy, s)


def _two_lines(draw, top, bottom, color_top, color_bottom, stroke_fill, max_w=W - 120):
    """아래 두 줄: 1줄 흰색, 2줄 강조색·더 크게. 글자는 화면 폭에 맞춰 줄인다."""
    top, bottom = (top or "").strip(), (bottom or "").strip()
    if not bottom:
        top, bottom = "", top
    f2 = _fit(draw, bottom, 150, max_w, 56)
    f1 = _fit(draw, top, 112, max_w, 48) if top else None
    y2 = H - 40 - f2.size
    if f1:
        y1 = y2 - f1.size - 6
        _outlined(draw, ((W - draw.textlength(top, font=f1)) / 2, y1), top, f1, color_top, max(8, f1.size // 9), stroke_fill)
    _outlined(draw, ((W - draw.textlength(bottom, font=f2)) / 2, y2), bottom, f2, color_bottom, max(8, f2.size // 9), stroke_fill)


# ── 정보형 레이아웃 ─────────────────────────────────────
def layout_navy_mint(im, top, bottom, b):
    main, accent = _hex(b["주색"]), _hex(b["강조색"])
    im = _shade_bottom(_tone(im, b["사진_톤"]), 0.52, 0.9, main)
    d = ImageDraw.Draw(im)
    if b["배지"]:
        _pill(d, (36, 34), b["배지"], _font(34, LABEL), accent, main)
    _two_lines(d, top, bottom, WHITE, b["강조색"], main)
    d.rectangle((0, H - 10, W, H), fill=accent)
    return im


def layout_cream_card(im_path_or_im, top, bottom, b):
    main, accent, paper, second = _hex(b["주색"]), _hex(b["강조색"]), _hex(b["바탕색"]), _hex(b["보조색"])
    src = im_path_or_im if isinstance(im_path_or_im, Image.Image) else Image.open(im_path_or_im).convert("RGB")
    img = ImageOps.fit(src, (int(W * 0.58), H), Image.LANCZOS, centering=(0.62, 0.35))
    if b["사진_톤"] == "warm":
        r, g, bl = img.split()
        img = ImageEnhance.Color(Image.merge("RGB", (r.point(lambda v: min(255, int(v * 1.04 + 4))), g, bl.point(lambda v: int(v * 0.96))))).enhance(1.06)
    canvas = Image.new("RGB", (W, H), paper)
    canvas.paste(img, (W - img.width, 0))
    grad = Image.new("L", (160, 1))
    for x in range(160):
        grad.putpixel((x, 0), int(255 * (x / 160)))
    grad = grad.resize((160, H))
    x0 = W - img.width
    canvas.paste(Image.composite(canvas.crop((x0, 0, x0 + 160, H)), Image.new("RGB", (160, H), paper), grad), (x0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, 14, H), fill=main)
    if b["배지"]:
        _pill(d, (44, 40), b["배지"], _font(32, LABEL), main, paper)
    max_w = int(W * 0.46)
    top, bottom = (top or "").strip(), (bottom or "").strip()
    if not bottom:
        top, bottom = "", top
    f1 = _fit(d, top, 92, max_w, 44) if top else None
    lines = _wrap(bottom, 11)
    f2 = min((_fit(d, ln, 124 if len(lines) == 1 else 104, max_w, 48) for ln in lines), key=lambda f: f.size)
    total = (f1.size + 16 if f1 else 0) + len(lines) * (f2.size + 6)
    y = max(150, (H - total) // 2)
    if f1:
        d.text((48, y), top, font=f1, fill=main)
        y += f1.size + 16
    for ln in lines:
        d.text((48, y), ln, font=f2, fill=second)
        y += f2.size + 6
    d.rectangle((48, y + 16, 48 + 220, y + 28), fill=accent)
    return canvas


def layout_coral_ribbon(im, top, bottom, b):
    accent = _hex(b["강조색"])
    im = _shade_bottom(_tone(im, b["사진_톤"]), 0.5, 0.88)
    d = ImageDraw.Draw(im)
    _two_lines(d, top, bottom, WHITE, b["강조색"], BLACK)
    if b["배지"]:
        rib = Image.new("RGBA", (420, 70), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rib)
        rd.rectangle((0, 0, 420, 70), fill=accent + (255,))
        rf = _font(34, LABEL)
        rd.text(((420 - rd.textlength(b["배지"], font=rf)) / 2, 14), b["배지"], font=rf, fill=WHITE)
        rib = rib.rotate(-45, expand=True, resample=Image.BICUBIC)
        im.paste(rib, (-150, -110), rib)
    return im


def layout_bottom_two(im, top, bottom, b=None):
    """예전 방식 — 아래 두 줄 (흰색 + 노란색, 검정 테두리)."""
    im = _shade_bottom(im, 0.55, 0.55)
    d = ImageDraw.Draw(im)
    _two_lines(d, top, bottom, WHITE, YELLOW, BLACK)
    return im


# ── 이야기형 레이아웃 ─────────────────────────────────────
def _band_texts(draw, top, bottom, band_top, color_top, color_bottom, stroke_fill=None, max_w=W - 140):
    f1 = _fit(draw, top, 96, max_w, 40, BRUSH) if top else None
    lines = _wrap(bottom, 12)
    f2 = min((_fit(draw, ln, 136 if len(lines) == 1 else 108, max_w, 48, BRUSH) for ln in lines), key=lambda f: f.size)
    y = band_top + 14
    if f1:
        x = (W - draw.textlength(top, font=f1)) / 2
        if stroke_fill is None:
            draw.text((x, y), top, font=f1, fill=color_top)
        else:
            _brush_text(draw, (x, y), top, f1, color_top, stroke_fill)
        y += f1.size - 6
    for ln in lines:
        x = (W - draw.textlength(ln, font=f2)) / 2
        if stroke_fill is None:
            for dx, dy in ((0, 0), (2, 0), (0, 2), (2, 2)):
                draw.text((x + dx, y + dy), ln, font=f2, fill=color_bottom)
        else:
            _brush_text(draw, (x, y), ln, f2, color_bottom, stroke_fill)
        y += int(f2.size * 0.96)


def layout_hanji_seal(im, top, bottom, b):
    paper, ink, seal = _hex(b["바탕색"]), _hex(b["주색"]), _hex(b["보조색"])
    im = _tone(im, b["사진_톤"])
    band_h = 250 if len(_wrap(bottom, 12)) > 1 or not top else 230
    mask = Image.new("L", (W, band_h), 255)
    md = ImageDraw.Draw(mask)
    for x in range(0, W, 6):
        md.rectangle((x, 0, x + 6, 10 + (x * 7 % 13)), fill=0)
    im.paste(_hanji(W, band_h, paper), (0, H - band_h), mask.filter(ImageFilter.GaussianBlur(3)))
    d = ImageDraw.Draw(im)
    _band_texts(d, top, bottom, H - band_h, tuple(min(255, c + 20) for c in ink), ink)
    if b["배지"]:
        _seal(im, (W - 150, 34), b["배지"], 118, -6, seal)
    return im


def layout_ink_gold(im, top, bottom, b):
    gold, seal = _hex(b["강조색"]), _hex(b["보조색"])
    im = _shade_bottom(_tone(im, b["사진_톤"]), 0.5, 0.92, (14, 10, 8))
    d = ImageDraw.Draw(im)
    frame = (217, 163, 58)
    d.rectangle((14, 14, W - 15, H - 15), outline=frame, width=5)
    d.rectangle((26, 26, W - 27, H - 27), outline=frame, width=2)
    _band_texts(d, top, bottom, H - 250, WHITE, gold, BLACK, W - 160)
    if b["배지"]:
        _seal(im, (W - 140, 44), b["배지"], 104, 0, seal)
    return im


def layout_scroll(im, top, bottom, b):
    paper, ink, seal = _hex(b["바탕색"]), _hex(b["주색"]), _hex(b["보조색"])
    im = _tone(im, b["사진_톤"])
    strip_w = 300
    im.paste(_hanji(strip_w, H, paper), (0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((strip_w - 6, 0, strip_w, H), fill=(120, 90, 50))
    text = ((top or "") + " " + (bottom or "")).replace(" ", "")
    half = len(text) // 2 + len(text) % 2
    cols = [text[:half], text[half:]] if len(text) > 8 else [text]
    size = 78 if max(len(c) for c in cols) <= 9 else 62
    f = _font(size, BRUSH)
    step = int(size * 0.85)
    for ci, col in enumerate(cols):
        x = strip_w - 120 - ci * 120 if len(cols) > 1 else strip_w // 2 - size // 2
        y = 40
        for ch in col:
            d.text((x, y), ch, font=f, fill=ink)
            y += step
    if b["배지"]:
        _seal(im, (strip_w - 140, H - 150), b["배지"], 100, 0, seal)
    return im


def layout_band(im, top, bottom, tag_text="옛이야기", b=None):
    """예전 방식 — 하단 띠 + 붓글씨 + 왼쪽 위 빨간 태그."""
    im = _shade_bottom(im)
    draw = ImageDraw.Draw(im)
    if tag_text:
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


LAYOUTS = {
    "navy_mint": (0.6, layout_navy_mint), "cream_card": (0.5, layout_cream_card), "coral_ribbon": (0.6, layout_coral_ribbon),
    "bottom_two": (0.5, layout_bottom_two), "hanji_seal": (0.5, layout_hanji_seal), "ink_gold": (0.5, layout_ink_gold),
    "scroll": (0.7, layout_scroll), "band": (0.55, layout_band),
}


def compose(image, out, top, bottom, channel="person", layout=None, tag_text=None, brand=None):
    """원본 이미지 + 문구 → out (1280×720 JPG). 쓴 레이아웃 이름을 돌려준다.
    layout: 레이아웃_이름 의 키. 비우면 채널 자리의 기본(정보형 navy_mint, 이야기형 hanji_seal).
    brand: {주색, 강조색, 바탕색, 보조색, 배지, 사진_톤} — 비우면 기본_브랜드."""
    slot = "mindam" if channel == "mindam" else "person"
    layout = (layout or ("hanji_seal" if slot == "mindam" else "navy_mint")).strip()
    if layout not in LAYOUTS:
        layout = "hanji_seal" if slot == "mindam" else "navy_mint"
    b = dict(기본_브랜드[slot], **{k: v for k, v in (brand or {}).items() if v not in (None, "")})
    center, fn = LAYOUTS[layout]
    if layout == "band":
        im = fn(_load(image, center), top, bottom, tag_text if tag_text is not None else "옛이야기", b)
    elif layout == "cream_card":
        im = fn(image, top, bottom, b)
    else:
        im = fn(_load(image, center), top, bottom, b)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im.save(out, quality=92)
    return "bottom" if layout == "bottom_two" else layout
