"""Localhost debug toolkit: profiles, deeplink, replay, location guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from viewlift_ai.services.debug_toolkit import (
    DEBUG_MODULES,
    apply_network_profile,
    open_deeplink,
    replay_http,
    set_device_location,
    validate_deeplink,
)
from viewlift_ai.services.rule_engine import RuleEngine
from viewlift_ai.services.rule_store import RuleConfigStore


def _engine(tmp_path: Path) -> RuleEngine:
    return RuleEngine(RuleConfigStore(tmp_path / "rules.json"))


def test_module_catalog_covers_phantom_groups() -> None:
    ids = [item.id for item in DEBUG_MODULES]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 25
    assert any(
        item.status == "app-only" and "Not added to the app" in item.note for item in DEBUG_MODULES
    )
    assert any(item.id == "bad-network" and item.href for item in DEBUG_MODULES)
    assert "ott" in ids
    assert "spm" in ids
    assert "hang-stacks" in ids
    assert "crash-stacks" in ids


def test_network_profile_upserts_one_rule_and_clears(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    applied = apply_network_profile(engine, "3g")
    assert applied["rule"]["latency_ms"] == 200
    assert applied["rule"]["download_kbps"] == 1600
    assert applied["packet_loss"] == "not applied"
    apply_network_profile(engine, "offline")
    assert len(engine.config.throttle) == 1
    assert engine.config.throttle[0].offline is True
    cleared = apply_network_profile(engine, "off")
    assert cleared["removed"] is True
    assert engine.config.throttle == []


def test_unknown_profile_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown profile"):
        apply_network_profile(_engine(tmp_path), "dialup")


def test_deeplink_blocks_unsafe_schemes() -> None:
    with pytest.raises(ValueError, match="blocked"):
        validate_deeplink("javascript:alert(1)", "booted")
    with pytest.raises(ValueError, match="blocked"):
        validate_deeplink("file:///etc/passwd", "booted")


def test_deeplink_uses_argv(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def runner(argv, **kwargs):
        seen.append(list(argv))

        class Done:
            returncode = 0
            stderr = ""

        return Done()

    result = open_deeplink("nesn://home", udid="booted", runner=runner)
    assert result["ok"] is True
    assert seen == [["xcrun", "simctl", "openurl", "booted", "nesn://home"]]


def test_replay_rejects_non_http_and_calls_opener() -> None:
    with pytest.raises(ValueError, match="http"):
        replay_http(method="GET", url="file:///tmp/x", headers={}, body=b"")

    def opener(method, url, headers, body):
        assert method == "POST"
        assert url == "https://example.com/a"
        assert "Host" not in headers
        return 201, {"X-Test": "1"}, b'{"ok":true}'

    sent = replay_http(
        method="POST",
        url="https://example.com/a",
        headers={"Host": "example.com", "Accept": "application/json"},
        body=b"{}",
        opener=opener,
    )
    assert sent["status"] == 201
    assert sent["body"] == b'{"ok":true}'


def test_location_refuses_while_log_stream_is_running() -> None:
    def boom(*args):
        raise AssertionError("device connection should not open")

    with pytest.raises(RuntimeError, match="log stream"):
        set_device_location("00008120-001E558E21D1A01E", 1.0, 2.0, streaming=True, runner=boom)


def test_location_rejects_bad_coordinates() -> None:
    with pytest.raises(ValueError, match="Latitude"):
        set_device_location("00008120-001E558E21D1A01E", 120.0, 0.0, runner=lambda *a: None)
