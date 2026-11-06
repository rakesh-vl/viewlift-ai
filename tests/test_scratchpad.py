"""Scratchpad library and Tools page."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.scratchpad import ScratchpadError, ScratchpadLibrary, compile_source

SAMPLE = """\
/**
{
  "api": 1,
  "name": "Trim text",
  "description": "Trim the ends.",
  "author": "you",
  "tags": "text"
}
**/

function main(state) {
  state.text = state.text.trim();
}
"""


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


def test_builtins_parse() -> None:
    library = ScratchpadLibrary(Path("/tmp/unused-scratchpad"))
    scripts = library.list_scripts()
    ids = {item.id for item in scripts}
    assert "pretty-json" in ids
    assert "jwt-decode" in ids
    assert "count" in ids
    assert all(item.error is None for item in scripts)
    assert all(item.builtin for item in scripts)


def test_tools_page_and_script_lifecycle(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        page = client.get("/tools")
        assert page.status_code == 200
        assert b'href="/tools"' in page.content
        assert b"New script" in page.content
        assert b"Scratchpad" in page.content

        home = client.get("/")
        assert b'href="/tools"' in home.content
        assert b">Tools<" in home.content

        listed = client.get("/api/tools/scripts")
        assert listed.status_code == 200
        body = listed.json()
        assert any(item["id"] == "pretty-json" and item["builtin"] for item in body["scripts"])

        created = client.post("/api/tools/scripts", json={"source": SAMPLE})
        assert created.status_code == 200
        saved = created.json()
        assert saved["id"] == "trim-text"
        assert saved["builtin"] is False
        assert "function main" in saved["source"]

        path = tmp_path / "scratchpad" / "trim-text.js"
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o600

        fetched = client.get("/api/tools/scripts/trim-text")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Trim text"

        updated = client.put(
            "/api/tools/scripts/trim-text",
            json={"source": SAMPLE.replace("Trim the ends.", "Trim both ends.")},
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "Trim both ends."

        blocked = client.put("/api/tools/scripts/pretty-json", json={"source": SAMPLE})
        assert blocked.status_code == 400

        removed = client.delete("/api/tools/scripts/pretty-json")
        assert removed.status_code == 400

        deleted = client.delete("/api/tools/scripts/trim-text")
        assert deleted.status_code == 200
        assert client.get("/api/tools/scripts/trim-text").status_code == 404
        assert not path.exists()


def test_rejects_bad_scripts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        missing = client.post("/api/tools/scripts", json={"source": "function main(state) {}"})
        assert missing.status_code == 400

        no_main = client.post(
            "/api/tools/scripts",
            json={"source": '/**\n{"api": 1, "name": "Nope"}\n**/\nfunction other() {}\n'},
        )
        assert no_main.status_code == 400

        huge = "/**\n" + ('{"api": 1, "name": "Big"}\n') + "**/\n" + ("x" * 70_000)
        assert client.post("/api/tools/scripts", json={"source": huge}).status_code == 400

        bad_id = client.get("/api/tools/scripts/Not_Valid")
        assert bad_id.status_code == 400
        with pytest.raises(ScratchpadError) as rejected:
            ScratchpadLibrary(tmp_path / "scratchpad").get("../secret")
        assert rejected.value.status == 400


def test_duplicate_names_get_a_new_id(tmp_path: Path) -> None:
    library = ScratchpadLibrary(tmp_path / "scratchpad")
    first = library.create(SAMPLE)
    second = library.create(SAMPLE)
    assert first.id == "trim-text"
    assert second.id == "trim-text-2"
    compiled = compile_source(second.source)
    assert compiled.name == "Trim text"
