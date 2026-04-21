from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth_system import db
from auth_system.deps import get_optional_token, try_get_current_user
from auth_system.models import Role, SubscriptionTier
from auth_system.security import (
    create_access_token,
    decode_access_token_ignore_exp,
    hash_password,
    verify_password,
)
from auth_system.settings import (
    ACCESS_TOKEN_EXPIRE_DAYS,
    ADMIN_EMAILS,
    COOKIE_NAME,
    COOKIE_SECURE,
    STAFF_EMAILS,
)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

MIN_PASSWORD_LEN = 8


def _resolve_role_for_email(email: str) -> Role:
    lowered = email.strip().lower()
    if lowered in ADMIN_EMAILS:
        return Role.ADMIN
    if lowered in STAFF_EMAILS:
        return Role.STAFF
    return Role.CUSTOMER


def _role_from_user_row(role_str: str) -> Role:
    try:
        return Role(role_str)
    except ValueError:
        return Role.CUSTOMER


def _set_auth_cookie(response: Response, token: str) -> None:
    max_age = ACCESS_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=max_age,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": error},
    )


@router.post("/register")
async def register_submit(
    request: Request,
    response: Response,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()] = "",
):
    email = (email or "").strip()
    if not email or "@" not in email:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Enter a valid email address."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(password or "") < MIN_PASSWORD_LEN:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if password_confirm and password != password_confirm:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Passwords do not match."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    role = _resolve_role_for_email(email)
    pwd_hash = hash_password(password)

    with db.get_connection() as conn:
        if db.get_user_by_email(conn, email):
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "An account with this email already exists."},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        user_id = db.create_user(conn, email, pwd_hash, role, SubscriptionTier.FREE, None)
        expires_at = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
        sid = db.create_session(conn, user_id, expires_at)
        token = create_access_token(user_id=user_id, session_id=sid, role=role)

    _set_auth_cookie(response, token)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    email = (email or "").strip()
    with db.get_connection() as conn:
        user = db.get_user_by_email(conn, email)
        if not user or not verify_password(password or "", user["password_hash"]):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid email or password."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        user = db.maybe_downgrade_expired_subscription(conn, user)
        role = _role_from_user_row(user["role"])
        expires_at = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
        sid = db.create_session(conn, int(user["id"]), expires_at)
        token = create_access_token(user_id=int(user["id"]), session_id=sid, role=role)

    _set_auth_cookie(response, token)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(response: Response, token: Annotated[Optional[str], Depends(get_optional_token)]):
    if token:
        payload = decode_access_token_ignore_exp(token)
        jti = payload.get("jti") if payload else None
        if jti:
            with db.get_connection() as conn:
                db.delete_session(conn, str(jti))
    _clear_auth_cookie(response)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Annotated[Optional[dict], Depends(try_get_current_user)],
):
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    expires = user.get("subscription_expires_at")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "subscription_expires_display": expires or "—",
        },
    )
