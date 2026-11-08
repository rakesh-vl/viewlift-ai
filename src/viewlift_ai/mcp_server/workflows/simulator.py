"""Simulator control: list, boot, erase, appearance, location, status bar, apps, media."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server.workflows.common import (
    DESTRUCTIVE,
    READ,
    WRITE,
    RunFn,
    host,
)


def register(mcp: FastMCP, run: RunFn) -> None:
    def call(
        name: str, args: dict[str, Any], *, mutating: bool = False, confirm: bool = False
    ) -> Any:
        logged = {**args, "confirm": confirm} if mutating else args
        return run(name, logged, lambda: host(name, args, mutating=mutating, confirm=confirm))

    def sim(simulatorId: str, simulatorName: str, **more: Any) -> dict[str, Any]:
        return {"simulatorId": simulatorId, "simulatorName": simulatorName, **more}

    @mcp.tool(annotations=READ)
    def list_sims(platform: str = "", bootedOnly: bool = False) -> dict[str, Any]:
        """Available simulators with udid, state, platform, and OS version. Booted first."""
        return call("list_sims", {"platform": platform, "bootedOnly": bootedOnly})

    @mcp.tool(annotations=WRITE)
    def boot_sim(
        simulatorId: str = "",
        simulatorName: str = "",
        open: bool = True,
        confirm: bool = False,  # noqa: A002
    ) -> dict[str, Any]:
        """Boot a simulator and wait until it is ready. confirm must be true."""
        return call(
            "boot_sim", sim(simulatorId, simulatorName, open=open), mutating=True, confirm=confirm
        )

    @mcp.tool(annotations=WRITE)
    def open_sim(confirm: bool = False) -> dict[str, Any]:
        """Bring the Simulator app to the front."""
        return call("open_sim", {}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=DESTRUCTIVE)
    def erase_sims(
        simulatorIds: list[str] | None = None,
        simulatorId: str = "",
        simulatorName: str = "",
        shutdownFirst: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Erase all content and settings of one or more simulators. Cannot be undone."""
        args = sim(
            simulatorId, simulatorName, simulatorIds=simulatorIds or [], shutdownFirst=shutdownFirst
        )
        return call("erase_sims", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def set_sim_appearance(
        mode: str, simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Switch a simulator to light or dark mode."""
        return call(
            "set_sim_appearance",
            sim(simulatorId, simulatorName, mode=mode),
            mutating=True,
            confirm=confirm,
        )

    @mcp.tool(annotations=WRITE)
    def set_sim_location(
        latitude: float,
        longitude: float,
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a simulator's GPS location. For a paired iPhone use set_location instead."""
        args = sim(simulatorId, simulatorName, latitude=latitude, longitude=longitude)
        return call("set_sim_location", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def reset_sim_location(
        simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Clear a simulated location."""
        return call(
            "reset_sim_location", sim(simulatorId, simulatorName), mutating=True, confirm=confirm
        )

    @mcp.tool(annotations=WRITE)
    def sim_statusbar(
        simulatorId: str = "",
        simulatorName: str = "",
        time: str = "",
        dataNetwork: str = "",
        wifiBars: int | None = None,
        cellularBars: int | None = None,
        batteryState: str = "",
        batteryLevel: int | None = None,
        operatorName: str = "",
        clear: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Override the status bar (time 9:41, full bars) for clean screenshots, or clear it."""
        args = sim(
            simulatorId,
            simulatorName,
            time=time,
            dataNetwork=dataNetwork,
            wifiBars=wifiBars,
            cellularBars=cellularBars,
            batteryState=batteryState,
            batteryLevel=batteryLevel,
            operatorName=operatorName,
            clear=clear,
        )
        return call("sim_statusbar", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def toggle_software_keyboard(confirm: bool = False) -> dict[str, Any]:
        """Show or hide the on-screen keyboard in Simulator (Cmd+K)."""
        return call("toggle_software_keyboard", {}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def toggle_connect_hardware_keyboard(confirm: bool = False) -> dict[str, Any]:
        """Connect or disconnect the Mac keyboard in Simulator (Shift+Cmd+K)."""
        return call("toggle_connect_hardware_keyboard", {}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def install_app_sim(
        appPath: str, simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Install a built .app on a simulator."""
        args = sim(simulatorId, simulatorName, appPath=appPath)
        return call("install_app_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def launch_app_sim(
        bundleId: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        launchArgs: list[str] | None = None,
        env: dict[str, str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Launch (or relaunch) an installed app on a simulator."""
        args = sim(
            simulatorId,
            simulatorName,
            bundleId=bundleId,
            launchArgs=launchArgs or [],
            env=env or {},
        )
        return call("launch_app_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def stop_app_sim(
        bundleId: str = "", simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Terminate an app on a simulator."""
        args = sim(simulatorId, simulatorName, bundleId=bundleId)
        return call("stop_app_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def record_sim_video(
        action: str = "start", simulatorId: str = "", simulatorName: str = ""
    ) -> dict[str, Any]:
        """Start or stop an H.264 screen recording. stop returns the .mp4 path."""
        return call("record_sim_video", sim(simulatorId, simulatorName, action=action))

    @mcp.tool(annotations=WRITE)
    def sim_push(
        payload: dict[str, Any],
        bundleId: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Deliver an APNs payload ({"aps": {...}}) to an app on a simulator."""
        args = sim(simulatorId, simulatorName, payload=payload, bundleId=bundleId)
        return call("sim_push", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def sim_privacy(
        action: str,
        service: str,
        bundleId: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Grant, revoke, or reset a privacy permission (camera, photos, location, ...)."""
        args = sim(simulatorId, simulatorName, action=action, service=service, bundleId=bundleId)
        return call("sim_privacy", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def sim_clone(
        name: str, simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Clone a simulator, for a clean copy to test on."""
        return call(
            "sim_clone", sim(simulatorId, simulatorName, name=name), mutating=True, confirm=confirm
        )
