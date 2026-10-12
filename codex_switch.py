"""One-shot local setting transition after a verified production completes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
from codex_bridge import read, write
from process_lock import file_lock


def state_file(root):
    return Path(root).resolve() / '대본' / '_상태' / 'codex-switch.json'


def call(port, path, body=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def resume_reserved(root):
    root = Path(root).resolve()
    value = read(state_file(root))
    if value.get('status') != 'waiting':
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--root', str(root), '--token', value['token']],
                     cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def reserve(root, port=8766, launch=True):
    root = Path(root).resolve()
    queue, job = call(port, '/api/queue'), call(port, '/api/job')
    active = next((i for i in queue.get('items', []) if i.get('status') == 'working'), None)
    if not active and (job.get('status') != 'running' or job.get('kind') != 'pipeline'):
        raise ValueError('현재 제작 중인 영상이 없습니다. Codex 제작 버튼으로 바로 전환하세요.')
    cfg = read(root / '설정.json')
    path = state_file(root)
    with file_lock(path.with_suffix('.lock'), timeout=5):
        value = read(path)
        if value.get('status') == 'waiting':
            return value
        value = {'status': 'waiting', 'token': uuid.uuid4().hex, 'port': port,
                 'account': cfg.get('유튜브_선택_person'), 'item': active.get('id') if active else '',
                 'started': job.get('started'), 'created': time.time()}
        write(path, value)
    if launch:
        resume_reserved(root)
    return value


def tick(root, token):
    root = Path(root).resolve()
    path = state_file(root)
    with file_lock(path.with_suffix('.lock'), timeout=5):
        value = read(path)
        if value.get('token') != token or value.get('status') != 'waiting':
            return True
        cfg = read(root / '설정.json')
        if cfg.get('유튜브_선택_person') != value.get('account'):
            value.update(status='cancelled', reason='선택 채널이 바뀌었습니다.')
            write(path, value)
            return True
        queue, job = call(value['port'], '/api/queue'), call(value['port'], '/api/job')
        if value['item']:
            item = next((i for i in queue.get('items', []) if i.get('id') == value['item']), None)
            if item is None or item.get('status') == 'cancelled':
                value.update(status='cancelled', reason='대상 제작이 취소되었습니다.')
                write(path, value)
                return True
            finished = item.get('status') == 'done'
        else:
            finished = job.get('started') == value['started'] and job.get('status') == 'done'
        if not finished or job.get('status') == 'running' or queue.get('status') == 'running':
            return False
        result = (item.get('result') or {}) if value['item'] else (job.get('result') or {})
        if result.get('video') and result.get('script'):
            source, script = Path(result['video']).resolve(), Path(result['script']).resolve()
            if source.is_file() and source.is_relative_to(root / '대본') and script.is_relative_to(root / '대본'):
                import video_export
                import 채널_프로필
                result['desktop_video'] = video_export.export(source, script, 채널_프로필.name('person'), source.parent)
        call(value['port'], '/api/codex/mode', {})
        # Older running servers turned motion off when enabling Codex.
        call(value['port'], '/api/config', {'후킹_장면수': 5})
        value.update(status='done', completed=time.time())
        write(path, value)
        if value.get('refresh_backend'):
            try:
                call(value['port'], '/api/restart', {})
            except OSError:
                pass  # Transition is saved even if the restart closes the connection.
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--token', required=True)
    args = parser.parse_args()
    try:
        with file_lock(state_file(args.root).with_suffix('.worker.lock'), timeout=10):
            while True:
                try:
                    if tick(args.root, args.token):
                        return
                except (OSError, ValueError):
                    pass  # Keep a reservation across a local server restart.
                time.sleep(5)
    except RuntimeError:
        return  # A worker already owns this reservation.


if __name__ == '__main__':
    main()
