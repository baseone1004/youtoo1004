"""대본 분량은 예상치, 완성 기준은 실제 재생 시간으로 판단한다."""
import math

MINIMUM_MINUTES = 10
MINIMUM_SECONDS = MINIMUM_MINUTES * 60
SCRIPT_LENGTHS = (10, 15, 20, 25, 30, 35, 40)
# Ask for a little extra script so normal narration pace still clears the 10 minute floor.
SCRIPT_MARGIN_MINUTES = 5
EXTENSION_TARGET_MINUTES = MINIMUM_MINUTES + 2


def script_target(requested, configured_cpm, language, speed=1.0):
    baseline = max(1, int(configured_cpm or 270))
    minutes = min(SCRIPT_LENGTHS, key=lambda m: abs(requested - m * baseline))
    # 일본어는 공백 없이 쓰므로 한국어 글자 수를 그대로 적용하지 않는다.
    estimated_cpm = max(baseline, 400) if language == "ja" else baseline
    return math.ceil((minutes + SCRIPT_MARGIN_MINUTES) * estimated_cpm * max(1.0, float(speed))), minutes


def require_duration(seconds, minimum=MINIMUM_SECONDS):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("재생 시간을 확인하지 못했습니다. 음성 파일을 확인한 뒤 이어서 제작하세요.")
    if seconds < minimum:
        raise ValueError(f"현재 길이는 {int(seconds // 60)}분 {int(seconds % 60)}초입니다. 최소 {minimum // 60}분에 못 미쳐 완료하지 않았습니다. 대본을 보충한 뒤 이어서 제작하세요.")


def additional_chars(chars, seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("나레이션 길이를 읽지 못해 필요한 추가 분량을 계산할 수 없습니다.")
    # Extend toward a small safety buffer above the minimum, then start rendering.
    target_seconds = EXTENSION_TARGET_MINUTES * 60
    return max(300, math.ceil(chars * (target_seconds - seconds) / seconds))
