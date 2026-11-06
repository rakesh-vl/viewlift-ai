"""Dashboard tab presence. The last tab to close wipes captured data."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()

_DEFAULT_WIPE_DELAY_S = 2.5


def _sessions(request: Request):
    return request.app.state.browser_sessions


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body


def _session_fields(body: dict[str, Any]) -> tuple[str, int]:
    raw_page = body.get("page")
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        page = 0
    return str(body.get("id") or ""), page


def _cancel_wipe(request: Request) -> None:
    task = getattr(request.app.state, "capture_wipe_task", None)
    if task is not None and not task.done():
        task.cancel()
    request.app.state.capture_wipe_task = None


def schedule_capture_wipe(request: Request) -> None:
    existing = getattr(request.app.state, "capture_wipe_task", None)
    if existing is not None and not existing.done():
        return
    delay = float(getattr(request.app.state, "capture_wipe_delay", _DEFAULT_WIPE_DELAY_S))
    app = request.app

    async def _run() -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if app.state.browser_sessions.alive():
            return
        await asyncio.to_thread(_wipe_capture, app)

    app.state.capture_wipe_task = asyncio.create_task(_run())


def _wipe_capture(app: Any) -> None:
    app.state.store.wipe_session()
    reveal = getattr(app.state, "reveal", None)
    if reveal is not None:
        reveal.wipe()
    tail = getattr(app.state, "log_tail", None)
    if tail is not None:
        tail.stop()
    ingest = getattr(app.state, "ingest", None)
    if ingest is not None:
        ingest.forget_client_activity()
    sessions = getattr(app.state, "debug_sessions", None)
    if sessions is not None:
        sessions.reset_session()


@router.post("/api/session/ping")
async def session_ping(request: Request) -> dict[str, bool]:
    session_id, page = _session_fields(await _json_body(request))
    _sessions(request).ping(session_id, page)
    _cancel_wipe(request)
    return {"ok": True}


@router.post("/api/session/leave")
async def session_leave(request: Request) -> dict[str, bool]:
    session_id, page = _session_fields(await _json_body(request))
    empty = _sessions(request).leave(session_id, page)
    if empty:
        schedule_capture_wipe(request)
    return {"ok": True, "scheduled": empty}
