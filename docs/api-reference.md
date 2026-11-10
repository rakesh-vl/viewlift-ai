# API reference

Two HTTP servers expose APIs:

- the **console** on `http://127.0.0.1:8741` (FastAPI), used by the dashboard and the MCP server;
- the **SDK ingest server** on `https://<receiver-ip>:8743` (stdlib, TLS, `VIEWLIFT_INGEST_PORT`), used only by the Swift SDK.

The console listens on loopback only. Routes marked "loopback" also reject requests whose client address is not `127.0.0.1` or `::1`. This is a second check in case the console is ever put behind a forwarder.

JSON bodies are shown as `{field, field?}`; `?` means optional.

## Pages

| Method and path | Template |
|---|---|
| `GET /` | `dashboard.html` (Network) |
| `GET /graphql` | `graphql.html` |
| `GET /flows/{flow_id}` | `flow_detail.html` |
| `GET /logs` | `logs.html` |
| `GET /setup` | `setup.html` |
| `GET /debug` | `debug_suite.html` |
| `GET /simulator`, `GET /devices` | `simulator.html` (Devices) |
| `GET /tools` | `tools.html` |
| `GET /agent` | `agent.html` |
| `GET /static/*` | Static assets |
| `GET /health` | JSON status |

## Tools

Source: [api/tools_routes.py](../src/viewlift_ai/api/tools_routes.py). The server stores scripts. It does not execute them.

| Method and path | Purpose |
|---|---|
| `GET /api/tools/scripts` | List built-in and saved scripts |
| `GET /api/tools/scripts/{id}` | One script, including source |
| `POST /api/tools/scripts {source}` | Save a new script |
| `PUT /api/tools/scripts/{id} {source}` | Replace a saved script |
| `DELETE /api/tools/scripts/{id}` | Delete a saved script |
| `POST /api/tools/action/{name}` | Run one server-side toolkit action |

`source` is a Boop script: a `/** { JSON } **/` header plus `function main(state)`. Built-in ids cannot be replaced or deleted.

`POST /api/tools/action/{name}` is loopback-only and requires `Content-Type: application/json`. The body is a JSON object, at most 40 MB. Unknown names return 404. Actions:

| Name | Purpose |
|---|---|
| `yaml-parse`, `yaml-dump` | Parse or emit YAML |
| `hash` | MD5, SHA-1, SHA-2, SHA-3, CRC-32, Adler-32 |
| `bcrypt-hash`, `bcrypt-verify` | bcrypt |
| `rsa-keypair`, `ed25519-keypair`, `ssh-keypair`, `csr` | Keys and a certificate signing request |
| `cuid2` | CUID2-style ids |
| `dns`, `whois`, `ip-info` | dig, whois, and local address facts plus PTR |
| `http-request` | HTTP or HTTPS request from this Mac. Other schemes are rejected |
| `image-metadata`, `image-strip`, `image-convert` | Pillow image work |

## Flows and reveal

Source: [api/routes.py](../src/viewlift_ai/api/routes.py)

| Method and path | Loopback | Purpose |
|---|---|---|
| `GET /api/flows` | no | List flows. Query: `method`, `host`, `status`, `content-type`, `text`, `kind` (`graphql` or `http`), `operation` (operation-name substring), `limit` (1 to 1000, default 200), `client_id` (repeatable), `device_key`, `bundle_id`, `auth_state`, `user_id`, `profile_id`, `anonymous_id`. Account filters match the session span that covered `created_at`. Returns `{flows, count}`. Each flow includes `graphql`, `operation`, and `operation_type`. |
| `POST /api/flows/delete` | no | Delete every stored flow. Logs, debug events, and the allowlist stay. Clears the in-memory reveal cache. Returns `{deleted}`. |
| `GET /api/flows/{id}` | for `reveal=true` | Stored detail, including cookies and JSON secrets. `?reveal=true` uses the in-memory copy when the reveal session is unlocked. |
| `GET /api/flows/{id}/summary` | no | Summary JSON |
| `GET /api/flows/{id}/har` | no | Single-flow HAR from the stored bytes |
| `GET /api/flows/{id}/curl` | no | cURL command as text from the stored request |
| `GET /api/export/har` | no | HAR for the filtered list. Same query as `/api/flows`. |
| `GET /api/reveal/status` | yes | `{unlocked, unlock_token, cached_flows, note}` |
| `POST /api/reveal/unlock {token}` | yes | Unlock raw viewing |
| `POST /api/reveal/lock` | yes | Lock again |

## Apps

Source: [api/apps_routes.py](../src/viewlift_ai/api/apps_routes.py). All loopback.

| Method and path | Purpose |
|---|---|
| `GET /api/apps` | Device and app tree with counts, ingest activity, LAN `peer`, `online_apps`, and `accounts` (auth states, user ids, guest ids) |
| `DELETE /api/apps/{client_id}/data` | Delete one app's flows, logs and debug events; forget the client if offline |
| `DELETE /api/apps/data` | Wipe all captured data, the reveal cache and ingest activity |

## Allowlist

Source: [api/rules_routes.py](../src/viewlift_ai/api/rules_routes.py). The editor is on the Setup page.

