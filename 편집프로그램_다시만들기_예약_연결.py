# -*- coding: utf-8 -*-
"""이미지 생성 중에 [다시 만들기]를 누르면 거절하지 않고 예약해 두었다가, 남은 장면을 다 만든 뒤 그 장면을 새로 만든다.
같은 생성 실행 안에서 처리하므로 제작 파이프라인은 예약된 장면까지 끝난 뒤에야 다음 단계(영상 변환·렌더)로 넘어간다."""
import time
from pathlib import Path


def _replace_once(text, old, new, what):
    if new in text:
        return text, False
    if old not in text:
        raise ValueError(f"편집프로그램에서 {what}을(를) 찾지 못했습니다.")
    return text.replace(old, new, 1), True


def apply(editor_dir):
    editor = Path(editor_dir)
    changed = False

    # ── core/imagegen.py: 예약 목록 + 생성 루프 끝에서 예약 장면 처리
    gen_file = editor / "core" / "imagegen.py"
    if not gen_file.is_file():
        return False
    text = gen_file.read_text(encoding="utf-8")
    text, c = _replace_once(
        text,
        "    files: dict[int, str] = field(default_factory=dict)\n",
        "    files: dict[int, str] = field(default_factory=dict)\n"
        "    queued: list[int] = field(default_factory=list)      # 생성 중에 예약된 다시 만들기 (남은 장면을 다 만든 뒤 처리)\n",
        "생성 상태(GenState)의 files 항목"); changed |= c
    text, c = _replace_once(
        text,
        '"error": self.error, "files": self.files}',
        '"error": self.error, "files": self.files, "queued": list(self.queued)}',
        "생성 상태 to_dict"); changed |= c
    text, c = _replace_once(
        text,
        "        self.state = GenState()\n        self._pause.clear(); self._stop.clear()\n",
        "        self.state = GenState(); self.settings = s\n        self._pause.clear(); self._stop.clear()\n",
        "Runner.start"); changed |= c
    text, c = _replace_once(
        text,
        "    def stop(self) -> None:\n",
        '''    def queue_regen(self, no: int) -> bool:
        """생성 중에 눌린 다시 만들기: 남은 장면을 다 만든 뒤 이 장면을 새로 만든다."""
        st = self.state
        if st.status not in ("running", "paused") or no in st.queued:
            return False
        st.queued.append(no); st.add(f"{no:03d} 다시 만들기 예약 (남은 장면을 다 만든 뒤)")
        return True

    def stop(self) -> None:
''',
        "Runner.stop"); changed |= c
    text, c = _replace_once(
        text,
        '''            for sc in scenes:
                if self._stop.is_set():
                    break
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.3)
                st.current = sc.no
                exists = [p for p in out.glob(f"{sc.no:03d}.*") if p.suffix.lower() in IMAGE_EXTS]
                if s.skip_existing and exists:
''',
        '''            by_no = {x.no: x for x in parse_prompts(s.prompts_file)}      # 예약된 다시 만들기는 이번 범위 밖 장면일 수 있다
            todo = list(scenes); regen = False
            while todo or st.queued:
                if self._stop.is_set():
                    break
                if todo:
                    sc = todo.pop(0); regen = False
                else:                                                    # 남은 장면이 없으면 예약된 다시 만들기 차례
                    no = st.queued.pop(0); sc = by_no.get(no); regen = True
                    if sc is None:
                        st.add(f"{no:03d} 프롬프트가 없어 다시 만들기 건너뜀"); continue
                    old = out / "이전"
                    for p in out.glob(f"{sc.no:03d}.*"):
                        if p.suffix.lower() in IMAGE_EXTS:
                            old.mkdir(exist_ok=True); p.rename(old / f"{p.stem}_{int(time.time())}{p.suffix}")
                    if sc.no in st.done:
                        st.done.remove(sc.no)
                    st.add(f"{sc.no:03d} 예약된 다시 만들기 시작")
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.3)
                st.current = sc.no
                exists = [p for p in out.glob(f"{sc.no:03d}.*") if p.suffix.lower() in IMAGE_EXTS]
                if s.skip_existing and exists and not regen:
''',
        "이미지 생성 루프"); changed |= c
    if changed:
        gen_file.write_text(text, encoding="utf-8")

    # ── app.py: 생성 중이면 400 대신 예약
    app_file = editor / "app.py"
    text = app_file.read_text(encoding="utf-8")
    text, c = _replace_once(
        text,
        '''    st = imagegen.runner.state
    if st.status in ("running", "paused"):
        raise HTTPException(400, "지금 생성 중입니다. 끝나거나 중단한 뒤 다시 누르세요.")
    out = Path(req.output_dir)
''',
        '''    st = imagegen.runner.state
    if st.status in ("running", "paused"):                       # 생성 중 → 남은 장면을 다 만든 뒤 처리하도록 예약
        running = getattr(imagegen.runner, "settings", None)
        if running and Path(running.output_dir).resolve() != Path(req.output_dir).resolve():
            raise HTTPException(400, "지금 다른 폴더의 이미지를 만드는 중입니다. 끝난 뒤 다시 누르세요.")
        if not imagegen.runner.queue_regen(req.scene):
            raise HTTPException(400, f"{req.scene:03d}번은 이미 다시 만들기가 예약되어 있습니다.")
        return {"ok": True, "queued": True, "moved": []}
    out = Path(req.output_dir)
''',
        "다시 만들기(/api/gen/regen) 처리"); changed |= c
    if c:
        app_file.write_text(text, encoding="utf-8")
    return changed
