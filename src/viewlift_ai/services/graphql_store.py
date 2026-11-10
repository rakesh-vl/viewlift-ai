"""Saved GraphQL endpoints. Usernames and passwords are encrypted at rest."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nacl.exceptions import CryptoError

from viewlift_ai.crypto import SecretBoxCipher

GRAPHQL_ENDPOINTS_FILENAME = "graphql-endpoints.json"
MAX_ENDPOINTS = 20
MAX_URL_LENGTH = 4096
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class GraphQLError(Exception):
    """A GraphQL playground request that should not be sent."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GraphQLEndpoint:
    """One saved GraphQL HTTP endpoint. The password is memory-only after load."""

    id: str
    name: str
    url: str
    playground_url: str
    username: str
    password: str

    def public(self) -> dict[str, object]:
        base = graphql_base(self.url)
        return {
            "id": self.id,
            "name": self.name,
            "base": base,
            "url": f"{base}/graphql",
            "playground_url": f"{base}/playground",
            "username": self.username,
            "has_password": bool(self.password),
        }


def validate_graphql_url(url: str, *, allow_empty: bool = False) -> str:
    """Accept an http(s) URL with no embedded userinfo."""
    text = (url or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise GraphQLError("GraphQL URL is required.")
    if len(text) > MAX_URL_LENGTH or any(char in text for char in "\r\n\t "):
        raise GraphQLError("URL is too long.")
    if not text.startswith(("http://", "https://")):
        raise GraphQLError("URL must be http or https.")
    parts = urlsplit(text)
    if parts.username is not None or parts.password is not None:
        raise GraphQLError("URL must not include a username or password.")
    if not parts.hostname:
        raise GraphQLError("URL must include a host.")
    return text


def graphql_origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.port is not None:
        port = parts.port
    elif scheme == "https":
        port = 443
    elif scheme == "http":
        port = 80
    else:
        port = 0
    return scheme, host, port


def same_graphql_origin(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return graphql_origin(left) == graphql_origin(right)


def graphql_base(value: str) -> str:
    """Scheme and host. A pasted /graphql or /playground URL keeps only the origin."""
    text = (value or "").strip()
    if not text:
        raise GraphQLError("GraphQL URL is required.")
    if "://" not in text:
        text = "https://" + text
    text = validate_graphql_url(text)
    scheme, host, port = graphql_origin(text)
    shown = f"[{host}]" if ":" in host else host
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{shown}"
    return f"{scheme}://{shown}:{port}"


def flow_graphql_base(flow: object) -> str:
    """Origin of one captured call. HTTP stays http. Everything else is https."""
    host = str(getattr(flow, "host", "") or "").strip()
    if not host or any(char in host for char in " \t\r\n/"):
        return ""
    protocol = str(getattr(flow, "protocol", "") or "").strip().upper()
    scheme = "http" if protocol == "HTTP" or protocol.startswith("HTTP/") else "https"
    try:
        return graphql_base(f"{scheme}://{host}")
    except GraphQLError:
        return ""


def _submitted_base(raw: dict[str, Any]) -> str:
    for key in ("base", "url", "playground_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return graphql_base(value)
    raise GraphQLError("GraphQL URL is required.")


def graphql_bases_from_flows(flows: list[object]) -> list[dict[str, object]]:
    """Newest distinct origin first. `flows` must already be newest-first."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for flow in flows:
        base = flow_graphql_base(flow)
        if not base:
            continue
        if base not in counts:
            order.append(base)
            counts[base] = 0
        counts[base] += 1
    return [{"base": base, "count": counts[base]} for base in order]


class GraphQLEndpointStore:
    """Atomic `graphql-endpoints.json` (mode 0600) under the data directory."""

    def __init__(self, path: Path, cipher: SecretBoxCipher) -> None:
        self._path = path
        self._cipher = cipher
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[GraphQLEndpoint]:
        with self._lock:
            if not self._path.is_file():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return []
            if not isinstance(raw, dict):
                return []
            items = raw.get("endpoints")
            if not isinstance(items, list):
                return []
            loaded: list[GraphQLEndpoint] = []
            for item in items:
                endpoint = self._read_item(item)
                if endpoint is not None:
                    loaded.append(endpoint)
            return loaded

    def get(self, endpoint_id: str) -> GraphQLEndpoint | None:
        for item in self.load():
            if item.id == endpoint_id:
                return item
        return None

    def replace(self, items: list[Any]) -> list[GraphQLEndpoint]:
        if len(items) > MAX_ENDPOINTS:
            raise GraphQLError(f"At most {MAX_ENDPOINTS} endpoints.")
        current = {item.id: item for item in self.load()}
        saved: list[GraphQLEndpoint] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise GraphQLError("Each endpoint must be an object.")
            endpoint = self._merge(raw, current)
            if endpoint.id in seen:
                raise GraphQLError("Duplicate endpoint id.")
            seen.add(endpoint.id)
            saved.append(endpoint)
        with self._lock:
            self._write(saved)
        return saved

    def _merge(self, raw: dict[str, Any], current: dict[str, GraphQLEndpoint]) -> GraphQLEndpoint:
        raw_id = raw.get("id")
        if isinstance(raw_id, str) and _ID_RE.fullmatch(raw_id):
            endpoint_id = raw_id
        else:
            endpoint_id = uuid.uuid4().hex
        previous = current.get(endpoint_id)
        base = _submitted_base(raw)
        url = f"{base}/graphql"
        playground = f"{base}/playground"
        name = raw.get("name") if isinstance(raw.get("name"), str) else ""
        name = " ".join(name.split())
        if not name:
            name = urlsplit(url).hostname or "GraphQL"
        if len(name) > 80:
            raise GraphQLError("Name is too long.")
        username = raw.get("username") if isinstance(raw.get("username"), str) else ""
        username = username.strip()
        if len(username) > 200:
            raise GraphQLError("Username is too long.")
        password = previous.password if previous is not None else ""
        supplied = raw.get("password")
        if "password" in raw and supplied is not None and not isinstance(supplied, str):
            raise GraphQLError("Password must be a string.")
        if isinstance(supplied, str) and supplied != "":
            if len(supplied) > 500:
                raise GraphQLError("Password is too long.")
            password = supplied
        elif raw.get("clear_password") is True:
            password = ""
        elif "clear_password" in raw and raw.get("clear_password") is not False:
            raise GraphQLError("clear_password must be true or false.")
        return GraphQLEndpoint(
            id=endpoint_id,
            name=name,
            url=url,
            playground_url=playground,
            username=username,
            password=password,
        )

    def _read_item(self, item: object) -> GraphQLEndpoint | None:
        if not isinstance(item, dict):
            return None
        endpoint_id = item.get("id")
        url = item.get("url")
        secret = item.get("secret")
        if not isinstance(endpoint_id, str) or not isinstance(url, str):
            return None
        if not isinstance(secret, str):
            return None
        try:
            base = graphql_base(url)
        except GraphQLError:
            return None
        username, password = self._open_secret(secret)
        if username is None:
            return None
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        return GraphQLEndpoint(
            id=endpoint_id,
            name=name or (urlsplit(base).hostname or "GraphQL"),
            url=f"{base}/graphql",
            playground_url=f"{base}/playground",
            username=username,
            password=password,
        )

    def _open_secret(self, secret: str) -> tuple[str, str] | tuple[None, None]:
        try:
            raw = self._cipher.decrypt(base64.b64decode(secret))
            data = json.loads(raw.decode("utf-8"))
        except (CryptoError, ValueError, json.JSONDecodeError, UnicodeError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        username = data.get("username") if isinstance(data.get("username"), str) else ""
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        return username, password

    def _seal(self, username: str, password: str) -> str:
        payload = json.dumps(
            {"password": password, "username": username},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.b64encode(self._cipher.encrypt(payload)).decode("ascii")

    def _write(self, items: list[GraphQLEndpoint]) -> None:
        payload = {
            "endpoints": [
                {
                    "id": item.id,
                    "name": item.name,
                    "playground_url": item.playground_url,
                    "secret": self._seal(item.username, item.password),
                    "url": item.url,
                }
                for item in items
            ]
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".graphql-",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
            os.chmod(self._path, 0o600)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