| Method and path | Purpose |
|---|---|
| `GET /api/rules` | `{allowlist}` |
| `PUT /api/rules/allowlist {patterns}` | Replace the allowlist (`allowlist` is accepted as an alias). Empty denies every HTTPS host. |

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
| `POST /api/setup/sdk/enable {host?, iface?, port?}` | Enable the SDK receiver (default port 8743) |
| `POST /api/setup/sdk/disable` | Disable it |
| `POST /api/setup/sdk/pairing-token` | Mint a pairing token |

## GraphQL

Source: [api/graphql_routes.py](../src/viewlift_ai/api/graphql_routes.py). Loopback only. `PUT` and `POST` require `Content-Type: application/json`. Bodies are at most 1 MB. The password is never returned.

| Method and path | Purpose |
|---|---|
| `GET /api/graphql/bases` | Distinct GraphQL origins in captured traffic, newest first. Same scope query as `/api/flows`. `{bases: [{base, count}]}`. |
| `GET /api/graphql/endpoints` | `{endpoints: [{id, name, base, url, playground_url, username, has_password}]}`. `url` is `{base}/graphql`. `playground_url` is `{base}/playground`. |
| `PUT /api/graphql/endpoints {endpoints}` | Replace the list. Each row accepts `base`, or a pasted `url` / `playground_url`; only the origin is kept. A blank `password` keeps the saved one. `clear_password: true` removes it. |
| `POST /api/graphql/execute {endpoint_id?, url?, query, variables?, operation_name?, headers?}` | Run one document. Response includes `status`, `body`, `elapsed_ms`, `auth_note`, `html_page`, `html_hint`. |
| `POST /api/graphql/schema {endpoint_id?, url?, headers?, refresh?}` | Introspection. Cached in memory for 10 minutes per endpoint. `refresh: true` fetches again. |

Saved basic auth is added only when the request URL has the same scheme, host, and port as that endpoint and the request has no `Authorization` header. Redirects onto another host are refused. These calls are not stored as captured flows.

## Logs

Source: [api/logs_routes.py](../src/viewlift_ai/api/logs_routes.py)

| Method and path | Purpose |
|---|---|
| `GET /api/logs/sources` | Booted simulators and paired devices (used by debug tools) |
| `GET /api/logs/status` | OS-log collector status (not used by the Logs page) |
| `POST /api/logs/delete` | Delete all logs |
| `GET /api/logs` | Query: `source` (single value or comma list such as `stdout,stderr`), `level`, `subsystem`, `text`, `limit`, `client_id`, `device_key`, `bundle_id`, `auth_state`, `user_id`, `profile_id`, `anonymous_id`. Account filters use session spans, same as flows. |
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
| `GET /api/debug/network-profiles` | no | Profile names for Debug Network tools. The button sends `net.profile` to the selected app |
| `GET /api/debug/clients` | yes | All known apps |
| `GET /api/debug/app?client_id=` | yes | Selected app identity. `409 client_required` when several apps are online and none is given. |
| `GET /api/debug/packages?client_id=` | yes | Swift packages from DerivedData, plus runtime frameworks |
| `GET /api/debug/events?client_id=&module=&limit=` | yes | Stored debug events |
| `GET /api/debug/analytics/traffic` | yes | Classified beacon and analytics flows. Query: `limit`, `user_beacon_url`, `player_beacon_url`, `client_id`, `device_key`. |
| `POST /api/debug/confirm {client_id, command}` | yes | Mint a confirmation token for a mutating command |
| `POST /api/debug/command {client_id, name, payload, confirmation?}` | yes | Enqueue a command; result arrives on `/ws/debug` |
| `POST /api/debug/invoke {client_id, name, payload, mutate?, timeout?}` | yes | Enqueue and wait for the result (default 12 s, max 25 s, `504` on timeout) |
| `POST /api/debug/ott/enrich-frames {frames}` | yes | Parse stack frames |
| `POST /api/debug/deeplink {url, udid?, kind?, client_id?}` | yes | Open a URL on a simulator or a paired device. `kind` is `simulator` or `device` |
| `POST /api/debug/location {udid?, client_id?, latitude, longitude, clear?}` | yes | Simulated GPS on a simulator or a paired device. `409` while a device log stream holds USB |
| `GET /api/host/targets` | yes | Simulators and paired devices, each with capabilities |
| `POST /api/host/call {tool, args, confirm?}` | yes | One host tool. Mutating tools require `confirm: true` |
| `POST /api/flows/{id}/replay` | yes | Resend a captured request from the Mac. Needs an unlocked reveal session. |
| `POST /api/flows/send` | yes | Send an edited request from the Mac. JSON: `method`, `url`, `headers`, `body` (string). Reveal is not required. The response is returned as received. The call is not stored as a flow. |

## Agent

| Method and path | Loopback | Purpose |
|---|---|---|
| `GET /api/agent/status` | yes | `{mcp: {port, url, listening}}` |

## WebSockets

| Path | Events |
|---|---|
| `/ws/flows?client_id=` | `{"type": "flow", "flow": <summary>, "app": <tag>}` |
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
