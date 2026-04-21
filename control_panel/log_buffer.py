from __future__ import annotations

from collections import deque
from threading import Lock

_MAX = 1000
_buffer: deque[str] = deque(maxlen=_MAX)
_lock = Lock()


def append_control_log(line: str) -> None:
    text = (line or "").rstrip()
    if not text:
        return
    with _lock:
        _buffer.append(text)


def get_recent_lines(limit: int = 200) -> list[str]:
    n = max(1, min(int(limit), _MAX))
    with _lock:
        return list(_buffer)[-n:]
