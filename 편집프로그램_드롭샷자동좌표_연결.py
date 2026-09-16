# -*- coding: utf-8 -*-
"""편집프로그램에 드롭샷 입력창·생성·다운로드 자동 탐색을 연결한다."""
from pathlib import Path


HELPERS = r'''
    def _dropshot_controls(self, keyword: str):
        try:
            from pywinauto import Desktop
            wins = [w for w in Desktop(backend="uia").windows()
                    if (keyword.lower() in (w.window_text() or "").lower()
                        or "드롭샷 ai" in (w.window_text() or "").lower())]
            if not wins:
                return None, []
            return wins[0], wins[0].descendants()
        except Exception:
            return None, []

    @staticmethod
    def _center(control):
        r = control.rectangle()
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def _find_prompt_input(self, s: GenSettings):
        _win, controls = self._dropshot_controls(s.window_keyword)
        for control in controls:
            name = (control.window_text() or "").strip()
            if control.element_info.control_type == "Button" and name.startswith("이미지 생성하기"):
                r = control.rectangle()
                return ((r.left + r.right) // 2, max(0, r.top - 169))
        return None

    def _find_download_button(self, s: GenSettings):
        _win, controls = self._dropshot_controls(s.window_keyword)
        sw, sh = pyautogui.size()
        images = []
        for control in controls:
            name = (control.window_text() or "").strip().lower()
            r = control.rectangle()
            if control.element_info.control_type == "Image" and name.startswith("generated image") \
                    and r.bottom > 160 and r.top < sh and r.right > 0 and r.left < sw:
                images.append((max(0, min(r.bottom, sh) - max(r.top, 0)), control))
        if not images:
            return None
        image = max(images, key=lambda item: item[0])[1]
        r = image.rectangle()
        pyautogui.moveTo((r.left + r.right) // 2, min(r.bottom - 18, sh - 5), duration=0.2)
        time.sleep(0.35)
        _win, hovered = self._dropshot_controls(s.window_keyword)
        for control in hovered:
            name = (control.window_text() or "").strip().lower()
            rr = control.rectangle()
            if control.element_info.control_type == "Button" and "다운로드" in name \
                    and rr.top >= r.top and rr.bottom <= r.bottom + 50:
                return self._center(control)
        return (r.left + int(r.width() * 0.59), min(r.bottom - 28, sh - 5))

'''


def apply(editor: Path) -> bool:
    target = editor / "core" / "imagegen.py"
    text = target.read_text(encoding="utf-8")
    changed = False
    if "def _dropshot_controls(self, keyword: str):" not in text:
        anchor = "    # -- 화면 변화 감지 (결과 그림 영역)\n"
        if anchor not in text:
            raise ValueError("드롭샷 자동 좌표 삽입 위치를 찾지 못했습니다.")
        text = text.replace(anchor, HELPERS + anchor, 1)
        changed = True
    old_prompt = "        pyperclip.copy(prompt)\n        pyautogui.click(*s.prompt_xy)\n"
    if old_prompt in text:
        new_prompt = ('        pyperclip.copy(prompt)\n'
                      '        prompt_xy = self._find_prompt_input(s) or tuple(s.prompt_xy)\n'
                      '        if not prompt_xy or prompt_xy == (0, 0):\n'
                      '            raise RuntimeError("드롭샷 프롬프트 입력창을 자동으로 찾지 못했습니다.")\n'
                      '        if getattr(self, "_last_prompt_found", None) != prompt_xy:\n'
                      '            self.state.add(f"프롬프트 입력창 자동 감지 → X={prompt_xy[0]}, Y={prompt_xy[1]}")\n'
                      '            self._last_prompt_found = prompt_xy\n'
                      '        pyautogui.click(*prompt_xy)\n')
        text = text.replace(old_prompt, new_prompt, 1); changed = True
    old_download = "            pyautogui.click(*s.download_xy)\n"
    if old_download in text:
        new_download = ('            download_xy = self._find_download_button(s) or tuple(s.download_xy)\n'
                        '            if not download_xy or download_xy == (0, 0):\n'
                        '                raise RuntimeError("드롭샷 다운로드 버튼을 자동으로 찾지 못했습니다.")\n'
                        '            if getattr(self, "_last_download_found", None) != download_xy:\n'
                        '                self.state.add(f"다운로드 버튼 자동 감지 → X={download_xy[0]}, Y={download_xy[1]}")\n'
                        '                self._last_download_found = download_xy\n'
                        '            pyautogui.click(*download_xy)\n')
        text = text.replace(old_download, new_download, 1); changed = True
    if changed:
        target.write_text(text, encoding="utf-8")
    req = editor / "requirements.txt"
    if req.exists() and "pywinauto" not in req.read_text(encoding="utf-8").lower():
        req.write_text(req.read_text(encoding="utf-8").rstrip() + "\npywinauto>=0.6.9\n", encoding="utf-8")
    return changed
