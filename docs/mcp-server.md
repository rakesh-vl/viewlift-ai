# MCP server and tools

The MCP server is how a coding agent debugs a connected iOS or tvOS app. It is a separate process. It talks to the console on this Mac over loopback HTTP, then the console talks to the app. An agent has no more access than the dashboard, and less access to secrets: credentials are masked even when the console reveal session is unlocked.

There are 157 tools in 17 groups, 7 prompts, and 2 resources. A new chat lists the 37 debug tools (find the app, analyze, read captured data, `app_inspect`, and the state changes, plus `manage_workflows`). Build, simulator, UI, and the other groups stay out of the prompt until that chat turns them on; see [Build, run, and drive apps](#build-run-and-drive-apps). The Agent page (`/agent`) still lists every tool. `describe_command` and the `viewlift://commands` resource return the live command list, which is the one to trust if this page and the server ever disagree.

The dashboard **Tools** page (encoders, formatters, the scratchpad) is unrelated. Those run in the browser. Nothing in that page is an MCP tool.

## Connect

The server needs the `mcp` extra (official Python SDK 1.22). MCP 2 cannot be installed next to this project's mitmproxy pin.

```bash
uv sync --extra mcp
scripts/viewlift-ai.sh start
```

`start` runs the console in the foreground and the MCP server in the background, with `VIEWLIFT_AI_MCP_TRANSPORT=streamable-http`. `start --bg` backgrounds both. `stop` stops both. Logs: `.viewlift-ai.log` and `.viewlift-ai-mcp.log`.

| | |
|---|---|
| Console | `http://127.0.0.1:8741` |
| MCP | `http://127.0.0.1:8742/mcp` |
| Move the MCP port | `VIEWLIFT_AI_MCP_PORT` |

Cursor config for an app repo, or for this repo (already at `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

Stdio, only when the HTTP companion is not running. `--directory` is the Viewlift AI checkout. The console must still be running.

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

Enable **viewlift-ai** in Cursor Settings → MCP. The Agent page shows whether port 8742 is listening.

The SDK receiver defaults to port 8743 (`VIEWLIFT_INGEST_PORT`). MCP stays on 8742. They collide only if the receiver is pointed at the MCP port. The receiver refuses that bind.

## How an agent should work

1. `console_status`. If the console is down, the tools cannot help.
2. `list_apps` with `online_only: true`. Pass that app's `client_id` on every later call. A bundle id works only when exactly one online app has it. Several apps on one phone are different clients. The server never picks the newest app.
3. Start broad. `triage_app` for a vague bug. `failed_requests` or `network_summary` for API trouble. `summarize_logs` for crashes and warnings.
4. Open one item. `get_flow`, `get_log`, `compare_flows`, or one `app_inspect` command.
5. After asking the user to tap something, call `wait_for_flow` or `wait_for_log` instead of polling.
6. Say what a write will change, then pass `confirm: true`.
7. Build, targets, simulator, UI, docs, and debugger tools are off until `manage_workflows`. Enable one group for the task, then disable it so later turns stay small.

`describe_command` with no argument lists every on-device command and the payload it expects.

## Safety

Every console response passes through `guard.scrub` before an agent sees it. The console's own redaction only covers cookies and `password` / `secret` JSON keys. The MCP layer masks more, including when reveal is unlocked.

| Always masked | Left readable |
|---|---|
| `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` | Header names, cookie names, domains, paths |
| JSON keys and name/value pairs whose name contains token, secret, password, apikey, credential, cookie, session id, csrf, or jwt | The key or pair name |
| Bearer, Basic, and JWT text inside logs and JSON strings | Surrounding message text |
| Signed URL parameters: `token`, `sig`, `signature`, `key`, `hdnts`, `Policy`, `X-Amz-*` | The rest of the URL |
| AWS, Google, GitHub, Slack, and Stripe key formats, and PEM private keys | |
| Reveal unlock tokens and mutation confirmation tokens (dropped) | |
| FairPlay SPC, CKC, and certificate material | DRM step timing and error codes |

Strings longer than 16,000 characters are truncated. Screenshot and layer image bytes are removed from JSON. `ui.screenshot` and `bug.capture` return the PNG as an MCP image instead.

| Switch | Effect |
|---|---|
| `VIEWLIFT_AI_MCP_READ_ONLY=1` | Every state-changing tool refuses, before it contacts the app |
| `VIEWLIFT_AI_MCP_ALLOW_REVEAL=1` | `get_flow` with `reveal: true` may read raw bodies after the console reveal session is already unlocked. Credentials stay masked. Off by default. |

`security_policy` and `viewlift://policy` report the active rules. `get_session_audit` lists this process's tool calls with the arguments already scrubbed.

Tools set MCP hints so a client can auto-approve reads and prompt for writes. `openWorldHint` is false on every tool except the three `apple_docs_*` tools, which read public pages from developer.apple.com. Nothing else calls the public internet on its own. `app_mutate`, `replay_flow`, `erase_sims`, `clean`, `swift_package_clean`, `debug_attach_sim`, and `debug_lldb_command` are marked destructive.

Agents cannot turn on proxy rewrite, map local, map remote, or breakpoints. `list_rules` only reports them. Agents cannot unlock reveal. Replay sends a captured request only when a person has already unlocked reveal in the dashboard.

## Find the app

These never run code on a device.

| Tool | Arguments | Returns |
|---|---|---|
| `console_status` | | Reachable or not, online apps, whether reveal is unlocked, whether any rewrite rule is active, and the policy |
| `security_policy` | | The mask list and both switches. No console call |
| `list_devices` | | Apps grouped by `device_key`. One phone can hold many apps |
| `list_apps` | `device_key?`, `bundle_id?`, `online_only?`, `auth_state?` | `client_id`, name, bundle id, device, model, `auth_state`, platform, online. Same app on two phones is two rows. User ids stay on the dashboard. |
| `get_app` | `client_id` | Version, build, capabilities, URL schemes, linked frameworks. The unlock token is removed |
| `describe_command` | `command?` | One command, or every command, with read/write and the payload note |

## Analyze

Read these before opening individual flows or log lines. They summarize data the console already stored.

| Tool | Arguments | Returns |
|---|---|---|
| `triage_app` | `client_id`, `include?` | One report. Sections run in parallel: `performance`, `memory`, `hangs`, `crashes`, `screen`, `network`, `logs`. Default is all of them. `crashes` comes from the crashes the console stored, so it works after the app has died. Unknown section names are rejected and nothing is sent |
| `crash_reports` | `client_id`, `limit?` | Recent crashes with `headline`, `crash_site`, replay status, signal or exception, thread, and the stack as `symbol (file:line)`. Includes symbolication status and the missing dSYM when there is one |
| `crash_detail` | `client_id`, `crash_id?` | One crash, newest by default: `headline`, `root_cause`, `crash_site` with code, `suggested_fix_area`, `steps_to_reproduce`, `timeline`, `environment`, full stack, `brief` (markdown), replay and report URLs, and the last screen as an image |
| `network_summary` | `client_id`, `host?`, `limit?` | Per host: count, failures, failure rate, p50, p95, bytes. Plus the slowest calls, the largest calls, requests seen 3 or more times, GraphQL operation counts, and short hints (TLS, 5xx, 401/403, retries, calls over 3 seconds) |
| `failed_requests` | `client_id`, `host?`, `limit?` | 4xx, 5xx, transport errors, and TLS failures, grouped by method, host, path, and status, with example flow ids |
| `summarize_logs` | `client_id`, `level?`, `limit?` | Counts by level, source, and subsystem. Warning and error lines are grouped by signature, with UUIDs, hex, and numbers collapsed, so the same failure counts as one issue |
| `compare_flows` | `flow_a`, `flow_b`, `client_id` | What differs: status, size, timing deltas, request and response headers, and JSON body paths. Both flows must belong to the app |
| `export_request` | `flow_id`, `client_id`, `language?` | The request rebuilt as `curl` (default), `swift` (`URLSession`), or `python`. Masked values stay `[REDACTED]`. The result is not runnable until those are filled in by a person |
| `wait_for_flow` | `client_id`, `host?`, `method?`, `status?`, `text?`, `timeout_s?` | Blocks until a matching request arrives that was not in the list at call time. Default 15 seconds, cap 30. Use this after a tap |
| `wait_for_log` | `client_id`, `text`, `level?`, `timeout_s?` | Same wait, for a new SDK log line containing `text` |

`limit` on summaries is capped at 1,000. `list_flows` and `list_logs` cap at 200.

## Crash reports

The console finds crashes two ways. The SDK records them and sends them on the next launch (or as the app dies, for an uncaught exception). A watcher also reads new `.ips` files in `~/Library/Logs/DiagnosticReports` every second and matches each to an app by bundle id, executable, and simulator UDID. Both land on one card per app and pid. A system report is never replaced by an SDK record, and a symbolicated copy replaces a raw one.

Symbolication runs on the Mac. It finds the dSYM by image UUID (Spotlight, then DerivedData and Archives), runs `atos`, and expands inlined frames. `symbolication.status` is `full`, `partial`, `missing_dsym`, or `not_needed`. Without a dSYM the frames keep their addresses.

Every stored crash also gets a `diagnosis`, refreshed whenever the card changes:

- `headline`: what failed in plain words. It comes from the `Swift runtime failure: ...` frame, the reason, `Fatal error:` console lines, the exception name, or the signal.
- `crash_site`: the first app frame with `file`, `line`, `function`, `path`, and a 7-line `snippet`. The snippet is read only when the file is inside a project root (Setup page or `VIEWLIFT_AI_PROJECT_ROOTS`).
- `root_cause`, `suggested_fix_area`, `steps_to_reproduce`, `timeline` (screens, taps, presses, requests, console errors, with seconds before the crash), `app_stack`, and `environment`.

The SDK adds `context` (screens, taps, last 10 requests without query strings, console tail, performance, app and device) and `replay`. `replay.status` is `pending` (recorded, uploads on the next launch), `uploading`, `ready`, or `failed`. The video is assembled from `crash/replay-chunk` events into `crash-media/<crash id>.mp4` in the data folder.

Routes, all loopback only and taking `client_id`:

| Route | Returns |
|---|---|
| `GET /api/debug/crashes?limit=` | Stored crashes, newest first |
| `GET /api/debug/crashes/{id}/replay.mp4` | The replay. Range requests work. `download=1` sends it as an attachment |
| `GET /api/debug/crashes/{id}/poster.jpg` | Last frame before the crash |
| `GET /api/debug/crashes/{id}/report.md` | Markdown brief for a person or an agent. `download=1` for a file |
| `GET /api/debug/crashes/{id}/detail` | Crash, brief, and poster as JSON, for `crash_detail` |

Crashes stream on `/ws/debug` as `debug-event` and `debug-crash`. The dashboard toasts a live crash on every page and opens Debug, then Hangs. The same socket also toasts a hang (`hang` / `detected`), a memory leak, and a failed playback event. Set `VIEWLIFT_CRASH_WATCH=0` to turn the `.ips` watcher off. See [Web UI](web-ui.md#debug-suite).

### Fix a crash with an agent

1. `crash_reports` for the app. Pick a crash by `headline` and `crash_site`.
2. `crash_detail`. It returns the diagnosis, the stack, the brief, and the last screen as an image.
3. Open `crash_site.path` at `crash_site.line` and fix the cause in `headline`.
4. Rebuild, run without the debugger, repeat `steps_to_reproduce`, and check `crash_reports` for a new crash.

When an app goes silent while Xcode's debugger is attached, the app row shows a hint: the debugger holds the crash, so continue or detach.

## Read captured data

Stored on this Mac. Bodies and headers are redacted. Unattributed proxy rows (no `client_id`) are left out of per-app results.

| Tool | Arguments | Returns |
|---|---|---|
| `list_flows` | `client_id`, `method?`, `host?`, `status?`, `text?`, `limit?` | HTTP summaries: method, host, path, status, timing, size, GraphQL operation |
| `get_flow` | `flow_id`, `client_id`, `reveal?` | One flow. A flow owned by another app is refused. `reveal: true` returns raw bodies only when `VIEWLIFT_AI_MCP_ALLOW_REVEAL=1` and reveal is already unlocked. Image bodies lose their base64 |
| `list_logs` | `client_id`, `level?`, `subsystem?`, `text?`, `process?`, `limit?` | SDK logs for the app, plus syslog lines whose message mentions its bundle id or executable and that are not tagged to a different app |
| `get_log` | `log_id`, `client_id` | The full line and `linked_flow_id` when the line carried a request id. A line owned by another app is refused |
| `list_debug_events` | `client_id`, `module?`, `limit?` | Events and command results the app posted |
| `list_packages` | `client_id` | Swift packages resolved for that app's Xcode build |
| `list_rules` | | Map local, map remote, rewrite, throttle, breakpoints, and any paused breakpoint. Read-only |
| `get_session_audit` | `limit?` | Tool calls this MCP process has made, newest first, arguments scrubbed |

## Ask the running app

`app_inspect` takes `client_id`, `command`, and an optional `payload`. It enqueues one read command and waits for that app's result (about 12 seconds, 25 at most). A command runs only if the app enabled its module. A disabled module returns `moduleDisabled`.

`ui.screenshot` and `bug.capture` also return a PNG. Hierarchy and layer captures do not: their embedded images are stripped so they do not fill the context. Pass a returned `handle` back as `payload.handle` to open the next object. Handles are weak and short-lived.

| Area | Command | Payload | What you get |
|---|---|---|---|
| Performance | `performance.snapshot` | | CPU, FPS, memory now |
| Memory | `memory.snapshot` | | Footprint, tracked objects, suspected leaks |
| Memory | `memory.graph` | | Tracked objects and who retains them |
| Memory | `memory.leaks` | | Class, creation stack, retainers, cycle path, replay frames |
| Memory | `memory.heap` | | Live instance counts per class, and growth since the last call |
| Memory | `memory.object` | `id` | One tracked object: class chain, lifetime, stack, retainers, cycles |
| Memory | `memory.live_start` | | Start the live memory feed and a rolling screen replay |
| Memory | `memory.live_stop` | | Stop both |
| UI | `ui.hierarchy` | | View tree with frames and ids. Depth 100, up to 2,500 nodes |
| UI | `ui.describe` | `id` | Class, properties, SwiftUI or UIKit role |
| UI | `ui.layers` | `offset?`, `ids?`, `scale?` | Page of per-view layer sizes. Images are omitted |
| UI | `ui.screenshot` | | PNG of the key window, also as an MCP image |
| UI | `ui.focus` | | Focused view id and frame, and the last focus rejection |
| UI | `ui.accessibility_tree` | `limit?` | Elements by role, label, identifier, value, and frame, plus the screen name and the focused element |
| UI | `ui.find` | `label?`, `identifier?`, `text?`, `role?`, `view_id?` | The matching elements only |
| UI | `ui.wait_for` | the same keys, `gone?`, `timeout?` | Waits (10 seconds at most) until an element appears, or disappears with `gone` |
| Previews | `preview.list` | | Views the app registered with `ViewliftPreviews.register`, with their states |
| Previews | `preview.render` | `name`, `state?`, `color_scheme?`, `width?`, `height?`, `scale?` | PNG of one registered view, drawn off screen, under 480 KB |
| SwiftUI | `swiftui.renders` | | Re-render counts per tracked view |
| Assets | `assets.audit` | | Images larger than the size they are shown at |
| Accessibility | `accessibility.audit` | | Missing labels and small touch targets |
| Storage | `storage.defaults` | | UserDefaults keys. Secret-like values are masked |
| Storage | `storage.keychain_metadata` | | Keychain items, attributes only, no secret bytes |
| Storage | `storage.files` | `path` | Sandbox listing. Paths are relative to the app home |
| Storage | `storage.file_read` | `path` | One file, up to 512 KB |
| Storage | `storage.sqlite_schema` | `path` | Table list. The database is opened read-only |
| Storage | `storage.sqlite_query` | `path`, `query` | One `SELECT`, `PRAGMA`, `WITH`, or `EXPLAIN`. Up to 1,000 rows |
| Storage | `storage.cookies` | | `HTTPCookieStorage` cookies. Values are masked |
| Runtime | `runtime.search` | `query` | Objective-C classes matching the query |
| Runtime | `runtime.describe` | `class` | Methods and properties |
| Runtime | `runtime.libraries` | | Loaded images and their class counts. App images come first |
| Runtime | `runtime.image_classes` | image path | Classes defined in that image |
| Explorer | `explorer.roots` | | Shortcuts: application, delegate, key window, top view controller, and others |
| Explorer | `explorer.object` | `handle`, `view_id`, or `memory_id` | Class chain, properties, ivars, Swift fields, methods, elements |
| Explorer | `explorer.instances` | `class`, `limit?` | Live instances of that class and its subclasses |
| Settings | `settings.get` | | Modules, network-capture switch, and body limits as applied on the device |
| Macros | `macro.list` | | Recorded touch macros |
| Bug | `bug.capture` | | Screenshot plus a performance and memory snapshot |
| OTT | `ott.snapshot` | | User, plan, TVE, theme, modules, metadata map |
| Hangs | `hang.list` | | Main-thread stalls with screen, taps, console, and stack |
| Crashes | `crash.list` | | Fatal signals and uncaught exceptions the SDK recorded, with frames and images |
| Activity | `activity.snapshot` | | Current screen and playback state. No stream URL |
| Activity | `activity.live_start` | `preview?` | Push screen and playback about once a second. `preview: true` also pushes a small JPEG |
| Activity | `activity.live_stop` | | Stop that feed |
| Playback | `playback.snapshot` | | Stalls, bitrate, live edge, route. No stream URL |
| Playback | `playback.drm` | | FairPlay step timing and error codes. No SPC or CKC |
| Playback | `playback.waterfall` | | HLS and DASH request metadata. No query string or body |

`memory.live_start` and `activity.live_start` only arm a stream. The samples themselves are ephemeral and are not stored as debug events. Call the matching `live_stop` when finished. Take `memory.heap` twice, with the user exercising the app in between, to see growth.

## Change state

Every tool in this section requires `confirm: true`. Call it only after telling the user what will change. `VIEWLIFT_AI_MCP_READ_ONLY=1` makes all of them return an error and send nothing.

`app_mutate` takes `client_id`, `command`, `payload`, and `confirm`. The console mints the one-use device confirmation itself. The agent never sees that token.

| Tool or command | Arguments | Effect |
|---|---|---|
| `set_network_profile` | `profile`, `client_id?`, `confirm` | `wifi`, `4g`, `3g`, `edge`, `gprs`, `offline`, or `off`. With `client_id`, the SDK shapes that app's captured traffic (`net.profile`). Without it, the proxy throttles every proxied app on this Mac. Does not enable rewrite |
| `open_deeplink` | `url`, `client_id`, `confirm` | Opens the URL on the simulator or paired device that hosts this app |
| `set_location` | `udid` or `client_id`, `latitude?`, `longitude?`, `clear?`, `confirm` | Simulated GPS on a simulator or a paired iPhone, iPad, or Apple TV. A device on iOS 17+ needs the developer tunnel. `udid` is the hardware id, not an app `device_key` |
| `replay_flow` | `flow_id`, `confirm` | Resends one captured request. Fails unless reveal is already unlocked. Does not unlock it |
| `ui.highlight` | `id` | Outlines one view |
| `ui.edit` | `id` plus fields | Changes a view property |
| `ui.visual` | flags | Guidelines, baselines, oversized-image marks, repaint borders, 5× slower animations |
| `storage.defaults_set` | `key`, `value` | Writes one UserDefaults key |
| `storage.defaults_delete` | `key` | Removes one key |
| `storage.file_write` | `path`, `content` | Writes a sandbox file |
| `storage.file_rename` | `path`, `name` | Renames a file or folder |
| `storage.file_delete` | `path` | Deletes a file or folder. Top-level containers are refused |
| `storage.snapshot_create` | `paths` | Saves defaults and the named files |
| `storage.snapshot_restore` | snapshot | Restores a saved snapshot |
| `storage.cookie_delete` | `name`, `domain`, `path` | Deletes matching cookies |
| `macro.record_start` | | Starts recording touches |
| `macro.record_stop` | | Stops and saves the macro |
| `macro.replay` | | Replays a recorded macro |
| `push.local` | title, body, … | Schedules a local notification |
| `nav.open_url` | `url` | Opens a URL inside the paired app. `javascript`, `data`, and `file` are rejected. The query is not echoed back |
| `explorer.set` | `handle`, `key`, `value` | Sets one Objective-C property through KVC |
| `explorer.call` | `handle`, `selector` | Calls a zero-argument method. `init`, `set`, `copy`, `dealloc`, and teardown selectors are refused |
| `settings.set` | see below | Persists device overrides until reset |
| `ui.tap` | element query, or `x` and `y` | Runs the element's action: a control event, a cell selection, or `accessibilityActivate` |
| `ui.long_press` | element query, `action?` | Runs a named accessibility custom action (context menus usually expose one) |
| `ui.swipe`, `ui.scroll` | `direction`, element query? | Scrolls the nearest scroll view one page. `swipe` takes finger direction, `scroll` takes content direction |
| `ui.type_text` | `text`, element query?, `replace?`, `submit?` | Types into the focused field or the matched one. The text is not echoed back |
| `ui.key` | `key` | `return`, `delete`, `tab`, or `escape` on the focused input |
| `ui.remote` | `press`, `repeat?` | tvOS only: moves focus up, down, left, or right, or sends `select`, `menu`, or `playpause` |

`settings.set` accepts `modules`, `capture_network`, `max_request_body_bytes`, `max_response_body_bytes`, `body_content_types`, and `reset`. Overrides can turn off modules the app was built with. They cannot add modules the app did not enable. Body limits stay capped at 262,144 bytes.

## Build, run, and drive apps

These tools run Xcode, simulators, devices, LLDB, and SwiftPM on this Mac. They cover the tool set of [XcodeBuildMCP / MobileBuildMCP](https://www.mobilebuildmcp.com/docs/tools), element-based simulator control in the style of [ios-simulator-skill](https://github.com/conorluddy/ios-simulator-skill), Apple documentation search like [apple-doc-mcp](https://github.com/MightyDillah/apple-doc-mcp), and preview snapshot testing like [Prefire](https://github.com/BarredEwe/Prefire). They are reimplemented here, so the same scrubbing, audit, read-only switch, and confirm rules apply.

The MCP process does not run anything itself. It posts to the console at `POST /api/host/call`, which is loopback-only and requires a JSON body. The console runs each command as an argv list with a timeout, never through a shell.

### Workflows

Each optional group is a workflow. A chat lists a workflow's tools only after that chat enables it, so the schemas are not in the prompt the rest of the time. Every workflow starts off.

| Workflow | Tools |
|---|---|
| `project` | Discover projects and schemes, build settings, bundle ids, `clean`, scaffolding, plus `doctor`, session defaults, and build jobs |
| `targets` | One call for a simulator or a paired device: `list_targets`, `resolve_target`, `screenshot`, `record_video`, `launch_app`, `stop_app`, `install_app`, `build_run`, `open_url` |
| `simulator` | List, boot, open, and erase simulators; appearance, location, status bar, keyboards, push, privacy, clone; build, run, test, install, launch, and stop; screenshots and video |
| `ui` | `screen_map`, `snapshot_ui`, `find_element`, `tap`, `long_press`, `swipe`, `drag`, `gesture`, `touch`, `type_text`, `key_press`, `key_sequence`, `button`, `tv_remote`, `wait_for_ui`, `batch`, plus `ui_runner_start`, `ui_runner_stop`, `ui_runner_status` |
| `docs` | `apple_docs_list_frameworks`, `apple_docs_search`, `apple_docs_get` |
| `device` | Physical devices through `devicectl`, plus the developer tunnel and Apple TV pairing |
| `macos` | Build, run, test, launch, and stop macOS apps |
| `lldb` | Attach to a simulator or device app, breakpoints, continue, stack, variables, raw commands, detach |
| `swiftpm` | `swift build`, `test`, `run`, `stop`, `dump-package`, and `clean` |
| `coverage` | Coverage from the last test result bundle, per target and per file |
| `previews` | List source previews, list and render app previews, compare to baselines, approve baselines |

`manage_workflows` changes the list for this chat only and sends `notifications/tools/list_changed`. Enable one group for the task, then disable it when the task ends. Another chat is not affected. A tool that is not listed can still be called; listing is what fills the prompt.

`VIEWLIFT_AI_MCP_WORKFLOWS` is the starting set for a new chat. Leave it unset for the debug tools only. `project,targets,simulator,ui,docs` is the wider set, and `all` turns every group on.

### Project roots

Tools that take a path (projects, workspaces, packages, scaffolding, preview scans) only accept paths inside a project root. Set roots on the Setup page, or with `VIEWLIFT_AI_PROJECT_ROOTS` (colon-separated). Paths are resolved, symlinks included, before the check. `/` and the home folder cannot be roots. Build outputs in DerivedData are also readable.

### Defaults and jobs

`session_set_defaults` stores the project or workspace, scheme, configuration, simulator, device, and bundle id, so later calls can omit them. Named profiles (`session_use_defaults_profile`) keep an iOS and a tvOS setup side by side. `sync_xcode_defaults` reads the workspace Xcode last built.

Builds and tests run as background jobs. A call waits up to 25 seconds; if the job is still running it returns a `job_id`. `wait_job` waits up to 30 seconds more, `get_job` reads progress with a log tail, and `cancel_job` stops it. Results are condensed: errors, warnings, failed tests, and the pass count, not the full log.

`extraArgs` on build and test tools accepts a short flag list and plain `KEY=VALUE` build settings. Settings that inject compiler, linker, or loader input (`OTHER_*FLAGS`, `LD_*`, `DYLD_*`, script phases, signing) are refused.

### UI automation

Pass `client_id` to drive an app that runs the SDK, on a simulator or a real iPhone, iPad, or Apple TV. The SDK reads the accessibility tree, so elements are found by label, identifier, role, or text instead of coordinates. Queries accept `window_id` and `scene_id`. Frames include `screen_frame` in screen points so host taps line up under Stage Manager. Without `client_id`, the same tools drive a simulator through [AXe](https://github.com/cameroncooke/AXe) or [idb](https://fbidb.io), whichever is installed (`doctor` reports both).

Gestures the SDK cannot make (`drag`, `touch`, `button`, and a long press with no accessibility action) go to the UI runner when one is online for that app and device. Start it with `ui_runner_start` (confirm). It is an XCUITest bundle that connects out to the receiver. It needs a development team on a physical device (`developmentTeam` or `VIEWLIFT_DEVELOPMENT_TEAM`). Apple TV remote presses without `client_id` go to the simulator window.

`screen_map` returns a few lines: the screen name, tappable items, inputs, and visible text. Start with it; it costs far fewer tokens than a screenshot. `batch` runs up to 30 steps with one confirm.

Every input tool changes app state and requires `confirm: true`. `type_text` never echoes the typed text, and agents should only type test data. Long presses, drags, and raw touches have no public in-app trigger, so with `client_id` they return an error that points to the simulator path; `ui.long_press` runs an accessibility custom action when the element has one.

### LLDB

`debug_attach_sim` attaches `lldb` to a running simulator app, and `debug_attach_device` does the same for a paired device. The session stays open across calls. `debug_lldb_command` refuses commands that would run code on the Mac rather than in the app: `script`, `shell`, `platform shell`, `command script`, `command source`, `process launch`, `run`, target creation, and environment or argument changes.

### Apple documentation

`apple_docs_search` searches the framework indexes on developer.apple.com (default SwiftUI, UIKit, and Foundation) by exact name, prefix, wildcard (`Lazy*Grid`), or keywords, and lists symbols before articles. `apple_docs_get` returns one page: declaration, platforms, abstract, parameters, overview, topics, and related pages. Responses are cached for 7 days under the console data folder.

### Previews

`list_previews` scans Swift sources under the project roots for `#Preview` and `PreviewProvider`. Rendering needs the app: views registered with `ViewliftPreviews.register` (see [iOS SDK guide](ios-sdk.md#previews)) are listed by `list_app_previews` and drawn on the device by `render_preview`. `snapshot_previews` renders every registered state and compares each PNG with its baseline. The first run records the baseline. Later runs report `passed` or `changed` with the changed pixel count and region. `approve_preview_baseline` accepts the new images. Baselines live under the console data folder, one suite per bundle id and platform.

### Not ported

Xcode IDE bridge tools (`xcode_ide_*`, `xcode_tools_bridge_*`) and Android tools are not included.

### Capability matrix

`list_targets` returns this set per destination. A button or tool that needs a missing capability returns a clear error.

| | iOS sim | iPad sim | tvOS sim | iPhone | iPad | Apple TV |
|---|---|---|---|---|---|---|
| Build, install, launch, stop, deeplink | yes | yes | yes | yes | yes | yes |
| Screenshot | yes | yes | yes | yes, tunnel on 17+ | yes, tunnel on 17+ | yes, tunnel on 17+ |
| Screen recording | yes | yes | yes | USB only | USB only | no |
| Simulated location | yes | yes | yes | yes, tunnel on 17+ | yes, tunnel on 17+ | yes, tunnel on 17+ |
| UI runner | yes | yes | yes | yes, signing team | yes, signing team | yes, signing team |
| Host UI driver (AXe or idb) | yes | yes | yes | no | no | no |
| Privacy grants, status bar, erase, clone | yes | yes | yes | no | no | no |
| Push | yes | yes | no | yes | yes | badge only |
| `tv_remote` | no | no | yes | no | no | yes |

## Prompts and resources

Prompts are workflows, not tools. The client fills the arguments and the agent follows the steps.

| Prompt | Arguments | Steps |
|---|---|---|
| `triage_bug` | `client_id`, `symptom` | `triage_app`, then `failed_requests` and `summarize_logs`, then one drill-down. End with a cause and evidence. Do not change state unless the user agrees |
| `investigate_request` | `client_id`, `endpoint` | `list_flows`, `compare_flows` on a good call and a bad call, then `network_summary`. Offer `export_request` in Swift |
| `check_screen` | `client_id` | Screenshot, `activity.snapshot`, accessibility and asset audits, SwiftUI re-renders, performance |
| `hunt_memory_leak` | `client_id`, `flow` | `memory.heap` before and after the user runs the flow, then `memory.leaks`, `memory.graph`, and `memory.object` |
| `build_and_verify` | `scheme`, `change` | `doctor` and defaults, a confirmed `build_run_sim` polled with `wait_job`, then `screen_map` and taps to reach the change. Fix compile errors from the condensed summary |
| `walk_screen_flow` | `flow`, `client_id?` | `screen_map`, then one confirmed `tap`, `type_text`, or `swipe` per step, with `wait_for_ui` and `screen_map` after each. Without `client_id` it drives the booted simulator |
| `check_preview` | `client_id`, `name` | `list_previews` and `list_app_previews`, `render_preview` in light and dark, then `snapshot_previews`. Approve a baseline only after the user agrees |

| Resource | Contents |
|---|---|
| `viewlift://commands` | Every on-device command with its note, split into read and write |
| `viewlift://policy` | The mask list, the switches, and the string limit |

## Adding a tool

| File | Role |
|---|---|
| [server.py](../src/viewlift_ai/mcp_server/server.py) | FastMCP wrappers, annotations, prompts, resources. `main()` picks the transport |
| [tools.py](../src/viewlift_ai/mcp_server/tools.py) | Tool logic and `resolve_target`. No MCP or FastAPI imports |
| [guard.py](../src/viewlift_ai/mcp_server/guard.py) | Scrubbing, the read-only and reveal switches, the call audit |
| [insights.py](../src/viewlift_ai/mcp_server/insights.py) | Network, log, diff, and code generation over rows that are already scrubbed |
| [client.py](../src/viewlift_ai/mcp_server/client.py) | Loopback HTTP client. 40 second timeout |
| [runtime.py](../src/viewlift_ai/mcp_server/runtime.py) | Transport and port |
| [catalog.py](../src/viewlift_ai/mcp_server/catalog.py) | `TOOL_GROUPS` and `COMMAND_NOTES` for the Agent page. Each group names its workflow |
| [workflows/](../src/viewlift_ai/mcp_server/workflows/) | Workflow tools, one module per area, and `WorkflowGate` |
| [services/host/](../src/viewlift_ai/services/host/) | The console side of workflow tools: the argv runner, project roots, jobs, defaults, and one module per tool family |
| [host_routes.py](../src/viewlift_ai/api/host_routes.py) | `/api/host/tools`, `/api/host/call`, and `/api/host/roots` |

To add a tool: implement it in `tools.py` against `ConsoleClient`, wrap it in `server.py` with a one-line docstring (agents read that docstring), add a `ToolDoc` in `catalog.py`, and test it. `tests/test_agent_page.py` fails when the catalog and the registered tools differ. Route the console response through `_call` so scrubbing and the audit apply. Tests live in [tests/test_mcp_tools.py](../tests/test_mcp_tools.py) and [tests/test_mcp_guard.py](../tests/test_mcp_guard.py).

To add a workflow tool: add a handler in a `services/host` module and register it with `toolkit.add(name, handler, mutating=...)`. Validate every argument with the helpers in `runner.py` and resolve paths through `ProjectRoots`. Wrap it in the matching `workflows/` module with `host(...)`, add its `ToolDoc` to the workflow's group in `catalog.py`, and test it with a fake runner as in [tests/test_host_tools.py](../tests/test_host_tools.py).

To add an on-device command: add it to `READ_COMMANDS` or `MUTATING_COMMANDS`, add a `COMMAND_NOTES` entry, and handle it in the Swift executor. See [Contributing](contributing.md#add-a-debug-command). Payload and module limits are in [iOS SDK internals](ios-sdk-internals.md#commands).

Next: [Web UI](web-ui.md)
