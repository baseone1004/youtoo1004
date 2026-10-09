"""Local production library, versioned text recovery and render preflight."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import shutil

_LOCK = threading.RLock()
MEDIA = re.compile(r"^(\d{1,4})\.(jpg|jpeg|png|webp|mp4)$", re.I)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _store(root):
    return Path(root).resolve() / "대본" / "_상태" / "문서백업"


def _index(root):
    p = _store(root) / "index.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _allowed(root, path):
    folder = Path(root).resolve() / "대본"
    p = Path(path).resolve()
    if not p.is_relative_to(folder) or any(x.startswith("_") for x in p.relative_to(folder).parts):
        raise ValueError("대본 작업 폴더의 문서만 백업·복원할 수 있습니다.")
    if p.suffix.lower() not in (".txt", ".srt"):
        raise ValueError("대본·프롬프트·자막 문서만 복원할 수 있습니다.")
    return p, p.relative_to(folder).as_posix()


def backup_file(root, path):
    p, key = _allowed(root, path)
    with _LOCK:
        if not p.is_file() or p.stat().st_size > 5 * 1024 * 1024:
            return False
        raw = p.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        index = _index(root)
        previous = index.get(key, [])
        if previous and previous[-1]["sha256"] == digest:
            return False
        file = hashlib.sha256(key.encode()).hexdigest() + "_" + digest + ".bin"
        _write(_store(root) / file, raw)
        index[key] = (previous + [{"file": file, "sha256": digest, "time": time.time()}])[-3:]
        _write(_store(root) / "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode())
        # Keep the latest three versions, including unchanged revisions shared by content hash.
        referenced = {v["file"] for revisions in index.values() for v in revisions}
        for old in _store(root).glob("*.bin"):
            if old.name not in referenced:
                old.unlink()
    return True


def snapshot(root):
    root = Path(root).resolve()
    scripts = root / "대본"
    paths = list(scripts.glob("*.txt"))
    paths += list(scripts.glob("*_자료/*.srt"))
    paths += list(scripts.glob("민담/*/*.txt")) + list(scripts.glob("민담/*/*.srt"))
    return sum(backup_file(root, p) for p in paths)


def restore_missing(root, script_key):
    root = Path(root).resolve()
    script, key = _allowed(root, root / "대본" / script_key)
    if script.name == "final.txt":
        belongs = lambda k: k.startswith(str(Path(key).parent).replace("\\", "/") + "/")
    else:
        stem = key[:-4]
        belongs = lambda k: k == key or k.startswith(stem + "_")
    restored = []
    with _LOCK:
        for relative, versions in _index(root).items():
            if not belongs(relative) or not versions:
                continue
            target, _ = _allowed(root, root / "대본" / relative)
            if target.exists():
                continue
            version = versions[-1]
            file = (_store(root) / version["file"]).resolve()
            if file.parent != _store(root) or file.suffix != ".bin":
                raise ValueError("백업 경로가 올바르지 않습니다.")
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != version["sha256"]:
                raise ValueError("백업 내용이 변경돼 복원할 수 없습니다.")
            _write(target, raw)
            restored.append(relative)
    return {"count": len(restored), "files": restored}


def library(root):
    root = Path(root).resolve()
    scripts = root / "대본"
    with _LOCK:
        index = _index(root)
    paths = set(scripts.glob("*.txt")) | set(scripts.glob("민담/*/final.txt"))
    paths |= {scripts / (p.name[:-3] + ".txt") for p in scripts.glob("*_자료") if p.is_dir()}
    paths |= {scripts / key for key in index if "/" not in key or key.endswith("/final.txt")}
    rows = []
    for p in paths:
        name = p.name
        if any(x in name for x in ("프롬프트", "썸네일", "_유튜브최적화", "_메타", "_테스트")):
            continue
        a = p.parent if name == "final.txt" else p.with_name(p.stem + "_자료")
        key = p.relative_to(scripts).as_posix()
        images = [f for f in (a / "images").glob("*") if MEDIA.match(f.name)]
        rows.append({"id": key, "name": p.parent.name if name == "final.txt" else p.stem,
                     "script": str(p), "script_exists": p.is_file(), "recoverable": bool(index.get(key)),
                     "folder": str(a if a.exists() else p.parent),
                     "video": str(a / "최종.mp4") if (a / "최종.mp4").exists() else "",
                     "audio": (a / "나레이션.mp3").is_file(), "media_count": len(images),
                     "mtime": max(p.stat().st_mtime if p.exists() else 0, a.stat().st_mtime if a.exists() else 0)})
    for p in (root / "업로드").glob("*/*"):
        if p.is_dir() and (p / "최종.mp4").is_file():
            rows.append({"id": "upload/" + p.relative_to(root / "업로드").as_posix(), "name": p.parent.name + " / " + p.name,
                         "folder": str(p), "video": str(p / "최종.mp4"), "script_exists": False,
                         "recoverable": False, "package": True, "mtime": p.stat().st_mtime})
    return sorted(rows, key=lambda x: -x["mtime"])


def media_report(flow, images):
    flow = Path(flow)
    expected, flow_duplicates = [], []
    for line in flow.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?\s*$", line)
        if not match:
            if line.strip() and not line.lstrip().startswith("#"):
                raise ValueError("장면 연결표 형식이 올바르지 않습니다.")
            continue
        no, start, end = int(match[1]), int(match[2]), int(match[3] or match[2])
        if min(no, start) < 1 or end < start:
            raise ValueError("장면·자막 번호가 올바르지 않습니다.")
        if no in expected:
            flow_duplicates.append(no)
        expected.append(no)
    if not expected:
        raise ValueError("장면 연결표가 비어 있습니다. 이미지 프롬프트와 자막을 먼저 만드세요.")
    found = {}
    for p in Path(images).glob("*"):
        match = MEDIA.match(p.name)
        if match and p.is_file() and p.stat().st_size:
            found.setdefault(int(match[1]), []).append(p)
    missing, duplicates, selected = [], list(flow_duplicates), []
    for no in sorted(set(expected)):
        choices = found.get(no, [])
        videos = [p for p in choices if p.suffix.lower() == ".mp4"]
        choices = videos or choices
        if not choices:
            missing.append(no)
        elif len(choices) > 1:
            duplicates.append(no)
        else:
            selected.append({"number": no, "file": str(choices[0].resolve()), "type": "video" if videos else "image"})
    return {"ok": not missing and not duplicates, "expected": len(set(expected)), "matched": len(selected),
            "missing": missing, "duplicates": sorted(set(duplicates)), "selected": selected,
            "extra": sorted(set(found) - set(expected))}


def require_render_media(flow, images, report_path=None):
    report = media_report(flow, images)
    if report_path:
        _write(Path(report_path), json.dumps(report, ensure_ascii=False, indent=2).encode())
    if not report["ok"]:
        parts = []
        if report["missing"]:
            parts.append("누락 " + ", ".join(f"{n:03d}" for n in report["missing"]))
        if report["duplicates"]:
            parts.append("중복 " + ", ".join(f"{n:03d}" for n in report["duplicates"]))
        raise ValueError("장면 파일을 확인하세요: " + " · ".join(parts) + ". 검사 기록은 작업 폴더에 저장했습니다.")
    return report


def upload_cleanup(root, item_id, queue_items=(), execute=False):
    """Only delete the selected library work and explicitly linked upload packages."""
    root = Path(root).resolve()
    row = next((x for x in library(root) if x["id"] == item_id), None)
    if not row:
        raise ValueError("작업 보관함에서 정리할 작업을 다시 선택하세요.")
    script = Path(row["script"]).resolve() if row.get("script") else None
    packages = set()
    links = {}
    for package in (root / "업로드").glob("*/*"):
        marker = package / "제작원본.json"
        if marker.is_file():
            source = json.loads(marker.read_text(encoding="utf-8")).get("script", "")
            if source:
                links[package.resolve()] = (root / source).resolve()
    for item in queue_items:
        result = item.get("result") or {}
        source = result.get("script") or item.get("script_file")
        if source and result.get("upload_dir"):
            links.setdefault(Path(result["upload_dir"]).resolve(), (root / source).resolve())
    if row.get("package"):
        package = Path(row["folder"]).resolve()
        packages.add(package)
        script = links.get(package)
    if script:
        script, key = _allowed(root, script)
        packages.update(p for p, s in links.items() if s == script)
        assets = script.parent if script.name == "final.txt" else script.with_name(script.stem + "_자료")
        targets = {assets, script}
        if script.name != "final.txt":
            # Exact known companion names; do not glob unrelated similarly named works.
            targets.update(script.with_name(script.stem + suffix) for suffix in
                           ("_이미지프롬프트.txt", "_이미지프롬프트_플로우.txt", "_유튜브최적화.txt",
                            "_썸네일.txt", "_메타.txt", "_테스트.txt", "_썸네일프롬프트.txt"))
    else:
        key = None
        targets = set()
    targets.update(packages)
    for p in targets:
        allowed = root / ("업로드" if p in packages else "대본")
        if p == allowed or not p.resolve().is_relative_to(allowed) or p.is_symlink() or getattr(p, "is_junction", lambda: False)() or any(x.startswith("_") for x in p.relative_to(allowed).parts):
            raise ValueError("선택한 작업 폴더 밖의 파일은 정리할 수 없습니다.")
        # Reject junctions/symlinks anywhere inside recursive targets, too.
        if p.is_dir() and any(not c.resolve().is_relative_to(p) or c.is_symlink() or
                             (getattr(c, "is_junction", lambda: False)()) for c in p.rglob("*")):
            raise ValueError("외부 폴더 연결이 있는 작업은 자동 정리할 수 없습니다.")
    existing = sorted((p for p in targets if p.exists()), key=lambda p: len(p.parts), reverse=True)
    result = {"name": row["name"], "paths": [str(p) for p in existing], "linked_script": str(script) if script else "",
              "count": len(existing), "source_linked": bool(script)}
    if not execute:
        return result
    with _LOCK:
        # All paths have been validated above before the first removal.
        for p in existing:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        if key:
            index = _index(root)
            deleted = {p.relative_to(root / "대본").as_posix() for p in targets if p not in packages}
            index = {k: v for k, v in index.items() if not any(k == d or k.startswith(d + "/") for d in deleted)}
            _write(_store(root) / "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode())
            keep = {v["file"] for versions in index.values() for v in versions}
            for p in _store(root).glob("*.bin"):
                if p.name not in keep:
                    p.unlink()
    return result
