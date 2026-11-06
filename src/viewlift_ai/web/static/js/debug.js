(() => {
  const MUTATING = new Set([
    "ui.highlight", "ui.edit", "storage.defaults_set", "storage.file_write",
    "storage.snapshot_create", "storage.snapshot_restore", "feature_flags.set",
    "feature_flags.reset", "macro.record_start", "macro.record_stop", "macro.replay",
    "push.local",
  ]);

  const state = {
    clientId: "",
    client: null,
    samples: [],
    selectedView: null,
    hierarchy: [],
    collapsed: new Set(),
    hierarchyZoom: 1,
    hierarchyParents: new Map(),
    hierarchyShot: null,
    hierarchyDrawn: [],
    describeId: "",
    hierarchyView: { yaw: 0.55, pitch: 0.2, spacing: 18, panX: 0, panY: 0 },
    macro: null,
    bugImage: null,
    drawing: false,
    analytics: null,
    analyticsBeaconUrls: { user: "", player: "" },
    selectedAnalyticsId: "",
    analyticsFollow: false,
    analyticsFollowSig: "",
    analyticsFlowWs: null,
    storagePath: "",
    storageDbPath: "",
    selectedStoragePath: "",
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const escapeAttr = (value) => escapeHtml(value).replaceAll("'", "&#39;");
  const humanize = (key) => String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/^\w/, (char) => char.toUpperCase());

  const Debug = {
    async init() {
      this.bindTabs();
      this.bindCommands();
      this.bindForms();
      this.bindBugCanvas();
      document.getElementById("debug-refresh").addEventListener("click", async () => {
        if (window.VLScope) await window.VLScope.refresh();
        await this.loadClients();
      });
      document.addEventListener("vl:scope-change", async () => {
        await this.loadClients();
        this.loadAnalyticsTraffic();
        this.loadWaterfall();
      });
      document.addEventListener("vl:apps-change", () => this.loadClients());
      document.getElementById("macro-replay").addEventListener("click", () => {
        this.sendCommand("macro.replay", state.macro || { actions: [] });
      });
      document.getElementById("ott-spm-refresh")?.addEventListener("click", () => this.loadSpm());
      document.getElementById("analytics-refresh")?.addEventListener("click", () => this.loadAnalyticsTraffic());
      ["af-kind", "af-method", "af-status", "af-host"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", () => {
          this.renderAnalyticsTraffic();
          this.followAnalytics();
        });
      });
      document.getElementById("af-text")?.addEventListener("input", () => {
        this.renderAnalyticsTraffic();
        this.followAnalytics();
      });
      document.getElementById("analytics-follow")?.addEventListener("click", () => {
        this.setAnalyticsFollow(!state.analyticsFollow);
      });
      document.getElementById("analytics-rows")?.addEventListener("click", (event) => {
        const row = event.target.closest("tr[data-id]");
        if (!row) return;
        this.setAnalyticsFollow(false);
        this.selectAnalytics(row.dataset.id);
      });
      this.bindStorage();
      document.getElementById("ui-expand").addEventListener("click", () => {
        state.collapsed.clear();
        this.drawHierarchy();
      });
      document.getElementById("ui-collapse").addEventListener("click", () => {
        state.collapsed = new Set(this.hierarchyNodes()
          .filter(({ node }) => (node.children || []).length)
          .map(({ node }) => String(node.id)));
        this.drawHierarchy();
      });
      document.getElementById("ui-zoom-in").addEventListener("click", () => {
        state.hierarchyZoom = Math.min(6, state.hierarchyZoom * 1.25);
        this.drawHierarchy3D();
      });
      document.getElementById("ui-zoom-out").addEventListener("click", () => {
        state.hierarchyZoom = Math.max(0.25, state.hierarchyZoom / 1.25);
        this.drawHierarchy3D();
      });
      this.bindHierarchyCanvas();
      await this.loadClients();
      await this.loadProfiles();
      await this.loadWaterfall();
      await this.loadAnalyticsTraffic();
      this.connectWebSocket();
    },

    bindTabs() {
      const tabs = document.getElementById("debug-tabs");
      tabs.addEventListener("click", (event) => {
        const button = event.target.closest("[data-debug-tab]");
        if (!button) return;
        document.querySelectorAll("[data-debug-tab]").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        document.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.classList.toggle("hidden", panel.dataset.panel !== button.dataset.debugTab);
        });
        if (button.dataset.debugTab === "storage") this.openStorage(state.storagePath || "");
        if (button.dataset.debugTab === "analytics" && state.analyticsFollow) {
          this.ensureAnalyticsFlowSocket();
          this.loadAnalyticsTraffic();
        } else if (state.analyticsFollow) {
          this.closeAnalyticsFlowSocket();
        }
      });
      tabs.querySelector("[data-debug-tab]")?.classList.add("active");
    },

    bindCommands() {
      document.body.addEventListener("click", (event) => {
        const button = event.target.closest("[data-command]");
        if (button) this.sendCommand(button.dataset.command, {});
      });
    },

    bindForms() {
      document.querySelectorAll("[data-command-form]").forEach((form) => {
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          const payload = {};
          for (const [key, raw] of new FormData(form).entries()) {
            const input = form.elements.namedItem(key);
            if (key === "paths") {
              payload[key] = String(raw).split(",").map((item) => item.trim()).filter(Boolean);
            } else if (input?.type === "number") {
              payload[key] = Number(raw);
            } else {
              payload[key] = raw;
            }
          }
          this.sendCommand(form.dataset.commandForm, payload);
        });
      });
      document.getElementById("deeplink-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const response = await fetch("/api/debug/deeplink", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            udid: document.getElementById("deeplink-udid").value.trim(),
            url: document.getElementById("deeplink-url").value.trim(),
            client_id: state.clientId || null,
          }),
        });
        const data = await response.json();
        document.getElementById("deeplink-status").textContent = data.detail || "Failed";
      });
    },

    /*
     * Debug commands always target exactly one app. The global scope picks it; with
     * "All apps" or a device scope, the only online app in that scope is used, otherwise
     * the user must choose so two apps on one simulator never receive each other's commands.
     */
    targetClientId() {
      const scope = window.VLScope;
      if (!scope) return "";
      const selected = scope.clientId();
      if (selected) return selected;
      const online = scope.onlineApps().filter((app) => scope.matches(app.id));
      return online.length === 1 ? online[0].id : "";
    },

    async loadClients() {
      const previous = state.clientId;
      const next = this.targetClientId();
      if (next !== previous) this.resetClientState();
      state.clientId = next;
      state.client = null;
      if (next) {
        try {
          const response = await fetch(`/api/debug/app?client_id=${encodeURIComponent(next)}`);
          if (response.ok) state.client = (await response.json()).client;
        } catch (_) {
          state.client = null;
        }
        if (!state.client && window.VLScope) state.client = window.VLScope.app(next);
      }
      this.renderClientStatus();
      if (next !== previous) {
        if (state.clientId) await this.loadEvents();
        await this.loadSpm();
        const storageOpen = document.querySelector('[data-panel="storage"]:not(.hidden)');
        if (storageOpen && state.clientId) this.openStorage("");
      }
    },

    resetClientState() {
      Object.assign(state, {
        samples: [],
        selectedView: null,
        hierarchy: [],
        collapsed: new Set(),
        hierarchyParents: new Map(),
        hierarchyShot: null,
        hierarchyDrawn: [],
        describeId: "",
        macro: null,
        bugImage: null,
        analytics: null,
        analyticsBeaconUrls: { user: "", player: "" },
        selectedAnalyticsId: "",
        storagePath: "",
        storageDbPath: "",
        selectedStoragePath: "",
      });
      const clearIds = [
        "ott-summary", "ott-sections", "ott-spm", "ott-frameworks", "analytics-clients",
        "hangs-result", "performance-metrics", "memory-result", "ui-tree", "ui-properties",
        "swiftui-result", "assets-result", "accessibility-result", "storage-inspector",
        "storage-files", "flags-result", "bug-result", "macro-result", "runtime-result", "push-result",
      ];
      clearIds.forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.innerHTML = "";
      });
      ["performance-chart", "memory-graph", "ui-3d", "bug-canvas"].forEach((id) => {
        const canvas = document.getElementById(id);
        if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
      });
      const clients = document.getElementById("analytics-clients");
      if (clients) clients.style.display = "none";
      const detail = document.getElementById("analytics-detail");
      if (detail) {
        detail.innerHTML = `<p class="muted empty">Select a beacon or client call to see params, request, and body.</p>`;
      }
      const title = document.getElementById("analytics-detail-title");
      if (title) title.textContent = "Detail";
      const count = document.getElementById("analytics-count");
      if (count) count.textContent = "0";
      const crumb = document.getElementById("storage-crumb");
      if (crumb) crumb.innerHTML = "";
      const sqlPath = document.getElementById("storage-sql-path");
      if (sqlPath) sqlPath.textContent = "Select a database";
    },

    renderChooser() {
      const scope = window.VLScope;
      const node = document.getElementById("debug-client-status");
      const online = scope ? scope.onlineApps().filter((app) => scope.matches(app.id)) : [];
      if (!online.length) {
        node.textContent = "No debug app is online. Run a debug build that links ViewliftNetworkCapture.";
        return;
      }
      node.innerHTML = `<span>${online.length} apps are online. Pick one to run debug tools:</span>`
        + online.map((app) => `<button type="button" class="btn ghost" data-pick-app="${escapeHtml(app.id)}">`
          + `${escapeHtml(app.app_name)} · ${escapeHtml(app.device_name || app.platform || "")}</button>`).join("");
      node.querySelectorAll("[data-pick-app]").forEach((button) => {
        button.addEventListener("click", () => scope.set(`c:${button.dataset.pickApp}`));
      });
    },

    renderClientStatus() {
      const node = document.getElementById("debug-client-status");
      const name = document.getElementById("debug-client-name");
      this.renderIdentity();
      if (!state.client) {
        if (name) name.textContent = "No app selected";
        this.renderChooser();
        return;
      }
      if (name) {
        const where = state.client.device_name || state.client.device_model || state.client.platform || "";
        name.innerHTML = `${window.VLScope ? window.VLScope.badge(state.client.id) : ""}`
          + `${escapeHtml(state.client.app_name)}${where ? ` <span class="muted">on ${escapeHtml(where)}</span>` : ""}`;
      }
      const udidInput = document.getElementById("deeplink-udid");
      if (udidInput && !udidInput.value && state.client.is_simulator && state.client.device_key) {
        udidInput.value = state.client.device_key;
      }
      const capabilities = Object.entries(state.client.capabilities || {})
        .map(([name, status]) => `<span class="capability ${escapeHtml(status)}">${escapeHtml(name)}: ${escapeHtml(status)}</span>`)
        .join(" · ");
      node.innerHTML = capabilities;
      const schemes = state.client.url_schemes || [];
      const deeplink = document.getElementById("deeplink-url");
      if (deeplink && schemes.length) deeplink.placeholder = `${schemes[0]}://`;
    },

    renderIdentity() {
      const target = document.getElementById("app-identity");
      if (!target) return;
      const client = state.client;
      if (!client) {
        target.innerHTML = "";
        return;
      }
      const rows = [
        ["App", client.app_name],
        ["Device", [client.device_name, client.device_model].filter(Boolean).join(" · ")],
        ["Bundle id", client.bundle_id],
        ["Version", [client.app_version, client.build ? `(${client.build})` : ""].filter(Boolean).join(" ")],
        ["Platform", [client.platform, client.os_version].filter(Boolean).join(" ")],
        ["Executable", client.executable],
        ["Capture SDK", client.sdk_version],
      ].filter(([, value]) => value);
      target.innerHTML = rows
        .map(([label, value]) => `<div class="metric"><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
        .join("");
    },

    async sendCommand(name, payload) {
      if (!state.clientId) {
        alert("Choose one app in Scope first. Several apps may be running on this device.");
        return;
      }
      let confirmation = null;
      if (MUTATING.has(name)) {
        if (!confirm(`Run ${name} on ${state.client?.app_name || "the app"}?`)) return;
        const tokenResponse = await fetch("/api/debug/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: state.clientId, command: name }),
        });
        const token = await tokenResponse.json();
        if (!tokenResponse.ok) {
          alert(token.detail || "Confirmation failed");
          return;
        }
        confirmation = token.token;
      }
      const response = await fetch("/api/debug/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: state.clientId,
          name,
          payload,
          confirmation,
        }),
      });
      const data = await response.json();
      if (!response.ok) alert(data.detail || "Command failed");
    },

    connectWebSocket() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/ws/debug`);
      socket.addEventListener("message", (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (data.type === "debug-client" && data.client?.id === state.clientId) this.loadClients();
        if (data.type === "debug-event" && data.event?.client_id === state.clientId) {
          this.handleEvent(data.event);
        }
      });
      socket.addEventListener("close", () => setTimeout(() => this.connectWebSocket(), 1500));
    },

    async loadEvents() {
      if (!state.clientId) return;
      const data = await (await fetch(`/api/debug/events?client_id=${encodeURIComponent(state.clientId)}&limit=300`)).json();
      const events = (data.events || []).reverse();
      state.samples = [];
      for (const event of events) this.handleEvent(event);
    },

    handleEvent(event) {
      if (event.module === "performance" && event.kind === "sample") {
        state.samples.push(event.payload);
        if (state.samples.length > 120) state.samples.shift();
        this.renderPerformance();
        return;
      }
      if (event.kind !== "command-result") return;
      const data = event.payload || {};
      if (!data.ok) {
        if (data.name === "ui.describe") {
          this.renderDescribe({ found: false, error: data.error || "Command failed" });
          return;
        }
        this.renderError(event.module, data.error || "Command failed");
        return;
      }
      this.renderResult(data.name, data.result || {});
    },

    renderResult(name, result) {
      if (name === "ott.snapshot") {
        this.renderOtt(result);
        this.captureAnalyticsFromOtt(result);
        return;
      }
      if (name === "hang.list" || name === "crash.list") return this.renderStacks(name, result);
      if (name.startsWith("memory.")) return this.renderMemory(result);
      if (name === "ui.hierarchy") return this.renderHierarchy(result);
      if (name === "ui.describe") return this.renderDescribe(result);
      if (name === "ui.screenshot") return this.renderScreenshot(result, "ui-properties");
      if (name === "swiftui.renders") return this.renderTable("swiftui-result", result.components || []);
      if (name === "assets.audit") return this.renderTable("assets-result", result.assets || []);
      if (name === "accessibility.audit") return this.renderTable("accessibility-result", result.findings || []);
      if (name.startsWith("storage.")) return this.renderStorage(name, result);
      if (name.startsWith("feature_flags.")) return this.renderFlags(result.flags || []);
      if (name.startsWith("macro.")) return this.renderMacro(result);
      if (name.startsWith("runtime.")) return this.renderRuntime(name, result);
      if (name === "bug.capture") return this.renderBug(result);
      if (name === "push.local") {
        document.getElementById("push-result").textContent = "Notification scheduled.";
      }
    },

    captureAnalyticsFromOtt(result) {
      const analytics = result.analytics || {};
      const beacons = analytics.beacons || {};
      state.analyticsBeaconUrls = {
        user: beacons.user_beacon_url || "",
        player: beacons.player_beacon_url || "",
      };
      const clients = analytics.registered_clients || [];
      const host = document.getElementById("analytics-clients");
      if (!host) return;
      if (!clients.length && !beacons.user_beacon_url) {
        host.style.display = "none";
        return;
      }
      host.style.display = "block";
      host.innerHTML = `<h3>Analytics clients reported by the app</h3>`
        + this.kvHtml({
          configured: analytics.configured,
          clients: (clients || []).join(", "),
          user_beacon_url: beacons.user_beacon_url || "",
          player_beacon_url: beacons.player_beacon_url || "",
          ...(analytics.flags || {}),
        });
      this.loadAnalyticsTraffic();
    },

    async loadAnalyticsTraffic() {
      const gen = (state.analyticsGen = (state.analyticsGen || 0) + 1);
      const params = new URLSearchParams({ limit: "400" });
      if (state.analyticsBeaconUrls.user) params.set("user_beacon_url", state.analyticsBeaconUrls.user);
      if (state.analyticsBeaconUrls.player) params.set("player_beacon_url", state.analyticsBeaconUrls.player);
      if (window.VLScope) window.VLScope.applyTo(params);
      try {
        const data = await (await fetch(`/api/debug/analytics/traffic?${params}`)).json();
        if (gen !== state.analyticsGen) return;
        state.analytics = data;
        this.renderAnalyticsTraffic();
        this.followAnalytics();
      } catch (error) {
        if (gen !== state.analyticsGen) return;
        const target = document.getElementById("analytics-rows");
        if (target) {
          target.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(error)}</td></tr>`;
        }
      }
    },

    renderAnalyticsTraffic() {
      const data = state.analytics || { counts: {}, total: 0 };
      const counts = data.counts || {};
      const summary = [
        ["Total", String(data.total || 0)],
        ["User beacon", String(counts.user_beacon || 0)],
        ["Player beacon", String(counts.player_beacon || 0)],
        ["Other beacon", String(counts.beacon || 0)],
        ["Clients", String((data.clients || []).length)],
      ];
      const summaryEl = document.getElementById("analytics-summary");
      if (summaryEl) {
        summaryEl.innerHTML = summary
          .map(([label, value]) => `<div class="metric"><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
          .join("");
      }
      const all = this.allAnalyticsRows();
      this.syncAnalyticsSelects(all);
      const rows = this.filterAnalytics(all);
      const count = document.getElementById("analytics-count");
      if (count) count.textContent = String(rows.length);
      const target = document.getElementById("analytics-rows");
      if (!target) return;
      if (!rows.length) {
        target.innerHTML = `<tr><td colspan="7" class="muted">No matching analytics traffic. Play content or navigate, then refresh.</td></tr>`;
        return;
      }
      target.innerHTML = rows.map((row) => `
        <tr data-id="${escapeAttr(row.id)}"${row.id === state.selectedAnalyticsId ? ' class="active"' : ""}>
          <td>${escapeHtml(String(row.created_at || "").replace("T", " ").slice(0, 19))}</td>
          <td>${escapeHtml(this.kindLabel(row.category))}</td>
          <td class="method ${escapeAttr(row.method)}">${escapeHtml(row.method || "")}</td>
          <td>${escapeHtml(row.status ?? "")}</td>
          <td title="${escapeAttr(row.host || "")}">${escapeHtml(row.host || "")}</td>
          <td title="${escapeAttr(row.path || "")}">${escapeHtml(row.path || "")}</td>
          <td title="${escapeAttr(row.event_hint || "")}">${escapeHtml(row.event_hint || "")}</td>
        </tr>`).join("");
    },

    allAnalyticsRows() {
      const data = state.analytics || {};
      return [
        ...(data.user_beacon || []),
        ...(data.player_beacon || []),
        ...(data.beacon || []),
        ...(data.clients || []),
      ].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    },

    kindLabel(category) {
      if (category === "user_beacon") return "User beacon";
      if (category === "player_beacon") return "Player beacon";
      if (category === "beacon") return "Other beacon";
      if (String(category || "").startsWith("client:")) return category.slice("client:".length);
      return category || "";
    },

    syncAnalyticsSelects(rows) {
      const statuses = [...new Set(rows.map((row) => row.status).filter((value) => value != null && value !== ""))]
        .map(String)
        .sort((a, b) => Number(a) - Number(b) || a.localeCompare(b));
      const hosts = [...new Set(rows.map((row) => row.host).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const clients = [...new Set(
        rows.map((row) => row.category).filter((value) => String(value || "").startsWith("client:"))
      )].sort((a, b) => this.kindLabel(a).localeCompare(this.kindLabel(b)));
      this.fillSelect("af-status", statuses.map((value) => ({ value, label: value })));
      this.fillSelect("af-host", hosts.map((value) => ({ value, label: value })));
      this.fillSelect("af-kind", clients.map((value) => ({ value, label: this.kindLabel(value) })), true);
    },

    fillSelect(id, entries, keepExisting) {
      const el = document.getElementById(id);
      if (!el) return;
      const current = el.value;
      const head = [];
      if (keepExisting) {
        [...el.options].forEach((option) => {
          if (!String(option.value).startsWith("client:")) head.push(option.outerHTML);
        });
      } else {
        const first = el.options[0];
        head.push(first && first.value === ""
          ? `<option value="">${escapeHtml(first.textContent)}</option>`
          : `<option value="">Any</option>`);
      }
      el.innerHTML = head.join("") + entries
        .map((entry) => `<option value="${escapeAttr(entry.value)}">${escapeHtml(entry.label)}</option>`)
        .join("");
      const allowed = new Set([...el.options].map((option) => option.value));
      el.value = allowed.has(current) ? current : "";
    },

    filterAnalytics(rows) {
      const kind = document.getElementById("af-kind")?.value || "";
      const method = (document.getElementById("af-method")?.value || "").toUpperCase();
      const status = document.getElementById("af-status")?.value || "";
      const host = document.getElementById("af-host")?.value || "";
      const text = (document.getElementById("af-text")?.value || "").trim().toLowerCase();
      return rows.filter((row) => {
        if (kind && row.category !== kind) return false;
        if (method && String(row.method || "").toUpperCase() !== method) return false;
        if (status && String(row.status ?? "") !== status) return false;
        if (host && row.host !== host) return false;
        if (text) {
          const hay = [
            row.event_hint, row.path, row.host, row.query, JSON.stringify(row.params || {}),
          ].join(" ").toLowerCase();
          if (!hay.includes(text)) return false;
        }
        return true;
      });
    },

    async selectAnalytics(id) {
      const gen = (state.analyticsDetailGen = (state.analyticsDetailGen || 0) + 1);
      state.selectedAnalyticsId = id;
      document.querySelectorAll("#analytics-rows tr").forEach((row) => {
        row.classList.toggle("active", row.dataset.id === id);
      });
      const row = this.allAnalyticsRows().find((item) => item.id === id);
      const title = document.getElementById("analytics-detail-title");
      const detail = document.getElementById("analytics-detail");
      if (!row || !detail) return;
      if (title) title.textContent = `${row.method || ""} ${row.host || ""}${row.path || ""}`;
      const query = row.query ? `<p class="muted">?${escapeHtml(row.query)}</p>` : "";
      detail.innerHTML = `
        <h3>Params</h3>
        ${query}
        ${this.paramsHtml(row.params)}
        <p><a href="/flows/${encodeURIComponent(row.id)}">Open flow page</a></p>
        <h3>Request</h3>
        <div id="analytics-flow"><p class="muted">Loading request…</p></div>
      `;
      this.scrollAnalyticsRow(id);
      try {
        const response = await fetch(`/api/flows/${encodeURIComponent(id)}`);
        const host = document.getElementById("analytics-flow");
        if (!host || state.analyticsDetailGen !== gen || state.selectedAnalyticsId !== id) return;
        if (!response.ok) {
          host.innerHTML = `<p class="muted">Flow not found</p>`;
          return;
        }
        const flow = await response.json();
        if (state.analyticsDetailGen !== gen || state.selectedAnalyticsId !== id) return;
        if (window.ViewliftNetwork && typeof ViewliftNetwork.renderDetail === "function") {
          ViewliftNetwork.renderDetail(flow, host);
        } else {
          host.textContent = JSON.stringify(flow, null, 2);
        }
      } catch (error) {
        const host = document.getElementById("analytics-flow");
        if (host && state.analyticsDetailGen === gen && state.selectedAnalyticsId === id) host.textContent = String(error);
      }
    },

    setAnalyticsFollow(on) {
      state.analyticsFollow = on;
      const button = document.getElementById("analytics-follow");
      if (button) {
        button.classList.toggle("active", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.textContent = on ? "Following" : "Follow latest";
      }
      if (!on) {
        this.closeAnalyticsFlowSocket();
        return;
      }
      if (this.analyticsTabVisible()) this.ensureAnalyticsFlowSocket();
      this.loadAnalyticsTraffic();
    },

    analyticsTabVisible() {
      const panel = document.querySelector('[data-panel="analytics"]');
      return !!panel && !panel.classList.contains("hidden");
    },

    followAnalytics() {
      if (!state.analyticsFollow) return;
      const latest = this.filterAnalytics(this.allAnalyticsRows())[0];
      if (!latest) return;
      const sig = `${latest.id}|${latest.status}|${latest.created_at}|${Object.keys(latest.params || {}).length}`;
      if (sig === state.analyticsFollowSig && latest.id === state.selectedAnalyticsId) {
        this.scrollAnalyticsRow(latest.id);
        return;
      }
      state.analyticsFollowSig = sig;
      this.selectAnalytics(latest.id);
    },

    scrollAnalyticsRow(id) {
      const row = document.querySelector(`#analytics-rows tr[data-id="${CSS.escape(id)}"]`);
      if (row) row.scrollIntoView({ block: "nearest", inline: "nearest" });
    },

    ensureAnalyticsFlowSocket() {
      const current = state.analyticsFlowWs;
      if (current && (current.readyState === 0 || current.readyState === 1)) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/ws/flows`);
      state.analyticsFlowWs = socket;
      socket.addEventListener("message", () => {
        if (!state.analyticsFollow) return;
        clearTimeout(state.analyticsFollowTimer);
        state.analyticsFollowTimer = setTimeout(() => this.loadAnalyticsTraffic(), 200);
      });
      socket.addEventListener("close", () => {
        if (state.analyticsFlowWs !== socket) return;
        state.analyticsFlowWs = null;
        if (state.analyticsFollow && this.analyticsTabVisible()) {
          setTimeout(() => this.ensureAnalyticsFlowSocket(), 1500);
        }
      });
    },

    closeAnalyticsFlowSocket() {
      const socket = state.analyticsFlowWs;
      state.analyticsFlowWs = null;
      clearTimeout(state.analyticsFollowTimer);
      if (!socket) return;
      try { socket.close(); } catch (_) { /* already closed */ }
    },

    paramsHtml(params) {
      const entries = Object.entries(params || {});
      if (!entries.length) return `<p class="muted">No parsed params</p>`;
      return `<dl class="ott-kv">${entries.map(([key, value]) => {
        const text = value && typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
        const body = text.includes("\n") ? `<pre class="pre">${escapeHtml(text)}</pre>` : escapeHtml(text);
        return `<dt>${escapeHtml(key)}</dt><dd>${body}</dd>`;
      }).join("")}</dl>`;
    },

    async loadSpm() {
      const target = document.getElementById("ott-spm");
      const source = document.getElementById("ott-spm-source");
      const frameworks = document.getElementById("ott-frameworks");
      if (!target) return;
      const params = new URLSearchParams();
      if (state.clientId) params.set("client_id", state.clientId);
      try {
        const data = await (await fetch(`/api/debug/packages?${params}`)).json();
        this.renderRuntimeFrameworks(frameworks, data.runtime_frameworks || []);
        if (!data.ok) {
          if (source) source.textContent = data.error || "No packages detected.";
          target.innerHTML = "";
          return;
        }
        const app = data.app || {};
        const how = data.source === "workspace-state" ? "Xcode resolved state" : "Package.resolved";
        const sdk = data.capture_sdk;
        if (sdk && state.client && !state.client.sdk_version) {
          state.client.sdk_version = sdk.version || sdk.branch || (sdk.revision || "").slice(0, 8);
          this.renderIdentity();
        }
        if (source) {
          source.textContent = [
            `${data.count} packages`,
            `${app.bundle_id || app.executable} ${app.configuration ? `(${app.configuration})` : ""}`.trim(),
            `matched by ${app.matched_by}`,
            `built ${app.built_at ? new Date(app.built_at).toLocaleString() : "unknown"}`,
            `from ${how}`,
            data.workspace || data.derived_data,
          ].filter(Boolean).join(" · ");
        }
        const rows = (data.packages || []).map((pkg) => ({
          package: pkg.identity,
          version: pkg.version,
          revision: pkg.revision,
          location: pkg.location,
        }));
        target.innerHTML = this.tableHtml(rows);
      } catch (error) {
        target.textContent = String(error);
      }
    },

    renderRuntimeFrameworks(target, frameworks) {
      if (!target) return;
      if (!frameworks.length) {
        target.innerHTML = `<p class="muted">The paired app has not reported loaded frameworks. Update ViewliftNetworkCapture in the app.</p>`;
        return;
      }
      target.innerHTML = this.tableHtml(frameworks.map((item) => ({
        framework: item.name || item.id,
        bundle_id: item.id,
        version: item.version || "",
      })));
    },

    renderOtt(result) {
      const snapshot = result || {};
      const isScalar = (value) => value === null || ["string", "number", "boolean"].includes(typeof value);
      const summarySource = snapshot.summary && typeof snapshot.summary === "object"
        ? snapshot.summary
        : Object.fromEntries(Object.entries(snapshot).filter(([, value]) => isScalar(value)));
      document.getElementById("ott-summary").innerHTML = Object.entries(summarySource)
        .filter(([, value]) => isScalar(value) && value !== "")
        .map(([key, value]) => `<div class="metric"><span class="muted">${escapeHtml(humanize(key))}</span><strong>${escapeHtml(String(value))}</strong></div>`)
        .join("");
      const sections = Object.entries(snapshot).filter(([key, value]) => key !== "summary" && !isScalar(value));
      document.getElementById("ott-sections").innerHTML = sections.length
        ? sections.map(([key, value]) => this.ottCard(humanize(key), value)).join("")
        : `<p class="muted">The snapshot has no sections.</p>`;
    },

    ottCard(title, data) {
      if (Array.isArray(data)) {
        return `<div class="ott-card"><h3>${escapeHtml(title)} (${data.length})</h3>${this.listHtml(data)}</div>`;
      }
      const flat = {};
      const nested = [];
      Object.entries(data || {}).forEach(([key, value]) => {
        if (value && typeof value === "object" && (Array.isArray(value) ? value.some((item) => item && typeof item === "object") : true)) {
          nested.push([key, value]);
        } else {
          flat[key] = Array.isArray(value) ? value.join(", ") : value;
        }
      });
      const nestedHtml = nested.map(([key, value]) => {
        const label = Array.isArray(value) ? `${humanize(key)} (${value.length})` : humanize(key);
        const body = Array.isArray(value) ? this.listHtml(value) : this.kvHtml(value);
        return `<details><summary>${escapeHtml(label)}</summary>${body}</details>`;
      }).join("");
      return `<div class="ott-card"><h3>${escapeHtml(title)}</h3>${this.kvHtml(flat)}${nestedHtml}</div>`;
    },

    listHtml(items) {
      if (!items.length) return `<p class="muted">empty</p>`;
      return items.map((item, index) => {
        if (!item || typeof item !== "object") return `<div class="mono">${escapeHtml(String(item))}</div>`;
        const label = item.name || item.title || item.id || item.module_type || `#${index + 1}`;
        return `<details><summary>${escapeHtml(index + 1)}. ${escapeHtml(String(label))}</summary>`
          + `<pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></details>`;
      }).join("");
    },

    kvHtml(data) {
      const entries = Object.entries(data || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
      if (!entries.length) return `<p class="muted">empty</p>`;
      return `<dl class="ott-kv">${entries.map(([key, value]) => {
        const text = typeof value === "object" ? JSON.stringify(value) : String(value);
        return `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(text)}</dd>`;
      }).join("")}</dl>`;
    },

    renderStacks(name, result) {
      const events = result.events || [];
      const target = document.getElementById("hangs-result");
      if (!events.length) {
        target.innerHTML = `<p class="muted">${escapeHtml(name)} · no events yet. ${escapeHtml(result.note || "")}</p>`;
        return;
      }
      target.innerHTML = `<p class="muted">${escapeHtml(name)} · ${events.length} event(s)</p>`
        + events.map((event) => {
          const frames = event.frames || [];
          const head = [
            event.kind || name,
            event.duration_ms != null ? `${Math.round(event.duration_ms)}ms` : "",
            event.name || "",
            event.thread || "",
          ].filter(Boolean).join(" · ");
          return `<div class="stack-event"><h4>${escapeHtml(head)}</h4>${event.reason ? `<p>${escapeHtml(event.reason)}</p>` : ""}`
            + frames.map((frame) => {
              const loc = frame.file && frame.line != null
                ? `${frame.file}:${frame.line}`
                : (frame.file || "");
              const klass = frame.class || "";
              const sym = frame.symbol || frame.raw || "";
              return `<div class="stack-frame"><span class="loc">${escapeHtml(loc)}</span> `
                + `<span class="sym">${escapeHtml(klass ? klass + " · " : "")}${escapeHtml(sym)}</span></div>`;
            }).join("")
            + `</div>`;
        }).join("");
    },

    tableHtml(rows) {
      if (!rows.length) return `<p class="muted">empty</p>`;
      const keys = Object.keys(rows[0]);
      return `<table class="debug-table"><thead><tr>${keys.map((k) => `<th>${escapeHtml(k)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((row) => `<tr>${keys.map((k) => `<td>${escapeHtml(row[k])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    },

    renderPerformance() {
      const sample = state.samples.at(-1) || {};
      const metrics = [
        ["CPU", `${(sample.cpu_percent || 0).toFixed(1)}%`],
        ["FPS", `${(sample.fps || 0).toFixed(1)}`],
        ["Memory", this.bytes(sample.memory_bytes || 0)],
        ["Peak", this.bytes(sample.peak_memory_bytes || 0)],
        ["Thermal", sample.thermal_state || "unknown"],
        ["Hangs", `${sample.hang_count || 0}`],
      ];
      document.getElementById("performance-metrics").innerHTML = metrics
        .map(([label, value]) => `<div class="metric"><span class="muted">${label}</span><strong>${escapeHtml(value)}</strong></div>`)
        .join("");
      const canvas = document.getElementById("performance-chart");
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      this.plot(ctx, state.samples.map((item) => item.cpu_percent || 0), "#e6b85c", 100);
      this.plot(ctx, state.samples.map((item) => item.fps || 0), "#65b7e8", 120);
      const maxMemory = Math.max(...state.samples.map((item) => item.memory_bytes || 0), 1);
      this.plot(ctx, state.samples.map((item) => (item.memory_bytes || 0) / maxMemory * 100), "#7dcea0", 100);
    },

    plot(ctx, values, color, max) {
      if (values.length < 2) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      values.forEach((value, index) => {
        const x = index / Math.max(1, values.length - 1) * ctx.canvas.width;
        const y = ctx.canvas.height - Math.min(max, value) / max * ctx.canvas.height;
        index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    },

    renderMemory(result) {
      const target = document.getElementById("memory-result");
      if (result.nodes) {
        this.drawGraph(document.getElementById("memory-graph"), result);
        target.textContent = result.coverage || "";
      } else {
        this.renderTable("memory-result", result.suspected_leaks || []);
      }
    },

    drawGraph(canvas, graph) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const nodes = graph.nodes || [];
      const positions = new Map();
      nodes.forEach((node, index) => {
        const angle = index / Math.max(1, nodes.length) * Math.PI * 2;
        positions.set(node.id, {
          x: canvas.width / 2 + Math.cos(angle) * Math.min(350, 40 + nodes.length * 8),
          y: canvas.height / 2 + Math.sin(angle) * Math.min(190, 30 + nodes.length * 5),
        });
      });
      ctx.strokeStyle = "#41566e";
      for (const edge of graph.edges || []) {
        const from = positions.get(edge.from), to = positions.get(edge.to);
        if (!from || !to) continue;
        ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
      }
      for (const node of nodes) {
        const point = positions.get(node.id);
        ctx.fillStyle = "#3d7ea6";
        ctx.beginPath(); ctx.arc(point.x, point.y, 8, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#c8d3df";
        ctx.fillText(node.class || "Object", point.x + 11, point.y + 4);
      }
    },

    hierarchyNodes() {
      const flat = [];
      const walk = (node, depth) => {
        flat.push({ node, depth });
        (node.children || []).forEach((child) => walk(child, depth + 1));
      };
      state.hierarchy.forEach((window) => walk(window, 0));
      return flat;
    },

    renderHierarchy(result) {
      state.hierarchy = result.windows || [];
      state.selectedView = null;
      state.describeId = "";
      const flat = this.hierarchyNodes();
      state.hierarchyParents = new Map();
      flat.forEach(({ node }) => {
        (node.children || []).forEach((child) => state.hierarchyParents.set(String(child.id), String(node.id)));
      });
      state.collapsed = new Set(flat
        .filter(({ node, depth }) => depth >= 10 && (node.children || []).length)
        .map(({ node }) => String(node.id)));
      state.hierarchyShot = null;
      const shot = result.screenshot;
      if (shot && shot.base64) {
        const image = new Image();
        const entry = { image, width: Number(shot.width) || 0, height: Number(shot.height) || 0 };
        image.onload = () => {
          if (state.hierarchyShot === entry) this.drawHierarchy3D();
        };
        image.src = `data:${shot.mime || "image/jpeg"};base64,${shot.base64}`;
        state.hierarchyShot = entry;
      }
      const props = document.getElementById("ui-properties");
      if (props) {
        const notes = [];
        if (result.truncated) notes.push("Hierarchy truncated at the SDK node budget.");
        if (!shot) notes.push("No screenshot in payload; layers show colors only. Update the SDK for textures.");
        notes.push("Select a view in the tree or click a layer.");
        props.textContent = notes.join(" ");
      }
      this.drawHierarchy();
    },

    findHierarchyNode(id) {
      const match = this.hierarchyNodes().find(({ node }) => String(node.id) === id);
      return match ? match.node : null;
    },

    selectView(id, reveal) {
      const node = this.findHierarchyNode(id);
      if (!node) return;
      state.selectedView = node;
      if (reveal) {
        let parent = state.hierarchyParents.get(id);
        while (parent) {
          state.collapsed.delete(parent);
          parent = state.hierarchyParents.get(parent);
        }
      }
      this.drawHierarchy();
      if (reveal) {
        document.querySelector(`#ui-tree [data-view-id="${CSS.escape(id)}"]`)
          ?.scrollIntoView({ block: "nearest" });
      }
      state.describeId = id;
      this.paintViewSheet(node, null, "");
      this.sendCommand("ui.describe", { id });
    },

    renderDescribe(result) {
      if (!state.describeId) return;
      if (result.id && String(result.id) !== String(state.describeId)) return;
      const node = this.findHierarchyNode(state.describeId);
      if (!node) return;
      this.paintViewSheet(node, result.found ? result : null, result.error || "");
    },

    paintViewSheet(node, detail, error) {
      const props = document.getElementById("ui-properties");
      if (!props) return;
      const role = (detail && detail.role) || node.role || "uikit";
      const labels = { uikit: "UIKit", swiftui: "SwiftUI", "uikit-in-swiftui": "UIKit inside SwiftUI" };
      const title = (detail && detail.title) || node.class || "View";
      const summary = (detail && detail.summary) || "Reading properties from the app…";
      const sections = (detail && detail.sections) || this.fallbackSections(node);
      const body = sections.map((section) => {
        const rows = (section.rows || []).map((row) =>
          `<div class="view-row"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></div>`
        ).join("");
        return `<section class="view-section"><h4>${escapeHtml(section.title)}</h4>${rows}</section>`;
      }).join("");
      let note = "";
      if (error) {
        const stale = String(error).includes("unsupportedCommand");
        note = stale
          ? "Full property sheet needs an app rebuild with this SDK. The summary above is from the hierarchy."
          : error;
      }
      props.innerHTML = `<div class="view-sheet"><div class="view-sheet-head"><span class="view-role">${escapeHtml(labels[role] || role)}</span><h3>${escapeHtml(title)}</h3><button class="btn ghost" id="ui-highlight" type="button">Highlight</button></div><p>${escapeHtml(summary)}</p>${note ? `<p class="warn-text">${escapeHtml(note)}</p>` : ""}${body}</div>`;
      document.getElementById("ui-highlight").onclick = () =>
        this.sendCommand("ui.highlight", { id: state.describeId });
    },

    fallbackSections(node) {
      const frame = `x ${Math.round(node.x || 0)}, y ${Math.round(node.y || 0)}, ${Math.round(node.width || 0)} × ${Math.round(node.height || 0)}`;
      const rows = [
        ["Class", node.class || ""],
        ["Module", node.module || ""],
        ["Screen", node.controller || ""],
        ["SwiftUI view", node.swiftui || ""],
        ["Frame", frame],
        ["Text", node.text || ""],
        ["Background", node.background || ""],
        ["Opacity", `${Math.round(Number(node.alpha ?? 1) * 100)}%`],
        ["Shown", node.hidden ? "No" : "Yes"],
        ["Accessibility label", node.accessibility_label || ""],
        ["Accessibility id", node.accessibility_identifier || ""],
      ].filter(([, value]) => value !== "");
      return [{ title: "From the hierarchy", rows: rows.map(([label, value]) => ({ label, value })) }];
    },

    bindHierarchyCanvas() {
      const canvas = document.getElementById("ui-3d");
      if (!canvas) return;
      const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
      const point = (event) => {
        const rect = canvas.getBoundingClientRect();
        return { x: event.clientX - rect.left - canvas.clientLeft, y: event.clientY - rect.top - canvas.clientTop };
      };
      let drag = null;
      canvas.addEventListener("contextmenu", (event) => event.preventDefault());
      canvas.addEventListener("pointerdown", (event) => {
        drag = { x: event.clientX, y: event.clientY, moved: false, pan: event.shiftKey || event.button === 2 };
        canvas.setPointerCapture(event.pointerId);
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!drag) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) < 3) return;
        drag.moved = true;
        drag.x = event.clientX;
        drag.y = event.clientY;
        const view = state.hierarchyView;
        if (drag.pan) {
          view.panX += dx;
          view.panY += dy;
        } else {
          view.yaw = clamp(view.yaw + dx * 0.008, -1.4, 1.4);
          view.pitch = clamp(view.pitch + dy * 0.008, -1.4, 1.4);
        }
        this.drawHierarchy3D();
      });
      canvas.addEventListener("pointerup", (event) => {
        const wasClick = drag && !drag.moved;
        drag = null;
        if (!wasClick) return;
        const { x, y } = point(event);
        const node = this.hitTestHierarchy(x, y);
        if (node) this.selectView(String(node.id), true);
      });
      canvas.addEventListener("pointercancel", () => { drag = null; });
      canvas.addEventListener("wheel", (event) => {
        if (!state.hierarchy.length) return;
        event.preventDefault();
        if (event.altKey) {
          state.hierarchyView.spacing = clamp(state.hierarchyView.spacing - event.deltaY * 0.05, 0, 120);
          this.syncSpacingInput();
        } else {
          state.hierarchyZoom = clamp(state.hierarchyZoom * Math.exp(-event.deltaY * 0.0015), 0.25, 6);
        }
        this.drawHierarchy3D();
      }, { passive: false });
      canvas.addEventListener("dblclick", () => this.setHierarchyView({ yaw: 0.55, pitch: 0.2 }));
      document.getElementById("ui-3d-flat")?.addEventListener("click", () =>
        this.setHierarchyView({ yaw: 0, pitch: 0 }));
      document.getElementById("ui-3d-reset")?.addEventListener("click", () =>
        this.setHierarchyView({ yaw: 0.55, pitch: 0.2 }));
      document.getElementById("ui-3d-spacing")?.addEventListener("input", (event) => {
        state.hierarchyView.spacing = Number(event.target.value) || 0;
        this.drawHierarchy3D();
      });
      if (window.ResizeObserver) new ResizeObserver(() => this.drawHierarchy3D()).observe(canvas);
    },

    setHierarchyView(angles) {
      Object.assign(state.hierarchyView, angles, { panX: 0, panY: 0 });
      state.hierarchyZoom = 1;
      this.drawHierarchy3D();
    },

    syncSpacingInput() {
      const input = document.getElementById("ui-3d-spacing");
      if (input) input.value = String(Math.round(state.hierarchyView.spacing));
    },

    hierarchyLayers() {
      const root = state.hierarchy.find((window) => window.key_window) || state.hierarchy[0];
      const layers = [];
      const walk = (node, depth) => {
        if (node.hidden || Number(node.alpha ?? 1) <= 0.01) return;
        const drawable = node.width > 0 && node.height > 0;
        const index = layers.length;
        if (drawable) layers.push({ node, depth, leaf: true });
        (node.children || []).forEach((child) => walk(child, depth + 1));
        if (drawable) layers[index].leaf = layers.length === index + 1;
      };
      if (root) walk(root, 0);
      return { root, layers };
    },

    hitTestHierarchy(x, y) {
      for (let i = state.hierarchyDrawn.length - 1; i >= 0; i -= 1) {
        const { node, m } = state.hierarchyDrawn[i];
        const [a, b, c, d, e, f] = m;
        const det = a * d - b * c;
        if (!det) continue;
        const u = (d * (x - e) - c * (y - f)) / det;
        const v = (-b * (x - e) + a * (y - f)) / det;
        if (u >= node.x && u <= node.x + node.width && v >= node.y && v <= node.y + node.height) return node;
      }
      return null;
    },

    drawHierarchy3D() {
      const canvas = document.getElementById("ui-3d");
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const cssW = canvas.clientWidth || 800;
      const cssH = canvas.clientHeight || 520;
      if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      state.hierarchyDrawn = [];
      const { root, layers } = this.hierarchyLayers();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = "12px sans-serif";
      ctx.fillStyle = "#7d8b99";
      if (!root || !layers.length) {
        ctx.fillText("Capture hierarchy to render layers.", 16, 24);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        return;
      }
      ctx.fillText("Drag rotate · Shift-drag pan · Wheel zoom · Alt-wheel spacing · Click select", 12, cssH - 10);
      const view = state.hierarchyView;
      const maxDepth = layers.reduce((max, layer) => Math.max(max, layer.depth), 0);
      const stack = maxDepth * view.spacing;
      const cosY = Math.cos(view.yaw);
      const sinY = Math.sin(view.yaw);
      const cosP = Math.cos(view.pitch);
      const sinP = Math.sin(view.pitch);
      const extentX = Math.abs(cosY) * root.width + Math.abs(sinY) * stack;
      const extentY = Math.abs(cosP) * root.height + Math.abs(sinY * sinP) * root.width
        + Math.abs(cosY * sinP) * stack;
      const fit = Math.min((cssW - 48) / Math.max(1, extentX), (cssH - 56) / Math.max(1, extentY));
      const scale = fit * state.hierarchyZoom;
      const cx = root.x + root.width / 2;
      const cy = root.y + root.height / 2;
      const ox = cssW / 2 + view.panX;
      const oy = (cssH - 16) / 2 + view.panY;
      const matrix = (depth) => {
        const z = depth * view.spacing - stack / 2;
        const a = scale * cosY;
        const b = scale * sinY * sinP;
        const d = scale * cosP;
        return [a, b, 0, d, ox + scale * z * sinY - a * cx, oy - scale * z * cosY * sinP - b * cx - d * cy];
      };
      const apply = (m) => ctx.setTransform(m[0] * dpr, m[1] * dpr, m[2] * dpr, m[3] * dpr, m[4] * dpr, m[5] * dpr);
      const shot = state.hierarchyShot;
      const shotReady = Boolean(shot && shot.image.complete && shot.image.naturalWidth && shot.width);
      const ratio = shotReady ? shot.image.naturalWidth / shot.width : 1;
      const rootArea = root.width * root.height;
      const textured = new Set(["image", "label", "button", "text", "control"]);
      const drawCrop = (node) => {
        const x0 = Math.max(0, node.x - root.x);
        const y0 = Math.max(0, node.y - root.y);
        const x1 = Math.min(shot.width, node.x - root.x + node.width);
        const y1 = Math.min(shot.height || root.height, node.y - root.y + node.height);
        if (x1 <= x0 || y1 <= y0) return;
        ctx.drawImage(shot.image, x0 * ratio, y0 * ratio, (x1 - x0) * ratio, (y1 - y0) * ratio,
          root.x + x0, root.y + y0, x1 - x0, y1 - y0);
      };
      const selectedId = state.selectedView ? String(state.selectedView.id) : "";
      let selected = null;
      layers.forEach((layer) => {
        const { node, depth, leaf } = layer;
        const m = matrix(depth);
        state.hierarchyDrawn.push({ node, m });
        apply(m);
        ctx.globalAlpha = Math.max(0.15, Math.min(1, Number(node.alpha ?? 1)));
        if (node.background) {
          ctx.fillStyle = node.background;
          ctx.fillRect(node.x, node.y, node.width, node.height);
        }
        const wantsTexture = depth === 0 || (leaf && (textured.has(node.kind) || node.background
          || node.width * node.height < rootArea * 0.5));
        if (shotReady && wantsTexture) drawCrop(node);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = `hsla(${(depth * 37) % 360}, 65%, 62%, ${depth === 0 ? 0.9 : 0.55})`;
        ctx.lineWidth = 1 / scale;
        ctx.strokeRect(node.x, node.y, node.width, node.height);
        if (String(node.id) === selectedId) selected = { node, m };
      });
      if (selected) {
        const { node, m } = selected;
        apply(m);
        ctx.fillStyle = "rgba(255, 176, 32, 0.28)";
        ctx.fillRect(node.x, node.y, node.width, node.height);
        ctx.strokeStyle = "#ffb020";
        ctx.lineWidth = 2.5 / scale;
        ctx.strokeRect(node.x, node.y, node.width, node.height);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const px = m[0] * node.x + m[2] * node.y + m[4];
        const py = m[1] * node.x + m[3] * node.y + m[5];
        ctx.fillStyle = "#ffb020";
        ctx.fillText(`${node.class} ${Math.round(node.width)}×${Math.round(node.height)}`,
          Math.max(4, px + 4), Math.max(14, py - 6));
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    },

    drawHierarchy() {
      const visible = [];
      const walk = (node, depth) => {
        const children = node.children || [];
        const collapsed = state.collapsed.has(String(node.id));
        visible.push({ node, depth, collapsed, hasChildren: children.length > 0 });
        if (!collapsed) children.forEach((child) => walk(child, depth + 1));
      };
      state.hierarchy.forEach((window) => walk(window, 0));
      const tree = document.getElementById("ui-tree");
      const scroll = tree.scrollTop;
      const selectedId = state.selectedView ? String(state.selectedView.id) : "";
      tree.innerHTML = visible.map(({ node, depth, collapsed, hasChildren }) => {
        const marker = hasChildren ? (collapsed ? "▸" : "▾") : "·";
        const active = String(node.id) === selectedId ? " active" : "";
        const dim = node.hidden || Number(node.alpha ?? 1) <= 0.01 ? " dim" : "";
        const hint = node.text || node.accessibility_identifier || node.accessibility_label || "";
        const extra = hint ? ` <em>${escapeHtml(String(hint).slice(0, 40))}</em>` : "";
        const badge = node.role === "swiftui"
          ? (node.swiftui || "SwiftUI")
          : node.role === "uikit-in-swiftui" ? "in SwiftUI" : "";
        const role = badge ? ` <i class="role-tag">${escapeHtml(badge)}</i>` : "";
        return `<div class="tree-row${active}${dim}" data-view-id="${escapeHtml(node.id)}" style="padding-left:${depth * 14 + 4}px"><button type="button" class="tree-toggle" data-toggle="${escapeHtml(node.id)}" ${hasChildren ? "" : "disabled"}>${marker}</button><span>${escapeHtml(node.class)} ${Math.round(node.width)}×${Math.round(node.height)}${extra}${role}</span></div>`;
      }).join("");
      tree.scrollTop = scroll;
      tree.onclick = (event) => {
        const toggle = event.target.closest("[data-toggle]");
        if (toggle && !toggle.disabled) {
          const id = toggle.dataset.toggle;
          if (state.collapsed.has(id)) state.collapsed.delete(id);
          else state.collapsed.add(id);
          this.drawHierarchy();
          return;
        }
        const row = event.target.closest("[data-view-id]");
        if (row) this.selectView(row.dataset.viewId, false);
      };
      this.drawHierarchy3D();
    },

    renderScreenshot(result, targetId) {
      document.getElementById(targetId).innerHTML =
        `<img alt="App screenshot" style="max-width:100%" src="data:${escapeHtml(result.mime)};base64,${result.base64}">`;
    },

    renderStorage(name, result) {
      if (name === "storage.files") return this.renderFileBrowser(result);
      if (name === "storage.file_read") return this.renderFilePreview(result);
      const targetId = "storage-inspector";
      if (result.values) {
        const rows = Object.entries(result.values).map(([key, value]) => ({ key, value: JSON.stringify(value) }));
        return this.renderTable(targetId, rows);
      }
      for (const key of ["items", "files", "rows"]) {
        if (Array.isArray(result[key])) return this.renderTable(targetId, result[key]);
      }
      const target = document.getElementById(targetId);
      if (target) target.textContent = JSON.stringify(result, null, 2);
    },

    bindStorage() {
      document.getElementById("storage-crumb")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-storage-path]");
        if (button) this.openStorage(button.dataset.storagePath || "");
      });
      document.getElementById("storage-files")?.addEventListener("click", (event) => {
        const schema = event.target.closest("[data-storage-schema]");
        if (schema) {
          const path = schema.dataset.storageSchema || "";
          this.selectStorageFile(path, path);
          this.sendCommand("storage.sqlite_schema", { path });
          return;
        }
        if (event.target.closest("input")) return;
        const row = event.target.closest("tr[data-path]");
        if (!row) return;
        if (row.dataset.directory === "true") {
          this.openStorage(row.dataset.path || "");
          return;
        }
        this.selectStorageFile(row.dataset.path || "", row.dataset.name || "");
        this.sendCommand("storage.file_read", { path: row.dataset.path || "" });
      });
      document.getElementById("storage-snapshot")?.addEventListener("click", () => {
        const paths = [...document.querySelectorAll("#storage-files input[data-storage-check]:checked")]
          .map((input) => input.value)
          .filter(Boolean);
        if (!paths.length) {
          alert("Select one or more files first.");
          return;
        }
        this.sendCommand("storage.snapshot_create", { paths });
      });
      document.getElementById("storage-sql")?.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!state.storageDbPath) {
          alert("Select a database first.");
          return;
        }
        const query = document.getElementById("storage-sql-query")?.value.trim() || "";
        if (!query) return;
        this.sendCommand("storage.sqlite_query", { path: state.storageDbPath, query });
      });
      document.getElementById("storage-sql-schema")?.addEventListener("click", () => {
        if (!state.storageDbPath) {
          alert("Select a database first.");
          return;
        }
        this.sendCommand("storage.sqlite_schema", { path: state.storageDbPath });
      });
    },

    openStorage(path) {
      state.storagePath = String(path || "").replace(/^\/+|\/+$/g, "");
      this.renderStorageCrumb();
      const target = document.getElementById("storage-files");
      if (!state.clientId) {
        if (target) {
          target.innerHTML = `<tr><td colspan="4" class="muted">Choose one app in Scope first.</td></tr>`;
        }
        return;
      }
      this.sendCommand("storage.files", { path: state.storagePath });
    },

    renderStorageCrumb() {
      const crumb = document.getElementById("storage-crumb");
      if (!crumb) return;
      const parts = state.storagePath ? state.storagePath.split("/").filter(Boolean) : [];
      const items = [{ label: "Sandbox", path: "" }];
      let acc = "";
      parts.forEach((part) => {
        acc = acc ? `${acc}/${part}` : part;
        items.push({ label: part, path: acc });
      });
      crumb.innerHTML = items.map((item, index) => {
        if (index === items.length - 1) return `<span>${escapeHtml(item.label)}</span>`;
        return `<button type="button" data-storage-path="${escapeAttr(item.path)}">${escapeHtml(item.label)}</button>`;
      }).join(`<span class="muted">/</span>`);
    },

    renderFileBrowser(result) {
      this.renderStorageCrumb();
      const target = document.getElementById("storage-files");
      if (!target) return;
      const files = Array.isArray(result.files) ? result.files.slice() : [];
      files.sort((a, b) => {
        if (!!a.directory !== !!b.directory) return a.directory ? -1 : 1;
        return String(a.name || "").localeCompare(String(b.name || ""));
      });
      const rows = [];
      if (state.storagePath) {
        const parent = state.storagePath.split("/").slice(0, -1).join("/");
        rows.push(`<tr data-path="${escapeAttr(parent)}" data-directory="true"><td></td><td class="file-dir">..</td><td></td><td></td></tr>`);
      }
      if (!files.length && !state.storagePath) {
        rows.push(`<tr><td colspan="4" class="muted">Sandbox is empty.</td></tr>`);
      } else if (!files.length) {
        rows.push(`<tr><td colspan="4" class="muted">Empty folder.</td></tr>`);
      }
      files.forEach((file) => {
        const directory = !!file.directory;
        const name = String(file.name || "");
        const path = String(file.path || "");
        const db = !directory && /\.(sqlite3?|db)$/i.test(name);
        const check = directory
          ? ""
          : `<input type="checkbox" data-storage-check value="${escapeAttr(path)}" aria-label="Select ${escapeAttr(name)}">`;
        const schema = db
          ? `<button type="button" class="btn ghost storage-schema" data-storage-schema="${escapeAttr(path)}">Schema</button>`
          : "";
        const active = path === state.selectedStoragePath ? ` class="active"` : "";
        rows.push(`<tr data-path="${escapeAttr(path)}" data-directory="${directory ? "true" : "false"}" data-name="${escapeAttr(name)}"${active}>
          <td>${check}</td>
          <td class="${directory ? "file-dir" : ""}">${escapeHtml(directory ? `${name}/` : name)}${schema}</td>
          <td>${directory ? "" : escapeHtml(this.fmtBytes(file.bytes))}</td>
          <td>${escapeHtml(this.fmtTime(file.modified))}</td>
        </tr>`);
      });
      target.innerHTML = rows.join("");
    },

    selectStorageFile(path, name) {
      state.selectedStoragePath = path;
      document.querySelectorAll("#storage-files tr").forEach((row) => {
        row.classList.toggle("active", row.dataset.path === path);
      });
      if (/\.(sqlite3?|db)$/i.test(name || path)) this.useDatabase(path);
    },

    useDatabase(path) {
      state.storageDbPath = path;
      const label = document.getElementById("storage-sql-path");
      if (label) label.textContent = path || "Select a database";
    },

    renderFilePreview(result) {
      const target = document.getElementById("storage-inspector");
      if (!target) return;
      const path = result.path || state.selectedStoragePath || "";
      if (result.encoding === "base64") {
        const bytes = Math.floor((String(result.content || "").length * 3) / 4);
        target.innerHTML = `<p><strong>${escapeHtml(path)}</strong></p><p class="muted">binary · ${escapeHtml(this.fmtBytes(bytes))}</p>`;
        return;
      }
      target.innerHTML = `<p><strong>${escapeHtml(path)}</strong></p><pre class="pre">${escapeHtml(result.content || "")}</pre>`;
    },

    fmtBytes(value) {
      const size = Number(value);
      if (!Number.isFinite(size)) return "";
      if (size < 1024) return `${Math.round(size)} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
      return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    },

    fmtTime(value) {
      const n = Number(value);
      if (!n) return "";
      const ms = n > 1e12 ? n : n * 1000;
      const date = new Date(ms);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString();
    },

    renderFlags(flags) {
      document.getElementById("flags-result").innerHTML = flags.map((flag) =>
        `<label class="flag-row"><span class="grow"><strong>${escapeHtml(flag.title)}</strong><br><span class="muted">${escapeHtml(flag.key)} · ${escapeHtml(flag.group)}</span></span><input type="checkbox" data-flag="${escapeHtml(flag.key)}" ${flag.enabled ? "checked" : ""}></label>`
      ).join("");
      document.querySelectorAll("[data-flag]").forEach((input) => {
        input.onchange = () => this.sendCommand("feature_flags.set", {
          key: input.dataset.flag,
          enabled: input.checked,
        });
      });
    },

    renderMacro(result) {
      state.macro = { actions: result.actions || [] };
      this.renderTable("macro-result", result.actions || []);
    },

    renderRuntime(name, result) {
      const target = document.getElementById("runtime-result");
      if (name === "runtime.search") {
        target.innerHTML = (result.classes || []).map((className) =>
          `<button class="btn ghost runtime-class" data-class="${escapeHtml(className)}">${escapeHtml(className)}</button>`
        ).join(" ");
        target.querySelectorAll("[data-class]").forEach((button) => {
          button.onclick = () => this.sendCommand("runtime.describe", { class: button.dataset.class });
        });
      } else {
        target.textContent = JSON.stringify(result, null, 2);
      }
    },

    renderBug(result) {
      const canvas = document.getElementById("bug-canvas");
      const image = new Image();
      image.onload = () => {
        canvas.width = image.width;
        canvas.height = image.height;
        canvas.getContext("2d").drawImage(image, 0, 0);
        state.bugImage = image;
      };
      image.src = `data:${result.screenshot_mime};base64,${result.screenshot_base64}`;
      document.getElementById("bug-result").textContent =
        JSON.stringify({ performance: result.performance, memory: result.memory, app: result.app }, null, 2);
    },

    bindBugCanvas() {
      const canvas = document.getElementById("bug-canvas");
      const point = (event) => {
        const rect = canvas.getBoundingClientRect();
        return {
          x: (event.clientX - rect.left) * canvas.width / rect.width,
          y: (event.clientY - rect.top) * canvas.height / rect.height,
        };
      };
      canvas.addEventListener("pointerdown", (event) => {
        state.drawing = true;
        const p = point(event);
        const ctx = canvas.getContext("2d");
        ctx.beginPath(); ctx.moveTo(p.x, p.y);
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!state.drawing) return;
        const p = point(event), ctx = canvas.getContext("2d");
        ctx.strokeStyle = "#ff3b30"; ctx.lineWidth = 5; ctx.lineTo(p.x, p.y); ctx.stroke();
      });
      window.addEventListener("pointerup", () => { state.drawing = false; });
      document.getElementById("bug-clear").onclick = () => {
        if (state.bugImage) canvas.getContext("2d").drawImage(state.bugImage, 0, 0);
      };
      document.getElementById("bug-download").onclick = () => {
        const link = document.createElement("a");
        link.download = "viewlift-bug.png";
        link.href = canvas.toDataURL("image/png");
        link.click();
      };
    },

    renderTable(targetId, rows) {
      const target = document.getElementById(targetId);
      if (!rows.length) {
        target.innerHTML = `<p class="muted">No data.</p>`;
        return;
      }
      const columns = [...new Set(rows.flatMap((row) => Object.keys(row || {})))].slice(0, 12);
      target.innerHTML = `<table class="debug-table"><thead><tr>${columns.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((key) => `<td>${escapeHtml(typeof row[key] === "object" ? JSON.stringify(row[key]) : row[key])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    },

    renderError(module, message) {
      const targets = {
        memory: "memory-result", ui: "ui-properties", swiftui: "swiftui-result",
        assets: "assets-result", accessibility: "accessibility-result", storage: "storage-inspector",
        feature_flags: "flags-result", bug: "bug-result", macro: "macro-result",
        runtime: "runtime-result", push: "push-result",
        ott: "ott-sections", hang: "hangs-result", crash: "hangs-result",
      };
      const target = document.getElementById(targets[module]);
      if (target) target.innerHTML = `<p class="warn-text">${escapeHtml(message)}</p>`;
    },

    async loadProfiles() {
      const data = await (await fetch("/api/debug/network-profiles")).json();
      const host = document.getElementById("profile-buttons");
      const names = (data.profiles || []).map((item) => item.id).concat(["off"]);
      host.innerHTML = names.map((name) => `<button class="btn ghost" data-profile="${escapeHtml(name)}">${escapeHtml(name)}</button>`).join("");
      host.onclick = async (event) => {
        const button = event.target.closest("[data-profile]");
        if (!button) return;
        const response = await fetch("/api/debug/network-profile", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: button.dataset.profile }),
        });
        const result = await response.json();
        document.getElementById("profile-status").textContent =
          response.ok ? `${result.profile} applied; packet loss ${result.packet_loss}` : result.detail;
      };
    },

    async loadWaterfall() {
      const params = new URLSearchParams({ limit: "80" });
      if (window.VLScope) window.VLScope.applyTo(params);
      const flows = (await (await fetch(`/api/flows?${params}`)).json()).flows || [];
      const host = document.getElementById("waterfall");
      if (!flows.length) { host.innerHTML = `<p class="muted">No flows.</p>`; return; }
      const start = Math.min(...flows.map((flow) => Date.parse(flow.created_at)));
      const finish = Math.max(...flows.map((flow) => Date.parse(flow.created_at) + (flow.duration_ms || 1)));
      const span = Math.max(1, finish - start);
      host.innerHTML = flows.map((flow) => {
        const left = (Date.parse(flow.created_at) - start) / span * 100;
        const width = Math.max(1, (flow.duration_ms || 1) / span * 100);
        return `<a class="fall-row" href="/flows/${encodeURIComponent(flow.id)}"><span class="fall-label">${escapeHtml(flow.method)} ${escapeHtml(flow.host)}${escapeHtml(flow.path)}</span><span class="fall-track"><span class="fall-bar" style="left:${left}%;width:${Math.min(width, 100 - left)}%"></span></span></a>`;
      }).join("");
    },

    bytes(value) {
      if (value < 1024) return `${value} B`;
      if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
      return `${(value / 1024 ** 2).toFixed(1)} MB`;
    },
  };

  window.ViewliftDebug = Debug;
})();
