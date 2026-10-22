"""Secure SDK ingest receiver: pairing, session tokens, rate limits, redaction."""

from __future__ import annotations

import hashlib
import secrets
import ssl
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

from viewlift_ai.models.flow import FlowSource, FlowTiming
from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.services.bind_policy import (
    InterfaceLister,
    SetupValidationError,
    is_loopback_ip,
    preferred_lan_host,
    validate_port,
    validate_receiver_bind_ip,
)
from viewlift_ai.services.capture import CaptureService, HttpCaptureSnapshot
from viewlift_ai.services.debug_advertise import DebugAdvertiser, DebugPublishing
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.setup import list_ipv4_interfaces

TimeFn = Callable[[], float]

DEFAULT_INGEST_PORT = 8742
PAIRING_TTL_SECONDS = 300.0
SESSION_TTL_SECONDS = 3600.0
DEFAULT_MAX_BODY_BYTES = 262_144
DEFAULT_RATE_LIMIT = 600  # requests per minute per session
DEFAULT_MAX_JSON_BYTES = 1_048_576


@dataclass(slots=True)
class PairingToken:
    token: str
    expires_at: float
    used: bool = False


@dataclass(slots=True)
class SessionToken:
    token: str
    expires_at: float


@dataclass(slots=True)
class IngestActivity:
    """Counters for the Setup/Network UI so a silent SDK is diagnosable."""

    last_client: str | None = None
    last_seen_at: str | None = None
    last_path: str | None = None
    last_error: str | None = None
    pairings: int = 0
    flows: int = 0
    logs: int = 0
    rejected: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "last_client": self.last_client,
            "last_seen_at": self.last_seen_at,
            "last_path": self.last_path,
            "last_error": self.last_error,
            "pairings": self.pairings,
            "flows": self.flows,
            "logs": self.logs,
            "rejected": self.rejected,
        }


