"""Logs page and APIs: app console (stdout/stderr) list/detail, requestId → flow link.

Logs arrive from the ViewliftNetworkCapture SDK over `/v1/logs`. The page no longer starts
simctl or device syslog streams; those OS-log collectors are unreachable from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.api.scope import scope_kwargs
from viewlift_ai.services.log_present import log_detail, log_summary

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _tail(request: Request):
    svc = getattr(request.app.state, "log_tail", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Log tail service unavailable")
    return svc


def _store(request: Request):
    return request.app.state.store


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "logs.html",
        {
            "title": "Logs",
            "active_nav": "logs",
        },
    )


@router.get("/api/logs/sources")
def list_log_sources(request: Request) -> dict[str, object]:
    """Booted simulators and paired devices. Used by debug tools, not by the Logs page."""
    return _tail(request).list_sources()


@router.get("/api/logs/status")
def log_status(request: Request) -> dict[str, object]:
    return _tail(request).status()


@router.post("/api/logs/delete")
def delete_logs(request: Request) -> dict[str, int]:
    """Delete every stored log. Flows and rules are kept."""
    return {"deleted": _store(request).delete_logs()}


@router.get("/api/logs")
def list_logs(
    request: Request,
    source: Annotated[str | None, Query()] = None,
    level: Annotated[str | None, Query()] = None,
    subsystem: Annotated[str | None, Query()] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    client_id: Annotated[list[str] | None, Query()] = None,
    device_key: Annotated[str | None, Query()] = None,
    bundle_id: Annotated[str | None, Query()] = None,
    auth_state: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    profile_id: Annotated[str | None, Query()] = None,
    anonymous_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    logs = _store(request).list_logs(
        limit=limit,
        source=source or None,
        level=level or None,
        subsystem=subsystem or None,
        text=text or None,
        **scope_kwargs(
            request,
            client_id,
            device_key,
            bundle_id,
            auth_state,
            user_id,
            profile_id,
            anonymous_id,
        ),
    )
    return {"logs": [log_summary(item) for item in logs]}


@router.get("/api/logs/{log_id}")
def get_log(request: Request, log_id: str) -> dict[str, object]:
    log = _store(request).get_log(log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")
    detail = log_detail(log)
    flow_id = None
    if log.request_id:
        flow_id = _store(request).flow_id_for_request(
            log.request_id, client_id=log.client_id
        )
    detail["linked_flow_id"] = flow_id
    return detail
