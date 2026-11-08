"""Project discovery, schemes, build settings, and simulator build, run, and test via xcodebuild."""

from __future__ import annotations

import os
import plistlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai import defaults
from viewlift_ai.services.host import simctl
from viewlift_ai.services.host.jobs import JobContext, condense_log
from viewlift_ai.services.host.runner import (
    HostError,
    check_extra_args,
    check_launch_args,
    check_name,
)

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_SKIP_DIRS = frozenset(
    {".git", ".build", "DerivedData", "node_modules", "Pods", "Carthage", ".swiftpm", "build"}
)
_SIM_PLATFORM = {
    "ios": "iOS Simulator",
    "tvos": "tvOS Simulator",
    "watchos": "watchOS Simulator",
    "visionos": "visionOS Simulator",
}
_DEVICE_PLATFORM = {"ios": "iOS", "tvos": "tvOS", "watchos": "watchOS", "visionos": "visionOS"}
MAX_WAIT = defaults.JOB_WAIT_SECONDS


def project_args(ctx: HostContext, args: dict[str, Any]) -> tuple[list[str], Path]:
    merged = ctx.defaults.merged(args)
    workspace = str(merged.get("workspacePath") or "")
    project = str(merged.get("projectPath") or "")
    if workspace and project:
        raise HostError("projectPath and workspacePath are mutually exclusive.")
    if workspace:
        path = ctx.roots.resolve(workspace)
        if path.suffix != ".xcworkspace":
            raise HostError("workspacePath must end in .xcworkspace.")
        return ["-workspace", str(path)], path.parent
    if project:
        path = ctx.roots.resolve(project)
        if path.suffix != ".xcodeproj":
            raise HostError("projectPath must end in .xcodeproj.")
        return ["-project", str(path)], path.parent
    raise HostError("Pass projectPath or workspacePath, or set one with session_set_defaults.")


def scheme_arg(ctx: HostContext, args: dict[str, Any]) -> str:
    scheme = str(ctx.defaults.merged(args).get("scheme") or "")
    if not scheme:
        raise HostError("scheme is required. Call list_schemes.")
    return check_name(scheme, "scheme")


def configuration_arg(ctx: HostContext, args: dict[str, Any]) -> str:
    return check_name(
        str(ctx.defaults.merged(args).get("configuration") or "Debug"), "configuration"
    )


def derived_data_args(ctx: HostContext, args: dict[str, Any]) -> list[str]:
    raw = str(ctx.defaults.merged(args).get("derivedDataPath") or "")
    if not raw:
        return []
    path = Path(raw).expanduser()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    return ["-derivedDataPath", str(ctx.roots.resolve(str(path)))]


def sim_destination(sim: dict[str, Any]) -> str:
    platform = _SIM_PLATFORM.get(str(sim.get("platform", "")).lower(), "iOS Simulator")
    return f"platform={platform},id={sim['udid']}"


def device_destination(ctx: HostContext, args: dict[str, Any], udid: str) -> str:
    platform = str(ctx.defaults.merged(args).get("platform") or "ios").lower()
    return f"platform={_DEVICE_PLATFORM.get(platform, 'iOS')},id={udid}"


def xcodebuild_argv(
    ctx: HostContext, args: dict[str, Any], action: list[str], destination: str
) -> tuple[list[str], Path]:
    proj, cwd = project_args(ctx, args)
    argv = [
        "xcodebuild",
        *proj,
        "-scheme",
        scheme_arg(ctx, args),
        "-configuration",
        configuration_arg(ctx, args),
        "-destination",
        destination,
        "-skipMacroValidation",
        *derived_data_args(ctx, args),
        *check_extra_args(args.get("extraArgs")),
        *action,
    ]
    return argv, cwd


