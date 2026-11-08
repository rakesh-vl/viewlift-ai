"""Console host routes and the MCP workflow layer: confirm, read-only mode, gating, scrubbing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.debug_session import MUTATING_COMMANDS, READ_COMMANDS
from viewlift_ai.services.host.runner import ROOTS_ENV

pytest.importorskip("mcp")

from viewlift_ai.mcp_server import guard  # noqa: E402
from viewlift_ai.mcp_server.workflows import common, ui  # noqa: E402

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"a" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )


class FakeConsole:
    def __init__(self, answer: dict[str, Any], clients: list[dict[str, Any]] | None = None) -> None:
        self.answer = answer
        self.clients = clients or []
        self.posts: list[tuple[str, Any]] = []

    def post(self, path: str, body: Any) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.answer

    def get(self, path: str, query: Any = None) -> dict[str, Any]:
        if path == "/api/debug/clients":
            return {"clients": self.clients}
        return {}


def test_host_routes_list_tools_and_gate_confirm(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        tools = client.get("/api/host/tools").json()["tools"]
        names = {item["name"]: item["mutating"] for item in tools}
        assert names["list_sims"] is False
        assert names["erase_sims"] is True
        refused = client.post("/api/host/call", json={"tool": "erase_sims", "args": {}})
        assert refused.json() == {"ok": False, "detail": "confirm must be true"}
        bad = client.post("/api/host/call", json={"tool": 3, "args": {}})
        assert bad.status_code == 400


def test_host_routes_require_json_content_type(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.post(
            "/api/host/call",
            content='{"tool":"list_sims"}',
            headers={"content-type": "text/plain"},
        )
        # FastAPI refuses the non-JSON body (422) before the route's own 415 check.
        assert response.status_code in {415, 422}


def test_host_roots_route_saves_and_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    folder = tmp_path / "apps"
    folder.mkdir()
    with TestClient(create_app(_config(tmp_path))) as client:
        saved = client.post("/api/host/roots", json={"roots": [str(folder)]}).json()
        assert saved["active"] == [str(folder.resolve())]
        assert client.get("/api/host/roots").json()["saved"] == [str(folder.resolve())]
        rejected = client.post("/api/host/roots", json={"roots": ["/"]})
        assert rejected.status_code == 400


def test_mcp_host_calls_need_confirm_and_honour_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = FakeConsole({"ok": True})
    monkeypatch.setattr(common, "api", lambda: console)
    assert common.host("erase_sims", {}, mutating=True)["detail"] == "confirm must be true"
    monkeypatch.setenv(guard.READ_ONLY_ENV, "1")
    assert "read-only" in common.host("erase_sims", {}, mutating=True, confirm=True)["detail"]
    assert console.posts == []
    monkeypatch.delenv(guard.READ_ONLY_ENV)
    common.host("erase_sims", {"simulatorId": ""}, mutating=True, confirm=True)
    path, body = console.posts[-1]
    assert path == "/api/host/call"
    assert body == {"tool": "erase_sims", "args": {}, "confirm": True}


def test_mcp_host_results_are_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    console = FakeConsole({"ok": True, "stdout": f"Authorization: Bearer {JWT}"})
    monkeypatch.setattr(common, "api", lambda: console)
    result = common.host("doctor", {})
    assert JWT not in str(result)


def test_ui_actions_refuse_without_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    console = FakeConsole({"ok": True})
    monkeypatch.setattr(common, "api", lambda: console)
    blocked = ui.action("tap", "", {}, "ui.tap", {"label": "Play"}, {"label": "Play"}, False)
    assert blocked == {"ok": False, "detail": "confirm must be true"}
    no_sdk_path = ui.action("drag", "app-1", {}, None, {}, {"startX": 1}, True)
    assert "outside the SDK" in no_sdk_path["detail"]
    no_runner = ui.action("drag", "app-1", {}, None, {}, {"startX": 1}, True, "ui.drag")
    assert "ui_runner_start" in no_runner["detail"]
    assert console.posts == []


SDK_PHONE = {
    "id": "app-1",
    "bundle_id": "com.demo",
    "online": True,
    "is_simulator": False,
    "device_key": "vendor:abc",
    "device_model": "iPhone17,1",
    "capabilities": {"ui": "available"},
}
RUNNER = {
    "id": "runner-1",
    "bundle_id": "com.demo",
    "online": True,
    "is_simulator": False,
    "device_key": "runner:00008140-000A",
    "device_model": "iPhone17,1",
    "capabilities": {"ui": "available", "runner": "available"},
}


def test_runner_for_matches_same_app_and_device() -> None:
    assert ui.runner_for(SDK_PHONE, [SDK_PHONE, RUNNER]) == "runner-1"
    assert ui.runner_for(RUNNER, [RUNNER]) == "runner-1"
    other_model = {**RUNNER, "device_model": "iPad14,1"}
    assert ui.runner_for(SDK_PHONE, [SDK_PHONE, other_model]) is None
    sim_app = {**SDK_PHONE, "is_simulator": True, "device_key": "SIM-1"}
    sim_runner = {**RUNNER, "is_simulator": True, "device_key": "runner:SIM-1"}
    assert ui.runner_for(sim_app, [sim_app, sim_runner]) == "runner-1"
    twins = [SDK_PHONE, RUNNER, {**RUNNER, "id": "runner-2"}]
    assert ui.runner_for(SDK_PHONE, twins) is None


def test_runner_for_prefers_the_device_hint_over_the_model() -> None:
    phone = {**SDK_PHONE, "device_hint": "abc"}
    same = {**RUNNER, "device_hint": "abc"}
    other = {**RUNNER, "id": "runner-2", "device_hint": "def"}
    assert ui.runner_for(phone, [phone, same, other]) == "runner-1"
    assert ui.runner_for(phone, [phone, other]) is None


def test_gesture_outside_the_sdk_goes_to_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    console = FakeConsole({"ok": True}, clients=[SDK_PHONE, RUNNER])
    monkeypatch.setattr(common, "api", lambda: console)
    args = {"startX": 1, "startY": 2, "endX": 3, "endY": 4}
    ui.action("drag", "app-1", {}, None, {}, args, True, "ui.drag")
    assert console.posts
    path, body = console.posts[-1]
    assert path == "/api/debug/invoke"
    assert body["client_id"] == "runner-1"
    assert body["name"] == "ui.drag"
    assert body["payload"] == args


def test_ui_wait_requires_a_query() -> None:
    assert ui.wait_for("", {}, {}, False, 1.0)["ok"] is False


def test_sdk_automation_commands_are_classified() -> None:
    for name in (
        "ui.accessibility_tree",
        "ui.find",
        "ui.wait_for",
        "preview.list",
        "preview.render",
    ):
        assert name in READ_COMMANDS
    for name in ("ui.tap", "ui.type_text", "ui.swipe", "ui.scroll", "ui.key", "ui.remote"):
        assert name in MUTATING_COMMANDS
        assert name not in READ_COMMANDS


class _Chat:
    """Stand-in MCP session. Builtin object() cannot be a weak-dict key."""


def test_default_session_lists_debug_tools_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from viewlift_ai.mcp_server.server import GATE, mcp
    from viewlift_ai.mcp_server.workflows import WORKFLOWS_ENV

    monkeypatch.delenv(WORKFLOWS_ENV, raising=False)
    session = _Chat()
    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert "tap" in registered
    assert "build_sim" in registered
    assert "doctor" in registered
    assert not GATE.is_listed("tap", session)
    assert not GATE.is_listed("build_sim", session)
    assert not GATE.is_listed("doctor", session)
    assert GATE.is_listed("list_apps", session)
    assert GATE.is_listed("manage_workflows", session)
    assert GATE.is_listed("app_inspect", session)


def test_workflow_lists_stay_inside_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from viewlift_ai.mcp_server.server import GATE
    from viewlift_ai.mcp_server.workflows import WORKFLOWS_ENV

    monkeypatch.delenv(WORKFLOWS_ENV, raising=False)
    one, two = _Chat(), _Chat()
    GATE.apply(one, {"ui"})
    GATE.apply(two, {"simulator"})
    assert GATE.is_listed("tap", one)
    assert not GATE.is_listed("build_sim", one)
    assert not GATE.is_listed("tap", two)
    assert GATE.is_listed("build_sim", two)
    assert not GATE.is_listed("doctor", one)
    GATE.apply(one, {"project"})
    assert GATE.is_listed("doctor", one)
    assert GATE.is_listed("wait_job", one)
    assert not GATE.is_listed("doctor", two)
    assert not GATE.is_listed("tap", one)
    assert GATE.status(one)["enabled"] == ["project"]
    assert GATE.status(two)["enabled"] == ["simulator"]
