"""Device/simulator setup: CA tokens, mobileconfig, LAN bind policy, simctl helpers."""

from __future__ import annotations

import base64
import ipaddress
import plistlib
import re
import secrets
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from viewlift_ai.proxy import ca as ca_mod
from viewlift_ai.services.bind_policy import (
    InterfaceLister,
    SetupValidationError,
    interface_scope,
    is_loopback_ip,
    validate_ip,
    validate_lan_bind_ip,
    validate_port,
)

CommandRunner = Callable[..., Any]
TimeFn = Callable[[], float]

TOKEN_TTL_SECONDS = 60.0
PRIVATE_MARKERS = (b"PRIVATE KEY", b"BEGIN RSA PRIVATE", b"BEGIN EC PRIVATE")

__all__ = [
    "SetupValidationError",
    "SetupService",
    "validate_ip",
    "validate_port",
    "list_ipv4_interfaces",
    "build_mobileconfig",
    "mobileconfig_data_blobs",
]


@dataclass(slots=True)
class DownloadToken:
    token: str
    kind: str
    expires_at: float
    used: bool = False
    payload: bytes = b""
    media_type: str = "application/octet-stream"
    filename: str = "download.bin"


@dataclass(slots=True)
class ProxyAuthCredentials:
    username: str
    password: str


@dataclass(slots=True)
class ProxyBindState:
    host: str = "127.0.0.1"
    port: int = 8080
    lan_enabled: bool = False
    auth: ProxyAuthCredentials | None = None


@dataclass(slots=True)
class TestRequestResult:
    ok: bool
    status_code: int | None
    detail: str
    via_proxy: str


@dataclass(slots=True)
class CommandResult:
    ok: bool
    detail: str
    argv: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


