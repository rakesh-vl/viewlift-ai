"""REST API and Rules page for allowlist / map / rewrite / throttle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.models.breakpoints import pending_to_dict
from viewlift_ai.proxy.breakpoints import BreakpointRegistry
from viewlift_ai.services.rule_engine import (
    RuleEngine,
    RuleValidationError,
    breakpoint_to_api,
    map_local_to_api,
    map_remote_to_api,
    rewrite_to_api,
    throttle_to_api,
)

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _engine(request: Request) -> RuleEngine:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Rule engine unavailable")
    return engine


def _breakpoints(request: Request) -> BreakpointRegistry:
    registry = getattr(request.app.state, "breakpoints", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Breakpoint registry unavailable")
    return registry


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request) -> HTMLResponse:
    engine = _engine(request)
    return TEMPLATES.TemplateResponse(
        request,
        "rules.html",
        {
            "title": "Rules",
            "mutating_enabled": engine.any_mutating_enabled(),
            "active_nav": "rules",
        },
    )


@router.get("/api/rules")
def get_rules(request: Request) -> dict[str, object]:
    return _engine(request).list_rules()


@router.get("/api/rules/status")
def rules_status(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {"mutating_enabled": engine.any_mutating_enabled()}


@router.put("/api/rules/allowlist")
def put_allowlist(request: Request, body: dict[str, Any]) -> dict[str, object]:
    patterns = body.get("patterns")
    if patterns is None and "allowlist" in body:
        patterns = body["allowlist"]
    if not isinstance(patterns, list):
        raise HTTPException(status_code=400, detail="patterns must be a list of strings")
    cleaned = [str(p) for p in patterns]
    cfg = _engine(request).set_allowlist(cleaned)
    return {"allowlist": list(cfg.allowlist)}


@router.put("/api/rules/map-local/root")
def put_map_local_root(request: Request, body: dict[str, Any]) -> dict[str, object]:
    root = body.get("map_local_root", body.get("root"))
    if root is None:
        raise HTTPException(status_code=400, detail="map_local_root is required")
    cfg = _engine(request).set_map_local_root(str(root))
    return {"map_local_root": cfg.map_local_root}


@router.post("/api/rules/map-local")
def create_map_local(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return map_local_to_api(_engine(request).add_map_local(body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/rules/map-local/{rule_id}")
def patch_map_local(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return map_local_to_api(_engine(request).update_map_local(rule_id, body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rules/map-local/{rule_id}/toggle")
def toggle_map_local(
    request: Request,
    rule_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return map_local_to_api(_engine(request).toggle_map_local(rule_id, enabled))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rules/map-local/{rule_id}")
def delete_map_local(request: Request, rule_id: str) -> dict[str, str]:
    try:
        _engine(request).delete_map_local(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": rule_id}


@router.post("/api/rules/map-remote")
def create_map_remote(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return map_remote_to_api(_engine(request).add_map_remote(body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/rules/map-remote/{rule_id}")
def patch_map_remote(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return map_remote_to_api(_engine(request).update_map_remote(rule_id, body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rules/map-remote/{rule_id}/toggle")
def toggle_map_remote(
    request: Request,
    rule_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return map_remote_to_api(_engine(request).toggle_map_remote(rule_id, enabled))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rules/map-remote/{rule_id}")
def delete_map_remote(request: Request, rule_id: str) -> dict[str, str]:
    try:
        _engine(request).delete_map_remote(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": rule_id}


@router.post("/api/rules/rewrite")
def create_rewrite(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return rewrite_to_api(_engine(request).add_rewrite(body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/rules/rewrite/{rule_id}")
def patch_rewrite(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return rewrite_to_api(_engine(request).update_rewrite(rule_id, body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rules/rewrite/{rule_id}/toggle")
def toggle_rewrite(
    request: Request,
    rule_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return rewrite_to_api(_engine(request).toggle_rewrite(rule_id, enabled))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rules/rewrite/{rule_id}")
def delete_rewrite(request: Request, rule_id: str) -> dict[str, str]:
    try:
        _engine(request).delete_rewrite(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": rule_id}


@router.post("/api/rules/throttle")
def create_throttle(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return throttle_to_api(_engine(request).add_throttle(body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/rules/throttle/{rule_id}")
def patch_throttle(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return throttle_to_api(_engine(request).update_throttle(rule_id, body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rules/throttle/{rule_id}/toggle")
def toggle_throttle(
    request: Request,
    rule_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return throttle_to_api(_engine(request).toggle_throttle(rule_id, enabled))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rules/throttle/{rule_id}")
def delete_throttle(request: Request, rule_id: str) -> dict[str, str]:
    try:
        _engine(request).delete_throttle(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": rule_id}


@router.post("/api/rules/breakpoints")
def create_breakpoint(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return breakpoint_to_api(_engine(request).add_breakpoint(body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/rules/breakpoints/{rule_id}")
def patch_breakpoint(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return breakpoint_to_api(_engine(request).update_breakpoint(rule_id, body))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rules/breakpoints/{rule_id}/toggle")
def toggle_breakpoint(
    request: Request,
    rule_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return breakpoint_to_api(_engine(request).toggle_breakpoint(rule_id, enabled))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rules/breakpoints/{rule_id}")
def delete_breakpoint_rule(request: Request, rule_id: str) -> dict[str, str]:
    try:
        _engine(request).delete_breakpoint(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": rule_id}


@router.get("/api/breakpoints/pending")
def list_pending_breakpoints(request: Request) -> dict[str, object]:
    items = _breakpoints(request).list_pending()
    return {"pending": [pending_to_dict(p) for p in items]}


@router.post("/api/breakpoints/{pause_id}/resume")
def resume_breakpoint(
    request: Request,
    pause_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        item = _breakpoints(request).resume(pause_id, body or {})
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "resumed", "pending": pending_to_dict(item)}


@router.post("/api/breakpoints/{pause_id}/drop")
def drop_breakpoint(request: Request, pause_id: str) -> dict[str, Any]:
    try:
        item = _breakpoints(request).drop(pause_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "dropped", "pending": pending_to_dict(item)}
