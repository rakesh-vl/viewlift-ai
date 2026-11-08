"""Environment check, session defaults, project roots, and job tools."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai import defaults
from viewlift_ai.services.host.context import derived_data
from viewlift_ai.services.host.runner import HostError, which

if TYPE_CHECKING:
    from viewlift_ai.services.host.toolkit import HostToolkit


def _version(toolkit: HostToolkit, argv: list[str]) -> str:
    try:
        result = toolkit.ctx.run(argv, timeout=20)
    except HostError:
        return ""
    if not result.ok:
        return ""
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0][:200] if lines else ""


def doctor(toolkit: HostToolkit, _args: dict[str, Any]) -> dict[str, Any]:
    ctx = toolkit.ctx
    xcode = _version(toolkit, ["xcode-select", "-p"])
    checks = {
        "xcode_path": xcode,
        "xcodebuild": _version(toolkit, ["xcodebuild", "-version"]),
        "swift": _version(toolkit, ["swift", "--version"]),
        "simctl": bool(xcode),
        "devicectl": bool(_version(toolkit, ["xcrun", "--find", "devicectl"])),
        "lldb": bool(_version(toolkit, ["xcrun", "--find", "lldb"])),
        "xccov": bool(_version(toolkit, ["xcrun", "--find", "xccov"])),
        "axe": which("axe") or "",
        "idb": which("idb") or "",
    }
    hints = []
    if not checks["xcodebuild"]:
        hints.append("Install Xcode and run `sudo xcode-select -s /Applications/Xcode.app`.")
    if not checks["axe"] and not checks["idb"]:
        hints.append(
            "UI input without the SDK needs AXe (`brew install cameroncooke/axe/axe`) or idb. "
            "Apps running the ViewliftNetworkCapture SDK do not need either."
        )
    roots = ctx.roots.status()
    if not roots["active"]:
        hints.append("Add a project root on the Setup page so build tools can open your project.")
    devices = _device_support(toolkit, hints)
    return {
        "checks": checks,
        "devices": devices,
        "project_roots": roots,
        "defaults": ctx.defaults.show(),
        "hints": hints,
    }


def _device_support(toolkit: HostToolkit, hints: list[str]) -> dict[str, Any]:
    """Tunnel, UI runner, and recorder readiness for physical devices."""
    from viewlift_ai.services.host import device_tunnel, device_video, ui_runner

    tunnel = toolkit.call("device_tunnel_status", {})
    runner_project = ui_runner.project_path()
    recorder = device_video.package_dir()
    receiver = toolkit.ctx.receiver() if toolkit.ctx.receiver is not None else {}
    team = toolkit.ctx.defaults.merged({}).get("developmentTeam") or ui_runner.os.environ.get(
        ui_runner.TEAM_ENV, ""
    )
    out = {
        "pymobiledevice3": device_tunnel.available(),
        "tunnel_running": bool(tunnel.get("running")),
        "tunnel_address": tunnel.get("address"),
        "ui_runner_project": str(runner_project) if runner_project.exists() else "",
        "ui_runner_team": bool(team),
        "screen_recorder_package": str(recorder) if recorder.exists() else "",
        "sdk_receiver": bool(receiver.get("url")),
    }
    if not out["pymobiledevice3"]:
        hints.append("Device location, screenshots, and syslog need pymobiledevice3 (uv sync).")
    elif not out["tunnel_running"]:
        hints.append(
            "iOS, iPadOS, and tvOS 17+ devices need the developer tunnel: device_tunnel_start "
            "or the Setup page."
        )
    if not out["sdk_receiver"]:
        hints.append("Enable the SDK receiver on Setup before starting the UI runner.")
    if not out["ui_runner_team"]:
        hints.append(
            "The UI runner on a device needs a signing team: session_set_defaults "
            f"developmentTeam or {ui_runner.TEAM_ENV}."
        )
    return out


def _recent_derived_workspace(roots: list[Path]) -> Path | None:
    base = derived_data()
    if not base.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for info in base.glob("*/info.plist"):
        try:
            data = plistlib.loads(info.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        raw = data.get("WorkspacePath")
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if not path.exists() or not any(path.is_relative_to(root) for root in roots):
            continue
        stamp = info.stat().st_mtime
        if best is None or stamp > best[0]:
            best = (stamp, path)
    return best[1] if best else None


def sync_xcode_defaults(toolkit: HostToolkit, _args: dict[str, Any]) -> dict[str, Any]:
    """Use the project Xcode built most recently (inside the project roots) as the default."""
    ctx = toolkit.ctx
    path = _recent_derived_workspace(ctx.roots.roots())
    if path is None:
        raise HostError("No recent Xcode build found inside the project roots.")
    key = "workspacePath" if path.suffix == ".xcworkspace" else "projectPath"
    values: dict[str, Any] = {key: str(path)}
    schemes = toolkit.call("list_schemes", {key: str(path)})
    names = schemes.get("schemes") if isinstance(schemes, dict) else None
    current = ctx.defaults.current().get("scheme")
    if isinstance(names, list) and names and current not in names:
        values["scheme"] = names[0]
    return {"synced": values, **ctx.defaults.set(values)}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add("doctor", lambda args: doctor(toolkit, args))
    toolkit.add("session_show_defaults", lambda _args: ctx.defaults.show())
    toolkit.add(
        "session_set_defaults",
        lambda args: ctx.defaults.set(
            {k: v for k, v in args.items() if k != "profile"}, profile=str(args.get("profile", ""))
        ),
    )
    toolkit.add(
        "session_clear_defaults",
        lambda args: ctx.defaults.clear(
            [str(item) for item in args.get("keys") or []], all_profiles=bool(args.get("all"))
        ),
    )
    toolkit.add(
        "session_use_defaults_profile",
        lambda args: ctx.defaults.use_profile(
            str(args.get("profile", "")), create=bool(args.get("create"))
        ),
    )
    toolkit.add("sync_xcode_defaults", lambda args: sync_xcode_defaults(toolkit, args))
    toolkit.add("project_roots", lambda _args: ctx.roots.status())
    toolkit.add(
        "get_job",
        lambda args: ctx.jobs.describe(
            str(args.get("job_id", "")), log_tail=int(args.get("log_tail", 0))
        ),
    )
    toolkit.add(
        "wait_job",
        lambda args: ctx.jobs.wait(
            str(args.get("job_id", "")),
            float(args.get("timeout_s", defaults.JOB_WAIT_SECONDS)),
        ),
    )
    toolkit.add("cancel_job", lambda args: ctx.jobs.cancel(str(args.get("job_id", ""))))
    toolkit.add("list_jobs", lambda _args: {"jobs": ctx.jobs.list()})
