"""XCUITest runner that drives any app on a simulator or device, iOS or tvOS.

The runner (`ios/ViewliftUIRunner`) is a long-running UI test. It connects out to the SDK
receiver with the debug key and the pinned certificate, registers as a debug client, and
serves the same `ui.*` commands as the SDK. That covers touches the SDK cannot make from
inside the app (drags, raw touches, hardware buttons, system alerts) and apps without the
SDK. The build is cached per platform and signing settings; later starts only launch it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host import target as targets
from viewlift_ai.services.host.runner import HostError

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.jobs import Job, JobContext
    from viewlift_ai.services.host.toolkit import HostToolkit

PROJECT_ENV = "VIEWLIFT_UI_RUNNER_PROJECT"
TEAM_ENV = "VIEWLIFT_DEVELOPMENT_TEAM"
BUNDLE_ENV = "VIEWLIFT_UI_RUNNER_BUNDLE_ID"
SCHEME = "ViewliftUIRunner"
ENV_PREFIX = "TEST_RUNNER_"
BUILD_TIMEOUT = 1800.0
_SDKS = {
    ("simulator", "tvos"): "appletvsimulator",
    ("device", "tvos"): "appletvos",
    ("simulator", "ios"): "iphonesimulator",
    ("device", "ios"): "iphoneos",
}
_RUNNING: dict[str, dict[str, Any]] = {}


def project_path() -> Path:
    override = (os.environ.get(PROJECT_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[4]
        / "ios"
        / "ViewliftUIRunner"
        / "ViewliftUIRunner.xcodeproj"
    )


def client_id_for(udid: str, bundle_id: str) -> str:
    digest = hashlib.sha256(f"{udid}|{bundle_id}".encode()).hexdigest()[:16]
    return f"runner-{digest}"


def sdk_for(target: dict[str, Any]) -> str:
    platform = "tvos" if target.get("platform") == "tvos" else "ios"
    if target.get("platform") not in {"ios", "ipados", "tvos"}:
        raise HostError("The UI runner supports iPhone, iPad, and Apple TV targets.")
    return _SDKS[(str(target.get("kind")), platform)]


def signing(ctx: HostContext, args: dict[str, Any], *, device: bool) -> dict[str, str]:
    """Team and bundle id for device builds. Simulators build unsigned-friendly defaults."""
    merged = ctx.defaults.merged(args)
    team = str(merged.get("developmentTeam") or os.environ.get(TEAM_ENV) or "").strip()
    bundle = str(args.get("runnerBundleId") or os.environ.get(BUNDLE_ENV) or "").strip()
    if device and not team:
        raise HostError(
            "A device build needs a signing team. Pass developmentTeam, save it with "
            f"session_set_defaults, or set {TEAM_ENV}."
        )
    settings = {}
    if team:
        settings["DEVELOPMENT_TEAM"] = team
    if bundle:
        settings["PRODUCT_BUNDLE_IDENTIFIER"] = bundle
    return settings


def _sources_stamp(project: Path) -> float:
    root = project.parent
    files = [project / "project.pbxproj", *sorted((root / "UITests").glob("*.swift"))]
    return max((path.stat().st_mtime for path in files if path.is_file()), default=0.0)


def build_dir(ctx: HostContext, sdk: str, settings: dict[str, str]) -> Path:
    key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:10]
    return ctx.work_dir / "ui-runner" / f"{sdk}-{key}"


def find_xctestrun(folder: Path, sdk: str) -> Path | None:
    products = folder / "Build" / "Products"
    found = sorted(products.glob(f"*_{sdk}*.xctestrun")) if products.is_dir() else []
    return found[-1] if found else None


def build_argv(
    project: Path, target: dict[str, Any], folder: Path, settings: dict[str, str]
) -> list[str]:
    argv = [
        "xcodebuild",
        "build-for-testing",
        "-project",
        str(project),
        "-scheme",
        SCHEME,
        "-destination",
        f"id={target['udid']}",
        "-derivedDataPath",
        str(folder),
    ]
    if target.get("kind") == targets.DEVICE:
        argv.append("-allowProvisioningUpdates")
    argv.extend(f"{key}={value}" for key, value in sorted(settings.items()))
    return argv


def test_argv(xctestrun: Path, target: dict[str, Any]) -> list[str]:
    return [
        "xcodebuild",
        "test-without-building",
        "-xctestrun",
        str(xctestrun),
        "-destination",
        f"id={target['udid']}",
    ]


def runner_env(
    receiver: dict[str, Any], *, bundle_id: str, client_id: str, udid: str
) -> dict[str, str]:
    values = {
        "VIEWLIFT_RUNNER_URL": str(receiver.get("url") or ""),
        "VIEWLIFT_RUNNER_FINGERPRINT": str(receiver.get("fingerprint") or ""),
        "VIEWLIFT_RUNNER_DEBUG_KEY": str(receiver.get("debug_key") or ""),
        "VIEWLIFT_RUNNER_BUNDLE_ID": bundle_id,
        "VIEWLIFT_RUNNER_CLIENT_ID": client_id,
        "VIEWLIFT_RUNNER_DEVICE_KEY": f"runner:{udid}",
    }
    if not all(values.values()):
        raise HostError("Enable the SDK receiver on the Setup page before starting the UI runner.")
    return {f"{ENV_PREFIX}{key}": value for key, value in values.items()}


def _receiver(ctx: HostContext) -> dict[str, Any]:
    return ctx.receiver() if ctx.receiver is not None else {}


def start(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    target = targets.resolve(ctx, args)
    bundle_id = str(args.get("bundleId") or target.get("bundle_id") or "").strip()
    if not bundle_id:
        raise HostError("bundleId is required: the app the runner drives.")
    udid = str(target["udid"])
    if udid in _RUNNING and not _RUNNING[udid]["job"].done.is_set():
        running = _RUNNING[udid]
        return {"already_running": True, **{k: v for k, v in running.items() if k != "job"}}
    project = project_path()
    if not (project / "project.pbxproj").is_file():
        raise HostError(f"UI runner project not found at {project}. Set {PROJECT_ENV}.")
    sdk = sdk_for(target)
    settings = signing(ctx, args, device=target.get("kind") == targets.DEVICE)
    folder = build_dir(ctx, sdk, settings)
    client_id = client_id_for(udid, bundle_id)
    env = runner_env(_receiver(ctx), bundle_id=bundle_id, client_id=client_id, udid=udid)
    rebuild = bool(args.get("rebuild"))

    def body(job_ctx: JobContext) -> dict[str, Any]:
        xctestrun = find_xctestrun(folder, sdk)
        stale = xctestrun is None or xctestrun.stat().st_mtime < _sources_stamp(project)
        if rebuild or stale:
            code, _ = job_ctx.run(
                build_argv(project, target, folder, settings), timeout=BUILD_TIMEOUT
            )
            if code != 0:
                raise HostError("The UI runner build failed. See the job log (signing or SDK).")
            xctestrun = find_xctestrun(folder, sdk)
        if xctestrun is None:
            raise HostError("The UI runner build produced no .xctestrun file.")
        code, _ = job_ctx.run(test_argv(xctestrun, target), timeout=24 * 3600.0, env=env)
        return {"ok": code == 0, "exit_code": code}

    job: Job = ctx.jobs.start("ui_runner", f"UI runner on {target.get('name') or udid}", body)
    info = {
        "client_id": client_id,
        "job_id": job.id,
        "udid": udid,
        "kind": target.get("kind"),
        "platform": target.get("platform"),
        "bundle_id": bundle_id,
    }
    _RUNNING[udid] = {**info, "job": job}
    return {
        **info,
        "next": "Poll wait_job until list_apps shows client_id, then pass it to UI tools.",
    }


def stop(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    udid = str(args.get("udid") or args.get("deviceId") or args.get("simulatorId") or "").strip()
    if not udid:
        udid = str(targets.resolve(ctx, args)["udid"])
    running = _RUNNING.pop(udid, None)
    if running is None:
        raise HostError("No UI runner is running on this target.")
    ctx.jobs.cancel(running["job"].id)
    return {"udid": udid, "stopped": True, "client_id": running["client_id"]}


def status(_ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for udid, running in list(_RUNNING.items()):
        job = running["job"]
        if job.done.is_set():
            _RUNNING.pop(udid, None)
            continue
        rows.append({k: v for k, v in running.items() if k != "job"} | {"state": job.state})
    return {"runners": rows, "count": len(rows)}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add("ui_runner_start", lambda args: start(ctx, args), mutating=True)
    toolkit.add("ui_runner_stop", lambda args: stop(ctx, args), mutating=True)
    toolkit.add("ui_runner_status", lambda args: status(ctx, args))
