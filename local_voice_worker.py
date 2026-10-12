"""Runs only in the isolated Qwen environment. Offline during generation."""
import json
import os
from pathlib import Path
import sys

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    progress = Path(sys.argv[2])
    def report(message):
        tmp = progress.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(message=message), ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, progress)
    missing = [c for c in request['cues'] if not Path(c['file']).is_file()]
    if not missing:
        return
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    report('로컬 모델을 불러오는 중입니다. 첫 실행은 시간이 걸립니다.')
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
    model = Qwen3TTSModel.from_pretrained(request['model'], device_map='cpu', dtype=torch.float32, attn_implementation='sdpa')
    prompt = model.create_voice_clone_prompt(ref_audio=request['reference'], ref_text=request['reference_text'], x_vector_only_mode=False)
    for i, cue in enumerate(missing, 1):
        report(f'로컬 복제 음성 {i}/{len(missing)}구간 생성 중')
        waves, rate = model.generate_voice_clone(text=cue['text'], language=request['language'], voice_clone_prompt=prompt, max_new_tokens=2048)
        path = Path(cue['file'])
        temp = path.with_suffix('.tmp.wav')
        sf.write(str(temp), waves[0], rate)
        if len(waves[0]) == 0:
            raise ValueError('Empty generated audio')
        os.replace(temp, path)
    report('로컬 음성 생성 완료 · 자막과 파일을 정리합니다.')

if __name__ == '__main__':
    main()
