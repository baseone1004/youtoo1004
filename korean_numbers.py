"""한국어 낭독용 숫자 정규화. 값은 유지하고 수사·단위에 맞게 표기를 바꾼다."""
import re

VERSION = 1
DIGITS = "영일이삼사오육칠팔구"
ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
COUNTERS = "가지|번째|시간|사람|마리|군데|개|명|번|살|시|권|잔|대|장|채|켤레|쌍"
NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"


def sino(number):
    number = int(number)
    if number == 0:
        return "영"
    groups = []
    for group_unit in ("", "만", "억", "조", "경"):
        part = number % 10000
        number //= 10000
        if part:
            chunk = ""
            for place, unit in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
                digit, part = divmod(part, place)
                if digit:
                    chunk += ("" if digit == 1 and unit else DIGITS[digit]) + unit
            groups.append(chunk + group_unit)
        if not number:
            return "".join(reversed(groups))
    return "".join(DIGITS[int(x)] for x in str(number)) + "".join(reversed(groups))


def native(number):
    number = int(number)
    if not 1 <= number <= 99:
        return sino(number)
    if number == 20:
        return "스무"
    return TENS[number // 10] + ONES[number % 10]


def read_number(value, counter=""):
    value = value.replace(",", "")
    if "." in value:
        whole, fraction = value.split(".", 1)
        return sino(whole) + " 점 " + "".join(DIGITS[int(x)] for x in fraction)
    if counter == "번째" and int(value) == 1:
        return "첫"
    if counter == "월" and int(value) in (6, 10):
        return {6: "유", 10: "시"}[int(value)]
    return native(value) if re.fullmatch(COUNTERS, counter) else sino(value)


def normalize(text):
    # 원문 주소·식별자가 낭독 본문에 있어도 주소 자체는 변경하지 않는다.
    protected = []
    def protect(match):
        protected.append(match.group(0))
        return "\ue000" + chr(0xE100 + len(protected) - 1) + "\ue001"
    text = re.sub(r"https?://\S+|\b[A-Za-z_]+[\w.-]*\d[\w.-]*", protect, text)
    text = re.sub(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)",
                  lambda m: f"{sino(m[1])} 년 {read_number(m[2], '월')}월 {sino(m[3])} 일", text)
    # 시:분 표기는 시에는 고유어, 분에는 한자어 수사를 사용한다.
    text = re.sub(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", lambda m: f"{native(m[1])} 시 {sino(m[2])} 분", text)
    text = re.sub(rf"(?<![\w])[-−]({NUM})", lambda m: "마이너스 " + m[1], text)
    units = COUNTERS + "|퍼센트|킬로미터|킬로그램|센티미터|밀리미터|년|월|일|분|초|원|배|세|층|위|호|점|도|%|℃|km|kg|cm|mm"
    spoken_units = {"%": "퍼센트", "℃": "도", "km": "킬로미터", "kg": "킬로그램", "cm": "센티미터", "mm": "밀리미터"}
    def with_unit(match):
        return read_number(match[1], match[2]) + " " + spoken_units.get(match[2], match[2])
    text = re.sub(rf"({NUM})\s*[~～–]\s*({NUM})\s*({units})", lambda m: with_unit_parts(m[1], m[3]) + "에서 " + with_unit_parts(m[2], m[3]), text)
    text = re.sub(rf"({NUM})\s*({units})", with_unit, text)
    text = re.sub(rf"(?<![\w])[-−]({NUM})", lambda m: "마이너스 " + read_number(m[1]), text)
    text = re.sub(NUM, lambda m: read_number(m[0]), text)
    # AI가 이미 '오 가지', '삼 명'처럼 한자어로 풀어 쓴 경우도 바로잡는다.
    sino_native = {sino(n): native(n) for n in range(1, 100)}
    candidates = "|".join(sorted(sino_native, key=len, reverse=True))
    text = re.sub(rf"(?<![가-힣])({candidates})\s+({COUNTERS})(?=\s|[은는이가을를의에로와과도.,!?]|$)",
                  lambda m: ("첫" if m[1] == "일" and m[2] == "번째" else sino_native[m[1]]) + " " + m[2], text)
    for i, original in enumerate(protected):
        text = text.replace("\ue000" + chr(0xE100 + i) + "\ue001", original)
    return text


def with_unit_parts(value, unit):
    names = {"%": "퍼센트", "℃": "도", "km": "킬로미터", "kg": "킬로그램", "cm": "센티미터", "mm": "밀리미터"}
    return read_number(value, unit) + " " + names.get(unit, unit)
