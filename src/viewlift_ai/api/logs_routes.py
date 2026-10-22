"""Logs page and APIs: start/stop sources, list/detail, requestId → flow link."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.services.log_present import log_detail, log_summary

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
    result = _tail(request).start(
        source,
        udid=str(udid) if udid else None,
        process=str(process) if process else None,
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


@router.get("/api/logs")
def list_logs(
    request: Request,
    source: Annotated[str | None, Query()] = None,
    level: Annotated[str | None, Query()] = None,
    subsystem: Annotated[str | None, Query()] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, object]:
    logs = _store(request).list_logs(
        limit=limit,
        source=source or None,
        level=level or None,
        subsystem=subsystem or None,
        text=text or None,
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
        flow_id = _store(request).flow_id_for_request(log.request_id)
    detail["linked_flow_id"] = flow_id
    return detail


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body
