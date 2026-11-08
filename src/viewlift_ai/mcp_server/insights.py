"""Pure analysis over already scrubbed console rows. No I/O and no MCP SDK import."""

from __future__ import annotations

import json
import math
import re
import shlex
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from viewlift_ai.services.host.crash_diagnosis import stack_line as _stack_line

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_SPACES = re.compile(r"\s+")
_SKIP_HEADERS = frozenset({"content-length", "host", "connection", "accept-encoding"})
_ERROR_LEVELS = frozenset({"error", "fault"})


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct / 100 * len(ordered))))
    return round(ordered[rank - 1], 1)


def is_failure(flow: Mapping[str, Any]) -> bool:
    status = flow.get("status")
    return bool(
        flow.get("error") or flow.get("tls_failed") or (isinstance(status, int) and status >= 400)
    )


def _flow_brief(flow: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": flow.get("id"),
        "method": flow.get("method"),
        "host": flow.get("host"),
        "path": flow.get("path"),
        "status": flow.get("status"),
        "duration_ms": flow.get("duration_ms"),
        "size_bytes": flow.get("size_bytes"),
        "operation": flow.get("operation"),
        "error": flow.get("error"),
        "created_at": flow.get("created_at"),
    }


def network_summary(flows: Iterable[Mapping[str, Any]], *, top: int = 5) -> dict[str, Any]:
    """Per-host health, latency percentiles, slow and large calls, and repeated requests."""
    rows = [flow for flow in flows if isinstance(flow, Mapping)]
    by_host: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for flow in rows:
        by_host[str(flow.get("host") or "?")].append(flow)

    hosts = []
    for host, items in by_host.items():
        durations = [d for d in (_number(f.get("duration_ms")) for f in items) if d is not None]
        failures = sum(1 for f in items if is_failure(f))
        hosts.append(
            {
                "host": host,
                "requests": len(items),
                "failures": failures,
                "failure_rate": round(failures / len(items), 3),
                "p50_ms": percentile(durations, 50),
                "p95_ms": percentile(durations, 95),
                "bytes": int(sum(_number(f.get("size_bytes")) or 0 for f in items)),
            }
        )
    hosts.sort(key=lambda row: (-row["failures"], -row["requests"]))

    statuses = Counter(
        str(f.get("status")) if f.get("status") is not None else "none" for f in rows
    )
    timed = [f for f in rows if _number(f.get("duration_ms")) is not None]
    slowest = sorted(timed, key=lambda f: -(_number(f.get("duration_ms")) or 0))[:top]
    sized = [f for f in rows if _number(f.get("size_bytes")) is not None]
    largest = sorted(sized, key=lambda f: -(_number(f.get("size_bytes")) or 0))[:top]

    repeats = Counter(
        (f.get("method"), f.get("host"), f.get("path"), f.get("operation")) for f in rows
    )
    repeated = [
        {"method": m, "host": h, "path": p, "operation": o, "count": count}
        for (m, h, p, o), count in repeats.most_common()
        if count >= 3
    ][: top * 2]

    operations = Counter(str(f.get("operation")) for f in rows if f.get("operation"))
    all_durations = [d for d in (_number(f.get("duration_ms")) for f in rows) if d is not None]
    return {
        "requests": len(rows),
        "failures": sum(1 for f in rows if is_failure(f)),
        "p50_ms": percentile(all_durations, 50),
        "p95_ms": percentile(all_durations, 95),
        "status_counts": dict(statuses.most_common()),
        "hosts": hosts,
        "slowest": [_flow_brief(f) for f in slowest],
        "largest": [_flow_brief(f) for f in largest],
        "repeated": repeated,
        "graphql_operations": dict(operations.most_common(top * 2)),
        "hints": _network_hints(rows, repeated, all_durations),
    }


