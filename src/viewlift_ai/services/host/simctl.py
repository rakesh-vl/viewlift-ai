"""Simulator control through `xcrun simctl`."""

from __future__ import annotations

import json
import re
import signal
import uuid
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host.context import png_payload
from viewlift_ai.services.host.runner import (
    HostError,
    check_bundle_id,
    check_launch_args,
    check_name,
    check_udid,
)

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_PLATFORMS = {"ios": "iOS", "tvos": "tvOS", "watchos": "watchOS", "visionos": "xrOS"}
_PRIVACY_SERVICES = frozenset(
    {
        "all",
        "calendar",
        "contacts-limited",
        "contacts",
        "location",
        "location-always",
        "photos-add",
        "photos",
        "media-library",
        "microphone",
        "motion",
        "reminders",
        "siri",
        "camera",
    }
)
_RECORDINGS: dict[str, Any] = {}


def list_simulators(ctx: HostContext) -> list[dict[str, Any]]:
    data = ctx.run_json(["xcrun", "simctl", "list", "devices", "available", "-j"], timeout=30)
    rows: list[dict[str, Any]] = []
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, dict):
        return rows
    for runtime, items in devices.items():
        match = re.search(r"SimRuntime\.(\w+?)-([\d-]+)$", str(runtime))
        platform = match.group(1) if match else ""
        version = match.group(2).replace("-", ".") if match else ""
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "udid": item.get("udid"),
                    "name": item.get("name"),
                    "state": item.get("state"),
                    "platform": "visionOS" if platform == "xrOS" else platform,
                    "os_version": version,
                    "device_type": str(item.get("deviceTypeIdentifier", "")).rsplit(".", 1)[-1],
                }
            )
    return rows


def _version_key(row: dict[str, Any]) -> tuple[int, ...]:
    return tuple(
        int(part) for part in str(row.get("os_version") or "0").split(".") if part.isdigit()
    )


