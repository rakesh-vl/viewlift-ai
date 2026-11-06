# MCP server

The MCP server lets Cursor or another agent read flows, logs and app state, and run debug commands on a connected app. It is a separate process that talks to the console over loopback HTTP, so it has no more access than the dashboard.

## Install and run

The server needs the `mcp` extra (official Python SDK 1.22; MCP 2 cannot be installed next to this project's mitmproxy pin).

```bash
uv sync --extra mcp
scripts/viewlift-ai.sh start
```

The script starts the console and the MCP server with `VIEWLIFT_AI_MCP_TRANSPORT=streamable-http` on `http://127.0.0.1:8742/mcp`. Cursor config:

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

For a stdio server started by Cursor itself:

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/Viewlift_AI", "--extra", "mcp", "viewlift-ai-mcp"],
      "env": {"VIEWLIFT_AI_URL": "http://127.0.0.1:8741"}
    }
  }
}
```

The console must be running in both cases. The **Agent** page (`/agent`) shows whether the MCP port is listening.

## Rules for every tool

- Per-app tools require `client_id`. Call `list_devices` or `list_apps` first. The server never picks the most recent app.
- A bundle id can stand in for `client_id` only if exactly one online, non-superseded app has it. Otherwise the tool returns the app list and does nothing.
- Mutating tools require `confirm: true`.
- Tools never enable proxy rewrite rules and never unlock reveal.

## Tools

| Tool | Arguments | client_id | confirm | Notes |
|---|---|---|---|---|
| `list_devices` | none | no | no | Device and app tree |
| `list_apps` | `device_key?`, `bundle_id?`, `online_only?` | no | no | |
| `get_app` | `client_id` | yes | no | Identity, capabilities, URL schemes, frameworks |
| `list_flows` | `client_id`, `method?`, `host?`, `status?`, `text?`, `limit?` (max 200) | yes | no | Excludes unattributed proxy flows |
| `get_flow` | `flow_id`, `client_id`, `reveal?` | yes | no | Refuses flows owned by another app. Raw only if reveal is already unlocked. |
| `list_logs` | `client_id`, `level?`, `subsystem?`, `text?`, `process?`, `limit?` | yes | no | Returns SDK logs and matching syslog lines |
| `list_packages` | `client_id` | yes | no | Swift packages from DerivedData |
| `list_debug_events` | `client_id`, `module?`, `limit?` | yes | no | |
| `app_inspect` | `client_id`, `command`, `payload?` | yes | no | Read commands only. Screenshots come back as an image. |
| `app_mutate` | `client_id`, `command`, `payload?`, `confirm` | yes | yes | Mutating commands only |
| `set_network_profile` | `profile`, `confirm` | no | yes | Global: affects every proxied app |
| `open_deeplink` | `url`, `client_id`, `confirm` | yes | yes | Simulator apps only |
| `set_location` | `udid`, `latitude?`, `longitude?`, `clear?`, `confirm` | no | yes | Paired hardware UDID, not an app `device_key` |
| `replay_flow` | `flow_id`, `confirm` | no | yes | Needs reveal already unlocked |

## On-device commands

Read commands (`app_inspect`):

`performance.snapshot`, `memory.snapshot`, `memory.graph`, `ui.hierarchy`, `ui.screenshot`, `swiftui.renders`, `assets.audit`, `accessibility.audit`, `storage.defaults`, `storage.keychain_metadata`, `storage.files`, `storage.file_read`, `storage.sqlite_schema`, `storage.sqlite_query`, `feature_flags.list`, `runtime.search`, `runtime.describe`, `macro.list`, `bug.capture`, `ott.snapshot`, `hang.list`, `crash.list`.

Mutating commands (`app_mutate`):

`ui.highlight`, `ui.edit`, `storage.defaults_set`, `storage.file_write`, `storage.snapshot_create`, `storage.snapshot_restore`, `feature_flags.set`, `feature_flags.reset`, `macro.record_start`, `macro.record_stop`, `macro.replay`, `push.local`.

A command only works if the app enabled its module (`debugModules` in the SDK). Payload fields are listed in [iOS SDK internals](ios-sdk-internals.md#commands).

## Code layout

| File | Role |
|---|---|
| [server.py](../src/viewlift_ai/mcp_server/server.py) | `FastMCP("viewlift-ai")` with instructions and `@mcp.tool()` wrappers; `main()` picks the transport |
| [tools.py](../src/viewlift_ai/mcp_server/tools.py) | Tool logic, `resolve_target`, confirm checks. No MCP or FastAPI imports, so it is unit-testable. |
| [client.py](../src/viewlift_ai/mcp_server/client.py) | `ConsoleClient` (urllib, 40 s timeout, loopback URLs only) |
| [runtime.py](../src/viewlift_ai/mcp_server/runtime.py) | Transport and port selection |
| [catalog.py](../src/viewlift_ai/mcp_server/catalog.py) | `TOOL_GROUPS` and `COMMAND_NOTES` rendered on the Agent page |

## Adding a tool

1. Write the logic in `tools.py` as a function that takes a `ConsoleClient`. Call console routes, not services.
2. Add a thin `@mcp.tool()` wrapper in `server.py` with a one-line docstring (agents read it).
3. Add a `ToolDoc` entry to the right group in `catalog.py`.
4. Add tests in `tests/test_mcp_tools.py`. `tests/test_agent_page.py` fails if the catalog and the registered tools differ.

Adding a new on-device command also needs an entry in `READ_COMMANDS` or `MUTATING_COMMANDS`, a note in `COMMAND_NOTES`, and a handler in the Swift `DebugCommandExecutor`. See [Contributing](contributing.md#add-a-debug-command).

## Key files

- [src/viewlift_ai/mcp_server](../src/viewlift_ai/mcp_server)
- [tests/test_mcp_tools.py](../tests/test_mcp_tools.py)
- [tests/test_agent_page.py](../tests/test_agent_page.py)

Next: [Web UI](web-ui.md)
