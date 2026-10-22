"""Breakpoint pause models (no framework imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from viewlift_ai.models.rules import BREAKPOINT_TIMED_OUT, BreakpointStage

__all__ = [
    "BREAKPOINT_TIMED_OUT",
    "BreakpointAction",
    "BreakpointDecision",
    "BreakpointEvent",
    "BreakpointEventKind",
    "PendingBreakpoint",
    "pending_to_dict",
]


BreakpointAction = Literal["resume", "drop", "timeout"]


class BreakpointEventKind(StrEnum):
    PAUSED = "paused"
    RESUMED = "resumed"
    DROPPED = "dropped"
    TIMED_OUT = "timed_out"
    CLEARED = "cleared"


@dataclass(slots=True, frozen=True)
class BreakpointDecision:
    """Outcome after a pause resolves."""

    action: BreakpointAction
    headers: dict[str, str] | None = None
    body: bytes | None = None
    status: int | None = None


@dataclass(slots=True)
class PendingBreakpoint:
    """One asynchronously paused flow visible to API/UI."""

    id: str
    flow_id: str
    rule_id: str
    stage: BreakpointStage
    method: str
    host: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    status: int | None = None
    created_at: float = 0.0
    timeout_at: float = 0.0
    timed_out: bool = False


@dataclass(slots=True, frozen=True)
class BreakpointEvent:
    kind: BreakpointEventKind
    pending: PendingBreakpoint | None = None


def pending_to_dict(item: PendingBreakpoint) -> dict[str, Any]:
    body_text = item.body.decode("utf-8", errors="replace")
    return {
        "id": item.id,
        "flow_id": item.flow_id,
        "rule_id": item.rule_id,
        "stage": str(item.stage),
        "method": item.method,
        "host": item.host,
        "path": item.path,
        "headers": dict(item.headers),
        "body": body_text,
        "body_bytes": len(item.body),
        "status": item.status,
        "created_at": item.created_at,
        "timeout_at": item.timeout_at,
        "timed_out": bool(item.timed_out),
        "error": BREAKPOINT_TIMED_OUT if item.timed_out else None,
    }
