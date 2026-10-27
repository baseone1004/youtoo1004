"""FFmpeg fallback renderer used when the optional visual editor is unavailable.

It consumes the same image-to-caption flow, SRT, narration and numbered scene
files as the main production pipeline.  It writes to a temporary MP4 and only
replaces the requested target after FFmpeg exits successfully.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import 나레이션


def _seconds(value: str) -> float:
    h, m, rest = value.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _captions(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    cues = []
    for match in re.finditer(r"(\d{2,}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)", text, re.S):
        start, end = _seconds(match.group(1)), _seconds(match.group(2))
        body = "\n".join(line.strip() for line in match.group(3).splitlines() if line.strip() and not line.strip().isdigit())
        if end <= start or not body:
            continue
        cues.append((start, end, body))
    if not cues:
        raise ValueError("SRT 자막의 시간 정보를 읽지 못했습니다.")
    return cues


def _flow(path: Path, cue_count: int):
    rows = []
    expected_scene = 1
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?\s*", line)
        if not match:
            raise ValueError("플로우.txt 형식이 잘못됐습니다. 이미지번호: 자막번호 범위를 확인하세요.")
        scene, first = int(match[1]), int(match[2])
        last = int(match[3] or first)
        if scene != expected_scene or first < 1 or last < first or last > cue_count:
            raise ValueError("플로우.txt의 장면·자막 연결 번호가 비어 있거나 겹칩니다.")
        rows.append((scene, first, last))
        expected_scene += 1
    if not rows or rows[0][1] != 1 or rows[-1][2] != cue_count:
        raise ValueError("플로우.txt가 SRT 전체 자막을 연결하지 않습니다.")
    for before, after in zip(rows, rows[1:]):
        if before[2] + 1 != after[1]:
            raise ValueError("플로우.txt의 자막 범위에 빠지거나 겹친 번호가 있습니다.")
    return rows


def _filter_path(path: Path) -> str:
    # libavfilter has its own parser (separate from shell quoting).
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
    return value


def _font_for(srt: Path):
    if "/민담/" in str(srt.resolve()).replace("\\", "/"):
        return "Batang", 22, True
    if re.search(r"[ぁ-んァ-ン]", srt.read_text(encoding="utf-8-sig")):
        return "Yu Gothic", 22, True
    return "Malgun Gothic", 22, True


def render(srt, flow, images_dir, narration, output, *, job=None, ken_burns=True,
           width=1920, height=1080, fps=30, crf=18, preset="medium"):
    """Create a caption-burned MP4 with a static/animated image per flow range."""
    srt, flow, images_dir = Path(srt), Path(flow), Path(images_dir)
    narration, output = Path(narration), Path(output)
    for item in (srt, flow, narration):
        if not item.is_file():
            raise FileNotFoundError(f"필수 제작 파일이 없습니다: {item.name}")
    if width < 320 or height < 180 or fps < 1 or fps > 60:
        raise ValueError("렌더 크기 또는 프레임 속도 설정이 올바르지 않습니다.")
    cues = _captions(srt)
    rows = _flow(flow, len(cues))
    duration = 나레이션.probe_duration(나레이션.find_ffmpeg("ffprobe"), str(narration))
    if not duration or duration <= 0:
        raise ValueError("나레이션 길이를 확인하지 못했습니다.")

    assets = []
    for i, (scene, first, last) in enumerate(rows):
        still = next((images_dir / f"{scene:03d}{ext}" for ext in (".jpg", ".png", ".jpeg", ".webp") if (images_dir / f"{scene:03d}{ext}").is_file()), None)
        clip = images_dir / f"{scene:03d}.mp4"
        media = clip if clip.is_file() and clip.stat().st_size else still
        if not media:
            raise ValueError(f"{scene:03d}번 장면 이미지가 없습니다. 이미지 생성 후 다시 렌더하세요.")
        start = cues[first - 1][0]
        end = cues[rows[i + 1][1] - 1][0] if i + 1 < len(rows) else duration
        if i == 0:
            start = 0.0
        end = min(duration, max(start + .05, end))
        assets.append((media, start, end - start, media.suffix.lower() == ".mp4"))

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.{os.getpid()}.rendering{output.suffix or '.mp4'}")
    ffmpeg = 나레이션.find_ffmpeg("ffmpeg")
    try:
        args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
        for media, _start, seconds, is_clip in assets:
            if is_clip:
                args += ["-stream_loop", "-1", "-i", str(media)]
            else:
                args += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}", "-i", str(media)]
        audio_index = len(assets)
        args += ["-i", str(narration)]
        filters, labels = [], []
        for i, (_media, _start, seconds, _is_clip) in enumerate(assets):
            filt = (f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p,"
                    f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS[v{i}]")
            filters.append(filt)
            labels.append(f"[v{i}]")
        filters.append("".join(labels) + f"concat=n={len(assets)}:v=1:a=0[base]")
        font, font_size, bold = _font_for(srt)
        subtitle_filter = (f"subtitles=filename='{_filter_path(srt)}':"
                           f"force_style='FontName={font},FontSize={font_size},Bold={1 if bold else 0},"
                           "Outline=2,Shadow=0,Alignment=2,MarginV=48,WrapStyle=2'")
        filters.append(f"[base]{subtitle_filter},format=yuv420p[outv]")
        args += ["-filter_complex", ";".join(filters), "-map", "[outv]", "-map", f"{audio_index}:a:0",
                 "-t", f"{duration:.3f}", "-r", str(fps), "-c:v", "libx264", "-crf", str(crf),
                 "-preset", preset, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(partial)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
        while proc.poll() is None:
            if job and job.cancel_requested:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("사용자가 렌더링을 중단했습니다.")
            line = proc.stdout.readline() if proc.stdout else ""
            if line.startswith("out_time_ms=") and job:
                try:
                    value = int(line.split("=", 1)[1]) / 1_000_000
                    job.progress = max(0.0, min(.99, value / duration))
                    job.stage = "FFmpeg 영상 렌더링"
                except ValueError:
                    pass
        stderr = proc.stderr.read() if proc.stderr else ""
        if proc.returncode:
            raise RuntimeError("FFmpeg 렌더 실패: " + (stderr.strip()[-1200:] or f"종료 코드 {proc.returncode}"))
        if not partial.is_file() or partial.stat().st_size < 1024:
            raise RuntimeError("FFmpeg가 유효한 영상 파일을 만들지 못했습니다.")
        measured = 나레이션.probe_duration(나레이션.find_ffmpeg("ffprobe"), str(partial))
        if measured < duration * .98:
            raise RuntimeError(f"완성 영상이 나레이션보다 짧습니다 ({measured:.1f}/{duration:.1f}초).")
        os.replace(partial, output)
        if job:
            job.progress = 1.0
            job.stage = "완료"
        return {"output": str(output), "duration": measured, "scenes": len(assets)}
    finally:
        partial.unlink(missing_ok=True)
