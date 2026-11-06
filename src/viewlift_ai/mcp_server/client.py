"""Loopback HTTP client for the running Viewlift AI console."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

DEFAULT_URL = "http://127.0.0.1:8741"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ConsoleError(Exception):
    def __init__(self, status: int, detail: object) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"console returned {status}")


def console_base_url(value: str | None = None) -> str:
    raw = (value if value is not None else os.environ.get("VIEWLIFT_AI_URL") or DEFAULT_URL).strip()
    parsed = urlparse.urlsplit(raw)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or host not in _LOOPBACK:
        raise ValueError("VIEWLIFT_AI_URL must be a loopback http URL")
    return raw.rstrip("/")


class ConsoleClient:
    """Talks only to the console already bound on loopback."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 40.0) -> None:
        self.base_url = console_base_url(base_url)
        self.timeout = timeout

    def get(self, path: str, query: Mapping[str, object] | None = None) -> dict[str, Any]:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: Mapping[str, object] | None = None) -> dict[str, Any]:
        return self.request("POST", path, body=body)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        if query:
            pairs = [(key, str(value)) for key, value in query.items() if value is not None]
            if pairs:
                url = f"{url}?{urlparse.urlencode(pairs)}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(dict(body)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urlrequest.Request(url, data=data, headers=headers, method=method)
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except urlerror.HTTPError as exc:
            detail: object
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                detail = exc.reason
            raise ConsoleError(exc.code, detail) from exc
        except urlerror.URLError as exc:
            raise ConsoleError(503, f"console is not reachable at {self.base_url}") from exc
        if not raw:
            return {}
        parsed_body = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed_body, dict):
            raise ConsoleError(502, "console response was not an object")
        return parsed_body
