# -*- coding: utf-8 -*-
"""
나레이션.py — 인월드(Inworld) TTS 로 대본을 문장 단위로 읽고, 합친 mp3 + 문장별 SRT + 플로우 txt 를 만든다.

  문장 1개 = 자막 1개 = 이미지 1장. 문장마다 따로 합성하므로 자막 시간이 정확하다.
  설정.json: "인월드_API_키", "인월드_목소리", "인월드_모델"(기본 inworld-tts-1.5), "인월드_속도"(기본 1.0)
  API: POST https://api.inworld.ai/tts/v1/voice  (Authorization: Basic <API_KEY>)

python 나레이션.py "대본 파일" [출력 폴더]
"""
import sys, os, re, json, base64, subprocess, shutil, time
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)
import requests

INWORLD_URL = "https://api.inworld.ai/tts/v1/voice"
# 프로그램 폴더 안의 bin/ → 같이 배포되는 편집프로그램의 bin/ → (개발용) 다운로드 폴더의 편집프로그램 순으로 찾는다. PATH 에 있으면 그것을 먼저 쓴다.
FFMPEG_후보 = [os.path.join(BASE, "bin", "ffmpeg.exe"),
            os.path.join(BASE, "편집프로그램", "bin", "ffmpeg.exe"),
            os.path.join(os.path.expanduser("~"), "Downloads", "편집프로그램", "bin", "ffmpeg.exe")]
문장_간격 = 0.28          # 묶음 사이 무음(초) — 묶음 안의 쉼은 목소리가 알아서 두고, 긴 쉼은 쉼_최대 로 줄인다
동시_요청 = 3
자막_최대_글자 = 18     # 화면에 한 번에 보여줄 자막 길이(공백 제외)


def find_ffmpeg(name="ffmpeg"):
    p = shutil.which(name)
    if p:
        return p
    for c in FFMPEG_후보:
        c2 = c.replace("ffmpeg.exe", f"{name}.exe")
        if os.path.exists(c2):
            return c2
    raise FileNotFoundError(f"{name} 을 찾을 수 없습니다. ffmpeg 를 설치하거나 PATH 에 넣어주세요.")


_붙임 = "⁠"     # 따옴표 안 문장 사이에 잠시 넣는 표시 (word joiner) — 여기서는 문장을 자르지 않는다


def detect_language(text):
    """대본의 주 언어를 가볍게 판별한다. 지원 대상은 한국어·영어·일본어다."""
    sample = str(text or "")[:12000]
    ko = len(re.findall(r"[가-힣]", sample))
    ja = len(re.findall(r"[ぁ-んァ-ヶー]", sample))
    en = len(re.findall(r"[A-Za-z]", sample))
    if ja and ja * 3 >= ko:
        return "ja"
    if ko and ko * 2 >= en:
        return "ko"
    return "en" if en else "ko"


def _foreign_sentences(text):
    """공백 없는 일본어와 영문 약어·소수를 보존해 문장 경계를 찾는다."""
    ends = ".!?。！？"
    closers = "\"'”’」』》〉】)]}"
    parts, start, i = [], 0, 0
    while i < len(text):
        char = text[i]
        decimal = char == "." and i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit()
        before, following = text[start:i + 1], text[i + 1:]
        abbreviation = char == "." and (
            bool(re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)\.$", before, re.I))
            or (i + 1 < len(text) and text[i + 1].isascii() and text[i + 1].isalpha())
            or (bool(re.search(r"(?:\b[A-Za-z]\.){2,}$", before)) and bool(re.match(r"\s+[a-z]", following)))
        )
        if char == "\n" or (char in ends and not decimal and not abbreviation):
            end = i + 1
            if char != "\n":
                while end < len(text) and text[end] in ends + closers:
                    end += 1
                suffix = text[i + 1:end]
                if ((any(q in suffix for q in "」』") and re.match(r"(?:と|って|など|なんて)", text[end:]))
                        or (any(q in suffix for q in '\"”') and re.match(r" +[a-z]", text[end:]))):
                    i = end
                    continue
            piece = text[start:end].strip()
            if piece:
                parts.append(piece)
            start, i = end, end
        else:
            i += 1
    if text[start:].strip():
        parts.append(text[start:].strip())
    return [part for part in parts if any(ch.isalnum() for ch in part)]


