# -*- coding: utf-8 -*-
"""편집프로그램 썸네일에 개인 한글 글꼴, 넓은 줄 간격, 빨간 포인트를 적용한다."""
from pathlib import Path


def apply(editor_dir):
    editor = Path(editor_dir)
    bundled_font = Path(__file__).with_name("assets") / "fonts" / "BlackHanSans-Regular.ttf"
    font_target = editor / "user_fonts" / "BlackHanSans-Regular.ttf"
    font_target.parent.mkdir(parents=True, exist_ok=True)
    font_changed = False
    if bundled_font.is_file() and (not font_target.is_file() or font_target.read_bytes() != bundled_font.read_bytes()):
        font_target.write_bytes(bundled_font.read_bytes())
        font_changed = True
    dohyeon_font = Path(__file__).with_name("assets") / "fonts" / "DoHyeon-Regular.ttf"
    dohyeon_target = editor / "user_fonts" / "DoHyeon-Regular.ttf"
    if dohyeon_font.is_file() and (not dohyeon_target.is_file() or dohyeon_target.read_bytes() != dohyeon_font.read_bytes()):
        dohyeon_target.write_bytes(dohyeon_font.read_bytes())
        font_changed = True
    brush_font = Path(__file__).with_name("assets") / "fonts" / "NanumBrushScript-Regular.ttf"
    brush_target = editor / "user_fonts" / "NanumBrushScript-Regular.ttf"
    if brush_font.is_file() and (not brush_target.is_file() or brush_target.read_bytes() != brush_font.read_bytes()):
        brush_target.write_bytes(brush_font.read_bytes())
        font_changed = True
    nalgae_font = Path(__file__).with_name("assets") / "fonts" / "HakgyoansimNalgaeR.ttf"
    nalgae_target = editor / "user_fonts" / "HakgyoansimNalgaeR.ttf"
    if nalgae_font.is_file() and (not nalgae_target.is_file() or nalgae_target.read_bytes() != nalgae_font.read_bytes()):
        nalgae_target.write_bytes(nalgae_font.read_bytes())
        font_changed = True
    target = editor / "core" / "thumbnail.py"
    source = target.read_text(encoding="utf-8")
    original = source
    source = source.replace("from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps\n",
                            "from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat\n", 1)
    anchor = "def font_file(name: str) -> str:\n"
    if 'in ("hakgyoansim nalgae r", "학교안심 날개 r"):' not in source:
        source = source.replace(anchor, anchor + '    if (name or "").lower() in ("hakgyoansim nalgae r", "학교안심 날개 r"):\n'
                                '        custom = Path(__file__).resolve().parents[1] / "user_fonts" / "HakgyoansimNalgaeR.ttf"\n'
                                '        if custom.is_file():\n            return str(custom)\n', 1)
    custom = '''def font_file(name: str) -> str:
    try:
        from .user_fonts import FONT_DIR, list_fonts
        for f in list_fonts():
            if f.get("name", "").lower() == (name or "").lower() or f.get("label", "").lower() == (name or "").lower():
                return str(FONT_DIR / f["file_name"])
    except Exception:
        pass
'''
    if "from .user_fonts import FONT_DIR, list_fonts" not in source:
        if anchor not in source:
            raise ValueError("썸네일 글꼴 연결 위치를 찾지 못했습니다.")
        source = source.replace(anchor, custom, 1)
    if 'if (name or "").lower() == "black han sans":' not in source:
        source = source.replace(anchor, anchor + '    if (name or "").lower() == "black han sans":\n'
                                '        custom = Path(__file__).resolve().parents[1] / "user_fonts" / "BlackHanSans-Regular.ttf"\n'
                                '        if custom.is_file():\n            return str(custom)\n', 1)
    if 'if (name or "").lower() == "do hyeon":' not in source:
        source = source.replace(anchor, anchor + '    if (name or "").lower() == "do hyeon":\n'
                                '        custom = Path(__file__).resolve().parents[1] / "user_fonts" / "DoHyeon-Regular.ttf"\n'
                                '        if custom.is_file():\n            return str(custom)\n', 1)
    if 'if (name or "").lower() == "nanum brush script":' not in source:
        source = source.replace(anchor, anchor + '    if (name or "").lower() == "nanum brush script":\n'
                                '        custom = Path(__file__).resolve().parents[1] / "user_fonts" / "NanumBrushScript-Regular.ttf"\n'
                                '        if custom.is_file():\n            return str(custom)\n', 1)
    if 'if (name or "").lower() == "malgun gothic bold":' not in source:
        source = source.replace(anchor, anchor + '    if (name or "").lower() == "malgun gothic bold":\n        return r"C:\\Windows\\Fonts\\malgunbd.ttf"\n', 1)
    source = source.replace("    gap = 12\n", "    gap = 30\n", 1)
    bold_draw = '''    # Regular 글꼴도 썸네일에서 힘 있게 보이도록 안쪽 획을 겹쳐 그린다.
    bold_width = 3
    for dx in range(-bold_width, bold_width + 1):
        for dy in range(-bold_width, bold_width + 1):
            if dx * dx + dy * dy <= bold_width * bold_width:
                draw.text((x + dx, y + dy), text, font=font, fill=fill)
    draw.text((x, y), text, font=font, fill=fill)
'''
    source = source.replace(bold_draw, '    draw.text((x, y), text, font=font, fill=fill)\n', 1)
    if "def _thumbnail_lines(top: str, bottom: str)" not in source:
        compose_anchor = "\ndef compose(image: str, out: str = \"\""
        if compose_anchor not in source:
            compose_anchor = "\ndef compose(image: str, out: str"
        helper = '''
def _thumbnail_lines(top: str, bottom: str) -> list[str]:
    """긴 문구를 모바일에서 읽기 쉬운 2~3줄로 단어 단위 분배한다."""
    words = (f"{top} {bottom}").split()
    if not words:
        return []
    count = 3 if len("".join(words)) >= 12 and len(words) >= 3 else min(2, len(words))
    target = max(1, sum(len(w) for w in words) // count)
    lines = []
    current = []
    for word in words:
        current_len = sum(len(w) for w in current)
        if current and len(lines) < count - 1 and current_len + len(word) > target:
            lines.append(" ".join(current)); current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines

'''
        at = source.find(compose_anchor)
        if at < 0:
            raise ValueError("썸네일 문구 배치 위치를 찾지 못했습니다.")
        source = source[:at + 1] + helper + source[at + 1:]
    source = source.replace('    lines = [t for t in (top, bottom) if t and t.strip()]\n',
                            '    lines = _thumbnail_lines(top, bottom)\n', 1)
    mirror_marker = '        left_detail = sum(ImageStat.Stat(edges.crop((0, 0, W // 2, H))).mean)\n'
    if mirror_marker not in source:
        fit_line = '        im = ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(0.5, 0.45))\n'
        mirror_code = '''    # 왼쪽 글자 배치에서는 인물/피사체가 오른쪽에 오도록 자동 보정한다.
    if position == "left":
        edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
        left_detail = sum(ImageStat.Stat(edges.crop((0, 0, W // 2, H))).mean)
        right_detail = sum(ImageStat.Stat(edges.crop((W // 2, 0, W, H))).mean)
        if left_detail > right_detail * 1.12:
            im = ImageOps.mirror(im)
'''
        source = source.replace(fit_line, fit_line + mirror_code, 1)
    brush_marker = '        brush = Image.new("RGBA", (W, H), (0, 0, 0, 0))\n'
    if brush_marker not in source:
        y_anchor = '''    # 참고 썸네일처럼 강한 빨간 포인트 바를 넣어 작은 화면에서도 시선을 끈다.
'''
        brush_code = '''    if box:
        max_tw = max(draw.textlength(t, font=f) for t, f in zip(lines, fonts))
        x0 = 20 if position == "left" else max(20, int((W - max_tw) / 2) - 35)
        x1 = min(W - 20, int(x0 + max_tw + 80))
        y0, y1 = max(8, int(y) - 28), min(H - 8, int(y + block_h) + 28)
        brush = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(brush)
        bd.polygon([(x0, y0 + 16), (x0 + 35, y0), (x1 - 50, y0 + 8), (x1, y0 + 25),
                    (x1 - 18, y1 - 5), (x0 + 22, y1), (x0, y1 - 18)], fill=(8, 6, 5, 205))
        for offset, inset in ((10, 16), (24, 3), (y1 - y0 - 15, 22)):
            bd.line((x0 + inset, y0 + offset, x1 - inset, y0 + offset), fill=(20, 14, 10, 150), width=9)
        im = Image.alpha_composite(im.convert("RGBA"), brush).convert("RGB")
        draw = ImageDraw.Draw(im)
'''
        if y_anchor not in source:
            raise ValueError("썸네일 붓 배경 연결 위치를 찾지 못했습니다.")
        source = source.replace(y_anchor, brush_code + y_anchor, 1)
    old_box = '''        if box:
            pad = 14
            bx = Image.new("RGBA", (int(tw) + pad * 2, h + pad), (0, 0, 0, 170))
            im.paste(bx, (int(x) - pad, int(y) - pad // 2), bx)
            draw = ImageDraw.Draw(im)
'''
    source = source.replace(old_box, "", 1)
    source = source.replace("    outline_w = max(4, size // 18)\n", "    outline_w = max(3, size // 32)\n", 1)
    marker = "    # 참고 썸네일처럼 강한 빨간 포인트 바를 넣어 작은 화면에서도 시선을 끈다.\n"
    if marker not in source:
        before = '''    colors = [top_color, bottom_color] if len(lines) == 2 else [bottom_color]
'''
        accent = '''    # 참고 썸네일처럼 강한 빨간 포인트 바를 넣어 작은 화면에서도 시선을 끈다.
    accent_y = max(18, int(y) - 18)
    draw.rounded_rectangle((margin, accent_y, min(W - margin, margin + 190), accent_y + 12), radius=6, fill=(235, 45, 45))
    colors = [top_color, bottom_color] if len(lines) == 2 else [bottom_color]
'''
        if before not in source:
            raise ValueError("썸네일 색상 연결 위치를 찾지 못했습니다.")
        source = source.replace(before, accent, 1)
    source = source.replace('    colors = [top_color, bottom_color] if len(lines) == 2 else [bottom_color]\n',
                            '    colors = ([top_color, "#FFFFFF", bottom_color] if len(lines) == 3\n'
                            '              else [top_color, bottom_color] if len(lines) == 2 else [bottom_color])\n', 1)
    source = source.replace('    colors = (["#FFE45C", "#FFFFFF", "#FF3B30"] if len(lines) == 3\n'
                            '              else ["#FFFFFF", "#FF3B30"] if len(lines) == 2 else ["#FFE45C"])\n',
                            '    colors = ([top_color, "#FFFFFF", bottom_color] if len(lines) == 3\n'
                            '              else [top_color, bottom_color] if len(lines) == 2 else [bottom_color])\n', 1)
    if source != original:
        target.write_text(source, encoding="utf-8")
        return True
    return font_changed
