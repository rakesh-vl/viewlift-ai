"""HTTP/SDK flow models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

TLS_FAILED_LIKELY_PINNING = "TLS failed (likely certificate pinning)"


class FlowSource(StrEnum):
    PROXY = "Proxy"
    SDK = "SDK"


class RetentionPolicy(StrEnum):
    """Default: wipe with the process session. Opt-in keeps rows for 24h."""

    SESSION = "session"
    HOURS_24 = "24h"


@dataclass(slots=True, frozen=True)
class FlowTiming:
    """Optional phase timings in milliseconds (null when not measured)."""

    connect_ms: float | None = None
    tls_ms: float | None = None
    ttfb_ms: float | None = None
    download_ms: float | None = None


@dataclass(slots=True, frozen=True)
class WebSocketMessageRecord:
    """One decrypted WebSocket frame (allowlisted hosts only)."""

    from_client: bool
    content: bytes
    is_text: bool
    timestamp: float


@dataclass(slots=True)
class FlowRecord:
    """Captured network flow. Bodies/headers are plaintext in memory only."""

    id: str
    method: str
    host: str
    path: str
    status: int | None
    duration_ms: float | None
    size_bytes: int | None
    protocol: str
    source: FlowSource
    created_at: datetime
    request_headers: Mapping[str, str] = field(default_factory=dict)
    request_body: bytes = b""
    response_headers: Mapping[str, str] = field(default_factory=dict)
    response_body: bytes = b""
    error: str | None = None
    request_id: str | None = None
    tls_failed: bool = False
    websocket_messages: Sequence[WebSocketMessageRecord] = field(default_factory=tuple)
    content_type: str | None = None
    query: str | None = None
    timing: FlowTiming = field(default_factory=FlowTiming)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)
