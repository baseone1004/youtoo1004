# -*- coding: utf-8 -*-
"""Create review-ready vertical highlight clips from a finished long-form video."""
import json
import os
import re
import subprocess
from pathlib import Path


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    pattern = re.compile(
        r"(?:^|\n)\s*\d+\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n"
        r"(.*?)(?=\n\s*\n|\Z)", re.S)
    def seconds(value):
        h, m, rest = value.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    cues = []
    for match in pattern.finditer(text):
        caption = re.sub(r"\s+", " ", match.group(3)).strip()
        if caption:
            cues.append({"start": seconds(match.group(1)), "end": seconds(match.group(2)), "text": caption})
    if not cues:
        raise ValueError("쇼츠 구간을 찾을 나레이션 자막이 없습니다.")
    return cues


def _read_json_array(text):
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("AI 쇼츠 구간 추천을 읽지 못했습니다.")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, list):
        raise ValueError("AI 쇼츠 구간 추천 형식이 올바르지 않습니다.")
    return value


def _suggest(cues, ai, title, language, count):
    if ai is None:
        return []
    rows = "\n".join(f"{i + 1}\t{c['start']:.2f}-{c['end']:.2f}\t{c['text']}" for i, c in enumerate(cues))
    system = (
        "너는 완성된 롱폼 영상에서 쇼츠 하이라이트를 편집하는 사람이다. "
        f"서로 겹치지 않는 쇼츠 구간 {count}개를 JSON 배열로만 골라라. "
        "각 구간은 자막 번호 기준으로 30~58초여야 한다. 시작은 바로 관심을 끄는 말이나 장면으로, "
        "끝은 짧은 결론이나 자연스러운 여운으로 끝낸다. 문맥이 끊기거나 설명이 필요한 구간은 고르지 않는다. "
        "대본에 없는 말·사실·치료 조언을 만들지 않는다. 제목은 시청자의 언어로 짧고 정확하게 쓴다. "
        '형식: [{"start_cue": 1, "end_cue": 14, "title": "쇼츠 제목"}]. '
        "자막 번호는 아래 목록의 1부터 시작하는 번호이며, end_cue도 포함한다. 설명은 쓰지 않는다."
    )
    user = (f"영상 제목: {title}\n출력 언어: {language}\n"
            "자막 목록(번호, 시작-끝 초, 실제 나레이션 자막):\n" + rows)
    return _read_json_array(ai.ask(system, user))


def _fallback_windows(cues, count):
    total = cues[-1]["end"] - cues[0]["start"]
    duration = min(52.0, max(30.0, total * 0.035))
    last_start = max(0.0, cues[-1]["end"] - duration)
    if count <= 1 or last_start <= cues[0]["start"]:
        starts = [max(0.0, cues[0]["start"] + 1.0)]
    else:
        starts = [cues[0]["start"] + (last_start - cues[0]["start"]) * i / (count - 1)
                  for i in range(count)]
    result = []
    for target in starts[:count]:
        first = next((i for i, cue in enumerate(cues) if cue["start"] >= target), 0)
        last = next((i for i in range(first, len(cues)) if cues[i]["end"] - cues[first]["start"] >= duration), len(cues) - 1)
        if cues[last]["end"] - cues[first]["start"] < 20:
            continue
        result.append({"start_cue": first + 1, "end_cue": last + 1, "title": ""})
    return result


