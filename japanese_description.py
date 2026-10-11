"""Japanese upload descriptions: native copy instructions and stable footer."""
import re

RULES = """
[일본 채널 설명글 — 설명글을 작성할 때만 적용하는 최우선 규칙]
기존의 '세~다섯 문장 요약', '해시태그 세 개', 한국어 고정 문장보다 이 규칙을 우선한다.
[설명글] 블록명은 유지하고 내용은 일본 시청자가 자연스럽게 읽는 です・ます체로 쓴다.
한국 채널의 상세 설명과 같은 흐름으로, 주제에 따라 약 800~1,400자, 짧은 문단 10~14개를 목표로 한다.
문단 사이는 빈 줄을 넣는다. 설명글은 나레이션에 포함하지 않는다. 다음 번호나 소제목은 출력하지 않는다.
1. 제목의 핵심 상황을 넣은 공감 질문으로 시작한다. 예: 「親と話していて、なぜか気持ちがすれ違う。そんな経験はありませんか。」
2. 시청자가 겪을 구체적인 장면과 그때의 감정을 한두 문단으로 풀어 준다.
3. 「今回の動画では、…について、身近な場面を交えながら考えていきます。」처럼 영상의 핵심 내용을 소개한다.
4. 실제 대본에서 다루는 사례·관점·실천 방법을 한 문단으로 구체적으로 소개한다. 영상에 없는 내용을 약속하지 않는다.
5. 제공된 자료에서 확인되는 출처가 있을 때만 근거를 짧게 소개한다. 대본의 언급이나 출처후보만으로 검증됐다고 보지 않는다.
   확인되지 않은 기관명·연구·수치·인물을 만들어 넣지 않는다. 자료가 없으면 근거 문단을 생략한다.
   한국 기관을 일본 기관으로 바꾸거나 한국어 예시의 출처를 그대로 복사하지 않는다.
6. 시청자가 생각해 볼 핵심 질문 하나를 자연스럽게 제시한다.
7. 누구 한쪽을 탓하지 않는 따뜻한 정리 한두 문단과 영상을 통해 얻을 수 있는 관점을 쓴다.
8. 실제 주제에 맞는 경험 질문 + 「よろしければ、コメントで聞かせてください。」처럼 부담 없는 참여 안내를 넣는다.
9. 「チャンネル登録や高評価で応援していただけると、これからの動画づくりの励みになります。」로 마무리한다.
「あなた」を 반복하거나 한국식 직역을 하지 않는다. 한국의 명절 등은 영상에서 다루는 경우에만 언급한다.
굵게 표시하는 별표·과장된 효과 보장·낚시 문구는 쓰지 않는다.
구분선·정보 제공 안내·AI 사용 안내·맨 아래 해시태그는 프로그램이 붙이므로 본문에 중복 작성하지 않는다.
[태그]에는 실제 주제와 맞는 일본어 태그 약 10개를 쓴다. 채널명 태그는 제공된 일본어 채널명만 사용한다.
"""

DIVIDER = "────────────────────"
NOTICE = ("※ 本動画は、人間関係や心理について理解を深めるための情報提供を目的としています。"
          "特定の個人を診断したり、すべての方に当てはまると断定したりするものではありません。")
AI_NOTICE = "※ 本動画の台本・画像・音声の制作にはAI技術を使用しています。"
COMMENT = "よろしければ、感じたことやご自身の経験をコメントで聞かせてください。"
SUBSCRIBE = "チャンネル登録や高評価で応援していただけると、これからの動画づくりの励みになります。"


def format_description(description, tags=""):
    if not (description or "").strip():
        return ""
    text = description.strip().replace("**", "")
    found = re.findall(r"#([^\s#,，]+)", text)
    text = re.sub(r"#[^\s#,，]+", "", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"[─━\-—_]{3,}", stripped):
            continue
        if stripped.startswith("※") and re.search(r"本動画|本映像|AI|人工知能|본 영상", stripped):
            continue
        lines.append(line)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if "コメント" not in body:
        body += "\n\n" + COMMENT
    if "チャンネル登録" not in body:
        body += "\n\n" + SUBSCRIBE
    words = re.findall(r"#([^\s#,，]+)", tags) if "#" in tags else re.split(r"[,，\s]+", tags)
    hashtags = []
    for word in found + words:
        word = word.strip().lstrip("#")
        if word and not re.search(r"[가-힣]", word) and word not in hashtags:
            hashtags.append(word)
    result = body + "\n\n" + DIVIDER + "\n\n" + NOTICE + "\n" + AI_NOTICE + "\n\n" + DIVIDER
    if hashtags:
        result += "\n\n\n\n" + " ".join("#" + word for word in hashtags[:10])
    return result


def format_metadata(text):
    """Replace only the description block, preserving all parser block names."""
    pattern = r"(^\[설명글\][ \t]*\n)(.*?)(?=^\[[^\]\n]+\][ \t]*$|\Z)"
    tags = re.search(r"^\[태그\][ \t]*\n(.*?)(?=^\[[^\]\n]+\][ \t]*$|\Z)", text, re.M | re.S)
    return re.sub(pattern, lambda m: m[1] + format_description(m[2], tags[1].strip() if tags else "") + "\n\n", text, flags=re.M | re.S)