@dataclass(slots=True)
class IngestBindState:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = DEFAULT_INGEST_PORT
    iface: str = ""


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
            "pairing_token": pairing,
            "pinned_fingerprint": self._fingerprint,
            "debug_key": self.debug_key() if self._bind.enabled else None,
            "note": (
                "No proxy or CA install. The app reads the address, port, certificate "
                "fingerprint, and debug key from this dashboard. Pairing token is "
                "one-use and short-lived; exchange it for a session token."
                if self._bind.enabled
                else (
                    "Receiver is off. Pick localhost, Wi-Fi, or a VPN interface "
                    "(NetBird or Tailscale) and a port in Setup."
                )
            ),
            "max_body_bytes": self.max_body_bytes,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "server_running": self._server is not None,
            "activity": self.activity(),
        }

    def activity(self) -> dict[str, object]:
        with self._lock:
            return self._activity.as_dict()

    def record_request(self, client: str, path: str, *, error: str | None = None) -> None:
        with self._lock:
            act = self._activity
            act.last_client = client
            act.last_seen_at = datetime.now(UTC).isoformat()
            act.last_path = path
            if error is not None:
                act.last_error = f"{path}: {error}"
                act.rejected += 1
                return
            if path == "/v1/pair":
                act.pairings += 1
            elif path == "/v1/flows":
                act.flows += 1
            elif path == "/v1/logs":
                act.logs += 1

    def receiver_url(self) -> str | None:
        if not self._bind.enabled:
            return None
        return f"https://{self._bind.host}:{self._bind.port}"

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
        if current is not None and str(current.get("ip") or "") != self._bind.host:
            self.enable(
                host=str(current.get("ip") or ""),
                port=self._bind.port,
                iface=self._bind.iface,
            )
        self._publish_receiver()
        return self._bind.host

    def start_network_watch(self) -> None:
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
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
                self.follow_network()
            except (SetupValidationError, OSError):
                pass
            self._watch_stop.wait(2)

    def enable(
        self,
        *,
        host: str,
        port: int = DEFAULT_INGEST_PORT,
        iface: str | None = None,
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
        validated_port = validate_port(port)
        self._ensure_tls_material()
        with self._lock:
            requires_rebind = self._server is not None and (
                self._bind.host != ip or self._bind.port != validated_port
            )
        if requires_rebind:
            self.stop_https_server()
        with self._lock:
            self._bind = IngestBindState(
                enabled=True,
                host=ip,
                port=validated_port,
                iface=chosen_iface,
            )
            self._mint_pairing_locked()
        self.start_https_server()
        self._publish_receiver()
        return self.status()

    def disable(self) -> dict[str, object]:
        self._manual_disable = True
        self.advertiser.stop()
        self.stop_https_server()
        with self._lock:
            self._bind = IngestBindState(enabled=False, host="127.0.0.1", port=self._bind.port)
            self._pairing.clear()
            self._sessions.clear()
            self._rate.clear()
        return self.status()

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

    def exchange_pairing(self, pairing_token: str) -> dict[str, object]:
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

    def ingest_flow(self, payload: dict[str, Any]) -> dict[str, object]:
        flow_id = str(payload.get("id") or uuid.uuid4())
        method = str(payload.get("method") or "GET").upper()
        url = str(payload.get("url") or "")
        host, path, query = _split_url(url, payload.get("host"), payload.get("path"))
        req_headers = _as_str_map(payload.get("request_headers"))
        resp_headers = _as_str_map(payload.get("response_headers"))
        req_body = _decode_body(payload.get("request_body"), self.max_body_bytes)
        resp_body = _decode_body(payload.get("response_body"), self.max_body_bytes)
        # Extra redaction keys from SDK config
        extra_keys = payload.get("extra_redaction_keys") or []
        redaction = self._redaction_with_extras(extra_keys)
        snapshot = HttpCaptureSnapshot(
            id=flow_id,
            method=method,
            host=host,
            path=path,
            status=_as_optional_int(payload.get("status")),
            duration_ms=_as_optional_float(payload.get("duration_ms")),
            protocol=str(payload.get("protocol") or "HTTPS"),
            request_headers=redaction.redact_headers(req_headers),
            request_body=redaction.redact_body(req_body),
            response_headers=redaction.redact_headers(resp_headers),
            response_body=redaction.redact_body(resp_body),
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
        )
        flow = self.capture.persist_http(snapshot)
        return {"ok": True, "id": flow.id, "source": FlowSource.SDK.value}

    def ingest_log(self, payload: dict[str, Any]) -> dict[str, object]:
        message = str(payload.get("message") or "")
        if len(message.encode("utf-8")) > self.max_json_bytes:
            msg = "log message exceeds size cap"
            raise ValueError(msg)
        record = self.log_capture.persist(
            level=str(payload.get("level") or "info"),
            subsystem=str(payload.get("subsystem") or "sdk"),
            message=message,
            source="sdk",
            request_id=str(payload["request_id"]) if payload.get("request_id") else None,
            log_id=str(payload["id"]) if payload.get("id") else None,
        )
        return {"ok": True, "id": record.id, "source": "sdk"}

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

            def _no_store(self, code: int, payload: dict[str, object]) -> None:
                path = self.path.split("?", 1)[0]
                if path != "/health":
                    error = None if code < 400 else f"{code} {payload.get('detail') or ''}".strip()
                    service.record_request(self.client_address[0], path, error=error)
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
                        result = service.exchange_pairing(token)
                        self._no_store(200, result)
                        return
                    if path in {"/v1/flows", "/v1/logs"}:
                        service.require_session(self.headers.get("Authorization"))
                        body = self._read_json()
                        if path == "/v1/flows":
                            self._no_store(200, service.ingest_flow(body))
                        else:
                            self._no_store(200, service.ingest_log(body))
                        return
                    if path == "/v1/debug/register":
                        service.require_session(self.headers.get("Authorization"))
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        self._no_store(200, debug.register(self._read_json()))
                        return
                    if path == "/v1/debug/events":
                        service.require_session(self.headers.get("Authorization"))
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        client_id = self.headers.get("X-Viewlift-Client-ID") or ""
                        self._no_store(200, debug.ingest_event(client_id, self._read_json()))
                        return
                    self._no_store(404, {"ok": False, "detail": "not found"})
                except LookupError as exc:
                    self._no_store(404, {"ok": False, "detail": str(exc)})
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
                        service.require_session(self.headers.get("Authorization"))
                        debug = service.debug_sessions
                        if debug is None:
                            raise RuntimeError("debug session service unavailable")
                        client_id = self.headers.get("X-Viewlift-Client-ID") or ""
                        self._no_store(200, {"commands": debug.poll(client_id)})
                    except LookupError as exc:
                        self._no_store(404, {"ok": False, "detail": str(exc)})
                    except PermissionError as exc:
                        self._no_store(401, {"ok": False, "detail": str(exc)})
                    except Exception as exc:
                        self._no_store(500, {"ok": False, "detail": str(exc)})
                    return
                self._no_store(404, {"ok": False, "detail": "not found"})

        server = ThreadingHTTPServer((self._bind.host, self._bind.port), Handler)
        server.socket = self.ssl_context().wrap_socket(server.socket, server_side=True)
        self._server = server

        def _serve() -> None:
            try:
                server.serve_forever(poll_interval=0.5)
            except Exception:
                pass

        self._server_thread = threading.Thread(
            target=_serve,
            name="sdk-ingest-https",
            daemon=True,
        )
        self._server_thread.start()

    def stop_https_server(self) -> None:
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

    def _redaction_with_extras(self, extra_keys: Any) -> RedactionService:
        if not extra_keys:
            return self._redaction
        keys = {str(k).lower() for k in extra_keys if str(k).strip()}
        if not keys:
            return self._redaction
        base_headers = set(self._redaction._headers)  # noqa: SLF001
        base_json = set(self._redaction._json_keys)  # noqa: SLF001
        return RedactionService(
            sensitive_headers=frozenset(base_headers | keys),
            sensitive_json_keys=frozenset(base_json | keys),
        )


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
