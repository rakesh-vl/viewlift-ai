"""Allowlist API. The Rules page is gone; Setup edits the list."""

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


def test_allowlist_defaults_and_rules_page_gone(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        res = client.get("/api/rules")
        assert res.status_code == 200
        assert res.json() == {"allowlist": []}
        assert client.get("/rules").status_code == 404
        page = client.get("/setup")
        assert page.status_code == 200
        assert b"Allowlist" in page.content
        assert b'href="/rules"' not in page.content


def test_allowlist_replace(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.put("/api/rules/allowlist", json={"patterns": "nope"})
        assert bad.status_code == 400
        ok = client.put("/api/rules/allowlist", json={"patterns": ["*.example.com", ""]})
        assert ok.status_code == 200
        assert ok.json()["allowlist"] == ["*.example.com"]
        assert client.get("/api/rules").json()["allowlist"] == ["*.example.com"]


def test_health_reports_allowlist(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put("/api/rules/allowlist", json={"patterns": ["api.example.com"]})
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["allowlist"] == ["api.example.com"]
        assert "mutating_rules_enabled" not in body
