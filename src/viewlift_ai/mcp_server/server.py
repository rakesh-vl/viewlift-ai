"""MCP server. Requires the optional `mcp` extra. Tools live in tools.py."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.types import ToolAnnotations

from viewlift_ai.mcp_server import guard, tools, workflows
from viewlift_ai.mcp_server.catalog import read_commands, write_commands
from viewlift_ai.mcp_server.client import ConsoleClient
from viewlift_ai.mcp_server.runtime import transport_choice

mcp = FastMCP(
    "viewlift-ai",
    instructions=(
        "Local Viewlift AI debug console. Several apps can share one device. "
        "Call console_status or list_apps first, then pass that app's client_id. "
        "Never assume the most recent app. Start broad with triage_app, network_summary, "
        "failed_requests, or summarize_logs, then drill in with get_flow, get_log, and "
        "app_inspect. Use wait_for_flow or wait_for_log after a UI action. "
        "Mutating tools require confirm=true; say what will change first. "
        "set_network_profile needs a client_id and shapes that app through the SDK. "
        "Device tools take a hardware UDID or the app's client_id, never an app device_key. "
        "Credentials, cookies, API keys, signed URL parameters, emails, phone numbers, "
        "user ids, device ids, and IP addresses are always masked as [REDACTED]. "
        "Never ask the user to paste them. The local web dashboard can still show them. "
        "Extra tools are off. Call manage_workflows with the one group you need "
        "(project, targets, simulator, device, macos, ui, lldb, swiftpm, coverage, docs, previews) "
        "and disable it when that task ends. Call describe_command for on-device payloads."
    ),
)

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WAIT = ToolAnnotations(readOnlyHint=True, idempotentHint=False, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def _api() -> ConsoleClient:
    return ConsoleClient()


def _run(name: str, args: dict[str, Any], call: Callable[[], Any]) -> Any:
    started = time.monotonic()
    result = call()
    guard.AUDIT.record(name, args, result, (time.monotonic() - started) * 1000)
    return result


@mcp.tool(annotations=_READ)
def console_status() -> dict[str, Any]:
    """Health check: console reachability, online apps, reveal lock, active rules, and policy."""
    return _run("console_status", {}, lambda: tools.console_status(_api()))


@mcp.tool(annotations=_READ)
def security_policy() -> dict[str, Any]:
    """What this server always masks, and whether read-only or reveal switches are on."""
    return guard.policy()


@mcp.tool(annotations=_READ)
def list_devices() -> dict[str, Any]:
    """Group apps by device. One phone or simulator can host several apps."""
    return _run("list_devices", {}, lambda: tools.list_devices(_api()))


@mcp.tool(annotations=_READ)
def list_apps(
    device_key: str = "",
    bundle_id: str = "",
    online_only: bool = False,
    auth_state: str = "",
) -> dict[str, Any]:
    """List apps. Optional filters: device_key, bundle_id, online_only, auth_state.

    auth_state is cold_start, guest, logged_in, tve, or unknown. Two phones running the
    same app have different client_id and device_key values. Account ids stay on the dashboard.
    """
    args = {
        "device_key": device_key,
        "bundle_id": bundle_id,
        "online_only": online_only,
        "auth_state": auth_state,
    }
    return _run("list_apps", args, lambda: tools.list_apps(_api(), **args))


@mcp.tool(annotations=_READ)
def get_app(client_id: str) -> dict[str, Any]:
    """Identity, capabilities, url schemes, and frameworks for one app."""
    return _run("get_app", {"client_id": client_id}, lambda: tools.get_app(_api(), client_id))


@mcp.tool(annotations=_READ)
def triage_app(client_id: str, include: list[str] | None = None) -> dict[str, Any]:
    """One-shot health report for one app. Start here when the bug is vague.

    include: any of performance, memory, hangs, crashes, screen, network, logs (default all).
    """
    args = {"client_id": client_id, "include": include}
    return _run("triage_app", args, lambda: tools.triage_app(_api(), **args))


@mcp.tool(annotations=_READ)
def crash_reports(client_id: str, limit: int = 3) -> dict[str, Any]:
    """Crashes for one app, newest first, with the whole stack as file:line per frame.

    Works after the app has died. Covers Swift traps, SIGSEGV, SIGABRT, uncaught exceptions,
    and macOS crash reports. Check `symbolication`: `missing_dsym` means build the app in Debug
    on this Mac so file and line can be resolved.
    """
    args = {"client_id": client_id, "limit": limit}
    return _run("crash_reports", args, lambda: tools.crash_reports(_api(), **args))


@mcp.tool(annotations=_READ)
def crash_detail(client_id: str, crash_id: str = "") -> dict[str, Any] | list[Any]:
    """Fix one crash: plain-language cause, crash site with code, stack, steps, and timeline.

    Defaults to the newest crash. Returns the last screen before the crash as an image when a
    replay was recorded. `brief` is a markdown report you can follow step by step. Open
    `crash_site.path` at `crash_site.line` first.
    """
    args = {"client_id": client_id, "crash_id": crash_id}
    result = _run("crash_detail", args, lambda: tools.crash_detail(_api(), **args))
    cleaned, poster = tools.split_poster(result)
    if poster is None:
        return cleaned
    return [cleaned, Image(data=poster, format="jpeg")]


@mcp.tool(annotations=_READ)
def list_flows(
    client_id: str,
    method: str = "",
    host: str = "",
    status: int | None = None,
    text: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Redacted HTTP summaries for one app. client_id is required."""
    args = {
        "client_id": client_id,
        "method": method,
        "host": host,
        "status": status,
        "text": text,
        "limit": limit,
    }
    return _run("list_flows", args, lambda: tools.list_flows(_api(), **args))


