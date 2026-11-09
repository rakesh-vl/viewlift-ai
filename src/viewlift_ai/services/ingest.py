"""Secure SDK ingest receiver: pairing, session tokens, and rate limits."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import ssl
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from viewlift_ai import defaults
from viewlift_ai.models.flow import FlowSource, FlowTiming
from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.services.bind_policy import (
    InterfaceLister,
    SetupValidationError,
    interface_scope,
    is_loopback_ip,
    is_vpn_address,
    preferred_lan_host,
    validate_port,
    validate_receiver_bind_ip,
)
from viewlift_ai.services.capture import CaptureService, HttpCaptureSnapshot
from viewlift_ai.services.debug_advertise import (
    DebugAdvertiser,
    DebugPublishing,
)
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.sdk_integration import sdk_integration
from viewlift_ai.services.setup import list_ipv4_interfaces

TimeFn = Callable[[], float]

DEFAULT_INGEST_PORT = defaults.INGEST_PORT
RECEIVER_PREF_FILENAME = "receiver.json"
PAIRING_TTL_SECONDS = 300.0
SESSION_TTL_SECONDS = 3600.0
DEFAULT_MAX_BODY_BYTES = defaults.MAX_BODY_BYTES
DEFAULT_RATE_LIMIT = 600  # requests per minute per session
DEFAULT_MAX_JSON_BYTES = 1_048_576
# Tailscale addresses seen on this Mac. They stay stable, so they keep being advertised while the
# VPN is down. A phone that sees any advertisement then learns the address for use off the Wi-Fi.
MAX_REMEMBERED_VPN_HOSTS = 4
# `stdout`/`stderr` come from the SDK console tee. `sdk` is explicit LogService calls.
SDK_LOG_SOURCES = frozenset({"sdk", "stdout", "stderr"})


def refuse_mcp_clash(host: str, port: int) -> None:
    """The MCP server listens on loopback. A loopback receiver on the same port cannot bind."""
    if is_loopback_ip(host) and port == defaults.mcp_port():
        msg = (
            f"Port {port} is the MCP server's port. Pick another receiver port, "
            "or set VIEWLIFT_AI_MCP_PORT to move the MCP server."
        )
        raise SetupValidationError(msg)


@dataclass(slots=True)
class PairingToken:
    token: str
    expires_at: float
    used: bool = False


@dataclass(slots=True)
class SessionToken:
    token: str
    expires_at: float
    client_id: str | None = None
    device_key: str | None = None


class ClientMismatchError(PermissionError):
    """Session token is bound to a different app instance (HTTP 403, no re-pair)."""


@dataclass(slots=True)
class ClientActivity:
    flows: int = 0
    logs: int = 0
    last_at: str | None = None
    last_path: str | None = None
    peer: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "flows": self.flows,
            "logs": self.logs,
            "last_at": self.last_at,
            "last_path": self.last_path,
            "peer": self.peer,
        }


@dataclass(slots=True)
class IngestActivity:
    """Counters for the Setup/Network UI so a silent SDK is diagnosable."""

    last_client: str | None = None
    last_client_id: str | None = None
    last_seen_at: str | None = None
    last_path: str | None = None
    last_error: str | None = None
    pairings: int = 0
    flows: int = 0
    logs: int = 0
    rejected: int = 0
    per_client: dict[str, ClientActivity] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "last_client": self.last_client,
            "last_client_id": self.last_client_id,
            "last_seen_at": self.last_seen_at,
            "last_path": self.last_path,
            "last_error": self.last_error,
            "pairings": self.pairings,
            "flows": self.flows,
            "logs": self.logs,
            "rejected": self.rejected,
            "clients": {key: value.as_dict() for key, value in self.per_client.items()},
        }


@dataclass(slots=True)
class IngestBindState:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = field(default_factory=defaults.ingest_port)
    iface: str = ""
    # Also listen on VPN addresses (Tailscale, NetBird) and advertise them as alternates.
    include_vpn: bool = False
    extra_hosts: tuple[str, ...] = ()


@dataclass(slots=True)
class IngestService:
    """
    Framework-free ingest logic. FastAPI/uvicorn adapter binds HTTPS separately.

    Disabled until Setup selects an interface. The receiver follows that interface's
    current address, including Wi-Fi and VPN. Never binds 0.0.0.0.
    """

    capture: CaptureService
    log_capture: LogCaptureService
    cert_dir: Path
    redaction: RedactionService | None = None
    time_fn: TimeFn | None = None
    pairing_ttl: float = PAIRING_TTL_SECONDS
    session_ttl: float = SESSION_TTL_SECONDS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT
    interface_lister: InterfaceLister | None = None
    debug_sessions: DebugSessionService | None = None
    advertiser: DebugPublishing = field(default_factory=DebugAdvertiser)
    _bind: IngestBindState = field(default_factory=IngestBindState)
    _pairing: dict[str, PairingToken] = field(default_factory=dict)
    _sessions: dict[str, SessionToken] = field(default_factory=dict)
    _rate: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _activity: IngestActivity = field(default_factory=IngestActivity, init=False)
    _fingerprint: str | None = field(default=None, init=False)
    _cert_path: Path | None = field(default=None, init=False)
    _key_path: Path | None = field(default=None, init=False)
    _server_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _server: Any = field(default=None, init=False, repr=False)
    _extra_servers: list[tuple[Any, threading.Thread]] = field(
        default_factory=list, init=False, repr=False
    )
    _known_vpn: tuple[str, ...] = field(default=(), init=False)
    _known_vpn_loaded: bool = field(default=False, init=False)
    _manual_disable: bool = field(default=False, init=False)
    _watch_stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _watch_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _redaction: RedactionService = field(init=False, repr=False)
    _time: TimeFn = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_redaction", self.redaction or RedactionService())
        object.__setattr__(self, "_time", self.time_fn or time.time)
        self.cert_dir = Path(self.cert_dir)
        self.cert_dir.mkdir(parents=True, exist_ok=True)

    @property
    def bind(self) -> IngestBindState:
        return self._bind

    @property
    def enabled(self) -> bool:
        return self._bind.enabled

    def status(self) -> dict[str, object]:
        self._ensure_tls_material()
        pairing = self._peek_pairing()
        return {
            "enabled": self._bind.enabled,
            "host": self._bind.host if self._bind.enabled else None,
            "port": self._bind.port if self._bind.enabled else None,
            "iface": self._bind.iface if self._bind.enabled else None,
            "receiver_url": self.receiver_url(),
            "receiver_urls": self.receiver_urls(),
            "include_vpn": self._bind.include_vpn,
            "extra_hosts": list(self._bind.extra_hosts),
            "vpn": self.vpn_status(),
            "pairing_token": pairing,
            "pinned_fingerprint": self._fingerprint,
            "debug_key": self.debug_key() if self._bind.enabled else None,
            "note": (
                "No proxy or CA install. The app reads the address, port, certificate "
                "fingerprint, and debug key from this dashboard. Pairing token is "
                "one-use and short-lived; exchange it for a session token."
                if self._bind.enabled
                else (
                    "Receiver is off, so the app stays offline. USB does not register it. "
                    "Enable Wi-Fi or a VPN interface (NetBird or Tailscale). "
                    "A proxy is not required."
                )
            ),
            "max_body_bytes": self.max_body_bytes,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "server_running": self._server is not None,
            "activity": self.activity(),
            "integration": sdk_integration(
                enabled=self._bind.enabled,
                host=self._bind.host if self._bind.enabled else None,
                port=self._bind.port if self._bind.enabled else None,
                fingerprint=self._fingerprint,
            ),
        }

    def activity(self) -> dict[str, object]:
        with self._lock:
            return self._activity.as_dict()

    def record_request(
        self,
        client: str,
        path: str,
        *,
        error: str | None = None,
        client_id: str | None = None,
    ) -> None:
        with self._lock:
            act = self._activity
            now = datetime.now(UTC).isoformat()
            act.last_client = client
            act.last_seen_at = now
            act.last_path = path
            if client_id:
                act.last_client_id = client_id
            if error is not None:
                act.last_error = f"{path}: {error}"
                act.rejected += 1
                return
            per = act.per_client.setdefault(client_id, ClientActivity()) if client_id else None
            if per is not None:
                per.last_at = now
                per.last_path = path
                per.peer = client
            if path == "/v1/pair":
                act.pairings += 1
            elif path == "/v1/flows":
                act.flows += 1
                if per is not None:
                    per.flows += 1
            elif path == "/v1/logs":
                act.logs += 1
                if per is not None:
                    per.logs += 1

    def forget_client_activity(self, client_id: str | None = None) -> None:
        with self._lock:
            if client_id is None:
                self._activity.per_client.clear()
            else:
                self._activity.per_client.pop(client_id, None)

    def receiver_url(self) -> str | None:
        if not self._bind.enabled:
            return None
        return f"https://{self._bind.host}:{self._bind.port}"

    def receiver_urls(self) -> list[str]:
        """Primary receiver first, then VPN alternates."""
        if not self._bind.enabled:
            return []
        hosts = [self._bind.host, *self._bind.extra_hosts]
        return [f"https://{host}:{self._bind.port}" for host in hosts]

    def remembered_vpn_hosts(self) -> tuple[str, ...]:
        """Tailscale addresses this Mac had before. Loaded once from receiver.json."""
        if not self._known_vpn_loaded:
            pref = self._load_receiver_pref() or {}
            raw = pref.get("vpn_hosts")
            found: list[str] = []
            if isinstance(raw, list):
                for item in raw:
                    host = str(item).strip()
                    if is_vpn_address(host) and host not in found:
                        found.append(host)
            self._known_vpn = tuple(found[:MAX_REMEMBERED_VPN_HOSTS])
            self._known_vpn_loaded = True
        return self._known_vpn

    def _remember_vpn_hosts(self, live: tuple[str, ...]) -> None:
        """Keep live Tailscale addresses first, then older ones, newest first."""
        current = [host for host in live if is_vpn_address(host)]
        older = [host for host in self.remembered_vpn_hosts() if host not in current]
        self._known_vpn = tuple([*current, *older][:MAX_REMEMBERED_VPN_HOSTS])

    def advertised_alternates(self) -> tuple[str, ...]:
        """Addresses a phone can fall back to: live VPN hosts, then remembered ones not up now."""
        if not self._bind.include_vpn:
            return self._bind.extra_hosts
        extras = list(self._bind.extra_hosts)
        for host in self.remembered_vpn_hosts():
            if host not in extras and host != self._bind.host:
                extras.append(host)
        return tuple(extras)

    def vpn_status(self) -> dict[str, object]:
        """What the Setup page shows about reaching this Mac off the Wi-Fi."""
        listening = [
            host for host in (self._bind.host, *self._bind.extra_hosts) if is_vpn_address(host)
        ]
        remembered = list(self.remembered_vpn_hosts())
        if not self._bind.enabled:
            note = "Receiver is off."
        elif listening:
            note = (
                "Reachable over the VPN. A phone learns this address the first time it "
                "sees this Mac, on any network, and keeps it."
            )
        elif self._bind.include_vpn:
            note = (
                "No Tailscale or VPN address is up on this Mac. Connect Tailscale. "
                "The receiver binds it within seconds."
            )
        else:
            note = "Tick the VPN option so a phone off the Wi-Fi can reach this Mac."
        return {
            "include": self._bind.include_vpn,
            "listening": listening,
            "advertised": list(self.advertised_alternates()) if self._bind.enabled else [],
            "remembered": remembered,
            "note": note,
        }

    def vpn_hosts(self, rows: list[dict[str, str]], *, primary: str) -> tuple[str, ...]:
        """VPN interface addresses other than the primary bind. Each must pass bind policy."""
        found: list[str] = []
        for row in rows:
            ip = str(row.get("ip") or "")
            if not ip or ip == primary or ip in found:
                continue
            if interface_scope(str(row.get("iface") or ""), ip) != "vpn":
                continue
            try:
                found.append(validate_receiver_bind_ip(ip, interfaces=rows))
            except SetupValidationError:
                continue
        return tuple(found)

    def desired_bind_host(self) -> str:
        """LAN address when one exists, otherwise localhost."""
        lister = self.interface_lister or list_ipv4_interfaces
        return preferred_lan_host(lister()) or "127.0.0.1"

    def follow_network(self) -> str:
        """Keep the selected interface. Update only when that interface's address changes."""
        if not self.enabled or not self._bind.iface:
            return self._bind.host
        rows = (self.interface_lister or list_ipv4_interfaces)()
        current = next((row for row in rows if row.get("iface") == self._bind.iface), None)
        primary = str(current.get("ip") or "") if current is not None else self._bind.host
        extras_changed = (
            current is not None
            and self._bind.include_vpn
            and self.vpn_hosts(rows, primary=primary) != self._bind.extra_hosts
        )
        if (current is not None and primary != self._bind.host) or extras_changed:
            self.enable(
                host=primary,
                port=self._bind.port,
                iface=self._bind.iface,
                include_vpn=self._bind.include_vpn,
            )
        self._publish_receiver()
        return self._bind.host

    def start_network_watch(self) -> None:
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self.restore_saved()
        self._manual_disable = False
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_network,
            name="sdk-network-watch",
            daemon=True,
        )
        self._watch_thread.start()

    def stop_network_watch(self) -> None:
        self._watch_stop.set()
        thread = self._watch_thread
        self._watch_thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        self.advertiser.stop()

    def _watch_network(self) -> None:
        while not self._watch_stop.is_set():
            try:
                if not self.enabled and not self._manual_disable:
                    # The saved interface may not have been up at launch. Keep trying.
                    self.restore_saved()
                self.follow_network()
            except (SetupValidationError, OSError):
                pass
            self._watch_stop.wait(2)

    def enable(
        self,
        *,
        host: str,
        port: int | None = None,
        iface: str | None = None,
        include_vpn: bool = False,
    ) -> dict[str, object]:
        self._manual_disable = False
        rows = (self.interface_lister or list_ipv4_interfaces)()
        chosen_iface = (iface or "").strip()
        if chosen_iface:
            match = next((row for row in rows if row.get("iface") == chosen_iface), None)
            if match is None:
                msg = f"Interface {chosen_iface} is not active"
                raise SetupValidationError(msg)
            candidate = str(match.get("ip") or "")
        else:
            candidate = (host or "").strip() or "127.0.0.1"
        ip = validate_receiver_bind_ip(candidate, interfaces=rows)
        if not chosen_iface:
            chosen_iface = next(
                (str(row.get("iface") or "") for row in rows if str(row.get("ip") or "") == ip),
                "lo0" if is_loopback_ip(ip) else "",
            )
        validated_port = validate_port(defaults.ingest_port() if port is None else port)
        refuse_mcp_clash(ip, validated_port)
        extras = self.vpn_hosts(rows, primary=ip) if include_vpn else ()
        self._remember_vpn_hosts((ip, *extras))
        self._ensure_tls_material()
        with self._lock:
            requires_rebind = self._server is not None and (
                self._bind.host != ip
                or self._bind.port != validated_port
                or self._bind.extra_hosts != extras
            )
        if requires_rebind:
            self.stop_https_server()
        with self._lock:
            self._bind = IngestBindState(
                enabled=True,
                host=ip,
                port=validated_port,
                iface=chosen_iface,
                include_vpn=bool(include_vpn),
                extra_hosts=extras,
            )
            self._mint_pairing_locked()
        self.start_https_server()
        self._publish_receiver()
        self._save_receiver_pref()
        return self.status()

    def stop(self) -> None:
        """Release the listener. Leave receiver.json so the next start can restore it."""
        previous = self._bind
        self.advertiser.stop()
        self.stop_https_server()
        with self._lock:
            self._bind = IngestBindState(enabled=False, host="127.0.0.1", port=previous.port)
            self._pairing.clear()
            self._sessions.clear()
            self._rate.clear()

    def disable(self) -> dict[str, object]:
        previous = self._bind
        self._manual_disable = True
        self.stop()
        self._save_receiver_pref(
            enabled=False,
            iface=previous.iface,
            port=previous.port,
            include_vpn=previous.include_vpn,
        )
        return self.status()

    def restore_saved(self) -> bool:
        """Bring the last enabled receiver back after a console restart."""
        if self.enabled:
            return True
        pref = self._load_receiver_pref()
        if not pref or not pref.get("enabled"):
            return False
        iface = str(pref.get("iface") or "").strip()
        if not iface:
            return False
        try:
            port = int(pref.get("port") or defaults.ingest_port())
        except (TypeError, ValueError):
            return False
        try:
            self.enable(
                host="",
                port=port,
                iface=iface,
                include_vpn=bool(pref.get("include_vpn")),
            )
        except (SetupValidationError, OSError):
            return False
        return self.enabled

    def _pref_path(self) -> Path:
        return self.cert_dir / RECEIVER_PREF_FILENAME

    def _load_receiver_pref(self) -> dict[str, object] | None:
        path = self._pref_path()
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _save_receiver_pref(
        self,
        *,
        enabled: bool | None = None,
        iface: str | None = None,
        port: int | None = None,
        include_vpn: bool | None = None,
    ) -> None:
        payload = {
            "enabled": self._bind.enabled if enabled is None else enabled,
            "iface": self._bind.iface if iface is None else iface,
            "port": self._bind.port if port is None else port,
            "include_vpn": self._bind.include_vpn if include_vpn is None else include_vpn,
            "vpn_hosts": list(self.remembered_vpn_hosts()),
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        path = self._pref_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".receiver-", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def mint_pairing_token(self) -> dict[str, object]:
        if not self._bind.enabled:
            msg = "Enable the SDK receiver before minting a pairing token."
            raise SetupValidationError(msg)
        with self._lock:
            token = self._mint_pairing_locked()
        return {
            "pairing_token": token,
            "expires_in": int(self.pairing_ttl),
            "receiver_url": self.receiver_url(),
            "pinned_fingerprint": self._fingerprint,
        }

    def _publish_receiver(self) -> None:
        if not self.enabled or not self._fingerprint:
            self.advertiser.stop()
            return
        self.advertiser.publish(
            self._bind.host,
            self._bind.port,
            fingerprint=self._fingerprint,
            debug_key=self.debug_key(),
            alt=self.advertised_alternates(),
        )

    def debug_key(self) -> str:
        """Stable local debug key. Mode 0600. Not a substitute for the cert pin."""
        path = self.cert_dir / "debug.key"
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        key = secrets.token_urlsafe(32)
        path.write_text(key + "\n", encoding="utf-8")
        import os

        os.chmod(path, 0o600)
        return key

    def mint_pairing_for_debug_key(self, presented: str) -> dict[str, object]:
        if not self._bind.enabled:
            msg = "SDK receiver is disabled"
            raise PermissionError(msg)
        expected = self.debug_key()
        if not presented or not secrets.compare_digest(presented, expected):
            msg = "invalid debug key"
            raise PermissionError(msg)
        with self._lock:
            token = self._mint_pairing_locked()
        return {"pairing_token": token, "expires_in": int(self.pairing_ttl)}

    def exchange_pairing(
        self,
        pairing_token: str,
        *,
        client_id: str | None = None,
        device_key: str | None = None,
    ) -> dict[str, object]:
        if not self._bind.enabled:
            msg = "SDK receiver is disabled"
            raise PermissionError(msg)
        now = self._time()
        with self._lock:
            entry = self._pairing.get(pairing_token)
            if entry is None:
                msg = "pairing token not found"
                raise LookupError(msg)
            if entry.used:
                msg = "pairing token already used"
                raise PermissionError(msg)
            if now >= entry.expires_at:
                msg = "pairing token expired"
                raise TimeoutError(msg)
            entry.used = True
            session = secrets.token_urlsafe(32)
            self._sessions[session] = SessionToken(
                token=session,
                expires_at=now + self.session_ttl,
                client_id=_client_identifier(client_id),
                device_key=(device_key or "").strip()[:200] or None,
            )
        return {
            "session_token": session,
            "expires_in": int(self.session_ttl),
            "token_type": "Bearer",
        }

    def require_session(self, authorization: str | None) -> str:
        if not self._bind.enabled:
            msg = "SDK receiver is disabled"
            raise PermissionError(msg)
        token = _bearer(authorization)
        if not token:
            msg = "missing session bearer token"
            raise PermissionError(msg)
        now = self._time()
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None or now >= entry.expires_at:
                msg = "invalid or expired session token"
                raise PermissionError(msg)
            if not self._allow_rate_locked(token, now):
                msg = "rate limit exceeded"
                raise OverflowError(msg)
        return token

    def bind_client(self, token: str, presented: str | None) -> str | None:
        """
        Resolve the app instance for a session. First presented id binds an unbound
        session; a different id later is rejected so one app cannot write as another.
        """
        candidate = _client_identifier(presented)
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                msg = "invalid or expired session token"
                raise PermissionError(msg)
            if entry.client_id is None:
                entry.client_id = candidate
                return candidate
            if candidate is not None and candidate != entry.client_id:
                msg = "session is bound to a different client"
                raise ClientMismatchError(msg)
            return entry.client_id

    def session_device_key(self, token: str) -> str | None:
        with self._lock:
            entry = self._sessions.get(token)
            return entry.device_key if entry is not None else None

    def ingest_flow(
        self,
        payload: dict[str, Any],
        *,
        client_id: str | None = None,
    ) -> dict[str, object]:
        flow_id = str(payload.get("id") or uuid.uuid4())
        method = str(payload.get("method") or "GET").upper()
        url = str(payload.get("url") or "")
        host, path, query = _split_url(url, payload.get("host"), payload.get("path"))
        req_headers = _as_str_map(payload.get("request_headers"))
        resp_headers = _as_str_map(payload.get("response_headers"))
        req_body = _decode_body(payload.get("request_body"), self.max_body_bytes)
        resp_body = _decode_body(payload.get("response_body"), self.max_body_bytes)
        snapshot = HttpCaptureSnapshot(
            id=flow_id,
            method=method,
            host=host,
            path=path,
            status=_as_optional_int(payload.get("status")),
            duration_ms=_as_optional_float(payload.get("duration_ms")),
            protocol=str(payload.get("protocol") or "HTTPS"),
            request_headers=req_headers,
            request_body=req_body,
            response_headers=resp_headers,
            response_body=resp_body,
            error=str(payload["error"]) if payload.get("error") else None,
            request_id=str(payload["request_id"]) if payload.get("request_id") else None,
            content_type=str(payload["content_type"]) if payload.get("content_type") else None,
            query=query,
            timing=FlowTiming(
                connect_ms=_as_optional_float((payload.get("timing") or {}).get("connect_ms")),
                tls_ms=_as_optional_float((payload.get("timing") or {}).get("tls_ms")),
                ttfb_ms=_as_optional_float((payload.get("timing") or {}).get("ttfb_ms")),
                download_ms=_as_optional_float((payload.get("timing") or {}).get("download_ms")),
            ),
            created_at=datetime.now(UTC),
            record_bodies=True,
            source=FlowSource.SDK,
            client_id=client_id,
        )
        flow = self.capture.persist_http(snapshot)
        self._touch(client_id)
        return {"ok": True, "id": flow.id, "source": FlowSource.SDK.value}

    def _touch(self, client_id: str | None) -> None:
        """An upload proves the app is alive even when the command poll is stalled."""
        if self.debug_sessions is not None:
            self.debug_sessions.touch(client_id)

    def ingest_log(
        self,
        payload: dict[str, Any],
        *,
        client_id: str | None = None,
    ) -> dict[str, object]:
        message = str(payload.get("message") or "")
        if len(message.encode("utf-8")) > self.max_json_bytes:
            msg = "log message exceeds size cap"
            raise ValueError(msg)
        source = str(payload.get("source") or "sdk")
        if source not in SDK_LOG_SOURCES:
            source = "sdk"
        record = self.log_capture.persist(
            level=str(payload.get("level") or "info"),
            subsystem=str(payload.get("subsystem") or "sdk"),
            message=message,
            source=source,
            request_id=str(payload["request_id"]) if payload.get("request_id") else None,
            log_id=str(payload["id"]) if payload.get("id") else None,
            client_id=client_id,
        )
        self._touch(client_id)
        return {"ok": True, "id": record.id, "source": source}

    def ssl_context(self) -> ssl.SSLContext:
        self._ensure_tls_material()
        assert self._cert_path is not None
        assert self._key_path is not None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(self._cert_path), str(self._key_path))
        return ctx

    def start_https_server(self) -> None:
        """Bind HTTPS ingest on the selected LAN host using stdlib HTTPServer."""
        if not self._bind.enabled:
            return
        if self._server is not None:
            return
        import json as json_mod
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return

            _client_id: str | None = None

            def _no_store(self, code: int, payload: dict[str, object]) -> None:
                path = self.path.split("?", 1)[0]
                if path != "/health":
                    error = None if code < 400 else f"{code} {payload.get('detail') or ''}".strip()
                    service.record_request(
                        self.client_address[0],
                        path,
                        error=error,
                        client_id=self._client_id,
                    )
                body = json_mod.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0:
                    return {}
                if length > service.max_json_bytes:
                    raise ValueError("request body too large")
                raw = self.rfile.read(length)
                data = json_mod.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                return data

            def _authorized_client(self) -> str | None:
                token = service.require_session(self.headers.get("Authorization"))
                self._client_id = service.bind_client(
                    token, self.headers.get("X-Viewlift-Client-ID")
                )
                return self._client_id

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                try:
                    if path == "/v1/debug/pair":
                        presented = self.headers.get("X-Viewlift-Debug-Key") or ""
                        result = service.mint_pairing_for_debug_key(presented)
                        self._no_store(200, result)
                        return
                    if path == "/v1/pair":
                        body = self._read_json()
                        token = str(body.get("pairing_token") or "")
                        result = service.exchange_pairing(
                            token,
                            client_id=str(body.get("client_id") or "") or None,
                            device_key=str(body.get("device_key") or "") or None,
                        )
                        self._client_id = _client_identifier(body.get("client_id"))
                        self._no_store(200, result)
                        return
                    if path in {"/v1/flows", "/v1/logs"}:
                        client_id = self._authorized_client()
                        body = self._read_json()
                        if path == "/v1/flows":
                            self._no_store(200, service.ingest_flow(body, client_id=client_id))
                        else:
                            self._no_store(200, service.ingest_log(body, client_id=client_id))
                        return
                    if path == "/v1/debug/register":
                        body = self._read_json()
                        token = service.require_session(self.headers.get("Authorization"))
                        self._client_id = service.bind_client(
                            token, str(body.get("client_id") or "")
                        )
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        if self._client_id and not body.get("client_id"):
                            body["client_id"] = self._client_id
                        if not body.get("device_key"):
                            body["device_key"] = service.session_device_key(token) or ""
                        self._no_store(200, debug.register(body))
                        return
                    if path == "/v1/debug/events":
                        client_id = self._authorized_client() or ""
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        self._no_store(200, debug.ingest_event(client_id, self._read_json()))
                        return
                    self._no_store(404, {"ok": False, "detail": "not found"})
                except LookupError as exc:
                    self._no_store(404, {"ok": False, "detail": str(exc)})
                except ClientMismatchError as exc:
                    self._no_store(403, {"ok": False, "detail": str(exc)})
                except PermissionError as exc:
                    self._no_store(401, {"ok": False, "detail": str(exc)})
                except TimeoutError as exc:
                    self._no_store(410, {"ok": False, "detail": str(exc)})
                except OverflowError as exc:
                    self._no_store(429, {"ok": False, "detail": str(exc)})
                except ValueError as exc:
                    self._no_store(400, {"ok": False, "detail": str(exc)})
                except Exception as exc:
                    self._no_store(500, {"ok": False, "detail": str(exc)})

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    self._no_store(
                        200,
                        {"ok": True, "enabled": service.enabled, "source": "sdk-ingest"},
                    )
                    return
                if path == "/v1/debug/commands":
                    try:
                        client_id = self._authorized_client() or ""
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        self._no_store(200, {"commands": debug.poll(client_id)})
                    except LookupError as exc:
                        self._no_store(404, {"ok": False, "detail": str(exc)})
                    except ClientMismatchError as exc:
                        self._no_store(403, {"ok": False, "detail": str(exc)})
                    except PermissionError as exc:
                        self._no_store(401, {"ok": False, "detail": str(exc)})
                    except Exception as exc:
                        self._no_store(500, {"ok": False, "detail": str(exc)})
                    return
                self._no_store(404, {"ok": False, "detail": "not found"})

        context = self.ssl_context()
        for index, host in enumerate((self._bind.host, *self._bind.extra_hosts)):
            try:
                server = ThreadingHTTPServer((host, self._bind.port), Handler)
            except OSError:
                if index == 0:
                    raise
                continue  # A VPN address that cannot bind must not take the LAN receiver down.
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(
                target=_serve_forever,
                args=(server,),
                name="sdk-ingest-https" if index == 0 else f"sdk-ingest-https-{index}",
                daemon=True,
            )
            if index == 0:
                self._server = server
                self._server_thread = thread
            else:
                self._extra_servers.append((server, thread))
            thread.start()

    def stop_https_server(self) -> None:
        extras = self._extra_servers
        self._extra_servers = []
        for extra_server, extra_thread in extras:
            _stop_server(extra_server, extra_thread)
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        thread = self._server_thread
        self._server_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _ensure_tls_material(self) -> None:
        cert_path = self.cert_dir / "ingest-cert.pem"
        key_path = self.cert_dir / "ingest-key.pem"
        if not cert_path.is_file() or not key_path.is_file():
            _generate_self_signed(cert_path, key_path)
        else:
            # Keep private key locked down.
            try:
                import os

                os.chmod(key_path, 0o600)
            except OSError:
                pass
        der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(
            serialization.Encoding.DER
        )
        self._fingerprint = hashlib.sha256(der).hexdigest()
        self._cert_path = cert_path
        self._key_path = key_path

    def _mint_pairing_locked(self) -> str:
        token = secrets.token_urlsafe(24)
        self._pairing[token] = PairingToken(
            token=token,
            expires_at=self._time() + self.pairing_ttl,
        )
        return token

    def _peek_pairing(self) -> str | None:
        now = self._time()
        with self._lock:
            for token, entry in self._pairing.items():
                if not entry.used and now < entry.expires_at:
                    return token
            if self._bind.enabled:
                return self._mint_pairing_locked()
        return None

    def _allow_rate_locked(self, session: str, now: float) -> bool:
        window = self._rate[session]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.rate_limit_per_minute:
            return False
        window.append(now)
        return True


