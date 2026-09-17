# -*- coding: utf-8 -*-
"""썸네일 문구 합성 — 글자 없는 원본(raw) 위에 채널별 레이아웃으로 문구를 얹는다.

  심리해독소: 이미지 아래쪽에 상단 제목(흰색)·하단 제목(노란색, 더 크게) 두 줄, 상자 없이 굵은 검정 테두리, 화면 폭을 거의 채우는 큰 글씨
  민담·야담:  아래쪽 어두운 띠 위에 큰 붓글씨 자막, 왼쪽 위 채널 태그
글꼴: 심리해독소는 Black Han Sans(굵은 고딕), 민담은 Nanum Brush Script(붓글씨) — 둘 다 assets/fonts/ 에 있다."""
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1280, 720
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
FONT = os.path.join(FONT_DIR, "BlackHanSans-Regular.ttf")
BRUSH = os.path.join(FONT_DIR, "NanumBrushScript-Regular.ttf")
YELLOW, WHITE, RED, GOLD, BLACK = "#FFE45C", "#FFFFFF", "#FF3B30", "#FFD54A", "#000000"


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
    return ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(center, 0.35))


def _shade_bottom(im, start=0.45, strength=0.92):
    grad = Image.new("L", (1, H))
    for y in range(H):
        t = (y / H - start) / (1 - start)
        grad.putpixel((0, y), int(255 * strength * min(1.0, max(0.0, t)) ** 0.8))
    return Image.composite(Image.new("RGB", (W, H), (6, 6, 8)), im, grad.resize((W, H)))


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


def layout_bottom_two(im, top, bottom):
    """E — 이미지 아래 두 줄 (벤치마킹 채널 스타일): 상자 없이 굵은 검정 테두리 글씨. 1줄 흰색(상단 제목), 2줄 노란색·더 크게(하단 제목).
    글씨가 화면 폭을 거의 채우고, 아래쪽만 살짝 어둡게 해서 어떤 그림 위에서도 읽힌다."""
    im = _shade_bottom(im, 0.55, 0.55)
    draw = ImageDraw.Draw(im)
    top, bottom = (top or "").strip(), (bottom or "").strip()
    if not bottom:
        top, bottom = "", top
    rows = []
    if top:
        rows.append([top, _fit(draw, top, 126, W - 90, 64), WHITE])
    rows.append([bottom, _fit(draw, bottom, 150, W - 90, 64), YELLOW])
    stroke = lambda f: max(8, f.size // 9)
    boxes = [draw.textbbox((0, 0), t, font=f, stroke_width=stroke(f)) for t, f, _ in rows]
    heights = [b[3] - b[1] for b in boxes]
    gap = 6
    y = H - 30 - sum(heights) - gap * (len(rows) - 1)
    for (text, f, color), bb, h in zip(rows, boxes, heights):
        x = (W - (bb[2] - bb[0])) / 2 - bb[0]
        _outlined(draw, (x, y - bb[1]), text, f, color, stroke(f))
        y += h + gap
    return im


def compose(image, out, top, bottom, channel="person", layout=None, tag_text=None):
    """원본 이미지 + 문구 → out (1280×720 JPG). 쓴 레이아웃 이름을 돌려준다.
    layout: 'band'(하단 띠+붓글씨) / 'bottom_two'(아래 두 줄). 비우면 채널 자리의 기본(민담=band, 그 외=bottom_two)."""
    layout = (layout or ("band" if channel == "mindam" else "bottom_two")).strip()
    if layout == "band":
        im, layout = layout_band(_load(image, 0.55), top, bottom, tag_text or "옛이야기"), "band"
    else:
        im, layout = layout_bottom_two(_load(image, 0.5), top, bottom), "bottom"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im.save(out, quality=92)
    return layout
