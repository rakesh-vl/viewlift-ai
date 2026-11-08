"""macOS app build, run, and test via xcodebuild and `open`."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host import xcodebuild
from viewlift_ai.services.host.jobs import JobContext
from viewlift_ai.services.host.runner import HostError, check_bundle_id, check_launch_args

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_DESTINATION = "platform=macOS"


def _destination(args: dict[str, Any]) -> str:
    arch = str(args.get("arch") or "")
    if arch:
        if arch not in {"arm64", "x86_64"}:
            raise HostError("arch must be arm64 or x86_64.")
        return f"{_DESTINATION},arch={arch}"
    return _DESTINATION


def _open(ctx: HostContext, app_path: str, launch_args: list[str]) -> dict[str, Any]:
    argv = ["open", "-n", app_path]
    if launch_args:
        argv += ["--args", *launch_args]
    ctx.run_ok(argv, timeout=60)
    return {"launched": app_path}


def build_macos(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    return xcodebuild.start_build_job(
        ctx,
        args,
        kind="build_macos",
        title=f"Build {xcodebuild.scheme_arg(ctx, args)} for macOS",
        action=["build"],
        destination=_destination(args),
    )


def build_run_macos(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    destination = _destination(args)
    launch_args = check_launch_args(args.get("launchArgs"))

    def after(job: JobContext) -> dict[str, Any]:
        product = xcodebuild.app_product(ctx, args, destination)
        job.log(f"Opening {product['appPath']}")
        return {"product": product, "launched": _open(ctx, product["appPath"], launch_args)}

    return xcodebuild.start_build_job(
        ctx,
        args,
        kind="build_run_macos",
        title=f"Build and run {xcodebuild.scheme_arg(ctx, args)} on macOS",
        action=["build"],
        destination=destination,
        after=after,
    )


def test_macos(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    return xcodebuild.start_test_job(ctx, args, _destination(args), "macOS")


def get_mac_app_path(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    return xcodebuild.app_product(ctx, args, _destination(args))


def launch_mac_app(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = ctx.resolve_artifact(str(args.get("appPath", "")))
    if path.suffix != ".app":
        raise HostError("appPath must be a .app bundle.")
    return _open(ctx, str(path), check_launch_args(args.get("launchArgs")))


def stop_mac_app(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("appPath") or "")
    bundle = str(args.get("bundleId") or "")
    if raw and not bundle:
        plist = Path(ctx.resolve_artifact(raw)) / "Contents" / "Info.plist"
        if plist.is_file():
            bundle = str(plistlib.loads(plist.read_bytes()).get("CFBundleIdentifier", ""))
    bundle = check_bundle_id(bundle)
    result = ctx.run(["osascript", "-e", f'quit app id "{bundle}"'], timeout=20)
    return {"bundleId": bundle, "stopped": result.ok}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "build_macos": (build_macos, True),
        "build_run_macos": (build_run_macos, True),
        "test_macos": (test_macos, True),
        "get_mac_app_path": (get_mac_app_path, False),
        "launch_mac_app": (launch_mac_app, True),
        "stop_mac_app": (stop_mac_app, True),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
