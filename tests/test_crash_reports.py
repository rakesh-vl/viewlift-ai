"""Crash capture: SDK events, macOS `.ips` reports, symbolication, and the stored card."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.mcp_server import insights
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus
from viewlift_ai.services.host.crash_reports import (
    CrashPipeline,
    CrashReportWatcher,
    event_from_report,
    match_client,
    read_report,
)
from viewlift_ai.services.host.runner import CommandResult
from viewlift_ai.services.host.symbolicate import (
    Symbolicator,
    class_of,
    is_system_image,
    parse_atos_line,
    parse_uuid_lines,
)

UUID = "6CF15444-AC7C-3927-9391-D8DB427A5658"
BASE = "0x102ab0000"


def _service(tmp_path: Path) -> DebugSessionService:
    store = EncryptedSqliteStore(
        tmp_path / "crash.db",
        SecretBoxCipher(b"k" * SecretBox.KEY_SIZE),
        retention=RetentionPolicy.SESSION,
    )
    service = DebugSessionService(store=store, events=EventBus())
    service.register(
        {
            "client_id": "app-1",
            "app_name": "OneApp",
            "bundle_id": "com.viewlift.oneapp",
            "executable": "OneApp",
            "platform": "iOS",
            "device_key": "SIM-UDID-1",
            "is_simulator": True,
            "debugger_attached": True,
        }
    )
    return service


def _sdk_event(**extra: Any) -> dict[str, Any]:
    event = {
        "kind": "fatal",
        "source": "sdk-signal",
        "timestamp": 1_700_000_000.0,
        "signal": "SIGTRAP",
        "name": "SIGTRAP",
        "reason": "Breakpoint trap.",
        "pid": 4242,
        "arch": "arm64",
        "thread": "main",
        "executable": "OneApp",
        "previous_session": False,
        "frames": [
            {
                "index": 0,
                "address": "0x102ab1000",
                "image": "OneApp",
                "image_uuid": UUID,
                "image_base": BASE,
            },
            {
                "index": 1,
                "address": "0x102ab2000",
                "image": "OneApp",
                "image_uuid": UUID,
                "image_base": BASE,
            },
        ],
        "images": [
            {"name": "OneApp", "path": "/tmp/OneApp.app/OneApp", "uuid": UUID, "base": BASE}
        ],
    }
    event.update(extra)
    return event


class FakeRunner:
    """Answers mdfind, dwarfdump, and atos the way the real tools do."""

    def __init__(self, dwarf: Path | None) -> None:
        self.dwarf = dwarf
        self.atos_calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> CommandResult:
        tool = argv[0]
        if tool == "mdfind":
            return CommandResult(argv, 0, "", "")
        if tool == "dwarfdump":
            if self.dwarf is None:
                return CommandResult(argv, 0, "", "")
            line = f"UUID: {UUID} (arm64) {self.dwarf}\n"
            return CommandResult(argv, 0, line, "")
        self.atos_calls.append(argv)
        addresses = argv[argv.index("|||") + 1 :]
        lines = [
            "static UIHierarchyCapture.screenshot(of: UIWindow) (in OneApp) "
            "(/src/UIHierarchyCapture.swift:63)|||"
            "Swift runtime failure: arithmetic overflow (in OneApp) "
            "(/src/UIHierarchyCapture.swift:63)"
            if address == "0x102ab1000"
            else "DebugBridgeService.run() (in OneApp) (/src/DebugBridgeService.swift:99)"
            for address in addresses
        ]
        return CommandResult(argv, 0, "\n".join(lines) + "\n", "")


def _symbolicator(tmp_path: Path, *, with_dsym: bool) -> tuple[Symbolicator, FakeRunner]:
    dsym = tmp_path / "OneApp.app.dSYM" / "Contents" / "Resources" / "DWARF"
    dsym.mkdir(parents=True)
    dwarf = dsym / "OneApp"
    dwarf.write_bytes(b"x")
    runner = FakeRunner(dwarf if with_dsym else None)
    return Symbolicator(runner=runner, search_dirs=lambda: [tmp_path]), runner


def test_parsers_read_tool_output() -> None:
    assert parse_uuid_lines(f"UUID: {UUID.lower()} (arm64) /x/OneApp\n")[0][:2] == (UUID, "arm64")
    rows = parse_atos_line(
        "Foo.bar(x: Int) (in App) (/s/Foo.swift:12)|||baz (in App) (/s/Baz.swift:3)"
    )
    assert [(row["file"], row["line"]) for row in rows] == [("Foo.swift", 12), ("Baz.swift", 3)]
    assert parse_atos_line("0x1234") == []
    assert class_of("static UIHierarchyCapture.screenshot(of: UIWindow)") == "UIHierarchyCapture"
    assert is_system_image("/usr/lib/libobjc.A.dylib")
    assert not is_system_image("/Users/me/Library/Developer/Xcode/DerivedData/x/OneApp.app/OneApp")


def test_symbolication_adds_file_line_and_expands_inlined_frames(tmp_path: Path) -> None:
    symbolicator, runner = _symbolicator(tmp_path, with_dsym=True)

    event = symbolicator.symbolicate_event(_sdk_event())

    stack = [(f.get("file"), f.get("line"), f.get("inlined")) for f in event["frames"]]
    assert stack == [
        ("UIHierarchyCapture.swift", 63, True),
        ("UIHierarchyCapture.swift", 63, False),
        ("DebugBridgeService.swift", 99, False),
    ]
    assert [f["index"] for f in event["frames"]] == [0, 1, 2]
    assert event["symbolication"]["status"] == "full"
    # Frame 0 is the exact pc. Return addresses are looked up one byte earlier.
    assert runner.atos_calls[0][-2:] == ["0x102ab1000", "0x102ab1fff"]


def test_missing_dsym_is_reported_not_hidden(tmp_path: Path) -> None:
    symbolicator, _ = _symbolicator(tmp_path, with_dsym=False)

    event = symbolicator.symbolicate_event(_sdk_event())

    assert event["symbolication"]["status"] == "missing_dsym"
    assert event["symbolication"]["missing_dsym"] == ["OneApp"]
    assert "file" not in event["frames"][0]


def test_sdk_crash_is_stored_published_and_marks_app_crashed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    seen: list[tuple[str, dict[str, Any]]] = []
    service.on_crash = lambda client_id, stored: seen.append((client_id, stored))

    result = service.ingest_event(
        "app-1", {"module": "crash", "kind": "fatal", "id": "dev-uuid", "payload": _sdk_event()}
    )

    assert result["id"] == "crash-app-1-4242"
    stored = service.list_events(client_id="app-1", module="crash")
    assert [row["id"] for row in stored] == ["crash-app-1-4242"]
    assert seen and seen[0][0] == "app-1"
    row = service.get_client("app-1")
    assert row["crashed"] is False  # still heartbeating, so not yet reported as down
    assert row["debugger_attached"] is True


def test_os_crash_report_replaces_and_is_not_replaced_by_sdk_record(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_crash("app-1", _sdk_event())

    report = _sdk_event(source="crash-report", reason="from ips", threads=[])
    service.record_crash("app-1", report)
    service.record_crash("app-1", _sdk_event())  # SDK record arrives late, after the OS report

    rows = service.list_events(client_id="app-1", module="crash")
    assert len(rows) == 1
    assert rows[0]["payload"]["source"] == "crash-report"
    assert rows[0]["payload"]["reason"] == "from ips"


def test_previous_session_crash_does_not_mark_running_app_crashed(tmp_path: Path) -> None:
    service = _service(tmp_path)

    service.record_crash("app-1", _sdk_event(previous_session=True))

    assert service.get_client("app-1")["crashed_at"] is None


def test_crashed_app_reports_crashed_once_it_goes_quiet(tmp_path: Path) -> None:
    clock = [1000.0]
    store = EncryptedSqliteStore(
        tmp_path / "t.db",
        SecretBoxCipher(b"k" * SecretBox.KEY_SIZE),
        retention=RetentionPolicy.SESSION,
    )
    service = DebugSessionService(store=store, events=EventBus(), time_fn=lambda: clock[0])
    service.register({"client_id": "a", "app_name": "A", "bundle_id": "b", "platform": "iOS"})
    service.record_crash("a", _sdk_event(timestamp=1000.0))
    clock[0] += 60

    row = service.get_client("a")

    assert row["state"] == "offline"
    assert row["crashed"] is True
    assert "crashed" in row["hint"]


def test_silent_app_with_debugger_gets_an_xcode_hint(tmp_path: Path) -> None:
    clock = [1000.0]
    store = EncryptedSqliteStore(
        tmp_path / "t.db",
        SecretBoxCipher(b"k" * SecretBox.KEY_SIZE),
        retention=RetentionPolicy.SESSION,
    )
    service = DebugSessionService(store=store, events=EventBus(), time_fn=lambda: clock[0])
    service.register(
        {
            "client_id": "a",
            "app_name": "A",
            "bundle_id": "b",
            "platform": "iOS",
            "debugger_attached": True,
        }
    )
    clock[0] += 12

    assert "Xcode" in service.get_client("a")["hint"]


def _ips(tmp_path: Path, *, proc: str = "OneApp", udid: str = "SIM-UDID-1") -> Path:
    header = {"app_name": proc, "bug_type": "309", "name": proc}
    body = {
        "captureTime": "2026-11-08 16:29:28.7676 +0530",
        "pid": 4242,
        "procName": proc,
        "procPath": f"/Users/x/CoreSimulator/Devices/{udid}/data/{proc}.app/{proc}",
        "bundleInfo": {
            "CFBundleIdentifier": "com.viewlift.oneapp" if proc == "OneApp" else "com.other"
        },
        "cpuType": "ARM-64",
        "exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"},
        "asi": {"libswiftCore.dylib": ["Fatal error: arithmetic overflow"]},
        "faultingThread": 0,
        "threads": [
            {
                "id": 1,
                "queue": "com.apple.main-thread",
                "triggered": True,
                "frames": [
                    {
                        "imageOffset": 4096,
                        "imageIndex": 0,
                        "symbol": "UIHierarchyCapture.screenshot",
                        "sourceFile": "/src/UIHierarchyCapture.swift",
                        "sourceLine": 63,
                    },
                    {"imageOffset": 20, "imageIndex": 1, "symbol": "start"},
                ],
            },
            {"id": 2, "name": "worker", "frames": [{"imageOffset": 8, "imageIndex": 1}]},
        ],
        "usedImages": [
            {
                "base": 4338000000,
                "uuid": UUID.lower(),
                "path": f"/x/{proc}.app/{proc}",
                "name": proc,
            },
            {
                "base": 6000000000,
                "uuid": "74e52480-c2bd-3c8d-812d-95fe2b74a096",
                "path": "/usr/lib/dyld",
            },
        ],
    }
    path = tmp_path / f"{proc}-2026-11-08-162929.ips"
    path.write_text(json.dumps(header) + "\n" + json.dumps(body), encoding="utf-8")
    return path


def test_ips_report_becomes_a_complete_event(tmp_path: Path) -> None:
    path = _ips(tmp_path)

    event = event_from_report(read_report(path) or {}, path)

    assert event["name"] == "EXC_BREAKPOINT (SIGTRAP)"
    assert event["reason"] == "Fatal error: arithmetic overflow"
    assert event["thread"] == "com.apple.main-thread (id 1)"
    assert event["frames"][0]["file"] == "UIHierarchyCapture.swift"
    assert event["frames"][0]["line"] == 63
    assert event["frames"][0]["image_uuid"] == UUID
    assert event["frames"][0]["address"] == hex(4338000000 + 4096)
    assert len(event["threads"]) == 1
    assert event["arch"] == "arm64"


def test_report_matches_the_simulator_that_ran_it(tmp_path: Path) -> None:
    path = _ips(tmp_path, udid="SIM-B")
    parsed = read_report(path) or {}
    clients = [
        {"id": "a", "bundle_id": "com.viewlift.oneapp", "executable": "OneApp",
         "device_key": "SIM-A", "is_simulator": True, "last_seen_at": "2026-01-02"},
        {"id": "b", "bundle_id": "com.viewlift.oneapp", "executable": "OneApp",
         "device_key": "SIM-B", "is_simulator": True, "last_seen_at": "2026-01-01"},
        {"id": "c", "bundle_id": "com.other", "executable": "Other", "last_seen_at": "2026-01-03"},
    ]  # fmt: skip

    assert match_client(clients, parsed["header"], parsed["report"]) == "b"


def test_watcher_delivers_new_reports_once_and_skips_other_apps(tmp_path: Path) -> None:
    service = _service(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    symbolicator, _ = _symbolicator(tmp_path / "dsym", with_dsym=False)
    pipeline = CrashPipeline(service, symbolicator)
    watcher = CrashReportWatcher(service, pipeline, reports_dir=reports, backlog_seconds=0)
    assert watcher.scan_once() == []

    _ips(reports)
    _ips(reports, proc="Unrelated")
    assert watcher.scan_once() == ["OneApp-2026-11-08-162929.ips"]
    assert watcher.scan_once() == []

    stored = service.list_events(client_id="app-1", module="crash")
    assert stored[0]["payload"]["source"] == "crash-report"


def test_crash_summary_gives_file_line_stack() -> None:
    event = _sdk_event(
        frames=[
            {"index": 0, "file": "UIHierarchyCapture.swift", "line": 63, "symbol": "screenshot",
             "image": "OneApp"},
            {"index": 1, "address": "0x1", "image": "UIKitCore"},
        ],
        symbolication={"status": "partial", "missing_dsym": ["Lib"]},
    )  # fmt: skip

    summary = insights.crash_summary([{"id": "x", "created_at": "2026", "payload": event}])

    crash = summary["crashes"][0]
    assert crash["first_app_frame"].startswith("0  UIHierarchyCapture.swift:63")
    assert len(crash["stack"]) == 2
    assert crash["symbolication"] == "partial"
