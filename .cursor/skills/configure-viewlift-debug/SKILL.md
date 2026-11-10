---
name: configure-viewlift-debug
description: >-
  Wires an iOS and tvOS app to the Viewlift AI debug console and configures
  the viewlift-ai MCP server so coding agents can inspect traffic, logs, UI,
  memory, and playback. Use when the user asks to set up Viewlift AI, debug
  an iOS or tvOS app with this console, add ViewliftNetworkCapture, enable
  the debug bridge, or connect Cursor MCP to the local debug tools.
---

# Configure Viewlift AI debug (iOS, tvOS, MCP)

Connect a debug build to the local console at `http://127.0.0.1:8741`, then point the agent at the MCP server at `http://127.0.0.1:8742/mcp`. Do both. An app that only has the SDK gives the dashboard data the agent cannot query. MCP without the SDK has nothing to inspect.

Full references: [docs/ios-sdk.md](../../../docs/ios-sdk.md), [docs/mcp-server.md](../../../docs/mcp-server.md), [AGENTS.md](../../../AGENTS.md).

## Rules

- Debug builds only. Call `CaptureService.shared.start()` inside `#if DEBUG`. Never add it to Release, TestFlight, or App Store configs.
- Do not commit pairing tokens, fingerprints, receiver URLs with credentials, or auth tokens.
- Add `authorization`, `access_token`, and the app's token JSON keys to `extraRedactionKeys` when the app sends them.
- Do not unlock reveal. Mutating MCP tools need `confirm: true`, and the agent says what will change first.
- Several apps can share one device. Never assume the newest app. Call `list_apps` and pass that `client_id`.
- If the target is AppCMS / ViewliftOne, follow [reapply-viewlift-network-capture](../reapply-viewlift-network-capture/SKILL.md) for the host files. Still do the MCP step in this skill.

## 1. Console and MCP process

From the Viewlift AI checkout:

```bash
scripts/viewlift-ai.sh status || scripts/viewlift-ai.sh start --bg
```

`start` launches the dashboard and the MCP server. Confirm both:

- Dashboard: `curl -sf http://127.0.0.1:8741/health`
- MCP: something accepts TCP on `127.0.0.1:8742`

In the dashboard, open **Setup** and enable the SDK receiver. Leave it on the Mac's LAN address for a device. The receiver defaults to port 8743 (`VIEWLIFT_INGEST_PORT`). MCP stays on 8742. A simulator on this Mac can use the receiver on loopback without moving MCP.

## 2. SDK on every app target

iOS 16+ and tvOS 16+. Repeat for each app target (iOS and tvOS).

1. Add Swift package `https://github.com/rakesh-vl/viewlift-ai.git`, branch `main`, product `ViewliftNetworkCapture`. Use the local package `ios/ViewliftNetworkCapture` only while editing this repository.
2. On both Info.plists:

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Viewlift AI uploads debug network captures to your Mac on the local network.</string>
<key>NSBonjourServices</key>
<array>
    <string>_viewliftdebug._tcp</string>
</array>
```

3. Start capture before any `URLSession`. UIKit: first lines of `application(_:didFinishLaunchingWithOptions:)`. SwiftUI: the `App.init`.

```swift
import ViewliftNetworkCapture

#if DEBUG
CaptureService.shared.start()
#endif
```

`start()` discovers the receiver over Bonjour and enables every debug module. Use `CaptureConfiguration` only for a fixed receiver or a smaller module set. See [docs/ios-sdk.md](../../../docs/ios-sdk.md).

4. Build the iOS target and the tvOS target in Debug. Confirm Release does not call `start()`.

## 3. MCP in the app project

Write `.cursor/mcp.json` in the **app** repository (create `.cursor/` if needed). This is how agents in that project reach the debug tools. The URL has no secret.

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

If `VIEWLIFT_AI_MCP_PORT` is not 8742, use that port. Do not also add a second `viewlift-ai` server. After writing the file, tell the user to enable **viewlift-ai** in Cursor Settings → MCP. The console must already be running. The Agent page at `http://127.0.0.1:8741/agent` shows whether port 8742 is listening.

Stdio is only for a machine where the HTTP companion is not running. Point `--directory` at the Viewlift AI checkout, not the app repo:

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/Viewlift_AI", "--extra", "mcp", "viewlift-ai-mcp"],
      "env": {"VIEWLIFT_AI_URL": "http://127.0.0.1:8741"}
    }
  }
}
```

Prefer the HTTP URL when `scripts/viewlift-ai.sh` is how the console is started.

## 4. How to use the tools

1. `console_status` or `list_apps` with `online_only: true`. Pick the `client_id` for this bundle and device.
2. Vague bug: `triage_app`. Then `failed_requests`, `summarize_logs`, `get_flow`, `get_log`.
3. On-device reads go through `app_inspect` (`ui.screenshot`, `ui.hierarchy`, `memory.heap`, `hang.list`, `crash.list`, `playback.snapshot`). `describe_command` lists payloads.
4. Writes go through `app_mutate` with `confirm: true`.
5. Credentials, cookies, API keys, and signed URL parameters are always `[REDACTED]`. Do not ask the user to paste them. `security_policy` lists the rules.

## 5. Done when

- [ ] Debug build of each target calls `start()` and Release does not
- [ ] Both Info.plists have the Bonjour service and the local-network string
- [ ] App appears on the dashboard after launch
- [ ] `.cursor/mcp.json` in the app repo points at `127.0.0.1` and the MCP port
- [ ] `console_status` from the agent shows the app online