@mcp.tool(annotations=_READ)
def get_flow(flow_id: str, client_id: str, reveal: bool = False) -> dict[str, Any]:
    """One flow for that app. Credentials stay masked even with reveal."""
    args = {"flow_id": flow_id, "client_id": client_id, "reveal": reveal}
    return _run("get_flow", args, lambda: tools.get_flow(_api(), **args))


@mcp.tool(annotations=_READ)
def network_summary(client_id: str, host: str = "", limit: int = 300) -> dict[str, Any]:
    """Per-host failure rate and p50/p95 latency, slowest, largest, and repeated requests."""
    args = {"client_id": client_id, "host": host, "limit": limit}
    return _run("network_summary", args, lambda: tools.network_summary(_api(), **args))


@mcp.tool(annotations=_READ)
def failed_requests(client_id: str, host: str = "", limit: int = 300) -> dict[str, Any]:
    """4xx, 5xx, transport errors, and TLS failures grouped by endpoint, with flow ids."""
    args = {"client_id": client_id, "host": host, "limit": limit}
    return _run("failed_requests", args, lambda: tools.failed_requests(_api(), **args))


@mcp.tool(annotations=_READ)
def compare_flows(flow_a: str, flow_b: str, client_id: str) -> dict[str, Any]:
    """Diff two flows: status, timing, headers, and JSON body paths. Good for works-vs-fails."""
    args = {"flow_a": flow_a, "flow_b": flow_b, "client_id": client_id}
    return _run("compare_flows", args, lambda: tools.compare_flows(_api(), **args))


@mcp.tool(annotations=_READ)
def export_request(flow_id: str, client_id: str, language: str = "curl") -> dict[str, Any]:
    """Rebuild one request as curl, swift (URLSession), or python. Secrets stay masked."""
    args = {"flow_id": flow_id, "client_id": client_id, "language": language}
    return _run("export_request", args, lambda: tools.export_request(_api(), **args))


