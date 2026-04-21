from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import bcrypt
import jwt

from auth_system.models import Role
from auth_system.settings import JWT_ALGORITHM, JWT_SECRET, ACCESS_TOKEN_EXPIRE_DAYS


BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("Password is required")
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS))
    return hashed.decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(*, user_id: int, session_id: str, role: Role) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "jti": session_id,
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def decode_access_token_ignore_exp(token: str) -> Optional[dict[str, Any]]:
    """Validate signature but not exp — used to revoke server session on logout."""
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return None


def is_valid_session_jti(jti: str) -> bool:
    try:
        UUID(jti, version=4)
        return True
    except (ValueError, TypeError, AttributeError):
        return False