def _valid_windows(cues, suggestions, count):
    windows = []
    for item in suggestions:
        try:
            first, last = int(item["start_cue"]) - 1, int(item["end_cue"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        if first < 0 or last < first or last >= len(cues):
            continue
        start, end = cues[first]["start"], cues[last]["end"]
        duration = end - start
        if not 25 <= duration <= 62:
            continue
        if any(start < old["end"] and end > old["start"] for old in windows):
            continue
        windows.append({"start": start, "end": end, "first": first, "last": last,
                        "title": re.sub(r"\s+", " ", str(item.get("title") or "")).strip()})
        if len(windows) == count:
            break
    return windows


def _timestamp(value):
    ms = int(round(value * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _safe_stem(value):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", value or "").strip(" .")
    return (value[:52] or "쇼츠")


def _filter_path(path):
    value = str(Path(path).resolve()).replace("\\", "/")
    for char in ("\\", ":", "'", ",", "[", "]", ";"):
        value = value.replace(char, "\\" + char)
    return value


def _render(ffmpeg, video, output, start, end, cancel=None):
    duration = end - start
    output_path = Path(output)
    output_stem = re.sub(r"\.rendering(?:-\d+)?$", "", output_path.stem)
    subtitle = output_path.with_name(output_stem + ".srt")
    if subtitle.is_file():
        subtitle_text = subtitle.read_text(encoding="utf-8-sig", errors="replace")
        path_text = str(subtitle.resolve()).replace("\\", "/")
        font = "Batang" if "/민담/" in path_text else "Yu Gothic" if re.search(r"[ぁ-んァ-ン]", subtitle_text) else "Malgun Gothic"
        subtitle_filter = (
            f"subtitles=filename='{_filter_path(subtitle)}':"
            f"force_style='FontName={font},FontSize=48,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginL=64,MarginR=64,MarginV=300,WrapStyle=2'"
        )
    else:
        subtitle_filter = "null"
    graph = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=24:12,eq=brightness=-0.28,setsar=1[back];"
        "[fg]scale=1080:608:force_original_aspect_ratio=decrease,"
        "pad=1080:608:(ow-iw)/2:(oh-ih)/2,"
        "crop=1080:550:0:0,scale=1080:608:flags=lanczos,setsar=1[front];"
        f"[back][front]overlay=(W-w)/2:(H-h)/2,{subtitle_filter},format=yuv420p[v]"
    )
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", video,
               "-t", f"{duration:.3f}", "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
               "-movflags", "+faststart", output]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while process.poll() is None:
        if cancel and cancel():
            process.kill()
            process.communicate()
            raise RuntimeError("사용자가 쇼츠 제작을 중단했습니다.")
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    _, error = process.communicate()
    if process.returncode:
        raise RuntimeError("쇼츠 세로 영상 변환 실패: " + error.decode("utf-8", "replace")[-600:])
    if not os.path.isfile(output) or os.path.getsize(output) < 50_000:
        raise RuntimeError("쇼츠 영상 파일이 비어 있습니다.")


def create(video, srt, out_dir, title, language, ffmpeg, ai=None, count=2, cancel=None, log=None):
    """Select caption-aligned highlights and render them on a 9:16 blurred-background canvas."""
    count = max(0, min(2, int(count or 0)))
    if count == 0:
        return []
    if not os.path.isfile(video) or not os.path.isfile(srt):
        raise ValueError("쇼츠를 만들 완성 영상과 자막 파일이 필요합니다.")
    cues = parse_srt(srt)
    suggestions = []
    if ai:
        try:
            suggestions = _suggest(cues, ai, title, language, count)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"   AI 구간 선택을 못 받아 안전한 구간으로 대체합니다: {exc}")
    windows = _valid_windows(cues, suggestions, count)
    if len(windows) < count:
        for candidate in _valid_windows(cues, _fallback_windows(cues, count * 3), count * 3):
            if any(candidate["start"] < old["end"] and candidate["end"] > old["start"] for old in windows):
                continue
            windows.append(candidate)
            if len(windows) == count:
                break
    if len(windows) < count:
        raise ValueError(f"쇼츠 {count}편을 만들 수 있는 서로 다른 자막 구간이 부족합니다. 현재 {len(windows)}편 구간만 찾았습니다. 대본·자막을 확인한 뒤 다시 제작하세요.")
    source = Path(video)
    generation = f"captioned-v2_{source.stat().st_mtime_ns}_{source.stat().st_size}"
    target = Path(out_dir) / generation
    target.mkdir(parents=True, exist_ok=True)
    results = []
    for index, window in enumerate(windows[:count], 1):
        if cancel and cancel():
            raise RuntimeError("사용자가 쇼츠 제작을 중단했습니다.")
        start, end = window["start"], window["end"]
        clip_title = window["title"] or f"{title} · 쇼츠 {index}"
        stem = f"쇼츠_{index:02d}_{_safe_stem(clip_title)}"
        output = target / (stem + ".mp4")
        clip_cues = cues[window["first"]:window["last"] + 1]
        subtitles = []
        for cue_index, cue in enumerate(clip_cues, 1):
            relative_start = max(0.0, cue["start"] - start)
            relative_end = max(relative_start + 0.05, min(end - start, cue["end"] - start))
            subtitles.append(f"{cue_index}\n{_timestamp(relative_start)} --> {_timestamp(relative_end)}\n{cue['text']}\n")
        srt_out = target / (stem + ".srt")
        srt_out.write_text("\n".join(subtitles), encoding="utf-8")
        if not output.is_file():
            temp = target / f"{stem}.rendering-{os.getpid()}.mp4"
            try:
                _render(ffmpeg, os.path.abspath(video), str(temp), start, end, cancel)
                os.replace(temp, output)
            finally:
                if os.path.exists(temp):
                    os.remove(temp)
        metadata = {"title": clip_title, "source_title": title, "language": language,
                    "start_seconds": round(start, 3), "end_seconds": round(end, 3),
                    "duration_seconds": round(end - start, 3), "video": output.name, "subtitle": srt_out.name}
        json_out = target / (stem + ".json")
        json_out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {**metadata, "path": str(output), "srt": str(srt_out), "metadata": str(json_out)}
        results.append(result)
        if log:
            log(f"   ✓ 쇼츠 {index}/{count} · {end - start:.0f}초 · {clip_title}")
    return results