def _serve_forever(server: Any) -> None:
    try:
        server.serve_forever(poll_interval=0.5)
    except Exception:
        pass


def _stop_server(server: Any, thread: threading.Thread) -> None:
    for action in (server.shutdown, server.server_close):
        try:
            action()
        except Exception:
            pass
    if thread.is_alive():
        thread.join(timeout=2.0)


def _generate_self_signed(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Viewlift AI SDK Ingest"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Viewlift AI Local"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365 * 5))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key_bytes)
    import os

    os.chmod(key_path, 0o600)


def _client_identifier(value: object) -> str | None:
    text = str(value or "").strip()[:128]
    if text and all(char.isalnum() or char in "._-" for char in text):
        return text
    return None


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _decode_body(value: Any, max_bytes: int) -> bytes:
    import base64

    if value is None:
        return b""
    if isinstance(value, bytes):
        return value[:max_bytes]
    if isinstance(value, str):
        # Prefer base64; fall back to utf-8 text.
        try:
            raw = base64.b64decode(value, validate=False)
        except Exception:
            raw = value.encode("utf-8")
        return raw[:max_bytes]
    return b""


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_url(
    url: str,
    host_hint: Any,
    path_hint: Any,
) -> tuple[str, str, str | None]:
    from urllib.parse import urlparse

    if url:
        parsed = urlparse(url)
        host = parsed.hostname or str(host_hint or "unknown")
        path = parsed.path or "/"
        query = parsed.query or None
        return host, path, query
    host = str(host_hint or "unknown")
    path = str(path_hint or "/")
    return host, path, None
