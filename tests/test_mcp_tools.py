"""MCP tool targeting and the loopback invoke route. Does not start stdio."""

from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.mcp_server.client import console_base_url
from viewlift_ai.mcp_server.runtime import transport_choice
from viewlift_ai.mcp_server.tools import (
    app_inspect,
    app_mutate,
    get_flow,
    open_deeplink,
    replay_flow,
    set_network_profile,
    split_image,
)
from viewlift_ai.models.flow import RetentionPolicy


class FakeConsole:
    def __init__(self, clients: list[dict[str, Any]]) -> None:
        self.clients = clients
        self.calls: list[tuple[str, str, object]] = []
        self.unlocked = False

    def get(self, path: str, query: object = None) -> dict[str, Any]:
        self.calls.append(("GET", path, query))
        if path == "/api/debug/clients":
            return {"clients": self.clients}
        if path == "/api/reveal/status":
            return {"unlocked": self.unlocked, "unlock_token": "do-not-leak"}
        if path.startswith("/api/flows/"):
            return {
                "id": path.rsplit("/", 1)[-1],
                "client_id": "app-a",
                "host": "example.com",
                "revealed": False,
            }
        if path == "/api/logs":
            return {"logs": []}
        return {}

    def post(self, path: str, body: object = None) -> dict[str, Any]:
        self.calls.append(("POST", path, body))
        return {"ok": True}


def _posted(console: FakeConsole) -> list[tuple[str, object]]:
    return [(path, body) for method, path, body in console.calls if method == "POST"]


def test_script_transport_is_loopback_http() -> None:
    assert transport_choice({}) == ("stdio", "", None)
    assert transport_choice({"VIEWLIFT_AI_MCP_TRANSPORT": "streamable-http"}) == (
        "streamable-http",
        "127.0.0.1",
        8742,
    )
    assert transport_choice(
        {"VIEWLIFT_AI_MCP_TRANSPORT": "streamable-http", "VIEWLIFT_AI_MCP_PORT": "9000"}
    )[2] == 9000
    with pytest.raises(ValueError, match="TCP port"):
        transport_choice(
            {"VIEWLIFT_AI_MCP_TRANSPORT": "streamable-http", "VIEWLIFT_AI_MCP_PORT": "nope"}
        )


def test_console_url_must_be_loopback() -> None:
    assert console_base_url("http://127.0.0.1:8741") == "http://127.0.0.1:8741"
    with pytest.raises(ValueError, match="loopback"):
        console_base_url("http://example.com:8741")


def test_inspect_without_client_does_not_enqueue() -> None:
    console = FakeConsole(
        [
            {"id": "app-a", "bundle_id": "com.a", "online": True, "device_key": "SIM-1"},
            {"id": "app-b", "bundle_id": "com.b", "online": True, "device_key": "SIM-1"},
        ]
    )
    result = app_inspect(console, client_id="", command="performance.snapshot")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert _posted(console) == []
    assert {item["id"] for item in result["apps"]} == {"app-a", "app-b"}


def test_ambiguous_bundle_does_not_enqueue() -> None:
    console = FakeConsole(
        [
            {"id": "app-a", "bundle_id": "com.same", "online": True},
            {"id": "app-b", "bundle_id": "com.same", "online": True},
        ]
    )
    result = app_inspect(console, client_id="com.same", command="ott.snapshot")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert _posted(console) == []


def test_unique_bundle_targets_that_app() -> None:
    console = FakeConsole(
        [
            {"id": "app-a", "bundle_id": "com.a", "online": True},
            {"id": "app-b", "bundle_id": "com.b", "online": False},
        ]
    )
    app_inspect(console, client_id="com.a", command="hang.list", payload={})  # type: ignore[arg-type]
    assert _posted(console) == [
        ("/api/debug/invoke", {"client_id": "app-a", "name": "hang.list", "payload": {}})
    ]


def test_mutate_without_confirm_does_not_enqueue() -> None:
    console = FakeConsole([{"id": "app-a", "bundle_id": "com.a", "online": True}])
    result = app_mutate(  # type: ignore[arg-type]
        console,
        client_id="app-a",
        command="storage.defaults_set",
        payload={"key": "x", "value": True},
        confirm=False,
    )
    assert result["detail"] == "confirm must be true"
    assert _posted(console) == []


def test_mutate_with_confirm_sets_mutate_flag() -> None:
    console = FakeConsole([{"id": "app-a", "bundle_id": "com.a", "online": True}])
    app_mutate(  # type: ignore[arg-type]
        console,
        client_id="app-a",
        command="ui.highlight",
        payload={"id": "row"},
        confirm=True,
    )
    assert _posted(console) == [
        (
            "/api/debug/invoke",
            {
                "client_id": "app-a",
                "name": "ui.highlight",
                "payload": {"id": "row"},
                "mutate": True,
            },
        )
    ]


def test_deeplink_uses_simulator_device_key() -> None:
    console = FakeConsole(
        [
            {
                "id": "app-a",
                "bundle_id": "com.a",
                "online": True,
                "is_simulator": True,
                "device_key": "SIM-UDID",
            }
        ]
    )
    open_deeplink(console, url="demo://play", client_id="app-a", confirm=True)  # type: ignore[arg-type]
    assert _posted(console) == [
        (
            "/api/debug/deeplink",
            {"url": "demo://play", "client_id": "app-a"},
        )
    ]


