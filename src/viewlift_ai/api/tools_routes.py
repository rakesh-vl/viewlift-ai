"""Tools page: developer toolkit plus a Boop-style scratchpad.

Scratchpad scripts are stored here and run in the browser. Toolkit actions that the
browser cannot do on its own (YAML, keys, bcrypt, DNS, WHOIS, images) run here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.services.devtools import DevToolError, DevToolsService
from viewlift_ai.services.scratchpad import ScratchpadError, ScratchpadLibrary

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
MAX_ACTION_BODY_BYTES = 40 * 1024 * 1024

router = APIRouter()


def _devtools(request: Request) -> DevToolsService:
    service = getattr(request.app.state, "devtools", None)
    if service is None:
        service = DevToolsService()
        request.app.state.devtools = service
    return service


def _require_local_json(request: Request) -> None:
    # Starlette TestClient reports host "testclient"; production binds 127.0.0.1 only.
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Tool actions are loopback-only")
    # A JSON content type forces a CORS preflight, so other sites cannot post here.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_ACTION_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request is larger than 40 MB.")


def _library(request: Request) -> ScratchpadLibrary:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=503, detail="Scratchpad unavailable")
    return ScratchpadLibrary(config.data_dir / "scratchpad")


def _source(body: Any) -> str:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    source = body.get("source")
    if not isinstance(source, str):
        raise HTTPException(status_code=400, detail="source is required.")
    return source


def _guard(action):
    try:
        return action()
    except ScratchpadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request) -> HTMLResponse:
    engine = getattr(request.app.state, "rule_engine", None)
    mutating = bool(engine.any_mutating_enabled()) if engine is not None else False
    return TEMPLATES.TemplateResponse(
        request,
        "tools.html",
        {"title": "Tools", "active_nav": "tools", "mutating_enabled": mutating},
    )


@router.get("/api/tools/scripts")
def list_scripts(request: Request) -> dict[str, object]:
    scripts = _library(request).list_scripts()
    return {"scripts": [item.public() for item in scripts]}


@router.get("/api/tools/scripts/{script_id}")
def get_script(request: Request, script_id: str) -> dict[str, object]:
    return _guard(lambda: _library(request).get(script_id).public(include_source=True))


@router.post("/api/tools/scripts")
async def create_script(request: Request) -> dict[str, object]:
    source = _source(await request.json())
    record = _guard(lambda: _library(request).create(source))
    return record.public(include_source=True)


@router.put("/api/tools/scripts/{script_id}")
async def update_script(request: Request, script_id: str) -> dict[str, object]:
    source = _source(await request.json())
    record = _guard(lambda: _library(request).update(script_id, source))
    return record.public(include_source=True)


@router.delete("/api/tools/scripts/{script_id}")
def delete_script(request: Request, script_id: str) -> dict[str, bool]:
    _guard(lambda: _library(request).delete(script_id))
    return {"ok": True}


@router.post("/api/tools/action/{name}")
async def run_action(request: Request, name: str) -> dict[str, Any]:
    _require_local_json(request)
    raw = await request.body()
    if len(raw) > MAX_ACTION_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request is larger than 40 MB.")
    try:
        payload = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.") from exc
    service = _devtools(request)
    try:
        return await asyncio.to_thread(service.run, name, payload)
    except DevToolError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
