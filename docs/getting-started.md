# Getting started

## Requirements

- macOS. The secret key lives in the Keychain; a `0600` file is used if the Keychain is unavailable.
- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/).
- Xcode command line tools (`xcrun simctl`) for simulator logs, CA install and deep links.
- For physical devices: a paired device reachable over USB or the network (handled by `pymobiledevice3`).

## Install and check

```bash
cd /path/to/Viewlift_AI
uv sync --extra dev
uv run pytest
uv run ruff check src tests
```

Add `--extra mcp` if you also want the MCP server:

```bash
uv sync --extra dev --extra mcp
```

## Run

The control script starts the console and the MCP server together:

```bash
scripts/viewlift-ai.sh start        # foreground console, MCP in background
scripts/viewlift-ai.sh start --bg   # both in background
scripts/viewlift-ai.sh status
scripts/viewlift-ai.sh restart
scripts/viewlift-ai.sh stop
```

Background output goes to `.viewlift-ai.log` and `.viewlift-ai-mcp.log` in the repository root.

To run only the console:

```bash
uv run viewlift-ai
```

Then open `http://127.0.0.1:8741`.

## Listeners and ports

| Listener | Default | Bind | Notes |
|---|---|---|---|
| Dashboard and API (uvicorn) | 8741 | `127.0.0.1` | `VIEWLIFT_HOST` and `VIEWLIFT_PORT`. Loopback only |
| mitmproxy | 8080 | `127.0.0.1` | LAN bind only from the Setup page, with generated basic auth |
| SDK ingest (HTTPS) | 8743 | off | `VIEWLIFT_INGEST_PORT`. Enabled from Setup on a loopback, LAN, or VPN address |
| MCP (streamable HTTP) | 8742, path `/mcp` | `127.0.0.1` | Set `VIEWLIFT_AI_MCP_PORT` to move it |

The receiver refuses to bind the MCP port. A simulator on the same Mac can use the receiver on `127.0.0.1:8743` while MCP stays on 8742.

## Where data lives

All app data lives under `~/Library/Application Support/ViewliftAI`:

- `viewlift.db`: the SQLite store for flows, logs, debug events and clients, with sensitive columns encrypted.
- `rules.json`: rule configuration (mode `0600`).
- `mitmproxy/`: proxy CA. The private key is `0600` and is never served.
- `ingest/`: the receiver's self-signed certificate, key and `debug.key`.
- `secret.key`: only when the Keychain fallback is active.

By default retention is per session: captured data is wiped when the console stops and shortly after the last dashboard tab closes. See [Configuration](configuration.md#retention).

## Health check

```bash
curl -s http://127.0.0.1:8741/health
```

The response reports version, retention, whether the Keychain fallback is active, data directory, proxy state, allowlist, SDK receiver state, log tail state and reveal state.

## First capture

Pick one path:

- **Proxy path**: open **Setup**, install the CA on the simulator or device, add hosts to the allowlist, point the device's Wi-Fi proxy at the Mac. Works for any app. Empty allowlist means no HTTPS decryption.
- **SDK path**: open **Setup**, enable the SDK receiver, then add `ViewliftNetworkCapture` to the app. No proxy or CA needed. Inspection only. See the [iOS SDK guide](ios-sdk.md).

When you finish, remove the Wi-Fi proxy and the debug CA from the device.

## Key files

- [scripts/viewlift-ai.sh](../scripts/viewlift-ai.sh)
- [src/viewlift_ai/app.py](../src/viewlift_ai/app.py)
- [src/viewlift_ai/config.py](../src/viewlift_ai/config.py)
- [pyproject.toml](../pyproject.toml)

Next: [Architecture](architecture.md)
