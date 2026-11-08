"""Crash replay upload, diagnosis, the markdown brief, and the media routes."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.api.debug_routes import router as debug_router
from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.mcp_server import tools
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.crash_media import CrashMediaStore
from viewlift_ai.services.debug_session import DebugSessionService, merge_crash
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus
from viewlift_ai.services.host.crash_brief import brief_markdown
from viewlift_ai.services.host.crash_diagnosis import diagnose, make_diagnoser

CRASH_ID = "crash-app-1-4242"
VIDEO = bytes(range(256)) * 40


def _service(tmp_path: Path, roots: list[Path] | None = None) -> DebugSessionService:
    store = EncryptedSqliteStore(
        tmp_path / "crash.db",
        SecretBoxCipher(b"k" * SecretBox.KEY_SIZE),
        retention=RetentionPolicy.SESSION,
    )
    service = DebugSessionService(
        store=store, events=EventBus(), media=CrashMediaStore(tmp_path / "media")
    )
    service.diagnose_crash = make_diagnoser(lambda: roots or [])
    service.register(
        {
            "client_id": "app-1",
            "app_name": "OneApp",
            "bundle_id": "com.viewlift.oneapp",
            "executable": "OneApp",
            "platform": "iOS",
        }
    )
    return service


def _crash(**extra: Any) -> dict[str, Any]:
    event = {
        "kind": "fatal",
        "source": "sdk-signal",
        "timestamp": 1_000.0,
        "signal": "SIGTRAP",
        "name": "SIGTRAP",
        "reason": "Breakpoint trap. A Swift runtime failure, failed precondition, or force unwrap.",
        "pid": 4242,
        "thread": "main",
        "executable": "OneApp",
        "previous_session": True,
        "frames": [
            {
                "index": 0,
                "image": "OneApp",
                "symbol": "Swift runtime failure: arithmetic overflow",
                "file": "<compiler-generated>",
                "inlined": True,
            },
            {
                "index": 1,
                "image": "OneApp",
                "symbol": "PlayerViewModel.advance(by:)",
                "class": "PlayerViewModel",
                "file": "PlayerViewModel.swift",
                "line": 5,
            },
            {"index": 2, "image": "UIKitCore", "symbol": "-[UIApplication sendAction:]"},
        ],
        "context": {
            "captured_at": 999.5,
            "screens": [
                {"time": 990.0, "screen_class": "HomeViewController", "title": "Home"},
                {"time": 996.0, "screen_class": "PlayerViewController", "title": ""},
            ],
            "actions": [
                {"kind": "tap", "label": "Play", "target_class": "PlayButton", "ago_ms": 1500}
            ],
            "requests": [
                {
                    "time": 997.0,
                    "method": "GET",
                    "host": "api.example.com",
                    "path": "/v1/stream",
                    "status": 500,
                }
            ],
            "console": [{"message": "Player error: bad manifest", "ago_ms": 400}],
            "environment": {"app_version": "1.2", "os_version": "18.0"},
        },
        "replay": {"status": "pending", "frames": 40},
    }
    event.update(extra)
    return event


def _chunk(index: int, total: int, data: bytes, **extra: Any) -> dict[str, Any]:
    payload = {
        "crash_id": "sdk-id",
        "pid": 4242,
        "index": index,
        "total": total,
        "mime": "video/mp4",
        "frames": 40,
        "duration_ms": 10_500,
        "base64": base64.b64encode(data).decode(),
    }
    payload.update(extra)
    return {"module": "crash", "kind": "replay-chunk", "payload": payload}


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "Sources").mkdir(parents=True)
    (root / "Sources" / "PlayerViewModel.swift").write_text(
        "struct PlayerViewModel {\n"
        "    var position: Int8 = 120\n"
        "\n"
        "    mutating func advance(by step: Int8) {\n"
        "        position += step\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return root


def test_diagnosis_names_the_failure_the_line_and_what_led_there(tmp_path: Path) -> None:
    root = _source(tmp_path)
    result = diagnose(_crash(), [root.resolve()])
    assert result["headline"].startswith("Arithmetic overflow")
    site = result["crash_site"]
    assert (site["file"], site["line"]) == ("PlayerViewModel.swift", 5)
    crash_line = [row for row in site["snippet"] if row["crash"]]
    assert crash_line == [{"line": 5, "code": "        position += step", "crash": True}]
    assert 'Tapped "Play"' in result["root_cause"]
    assert "api.example.com/v1/stream" in result["root_cause"]
    assert result["steps_to_reproduce"][0] == "Opened HomeViewController (Home)"
    assert result["steps_to_reproduce"][-1].startswith("The app crashes")
    kinds = [row["kind"] for row in result["timeline"]]
    assert kinds[-1] == "crash" and "tap" in kinds and "request-failed" in kinds
    assert result["environment"]["version"] == "1.2"


def test_generic_signal_summary_is_not_mistaken_for_a_precondition() -> None:
    event = _crash()
    event["frames"] = event["frames"][1:]
    result = diagnose(event, [])
    assert result["headline"].startswith("Swift runtime trap")


def test_force_unwrap_and_exception_headlines() -> None:
    nil_crash = _crash(source="crash-report", reason="Fatal error: Unexpectedly found nil")
    nil_crash["frames"] = nil_crash["frames"][1:]
    assert diagnose(nil_crash, [])["headline"].startswith("Force unwrap of nil")
    exception = _crash(
        kind="exception",
        source="sdk-exception",
        name="NSRangeException",
        reason="index 4 beyond bounds [0 .. 2]",
    )
    exception["frames"] = exception["frames"][1:]
    result = diagnose(exception, [])
    assert result["headline"] == "Uncaught NSRangeException: index 4 beyond bounds [0 .. 2]"
    assert "range" in result["suggested_fix_area"].lower()


def test_snippet_is_not_read_outside_project_roots(tmp_path: Path) -> None:
    root = _source(tmp_path)
    event = _crash()
    event["frames"][1]["path"] = str(root / "Sources" / "PlayerViewModel.swift")
    other = tmp_path / "elsewhere"
    other.mkdir()
    site = diagnose(event, [other.resolve()])["crash_site"]
    assert "snippet" not in site
    assert "outside the project roots" in site["snippet_note"]


def test_chunks_assemble_in_any_order_and_mark_the_replay_ready(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_crash("app-1", _crash())
    half = len(VIDEO) // 2
    poster = {"mime": "image/jpeg", "base64": base64.b64encode(b"jpeg").decode()}
    timeline = [{"offset_ms": 0, "before_crash_ms": 10_000, "screen_class": "Player"}]
    service.ingest_event("app-1", _chunk(1, 2, VIDEO[half:]))
    assert service.crash("app-1", CRASH_ID)["replay"]["status"] == "pending"
    service.ingest_event("app-1", _chunk(0, 2, VIDEO[:half], poster=poster, timeline=timeline))
    stored = service.crash("app-1", CRASH_ID)
    assert stored["replay"]["status"] == "ready"
    assert stored["replay"]["timeline"] == timeline
    assert service.media.video_path(CRASH_ID).read_bytes() == VIDEO
    assert service.media.poster_path(CRASH_ID).read_bytes() == b"jpeg"
    assert stored["diagnosis"]["headline"].startswith("Arithmetic overflow")


def test_replay_before_its_crash_is_held_until_the_crash_arrives(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.ingest_event("app-1", _chunk(0, 1, VIDEO))
    service.record_crash("app-1", _crash())
    assert service.crash("app-1", CRASH_ID)["replay"]["status"] == "ready"


def test_os_report_keeps_the_sdk_context_and_replay(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_crash("app-1", _crash())
    service.ingest_event("app-1", _chunk(0, 1, VIDEO))
    report = _crash(source="crash-report", name="EXC_BREAKPOINT (SIGTRAP)")
    report.pop("context")
    report.pop("replay")
    service.record_crash("app-1", report)
    stored = service.crash("app-1", CRASH_ID)
    assert stored["source"] == "crash-report"
    assert stored["replay"]["status"] == "ready"
    assert stored["context"]["screens"]


def test_merge_keeps_the_furthest_replay_status() -> None:
    merged = merge_crash({"replay": {"status": "pending"}}, {"replay": {"status": "ready"}})
    assert merged["replay"]["status"] == "ready"
    merged = merge_crash({"replay": {"status": "ready", "n": 1}}, {"replay": {"status": "pending"}})
    assert merged["replay"] == {"status": "ready", "n": 1}


def test_failed_replay_and_bad_chunks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_crash("app-1", _crash())
    failed = {"module": "crash", "kind": "replay-failed", "payload": {"pid": 4242, "error": "x"}}
    service.ingest_event("app-1", failed)
    assert service.crash("app-1", CRASH_ID)["replay"]["status"] == "failed"
    with pytest.raises(ValueError):
        service.ingest_event("app-1", _chunk(5, 2, VIDEO))
    with pytest.raises(ValueError):
        service.ingest_event("app-1", _chunk(0, 1, b"", base64="not base64!"))


def test_prune_drops_media_of_deleted_crashes(tmp_path: Path) -> None:
    media = CrashMediaStore(tmp_path / "media")
    media.add_chunk("crash-a-1", 0, 1, b"a")
    media.add_chunk("crash-b-2", 0, 1, b"b")
    media.save_poster("crash-b-2", b"p")
    assert media.prune({"crash-a-1"}) == 2
    assert media.has_video("crash-a-1") and not media.has_video("crash-b-2")


def test_brief_has_every_section_an_agent_needs(tmp_path: Path) -> None:
    root = _source(tmp_path)
    event = _crash()
    event["diagnosis"] = diagnose(event, [root.resolve()])
    event["replay"] = {"status": "ready"}
    text = brief_markdown(CRASH_ID, event, replay_url="http://127.0.0.1/r.mp4")
    for heading in (
        "## Root cause hypothesis",
        "## Crash site",
        "## Suggested fix area",
        "## Stack (app frames first)",
        "## Steps to reproduce",
        "## Timeline",
        "## Last requests",
        "## Console",
        "## Environment",
        "## Replay",
        "## How to verify the fix",
    ):
        assert heading in text
    assert "> 5 |         position += step" in text
    assert "http://127.0.0.1/r.mp4" in text


def _client(service: DebugSessionService) -> TestClient:
    app = FastAPI()
    app.include_router(debug_router)
    app.state.debug_sessions = service
    return TestClient(app, base_url="http://127.0.0.1")


def test_routes_serve_video_with_ranges_poster_report_and_detail(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_crash("app-1", _crash())
    poster = {"mime": "image/jpeg", "base64": base64.b64encode(b"jpeg").decode()}
    service.ingest_event("app-1", _chunk(0, 1, VIDEO, poster=poster))
    client = _client(service)
    base = f"/api/debug/crashes/{CRASH_ID}"

    full = client.get(f"{base}/replay.mp4", params={"client_id": "app-1"})
    assert full.status_code == 200 and full.content == VIDEO
    assert full.headers["content-type"] == "video/mp4"
    part = client.get(
        f"{base}/replay.mp4", params={"client_id": "app-1"}, headers={"Range": "bytes=0-9"}
    )
    assert part.status_code == 206 and part.content == VIDEO[:10]
    download = client.get(f"{base}/replay.mp4", params={"client_id": "app-1", "download": 1})
    assert download.headers["content-disposition"].startswith("attachment")

    assert client.get(f"{base}/poster.jpg", params={"client_id": "app-1"}).content == b"jpeg"
    report = client.get(f"{base}/report.md", params={"client_id": "app-1"})
    assert report.status_code == 200 and "## Root cause hypothesis" in report.text
    detail = client.get(f"{base}/detail", params={"client_id": "app-1"}).json()
    assert detail["replay_url"].endswith(f"{base}/replay.mp4?client_id=app-1")
    assert detail["poster"]["base64"] == poster["base64"]

    missing = client.get(
        "/api/debug/crashes/crash-app-1-9/replay.mp4", params={"client_id": "app-1"}
    )
    assert missing.status_code == 404


class _DetailConsole:
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        self.paths: list[str] = []

    def get(self, path: str, query: object = None) -> dict[str, Any]:
        self.paths.append(path)
        if path == "/api/debug/clients":
            return {"clients": [{"id": "app-1", "app_name": "OneApp"}]}
        if path == "/api/debug/crashes":
            return {"events": [{"id": CRASH_ID, "payload": self.detail["crash"]}]}
        if path.endswith("/detail"):
            return self.detail
        return {}


def test_crash_detail_tool_defaults_to_the_newest_crash_and_returns_the_poster() -> None:
    event = _crash()
    event["diagnosis"] = diagnose(event, [])
    poster = {"mime": "image/jpeg", "base64": base64.b64encode(b"jpeg").decode()}
    api = _DetailConsole({"crash": event, "brief": "# Crash", "poster": poster})
    result = tools.crash_detail(api, client_id="app-1")  # type: ignore[arg-type]
    assert api.paths[-1] == f"/api/debug/crashes/{CRASH_ID}/detail"
    assert result["headline"].startswith("Arithmetic overflow")
    assert result["stack"] and result["brief"] == "# Crash"
    cleaned, image = tools.split_poster(result)
    assert image == b"jpeg" and "poster" not in cleaned