def _network_hints(
    rows: list[Mapping[str, Any]], repeated: list[dict[str, Any]], durations: list[float]
) -> list[str]:
    hints = []
    tls = sum(1 for f in rows if f.get("tls_failed"))
    if tls:
        hints.append(f"{tls} TLS failures. The app may pin certificates or the CA is not trusted.")
    server = sum(1 for f in rows if isinstance(f.get("status"), int) and f["status"] >= 500)
    if server:
        hints.append(f"{server} server errors (5xx).")
    auth = sum(1 for f in rows if f.get("status") in (401, 403))
    if auth:
        hints.append(f"{auth} auth failures (401/403). Check token refresh and entitlement.")
    if repeated:
        top = repeated[0]
        hints.append(
            f"{top['method']} {top['host']}{top['path'] or ''} ran {top['count']} times. "
            "Possible over-fetching or a retry loop."
        )
    slow = sum(1 for d in durations if d >= 3000)
    if slow:
        hints.append(f"{slow} requests took 3 seconds or more.")
    return hints


def failed_requests(flows: Iterable[Mapping[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    """Failed flows grouped by host, path, and status, newest first inside each group."""
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for flow in flows:
        if isinstance(flow, Mapping) and is_failure(flow):
            key = (
                flow.get("method"),
                flow.get("host"),
                flow.get("path"),
                flow.get("status"),
                flow.get("error") or ("tls_failed" if flow.get("tls_failed") else None),
            )
            groups[key].append(flow)
    rows = [
        {
            "method": method,
            "host": host,
            "path": path,
            "status": status,
            "error": error,
            "count": len(items),
            "latest": _flow_brief(items[0]),
            "flow_ids": [item.get("id") for item in items[:10]],
        }
        for (method, host, path, status, error), items in groups.items()
    ]
    rows.sort(key=lambda row: -row["count"])
    return {"groups": rows[:limit], "failed": sum(row["count"] for row in rows)}


def log_signature(message: object) -> str:
    """Collapse ids and numbers so repeated log lines group together."""
    text = str(message or "")
    text = _UUID.sub("<uuid>", text)
    text = _HEX.sub("<hex>", text)
    text = _NUMBER.sub("#", text)
    return _SPACES.sub(" ", text).strip()[:200]


def log_summary(logs: Iterable[Mapping[str, Any]], *, top: int = 15) -> dict[str, Any]:
    """Counts by level, source, and subsystem, plus the most frequent warning and error lines."""
    rows = [row for row in logs if isinstance(row, Mapping)]
    levels = Counter(str(row.get("level") or "?") for row in rows)
    sources = Counter(str(row.get("source") or "?") for row in rows)
    subsystems = Counter(str(row.get("subsystem") or "?") for row in rows)
    issues: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        level = str(row.get("level") or "")
        if level not in _ERROR_LEVELS and level != "warning":
            continue
        signature = log_signature(row.get("message"))
        key = (level, signature)
        entry = issues.get(key)
        stamp = row.get("created_at")
        if entry is None:
            issues[key] = {
                "level": level,
                "signature": signature,
                "subsystem": row.get("subsystem"),
                "count": 1,
                "first_seen": stamp,
                "last_seen": stamp,
                "example_id": row.get("id"),
                "request_id": row.get("request_id"),
            }
            continue
        entry["count"] += 1
        if isinstance(stamp, str):
            if not entry["first_seen"] or stamp < entry["first_seen"]:
                entry["first_seen"] = stamp
            if not entry["last_seen"] or stamp > entry["last_seen"]:
                entry["last_seen"] = stamp
    ranked = sorted(
        issues.values(),
        key=lambda item: (item["level"] not in _ERROR_LEVELS, -item["count"]),
    )
    return {
        "total": len(rows),
        "levels": dict(levels.most_common()),
        "sources": dict(sources.most_common()),
        "subsystems": dict(subsystems.most_common(top)),
        "top_issues": ranked[:top],
    }


def _headers(detail: Mapping[str, Any], side: str) -> dict[str, str]:
    block = detail.get(side)
    headers = block.get("headers") if isinstance(block, Mapping) else None
    if not isinstance(headers, Mapping):
        return {}
    return {str(k): str(v) for k, v in headers.items()}


def _body_text(detail: Mapping[str, Any], side: str) -> str | None:
    block = detail.get(side)
    body = block.get("body") if isinstance(block, Mapping) else None
    if not isinstance(body, Mapping):
        return None
    raw = body.get("raw")
    return raw if isinstance(raw, str) else None


def _json_paths(value: Any, prefix: str = "", out: dict[str, str] | None = None) -> dict[str, str]:
    out = {} if out is None else out
    if len(out) > 2000:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            _json_paths(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, list):
        out[f"{prefix}[]"] = f"array({len(value)})"
        if value:
            _json_paths(value[0], f"{prefix}[0]", out)
    else:
        out[prefix or "$"] = json.dumps(value, ensure_ascii=False)[:80]
    return out


def _parse(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None


def _diff_maps(left: Mapping[str, str], right: Mapping[str, str], limit: int) -> dict[str, Any]:
    lkeys = {k.lower(): k for k in left}
    rkeys = {k.lower(): k for k in right}
    changed = [
        {"name": lkeys[k], "a": left[lkeys[k]], "b": right[rkeys[k]]}
        for k in sorted(lkeys.keys() & rkeys.keys())
        if left[lkeys[k]] != right[rkeys[k]]
    ]
    return {
        "only_a": sorted(lkeys[k] for k in lkeys.keys() - rkeys.keys())[:limit],
        "only_b": sorted(rkeys[k] for k in rkeys.keys() - lkeys.keys())[:limit],
        "changed": changed[:limit],
    }


def compare_flows(a: Mapping[str, Any], b: Mapping[str, Any], *, limit: int = 40) -> dict[str, Any]:
    """Side-by-side difference of two redacted flow details."""
    fields = ("method", "host", "path", "query", "status", "duration_ms", "size_bytes", "error")
    overview = {
        name: {"a": a.get(name), "b": b.get(name)} for name in fields if a.get(name) != b.get(name)
    }
    timing_a = a.get("timing") if isinstance(a.get("timing"), Mapping) else {}
    timing_b = b.get("timing") if isinstance(b.get("timing"), Mapping) else {}
    timing = {}
    for name in sorted(set(timing_a) | set(timing_b)):
        left, right = _number(timing_a.get(name)), _number(timing_b.get(name))
        if left is not None and right is not None and left != right:
            timing[name] = {"a": left, "b": right, "delta": round(right - left, 1)}
    result: dict[str, Any] = {
        "a": a.get("id"),
        "b": b.get("id"),
        "overview": overview,
        "timing": timing,
        "request_headers": _diff_maps(_headers(a, "request"), _headers(b, "request"), limit),
        "response_headers": _diff_maps(_headers(a, "response"), _headers(b, "response"), limit),
    }
    for side in ("request", "response"):
        left = _parse(_body_text(a, side))
        right = _parse(_body_text(b, side))
        if left is None and right is None:
            continue
        result[f"{side}_body"] = _diff_maps(
            _json_paths(left) if left is not None else {},
            _json_paths(right) if right is not None else {},
            limit,
        )
    return result


def _url(detail: Mapping[str, Any]) -> str:
    overview = detail.get("overview")
    if isinstance(overview, Mapping) and isinstance(overview.get("url"), str):
        return str(overview["url"])
    path = str(detail.get("path") or "/")
    query = detail.get("query")
    if isinstance(query, str) and query and "?" not in path:
        path = f"{path}?{query}"
    return f"https://{detail.get('host') or ''}{path}"


def _request_parts(detail: Mapping[str, Any]) -> tuple[str, str, dict[str, str], str | None]:
    method = str(detail.get("method") or "GET").upper()
    headers = {
        k: v for k, v in _headers(detail, "request").items() if k.lower() not in _SKIP_HEADERS
    }
    return method, _url(detail), headers, _body_text(detail, "request")


def _swift_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def request_code(detail: Mapping[str, Any], language: str) -> str:
    """Rebuild a request as curl, Swift URLSession, or Python. Masked values stay masked."""
    method, url, headers, body = _request_parts(detail)
    lang = language.strip().lower()
    if lang == "curl":
        parts = ["curl", "-X", shlex.quote(method), shlex.quote(url)]
        for key, value in headers.items():
            parts.extend(["-H", shlex.quote(f"{key}: {value}")])
        if body:
            parts.extend(["--data-binary", shlex.quote(body)])
        return " \\\n  ".join(parts)
    if lang == "swift":
        lines = [
            f"var request = URLRequest(url: URL(string: {_swift_string(url)})!)",
            f"request.httpMethod = {_swift_string(method)}",
        ]
        lines += [
            f"request.setValue({_swift_string(v)}, forHTTPHeaderField: {_swift_string(k)})"
            for k, v in headers.items()
        ]
        if body:
            lines.append(f"request.httpBody = Data({_swift_string(body)}.utf8)")
        lines += [
            "let (data, response) = try await URLSession.shared.data(for: request)",
            "print((response as? HTTPURLResponse)?.statusCode ?? -1, data.count)",
        ]
        return "\n".join(lines)
    if lang == "python":
        lines = [
            "import requests",
            "",
            f"headers = {json.dumps(headers, indent=4, ensure_ascii=False)}",
        ]
        call = f"response = requests.request({method!r}, {url!r}, headers=headers"
        if body:
            lines.append(f"body = {body!r}")
            call += ", data=body.encode()"
        lines += [f"{call}, timeout=30)", "print(response.status_code, len(response.content))"]
        return "\n".join(lines)
    raise ValueError("language must be curl, swift, or python")


def crash_summary(events: Iterable[Mapping[str, Any]], *, limit: int = 3) -> dict[str, Any]:
    """Newest crashes first, each with its whole stack as one line per frame."""
    rows = []
    for item in events:
        payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else item
        if not isinstance(payload, Mapping):
            continue
        frames = [frame for frame in payload.get("frames") or [] if isinstance(frame, Mapping)]
        executable = str(payload.get("executable") or "")
        app = [frame for frame in frames if executable and frame.get("image") == executable]
        raw_symbolication = payload.get("symbolication")
        symbolication = raw_symbolication if isinstance(raw_symbolication, Mapping) else {}
        raw_diagnosis = payload.get("diagnosis")
        diagnosis = raw_diagnosis if isinstance(raw_diagnosis, Mapping) else {}
        raw_site = diagnosis.get("crash_site")
        site = raw_site if isinstance(raw_site, Mapping) else {}
        replay = payload.get("replay") if isinstance(payload.get("replay"), Mapping) else {}
        rows.append(
            {
                "id": item.get("id") or payload.get("id"),
                "headline": diagnosis.get("headline"),
                "crash_site": (
                    {key: site.get(key) for key in ("file", "line", "function", "path")}
                    if site
                    else None
                ),
                "replay": replay.get("status"),
                "when": item.get("created_at"),
                "source": payload.get("source"),
                "name": payload.get("name"),
                "reason": payload.get("reason"),
                "thread": payload.get("thread"),
                "previous_session": bool(payload.get("previous_session")),
                "symbolication": symbolication.get("status"),
                "missing_dsym": symbolication.get("missing_dsym"),
                "first_app_frame": _stack_line(app[0]) if app else None,
                "stack": [_stack_line(frame) for frame in frames],
                "other_threads": len(payload.get("threads") or []),
            }
        )
    rows.sort(key=lambda row: str(row.get("when") or ""), reverse=True)
    return {"count": len(rows), "crashes": rows[: max(1, limit)]}
