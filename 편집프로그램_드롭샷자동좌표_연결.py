# -*- coding: utf-8 -*-
"""프롬프트·다운로드는 저장 좌표를 쓰고 생성 버튼만 기존 자동 탐색을 유지한다."""
import re
from pathlib import Path


def apply(editor: Path) -> bool:
    target = editor / "core" / "imagegen.py"
    text = target.read_text(encoding="utf-8")
    original = text
    text = re.sub(
        r"(?s)        prompt_xy = self\._find_prompt_input\(s\) or tuple\(s\.prompt_xy\)\n"
        r".*?        pyautogui\.click\(\*prompt_xy\)\n",
        "        pyautogui.click(*s.prompt_xy)\n", text, count=1)
    text = re.sub(
        r"(?s)            download_xy = self\._find_download_button\(s\) or tuple\(s\.download_xy\)\n"
        r".*?            pyautogui\.click\(\*download_xy\)\n",
        "            pyautogui.click(*s.download_xy)\n", text, count=1)
    if text != original:
        target.write_text(text, encoding="utf-8")
        return True
    return False
