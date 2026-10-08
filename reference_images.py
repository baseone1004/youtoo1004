"""레퍼런스 이미지를 로컬에 보관하고 채널별 생성 설정을 만든다."""
import base64
import hashlib
import io
from pathlib import Path
from PIL import Image, ImageOps
import 채널_프로필

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 10 * 1024 * 1024

def save_reference(slot, data_url):
    if slot not in 채널_프로필.SLOTS:
        raise ValueError("채널을 먼저 선택하세요.")
    if not isinstance(data_url, str) or len(data_url) > MAX_BYTES * 4 // 3 + 200:
        raise ValueError("10MB 이하 이미지를 선택하세요.")
    try:
        header, encoded = data_url.split(",", 1)
        if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
            raise ValueError()
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError()
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image.thumbnail((2048, 2048))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
        normalized = buffer.getvalue()
        if len(normalized) > MAX_BYTES:
            raise ValueError()
    except (ValueError, OSError, Image.DecompressionBombError):
        raise ValueError("PNG·JPG·WEBP 이미지(10MB 이하, 2500만 화소 이하)를 선택하세요.") from None
    folder = ROOT / "레퍼런스"
    folder.mkdir(exist_ok=True)
    path = folder / (slot + "_" + hashlib.sha256(normalized).hexdigest()[:20] + ".png")
    path.write_bytes(normalized)
    try:
        profile = 채널_프로필.save(slot, {"마스코트": {"이미지": str(path.relative_to(ROOT)), "레퍼런스_사용": True}})
    except Exception:
        # Keep previous profile intact; file can be reused on the next upload.
        raise ValueError("레퍼런스 설정을 저장하지 못했습니다. 다시 시도하세요.") from None
    return profile

def reference_options(slot):
    mascot = 채널_프로필.get(slot).get("마스코트") or {}
    if not mascot.get("레퍼런스_사용"):
        return {}
    path = (ROOT / mascot.get("이미지", "")).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        raise ValueError("프로그램 안에 저장된 레퍼런스 이미지를 선택하세요.") from None
    if not path.is_file():
        raise ValueError("레퍼런스 이미지가 없습니다. 다시 올리거나 레퍼런스 사용을 꺼 주세요.")
    return {"reference_image": str(path), "reference_model": mascot.get("생성_모델", "bytedance/seedream-v4-edit")}
