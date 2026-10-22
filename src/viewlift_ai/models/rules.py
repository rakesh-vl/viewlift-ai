"""Rule configuration data objects (no framework imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class RewriteActionKind(StrEnum):
    SET_HEADER = "set_header"
    REMOVE_HEADER = "remove_header"
    SET_STATUS = "set_status"
    REPLACE_BODY_LITERAL = "replace_body_literal"
    REPLACE_BODY_REGEX = "replace_body_regex"


@dataclass(slots=True)
class RuleMatch:
    """Method/host/path glob match. Empty method or '*' matches any method."""

    method: str = "*"
    host: str = "*"
    path: str = "*"


@dataclass(slots=True)
class MapLocalRule:
    id: str
    enabled: bool = False
    order: int = 0
    match: RuleMatch = field(default_factory=RuleMatch)
    relative_path: str = ""


@dataclass(slots=True)
class MapRemoteRule:
    id: str
    enabled: bool = False
    order: int = 0
    match: RuleMatch = field(default_factory=RuleMatch)
    target_url: str = ""
    preserve_path: bool = True
    preserve_query: bool = True


@dataclass(slots=True)
class RewriteAction:
    kind: RewriteActionKind
    header_name: str | None = None
    header_value: str | None = None
    status: int | None = None
    find: str | None = None
    replace: str | None = None


@dataclass(slots=True)
class RewriteRule:
    id: str
    enabled: bool = False
    order: int = 0
    match: RuleMatch = field(default_factory=RuleMatch)
    actions: list[RewriteAction] = field(default_factory=list)


@dataclass(slots=True)
class ThrottleRule:
    id: str
    enabled: bool = False
    order: int = 0
    """Empty host or '*' = global. Otherwise host glob."""
    host: str = "*"
    latency_ms: int = 0
    upload_kbps: int | None = None
    download_kbps: int | None = None
    offline: bool = False


class BreakpointStage(StrEnum):
    REQUEST = "request"
    RESPONSE = "response"


@dataclass(slots=True)
class BreakpointRule:
    """Pause matching request and/or response independently. Off by default."""

    id: str
    enabled: bool = False
    order: int = 0
    match: RuleMatch = field(default_factory=RuleMatch)
    request_enabled: bool = False
    response_enabled: bool = False


@dataclass(slots=True)
class RulesConfig:
    """All mutating rule lists default empty; allowlist empty (deny)."""

    allowlist: list[str] = field(default_factory=list)
    map_local_root: str = ""
    map_local: list[MapLocalRule] = field(default_factory=list)
    map_remote: list[MapRemoteRule] = field(default_factory=list)
    rewrite: list[RewriteRule] = field(default_factory=list)
    throttle: list[ThrottleRule] = field(default_factory=list)
    breakpoints: list[BreakpointRule] = field(default_factory=list)

    def any_mutating_enabled(self) -> bool:
        return any(
            (
                *(r.enabled for r in self.map_local),
                *(r.enabled for r in self.map_remote),
                *(r.enabled for r in self.rewrite),
                *(r.enabled for r in self.throttle),
                *(r.enabled for r in self.breakpoints),
            )
        )


@dataclass(slots=True, frozen=True)
class RequestMatchContext:
    method: str
    host: str
    path: str


@dataclass(slots=True, frozen=True)
class ThrottleEffect:
    offline: bool = False
    latency_ms: int = 0
    upload_kbps: int | None = None
    download_kbps: int | None = None


@dataclass(slots=True, frozen=True)
class MapLocalResult:
    status: int
    body: bytes
    content_type: str | None
    relative_path: str
    missing: bool = False
    rejected: bool = False
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class MapRemoteResult:
    url: str


@dataclass(slots=True, frozen=True)
class RewriteResult:
    status: int
    headers: dict[str, str]
    body: bytes
    error: str | None = None


RuleKind = Literal["map_local", "map_remote", "rewrite", "throttle", "breakpoints"]

BREAKPOINT_TIMED_OUT = "breakpoint timed out"


def match_to_dict(match: RuleMatch) -> dict[str, Any]:
    return {"method": match.method, "host": match.host, "path": match.path}


def match_from_dict(data: dict[str, Any] | None) -> RuleMatch:
    raw = data or {}
    return RuleMatch(
        method=str(raw.get("method") or "*"),
        host=str(raw.get("host") or "*"),
        path=str(raw.get("path") or "*"),
    )
