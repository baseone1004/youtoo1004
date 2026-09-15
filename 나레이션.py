# -*- coding: utf-8 -*-
"""
나레이션.py — 인월드(Inworld) TTS 로 대본을 문장 단위로 읽고, 합친 mp3 + 문장별 SRT + 플로우 txt 를 만든다.

  문장 1개 = 자막 1개 = 이미지 1장 (DINO 방식). 문장마다 따로 합성하므로 자막 시간이 정확하다.
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
FFMPEG_후보 = [r"C:\Users\baseo\Downloads\DINO_7.5_고객용\필수 프로그램 파일\ffmpeg.exe",
            r"C:\Users\baseo\Downloads\편집프로그램\bin\ffmpeg.exe"]
문장_간격 = 0.35          # 문장 사이 무음(초)
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


def split_sentences(body):
    body = re.sub(r"[?!？！]+", ".", body)
    body = re.sub(r"(\.{2,}|…+)", ".", body)
    body = re.sub(r"\s+", " ", body.strip())
    parts = re.split(r"(?<=[.。])\s+", body)
    return [p.strip() for p in parts if p.strip() and re.search(r"[가-힣a-zA-Z0-9]", p)]


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


def synthesize(sentences, out_dir, api_key, voice_id, model="inworld-tts-1.5-max", speed=1.0, log=print, cancel=None,
               temperature=None, name="나레이션"):
    """문장 목록 → out_dir/나레이션.mp3, 나레이션.srt, 플로우.txt. 이미 있는 문장 파일은 재사용."""
    ffmpeg, ffprobe = find_ffmpeg("ffmpeg"), find_ffmpeg("ffprobe")
    out_dir = os.path.abspath(out_dir)          # concat 목록은 절대 경로여야 함 (목록 파일 기준 상대경로로 해석되므로)
    os.makedirs(out_dir, exist_ok=True)
    part_dir = os.path.join(out_dir, "tts_parts"); os.makedirs(part_dir, exist_ok=True)
    tts = Inworld(api_key, voice_id, model, speed, temperature)
    n = len(sentences)
    log(f"   인월드 TTS · 목소리 {voice_id} · {model} · 문장 {n}개")
    done = [0]
    errors = []

    def work(i):
        if cancel and cancel():
            return
        p = os.path.join(part_dir, f"{i + 1:04d}.mp3")
        if os.path.exists(p) and os.path.getsize(p) > 500:
            done[0] += 1; return
        try:
            tts.synth(sentences[i], p)
        except SystemExit as e:
            errors.append(str(e)); raise
        except Exception as e:  # noqa: BLE001
            errors.append(f"{i + 1:04d}: {e}")
            if len(errors) == 1:
                log(f"   ! {i + 1:04d}번 문장 실패: {e}")
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == n:
            log(f"      {done[0]}/{n}")

    with ThreadPoolExecutor(max_workers=동시_요청) as ex:
        list(ex.map(work, range(n)))
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
    for i, s in enumerate(sentences):
        p = os.path.join(part_dir, f"{i + 1:04d}.mp3")
        if not os.path.exists(p):
            continue
        d = probe_duration(ffprobe, p)
        cue_start = len(srt) + 1
        chunks = split_subtitle_text(s)
        weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
        total_weight = sum(weights)
        elapsed = 0.0
        for chunk_no, (chunk, weight) in enumerate(zip(chunks, weights)):
            start = t + elapsed
            elapsed += d * weight / total_weight
            end = t + d if chunk_no == len(chunks) - 1 else t + elapsed
            srt.append(f"{len(srt) + 1}\n{fmt_srt(start)} --> {fmt_srt(end)}\n{chunk}\n")
        cue_end = len(srt)
        lst.append(f"file '{p.replace(os.sep, '/')}'"); lst.append(f"file '{silence.replace(os.sep, '/')}'")
        flow.append(f"{i + 1}: {cue_start}" if cue_start == cue_end else f"{i + 1}: {cue_start}-{cue_end}")
        t += d + 문장_간격
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
