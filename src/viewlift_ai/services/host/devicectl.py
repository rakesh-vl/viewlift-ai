"""Physical iPhone, iPad, and Apple TV builds and app control via `xcrun devicectl`."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from viewlift_ai.services.host import xcodebuild
from viewlift_ai.services.host.jobs import JobContext
from viewlift_ai.services.host.runner import (
    HostError,
    check_bundle_id,
    check_launch_args,
    check_udid,
)

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_LAUNCHED: dict[tuple[str, str], int] = {}


def _devicectl_json(ctx: HostContext, argv: list[str], timeout: float = 60) -> dict[str, Any]:
    path = ctx.temp_file(".json")
    try:
        ctx.run_ok(["xcrun", "devicectl", *argv, "--json-output", str(path)], timeout=timeout)
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HostError("devicectl did not return JSON.") from exc
    finally:
        path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else {}


def list_devices(ctx: HostContext) -> list[dict[str, Any]]:
    data = _devicectl_json(ctx, ["list", "devices"])
    rows = []
    for item in (data.get("result") or {}).get("devices") or []:
        if not isinstance(item, dict):
            continue
        props = item.get("deviceProperties") or {}
        hardware = item.get("hardwareProperties") or {}
        connection = item.get("connectionProperties") or {}
        # Xcode 26+ lists booted and shut-down simulators here too; simctl owns those.
        if str(hardware.get("reality") or "").lower() == "simulated":
            continue
        rows.append(
            {
                "identifier": item.get("identifier"),
                "udid": hardware.get("udid"),
                "name": props.get("name"),
                "platform": hardware.get("platform"),
                "model": hardware.get("marketingName") or hardware.get("productType"),
                "product_type": hardware.get("productType"),
                "device_type": hardware.get("deviceType"),
                "os_version": props.get("osVersionNumber"),
                "transport": connection.get("transportType"),
                "tunnel": connection.get("tunnelState"),
                "paired": connection.get("pairingState") == "paired",
                "developer_mode": props.get("developerModeStatus"),
            }
        )
    return rows


def resolve_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    wanted = str(ctx.defaults.merged(args).get("deviceId") or "")
    devices = list_devices(ctx)
    if wanted:
        check_udid(wanted)
        for row in devices:
            if wanted in {row.get("udid"), row.get("identifier")}:
                return row
        raise HostError(f"No paired device with id {wanted}. Call list_physical_devices.")
    connected = [row for row in devices if row.get("paired") and row.get("tunnel") == "connected"]
    if len(connected) == 1:
        return connected[0]
    raise HostError("Pass deviceId. Call list_physical_devices to see paired devices.")


def _device_id(row: dict[str, Any]) -> str:
    return str(row.get("identifier") or row.get("udid"))


def _install(ctx: HostContext, device: dict[str, Any], app_path: str) -> dict[str, Any]:
    _devicectl_json(
        ctx, ["device", "install", "app", "--device", _device_id(device), app_path], 600
    )
    return {"device": device.get("name"), "installed": app_path}


_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}")
_BLOCKED_SCHEMES = frozenset({"javascript", "file", "data", "about", "blob"})
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://\S{0,2040}")


def _launch_env(env: object) -> str | None:
    if not isinstance(env, dict) or not env:
        return None
    clean = {
        str(key): str(value)
        for key, value in env.items()
        if _ENV_KEY.fullmatch(str(key)) and not str(key).startswith(("DYLD_", "LD_"))
    }
    return json.dumps(clean) if clean else None


def _launch(
    ctx: HostContext,
    device: dict[str, Any],
    bundle: str,
    launch_args: list[str],
    *,
    env: object = None,
    payload_url: str = "",
) -> dict[str, Any]:
    argv = ["device", "process", "launch", "--device", _device_id(device), "--terminate-existing"]
    env_json = _launch_env(env)
    if env_json:
        argv.extend(["--environment-variables", env_json])
    if payload_url:
        argv.extend(["--payload-url", payload_url])
    data = _devicectl_json(ctx, [*argv, bundle, *launch_args], 120)
    process = (data.get("result") or {}).get("process") or {}
    pid = process.get("processIdentifier")
    if isinstance(pid, int):
        _LAUNCHED[(_device_id(device), bundle)] = pid
    return {"device": device.get("name"), "bundleId": bundle, "pid": pid}


def check_url(url: str) -> str:
    text = (url or "").strip()
    scheme = text.split(":", 1)[0].lower()
    if scheme in _BLOCKED_SCHEMES:
        raise HostError(f"Scheme {scheme} is blocked.")
    if not _URL.fullmatch(text):
        raise HostError("url must be a scheme plus :// with no spaces.")
    return text


def list_apps(ctx: HostContext, device: dict[str, Any]) -> list[dict[str, Any]]:
    data = _devicectl_json(
        ctx, ["device", "info", "apps", "--device", _device_id(device)], 60
    )
    rows = []
    for item in (data.get("result") or {}).get("apps") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "bundleId": item.get("bundleIdentifier"),
                "name": item.get("name"),
                "version": item.get("version"),
                "build": item.get("bundleVersion"),
                "url": item.get("url"),
                "removable": item.get("removable"),
            }
        )
    return rows


def installed_bundle_ids(ctx: HostContext, device: dict[str, Any]) -> set[str]:
    return {str(row["bundleId"]) for row in list_apps(ctx, device) if row.get("bundleId")}


def list_processes(ctx: HostContext, device: dict[str, Any]) -> list[dict[str, Any]]:
    data = _devicectl_json(
        ctx, ["device", "info", "processes", "--device", _device_id(device)], 60
    )
    rows = []
    for item in (data.get("result") or {}).get("runningProcesses") or []:
        if not isinstance(item, dict):
            continue
        executable = str(item.get("executable") or "")
        rows.append({"pid": item.get("processIdentifier"), "executable": executable})
    return rows


def _path_of(url: str) -> str:
    parsed = urlsplit(url)
    return unquote(parsed.path if parsed.scheme else url).rstrip("/")


def find_pid(ctx: HostContext, device: dict[str, Any], bundle: str) -> int | None:
    """Running pid for a bundle id: match process executables against the app's bundle path."""
    app = next((row for row in list_apps(ctx, device) if row.get("bundleId") == bundle), None)
    if app is None or not app.get("url"):
        return None
    root = _path_of(str(app["url"]))
    for row in list_processes(ctx, device):
        if isinstance(row.get("pid"), int) and _path_of(row["executable"]).startswith(root + "/"):
            return int(row["pid"])
    return None


