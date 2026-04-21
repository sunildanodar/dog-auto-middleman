from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Callable

from aiohttp import web

from control_panel.log_buffer import append_control_log, get_recent_lines
from control_panel.runtime import (
    get_bot,
    is_auto_mm_enabled,
    is_discord_ready,
    set_auto_mm_enabled,
    uptime_seconds,
)

import database as escrow_db

_control_site_started = False


def _env_bool(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def _expected_key() -> str:
    return os.getenv("BOT_CONTROL_API_KEY", "").strip()


def _panel_channel_id() -> int:
    return int(os.getenv("BOT_CONTROL_PANEL_CHANNEL_ID", "0") or 0)


def _announce_channel_id() -> int:
    return int(os.getenv("BOT_CONTROL_ANNOUNCE_CHANNEL_ID", "0") or 0)


@web.middleware
async def control_api_auth(request: web.Request, handler: Callable) -> web.Response:
    if request.path in ("/health", "/"):
        return await handler(request)
    expected = request.app.get("api_key", "")
    if not expected:
        return web.json_response({"error": "BOT_CONTROL_API_KEY is not set"}, status=503)
    got = request.headers.get("X-Bot-Control-Key", "")
    if got != expected:
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


def _ticket_summary(row: tuple) -> dict[str, Any]:
    return {
        "ticket_id": row[0],
        "channel_id": row[1],
        "buyer_id": row[2],
        "seller_id": row[3],
        "crypto": row[4],
        "amount": row[5],
        "status": row[6],
        "deal_id": row[12] if len(row) > 12 else None,
    }


async def handle_status(request: web.Request) -> web.Response:
    open_rows = escrow_db.get_open_tickets()
    data = {
        "online": is_discord_ready(),
        "uptime_seconds": round(uptime_seconds(), 3),
        "auto_mm_enabled": is_auto_mm_enabled(),
        "active_tickets": [_ticket_summary(r) for r in open_rows],
        "active_tickets_count": len(open_rows),
    }
    return web.json_response(data)


async def handle_logs(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "200"))
    except ValueError:
        limit = 200
    return web.json_response({"lines": get_recent_lines(limit)})


async def handle_mode(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    if "auto_mm" not in body:
        return web.json_response({"error": "missing auto_mm boolean"}, status=400)
    set_auto_mm_enabled(bool(body["auto_mm"]))
    append_control_log(f"[CONTROL_API] auto_mm set to {is_auto_mm_enabled()}")
    return web.json_response({"ok": True, "auto_mm_enabled": is_auto_mm_enabled()})


async def handle_restart(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    use_exec = _env_bool("BOT_CONTROL_RESTART_USE_EXEC")

    async def _do_restart() -> None:
        await asyncio.sleep(0.4)
        try:
            await bot.close()
        except Exception:
            pass
        if use_exec:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            os._exit(0)

    append_control_log("[CONTROL_API] restart requested via API")
    asyncio.create_task(_do_restart())
    return web.json_response({"ok": True, "message": "Restart scheduled (process will exit)"})


async def handle_send_panel(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        body = await request.json() if request.content_type == "application/json" else {}
    except json.JSONDecodeError:
        body = {}
    cid = int(body.get("channel_id") or _panel_channel_id() or 0)
    if not cid:
        return web.json_response({"error": "channel_id required or set BOT_CONTROL_PANEL_CHANNEL_ID"}, status=400)

    deliver = request.app["deliver_panel"]
    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception as exc:
            return web.json_response({"error": f"channel not found: {exc}"}, status=404)
    try:
        await deliver(channel)
    except Exception as exc:
        return web.json_response({"error": str(exc)[:800]}, status=500)
    append_control_log(f"[CONTROL_API] panel sent to channel {cid}")
    return web.json_response({"ok": True, "channel_id": cid})


async def handle_force_close(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    reason = str(body.get("reason") or "Control panel force close")
    all_open = bool(body.get("all_open"))
    ids = body.get("ticket_ids") or []

    log_action = request.app["log_action"]
    unlock_deal = request.app["unlock_deal"]

    closed: list[int] = []
    if all_open:
        rows = escrow_db.get_open_tickets()
        ids = [int(r[0]) for r in rows]
    for raw in ids:
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        ticket = escrow_db.get_ticket(tid)
        if not ticket:
            continue
        escrow_db.update_ticket(tid, status="cancelled")
        try:
            unlock_deal(tid)
        except Exception:
            pass
        log_action("FORCE_CANCEL_CONTROL_API", tid, 0, reason)
        closed.append(tid)
    append_control_log(f"[CONTROL_API] force-closed tickets: {closed}")
    return web.json_response({"ok": True, "closed_ticket_ids": closed})


async def handle_announce(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    content = str(body.get("content") or "").strip()
    if not content:
        return web.json_response({"error": "content required"}, status=400)
    cid = int(body.get("channel_id") or _announce_channel_id() or 0)
    if not cid:
        return web.json_response({"error": "channel_id or BOT_CONTROL_ANNOUNCE_CHANNEL_ID required"}, status=400)
    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception as exc:
            return web.json_response({"error": f"channel not found: {exc}"}, status=404)
    try:
        await channel.send(content=content[:2000])
    except Exception as exc:
        return web.json_response({"error": str(exc)[:800]}, status=500)
    append_control_log(f"[CONTROL_API] announcement sent to {cid}")
    return web.json_response({"ok": True, "channel_id": cid})


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot_ready": is_discord_ready()})


def create_app(
    bot: Any,
    deliver_panel_coroutine: Any,
    log_action_fn: Any,
    unlock_deal_fn: Any,
) -> web.Application:
    app = web.Application(middlewares=[control_api_auth])
    app["bot"] = bot
    app["api_key"] = _expected_key()
    app["deliver_panel"] = deliver_panel_coroutine
    app["log_action"] = log_action_fn
    app["unlock_deal"] = unlock_deal_fn
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/logs", handle_logs)
    app.router.add_post("/mode", handle_mode)
    app.router.add_post("/restart", handle_restart)
    app.router.add_post("/panel/send", handle_send_panel)
    app.router.add_post("/tickets/force-close", handle_force_close)
    app.router.add_post("/announce", handle_announce)
    return app


async def start_control_api(
    bot: Any,
    deliver_panel_coroutine: Any,
    log_action_fn: Any,
    unlock_deal_fn: Any,
) -> None:
    global _control_site_started
    if _control_site_started:
        return
    if not _env_bool("BOT_CONTROL_API_ENABLED", False):
        return
    key = _expected_key()
    if not key:
        print("[CONTROL_API] BOT_CONTROL_API_ENABLED but BOT_CONTROL_API_KEY is empty — not starting.")
        return
    host = os.getenv("BOT_CONTROL_API_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("BOT_CONTROL_API_PORT", "9780"))
    except ValueError:
        port = 9780
    app = create_app(bot, deliver_panel_coroutine, log_action_fn, unlock_deal_fn)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _control_site_started = True
    append_control_log(f"[CONTROL_API] listening on http://{host}:{port}")
    print(f"[CONTROL_API] listening on http://{host}:{port}")