def app_product(ctx: HostContext, args: dict[str, Any], destination: str) -> dict[str, Any]:
    """Locate the built .app and its bundle id from build settings."""
    proj, cwd = project_args(ctx, args)
    argv = [
        "xcodebuild",
        "-showBuildSettings",
        "-json",
        *proj,
        "-scheme",
        scheme_arg(ctx, args),
        "-configuration",
        configuration_arg(ctx, args),
        "-destination",
        destination,
        *derived_data_args(ctx, args),
    ]
    data = ctx.run_json(argv, cwd=cwd, timeout=180)
    scheme = scheme_arg(ctx, args)
    candidates = []
    for entry in data if isinstance(data, list) else []:
        settings = entry.get("buildSettings") if isinstance(entry, dict) else None
        if not isinstance(settings, dict) or settings.get("WRAPPER_EXTENSION") != "app":
            continue
        candidates.append((entry.get("target") == scheme, settings))
    if not candidates:
        raise HostError("No application target found for this scheme.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    settings = candidates[0][1]
    path = Path(settings.get("TARGET_BUILD_DIR", "")) / settings.get("FULL_PRODUCT_NAME", "")
    return {
        "appPath": str(path),
        "bundleId": settings.get("PRODUCT_BUNDLE_IDENTIFIER", ""),
        "productName": settings.get("PRODUCT_NAME", ""),
        "exists": path.exists(),
    }


def start_build_job(
    ctx: HostContext,
    args: dict[str, Any],
    *,
    kind: str,
    title: str,
    action: list[str],
    destination: str,
    after: Any = None,
) -> dict[str, Any]:
    argv, cwd = xcodebuild_argv(ctx, args, action, destination)

    def body(job: JobContext) -> dict[str, Any]:
        code, output = job.run(argv, cwd=cwd)
        summary = condense_log(output)
        result: dict[str, Any] = {"ok": code == 0, "exit_code": code, "summary": summary}
        if code == 0 and after is not None:
            result.update(after(job))
        return result

    job = ctx.jobs.start(kind, title, body)
    wait = max(0.0, min(float(args.get("waitSeconds", 20)), MAX_WAIT))
    job.done.wait(wait)
    out = ctx.jobs.describe(job.id)
    if job.state == "running":
        out["next"] = "Call wait_job with this job_id until state is not running."
    return out


def discover(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("workspaceRoot") or "")
    bases = [ctx.roots.resolve(raw)] if raw else ctx.roots.roots()
    if not bases:
        ctx.roots.resolve("")
    max_depth = max(1, min(int(args.get("maxDepth", 5)), 8))
    projects: list[str] = []
    workspaces: list[str] = []
    packages: list[str] = []
    for base in bases:
        base_depth = len(base.parts)
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            depth = len(current.parts) - base_depth
            keep = []
            for name in dirnames:
                if name.endswith(".xcodeproj"):
                    projects.append(str(current / name))
                elif name.endswith(".xcworkspace"):
                    workspaces.append(str(current / name))
                elif name not in _SKIP_DIRS and not name.startswith(".") and depth < max_depth:
                    keep.append(name)
            dirnames[:] = keep
            if "Package.swift" in filenames:
                packages.append(str(current))
            if len(projects) + len(workspaces) + len(packages) > 400:
                break
    return {
        "projects": sorted(projects),
        "workspaces": sorted(workspaces),
        "packages": sorted(packages),
    }


def list_schemes(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    proj, cwd = project_args(ctx, args)
    data = ctx.run_json(["xcodebuild", "-list", "-json", *proj], cwd=cwd, timeout=120)
    body = (data.get("workspace") or data.get("project") or {}) if isinstance(data, dict) else {}
    return {
        "schemes": body.get("schemes", []),
        "targets": body.get("targets", []),
        "configurations": body.get("configurations", []),
    }


_KEY_SETTINGS = (
    "PRODUCT_BUNDLE_IDENTIFIER",
    "PRODUCT_NAME",
    "MARKETING_VERSION",
    "CURRENT_PROJECT_VERSION",
    "SDKROOT",
    "SUPPORTED_PLATFORMS",
    "IPHONEOS_DEPLOYMENT_TARGET",
    "TVOS_DEPLOYMENT_TARGET",
    "MACOSX_DEPLOYMENT_TARGET",
    "SWIFT_VERSION",
    "TARGET_BUILD_DIR",
    "FULL_PRODUCT_NAME",
    "INFOPLIST_FILE",
    "CODE_SIGN_STYLE",
    "SWIFT_ACTIVE_COMPILATION_CONDITIONS",
    "BUILD_DIR",
)


def show_build_settings(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    proj, cwd = project_args(ctx, args)
    argv = [
        "xcodebuild",
        "-showBuildSettings",
        "-json",
        *proj,
        "-scheme",
        scheme_arg(ctx, args),
        "-configuration",
        configuration_arg(ctx, args),
    ]
    data = ctx.run_json(argv, cwd=cwd, timeout=180)
    wanted = [str(key) for key in args.get("keys") or []] or list(_KEY_SETTINGS)
    full = bool(args.get("all"))
    rows = []
    for entry in data if isinstance(data, list) else []:
        settings = entry.get("buildSettings", {}) if isinstance(entry, dict) else {}
        picked = settings if full else {key: settings[key] for key in wanted if key in settings}
        rows.append({"target": entry.get("target"), "settings": picked})
    return {"targets": rows}


def bundle_id_from_app(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = ctx.resolve_artifact(str(args.get("appPath", "")))
    for plist in (path / "Info.plist", path / "Contents" / "Info.plist"):
        if plist.is_file():
            try:
                data = plistlib.loads(plist.read_bytes())
            except (plistlib.InvalidFileException, ValueError) as exc:
                raise HostError("Info.plist could not be read.") from exc
            return {
                "bundleId": data.get("CFBundleIdentifier", ""),
                "version": data.get("CFBundleShortVersionString", ""),
                "build": data.get("CFBundleVersion", ""),
                "executable": data.get("CFBundleExecutable", ""),
            }
    raise HostError("No Info.plist inside appPath.")


def clean(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    proj, cwd = project_args(ctx, args)
    argv = [
        "xcodebuild",
        *proj,
        "-scheme",
        scheme_arg(ctx, args),
        "-configuration",
        configuration_arg(ctx, args),
        *derived_data_args(ctx, args),
        "clean",
    ]
    result = ctx.run(argv, cwd=cwd, timeout=300)
    return {"ok": result.ok, "summary": condense_log(result.stdout + result.stderr)}


def build_sim(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = simctl.resolve_simulator(ctx, args)
    return start_build_job(
        ctx,
        args,
        kind="build_sim",
        title=f"Build {scheme_arg(ctx, args)} for {sim['name']}",
        action=["build"],
        destination=sim_destination(sim),
    )


def build_run_sim(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = simctl.resolve_simulator(ctx, args)
    destination = sim_destination(sim)
    launch_args = check_launch_args(args.get("launchArgs"))
    env = args.get("env") if isinstance(args.get("env"), dict) else None

    def after(job: JobContext) -> dict[str, Any]:
        job.log("Locating the built app")
        product = app_product(ctx, args, destination)
        if sim["state"] != "Booted":
            job.log(f"Booting {sim['name']}")
            simctl.boot(ctx, sim["udid"])
        ctx.run(["open", "-a", "Simulator"], timeout=20)
        job.log(f"Installing {product['appPath']}")
        ctx.run_ok(["xcrun", "simctl", "install", sim["udid"], product["appPath"]], timeout=300)
        job.log(f"Launching {product['bundleId']}")
        launched = simctl.launch(ctx, sim["udid"], product["bundleId"], launch_args, env)
        return {"product": product, "launched": launched}

    return start_build_job(
        ctx,
        args,
        kind="build_run_sim",
        title=f"Build and run {scheme_arg(ctx, args)} on {sim['name']}",
        action=["build"],
        destination=destination,
        after=after,
    )


def result_bundle_path(ctx: HostContext) -> Path:
    path = ctx.work_dir / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{uuid.uuid4().hex[:12]}.xcresult"


def start_test_job(
    ctx: HostContext, args: dict[str, Any], destination: str, label: str
) -> dict[str, Any]:
    bundle = result_bundle_path(ctx)
    action = ["-resultBundlePath", str(bundle)]
    if args.get("coverage", True):
        action += ["-enableCodeCoverage", "YES"]
    action.append("test")
    out = start_build_job(
        ctx,
        args,
        kind="test",
        title=f"Test {scheme_arg(ctx, args)} on {label}",
        action=action,
        destination=destination,
    )
    out["xcresult"] = str(bundle)
    return out


def test_sim(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = simctl.resolve_simulator(ctx, args)
    return start_test_job(ctx, args, sim_destination(sim), sim["name"])


def get_sim_app_path(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = simctl.resolve_simulator(ctx, args)
    return app_product(ctx, args, sim_destination(sim))


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "discover_projs": (discover, False),
        "list_schemes": (list_schemes, False),
        "show_build_settings": (show_build_settings, False),
        "get_app_bundle_id": (bundle_id_from_app, False),
        "get_mac_bundle_id": (bundle_id_from_app, False),
        "clean": (clean, True),
        "build_sim": (build_sim, True),
        "build_run_sim": (build_run_sim, True),
        "test_sim": (test_sim, True),
        "get_sim_app_path": (get_sim_app_path, False),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
