# Viewlift AI

Local iOS/tvOS debug console: HTTP proxy, device/simulator logs, and SDK capture.
Privacy defaults are fail-closed. Dashboard binds to `127.0.0.1` only.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended)
- macOS (Keychain for the per-install secret; file fallback if Keychain fails)

## Quick start

```bash
cd /path/to/Viewlift_AI
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run viewlift-ai
```

App data lives under `~/Library/Application Support/ViewliftAI`.
Sensitive request/response headers and bodies are encrypted at rest with PyNaCl SecretBox.
The encryption key is stored in the macOS Keychain when possible; otherwise a mode-`0600` key file is used and that fallback is reported by config.

Open `http://127.0.0.1:8741` after startup.

## Included

- Live HTTP/HTTPS and WebSocket inspection through the local mitmproxy engine.
- Request/response detail, filters, timing, redacted HAR, and redacted cURL export.
- Breakpoints, map local/remote, rewrites, throttling, and offline mode.
- iOS/tvOS simulator logs and paired physical-device syslog.
- `ViewliftNetworkCapture` SPM for HTTP/HTTPS and logs without proxy or installed CA.
- Guided iPhone, Apple TV, simulator, proxy CA, and SDK setup.

Use the **Setup** page before exposing either listener to the LAN. Both listeners
are off or loopback-only by default. HTTPS decryption is allowlist-only. Mutating
rules remain disabled until explicitly enabled.

## Swift package

The package lives at `ios/ViewliftNetworkCapture`. Add it as a local Swift package.
See its README for SwiftUI/UIKit integration.

SDK capture is inspection-only. Breakpoints, mapping, rewriting, and throttling use
the proxy path. SDK mode does not capture WebSocket frames, `WKWebView`,
Network.framework, third-party sockets, or background `URLSession`.

## Documentation

Developer guides live in [docs/](docs/README.md): getting started, architecture,
data flows, configuration, API reference, security model, rules, multi-app scope,
MCP server, web UI, the iOS/tvOS SDK, AppCMS integration and contributing.

## Layout

- `src/viewlift_ai/` — Python console (`api/`, `services/`, `proxy/`, `models/`, `web/`, `mcp_server/`)
- `ios/ViewliftNetworkCapture/` — iOS/tvOS Swift package
- `tests/` — Python service and API tests

See [docs/architecture.md](docs/architecture.md) for a module-by-module map.

## Security and cleanup

- Sensitive flow headers and bodies are encrypted at rest.
- Redaction happens before display/export and before SDK data leaves the device.
- Session retention is default. Optional retained data expires after 24 hours.
- Remove the Wi-Fi proxy and debug CA when capture is finished.
- Never enable `VIEWLIFT_CAPTURE_ENABLED` for an App Store build.

## Agent MCP

`scripts/viewlift-ai.sh start` starts the console and a loopback MCP server.
The companion listens on `127.0.0.1` port `VIEWLIFT_AI_MCP_PORT` (default 8742), path `/mcp`.
`scripts/viewlift-ai.sh stop` stops both.
The `mcp` extra is the official Python SDK 1.22. MCP 2 cannot be installed next to this
project's mitmproxy pin.
`uv run --extra mcp viewlift-ai-mcp` with no transport env stays on stdio for a manual Cursor command.
Several apps on one device stay separate clients. Tools require `client_id`.
Mutating tools require `confirm: true`. They do not enable proxy rewrite.
Raw flow bodies and replay work only when reveal is already unlocked in the console.

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```
