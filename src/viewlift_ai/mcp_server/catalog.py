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
    workflow: str = "core"


WORKFLOW_IDS: tuple[str, ...] = (
    "project",
    "targets",
    "simulator",
    "device",
    "macos",
    "ui",
    "lldb",
    "swiftpm",
    "coverage",
    "docs",
    "previews",
)


TOOL_GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup(
        "discover",
        "Find the app",
        "Call one of these first. They never run anything on a device.",
        (
            ToolDoc(
                "console_status",
                "",
                "Console health, online apps, reveal lock, active rules, and agent policy.",
            ),
            ToolDoc(
                "security_policy",
                "",
                "What is always masked, and whether read-only or reveal switches are on.",
            ),
            ToolDoc(
                "list_devices",
                "",
                "Apps grouped by device_key. One phone can hold many apps.",
            ),
            ToolDoc(
                "list_apps",
                "device_key?, bundle_id?, online_only?, auth_state?",
                "Flat list with client_id, bundle id, device, model, auth state, and online state.",
            ),
            ToolDoc(
                "get_app",
                "client_id",
                "Version, build, capabilities, URL schemes, and linked frameworks.",
            ),
            ToolDoc(
                "describe_command",
                "command?",
                "Payload notes for app_inspect and app_mutate commands.",
            ),
            ToolDoc(
                "manage_workflows",
                "enable?, disable?",
                "Turns groups on or off for this chat. Disable a group when the task ends.",
            ),
        ),
    ),
    ToolGroup(
        "analyze",
        "Analyze",
        "Summaries an agent should read before opening single flows or logs.",
        (
            ToolDoc(
                "triage_app",
                "client_id, include?",
                "Performance, memory, hangs, crashes, screen, failed calls, and top errors.",
            ),
            ToolDoc(
                "crash_reports",
                "client_id, limit?",
                "Stored crashes with headline, crash site, and the whole stack as file:line.",
            ),
            ToolDoc(
                "crash_detail",
                "client_id, crash_id?",
                "One crash: cause, code at the crash line, repro steps, timeline, last screen.",
            ),
            ToolDoc(
                "network_summary",
                "client_id, host?, limit?",
                "Per-host failure rate, p50 and p95 latency, slowest, largest, and repeated calls.",
            ),
            ToolDoc(
                "failed_requests",
                "client_id, host?, limit?",
                "4xx, 5xx, transport, and TLS failures grouped by endpoint.",
            ),
            ToolDoc(
                "summarize_logs",
                "client_id, level?, limit?",
                "Log counts and the most frequent error signatures.",
            ),
            ToolDoc(
                "compare_flows",
                "flow_a, flow_b, client_id",
                "Diff of status, timing, headers, and JSON body paths between two requests.",
            ),
            ToolDoc(
                "export_request",
                "flow_id, client_id, language?",
                "One request as curl, Swift URLSession, or Python. Secrets stay masked.",
            ),
            ToolDoc(
                "wait_for_flow",
                "client_id, host?, method?, status?, text?, timeout_s?",
                "Waits up to 30 seconds for a new matching request.",
            ),
            ToolDoc(
                "wait_for_log",
                "client_id, text, level?, timeout_s?",
                "Waits up to 30 seconds for a new matching log line.",
            ),
        ),
    ),
    ToolGroup(
        "mac",
        "Read captured data",
        "Data the console already stored on this Mac. Credentials, cookies, and keys are masked.",
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
                "get_log",
                "log_id, client_id",
                "Full text of one log line and the flow it links to.",
            ),
            ToolDoc(
                "get_session_audit",
                "limit?",
                "Tool calls this MCP session made, with scrubbed arguments.",
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
            ToolDoc(
                "appcms_section",
                "client_id, name?, args?",
                "AppCMS state by section: config, env, pages, session, plan, entitlements, TVE.",
            ),
            ToolDoc(
                "appcms_diagnostics",
                "client_id",
                "Rule-based AppCMS bug findings: stale isSubscribed, login drift, ID mismatch.",
            ),
            ToolDoc(
                "host_section",
                "client_id, name?, args?",
                "Any debug host (Player, Auth, Analytics) by section; empty name lists pages.",
            ),
            ToolDoc(
                "host_diagnostics",
                "client_id",
                "Rule-based findings from the app's debug host.",
            ),
        ),
    ),
    ToolGroup(
        "change",
        "Change state",
        (
            "Every tool here needs confirm: true. The agent should say what it will change first. "
            "VIEWLIFT_AI_MCP_READ_ONLY=1 turns all of them off."
        ),
        (
            ToolDoc(
                "app_mutate",
                "client_id, command, payload?, confirm",
                "Runs one write command inside that app.",
                True,
            ),
            ToolDoc(
                "appcms_action",
                "client_id, name, payload?, confirm",
                "One whitelisted AppCMS debug action: token refresh or expiry, refetch, flags.",
                True,
            ),
            ToolDoc(
                "host_action",
                "client_id, name, payload?, confirm",
                "One whitelisted debug-host action, for example seek, logout, send_test_event.",
                True,
            ),
            ToolDoc(
                "set_network_profile",
                "profile, client_id, confirm",
                "wifi, 4g, 3g, edge, gprs, offline, or off. Shapes that app through the SDK.",
                True,
            ),
            ToolDoc(
                "open_deeplink",
                "url, client_id, confirm",
                "Opens a URL on the simulator or device that runs that app, or inside the app.",
                True,
            ),
            ToolDoc(
                "set_location",
                "client_id | udid, latitude?, longitude?, clear?, confirm",
                "Simulated GPS on the app's simulator or device (iOS 17+ needs the tunnel).",
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
    ToolGroup(
        "session",
        "Defaults and jobs",
        (
            "Part of the project workflow. Defaults save project, scheme, simulator, and device "
            "so later calls can omit them. Builds and tests run as jobs; poll them with wait_job."
        ),
        (
            ToolDoc("doctor", "", "Xcode, simctl, devicectl, lldb, AXe or idb, and project roots."),
            ToolDoc("session_show_defaults", "", "Active defaults profile."),
            ToolDoc(
                "session_set_defaults",
                "projectPath?, workspacePath?, scheme?, simulatorId?, deviceId?, bundleId?, ...",
                "Saves defaults for later calls. Local config only.",
            ),
            ToolDoc("session_clear_defaults", "keys?, all?", "Clears keys or profiles."),
            ToolDoc(
                "session_use_defaults_profile", "profile, create?", "Switches the defaults profile."
            ),
            ToolDoc("sync_xcode_defaults", "", "Uses the project Xcode built last as the default."),
            ToolDoc("get_job", "job_id, log_tail?", "State and condensed result of one job."),
            ToolDoc("wait_job", "job_id, timeout_s?", "Waits up to 30 seconds for a job."),
            ToolDoc("cancel_job", "job_id", "Stops a running job."),
            ToolDoc("list_jobs", "", "Recent jobs."),
        ),
        "project",
    ),
    ToolGroup(
        "project",
        "Projects",
        "Paths must sit inside a project root set on the Setup page.",
        (
            ToolDoc(
                "discover_projs",
                "workspaceRoot?, maxDepth?",
                "Xcode projects, workspaces, packages.",
            ),
            ToolDoc(
                "list_schemes", "projectPath | workspacePath", "Schemes, targets, configurations."
            ),
            ToolDoc(
                "show_build_settings",
                "scheme, projectPath | workspacePath, keys?, all?",
                "Key build settings per target.",
            ),
            ToolDoc(
                "get_app_bundle_id", "appPath", "Bundle id and version of an iOS or tvOS .app."
            ),
            ToolDoc("get_mac_bundle_id", "appPath", "Bundle id and version of a macOS .app."),
            ToolDoc(
                "clean", "scheme, projectPath | workspacePath, confirm", "xcodebuild clean.", True
            ),
            ToolDoc(
                "scaffold_ios_project",
                "projectName, outputPath, bundleIdentifier?, platform?, confirm",
                "New SwiftUI iOS or tvOS app project.",
                True,
            ),
            ToolDoc(
                "scaffold_macos_project",
                "projectName, outputPath, bundleIdentifier?, confirm",
                "New SwiftUI macOS app project.",
                True,
            ),
        ),
        "project",
    ),
    ToolGroup(
        "targets",
        "Any simulator or device",
        (
            "One call for a simulator or a paired iPhone, iPad, or Apple TV. Pick it with "
            "client_id, udid, simulatorId, or deviceId; the console routes to simctl, devicectl, "
            "or the device tunnel."
        ),
        (
            ToolDoc("list_targets", "", "Simulators and devices with platform and capabilities."),
            ToolDoc(
                "resolve_target",
                "client_id | udid | simulatorId | deviceId",
                "Which simulator or device an app runs on.",
            ),
            ToolDoc("host_environment", "", "Xcode version and a destination per platform."),
            ToolDoc(
                "screenshot",
                "client_id | udid | simulatorId | deviceId, maxDimension?",
                "PNG of the screen. Devices on iOS 17+ need the tunnel.",
            ),
            ToolDoc(
                "record_video",
                "action, client_id | udid | simulatorId | deviceId",
                "Screen recording on a simulator or USB iPhone or iPad.",
            ),
            ToolDoc(
                "launch_app",
                "bundleId, client_id | udid | ..., launchArgs?, env?, confirm",
                "Launches an installed app.",
                True,
            ),
            ToolDoc("stop_app", "bundleId, client_id | udid | ..., confirm", "Stops an app.", True),
            ToolDoc("install_app", "appPath, udid | ..., confirm", "Installs a built .app.", True),
            ToolDoc(
                "build_run",
                "scheme, udid | simulatorId | deviceId, launchArgs?, confirm",
                "Builds, installs, and launches as a job.",
                True,
            ),
            ToolDoc(
                "open_url",
                "url, bundleId?, client_id | udid | ..., confirm",
                "Opens a URL or deep link.",
                True,
            ),
        ),
        "targets",
    ),
    ToolGroup(
        "simulator",
        "Simulators",
        "Build, run, and test on simulators, and control simulator state with simctl.",
        (
            ToolDoc("list_sims", "platform?, bootedOnly?", "Simulators with udid, state, and OS."),
            ToolDoc(
                "boot_sim", "simulatorId | simulatorName, confirm", "Boots and opens one.", True
            ),
            ToolDoc("open_sim", "confirm", "Brings Simulator to the front.", True),
            ToolDoc("erase_sims", "simulatorIds?, confirm", "Erases content and settings.", True),
            ToolDoc("set_sim_appearance", "mode, confirm", "Light or dark mode.", True),
            ToolDoc("set_sim_location", "latitude, longitude, confirm", "Simulated GPS.", True),
            ToolDoc("reset_sim_location", "confirm", "Clears simulated GPS.", True),
            ToolDoc(
                "sim_statusbar",
                "time?, wifiBars?, batteryLevel?, clear?, confirm",
                "Status bar override.",
                True,
            ),
            ToolDoc(
                "toggle_software_keyboard",
                "confirm",
                "Shows or hides the on-screen keyboard.",
                True,
            ),
            ToolDoc(
                "toggle_connect_hardware_keyboard", "confirm", "Connects the Mac keyboard.", True
            ),
            ToolDoc(
                "build_sim",
                "scheme, projectPath | workspacePath, simulatorName?, extraArgs?, confirm",
                "Builds for a simulator as a job.",
                True,
            ),
            ToolDoc(
                "build_run_sim",
                "scheme, projectPath | workspacePath, simulatorName?, launchArgs?, confirm",
                "Builds, installs, and launches.",
                True,
            ),
            ToolDoc(
                "test_sim", "scheme, extraArgs?, coverage?, confirm", "Runs tests as a job.", True
            ),
            ToolDoc("get_sim_app_path", "scheme, projectPath | workspacePath", "Built .app path."),
            ToolDoc("install_app_sim", "appPath, confirm", "Installs a .app.", True),
            ToolDoc(
                "launch_app_sim", "bundleId, launchArgs?, env?, confirm", "Launches an app.", True
            ),
            ToolDoc("stop_app_sim", "bundleId, confirm", "Terminates an app.", True),
            ToolDoc("record_sim_video", "action", "Starts or stops a screen recording."),
            ToolDoc("sim_push", "payload, bundleId, confirm", "Delivers an APNs payload.", True),
            ToolDoc(
                "sim_privacy",
                "action, service, bundleId?, confirm",
                "Grants or revokes a permission.",
                True,
            ),
            ToolDoc("sim_clone", "name, confirm", "Clones a simulator.", True),
        ),
        "simulator",
    ),
    ToolGroup(
        "device",
        "Physical devices",
        (
            "Paired hardware through devicectl, plus the iOS 17+ developer tunnel and Apple TV "
            "pairing. Code signing must be set up in Xcode."
        ),
        (
            ToolDoc("list_physical_devices", "", "Paired iPhone, iPad, and Apple TV hardware."),
            ToolDoc(
                "build_device", "scheme, deviceId?, confirm", "Builds for a device as a job.", True
            ),
            ToolDoc(
                "build_run_device",
                "scheme, deviceId?, launchArgs?, confirm",
                "Builds, installs, and launches on a device.",
                True,
            ),
            ToolDoc("test_device", "scheme, deviceId?, confirm", "Runs tests on a device.", True),
            ToolDoc("get_device_app_path", "scheme, deviceId?", "Built .app path for a device."),
            ToolDoc("install_app_device", "appPath, deviceId?, confirm", "Installs a .app.", True),
            ToolDoc(
                "launch_app_device",
                "bundleId, deviceId?, env?, console?, confirm",
                "Launches an app; console streams its output to a job.",
                True,
            ),
            ToolDoc("stop_app_device", "bundleId | processId, confirm", "Stops an app.", True),
            ToolDoc(
                "open_url_device",
                "url, bundleId, deviceId?, confirm",
                "Opens a URL in an app.",
                True,
            ),
            ToolDoc("list_device_apps", "deviceId?, includeSystem?", "Installed apps."),
            ToolDoc("list_device_processes", "deviceId?", "Running processes with pid."),
            ToolDoc(
                "copy_from_device",
                "source, bundleId, deviceId?, confirm",
                "Copies a file from the app container to this Mac.",
                True,
            ),
            ToolDoc("device_tunnel_status", "", "Whether the iOS 17+ developer tunnel runs."),
            ToolDoc(
                "device_tunnel_start", "confirm", "Starts the tunnel (administrator prompt).", True
            ),
            ToolDoc("device_tunnel_stop", "confirm", "Stops the tunnel.", True),
            ToolDoc("device_pair_browse", "", "Apple TVs waiting to pair."),
            ToolDoc("device_pair_start", "identifier, confirm", "Starts Apple TV pairing.", True),
            ToolDoc("device_pair_pin", "pin, confirm", "Sends the PIN the TV shows.", True),
            ToolDoc("device_pair_status", "", "Pairing state."),
            ToolDoc("list_capture_devices", "", "USB iPhones and iPads that can be recorded."),
        ),
        "device",
    ),
    ToolGroup(
        "macos",
        "macOS apps",
        "Build, run, and test Mac targets.",
        (
            ToolDoc("build_macos", "scheme, arch?, confirm", "Builds a macOS scheme.", True),
            ToolDoc(
                "build_run_macos", "scheme, launchArgs?, confirm", "Builds and opens it.", True
            ),
            ToolDoc("test_macos", "scheme, extraArgs?, confirm", "Runs macOS tests.", True),
            ToolDoc("get_mac_app_path", "scheme", "Built macOS .app path."),
            ToolDoc("launch_mac_app", "appPath, launchArgs?, confirm", "Opens a .app.", True),
            ToolDoc("stop_mac_app", "bundleId | appPath, confirm", "Quits an app.", True),
        ),
        "macos",
    ),
    ToolGroup(
        "ui",
        "UI automation",
        (
            "Find elements by label, identifier, role, or text. With client_id the SDK acts "
            "inside the app (simulator, iPhone, iPad, Apple TV). The XCUITest UI runner drives "
            "any app on a simulator or device and covers drags, raw touches, and buttons. "
            "Without client_id, AXe or idb drives a simulator."
        ),
        (
            ToolDoc("screen_map", "client_id | simulatorId", "Screen summary in a few lines."),
            ToolDoc(
                "snapshot_ui",
                "client_id | simulatorId, label?, role?, limit?",
                "Accessibility elements.",
            ),
            ToolDoc("find_element", "label | identifier | text | role", "Matching elements."),
            ToolDoc(
                "tap",
                "label | identifier | text | view_id | x,y, confirm",
                "Taps one element.",
                True,
            ),
            ToolDoc(
                "long_press",
                "label | identifier | x,y, duration?, confirm",
                "Press and hold.",
                True,
            ),
            ToolDoc("swipe", "direction | startX..endY, confirm", "Swipe or scroll.", True),
            ToolDoc(
                "drag",
                "startX, startY, endX, endY, client_id?, confirm",
                "Slow drag (simulator or UI runner).",
                True,
            ),
            ToolDoc("gesture", "preset, confirm", "Scroll and edge-swipe presets.", True),
            ToolDoc(
                "touch", "x, y, down?, up?, client_id?, confirm", "Raw touch at a point.", True
            ),
            ToolDoc(
                "type_text",
                "text, identifier?, replace?, submit?, confirm",
                "Types into a field.",
                True,
            ),
            ToolDoc("key_press", "key | keyCode, confirm", "Presses one key.", True),
            ToolDoc(
                "key_sequence", "keyCodes, confirm", "Presses keys in order on a simulator.", True
            ),
            ToolDoc(
                "button", "button, client_id?, confirm", "Home, lock, side, or volume button.", True
            ),
            ToolDoc(
                "tv_remote",
                "press, client_id | simulatorId, repeat?, confirm",
                "Apple TV remote press (app or simulator).",
                True,
            ),
            ToolDoc(
                "ui_runner_start",
                "bundleId | client_id, udid | simulatorId | deviceId, developmentTeam?, confirm",
                "Starts the XCUITest runner; it appears in list_apps.",
                True,
            ),
            ToolDoc("ui_runner_stop", "udid | simulatorId | deviceId, confirm", "Stops it.", True),
            ToolDoc("ui_runner_status", "", "Running UI runners and their client_id."),
            ToolDoc(
                "wait_for_ui",
                "label | identifier | text, gone?, timeout_s?",
                "Waits for an element.",
            ),
            ToolDoc("batch", "steps, client_id?, confirm", "Runs up to 30 UI steps.", True),
        ),
        "ui",
    ),
    ToolGroup(
        "lldb",
        "LLDB debugger",
        (
            "Attach to a simulator or device app. Every call except debug_sessions needs "
            "confirm: true. Output is masked."
        ),
        (
            ToolDoc("debug_attach_sim", "bundleId | pid, confirm", "Attaches LLDB.", True),
            ToolDoc(
                "debug_attach_device",
                "bundleId | pid, deviceId?, confirm",
                "Attaches LLDB on a paired device (Xcode 16+).",
                True,
            ),
            ToolDoc(
                "debug_breakpoint_add",
                "file, line | function, condition?, confirm",
                "Sets a breakpoint.",
                True,
            ),
            ToolDoc(
                "debug_breakpoint_remove", "breakpointId, confirm", "Deletes a breakpoint.", True
            ),
            ToolDoc("debug_continue", "waitForStopSeconds?, confirm", "Resumes the process.", True),
            ToolDoc("debug_stack", "threadIndex?, allThreads?, confirm", "Backtrace.", True),
            ToolDoc("debug_variables", "frameIndex?, depth?, confirm", "Frame variables.", True),
            ToolDoc(
                "debug_lldb_command",
                "command, confirm",
                "One LLDB command. Host-code commands are refused.",
                True,
            ),
            ToolDoc("debug_detach", "confirm", "Detaches LLDB.", True),
            ToolDoc("debug_sessions", "", "Open LLDB sessions."),
        ),
        "lldb",
    ),
    ToolGroup(
        "swiftpm",
        "Swift packages",
        "swift build, test, and run for packages inside the project roots.",
        (
            ToolDoc(
                "swift_package_build",
                "packagePath, targetName?, confirm",
                "swift build as a job.",
                True,
            ),
            ToolDoc(
                "swift_package_test",
                "packagePath, filter?, coverage?, confirm",
                "swift test as a job.",
                True,
            ),
            ToolDoc(
                "swift_package_run",
                "packagePath, executableName?, arguments?, confirm",
                "swift run in the background.",
                True,
            ),
            ToolDoc("swift_package_stop", "job_id?, confirm", "Stops swift run jobs.", True),
            ToolDoc("swift_package_list", "packagePath?", "Products, targets, and running jobs."),
            ToolDoc("swift_package_clean", "packagePath, confirm", "swift package clean.", True),
        ),
        "swiftpm",
    ),
    ToolGroup(
        "coverage",
        "Coverage",
        "Reads the .xcresult from a test job.",
        (
            ToolDoc(
                "get_coverage_report",
                "xcresultPath?, target?, showFiles?",
                "Line coverage per target.",
            ),
            ToolDoc(
                "get_file_coverage", "file, xcresultPath?", "Uncovered line ranges in one file."
            ),
        ),
        "coverage",
    ),
    ToolGroup(
        "docs",
        "Apple documentation",
        "Public developer.apple.com data, cached for a week.",
        (
            ToolDoc("apple_docs_list_frameworks", "filter?", "Framework slugs."),
            ToolDoc(
                "apple_docs_search",
                "query, frameworks?, limit?",
                "Symbols and articles; wildcards work.",
            ),
            ToolDoc(
                "apple_docs_get", "path, maxChars?", "Declaration, availability, overview, topics."
            ),
        ),
        "docs",
    ),
    ToolGroup(
        "previews",
        "Previews and snapshots",
        (
            "Source #Preview listing, renders of previews the app registered with "
            "ViewliftPreviews.register, and pixel diffs against stored baselines."
        ),
        (
            ToolDoc("list_previews", "path?, filter?", "#Preview and PreviewProvider in source."),
            ToolDoc("list_app_previews", "client_id", "Previews the running app registered."),
            ToolDoc(
                "render_preview",
                "client_id, name, state?, color_scheme?, width?, height?",
                "PNG of one preview from the app.",
            ),
            ToolDoc(
                "snapshot_previews",
                "client_id, names?, color_schemes?, suite?",
                "Renders all and diffs with baselines.",
            ),
            ToolDoc(
                "approve_preview_baseline",
                "client_id | suite, names?, confirm",
                "Accepts renders.",
                True,
            ),
        ),
        "previews",
    ),
)

COMMAND_NOTES: dict[str, str] = {
    "performance.snapshot": "CPU, FPS, and memory right now",
    "memory.snapshot": "Footprint, tracked objects, and suspected leak summaries",
    "memory.graph": "Tracked objects and the app objects they reference, for retain cycles",
    "memory.leaks": (
        "Leak reports with class, creation stack, retainers, cycle paths, actions, and "
        "replay frames"
    ),
    "memory.heap": (
        "Live instance counts per app class from a heap walk, with growth since the last "
        "call"
    ),
    "memory.object": (
        "One tracked object: class chain, lifetime, creation stack, retainers, and cycles "
        "(payload: id)"
    ),
    "memory.live_start": (
        "Stream live memory activity and record a rolling screen replay for leak reports"
    ),
    "memory.live_stop": "Stop the live memory feed and the screen replay",
    "ui.hierarchy": "View tree with frames and ids",
    "ui.describe": "One view's class, properties, and SwiftUI or UIKit role (payload: id)",
    "ui.layers": (
        "Per-view layer images for the 3D view, paged after ui.hierarchy "
        "(payload: offset, optional ids, scale). Images are left out of agent output"
    ),
    "ui.screenshot": "PNG of the key window",
    "swiftui.renders": "Re-render counts per tracked view",
    "assets.audit": "Images larger than their display size",
    "accessibility.audit": (
        "Key-window audit with severity, fix, and frame per finding: missing labels, small "
        "targets, low contrast, Dynamic Type, unreachable controls. Includes a JPEG screenshot"
    ),
    "storage.defaults": "UserDefaults keys and values",
    "storage.keychain_metadata": "Keychain items without secret values",
    "storage.files": "Sandbox listing (payload: path)",
    "storage.file_read": "One sandbox file (payload: path)",
    "storage.sqlite_schema": "Tables of one database (payload: path)",
    "storage.sqlite_query": "SELECT, PRAGMA, WITH, or EXPLAIN only",
    "runtime.search": "Objective-C classes (payload: query)",
    "runtime.describe": "Methods and properties (payload: class)",
    "macro.list": "Recorded taps, swipes, and remote presses with the element each one hit",
    "push.status": "Notification permission, pending test notifications, and tapped actions",
    "bug.capture": "JPEG screenshot plus screen, app, device, performance, and recent taps",
    "ott.snapshot": "User, plan, TVE, theme, modules, metadataMap",
    "ott.sections": "Host section names, action names, and provider (AppCMS) for this app",
    "ott.section": (
        "One host section by name (payload: name from ott.sections; optional args object, "
        "for example entitlements args.content_id)"
    ),
    "ott.diagnostics": "Host bug findings with severity, evidence, hint, and source files",
    "ott.action": (
        "One whitelisted host debug action (payload: name, payload object). "
        "Names come from ott.sections actions"
    ),
    "hang.list": "Main-thread hangs with screen, screenshot, taps, console, and stack",
    "crash.list": "Uncaught exceptions and fatal signals from this run, plus the last run's crash",
    "activity.snapshot": "Current screen and playback state, without stream URLs",
    "activity.live_start": (
        "Stream screen and playback once a second. preview:true adds a small JPEG"
    ),
    "activity.live_stop": "Stop the live activity feed",
    "playback.snapshot": "Playback timeline: stalls, bitrate, live edge, route. No stream URLs",
    "playback.drm": "FairPlay step timing and error codes. No SPC or CKC",
    "playback.waterfall": "HLS and DASH request metadata. No query string or body",
    "ui.focus": "Focused view id, frame, last focus rejection, and recent focus moves",
    "ui.highlight": "Outline one view (payload: id)",
    "ui.edit": "Change a view property (payload: id plus fields)",
    "ui.visual": (
        "On-device guidelines, baselines, oversized images, repaint borders, and 5× "
        "slower animations"
    ),
    "storage.defaults_set": "Write one key (payload: key, value)",
    "storage.file_write": "Write a sandbox file (payload: path, content)",
    "storage.snapshot_create": "Save defaults and files (payload: paths)",
    "storage.snapshot_restore": "Restore a saved snapshot",
    "macro.record_start": "Start recording touches",
    "macro.record_stop": "Stop and save the macro",
    "macro.replay": "Replay a recorded macro",
    "push.local": (
        "Schedule a local notification (payload: title, subtitle, body, delay, badge, sound, "
        "image_url or image_base64 + image_type, actions [{title, destructive, foreground, input}], "
        "thread_id, user_info). Asks for permission the first time"
    ),
    "push.clear": "Remove pending and delivered test notifications",
    "nav.open_url": "Open a URL inside the paired app (payload: url). Query is not echoed",
    "runtime.libraries": "Loaded dyld images with Objective-C class counts, app images first",
    "runtime.image_classes": "Classes defined in one image (payload: image path)",
    "explorer.roots": (
        "Shortcut objects: UIApplication, app delegate, key window, top view controller, "
        "and more"
    ),
    "explorer.object": (
        "Properties, ivars, Swift fields, methods, and elements (payload: handle, "
        "view_id, or memory_id)"
    ),
    "explorer.instances": (
        "Live heap instances of a class and its subclasses (payload: class, limit)"
    ),
    "storage.cookies": "HTTPCookieStorage cookies with masked values",
    "settings.get": (
        "Debug modules, network capture switch, and body limits as applied on the device"
    ),
    "explorer.set": "Set one Objective-C property through KVC (payload: handle, key, value)",
    "explorer.call": (
        "Call a zero-argument method (payload: handle, selector). Ownership and teardown "
        "selectors are refused"
    ),
    "storage.defaults_delete": "Remove one UserDefaults key (payload: key)",
    "storage.file_rename": "Rename a sandbox file or folder (payload: path, name)",
    "storage.file_delete": (
        "Delete a sandbox file or folder (payload: path). Top-level containers are "
        "refused"
    ),
    "storage.cookie_delete": "Delete cookies by name and domain (payload: name, domain, path)",
    "ui.accessibility_tree": (
        "Flat accessibility elements with role, label, identifier, value, frame, and focus "
        "(payload: limit)"
    ),
    "ui.find": "Elements by label, identifier, role, or text (payload: the same keys)",
    "ui.wait_for": "Wait for an element to appear or go away (payload: query, gone, timeout)",
    "preview.list": "Previews the app registered with ViewliftPreviews.register, with states",
    "preview.render": (
        "PNG of one registered preview (payload: name, state, color_scheme, width, height, scale)"
    ),
    "ui.tap": "Tap an element by view_id, identifier, label, or text, or a point (payload: x, y)",
    "ui.long_press": "Press and hold an element (payload: query, duration)",
    "ui.swipe": "Swipe the nearest scroll view up, down, left, or right (payload: direction)",
    "ui.scroll": "Scroll the nearest scroll view by a page (payload: direction)",
    "ui.type_text": "Type into the focused or matched field (payload: text, replace, submit)",
    "ui.key": "Press return, delete, tab, or escape in the focused field (payload: key)",
    "ui.remote": "tvOS remote press that moves focus or selects (payload: press, repeat)",
    "ui.drag": "UI runner only: drag between two points (payload: startX, startY, endX, endY)",
    "ui.touch": "UI runner only: tap a screen point (payload: x, y)",
    "ui.button": "UI runner only: home, volume-up, or volume-down",
    "app.launch": "UI runner only: bring the target app to the foreground",
    "app.terminate": "UI runner only: terminate the target app",
    "net.profile": (
        "Shape this app's own traffic (payload: profile wifi, 4g, 3g, edge, gprs, offline, "
        "or off; optional latency_ms and loss_percent)"
    ),
    "settings.set": (
        "Persisted device overrides (payload: modules, capture_network, max_request_body_bytes, "
        "max_response_body_bytes, body_content_types, reset)"
    ),
}


def tool_names() -> list[str]:
    return [tool.name for group in TOOL_GROUPS for tool in group.tools]


def command_rows(names: set[str]) -> list[dict[str, str]]:
    return [{"name": name, "note": COMMAND_NOTES.get(name, "")} for name in sorted(names)]


def read_commands() -> list[dict[str, str]]:
    return command_rows(READ_COMMANDS)


def write_commands() -> list[dict[str, str]]:
    return command_rows(MUTATING_COMMANDS)
