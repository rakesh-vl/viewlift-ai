# Security model

Viewlift AI handles production-like traffic from real devices, so its defaults are fail-closed: loopback binds, deny-all decryption, disabled mutations, encrypted storage and session-only retention. This page explains what each control does and where it stops.

## Network exposure

| Listener | Default | How it can widen |
|---|---|---|
| Dashboard and API | `127.0.0.1:8741` | `VIEWLIFT_HOST` and `VIEWLIFT_PORT`. Loopback only. |
| mitmproxy | `127.0.0.1:8080` | Setup page, `POST /api/setup/proxy` with `enable_lan: true`. The IP must be a private IPv4 address on an active interface. Each LAN bind gets new basic-auth credentials (`vl<hex>` user, random password). Environment variables cannot set a non-loopback proxy host. |
| SDK receiver | off, port 8743 | Setup page. Loopback, LAN or VPN address only (`utun`, `tailscale`, `wt`, `nebula`, `ham` interfaces or `100.64.0.0/10`). Never `0.0.0.0`, multicast, link-local or public. HTTPS with a pinned certificate and a debug key. Refuses the MCP port. |
| MCP | `127.0.0.1:8742/mcp` | Never. Its console URL must also be loopback. |

Validators live in [services/bind_policy.py](../src/viewlift_ai/services/bind_policy.py).

## HTTPS decryption allowlist

`HostAllowlist` ([proxy/allowlist.py](../src/viewlift_ai/proxy/allowlist.py)) matches exact hosts or `fnmatch` globs, case-insensitive, trailing dot ignored. An empty allowlist denies everything. Non-allowlisted TLS connections are tunnelled without decryption and are not captured. Allowlist edits apply immediately.

## Encryption at rest

