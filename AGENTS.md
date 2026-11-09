# Agents

Viewlift AI is a local debug console for iOS and tvOS. The dashboard is `http://127.0.0.1:8741`. The MCP server is `http://127.0.0.1:8742/mcp`. Agents use MCP to read captured traffic, logs, and on-device state, and to run debug commands. They do not get auth tokens, cookies, or API keys.

When asked to connect an app, follow [.cursor/skills/configure-viewlift-debug/SKILL.md](.cursor/skills/configure-viewlift-debug/SKILL.md). AppCMS / ViewliftOne host files are in [.cursor/skills/reapply-viewlift-network-capture/SKILL.md](.cursor/skills/reapply-viewlift-network-capture/SKILL.md).

## Run

```bash
scripts/viewlift-ai.sh start --bg
scripts/viewlift-ai.sh status
scripts/viewlift-ai.sh stop
```

`start` launches the console and the MCP server. Enable the SDK receiver on the Setup page before expecting a device to appear.

## MCP config

Put this in the app repository as `.cursor/mcp.json`, and in this repository if it is missing:

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

Then enable **viewlift-ai** in Cursor Settings → MCP. The console must be running.

## Working rules

- Name a `client_id` from `list_apps`. One device can host several apps.
- Start with `triage_app`, `network_summary`, `failed_requests`, or `summarize_logs`. Open one flow or log after that.
- `app_inspect` is read-only. `app_mutate`, `set_network_profile`, `open_deeplink`, `set_location`, and `replay_flow` change state and require `confirm: true`. Say what will change first.
- Do not enable proxy rewrite. Do not unlock reveal. Do not ask the user to paste tokens. Masked values stay `[REDACTED]`.
- SDK `start()` belongs in Debug only.

## Fix a crash

1. `list_apps`, then `crash_reports` for that `client_id`. Each crash has a `headline`, a `crash_site`, and the stack as `file:line`.
2. `crash_detail` (newest crash by default). Read `root_cause`, `crash_site` (with the code around the crash line), `steps_to_reproduce`, `timeline`, and `suggested_fix_area`. The image is the last screen before the crash. `brief` is the same report as markdown.
3. Open `crash_site.path` at `crash_site.line`. Fix the cause named in `headline`, not only the symptom. Keep the change small.
4. If `symbolication` is `missing_dsym`, build the app in Debug on this Mac first. Without file and line, start from `app_stack`.
5. Build every iOS and tvOS target. Run without the debugger, repeat `steps_to_reproduce` (use `screen_map` and taps when the `ui` group is on), then call `crash_reports` again and confirm no new crash.

The dashboard card has the same data plus a replay video of the last ten seconds, with taps drawn, that can be downloaded.

## Build, run, and drive apps

- A chat starts with the debug tools only. `manage_workflows` turns on one group for that chat (`project`, `targets`, `simulator`, `ui`, `docs`, `device`, `macos`, `lldb`, `swiftpm`, `coverage`, `previews`). Disable the group when the task ends. `VIEWLIFT_AI_MCP_WORKFLOWS=all` starts a chat with every group.
- Path tools only work inside project roots. Add the app repository on the Setup page, or set `VIEWLIFT_AI_PROJECT_ROOTS`.
- Call `doctor` and `session_show_defaults` first. Set the project, scheme, and simulator once with `session_set_defaults`.
- Builds and tests return a `job_id` when they run longer than 25 seconds. Poll `wait_job`.
- Read a screen with `screen_map` before a screenshot. Find elements by label or identifier, not coordinates. Pass `client_id` for an app that runs the SDK (any device, including Apple TV). Omit it to drive a simulator. For gestures the SDK cannot make, start `ui_runner_start` on that target first. `targets` tools (`launch_app`, `screenshot`, `build_run`, `open_url`) take a simulator or a device.
- Taps, typing, swipes, `tv_remote`, `batch`, simulator changes, installs, launches, and every LLDB tool except `debug_sessions` change state and require `confirm: true`. Type only test data.
- Look up Apple APIs with `apple_docs_search` and `apple_docs_get`.

Tool reference: [docs/mcp-server.md](docs/mcp-server.md). SDK reference: [docs/ios-sdk.md](docs/ios-sdk.md). Command payloads: [docs/ios-sdk-internals.md](docs/ios-sdk-internals.md#commands).

## Prompt for an iOS or tvOS app repo

The copy-paste prompt is [docs/connect-app-prompt.md](docs/connect-app-prompt.md). Paste it into the app repository's agent chat, or save it as that repo's `AGENTS.md`. It tells the agent to add `ViewliftNetworkCapture` with Swift Package Manager (`https://github.com/rakesh-vl/viewlift-ai.git`, branch `main`) on every iOS and tvOS app target.
