"""Apps seen only through the capture proxy, with no SDK linked.

A phone that points its Wi-Fi proxy at this Mac (same network or Tailscale) sends traffic
without ever registering. The proxy sees the peer address and the User-Agent, which is
enough to list the app. Memory only. Request and response bodies are never kept here.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PROXY_ONLINE_SECONDS = 90.0
PROXY_FORGET_SECONDS = 3600.0
MAX_PROXY_CLIENTS = 200

_PRODUCT = re.compile(r"^(?P<name>[^/\s()]+)(?:/(?P<version>[^\s()]+))?")
_BUNDLE = re.compile(r"\((?P<bundle>[A-Za-z0-9-]+(?:\.[A-Za-z0-9_-]+)+)[;)]")
_BROWSERS = (("Edg", "Edge"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari"))


@dataclass(frozen=True, slots=True)
class ParsedAgent:
    app_name: str
    app_version: str = ""
    bundle_id: str = ""
    platform: str = ""


def parse_user_agent(user_agent: str) -> ParsedAgent:
    """Best-effort app identity from a User-Agent such as `OneApp/3.2 CFNetwork/1568 Darwin/24`."""
    text = (user_agent or "").strip()[:400]
    if not text:
        return ParsedAgent(app_name="Unknown app")
    match = _PRODUCT.match(text)
    name = match.group("name") if match else ""
    version = (match.group("version") or "") if match else ""
    bundle = _BUNDLE.search(text)
    platform = _platform(text)
    if name == "Mozilla" or not name:
        label = next((label for token, label in _BROWSERS if token in text), "Browser")
        return ParsedAgent(app_name=label, platform=platform)
    return ParsedAgent(
        app_name=name,
        app_version=version,
        bundle_id=bundle.group("bundle") if bundle else "",
        platform=platform,
    )


def _platform(text: str) -> str:
    lowered = text.lower()
    if "appletv" in lowered or "tvos" in lowered:
        return "tvOS"
    if "iphone" in lowered or "ipad" in lowered or "; ios " in lowered or " ios " in lowered:
        return "iOS"
    if "android" in lowered or "okhttp" in lowered or "dalvik" in lowered:
        return "Android"
    if "macintosh" in lowered or "mac os x" in lowered:
        return "macOS"
    if "cfnetwork" in lowered or "darwin" in lowered:
        return "Apple"
    return ""


def normalize_peer(peer: object) -> str:
    """Strip IPv6-mapped IPv4 and reject anything that is not an IP address."""
    text = str(peer or "").strip()
    if text.startswith("::ffff:"):
        text = text[len("::ffff:") :]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


@dataclass(slots=True)
class ProxyClient:
    id: str
    peer: str
    app_name: str
    app_version: str
    bundle_id: str
    platform: str
    user_agent: str
    first_seen: float
    last_seen: float
    requests: int = 0
    last_host: str = ""

    def as_dict(self, now: float) -> dict[str, object]:
        online = now - self.last_seen <= PROXY_ONLINE_SECONDS
        return {
            "id": self.id,
            "source": "proxy",
            "peer": self.peer,
            "app_name": self.app_name,
            "app_version": self.app_version,
            "bundle_id": self.bundle_id,
            "platform": self.platform,
            "user_agent": self.user_agent,
            "requests": self.requests,
            "last_host": self.last_host,
            "online": online,
            "state": "online" if online else "offline",
            "first_seen_at": datetime.fromtimestamp(self.first_seen, UTC).isoformat(),
            "last_seen_at": datetime.fromtimestamp(self.last_seen, UTC).isoformat(),
        }


@dataclass(slots=True)
class ProxyClientRegistry:
    """Thread-safe: written from the mitmproxy thread, read from FastAPI."""

    time_fn: Callable[[], float] = time.time
    ignore_loopback: bool = True
    _clients: dict[str, ProxyClient] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, *, peer: object, user_agent: str, host: str = "") -> None:
        address = normalize_peer(peer)
        if not address:
            return
        if self.ignore_loopback and ipaddress.ip_address(address).is_loopback:
            return
        parsed = parse_user_agent(user_agent)
        key = f"proxy:{address}:{parsed.app_name.lower()}"
        now = self.time_fn()
        with self._lock:
            entry = self._clients.get(key)
            if entry is None:
                self._prune_locked(now)
                entry = ProxyClient(
                    id=key,
                    peer=address,
                    app_name=parsed.app_name,
                    app_version=parsed.app_version,
                    bundle_id=parsed.bundle_id,
                    platform=parsed.platform,
                    user_agent=(user_agent or "")[:400],
                    first_seen=now,
                    last_seen=now,
                )
                self._clients[key] = entry
            entry.last_seen = now
            entry.requests += 1
            if host:
                entry.last_host = host[:253]
            if parsed.app_version and not entry.app_version:
                entry.app_version = parsed.app_version
            if parsed.bundle_id and not entry.bundle_id:
                entry.bundle_id = parsed.bundle_id

    def snapshot(self) -> list[dict[str, object]]:
        now = self.time_fn()
        with self._lock:
            rows = [entry.as_dict(now) for entry in self._clients.values()]
        rows.sort(key=lambda row: str(row["last_seen_at"]), reverse=True)
        return rows

    def clear(self) -> None:
        with self._lock:
            self._clients.clear()

    def _prune_locked(self, now: float) -> None:
        stale = [
            key
            for key, entry in self._clients.items()
            if now - entry.last_seen > PROXY_FORGET_SECONDS
        ]
        for key in stale:
            del self._clients[key]
        if len(self._clients) >= MAX_PROXY_CLIENTS:
            oldest = min(self._clients.values(), key=lambda entry: entry.last_seen)
            del self._clients[oldest.id]


def unmatched_proxy_rows(
    proxy_rows: list[dict[str, object]],
    sdk_apps: list[dict[str, Any]],
    activity: dict[str, Any],
) -> list[dict[str, object]]:
    """Drop proxy rows that describe an app already registered through the SDK."""
    remaining: list[dict[str, object]] = []
    for row in proxy_rows:
        name = str(row["app_name"]).lower()
        bundle = str(row["bundle_id"]).lower()
        matched = False
        for app in sdk_apps:
            peer = str((activity.get(app["id"]) or {}).get("peer") or "")
            names = {
                str(app.get("app_name") or "").lower(),
                str(app.get("executable") or "").lower(),
            }
            same_bundle = bool(bundle) and bundle == str(app.get("bundle_id") or "").lower()
            same_app = same_bundle or name in names
            if same_app and peer and normalize_peer(peer) == row["peer"]:
                app["via_proxy"] = True
                matched = True
                break
        if not matched:
            remaining.append(row)
    return remaining
