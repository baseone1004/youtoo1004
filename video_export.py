"""Export final videos to the user's actual Windows desktop."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

def desktop():
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            value = winreg.QueryValueEx(key, 'Desktop')[0]
            return Path(os.path.expandvars(value)).resolve()
    return Path.home() / 'Desktop'

def export(source, script, channel, assets):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(channel)).strip(' .')[:80] or '채널'
    directory = desktop() / '영상' / name
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(script).stem).strip(' .')[:100]
    digest = hashlib.sha256(str(Path(script).resolve()).encode()).hexdigest()[:8]
    target = directory / f'{stem}_{digest}.mp4'
    temp = target.with_suffix('.tmp')
    shutil.copy2(source, temp)
    os.replace(temp, target)
    metadata = Path(assets) / '.desktop-export.json'
    metadata.write_text(json.dumps(dict(path=str(target), source=str(Path(source).resolve())), ensure_ascii=False), encoding='utf-8')
    return str(target)

def saved(assets):
    try:
        value = json.loads((Path(assets) / '.desktop-export.json').read_text(encoding='utf-8'))
        path = Path(value['path']).resolve()
        if path.is_relative_to((desktop() / '영상').resolve()) and path.suffix == '.mp4' and path.is_file():
            return str(path)
    except (OSError, ValueError, KeyError):
        pass
    return ''

def is_registered(root, path):
    target = str(Path(path).resolve())
    return any(saved(p.parent) == target for p in (Path(root) / '대본').rglob('.desktop-export.json'))
