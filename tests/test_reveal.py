"""Session-memory reveal: unlock token, no export leak."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.capture import CaptureService, HttpCaptureSnapshot
from viewlift_ai.services.event_bus import FlowEventBus
from viewlift_ai.services.reveal import RevealSession


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
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


def test_reveal_unlock_and_detail(tmp_path: Path) -> None:
    with TestClient(create_app(_cfg(tmp_path))) as client:
        status = client.get("/api/reveal/status")
        assert status.status_code == 200
        token = status.json()["unlock_token"]
        assert status.json()["unlocked"] is False

        bad = client.post("/api/reveal/unlock", json={"token": "nope"})
        assert bad.status_code == 403

        ok = client.post("/api/reveal/unlock", json={"token": token})
        assert ok.status_code == 200

        reveal: RevealSession = client.app.state.reveal
        capture: CaptureService = CaptureService(
            client.app.state.store,
            FlowEventBus(),
            reveal=reveal,
        )
        capture.persist_http(
            HttpCaptureSnapshot(
                id="flow-reveal",
                method="GET",
                host="api.test",
                path="/x",
                status=200,
                duration_ms=1.0,
                protocol="HTTPS",
                request_headers={"Authorization": "Bearer secret", "Cookie": "sid=1"},
                request_body=b'{"token":"raw","password":"pw"}',
                response_headers={"Content-Type": "application/json"},
                response_body=b'{"ok":true}',
            )
        )

        redacted = client.get("/api/flows/flow-reveal")
        assert redacted.status_code == 200
        assert redacted.json()["request"]["headers"]["Authorization"] == "Bearer secret"
        assert redacted.json()["request"]["headers"]["Cookie"] == "[REDACTED]"

        revealed = client.get("/api/flows/flow-reveal?reveal=true")
        assert revealed.status_code == 200
        body = revealed.json()
        assert body["revealed"] is True
        assert body["request"]["headers"]["Authorization"] == "Bearer secret"

        curl = client.get("/api/flows/flow-reveal/curl")
        assert "Bearer secret" in curl.text
        assert "pw" not in curl.text

        har = client.get("/api/flows/flow-reveal/har")
        assert b"Bearer secret" in har.content
        assert b"pw" not in har.content
