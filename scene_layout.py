"""Persistent image grouping; narration sentences and subtitle times stay intact."""
import hashlib
import json
from pathlib import Path
import re

from production_library import _write

MAX_SCENES = 150
PLAN_NAME = "장면구성.json"
SENTENCE_FLOW = "문장플로우.txt"


def balanced_ranges(sentences, limit=MAX_SCENES):
    """Contiguous whole sentences, balanced by text length, at most limit scenes."""
    count = min(len(sentences), limit)
    if not count:
        return []
    cumulative = [0]
    for text in sentences:
        cumulative.append(cumulative[-1] + max(1, len(re.sub(r"\s+", "", text))))
    ranges, previous = [], 0
    for scene in range(1, count):
        last = len(sentences) - (count - scene)
        target = cumulative[-1] * scene / count
        boundary = min(range(previous + 1, last + 1), key=lambda i: abs(cumulative[i] - target))
        ranges.append((previous + 1, boundary))
        previous = boundary
    ranges.append((previous + 1, len(sentences)))
    return ranges


def image_units(sentences, assets, prompts):
    folder = Path(assets)
    plan_path = folder / PLAN_NAME
    digest = hashlib.sha256(json.dumps(sentences, ensure_ascii=False).encode()).hexdigest()
    existing = Path(prompts).is_file() and bool(re.search(r"^===\s*\d+\s*===", Path(prompts).read_text(encoding="utf-8-sig"), re.M))
    existing = existing or any(re.match(r"\d{1,4}\.(?:png|jpg|jpeg|webp|mp4)$", p.name, re.I) for p in (folder / "images").glob("*"))
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan["sha256"] != digest:
                if existing:
                    raise ValueError("장면 구성 뒤 대본이 바뀌었습니다. 기존 이미지 번호를 보호하기 위해 멈췄습니다. 새 제작물로 시작하세요.")
                ranges = balanced_ranges(sentences)
            else:
                ranges = [tuple(pair) for pair in plan["ranges"]]
                expected = 1
                for a, b in ranges:
                    if a != expected or b < a or b > len(sentences):
                        raise ValueError("저장된 장면 연결 범위를 확인하세요.")
                    expected = b + 1
                if expected != len(sentences) + 1 or len(ranges) > MAX_SCENES:
                    raise ValueError("저장된 장면 연결 범위를 확인하세요.")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("저장된 장면 구성 파일을 확인하세요. 기존 이미지 번호는 변경하지 않았습니다.") from exc
    elif existing:
        # Unversioned productions used one image per sentence. Never renumber
        # previously submitted/paid scenes when the user resumes them.
        return [(i, i, text) for i, text in enumerate(sentences, 1)]
    else:
        ranges = balanced_ranges(sentences)
    plan = dict(version=1, limit=MAX_SCENES, sha256=digest, ranges=ranges)
    serialized = json.dumps(plan, ensure_ascii=False, indent=2).encode()
    if not plan_path.exists() or plan_path.read_bytes() != serialized:
        _write(plan_path, serialized)
    return [(a, b, " ".join(sentences[a - 1:b])) for a, b in ranges]


def write_image_flow(folder, units, fresh_narration=False):
    """Keep sentence->cue map separately; editor reads image->cue in 플로우.txt."""
    folder = Path(folder)
    flow, original = folder / "플로우.txt", folder / SENTENCE_FLOW
    if fresh_narration and flow.exists():
        _write(original, flow.read_bytes())
    source = original if original.exists() else flow
    if not source.exists():
        return None
    mapping, expected_sentence, expected_cue = {}, 1, 1
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?\s*", line)
        if not match:
            raise ValueError("문장·자막 연결표 형식이 올바르지 않습니다.")
        no, start, end = int(match[1]), int(match[2]), int(match[3] or match[2])
        if no != expected_sentence or start != expected_cue or end < start:
            raise ValueError("문장·자막 연결표에 빠지거나 겹친 번호가 있습니다.")
        mapping[no] = (start, end)
        expected_sentence, expected_cue = no + 1, end + 1
    if not units or len(mapping) != units[-1][1]:
        raise ValueError("대본과 나레이션 문장 수가 달라 이미지 연결을 멈췄습니다.")
    lines = ["# 이미지번호: 자막번호 (연속 문장 묶음 전체에 그림 한 장)"]
    for no, (a, b, _) in enumerate(units, 1):
        start, end = mapping[a][0], mapping[b][1]
        lines.append(f"{no}: {start}" if start == end else f"{no}: {start}-{end}")
    if not original.exists():
        _write(original, source.read_bytes())
    data = ("\n".join(lines) + "\n").encode()
    if not flow.exists() or flow.read_bytes() != data:
        _write(flow, data)
    return str(flow)
