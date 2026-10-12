"""File bridge for user-directed Codex text/image generation; no paid API fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

try:
    from process_lock import file_lock
except ImportError:
    from core.process_lock import file_lock


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def wait_result(folder, kind, payload, cancelled=lambda: False, timeout=7200, interval=1):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(json.dumps([kind, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = folder / (identity + '.json')
    with file_lock(folder / '.lock', timeout=10):
        record = read(path)
        if record.get('status') != 'done':
            record = {'id': identity, 'kind': kind, 'payload': payload, 'status': 'waiting',
                      'token': uuid.uuid4().hex, 'created': time.time()}
            write(path, record)
        token = record.get('token')
    deadline = time.monotonic() + timeout
    while True:
        with file_lock(folder / '.lock', timeout=10):
            record = read(path)
            if not record or record.get('token') != token or record.get('status') == 'cancelled':
                raise RuntimeError('Codex 요청이 중단·초기화되었습니다. 이어 만들기 후 새 요청을 사용하세요.')
            if cancelled():
                record['status'] = 'cancelled'
                write(path, record)
                raise RuntimeError('사용자가 중단했습니다. Codex 결과 연결 대기를 멈췄습니다.')
            if record.get('status') == 'done':
                result = folder / (identity + ('.png' if kind == 'image' else '.txt'))
                if not result.is_file():
                    raise RuntimeError('Codex 결과 파일이 없습니다. 요청을 다시 준비해 주세요.')
                return result if kind == 'image' else result.read_text(encoding='utf-8')
            if record.get('status') == 'error':
                raise RuntimeError('Codex 연결이 중단됐습니다. 도구와 사용 한도를 확인한 뒤 이어 만들기를 누르세요.')
            if time.monotonic() >= deadline:
                record['status'] = 'cancelled'
                write(path, record)
                raise RuntimeError('Codex 결과 대기 시간이 지났습니다. 이어 만들기 후 요청문을 다시 전달하세요.')
        time.sleep(interval)


def pending(root):
    root = Path(root).resolve()
    records = []
    for path in (root / '대본').glob('**/.codex-requests/*.json'):
        if not path.resolve().is_relative_to(root / '대본'):
            continue
        value = read(path)
        if value.get('status') == 'waiting':
            records.append((path, value))
    return sorted(records, key=lambda x: x[1].get('created', 0))


def cancel_all(root):
    for path, _ in pending(root):
        with file_lock(path.parent / '.lock', timeout=10):
            record = read(path)
            if record.get('status') == 'waiting':
                record['status'] = 'cancelled'
                write(path, record)


def complete(root, identity, token, source=None, failed=False):
    if not re.fullmatch(r'[a-f0-9]{64}', identity) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('요청 번호를 확인하세요.')
    candidates = [(p, v) for p, v in pending(root) if v.get('id') == identity]
    if len(candidates) != 1:
        raise ValueError('현재 대기 중인 요청만 등록할 수 있습니다. 중단·초기화된 요청은 사용할 수 없습니다.')
    path, _ = candidates[0]
    with file_lock(path.parent / '.lock', timeout=10):
        record = read(path)
        if record.get('status') != 'waiting' or record.get('token') != token:
            raise ValueError('이전 요청의 결과입니다. 현재 요청 번호와 토큰을 다시 읽으세요.')
        if failed:
            record['status'] = 'error'
            write(path, record)
            return
        source = Path(source).resolve()
        dest = path.with_suffix('.png' if record['kind'] == 'image' else '.txt')
        temp = dest.with_suffix('.tmp')
        try:
            if record['kind'] == 'image':
                from PIL import Image, ImageOps
                with Image.open(source) as image:
                    if image.width * image.height > 40_000_000 or image.width < 64 or image.height < 64:
                        raise ValueError('이미지 크기를 확인하세요.')
                    image.load()
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    image.save(temp, format='PNG')
            else:
                if source.stat().st_size > 2_000_000:
                    raise ValueError('텍스트 결과가 너무 큽니다.')
                text = source.read_text(encoding='utf-8-sig').strip()
                if not text:
                    raise ValueError('빈 결과는 등록할 수 없습니다.')
                temp.write_text(text, encoding='utf-8')
            os.replace(temp, dest)
            record['status'] = 'done'
            write(path, record)
        finally:
            temp.unlink(missing_ok=True)


def request_message(root):
    root = Path(root).resolve()
    return (f'이 폴더의 Codex 제작 요청을 처리해 주세요: {root}\n'
            'codex_bridge.py 도우미를 사용하세요. --root 폴더 next로 현재 요청을 읽고, '
            'kind=text이면 system/user 지침에 맞춰 결과만 UTF-8 TXT로 저장하세요. '
            '자료 조사 요청은 실제 출처를 확인하고 URL을 꾸며내지 마세요. '
            'kind=image이면 내장 이미지 생성 도구로 prompt와 reference_image를 사용해 16:9 이미지 한 장을 만드세요. '
            '내장 도구가 없거나 한도에 막히면 fail로 알리고 멈추세요. 유료 API로 대신 생성하지 마세요.\n'
            'complete --id 요청번호 --token 현재토큰 --source 실제결과파일로 등록하고 next를 다시 읽으세요. '
            '결과의 언어·장면 번호를 바꾸지 말고 완료한 장면을 재생성하지 마세요. '
            'next가 비어도 프로그램이 다음 요청을 준비할 수 있으므로 status로 현재 단계도 확인하세요. '
            '완성되거나 중단되면 끝내세요. 설정·키를 읽거나 노출하지 마세요.\n'
            f'도우미: {root / "codex_bridge.py"}\n'
            '모든 명령에 --root 폴더를 지정하세요. 프로그램이 음성·영상 API를 사용하는 단계는 별도 비용이 듭니다.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('action', choices=['next', 'status', 'complete', 'fail'])
    parser.add_argument('--id')
    parser.add_argument('--token')
    parser.add_argument('--source')
    args = parser.parse_args()
    if args.action in ('complete', 'fail'):
        complete(args.root, args.id or '', args.token or '', args.source, args.action == 'fail')
        print(json.dumps({'ok': True}))
    else:
        rows = pending(args.root)
        value = rows[0][1] if args.action == 'next' and rows else {'pending': len(rows)}
        if args.action == 'status':
            import urllib.request
            try:
                with urllib.request.urlopen('http://127.0.0.1:8766/api/job', timeout=2) as response:
                    job = json.load(response)
                value['program'] = {k: job.get(k) for k in ('status', 'stage')}
            except (OSError, ValueError):
                value['program'] = {'status': 'offline'}
        print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
