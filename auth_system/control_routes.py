from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth_system.deps import require_staff, try_get_current_user
from auth_system.settings import CONTROL_BOT_API_BASE, CONTROL_BOT_API_KEY

router = APIRouter(prefix="/control", tags=["control"])
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _require_proxy_config() -> None:
    if not CONTROL_BOT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CONTROL_BOT_API_KEY is not set on the web server (.env).",
        )


async def _proxy(method: str, path: str, json_body: Optional[dict] = None) -> Any:
    _require_proxy_config()
    url = f"{CONTROL_BOT_API_BASE}{path}"
    headers = {"X-Bot-Control-Key": CONTROL_BOT_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.request(method, url, headers=headers, json=json_body)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach bot API at {CONTROL_BOT_API_BASE}: {exc}",
        ) from exc
    if r.status_code >= 400:
        detail: Any
        try:
            detail = r.json()
        except json.JSONDecodeError:
            detail = r.text[:2000]
        raise HTTPException(status_code=r.status_code, detail=detail)
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except json.JSONDecodeError:
        return {"raw": r.text[:2000]}


@router.get("", response_class=HTMLResponse)
async def control_dashboard(request: Request, user: Annotated[Optional[dict], Depends(try_get_current_user)]):
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.get("role") not in ("admin", "staff"):
        return templates.TemplateResponse(
            "control_denied.html",
            {"request": request},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return templates.TemplateResponse(
        "control_dashboard.html",
        {
            "request": request,
            "user": user,
            "proxy_configured": bool(CONTROL_BOT_API_KEY),
            "bot_api_base": CONTROL_BOT_API_BASE,
        },
    )


@router.get("/api/status")
async def api_status(_staff: Annotated[dict, Depends(require_staff)]):
    return await _proxy("GET", "/status")


@router.get("/api/logs")
async def api_logs(_staff: Annotated[dict, Depends(require_staff)], limit: int = 200):
    return await _proxy("GET", f"/logs?limit={max(1, min(limit, 500))}")


@router.post("/api/restart")
async def api_restart(_staff: Annotated[dict, Depends(require_staff)]):
    return await _proxy("POST", "/restart")


@router.post("/api/mode")
async def api_mode(_staff: Annotated[dict, Depends(require_staff)], body: dict = Body(...)):
    return await _proxy("POST", "/mode", body)


@router.post("/api/panel/send")
async def api_panel_send(
    _staff: Annotated[dict, Depends(require_staff)],
    body: Optional[dict] = Body(default=None),
):
    return await _proxy("POST", "/panel/send", body or {})


@router.post("/api/tickets/force-close")
async def api_force_close(_staff: Annotated[dict, Depends(require_staff)], body: dict = Body(...)):
    return await _proxy("POST", "/tickets/force-close", body)


@router.post("/api/announce")
async def api_announce(_staff: Annotated[dict, Depends(require_staff)], body: dict = Body(...)):
    return await _proxy("POST", "/announce", body)
