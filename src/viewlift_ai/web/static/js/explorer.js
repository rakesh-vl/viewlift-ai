// Explorer tab: live object browser, cookies, and device debug settings.
(() => {
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const $ = (id) => document.getElementById(id);

  const state = {
    send: () => false,
    notify: () => {},
    selectedViewId: () => "",
    stack: [],
    current: null,
    lastObject: null,
    counts: {},
    tab: "fields",
    showSystem: false,
    filter: "",
  };

  const MODULE_LABELS = {
    performance: "Performance", memory: "Memory", ui: "UI inspector", swiftui: "SwiftUI probes",
    assets: "Assets audit", accessibility: "Accessibility audit", storage: "Storage",
    runtime: "Runtime browser", macro: "Macros", bug: "Bug report", push: "Push", ott: "OTT snapshot",
    crash: "Crashes", hang: "Hangs", activity: "Live activity", playback: "Playback", nav: "Navigation",
    explorer: "Object explorer", replay: "Crash replay",
  };

  const valueHtml = (value) => {
    const item = value || {};
    switch (item.kind) {
      case "nil":
        return `<span class="muted">nil</span>`;
      case "bool":
        return `<span class="plist-bool">${item.value ? "true" : "false"}</span>`;
      case "number":
        return `<span class="plist-num">${escapeHtml(item.value ?? item.preview ?? "")}</span>`;
      case "string":
        return `<span class="plist-string">"${escapeHtml(item.value)}"</span>`;
      case "object":
      case "value":
        return `<button type="button" class="explorer-link" data-open-handle="${escapeHtml(item.handle)}" data-label="${escapeHtml(item.class)}" title="Open ${escapeHtml(item.class)}">`
          + `<span class="explorer-class">${escapeHtml(String(item.class || "").split(".").pop())}</span> <span class="muted">${escapeHtml(item.preview)}</span> <span class="explorer-open" aria-hidden="true">›</span></button>`;
      case "struct":
        return `<span class="mono">${escapeHtml(item.preview)}</span>`;
      case "void":
        return `<span class="muted">void</span>`;
      default:
        return `<span class="muted">${escapeHtml(item.preview || "unreadable")}</span>`;
    }
  };

  /** JSON when it parses, otherwise the raw text, so `hello` and `"hello"` both set a string. */
  const parseInput = (raw, type) => {
    if (/NSString|NSURL|UIColor|CGRect|CGPoint|CGSize|UIEdgeInsets/.test(type || "")) return raw;
    try { return JSON.parse(raw); } catch (_) { return raw; }
  };

  const editText = (value) => {
    const item = value || {};
    if (item.kind === "string") return item.value || "";
    if (item.kind === "bool" || item.kind === "number") return String(item.value ?? "");
    if (item.kind === "struct" || item.kind === "value") {
      const preview = String(item.preview || "");
      const brace = preview.indexOf("{");
      return brace >= 0 ? preview.slice(brace) : preview;
    }
    return "";
  };

  // Swift classes carry a module prefix ("App.Player"); system classes do not.
  const isSystemClass = (name) => /^(NS|UI|CA|CF|AV|WK|_)/.test(String(name || "")) && !String(name).includes(".");
  const displayClass = (name) => String(name || "")
    .replace(/^NSKVONotifying_/, "")
    .replace(/^GUL_/, "")
    .replace(/-[0-9A-F]{8}-[0-9A-F-]{27}$/i, "")
    .split(".").pop();
  const SCREEN_ROOTS = ["Top view controller", "Root view controller", "Key window"];
  const QUICK_CLASSES = ["UIViewController", "UINavigationController", "UIWindow", "AVPlayer", "WKWebView"];

  const Explorer = {
    init({ send, notify, selectedViewId }) {
      state.send = send;
      state.notify = notify || state.notify;
      state.selectedViewId = selectedViewId || state.selectedViewId;
      $("explorer-instances")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const name = event.target.elements.class.value.trim();
        if (name) this.findInstances(name);
      });
      $("explorer-filter")?.addEventListener("input", (event) => {
        state.filter = event.target.value.trim().toLowerCase();
        this.applyFilter();
      });
      $("explorer-system")?.addEventListener("change", (event) => {
        state.showSystem = event.target.checked;
        event.target.closest(".ui-check")?.classList.toggle("is-on", state.showSystem);
        if (state.lastObject) this.renderObject("explorer.object", state.lastObject, true);
      });
      $("explorer-tabs")?.addEventListener("click", (event) => {
        const tab = event.target.closest("[data-explorer-tab]");
        if (!tab) return;
        state.tab = tab.dataset.explorerTab;
        this.paintTabs();
      });
      $("explorer-back")?.addEventListener("click", () => {
        if (state.stack.length < 2) return;
        state.stack.pop();
        const entry = state.stack[state.stack.length - 1];
        this.load({ handle: entry.handle });
      });
      $("explorer-crumb")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-crumb]");
        if (!button) return;
        const index = Number(button.dataset.crumb);
        const entry = state.stack[index];
        if (!entry) return;
        state.stack = state.stack.slice(0, index + 1);
        this.load({ handle: entry.handle });
      });
      $("explorer-quick")?.addEventListener("click", (event) => {
        const chip = event.target.closest("[data-quick-class]");
        if (chip) this.findInstances(chip.dataset.quickClass);
      });
      $("ui-explore")?.addEventListener("click", () => {
        const viewId = state.selectedViewId();
        if (!viewId) {
          state.notify("Select a view in the hierarchy first.");
          return;
        }
        this.open({ view_id: viewId }, true);
      });
      document.body.addEventListener("click", (event) => this.onClick(event));
      document.body.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        const input = event.target.closest("[data-edit-input]");
        if (input) {
          event.preventDefault();
          this.commitEdit(input);
        }
      });
      $("settings-result")?.addEventListener("submit", (event) => {
        event.preventDefault();
        this.saveSettings(event.target);
      });
      document.querySelector('[data-debug-tab="settings"]')?.addEventListener("click", () => {
        if (!$("settings-result")?.childElementCount) state.send("settings.get", {});
      });
      document.querySelector('[data-debug-tab="explorer"]')?.addEventListener("click", () => {
        if (!$("explorer-roots")?.querySelector("[data-open-handle]")) state.send("explorer.roots", {});
      });
      this.renderQuick("");
    },

    /** Another app was selected; its handles mean nothing here. */
    reset() {
      state.stack = [];
      state.current = null;
      state.lastObject = null;
      ["explorer-crumb", "settings-result"].forEach((id) => {
        const node = $(id);
        if (node) node.innerHTML = "";
      });
      const result = $("explorer-result");
      if (result) result.innerHTML = `<p class="muted">Pick a shortcut on the left to start.</p>`;
      const call = $("explorer-call");
      if (call) call.hidden = true;
      const toolbar = $("explorer-toolbar");
      if (toolbar) toolbar.hidden = true;
      const roots = $("explorer-roots");
      if (roots) roots.innerHTML = `<p class="muted">Open this tab with an app selected to load shortcuts.</p>`;
      this.renderQuick("");
      this.paintBack();
    },

    showTab() {
      const tab = document.querySelector('[data-debug-tab="explorer"]');
      if (tab && !tab.classList.contains("active")) tab.click();
    },

    /** Opens an object from another tab. `reset` starts a new breadcrumb trail. */
    open(payload, reset = false) {
      if (reset) state.stack = [];
      this.showTab();
      this.load(payload);
    },

    load(payload) {
      $("explorer-result").innerHTML = `<p class="muted">Loading object…</p>`;
      state.send("explorer.object", payload);
    },

    findInstances(className) {
      this.showTab();
      const input = $("explorer-instances")?.elements.class;
      if (input) input.value = className;
      $("explorer-toolbar").hidden = true;
      $("explorer-result").innerHTML = `<p class="muted">Looking for live ${escapeHtml(className)} objects…</p>`;
      state.send("explorer.instances", { class: className, limit: 200 });
    },

    setClassHints(classes) {
      const list = $("explorer-class-hints");
      if (list) list.innerHTML = classes.map((name) => `<option value="${escapeHtml(name)}"></option>`).join("");
    },

    onClick(event) {
      const open = event.target.closest("[data-open-handle]");
      if (open && open.closest("#explorer-roots, #explorer-result, #explorer-call")) {
        const fromRoots = Boolean(open.closest("#explorer-roots")) || Boolean(open.closest("[data-instances]"));
        if (fromRoots) state.stack = [];
        this.load({ handle: open.dataset.openHandle });
        return;
      }
      const explore = event.target.closest("[data-explore-class]");
      if (explore) {
        this.findInstances(explore.dataset.exploreClass);
        return;
      }
      const call = event.target.closest("[data-call]");
      if (call && state.current) {
        state.send("explorer.call", { handle: state.current.handle, selector: call.dataset.call });
        return;
      }
      const edit = event.target.closest("[data-edit-key]");
      if (edit) {
        this.beginEdit(edit);
        return;
      }
      const save = event.target.closest("[data-edit-save]");
      if (save) {
        const input = save.parentElement?.querySelector("[data-edit-input]");
        if (input) this.commitEdit(input);
        return;
      }
      const describe = event.target.closest("[data-describe-class]");
      if (describe && describe.closest("#explorer-result")) {
        window.ViewliftRuntime?.describe(describe.dataset.describeClass);
        return;
      }
      if (event.target.closest("[data-show-system]")) {
        const box = $("explorer-system");
        if (box) {
          box.checked = true;
          box.dispatchEvent(new Event("change"));
        }
        return;
      }
      const cookie = event.target.closest("[data-cookie-delete]");
      if (cookie) {
        state.send("storage.cookie_delete", {
          name: cookie.dataset.cookieDelete,
          domain: cookie.dataset.domain || "",
          path: cookie.dataset.path || "",
        });
        return;
      }
      if (event.target.closest("[data-settings-reset]")) {
        state.send("settings.set", { reset: true });
      }
    },

    beginEdit(button) {
      const cell = button.closest("tr")?.querySelector(".explorer-value");
      if (!cell || cell.querySelector("[data-edit-input]")) return;
      const row = state.current?.rows?.get(button.dataset.editKey);
      cell.innerHTML = `<span class="explorer-edit"><input data-edit-input data-key="${escapeHtml(button.dataset.editKey)}" data-type="${escapeHtml(row?.type || "")}" value="${escapeHtml(editText(row?.value))}" aria-label="New value for ${escapeHtml(button.dataset.editKey)}">`
        + `<button type="button" class="btn ghost" data-edit-save>Save</button></span>`;
      cell.querySelector("input")?.focus();
    },

    commitEdit(input) {
      if (!state.current) return;
      state.send("explorer.set", {
        handle: state.current.handle,
        key: input.dataset.key,
        value: parseInput(input.value, input.dataset.type),
      });
    },

    /** Returns true when this module rendered the result. */
    onResult(name, result) {
      const handlers = {
        "explorer.roots": () => this.renderRoots(result),
        "explorer.object": () => this.renderObject(name, result),
        "explorer.set": () => this.renderObject(name, result),
        "explorer.instances": () => this.renderInstances(result),
        "explorer.call": () => this.renderCall(result),
        "settings.get": () => this.renderSettings(result),
        "settings.set": () => {
          state.notify("Device settings saved");
          this.renderSettings(result);
        },
      };
      const handler = handlers[name];
      if (!handler) return false;
      handler();
      return true;
    },

    renderRoots(result) {
      const rows = result.roots || [];
      const groups = [
        ["Current screen", rows.filter((row) => SCREEN_ROOTS.includes(row.name))],
        ["Your app", rows.filter((row) => !SCREEN_ROOTS.includes(row.name) && !isSystemClass(row.value?.class))],
        ["System", rows.filter((row) => !SCREEN_ROOTS.includes(row.name) && isSystemClass(row.value?.class))],
      ];
      const item = (row) => {
        const value = row.value || {};
        if (!value.handle) return `<li class="muted">${escapeHtml(row.name)}: ${escapeHtml(value.preview || "nil")}</li>`;
        return `<li><button type="button" class="explorer-root" data-open-handle="${escapeHtml(value.handle)}">`
          + `<span>${escapeHtml(row.name)}</span><span class="mono muted">${escapeHtml(displayClass(value.class))}</span></button></li>`;
      };
      $("explorer-roots").innerHTML = rows.length
        ? groups.filter(([, items]) => items.length).map(([title, items]) =>
          `<h3>${escapeHtml(title)}</h3><ul class="explorer-root-list">${items.map(item).join("")}</ul>`).join("")
        : `<p class="muted">No shortcut objects reported.</p>`;
      const top = rows.find((row) => row.name === "Top view controller")?.value?.class || "";
      this.renderQuick(top);
    },

    renderQuick(topClass) {
      const host = $("explorer-quick");
      if (!host) return;
      const classes = [...new Set([topClass, ...QUICK_CLASSES].filter(Boolean))];
      host.innerHTML = `<span class="muted">Find:</span>` + classes.map((name) =>
        `<button type="button" class="runtime-chip" data-quick-class="${escapeHtml(name)}" title="${escapeHtml(name)}">${escapeHtml(displayClass(name))}</button>`).join("");
    },

    renderInstances(result) {
      if (result.error) return this.renderError(result.error);
      const rows = result.instances || [];
      const note = result.truncated ? ` Showing the first ${rows.length}.` : "";
      $("explorer-result").innerHTML = `<div data-instances><h3>${rows.length} live ${escapeHtml(displayClass(result.class))} object${rows.length === 1 ? "" : "s"}</h3>`
        + `<p class="muted">Subclasses included.${note} Click one to open it. Objects can be released while you browse.</p>`
        + (rows.length
          ? `<ul class="explorer-instance-list">${rows.map((row) => `<li><button type="button" class="explorer-root" data-open-handle="${escapeHtml(row.handle)}">`
            + `<span class="mono">${escapeHtml(displayClass(row.class))}</span><span class="muted">${escapeHtml(row.preview)}</span></button></li>`).join("")}</ul>`
          : `<p class="muted">No live instances found. Check the spelling, or open the screen that creates them.</p>`)
        + `</div>`;
    },

    renderError(message) {
      $("explorer-result").innerHTML = `<p class="warn-text">${escapeHtml(message)}</p>`;
    },

    renderCall(result) {
      const host = $("explorer-call");
      if (!host) return;
      host.hidden = false;
      host.innerHTML = result.error
        ? `<p class="warn-text">${escapeHtml(result.error)}</p>`
        : `<span class="mono">-${escapeHtml(result.selector)}</span> returned ${valueHtml(result.result)}`;
    },

    fieldRow(row, kind, rows) {
      rows.set(row.name, row);
      const edit = row.writable
        ? `<button type="button" class="btn ghost" data-edit-key="${escapeHtml(row.name)}">Edit</button>`
        : "";
      const tag = kind === "i" ? `<span class="explorer-tag">ivar</span>` : "";
      return `<tr data-filter="${escapeHtml(`${row.name} ${row.type}`.toLowerCase())}">`
        + `<th class="mono">${escapeHtml(row.name)}${tag}</th>`
        + `<td class="explorer-value">${valueHtml(row.value)}</td>`
        + `<td class="plist-type">${escapeHtml(row.type)}</td><td class="explorer-actions">${edit}</td></tr>`;
    },

    fieldsHtml(result, rows) {
      const table = (body) => `<table class="plist-table explorer-table"><tbody>${body}</tbody></table>`;
      const levels = (result.levels || []).filter((level) => (level.properties || []).length || (level.ivars || []).length);
      const appLevels = levels.filter((level) => !isSystemClass(level.class));
      const keep = (level, index) => state.showSystem || (appLevels.length ? !isSystemClass(level.class) : index === 0);
      const shown = levels.filter(keep);
      const hidden = levels.length - shown.length;
      const swift = result.swift || [];
      let html = swift.length
        ? `<details class="explorer-section" open><summary>Swift fields <span class="muted">${swift.length}</span></summary>${table(swift.map((row) => this.fieldRow(row, "s", rows)).join(""))}</details>`
        : "";
      html += shown.map((level, index) => {
        const props = level.properties || [];
        const ivars = level.ivars || [];
        const body = props.map((row) => this.fieldRow(row, "p", rows)).join("") + ivars.map((row) => this.fieldRow(row, "i", rows)).join("");
        const open = index === 0 || !isSystemClass(level.class);
        return `<details class="explorer-section" ${open ? "open" : ""}><summary>${escapeHtml(displayClass(level.class))} `
          + `<span class="muted">${props.length} properties${ivars.length ? `, ${ivars.length} ivars` : ""}</span></summary>${table(body)}</details>`;
      }).join("");
      if (hidden) {
        html += `<p class="muted">${hidden} UIKit / Foundation level${hidden === 1 ? " is" : "s are"} hidden. `
          + `<button type="button" class="btn ghost" data-show-system>Show them</button></p>`;
      }
      return html || `<p class="muted">No readable fields.</p>`;
    },

    methodsHtml(methods) {
      if (!methods.length) return `<p class="muted">No methods listed.</p>`;
      const sorted = [...methods].sort((left, right) => Number(right.callable) - Number(left.callable) || left.selector.localeCompare(right.selector));
      return `<p class="muted">Call runs a method that takes no arguments, after you confirm.</p>`
        + `<table class="plist-table explorer-table"><tbody>${sorted.map((method) => {
          const call = method.callable
            ? `<button type="button" class="btn ghost" data-call="${escapeHtml(method.selector)}">Call</button>`
            : "";
          return `<tr data-filter="${escapeHtml(method.selector.toLowerCase())}"><th class="mono">-${escapeHtml(method.selector)}</th>`
            + `<td class="plist-type">${escapeHtml(method.returns)}</td><td class="explorer-actions">${call}</td></tr>`;
        }).join("")}</tbody></table>`;
    },

    elementsHtml(collection) {
      return `<table class="plist-table explorer-table"><tbody>${(collection.items || []).map((item) =>
        `<tr data-filter="${escapeHtml(String(item.key).toLowerCase())}"><th class="mono">${escapeHtml(item.key)}</th><td class="explorer-value">${valueHtml(item.value)}</td></tr>`
      ).join("")}</tbody></table>`
        + (collection.count > (collection.items || []).length ? `<p class="muted">Showing ${(collection.items || []).length} of ${collection.count}.</p>` : "");
    },

    renderObject(name, result, keepTab = false) {
      if (result.error) {
        if (name === "explorer.set") return state.notify(result.error);
        return this.renderError(result.error);
      }
      if (name === "explorer.set" && result.updated) state.notify(`Set ${result.updated}`);
      const top = state.stack[state.stack.length - 1];
      if (!top || top.handle !== result.handle) {
        state.stack.push({ handle: result.handle, label: displayClass(result.class) });
        if (state.stack.length > 30) state.stack.shift();
        if (!keepTab && name === "explorer.object") state.tab = result.collection ? "elements" : "fields";
      }
      state.lastObject = result;
      const rows = new Map();
      const fields = this.fieldsHtml(result, rows);
      const methods = result.methods || [];
      state.current = { handle: result.handle, rows };
      state.counts = {
        fields: rows.size,
        methods: methods.length,
        elements: result.collection ? result.collection.count : null,
      };
      if (state.tab === "elements" && !result.collection) state.tab = "fields";
      const chain = (result.superclasses || []).map((cls) =>
        `<button type="button" class="btn ghost explorer-chip" data-describe-class="${escapeHtml(cls)}" title="Open ${escapeHtml(cls)} in the runtime browser">${escapeHtml(displayClass(cls))}</button>`
      ).join(`<span class="muted" aria-hidden="true">›</span>`);
      const module = String(result.module || "").split("/").pop();
      const description = String(result.description || "");
      const firstLine = description.split("\n")[0].slice(0, 160);
      $("explorer-result").innerHTML = `<header class="explorer-head">
          <h3 class="mono" title="${escapeHtml(result.class)}">${escapeHtml(displayClass(result.class))}</h3>
          ${module ? `<span class="muted">${escapeHtml(module)}</span>` : ""}
          <button type="button" class="btn ghost" data-explore-class="${escapeHtml(result.class)}">All instances</button>
          <button type="button" class="btn ghost" data-describe-class="${escapeHtml(result.class)}">Class info</button>
        </header>
        ${chain ? `<div class="explorer-chain"><span class="muted">Inherits</span>${chain}</div>` : ""}
        ${description ? `<details class="explorer-description-wrap"><summary class="mono">${escapeHtml(firstLine)}</summary><pre class="explorer-description">${escapeHtml(description)}</pre></details>` : ""}
        <div data-pane="fields">${fields}</div>
        <div data-pane="methods">${this.methodsHtml(methods)}</div>
        ${result.collection ? `<div data-pane="elements">${this.elementsHtml(result.collection)}</div>` : ""}`;
      $("explorer-toolbar").hidden = false;
      this.paintTabs();
      this.renderCrumb();
      this.paintBack();
      if (name === "explorer.object") $("explorer-call").hidden = true;
    },

    paintTabs() {
      const counts = state.counts || {};
      const tabs = [["fields", "Fields", counts.fields], ["methods", "Methods", counts.methods]];
      if (counts.elements != null) tabs.unshift(["elements", "Elements", counts.elements]);
      $("explorer-tabs").innerHTML = tabs.map(([id, label, count]) =>
        `<button type="button" role="tab" data-explorer-tab="${id}" aria-selected="${state.tab === id}" class="${state.tab === id ? "active" : ""}">${label} <span class="muted">${count ?? 0}</span></button>`).join("");
      document.querySelectorAll("#explorer-result [data-pane]").forEach((pane) => {
        pane.hidden = pane.dataset.pane !== state.tab;
      });
      const system = $("explorer-system")?.closest(".ui-check");
      if (system) system.hidden = state.tab !== "fields";
      this.applyFilter();
    },

    paintBack() {
      const back = $("explorer-back");
      if (back) back.disabled = state.stack.length < 2;
    },

    renderCrumb() {
      $("explorer-crumb").innerHTML = state.stack.map((entry, index) =>
        index === state.stack.length - 1
          ? `<span>${escapeHtml(entry.label)}</span>`
          : `<button type="button" data-crumb="${index}">${escapeHtml(entry.label)}</button>`
      ).join(`<span class="muted">›</span>`);
    },

    applyFilter() {
      const needle = state.filter;
      const pane = document.querySelector(`#explorer-result [data-pane="${CSS.escape(state.tab)}"]`);
      if (!pane) return;
      pane.querySelectorAll("[data-filter]").forEach((row) => {
        row.hidden = Boolean(needle) && !row.dataset.filter.includes(needle);
      });
      if (needle) pane.querySelectorAll("details").forEach((section) => { section.open = true; });
    },

    renderCookies(name, result) {
      if (name === "storage.cookie_delete") {
        state.notify(`Deleted ${result.deleted || 0} cookie${result.deleted === 1 ? "" : "s"}`);
        state.send("storage.cookies", {});
        return;
      }
      const target = $("storage-inspector");
      const cookies = result.cookies || [];
      const note = `<p class="muted">Values are masked on the device. Names, scope, and flags are shown.</p>`;
      if (!cookies.length) {
        target.innerHTML = `${note}<p class="muted">HTTPCookieStorage is empty.</p>`;
        return;
      }
      const flags = (cookie) => [cookie.secure && "Secure", cookie.http_only && "HttpOnly", cookie.same_site, cookie.session_only && "Session"]
        .filter(Boolean).join(" · ");
      const expires = (cookie) => (cookie.expires ? new Date(cookie.expires * 1000).toLocaleString() : "Session");
      target.innerHTML = `${note}<table class="debug-table"><thead><tr><th scope="col">Name</th><th scope="col">Domain</th><th scope="col">Path</th><th scope="col">Value</th><th scope="col">Expires</th><th scope="col">Flags</th><th scope="col"></th></tr></thead><tbody>`
        + cookies.map((cookie) => `<tr>
            <td class="mono">${escapeHtml(cookie.name)}</td><td>${escapeHtml(cookie.domain)}</td><td>${escapeHtml(cookie.path)}</td>
            <td class="mono">${escapeHtml(cookie.value_preview)} <span class="muted">(${cookie.value_length})</span></td>
            <td>${escapeHtml(expires(cookie))}</td><td>${escapeHtml(flags(cookie))}</td>
            <td><button type="button" class="btn ghost" data-cookie-delete="${escapeHtml(cookie.name)}" data-domain="${escapeHtml(cookie.domain)}" data-path="${escapeHtml(cookie.path)}">Delete</button></td>
          </tr>`).join("")
        + `</tbody></table>`;
    },

    renderSettings(result) {
      const modules = result.modules || [];
      const types = (result.body_content_types || []).join(", ");
      const limit = result.max_body_bytes_limit || window.ViewliftLimits?.get("max_body_bytes") || "";
      $("settings-result").innerHTML = `<form class="settings-form">
        <fieldset class="settings-group"><legend>Network capture</legend>
          <label class="flag-row"><span class="grow"><strong>Capture requests</strong><br><span class="muted">Off lets traffic pass untouched while the debug bridge stays connected.</span></span>
            <input type="checkbox" name="capture_network" ${result.capture_network ? "checked" : ""}></label>
          <label class="settings-field">Request body limit (bytes)<input type="number" name="max_request_body_bytes" min="0" max="${limit}" value="${Number(result.max_request_body_bytes || 0)}"></label>
          <label class="settings-field">Response body limit (bytes)<input type="number" name="max_response_body_bytes" min="0" max="${limit}" value="${Number(result.max_response_body_bytes || 0)}"></label>
          <label class="settings-field">Keep bodies only for these Content-Types<input type="text" name="body_content_types" value="${escapeHtml(types)}" placeholder="Empty keeps all, for example application/json, text/"></label>
        </fieldset>
        <fieldset class="settings-group"><legend>Debug modules</legend>
          ${modules.map((module) => `<label class="flag-row"><span class="grow"><strong>${escapeHtml(MODULE_LABELS[module.id] || module.id)}</strong><br><span class="muted">${escapeHtml(module.id)}${module.host_enabled ? "" : " · off in the app's configuration"}</span></span>
            <input type="checkbox" name="module" value="${escapeHtml(module.id)}" ${module.enabled ? "checked" : ""} ${module.host_enabled ? "" : "disabled"}></label>`).join("")}
        </fieldset>
        <div class="settings-actions">
          <button type="submit" class="btn">Save to device</button>
          <button type="button" class="btn ghost" data-settings-reset ${result.overridden ? "" : "disabled"}>Reset to app defaults</button>
          <span class="muted">${result.overridden ? "Overrides active on this device." : "Using the app's configuration."}</span>
        </div>
      </form>`;
    },

    saveSettings(form) {
      const number = (key) => Number(form.elements[key].value || 0);
      state.send("settings.set", {
        capture_network: form.elements.capture_network.checked,
        max_request_body_bytes: number("max_request_body_bytes"),
        max_response_body_bytes: number("max_response_body_bytes"),
        body_content_types: form.elements.body_content_types.value.split(",").map((item) => item.trim()).filter(Boolean),
        modules: [...form.querySelectorAll('input[name="module"]:checked')].map((input) => input.value),
      });
    },
  };

  window.ViewliftExplorer = Explorer;
})();
