"""Swift package auto-detection from a fake Xcode DerivedData tree."""

from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path

from viewlift_ai.services.package_detect import (
    AppIdentity,
    derived_data_root,
    detect_packages,
)


def _make_build(
    root: Path,
    folder: str,
    *,
    bundle_id: str,
    executable: str,
    workspace: Path,
    mtime: float,
) -> Path:
    derived = root / folder
    app = derived / "Build" / "Products" / "Debug-iphonesimulator" / f"{executable}.app"
    app.mkdir(parents=True)
    with (app / "Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleExecutable": executable,
                "CFBundleShortVersionString": "2.4",
                "CFBundleVersion": "77",
            },
            handle,
        )
    with (derived / "info.plist").open("wb") as handle:
        plistlib.dump({"WorkspacePath": str(workspace)}, handle)
    os.utime(app, (mtime, mtime))
    return derived


def _write_workspace_state(derived: Path) -> None:
    state = derived / "SourcePackages" / "workspace-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "object": {
                    "dependencies": [
                        {
                            "packageRef": {
                                "identity": "alamofire",
                                "name": "Alamofire",
                                "location": "https://github.com/Alamofire/Alamofire.git",
                                "kind": "remoteSourceControl",
                            },
                            "state": {
                                "name": "sourceControlCheckout",
                                "checkoutState": {"version": "5.9.1", "revision": "f455c29758"},
                            },
                        },
                        {
                            "packageRef": {"identity": "viewlift-ai", "name": "viewlift-ai"},
                            "state": {
                                "checkoutState": {"branch": "main", "revision": "abcdef1234"},
                            },
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_detects_packages_from_workspace_state(tmp_path: Path) -> None:
    derived = _make_build(
        tmp_path,
        "Demo-abc",
        bundle_id="com.example.demo",
        executable="Demo",
        workspace=tmp_path / "Demo.xcworkspace",
        mtime=1_700_000_000,
    )
    _write_workspace_state(derived)

    result = detect_packages(AppIdentity(bundle_id="com.example.demo"), root=tmp_path)

    assert result["ok"] is True
    assert result["source"] == "workspace-state"
    assert result["app"]["matched_by"] == "bundle_id"
    assert result["app"]["version"] == "2.4"
    versions = {row["identity"]: row["version"] for row in result["packages"]}
    assert versions == {"alamofire": "5.9.1", "viewlift-ai": "branch main"}


def test_falls_back_to_package_resolved(tmp_path: Path) -> None:
    workspace = tmp_path / "src" / "Demo.xcworkspace"
    resolved = workspace / "xcshareddata" / "swiftpm" / "Package.resolved"
    resolved.parent.mkdir(parents=True)
    resolved.write_text(
        json.dumps({"pins": [{"identity": "kingfisher", "state": {"version": "7.0.0"}}]}),
        encoding="utf-8",
    )
    _make_build(
        tmp_path / "dd",
        "Demo-abc",
        bundle_id="com.example.demo",
        executable="Demo",
        workspace=workspace,
        mtime=1_700_000_000,
    )

    result = detect_packages(AppIdentity(executable="Demo"), root=tmp_path / "dd")

    assert result["source"] == "package-resolved"
    assert result["app"]["matched_by"] == "executable"
    assert result["packages"][0]["version"] == "7.0.0"


def test_prefers_newest_matching_build(tmp_path: Path) -> None:
    old = _make_build(
        tmp_path, "Old", bundle_id="com.example.demo", executable="Demo",
        workspace=tmp_path / "old.xcworkspace", mtime=1_600_000_000,
    )
    new = _make_build(
        tmp_path, "New", bundle_id="com.example.demo", executable="Demo",
        workspace=tmp_path / "new.xcworkspace", mtime=1_700_000_000,
    )
    _write_workspace_state(old)
    _write_workspace_state(new)

    result = detect_packages(AppIdentity(bundle_id="com.example.demo"), root=tmp_path)

    assert result["derived_data"] == str(new)


def test_reports_missing_pairing_and_build(tmp_path: Path) -> None:
    assert detect_packages(AppIdentity(), root=tmp_path)["error"] == "No debug app is paired yet."
    missing = detect_packages(AppIdentity(bundle_id="com.example.none"), root=tmp_path)
    assert missing["ok"] is False
    assert "com.example.none" in missing["error"]


def test_reads_google_service_file(tmp_path: Path) -> None:
    derived = _make_build(
        tmp_path,
        "Demo-abc",
        bundle_id="com.example.demo",
        executable="Demo",
        workspace=tmp_path / "Demo.xcworkspace",
        mtime=1_700_000_000,
    )
    secret = "AIzaSyTESTKEY_not_real"
    app = derived / "Build" / "Products" / "Debug-iphonesimulator" / "Demo.app"
    with (app / "GoogleService-Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "API_KEY": secret,
                "BUNDLE_ID": "com.other.app",
                "PROJECT_ID": "vl-demo",
                "GOOGLE_APP_ID": "1:1:ios:abc",
                "GCM_SENDER_ID": "123",
                "STORAGE_BUCKET": "vl-demo.appspot.com",
                "IS_ANALYTICS_ENABLED": False,
                "IS_GCM_ENABLED": True,
                "CLIENT_ID": "123-abc.apps.googleusercontent.com",
            },
            handle,
        )

    result = detect_packages(AppIdentity(bundle_id="com.example.demo"), root=tmp_path)
    info = result["google_service_file"]

    assert info["present"] is True
    assert info["api_key"] == secret
    assert info["project_id"] == "vl-demo"
    assert info["bundle_id"] == "com.other.app"
    assert info["app_bundle_id"] == "com.example.demo"
    assert info["bundle_id_mismatch"] is True
    assert info["analytics_enabled"] is False
    assert info["gcm_enabled"] is True
    assert info["client_id"] == "123-abc.apps.googleusercontent.com"
    assert "Firebase stays off" in info["note"]


def test_reports_missing_google_service_file(tmp_path: Path) -> None:
    _make_build(
        tmp_path,
        "Demo-abc",
        bundle_id="com.example.demo",
        executable="Demo",
        workspace=tmp_path / "Demo.xcworkspace",
        mtime=1_700_000_000,
    )

    result = detect_packages(AppIdentity(bundle_id="com.example.demo"), root=tmp_path)

    assert result["google_service_file"]["present"] is False
    assert "GoogleService-Info.plist" in result["google_service_file"]["note"]
    missing = detect_packages(AppIdentity(bundle_id="com.example.none"), root=tmp_path)
    assert missing["google_service_file"]["present"] is False


def test_derived_data_root_order(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIEWLIFT_DERIVED_DATA", str(tmp_path))
    assert derived_data_root(lambda: "/elsewhere") == tmp_path
    monkeypatch.delenv("VIEWLIFT_DERIVED_DATA")
    assert derived_data_root(lambda: "/custom/dd") == Path("/custom/dd")
    assert derived_data_root(lambda: None).name == "DerivedData"
