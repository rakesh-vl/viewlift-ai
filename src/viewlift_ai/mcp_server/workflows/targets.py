"""Target-aware tools: one call works on a simulator or a paired device, iOS, iPadOS, or tvOS.

Pick the target with client_id (an app that runs the SDK), udid, simulatorId or simulatorName,
or deviceId. With none, the session default simulator is used. The console decides whether the
target is a simulator or a device and calls simctl, devicectl, or the device tunnel for it.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server.workflows.common import (
    READ,
    WRITE,
    RunFn,
    compact,
    host,
    split_host_image,
    target_args,
)


def register(mcp: FastMCP, run: RunFn) -> None:  # noqa: C901 - one block of tool definitions
    def call(
        name: str,
        routing: dict[str, Any],
        more: dict[str, Any],
        *,
        mutating: bool = False,
        confirm: bool = False,
    ) -> Any:
        logged = compact({**routing, **more, "confirm": confirm if mutating else None})

        def invoke() -> Any:
            args, error = target_args(**routing)
            if error:
                return error
            return host(name, {**args, **more}, mutating=mutating, confirm=confirm)

        return run(name, logged, invoke)

    def routing(
        client_id: str, udid: str, simulatorId: str, simulatorName: str, deviceId: str
    ) -> dict[str, Any]:
        return {
            "client_id": client_id,
            "udid": udid,
            "simulatorId": simulatorId,
            "simulatorName": simulatorName,
            "deviceId": deviceId,
        }

    @mcp.tool(annotations=READ)
    def list_targets() -> dict[str, Any]:
        """Every simulator and paired device with kind, platform, state, and capabilities."""
        return run("list_targets", {}, lambda: host("list_targets", {}))

    @mcp.tool(annotations=READ)
    def resolve_target(
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
    ) -> dict[str, Any]:
        """Which simulator or device an app (client_id) or id points at, and what it supports.

        Device apps are matched by model, OS, and installed bundle id; ambiguity is refused.
        """
        return call(
            "resolve_target", routing(client_id, udid, simulatorId, simulatorName, deviceId), {}
        )

    @mcp.tool(annotations=READ)
    def host_environment() -> dict[str, Any]:
        """Installed Xcode and a suggested -destination per platform from what is installed."""
        return run("host_environment", {}, lambda: host("host_environment", {}))

    @mcp.tool(annotations=READ)
    def screenshot(
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        maxDimension: int = 1000,
    ) -> dict[str, Any] | list[Any]:
        """PNG of a simulator or device screen, shrunk to maxDimension px (0 keeps full size).

        Devices on iOS 17+ need the device tunnel (device_tunnel_start). For an app with the
        SDK, app_inspect ui.screenshot also works without the tunnel.
        """
        result = call(
            "screenshot",
            routing(client_id, udid, simulatorId, simulatorName, deviceId),
            {"maxDimension": maxDimension},
        )
        return split_host_image(result) if isinstance(result, dict) else result

    @mcp.tool(annotations=READ)
    def record_video(
        action: str = "start",
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
    ) -> dict[str, Any]:
        """Start or stop a screen recording on a simulator or a USB iPhone or iPad.

        stop returns the file path. Apple TV hardware cannot be recorded; use its simulator.
        """
        return call(
            "record_video",
            routing(client_id, udid, simulatorId, simulatorName, deviceId),
            {"action": action},
        )

    @mcp.tool(annotations=WRITE)
    def launch_app(
        bundleId: str = "",
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        launchArgs: list[str] | None = None,
        env: dict[str, str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Launch (or relaunch) an installed app on a simulator or device. confirm=true."""
        more = {"bundleId": bundleId, "launchArgs": launchArgs or [], "env": env or {}}
        return call(
            "launch_app",
            routing(client_id, udid, simulatorId, simulatorName, deviceId),
            more,
            mutating=True,
            confirm=confirm,
        )

    @mcp.tool(annotations=WRITE)
    def stop_app(
        bundleId: str = "",
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Terminate an app on a simulator or device. confirm=true."""
        return call(
            "stop_app",
            routing(client_id, udid, simulatorId, simulatorName, deviceId),
            {"bundleId": bundleId},
            mutating=True,
            confirm=confirm,
        )

    @mcp.tool(annotations=WRITE)
    def install_app(
        appPath: str,
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Install a built .app on a simulator or device. confirm=true."""
        return call(
            "install_app",
            routing("", udid, simulatorId, simulatorName, deviceId),
            {"appPath": appPath},
            mutating=True,
            confirm=confirm,
        )

    @mcp.tool(annotations=WRITE)
    def build_run(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        launchArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build, install, and launch on a simulator or device. Returns a job; poll wait_job."""
        more = {
            "scheme": scheme,
            "projectPath": projectPath,
            "workspacePath": workspacePath,
            "configuration": configuration,
            "extraArgs": extraArgs or [],
            "launchArgs": launchArgs or [],
            "waitSeconds": waitSeconds,
        }
        return call(
            "build_run",
            routing("", udid, simulatorId, simulatorName, deviceId),
            more,
            mutating=True,
            confirm=confirm,
        )

    @mcp.tool(annotations=WRITE)
    def open_url(
        url: str,
        bundleId: str = "",
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Open a URL or deep link on a simulator or device. Devices need the app's bundleId.

        Only http, https, and custom schemes; no credentials in the URL. confirm=true.
        """
        return call(
            "open_url",
            routing(client_id, udid, simulatorId, simulatorName, deviceId),
            {"url": url, "bundleId": bundleId},
            mutating=True,
            confirm=confirm,
        )
