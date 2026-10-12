"""Install the isolated runtime and download official weights once."""
import os
from pathlib import Path
import subprocess
import sys

def main():
    root = Path(__file__).resolve().parent
    python = root / '.local-voice' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        if sys.version_info >= (3, 14):
            raise SystemExit('Python 3.12 또는 3.13으로 이 설치 파일을 실행하세요. 프로그램의 Python 3.14와 분리해 설치합니다.')
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.local-voice')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', 'qwen-tts==0.1.1'], check=True)
    subprocess.run([str(python), '-c', "from huggingface_hub import snapshot_download; from pathlib import Path; import sys; p=Path(sys.argv[1]); snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base',local_dir=str(p)); import torch; from qwen_tts import Qwen3TTSModel; Qwen3TTSModel.from_pretrained(str(p),device_map='cpu',dtype=torch.float32,attn_implementation='sdpa'); (p/'.ready').write_text('load-verified')", str(root / '로컬음성' / 'models' / 'base')], check=True)
    print('로컬 음성 설치 완료. 설정에서 한국어·일본어 녹음을 각각 저장하세요.')

if __name__ == '__main__':
    main()
