"""Rules REST API validation, toggles, banner flag, Rules page."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy


def _client(tmp_path: Path) -> TestClient:
    cfg = AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"t" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )
    return TestClient(create_app(cfg))


def test_rules_defaults_and_page(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        res = client.get("/api/rules")
        assert res.status_code == 200
        body = res.json()
        assert body["allowlist"] == []
        assert body["map_local"] == []
        assert body["breakpoints"] == []
        assert body["mutating_enabled"] is False

        page = client.get("/rules")
        assert page.status_code == 200
        assert b"Map local" in page.content
        assert b"Breakpoints" in page.content
        assert b"mutating-banner" in page.content
        # Banner not shown by default
        assert b'mutating-banner show' not in page.content.replace(b"\n", b" ")


def test_allowlist_and_map_remote_validation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.put("/api/rules/allowlist", json={"patterns": "nope"})
        assert bad.status_code == 400

        ok = client.put("/api/rules/allowlist", json={"patterns": ["*.example.com"]})
        assert ok.status_code == 200
        assert ok.json()["allowlist"] == ["*.example.com"]

        rejected = client.post(
            "/api/rules/map-remote",
            json={
                "match": {"method": "*", "host": "a.com", "path": "*"},
                "target_url": "ftp://evil/",
            },
        )
        assert rejected.status_code == 400

        created = client.post(
            "/api/rules/map-remote",
            json={
                "match": {"method": "GET", "host": "a.com", "path": "/x"},
                "target_url": "https://b.com/y",
            },
        )
        assert created.status_code == 200
        assert created.json()["enabled"] is False
        rule_id = created.json()["id"]

        toggled = client.post(
            f"/api/rules/map-remote/{rule_id}/toggle",
            params={"enabled": True},
        )
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is True

        status = client.get("/api/rules/status")
        assert status.json()["mutating_enabled"] is True

        page = client.get("/rules")
        assert b'class="mutating-banner show"' in page.content


def test_map_local_relative_validation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.post(
            "/api/rules/map-local",
            json={
                "match": {"method": "*", "host": "*", "path": "*"},
                "relative_path": "../secret",
            },
        )
        assert bad.status_code == 400

        root = client.put(
            "/api/rules/map-local/root",
            json={"map_local_root": str(tmp_path / "fixtures")},
        )
        assert root.status_code == 200


def test_rewrite_replacement_cap_api(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        huge = "x" * (256 * 1024 + 1)
        res = client.post(
            "/api/rules/rewrite",
            json={
                "match": {"method": "*", "host": "*", "path": "*"},
                "actions": [
                    {
                        "kind": "replace_body_literal",
                        "find": "a",
                        "replace": huge,
                    }
                ],
            },
        )
        assert res.status_code == 400


def test_throttle_crud(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/rules/throttle",
            json={
                "host": "*.cdn.com",
                "latency_ms": 50,
                "upload_kbps": 128,
                "download_kbps": 256,
                "offline": False,
            },
        )
        assert created.status_code == 200
        assert created.json()["enabled"] is False
        rule_id = created.json()["id"]

        patched = client.patch(
            f"/api/rules/throttle/{rule_id}",
            json={"latency_ms": 100, "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["latency_ms"] == 100

        deleted = client.delete(f"/api/rules/throttle/{rule_id}")
        assert deleted.status_code == 200
        listing = client.get("/api/rules")
        assert listing.json()["throttle"] == []


def test_health_includes_mutating_flag(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["mutating_rules_enabled"] is False
