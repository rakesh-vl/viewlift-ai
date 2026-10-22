"""Classify captured flows as user/player beacons or VLAnalytics SDK traffic."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlparse

from viewlift_ai.models.flow import FlowRecord
from viewlift_ai.services.flow_present import body_view

# Host / path markers for third-party VLAnalytics clients.
CLIENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("appsflyer", "appsFlyer"),
    ("appsflyersdk", "appsFlyer"),
    ("clevertap", "cleverTap"),
    ("wzrkt.com", "cleverTap"),
    ("braze.com", "braze"),
    ("appboy.com", "braze"),
    ("demdex.net", "adobe"),
    ("omtrdc.net", "adobe"),
    ("adobedc.net", "adobe"),
    ("adobe.io", "adobe"),
    ("comscore.com", "comscore"),
    ("scorecardresearch", "comscore"),
    ("conviva.com", "conviva"),
    ("cws.conviva", "conviva"),
    ("urbanairship", "airship"),
    ("airship.com", "airship"),
    ("asnapieu.com", "airship"),
    ("facebook.com", "facebook"),
    ("facebook.net", "facebook"),
    ("graph.facebook", "facebook"),
    ("datadoghq", "dataDog"),
    ("browser-intake.datadog", "dataDog"),
    ("firebase", "firebase"),
    ("app-measurement", "firebase"),
    ("google-analytics", "firebase"),
    ("googletagmanager", "firebase"),
    ("amplitude.com", "amplitude"),
    ("mixpanel.com", "mixpanel"),
    ("segment.io", "segment"),
    ("segment.com", "segment"),
    ("mux.com", "mux"),
    ("bitmovin.com", "bitmovin"),
)

_BEACON_HOST_RE = re.compile(r"beacon|vlbeacon|viewlift", re.I)
_USER_HINTS = ("userbeacon", "user-beacon", "user_beacon", "/user/", "ubeacon")
_PLAYER_HINTS = ("playerbeacon", "player-beacon", "player_beacon", "/player/", "pbeacon")

# Common VLBeacon / analytics parameter keys to surface first.
_PRIORITY_KEYS = (
    "event",
    "eventName",
    "event_name",
    "en",
    "userId",
    "userid",
    "uid",
    "vid",
    "profid",
    "profileId",
    "siteId",
    "site",
    "pfm",
    "player",
    "media_type",
    "stream_id",
    "mvpdprovider",
    "tveProvider",
    "guid",
    "title",
    "apos",
    "vpos",
    "bitrate",
    "resolution",
    "appversion",
    "environment",
    "source",
)


def classify_flow(
    flow: FlowRecord,
    *,
    user_beacon_hosts: set[str] | None = None,
    player_beacon_hosts: set[str] | None = None,
) -> str | None:
    """Return category or None if not analytics-related."""
    host = (flow.host or "").lower()
    path = (flow.path or "").lower()
    query = (flow.query or "").lower()
    blob = f"{host}{path}?{query}"

    user_hosts = {h.lower() for h in (user_beacon_hosts or set()) if h}
    player_hosts = {h.lower() for h in (player_beacon_hosts or set()) if h}

    if host in user_hosts or any(h and h in host for h in user_hosts):
        return "user_beacon"
    if host in player_hosts or any(h and h in host for h in player_hosts):
        return "player_beacon"

    if any(hint in blob for hint in _USER_HINTS):
        return "user_beacon"
    if any(hint in blob for hint in _PLAYER_HINTS):
        return "player_beacon"
    if "beacon" in blob and _BEACON_HOST_RE.search(host + path):
        # Ambiguous beacon endpoint — prefer path cues, else mark generic beacon.
        if "player" in blob:
            return "player_beacon"
        if "user" in blob:
            return "user_beacon"
        return "beacon"

    for marker, client in CLIENT_MARKERS:
        if marker in host or marker in path:
            return f"client:{client}"
    return None


def extract_params(flow: FlowRecord, *, max_keys: int = 80) -> dict[str, Any]:
    """Readable request parameters from query + body (redaction happens at API layer)."""
    params: dict[str, Any] = {}
    for key, value in parse_qsl(flow.query or "", keep_blank_values=True):
        params[key] = value

    ctype = ""
    for name, value in (flow.request_headers or {}).items():
        if name.lower() == "content-type":
            ctype = value
            break
    view = body_view(flow.request_body or b"", ctype)
    text = view.get("pretty") or view.get("text") or ""
    if isinstance(text, str) and text.strip():
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    params.setdefault(str(key), value)
            elif isinstance(parsed, list):
                params.setdefault("_body_array", parsed[:40])
        elif "=" in stripped and "&" in stripped:
            for key, value in parse_qsl(stripped, keep_blank_values=True):
                params.setdefault(key, unquote_plus(value))
        else:
            params.setdefault("_body_text", stripped[:2_000])

    # Prefer known keys first for UI scannability.
    ordered: dict[str, Any] = {}
    for key in _PRIORITY_KEYS:
        if key in params:
            ordered[key] = params.pop(key)
    for key in sorted(params.keys()):
        if len(ordered) >= max_keys:
            ordered["_truncated"] = True
            break
        ordered[key] = params[key]
    return ordered


def summarize_analytics_flow(
    flow: FlowRecord,
    category: str,
    *,
    redacted_params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": flow.id,
        "category": category,
        "method": flow.method,
        "host": flow.host,
        "path": flow.path,
        "query": flow.query,
        "status": flow.status,
        "duration_ms": flow.duration_ms,
        "source": flow.source.value,
        "created_at": flow.created_at.isoformat(),
        "params": redacted_params,
        "event_hint": _event_hint(redacted_params, category),
    }


def _event_hint(params: dict[str, Any], category: str) -> str:
    for key in ("eventName", "event_name", "event", "en", "name"):
        value = params.get(key)
        if value not in (None, ""):
            return str(value)
    if category.startswith("client:"):
        return category.split(":", 1)[1]
    return category


def hosts_from_urls(*urls: str | None) -> set[str]:
    out: set[str] = set()
    for url in urls:
        if not url:
            continue
        parsed = urlparse(url if "://" in url else f"https://{url}")
        if parsed.hostname:
            out.add(parsed.hostname.lower())
    return out


def list_analytics_traffic(
    flows: list[FlowRecord],
    *,
    user_beacon_url: str | None = None,
    player_beacon_url: str | None = None,
    redact_params,
) -> dict[str, Any]:
    user_hosts = hosts_from_urls(user_beacon_url)
    player_hosts = hosts_from_urls(player_beacon_url)
    buckets: dict[str, list[dict[str, Any]]] = {
        "user_beacon": [],
        "player_beacon": [],
        "beacon": [],
        "clients": [],
    }
    counts: dict[str, int] = {}
    for flow in flows:
        category = classify_flow(
            flow,
            user_beacon_hosts=user_hosts,
            player_beacon_hosts=player_hosts,
        )
        if category is None:
            continue
        counts[category] = counts.get(category, 0) + 1
        params = redact_params(extract_params(flow))
        row = summarize_analytics_flow(flow, category, redacted_params=params)
        if category.startswith("client:"):
            buckets["clients"].append(row)
        elif category in buckets:
            buckets[category].append(row)
        else:
            buckets["beacon"].append(row)
    return {
        "counts": counts,
        "user_beacon": buckets["user_beacon"],
        "player_beacon": buckets["player_beacon"],
        "beacon": buckets["beacon"],
        "clients": buckets["clients"],
        "total": sum(counts.values()),
    }