class SetupService:
    """In-memory setup state. Private CA key never enters payloads or tokens."""

    def __init__(
        self,
        *,
        confdir: Path,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 8080,
        token_ttl_seconds: float = TOKEN_TTL_SECONDS,
        time_fn: TimeFn | None = None,
        runner: CommandRunner | None = None,
        keychain_fallback_active: bool = False,
        interface_lister: InterfaceLister | None = None,
    ) -> None:
        self._confdir = Path(confdir)
        self._token_ttl = token_ttl_seconds
        self._time = time_fn or time.time
        self._runner: CommandRunner = runner or subprocess.run
        self._keychain_fallback_active = keychain_fallback_active
        self._interface_lister: InterfaceLister = interface_lister or (
            lambda: list_ipv4_interfaces(runner=self._runner)
        )
        self._tokens: dict[str, DownloadToken] = {}
        self._bind = ProxyBindState(host=proxy_host, port=proxy_port)
        self._last_test: TestRequestResult | None = None
        self._sdk_status_provider: Callable[[], dict[str, object]] | None = None

    def bind_sdk_status(self, provider: Callable[[], dict[str, object]]) -> None:
        """Optional hook so Setup status can surface live ingest receiver state."""
        self._sdk_status_provider = provider

    @property
    def confdir(self) -> Path:
        return self._confdir

    @property
    def bind(self) -> ProxyBindState:
        return self._bind

    def ensure_ca(self) -> dict[str, object]:
        paths = ca_mod.ensure_ca(self._confdir)
        mode = ca_mod.private_key_mode(self._confdir)
        return {
            "ready": True,
            "public_cert": str(paths.public_pem),
            "private_key_mode": oct(mode) if mode is not None else None,
        }

    def status(self) -> dict[str, object]:
        ready = ca_mod.ca_ready(self._confdir)
        mode = ca_mod.private_key_mode(self._confdir) if ready else None
        auth = self._bind.auth
        return {
            "ca_ready": ready,
            "private_key_mode": oct(mode) if mode is not None else None,
            "keychain_fallback_active": self._keychain_fallback_active,
            "proxy": {
                "host": self._bind.host,
                "port": self._bind.port,
                "lan_enabled": self._bind.lan_enabled,
                "auth_required": not is_loopback_ip(self._bind.host),
                "auth": (
                    {"username": auth.username, "password": auth.password}
                    if auth is not None
                    else None
                ),
            },
            "interfaces": self._interface_lister(),
            "sdk_receiver": (
                self._sdk_status_provider()
                if self._sdk_status_provider is not None
                else {
                    "enabled": False,
                    "note": "SDK receiver not wired.",
                    "receiver_url": None,
                    "pairing_token": None,
                    "pinned_fingerprint": None,
                }
            ),
            "last_test": (
                {
                    "ok": self._last_test.ok,
                    "status_code": self._last_test.status_code,
                    "detail": self._last_test.detail,
                    "via_proxy": self._last_test.via_proxy,
                }
                if self._last_test is not None
                else None
            ),
        }

    def mint_ca_download_token(self) -> dict[str, object]:
        pem = ca_mod.public_ca_pem(self._confdir)
        return self._mint_token(
            kind="ca_pem",
            payload=pem,
            media_type="application/x-pem-file",
            filename="viewlift-ca-cert.pem",
        )

    def mint_mobileconfig_token(self, *, proxy_host: str, proxy_port: int) -> dict[str, object]:
        host = validate_ip(proxy_host)
        port = validate_port(proxy_port)
        payload = build_mobileconfig(
            confdir=self._confdir,
            proxy_host=host,
            proxy_port=port,
        )
        for marker in PRIVATE_MARKERS:
            if marker in payload:
                msg = "mobileconfig must not contain private key material"
                raise RuntimeError(msg)
        return self._mint_token(
            kind="mobileconfig",
            payload=payload,
            media_type="application/x-apple-aspen-config",
            filename="viewlift-proxy.mobileconfig",
        )

    def consume_download(self, token: str) -> DownloadToken:
        entry = self._tokens.get(token)
        if entry is None:
            msg = "download token not found"
            raise LookupError(msg)
        now = self._time()
        if entry.used:
            msg = "download token already used"
            raise PermissionError(msg)
        if now >= entry.expires_at:
            msg = "download token expired"
            raise TimeoutError(msg)
        entry.used = True
        return entry

    def configure_proxy_bind(
        self,
        *,
        host: str,
        port: int,
        enable_lan: bool,
    ) -> ProxyBindState:
        validated_port = validate_port(port)
        candidate = validate_ip(host)
        loopback = is_loopback_ip(candidate)
        if not loopback and not enable_lan:
            msg = "Non-loopback proxy bind requires explicit enable_lan=true"
            raise SetupValidationError(msg)
        if loopback:
            self._bind = ProxyBindState(
                host=candidate,
                port=validated_port,
                lan_enabled=False,
                auth=None,
            )
            return self._bind
        ip = validate_lan_bind_ip(candidate, interface_lister=self._interface_lister)
        auth = ProxyAuthCredentials(
            username=f"vl{secrets.token_hex(3)}",
            password=secrets.token_urlsafe(18),
        )
        self._bind = ProxyBindState(
            host=ip,
            port=validated_port,
            lan_enabled=True,
            auth=auth,
        )
        return self._bind

    def run_test_request(
        self,
        *,
        url: str = "http://example.com/",
        opener_factory: Callable[..., Any] | None = None,
    ) -> TestRequestResult:
        proxy_url = f"http://{self._bind.host}:{self._bind.port}"
        handlers = []
        proxy_map = {"http": proxy_url, "https": proxy_url}
        if self._bind.auth is not None:
            user = self._bind.auth.username
            password = self._bind.auth.password
            auth_proxy = f"http://{user}:{password}@{self._bind.host}:{self._bind.port}"
            proxy_map = {"http": auth_proxy, "https": auth_proxy}
        handlers.append(ProxyHandler(proxy_map))
        factory = opener_factory or (lambda: build_opener(*handlers))
        opener = factory()
        req = Request(url, method="GET", headers={"User-Agent": "ViewliftAI-SetupTest/1.0"})
        try:
            with opener.open(req, timeout=10) as resp:
                code = getattr(resp, "status", None) or resp.getcode()
                result = TestRequestResult(
                    ok=200 <= int(code) < 500,
                    status_code=int(code),
                    detail=f"Proxy test reached {url}",
                    via_proxy=f"{self._bind.host}:{self._bind.port}",
                )
        except HTTPError as exc:
            result = TestRequestResult(
                ok=True,
                status_code=int(exc.code),
                detail=f"Proxy responded with HTTP {exc.code} for {url}",
                via_proxy=f"{self._bind.host}:{self._bind.port}",
            )
        except URLError as exc:
            result = TestRequestResult(
                ok=False,
                status_code=None,
                detail=f"Proxy test failed: {exc.reason!s}",
                via_proxy=f"{self._bind.host}:{self._bind.port}",
            )
        except OSError as exc:
            result = TestRequestResult(
                ok=False,
                status_code=None,
                detail=f"Proxy test failed: {exc}",
                via_proxy=f"{self._bind.host}:{self._bind.port}",
            )
        self._last_test = result
        return result

    def install_simulator_ca(self) -> CommandResult:
        paths = ca_mod.ensure_ca(self._confdir)
        boot = self._require_booted_simulator()
        if not boot.ok:
            return boot
        argv = [
            "xcrun",
            "simctl",
            "keychain",
            "booted",
            "add-root-cert",
            str(paths.public_pem),
        ]
        return self._run_argv(
            argv,
            success="Installed public CA into booted simulator keychain.",
            failure=(
                "Failed to install CA into booted simulator. "
                "Boot an iOS/tvOS simulator, then retry. "
                "Command: xcrun simctl keychain booted add-root-cert <public-cert.pem>"
            ),
        )

    def configure_simulator_proxy(self) -> CommandResult:
        boot = self._require_booted_simulator()
        if not boot.ok:
            return boot
        host = self._bind.host
        port = str(self._bind.port)
        # Prefer documented simctl status_bar/network hooks when absent: try launchctl env.
        # URLSession often ignores these; return exact next steps on failure.
        http_argv = [
            "xcrun",
            "simctl",
            "spawn",
            "booted",
            "launchctl",
            "setenv",
            "http_proxy",
            f"{host}:{port}",
        ]
        http_result = self._run_argv(
            http_argv,
            success="Set http_proxy in booted simulator.",
            failure=(
                "Could not set http_proxy via simctl spawn launchctl. "
                f"Boot a simulator and set Mac/system proxy to {host}:{port}, "
                "or configure the app under test to use that proxy explicitly."
            ),
        )
        if not http_result.ok:
            return http_result
        https_argv = [
            "xcrun",
            "simctl",
            "spawn",
            "booted",
            "launchctl",
            "setenv",
            "https_proxy",
            f"{host}:{port}",
        ]
        return self._run_argv(
            https_argv,
            success=(
                f"Configured simulator proxy env to {host}:{port}. "
                "Note: URLSession may ignore launchctl env; if traffic is missing, "
                f"set the Mac system HTTP proxy to {host}:{port} or use a device profile."
            ),
            failure=(
                "http_proxy was set but https_proxy failed. "
                f"Set Mac system proxy to {host}:{port} manually, or use a physical device "
                "with the .mobileconfig profile."
            ),
        )

    def _require_booted_simulator(self) -> CommandResult:
        argv = ["xcrun", "simctl", "list", "devices", "booted"]
        try:
            completed = self._runner(
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return CommandResult(
                ok=False,
                detail=(
                    "xcrun not found. Install Xcode command-line tools, then retry "
                    "simulator CA/proxy setup."
                ),
                argv=argv,
            )
        except OSError as exc:
            return CommandResult(
                ok=False,
                detail=f"Failed to query booted simulators: {exc}",
                argv=argv,
            )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            return CommandResult(
                ok=False,
                detail=(
                    "simctl list failed. Install Xcode, accept the license, then boot a "
                    f"simulator. stderr: {stderr.strip() or '(empty)'}"
                ),
                argv=argv,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        if "(Booted)" not in stdout and "Booted" not in stdout:
            return CommandResult(
                ok=False,
                detail=(
                    "No booted simulator. Open Simulator.app, boot an iOS/tvOS device, "
                    "then retry."
                ),
                argv=argv,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return CommandResult(ok=True, detail="Booted simulator found.", argv=argv, stdout=stdout)

    def _run_argv(self, argv: list[str], *, success: str, failure: str) -> CommandResult:
        try:
            completed = self._runner(
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return CommandResult(
                ok=False,
                detail=f"{failure} Missing executable: {argv[0]}",
                argv=argv,
            )
        except OSError as exc:
            return CommandResult(
                ok=False,
                detail=f"{failure} OS error: {exc}",
                argv=argv,
            )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            err = stderr.strip() or stdout.strip() or f"exit {completed.returncode}"
            return CommandResult(
                ok=False,
                detail=f"{failure} ({err})",
                argv=argv,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return CommandResult(
            ok=True,
            detail=success,
            argv=argv,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _mint_token(
        self,
        *,
        kind: str,
        payload: bytes,
        media_type: str,
        filename: str,
    ) -> dict[str, object]:
        for marker in PRIVATE_MARKERS:
            if marker in payload:
                msg = "refusing to mint download that contains private key material"
                raise RuntimeError(msg)
        token = secrets.token_urlsafe(32)
        expires_at = self._time() + self._token_ttl
        self._tokens[token] = DownloadToken(
            token=token,
            kind=kind,
            expires_at=expires_at,
            payload=payload,
            media_type=media_type,
            filename=filename,
        )
        return {
            "token": token,
            "expires_in": int(self._token_ttl),
            "kind": kind,
            "filename": filename,
        }


def list_ipv4_interfaces(*, runner: CommandRunner | None = None) -> list[dict[str, str]]:
    """List IPv4 addresses from `ifconfig -a` (argv, no shell)."""
    run = runner or subprocess.run
    argv = ["ifconfig", "-a"]
    try:
        completed = run(argv, check=False, capture_output=True, text=True)
    except OSError:
        return [{"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"}]
    if completed.returncode != 0:
        return [{"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"}]
    results: list[dict[str, str]] = []
    current: str | None = None
    for line in (completed.stdout or "").splitlines():
        iface_match = re.match(r"^([a-zA-Z0-9._-]+):", line)
        if iface_match:
            current = iface_match.group(1)
            continue
        inet_match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", line)
        if not inet_match or current is None:
            continue
        ip = inet_match.group(1)
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.version != 4:
            continue
        scope = interface_scope(current, ip)
        results.append({"iface": current, "ip": ip, "scope": scope})
    if not any(row["ip"] == "127.0.0.1" for row in results):
        results.insert(0, {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"})
    return results


def build_mobileconfig(*, confdir: Path, proxy_host: str, proxy_port: int) -> bytes:
    """Unsigned CA-only profile. Global HTTP proxy requires a supervised iPhone."""
    host = validate_ip(proxy_host)
    port = validate_port(proxy_port)
    der = ca_mod.public_ca_der(confdir)
    profile_uuid = str(uuid.uuid4()).upper()
    ca_uuid = str(uuid.uuid4()).upper()
    proxy_hint = (
        f"After install, set Wi-Fi proxy manually to {host}:{port}. "
        "Do not use 127.0.0.1 on the phone; that address is the phone itself."
        if host != "127.0.0.1"
        else (
            "After install, set Wi-Fi proxy manually to this Mac's LAN IP and port. "
            "127.0.0.1 is the phone, not the Mac."
        )
    )
    profile = {
        "PayloadContent": [
            {
                "PayloadType": "com.apple.security.root",
                "PayloadUUID": ca_uuid,
                "PayloadIdentifier": f"ai.viewlift.debug.ca.{ca_uuid}",
                "PayloadVersion": 1,
                "PayloadDisplayName": "Viewlift AI Proxy CA",
                "PayloadDescription": "Public trust certificate for local Viewlift AI proxy.",
                "PayloadContent": der,
            },
        ],
        "PayloadDisplayName": "Viewlift AI Proxy CA",
        "PayloadIdentifier": f"ai.viewlift.debug.profile.{profile_uuid}",
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": profile_uuid,
        "PayloadVersion": 1,
        "PayloadDescription": (
            "Installs only the Viewlift AI public CA. "
            "A global HTTP proxy profile can be installed only on a supervised device, "
            f"so this profile does not include one. {proxy_hint}"
        ),
    }
    data = plistlib.dumps(profile, fmt=plistlib.FMT_XML)
    for marker in PRIVATE_MARKERS:
        if marker in data:
            msg = "mobileconfig contained private key material"
            raise RuntimeError(msg)
    return data


def mobileconfig_data_blobs(xml_bytes: bytes) -> list[bytes]:
    """Extract <data> blobs from a mobileconfig XML plist (for tests)."""
    text = xml_bytes.decode("utf-8", errors="ignore")
    blobs: list[bytes] = []
    for chunk in re.findall(r"<data>\s*([^<]+)\s*</data>", text):
        cleaned = re.sub(r"\s+", "", chunk)
        try:
            blobs.append(base64.b64decode(cleaned))
        except Exception:
            continue
    return blobs
