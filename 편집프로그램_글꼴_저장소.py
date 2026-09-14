# -*- coding: utf-8 -*-
"""편집프로그램에 등록한 개인 글꼴을 안전하게 보관한다."""
import base64
import binascii
import json
import uuid
from pathlib import Path

from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "user_fonts"
INDEX = FONT_DIR / "fonts.json"
EXTS = {".ttf", ".otf", ".ttc"}
MAX_BYTES = 20 * 1024 * 1024


def list_fonts():
    if not INDEX.is_file():
        return []
    try:
        entries = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in entries if (FONT_DIR / entry.get("file_name", "")).is_file()]


def import_font(file_name, encoded, label=""):
    ext = Path(file_name).suffix.lower()
    if ext not in EXTS:
        raise ValueError(".ttf, .otf, .ttc 글꼴 파일만 등록할 수 있습니다.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("글꼴 파일을 읽지 못했습니다.") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("글꼴 파일은 20MB 이하여야 합니다.")
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex + ext
    path = FONT_DIR / stored_name
    try:
        path.write_bytes(raw)
        family = ImageFont.truetype(str(path), 16).getname()[0].strip()
        if not family:
            raise ValueError("글꼴 이름을 읽지 못했습니다.")
        existing = list_fonts()
        shown = label.strip()[:40] or f"내 글꼴 {len(existing) + 1}"
        entry = {"name": family, "label": shown, "file_name": stored_name, "custom": True}
        existing.append(entry)
        INDEX.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**entry, "fonts_dir": str(FONT_DIR)}
    except Exception:
        path.unlink(missing_ok=True)
        raise