Values are encrypted with PyNaCl SecretBox (XSalsa20-Poly1305, random nonce) in [crypto.py](../src/viewlift_ai/crypto.py). The key comes from the Keychain or a `0600` fallback file (see [Configuration](configuration.md#encryption-key)).

| Encrypted | Plaintext (needed for filtering and indexes) |
|---|---|
| Request and response headers and bodies | Method, host, path, query, status, duration, size, protocol, source, created_at, error, request_id, content_type, timing, client_id |
| WebSocket messages | Log level, subsystem, source, request_id, udid |
| Log message | Client device_key, bundle_id, timestamps |
| Debug event payload, client payload | |

Text search decrypts a bounded batch of recent rows in memory. Note that URL paths and query strings are stored in plaintext.

Crash replay videos and posters in `crash-media/` are not encrypted. They show whatever was on screen in the ten seconds before a crash, which can include personal data in a test account. They stay in the data folder, are served only on loopback, and are deleted when their crash is deleted (checked at startup and after each upload). The replay `context` sent with a crash holds request method, host, and path without query strings, headers, or bodies, and console lines, which are not redacted (see known gap 4).

Other files: `rules.json` `0600` (atomic write), CA private key `0600` and never served (export refuses anything containing `PRIVATE KEY`), ingest key and `debug.key` `0600`.

## Redaction

The dashboard shows captured headers, bodies, cookies, analytics parameters, HAR, cURL, replay responses, and `GoogleService-Info.plist` values, including API keys. Those bytes are encrypted at rest.

Agents do not see them. Every MCP tool result passes through `guard.scrub_agent` ([mcp_server/guard.py](../src/viewlift_ai/mcp_server/guard.py)), which replaces credentials, cookies, API keys, signed URL parameters, and account identifiers with `[REDACTED]`. `RedactionService` remains in the tree and is not applied on the dashboard path.

## Reveal (raw data)

`RevealSession` ([services/reveal.py](../src/viewlift_ai/services/reveal.py)) keeps a second copy of headers and bodies in process memory. The dashboard already shows the stored copy. Replaying a flow still requires unlocking with a per-process token (constant-time compare). The token rotates and the cache is wiped on shutdown, on last-tab close and on "Clear all data". HAR and cURL from the dashboard use the stored bytes. `POST /api/flows/send` is loopback-only and returns the response as received. The MCP server never unlocks reveal and strips the token from responses.

## SDK pairing

- Pairing tokens: one use, 5 minutes, constant-time compare. Minted from the dashboard or by presenting the debug key to `/v1/debug/pair`.
- Session tokens: 1 hour, Bearer, 600 requests per minute.
- A session is bound to the first `client_id` it presents.
- The SDK pins the receiver certificate's SHA-256 fingerprint and bypasses system trust.
- Size limits: 1 MiB request, 256 KiB per body, 512 KiB per debug event.

## Debug bridge guardrails

- Only commands in `READ_COMMANDS` or `MUTATING_COMMANDS` ([services/debug_session.py](../src/viewlift_ai/services/debug_session.py)) are accepted.
- Mutating commands need a one-use, 30-second confirmation bound to the client and command name. MCP tools add their own `confirm: true` check.
- All debug routes are loopback-only.
- Keychain events are scrubbed of `value`, `data`, `secret`, `password` and `token` keys. The SDK itself never reads keychain values.
- Commands expire after 30 seconds.

## Process safety

All subprocess calls use argument lists, never a shell (tested). Deep links reject `javascript`, `file`, `data`, `about` and `blob` schemes. UDIDs and log filters are validated by regex before reaching `simctl`.

## Developer tunnel and UI runner

The iOS 17+ developer tunnel (`device_tunnel_start`) runs `pymobiledevice3 remote tunneld` through an administrator prompt (`osascript` with administrator privileges). The console process stays unprivileged. Start it only from Setup, and only when a device needs location, a screenshot, or syslog. `VIEWLIFT_TUNNELD_ADDRESS` must be loopback.

The UI runner is a signed XCUITest process. It connects out to the receiver with the same debug key and certificate pin as an SDK app. It can launch and drive the bundle id you pass, on a simulator or a trusted device. Treat it as a debug tool: test data only, and stop it when the task ends.

## Release builds

The Swift SDK does nothing unless compiled with `DEBUG` or `VIEWLIFT_CAPTURE_ENABLED`. Never enable it in App Store builds.

## Known gaps

These were found while reading the code for this documentation. They are current behavior, not design goals.

1. **Receiver and MCP ports.** The receiver defaults to 8743 and refuses the MCP port. Setting `VIEWLIFT_INGEST_PORT` to the MCP port fails the bind on purpose.
2. **No Origin or CSRF check.** Several POST endpoints parse JSON regardless of content type, and there is no `Origin` or `Host` validation. A web page open in the same browser could probably send simple cross-site requests to endpoints such as `/api/debug/invoke`, `/api/setup/proxy` or `/api/logs/start`. DNS rebinding is a similar risk.
3. **Breakpoint payloads are raw.** Paused flows are sent unredacted on `/ws/flows` (not scoped by app) and `/api/breakpoints/pending`.
4. **Logs are not redacted.** Simulator, device and SDK log messages are encrypted but not redacted.
5. **The dashboard shows `Authorization`.** MCP masks it. `extra_redaction_keys` is no longer applied before storage.
6. **The unlock token is readable** from `/api/setup/status`, which is not loopback-guarded. Reveal gates by intent, not by secrecy.
7. **Retention edges.** `24h` cleanup only runs at startup. The last-tab wipe runs regardless of retention mode. The reveal cache has no size cap.
8. **SDK side.** Bodies over the cap are truncated before redaction, so truncated JSON is uploaded unredacted. Bonjour discovery can replace a configured receiver and pin, and the debug key is advertised in the TXT record. See [iOS SDK internals](ios-sdk-internals.md#known-issues).

## Key files

- [src/viewlift_ai/services/bind_policy.py](../src/viewlift_ai/services/bind_policy.py)
- [src/viewlift_ai/proxy/redact.py](../src/viewlift_ai/proxy/redact.py)
- [src/viewlift_ai/services/reveal.py](../src/viewlift_ai/services/reveal.py)
- [src/viewlift_ai/services/encrypted_store.py](../src/viewlift_ai/services/encrypted_store.py)
- [tests/test_proxy_security.py](../tests/test_proxy_security.py)

Next: [Rules and breakpoints](rules-and-breakpoints.md)
