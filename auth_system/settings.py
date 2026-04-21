from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

_BASE = Path(__file__).resolve().parent.parent
_ENV = _BASE / ".env"
_EXAMPLE = _BASE / ".env.example"
if _ENV.exists():
    load_dotenv(_ENV)
elif _EXAMPLE.exists():
    load_dotenv(_EXAMPLE)
else:
    load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


AUTH_DB_PATH = os.getenv("AUTH_DB_PATH", str(_BASE / "auth_users.db"))

_raw_secret = os.getenv("AUTH_JWT_SECRET", "").strip()
if _raw_secret:
    JWT_SECRET = _raw_secret
else:
    JWT_SECRET = secrets.token_hex(32)
    if not _env_bool("AUTH_ALLOW_INSECURE_JWT", False):
        raise RuntimeError(
            "Set AUTH_JWT_SECRET in .env for stable tokens across restarts, "
            "or set AUTH_ALLOW_INSECURE_JWT=true only for local development."
        )

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = _env_int("AUTH_ACCESS_TOKEN_DAYS", 7)
SUBSCRIPTION_PLAN_DAYS = _env_int("AUTH_SUBSCRIPTION_PLAN_DAYS", 30)

COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "auth_token")
COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", False)

ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.getenv("AUTH_ADMIN_EMAILS", "").split(",")
    if e.strip()
}
STAFF_EMAILS = {
    e.strip().lower()
    for e in os.getenv("AUTH_STAFF_EMAILS", "").split(",")
    if e.strip()
}

# System 2: web UI proxies to the bot process HTTP API (same key as BOT_CONTROL_API_KEY)
CONTROL_BOT_API_BASE = os.getenv("CONTROL_BOT_API_BASE", "http://127.0.0.1:9780").rstrip("/")
CONTROL_BOT_API_KEY = os.getenv("CONTROL_BOT_API_KEY", "").strip()