def split_sentences(body, quotes=True):
    """문장 나누기 (나레이션·자막·이미지 번호가 모두 여기서 나온 번호를 쓴다).
    quotes=True: 따옴표 안의 대사는 마침표가 여러 개여도 한 문장으로 묶고, 따옴표 자체("“”‘’)는 자막·나레이션에 넣지 않는다.
    quotes=False: 예전 방식 (따옴표를 그대로 두고 마침표마다 자른다) — 예전 방식으로 만들던 편을 끝까지 같은 번호로 만들 때."""
    body = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    language = detect_language(body)
    if language in ("en", "ja"):
        parts = _foreign_sentences(body)
        return [re.sub(r'["“”‘’]', "", part) for part in parts] if quotes else parts
    body = re.sub(r"[?!？！]+", ".", body)
    body = re.sub(r"(\.{2,}|…+)", ".", body)
    body = re.sub(r"\s+", " ", body.strip())
    if quotes:
        body = re.sub(r'["“]([^"“”]{1,400})["”]', lambda m: re.sub(r"([.。])\s+", "\\1" + _붙임, m.group(0)), body)
        body = re.sub(r'["“”‘’]', "", body)
    parts = re.split(r"(?<=[.。])\s+", body)
    return [p.replace(_붙임, " ").strip() for p in parts if p.strip() and re.search(r"[가-힣a-zA-Z0-9]", p)]


def script_body(text):
    if "[대본]" in text:
        text = text.split("[대본]", 1)[1]
    if "===sum===" in text:
        text = text.split("===sum===", 1)[0]
    return text.strip()


