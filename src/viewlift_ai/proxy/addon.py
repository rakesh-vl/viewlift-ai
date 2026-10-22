"""Thin mitmproxy hooks. Decisions and persistence live in services."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from mitmproxy import http, tls

from viewlift_ai.models.flow import FlowTiming, WebSocketMessageRecord
from viewlift_ai.proxy.allowlist import HostAllowlist
from viewlift_ai.proxy.breakpoints import BreakpointRegistry
from viewlift_ai.proxy.rules import (
    apply_request_rules,
    apply_response_rules,
    flow_error_with_breakpoint_mark,
)
from viewlift_ai.services.capture import CaptureService, HttpCaptureSnapshot
from viewlift_ai.services.rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class ViewliftCaptureAddon:
    """Allowlist tunnel vs decrypt; rules; capture allowlisted HTTP/WebSocket only."""

    def __init__(
        self,
        allowlist: HostAllowlist,
        capture: CaptureService,
        rule_engine: RuleEngine | None = None,
        breakpoints: BreakpointRegistry | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._capture = capture
        self._rules = rule_engine
        self._breakpoints = breakpoints
        self._tls_failure_ids: dict[str, str] = {}

    def tls_clienthello(self, data: tls.ClientHelloData) -> None:
        host = _clienthello_host(data)
        if not self._allowlist.allows(host):
            data.ignore_connection = True

    def tls_failed_client(self, data: tls.TlsData) -> None:
        self._record_tls_failure(data)

    def tls_failed_server(self, data: tls.TlsData) -> None:
        self._record_tls_failure(data)

    async def request(self, flow: http.HTTPFlow) -> None:
        """Throttle → map local → map remote → request breakpoint (before forward)."""
        if self._rules is None:
            return
        await apply_request_rules(
            self._rules,
            flow,
            breakpoints=self._breakpoints,
        )

    async def response(self, flow: http.HTTPFlow) -> None:
        host = _flow_host(flow)
        if self._rules is not None:
            await apply_response_rules(
                self._rules,
                flow,
                breakpoints=self._breakpoints,
            )
        if not self._allowlist.allows(host):
            return
        self._persist_http(flow, record_bodies=True)

    def error(self, flow: http.HTTPFlow) -> None:
        host = _flow_host(flow)
        if not self._allowlist.allows(host):
            return
        if flow.error and _looks_like_tls_failure(flow.error.msg):
            self._capture.persist_tls_failure(
                flow_id=flow.id,
                host=host or "unknown",
                error=flow.error.msg,
                protocol=_protocol(flow),
            )
            return
        self._persist_http(flow, record_bodies=True)

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        host = _flow_host(flow)
        if not self._allowlist.allows(host):
            return
        if flow.websocket is None or not flow.websocket.messages:
            return
        self._persist_http(flow, record_bodies=True)

    def _record_tls_failure(self, data: tls.TlsData) -> None:
        host = _tls_host(data)
        if not self._allowlist.allows(host):
            return
        key = host or "unknown"
        flow_id = self._tls_failure_ids.get(key)
        if flow_id is None:
            flow_id = str(uuid.uuid4())
            self._tls_failure_ids[key] = flow_id
        self._capture.persist_tls_failure(flow_id=flow_id, host=key)

    def _persist_http(self, flow: http.HTTPFlow, *, record_bodies: bool) -> None:
        try:
            snapshot = _to_snapshot(flow, record_bodies=record_bodies)
            self._capture.persist_http(snapshot)
        except Exception:
            logger.exception("Failed to persist flow %s", getattr(flow, "id", "?"))


def _clienthello_host(data: tls.ClientHelloData) -> str:
    if data.client_hello.sni:
        return data.client_hello.sni
    server = data.context.server
    if server.address:
        return str(server.address[0])
    return ""


def _tls_host(data: tls.TlsData) -> str:
    conn = data.conn
    sni = getattr(conn, "sni", None)
    if sni:
        return str(sni)
    server = data.context.server
    if server.address:
        return str(server.address[0])
    return ""


def _flow_host(flow: http.HTTPFlow) -> str:
    try:
        return flow.request.pretty_host or flow.request.host or ""
    except Exception:
        return ""


def _protocol(flow: http.HTTPFlow) -> str:
    if flow.websocket is not None:
        return "WebSocket"
    http_version = getattr(flow.response, "http_version", None) or getattr(
        flow.request, "http_version", "HTTP/1.1"
    )
    if flow.request.scheme == "https":
        return f"HTTPS/{http_version}" if "/" not in str(http_version) else str(http_version)
    return str(http_version)


def _looks_like_tls_failure(message: str) -> bool:
    lower = message.lower()
    needles = (
        "tls",
        "ssl",
        "certificate",
        "handshake",
        "unknown ca",
        "bad certificate",
    )
    return any(n in lower for n in needles)


def _headers_to_dict(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items(multi=True):
        if key in out:
            out[key] = f"{out[key]}, {value}"
        else:
            out[key] = value
    return out


def _duration_ms(flow: http.HTTPFlow) -> float | None:
    start = flow.request.timestamp_start
    end = None
    if flow.response is not None:
        end = flow.response.timestamp_end or flow.response.timestamp_start
    if start is None or end is None:
        return None
    return max(0.0, (end - start) * 1000.0)


def _timing(flow: http.HTTPFlow) -> FlowTiming:
    """Best-effort phase timings from mitmproxy connection/request stamps."""
    connect_ms: float | None = None
    tls_ms: float | None = None
    ttfb_ms: float | None = None
    download_ms: float | None = None

    server = getattr(flow, "server_conn", None)
    if server is not None:
        tcp = getattr(server, "timestamp_tcp_setup", None)
        start = getattr(server, "timestamp_start", None)
        tls_setup = getattr(server, "timestamp_tls_setup", None)
        if start is not None and tcp is not None:
            connect_ms = max(0.0, (tcp - start) * 1000.0)
        if tls_setup is not None and tcp is not None:
            tls_ms = max(0.0, (tls_setup - tcp) * 1000.0)

    req_end = flow.request.timestamp_end
    if flow.response is not None:
        resp_start = flow.response.timestamp_start
        resp_end = flow.response.timestamp_end
        if req_end is not None and resp_start is not None:
            ttfb_ms = max(0.0, (resp_start - req_end) * 1000.0)
        if resp_start is not None and resp_end is not None:
            download_ms = max(0.0, (resp_end - resp_start) * 1000.0)

    return FlowTiming(
        connect_ms=connect_ms,
        tls_ms=tls_ms,
        ttfb_ms=ttfb_ms,
        download_ms=download_ms,
    )


def _content_type(headers: dict[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return None


def _path_and_query(raw_path: str) -> tuple[str, str | None]:
    path = raw_path or "/"
    if "?" not in path:
        return path, None
    base, query = path.split("?", 1)
    return base or "/", query or None


def _ws_messages(flow: http.HTTPFlow) -> tuple[WebSocketMessageRecord, ...]:
    if flow.websocket is None:
        return ()
    records: list[WebSocketMessageRecord] = []
    for msg in flow.websocket.messages:
        if msg.dropped:
            continue
        records.append(
            WebSocketMessageRecord(
                from_client=bool(msg.from_client),
                content=bytes(msg.content),
                is_text=bool(msg.is_text),
                timestamp=float(msg.timestamp or 0.0),
            )
        )
    return tuple(records)


def _to_snapshot(flow: http.HTTPFlow, *, record_bodies: bool) -> HttpCaptureSnapshot:
    request = flow.request
    response = flow.response
    host = _flow_host(flow)
    path, query = _path_and_query(request.path or "/")
    req_body = bytes(request.raw_content or b"") if record_bodies else b""
    resp_body = b""
    status = None
    resp_headers: dict[str, str] = {}
    if response is not None:
        status = int(response.status_code)
        resp_headers = _headers_to_dict(response.headers)
        if record_bodies:
            resp_body = bytes(response.raw_content or b"")

    error = flow.error.msg if flow.error else None
    error = flow_error_with_breakpoint_mark(flow, error)
    tls_failed = bool(error and _looks_like_tls_failure(error))
    req_headers = _headers_to_dict(request.headers)

    return HttpCaptureSnapshot(
        id=flow.id,
        method=request.method,
        host=host,
        path=path,
        status=status,
        duration_ms=_duration_ms(flow),
        protocol=_protocol(flow),
        request_headers=req_headers,
        request_body=req_body,
        response_headers=resp_headers,
        response_body=resp_body,
        error=error,
        tls_failed=tls_failed,
        request_id=_request_id_from_headers(req_headers),
        websocket_messages=_ws_messages(flow),
        record_bodies=record_bodies,
        content_type=_content_type(resp_headers),
        query=query,
        timing=_timing(flow),
    )


def _request_id_from_headers(headers: dict[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() in {"x-request-id", "request-id", "x-correlation-id"}:
            text = (value or "").strip()
            if text:
                return text
    return None
