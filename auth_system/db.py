from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from auth_system.models import Role, SubscriptionTier
from auth_system.settings import AUTH_DB_PATH, SUBSCRIPTION_PLAN_DAYS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@contextmanager
def get_connection():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                subscription_tier TEXT NOT NULL,
                subscription_expires_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "role": row["role"],
        "subscription_tier": row["subscription_tier"],
        "subscription_expires_at": row["subscription_expires_at"],
        "created_at": row["created_at"],
    }


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[dict[str, Any]]:
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[dict[str, Any]]:
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),))
    row = c.fetchone()
    return _row_to_user(row) if row else None


def create_user(
    conn: sqlite3.Connection,
    email: str,
    password_hash: str,
    role: Role,
    subscription_tier: SubscriptionTier = SubscriptionTier.FREE,
    subscription_expires_at: Optional[datetime] = None,
) -> int:
    now = _utcnow()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO users (email, password_hash, role, subscription_tier, subscription_expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            email.strip().lower(),
            password_hash,
            role.value,
            subscription_tier.value,
            _iso(subscription_expires_at),
            _iso(now),
        ),
    )
    return int(c.lastrowid)


def subscription_is_expired(tier: str, expires_at_raw: Optional[str]) -> bool:
    if tier in (SubscriptionTier.FREE.value,):
        return False
    exp = _parse_iso(expires_at_raw)
    if exp is None:
        return False
    return _utcnow() > exp


def maybe_downgrade_expired_subscription(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    if not subscription_is_expired(user["subscription_tier"], user["subscription_expires_at"]):
        return user
    c = conn.cursor()
    c.execute(
        """
        UPDATE users
        SET subscription_tier = ?, subscription_expires_at = NULL
        WHERE id = ?
        """,
        (SubscriptionTier.FREE.value, user["id"]),
    )
    user = dict(user)
    user["subscription_tier"] = SubscriptionTier.FREE.value
    user["subscription_expires_at"] = None
    return user


def set_user_subscription(
    conn: sqlite3.Connection,
    user_id: int,
    tier: SubscriptionTier,
    *,
    plan_months: int = 1,
) -> None:
    """Set paid tier with expiry: each plan month uses AUTH_SUBSCRIPTION_PLAN_DAYS (default 30)."""
    if tier == SubscriptionTier.FREE:
        expires = None
    else:
        months = max(1, plan_months)
        expires = _utcnow() + timedelta(days=SUBSCRIPTION_PLAN_DAYS * months)
    c = conn.cursor()
    c.execute(
        """
        UPDATE users
        SET subscription_tier = ?, subscription_expires_at = ?
        WHERE id = ?
        """,
        (tier.value, _iso(expires), user_id),
    )


def create_session(conn: sqlite3.Connection, user_id: int, expires_at: datetime) -> str:
    sid = str(uuid4())
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO sessions (id, user_id, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (sid, user_id, _iso(expires_at), _iso(_utcnow())),
    )
    return sid


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[dict[str, Any]]:
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = c.fetchone()
    if not row:
        return None
    exp = _parse_iso(row["expires_at"])
    if exp and _utcnow() > exp:
        c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return None
    return {"id": row["id"], "user_id": row["user_id"], "expires_at": row["expires_at"]}


def delete_expired_sessions(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_utcnow()),))
