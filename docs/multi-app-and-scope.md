# Multi-app and scope

One device or simulator can run several apps with the SDK at once (for example two white-label builds). The console keeps them apart and never guesses which one you mean.

## Identities

| Identifier | Source | Meaning |
|---|---|---|
| `client_id` | SDK, `UserDefaults` key `com.viewlift.networkcapture.client-id` | One app install. Sanitized to `[A-Za-z0-9._-]`, at most 128 characters. |
| `device_key` | SDK `DeviceIdentity` | `SIMULATOR_UDID` on a simulator, `vendor:<identifierForVendor>` on hardware, `hint:<model>\|<name>` as a fallback. |
| `device_hint` | SDK `DeviceIdentity` | Stable across apps on one physical device until reboot. The dashboard groups those apps together. Empty on simulators. |
| `bundle_id`, `executable` | SDK `AppIdentity` | Used for display, package detection and log attribution. |

An ingest session is bound to exactly one `client_id`; a different id on the same session gets `403`.

## Client registry

`DebugSessionService` ([services/debug_session.py](../src/viewlift_ai/services/debug_session.py)) keeps known clients in memory and in the encrypted `clients` table, and reloads them at startup.

- **Presence** has three states, reported as `state` next to the boolean `online`:
  - `online`: seen in the last 45 seconds. Polls, events, flow uploads and log uploads all count as heartbeats.
  - `background`: the app sent a `lifecycle` / `background` event (iOS suspends it, so silence is expected). It stays listed as backgrounded for 30 minutes, then becomes `offline`. Commands are refused with a message that says the app is in the background.
  - `offline`: silent past the TTL and not backgrounded.
- The SDK sends `lifecycle` events (`background`, `foreground`, `network`) on `/v1/debug/events`. They update presence and are not stored as debug events. On return it re-registers at once, so the app flips back to `online` without waiting for the next poll.
- **Proxy-only apps**: traffic that arrives through the capture proxy from a non-loopback peer (same Wi-Fi or Tailscale) is grouped by peer address and User-Agent in memory ([services/proxy_clients.py](../src/viewlift_ai/services/proxy_clients.py)). `GET /api/apps` lists them under `proxy_clients`, minus apps already registered through the SDK from the same peer. They cannot be used as a scope, because flows are not tagged with a client.
- **Supersede**: when a new `client_id` registers with the same `device_key` and `bundle_id` as an older one (a reinstall), the older one is marked `superseded_by` the new one.
- `apps_tree()` groups clients by `device_key` (or `client:<id>` if none), with online flags and per-app flow, log and event counts. This is what `GET /api/apps` returns.

## Attribution rules

| Data | Attributed to an app? |
|---|---|
| SDK flows and logs | Always, by `client_id` |
| Debug events | Always |
| Proxy flows | Never. The proxy cannot tell which app sent a request, so `client_id` is `None`. |
| Simulator and device syslog | Only when the stream is narrowed to the app's executable. Otherwise stored with `udid` only. |

Unattributed rows appear only under "All apps" in the dashboard. MCP tools exclude them from per-app results.

## Query scoping

`resolve_client_ids(request, client_id, device_key)` in [api/scope.py](../src/viewlift_ai/api/scope.py):

- no parameters: returns `None`, meaning all rows, including unattributed ones;
- `client_id` (repeatable): restricts to those ids;
- `device_key`: expands to every `client_id` on that device. An empty expansion matches nothing.

Used by `/api/flows`, `/api/export/har`, `/api/logs` and `/api/debug/analytics/traffic`. Flow-to-log links (`flow_id_for_request`, `logs_for_request`) are scoped the same way.

## Selection safety

- `/api/debug/app` and `/api/debug/packages` fall back to the only online app if exactly one is online. With several, they return `409 client_required`.
- `/api/debug/invoke` always requires an explicit, online `client_id`.
- The MCP server requires `client_id` on every per-app tool. A bundle id is accepted only if exactly one online, non-superseded app has it.

## Dashboard scope picker

[web/static/js/shell.js](../src/viewlift_ai/web/static/js/shell.js) exposes `window.VLScope`:

- scope is stored per tab in `sessionStorage` under `vl-scope`, as `all`, `app` or `device`;
- it adds `client_id` or `device_key` to API queries and fires a `vl:scope-change` event;
- WebSocket events are filtered in the browser with `VLScope.matches` using the `app` tag on each event;
- "Clear app data" calls `DELETE /api/apps/{client_id}/data`; "Clear all data" calls `DELETE /api/apps/data`.

## Key files

- [src/viewlift_ai/api/scope.py](../src/viewlift_ai/api/scope.py)
- [src/viewlift_ai/api/apps_routes.py](../src/viewlift_ai/api/apps_routes.py)
- [src/viewlift_ai/services/debug_session.py](../src/viewlift_ai/services/debug_session.py)
- [tests/test_multi_app.py](../tests/test_multi_app.py)

Next: [MCP server](mcp-server.md)
