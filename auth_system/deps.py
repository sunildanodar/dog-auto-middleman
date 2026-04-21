from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Cookie, Depends, HTTPException, status

from auth_system import db
from auth_system.security import decode_access_token, is_valid_session_jti
from auth_system.settings import COOKIE_NAME


def get_optional_token(
    auth_token: Annotated[Optional[str], Cookie(alias=COOKIE_NAME)] = None,
) -> Optional[str]:
    if auth_token and auth_token.strip():
        return auth_token.strip()
    return None


def _session_user_from_token(token: str) -> Optional[dict]:
    payload = decode_access_token(token)
    if not payload:
        return None
    jti = payload.get("jti")
    if not jti or not is_valid_session_jti(str(jti)):
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    with db.get_connection() as conn:
        sess = db.get_session(conn, str(jti))
        if not sess or int(sess["user_id"]) != user_id:
            return None
        user = db.get_user_by_id(conn, user_id)
        if not user:
            return None
        user = db.maybe_downgrade_expired_subscription(conn, user)
        return {
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "subscription_tier": user["subscription_tier"],
            "subscription_expires_at": user["subscription_expires_at"],
            "created_at": user["created_at"],
        }


def try_get_current_user(
    token: Annotated[Optional[str], Depends(get_optional_token)],
) -> Optional[dict]:
    if not token:
        return None
    return _session_user_from_token(token)


def get_current_user(
    token: Annotated[Optional[str], Depends(get_optional_token)],
) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = _session_user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    return user


def require_staff(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff or admin access required")
    return user
