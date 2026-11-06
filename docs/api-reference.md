# API reference

Two HTTP servers expose APIs:

- the **console** on `http://127.0.0.1:8741` (FastAPI), used by the dashboard and the MCP server;
- the **SDK ingest server** on `https://<receiver-ip>:8742` (stdlib, TLS), used only by the Swift SDK.

The console listens on loopback only. Routes marked "loopback" also reject requests whose client address is not `127.0.0.1` or `::1`. This is a second check in case the console is ever put behind a forwarder.

JSON bodies are shown as `{field, field?}`; `?` means optional.

## Pages

| Method and path | Template |
|---|---|
| `GET /` | `dashboard.html` (Network) |
| `GET /flows/{flow_id}` | `flow_detail.html` |
| `GET /logs` | `logs.html` |
| `GET /rules` | `rules.html` |
| `GET /setup` | `setup.html` |
| `GET /debug` | `debug_suite.html` |
| `GET /agent` | `agent.html` |
| `GET /static/*` | Static assets |
| `GET /health` | JSON status |

## Flows and reveal

Source: [api/routes.py](../src/viewlift_ai/api/routes.py)

| Method and path | Loopback | Purpose |
|---|---|---|
| `GET /api/flows` | no | List flows. Query: `method`, `host`, `status`, `content-type`, `text`, `limit` (1 to 1000, default 200), `client_id` (repeatable), `device_key`. Returns `{flows, count}`. |
| `GET /api/flows/{id}` | for `reveal=true` | Redacted detail. `?reveal=true` returns raw data when the reveal session is unlocked and raw is cached. |
| `GET /api/flows/{id}/summary` | no | Summary JSON |
| `GET /api/flows/{id}/har` | no | Single-flow HAR (redacted) |
| `GET /api/flows/{id}/curl` | no | cURL command as text (redacted) |
| `GET /api/export/har` | no | HAR for the filtered list. Same query as `/api/flows`. |
| `GET /api/reveal/status` | yes | `{unlocked, unlock_token, cached_flows, note}` |
| `POST /api/reveal/unlock {token}` | yes | Unlock raw viewing |
| `POST /api/reveal/lock` | yes | Lock again |

## Apps

Source: [api/apps_routes.py](../src/viewlift_ai/api/apps_routes.py). All loopback.

| Method and path | Purpose |
|---|---|
| `GET /api/apps` | Device and app tree with counts, ingest activity and `online_apps` |
| `DELETE /api/apps/{client_id}/data` | Delete one app's flows, logs and debug events; forget the client if offline |
| `DELETE /api/apps/data` | Wipe all captured data, the reveal cache and ingest activity |

## Rules and breakpoints

Source: [api/rules_routes.py](../src/viewlift_ai/api/rules_routes.py). `{kind}` is one of `map-local`, `map-remote`, `rewrite`, `throttle`, `breakpoints`.

| Method and path | Purpose |
|---|---|
| `GET /api/rules` | Full rules config |
| `GET /api/rules/status` | `{mutating_enabled}` |
| `PUT /api/rules/allowlist {patterns}` | Replace allowlist (`allowlist` is accepted as an alias) |
| `PUT /api/rules/map-local/root {map_local_root}` | Set map-local root (`root` alias) |
| `POST /api/rules/{kind}` | Create a rule |
| `PATCH /api/rules/{kind}/{id}` | Update a rule |
| `POST /api/rules/{kind}/{id}/toggle?enabled=true` | Enable or disable |
| `DELETE /api/rules/{kind}/{id}` | Delete |
| `GET /api/breakpoints/pending` | Paused flows |
| `POST /api/breakpoints/{pause_id}/resume {headers?, body?, status?}` | Resume, optionally edited |
| `POST /api/breakpoints/{pause_id}/drop` | Kill the paused flow |

## Setup

Source: [api/setup_routes.py](../src/viewlift_ai/api/setup_routes.py)

| Method and path | Purpose |
|---|---|
| `GET /api/setup/status` | CA state, key mode, Keychain fallback, proxy bind (including LAN auth), interfaces, SDK receiver status, last test, reveal status, apps tree |
| `POST /api/setup/ca/ensure` | Create the CA if missing |
| `POST /api/setup/ca/token` | Mint a one-use, 60-second CA download token |
| `GET /api/setup/ca/cert?token=` | Download the public CA PEM. `404` unknown, `410` used or expired. |
| `POST /api/setup/mobileconfig/token {host, port}` | Mint a profile download token |
| `GET /api/setup/mobileconfig?token=` | Download the iOS configuration profile |
| `POST /api/setup/proxy {host, port, enable_lan}` | Rebind mitmproxy. A LAN bind returns basic-auth credentials. |
| `POST /api/setup/simulator/install-ca` | `xcrun simctl keychain booted add-root-cert` |
| `POST /api/setup/simulator/configure-proxy` | Set `http_proxy`/`https_proxy` in the booted simulator |
| `POST /api/setup/test {url}` / `GET /api/setup/test` | Run or read a test request through the proxy |
| `POST /api/setup/sdk/enable {host?, iface?, port?}` | Enable the SDK receiver (default port 8742) |
| `POST /api/setup/sdk/disable` | Disable it |
| `POST /api/setup/sdk/pairing-token` | Mint a pairing token |

## Logs

Source: [api/logs_routes.py](../src/viewlift_ai/api/logs_routes.py)

