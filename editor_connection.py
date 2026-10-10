"""로컬 편집기 포트 선택. 다른 프로그램에는 설정을 전송하지 않는다."""
import json
import os
import socket
import urllib.request
from pathlib import Path

STATE = Path(__file__).with_name(".editor-port.json")

def editor_port():
    dedicated = os.environ.get("YOUTOO_ACCOUNT")
    if dedicated:
        port = int(os.environ.get("YOUTUBE_EDITOR_PORT", "0"))
        if 18800 <= port < 18900:
            return port
        raise RuntimeError("채널 전용 편집기 연결 정보가 없습니다. 기본 창에서 채널 창을 다시 여세요.")
    try:
        port = int(json.loads(STATE.read_text(encoding="utf-8"))["port"])
        return port if port in [8765, *range(8767, 8777)] else 8765
    except (OSError, ValueError, KeyError, TypeError):
        return 8765

def is_editor(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1) as response:
            data = json.load(response)
        return isinstance(data, dict) and data.get("image_model") == "z-image" and "kie_key_saved" in data
    except Exception:
        return False

def select_editor_port():
    if os.environ.get("YOUTOO_ACCOUNT"):
        return editor_port()
    ports = list(dict.fromkeys([editor_port(), 8765, *range(8767, 8777)]))
    for port in ports:
        if is_editor(port):
            break
    else:
        for port in ports:
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    break
                except OSError:
                    continue
        else:
            raise RuntimeError("편집기용 빈 포트를 찾지 못했습니다.")
    STATE.write_text(json.dumps({"port": port}), encoding="utf-8")
    return port

def editor_url():
    return f"http://127.0.0.1:{editor_port()}"
