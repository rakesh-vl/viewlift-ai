# Configuration

The console reads a small set of environment variables at startup. Everything else (proxy LAN bind, SDK receiver, rules) is configured at runtime from the dashboard and saved under the data directory.

## Environment variables

### Console (`viewlift-ai`)

| Variable | Default | Effect |
|---|---|---|
| `VIEWLIFT_PROXY_ENABLED` | `true` | Start mitmproxy. Truthy values: `1`, `true`, `yes`, `on`. |
| `VIEWLIFT_PROXY_HOST` | `127.0.0.1` | Proxy bind. Must be loopback; any other value fails startup. Use the Setup page for a LAN bind. |
| `VIEWLIFT_PROXY_PORT` | `8080` | Proxy port. |
| `VIEWLIFT_ALLOWLIST` | empty | Comma-separated hosts or globs. Seeds the rules allowlist only when `rules.json` has none. |
| `VIEWLIFT_RETENTION` | `session` | `session` (or `sess`) or `24h` (or `24`, `hours_24`, `day`). Any other value fails startup. |
| `VIEWLIFT_SDK_WATCH` | set to `1` by `main()` | Starts the ingest network-follow thread that re-binds the receiver when interfaces change. |
| `VIEWLIFT_DERIVED_DATA` | unset | Override the DerivedData root used by package detection. Falls back to Xcode's custom location, then `~/Library/Developer/Xcode/DerivedData`. |

The dashboard host and port (`127.0.0.1:8741`) are fixed in `AppConfig` and have no environment override.

### MCP server (`viewlift-ai-mcp`)

| Variable | Default | Effect |
|---|---|---|
| `VIEWLIFT_AI_URL` | `http://127.0.0.1:8741` | Console base URL. Must be a loopback `http` or `https` URL. |
| `VIEWLIFT_AI_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. The control script sets `streamable-http`. |
| `VIEWLIFT_AI_MCP_PORT` | `8742` | Port for streamable HTTP, path `/mcp`. |

### Swift side (not read by Python)

| Name | Where | Effect |
|---|---|---|
| `VIEWLIFT_CAPTURE_ENABLED` | Swift compilation condition | Enables SDK code outside `DEBUG`. See the caveat in the [iOS SDK guide](ios-sdk.md#build-configurations). Never set it for App Store builds. |
| `VIEWLIFT_CAPTURE_HOST`, `VIEWLIFT_CAPTURE_PORT`, `VIEWLIFT_CAPTURE_FINGERPRINT`, `VIEWLIFT_CAPTURE_DEBUG_KEY` | Xcode scheme environment, read by the AppCMS host file | Optional overrides for a fixed receiver. See [AppCMS integration](appcms-integration.md). |

## Data directory

`~/Library/Application Support/ViewliftAI` (from `platformdirs`):

| Path | Contents |
|---|---|
| `viewlift.db` | SQLite store (flows, logs, debug events, clients, meta) |
| `rules.json` | Rules configuration, mode `0600`, written atomically |
| `scratchpad/*.js` | User scratchpad scripts, mode `0600`. Built-ins are not stored here. |
| `mitmproxy/` | Proxy CA; private key `0600` |
| `ingest/ingest-cert.pem`, key | Receiver certificate and key (key `0600`) |
| `ingest/debug.key` | Stable debug key for SDK auto-pairing, `0600` |
| `secret.key` | Encryption key, only when the Keychain fallback is active |

## Encryption key

`KeychainSecretStore` resolves the 32-byte SecretBox key in this order:

1. Keychain item, service `ViewliftAI`, account `secret-box-key` (hex value), via the `security` CLI.
2. An existing `secret.key` file.
3. A new random key, saved to the Keychain, or to `secret.key` (mode `0600`) if the Keychain write fails.

When the file is used, `/health` and the Setup page report `keychain_fallback: true`.

## Retention

| Mode | Behavior |
|---|---|
| `session` (default) | Flows, logs and debug events are wiped at shutdown. Known clients survive; offline or superseded ones are removed. |
| `24h` | Rows older than 24 hours are removed when the store opens (at startup). There is no periodic cleanup while running. |

Independently of the mode, when the last dashboard tab closes the console waits 2.5 seconds and then wipes captured data, the reveal cache, the log tail and ingest activity. Reopening a tab within that window cancels the wipe.

## rules.json

Managed by `RuleConfigStore` ([services/rule_store.py](../src/viewlift_ai/services/rule_store.py)). It holds the allowlist, the map-local root and lists of map-local, map-remote, rewrite, throttle and breakpoint rules. Edit it through the Rules page or `/api/rules/*`, not by hand. All mutating rules default to disabled. See [Rules and breakpoints](rules-and-breakpoints.md).

## Key files

- [src/viewlift_ai/config.py](../src/viewlift_ai/config.py)
- [src/viewlift_ai/mcp_server/runtime.py](../src/viewlift_ai/mcp_server/runtime.py)
- [src/viewlift_ai/mcp_server/client.py](../src/viewlift_ai/mcp_server/client.py)
- [src/viewlift_ai/services/package_detect.py](../src/viewlift_ai/services/package_detect.py)

Next: [API reference](api-reference.md)
