"""Localhost debug toolkit: profiles, deeplink, replay, location guard."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from viewlift_ai.services.debug_toolkit import (
    DEBUG_MODULES,
    NETWORK_PROFILES,
    list_network_profiles,
    open_deeplink,
    replay_http,
    reveal_simulator_asset,
    set_device_location,
    validate_deeplink,
)


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


def test_network_profile_catalog() -> None:
    rows = list_network_profiles()
    ids = [row["id"] for row in rows]
    assert ids == list(NETWORK_PROFILES)
    offline = next(row for row in rows if row["id"] == "offline")
    assert offline["offline"] is True
    three_g = next(row for row in rows if row["id"] == "3g")
    assert three_g["latency_ms"] == 200


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


def test_replay_decodes_gzip_before_the_size_cap() -> None:
    payload = gzip.compress(b'{"ok":true}')

    def opener(method, url, headers, body):
        assert method == "GET"
        return 200, {"Content-Encoding": "gzip"}, payload

    sent = replay_http(
        method="GET",
        url="https://example.com/v3/monetization",
        headers={"Accept-Encoding": "gzip"},
        body=b"",
        opener=opener,
    )
    assert sent["body"] == b'{"ok":true}'
    assert sent["headers"]["Content-Encoding"] == "gzip"


def test_location_refuses_while_log_stream_is_running() -> None:
    def boom(*args):
        raise AssertionError("device connection should not open")

    with pytest.raises(RuntimeError, match="log stream"):
        set_device_location("00008120-001E558E21D1A01E", 1.0, 2.0, streaming=True, runner=boom)


def test_location_rejects_bad_coordinates() -> None:
    with pytest.raises(ValueError, match="Latitude"):
        set_device_location("00008120-001E558E21D1A01E", 120.0, 0.0, runner=lambda *a: None)


def _finder_runner(seen: list[list[str]]):
    def runner(argv, **kwargs):
        seen.append(list(argv))

        class Done:
            returncode = 0
            stderr = ""

        return Done()

    return runner


def test_reveal_asset_opens_simulator_file(tmp_path: Path) -> None:
    root = tmp_path / "CoreSimulator"
    asset = root / "Devices" / "A" / "icon.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png")
    seen: list[list[str]] = []
    result = reveal_simulator_asset(str(asset), root=root, runner=_finder_runner(seen))
    assert result["ok"] is True
    assert seen == [["open", "-R", str(asset.resolve())]]


def test_reveal_asset_refuses_paths_outside_the_simulator(tmp_path: Path) -> None:
    root = tmp_path / "CoreSimulator"
    root.mkdir()
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"nope")
    seen: list[list[str]] = []

    def runner(argv, **kwargs):
        seen.append(list(argv))
        raise AssertionError("Finder should not open")

    with pytest.raises(ValueError, match="simulator"):
        reveal_simulator_asset(str(secret), root=root, runner=runner)
    link = root / "link.png"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="simulator"):
        reveal_simulator_asset(str(link), root=root, runner=runner)
    with pytest.raises(ValueError, match="absolute"):
        reveal_simulator_asset("icon.png", root=root, runner=runner)
    assert seen == []


def test_reveal_asset_refuses_other_file_types(tmp_path: Path) -> None:
    root = tmp_path / "CoreSimulator"
    notes = root / "notes.txt"
    notes.parent.mkdir()
    notes.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="file type"):
        reveal_simulator_asset(str(notes), root=root, runner=lambda *args, **kwargs: None)
