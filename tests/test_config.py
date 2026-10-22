"""Config, Keychain, and fallback-key behavior."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from viewlift_ai.config import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    SECRET_KEY_BYTES,
    KeychainSecretStore,
    default_data_dir,
    load_config,
)


def test_default_data_dir_points_at_application_support() -> None:
    path = default_data_dir()
    assert path.name == "ViewliftAI"
    assert "Application Support" in path.parts


def test_keychain_hit_skips_fallback(tmp_path: Path) -> None:
    key = b"\x11" * SECRET_KEY_BYTES
    calls: list[list[str]] = []

    def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(list(args))
        if args[1] == "find-generic-password":
            return SimpleNamespace(returncode=0, stdout=key.hex() + "\n", stderr="")
        raise AssertionError("add-generic-password should not run")

    store = KeychainSecretStore(fallback_path=tmp_path / "secret.key", runner=runner)
    state = store.resolve()
    assert state.key == key
    assert state.used_keychain_fallback is False
    assert state.fallback_path is None
    assert not (tmp_path / "secret.key").exists()
    assert calls[0][1] == "find-generic-password"
    assert KEYCHAIN_SERVICE in calls[0]
    assert KEYCHAIN_ACCOUNT in calls[0]


def test_keychain_miss_writes_mode_0600_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "secret.key"

    def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="err")

    store = KeychainSecretStore(fallback_path=fallback, runner=runner)
    state = store.resolve()
    assert state.used_keychain_fallback is True
    assert state.fallback_path == fallback
    assert fallback.is_file()
    assert len(state.key) == SECRET_KEY_BYTES
    mode = stat.S_IMODE(os.stat(fallback).st_mode)
    assert mode == 0o600


def test_load_config_surfaces_fallback_state(tmp_path: Path) -> None:
    def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    store = KeychainSecretStore(fallback_path=tmp_path / "secret.key", runner=runner)
    cfg = load_config(data_dir=tmp_path, secret_store=store)
    assert cfg.keychain_fallback_active is True
    assert cfg.database_path == tmp_path / "viewlift.db"
    assert cfg.secret.used_keychain_fallback is True


def test_retention_env_and_proxy_host_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    store = KeychainSecretStore(fallback_path=tmp_path / "secret.key", runner=runner)
    monkeypatch.setenv("VIEWLIFT_RETENTION", "24h")
    cfg = load_config(data_dir=tmp_path, secret_store=store)
    assert cfg.retention.value == "24h"

    monkeypatch.setenv("VIEWLIFT_PROXY_HOST", "192.168.1.50")
    with pytest.raises(Exception, match="not loopback"):
        load_config(data_dir=tmp_path / "other", secret_store=store)


def test_existing_fallback_reused_when_keychain_empty(tmp_path: Path) -> None:
    key = b"\x22" * SECRET_KEY_BYTES
    fallback = tmp_path / "secret.key"
    fallback.write_bytes(key)
    os.chmod(fallback, 0o600)

    def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    store = KeychainSecretStore(fallback_path=fallback, runner=runner)
    state = store.resolve()
    assert state.key == key
    assert state.used_keychain_fallback is True
