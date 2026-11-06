(() => {
  const root = document.documentElement;
  const lightQuery = window.matchMedia("(prefers-color-scheme: light)");

  function store(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage disabled: preference lasts for this page only */
    }
  }

  function currentTheme() {
    return root.dataset.theme || (lightQuery.matches ? "light" : "dark");
  }

  function syncThemeButton(button) {
    const next = currentTheme() === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", `Switch to ${next} theme`);
  }

  function syncRailButton(button) {
    const collapsed = root.dataset.rail === "collapsed";
    button.setAttribute("aria-expanded", String(!collapsed));
    button.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  }

  const tabId = (() => {
    try {
      const existing = sessionStorage.getItem("vl-browser");
      if (existing) return existing;
      const created = crypto.randomUUID();
      sessionStorage.setItem("vl-browser", created);
      return created;
    } catch {
      return "";
    }
  })();
  const page = (() => {
    try {
      const next = Number(sessionStorage.getItem("vl-page") || "0") + 1;
      sessionStorage.setItem("vl-page", String(next));
      return next;
    } catch {
      return 1;
    }
  })();

  function sessionBody() {
    return JSON.stringify({ id: tabId, page });
  }

  function pingSession() {
    if (!tabId) return;
    fetch("/api/session/ping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: sessionBody(),
      keepalive: true,
    }).catch(() => {});
  }

  if (tabId) {
    pingSession();
    setInterval(pingSession, 15000);
    window.addEventListener("pagehide", () => {
      const blob = new Blob([sessionBody()], { type: "application/json" });
      if (!navigator.sendBeacon || !navigator.sendBeacon("/api/session/leave", blob)) {
        fetch("/api/session/leave", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: sessionBody(),
          keepalive: true,
        }).catch(() => {});
      }
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hue(text) {
    let h = 0;
    for (const char of String(text || "")) h = (h * 31 + char.charCodeAt(0)) >>> 0;
    return h % 360;
  }

  function initials(name) {
    const words = String(name || "?").replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/);
    const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2);
    return letters.toUpperCase();
  }

  /*
   * Device > App scope shared by every page. Each browser tab keeps its own choice in
   * sessionStorage, so two tabs can watch two apps running on the same simulator.
   * Values: "" (all apps), "d:<device_key>", "c:<client_id>".
   */
  const Scope = {
    value: (() => {
      try {
        return sessionStorage.getItem("vl-scope") || "";
      } catch {
        return "";
      }
    })(),
    devices: [],
    proxyApps: [],
    byId: new Map(),
    loaded: false,

    get() {
      if (this.value.startsWith("c:")) return { kind: "app", clientId: this.value.slice(2) };
      if (this.value.startsWith("d:")) return { kind: "device", deviceKey: this.value.slice(2) };
      return { kind: "all" };
    },

    clientId() {
      const scope = this.get();
      return scope.kind === "app" ? scope.clientId : null;
    },

    selectedApp() {
      const id = this.clientId();
      return id ? this.byId.get(id) || null : null;
    },

    app(clientId) {
      return clientId ? this.byId.get(clientId) || null : null;
    },

    appCount() {
      return this.byId.size;
    },

    multiple() {
      return this.byId.size > 1;
    },

    onlineApps() {
      return Array.from(this.byId.values()).filter((app) => app.online && !app.superseded_by);
    },

    /* online | background | offline | reinstalled. Background means suspended, not lost. */
    stateOf(app) {
      if (app.superseded_by) return "reinstalled";
      return app.state || (app.online ? "online" : "offline");
    },

    matches(clientId) {
      const scope = this.get();
      if (scope.kind === "all") return true;
      if (scope.kind === "app") return clientId === scope.clientId;
      const app = this.app(clientId);
      return !!app && app.device_key === scope.deviceKey;
    },

    applyTo(params) {
      const scope = this.get();
      if (scope.kind === "app") params.append("client_id", scope.clientId);
      if (scope.kind === "device") params.set("device_key", scope.deviceKey);
      return params;
    },

    wsQuery() {
      const id = this.clientId();
      return id ? `?client_id=${encodeURIComponent(id)}` : "";
    },

    set(value, { silent = false } = {}) {
      const next = value || "";
      if (next === this.value) return;
      this.value = next;
      try {
        sessionStorage.setItem("vl-scope", next);
      } catch {
        /* storage disabled: scope lasts for this page only */
      }
      this.render();
      if (!silent) document.dispatchEvent(new CustomEvent("vl:scope-change", { detail: this.get() }));
    },

    badge(clientId) {
      if (!clientId || !this.multiple()) return "";
      const app = this.app(clientId);
      const name = app ? app.app_name : "Unknown app";
      const key = app ? app.bundle_id || app.id : clientId;
      const title = app ? `${app.app_name} · ${app.bundle_id} · ${app.device_name || ""}` : clientId;
      return `<span class="app-badge" style="--badge-h:${hue(key)}" title="${escapeHtml(title)}">${escapeHtml(initials(name))}</span>`;
    },

    label(clientId) {
      const app = this.app(clientId);
      if (!app) return clientId ? "Unknown app" : "Unattributed (proxy or Mac logs)";
      return `${app.app_name} ${app.app_version || ""} · ${app.bundle_id}`.replace(/\s+·/, " ·");
    },

    async refresh() {
      try {
        const res = await fetch("/api/apps");
        if (!res.ok) return;
        const data = await res.json();
        this.devices = data.devices || [];
        this.proxyApps = data.proxy_clients || [];
        this.byId = new Map();
        for (const device of this.devices) {
          for (const app of device.apps || []) {
            this.byId.set(app.id, { ...app, device_name: device.device_name });
          }
        }
        this.loaded = true;
        this.render();
        document.dispatchEvent(new CustomEvent("vl:apps-change", { detail: this }));
      } catch {
        /* dashboard server restarting: keep the last known list */
      }
    },

    render() {
      const select = document.getElementById("scope-select");
      if (!select) return;
      const groups = this.devices.map((device) => {
        const apps = device.apps || [];
        const where = [device.platform, device.os_version, device.is_simulator ? "Simulator" : "Device"]
          .filter(Boolean)
          .join(" ");
        const options = [];
        if (apps.length > 1) {
          options.push(
            `<option value="d:${escapeHtml(device.device_key)}">All apps on this device (${apps.length})</option>`
          );
        }
        for (const app of apps) {
          const state = Scope.stateOf(app);
          const glyph = state === "online" ? "\u25CF" : state === "background" ? "\u25D0" : "\u25CB";
          const label = `${glyph} ${app.app_name} ${app.app_version || ""} · ${state}`;
          options.push(`<option value="c:${escapeHtml(app.id)}">${escapeHtml(label)}</option>`);
        }
        return `<optgroup label="${escapeHtml(`${device.device_name} · ${where}`)}">${options.join("")}</optgroup>`;
      });
      select.innerHTML = `<option value="">All apps</option>${groups.join("")}`;
      const present = Array.from(select.options).some((option) => option.value === this.value);
      if (!present) {
        const missing = this.get().kind === "app" ? "Selected app (not connected)" : "Selected device (not connected)";
        select.insertAdjacentHTML(
          "beforeend",
          `<option value="${escapeHtml(this.value)}">${escapeHtml(missing)}</option>`
        );
      }
      select.value = this.value;
      this.renderMeta();
    },

    renderMeta() {
      const meta = document.getElementById("scope-meta");
      const clear = document.getElementById("scope-clear");
      const online = this.onlineApps().length;
      const scope = this.get();
      if (clear) clear.textContent = scope.kind === "all" ? "Clear all data" : "Clear app data";
      if (!meta) return;
      if (!this.loaded) {
        meta.textContent = "";
        return;
      }
      const proxyOnly = this.proxyApps.filter((app) => app.online).length;
      const proxyNote = proxyOnly ? `${proxyOnly} proxy-only app${proxyOnly === 1 ? "" : "s"} active (see Setup)` : "";
      if (!this.byId.size) {
        meta.textContent = [
          "No SDK app has registered yet. Proxy traffic still shows under All apps.",
          proxyNote,
        ].filter(Boolean).join(" · ");
        return;
      }
      const sharedDevice = this.devices.some((device) => (device.apps || []).filter((a) => a.online).length > 1);
      const parts = [`${online} of ${this.byId.size} app${this.byId.size === 1 ? "" : "s"} online`];
      const backgrounded = Array.from(this.byId.values()).filter((a) => this.stateOf(a) === "background").length;
      if (backgrounded) parts.push(`${backgrounded} in background`);
      if (proxyNote) parts.push(proxyNote);
      if (sharedDevice) parts.push("several apps share one device; pick one to isolate its traffic");
      const app = this.selectedApp();
      if (app && app.counts) parts.push(`${app.counts.flows} flows, ${app.counts.logs} logs stored`);
      meta.textContent = parts.join(" · ");
    },

    async clear() {
      const scope = this.get();
      if (scope.kind === "all") {
        const ok = window.confirm(
          "Clear captured flows, logs, and debug events for every app? This cannot be undone."
        );
        if (!ok) return;
        await fetch("/api/apps/data", { method: "DELETE" });
      } else {
        const targets =
          scope.kind === "app"
            ? [scope.clientId]
            : Array.from(this.byId.values())
                .filter((app) => app.device_key === scope.deviceKey)
                .map((app) => app.id);
        const names = targets.map((id) => (this.app(id) || { app_name: id }).app_name).join(", ");
        const ok = window.confirm(`Clear captured flows, logs, and debug events for ${names}?`);
        if (!ok) return;
        for (const id of targets) {
          await fetch(`/api/apps/${encodeURIComponent(id)}/data`, { method: "DELETE" });
        }
      }
      await this.refresh();
      document.dispatchEvent(new CustomEvent("vl:scope-change", { detail: this.get() }));
    },

    watch(onState) {
      const report = typeof onState === "function" ? onState : () => {};
      const proto = location.protocol === "https:" ? "wss" : "ws";
      let socket;
      try {
        socket = new WebSocket(`${proto}://${location.host}/ws/debug?client_id=__apps__`);
      } catch {
        report("offline");
        setTimeout(() => this.watch(onState), 3000);
        return;
      }
      socket.addEventListener("open", () => report("live"));
      socket.addEventListener("message", (event) => {
        let msg;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg && msg.type === "apps-changed") this.refresh();
      });
      socket.addEventListener("close", () => {
        report("reconnecting");
        setTimeout(() => this.watch(onState), 3000);
      });
    },
  };

  function setConnection(state) {
    const dot = document.getElementById("live-dot");
    const label = document.getElementById("live-label");
    if (!dot || !label) return;
    dot.classList.toggle("on", state === "live");
    dot.classList.toggle("off", state === "reconnecting" || state === "offline");
    label.textContent = state;
  }

  window.VLScope = Scope;

  document.addEventListener("DOMContentLoaded", () => {
    const scopeSelect = document.getElementById("scope-select");
    if (scopeSelect) {
      scopeSelect.addEventListener("change", () => Scope.set(scopeSelect.value));
      document.getElementById("scope-clear")?.addEventListener("click", () => Scope.clear());
      Scope.render();
      Scope.refresh();
      setInterval(() => Scope.refresh(), 15000);
    }
    // Apps socket is open on every page. It owns the sidebar indicator so
    // Rules, Debug, Agent, and Setup show the same state as Network and Logs.
    Scope.watch(setConnection);

    const themeButton = document.getElementById("theme-toggle");
    if (themeButton) {
      syncThemeButton(themeButton);
      themeButton.addEventListener("click", () => {
        const next = currentTheme() === "dark" ? "light" : "dark";
        root.dataset.theme = next;
        store("vl-theme", next);
        syncThemeButton(themeButton);
      });
      lightQuery.addEventListener("change", () => syncThemeButton(themeButton));
    }

    const railButton = document.getElementById("rail-toggle");
    if (railButton) {
      syncRailButton(railButton);
      railButton.addEventListener("click", () => {
        const collapsed = root.dataset.rail === "collapsed";
        if (collapsed) {
          delete root.dataset.rail;
        } else {
          root.dataset.rail = "collapsed";
        }
        store("vl-rail", collapsed ? "expanded" : "collapsed");
        syncRailButton(railButton);
      });
    }
  });
})();
