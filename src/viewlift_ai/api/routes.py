"""Dashboard pages and JSON/export API for network flows."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.services.flow_export import build_curl, har_json
from viewlift_ai.services.flow_present import flow_detail, flow_summary
from viewlift_ai.services.reveal import RevealSession

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _store(request: Request):
    return request.app.state.store


def _redaction(request: Request) -> RedactionService:
    return request.app.state.redaction


def _reveal(request: Request) -> RevealSession:
    session = getattr(request.app.state, "reveal", None)
    if session is None:
        raise HTTPException(status_code=503, detail="Reveal session unavailable")
    return session


def _require_loopback(request: Request) -> None:
    # Starlette TestClient reports host "testclient"; production binds 127.0.0.1 only.
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Reveal APIs are loopback-only")


def _mutating_enabled(request: Request) -> bool:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        return False
    return bool(engine.any_mutating_enabled())


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "title": "Network",
            "active_nav": "network",
            "mutating_enabled": _mutating_enabled(request),
        },
    )


@router.get("/flows/{flow_id}", response_class=HTMLResponse)
async def flow_detail_page(request: Request, flow_id: str) -> HTMLResponse:
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return TEMPLATES.TemplateResponse(
        request,
        "flow_detail.html",
        {
            "title": f"{flow.method} {flow.host}{flow.path}",
            "flow_id": flow_id,
            "flow": flow_detail(flow, redaction=_redaction(request)),
            "active_nav": "network",
            "mutating_enabled": _mutating_enabled(request),
        },
    )


@router.get("/api/flows")
def list_flows(
    request: Request,
    method: Annotated[str | None, Query()] = None,
    host: Annotated[str | None, Query()] = None,
    status: Annotated[int | None, Query()] = None,
    content_type: Annotated[str | None, Query(alias="content-type")] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, object]:
    flows = _store(request).list_flows(
        limit=limit,
        method=method or None,
        host=host or None,
        status=status,
        content_type=content_type or None,
        text=text or None,
    )
    return {"flows": [flow_summary(f) for f in flows], "count": len(flows)}


@router.get("/api/flows/{flow_id}")
def get_flow(
    request: Request,
    flow_id: str,
    reveal: Annotated[bool, Query()] = False,
) -> dict[str, object]:
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    raw = None
    if reveal:
        _require_loopback(request)
        session = _reveal(request)
        if not session.unlocked:
            raise HTTPException(status_code=403, detail="Reveal session is locked")
        raw = session.get_raw(flow_id)
        if raw is None:
            raise HTTPException(
                status_code=404,
                detail="No in-memory raw payload for this flow (may predate this session)",
            )
    return flow_detail(flow, redaction=_redaction(request), reveal_raw=raw)


@router.get("/api/reveal/status")
def reveal_status(request: Request) -> dict[str, object]:
    _require_loopback(request)
    return _reveal(request).status()


@router.post("/api/reveal/unlock")
async def reveal_unlock(request: Request) -> dict[str, object]:
    _require_loopback(request)
    body = await _json_body(request)
    token = str(body.get("token") or "")
    ok = _reveal(request).unlock(token)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid unlock token")
    return {"ok": True, "unlocked": True}


@router.post("/api/reveal/lock")
def reveal_lock(request: Request) -> dict[str, object]:
    _require_loopback(request)
    _reveal(request).lock()
    return {"ok": True, "unlocked": False}


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body


@router.get("/api/flows/{flow_id}/har")
def export_flow_har(request: Request, flow_id: str) -> Response:
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    payload = har_json([flow], redaction=_redaction(request))
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="flow-{flow_id}.har"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/export/har")
def export_har(
    request: Request,
    method: Annotated[str | None, Query()] = None,
    host: Annotated[str | None, Query()] = None,
    status: Annotated[int | None, Query()] = None,
    content_type: Annotated[str | None, Query(alias="content-type")] = None,
    text: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Response:
    flows = _store(request).list_flows(
        limit=limit,
        method=method or None,
        host=host or None,
        status=status,
        content_type=content_type or None,
        text=text or None,
    )
    payload = har_json(flows, redaction=_redaction(request))
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="viewlift-flows.har"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/flows/{flow_id}/curl")
def export_flow_curl(request: Request, flow_id: str) -> PlainTextResponse:
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    command = build_curl(flow, redaction=_redaction(request))
    return PlainTextResponse(
        content=command,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/flows/{flow_id}/summary")
def get_flow_summary(request: Request, flow_id: str) -> JSONResponse:
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return JSONResponse(flow_summary(flow))