@mcp.tool(annotations=_WAIT)
def wait_for_flow(
    client_id: str,
    host: str = "",
    method: str = "",
    status: int | None = None,
    text: str = "",
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Block until a new matching request arrives (max 30 s). Call right after a UI action."""
    args = {
        "client_id": client_id,
        "host": host,
        "method": method,
        "status": status,
        "text": text,
        "timeout_s": timeout_s,
    }
    return _run("wait_for_flow", args, lambda: tools.wait_for_flow(_api(), **args))


@mcp.tool(annotations=_READ)
def list_logs(
    client_id: str,
    level: str = "",
    subsystem: str = "",
    text: str = "",
    process: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """SDK logs for one app, plus syslog lines that mention its bundle id or executable."""
    args = {
        "client_id": client_id,
        "level": level,
        "subsystem": subsystem,
        "text": text,
        "process": process,
        "limit": limit,
    }
    return _run("list_logs", args, lambda: tools.list_logs(_api(), **args))


@mcp.tool(annotations=_READ)
def get_log(log_id: str, client_id: str) -> dict[str, Any]:
    """Full text of one log line and the flow id it links to."""
    args = {"log_id": log_id, "client_id": client_id}
    return _run("get_log", args, lambda: tools.get_log(_api(), **args))


@mcp.tool(annotations=_READ)
def summarize_logs(client_id: str, level: str = "", limit: int = 500) -> dict[str, Any]:
    """Log counts by level and subsystem, and the most frequent error signatures."""
    args = {"client_id": client_id, "level": level, "limit": limit}
    return _run("summarize_logs", args, lambda: tools.summarize_logs(_api(), **args))


@mcp.tool(annotations=_WAIT)
def wait_for_log(
    client_id: str, text: str, level: str = "", timeout_s: float = 15.0
) -> dict[str, Any]:
    """Block until a new SDK log line containing text arrives (max 30 s)."""
    args = {"client_id": client_id, "text": text, "level": level, "timeout_s": timeout_s}
    return _run("wait_for_log", args, lambda: tools.wait_for_log(_api(), **args))


@mcp.tool(annotations=_READ)
def list_packages(client_id: str) -> dict[str, Any]:
    """Swift packages for one app, found from its Xcode build."""
    args = {"client_id": client_id}
    return _run("list_packages", args, lambda: tools.list_packages(_api(), client_id))


@mcp.tool(annotations=_READ)
def list_debug_events(client_id: str, module: str = "", limit: int = 50) -> dict[str, Any]:
    """Debug events posted by one app."""
    args = {"client_id": client_id, "module": module, "limit": limit}
    return _run("list_debug_events", args, lambda: tools.list_debug_events(_api(), **args))


@mcp.tool(annotations=_READ)
def describe_command(command: str = "") -> dict[str, Any]:
    """Payload notes for app_inspect and app_mutate commands. Empty command lists all."""
    return tools.describe_command(command)


@mcp.tool(annotations=_READ)
def app_inspect(
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Run one read-only on-device command and wait for the result. client_id is required.

    Call describe_command for the command list and payloads. ui.screenshot and bug.capture
    also return an image. Pass an explorer handle back as payload.handle to drill in.
    """
    args = {"client_id": client_id, "command": command, "payload": payload}
    result = _run("app_inspect", args, lambda: tools.app_inspect(_api(), **args))
    cleaned, image = tools.split_image(result)
    if image is None:
        return cleaned
    return [cleaned, Image(data=image, format=tools.image_format(image))]


@mcp.tool(annotations=_READ)
def appcms_section(
    client_id: str, name: str = "", args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """AppCMS host state by section. Empty name lists sections and actions.

    Sections include overview, config, environment, pages, session, subscription,
    entitlements, tve, devices, analytics, player. Tokens arrive as presence and length only.
    entitlements accepts args {"content_id": "..."} to explain why a paywall shows.
    """
    args = {"client_id": client_id, "name": name, "args": args}
    return _run("appcms_section", args, lambda: tools.appcms_section(_api(), **args))


@mcp.tool(annotations=_READ)
def appcms_diagnostics(client_id: str) -> dict[str, Any]:
    """AppCMS bug findings: isSubscribed drift, stale login keys, device ID mismatch, and more."""
    args = {"client_id": client_id}
    return _run("appcms_diagnostics", args, lambda: tools.appcms_diagnostics(_api(), **args))


@mcp.tool(annotations=_DESTRUCTIVE)
def appcms_action(
    client_id: str,
    name: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Run one whitelisted AppCMS debug action. confirm must be true.

    Names come from appcms_section() actions, for example refresh_token, expire_token,
    refetch_subscription, refresh_entitlements, override_flag, switch_language, force_logout.
    """
    args = {"client_id": client_id, "name": name, "payload": payload, "confirm": confirm}
    return _run("appcms_action", args, lambda: tools.appcms_action(_api(), **args))


@mcp.tool(annotations=_READ)
def host_section(
    client_id: str, name: str = "", args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Debug-host state by section for any app that registers OTTDebugHost.

    Empty name returns the provider (AppCMS, PlayerSample, AuthSample, AnalyticsSample),
    its pages with titles, and its actions with input fields. Tokens arrive as metadata only;
    emails, user ids, and device ids arrive as [REDACTED].
    """
    args = {"client_id": client_id, "name": name, "args": args}
    return _run("host_section", args, lambda: tools.host_section(_api(), **args))


@mcp.tool(annotations=_READ)
def host_diagnostics(client_id: str) -> dict[str, Any]:
    """Rule-based findings from the app's debug host: playback errors, token expiry, drift."""
    args = {"client_id": client_id}
    return _run("host_diagnostics", args, lambda: tools.host_diagnostics(_api(), **args))


@mcp.tool(annotations=_DESTRUCTIVE)
def host_action(
    client_id: str,
    name: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Run one whitelisted debug-host action. confirm must be true.

    Names and input fields come from host_section() with an empty name, for example
    seek {seconds}, toggle_captions, logout, send_test_event {name}.
    """
    args = {"client_id": client_id, "name": name, "payload": payload, "confirm": confirm}
    return _run("host_action", args, lambda: tools.host_action(_api(), **args))


@mcp.tool(annotations=_DESTRUCTIVE)
def app_mutate(
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Change one running app. confirm must be true.

    Call describe_command for the write commands and their payloads.
    """
    args = {"client_id": client_id, "command": command, "payload": payload, "confirm": confirm}
    return _run("app_mutate", args, lambda: tools.app_mutate(_api(), **args))


@mcp.tool(annotations=_WRITE)
def set_network_profile(profile: str, client_id: str = "", confirm: bool = False) -> dict[str, Any]:
    """Shape one app's traffic: wifi, 4g, 3g, edge, gprs, offline, or off.

    client_id is required. The SDK applies the profile on that app's simulator or device.
    """
    args = {"profile": profile, "client_id": client_id, "confirm": confirm}
    return _run("set_network_profile", args, lambda: tools.set_network_profile(_api(), **args))


@mcp.tool(annotations=_WRITE)
def open_deeplink(url: str, client_id: str, confirm: bool = False) -> dict[str, Any]:
    """Open a URL for this app on its simulator, iPhone, iPad, or Apple TV."""
    args = {"url": url, "client_id": client_id, "confirm": confirm}
    return _run("open_deeplink", args, lambda: tools.open_deeplink(_api(), **args))


@mcp.tool(annotations=_WRITE)
def set_location(
    client_id: str = "",
    udid: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    clear: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Simulate GPS where client_id runs (simulator or device), or on a udid from list_targets."""
    args = {
        "client_id": client_id,
        "udid": udid,
        "latitude": latitude,
        "longitude": longitude,
        "clear": clear,
        "confirm": confirm,
    }
    return _run("set_location", args, lambda: tools.set_location(_api(), **args))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def replay_flow(flow_id: str, confirm: bool = False) -> dict[str, Any]:
    """Resend one captured request. Fails unless reveal is already unlocked. Does not unlock it."""
    args = {"flow_id": flow_id, "confirm": confirm}
    return _run("replay_flow", args, lambda: tools.replay_flow(_api(), **args))


@mcp.tool(annotations=_READ)
def get_session_audit(limit: int = 50) -> dict[str, Any]:
    """Tool calls this MCP session made, newest first, with scrubbed arguments."""
    return {"calls": guard.AUDIT.rows(limit)}


GATE = workflows.register_all(mcp, _run)


@mcp.tool(annotations=_WRITE)
async def manage_workflows(
    ctx: Context,
    enable: list[str] | None = None,
    disable: list[str] | None = None,
) -> dict[str, Any]:
    """Turn tool groups on or off for this chat. Does not change the app or other chats.

    Workflows: project, targets, simulator, device, macos, ui, lldb, swiftpm, coverage, docs,
    previews.
    Pass ["all"] to enable everything. Disable a group when that task ends.
    """
    session = ctx.session
    current = GATE.enabled_for(session)
    wanted_on = set(enable or [])
    if "all" in wanted_on:
        wanted_on = set(workflows.WORKFLOW_IDS)
    target = (current | wanted_on) - set(disable or [])
    changed = target != current
    if changed:
        GATE.apply(session, target)
        try:
            await session.send_tool_list_changed()
        except Exception:  # noqa: BLE001 - older clients may not accept the notification
            pass
    status = GATE.status(session)
    status["changed"] = changed
    guard.AUDIT.record("manage_workflows", {"enable": enable, "disable": disable}, status, 0.0)
    return status


@mcp.resource("viewlift://commands", mime_type="application/json")
def commands_resource() -> str:
    """Every on-device command with its payload notes."""
    return json.dumps({"read": read_commands(), "write": write_commands()}, indent=2)


@mcp.resource("viewlift://policy", mime_type="application/json")
def policy_resource() -> str:
    """Masking rules and policy switches for this server."""
    return json.dumps(guard.policy(), indent=2)


@mcp.prompt()
def triage_bug(client_id: str, symptom: str) -> str:
    """Guided first pass on a reported bug."""
    return (
        f"Debug this symptom in app {client_id}: {symptom}\n"
        "1. Call triage_app to collect performance, memory, hangs, crashes, network, and logs.\n"
        "2. Read failed_requests and summarize_logs for anything tied to the symptom.\n"
        "3. Drill into the top suspects with get_flow, get_log, or app_inspect.\n"
        "4. Report the likely cause with flow ids, log ids, or stack frames as evidence, "
        "and propose a fix. Do not change app state unless the user agrees."
    )


@mcp.prompt()
def investigate_request(client_id: str, endpoint: str) -> str:
    """Find out why one API call fails or is slow."""
    return (
        f"Investigate requests to {endpoint} in app {client_id}.\n"
        "1. list_flows with host or text set to the endpoint.\n"
        "2. If both a working and a failing call exist, run compare_flows on them.\n"
        "3. Check timing (connect, tls, ttfb, download) and repeated calls in network_summary.\n"
        "4. Use export_request with language=swift to show a minimal reproduction. "
        "Masked credentials stay masked."
    )


@mcp.prompt()
def check_screen(client_id: str) -> str:
    """Audit the screen the app is showing right now."""
    return (
        f"Audit the current screen of app {client_id}.\n"
        "1. app_inspect ui.screenshot and activity.snapshot.\n"
        "2. app_inspect accessibility.audit and assets.audit.\n"
        "3. app_inspect swiftui.renders and performance.snapshot for jank.\n"
        "4. List each problem with the view id and a concrete fix."
    )


@mcp.prompt()
def hunt_memory_leak(client_id: str, flow: str) -> str:
    """Find leaks around one user flow."""
    return (
        f"Hunt memory leaks in app {client_id} around this flow: {flow}\n"
        "1. app_inspect memory.heap to set a baseline.\n"
        "2. Ask the user to run the flow and return, then call memory.heap again for growth.\n"
        "3. app_inspect memory.leaks and memory.graph; open suspects with memory.object.\n"
        "4. Name the retain cycle and the line of code that should hold a weak reference."
    )


@mcp.prompt()
def build_and_verify(scheme: str, change: str) -> str:
    """Build, run, and check one change on a simulator."""
    return (
        f"Verify this change in scheme {scheme}: {change}\n"
        "0. manage_workflows enable project and simulator. Enable ui before screen_map or tap. "
        "Disable each group when finished.\n"
        "1. doctor, then session_show_defaults; set projectPath or workspacePath and scheme with "
        "session_set_defaults if missing (discover_projs and list_schemes help).\n"
        "2. Tell the user you will build and launch, then build_run_sim with confirm=true. "
        "Poll wait_job until the job ends; fix any errors from the condensed summary.\n"
        "3. screen_map to see the screen, then tap or type_text by label to reach the change.\n"
        "4. Confirm with screen_map or screenshot, and list_logs or failed_requests if the app "
        "runs the SDK (list_apps gives its client_id)."
    )


@mcp.prompt()
def walk_screen_flow(flow: str, client_id: str = "") -> str:
    """Walk a user flow by meaning, not coordinates."""
    target = f"app {client_id}" if client_id else "the booted simulator"
    groups = "ui" if client_id else "ui and simulator"
    return (
        f"Walk this flow in {target}: {flow}\n"
        f"0. manage_workflows enable {groups}. Disable the group when the flow ends.\n"
        "1. screen_map to read the current screen.\n"
        "2. For each step, tap, type_text, or swipe using a label or identifier from the map. "
        "Say what you will do before each state change and pass confirm=true.\n"
        "3. After each step call wait_for_ui for the next screen's element, then screen_map.\n"
        "4. If a step fails, snapshot_ui with a role or text filter to find the element. "
        "Report where the flow breaks with the screen summary as evidence."
    )


@mcp.prompt()
def check_preview(client_id: str, name: str) -> str:
    """Render one SwiftUI preview in the running app and review it."""
    return (
        f"Check the {name} preview in app {client_id}.\n"
        "0. manage_workflows enable previews, and disable it when the check ends.\n"
        "1. list_previews to find it in source, and list_app_previews to confirm the app "
        "registered it (ViewliftPreviews.register).\n"
        "2. render_preview in light and dark, then look for clipping, contrast, and layout bugs.\n"
        "3. snapshot_previews with names=[name] to compare with the baseline; explain any change.\n"
        "4. Only call approve_preview_baseline after the user accepts the new look."
    )


def main() -> None:
    try:
        transport, host, port = transport_choice()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if port is not None:
        mcp.settings.host = host
        mcp.settings.port = port
    mcp.run(transport=transport)  # type: ignore[arg-type]
