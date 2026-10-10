"""OS-owned file locks: released automatically if a process exits."""
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path, cancelled=None, waiting=None, timeout=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0"); handle.flush()
        acquired = False
        try:
            while not acquired:
                if cancelled and cancelled():
                    raise RuntimeError("취소됨")
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if timeout is not None and time.monotonic() - start >= timeout:
                        raise RuntimeError("다른 창에서 준비 중입니다. 잠시 뒤 다시 눌러 주세요.") from None
                    if waiting:
                        waiting()
                    time.sleep(0.25)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
