"""One place that decides whether a target is a simulator or a device, and what it supports.

Tools ask `resolve_target` instead of branching on `is_simulator` themselves. A target comes
from an explicit UDID or from an SDK client record (`device_key`, `device_model`,
`os_version`, `bundle_id`). Simulator clients carry their UDID in `device_key`. Device
clients only carry `vendor:<idfv>`, so the hardware UDID is matched against paired devices
by product type, OS version, and installed bundle id. Ambiguous matches are refused with
the candidate list. The resolver never guesses.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host import devicectl, simctl
from viewlift_ai.services.host.runner import HostError, check_udid

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

SIMULATOR = "simulator"
DEVICE = "device"

# Capabilities every target of a kind has. Platform-specific ones are added in `capabilities`.
_SIMULATOR_CAPS = (
    "build",
    "install",
    "launch",
    "stop",
    "deeplink",
    "location",
    "screenshot",
    "video",
    "logs",
    "lldb",
    "ui_host",
    "ui_runner",
    "push",
    "privacy",
    "statusbar",
    "appearance",
    "erase",
    "clone",
)
_DEVICE_CAPS = (
    "build",
    "install",
    "launch",
    "stop",
    "deeplink",
    "location",
    "screenshot",
    "logs",
    "lldb",
    "ui_host",
    "ui_runner",
    "apps",
    "processes",
    "container_copy",
)


def normalize_platform(platform: str, device_type: str = "", model: str = "") -> str:
    """`ios`, `ipados`, `tvos`, `visionos`, `watchos`, or `macos`."""
    raw = (platform or "").strip().lower()
    hint = f"{device_type} {model}".lower()
    if raw in {"tvos", "appletvos", "appletv"} or "apple tv" in hint or "appletv" in hint:
        return "tvos"
    if raw in {"xros", "visionos"}:
        return "visionos"
    if raw == "watchos":
        return "watchos"
    if raw == "macos":
        return "macos"
    if "ipad" in hint:
        return "ipados"
    return "ios"


def capabilities(kind: str, platform: str, *, usb: bool = True) -> list[str]:
    caps = list(_SIMULATOR_CAPS if kind == SIMULATOR else _DEVICE_CAPS)
    if platform == "tvos":
        caps.append("tv_remote")
        for missing in ("push", "privacy", "statusbar"):
            if missing in caps:
                caps.remove(missing)
    if kind == DEVICE and platform in {"ios", "ipados"} and usb:
        caps.append("video")
    return sorted(set(caps))


def _sim_target(row: dict[str, Any]) -> dict[str, Any]:
    platform = normalize_platform(str(row.get("platform")), str(row.get("device_type")))
    return {
        "kind": SIMULATOR,
        "udid": row.get("udid"),
        "name": row.get("name"),
        "platform": platform,
        "os_version": row.get("os_version"),
        "state": row.get("state"),
        "model": row.get("device_type"),
        "transport": "local",
        "capabilities": capabilities(SIMULATOR, platform),
    }


def _device_target(row: dict[str, Any]) -> dict[str, Any]:
    platform = normalize_platform(
        str(row.get("platform")), str(row.get("device_type")), str(row.get("model"))
    )
    transport = "usb" if str(row.get("transport") or "").lower() == "wired" else "network"
    return {
        "kind": DEVICE,
        "udid": row.get("udid"),
        "identifier": row.get("identifier"),
        "name": row.get("name"),
        "platform": platform,
        "os_version": row.get("os_version"),
        "state": row.get("tunnel") or ("paired" if row.get("paired") else "unpaired"),
        "model": row.get("model"),
        "product_type": row.get("product_type"),
        "transport": transport,
        "paired": bool(row.get("paired")),
        "developer_mode": row.get("developer_mode"),
        "capabilities": capabilities(DEVICE, platform, usb=transport == "usb"),
    }


def list_targets(ctx: HostContext) -> dict[str, Any]:
    """Simulators and paired devices. A missing tool on this Mac leaves that half empty."""
    targets: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        targets.extend(_sim_target(row) for row in simctl.list_simulators(ctx))
    except HostError as exc:
        problems.append(f"simulators: {exc}")
    try:
        targets.extend(_device_target(row) for row in devicectl.list_devices(ctx))
    except HostError as exc:
        problems.append(f"devices: {exc}")
    targets.sort(
        key=lambda row: (
            row["kind"] != DEVICE,
            row.get("state") not in {"Booted", "connected"},
            str(row.get("platform")),
            str(row.get("name")),
        )
    )
    return {"targets": targets, "count": len(targets), "problems": problems}


def _version_prefix(value: object) -> str:
    match = re.match(r"\d+(\.\d+)?", str(value or ""))
    return match.group(0) if match else ""


def match_device(
    ctx: HostContext, devices: list[dict[str, Any]], client: dict[str, Any]
) -> dict[str, Any]:
    """Pick the paired device an SDK client runs on, or raise with the candidates."""
    candidates = [row for row in devices if row.get("paired")] or list(devices)
    model = str(client.get("device_model") or "").strip()
    if model:
        by_model = [row for row in candidates if row.get("product_type") == model]
        candidates = by_model or candidates
    version = _version_prefix(client.get("os_version"))
    if version and len(candidates) > 1:
        by_version = [
            row for row in candidates if _version_prefix(row.get("os_version")) == version
        ]
        candidates = by_version or candidates
    bundle = str(client.get("bundle_id") or "").strip()
    if bundle and len(candidates) > 1:
        installed = []
        for row in candidates:
            try:
                apps = devicectl.installed_bundle_ids(ctx, row)
            except HostError:
                continue
            if bundle in apps:
                installed.append(row)
        candidates = installed or candidates
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise HostError("No paired device found. Connect the device and trust this Mac.")
    names = ", ".join(f"{row.get('name')} ({row.get('udid')})" for row in candidates)
    raise HostError(f"Several paired devices match this app. Pass udid. Candidates: {names}")


def resolve(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    """Resolve `udid` (simulator or device) or `client` (an SDK client record) to a target."""
    client = args.get("client") if isinstance(args.get("client"), dict) else None
    udid = str(args.get("udid") or args.get("deviceId") or "").strip()
    if not udid and client and client.get("is_simulator"):
        udid = str(client.get("device_key") or "").strip()
    if udid and udid != "booted":
        check_udid(udid)
        for row in simctl.list_simulators(ctx):
            if row.get("udid") == udid:
                return _with_client(_sim_target(row), client)
        for row in devicectl.list_devices(ctx):
            if udid in {row.get("udid"), row.get("identifier")}:
                return _with_client(_device_target(row), client)
        raise HostError(f"No simulator or paired device with id {udid}.")
    if client and not client.get("is_simulator"):
        row = match_device(ctx, devicectl.list_devices(ctx), client)
        return _with_client(_device_target(row), client)
    sim = simctl.resolve_simulator(ctx, args)
    return _with_client(_sim_target(sim), client)


def _with_client(target: dict[str, Any], client: dict[str, Any] | None) -> dict[str, Any]:
    if client:
        target["bundle_id"] = client.get("bundle_id")
        target["client_id"] = client.get("id")
        target["has_sdk"] = True
    else:
        target["has_sdk"] = False
    return target


def environment(ctx: HostContext) -> dict[str, Any]:
    """Installed Xcode plus one suggested destination per platform, from what is installed."""
    xcode = ctx.run(["xcodebuild", "-version"], timeout=20)
    lines = (xcode.stdout or "").strip().splitlines() if xcode.ok else []
    version = lines[0].replace("Xcode", "").strip() if lines else ""
    build = lines[1].replace("Build version", "").strip() if len(lines) > 1 else ""
    found = list_targets(ctx)
    destinations: dict[str, dict[str, Any]] = {}
    for row in found["targets"]:
        if row["kind"] != SIMULATOR:
            continue
        platform = row["platform"]
        best = destinations.get(platform)
        if best is None or _version_tuple(row["os_version"]) > _version_tuple(best["os_version"]):
            destinations[platform] = row
    sdk_platform = {"ios": "iOS", "ipados": "iOS", "tvos": "tvOS", "visionos": "visionOS"}
    suggested = {
        platform: {
            "name": row["name"],
            "udid": row["udid"],
            "os_version": row["os_version"],
            "destination": (
                f"platform={sdk_platform.get(platform, 'iOS')} Simulator,name={row['name']}"
            ),
        }
        for platform, row in destinations.items()
    }
    return {
        "xcode_version": version,
        "xcode_build": build,
        "destinations": suggested,
        "targets": found["targets"],
        "problems": found["problems"],
    }


def _version_tuple(value: object) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value or "0").split(".") if part.isdigit())


# ---- target-routed operations ---------------------------------------------------------------
# Each takes `udid`, `simulatorId`/`simulatorName`, `deviceId`, or `client`, resolves the target
# once, and calls the simulator or device tool for it. The outer tool is already confirmed.

_ROUTING_KEYS = ("udid", "client", "simulatorId", "simulatorName", "deviceId")


def _forward(target: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in args.items() if key not in _ROUTING_KEYS}
    if target["kind"] == SIMULATOR:
        out["simulatorId"] = target["udid"]
    else:
        out["deviceId"] = target["udid"]
    if target.get("bundle_id") and not out.get("bundleId"):
        out["bundleId"] = target["bundle_id"]
    return out


def _summary(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": target["kind"],
        "udid": target["udid"],
        "name": target.get("name"),
        "platform": target.get("platform"),
    }


def _routed(
    toolkit: HostToolkit, name: str, args: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    result = toolkit.call(name, args, confirm=True)
    if result.get("ok") is False:
        raise HostError(str(result.get("detail") or f"{name} failed."))
    return {**result, "target": _summary(target)}


def _require(target: dict[str, Any], capability: str, label: str) -> None:
    if capability not in target.get("capabilities", []):
        where = f"{target.get('platform')} {target['kind']}"
        raise HostError(f"{label} is not available on a {where}.")


def open_url(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    ctx = toolkit.ctx
    target = resolve(ctx, args)
    url = devicectl.check_url(str(args.get("url", "")))
    if target["kind"] == SIMULATOR:
        ctx.run_ok(["xcrun", "simctl", "openurl", target["udid"], url], timeout=20)
        return {"opened": True, "target": _summary(target)}
    forwarded = _forward(target, {"url": url, "bundleId": args.get("bundleId")})
    if not (forwarded.get("bundleId") or ctx.defaults.merged({}).get("bundleId")):
        raise HostError("bundleId is required to open a URL on a device.")
    return _routed(toolkit, "open_url_device", forwarded, target)


def launch_app(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    name = "launch_app_sim" if target["kind"] == SIMULATOR else "launch_app_device"
    return _routed(toolkit, name, _forward(target, args), target)


def stop_app(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    name = "stop_app_sim" if target["kind"] == SIMULATOR else "stop_app_device"
    return _routed(toolkit, name, _forward(target, args), target)


def install_app(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    name = "install_app_sim" if target["kind"] == SIMULATOR else "install_app_device"
    return _routed(toolkit, name, _forward(target, args), target)


def build_run(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    if target["kind"] == SIMULATOR:
        return _routed(toolkit, "build_run_sim", _forward(target, args), target)
    forwarded = _forward(target, args)
    forwarded.setdefault("platform", "tvOS" if target["platform"] == "tvos" else "iOS")
    return _routed(toolkit, "build_run_device", forwarded, target)


def screenshot(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    size = {"maxDimension": args.get("maxDimension", 1000)}
    if target["kind"] == SIMULATOR:
        return _routed(toolkit, "screenshot_sim", {**size, "simulatorId": target["udid"]}, target)
    return _routed(toolkit, "device_screenshot", {**size, "udid": target["udid"]}, target)


def location(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    _require(target, "location", "Simulated location")
    clear = bool(args.get("clear"))
    coords = {"latitude": args.get("latitude"), "longitude": args.get("longitude")}
    if target["kind"] == SIMULATOR:
        name = "reset_sim_location" if clear else "set_sim_location"
        forwarded = {"simulatorId": target["udid"], **({} if clear else coords)}
        return _routed(toolkit, name, forwarded, target)
    forwarded = {"udid": target["udid"], "clear": clear, **({} if clear else coords)}
    return _routed(toolkit, "device_location", forwarded, target)


def record_video(toolkit: HostToolkit, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve(toolkit.ctx, args)
    _require(target, "video", "Screen recording")
    action = str(args.get("action") or "start")
    if target["kind"] == SIMULATOR:
        forwarded = {"simulatorId": target["udid"], "action": action}
        return _routed(toolkit, "record_sim_video", forwarded, target)
    forwarded = {"udid": target["udid"], "action": action, "platform": target["platform"]}
    return _routed(toolkit, "record_device_video", forwarded, target)


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add("list_targets", lambda _args: list_targets(ctx))
    toolkit.add("resolve_target", lambda args: resolve(ctx, args))
    toolkit.add("host_environment", lambda _args: environment(ctx))
    toolkit.add("screenshot", lambda args: screenshot(toolkit, args))
    toolkit.add("record_video", lambda args: record_video(toolkit, args))
    for name, handler in (
        ("open_url", open_url),
        ("launch_app", launch_app),
        ("stop_app", stop_app),
        ("install_app", install_app),
        ("build_run", build_run),
        ("set_location", location),
    ):
        toolkit.add(name, lambda args, fn=handler: fn(toolkit, args), mutating=True)
