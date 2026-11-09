"""Find the Swift packages of the app paired through ViewliftNetworkCapture.

Nothing is configured by hand. The paired app reports its bundle id and
executable. This module finds the newest Xcode build of that app in
DerivedData and reads the package versions Xcode resolved for it.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DERIVED_DATA_ENV = "VIEWLIFT_DERIVED_DATA"
_SKIP_SUFFIX = ".noindex"
_MAX_PRODUCTS_PER_DIR = 40
_GOOGLE_SERVICE_FILE = "GoogleService-Info.plist"

DefaultsReader = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class AppIdentity:
    bundle_id: str = ""
    executable: str = ""
    app_name: str = ""

    def is_empty(self) -> bool:
        return not (self.bundle_id or self.executable or self.app_name)


@dataclass(frozen=True, slots=True)
class BuildMatch:
    derived_data: Path
    product: Path
    configuration: str
    bundle_id: str
    executable: str
    version: str
    build: str
    built_at: float
    matched_by: str


def derived_data_root(read_defaults: DefaultsReader | None = None) -> Path:
    """Env override, then Xcode's custom location, then Xcode's default."""
    env = os.environ.get(DERIVED_DATA_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    custom = (read_defaults or _xcode_custom_location)()
    if custom:
        path = Path(custom).expanduser()
        return path if path.is_absolute() else Path.home() / path
    return Path.home() / "Library" / "Developer" / "Xcode" / "DerivedData"


def detect_packages(
    identity: AppIdentity,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    base = root or derived_data_root()
    if identity.is_empty():
        return _miss(base, "No debug app is paired yet.")
    if not base.is_dir():
        return _miss(base, f"Xcode DerivedData not found at {base}.")
    match = find_build(identity, base)
    if match is None:
        target = identity.bundle_id or identity.executable or identity.app_name
        return _miss(
            base, f"No Xcode build of {target} found in DerivedData. Build it once in Xcode."
        )
    workspace = _workspace_path(match.derived_data)
    packages, source, source_path = _packages_for(match.derived_data, workspace)
    return {
        "ok": bool(packages),
        "error": None if packages else "The matched build has no resolved Swift packages.",
        "derived_data_root": str(base),
        "derived_data": str(match.derived_data),
        "workspace": str(workspace) if workspace else None,
        "source": source,
        "source_path": str(source_path) if source_path else None,
        "app": {
            "bundle_id": match.bundle_id,
            "executable": match.executable,
            "version": match.version,
            "build": match.build,
            "configuration": match.configuration,
            "product": str(match.product),
            "built_at": datetime.fromtimestamp(match.built_at, UTC).isoformat(),
            "matched_by": match.matched_by,
        },
        "count": len(packages),
        "packages": packages,
        "google_service_file": read_google_service_file(match.product, match.bundle_id),
    }


def find_build(identity: AppIdentity, root: Path) -> BuildMatch | None:
    best: BuildMatch | None = None
    for derived in _derived_dirs(root):
        for product in _app_products(derived):
            info = _read_plist(product / "Info.plist")
            if not info:
                continue
            matched_by = _match(identity, info)
            if not matched_by:
                continue
            candidate = BuildMatch(
                derived_data=derived,
                product=product,
                configuration=product.parent.name,
                bundle_id=str(info.get("CFBundleIdentifier") or ""),
                executable=str(info.get("CFBundleExecutable") or ""),
                version=str(info.get("CFBundleShortVersionString") or ""),
                build=str(info.get("CFBundleVersion") or ""),
                built_at=_mtime(product),
                matched_by=matched_by,
            )
            if best is None or _rank(candidate) > _rank(best):
                best = candidate
    return best


def parse_workspace_state(path: Path) -> list[dict[str, str]]:
    data = _read_json(path)
    obj = data.get("object") if isinstance(data, dict) else None
    deps = obj.get("dependencies") if isinstance(obj, dict) else None
    rows: list[dict[str, str]] = []
    for dep in deps if isinstance(deps, list) else []:
        if not isinstance(dep, dict):
            continue
        ref = _dict(dep.get("packageRef"))
        state = _dict(dep.get("state"))
        checkout = _dict(state.get("checkoutState"))
        rows.append(
            _package_row(
                identity=ref.get("identity"),
                name=ref.get("name"),
                location=ref.get("location"),
                kind=ref.get("kind"),
                version=checkout.get("version"),
                branch=checkout.get("branch"),
                revision=checkout.get("revision"),
                state=state.get("name"),
            )
        )
    return _sorted(rows)


def parse_package_resolved(path: Path) -> list[dict[str, str]]:
    data = _read_json(path)
    if not isinstance(data, dict):
        return []
    pins = data.get("pins")
    if not isinstance(pins, list):
        nested = data.get("object")
        pins = nested.get("pins") if isinstance(nested, dict) else []
    rows: list[dict[str, str]] = []
    for pin in pins if isinstance(pins, list) else []:
        if not isinstance(pin, dict):
            continue
        state = pin.get("state") if isinstance(pin.get("state"), dict) else {}
        rows.append(
            _package_row(
                identity=pin.get("identity") or pin.get("package"),
                name=pin.get("package"),
                location=pin.get("location") or pin.get("repositoryURL"),
                kind=pin.get("kind"),
                version=state.get("version"),
                branch=state.get("branch"),
                revision=state.get("revision"),
                state="pinned",
            )
        )
    return _sorted(rows)


def _packages_for(
    derived: Path, workspace: Path | None
) -> tuple[list[dict[str, str]], str | None, Path | None]:
    state = derived / "SourcePackages" / "workspace-state.json"
    if state.is_file():
        rows = parse_workspace_state(state)
        if rows:
            return rows, "workspace-state", state
    for resolved in _resolved_candidates(workspace):
        if resolved.is_file():
            rows = parse_package_resolved(resolved)
            if rows:
                return rows, "package-resolved", resolved
    return [], None, None


def _resolved_candidates(workspace: Path | None) -> Iterable[Path]:
    if workspace is None:
        return []
    shared = Path("xcshareddata") / "swiftpm" / "Package.resolved"
    if workspace.suffix == ".xcodeproj":
        return [workspace / "project.xcworkspace" / shared]
    return [workspace / shared]


def _workspace_path(derived: Path) -> Path | None:
    raw = _read_plist(derived / "info.plist").get("WorkspacePath")
    return Path(str(raw)) if raw else None


def _derived_dirs(root: Path) -> list[Path]:
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    return [item for item in entries if item.is_dir() and not item.name.endswith(_SKIP_SUFFIX)]


def _app_products(derived: Path) -> list[Path]:
    products = derived / "Build" / "Products"
    if not products.is_dir():
        return []
    try:
        apps = sorted(products.glob("*/*.app"), key=_mtime, reverse=True)
    except OSError:
        return []
    return apps[:_MAX_PRODUCTS_PER_DIR]


def _match(identity: AppIdentity, info: dict[str, Any]) -> str:
    bundle_id = str(info.get("CFBundleIdentifier") or "")
    executable = str(info.get("CFBundleExecutable") or "")
    names = {str(info.get(key) or "") for key in ("CFBundleDisplayName", "CFBundleName")}
    if identity.bundle_id and bundle_id == identity.bundle_id:
        return "bundle_id"
    if identity.executable and executable == identity.executable:
        return "executable"
    if identity.app_name and identity.app_name in names:
        return "app_name"
    return ""


_MATCH_WEIGHT = {"bundle_id": 3, "executable": 2, "app_name": 1}


def _rank(match: BuildMatch) -> tuple[int, float]:
    return (_MATCH_WEIGHT.get(match.matched_by, 0), match.built_at)


def _package_row(**fields: object) -> dict[str, str]:
    revision = str(fields.get("revision") or "")
    version = str(fields.get("version") or "")
    branch = str(fields.get("branch") or "")
    return {
        "identity": str(fields.get("identity") or ""),
        "name": str(fields.get("name") or ""),
        "version": version or (f"branch {branch}" if branch else revision[:12]),
        "branch": branch,
        "revision": revision[:12],
        "location": str(fields.get("location") or ""),
        "kind": str(fields.get("kind") or ""),
        "state": str(fields.get("state") or ""),
    }


def _sorted(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted((row for row in rows if row["identity"]), key=lambda row: row["identity"].lower())


def read_google_service_file(product: Path, app_bundle_id: str = "") -> dict[str, Any]:
    """GoogleService-Info.plist from a built .app. The dashboard shows every value."""
    path = product / _GOOGLE_SERVICE_FILE
    if not path.is_file():
        return {
            "present": False,
            "file": _GOOGLE_SERVICE_FILE,
            "looked_in": str(product),
            "note": f"{_GOOGLE_SERVICE_FILE} is not in the built app.",
        }
    raw = _read_plist(path)
    if not raw:
        return {
            "present": False,
            "file": _GOOGLE_SERVICE_FILE,
            "path": str(path),
            "note": f"{_GOOGLE_SERVICE_FILE} could not be read.",
        }
    plist_bundle = str(raw.get("BUNDLE_ID") or "")
    app_bundle = app_bundle_id or ""
    mismatch = bool(plist_bundle and app_bundle and plist_bundle != app_bundle)
    row: dict[str, Any] = {
        "present": True,
        "file": _GOOGLE_SERVICE_FILE,
        "bundle_id": plist_bundle,
        "app_bundle_id": app_bundle,
        "bundle_id_mismatch": mismatch,
    }
    used = {"BUNDLE_ID"}
    for source, name in (
        ("PROJECT_ID", "project_id"),
        ("GOOGLE_APP_ID", "google_app_id"),
        ("GCM_SENDER_ID", "gcm_sender_id"),
        ("STORAGE_BUCKET", "storage_bucket"),
        ("DATABASE_URL", "database_url"),
        ("TRACKING_ID", "tracking_id"),
        ("CLIENT_ID", "client_id"),
        ("REVERSED_CLIENT_ID", "reversed_client_id"),
        ("PLIST_VERSION", "plist_version"),
    ):
        used.add(source)
        value = _plist_scalar(raw.get(source))
        if value not in (None, ""):
            row[name] = value
    for source, name in (
        ("IS_ADS_ENABLED", "ads_enabled"),
        ("IS_ANALYTICS_ENABLED", "analytics_enabled"),
        ("IS_APPINVITE_ENABLED", "app_invite_enabled"),
        ("IS_GCM_ENABLED", "gcm_enabled"),
        ("IS_SIGNIN_ENABLED", "signin_enabled"),
    ):
        used.add(source)
        if source in raw:
            row[name] = bool(raw.get(source))
    for key, value in raw.items():
        if key in used:
            continue
        name = str(key).strip().lower()
        if not name:
            continue
        scalar = _plist_scalar(value)
        if scalar not in (None, ""):
            row[name] = scalar
    if mismatch:
        row["note"] = (
            "BUNDLE_ID in GoogleService-Info.plist does not match the app. "
            "Firebase stays off until they match."
        )
    row["path"] = str(path)
    return row


def _plist_scalar(value: object) -> object | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return value
    return None


def _miss(base: Path, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "derived_data_root": str(base),
        "count": 0,
        "packages": [],
        "google_service_file": {
            "present": False,
            "file": _GOOGLE_SERVICE_FILE,
            "note": error,
        },
    }


def _read_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _xcode_custom_location() -> str | None:
    try:
        completed = subprocess.run(
            ["defaults", "read", "com.apple.dt.Xcode", "IDECustomDerivedDataLocation"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or "").strip()
    return value if completed.returncode == 0 and value else None
