"""Agent docs page renders and stays in step with the MCP server."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.mcp_server.catalog import COMMAND_NOTES, tool_names
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.debug_session import ALLOWED_COMMANDS


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


def test_agent_page_lists_every_tool_and_command(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get("/agent")
        assert page.status_code == 200
        html = page.text
        assert 'aria-current="page"' in html
        for name in tool_names():
            assert f"<code>{name}</code>" in html
        for command in ALLOWED_COMMANDS:
            assert f"<code>{command}</code>" in html
        assert "http://127.0.0.1:8742/mcp" in html


def test_agent_status_reports_the_mcp_url(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        body = client.get("/api/agent/status").json()
        assert body["mcp"]["url"].endswith("/mcp")
        assert isinstance(body["mcp"]["listening"], bool)


def test_every_command_has_a_note() -> None:
    assert set(COMMAND_NOTES) == ALLOWED_COMMANDS


def test_debug_page_has_layout_explorer(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get("/debug")
        assert page.status_code == 200
        html = page.text
        for marker in (
            "ui-guides",
            "ui-baselines",
            "ui-oversized",
            "ui-hover",
            "ui-slow",
            "ui-device-repaint",
            "ui-path",
            "ui-match-next",
            "ui-zoom-label",
            "System views",
            "Zoom out",
            "Only this",
        ):
            assert marker in html


def test_debug_page_has_explorer_and_settings(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        html = client.get("/debug").text
        for marker in (
            'data-panel="explorer"',
            'data-panel="settings"',
            "explorer-roots",
            "explorer-instances",
            "explorer-result",
            "settings-result",
            "ui-explore",
            "/static/js/explorer.js",
        ):
            assert marker in html
        assert client.get("/static/js/explorer.js").status_code == 200


def test_catalog_matches_registered_tools() -> None:
    pytest.importorskip("mcp")
    from viewlift_ai.mcp_server.server import mcp

    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert set(tool_names()) == registered
