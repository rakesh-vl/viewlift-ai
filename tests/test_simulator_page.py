"""Simulator page: the HTML shell, the nav link, and the host tool names the script calls."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.host import HostToolkit

WEB = Path(__file__).resolve().parents[1] / "src" / "viewlift_ai" / "web"
SCRIPT = WEB / "static" / "js" / "simulator.js"
TEMPLATE = WEB / "templates" / "simulator.html"


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


def test_simulator_page_renders_and_is_linked_in_nav(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get("/simulator")
        assert page.status_code == 200
        assert 'id="sim-page"' in page.text
        assert 'aria-current="page"' in page.text
        assert client.get("/static/js/simulator.js?v=1").status_code == 200
        assert 'href="/simulator"' in client.get("/").text


def test_page_calls_only_registered_host_tools(tmp_path: Path) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    called = set(re.findall(r'(?:callTool|run)\(\s*"([a-z_]+)"', source))
    called |= set(re.findall(r'data-sim-action="([a-z_]+)"', template))
    called |= set(re.findall(r"^\s+([a-z_]+): \(\) =>", source, flags=re.MULTILINE))
    assert called, "no host tool calls found in simulator.js"
    registered = set(HostToolkit(tmp_path).names())
    missing = sorted(name for name in called if name not in registered)
    assert missing == [], f"simulator page calls unknown host tools: {missing}"
    for name in ("discover_projs", "list_schemes", "show_build_settings", "sync_xcode_defaults"):
        assert name in called, f"simulator page no longer auto-fills with {name}"


def test_page_script_ids_exist_in_template() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    wanted = set(re.findall(r'\$\("([a-z0-9-]+)"\)', source))
    present = set(re.findall(r'id="([a-z0-9-]+)"', template))
    missing = sorted(wanted - present)
    assert missing == [], f"simulator.js looks up ids the template lacks: {missing}"
