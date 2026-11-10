"""Send one GraphQL document from this Mac. Nothing here is stored as a captured flow."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit

from viewlift_ai.services.graphql_store import (
    GraphQLEndpoint,
    GraphQLEndpointStore,
    GraphQLError,
    same_graphql_origin,
    validate_graphql_url,
)
from viewlift_ai.services.http_body import BodyDecodeError, decode_content_encoding

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SCHEMA_TTL_SECONDS = 600
HTML_HINT = (
    "This URL returned a web page. The GraphQL endpoint is often /graphql on the same host."
)
AUTH_KEPT = (
    "Saved basic auth was not added because this request already has an Authorization header."
)
AUTH_HOST = "Saved basic auth was not added because this URL is a different host."
AUTH_AMBIGUOUS = (
    "More than one saved endpoint matches this host. Pick one to use its basic auth."
)
_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_Opener = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind
      name
      description
      fields(includeDeprecated: true) {
        name
        description
        args {
          name
          description
          defaultValue
          type { ...TypeRef }
        }
        type { ...TypeRef }
        isDeprecated
        deprecationReason
      }
      inputFields {
        name
        description
        defaultValue
        type { ...TypeRef }
      }
      interfaces { ...TypeRef }
      enumValues(includeDeprecated: true) {
        name
        description
        isDeprecated
        deprecationReason
      }
      possibleTypes { ...TypeRef }
    }
  }
}

fragment TypeRef on __Type {
  kind
  name
  ofType {
    kind
    name
    ofType {
      kind
      name
      ofType {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
            ofType {
              kind
              name
              ofType {
                kind
                name
              }
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class GraphQLResult:
    status: int
    content_type: str
    body: str
    truncated: bool
    elapsed_ms: int
    html_page: bool


class SchemaCache:
    """In-memory introspection bodies, keyed by endpoint id or URL."""

    def __init__(self, ttl_seconds: float = SCHEMA_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            stored, payload = found
            if now - stored > self._ttl:
                self._items.pop(key, None)
                return None
            return dict(payload)

    def put(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._items[key] = (time.monotonic(), dict(payload))


class SameOriginRedirect(urlrequest.HTTPRedirectHandler):
    """Follow a redirect only when it stays on the original scheme, host, and port."""

    def __init__(self, origin: str) -> None:
        super().__init__()
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if code not in (301, 302, 303, 307, 308):
            raise GraphQLError("GraphQL endpoint redirected. Use the final URL.")
        parts = urlsplit(newurl)
        if parts.username is not None or parts.password is not None:
            raise GraphQLError("Redirect URL must not include a username or password.")
        if not same_graphql_origin(self._origin, newurl):
            raise GraphQLError("Redirect left the GraphQL host.")
        merged = dict(req.header_items())
        merged.update(req.unredirected_hdrs)
        kept = {
            key: value
            for key, value in merged.items()
            if key.lower() not in _HOP_HEADERS
        }
        return urlrequest.Request(newurl, data=req.data, headers=kept, method=req.get_method())


def post_graphql(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    opener: _Opener | None = None,
) -> GraphQLResult:
    """POST one GraphQL body. Reads at most 2 MB and times out at 30 seconds."""
    target = validate_graphql_url(url)
    if len(body) > MAX_REQUEST_BYTES:
        raise GraphQLError("Request body is over 1 MB.")
    safe = {
        key: value
        for key, value in headers.items()
        if key.lower() not in _HOP_HEADERS and key.lower() != "accept-encoding"
    }
    started = time.perf_counter()
    send = opener or _urllib_graphql
    try:
        status, response_headers, raw = send("POST", target, safe, body)
    except GraphQLError:
        raise
    except ValueError as exc:
        raise GraphQLError(str(exc)) from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise GraphQLError(f"GraphQL host did not answer: {reason}", status=502) from exc
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    decoded = _decode_body(raw, response_headers)
    clipped, truncated = _clip(decoded)
    content_type = _content_type(response_headers)
    text = clipped.decode("utf-8-sig", errors="replace")
    return GraphQLResult(
        status=status,
        content_type=content_type,
        body=text,
        truncated=truncated,
        elapsed_ms=elapsed_ms,
        html_page=_looks_like_html(content_type, clipped),
    )


class GraphQLPlayground:
    """Resolve a saved endpoint, attach basic auth, and run a document or introspection."""

    def __init__(
        self,
        store: GraphQLEndpointStore,
        cache: SchemaCache,
        opener: _Opener | None = None,
    ) -> None:
        self._store = store
        self._cache = cache
        self._opener = opener

    def list_endpoints(self) -> dict[str, object]:
        return {"endpoints": [item.public() for item in self._store.load()]}

    def save_endpoints(self, payload: dict[str, Any]) -> dict[str, object]:
        items = payload.get("endpoints")
        if not isinstance(items, list):
            raise GraphQLError("endpoints must be a list.")
        saved = self._store.replace(items)
        return {"endpoints": [item.public() for item in saved]}

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise GraphQLError("Query is required.")
        variables = payload.get("variables", {})
        if variables is None:
            variables = {}
        if not isinstance(variables, dict):
            raise GraphQLError("Variables must be a JSON object.")
        operation = payload.get("operation_name", payload.get("operationName", ""))
        if operation is None:
            operation = ""
        if not isinstance(operation, str):
            raise GraphQLError("Operation name must be a string.")
        return self._run(payload, query=query, variables=variables, operation_name=operation)

    def schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint, url, note = self._resolve(payload)
        key = endpoint.id if endpoint is not None else url
        if payload.get("refresh") is not True:
            cached = self._cache.get(key)
            if cached is not None:
                cached["cached"] = True
                return cached
        result = self._run(
            payload,
            query=INTROSPECTION_QUERY,
            variables={},
            operation_name="IntrospectionQuery",
            resolved=(endpoint, url, note),
        )
        result["cached"] = False
        if _cacheable_schema(result):
            self._cache.put(key, result)
        return result

    def _run(
        self,
        payload: dict[str, Any],
        *,
        query: str,
        variables: dict[str, Any],
        operation_name: str,
        resolved: tuple[GraphQLEndpoint | None, str, str] | None = None,
    ) -> dict[str, Any]:
        endpoint, url, note = resolved if resolved is not None else self._resolve(payload)
        headers = _clean_headers(payload.get("headers"))
        headers, note = _with_basic_auth(headers, endpoint, url, note)
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
        document: dict[str, Any] = {"query": query, "variables": variables}
        if operation_name:
            document["operationName"] = operation_name
        body = json.dumps(document).encode("utf-8")
        result = post_graphql(url=url, headers=headers, body=body, opener=self._opener)
        return _public_result(result, note=note, endpoint_id=endpoint.id if endpoint else "")

    def _resolve(self, payload: dict[str, Any]) -> tuple[GraphQLEndpoint | None, str, str]:
        endpoint_id = str(payload.get("endpoint_id") or "").strip()
        raw_url = payload.get("url")
        endpoint: GraphQLEndpoint | None = None
        if endpoint_id:
            endpoint = self._store.get(endpoint_id)
            if endpoint is None:
                raise GraphQLError("Unknown GraphQL endpoint.")
        if isinstance(raw_url, str) and raw_url.strip():
            url = validate_graphql_url(raw_url)
        elif endpoint is not None:
            url = endpoint.url
        else:
            raise GraphQLError("GraphQL URL is required.")
        note = ""
        if endpoint is None:
            matches = [
                item
                for item in self._store.load()
                if same_graphql_origin(item.url, url) and item.password
            ]
            if len(matches) == 1:
                endpoint = matches[0]
            elif len(matches) > 1:
                note = AUTH_AMBIGUOUS
        return endpoint, url, note


def _with_basic_auth(
    headers: dict[str, str],
    endpoint: GraphQLEndpoint | None,
    url: str,
    note: str,
) -> tuple[dict[str, str], str]:
    if endpoint is None or not endpoint.password:
        return headers, note
    if not same_graphql_origin(endpoint.url, url):
        return headers, _join_note(note, AUTH_HOST)
    if any(key.lower() == "authorization" for key in headers):
        return headers, _join_note(note, AUTH_KEPT)
    token = base64.b64encode(f"{endpoint.username}:{endpoint.password}".encode()).decode("ascii")
    merged = dict(headers)
    merged["Authorization"] = f"Basic {token}"
    return merged, note


def _join_note(current: str, extra: str) -> str:
    if not current:
        return extra
    if extra in current:
        return current
    return f"{current} {extra}"


def _clean_headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GraphQLError("Headers must be an object.")
    if len(value) > 100:
        raise GraphQLError("Too many headers.")
    clean: dict[str, str] = {}
    seen: set[str] = set()
    for key, item in value.items():
        name = str(key).strip()
        if not isinstance(item, str):
            raise GraphQLError("Header values must be strings.")
        if not name or any(char in name or char in item for char in "\r\n"):
            raise GraphQLError("Header names and values must be one line.")
        lowered = name.lower()
        if lowered in seen:
            raise GraphQLError(f"Repeated header: {name}")
        seen.add(lowered)
        if lowered in _HOP_HEADERS or lowered == "accept-encoding":
            continue
        if len(item) > 8192:
            raise GraphQLError("A header value is too long.")
        clean[name] = item
    return clean


def _public_result(result: GraphQLResult, *, note: str, endpoint_id: str) -> dict[str, Any]:
    graphql_errors = False
    try:
        parsed = json.loads(result.body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("errors"):
        graphql_errors = True
    return {
        "ok": True,
        "status": result.status,
        "content_type": result.content_type,
        "body": result.body,
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
        "html_page": result.html_page,
        "html_hint": HTML_HINT if result.html_page else "",
        "auth_note": note,
        "graphql_errors": graphql_errors,
        "endpoint_id": endpoint_id,
    }


def _cacheable_schema(result: dict[str, Any]) -> bool:
    if result.get("status") != 200 or result.get("html_page") or result.get("truncated"):
        return False
    try:
        parsed = json.loads(str(result.get("body") or ""))
    except json.JSONDecodeError:
        return False
    data = parsed.get("data") if isinstance(parsed, dict) else None
    return isinstance(data, dict) and isinstance(data.get("__schema"), dict)


def _decode_body(raw: bytes, headers: dict[str, str]) -> bytes:
    """Turn gzip, deflate, or brotli into the JSON bytes the page can show."""
    try:
        return decode_content_encoding(raw, headers)
    except BodyDecodeError as exc:
        raise GraphQLError(str(exc)) from exc


def _clip(raw: bytes) -> tuple[bytes, bool]:
    if len(raw) > MAX_RESPONSE_BYTES:
        return raw[:MAX_RESPONSE_BYTES], True
    return raw, False


def _content_type(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return ""


def _looks_like_html(content_type: str, body: bytes) -> bool:
    lowered = content_type.lower()
    if "text/html" in lowered or "application/xhtml" in lowered:
        return True
    sample = body.lstrip()[:64].lower()
    return sample.startswith((b"<!doctype html", b"<html"))


def _urllib_graphql(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, str], bytes]:
    opener = urlrequest.build_opener(SameOriginRedirect(url))
    request = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        # Scheme and host were checked before this call. Redirects cannot leave that host.
        with opener.open(request, timeout=30) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            return int(response.status), dict(response.headers.items()), raw
    except urlerror.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        header_items = dict(exc.headers.items()) if exc.headers is not None else {}
        return int(exc.code), header_items, raw
