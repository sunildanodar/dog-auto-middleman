from __future__ import annotations

import time
from typing import Any, Optional

_bot_ref: Optional[Any] = None
_ready_monotonic: Optional[float] = None
_auto_mm_enabled: bool = True


def attach_bot(bot: Any) -> None:
    global _bot_ref
    _bot_ref = bot


def get_bot() -> Optional[Any]:
    return _bot_ref


def mark_ready() -> None:
    global _ready_monotonic
    if _ready_monotonic is None:
        _ready_monotonic = time.monotonic()


def uptime_seconds() -> float:
    if _ready_monotonic is None:
        return 0.0
    return time.monotonic() - _ready_monotonic


def is_discord_ready() -> bool:
    b = _bot_ref
    if b is None:
        return False
    try:
        return bool(b.is_ready())
    except Exception:
        return False


def is_auto_mm_enabled() -> bool:
    return _auto_mm_enabled


def set_auto_mm_enabled(value: bool) -> None:
    global _auto_mm_enabled
    _auto_mm_enabled = bool(value)
