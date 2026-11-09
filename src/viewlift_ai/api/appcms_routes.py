"""AppCMS and generic host left-menu pages: live sections, diagnostics, and confirmed actions."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from viewlift_ai.api.debug_routes import (
    TEMPLATES,
    _json_body,
    _loopback,
    _mutating,
    _sessions,
)

router = APIRouter()

# (slug, label, host section name or None for dashboard-only pages)
APPCMS_PAGES: tuple[tuple[str, str, str | None], ...] = (
    ("overview", "Overview", "overview"),
    ("config", "App config and flags", "config"),
    ("environment", "Environment and variables", "environment"),
    ("pages", "Pages and navigation", "pages"),
    ("session", "User and session", "session"),
    ("subscription", "Subscription and plans", "subscription"),
    ("entitlements", "Entitlements", "entitlements"),
    ("tve", "TVE", "tve"),
    ("devices", "Devices and IDs", "devices"),
    ("analytics", "Analytics and beacon", "analytics"),
    ("player", "Player", "player"),
    ("diagnostics", "Diagnostics", None),
    ("actions", "Actions", None),
    ("compare", "Compare apps", None),
)
PAGE_SLUGS = frozenset(slug for slug, _label, _section in APPCMS_PAGES)
READ_NAMES = frozenset({"ott.sections", "ott.section", "ott.diagnostics", "ott.snapshot"})
# Host pages come from the app's ott.sections manifest; these three are built into the dashboard.
HOST_TOOL_PAGES: tuple[tuple[str, str], ...] = (
    ("diagnostics", "Diagnostics"),
    ("actions", "Actions"),
    ("compare", "Compare apps"),
)
HOST_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _page(request: Request, slug: str) -> HTMLResponse:
    if slug not in PAGE_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown AppCMS page")
    return TEMPLATES.TemplateResponse(
        request,
        "appcms.html",
        {
            "title": "AppCMS",
            "mode": "appcms",
            "active_nav": "appcms",
            "active_page": slug,
            "pages": APPCMS_PAGES,
            "mutating_enabled": _mutating(request),
        },
    )


def _host_page(request: Request, slug: str) -> HTMLResponse:
    if slug and not HOST_SLUG.match(slug):
        raise HTTPException(status_code=404, detail="Unknown host page")
    tools = dict(HOST_TOOL_PAGES)
    section = "" if not slug or slug in tools else slug
    title = tools.get(slug) or slug.replace("_", " ").capitalize() or "Host debug"
    return TEMPLATES.TemplateResponse(
        request,
        "appcms.html",
        {
            "title": "Hosts",
            "mode": "host",
            "active_nav": "host",
            "active_page": slug,
            "host_section": section,
            "host_title": title,
            "host_tools": HOST_TOOL_PAGES,
            "mutating_enabled": _mutating(request),
        },
    )


@router.get("/appcms", response_class=HTMLResponse)
async def appcms_home(request: Request) -> HTMLResponse:
    return _page(request, "overview")


@router.get("/appcms/{slug}", response_class=HTMLResponse)
async def appcms_page(request: Request, slug: str) -> HTMLResponse:
    return _page(request, slug)


@router.get("/host", response_class=HTMLResponse)
async def host_home(request: Request) -> HTMLResponse:
    """Opens the first page the selected app lists in its manifest."""
    return _host_page(request, "")


@router.get("/host/{slug}", response_class=HTMLResponse)
async def host_page(request: Request, slug: str) -> HTMLResponse:
    return _host_page(request, slug)


async def _invoke(request: Request, **kwargs: Any) -> dict[str, object]:
    sessions = _sessions(request)
    try:
        return await asyncio.to_thread(lambda: sessions.invoke(**kwargs))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (OverflowError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _result(response: dict[str, object]) -> dict[str, object]:
    event = response.get("event")
    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return {"ok": False, "detail": "no result"}
    return {
        "ok": payload.get("ok", True) is not False,
        "result": payload.get("result"),
        "error": payload.get("error"),
    }


@router.post("/api/appcms/read")
async def appcms_read(request: Request) -> dict[str, object]:
    """Blocking read for one AppCMS command. Loopback only."""
    _loopback(request)
    body = await _json_body(request)
    name = str(body.get("name") or "")
    if name not in READ_NAMES:
        raise HTTPException(status_code=400, detail="not an AppCMS read command")
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    response = await _invoke(
        request,
        client_id=str(body.get("client_id") or "").strip(),
        name=name,
        payload=payload,
    )
    return _result(response)


@router.post("/api/appcms/action")
async def appcms_action(request: Request) -> dict[str, object]:
    """Run one host action. Needs the one-use token from /api/debug/confirm for ott.action."""
    _loopback(request)
    body = await _json_body(request)
    action = str(body.get("action") or "").strip()
    token = str(body.get("confirmation") or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    if not token:
        raise HTTPException(status_code=403, detail="confirmation is required")
    arguments = body.get("payload") or {}
    if not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    response = await _invoke(
        request,
        client_id=str(body.get("client_id") or "").strip(),
        name="ott.action",
        payload={"name": action, "payload": arguments},
        confirmation=token,
    )
    return _result(response)
