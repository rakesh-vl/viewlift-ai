# Web UI

The dashboard is server-rendered Jinja templates with plain JavaScript. There is no build step: edit files in [src/viewlift_ai/web](../src/viewlift_ai/web) and reload.

## Shell

Every page extends [base.html](../src/viewlift_ai/web/templates/base.html):

- left rail: Network, Logs, Rules, Debug, Tools, Agent, Setup, Health, with a live indicator, theme toggle and rail collapse;
- top bar: app scope picker and "Clear all data";
- a warning banner while any mutating rule is enabled.

[shell.js](../src/viewlift_ai/web/static/js/shell.js) runs on every page. It owns the scope picker (`window.VLScope`), sends `/api/session/ping` and `/api/session/leave` for tab presence, handles theme and rail state, and listens to `/ws/debug?client_id=__apps__` to refresh the app list. See [Multi-app and scope](multi-app-and-scope.md#dashboard-scope-picker).

Design guidance lives in [DESIGN.md](../DESIGN.md).

## Pages

| Page | Template | Script entry | Main endpoints |
|---|---|---|---|
| Network `/` | `dashboard.html` | `app.js` `initDashboard` | `/api/flows`, `/api/flows/delete`, `/ws/flows`, `/api/reveal/*`, `/api/export/har`, `/api/flows/{id}/curl`, `/api/breakpoints/*` |
| Flow detail `/flows/{id}` | `flow_detail.html` | server-rendered | `/api/flows/{id}/replay`, `/api/flows/{id}/curl` |
| Logs `/logs` | `logs.html` | `app.js` `initLogs` | `/api/logs/*`, `/ws/logs` |
| Rules `/rules` | `rules.html` | `app.js` `initRules` | `/api/rules/*` |
| Setup `/setup` | `setup.html` | `app.js` `initSetup` | `/api/setup/*`, `/api/apps` |
| Debug `/debug` | `debug_suite.html` | `debug.js` | `/api/debug/*`, `/ws/debug` |
| Tools `/tools` | `tools.html` | `js/toolkit/*.js`, `tools.js` | `/api/tools/scripts`, `/api/tools/action/{name}` |
| Agent `/agent` | `agent.html` | `agent.js` | `/api/agent/status`, `/api/debug/clients` |

### Network

Filter bar (method, host, status, content type, text), HAR export, reveal bar (token, unlock, lock) and an SDK activity hint. A split view shows the live flow table and a detail pane with overview, headers, cookies, bodies (JSON tree, image or text), query, timing waterfall and WebSocket frames. Pending breakpoints appear in the table with a resume or drop editor. Delete flows removes stored requests only. Device logs, debug events, and rules stay.

### Logs

Pick a simulator or device, set process and subsystem filters, start, stop, pause or delete. Paused rows are buffered. Selecting an app in the scope picker auto-targets its simulator and executable. Log detail links to the matching flow by `request_id`.

### Rules

Allowlist, map-local root, and a form and table for each rule kind with enable toggles. See [Rules and breakpoints](rules-and-breakpoints.md).

### Setup

CA status and one-use downloads (PEM and iOS profile), proxy bind (interface, port, LAN with basic auth), iPhone and Apple TV steps, simulator helpers and a test request, the SDK receiver (interface, port, pairing token, receiver URL, pinned fingerprint, app activity, Swift snippet) and the connected apps tree.

### Debug Suite

A session bar for the selected app and tabs: OTT, Analytics, Hangs, Performance, Memory, UI, SwiftUI, Assets, Accessibility, Storage, Flags, Bug report, Macros, Runtime, Push and Network tools (profiles, deep link, location). Read commands go to `/api/debug/command`; mutating ones call `/api/debug/confirm` first. Results stream on `/ws/debug`.

### Tools

Two views, switched in the index header. `#tool/<slug>` opens a toolkit tool. `#scratchpad` opens the scratchpad. `/` focuses the toolkit filter. Command-Enter runs the current tool.

The toolkit is 128 local tools in twelve groups: text, data, security, web, HTTP, SQL, time, network, calculators, generators, media, and DevOps. Inputs are saved in `localStorage` under `vl-kit-v1:<slug>`. Secrets and files are not saved. Most tools run in the page. YAML, bcrypt, MD5, SHA-3, keys, DNS, WHOIS, outbound HTTP, and image conversion call `POST /api/tools/action/{name}` on this Mac. There is no IP geolocation database. The JavaScript formatter is a layout pass, not a parser. gitignore templates are a curated set, with the iOS and Xcode template first. The JWT debugger and the Unix time converter (seconds, milliseconds, microseconds, nanoseconds, and the Apple 2001 reference date) are in this set.

The scratchpad is the Boop shape. Paste text, pick a script, and run it. A selection runs on that selection only. Command-Enter runs the selected script. Built-in scripts format JSON, decode JWT and base64, convert query strings and timestamps, and clean lines. New script saves a copy under the data directory. The server stores the file and does not run it. The page runs it in a Web Worker.

### Agent

MCP status, connect snippets, a sample session, the apps on each device, tool groups from `catalog.TOOL_GROUPS`, command tables, guardrails and troubleshooting.

## Assets

| File | Used by |
|---|---|
| `css/app.css` | All pages |
| `css/debug.css` | Debug Suite |
| `css/tools.css` | Tools |
| `css/agent.css` | Agent |
| `js/shell.js` | All pages |
| `js/app.js` | Network, Logs, Rules, Setup |
| `js/debug.js` | Debug Suite |
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
