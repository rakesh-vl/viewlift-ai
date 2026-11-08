"""LLDB on simulator and device apps: attach, breakpoints, stack, variables, commands, detach.

Every tool pauses or inspects a live process, so each needs confirm: true. Output passes through
the same masking as flows, but variables can still show app data; prefer test accounts.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server.workflows.common import DESTRUCTIVE, READ, WRITE, RunFn, host


def register(mcp: FastMCP, run: RunFn) -> None:
    def call(name: str, args: dict[str, Any], confirm: bool, *, mutating: bool = True) -> Any:
        return run(
            name,
            {**args, "confirm": confirm},
            lambda: host(name, args, mutating=mutating, confirm=confirm),
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    def debug_attach_sim(
        bundleId: str = "",
        pid: int | None = None,
        simulatorId: str = "",
        simulatorName: str = "",
        sessionId: str = "default",
        continueAfterAttach: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Attach LLDB to a running simulator app by bundleId or pid."""
        args = {
            "bundleId": bundleId,
            "pid": pid,
            "simulatorId": simulatorId,
            "simulatorName": simulatorName,
            "sessionId": sessionId,
            "continueAfterAttach": continueAfterAttach,
        }
        return call("debug_attach_sim", args, confirm)

    @mcp.tool(annotations=DESTRUCTIVE)
    def debug_attach_device(
        bundleId: str = "",
        pid: int | None = None,
        deviceId: str = "",
        sessionId: str = "default",
        continueAfterAttach: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Attach LLDB to a running app on a paired iPhone, iPad, or Apple TV.

        Needs Xcode 16 or later and a development-signed app. Find the pid with
        list_device_processes, or pass bundleId.
        """
        args = {
            "bundleId": bundleId,
            "pid": pid,
            "deviceId": deviceId,
            "sessionId": sessionId,
            "continueAfterAttach": continueAfterAttach,
        }
        return call("debug_attach_device", args, confirm)

    @mcp.tool(annotations=WRITE)
    def debug_breakpoint_add(
        file: str = "",
        line: int = 0,
        function: str = "",
        condition: str = "",
        sessionId: str = "default",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a breakpoint at file:line or on a function name, with an optional condition."""
        args = {
            "file": file,
            "line": line,
            "function": function,
            "condition": condition,
            "sessionId": sessionId,
        }
        return call("debug_breakpoint_add", args, confirm)

    @mcp.tool(annotations=WRITE)
    def debug_breakpoint_remove(
        breakpointId: int, sessionId: str = "default", confirm: bool = False
    ) -> dict[str, Any]:
        """Delete one breakpoint."""
        return call(
            "debug_breakpoint_remove",
            {"breakpointId": breakpointId, "sessionId": sessionId},
            confirm,
        )

    @mcp.tool(annotations=WRITE)
    def debug_continue(
        waitForStopSeconds: float = 0, sessionId: str = "default", confirm: bool = False
    ) -> dict[str, Any]:
        """Resume the process. waitForStopSeconds (max 25) waits for the next breakpoint hit."""
        args = {"waitForStopSeconds": waitForStopSeconds, "sessionId": sessionId}
        return call("debug_continue", args, confirm)

    @mcp.tool(annotations=WRITE)
    def debug_stack(
        threadIndex: int | None = None,
        maxFrames: int = 25,
        allThreads: bool = False,
        sessionId: str = "default",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Backtrace of the stopped thread, one thread, or all threads."""
        args = {
            "threadIndex": threadIndex,
            "maxFrames": maxFrames,
            "allThreads": allThreads,
            "sessionId": sessionId,
        }
        return call("debug_stack", args, confirm)

    @mcp.tool(annotations=WRITE)
    def debug_variables(
        frameIndex: int = 0, depth: int = 1, sessionId: str = "default", confirm: bool = False
    ) -> dict[str, Any]:
        """Local variables of one frame (depth 0-4). Secrets are masked."""
        args = {"frameIndex": frameIndex, "depth": depth, "sessionId": sessionId}
        return call("debug_variables", args, confirm)

    @mcp.tool(annotations=DESTRUCTIVE)
    def debug_lldb_command(
        command: str, timeout_s: float = 20, sessionId: str = "default", confirm: bool = False
    ) -> dict[str, Any]:
        """One LLDB command (po, expr, image lookup, ...). Commands that run code on the Mac
        (script, shell, platform shell, process launch) are refused."""
        args = {"command": command, "timeout_s": timeout_s, "sessionId": sessionId}
        return call("debug_lldb_command", args, confirm)

    @mcp.tool(annotations=WRITE)
    def debug_detach(sessionId: str = "default", confirm: bool = False) -> dict[str, Any]:
        """Detach LLDB and let the app run on."""
        return call("debug_detach", {"sessionId": sessionId}, confirm)

    @mcp.tool(annotations=READ)
    def debug_sessions() -> dict[str, Any]:
        """Open LLDB sessions with their pid and bundle id."""
        return run("debug_sessions", {}, lambda: host("debug_sessions", {}))
