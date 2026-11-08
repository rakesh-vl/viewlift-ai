"""The every-page socket forwards problems and leaves samples on the app socket."""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.api.ws import _debug_event_in_scope
from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy


def test_apps_socket_forwards_problems_only() -> None:
    scope = {"__apps__"}
    assert _debug_event_in_scope({"type": "apps-changed"}, scope)
    assert _debug_event_in_scope({"type": "debug-crash", "client_id": "app"}, scope)
    hang = {
        "type": "debug-event",
        "event": {"module": "hang", "kind": "detected", "client_id": "app"},
    }
    assert _debug_event_in_scope(hang, scope)
    leak = {"type": "debug-event", "event": {"module": "memory", "kind": "leak"}}
    assert _debug_event_in_scope(leak, scope)
    failed = {
        "type": "debug-event",
        "event": {"module": "playback", "kind": "event", "payload": {"kind": "item_failed"}},
    }
    assert _debug_event_in_scope(failed, scope)
    drm = {
        "type": "debug-event",
        "event": {
            "module": "playback",
            "kind": "event",
            "payload": {"kind": "drm", "phase": "failed"},
        },
    }
    assert _debug_event_in_scope(drm, scope)
    bitrate = {
        "type": "debug-event",
        "event": {"module": "playback", "kind": "event", "payload": {"kind": "bitrate"}},
    }
    assert not _debug_event_in_scope(bitrate, scope)
    sample = {
        "type": "debug-event",
        "event": {"module": "performance", "kind": "sample", "client_id": "app"},
    }
    assert not _debug_event_in_scope(sample, scope)
    assert _debug_event_in_scope(sample, {"app"})
    assert not _debug_event_in_scope(sample, {"other"})


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


def test_every_page_socket_delivers_each_alert_type_and_drops_samples(tmp_path: Path) -> None:
    sent = [
        {"type": "debug-event", "event": {"module": "performance", "kind": "sample", "id": "s"}},
        {"type": "debug-crash", "client_id": "app", "event": {"id": "crash-1", "payload": {}}},
        {"type": "debug-event", "event": {"module": "hang", "kind": "detected", "id": "h"}},
        {"type": "debug-event", "event": {"module": "memory", "kind": "leak", "id": "l"}},
        {
            "type": "debug-event",
            "event": {
                "module": "playback",
                "kind": "event",
                "id": "p",
                "payload": {"kind": "item_failed"},
            },
        },
        {
            "type": "debug-event",
            "event": {
                "module": "playback",
                "kind": "event",
                "id": "p2",
                "payload": {"kind": "stall_end"},
            },
        },
        {"type": "debug-event", "event": {"module": "ui", "kind": "focus", "id": "f"}},
    ]
    expected = ["crash-1", "h", "l", "p"]
    app = create_app(_config(tmp_path))
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/debug?client_id=__apps__") as socket,
    ):
        bus = app.state.debug_event_bus
        for _ in range(100):
            if bus.subscriber_count:
                break
            time.sleep(0.02)
        for item in sent:
            bus.publish_threadsafe(item)
        received = []
        for _ in expected:
            message = socket.receive_json()
            nested = message.get("event") or {}
            received.append(nested.get("id"))
    assert received == expected
