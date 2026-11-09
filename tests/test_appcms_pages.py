"""AppCMS left-menu pages and their loopback read and action routes."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.api.appcms_routes import APPCMS_PAGES
from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy


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


def _register(client: TestClient) -> tuple[str, object]:
    sessions = client.app.state.debug_sessions
    sessions.register(
        {
            "client_id": "appcms-1",
            "app_name": "BEK TV+",
            "bundle_id": "com.myoutdoortv.myoutdoortv",
            "platform": "iOS",
            "os_version": "26",
        }
    )
    return "appcms-1", sessions


def _answer(sessions: object, client_id: str, result: dict[str, object]) -> threading.Thread:
    """Plays the device: polls one command and posts its result."""

    def run() -> None:
        for _ in range(100):
            commands = sessions.poll(client_id)
            if commands:
                command = commands[0]
                sessions.ingest_event(
                    client_id,
                    {
                        "module": "ott",
                        "kind": "command-result",
                        "payload": {
                            "command_id": command["id"],
                            "name": command["name"],
                            "ok": True,
                            "result": result,
                        },
                    },
                )
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_every_appcms_page_renders_with_sub_menu(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        for slug, label, _section in APPCMS_PAGES:
            page = client.get(f"/appcms/{slug}")
            assert page.status_code == 200
            assert f'data-appcms-page="{slug}"' in page.text
            assert 'href="/appcms/diagnostics"' in page.text
            assert label.split(" ")[0] in page.text
        assert client.get("/appcms").status_code == 200
        assert client.get("/appcms/nope").status_code == 404
        assert client.get("/static/js/appcms.js").status_code == 200
        assert client.get("/static/css/appcms.css").status_code == 200


def test_appcms_link_is_in_the_primary_menu(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        text = client.get("/debug").text
        assert 'href="/appcms"' in text
        assert 'href="/host"' in text


@pytest.mark.parametrize(
    ("slug", "section"),
    [("", ""), ("playback", "playback"), ("diagnostics", ""), ("actions", ""), ("compare", "")],
)
def test_host_pages_render_from_the_manifest_shell(tmp_path: Path, slug: str, section: str) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get(f"/host/{slug}" if slug else "/host")
        assert page.status_code == 200
        assert 'data-appcms-mode="host"' in page.text
        assert f'data-appcms-section="{section}"' in page.text
        assert 'id="host-nav-pages"' in page.text
        assert 'href="/host/diagnostics"' in page.text


@pytest.mark.parametrize("slug", ["Bad", "1abc", "a-b", "x" * 60])
def test_host_page_rejects_unsafe_slugs(tmp_path: Path, slug: str) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get(f"/host/{slug}").status_code == 404


def test_read_rejects_non_appcms_commands(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.post("/api/appcms/read", json={"client_id": "x", "name": "ui.tap"})
        assert response.status_code == 400


def test_read_returns_section_for_the_dashboard(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client_id, sessions = _register(client)
        worker = _answer(
            sessions,
            client_id,
            {"section": "session", "data": {"refreshToken": "abcdef123", "logged_in": True}},
        )
        response = client.post(
            "/api/appcms/read",
            json={"client_id": client_id, "name": "ott.section", "payload": {"name": "session"}},
        )
        worker.join(timeout=5)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["result"]["data"]["refreshToken"] == "abcdef123"
        assert body["result"]["data"]["logged_in"] is True


def test_action_requires_confirmation(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client_id, _sessions = _register(client)
        response = client.post(
            "/api/appcms/action", json={"client_id": client_id, "action": "refresh_token"}
        )
        assert response.status_code == 403


def test_action_with_one_use_token_runs_once(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client_id, sessions = _register(client)
        token = client.post(
            "/api/debug/confirm", json={"client_id": client_id, "command": "ott.action"}
        ).json()["token"]
        worker = _answer(sessions, client_id, {"ok": True, "action": "refresh_token"})
        response = client.post(
            "/api/appcms/action",
            json={"client_id": client_id, "action": "refresh_token", "confirmation": token},
        )
        worker.join(timeout=5)
        assert response.status_code == 200
        assert response.json()["result"]["action"] == "refresh_token"
        again = client.post(
            "/api/appcms/action",
            json={"client_id": client_id, "action": "refresh_token", "confirmation": token},
        )
        assert again.status_code == 403


@pytest.mark.parametrize("name", ["ott.sections", "ott.diagnostics"])
def test_reads_need_no_confirmation(tmp_path: Path, name: str) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client_id, sessions = _register(client)
        worker = _answer(sessions, client_id, {"provider": "AppCMS", "sections": []})
        response = client.post("/api/appcms/read", json={"client_id": client_id, "name": name})
        worker.join(timeout=5)
        assert response.status_code == 200
