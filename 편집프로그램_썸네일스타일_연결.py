# -*- coding: utf-8 -*-
"""편집프로그램 썸네일에 개인 한글 글꼴, 넓은 줄 간격, 빨간 포인트를 적용한다."""
from pathlib import Path


def apply(editor_dir):
    target = Path(editor_dir) / "core" / "thumbnail.py"
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
    source = source.replace("    gap = 12\n", "    gap = 30\n", 1)
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
    if source != original:
        target.write_text(source, encoding="utf-8")
        return True
    return False
