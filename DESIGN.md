# Viewlift AI console design

The console is a local debug instrument for iOS and tvOS engineers. It should feel like bench test equipment: dense, calm, and precise. It should not feel like a marketing site.

## Brief

- Subject: a local Mac console for proxy traffic, device logs, rules, and SDK debug capture.
- Audience: app engineers at a desk with a simulator or phone beside them, scanning traffic for long sessions.
- Primary job: find the failing request quickly and inspect it.
- Content: real flows, logs, and device data only. No placeholder copy.

## Tokens

Tokens live once in `src/viewlift_ai/web/static/css/app.css`. Components use tokens only. `debug.css` uses the same tokens.

| Token | Dark | Light | Role |
|---|---|---|---|
| `--bg` | `#15171a` | `#eceef0` | Page base |
| `--surface` | `#1b1e22` | `#f8f9fa` | Panels, filter bars, detail pane |
| `--sunken` | `#101214` | `#e3e6e9` | Rail, inputs, tracks |
| `--line` | `#2e3338` | `#d3d8dd` | Structural borders |
| `--text` | `#e8e4dc` | `#1a1d21` | Body text |
| `--muted` | `#9b978f` | `#5d636b` | Secondary text (AA on surface) |
| `--accent` | `#f2a93b` | `#9a5200` | The only action color: primary buttons, active nav, focus, selection |
| `--ok` / `--warn` / `--err` | `#6fcf97` / `#e9c46a` / `#f07a6e` | `#1d7a46` / `#8a6100` / `#b3261e` | Status only |
| `--scope` | `#0b0d0f` | `#0b0d0f` | Canvas charts stay dark in both themes |

Brand: the official ViewLift logo (`static/img/viewlift-logo.png`, white on transparent) sits at the top of the rail. Light theme inverts it with `filter: invert(1)`. The collapsed rail shows only the top-left bracket corner (34px). Do not recolor the logo any other way.

HTTP methods have their own data colors: `--m-get`, `--m-post`, `--m-put`, `--m-patch`, and `--m-delete`.

Theme follows `prefers-color-scheme`. The rail toggle overrides it and saves the choice in `localStorage` (`vl-theme`).

## Type

- Mono (`ui-monospace`, SF Mono) is the display and data voice: brand, page titles, section titles, tables, values.
- Sans (system UI) is for prose, labels, and buttons.
- No web fonts. The tool must work offline and must not call external hosts.
- Scale: 11, 12, 13 (body), 15, 18, 24 (page titles, fluid with `clamp()`).
- Numbers in tables use `tabular-nums`.

## Shape and depth

- Radius: 3px for controls, 6px for cards and buttons, 10px for debug panels.
- Depth comes from tonal steps (`--sunken`, `--bg`, `--surface`, `--raised`) and 1px lines. No drop shadows, except the focus ring.

## Layout rules

- Shell: a left rail (196px, collapsible to 56px, saved as `vl-rail`) and a stage. Below 720px the rail becomes a top bar with icons only.
- Network and logs: a split view with a sticky table header. Rows show a 2px left edge for 3xx (warn) and 4xx/5xx (error). The selected row gets a 3px accent edge.
- Rules and setup: a spec-sheet layout. The section title and explanation sit in the left column, and controls sit on the right. The layout collapses to one column below 1100px.
- Setup steps use a numbered vertical track because they are a real sequence.
- Debug suite: sticky underline tabs, a metric strip with hairline dividers, and dark scope canvases.

## Motion

- The only ambient animation is the live connection ping on the rail status dot.
- Interactions use 160ms ease-out on color, opacity, and transform.
- `prefers-reduced-motion` disables animation.

## Voice

- Sentence case everywhere. Buttons say what happens ("Apply filters", "Refresh devices").
- Empty states say what to do next ("Select a request to inspect its headers, body, and timing.").
- Warnings state the consequence and link to the fix (the mutating banner links to Rules).

## Constraints

- Element IDs, `data-*` hooks, and class names used by `app.js` and `debug.js` are a contract. Restyle them, but do not rename them.
- Fixed table layout takes column widths from the `<th>` row, so width classes go on header cells.
