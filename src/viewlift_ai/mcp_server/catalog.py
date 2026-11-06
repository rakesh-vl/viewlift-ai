"""Tool reference shown on the Agent docs page. No MCP SDK import."""

from __future__ import annotations

from dataclasses import dataclass

from viewlift_ai.services.debug_session import MUTATING_COMMANDS, READ_COMMANDS


@dataclass(frozen=True, slots=True)
class ToolDoc:
    name: str
    args: str
    summary: str
    changes_state: bool = False


@dataclass(frozen=True, slots=True)
class ToolGroup:
    id: str
    title: str
    summary: str
    tools: tuple[ToolDoc, ...]


TOOL_GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup(
        "discover",
        "Find the app",
        "Call one of these first. They never run anything on a device.",
        (
            ToolDoc(
                "list_devices",
                "",
                "Apps grouped by device_key. One phone can hold many apps.",
            ),
            ToolDoc(
                "list_apps",
                "device_key?, bundle_id?, online_only?",
                "Flat list with client_id, bundle id, device, and online state.",
            ),
            ToolDoc(
                "get_app",
                "client_id",
                "Version, build, capabilities, URL schemes, and linked frameworks.",
            ),
        ),
    ),
    ToolGroup(
        "mac",
        "Read captured data",
        "Data the console already stored on this Mac. Bodies and headers stay redacted.",
        (
            ToolDoc(
                "list_flows",
                "client_id, method?, host?, status?, text?, limit?",
                "HTTP summaries for that app only.",
            ),
            ToolDoc(
                "get_flow",
                "flow_id, client_id, reveal?",
                "One request with headers, body, and timing. Refuses a flow from another app.",
            ),
            ToolDoc(
                "list_logs",
                "client_id, level?, subsystem?, text?, process?, limit?",
                "SDK logs for the app, plus syslog lines that mention its bundle id.",
            ),
            ToolDoc(
                "list_packages",
                "client_id",
                "Swift packages resolved for the app's Xcode build.",
            ),
            ToolDoc(
                "list_debug_events",
                "client_id, module?, limit?",
                "Recent events and command results the app posted.",
            ),
        ),
    ),
    ToolGroup(
        "inspect",
        "Ask the running app",
        "One tool sends a read command to the app and waits up to 12 seconds for the answer.",
        (
            ToolDoc(
                "app_inspect",
                "client_id, command, payload?",
                "Runs one read command. Screenshots come back as an image.",
            ),
        ),
    ),
    ToolGroup(
        "change",
        "Change state",
        "Every tool here needs confirm: true. The agent should say what it will change first.",
        (
            ToolDoc(
                "app_mutate",
                "client_id, command, payload?, confirm",
                "Runs one write command inside that app.",
                True,
            ),
            ToolDoc(
                "set_network_profile",
                "profile, confirm",
                "wifi, 4g, 3g, edge, gprs, offline, or off. Throttles every proxied app.",
                True,
            ),
            ToolDoc(
                "open_deeplink",
                "url, client_id, confirm",
                "Opens a URL on the simulator that runs that app. Simulators only.",
                True,
            ),
            ToolDoc(
                "set_location",
                "udid, latitude?, longitude?, clear?, confirm",
                "Simulated GPS on a USB-paired iPhone. Takes the hardware UDID.",
                True,
            ),
            ToolDoc(
                "replay_flow",
                "flow_id, confirm",
                "Resends one captured request. Needs reveal unlocked in the console.",
                True,
            ),
        ),
    ),
)

COMMAND_NOTES: dict[str, str] = {
    "performance.snapshot": "CPU, FPS, and memory right now",
    "memory.snapshot": "Footprint and live view controller counts",
    "memory.graph": "Object counts for leak hunting",
    "ui.hierarchy": "View tree with frames and ids",
    "ui.describe": "One view's class, properties, and SwiftUI or UIKit role (payload: id)",
    "ui.screenshot": "PNG of the key window",
    "swiftui.renders": "Re-render counts per tracked view",
    "assets.audit": "Images larger than their display size",
    "accessibility.audit": "Missing labels and small touch targets",
    "storage.defaults": "UserDefaults keys and values",
    "storage.keychain_metadata": "Keychain items without secret values",
    "storage.files": "Sandbox listing (payload: path)",
    "storage.file_read": "One sandbox file (payload: path)",
    "storage.sqlite_schema": "Tables of one database (payload: path)",
    "storage.sqlite_query": "SELECT, PRAGMA, WITH, or EXPLAIN only",
    "feature_flags.list": "Debug flag values and overrides",
    "runtime.search": "Objective-C classes (payload: query)",
    "runtime.describe": "Methods and properties (payload: class)",
    "macro.list": "Recorded touch macros",
    "bug.capture": "Screenshot plus performance and memory",
    "ott.snapshot": "User, plan, TVE, theme, modules, metadataMap",
    "hang.list": "Main-thread hangs with stack frames",
    "crash.list": "In-process exceptions with frames",
    "ui.highlight": "Outline one view (payload: id)",
    "ui.edit": "Change a view property (payload: id plus fields)",
    "storage.defaults_set": "Write one key (payload: key, value)",
    "storage.file_write": "Write a sandbox file (payload: path, content)",
    "storage.snapshot_create": "Save defaults and files (payload: paths)",
    "storage.snapshot_restore": "Restore a saved snapshot",
    "feature_flags.set": "Override a flag (payload: key, enabled)",
    "feature_flags.reset": "Drop an override (payload: key)",
    "macro.record_start": "Start recording touches",
    "macro.record_stop": "Stop and save the macro",
    "macro.replay": "Replay a recorded macro",
    "push.local": "Schedule a local notification",
}


def tool_names() -> list[str]:
    return [tool.name for group in TOOL_GROUPS for tool in group.tools]


def command_rows(names: set[str]) -> list[dict[str, str]]:
    return [{"name": name, "note": COMMAND_NOTES.get(name, "")} for name in sorted(names)]


def read_commands() -> list[dict[str, str]]:
    return command_rows(READ_COMMANDS)


def write_commands() -> list[dict[str, str]]:
    return command_rows(MUTATING_COMMANDS)
