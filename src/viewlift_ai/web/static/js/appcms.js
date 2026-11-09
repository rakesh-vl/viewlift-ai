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
  /* Used only while the console cannot serve /api/config/limits. Every normal interval comes from the server. */
  const LIMITS_RETRY_MS = 3000;

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

  /* Section names come from the server page list, so a new page needs no change here. */
  const SNAPSHOT_SECTIONS = (root.dataset.appcmsSections || "").split(",").filter(Boolean);

  const state = {
    clientId: "",
    sections: [],
    actions: [],
    provider: "",
    manifest: {},
    manifestAt: 0,
    data: null,
    sectionArgs: null,
  };

  /* Live refresh bookkeeping. */
  const live = {
    timer: 0,
    failures: 0,
    running: null,
    queued: false,
    renderedKey: "",
    badgeAt: 0,
    retryNote: "",
  };

  const snapshotCache = new Map();

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

  /* A failed command. `retryable` tells the live loop to keep going; otherwise it waits for the user. */
  class CommandError extends Error {
    constructor(message, { status = 0, code = "", phase = "", retryable = false } = {}) {
      super(message);
      this.name = "CommandError";
      this.status = status;
      this.code = code;
      this.phase = phase;
      this.retryable = retryable;
    }
  }

  const limitSeconds = (key) => {
    const value = Number(window.ViewliftLimits?.get?.(key));
    return Number.isFinite(value) && value > 0 ? value : 0;
  };

  function hasLiveLimits() {
    return Boolean(limitSeconds("live_refresh_seconds") && limitSeconds("live_backoff_max_seconds"));
  }

  /* Waits for the first limits response, then continues. A missing key must not freeze the page. */
  async function limitsReady() {
    await window.ViewliftLimits?.ready;
  }

  async function post(path, body) {
    let response;
    try {
      response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_) {
      throw new CommandError("The console is not reachable.", { code: "console_unreachable", retryable: true });
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail && typeof data.detail === "object" ? data.detail : { message: data.detail };
      throw new CommandError(detail.message || `Request failed (${response.status})`, {
        status: response.status,
        code: detail.code || "",
        phase: detail.phase || "",
        retryable: typeof detail.retryable === "boolean" ? detail.retryable : response.status >= 500,
      });
    }
    return data;
  }

  async function readRemote(name, payload = {}, clientId = state.clientId) {
    const data = await post("/api/appcms/read", { client_id: clientId, name, payload });
    if (!data.ok) throw new CommandError(data.error || "The app reported an error", { retryable: true });
    const result = data.result || {};
    if (result.error) throw new CommandError(`${result.error}: ${result.detail || ""}`.trim(), { code: result.error });
    return result;
  }

  function providerOk(provider) {
    return HOST_MODE ? Boolean(provider) && provider !== "none" : provider === "AppCMS";
  }

  async function freshSnapshot(clientId) {
    const hit = snapshotCache.get(clientId);
    if (hit) return hit.data;
    const data = await readRemote("ott.snapshot", {}, clientId);
    snapshotCache.set(clientId, { data });
    return data;
  }

  function pickGroups(snap, names) {
    const out = {};
    names.forEach((name) => {
      if (snap[name] !== undefined) out[name] = snap[name];
    });
    return out;
  }

  function textOf(object, key) {
    const value = object?.[key];
    return value === undefined || value === null ? "" : value;
  }

  function paywallFromCache(snap, contentId) {
    const sample = snap.entitlement_cache?.sample;
    const row = Array.isArray(sample) ? sample.find((item) => item && item.content_id === contentId) : null;
    if (!row) return { content_id: contentId, cached: false, note: "No row in the entitlement cache sample" };
    return { content_id: contentId, cached: true, ...row };
  }

  function sectionFromSnapshot(snap, name, args) {
    const config = snap.config || {};
    const user = snap.user || {};
    const flow = snap.flow || {};
    const subscription = snap.subscription || {};
    const device = snap.device || {};
    const entitlements = snap.entitlements_v2 || {};
    const session = snap.session || {};
    const tve = snap.tve || {};
    const navigation = snap.navigation || {};
    const analytics = snap.analytics || {};
    const player = snap.player || {};
    switch (name) {
      case "overview":
        return {
          summary: {
            flow: textOf(flow, "name"),
            logged_in: user.logged_in === true,
            subscribed: subscription.is_user_subscribed_effective === true,
            site: textOf(config, "site_name"),
            environment: textOf(config, "environment"),
            platform: snap.platform || textOf(device, "platform"),
          },
          ...pickGroups(snap, ["flow", "user", "geo", "session", "device", "google_service_file"]),
        };
      case "config":
        return {
          summary: {
            site: textOf(config, "site_name"),
            site_id: textOf(config, "site_id"),
            environment: textOf(config, "environment"),
            api_base_url: textOf(config, "api_base_url"),
          },
          ...pickGroups(snap, ["config", "site_features", "theme", "content_filter", "google_service_file"]),
        };
      case "environment":
        return {
          summary: {
            environment: textOf(config, "environment"),
            api_base_url: textOf(config, "api_base_url"),
            site_id: textOf(config, "site_id"),
            domain: textOf(config, "domain_name"),
            bundle_id: textOf(device, "bundle_id") || textOf(snap.bundle, "bundle_id"),
          },
          ...pickGroups(snap, ["config", "device", "bundle", "google_service_file"]),
          frameworks: snap.frameworks || [],
        };
      case "pages":
        return {
          summary: {
            top_vc: textOf(navigation, "top_vc"),
            page_path: textOf(navigation, "ud_page_path"),
            page_id: textOf(navigation, "ud_page_id"),
          },
          ...pickGroups(snap, ["navigation", "navigation_catalog", "page"]),
        };
      case "session":
        return {
          summary: {
            logged_in: user.logged_in === true,
            user_id: textOf(user, "user_id"),
            auth_token_present: session.auth_token_present === true,
            login_provider: textOf(user, "login_provider"),
          },
          ...pickGroups(snap, ["session", "user", "profiles", "parental", "personalization", "sso"]),
        };
      case "subscription":
        return {
          summary: {
            subscribed: subscription.is_user_subscribed_effective === true,
            plan_id: textOf(subscription, "plan_id"),
            status: textOf(subscription, "subscription_status"),
            processor: textOf(subscription, "payment_processor"),
          },
          ...pickGroups(snap, ["subscription", "plan_features", "user_lists"]),
        };
      case "entitlements": {
        const body = {
          summary: {
            subscribed: entitlements.is_subscribed === true,
            svod_plan_id: textOf(entitlements, "svod_plan_id"),
            has_svod: entitlements.has_svod === true,
            purchased: entitlements.purchased_content_count ?? 0,
          },
          ...pickGroups(snap, ["entitlements_v2", "entitlement_cache"]),
        };
        const contentId = String(args?.content_id || "").trim();
        if (contentId) body.paywall = paywallFromCache(snap, contentId);
        return body;
      }
      case "tve":
        return {
          summary: {
            signed_in: tve.signed_in_via_tve === true,
            provider: textOf(tve, "mvpd_provider") || textOf(tve, "selected_provider_title"),
            only_tve_flow: tve.only_tve_flow === true,
          },
          tve,
        };
      case "devices":
        return {
          summary: {
            registered: snap.devices?.count ?? 0,
            model: textOf(device, "model"),
            platform: textOf(device, "platform"),
          },
          ...pickGroups(snap, ["devices", "device"]),
        };
      case "analytics":
        return {
          summary: {
            configured: analytics.configured === true,
            clients: Array.isArray(analytics.registered_clients) ? analytics.registered_clients.length : 0,
          },
          analytics,
        };
      case "player":
        return {
          summary: {
            visible: player.visible === true,
            title: textOf(player, "title"),
            content_id: textOf(player, "content_id"),
            is_live: player.is_live === true,
          },
          ...pickGroups(snap, ["player", "player_prefs", "drm"]),
        };
      default:
        return snap[name] && typeof snap[name] === "object" ? snap[name] : {};
    }
  }

  function findingsFromSnapshot(snap) {
    const findings = [];
    const config = snap.config || {};
    const user = snap.user || {};
    const flow = snap.flow || {};
    const session = snap.session || {};
    const subscription = snap.subscription || {};
    const entitlements = snap.entitlements_v2 || {};
    const tve = snap.tve || {};
    const player = snap.player || {};
    const entitlement = player.entitlement || {};
    if (!config.site_id) {
      findings.push({
        id: "config.site_id",
        severity: "error",
        title: "Bootstrap config has no site id",
        evidence: { site_name: config.site_name || "" },
        hint: "Open AppConfiguration siteId. Bootstrap may not have finished.",
        files: ["AppConfiguration"],
      });
    }
    if (!config.api_base_url) {
      findings.push({
        id: "config.api_base_url",
        severity: "error",
        title: "API base URL is empty",
        evidence: { environment: config.environment || "" },
        hint: "Check AppConfiguration.apiBaseUrl after bootstrap.",
        files: ["AppConfiguration"],
      });
    }
    const loggedIn = user.logged_in === true || flow.is_logged_in === true;
    if (loggedIn && session.auth_token_present !== true) {
      findings.push({
        id: "session.token_missing",
        severity: "error",
        title: "User is logged in but the auth token is absent",
        evidence: { logged_in: true, auth_token_present: false },
        hint: "SessionAuthInterceptor and the stored auth token.",
        files: ["SessionAuthInterceptor.swift"],
      });
    }
    if (typeof user.logged_in === "boolean" && typeof flow.is_logged_in === "boolean" && user.logged_in !== flow.is_logged_in) {
      findings.push({
        id: "session.login_mismatch",
        severity: "warning",
        title: "Login flags disagree",
        evidence: { user_logged_in: user.logged_in, flow_logged_in: flow.is_logged_in },
        hint: "ParentalControlHelper user versus the flow section.",
        files: ["ParentalControlHelper"],
      });
    }
    if (typeof subscription.ud_is_subscribed === "boolean" && subscription.is_subscribed !== subscription.ud_is_subscribed) {
      findings.push({
        id: "subscription.flag_drift",
        severity: "warning",
        title: "Subscription flag and UserDefaults disagree",
        evidence: { is_subscribed: subscription.is_subscribed, ud_is_subscribed: subscription.ud_is_subscribed },
        hint: "User isSubscribed versus UserDefaults isSubscribed.",
        files: ["ParentalControlHelper"],
      });
    }
    if (typeof subscription.is_user_subscribed_effective === "boolean" && typeof entitlements.is_subscribed === "boolean" && subscription.is_user_subscribed_effective !== entitlements.is_subscribed) {
      findings.push({
        id: "entitlements.drift",
        severity: "warning",
        title: "Plan subscription and session entitlements disagree",
        evidence: { effective: subscription.is_user_subscribed_effective, entitlements: entitlements.is_subscribed },
        hint: "Compare the user plan with UserSessionStore entitlements.",
        files: ["UserSessionStoreObservable"],
      });
    }
    if (tve.signed_in_via_tve === true && !tve.mvpd_provider) {
      findings.push({
        id: "tve.provider_missing",
        severity: "warning",
        title: "TVE session has no MVPD provider",
        evidence: { selected_provider_title: tve.selected_provider_title || "" },
        hint: "AdobePrimeTime selected provider versus the user mvpdProvider.",
        files: ["AdobePrimeTimeServiceV2"],
      });
    }
    if (player.visible === true && entitlement.present === true && entitlement.playable === false) {
      findings.push({
        id: "player.not_playable",
        severity: "warning",
        title: "Visible player entitlement is not playable",
        evidence: {
          content_id: player.content_id || "",
          error_code: entitlement.error_code || "",
          vl_error_code: entitlement.vl_error_code || "",
        },
        hint: "Entitlement error on the current VLPlayerMFEController.",
        files: ["VLPlayerMFEController"],
      });
    }
    return findings;
  }

  async function read(name, payload = {}, clientId = state.clientId) {
    try {
      const result = await readRemote(name, payload, clientId);
      if (name === "ott.sections" && !providerOk(result.provider)) {
        const snap = await freshSnapshot(clientId);
        if (providerOk(snap.provider)) {
          return {
            provider: snap.provider,
            sections: SNAPSHOT_SECTIONS,
            actions: Array.isArray(result.actions) ? result.actions : [],
          };
        }
      }
      return result;
    } catch (error) {
      const missingHandler = /no_handler/.test(String(error.message || error));
      if (!missingHandler || (name !== "ott.section" && name !== "ott.diagnostics")) throw error;
      const snap = await freshSnapshot(clientId);
      if (!providerOk(snap.provider)) throw error;
      if (name === "ott.diagnostics") return { findings: findingsFromSnapshot(snap), captured_at: snap.captured_at };
      return {
        section: payload.name,
        data: sectionFromSnapshot(snap, payload.name, payload.args),
        captured_at: snap.captured_at,
      };
    }
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
    const open = rememberOpen(sections);
    sections.innerHTML = groups.length
      ? groups.map(([key, value]) => card(humanize(key), value, key)).join("")
      : `<p class="muted">The app returned no groups for this section.</p>`;
    restoreOpen(sections, open);
    applyFilter();
    renderHistoryDiff();
  }

  /* Live re-renders keep expanded rows open, so reading is never interrupted. */
  function detailKey(node, container) {
    const parts = [];
    for (let el = node; el && el !== container; el = el.parentElement) {
      if (el.tagName === "DETAILS") parts.unshift(el.querySelector(":scope > summary")?.textContent.trim() || "");
      else if (el.dataset?.card) parts.unshift(`card:${el.dataset.card}`);
    }
    return parts.join(" > ");
  }

  function rememberOpen(container) {
    return new Set([...container.querySelectorAll("details[open]")].map((node) => detailKey(node, container)));
  }

  function restoreOpen(container, keys) {
    if (!keys.size) return;
    container.querySelectorAll("details").forEach((node) => {
      if (keys.has(detailKey(node, container))) node.open = true;
    });
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

  /* True when the payload differs from what is on screen. Identical live polls then touch no DOM. */
  function isNewView(payload) {
    const key = JSON.stringify(payload);
    if (key === live.renderedKey) return false;
    live.renderedKey = key;
    return true;
  }

  function changeCount(before, after) {
    if (!before) return 0;
    const left = flatten(before);
    const right = flatten(after);
    const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
    return [...keys].filter((key) => left[key] !== right[key]).length;
  }

  const GOOGLE_SECTION_PAGES = new Set(["overview", "config", "environment"]);
  const googleCache = { clientId: "", at: 0, value: null };

  async function fetchGoogleService(force = false) {
    const now = Date.now();
    if (!force && googleCache.clientId === state.clientId && googleCache.value && now - googleCache.at < 30000) {
      return googleCache.value;
    }
    const params = new URLSearchParams();
    if (state.clientId) params.set("client_id", state.clientId);
    let value;
    try {
      const data = await (await fetch(`/api/debug/packages?${params}`)).json();
      value = data.google_service_file || {
        present: false,
        file: "GoogleService-Info.plist",
        note: data.error || "GoogleService-Info.plist was not found in the built app.",
      };
    } catch (error) {
      value = {
        present: false,
        file: "GoogleService-Info.plist",
        note: String(error.message || error),
      };
    }
    googleCache.clientId = state.clientId;
    googleCache.at = Date.now();
    googleCache.value = value;
    return value;
  }

  function withGoogleService(data, info) {
    if (!GOOGLE_SECTION_PAGES.has(SECTION)) return data;
    const fromDevice = data.google_service_file;
    const chosen = info && info.present ? info : (fromDevice || info);
    if (!chosen) return data;
    const next = { ...data, google_service_file: chosen };
    if (next.summary && typeof next.summary === "object" && !Array.isArray(next.summary)) {
      const summary = { ...next.summary };
      summary.google_service = chosen.present ? (chosen.project_id || "present") : "missing";
      if (chosen.bundle_id_mismatch) summary.google_bundle_mismatch = true;
      next.summary = summary;
    }
    return next;
  }

  function googleFinding(info) {
    if (!info || info.bundle_id_mismatch !== true) return null;
    return {
      id: "config.google_service_bundle",
      severity: "warning",
      title: "GoogleService-Info.plist bundle id does not match the app",
      evidence: {
        plist_bundle_id: info.bundle_id || "",
        app_bundle_id: info.app_bundle_id || "",
      },
      hint: "Firebase stays off until BUNDLE_ID matches the app. Download a plist for this bundle id from the Firebase console.",
      files: ["GoogleService-Info.plist"],
    };
  }

  async function loadSectionPage(extraArgs) {
    if (!SECTION) return;
    if (extraArgs !== undefined) state.sectionArgs = extraArgs;
    const payload = { name: SECTION };
    if (state.sectionArgs) payload.args = state.sectionArgs;
    const [result, google] = await Promise.all([
      read("ott.section", payload),
      GOOGLE_SECTION_PAGES.has(SECTION) ? fetchGoogleService() : Promise.resolve(null),
    ]);
    const data = withGoogleService(result.data || {}, google);
    if (!isNewView(data)) {
      stamp(result.captured_at);
      return;
    }
    const changed = changeCount(state.data, data);
    state.data = data;
    pushHistory(data);
    renderSection(data);
    stamp(result.captured_at, changed ? `${changed} value${changed === 1 ? "" : "s"} changed` : "");
  }

  function stamp(epoch, note = "") {
    const label = $("appcms-updated");
    if (!label) return;
    const when = epoch ? new Date(epoch * 1000) : new Date();
    label.textContent = `Captured ${when.toLocaleTimeString()}${note ? ` · ${note}` : ""}`;
  }

  async function loadDiagnostics() {
    const [result, google] = await Promise.all([read("ott.diagnostics"), fetchGoogleService()]);
    const findings = Array.isArray(result.findings) ? result.findings.slice() : [];
    const extra = googleFinding(google);
    if (extra && !findings.some((item) => item.id === extra.id)) findings.push(extra);
    updateBadge(findings);
    live.badgeAt = Date.now();
    const box = $("appcms-findings");
    if (!box) return;
    const order = { error: 0, warning: 1, info: 2 };
    findings.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
    if (!isNewView(findings)) {
      stamp(result.captured_at);
      return;
    }
    state.data = { findings };
    const open = rememberOpen(box);
    box.innerHTML = findings.length
      ? findings.map(findingHtml).join("")
      : `<p class="muted">No findings. Session, plan, entitlement, and config sources agree.</p>`;
    restoreOpen(box, open);
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
    return providerOk(state.provider);
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
    if (Date.now() - live.badgeAt < limitSeconds("live_diagnostics_seconds") * 1000) return;
    live.badgeAt = Date.now();
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
    const sectionOptions = state.sections.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(humanize(name))}</option>`).join("");
    const signature = `${options}|${sectionOptions}`;
    if (left.dataset.signature === signature) return;
    left.dataset.signature = signature;
    const keepLeft = left.value || state.clientId;
    const keepRight = right.value;
    left.innerHTML = options;
    right.innerHTML = options;
    if (keepLeft) left.value = keepLeft;
    right.value = keepRight || (apps.find((app) => app.id !== left.value)?.id || "");
    const keepSection = section.value;
    section.innerHTML = sectionOptions;
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
      live.renderedKey = "";
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
    const html = rows.length ? diffTable(rows, name(left), name(right)) : `<p class="muted">Both apps report the same ${escapeHtml(section)} values.</p>`;
    if (isNewView(html)) box.innerHTML = html;
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
    tasks.push(fetchGoogleService(true).then((value) => { bundle.google_service_file = value; }).catch(() => {}));
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
    const reconnecting = live.failures > 0 && Boolean(state.clientId);
    pill.textContent = reconnecting ? "Reconnecting" : providerSupported() ? `${state.provider} · live` : state.provider || "Waiting";
    pill.className = `memory-pill ${reconnecting ? "is-waiting" : providerSupported() ? "is-live" : state.provider ? "is-waiting" : "is-offline"}`;
  }

  /* ---------- live refresh ---------- */

  /* Delay before the next poll: the server interval, doubled per consecutive failure up to the server cap. */
  function liveDelayMs() {
    if (!hasLiveLimits()) return LIMITS_RETRY_MS;
    const base = limitSeconds("live_refresh_seconds") * 1000;
    const cap = limitSeconds("live_backoff_max_seconds") * 1000;
    return Math.min(cap, base * 2 ** Math.min(live.failures, 16));
  }

  function scheduleLive() {
    window.clearTimeout(live.timer);
    if (document.hidden || !state.clientId) return;
    live.timer = window.setTimeout(() => {
      if (!hasLiveLimits()) window.ViewliftLimits?.reload?.();
      load();
    }, liveDelayMs());
  }

  /* Starts a refresh now. Calls made while one is running collapse into one follow-up. */
  function load() {
    window.clearTimeout(live.timer);
    if (live.running) {
      live.queued = true;
      return live.running;
    }
    live.running = runLoad().finally(() => {
      live.running = null;
      if (live.queued) {
        live.queued = false;
        load();
      } else {
        scheduleLive();
      }
    });
    return live.running;
  }

  /* Clears the page when the target app changes, so one app's data never sits under another's name. */
  function resetView() {
    live.renderedKey = "";
    live.failures = 0;
    googleCache.at = 0;
    googleCache.value = null;
    googleCache.clientId = "";
    live.badgeAt = 0;
    state.data = null;
    state.sectionArgs = null;
    state.manifestAt = 0;
    state.provider = "";
    ["appcms-summary", "appcms-sections", "appcms-findings", "appcms-compare"].forEach((id) => {
      const node = $(id);
      if (node) node.innerHTML = "";
    });
  }

  function reportFailure(error) {
    live.failures += 1;
    renderClient();
    const seconds = Math.max(1, Math.round(liveDelayMs() / 1000));
    const message = String(error.message || error);
    if (error.retryable === false) {
      live.failures = 0;
      notice(message, "is-error");
      renderClient();
      return;
    }
    const kept = live.renderedKey ? " Showing the last data." : "";
    notice(`${message}${kept} Retrying in ${seconds}s.`, "is-warn");
  }

  function manifestStale() {
    const slow = limitSeconds("live_diagnostics_seconds") * 1000;
    return !state.provider || live.failures > 0 || Date.now() - state.manifestAt >= slow;
  }

  async function loadManifest() {
    const sections = await read("ott.sections");
    state.sections = Array.isArray(sections.sections) ? sections.sections : [];
    state.actions = Array.isArray(sections.actions) ? sections.actions : [];
    state.provider = sections.provider || "none";
    state.manifest = sections;
    state.manifestAt = Date.now();
    renderClient();
    renderHostNav();
  }

  async function runLoad() {
    await limitsReady();
    snapshotCache.clear();
    const clientId = targetClientId();
    if (clientId !== state.clientId) {
      state.clientId = clientId;
      resetView();
    }
    renderClient();
    if (!state.clientId) {
      notice(`Choose one ${HOST_MODE ? "" : "AppCMS "}app in Scope. Several apps may be running on this device.`);
      return;
    }
    try {
      if (manifestStale()) await loadManifest();
      if (!providerSupported()) {
        live.failures = 0;
        renderClient();
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
      if (PAGE === "diagnostics") await loadDiagnostics();
      else if (PAGE === "actions") {
        if (isNewView([state.actions, state.manifest.action_titles])) renderActions();
      } else if (PAGE === "compare") await loadCompare();
      else await loadSectionPage();
      if (live.failures > 0) live.failures = 0;
      notice("");
      renderClient();
      refreshBadge();
    } catch (error) {
      reportFailure(error);
    }
  }

  function scheduleRefresh(changed) {
    if (PAGE === "actions" || PAGE === "compare") return;
    if (SECTION && Array.isArray(changed) && changed.length && !changed.includes(SECTION) && !changed.includes("*")) {
      refreshBadge();
      return;
    }
    load();
  }

  function connectSocket() {
    window.VLLive.connect("/ws/debug", {
      onResync: load,
      onMessage: (data) => {
        const item = data.event;
        if (data.type !== "debug-event" || !item || item.client_id !== state.clientId) return;
        if (item.module === "ott" && item.kind === "changed") scheduleRefresh(item.payload?.sections);
      },
    });
  }

  function bind() {
    $("appcms-refresh")?.addEventListener("click", () => {
      live.renderedKey = "";
      googleCache.at = 0;
      load();
    });
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
      snapshotCache.clear();
      loadSectionPage(id ? { content_id: id } : null).catch((error) => notice(String(error.message || error), "is-error"));
    });
    ["appcms-left", "appcms-right", "appcms-compare-section", "appcms-diff-only"].forEach((id) => {
      $(id)?.addEventListener("change", () => loadCompare().catch((error) => notice(String(error.message || error), "is-error")));
    });
    document.addEventListener("vl:scope-change", load);
    document.addEventListener("vl:apps-change", () => {
      const app = window.VLScope?.app(state.clientId);
      const recovered = live.failures > 0 && app?.online !== false;
      if (targetClientId() !== state.clientId || !state.provider || recovered) load();
      if (PAGE === "compare") renderCompareChoices();
    });
    /* Pause polling in a hidden tab and catch up the moment it returns or the network does. */
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) window.clearTimeout(live.timer);
      else load();
    });
    window.addEventListener("online", load);
  }

  bind();
  connectSocket();
  load();
})();
