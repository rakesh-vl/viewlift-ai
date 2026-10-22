"""Application paths, retention, and per-install secret key loading."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.bind_policy import (
    SetupValidationError,
    validate_loopback_or_refuse_env,
)

APP_NAME = "ViewliftAI"
KEYCHAIN_SERVICE = "ViewliftAI"
KEYCHAIN_ACCOUNT = "secret-box-key"
SECRET_KEY_BYTES = 32

CommandRunner = Callable[..., Any]


def default_data_dir() -> Path:
    """~/Library/Application Support/ViewliftAI on macOS."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


@dataclass(slots=True, frozen=True)
class SecretKeyState:
    """Surfaces whether the key came from Keychain or a mode-0600 file."""

    key: bytes
    used_keychain_fallback: bool
    fallback_path: Path | None


@dataclass(slots=True)
class AppConfig:
    data_dir: Path
    database_path: Path
    secret_key_path: Path
    retention: RetentionPolicy
    secret: SecretKeyState
    host: str = "127.0.0.1"
    port: int = 8741
    proxy_enabled: bool = True
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8080
    allowlist_hosts: tuple[str, ...] = ()

    @property
    def keychain_fallback_active(self) -> bool:
        return self.secret.used_keychain_fallback

    @property
    def mitm_confdir(self) -> Path:
        return self.data_dir / "mitmproxy"


class KeychainSecretStore:
    """Load or create a 32-byte key via macOS `security`, with file fallback."""

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
        fallback_path: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self._service = service
        self._account = account
        self._fallback_path = fallback_path
        self._runner: CommandRunner = runner or subprocess.run

    def resolve(self) -> SecretKeyState:
        """Prefer Keychain, then existing fallback file, else create."""
        keychain_key = self.find_in_keychain()
        if keychain_key is not None:
            return SecretKeyState(
                key=keychain_key,
                used_keychain_fallback=False,
                fallback_path=None,
            )

        existing = self.load_fallback_if_present()
        if existing is not None:
            return SecretKeyState(
                key=existing,
                used_keychain_fallback=True,
                fallback_path=self._fallback_path,
            )

        key = secrets.token_bytes(SECRET_KEY_BYTES)
        if self.add_to_keychain(key):
            return SecretKeyState(key=key, used_keychain_fallback=False, fallback_path=None)

        self.write_fallback_file(key)
        return SecretKeyState(
            key=key,
            used_keychain_fallback=True,
            fallback_path=self._fallback_path,
        )

    def find_in_keychain(self) -> bytes | None:
        try:
            completed = self._runner(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self._service,
                    "-a",
                    self._account,
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        raw = (completed.stdout or "").strip()
        if not raw:
            return None
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            return None
        if len(key) != SECRET_KEY_BYTES:
            return None
        return key

    def add_to_keychain(self, key: bytes) -> bool:
        try:
            completed = self._runner(
                [
                    "security",
                    "add-generic-password",
                    "-s",
                    self._service,
                    "-a",
                    self._account,
                    "-w",
                    key.hex(),
                    "-U",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return completed.returncode == 0

    def write_fallback_file(self, key: bytes) -> None:
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_path.write_bytes(key)
        os.chmod(self._fallback_path, 0o600)

    def load_fallback_if_present(self) -> bytes | None:
        if not self._fallback_path.is_file():
            return None
        data = self._fallback_path.read_bytes()
        if len(data) != SECRET_KEY_BYTES:
            return None
        return data


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_host_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def load_config(
    *,
    data_dir: Path | None = None,
    retention: RetentionPolicy | None = None,
    secret_store: KeychainSecretStore | None = None,
    proxy_enabled: bool | None = None,
    proxy_host: str | None = None,
    proxy_port: int | None = None,
    allowlist_hosts: tuple[str, ...] | None = None,
) -> AppConfig:
    root = data_dir or default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    secret_path = root / "secret.key"
    store = secret_store or KeychainSecretStore(fallback_path=secret_path)
    secret = store.resolve()
    policy = retention if retention is not None else _env_retention()
    resolved_proxy_host = proxy_host or os.environ.get("VIEWLIFT_PROXY_HOST", "127.0.0.1")
    # Fail-closed: env/startup non-loopback requires Setup LAN flow (minted auth).
    resolved_proxy_host = validate_loopback_or_refuse_env(resolved_proxy_host)
    return AppConfig(
        data_dir=root,
        database_path=root / "viewlift.db",
        secret_key_path=secret_path,
        retention=policy,
        secret=secret,
        proxy_enabled=(
            proxy_enabled
            if proxy_enabled is not None
            else _env_bool("VIEWLIFT_PROXY_ENABLED", True)
        ),
        proxy_host=resolved_proxy_host,
        proxy_port=proxy_port if proxy_port is not None else _env_int("VIEWLIFT_PROXY_PORT", 8080),
        allowlist_hosts=(
            allowlist_hosts if allowlist_hosts is not None else _env_host_list("VIEWLIFT_ALLOWLIST")
        ),
    )


def _env_retention() -> RetentionPolicy:
    raw = (os.environ.get("VIEWLIFT_RETENTION") or "session").strip().lower()
    if raw in {"session", "sess"}:
        return RetentionPolicy.SESSION
    if raw in {"24h", "24", "hours_24", "day"}:
        return RetentionPolicy.HOURS_24
    msg = "VIEWLIFT_RETENTION must be 'session' or '24h'"
    raise SetupValidationError(msg)
