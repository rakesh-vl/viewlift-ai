// Explorer tab: live object browser, loaded libraries, cookies, and device debug settings.
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
    filter: "",
    libraries: [],
    libraryFilter: "",
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
        return `<button type="button" class="explorer-link" data-open-handle="${escapeHtml(item.handle)}" data-label="${escapeHtml(item.class)}">`
          + `<span class="explorer-class">${escapeHtml(item.class)}</span> <span class="muted">${escapeHtml(item.preview)}</span></button>`;
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
      $("explorer-crumb")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-crumb]");
        if (!button) return;
        const index = Number(button.dataset.crumb);
        const entry = state.stack[index];
        if (!entry) return;
        state.stack = state.stack.slice(0, index + 1);
        state.send("explorer.object", { handle: entry.handle });
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
    },

    /** Another app was selected; its handles mean nothing here. */
    reset() {
      state.stack = [];
      state.current = null;
      ["explorer-result", "explorer-crumb", "settings-result"].forEach((id) => {
        const node = $(id);
        if (node) node.innerHTML = "";
      });
      const call = $("explorer-call");
      if (call) call.hidden = true;
      const roots = $("explorer-roots");
      if (roots) roots.innerHTML = `<p class="muted">Load shortcuts to start from the app, its windows, or a shared singleton.</p>`;
    },

    showTab() {
      const tab = document.querySelector('[data-debug-tab="explorer"]');
      if (tab && !tab.classList.contains("active")) tab.click();
    },

    /** Opens an object from another tab. `reset` starts a new breadcrumb trail. */
    open(payload, reset = false) {
      if (reset) state.stack = [];
      this.showTab();
      $("explorer-result").innerHTML = `<p class="muted">Loading object…</p>`;
      state.send("explorer.object", payload);
    },

    findInstances(className) {
      this.showTab();
      const input = $("explorer-instances")?.elements.class;
      if (input) input.value = className;
      $("explorer-result").innerHTML = `<p class="muted">Walking the heap for ${escapeHtml(className)}…</p>`;
      state.send("explorer.instances", { class: className, limit: 200 });
    },

    onClick(event) {
      const open = event.target.closest("[data-open-handle]");
      if (open && open.closest("#explorer-roots, #explorer-result, #explorer-call")) {
        const fromRoots = Boolean(open.closest("#explorer-roots")) || Boolean(open.closest("[data-instances]"));
        if (fromRoots) state.stack = [];
        this.open({ handle: open.dataset.openHandle });
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
      const image = event.target.closest("[data-image-classes]");
      if (image) {
        $("runtime-result").innerHTML = `<p class="muted">Loading classes…</p>`;
        state.send("runtime.image_classes", { image: image.dataset.imageClasses });
        return;
      }
      const describe = event.target.closest("[data-describe-class]");
      if (describe) {
        state.send("runtime.describe", { class: describe.dataset.describeClass });
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
        "runtime.libraries": () => this.renderLibraries(result),
        "runtime.image_classes": () => this.renderImageClasses(result),
        "runtime.describe": () => this.renderDescribe(result),
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
      $("explorer-roots").innerHTML = rows.length
        ? `<h3>Shortcuts</h3><ul class="explorer-root-list">${rows.map((row) => `<li><span class="explorer-root-name">${escapeHtml(row.name)}</span>${valueHtml(row.value)}</li>`).join("")}</ul>`
        : `<p class="muted">No shortcut objects reported.</p>`;
    },

    renderInstances(result) {
      if (result.error) return this.renderError(result.error);
      const rows = result.instances || [];
      const note = result.truncated ? ` Showing the first ${rows.length}.` : "";
      $("explorer-result").innerHTML = `<div data-instances><h3>${rows.length} live ${escapeHtml(result.class)} instances</h3>`
        + `<p class="muted">Subclasses included.${note} Objects can be released while you browse.</p>`
        + (rows.length
          ? `<ul class="explorer-root-list">${rows.map((row) => `<li>${valueHtml(row)}</li>`).join("")}</ul>`
          : `<p class="muted">No live instances found.</p>`)
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

    renderObject(name, result) {
      if (result.error) {
        if (name === "explorer.set") return state.notify(result.error);
        return this.renderError(result.error);
      }
      if (name === "explorer.set" && result.updated) state.notify(`Set ${result.updated}`);
      const top = state.stack[state.stack.length - 1];
      if (!top || top.handle !== result.handle) {
        state.stack.push({ handle: result.handle, label: result.class });
        if (state.stack.length > 30) state.stack.shift();
      }
      const rows = new Map();
      const section = (title, items, kind) => {
        if (!items.length) return "";
        const body = items.map((row) => {
          const key = `${kind}:${row.name}`;
          rows.set(row.name, row);
          const edit = row.writable
            ? `<button type="button" class="btn ghost" data-edit-key="${escapeHtml(row.name)}">Edit</button>`
            : "";
          return `<tr data-filter="${escapeHtml(`${row.name} ${row.type}`.toLowerCase())}" data-row="${escapeHtml(key)}">`
            + `<th class="mono">${escapeHtml(row.name)}</th><td class="plist-type">${escapeHtml(row.type)}</td>`
            + `<td class="explorer-value">${valueHtml(row.value)}</td><td class="explorer-actions">${edit}</td></tr>`;
        }).join("");
        return `<details class="explorer-section" open><summary>${escapeHtml(title)} <span class="muted">${items.length}</span></summary>`
          + `<table class="plist-table explorer-table"><tbody>${body}</tbody></table></details>`;
      };
      const levels = (result.levels || []).map((level) =>
        section(`${level.class} properties`, level.properties || [], "p")
        + section(`${level.class} ivars`, level.ivars || [], "i")
      ).join("");
      const methods = result.methods || [];
      const methodBody = methods.map((method) => {
        const call = method.callable
          ? `<button type="button" class="btn ghost" data-call="${escapeHtml(method.selector)}">Call</button>`
          : "";
        return `<tr data-filter="${escapeHtml(method.selector.toLowerCase())}"><th class="mono">-${escapeHtml(method.selector)}</th>`
          + `<td class="plist-type">${escapeHtml(method.returns)}</td><td class="explorer-actions">${call}</td></tr>`;
      }).join("");
      const collection = result.collection
        ? `<details class="explorer-section" open><summary>Elements <span class="muted">${result.collection.count}</span></summary>`
          + `<table class="plist-table explorer-table"><tbody>${(result.collection.items || []).map((item) =>
            `<tr data-filter="${escapeHtml(String(item.key).toLowerCase())}"><th class="mono">${escapeHtml(item.key)}</th><td class="explorer-value">${valueHtml(item.value)}</td></tr>`
          ).join("")}</tbody></table></details>`
        : "";
      state.current = { handle: result.handle, rows };
      const chain = (result.superclasses || []).map((cls) =>
        `<button type="button" class="btn ghost explorer-chip" data-describe-class="${escapeHtml(cls)}">${escapeHtml(cls)}</button>`
      ).join("");
      const module = String(result.module || "").split("/").pop();
      $("explorer-result").innerHTML = `<header class="explorer-head">
          <h3 class="mono">${escapeHtml(result.class)}</h3>
          <span class="muted">${escapeHtml(module)}</span>
          <button type="button" class="btn ghost" data-explore-class="${escapeHtml(result.class)}">All instances</button>
          <button type="button" class="btn ghost" data-describe-class="${escapeHtml(result.class)}">Class info</button>
        </header>
        <div class="explorer-chain">${chain}</div>
        <pre class="explorer-description">${escapeHtml(result.description)}</pre>
        ${collection}
        ${section("Swift fields", result.swift || [], "s")}
        ${levels}
        ${methods.length ? `<details class="explorer-section"><summary>Methods <span class="muted">${methods.length}</span></summary><table class="plist-table explorer-table"><tbody>${methodBody}</tbody></table></details>` : ""}`;
      this.renderCrumb();
      this.applyFilter();
      if (name === "explorer.object") $("explorer-call").hidden = true;
    },

    renderCrumb() {
      $("explorer-crumb").innerHTML = state.stack.map((entry, index) =>
        index === state.stack.length - 1
          ? `<span>${escapeHtml(entry.label)}</span>`
          : `<button type="button" data-crumb="${index}">${escapeHtml(entry.label)}</button>`
      ).join(`<span class="muted">/</span>`);
    },

    applyFilter() {
      const needle = state.filter;
      document.querySelectorAll("#explorer-result [data-filter]").forEach((row) => {
        row.hidden = Boolean(needle) && !row.dataset.filter.includes(needle);
      });
    },

    renderLibraries(result) {
      state.libraries = result.images || [];
      const target = $("runtime-result");
      target.innerHTML = `<div class="defaults-bar"><input type="search" id="library-filter" placeholder="Filter ${state.libraries.length} images" autocomplete="off" aria-label="Filter loaded libraries"><span class="muted" id="library-count"></span></div>`
        + `<table class="debug-table"><thead><tr><th scope="col">Image</th><th scope="col">Classes</th><th scope="col">Path</th></tr></thead><tbody id="library-rows"></tbody></table>`;
      const paint = () => {
        const needle = state.libraryFilter;
        const shown = state.libraries.filter((row) => !needle || row.path.toLowerCase().includes(needle));
        $("library-count").textContent = `${shown.length} of ${state.libraries.length}`;
        $("library-rows").innerHTML = shown.map((row) => `<tr>
            <td>${escapeHtml(row.name)}${row.app ? ` <span class="capability available">app</span>` : ""}</td>
            <td>${row.classes ? `<button type="button" class="btn ghost" data-image-classes="${escapeHtml(row.path)}">${row.classes}</button>` : `<span class="muted">0</span>`}</td>
            <td class="mono muted">${escapeHtml(row.path)}</td>
          </tr>`).join("");
      };
      $("library-filter").addEventListener("input", (event) => {
        state.libraryFilter = event.target.value.trim().toLowerCase();
        paint();
      });
      state.libraryFilter = "";
      paint();
    },

    renderImageClasses(result) {
      const classes = result.classes || [];
      const name = String(result.image || "").split("/").pop();
      $("runtime-result").innerHTML = `<p><button type="button" class="btn ghost" data-command="runtime.libraries">All libraries</button> `
        + `<strong>${escapeHtml(name)}</strong> <span class="muted">${classes.length} classes${result.truncated ? ", truncated" : ""}</span></p>`
        + classes.map((cls) => `<button type="button" class="btn ghost runtime-class" data-describe-class="${escapeHtml(cls)}">${escapeHtml(cls)}</button>`).join(" ");
    },

    renderDescribe(result) {
      const target = $("runtime-result");
      if (result.error) {
        target.innerHTML = `<p class="warn-text">${escapeHtml(result.error)}</p>`;
        return;
      }
      const table = (title, rows, columns) => rows.length
        ? `<details class="explorer-section" open><summary>${escapeHtml(title)} <span class="muted">${rows.length}</span></summary><table class="plist-table explorer-table"><tbody>${rows.map((row) =>
          `<tr>${columns.map((column, index) => index === 0
            ? `<th class="mono">${escapeHtml(row[column])}</th>`
            : `<td class="plist-type">${escapeHtml(typeof row[column] === "boolean" ? (row[column] ? column : "") : row[column])}</td>`).join("")}</tr>`
        ).join("")}</tbody></table></details>`
        : "";
      const chain = (result.superclasses || []).map((cls) =>
        `<button type="button" class="btn ghost explorer-chip" data-describe-class="${escapeHtml(cls)}">${escapeHtml(cls)}</button>`
      ).join("");
      this.showRuntimeTab();
      target.innerHTML = `<header class="explorer-head"><h3 class="mono">${escapeHtml(result.class)}</h3>`
        + `<span class="muted">${escapeHtml(String(result.image || "").split("/").pop())} · ${result.instance_size || 0} bytes</span>`
        + `<button type="button" class="btn ghost" data-explore-class="${escapeHtml(result.class)}">Live instances</button></header>`
        + `<div class="explorer-chain">${chain}</div>`
        + ((result.protocols || []).length ? `<p class="muted">Conforms to ${escapeHtml(result.protocols.join(", "))}</p>` : "")
        + table("Properties", result.properties || [], ["name", "type", "readonly", "weak"])
        + table("Ivars", result.ivars || [], ["name", "type", "offset"])
        + table("Instance methods", result.methods || [], ["selector", "returns"])
        + table("Class methods", result.class_methods || [], ["selector", "returns"])
        + window.ViewliftNetwork.copyBlockHtml(JSON.stringify(result, null, 2), "Copy JSON");
    },

    showRuntimeTab() {
      const tab = document.querySelector('[data-debug-tab="runtime"]');
      if (tab && !tab.classList.contains("active")) tab.click();
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
