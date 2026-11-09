/* AppCMS and generic host pages: sections, diagnostics, confirmed actions, bug bundle, history, and app compare. */
(function () {
  "use strict";

  const root = document.querySelector("[data-appcms-page]");
  if (!root) return;

  const MODE = root.dataset.appcmsMode || "appcms";
  const HOST_MODE = MODE === "host";
  const PAGE = root.dataset.appcmsPage;
  const SECTION = root.dataset.appcmsSection;
  const PAGE_BASE = HOST_MODE ? "/host" : "/appcms";
  const HISTORY_LIMIT = 10;
  const REFRESH_THROTTLE_MS = 1500;

  /* Inputs for actions that take arguments. Anything else runs with an empty payload. */
  const ACTION_FORMS = {
    switch_language: [{ key: "code", label: "Language code", placeholder: "en" }],
    override_flag: [
      { key: "key", label: "Flag key", placeholder: "isDownloadEnabled" },
      { key: "value", label: "Value", type: "bool" },
    ],
    simulate_country: [{ key: "code", label: "Country code", placeholder: "US" }],
    open_page: [{ key: "path", label: "Page path", placeholder: "/" }],
  };

  const ACTION_NOTES = {
    refresh_token: "Refresh the auth token through SessionAuthInterceptor.",
    expire_token: "Move the token expiry into the past to reproduce expiry bugs.",
    refetch_identity: "Fetch the user identity again.",
    refetch_subscription: "Fetch /v3/subscription/user again.",
    refresh_entitlements: "Refresh entitlements in the session store.",
    clear_entitlement_cache: "Clear the quad and vertical player entitlement caches.",
    refetch_bootstrap: "Fetch the bootstrap config from the network, not the cache.",
    clear_page_cache: "Delete PageCached.json and the Core Data page cache.",
    switch_language: "Switch the app language and reload config.",
    override_flag: "In-memory override of one AppConfiguration boolean.",
    reset_flags: "Drop every flag override.",
    force_logout: "Log out with the app's own cleanup path.",
    open_page: "Open a CMS page path inside the app.",
    simulate_country: "Override the current country code for geo and plan testing.",
    reset_simulations: "Drop the simulated country and other simulations.",
  };

  const state = {
    clientId: "",
    sections: [],
    actions: [],
    provider: "",
    manifest: {},
    data: null,
    lastLoad: 0,
    refreshTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const humanize = (key) => String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/^\w/, (char) => char.toUpperCase());
  const isScalar = (value) => value === null || ["string", "number", "boolean"].includes(typeof value);

  function notice(message, kind = "") {
    const bar = $("appcms-notice");
    if (!bar) return;
    bar.textContent = message || "";
    bar.className = `appcms-notice ${kind}`.trim();
    bar.hidden = !message;
  }

  function targetClientId() {
    const scope = window.VLScope;
    if (!scope) return "";
    const selected = scope.clientId();
    if (selected) return selected;
    const online = scope.onlineApps().filter((app) => scope.matches(app.id));
    return online.length === 1 ? online[0].id : "";
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return data;
  }

  async function read(name, payload = {}, clientId = state.clientId) {
    const data = await post("/api/appcms/read", { client_id: clientId, name, payload });
    if (!data.ok) throw new Error(data.error || "The app reported an error");
    const result = data.result || {};
    if (result.error) throw new Error(`${result.error}: ${result.detail || ""}`.trim());
    return result;
  }

  /* ---------- rendering ---------- */

  function flatten(value, prefix = "", out = {}) {
    if (isScalar(value)) {
      out[prefix || "(value)"] = value;
      return out;
    }
    if (Array.isArray(value)) {
      if (!value.length) out[prefix] = "[]";
      value.forEach((item, index) => flatten(item, `${prefix}[${index}]`, out));
      return out;
    }
    const entries = Object.entries(value || {});
    if (!entries.length && prefix) out[prefix] = "{}";
    entries.forEach(([key, item]) => flatten(item, prefix ? `${prefix}.${key}` : key, out));
    return out;
  }

  function valueClass(key, value) {
    const lower = String(key).toLowerCase();
    if (value === true && /(mismatch|stale|drift|conflict|error|expired)/.test(lower)) return "is-bad";
    if (value === false && /(^ok$|_ok$|valid|configured|present)/.test(lower)) return "is-warn";
    return "";
  }

  function kvHtml(data) {
    const entries = Object.entries(data || {}).filter(([, value]) => value !== undefined && value !== "");
    if (!entries.length) return `<p class="muted">empty</p>`;
    return `<dl class="ott-kv">${entries.map(([key, value]) => {
      const text = value === null ? "null" : typeof value === "object" ? JSON.stringify(value) : String(value);
      return `<dt>${escapeHtml(key)}</dt><dd class="${valueClass(key, value)}">${escapeHtml(text)}</dd>`;
    }).join("")}</dl>`;
  }

  function listHtml(items) {
    if (!items.length) return `<p class="muted">empty</p>`;
    if (items.every(isScalar)) return `<div class="mono appcms-list">${items.map((item) => escapeHtml(String(item))).join("<br>")}</div>`;
    return items.map((item, index) => {
      if (isScalar(item)) return `<div class="mono">${escapeHtml(String(item))}</div>`;
      const label = item.name || item.title || item.id || item.path || item.key || `#${index + 1}`;
      return `<details><summary>${escapeHtml(index + 1)}. ${escapeHtml(String(label))}</summary>${cardBody(item)}</details>`;
    }).join("");
  }

  function cardBody(data) {
    if (Array.isArray(data)) return listHtml(data);
    const flat = {};
    const nested = [];
    Object.entries(data || {}).forEach(([key, value]) => {
      if (value && typeof value === "object" && !(Array.isArray(value) && value.every(isScalar))) {
        nested.push([key, value]);
      } else {
        flat[key] = Array.isArray(value) ? value.join(", ") : value;
      }
    });
    const nestedHtml = nested.map(([key, value]) => {
      const label = Array.isArray(value) ? `${humanize(key)} (${value.length})` : humanize(key);
      return `<details><summary>${escapeHtml(label)}</summary>${cardBody(value)}</details>`;
    }).join("");
    return kvHtml(flat) + nestedHtml;
  }

  function card(title, data, key) {
    const count = Array.isArray(data) ? ` (${data.length})` : "";
    return `<div class="ott-card" data-card="${escapeHtml(key)}"><h3>${escapeHtml(title)}${count}</h3>${cardBody(data)}</div>`;
  }

  function renderSection(data) {
    const summary = $("appcms-summary");
    const sections = $("appcms-sections");
    if (!summary || !sections) return;
    const scalars = Object.entries(data || {}).filter(([, value]) => isScalar(value));
    const extra = data && typeof data.summary === "object" && !Array.isArray(data.summary) ? Object.entries(data.summary) : [];
    summary.innerHTML = [...extra, ...scalars]
      .filter(([, value]) => isScalar(value) && value !== "")
      .map(([key, value]) => `<div class="metric"><span class="muted">${escapeHtml(humanize(key))}</span><strong class="metric-text ${valueClass(key, value)}">${escapeHtml(String(value))}</strong></div>`)
      .join("");
    const groups = Object.entries(data || {}).filter(([key, value]) => key !== "summary" && !isScalar(value));
    sections.innerHTML = groups.length
      ? groups.map(([key, value]) => card(humanize(key), value, key)).join("")
      : `<p class="muted">The app returned no groups for this section.</p>`;
    applyFilter();
    renderHistoryDiff();
  }

  function applyFilter() {
    const query = ($("appcms-search")?.value || "").trim().toLowerCase();
    document.querySelectorAll(".appcms .ott-card, .appcms .appcms-finding").forEach((node) => {
      if (!query) {
        node.hidden = false;
        node.querySelectorAll("dt, dd").forEach((cell) => cell.classList.remove("is-hit"));
        return;
      }
      const hit = node.textContent.toLowerCase().includes(query);
      node.hidden = !hit;
      node.querySelectorAll("dt").forEach((term) => {
        const desc = term.nextElementSibling;
        const match = `${term.textContent} ${desc?.textContent || ""}`.toLowerCase().includes(query);
        term.classList.toggle("is-hit", match);
        desc?.classList.toggle("is-hit", match);
      });
      if (hit) node.querySelectorAll("details").forEach((item) => {
        if (item.textContent.toLowerCase().includes(query)) item.open = true;
      });
    });
  }

  /* ---------- history ---------- */

  function historyKey(section = SECTION) {
    return `vl-appcms-hist:${state.clientId}:${section}`;
  }

  function loadHistory(section) {
    try {
      return JSON.parse(sessionStorage.getItem(historyKey(section)) || "[]");
    } catch (_) {
      return [];
    }
  }

  function pushHistory(data) {
    if (!SECTION || !state.clientId) return;
    const items = loadHistory(SECTION);
    const serial = JSON.stringify(data);
    if (items.length && JSON.stringify(items[0].data) === serial) return;
    items.unshift({ at: Date.now(), data });
    try {
      sessionStorage.setItem(historyKey(SECTION), JSON.stringify(items.slice(0, HISTORY_LIMIT)));
    } catch (_) {
      /* Storage full: history is a convenience. */
    }
    renderHistoryOptions();
  }

  function renderHistoryOptions() {
    const select = $("appcms-history-select");
    if (!select) return;
    const items = loadHistory(SECTION);
    const current = select.value;
    select.innerHTML = `<option value="">Pick an earlier snapshot</option>` + items.slice(1)
      .map((item, index) => `<option value="${index + 1}">${escapeHtml(new Date(item.at).toLocaleTimeString())}</option>`)
      .join("");
    if (current) select.value = current;
  }

  function renderHistoryDiff() {
    const box = $("appcms-history");
    const select = $("appcms-history-select");
    document.getElementById("appcms-history-diff")?.remove();
    if (!box || box.hidden || !select?.value || !state.data) return;
    const earlier = loadHistory(SECTION)[Number(select.value)];
    if (!earlier) return;
    const rows = diffRows(flatten(earlier.data), flatten(state.data), true);
    const html = rows.length
      ? diffTable(rows, "Then", "Now")
      : `<p class="muted">No differences.</p>`;
    box.insertAdjacentHTML("afterend", `<div id="appcms-history-diff" class="ott-card appcms-diff"><h3>Changes since ${escapeHtml(new Date(earlier.at).toLocaleTimeString())}</h3>${html}</div>`);
  }

  function diffRows(left, right, onlyDiff) {
    const keys = Array.from(new Set([...Object.keys(left), ...Object.keys(right)])).sort();
    return keys
      .map((key) => ({ key, left: left[key], right: right[key], same: JSON.stringify(left[key]) === JSON.stringify(right[key]) }))
      .filter((row) => !onlyDiff || !row.same);
  }

  function diffTable(rows, leftLabel, rightLabel) {
    const cell = (value) => (value === undefined ? `<span class="muted">missing</span>` : escapeHtml(String(value)));
    return `<table class="appcms-diff-table"><thead><tr><th>Key</th><th>${escapeHtml(leftLabel)}</th><th>${escapeHtml(rightLabel)}</th></tr></thead><tbody>${rows
      .map((row) => `<tr class="${row.same ? "" : "is-diff"}"><td class="mono">${escapeHtml(row.key)}</td><td>${cell(row.left)}</td><td>${cell(row.right)}</td></tr>`)
      .join("")}</tbody></table>`;
  }

  /* ---------- pages ---------- */

  async function loadSectionPage(extraArgs) {
    if (!SECTION) return;
    const payload = { name: SECTION };
    if (extraArgs) payload.args = extraArgs;
    const result = await read("ott.section", payload);
    state.data = result.data || {};
    pushHistory(state.data);
    renderSection(state.data);
    stamp(result.captured_at);
  }

  function stamp(epoch) {
    const label = $("appcms-updated");
    if (!label) return;
    const when = epoch ? new Date(epoch * 1000) : new Date();
    label.textContent = `Captured ${when.toLocaleTimeString()}`;
  }

  async function loadDiagnostics() {
    const result = await read("ott.diagnostics");
    const findings = Array.isArray(result.findings) ? result.findings : [];
    state.data = { findings };
    updateBadge(findings);
    const box = $("appcms-findings");
    if (!box) return;
    const order = { error: 0, warning: 1, info: 2 };
    findings.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
    box.innerHTML = findings.length
      ? findings.map(findingHtml).join("")
      : `<p class="muted">No findings. Session, plan, entitlement, and config sources agree.</p>`;
    stamp(result.captured_at);
    applyFilter();
  }

  function findingHtml(item) {
    const severity = ["error", "warning", "info"].includes(item.severity) ? item.severity : "info";
    const files = Array.isArray(item.files) && item.files.length
      ? `<div class="appcms-files">${item.files.map((file) => `<code>${escapeHtml(file)}</code>`).join(" ")}</div>`
      : "";
    const evidence = item.evidence && typeof item.evidence === "object" ? cardBody(item.evidence) : `<p>${escapeHtml(item.evidence || "")}</p>`;
    return `<article class="appcms-finding is-${severity}">
      <header><span class="appcms-sev">${escapeHtml(severity)}</span><h3>${escapeHtml(item.title || item.id || "Finding")}</h3><code class="muted">${escapeHtml(item.id || "")}</code></header>
      ${evidence}
      ${item.hint ? `<p class="appcms-hint">${escapeHtml(item.hint)}</p>` : ""}
      ${files}
    </article>`;
  }

  function updateBadge(findings) {
    const badge = $("appcms-diag-badge");
    if (!badge) return;
    const serious = findings.filter((item) => item.severity === "error" || item.severity === "warning").length;
    badge.textContent = String(serious);
    badge.hidden = serious === 0;
  }

  function providerSupported() {
    return HOST_MODE ? Boolean(state.provider) && state.provider !== "none" : state.provider === "AppCMS";
  }

  /* Manifest fields ({name, label, kind, options}) win over the built-in AppCMS forms. */
  function actionFields(name) {
    const fields = state.manifest.action_forms?.[name];
    if (!Array.isArray(fields)) return ACTION_FORMS[name] || [];
    return fields.map((field) => ({
      key: field.name,
      label: field.label || field.name,
      type: field.kind === "bool" ? "bool" : field.kind,
      options: Array.isArray(field.options) ? field.options : [],
      placeholder: field.kind === "number" ? "0" : "",
    }));
  }

  function actionNote(name) {
    return state.manifest.action_notes?.[name] || ACTION_NOTES[name] || "";
  }

  function manifestPages() {
    const pages = Array.isArray(state.manifest.pages) ? state.manifest.pages : [];
    if (pages.length) return pages.filter((page) => page && page.name);
    return state.sections.map((name) => ({ name, title: humanize(name), group: "" }));
  }

  function renderHostNav() {
    if (!HOST_MODE) return;
    const box = $("host-nav-pages");
    const pages = manifestPages();
    if (box) {
      let group = "";
      box.innerHTML = pages.map((page) => {
        const heading = page.group && page.group !== group ? `<div class="nav-sub-group muted">${escapeHtml(page.group)}</div>` : "";
        group = page.group || group;
        const active = page.name === PAGE;
        return `${heading}<a href="${PAGE_BASE}/${encodeURIComponent(page.name)}" class="nav-sub-link${active ? " active" : ""}"${active ? ' aria-current="page"' : ""}><span>${escapeHtml(page.title || humanize(page.name))}</span></a>`;
      }).join("");
    }
    const current = pages.find((page) => page.name === PAGE);
    const heading = $("appcms-heading");
    if (current && heading) heading.textContent = current.title || humanize(current.name);
    if (state.manifest.title) document.title = `${state.manifest.title} · Viewlift AI`;
  }

  async function refreshBadge() {
    if (PAGE === "diagnostics" || !state.clientId || !providerSupported()) return;
    try {
      const result = await read("ott.diagnostics");
      updateBadge(Array.isArray(result.findings) ? result.findings : []);
    } catch (_) {
      /* Badge is best effort. */
    }
  }

  function renderActions() {
    const box = $("appcms-actions");
    if (!box) return;
    if (!state.actions.length) {
      box.innerHTML = `<p class="muted">The app lists no actions. Actions exist only in DEBUG builds.</p>`;
      return;
    }
    box.innerHTML = state.actions.map((name) => {
      const fields = actionFields(name).map((field) => {
        const key = escapeHtml(field.key);
        if (field.type === "bool") {
          return `<label>${escapeHtml(field.label)} <select data-field="${key}"><option value="true">true</option><option value="false">false</option></select></label>`;
        }
        if (field.type === "select" && field.options.length) {
          const options = field.options.map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join("");
          return `<label>${escapeHtml(field.label)} <select data-field="${key}">${options}</select></label>`;
        }
        const kind = field.type === "number" ? ' inputmode="decimal" data-kind="number"' : "";
        return `<label>${escapeHtml(field.label)} <input type="text" data-field="${key}"${kind} placeholder="${escapeHtml(field.placeholder || "")}" autocomplete="off"></label>`;
      }).join("");
      const title = state.manifest.action_titles?.[name];
      return `<form class="appcms-action" data-action="${escapeHtml(name)}">
        <div><strong class="mono">${escapeHtml(title || name)}</strong><p class="muted">${escapeHtml(actionNote(name))}</p></div>
        <div class="appcms-action-fields">${fields}<button type="submit" class="btn danger">Run</button></div>
      </form>`;
    }).join("");
  }

  function actionPayload(form) {
    const payload = {};
    form.querySelectorAll("[data-field]").forEach((input) => {
      const raw = input.value.trim();
      if (!raw) return;
      if (input.dataset.kind === "number" && !Number.isNaN(Number(raw))) {
        payload[input.dataset.field] = Number(raw);
        return;
      }
      payload[input.dataset.field] = raw === "true" ? true : raw === "false" ? false : raw;
    });
    return payload;
  }

  function confirmDialog(message) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "debug-confirm";
      overlay.setAttribute("role", "alertdialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.innerHTML = `<div class="debug-confirm-box"><p></p><div class="debug-confirm-actions"><button type="button" class="btn ghost" data-answer="no">Cancel</button><button type="button" class="btn" data-answer="yes">Run</button></div></div>`;
      overlay.querySelector("p").textContent = message;
      const finish = (value) => {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        resolve(value);
      };
      const onKey = (event) => {
        if (event.key === "Escape") finish(false);
      };
      overlay.addEventListener("click", (event) => {
        const answer = event.target.closest("[data-answer]")?.dataset.answer;
        if (answer) finish(answer === "yes");
      });
      document.addEventListener("keydown", onKey, true);
      document.body.append(overlay);
      overlay.querySelector('[data-answer="no"]').focus();
    });
  }

  async function runAction(name, payload) {
    const app = window.VLScope?.app(state.clientId);
    const target = app?.app_name || "the app";
    const args = Object.keys(payload).length ? ` with ${JSON.stringify(payload)}` : "";
    if (!(await confirmDialog(`Run ${name} on ${target}${args}? This changes the running app.`))) return;
    const token = await post("/api/debug/confirm", { client_id: state.clientId, command: "ott.action" });
    const response = await post("/api/appcms/action", {
      client_id: state.clientId,
      action: name,
      payload,
      confirmation: token.token,
    });
    const box = $("appcms-action-result");
    const result = response.result || { error: response.error };
    if (box) box.innerHTML = card(name, result, name);
    if (result.error) notice(`${name} failed: ${result.detail || result.error}`, "is-error");
    else notice(`${name} finished.`, "is-ok");
  }

  /* ---------- compare ---------- */

  function renderCompareChoices() {
    const scope = window.VLScope;
    const left = $("appcms-left");
    const right = $("appcms-right");
    const section = $("appcms-compare-section");
    if (!scope || !left || !right || !section) return;
    const apps = scope.onlineApps();
    const options = apps.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(`${app.app_name} · ${app.platform || ""} ${app.device_name || ""}`.trim())}</option>`).join("");
    const keepLeft = left.value || state.clientId;
    const keepRight = right.value;
    left.innerHTML = options;
    right.innerHTML = options;
    if (keepLeft) left.value = keepLeft;
    right.value = keepRight || (apps.find((app) => app.id !== left.value)?.id || "");
    const keepSection = section.value;
    section.innerHTML = state.sections.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(humanize(name))}</option>`).join("");
    if (keepSection) section.value = keepSection;
  }

  async function loadCompare() {
    renderCompareChoices();
    const left = $("appcms-left")?.value;
    const right = $("appcms-right")?.value;
    const section = $("appcms-compare-section")?.value;
    const box = $("appcms-compare");
    if (!box) return;
    if (!left || !right || !section || left === right) {
      box.innerHTML = `<p class="muted">Pick two different online apps with the same debug host, for example iOS and tvOS, or a prod and a uat build.</p>`;
      return;
    }
    const [one, two] = await Promise.all([
      read("ott.section", { name: section }, left),
      read("ott.section", { name: section }, right),
    ]);
    const rows = diffRows(flatten(one.data || {}), flatten(two.data || {}), $("appcms-diff-only")?.checked);
    const name = (id) => window.VLScope?.app(id)?.app_name || id;
    state.data = { left: one.data, right: two.data };
    box.innerHTML = rows.length ? diffTable(rows, name(left), name(right)) : `<p class="muted">Both apps report the same ${escapeHtml(section)} values.</p>`;
  }

  /* ---------- bug bundle ---------- */

  async function downloadBundle() {
    if (!state.clientId) {
      notice("Choose one app in Scope first.", "is-error");
      return;
    }
    notice("Collecting sections, diagnostics, and recent network calls…");
    const bundle = { generated_at: new Date().toISOString(), client_id: state.clientId, provider: state.provider, sections: {} };
    const tasks = state.sections.map(async (name) => {
      try {
        bundle.sections[name] = (await read("ott.section", { name })).data;
      } catch (error) {
        bundle.sections[name] = { error: String(error.message || error) };
      }
    });
    tasks.push(read("ott.diagnostics").then((value) => { bundle.diagnostics = value.findings; }).catch((error) => { bundle.diagnostics = { error: String(error.message || error) }; }));
    tasks.push(fetch(`/api/debug/app?client_id=${encodeURIComponent(state.clientId)}`).then((response) => response.json()).then((value) => { bundle.app = value.client; }).catch(() => {}));
    tasks.push(fetch(`/api/flows?client_id=${encodeURIComponent(state.clientId)}&limit=50`).then((response) => response.json()).then((value) => { bundle.network = value.flows; }).catch(() => {}));
    await Promise.all(tasks);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    const app = window.VLScope?.app(state.clientId);
    const stampText = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = URL.createObjectURL(blob);
    link.download = `${HOST_MODE ? "host" : "appcms"}-bug-${(app?.app_name || "app").replace(/\W+/g, "-")}-${stampText}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    notice("Bug bundle downloaded. Tokens and PINs are masked on the device and again on this Mac.", "is-ok");
  }

  /* ---------- lifecycle ---------- */

  function renderClient() {
    const app = window.VLScope?.app(state.clientId);
    $("appcms-client").textContent = app ? `${app.app_name} · ${app.platform || ""} ${app.app_version || ""}`.trim() : "No app selected";
    const pill = $("appcms-provider");
    pill.textContent = state.provider || "Waiting";
    pill.className = `memory-pill ${providerSupported() ? "is-live" : state.provider ? "is-waiting" : "is-offline"}`;
  }

  async function load() {
    state.clientId = targetClientId();
    renderClient();
    if (!state.clientId) {
      notice(`Choose one ${HOST_MODE ? "" : "AppCMS "}app in Scope. Several apps may be running on this device.`);
      return;
    }
    try {
      const sections = await read("ott.sections");
      state.sections = Array.isArray(sections.sections) ? sections.sections : [];
      state.actions = Array.isArray(sections.actions) ? sections.actions : [];
      state.provider = sections.provider || "none";
      state.manifest = sections;
      renderClient();
      renderHostNav();
      if (!providerSupported()) {
        notice(HOST_MODE
          ? "This app registered no debug host. Run a DEBUG build that links ViewliftNetworkCapture and registers OTTDebugHost."
          : "This app has no AppCMS debug provider. Run an AppCMS DEBUG build with ViewliftNetworkCapture.", "is-error");
        return;
      }
      if (HOST_MODE && !PAGE) {
        const first = manifestPages()[0];
        if (first) {
          location.replace(`${PAGE_BASE}/${encodeURIComponent(first.name)}`);
          return;
        }
      }
      notice("");
      state.lastLoad = Date.now();
      if (PAGE === "diagnostics") await loadDiagnostics();
      else if (PAGE === "actions") renderActions();
      else if (PAGE === "compare") await loadCompare();
      else await loadSectionPage();
      refreshBadge();
    } catch (error) {
      notice(String(error.message || error), "is-error");
    }
  }

  function scheduleRefresh(changed) {
    if (PAGE === "actions" || PAGE === "compare") return;
    if (SECTION && Array.isArray(changed) && changed.length && !changed.includes(SECTION) && !changed.includes("*")) {
      refreshBadge();
      return;
    }
    window.clearTimeout(state.refreshTimer);
    const wait = Math.max(0, REFRESH_THROTTLE_MS - (Date.now() - state.lastLoad));
    state.refreshTimer = window.setTimeout(load, wait);
  }

  function connectSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${location.host}/ws/debug`);
    socket.addEventListener("message", (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      const item = data.event;
      if (data.type !== "debug-event" || !item || item.client_id !== state.clientId) return;
      if (item.module === "ott" && item.kind === "changed") scheduleRefresh(item.payload?.sections);
    });
    socket.addEventListener("close", () => setTimeout(connectSocket, 1500));
  }

  function bind() {
    $("appcms-refresh")?.addEventListener("click", load);
    $("appcms-search")?.addEventListener("input", applyFilter);
    $("appcms-copy")?.addEventListener("click", async () => {
      const text = JSON.stringify(state.data || {}, null, 2);
      try {
        await navigator.clipboard.writeText(text);
        notice("Copied as JSON.", "is-ok");
      } catch (_) {
        notice("Clipboard is blocked. Use Download bug bundle instead.", "is-error");
      }
    });
    $("appcms-bundle")?.addEventListener("click", () => downloadBundle().catch((error) => notice(String(error.message || error), "is-error")));
    $("appcms-history-toggle")?.addEventListener("click", (event) => {
      const box = $("appcms-history");
      box.hidden = !box.hidden;
      event.currentTarget.setAttribute("aria-pressed", String(!box.hidden));
      renderHistoryOptions();
      renderHistoryDiff();
    });
    $("appcms-history-select")?.addEventListener("change", renderHistoryDiff);
    $("appcms-actions")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.target.closest("[data-action]");
      if (!form) return;
      runAction(form.dataset.action, actionPayload(form)).catch((error) => notice(String(error.message || error), "is-error"));
    });
    $("appcms-paywall")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const id = $("appcms-paywall-id").value.trim();
      loadSectionPage(id ? { content_id: id } : undefined).catch((error) => notice(String(error.message || error), "is-error"));
    });
    ["appcms-left", "appcms-right", "appcms-compare-section", "appcms-diff-only"].forEach((id) => {
      $(id)?.addEventListener("change", () => loadCompare().catch((error) => notice(String(error.message || error), "is-error")));
    });
    document.addEventListener("vl:scope-change", load);
    document.addEventListener("vl:apps-change", () => {
      if (targetClientId() !== state.clientId || !state.provider) load();
      if (PAGE === "compare") renderCompareChoices();
    });
  }

  bind();
  connectSocket();
  load();
})();
