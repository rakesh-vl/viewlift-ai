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
    gone: new Map(),
    asking: false,

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
      if (app.crashed) return "crashed";
      return app.state || (app.online ? "online" : "offline");
    },

    /* The picker lists apps that are running now. A crashed app stays so its report is one click away. */
    listed(app) {
      const state = this.stateOf(app);
      return state === "online" || state === "background" || state === "crashed";
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
        const before = this.loaded ? this.byId : null;
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
        if (before) this.noteDisconnects(before);
      } catch {
        /* dashboard server restarting: keep the last known list */
      }
    },

    /* An app that was running and went silent. A crash or a paused debugger keeps its data for diagnosis. */
    noteDisconnects(before) {
      for (const [id, app] of this.byId) {
        const was = before.get(id);
        if (!was) continue;
        const prev = this.stateOf(was);
        if ((prev === "online" || prev === "background") && this.stateOf(app) === "offline" && !app.debugger_attached) {
          this.gone.set(id, app);
        }
      }
      this.offerCleanup();
    },

    /* One tab asks. The claim in localStorage stops every open tab from asking the same question. */
    claimPrompt(ids) {
      const key = `vl-disconnect:${ids.slice().sort().join(",")}`;
      try {
        const last = Number(localStorage.getItem(key) || "0");
        if (Date.now() - last < 60000) return false;
        localStorage.setItem(key, String(Date.now()));
      } catch {
        /* storage disabled: this tab asks on its own */
      }
      return true;
    },

    async offerCleanup() {
      if (!this.gone.size || this.asking || document.visibilityState !== "visible") return;
      const apps = Array.from(this.gone.values()).filter((app) => {
        const now = this.byId.get(app.id);
        return now && this.stateOf(now) === "offline";
      });
      this.gone.clear();
      if (!apps.length || !this.claimPrompt(apps.map((app) => app.id))) return;
      this.asking = true;
      const names = apps.map((app) => `• ${app.app_name} on ${app.device_name || "unknown device"}`).join("\n");
      const ok = window.confirm(
        `${apps.length === 1 ? "This app" : "These apps"} disconnected:\n${names}\n\n`
          + "Delete their captured requests, logs, debug events, crash replays, and alerts from this Mac? "
          + "This cannot be undone."
      );
      try {
        if (ok) await this.purge(apps.map((app) => app.id));
      } finally {
        this.asking = false;
      }
    },

    async purge(ids) {
      for (const id of ids) {
        await fetch(`/api/apps/${encodeURIComponent(id)}/data`, { method: "DELETE" });
      }
      Alerts.forgetClients(ids);
      await this.refresh();
      const scope = this.get();
      const stale =
        (scope.kind === "app" && !this.byId.has(scope.clientId))
        || (scope.kind === "device" && !Array.from(this.byId.values()).some((app) => app.device_key === scope.deviceKey));
      if (stale) this.set("", { silent: true });
      document.dispatchEvent(new CustomEvent("vl:scope-change", { detail: this.get() }));
    },

    render() {
      const select = document.getElementById("scope-select");
      if (!select) return;
      const groups = this.devices.map((device) => {
        const apps = (device.apps || []).filter((app) => this.listed(app));
        if (!apps.length) return "";
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
          const glyph = state === "online" ? "\u25CF" : state === "background" ? "\u25D0" : state === "crashed" ? "\u2716" : "\u25CB";
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
      const listed = Array.from(this.byId.values()).filter((a) => this.listed(a)).length;
      const parts = [listed ? `${online} of ${listed} app${listed === 1 ? "" : "s"} online` : "No app connected"];
      const backgrounded = Array.from(this.byId.values()).filter((a) => this.stateOf(a) === "background").length;
      if (backgrounded) parts.push(`${backgrounded} in background`);
      if (proxyNote) parts.push(proxyNote);
      if (sharedDevice) parts.push("several apps share one device; pick one to isolate its traffic");
      const app = this.selectedApp();
      if (app && app.counts) parts.push(`${app.counts.flows} flows, ${app.counts.logs} logs stored`);
      if (app && app.hint) parts.push(app.hint);
      meta.textContent = parts.join(" · ");
    },

    async clear() {
      const scope = this.get();
      if (scope.kind === "all") {
        const ok = window.confirm(
          "Clear captured flows, logs, debug events, crash replays, and alerts for every app? This cannot be undone."
        );
        if (!ok) return;
        await fetch("/api/apps/data", { method: "DELETE" });
        Alerts.clear();
        await this.refresh();
        document.dispatchEvent(new CustomEvent("vl:scope-change", { detail: this.get() }));
        return;
      }
      const targets =
        scope.kind === "app"
          ? [scope.clientId]
          : Array.from(this.byId.values())
              .filter((app) => app.device_key === scope.deviceKey)
              .map((app) => app.id);
      const names = targets.map((id) => (this.app(id) || { app_name: id }).app_name).join(", ");
      const ok = window.confirm(
        `Clear captured flows, logs, debug events, crash replays, and alerts for ${names}? This cannot be undone.`
      );
      if (!ok) return;
      await this.purge(targets);
    },

    /* A crash is shown on every page, so it is not missed while looking at network or logs. */
    announceCrash(msg) {
      if (!inScope(msg.client_id)) return;
      const payload = (msg.event && msg.event.payload) || {};
      const id = (msg.event && msg.event.id) || "";
      const ready = (payload.replay || {}).status === "ready";
      // A crash from the previous launch stays on the Hangs tab. Toast the live one, and the replay when it arrives.
      if (!ready && payload.previous_session) {
        Alerts.remember(`crash:${id}`);
        return;
      }
      const site = (payload.diagnosis || {}).crash_site || {};
      const where = site.file ? `${site.file}${site.line != null ? `:${site.line}` : ""}` : "";
      const headline = (payload.diagnosis || {}).headline || payload.name || "Fatal error";
      Alerts.push({
        key: ready ? `crash-ready:${id}` : `crash:${id}`,
        type: "crash",
        client: msg.client_id,
        tone: "err",
        kicker: ready ? "Replay ready" : "Crash",
        title: appLabel(msg.client_id),
        body: ready
          ? clip(`Last 10 seconds before the crash${where ? ` · ${where}` : ""}`, 180)
          : clip(`${headline}${where ? ` · ${where}` : ""}`, 180),
        href: "/debug#hangs",
        dest: "Debug · Hangs",
      });
    },

    announceIssue(msg) {
      const event = msg.event || {};
      if (!inScope(event.client_id)) return;
      const payload = event.payload || {};
      const name = appLabel(event.client_id);
      if (event.module === "hang" && event.kind === "detected") {
        const ms = Math.round(Number(payload.duration_ms) || 0);
        const screen = payload.screen_title || payload.screen_class || "main thread";
        const suspect = payload.suspect || {};
        const loc = suspect.file ? `${suspect.file}${suspect.line != null ? `:${suspect.line}` : ""}` : "";
        Alerts.push({
          key: `hang:${event.id || payload.id || ""}`,
          type: "hang",
          client: event.client_id,
          tone: "warn",
          kicker: "Hang",
          title: `${name} · ${ms} ms`,
          body: clip(`${screen}${loc ? ` · ${loc}` : ""}`, 180),
          href: "/debug#hangs",
          dest: "Debug · Hangs",
        });
        return;
      }
      if (event.module === "memory" && event.kind === "leak") {
        Alerts.push({
          key: `leak:${event.id || payload.object_id || payload.id || ""}`,
          type: "leak",
          client: event.client_id,
          tone: "warn",
          kicker: "Memory leak",
          title: name,
          body: clip(payload.class || "An object stayed alive after it should have been released", 180),
          href: "/debug#memory",
          dest: "Debug · Memory",
        });
        return;
      }
      if (event.module === "playback" && event.kind === "event") {
        const play = payload.kind;
        const failed = play === "item_failed" || play === "item_error_log" || (play === "drm" && payload.phase === "failed");
        if (!failed) return;
        const detail = payload.error_domain
          ? `${payload.error_domain}${payload.error_code != null ? ` ${payload.error_code}` : ""}`
          : play;
        Alerts.push({
          key: `play:${event.id || payload.playback_id || play}:${payload.error_code || ""}`,
          type: "playback",
          client: event.client_id,
          tone: "err",
          kicker: "Playback failed",
          title: name,
          body: clip(detail, 180),
          href: "/debug#ott",
          dest: "Debug · OTT",
        });
      }
    },

    announceFlow(msg) {
      const flow = msg.flow;
      if (!flow || !inScope(flow.client_id)) return;
      const status = Number(flow.status);
      const failed = flow.tls_failed || flow.error || (Number.isFinite(status) && status >= 400);
      if (!failed) return;
      const where = `${flow.method || "GET"} ${flow.host || ""}${flow.path || ""}`.trim();
      const statusText = flow.tls_failed ? "TLS failed" : (flow.status || "failed");
      Alerts.push({
        key: `flow:${flow.id}`,
        type: "request",
        client: flow.client_id,
        tone: "err",
        kicker: "Request failed",
        title: `${appLabel(flow.client_id)} · ${statusText}`,
        body: clip(flow.error || where, 180),
        href: `/flows/${encodeURIComponent(flow.id)}`,
        dest: "Network",
      });
    },

    announceLog(msg) {
      const log = msg.log;
      if (!log || !inScope(log.client_id)) return;
      if (log.level !== "error" && log.level !== "fault") return;
      // os_log binary dumps the SDK tees to stderr are not readable errors. They stay on the Logs page.
      if (String(log.message || "").trim().startsWith("OSLOG-")) return;
      Alerts.push({
        key: `log:${log.id}`,
        type: "console",
        client: log.client_id,
        tone: "err",
        kicker: log.level === "fault" ? "Fault" : "Console error",
        title: appLabel(log.client_id),
        body: clip(log.message, 180),
        href: `/logs?id=${encodeURIComponent(log.id)}`,
        dest: "Logs",
      });
    },

    watch(onState) {
      window.VLLive.connect("/ws/debug?client_id=__apps__", {
        onState,
        onResync: () => this.refresh(),
        onMessage: (msg) => {
          if (msg.type === "apps-changed") this.refresh();
          if (msg.type === "debug-crash") this.announceCrash(msg);
          if (msg.type === "debug-event") this.announceIssue(msg);
        },
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

  function inScope(clientId) {
    const scope = window.VLScope;
    if (!scope || !scope.loaded) return true;
    if (!clientId) return scope.get().kind === "all";
    return scope.matches(clientId);
  }

  function appLabel(clientId) {
    const app = window.VLScope && window.VLScope.app(clientId);
    return app ? app.app_name : "App";
  }

  function clip(text, max) {
    const clean = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    if (clean.length <= max) return clean;
    return `${clean.slice(0, max - 1)}…`;
  }

  const ALERT_TYPES = [
    { id: "crash", label: "Crashes" },
    { id: "hang", label: "Hangs" },
    { id: "leak", label: "Memory leaks" },
    { id: "playback", label: "Playback" },
    { id: "request", label: "Failed requests" },
    { id: "console", label: "Console errors" },
  ];

  /* Failed requests and console errors that land within this window share one row in the center. */
  const BURST_MS = 3000;
  const MAX_TOASTS = 4;

  function loadNotifySettings() {
    const types = {};
    for (const type of ALERT_TYPES) types[type.id] = true;
    try {
      const raw = JSON.parse(localStorage.getItem("vl-notify") || "null");
      if (!raw || typeof raw !== "object") return { enabled: true, group: true, types };
      const saved = raw.types && typeof raw.types === "object" ? raw.types : {};
      for (const type of ALERT_TYPES) {
        if (typeof saved[type.id] === "boolean") types[type.id] = saved[type.id];
      }
      return { enabled: raw.enabled !== false, group: raw.group !== false, types };
    } catch {
      return { enabled: true, group: true, types };
    }
  }

  function loadNotifyHistory() {
    try {
      const raw = JSON.parse(sessionStorage.getItem("vl-notify-log") || "[]");
      return Array.isArray(raw) ? raw.slice(0, 40) : [];
    } catch {
      return [];
    }
  }

  /* Bottom-right alerts plus the notification center in the scope bar. */
  const Alerts = {
    seen: new Map(),
    stack: null,
    open: false,
    unread: 0,
    settings: loadNotifySettings(),
    history: loadNotifyHistory(),

    remember(key) {
      if (!key || this.seen.has(key)) return false;
      this.seen.set(key, Date.now());
      if (this.seen.size > 200) this.seen.delete(this.seen.keys().next().value);
      return true;
    },

    allowed(item) {
      if (!this.settings.enabled) return false;
      return this.settings.types[item.type] !== false;
    },

    saveSettings() {
      try {
        localStorage.setItem("vl-notify", JSON.stringify(this.settings));
      } catch {
        /* storage disabled: the choice lasts for this page only */
      }
    },

    saveHistory() {
      try {
        sessionStorage.setItem("vl-notify-log", JSON.stringify(this.history.slice(0, 40)));
      } catch {
        /* storage disabled: the list lasts for this page only */
      }
    },

    push(item) {
      if (!this.allowed(item) || !item.key || !this.remember(item.key)) return;
      this.record(item);
      const stack = this.ensure();
      if (this.settings.group) {
        const existing = stack.querySelector(`[data-alert-type="${item.type}"]:not(.leaving)`);
        if (existing) {
          this.fill(existing, item, Number(existing.dataset.count || "1") + 1);
          this.arm(existing);
          return;
        }
      }
      const card = this.create(item);
      stack.append(card);
      liveCards(stack).slice(0, -MAX_TOASTS).forEach((old) => dismissAlert(old));
      window.requestAnimationFrame(() => card.classList.add("show"));
      this.arm(card);
    },

    record(item) {
      const now = Date.now();
      const last = this.history[0];
      // A busy app can fail a request or print an error many times a second. One row per burst; the count says how many.
      const burst = (item.type === "request" || item.type === "console")
        && last && last.type === item.type && last.client === (item.client || "") && now - last.at < BURST_MS;
      if (burst) {
        last.count = (last.count || 1) + 1;
        last.title = item.title;
        last.body = item.body;
        last.href = item.href;
        last.at = now;
      } else {
        this.history.unshift({
          type: item.type,
          client: item.client || "",
          title: item.title,
          body: item.body,
          href: item.href,
          dest: item.dest,
          count: 1,
          at: now,
        });
      }
      if (this.history.length > 40) this.history.length = 40;
      this.saveHistory();
      if (this.open) {
        this.paintList();
        return;
      }
      this.unread += 1;
      this.paintBadge();
    },

    create(item) {
      const card = document.createElement("div");
      card.className = `alert-toast alert-${item.tone === "warn" ? "warn" : "err"}`;
      card.dataset.alertType = item.type;
      const link = document.createElement("a");
      link.className = "alert-link";
      const close = document.createElement("button");
      close.type = "button";
      close.className = "alert-close";
      close.setAttribute("aria-label", "Dismiss alert");
      close.textContent = "\u00d7";
      close.addEventListener("click", () => {
        window.clearTimeout(Number(card.dataset.timer));
        dismissAlert(card);
      });
      card.append(link, close);
      this.fill(card, item, 1);
      return card;
    },

    fill(card, item, count) {
      card.dataset.count = String(count);
      card.dataset.client = item.client || "";
      const link = card.querySelector(".alert-link");
      link.href = item.href;
      const kicker = count > 1 ? `${item.kicker} · ${count}` : item.kicker;
      link.innerHTML = `<span class="alert-kicker">${escapeHtml(kicker)}</span>`
        + `<span class="alert-title">${escapeHtml(item.title)}</span>`
        + `<span class="alert-body">${escapeHtml(item.body)}</span>`
        + `<span class="alert-dest">${escapeHtml(item.dest)}</span>`;
    },

    arm(card) {
      window.clearTimeout(Number(card.dataset.timer));
      const timer = window.setTimeout(() => dismissAlert(card), 12000);
      card.dataset.timer = String(timer);
    },

    ensure() {
      if (this.stack && this.stack.isConnected) return this.stack;
      const stack = document.createElement("div");
      stack.id = "alert-stack";
      stack.className = "alert-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.append(stack);
      this.stack = stack;
      return stack;
    },

    setOpen(open) {
      this.open = open;
      const panel = document.getElementById("notify-panel");
      const button = document.getElementById("notify-toggle");
      if (panel) panel.hidden = !open;
      if (button) button.setAttribute("aria-expanded", String(open));
      if (open) {
        this.unread = 0;
        this.paintBadge();
        this.paintList();
      }
    },

    paintBadge() {
      const badge = document.getElementById("notify-count");
      if (!badge) return;
      const count = this.unread;
      badge.hidden = count < 1;
      badge.textContent = count > 9 ? "9+" : String(count);
    },

    paintControls() {
      const enabled = document.getElementById("notify-enabled");
      const group = document.getElementById("notify-group");
      if (enabled) enabled.checked = this.settings.enabled;
      if (group) group.checked = this.settings.group;
      const types = document.getElementById("notify-types");
      if (!types || types.childElementCount) return;
      types.innerHTML = ALERT_TYPES.map((type) => (
        `<label class="notify-switch"><input type="checkbox" data-notify-type="${type.id}"${this.settings.types[type.id] ? " checked" : ""}> ${escapeHtml(type.label)}</label>`
      )).join("");
    },

    paintList() {
      const list = document.getElementById("notify-list");
      if (!list) return;
      if (!this.settings.enabled) {
        list.innerHTML = `<p class="muted">Alerts are off. Nothing new is collected.</p>`;
        return;
      }
      if (!this.history.length) {
        list.innerHTML = `<p class="muted">No alerts yet.</p>`;
        return;
      }
      if (!this.settings.group) {
        list.innerHTML = this.history.map((item) => this.row(item)).join("");
        return;
      }
      const groups = ALERT_TYPES.map((type) => {
        const items = this.history.filter((item) => item.type === type.id);
        if (!items.length) return "";
        const total = items.reduce((sum, item) => sum + (item.count || 1), 0);
        return `<section class="notify-group"><h3>${escapeHtml(type.label)} · ${total}</h3>${items.map((item) => this.row(item)).join("")}</section>`;
      }).join("");
      list.innerHTML = groups || `<p class="muted">No alerts yet.</p>`;
    },

    row(item) {
      const when = new Date(item.at).toLocaleTimeString();
      const times = item.count > 1 ? ` · ${item.count} times` : "";
      return `<a class="notify-item" href="${escapeHtml(item.href)}"><span class="notify-when">${escapeHtml(when + times)}</span><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.body)}</span><span class="alert-dest">${escapeHtml(item.dest)}</span></a>`;
    },

    clear() {
      this.history = [];
      this.unread = 0;
      this.saveHistory();
      this.paintBadge();
      this.paintList();
      if (this.stack) [...this.stack.children].forEach((card) => dismissAlert(card));
    },

    /* Data for these apps was deleted, so their alerts would link to nothing. */
    forgetClients(ids) {
      const drop = new Set(ids);
      this.history = this.history.filter((item) => !drop.has(item.client));
      this.unread = Math.min(this.unread, this.history.length);
      this.saveHistory();
      this.paintBadge();
      this.paintList();
      if (this.stack) {
        [...this.stack.children].filter((card) => drop.has(card.dataset.client)).forEach((card) => dismissAlert(card));
      }
    },

    bind() {
      const button = document.getElementById("notify-toggle");
      const panel = document.getElementById("notify-panel");
      if (!button || !panel) return;
      this.paintControls();
      this.paintList();
      button.addEventListener("click", () => this.setOpen(!this.open));
      document.getElementById("notify-enabled")?.addEventListener("change", (event) => {
        this.settings.enabled = event.target.checked;
        this.saveSettings();
        if (!this.settings.enabled && this.stack) {
          [...this.stack.children].forEach((card) => dismissAlert(card));
        }
        this.paintList();
      });
      document.getElementById("notify-group")?.addEventListener("change", (event) => {
        this.settings.group = event.target.checked;
        this.saveSettings();
        this.paintList();
      });
      document.getElementById("notify-types")?.addEventListener("change", (event) => {
        const input = event.target.closest("[data-notify-type]");
        if (!input) return;
        this.settings.types[input.dataset.notifyType] = input.checked;
        this.saveSettings();
        if (!input.checked && this.stack) {
          this.stack.querySelectorAll(`[data-alert-type="${input.dataset.notifyType}"]`)
            .forEach((card) => dismissAlert(card));
        }
      });
      document.getElementById("notify-clear")?.addEventListener("click", () => this.clear());
      document.addEventListener("click", (event) => {
        if (!this.open || event.target.closest("#notify")) return;
        this.setOpen(false);
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && this.open) this.setOpen(false);
      });
    },
  };

  function dismissAlert(card) {
    if (!card || !card.isConnected || card.classList.contains("leaving")) return;
    // `leaving` takes the card out of grouping and of the visible count right away. The node goes after the fade.
    card.classList.add("leaving");
    card.classList.remove("show");
    window.clearTimeout(Number(card.dataset.timer));
    const remove = () => card.remove();
    card.addEventListener("transitionend", remove, { once: true });
    window.setTimeout(remove, 280);
  }

  function liveCards(stack) {
    return [...stack.children].filter((card) => !card.classList.contains("leaving"));
  }

  function watchFeed(path, onMessage) {
    window.VLLive.connect(path, { onMessage });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const scopeSelect = document.getElementById("scope-select");
    if (scopeSelect) {
      scopeSelect.addEventListener("change", () => Scope.set(scopeSelect.value));
      document.getElementById("scope-clear")?.addEventListener("click", () => Scope.clear());
      Scope.render();
      Scope.refresh();
      setInterval(() => Scope.refresh(), 15000);
    }
    document.addEventListener("visibilitychange", () => Scope.offerCleanup());
    // Apps socket is open on every page. It owns the sidebar indicator so
    // Rules, Debug, Agent, and Setup show the same state as Network and Logs.
    Scope.watch(setConnection);
    Alerts.bind();
    watchFeed("/ws/flows", (msg) => {
      if (msg && msg.type === "flow") Scope.announceFlow(msg);
    });
    watchFeed("/ws/logs", (msg) => {
      if (msg && msg.type === "log") Scope.announceLog(msg);
    });

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
