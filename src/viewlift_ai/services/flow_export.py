"""Redacted HAR and copy-as-cURL builders (server-side redaction only)."""

from __future__ import annotations

import base64
import json
import shlex
from collections.abc import Mapping, Sequence
from datetime import UTC
from typing import Any

from viewlift_ai.models.flow import FlowRecord
from viewlift_ai.proxy.redact import REDACTED, RedactionService


def build_har(
    flows: Sequence[FlowRecord],
    *,
    redaction: RedactionService | None = None,
) -> dict[str, Any]:
    """HAR 1.2 document. Always re-redacts before serialization."""
    redaction = redaction or RedactionService()
    entries = [_har_entry(flow, redaction) for flow in flows]
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "Viewlift AI", "version": "0.1.0"},
            "entries": entries,
        }
    }


def har_json(
    flows: Sequence[FlowRecord],
    *,
    redaction: RedactionService | None = None,
) -> str:
    return json.dumps(build_har(flows, redaction=redaction), ensure_ascii=False, indent=2)


def build_curl(
    flow: FlowRecord,
    *,
    redaction: RedactionService | None = None,
) -> str:
    """POSIX-shell-safe curl command with secrets redacted."""
    redaction = redaction or RedactionService()
    headers = redaction.redact_headers(dict(flow.request_headers))
    body = redaction.redact_body(flow.request_body)
    url = _absolute_url(flow)

    parts = ["curl", "-X", shlex.quote(flow.method or "GET"), shlex.quote(url)]
    for key, value in headers.items():
        if key.lower() == "content-length":
            continue
        parts.extend(["-H", shlex.quote(f"{key}: {value}")])
    if body:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = base64.b64encode(body).decode("ascii")
            parts.extend(["--data-binary", shlex.quote(text)])
        else:
            parts.extend(["--data-binary", shlex.quote(text)])
    return " ".join(parts)


def _har_entry(flow: FlowRecord, redaction: RedactionService) -> dict[str, Any]:
    req_headers = redaction.redact_headers(dict(flow.request_headers))
    resp_headers = redaction.redact_headers(dict(flow.response_headers))
    req_body = redaction.redact_body(flow.request_body)
    resp_body = redaction.redact_body(flow.response_body)
    started = flow.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)

    return {
        "startedDateTime": started.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "time": flow.duration_ms if flow.duration_ms is not None else 0,
        "request": {
            "method": flow.method,
            "url": _absolute_url(flow),
            "httpVersion": flow.protocol,
            "cookies": _har_cookies_from_header(_header(req_headers, "cookie")),
            "headers": _har_headers(req_headers),
            "queryString": _har_query(flow.query),
            "headersSize": -1,
            "bodySize": len(req_body),
            "postData": _har_post_data(req_body, _header(req_headers, "content-type")),
        },
        "response": {
            "status": flow.status if flow.status is not None else 0,
            "statusText": "",
            "httpVersion": flow.protocol,
            "cookies": _har_cookies_from_header(_header(resp_headers, "set-cookie")),
            "headers": _har_headers(resp_headers),
            "content": _har_content(
                resp_body,
                flow.content_type or _header(resp_headers, "content-type"),
            ),
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": len(resp_body),
        },
        "cache": {},
        "timings": {
            "blocked": -1,
            "dns": -1,
            "connect": _ms_or_neg(flow.timing.connect_ms),
            "send": 0,
            "wait": _ms_or_neg(flow.timing.ttfb_ms),
            "receive": _ms_or_neg(flow.timing.download_ms),
            "ssl": _ms_or_neg(flow.timing.tls_ms),
        },
    }


def _absolute_url(flow: FlowRecord) -> str:
    path = flow.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if flow.query and "?" not in path:
        path = f"{path}?{flow.query}"
    scheme = "https" if "https" in flow.protocol.lower() else "http"
    if flow.protocol == "WebSocket":
        scheme = "https"
    return f"{scheme}://{flow.host}{path}"


def _har_headers(headers: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in headers.items()]


def _har_query(query: str | None) -> list[dict[str, str]]:
    if not query:
        return []
    from urllib.parse import parse_qsl

    return [{"name": k, "value": v} for k, v in parse_qsl(query, keep_blank_values=True)]


def _har_cookies_from_header(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    if value == REDACTED:
        return [{"name": "cookie", "value": REDACTED}]
    out: list[dict[str, str]] = []
    for part in value.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, val = part.split("=", 1)
        out.append({"name": name.strip(), "value": val.strip()})
    return out


def _har_post_data(body: bytes, content_type: str | None) -> dict[str, Any]:
    if not body:
        return {"mimeType": content_type or "", "text": ""}
    try:
        text = body.decode("utf-8")
        return {"mimeType": content_type or "text/plain", "text": text}
    except UnicodeDecodeError:
        return {
            "mimeType": content_type or "application/octet-stream",
            "text": base64.b64encode(body).decode("ascii"),
            "encoding": "base64",
        }


def _har_content(body: bytes, content_type: str | None) -> dict[str, Any]:
    mime = content_type or "application/octet-stream"
    if not body:
        return {"size": 0, "mimeType": mime, "text": ""}
    try:
        text = body.decode("utf-8")
        return {"size": len(body), "mimeType": mime, "text": text}
    except UnicodeDecodeError:
        return {
            "size": len(body),
            "mimeType": mime,
            "text": base64.b64encode(body).decode("ascii"),
            "encoding": "base64",
        }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def _ms_or_neg(value: float | None) -> float:
    return float(value) if value is not None else -1
