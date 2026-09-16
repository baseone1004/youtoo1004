# -*- coding: utf-8 -*-
"""모든 MP4 왼쪽 위에 AI 제작 안내 문구를 표시한다."""
from pathlib import Path
import re


def apply(editor_dir):
    target = Path(editor_dir) / "core" / "renderer.py"
    if not target.is_file():
        return False
    source = target.read_text(encoding="utf-8")
    marker = "# youtoo-ai-production-notice"
    notice_block = r'''        # youtoo-ai-production-notice
        notice = ("drawtext=fontfile='C\\:/Windows/Fonts/malgunbd.ttf':"
                  "text='이 영상은 AI로 제작되었습니다':x=30:y=30:fontsize=32:"
                  "fontcolor=white:borderw=2:bordercolor=black")
        filters.append(f"{vlabel}{notice}[vnotice]")
        vlabel = "[vnotice]"'''
    if marker in source:
        updated = re.sub(r"        # youtoo-ai-production-notice\n.*?        vlabel = \"\[vnotice\]\"", lambda _m: notice_block, source, count=1, flags=re.S)
        if updated == source:
            return False
        target.write_text(updated, encoding="utf-8")
        return True
    old = '        filters.append(f"{vlabel}format=yuv420p[vout]")'
    new = notice_block + '\n        filters.append(f"{vlabel}format=yuv420p[vout]")'
    if old not in source:
        raise ValueError("편집프로그램 렌더러 구조가 바뀌어 AI 제작 표시를 적용하지 못했습니다.")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True
