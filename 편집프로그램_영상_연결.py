# -*- coding: utf-8 -*-
"""최종 편집에서 이미지와 같은 번호의 KIE MP4가 있으면 그 영상을 사용한다."""
from pathlib import Path


def apply(editor_dir):
    target = Path(editor_dir) / "core" / "renderer.py"
    if not target.is_file():
        return False
    source = target.read_text(encoding="utf-8")
    if 'job.add_log(f"KIE 영상 사용: {clip.name}")' in source:
        return False
    replacements = (
        ('        uniq = list(dict.fromkeys(s.image for s in segments))',
         '        clips = {s.image: s.image.with_suffix(".mp4") for s in segments\n'
         '                 if s.image.with_suffix(".mp4").is_file() and s.image.with_suffix(".mp4").stat().st_size > 0}\n'
         '        uniq = list(dict.fromkeys(s.image for s in segments if s.image not in clips))'),
        ('        simple = not opts.ken_burns and not use_transition',
         '        simple = not opts.ken_burns and not use_transition and not clips'),
        ('                # 이미지를 한 프레임만 디코딩하고 tpad 로 복제(재디코딩 없음) → fps → trim 으로 정확한 길이',
         '                clip = clips.get(s.image)\n'
         '                if clip:\n'
         '                    args += ["-stream_loop", "-1", "-i", str(clip)]\n'
         '                    filters.append(\n'
         '                        f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"\n'
         '                        f"setsar=1,format=yuv420p,fps={fps},trim=duration={dur:.4f},setpts=PTS-STARTPTS[v{i}]"\n'
         '                    )\n'
         '                    labels.append(f"[v{i}]")\n'
         '                    job.add_log(f"KIE 영상 사용: {clip.name}")\n'
         '                    continue\n'
         '                # 이미지를 한 프레임만 디코딩하고 tpad 로 복제(재디코딩 없음) → fps → trim 으로 정확한 길이'),
    )
    updated = source
    for old, new in replacements:
        if old not in updated:
            raise ValueError("편집프로그램 렌더러 구조가 바뀌어 KIE 영상 연결을 적용하지 못했습니다.")
        updated = updated.replace(old, new, 1)
    target.write_text(updated, encoding="utf-8")
    return True