def test_deeplink_routes_a_phone_through_the_console() -> None:
    console = FakeConsole(
        [
            {
                "id": "phone",
                "bundle_id": "com.a",
                "online": True,
                "is_simulator": False,
                "device_key": "vendor:abc",
            }
        ]
    )
    open_deeplink(console, url="demo://play", client_id="phone", confirm=True)  # type: ignore[arg-type]
    assert _posted(console) == [
        ("/api/debug/deeplink", {"url": "demo://play", "client_id": "phone"})
    ]


def test_network_profile_without_confirm_does_not_post() -> None:
    console = FakeConsole([])
    result = set_network_profile(console, profile="offline", confirm=False)  # type: ignore[arg-type]
    assert result["ok"] is False
    assert _posted(console) == []


def test_network_profile_with_client_shapes_that_app_only() -> None:
    console = FakeConsole([{"id": "app-a", "bundle_id": "com.a", "online": True}])
    set_network_profile(console, profile="3g", client_id="app-a", confirm=True)  # type: ignore[arg-type]
    assert _posted(console) == [
        (
            "/api/debug/invoke",
            {
                "client_id": "app-a",
                "name": "net.profile",
                "payload": {"profile": "3g"},
                "mutate": True,
            },
        )
    ]


def test_replay_does_not_unlock_reveal() -> None:
    console = FakeConsole([])
    result = replay_flow(console, flow_id="flow-1", confirm=True)  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "unlock_token" not in result
    assert "do-not-leak" not in str(result)
    assert _posted(console) == []


def test_get_flow_stays_redacted_while_reveal_is_locked() -> None:
    console = FakeConsole([{"id": "app-a", "bundle_id": "com.a", "online": True}])
    result = get_flow(console, flow_id="flow-1", client_id="app-a", reveal=True)  # type: ignore[arg-type]
    assert result["revealed"] is False
    assert "do-not-leak" not in str(result)
    revealed = [
        query
        for _method, _path, query in console.calls
        if isinstance(query, dict) and query.get("reveal")
    ]
    assert revealed == []


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"m" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )


def test_invoke_route_missing_client_returns_apps(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.post(
            "/api/debug/invoke",
            json={"name": "performance.snapshot", "payload": {}},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["apps"] == []
        assert client.app.state.debug_sessions.list_clients() == []


def test_invoke_route_waits_for_the_named_app(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        sessions = client.app.state.debug_sessions
        sessions.register(
            {
                "client_id": "app-a",
                "app_name": "Alpha",
                "bundle_id": "com.a",
                "device_key": "SIM-1",
                "device_name": "iPhone 16",
                "is_simulator": True,
            }
        )
        sessions.register(
            {
                "client_id": "app-b",
                "app_name": "Beta",
                "bundle_id": "com.b",
                "device_key": "SIM-1",
                "device_name": "iPhone 16",
                "is_simulator": True,
            }
        )

        def answer() -> None:
            for _ in range(100):
                polled = sessions.poll("app-a")
                if polled:
                    sessions.ingest_event(
                        "app-a",
                        {
                            "module": "ott",
                            "kind": "command-result",
                            "payload": {
                                "command_id": polled[0]["id"],
                                "name": "ott.snapshot",
                                "ok": True,
                                "result": {"app": "alpha"},
                            },
                        },
                    )
                    return
                threading.Event().wait(0.01)

        threading.Thread(target=answer, daemon=True).start()
        response = client.post(
            "/api/debug/invoke",
            json={
                "client_id": "app-a",
                "name": "ott.snapshot",
                "payload": {},
                "timeout": 2,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["event"]["payload"]["result"] == {"app": "alpha"}
        assert sessions.poll("app-b") == []


def test_split_image_removes_base64() -> None:
    encoded = base64.b64encode(b"\x89PNG").decode()
    cleaned, raw = split_image(
        {
            "event": {
                "payload": {
                    "result": {"mime": "image/png", "base64": encoded, "ok": True},
                }
            }
        }
    )
    assert raw == b"\x89PNG"
    assert "base64" not in cleaned["event"]["payload"]["result"]


def test_split_image_strips_hierarchy_texture() -> None:
    hierarchy = {
        "windows": [],
        "screenshot": {"mime": "image/jpeg", "base64": "AAAA", "width": 393, "height": 852},
    }
    cleaned, raw = split_image({"event": {"payload": {"result": hierarchy}}})
    assert raw is None
    texture = cleaned["event"]["payload"]["result"]["screenshot"]
    assert "base64" not in texture
    assert texture["width"] == 393
    assert hierarchy["screenshot"]["base64"] == "AAAA"


def test_split_image_strips_layer_images() -> None:
    page = {
        "layers": [{"id": "1", "mime": "image/png", "base64": "AAAA", "width": 10, "height": 20}],
        "empty": ["2"],
        "next_offset": 3,
        "total": 9,
        "done": False,
    }
    cleaned, raw = split_image({"event": {"payload": {"result": page}}})
    assert raw is None
    result = cleaned["event"]["payload"]["result"]
    assert result["layers"] == [{"id": "1", "mime": "image/png", "width": 10, "height": 20}]
    assert result["next_offset"] == 3
    assert page["layers"][0]["base64"] == "AAAA"
