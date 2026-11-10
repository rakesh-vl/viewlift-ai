# Web UI

The dashboard is server-rendered Jinja templates with plain JavaScript. There is no build step: edit files in [src/viewlift_ai/web](../src/viewlift_ai/web) and reload.

## Shell

Every page extends [base.html](../src/viewlift_ai/web/templates/base.html):

- left rail: Network, GraphQL, Logs, Debug, Devices, Tools, Agent, Setup, Health, with a live indicator, theme toggle and rail collapse;
- top bar: scope picker and "Clear all data". The picker separates the same app on two phones (device name, model, short device key, LAN address) and filters by bundle or by who was signed in (guest, logged in, TVE, user id, profile).

[shell.js](../src/viewlift_ai/web/static/js/shell.js) runs on every page. It owns the scope picker (`window.VLScope`), sends `/api/session/ping` and `/api/session/leave` for tab presence, handles theme and rail state, and listens to `/ws/debug?client_id=__apps__` to refresh the app list. See [Multi-app and scope](multi-app-and-scope.md#dashboard-scope-picker).

Design guidance lives in [DESIGN.md](../DESIGN.md).

## Pages

| Page | Template | Script entry | Main endpoints |
|---|---|---|---|
| Network `/` | `dashboard.html` | `app.js` `initDashboard` | `/api/flows`, `/api/flows/delete`, `/ws/flows`, `/api/reveal/*`, `/api/export/har`, `/api/flows/{id}/curl`, `/api/flows/send` |
| GraphQL `/graphql` | `graphql.html` | `graphql.js` | `/api/graphql/endpoints`, `/api/graphql/execute`, `/api/graphql/schema` |
| Flow detail `/flows/{id}` | `flow_detail.html` | server-rendered | `/api/flows/{id}/replay`, `/api/flows/{id}/curl`, `/api/flows/send` |
| Logs `/logs` | `logs.html` | `app.js` `initLogs` | `/api/logs/*`, `/ws/logs` |
| Setup `/setup` | `setup.html` | `app.js` `initSetup` | `/api/setup/*`, `/api/rules`, `/api/apps`, `/api/host/call` (tunnel, Apple TV pairing) |
| Debug `/debug` | `debug_suite.html` | `debug.js` | `/api/debug/*`, `/ws/debug` |
| Devices `/simulator` and `/devices` | `simulator.html` | `simulator.js` | `/api/host/targets`, `/api/host/call` |
| Tools `/tools` | `tools.html` | `js/toolkit/*.js`, `tools.js` | `/api/tools/scripts`, `/api/tools/action/{name}` |
| Agent `/agent` | `agent.html` | `agent.js` | `/api/agent/status`, `/api/debug/clients` |

### Network

Filter bar (method, host, status, content type, kind, operation, text), HAR export, reveal bar (token, unlock, lock) and an SDK activity hint. Kind is Any, GraphQL, or HTTP. Operation matches the GraphQL operation name. GraphQL rows show that name in the path column and `graphql` in the type column. A split view shows the live flow table and a detail pane with overview, headers, cookies, bodies (JSON tree, image or text), query, timing waterfall, WebSocket frames, and an Edit tab. Edit prefills the visible request. Copy cURL on that tab builds a multiline command in the browser from the form, including edits, so it can be imported into Postman. Send posts the form to `/api/flows/send` and shows the response as received. The header Copy cURL button still copies the stored request. Delete flows removes stored requests only. Device logs, debug events, and the proxy allowlist stay.

### GraphQL

Pick a saved endpoint or type any `http` or `https` URL. The page edits the query, variables, and headers, then Run posts them from this Mac. Saved basic auth from Setup is added only for the same scheme, host, and port, and only when the request has no `Authorization` header. Setup stores one base URL. Requests go to `/graphql` on that host, and the hosted playground is always `/playground` on the same host. Hosts already seen on captured GraphQL calls can fill the base. If the URL field is empty, the page uses the newest captured host for the current scope. Schema runs introspection. Searching the schema and choosing a field inserts a short operation. Named operations stay in this browser under `vl-graphql-v1:<endpoint id>`. Prettify uses the toolkit GraphQL formatter. On a captured GraphQL flow, Edit → Open in GraphQL fills the query, variables, and headers. Hop-by-hop headers are left out.

The password is write-only.

### Logs

Pick a simulator or device, set process and subsystem filters, start, stop, pause or delete. Paused rows are buffered. Selecting an app in the scope picker auto-targets its simulator and executable. Log detail links to the matching flow by `request_id`.

### Setup

CA status and one-use downloads (PEM and iOS profile), proxy bind (interface, port, LAN with basic auth), the HTTPS decryption allowlist (one host or glob per line; empty denies all), iPhone and Apple TV steps, the developer tunnel (administrator prompt, console stays unprivileged), Apple TV PIN pairing, simulator helpers and a test request, the SDK receiver (interface, port, pairing token, receiver URL, pinned fingerprint, app activity, Swift snippet) and the connected apps tree.

### Devices

`/simulator` and `/devices` list simulators and paired devices from `/api/host/targets`. Each row shows platform (iPhone, iPad, Apple TV) and connection (USB, network, or local). Buttons for boot, screenshot, launch, stop, build and run, open URL, location, recording, and the UI runner stay hidden until that target lists the capability. Project, scheme, bundle id, and development team come from session defaults. Deeplink and location forms on Debug use the same target list. A simulator `device_key` or a unique device model selects the matching row.

### Debug Suite

A session bar for the selected app and tabs: OTT, Analytics, Hangs, Performance, Memory, UI, SwiftUI, Assets, Accessibility, Storage, Flags, Bug report, Macros, Runtime, Explorer, Push, Network tools (profiles, deep link, location) and Device settings. Read commands go to `/api/debug/command`; mutating ones call `/api/debug/confirm` first. Results stream on `/ws/debug`.

The Hangs tab also holds the crash list. A crash appears as a card the moment the console learns of it:

- What happened: a plain-language headline, the crash site as `file:line` in the function, the code around that line with the crash line marked, the likely cause, and where to fix it.
- Replay: the last ten seconds of the screen with taps drawn and a countdown to the crash, playable on the card and downloadable as MP4. Before it arrives the card says it uploads on the app's next launch.
- Timeline: screens, taps, remote presses, requests, and console errors, in seconds before the crash. Clicking a row jumps the video to that moment.
- Steps to reproduce, last requests, console lines, and the environment.
- The full stack with app frames marked, system frames dimmed, and inlined frames tagged, plus other threads from the macOS report. A card without symbols names the missing dSYM.
- Copy for AI copies the markdown brief. Download report saves it. Copy stack copies the plain stack.

The bell in the scope bar opens the notification center. Show alerts turns the whole feature off: no toasts, and nothing new is collected. Group by type folds a burst into one toast (`Request failed · 3` keeps the latest line) and lists the center under a heading per type. Each type has its own checkbox: Crashes, Hangs, Memory leaks, Playback, Failed requests, Console errors. The choice is saved in this browser. Toasts sit at the bottom right, show at most 4 at a time, leave after 12 seconds, and open the matching page. Failed requests or console errors that arrive within 3 seconds of each other share one row in the center (`N times`), so a busy app cannot fill the list. The center keeps the last 40 for this tab. Clear removes them and dismisses the toasts on screen. The app switcher marks a crashed app.

The scope picker lists only apps that are running now (online, background, or crashed), with the most recently active device first. Each option names the device and model, and the signed-in user or guest when the app has reported a session. **By app** selects one bundle on every device. **By account** selects a user id, a profile, or guests. Those account filters follow who was signed in when the request was captured. Offline and reinstalled instances are hidden. When a running app goes offline, one open tab asks whether to delete its data. A crashed app or one paused in a debugger is not offered, so its report stays. Confirming deletes that app's flows, logs, debug events, crash replay media, and its alerts, then refreshes the dashboard. If that app was selected, the scope returns to All apps. Clear app data does the same for the current selection. Clear all data does it for every app and empties the notification center. `/debug#hangs`, `/debug#memory`, and `/debug#ott` open that tab.

Explorer opens live objects. The left column groups shortcuts into Current screen, Your app, and System, and finds live instances by class name (with suggestions from Runtime). Each object has Fields, Methods, and Elements tabs, a filter, a back button, and breadcrumbs. UIKit and Foundation levels are hidden until "Show system classes" is on. Writable properties can be edited inline. Only zero-argument methods can be called, and `init`, `set`, `dealloc` and other destructive selectors are refused on the device. Objects are referenced by short-lived handles, never by address.

Runtime lists the app's own classes first. Search ranks matches, "App only" hides system frameworks, and the arrow keys move through results. A class shows its superclass chain, protocols, and filterable properties, ivars, and methods. "Live instances" opens them in Explorer and "Copy interface" copies an Objective-C header. Loaded libraries list the classes in each image.

Accessibility scans the current screen and draws each finding on a screenshot. Findings are grouped by rule with a suggested fix, can be filtered by severity, and copy as Markdown. Bug report captures the screen, app, device, performance, and recent taps. Draw on the image with pen, box, or arrow, fill in title, steps, expected, and actual, then copy or download the report (Markdown plus PNG). Recent failed requests for the app are added. Macros records taps, swipes, and Apple TV remote presses while the step list updates live. Replay runs the steps through UI automation and marks each one passed or failed. Macros can be named, saved in this browser, and exported or imported as JSON. Push schedules a local notification with optional subtitle, badge, sound, image (URL or file, iOS only), up to four actions (text reply, destructive, opens app), thread, and user info. It shows permission state, pending notifications, and the actions the tester tapped. Network tools throttles the selected app (SDK) or the Mac proxy, opens deep links through the system or inside the app, sets a simulated location from presets or coordinates, and shows a request waterfall.

Storage adds defaults add and delete, file rename and delete, file type detection with plist preview, and cookies with masked values.

Device settings changes what the SDK does without a rebuild: debug modules on or off (within the modules the app was built with), network capture on or off, body size limits, and the body content types to keep. The device saves these overrides and keeps them across launches. Reset returns to the app's built-in configuration.

### AppCMS and host pages

Overview, App config, and Environment also show `GoogleService-Info.plist` from the paired app's newest Xcode build: project id, bundle id, sender id, storage bucket, API key, and the Firebase flags. A bundle id that does not match the app is a Diagnostics warning, because Firebase stays off until they match. The file is re-read at most every 30 seconds, and on Refresh. MCP tool results still mask the API key.

`/appcms/*` and `/host/*` refresh themselves while the tab is visible. The interval, the retry cap and the slow refresh for the diagnostics badge come from `/api/config/limits` (`live_refresh_seconds`, `live_backoff_max_seconds`, `live_diagnostics_seconds`; see [configuration](configuration.md)). The page keeps one refresh in flight, skips redraws when nothing changed, and keeps expanded rows open. A change event from the app, a return to the tab, a reconnected network, or the app coming back online refreshes at once.

When a command fails, the last data stays on screen with a yellow notice and a retry countdown. The wait doubles up to the cap and resets after one success. Command errors carry `code`, `phase` (`queued` means the app never took the command, `delivered` means it took it and did not answer) and `retryable`. A missing debug host is not retried as a failure; the page shows what to fix.

### Tools

Two views, switched in the index header. `#tool/<slug>` opens a toolkit tool. `#scratchpad` opens the scratchpad. `/` focuses the toolkit filter. Command-Enter runs the current tool.

The toolkit is 128 local tools in twelve groups: text, data, security, web, HTTP, SQL, time, network, calculators, generators, media, and DevOps. Inputs are saved in `localStorage` under `vl-kit-v1:<slug>`. Secrets and files are not saved. Most tools run in the page. YAML, bcrypt, MD5, SHA-3, keys, DNS, WHOIS, outbound HTTP, and image conversion call `POST /api/tools/action/{name}` on this Mac. There is no IP geolocation database. The JavaScript formatter is a layout pass, not a parser. gitignore templates are a curated set, with the iOS and Xcode template first. The JWT debugger and the Unix time converter (seconds, milliseconds, microseconds, nanoseconds, and the Apple 2001 reference date) are in this set.

The scratchpad is the Boop shape. Paste text, pick a script, and run it. A selection runs on that selection only. Command-Enter runs the selected script. Built-in scripts format JSON, decode JWT and base64, convert query strings and timestamps, and clean lines. New script saves a copy under the data directory. The server stores the file and does not run it. The page runs it in a Web Worker.

### Agent

MCP status, connect snippets, a sample session, the apps on each device, tool groups from `catalog.TOOL_GROUPS`, command tables, guardrails and troubleshooting. The tool catalog itself is [MCP server and tools](mcp-server.md). The Tools page above is the in-browser toolkit and is not part of MCP.

## Assets

| File | Used by |
|---|---|
| `css/app.css` | All pages |
| `css/debug.css` | Debug Suite |
| `css/tools.css` | Tools |
| `css/agent.css` | Agent |
| `css/graphql.css` | GraphQL |
| `js/shell.js` | All pages |
| `js/app.js` | Network, Logs, Setup |
| `js/graphql.js` | GraphQL |
| `js/debug.js` | Debug Suite |
| `js/explorer.js` | Debug Suite Explorer, cookies and Device settings |
| `js/runtime.js` | Debug Suite Runtime browser |
| `js/qa_tools.js` | Debug Suite Accessibility, Bug report, Macros, Push, and Network tools |
| `js/tools.js` | Tools |
| `js/agent.js` | Agent |
| `img/viewlift-logo.png` | Rail |

## Leftovers

`templates/debug.html` is not rendered by any route; `/debug` serves `debug_suite.html`. The matching legacy init code is near the end of `app.js`. Remove both together if you clean up.

## Testing UI changes

`tests/test_network_ui.py`, `tests/test_agent_page.py` and the page tests in `tests/test_logs_api.py` render templates through `TestClient`. Run them after template changes. For visual checks, open the page in a browser with the console running.

## Key files

- [src/viewlift_ai/web/templates](../src/viewlift_ai/web/templates)
- [src/viewlift_ai/web/static/js](../src/viewlift_ai/web/static/js)

Next: [iOS SDK guide](ios-sdk.md)
