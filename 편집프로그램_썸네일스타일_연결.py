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
    target = editor / "core" / "thumbnail.py"
    source = target.read_text(encoding="utf-8")
    original = source
    anchor = "def font_file(name: str) -> str:\n"
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
