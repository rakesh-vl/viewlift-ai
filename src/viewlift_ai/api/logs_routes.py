"""Logs page and APIs: start/stop sources, list/detail, requestId → flow link."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.api.scope import debug_sessions, resolve_client_ids
from viewlift_ai.services.log_present import log_detail, log_summary
from viewlift_ai.services.log_tail import AppAttribution

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _mutating_enabled(request: Request) -> bool:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        return False
    return bool(engine.any_mutating_enabled())


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
            "mutating_enabled": _mutating_enabled(request),
        },
    )


@router.get("/api/logs/sources")
def list_log_sources(request: Request) -> dict[str, object]:
    return _tail(request).list_sources()


@router.get("/api/logs/status")
def log_status(request: Request) -> dict[str, object]:
    return _tail(request).status()


@router.post("/api/logs/start")
async def start_logs(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    source = str(body.get("source") or "")
    udid = body.get("udid")
    process = body.get("process")
    attribution = None
    client = _scoped_client(request, body.get("client_id"))
    if client is not None:
        executable = str(client.get("executable") or "")
        if not udid and client.get("is_simulator"):
            udid = client.get("device_key")
        if not source:
            source = "simulator" if client.get("is_simulator") else "device"
        if not process and executable:
            process = executable
        if executable:
            attribution = AppAttribution(client_id=str(client["id"]), executable=executable)
    result = _tail(request).start(
        source,
        udid=str(udid) if udid else None,
        process=str(process) if process else None,
        attribution=attribution,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("detail") or "start failed"))
    return result


@router.post("/api/logs/filter")
async def set_log_filter(request: Request) -> dict[str, object]:
    """Update the live process / bundle-id filter without reopening the device."""
    body = await _json_body(request)
    result = _tail(request).set_filter(
        process=str(body.get("process") or ""),
        subsystem=str(body.get("subsystem") or ""),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("detail") or "invalid filter"))
    return result


@router.post("/api/logs/stop")
def stop_logs(request: Request) -> dict[str, object]:
    return _tail(request).stop()


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
) -> dict[str, object]:
    logs = _store(request).list_logs(
        limit=limit,
        source=source or None,
        level=level or None,
        subsystem=subsystem or None,
        text=text or None,
        client_ids=resolve_client_ids(request, client_id, device_key),
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


def _scoped_client(request: Request, client_id: object) -> dict[str, object] | None:
    sessions = debug_sessions(request)
    if sessions is None or not client_id:
        return None
    try:
        return sessions.get_client(str(client_id))
    except LookupError:
        return None


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body
