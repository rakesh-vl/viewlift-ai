"""Redact sensitive headers and nested JSON keys before persist/display."""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_HEADERS = frozenset({"cookie", "set-cookie"})
SENSITIVE_JSON_KEYS = frozenset({"password", "secret"})


class RedactionService:
    """Framework-free redaction. Case-insensitive header and JSON key matching."""

    def __init__(
        self,
        *,
        sensitive_headers: frozenset[str] = SENSITIVE_HEADERS,
        sensitive_json_keys: frozenset[str] = SENSITIVE_JSON_KEYS,
        replacement: str = REDACTED,
    ) -> None:
        self._headers = {h.lower() for h in sensitive_headers}
        self._json_keys = {k.lower() for k in sensitive_json_keys}
        self._replacement = replacement

    def redact_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in self._headers:
                out[key] = self._replacement
            else:
                out[key] = value
        return out

    def redact_json_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self._redact_object(value)
        if isinstance(value, list):
            return [self.redact_json_value(item) for item in value]
        return value

    def redact_body(self, body: bytes) -> bytes:
        """Redact nested JSON keys when body parses as JSON; otherwise return unchanged."""
        if not body:
            return body
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body
        stripped = text.lstrip()
        if not stripped or stripped[0] not in "{[":
            return body
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return body
        redacted = self.redact_json_value(parsed)
        return json.dumps(redacted, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        stripped = text.lstrip()
        if not stripped or stripped[0] not in "{[":
            return text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        redacted = self.redact_json_value(parsed)
        return json.dumps(redacted, separators=(",", ":"), ensure_ascii=False)

    def _redact_object(self, obj: MutableMapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in self._json_keys:
                out[key] = self._replacement
            else:
                out[key] = self.redact_json_value(value)
        return out
