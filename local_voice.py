"""Private voice profiles and local narration; no cloud synthesis fallback."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

MODEL = 'Qwen/Qwen3-TTS-12Hz-0.6B-Base'
SCRIPTS = {
    'ko': '안녕하세요. 마음읽기연구소입니다. 오늘은 말로 다 전하지 못한 마음을 천천히 살펴보겠습니다. 가까운 사람과 이야기를 나누다가, 같은 말을 서로 다르게 받아들인 적이 있으신가요? 그럴 때는 누가 옳은지 서둘러 판단하기보다, 그 말 뒤에 어떤 경험이 있었는지 생각해 보면 좋겠습니다. 잠시 숨을 고르고, 내 마음에도 귀를 기울여 주세요. 작은 이해가 쌓이면 우리의 대화도 조금씩 편안해질 수 있습니다.',
    'ja': 'こんにちは。今日は、言葉だけでは伝わりにくい気持ちについて、ゆっくり考えてみましょう。身近な人と話していて、同じ言葉なのに受け取り方が違うと感じたことはありませんか。そんなときは、どちらが正しいかを急いで決めずに、その人がどんな経験をしてきたのか、少し想像してみてください。ひと息ついて、自分の気持ちにも耳を傾けてみましょう。小さな理解の積み重ねが、毎日の会話を少しずつ穏やかにしてくれるかもしれません。',
}

def storage(root):
    return Path(root).resolve() / '로컬음성'

def python_path(root):
    return Path(root) / '.local-voice' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

def profile(root, language):
    if language not in SCRIPTS:
        raise ValueError('로컬 복제 음성은 한국어·일본어를 선택하세요.')
    path = storage(root) / language / 'profile.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        audio = path.parent / value['audio']
        if not audio.is_file() or audio.parent != path.parent or not value.get('text'):
            return None
        return dict(value, audio_path=str(audio))
    except (OSError, ValueError, KeyError):
        return None

def status(root):
    model = storage(root) / 'models' / 'base'
    ready = python_path(root).is_file() and (model / 'config.json').is_file() and (model / '.ready').is_file()
    previews = {}
    for lang in SCRIPTS:
        preview = storage(root) / lang / 'preview'
        try:
            revision = json.loads((preview / 'local-profile.json').read_text(encoding='utf-8'))['revision']
            current = profile(root, lang)
            previews[lang] = str(preview / '나레이션.mp3') if current and current['revision'] == revision and (preview / '나레이션.mp3').is_file() else ''
        except (OSError, ValueError, KeyError):
            previews[lang] = ''
    return dict(runtime_ready=ready, profiles={lang: bool(profile(root, lang)) for lang in SCRIPTS}, scripts=SCRIPTS,
                previews=previews,
                engine='Qwen3-TTS 0.6B · CPU', note='로컬 생성은 API 크레딧을 사용하지 않습니다. 긴 대본은 CPU에서 오래 걸릴 수 있습니다.')

def save_profile(root, language, encoded, text):
    import 나레이션
    if language not in SCRIPTS or not str(text).strip() or len(text) > 3000:
        raise ValueError('언어와 녹음에서 실제로 읽은 대본을 입력하세요.')
    try:
        data = base64.b64decode(encoded.split(',')[-1], validate=True)
    except (ValueError, TypeError):
        raise ValueError('음성 파일을 다시 선택하세요.') from None
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError('10MB 이하 녹음 파일을 선택하세요.')
    directory = storage(root) / language
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        src, wav = Path(tmp) / 'input', Path(tmp) / 'reference.wav'
        src.write_bytes(data)
        try:
            subprocess.run([나레이션.find_ffmpeg('ffmpeg'), '-v', 'error', '-y', '-i', str(src), '-vn', '-ar', '24000', '-ac', '1', str(wav)], check=True, capture_output=True, timeout=60)
            duration = 나레이션.probe_duration(나레이션.find_ffmpeg('ffprobe'), str(wav))
        except (subprocess.SubprocessError, OSError):
            raise ValueError('녹음 파일을 읽지 못했습니다. WAV·MP3·M4A 파일을 선택하세요.') from None
        if not 3 <= duration <= 60:
            raise ValueError('배경음악 없는 3~60초 녹음을 올려 주세요. 약 30초를 권장합니다.')
        revision = hashlib.sha256(wav.read_bytes() + text.strip().encode()).hexdigest()
        # Versioned files keep an in-progress job's reference intact.
        audio_name = revision + '.wav'
        os.replace(wav, directory / audio_name)
        payload = dict(audio=audio_name, text=text.strip(), revision=revision, duration=duration)
        temp = directory / 'profile.tmp'
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, directory / 'profile.json')
    return dict(ok=True, duration=duration)

def synthesize(root, sentences, out_dir, language, log=print, cancel=lambda: False):
    import 나레이션
    from process_lock import file_lock
    voice = profile(root, language)
    if not voice:
        raise ValueError('설정에서 해당 언어의 복제용 녹음과 읽은 대본을 먼저 저장하세요.')
    if not status(root)['runtime_ready']:
        raise ValueError('로컬 음성 모델 설치가 필요합니다. 로컬음성_설치.py를 실행하세요.')
    out = Path(out_dir).resolve()
    parts = out / '로컬_음성조각'
    parts.mkdir(parents=True, exist_ok=True)
    cues = []
    for number, sentence in enumerate(sentences, 1):
        for text in 나레이션.split_subtitle_text(sentence):
            digest = hashlib.sha256((voice['revision'] + language + text + MODEL).encode()).hexdigest()
            cues.append(dict(sentence=number, text=text, file=str(parts / (digest + '.wav'))))
    if not cues:
        raise ValueError('낭독할 대본이 비어 있습니다.')
    request = dict(model=str(storage(root) / 'models' / 'base'), reference=voice['audio_path'],
                   reference_text=voice['text'], language='Japanese' if language == 'ja' else 'Korean', cues=cues)
    req = parts / 'request.json'
    progress = parts / 'progress.json'
    req.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    progress.unlink(missing_ok=True)
    log('   로컬 복제 음성 · CPU · API 크레딧 사용 없음. 자막 구간별 실제 음성 길이로 시간을 맞춥니다.')
    with file_lock(storage(root) / '.generation.lock', timeout=1):
        with (parts / 'worker.log').open('w', encoding='utf-8') as output:
            proc = subprocess.Popen([str(python_path(root)), str(Path(__file__).with_name('local_voice_worker.py')), str(req), str(progress)],
                                    stdout=output, stderr=output, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            last = None
            try:
                while proc.poll() is None:
                    if cancel():
                        raise RuntimeError('사용자가 로컬 음성 제작을 중단했습니다.')
                    try:
                        info = json.loads(progress.read_text(encoding='utf-8'))
                        message = info.get('message')
                        if message and message != last:
                            log('   ' + message); last = message
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.5)
                if proc.returncode:
                    raise RuntimeError('로컬 음성 생성 실패. 작업 폴더의 로컬_음성조각/worker.log를 확인하세요. 인월드로 자동 전환하지 않습니다.')
            finally:
                if proc.poll() is None:
                    if os.name == 'nt':
                        # Windows venv python.exe can launch a second python process.
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=15)
                    else:
                        proc.terminate()
                    proc.wait(timeout=15)
    result = assemble(cues, out, log, cancel)
    result['voice_revision'] = voice['revision']
    (out / 'local-profile.json').write_text(json.dumps(dict(revision=voice['revision'])), encoding='utf-8')
    return result

def assemble(cues, out, log=print, cancel=lambda: False):
    """Generate captions from measured cue audio, preserving sentence→cue flow."""
    import 나레이션
    ffmpeg, ffprobe = 나레이션.find_ffmpeg('ffmpeg'), 나레이션.find_ffmpeg('ffprobe')
    subtitles, flow, files = [], {}, []
    elapsed = 0.0
    for i, cue in enumerate(cues, 1):
        if cancel():
            raise RuntimeError('취소됨')
        audio = Path(cue['file'])
        duration = 나레이션.probe_duration(ffprobe, str(audio))
        if duration <= 0 or duration > 90:
            raise ValueError('로컬 음성 조각의 길이가 잘못됐습니다.')
        subtitles.append(f'{i}\n{나레이션.fmt_srt(elapsed)} --> {나레이션.fmt_srt(elapsed + duration)}\n{cue["text"]}\n')
        flow.setdefault(cue['sentence'], []).append(i)
        # ffconcat syntax escapes single quotes; filenames are generated hashes.
        files.append("file '" + str(audio).replace('\\', '/').replace("'", "'\\''") + "'")
        elapsed += duration
    listing = out / '로컬_음성조각' / 'concat.txt'
    listing.write_text('\n'.join(files), encoding='utf-8')
    mp3, srt, mapping = out / '나레이션.mp3', out / '나레이션.srt', out / '플로우.txt'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c:a', 'libmp3lame', '-b:a', '192k', str(mp3)], check=True)
    srt.write_text('\n'.join(subtitles), encoding='utf-8')
    mapping.write_text('\n'.join(f'{n}: {v[0]}' if len(v) == 1 else f'{n}: {v[0]}-{v[-1]}' for n, v in flow.items()) + '\n', encoding='utf-8')
    sync = 나레이션.verify_subtitle_sync(str(srt), 나레이션.probe_duration(ffprobe, str(mp3)), 'audio-cue')
    if not sync['ok']:
        raise ValueError('로컬 음성의 자막 시간 검사 실패: ' + '; '.join(sync['issues']))
    (out / '나레이션_싱크.json').write_text(json.dumps(sync, ensure_ascii=False), encoding='utf-8')
    log(f'   ✓ 로컬 음성·자막 {len(cues)}구간 · {elapsed / 60:.1f}분')
    return dict(mp3=str(mp3), srt=str(srt), flow=str(mapping), duration=elapsed, sentences=len(cues), sync=sync)
