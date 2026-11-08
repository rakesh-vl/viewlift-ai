"""Host developer tools: validation, project roots, jobs, defaults, docs, previews, UI parsing."""

from __future__ import annotations

import base64
import json
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

from viewlift_ai.services.host import HostToolkit
from viewlift_ai.services.host.apple_docs import AppleDocs
from viewlift_ai.services.host.jobs import JobStore, condense_log
from viewlift_ai.services.host.lldb_session import _BLOCKED_COMMAND_RE
from viewlift_ai.services.host.previews import compare_png, decode_png, scan_previews
from viewlift_ai.services.host.runner import (
    ROOTS_ENV,
    CommandResult,
    HostError,
    ProjectRoots,
    check_bundle_id,
    check_extra_args,
    check_udid,
)
from viewlift_ai.services.host.session_defaults import SessionDefaults
from viewlift_ai.services.host.ui_driver import find_elements, normalize_axe, screen_map


def _png(width: int, height: int, pixel: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(pixel) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class FakeRunner:
    """Records argv lists and answers from a table keyed by the first few arguments."""

    def __init__(self, answers: dict[tuple[str, ...], str] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: Any) -> CommandResult:
        args = [str(item) for item in argv]
        self.calls.append(args)
        for prefix, stdout in self.answers.items():
            if tuple(args[: len(prefix)]) == prefix:
                return CommandResult(args, 0, stdout, "")
        return CommandResult(args, 1, "", "not faked")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv(ROOTS_ENV, str(root))
    return root


def test_extra_args_allow_settings_and_block_injection() -> None:
    assert check_extra_args(["-quiet", "SWIFT_VERSION=5.0", "-jobs", "4"]) == [
        "-quiet",
        "SWIFT_VERSION=5.0",
        "-jobs",
        "4",
    ]
    for bad in (
        "OTHER_SWIFT_FLAGS=-Xfrontend",
        "LD_RUNPATH_SEARCH_PATHS=/tmp",
        "CODE_SIGN_IDENTITY=x",
        "-exportArchive",
    ):
        with pytest.raises(HostError):
            check_extra_args([bad])
    with pytest.raises(HostError):
        check_extra_args(["SWIFT_VERSION=5\nrm"])


def test_identifier_validators() -> None:
    assert check_udid("C37A822A-0CE4-49B9-B17E-29583FDF1FFB")
    assert check_bundle_id("com.example.app")
    with pytest.raises(HostError):
        check_udid("booted; rm -rf /")
    with pytest.raises(HostError):
        check_bundle_id("com.example/app")


def test_project_roots_refuse_paths_outside_and_symlink_escape(
    project: Path, tmp_path: Path
) -> None:
    roots = ProjectRoots()
    inside = project / "App"
    inside.mkdir()
    assert roots.resolve(str(inside)) == inside.resolve()
    assert roots.resolve("App") == inside.resolve()
    outside = tmp_path / "secret"
    outside.mkdir()
    with pytest.raises(HostError):
        roots.resolve(str(outside))
    (project / "escape").symlink_to(outside)
    with pytest.raises(HostError):
        roots.resolve(str(project / "escape"))


def test_project_roots_refuse_home_and_persist(tmp_path: Path) -> None:
    store = tmp_path / "roots.json"
    roots = ProjectRoots(store)
    with pytest.raises(HostError):
        roots.set_saved(["/"])
    with pytest.raises(HostError):
        roots.set_saved([str(Path.home())])
    with pytest.raises(HostError):
        roots.set_saved(["relative/path"])
    folder = tmp_path / "work"
    folder.mkdir()
    roots.set_saved([str(folder)])
    assert ProjectRoots(store).status()["saved"] == [str(folder.resolve())]


def test_roots_required_before_any_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    with pytest.raises(HostError, match="No project roots"):
        ProjectRoots().resolve("/tmp")


def test_condense_log_keeps_errors_warnings_and_tests() -> None:
    log = "\n".join(
        [
            "CompileSwift normal arm64",
            "/a/B.swift:3:1: error: cannot find 'x' in scope",
            "/a/C.swift:9:2: warning: unused variable",
            "Test Case '-[T testA]' passed (0.01 seconds).",
            "Test Case '-[T testB]' failed (0.02 seconds).",
            "** BUILD FAILED **",
        ]
    )
    summary = condense_log(log)
    assert any("cannot find" in line for line in summary["errors"])
    assert any("unused variable" in line for line in summary["warnings"])
    assert summary["tests_passed"] == 1
    assert any("testB" in line for line in summary["test_failures"])


def test_job_store_runs_waits_and_reports(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")

    def body(ctx: Any) -> dict[str, Any]:
        ctx.log("hello from job")
        return {"ok": True, "value": 3}

    job = store.start("build", "demo", body)
    described = store.wait(job.id, 5)
    assert described["state"] == "succeeded"
    assert described["result"]["value"] == 3
    assert "hello from job" in store.describe(job.id, log_tail=5)["log_tail"]
    with pytest.raises(HostError):
        store.get("missing")


def test_job_store_records_host_errors(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")

    def body(_ctx: Any) -> dict[str, Any]:
        raise HostError("scheme not found")

    job = store.start("build", "demo", body, background=False)
    assert store.describe(job.id)["error"] == "scheme not found"


def test_job_cancel_stops_cooperative_body(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")

    def body(ctx: Any) -> dict[str, Any]:
        for _ in range(200):
            ctx.check_cancel()
            time.sleep(0.01)
        return {"ok": True}

    job = store.start("test", "slow", body)
    assert store.cancel(job.id)["state"] == "cancelled"


def test_session_defaults_profiles_and_merge(tmp_path: Path) -> None:
    path = tmp_path / "defaults.json"
    defaults = SessionDefaults(path)
    defaults.set({"scheme": "App", "simulatorName": "iPhone 17"})
    defaults.use_profile("tv", create=True)
    defaults.set({"scheme": "AppTV", "platform": "tvOS"})
    assert defaults.merged({})["scheme"] == "AppTV"
    assert defaults.merged({"scheme": "Other"})["scheme"] == "Other"
    reloaded = SessionDefaults(path)
    assert reloaded.merged({})["scheme"] == "AppTV"
    reloaded.use_profile("default")
    assert reloaded.merged({})["scheme"] == "App"


def test_toolkit_confirm_gate_unknown_tool_and_list_sims(tmp_path: Path) -> None:
    devices = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                {
                    "udid": "C37A822A-0CE4-49B9-B17E-29583FDF1FFB",
                    "name": "iPhone 17",
                    "state": "Booted",
                    "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17",
                }
            ]
        }
    }
    runner = FakeRunner({("xcrun", "simctl", "list"): json.dumps(devices)})
    toolkit = HostToolkit(tmp_path, runner=runner)
    assert toolkit.call("no_such_tool", {})["ok"] is False
    assert toolkit.is_mutating("erase_sims")
    assert toolkit.call("erase_sims", {"simulatorId": "x"}) == {
        "ok": False,
        "detail": "confirm must be true",
    }
    listed = toolkit.call("list_sims", {})
    assert listed["ok"] is True
    text = json.dumps(listed)
    assert "iPhone 17" in text
    assert "26.0" in text
    assert all(call[0] == "xcrun" for call in runner.calls)


def test_toolkit_turns_host_errors_into_details(tmp_path: Path) -> None:
    toolkit = HostToolkit(tmp_path, runner=FakeRunner())
    result = toolkit.call("boot_sim", {"simulatorId": "not a udid"}, confirm=True)
    assert result["ok"] is False
    assert result["detail"]


def test_lldb_blocks_commands_that_run_on_the_mac() -> None:
    for command in ("script print(1)", "platform shell ls", "shell ls", "process launch", "run"):
        assert _BLOCKED_COMMAND_RE.match(command), command
    for command in ("bt", "frame variable", "breakpoint set -n viewDidLoad", "po self.title"):
        assert not _BLOCKED_COMMAND_RE.match(command), command


def _docs(tmp_path: Path) -> tuple[AppleDocs, list[str]]:
    fetched: list[str] = []
    index = {
        "interfaceLanguages": {
            "swift": [
                {
                    "title": "SwiftUI",
                    "path": "/documentation/swiftui",
                    "type": "module",
                    "children": [
                        {
                            "title": "GridItem",
                            "path": "/documentation/swiftui/griditem",
                            "type": "struct",
                        },
                        {
                            "title": "LazyVGrid",
                            "path": "/documentation/swiftui/lazyvgrid",
                            "type": "struct",
                        },
                        {
                            "title": "Grids",
                            "path": "/documentation/swiftui/grids",
                            "type": "article",
                        },
                    ],
                }
            ]
        }
    }
    page = {
        "metadata": {
            "title": "GridItem",
            "roleHeading": "Structure",
            "platforms": [{"name": "iOS", "introducedAt": "14.0"}],
        },
        "abstract": [{"type": "text", "text": "A description of a row or a column."}],
        "primaryContentSections": [
            {
                "kind": "declarations",
                "declarations": [{"tokens": [{"text": "struct "}, {"text": "GridItem"}]}],
            }
        ],
        "references": {},
    }

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return json.dumps(index if "/index/" in url else page).encode()

    return AppleDocs(tmp_path / "docs", fetch), fetched


def test_apple_docs_search_ranks_exact_and_wildcard(tmp_path: Path) -> None:
    docs, fetched = _docs(tmp_path)
    result = docs.search({"query": "GridItem", "frameworks": ["swiftui"]})
    assert result["symbols"][0]["title"] == "GridItem"
    wildcard = docs.search({"query": "Lazy*", "frameworks": ["swiftui"]})
    assert [row["title"] for row in wildcard["symbols"]] == ["LazyVGrid"]
    assert len(fetched) == 1
    bad = docs.search({"query": "GridItem", "frameworks": ["../etc"]})
    assert bad["symbols"] == []
    assert bad["errors"][0]["framework"] == "../etc"


def test_apple_docs_get_reads_declaration_and_caches(tmp_path: Path) -> None:
    docs, fetched = _docs(tmp_path)
    page = docs.get({"path": "https://developer.apple.com/documentation/swiftui/griditem"})
    assert page["title"] == "GridItem"
    assert page["declaration"] == "struct GridItem"
    assert page["platforms"] == ["iOS 14.0"]
    fresh, fetched_again = _docs(tmp_path)
    fresh.get({"path": "/documentation/swiftui/griditem"})
    assert fetched_again == []
    with pytest.raises(HostError):
        docs.get({"path": "/etc/passwd"})


def test_scan_previews_finds_macros_and_providers(tmp_path: Path) -> None:
    source = tmp_path / "Views.swift"
    source.write_text(
        "\n".join(
            [
                'struct CardView: View { var body: some View { Text("x") } }',
                '#Preview("Card dark") {',
                "    CardView()",
                "}",
                "struct CardView_Previews: PreviewProvider {",
                "    static var previews: some View { CardView() }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    rows = scan_previews(tmp_path)
    names = {(row["kind"], row["name"]) for row in rows}
    assert ("#Preview", "Card dark") in names
    assert ("PreviewProvider", "CardView") in names


def test_png_decode_and_compare() -> None:
    red = _png(4, 3, (255, 0, 0))
    assert decode_png(red)[:3] == (4, 3, 3)
    assert compare_png(red, red)["match"] is True
    blue = _png(4, 3, (0, 0, 255))
    diff = compare_png(red, blue)
    assert diff["match"] is False
    assert diff["changed_pixels"] == 12
    assert compare_png(red, _png(5, 3, (255, 0, 0)))["reason"] == "size"


def test_preview_baselines_record_compare_and_approve(tmp_path: Path) -> None:
    toolkit = HostToolkit(tmp_path, runner=FakeRunner())
    red = base64.b64encode(_png(2, 2, (255, 0, 0))).decode()
    blue = base64.b64encode(_png(2, 2, (0, 0, 255))).decode()
    first = toolkit.call("preview_compare", {"name": "Card", "suite": "app-ios", "base64": red})
    assert first["status"] == "recorded"
    same = toolkit.call("preview_compare", {"name": "Card", "suite": "app-ios", "base64": red})
    assert same["status"] == "passed"
    changed = toolkit.call("preview_compare", {"name": "Card", "suite": "app-ios", "base64": blue})
    assert changed["status"] == "changed"
    assert toolkit.call("approve_preview_baseline", {"suite": "app-ios"})["ok"] is False
    approved = toolkit.call("approve_preview_baseline", {"suite": "app-ios"}, confirm=True)
    assert approved["approved"]
    after = toolkit.call("preview_compare", {"name": "Card", "suite": "app-ios", "base64": blue})
    assert after["status"] == "passed"


def test_axe_tree_normalizes_and_summarizes() -> None:
    nodes = [
        {
            "type": "Application",
            "AXLabel": "Demo",
            "frame": {"x": 0, "y": 0, "width": 390, "height": 844},
            "children": [
                {
                    "type": "Button",
                    "AXLabel": "Sign In",
                    "AXUniqueId": "login.submit",
                    "frame": {"x": 10, "y": 700, "width": 370, "height": 44},
                },
                {
                    "type": "TextField",
                    "AXLabel": "Email",
                    "frame": {"x": 10, "y": 200, "width": 370, "height": 44},
                },
                {
                    "type": "StaticText",
                    "AXLabel": "Welcome",
                    "frame": {"x": 10, "y": 100, "width": 370, "height": 20},
                },
            ],
        }
    ]
    elements = normalize_axe(nodes)
    assert {item["role"] for item in elements} >= {"button", "textfield", "statictext"}
    summary = json.dumps(screen_map(elements, screen="Login"))
    assert "Sign In" in summary
    assert "Email" in summary
    assert find_elements(elements, {"identifier": "login.submit"})[0]["label"] == "Sign In"
    assert find_elements(elements, {"text": "welc"})[0]["label"] == "Welcome"
    assert find_elements(elements, {"label": "sign"}) == []