def resolve_simulator(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    """Pick by simulatorId, then simulatorName (booted first, newest OS), then any booted one."""
    merged = ctx.defaults.merged(args)
    sims = list_simulators(ctx)
    wanted_platform = str(merged.get("platform") or "").lower()
    if wanted_platform:
        sims = [row for row in sims if str(row["platform"]).lower() == wanted_platform] or sims
    sim_id = str(args.get("simulatorId") or "")
    name = str(args.get("simulatorName") or "")
    if not sim_id and not name:
        sim_id = str(merged.get("simulatorId") or "")
        name = str(merged.get("simulatorName") or "")
    if sim_id:
        check_udid(sim_id)
        for row in sims:
            if row["udid"] == sim_id:
                return row
        raise HostError(f"No available simulator with id {sim_id}. Call list_sims.")
    if name:
        check_name(name, "simulatorName")
        matches = [row for row in sims if row["name"] == name]
        if not matches:
            raise HostError(f"No simulator named {name}. Call list_sims.")
        matches.sort(key=lambda row: (row["state"] == "Booted", _version_key(row)), reverse=True)
        return matches[0]
    booted = [row for row in sims if row["state"] == "Booted"]
    if booted:
        return booted[0]
    raise HostError("No simulator selected. Pass simulatorId or simulatorName, or boot one.")


def boot(ctx: HostContext, udid: str) -> dict[str, Any]:
    result = ctx.run(["xcrun", "simctl", "boot", udid], timeout=120)
    if not result.ok and "current state: Booted" not in result.stderr:
        raise HostError(f"simctl boot failed: {result.stderr.strip()[:400]}")
    ctx.run(["xcrun", "simctl", "bootstatus", udid, "-b"], timeout=180)
    return {"udid": udid, "booted": True}


def _list(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    rows = list_simulators(ctx)
    platform = str(args.get("platform") or "").lower()
    if platform:
        rows = [row for row in rows if str(row["platform"]).lower() == platform]
    if args.get("bootedOnly"):
        rows = [row for row in rows if row["state"] == "Booted"]
    rows.sort(key=lambda row: (row["state"] != "Booted", row["platform"], row["name"]))
    return {"simulators": rows, "count": len(rows)}


def _boot(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    out = boot(ctx, sim["udid"])
    if args.get("open", True):
        ctx.run(
            ["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", sim["udid"]], timeout=20
        )
    return {**out, "name": sim["name"]}


def _open(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.run_ok(["open", "-a", "Simulator"], timeout=20)
    return {"opened": True}


def _erase(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    ids = [check_udid(str(item)) for item in args.get("simulatorIds") or []]
    if not ids:
        ids = [resolve_simulator(ctx, args)["udid"]]
    erased = []
    for udid in ids:
        if args.get("shutdownFirst", True):
            ctx.run(["xcrun", "simctl", "shutdown", udid], timeout=60)
        ctx.run_ok(["xcrun", "simctl", "erase", udid], timeout=120)
        erased.append(udid)
    return {"erased": erased}


def _appearance(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode", "")).lower()
    if mode not in {"light", "dark"}:
        raise HostError("mode must be light or dark.")
    sim = resolve_simulator(ctx, args)
    ctx.run_ok(["xcrun", "simctl", "ui", sim["udid"], "appearance", mode], timeout=20)
    return {"udid": sim["udid"], "appearance": mode}


def _location(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(args["latitude"])
        lon = float(args["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HostError("latitude and longitude are required numbers.") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HostError("latitude or longitude is out of range.")
    sim = resolve_simulator(ctx, args)
    ctx.run_ok(["xcrun", "simctl", "location", sim["udid"], "set", f"{lat},{lon}"], timeout=20)
    return {"udid": sim["udid"], "latitude": lat, "longitude": lon}


def _reset_location(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    ctx.run_ok(["xcrun", "simctl", "location", sim["udid"], "clear"], timeout=20)
    return {"udid": sim["udid"], "cleared": True}


_STATUS_FLAGS = {
    "time": "--time",
    "dataNetwork": "--dataNetwork",
    "wifiMode": "--wifiMode",
    "wifiBars": "--wifiBars",
    "cellularMode": "--cellularMode",
    "cellularBars": "--cellularBars",
    "operatorName": "--operatorName",
    "batteryState": "--batteryState",
    "batteryLevel": "--batteryLevel",
}


def _statusbar(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    if args.get("clear"):
        ctx.run_ok(["xcrun", "simctl", "status_bar", sim["udid"], "clear"], timeout=20)
        return {"udid": sim["udid"], "cleared": True}
    argv = ["xcrun", "simctl", "status_bar", sim["udid"], "override"]
    for key, flag in _STATUS_FLAGS.items():
        if key in args and args[key] not in ("", None):
            value = str(args[key])
            if len(value) > 40 or "\n" in value:
                raise HostError(f"{key} is too long.")
            argv.extend([flag, value])
    if len(argv) == 5:
        raise HostError("Pass at least one status bar field, or clear=true.")
    ctx.run_ok(argv, timeout=20)
    return {"udid": sim["udid"], "override": {k: args[k] for k in _STATUS_FLAGS if k in args}}


def _keystroke(ctx: HostContext, key: str, modifiers: str) -> None:
    script = (
        'tell application "Simulator" to activate\n'
        f'tell application "System Events" to keystroke "{key}" using {{{modifiers}}}'
    )
    result = ctx.run(["osascript", "-e", script], timeout=15)
    if not result.ok:
        raise HostError(
            "Could not send the shortcut to Simulator. Allow your terminal or Cursor under "
            "System Settings > Privacy & Security > Accessibility."
        )


# Simulator maps the Mac keyboard onto the Siri Remote: arrows move focus, Return selects,
# Escape is Menu (Back), Space is Play/Pause, and Command-Shift-H is the TV button.
_TV_KEY_CODES = {
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "select": 36,
    "menu": 53,
    "playpause": 49,
}


def _tv_remote(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    if "tvos" not in str(sim.get("platform", "")).lower():
        raise HostError(f"{sim.get('name')} is not an Apple TV simulator.")
    if sim.get("state") != "Booted":
        raise HostError(f"{sim.get('name')} is not booted.")
    press = str(args.get("press") or "").strip().lower()
    repeat = max(1, min(int(args.get("repeat") or 1), 20))
    if press == "home":
        stroke = 'keystroke "h" using {command down, shift down}'
    elif press in _TV_KEY_CODES:
        stroke = f"key code {_TV_KEY_CODES[press]}"
    else:
        raise HostError("press must be up, down, left, right, select, menu, playpause, or home.")
    name = str(sim.get("name") or "").replace('"', "")
    script = "\n".join(
        [
            'tell application "Simulator" to activate',
            'tell application "System Events" to tell process "Simulator"',
            f'  set targets to (windows whose name contains "{name}")',
            '  if (count of targets) > 0 then perform action "AXRaise" of item 1 of targets',
            f"  repeat {repeat} times",
            f"    {stroke}",
            "    delay 0.15",
            "  end repeat",
            "end tell",
        ]
    )
    result = ctx.run(["osascript", "-e", script], timeout=30)
    if not result.ok:
        raise HostError(
            "Could not press the remote in Simulator. Allow your terminal or Cursor under "
            "System Settings > Privacy & Security > Accessibility."
        )
    return {"udid": sim["udid"], "pressed": press, "repeat": repeat}


def _software_keyboard(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    _keystroke(ctx, "k", "command down")
    return {"toggled": "software_keyboard"}


def _hardware_keyboard(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    _keystroke(ctx, "k", "command down, shift down")
    return {"toggled": "connect_hardware_keyboard"}


def _install(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = ctx.resolve_artifact(str(args.get("appPath", "")))
    sim = resolve_simulator(ctx, args)
    ctx.run_ok(["xcrun", "simctl", "install", sim["udid"], str(path)], timeout=300)
    return {"udid": sim["udid"], "installed": str(path)}


def launch(
    ctx: HostContext, udid: str, bundle_id: str, launch_args: list[str], env: dict[str, Any] | None
) -> dict[str, Any]:
    argv = ["xcrun", "simctl", "launch", "--terminate-running-process", udid, bundle_id]
    extra_env = {
        f"SIMCTL_CHILD_{key}": str(value)
        for key, value in (env or {}).items()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", str(key))
    }
    result = ctx.run_ok([*argv, *launch_args], timeout=60, env=extra_env or None)
    pid_match = re.search(r":\s*(\d+)\s*$", result.stdout.strip())
    return {
        "udid": udid,
        "bundleId": bundle_id,
        "pid": int(pid_match.group(1)) if pid_match else None,
    }


def _launch(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    merged = ctx.defaults.merged(args)
    bundle = check_bundle_id(str(merged.get("bundleId", "")))
    sim = resolve_simulator(ctx, args)
    env = args.get("env") if isinstance(args.get("env"), dict) else None
    return launch(ctx, sim["udid"], bundle, check_launch_args(args.get("launchArgs")), env)


def _stop(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    merged = ctx.defaults.merged(args)
    bundle = check_bundle_id(str(merged.get("bundleId", "")))
    sim = resolve_simulator(ctx, args)
    result = ctx.run(["xcrun", "simctl", "terminate", sim["udid"], bundle], timeout=30)
    return {"udid": sim["udid"], "bundleId": bundle, "stopped": result.ok}


def _screenshot(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    path = ctx.temp_file(".png")
    try:
        ctx.run_ok(
            ["xcrun", "simctl", "io", sim["udid"], "screenshot", "--type=png", str(path)],
            timeout=30,
        )
        payload = png_payload(
            path, max_dimension=int(args.get("maxDimension", 1000)), runner=ctx.runner
        )
    finally:
        path.unlink(missing_ok=True)
    return {"udid": sim["udid"], **payload}


def _record(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    udid = sim["udid"]
    action = str(args.get("action", "start")).lower()
    if action == "stop":
        process = _RECORDINGS.pop(udid, None)
        if process is None:
            raise HostError("No recording is running on this simulator.")
        proc, path = process
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=20)
        except Exception:  # noqa: BLE001
            proc.kill()
        return {"udid": udid, "stopped": True, "path": str(path)}
    if udid in _RECORDINGS:
        raise HostError("A recording is already running. Call with action=stop first.")
    path = ctx.work_dir / "recordings" / f"{udid}-{uuid.uuid4().hex[:8]}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    holder: dict[str, Any] = {}

    def body(job_ctx: Any) -> dict[str, Any]:
        holder["proc"] = job_ctx.spawn(
            ["xcrun", "simctl", "io", udid, "recordVideo", "--codec=h264", "--force", str(path)]
        )
        holder["proc"].wait()
        return {"path": str(path)}

    job = ctx.jobs.start("record_sim_video", f"Recording {sim['name']}", body)
    for _ in range(50):
        if "proc" in holder:
            break
        job.done.wait(0.05)
    if "proc" not in holder:
        raise HostError("Recording did not start.")
    _RECORDINGS[udid] = (holder["proc"], path)
    return {"udid": udid, "recording": True, "path": str(path), "job_id": job.id}


def _push(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    merged = ctx.defaults.merged(args)
    bundle = check_bundle_id(str(merged.get("bundleId", "")))
    payload = args.get("payload")
    if not isinstance(payload, dict) or "aps" not in payload:
        raise HostError("payload must be an object with an aps key.")
    sim = resolve_simulator(ctx, args)
    path = ctx.temp_file(".apns")
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
        ctx.run_ok(["xcrun", "simctl", "push", sim["udid"], bundle, str(path)], timeout=30)
    finally:
        path.unlink(missing_ok=True)
    return {"udid": sim["udid"], "bundleId": bundle, "sent": True}


def _privacy(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action", "")).lower()
    service = str(args.get("service", "")).lower()
    if action not in {"grant", "revoke", "reset"} or service not in _PRIVACY_SERVICES:
        services = ", ".join(sorted(_PRIVACY_SERVICES))
        raise HostError(f"action is grant, revoke, or reset; service is one of {services}.")
    sim = resolve_simulator(ctx, args)
    argv = ["xcrun", "simctl", "privacy", sim["udid"], action, service]
    bundle = str(ctx.defaults.merged(args).get("bundleId") or "")
    if bundle:
        argv.append(check_bundle_id(bundle))
    elif action != "reset":
        raise HostError("bundleId is required for grant and revoke.")
    ctx.run_ok(argv, timeout=30)
    return {"udid": sim["udid"], "action": action, "service": service, "bundleId": bundle}


def _clone(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    sim = resolve_simulator(ctx, args)
    name = check_name(str(args.get("name", "")), "name")
    result = ctx.run_ok(["xcrun", "simctl", "clone", sim["udid"], name], timeout=180)
    return {"source": sim["udid"], "udid": result.stdout.strip(), "name": name}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "list_sims": (_list, False),
        "boot_sim": (_boot, True),
        "open_sim": (_open, True),
        "erase_sims": (_erase, True),
        "set_sim_appearance": (_appearance, True),
        "set_sim_location": (_location, True),
        "reset_sim_location": (_reset_location, True),
        "sim_statusbar": (_statusbar, True),
        "toggle_software_keyboard": (_software_keyboard, True),
        "toggle_connect_hardware_keyboard": (_hardware_keyboard, True),
        "install_app_sim": (_install, True),
        "launch_app_sim": (_launch, True),
        "stop_app_sim": (_stop, True),
        "screenshot_sim": (_screenshot, False),
        "record_sim_video": (_record, False),
        "sim_push": (_push, True),
        "sim_privacy": (_privacy, True),
        "sim_clone": (_clone, True),
        "sim_tv_remote": (_tv_remote, True),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