def list_physical(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    rows = list_devices(ctx)
    return {"devices": rows, "count": len(rows)}


def build_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    return xcodebuild.start_build_job(
        ctx,
        args,
        kind="build_device",
        title=f"Build {xcodebuild.scheme_arg(ctx, args)} for {device.get('name')}",
        action=["-allowProvisioningUpdates", "build"],
        destination=xcodebuild.device_destination(ctx, args, str(device.get("udid"))),
    )


def build_run_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    destination = xcodebuild.device_destination(ctx, args, str(device.get("udid")))
    launch_args = check_launch_args(args.get("launchArgs"))

    def after(job: JobContext) -> dict[str, Any]:
        product = xcodebuild.app_product(ctx, args, destination)
        job.log(f"Installing {product['appPath']} on {device.get('name')}")
        installed = _install(ctx, device, product["appPath"])
        job.log(f"Launching {product['bundleId']}")
        launched = _launch(ctx, device, product["bundleId"], launch_args)
        return {"product": product, "installed": installed, "launched": launched}

    return xcodebuild.start_build_job(
        ctx,
        args,
        kind="build_run_device",
        title=f"Build and run {xcodebuild.scheme_arg(ctx, args)} on {device.get('name')}",
        action=["-allowProvisioningUpdates", "build"],
        destination=destination,
        after=after,
    )


def test_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    destination = xcodebuild.device_destination(ctx, args, str(device.get("udid")))
    return xcodebuild.start_test_job(ctx, args, destination, str(device.get("name")))


def get_device_app_path(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    return xcodebuild.app_product(
        ctx, args, xcodebuild.device_destination(ctx, args, str(device.get("udid")))
    )


def install_app_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = ctx.resolve_artifact(str(args.get("appPath", "")))
    return _install(ctx, resolve_device(ctx, args), str(path))


def launch_app_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    bundle = check_bundle_id(str(ctx.defaults.merged(args).get("bundleId", "")))
    device = resolve_device(ctx, args)
    launch_args = check_launch_args(args.get("launchArgs"))
    if not args.get("console"):
        return _launch(ctx, device, bundle, launch_args, env=args.get("env"))
    argv = ["xcrun", "devicectl", "device", "process", "launch", "--device", _device_id(device)]
    argv.extend(["--terminate-existing", "--console"])
    env_json = _launch_env(args.get("env"))
    if env_json:
        argv.extend(["--environment-variables", env_json])
    argv.extend([bundle, *launch_args])

    def body(job: JobContext) -> dict[str, Any]:
        proc = job.spawn(argv)
        return {"device": device.get("name"), "bundleId": bundle, "exit_code": proc.wait()}

    job = ctx.jobs.start("launch_app_device", f"Console of {bundle} on {device.get('name')}", body)
    return {"device": device.get("name"), "bundleId": bundle, "job_id": job.id, "console": True}


def open_url_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    """Open a URL in an app on a device. devicectl relaunches the app with the URL as payload."""
    bundle = check_bundle_id(str(ctx.defaults.merged(args).get("bundleId", "")))
    url = check_url(str(args.get("url", "")))
    device = resolve_device(ctx, args)
    launched = _launch(ctx, device, bundle, [], payload_url=url)
    return {**launched, "opened": True}


def device_apps(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    rows = list_apps(ctx, device)
    if not args.get("includeSystem"):
        rows = [row for row in rows if row.get("removable") is not False]
    return {"device": device.get("name"), "apps": rows, "count": len(rows)}


def device_processes(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    rows = list_processes(ctx, device)
    return {"device": device.get("name"), "processes": rows, "count": len(rows)}


def copy_from_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    """Copy a file or folder out of an app's data container into this Mac's work folder."""
    bundle = check_bundle_id(str(ctx.defaults.merged(args).get("bundleId", "")))
    source = str(args.get("source") or "").strip().lstrip("/")
    if not source or ".." in Path(source).parts or len(source) > 512:
        raise HostError("source must be a relative path inside the app container.")
    device = resolve_device(ctx, args)
    dest = ctx.work_dir / "device-files" / bundle / source
    dest.parent.mkdir(parents=True, exist_ok=True)
    _devicectl_json(
        ctx,
        [
            "device",
            "copy",
            "from",
            "--device",
            _device_id(device),
            "--domain-type",
            "appDataContainer",
            "--domain-identifier",
            bundle,
            "--source",
            source,
            "--destination",
            str(dest),
        ],
        300,
    )
    return {"device": device.get("name"), "bundleId": bundle, "copied_to": str(dest)}


def stop_app_device(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    device = resolve_device(ctx, args)
    pid = args.get("processId")
    if not isinstance(pid, int):
        bundle = check_bundle_id(str(ctx.defaults.merged(args).get("bundleId", "")))
        pid = find_pid(ctx, device, bundle) or _LAUNCHED.get((_device_id(device), bundle))
    if not isinstance(pid, int):
        raise HostError("The app is not running on this device.")
    _devicectl_json(
        ctx,
        ["device", "process", "terminate", "--device", _device_id(device), "--pid", str(pid)],
    )
    return {"device": device.get("name"), "stopped_pid": pid}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "list_physical_devices": (list_physical, False),
        "build_device": (build_device, True),
        "build_run_device": (build_run_device, True),
        "test_device": (test_device, True),
        "get_device_app_path": (get_device_app_path, False),
        "install_app_device": (install_app_device, True),
        "launch_app_device": (launch_app_device, True),
        "stop_app_device": (stop_app_device, True),
        "open_url_device": (open_url_device, True),
        "list_device_apps": (device_apps, False),
        "list_device_processes": (device_processes, False),
        "copy_from_device": (copy_from_device, True),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