class Inworld:
    def __init__(self, api_key, voice_id, model="inworld-tts-1.5-max", speed=1.0, temperature=None):
        if not api_key or not voice_id:
            raise SystemExit("설정.json 에 인월드_API_키 와 인월드_목소리(voice ID) 를 넣어주세요.")
        self.h = {"Authorization": f"Basic {api_key.strip()}", "Content-Type": "application/json"}
        self.voice, self.model, self.speed, self.temperature = voice_id.strip(), model or "inworld-tts-1.5-max", float(speed or 1.0), temperature

    def synth(self, text, out_path, retries=3):
        body = {"text": text, "voiceId": self.voice, "modelId": self.model,
                "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 24000}}
        if abs(self.speed - 1.0) > 0.01:
            body["audioConfig"]["speakingRate"] = self.speed
        if self.temperature is not None:
            body["temperature"] = float(self.temperature)
        last = ""
        for attempt in range(retries):
            try:
                r = requests.post(INWORLD_URL, headers=self.h, json=body, timeout=120)
                if r.status_code == 200:
                    j = r.json()
                    audio = j.get("audioContent") or (j.get("result") or {}).get("audioContent")
                    if not audio:
                        raise RuntimeError(f"audioContent 없음: {str(j)[:200]}")
                    with open(out_path, "wb") as f:
                        f.write(base64.b64decode(audio))
                    return out_path
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (401, 403):
                    raise SystemExit("인월드 API 키가 잘못되었거나 권한이 없습니다. " + last)
                if r.status_code == 400 and "voice" in r.text.lower():
                    raise SystemExit("인월드 목소리 ID 를 확인하세요. " + last)
                if r.status_code == 400 and "model" in r.text.lower():
                    raise SystemExit("인월드 모델 이름이 잘못되었습니다 ([설정]에서 inworld-tts-1.5-max 등으로 바꾸세요). " + last)
                if r.status_code == 400:
                    raise SystemExit("인월드 요청 오류: " + last)
            except requests.RequestException as e:
                last = str(e)
            time.sleep(2 * (attempt + 1))
        raise RuntimeError("인월드 TTS 실패: " + last)


def probe_duration(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def fmt_srt(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_subtitle_text(text, max_chars=자막_최대_글자):
    """긴 문장을 한 줄 자막으로 읽기 좋게 나눈다.

    쉼표·접속 구간을 우선하고, 그래도 길면 단어 경계에서 나눈다.
    원문의 문장부호는 그대로 보존한다.
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if max_chars < 1:
        raise ValueError("자막 최대 글자 수는 1 이상이어야 합니다.")
    units = [x.strip() for x in re.split(r"(?<=[,，;；:：])\s*|(?<=\uace0)\s+(?=[\uac00-\ud7a3])|(?<=\uba70)\s+(?=[\uac00-\ud7a3])", text) if x.strip()]
    out = []
    for unit in units:
        words = []
        for word in unit.split():
            words.extend(word[i:i + max_chars] for i in range(0, len(word), max_chars))
        best = [None] * (len(words) + 1)
        best[len(words)] = []
        for i in range(len(words) - 1, -1, -1):
            candidates = []
            size = 0
            for j in range(i, len(words)):
                size += len(words[j])
                if size > max_chars:
                    break
                if best[j + 1] is not None:
                    seq = [" ".join(words[i:j + 1])] + best[j + 1]
                    lengths = [len(x.replace(" ", "")) for x in seq]
                    score = (len(seq), max(lengths) - min(lengths), sum(x * x for x in lengths))
                    candidates.append((score, seq))
            if candidates:
                best[i] = min(candidates, key=lambda item: item[0])[1]
        out.extend(best[0] or [unit])
    merged = []
    for chunk in out:
        if merged and len((merged[-1] + " " + chunk).replace(" ", "")) <= max_chars:
            merged[-1] += " " + chunk
        else:
            merged.append(chunk)
    return merged or [text]


def detect_silences(ffmpeg, path, noise_db=-35, min_len=0.15):
    """음성 파일 안의 조용한 구간 [(시작, 끝)] — 문장 사이의 자연스러운 쉼을 찾는 데 쓴다."""
    out = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace")
    text = out.stderr if isinstance(getattr(out, "stderr", None), str) else ""
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", text)]
    return list(zip(starts, ends))


쉼_최대 = 0.45            # 읽힌 음성 안의 쉼(무음)이 이보다 길면 이 길이로 줄인다 (문장마다 길게 쉬어 뚝뚝 끊기는 느낌을 없앤다)
앞뒤_무음 = 0.08          # 묶음 파일 앞뒤에 남겨 두는 무음


def tighten_pauses(ffmpeg, ffprobe, src, dst, max_pause=쉼_최대, edge=앞뒤_무음):
    """묶음 음성의 앞뒤 무음을 잘라 내고, 안쪽의 긴 쉼은 max_pause 로 줄인다. 실패하면 원본을 그대로 복사."""
    import shutil as _sh
    total = probe_duration(ffprobe, src)
    sil = detect_silences(ffmpeg, src, noise_db=-40, min_len=0.12)
    if total <= 0:
        _sh.copy(src, dst); return dst
    start, end = 0.0, total
    if sil and sil[0][0] <= 0.02:
        start = max(0.0, sil[0][1] - edge); sil = sil[1:]
    if sil and sil[-1][1] >= total - 0.05:
        end = min(total, sil[-1][0] + edge * 1.5); sil = sil[:-1]
    segs, cur = [], start
    for a, b in sil:
        if b - a > max_pause and a > cur:
            segs.append((cur, a + max_pause)); cur = b          # 쉼의 앞 max_pause 만 남기고 나머지는 건너뛴다
    segs.append((cur, end))
    segs = [(a, b) for a, b in segs if b - a > 0.02]
    if not segs:
        _sh.copy(src, dst); return dst
    parts = "".join(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[s{i}];" for i, (a, b) in enumerate(segs))
    filt = parts + "".join(f"[s{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[out]"
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", src, "-filter_complex", filt, "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "160k", dst],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not os.path.exists(dst):
        _sh.copy(src, dst)
    return dst


def sentence_boundaries(ffmpeg, path, total, texts):
    """묶어 읽은 음성에서 문장 경계 시각 목록(길이 = 문장 수 - 1). 글자 수 비율로 예상한 자리에 가장 가까운 쉼(무음)의 가운데를 고른다.
    맞는 쉼이 없으면 예상 자리를 그대로 쓴다."""
    m = len(texts)
    if m <= 1:
        return []
    weights = [max(1, len(re.sub(r"\s+", "", t))) for t in texts]
    acc, expected = 0, []
    for w in weights[:-1]:
        acc += w
        expected.append(total * acc / sum(weights))
    mids = [(a + b) / 2 for (a, b) in detect_silences(ffmpeg, path) if a > 0.2 and b < total - 0.2]
    bounds, prev = [], 0.0
    for k, e in enumerate(expected):
        remaining = len(expected) - k - 1
        cands = [x for x in mids if x > prev + 0.3 and x < total - 0.3 * (remaining + 1)]
        pick = min(cands, key=lambda x: abs(x - e)) if cands else None
        if pick is None or abs(pick - e) > max(1.5, 0.25 * total / m):
            pick = max(prev + 0.3, min(e, total - 0.3 * (remaining + 1)))
        bounds.append(pick); prev = pick
        mids = [x for x in mids if x > pick]
    return bounds


def synthesize(sentences, out_dir, api_key, voice_id, model="inworld-tts-1.5-max", speed=1.0, log=print, cancel=None,
               temperature=None, name="나레이션", subtitle_lines=1, groups=None):
    """문장 목록 → out_dir/나레이션.mp3, 나레이션.srt, 플로우.txt. 이미 있는 부분 파일은 (같은 문장이면) 재사용.
    groups: [(첫 문장 번호, 끝 문장 번호)] — 이 범위의 문장을 한 번에 읽혀 억양이 이어지게 한다 (문장마다 따로 읽으면 매 문장이 새로 시작하는 느낌).
            None 이면 문장마다 따로 읽는다 (예전 방식). 문장별 자막 시각은 음성 안의 쉼(무음)으로 되찾는다."""
    ffmpeg, ffprobe = find_ffmpeg("ffmpeg"), find_ffmpeg("ffprobe")
    out_dir = os.path.abspath(out_dir)          # concat 목록은 절대 경로여야 함 (목록 파일 기준 상대경로로 해석되므로)
    os.makedirs(out_dir, exist_ok=True)
    part_dir = os.path.join(out_dir, "tts_parts"); os.makedirs(part_dir, exist_ok=True)
    tts = Inworld(api_key, voice_id, model, speed, temperature)
    n = len(sentences)
    if groups:
        groups = [(int(a), int(b)) for a, b in groups if 1 <= int(a) <= int(b) <= n]
    else:
        groups = [(i, i) for i in range(1, n + 1)]
    grouped = any(b > a for a, b in groups)
    log(f"   인월드 TTS · 목소리 {voice_id} · {model} · 문장 {n}개" + (f" · {len(groups)}묶음으로 이어 읽기" if grouped else ""))
    done = [0]
    errors = []
    # 예전에 만든 부분 파일에는 문장 텍스트(.txt)가 없다 → 개수가 지금 문장 수와 같을 때만 번호가 안 밀린 것으로 보고 재사용
    legacy_ok = (not grouped) and len([f for f in os.listdir(part_dir) if re.fullmatch(r"\d{4}\.mp3", f)]) == n

    def part_path(a, b):
        return os.path.join(part_dir, f"{a:04d}.mp3" if a == b else f"g{a:04d}_{b:04d}.mp3")

    def work(g):
        if cancel and cancel():
            return
        a, b = g
        text = " ".join(x.strip() for x in sentences[a - 1:b])
        p = part_path(a, b)
        txt = p[:-4] + ".txt"                    # 이 파일이 어떤 문장을 읽은 것인지 — 문장 나누기가 바뀌어 번호가 밀리면 다시 만든다
        try:
            same_text = (open(txt, encoding="utf-8").read().strip() == text) if os.path.isfile(txt) else legacy_ok
        except OSError:
            same_text = False
        if os.path.exists(p) and os.path.getsize(p) > 500 and same_text:
            done[0] += 1; return
        try:
            tts.synth(text, p)
            with open(txt, "w", encoding="utf-8") as f:
                f.write(text)
        except SystemExit as e:
            errors.append(str(e)); raise
        except Exception as e:  # noqa: BLE001
            errors.append(f"{a:04d}: {e}")
            if len(errors) == 1:
                log(f"   ! {a:04d}번 문장 실패: {e}")
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == len(groups):
            log(f"      {done[0]}/{len(groups)}")

    with ThreadPoolExecutor(max_workers=동시_요청) as ex:
        list(ex.map(work, groups))
    if cancel and cancel():
        raise RuntimeError("취소됨")
    fatal = [e for e in errors if "인월드" in e]
    if fatal:
        raise SystemExit(fatal[0])
    if errors:
        log(f"   ! 실패 문장 {len(errors)}개 (다시 실행하면 그 문장만 재시도): " + "; ".join(errors[:3]))

    # 무음 파일
    silence = os.path.join(part_dir, "_silence.mp3")
    if not os.path.exists(silence):
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(문장_간격),
                        "-c:a", "libmp3lame", "-b:a", "64k", silence], check=True)
    # 길이 계산 + SRT + concat 목록
    t = 0.0
    srt, lst, flow = [], [], ["# 이미지번호: 자막번호 (긴 문장은 짧은 한 줄 자막으로 나눔)"]
    for a, b in groups:
        p = part_path(a, b)
        if not os.path.exists(p):
            continue
        tp = p[:-4] + "_t.mp3"                    # 앞뒤 무음을 자르고 긴 쉼을 줄인 파일로 합친다 (원본은 그대로 둔다)
        if not os.path.exists(tp) or os.path.getmtime(tp) < os.path.getmtime(p):
            tighten_pauses(ffmpeg, ffprobe, p, tp)
        p = tp
        total = probe_duration(ffprobe, p)
        texts = sentences[a - 1:b]
        bounds = [0.0] + sentence_boundaries(ffmpeg, p, total, texts) + [total]
        for k, s in enumerate(texts):
            s_start, s_end = t + bounds[k], t + bounds[k + 1]
            d = max(0.05, s_end - s_start)
            cue_start = len(srt) + 1
            chunks = split_subtitle_text(s)
            if int(subtitle_lines or 1) > 1:
                line_count = int(subtitle_lines)
                chunks = ["\n".join(chunks[j:j + line_count]) for j in range(0, len(chunks), line_count)]
            weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
            total_weight = sum(weights)
            elapsed = 0.0
            for chunk_no, (chunk, weight) in enumerate(zip(chunks, weights)):
                start = s_start + elapsed
                elapsed += d * weight / total_weight
                end = s_end if chunk_no == len(chunks) - 1 else s_start + elapsed
                srt.append(f"{len(srt) + 1}\n{fmt_srt(start)} --> {fmt_srt(end)}\n{chunk}\n")
            cue_end = len(srt)
            flow.append(f"{a + k}: {cue_start}" if cue_start == cue_end else f"{a + k}: {cue_start}-{cue_end}")
        lst.append(f"file '{p.replace(os.sep, '/')}'"); lst.append(f"file '{silence.replace(os.sep, '/')}'")
        t += total + 문장_간격
    list_path = os.path.join(part_dir, "_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lst) + "\n")
    mp3 = os.path.join(out_dir, f"{name}.mp3")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_path,
                    "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "24000", mp3], check=True)
    srt_path = os.path.join(out_dir, f"{name}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt))
    flow_path = os.path.join(out_dir, "플로우.txt")
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write("\n".join(flow) + "\n")
    log(f"   ✓ 나레이션 {t / 60:.1f}분 · {mp3}")
    return dict(mp3=mp3, srt=srt_path, flow=flow_path, duration=t, sentences=len(srt))


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: python 나레이션.py "대본 파일" [출력 폴더]')
    with open("설정.json", encoding="utf-8") as f:
        cfg = json.load(f)
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        body = script_body(f.read())
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(sys.argv[1]) or ".", "나레이션")
    r = synthesize(split_sentences(body), out, cfg.get("인월드_API_키", ""), cfg.get("인월드_목소리", ""),
                   cfg.get("인월드_모델", "inworld-tts-1.5-max"), cfg.get("인월드_속도", 1.0))
    print(r)


if __name__ == "__main__":
    main()
