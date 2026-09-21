# -*- coding: utf-8 -*-
"""(예전) 모든 MP4 왼쪽 위에 'AI로 제작되었습니다' 문구를 그리던 패치 — 지금은 그 문구를 지운다.
AI 제작 고지는 유튜브 업로드 설정(변형/합성 콘텐츠 표시)과 설명란 안내문으로 하고, 영상 화면에는 넣지 않는다."""
from pathlib import Path
import re


def apply(editor_dir):
    target = Path(editor_dir) / "core" / "renderer.py"
    if not target.is_file():
        return False
    source = target.read_text(encoding="utf-8")
    if "# youtoo-ai-production-notice" not in source:
        return False
    pattern = r"        # youtoo-ai-production-notice\n.*?        vlabel = \"\[vnotice\]\"\n"
    updated = re.sub(pattern, "", source, count=1, flags=re.S)
    if updated == source:
        return False
    target.write_text(updated, encoding="utf-8")
    return True