| Method and path | Purpose |
|---|---|
| `GET /api/logs/sources` | Booted simulators, paired devices, SDK and TestFlight notes |
| `GET /api/logs/status` | Active stream |
| `POST /api/logs/start {source?, udid?, process?, client_id?}` | Start or retarget the stream (`simulator` or `device`) |
| `POST /api/logs/filter {process?, subsystem?}` | Change the live filter without reconnecting |
| `POST /api/logs/stop` | Stop streaming |
| `POST /api/logs/delete` | Delete all logs |
| `GET /api/logs` | Query: `source`, `level`, `subsystem`, `text`, `limit`, `client_id`, `device_key` |
| `GET /api/logs/{id}` | Detail, plus `linked_flow_id` resolved from `request_id` |

## Session

Source: [api/session_routes.py](../src/viewlift_ai/api/session_routes.py)

| Method and path | Purpose |
|---|---|
| `POST /api/session/ping {id, page}` | Tab heartbeat; cancels a pending wipe |
| `POST /api/session/leave {id, page}` | Sent with `sendBeacon`. If no tab remains, data is wiped after 2.5 seconds. |

## Debug

Source: [api/debug_routes.py](../src/viewlift_ai/api/debug_routes.py)

| Method and path | Loopback | Purpose |
|---|---|---|
| `GET /api/debug/modules` | no | Module catalog |
| `GET /api/debug/network-profiles` | no | Network profile list |
| `GET /api/debug/clients` | yes | All known apps |
| `GET /api/debug/app?client_id=` | yes | Selected app identity. `409 client_required` when several apps are online and none is given. |
| `GET /api/debug/packages?client_id=` | yes | Swift packages from DerivedData, plus runtime frameworks |
| `GET /api/debug/events?client_id=&module=&limit=` | yes | Stored debug events |
| `GET /api/debug/analytics/traffic` | yes | Classified beacon and analytics flows. Query: `limit`, `user_beacon_url`, `player_beacon_url`, `client_id`, `device_key`. |
| `POST /api/debug/confirm {client_id, command}` | yes | Mint a confirmation token for a mutating command |
| `POST /api/debug/command {client_id, name, payload, confirmation?}` | yes | Enqueue a command; result arrives on `/ws/debug` |
| `POST /api/debug/invoke {client_id, name, payload, mutate?, timeout?}` | yes | Enqueue and wait for the result (default 12 s, max 25 s, `504` on timeout) |
| `POST /api/debug/ott/enrich-frames {frames}` | yes | Parse stack frames |
| `POST /api/debug/network-profile {profile}` | yes | Apply a global throttle profile |
| `POST /api/debug/deeplink {url, udid?, client_id?}` | yes | Open a URL in a simulator |
| `POST /api/debug/location {udid?, client_id?, latitude, longitude, clear?}` | yes | Simulate device location. `409` while a device log stream holds USB. |
| `POST /api/flows/{id}/replay` | yes | Resend a captured request from the Mac. Needs an unlocked reveal session. |

## Agent

| Method and path | Loopback | Purpose |
|---|---|---|
| `GET /api/agent/status` | yes | `{mcp: {port, url, listening}}` |

## WebSockets

| Path | Events |
|---|---|
| `/ws/flows?client_id=` | `{"type": "flow", "flow": <summary>, "app": <tag>}` and `{"type": "breakpoint", "kind", "pending"}` |
| `/ws/logs?client_id=` | `{"type": "log", "log": <summary>, "app": <tag>}` |
| `/ws/debug?client_id=` | `debug-client`, `debug-command`, `debug-event`, `apps-changed` |
| `GET /api/ws/ping` | Subscriber counts (plain HTTP) |

`<tag>` is `{client_id, app_name, bundle_id, device_key}`. `client_id=__apps__` on `/ws/debug` receives only `apps-changed`. Breakpoint events on `/ws/flows` are not filtered by `client_id`.

## SDK ingest server

Source: [services/ingest.py](../src/viewlift_ai/services/ingest.py). HTTPS with a self-signed certificate; the SDK pins its SHA-256 fingerprint.

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Status |
| `POST /v1/debug/pair` | `X-Viewlift-Debug-Key` | Returns `{pairing_token}` |
| `POST /v1/pair {pairing_token, client_id?, device_key?}` | none | Returns `{session_token, expires_in: 3600, token_type: "Bearer"}` |
| `POST /v1/flows` | Bearer | Flow upload |
| `POST /v1/logs` | Bearer | Log upload |
| `POST /v1/debug/register` | Bearer | App registration |
| `POST /v1/debug/events` | Bearer | Debug event or command result |
| `GET /v1/debug/commands` | Bearer | Poll for commands (also the heartbeat) |

Bearer calls may send `X-Viewlift-Client-ID`. Status codes: `401` bad or expired token (or pairing token already used), `403` client mismatch, `404` unknown pairing token, `410` expired pairing token, `429` rate limited.

Flow payload:

```json
{
  "id": "optional",
  "method": "GET",
  "url": "https://api.example.com/v1/items?page=1",
  "status": 200,
  "duration_ms": 123,
  "protocol": "HTTP/1.1",
  "request_headers": {},
  "response_headers": {},
  "request_body": "<base64>",
  "response_body": "<base64>",
  "error": null,
  "request_id": "abc",
  "content_type": "application/json",
  "timing": {"connect_ms": 0, "tls_ms": 0, "ttfb_ms": 0, "download_ms": 0},
  "extra_redaction_keys": ["access_token"]
}
```

`host` and `path` may be sent instead of `url`. Log payload: `{message, level, subsystem?, request_id?, id?}` (subsystem defaults to `sdk`). Debug event payload: `{module, kind, payload, id?}`.

## Key files

- [src/viewlift_ai/api](../src/viewlift_ai/api)
- [src/viewlift_ai/services/ingest.py](../src/viewlift_ai/services/ingest.py)

Next: [Security model](security-model.md)
