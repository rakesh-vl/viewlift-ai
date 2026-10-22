"""Present FlowRecord as plain dicts for API/UI (no FastAPI)."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl

from viewlift_ai.models.flow import FlowRecord, FlowTiming, WebSocketMessageRecord
from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.services.reveal import RawFlowPayload

_IMAGE_PREFIXES = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/gif", b"GIF87a"),
    ("image/gif", b"GIF89a"),
    ("image/webp", b"RIFF"),
)

_MAX_IMAGE_PREVIEW = 512_000


def flow_summary(flow: FlowRecord) -> dict[str, Any]:
    return {
        "id": flow.id,
        "method": flow.method,
        "host": flow.host,
        "path": flow.path,
        "query": flow.query,
        "status": flow.status,
        "duration_ms": flow.duration_ms,
        "size_bytes": flow.size_bytes,
        "protocol": flow.protocol,
        "source": flow.source.value,
        "created_at": flow.created_at.isoformat(),
        "content_type": flow.content_type,
        "error": flow.error,
        "tls_failed": flow.tls_failed,
        "request_id": flow.request_id,
        "timing": timing_dict(flow.timing),
    }


def flow_detail(
    flow: FlowRecord,
    *,
    redaction: RedactionService | None = None,
    reveal_raw: RawFlowPayload | None = None,
) -> dict[str, Any]:
    """Detail view. Default redacts; reveal_raw is session-memory only (never export)."""
    redaction = redaction or RedactionService()
    if reveal_raw is not None:
        req_headers = dict(reveal_raw.request_headers)
        resp_headers = dict(reveal_raw.response_headers)
        req_body = bytes(reveal_raw.request_body)
        resp_body = bytes(reveal_raw.response_body)
        revealed = True
    else:
        req_headers = redaction.redact_headers(dict(flow.request_headers))
        resp_headers = redaction.redact_headers(dict(flow.response_headers))
        req_body = redaction.redact_body(flow.request_body)
        resp_body = redaction.redact_body(flow.response_body)
        revealed = False

    content_type = flow.content_type or _header_value(resp_headers, "content-type")
    return {
        **flow_summary(flow),
        "content_type": content_type,
        "revealed": revealed,
        "overview": {
            "url": _url(flow),
            "method": flow.method,
            "status": flow.status,
            "protocol": flow.protocol,
            "source": flow.source.value,
            "duration_ms": flow.duration_ms,
            "size_bytes": flow.size_bytes,
            "error": flow.error,
            "tls_failed": flow.tls_failed,
            "request_id": flow.request_id,
            "revealed": revealed,
        },
        "request": {
            "headers": req_headers,
            "cookies": _parse_cookie_header(_header_value(req_headers, "cookie")),
            "body": body_view(req_body, _header_value(req_headers, "content-type")),
        },
        "response": {
            "headers": resp_headers,
            "cookies": _parse_set_cookie(resp_headers),
            "body": body_view(resp_body, content_type),
        },
        "query": _query_pairs(flow.query),
        "timing": timing_dict(flow.timing),
        "websocket_messages": [_ws_message(m, redaction) for m in flow.websocket_messages],
    }


def timing_dict(timing: FlowTiming) -> dict[str, float | None]:
    return {
        "connect_ms": timing.connect_ms,
        "tls_ms": timing.tls_ms,
        "ttfb_ms": timing.ttfb_ms,
        "download_ms": timing.download_ms,
    }


def body_view(body: bytes, content_type: str | None) -> dict[str, Any]:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    raw_text = _decode_text(body)
    pretty: str | None = None
    kind = "binary"
    image: dict[str, Any] | None = None

    if not body:
        kind = "empty"
    elif ctype.startswith("image/") or _looks_like_image(body):
        kind = "image"
        image = _image_metadata(body, ctype or _sniff_image_type(body))
        if len(body) <= _MAX_IMAGE_PREVIEW:
            image["base64"] = base64.b64encode(body).decode("ascii")
            image["data_url"] = f"data:{image['content_type']};base64,{image['base64']}"
    elif ctype.endswith("json") or ctype in {"application/json", "text/json"} or _looks_json(
        raw_text
    ):
        kind = "json"
        pretty = _pretty_json(raw_text) if raw_text is not None else None
        if pretty is None and raw_text is not None:
            kind = "text"
    elif ctype.startswith("text/") or ctype in {
        "application/javascript",
        "application/xml",
        "application/x-www-form-urlencoded",
    }:
        kind = "text"
    elif raw_text is not None:
        kind = "text"

    return {
        "kind": kind,
        "content_type": content_type,
        "size_bytes": len(body),
        "raw": raw_text if raw_text is not None else None,
        "pretty": pretty,
        "hex_preview": body[:64].hex(" ") if kind == "binary" and body else None,
        "image": image,
    }


def _ws_message(
    message: WebSocketMessageRecord,
    redaction: RedactionService,
) -> dict[str, Any]:
    if message.is_text:
        try:
            text = message.content.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            text = redaction.redact_text(text)
        return {
            "from_client": message.from_client,
            "is_text": True,
            "timestamp": message.timestamp,
            "content": text,
            "size_bytes": len(message.content),
        }
    return {
        "from_client": message.from_client,
        "is_text": False,
        "timestamp": message.timestamp,
        "content": None,
        "hex_preview": message.content[:64].hex(" "),
        "size_bytes": len(message.content),
    }


def _url(flow: FlowRecord) -> str:
    proto = flow.protocol.lower()
    if "https" in proto or proto == "websocket":
        scheme = "https"
    else:
        scheme = "http"
    path = flow.path or "/"
    if flow.query and "?" not in path:
        path = f"{path}?{flow.query}"
    return f"{scheme}://{flow.host}{path}"


def _query_pairs(query: str | None) -> list[dict[str, str]]:
    if not query:
        return []
    return [{"name": k, "value": v} for k, v in parse_qsl(query, keep_blank_values=True)]


def _parse_cookie_header(value: str | None) -> list[dict[str, str]]:
    if not value or value == "[REDACTED]":
        return [{"name": "(redacted)", "value": value or ""}] if value else []
    out: list[dict[str, str]] = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, val = part.split("=", 1)
            out.append({"name": name.strip(), "value": val.strip()})
        else:
            out.append({"name": part, "value": ""})
    return out


def _parse_set_cookie(headers: Mapping[str, str]) -> list[dict[str, str]]:
    raw = _header_value(headers, "set-cookie")
    if not raw:
        return []
    if raw == "[REDACTED]":
        return [{"name": "(redacted)", "value": raw}]
    cookies: list[dict[str, str]] = []
    for segment in raw.split(", "):
        first = segment.split(";", 1)[0]
        if "=" in first:
            name, val = first.split("=", 1)
            cookies.append({"name": name.strip(), "value": val.strip()})
    return cookies


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def _decode_text(body: bytes) -> str | None:
    if not body:
        return ""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _looks_json(text: str | None) -> bool:
    if not text:
        return False
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in "{["


def _pretty_json(text: str) -> str | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _looks_like_image(body: bytes) -> bool:
    return any(body.startswith(prefix) for _ctype, prefix in _IMAGE_PREFIXES)


def _sniff_image_type(body: bytes) -> str:
    for ctype, prefix in _IMAGE_PREFIXES:
        if body.startswith(prefix):
            if ctype == "image/webp" and (len(body) < 12 or body[8:12] != b"WEBP"):
                continue
            return ctype
    return "application/octet-stream"


def _image_metadata(body: bytes, content_type: str) -> dict[str, Any]:
    width, height = _image_dimensions(body)
    return {
        "content_type": content_type or _sniff_image_type(body),
        "size_bytes": len(body),
        "width": width,
        "height": height,
    }


def _image_dimensions(body: bytes) -> tuple[int | None, int | None]:
    if body.startswith(b"\x89PNG\r\n\x1a\n") and len(body) >= 24:
        return int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
    if body.startswith(b"\xff\xd8\xff"):
        return _jpeg_dimensions(body)
    if body.startswith((b"GIF87a", b"GIF89a")) and len(body) >= 10:
        return int.from_bytes(body[6:8], "little"), int.from_bytes(body[8:10], "little")
    return None, None


def _jpeg_dimensions(body: bytes) -> tuple[int | None, int | None]:
    i = 2
    while i + 9 < len(body):
        if body[i] != 0xFF:
            break
        marker = body[i + 1]
        if marker == 0xD8:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            break
        if i + 4 > len(body):
            break
        length = int.from_bytes(body[i + 2 : i + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2} and i + 9 < len(body):
            height = int.from_bytes(body[i + 5 : i + 7], "big")
            width = int.from_bytes(body[i + 7 : i + 9], "big")
            return width, height
        i += 2 + length
    return None, None
