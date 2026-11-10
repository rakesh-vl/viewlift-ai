"""Allowlist API. The editor lives on the Setup page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from viewlift_ai.services.rule_engine import RuleEngine

router = APIRouter()


def _engine(request: Request) -> RuleEngine:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Rule engine unavailable")
    return engine


@router.get("/api/rules")
def get_rules(request: Request) -> dict[str, object]:
    return _engine(request).list_rules()


@router.put("/api/rules/allowlist")
def put_allowlist(request: Request, body: dict[str, Any]) -> dict[str, object]:
    patterns = body.get("patterns")
    if patterns is None and "allowlist" in body:
        patterns = body["allowlist"]
    if not isinstance(patterns, list):
        raise HTTPException(status_code=400, detail="patterns must be a list of strings")
    cleaned = [str(item) for item in patterns]
    cfg = _engine(request).set_allowlist(cleaned)
    return {"allowlist": list(cfg.allowlist)}
