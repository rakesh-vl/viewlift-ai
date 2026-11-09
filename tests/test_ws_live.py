"""Live dashboard sockets stay observable: heartbeats when idle, prompt cleanup on close."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai import defaults
from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src/viewlift_ai/web/static/js"


def _config(tmp_path: Path) -> AppConfig:
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


def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.mark.parametrize("path", ["/ws/flows", "/ws/logs", "/ws/debug"])
def test_idle_socket_gets_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(defaults, "LIVE_HEARTBEAT_SECONDS", 0.2)
    app = create_app(_config(tmp_path))
    with TestClient(app) as client, client.websocket_connect(path) as socket:
        message = socket.receive_json()
    assert message["type"] == "heartbeat"
    assert message["interval"] == 0.2


def test_closed_socket_unsubscribes_without_waiting_for_an_event(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        bus = app.state.debug_event_bus
        with client.websocket_connect("/ws/debug"):
            assert _wait(lambda: bus.subscriber_count == 1)
        assert _wait(lambda: bus.subscriber_count == 0)


def test_every_page_uses_the_shared_live_socket() -> None:
    base = (ROOT / "src/viewlift_ai/web/templates/base.html").read_text()
    assert base.index("live_socket.js") < base.index("shell.js")
    for name in ("shell.js", "app.js", "debug.js", "appcms.js", "live.js"):
        source = (JS / name).read_text()
        assert "new WebSocket(" not in source, name
        assert "VLLive.connect(" in source, name
