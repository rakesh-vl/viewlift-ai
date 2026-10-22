"""Map captured HTTP/WebSocket snapshots into FlowRecord and persist/publish."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from viewlift_ai.models.flow import (
    TLS_FAILED_LIKELY_PINNING,
    FlowRecord,
    FlowSource,
    FlowTiming,
    WebSocketMessageRecord,
)
from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.services.event_bus import FlowEventBus
from viewlift_ai.services.protocols import FlowStoreProtocol
from viewlift_ai.services.reveal import RevealSession


@dataclass(slots=True)
class HttpCaptureSnapshot:
    """Plain capture input — no mitmproxy types (keeps services testable)."""

    id: str
    method: str
    host: str
    path: str
    status: int | None
    duration_ms: float | None
    protocol: str
    request_headers: Mapping[str, str] = field(default_factory=dict)
    request_body: bytes = b""
    response_headers: Mapping[str, str] = field(default_factory=dict)
    response_body: bytes = b""
    error: str | None = None
    tls_failed: bool = False
    request_id: str | None = None
    websocket_messages: Sequence[WebSocketMessageRecord] = field(default_factory=tuple)
    created_at: datetime | None = None
    record_bodies: bool = True
    content_type: str | None = None
    query: str | None = None
    timing: FlowTiming = field(default_factory=FlowTiming)
    source: FlowSource = FlowSource.PROXY


class CaptureService:
    """Redact → store → event bus. Independent of FastAPI and mitmproxy."""

    def __init__(
        self,
        store: FlowStoreProtocol,
        event_bus: FlowEventBus,
        redaction: RedactionService | None = None,
        reveal: RevealSession | None = None,
    ) -> None:
        self._store = store
        self._bus = event_bus
        self._redaction = redaction or RedactionService()
        self._reveal = reveal

    def persist_http(self, snapshot: HttpCaptureSnapshot) -> FlowRecord:
        if self._reveal is not None and snapshot.record_bodies:
            self._reveal.remember(
                snapshot.id,
                request_headers=dict(snapshot.request_headers),
                request_body=bytes(snapshot.request_body),
                response_headers=dict(snapshot.response_headers),
                response_body=bytes(snapshot.response_body),
            )
        flow = self.map_snapshot(snapshot)
        self._store.save_flow(flow)
        self._bus.publish_threadsafe(flow)
        return flow

    def persist_tls_failure(
        self,
        *,
        flow_id: str,
        host: str,
        error: str | None = None,
        protocol: str = "HTTPS",
    ) -> FlowRecord:
        # Pinning and CA-trust failures are stored under one actionable message.
        _ = error
        snapshot = HttpCaptureSnapshot(
            id=flow_id,
            method="CONNECT",
            host=host,
            path="/",
            status=None,
            duration_ms=None,
            protocol=protocol,
            error=TLS_FAILED_LIKELY_PINNING,
            tls_failed=True,
            record_bodies=False,
        )
        return self.persist_http(snapshot)

    def map_snapshot(self, snapshot: HttpCaptureSnapshot) -> FlowRecord:
        req_headers = self._redaction.redact_headers(dict(snapshot.request_headers))
        resp_headers = self._redaction.redact_headers(dict(snapshot.response_headers))
        if snapshot.record_bodies:
            req_body = self._redaction.redact_body(snapshot.request_body)
            resp_body = self._redaction.redact_body(snapshot.response_body)
        else:
            req_body = b""
            resp_body = b""

        ws_messages = tuple(
            self._redact_ws_message(msg) for msg in snapshot.websocket_messages
        )
        if snapshot.record_bodies:
            size_bytes: int | None = len(resp_body)
        else:
            size_bytes = None

        content_type = snapshot.content_type or _header_ci(resp_headers, "content-type")
        path, query = _split_path_query(snapshot.path, snapshot.query)

        created = snapshot.created_at or datetime.now(UTC)
        return FlowRecord(
            id=snapshot.id,
            method=snapshot.method,
            host=snapshot.host,
            path=path,
            status=snapshot.status,
            duration_ms=snapshot.duration_ms,
            size_bytes=size_bytes,
            protocol=snapshot.protocol,
            source=snapshot.source,
            created_at=created,
            request_headers=req_headers,
            request_body=req_body,
            response_headers=resp_headers,
            response_body=resp_body,
            error=snapshot.error,
            request_id=snapshot.request_id,
            tls_failed=snapshot.tls_failed,
            websocket_messages=ws_messages,
            content_type=content_type,
            query=query,
            timing=snapshot.timing,
        )

    def _redact_ws_message(self, message: WebSocketMessageRecord) -> WebSocketMessageRecord:
        if not message.is_text:
            return message
        try:
            text = message.content.decode("utf-8")
        except UnicodeDecodeError:
            return message
        redacted = self._redaction.redact_text(text)
        if redacted == text:
            return message
        return replace(message, content=redacted.encode("utf-8"))


def _header_ci(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def _split_path_query(path: str, query: str | None) -> tuple[str, str | None]:
    if query is not None:
        base = path.split("?", 1)[0] or "/"
        return base, query or None
    if "?" not in path:
        return path or "/", None
    base, q = path.split("?", 1)
    return base or "/", q or None
