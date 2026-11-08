(() => {
  const MUTATING = new Set([
    "ui.highlight", "ui.edit", "ui.visual", "storage.defaults_set", "storage.file_write",
    "storage.snapshot_create", "storage.snapshot_restore", "feature_flags.set",
    "feature_flags.reset", "macro.record_start", "macro.record_stop", "macro.replay",
    "push.local", "nav.open_url", "explorer.set", "explorer.call", "storage.defaults_delete",
    "storage.file_rename", "storage.file_delete", "storage.cookie_delete", "settings.set",
  ]);

  // Matches StateDebugService protected paths. These containers must stay on device.
  const PROTECTED_STORAGE = new Set([
    "", "Documents", "Library", "tmp", "SystemData", "Library/Caches", "Library/Preferences",
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
    describeDetail: null,
    describeTimer: 0,
    explorerTab: "layout",
    viewQuery: "",
    matchIndex: 0,
    treeRoot: "",
    visibleRows: [],
    swiftUINames: new Map(),
    showSystem: true,
    layoutGuides: false,
    layoutBaselines: false,
    layoutOversized: false,
    hoverInspect: true,
    visualLock: false,
    hierarchyView: { yaw: 0.55, pitch: 0.2, spacing: 18, panX: 0, panY: 0 },
    hierarchyById: new Map(),
    layerImages: new Map(),
    layerEmpty: new Set(),
    layerCrop: new Set(),
    layerPending: false,
    layerProgress: { loaded: 0, total: 0 },
    layerCache: null,
    depthMode: "overlap",
    zRange: { low: 0, high: 100 },
    draw3DFrame: 0,
    macro: null,
    bugImage: null,
    drawing: false,
    analytics: null,
    analyticsBeaconUrls: { user: "", player: "" },
    selectedAnalyticsId: "",
    analyticsFollow: false,
    analyticsFollowSig: "",
    analyticsFlowWs: null,
    analyticsFrame: 0,
    storagePath: "",
    storageDbPath: "",
    selectedStoragePath: "",
    storageRead: "",
    storagePending: false,
    storageTimer: 0,
    storageInfoTimer: 0,
    previewUrl: "",
    playbackEvents: [],
    playbackDigest: {},
    playbackContentId: "",
    playbackPlaybackId: "",
    playbackPaint: 0,
    playbackDirty: false,
    noticeTimer: 0,
    focusPresses: [],
    crashes: [],
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const escapeAttr = (value) => escapeHtml(value).replaceAll("'", "&#39;");
  const formatMetric = (value) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return value ?? "";
    if (Math.abs(value) >= 100) return String(Math.round(value));
    return String(Math.round(value * 100) / 100);
  };
  const ANALYTICS_CLIENTS = [
    ["appsflyersdk", "appsFlyer"],
    ["appsflyer", "appsFlyer"],
    ["clevertap", "cleverTap"],
    ["wzrkt.com", "cleverTap"],
    ["braze.com", "braze"],
    ["appboy.com", "braze"],
    ["demdex.net", "adobe"],
    ["omtrdc.net", "adobe"],
    ["adobedc.net", "adobe"],
    ["adobe.io", "adobe"],
    ["comscore.com", "comscore"],
    ["scorecardresearch", "comscore"],
    ["cws.conviva", "conviva"],
    ["conviva.com", "conviva"],
    ["urbanairship", "airship"],
    ["airship.com", "airship"],
    ["asnapieu.com", "airship"],
    ["graph.facebook", "facebook"],
    ["facebook.net", "facebook"],
    ["facebook.com", "facebook"],
    ["browser-intake.datadog", "dataDog"],
    ["datadoghq", "dataDog"],
    ["google-analytics", "firebase"],
    ["googletagmanager", "firebase"],
    ["app-measurement", "firebase"],
    ["firebase", "firebase"],
    ["amplitude.com", "amplitude"],
    ["mixpanel.com", "mixpanel"],
    ["segment.io", "segment"],
    ["segment.com", "segment"],
    ["bitmovin.com", "bitmovin"],
    ["mux.com", "mux"],
  ];
  const ANALYTICS_CAP = 400;

  function beaconHost(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    try {
      return new URL(raw.includes("://") ? raw : `https://${raw}`).hostname.toLowerCase();
    } catch (_) {
      return "";
    }
  }

  function hostHits(host, needle) {
    return !!needle && (host === needle || host.includes(needle));
  }

  function classifyAnalytics(flow, beacons) {
    const host = String(flow.host || "").toLowerCase();
    const path = String(flow.path || "").toLowerCase();
    const query = String(flow.query || "").toLowerCase();
    const blob = `${host}${path}?${query}`;
    const user = beaconHost(beacons && beacons.user);
    const player = beaconHost(beacons && beacons.player);
    if (hostHits(host, user)) return "user_beacon";
    if (hostHits(host, player)) return "player_beacon";
    if (blob.includes("userbeacon") || blob.includes("user-beacon") || blob.includes("user_beacon") || blob.includes("/user/") || blob.includes("ubeacon")) {
      return "user_beacon";
    }
    if (blob.includes("playerbeacon") || blob.includes("player-beacon") || blob.includes("player_beacon") || blob.includes("/player/") || blob.includes("pbeacon")) {
      return "player_beacon";
    }
    if (blob.includes("beacon") && /beacon|vlbeacon|viewlift/i.test(host + path)) {
      if (blob.includes("player")) return "player_beacon";
      if (blob.includes("user")) return "user_beacon";
      return "beacon";
    }
    for (const [marker, client] of ANALYTICS_CLIENTS) {
      if (host.includes(marker) || path.includes(marker)) return `client:${client}`;
    }
    return "";
  }

  function analyticsEventHint(flow, category) {
    const params = new URLSearchParams(String(flow.query || "").replace(/^\?/, ""));
    for (const key of ["eventName", "event_name", "event", "en", "name"]) {
      const value = params.get(key);
      if (value) return value;
    }
    if (String(category).startsWith("client:")) return category.slice("client:".length);
    return category || "";
  }

  const humanize = (key) => String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/^\w/, (char) => char.toUpperCase());

  const Debug = {
    async init() {
      this.bindTabs();
      window.ViewliftMemory?.init({ send: (name, payload) => this.sendCommand(name, payload) });
      window.ViewliftExplorer?.init({
        send: (name, payload) => this.sendCommand(name, payload),
        notify: (message) => this.notify(message),
        selectedViewId: () => (state.selectedView
          ? String(state.selectedView.describe_id || state.selectedView.id)
          : ""),
      });
      window.ViewliftLive?.init({
        send: (name, payload) => this.sendCommand(name, payload),
        clientId: () => state.clientId,
        paint: (payload) => this.renderScreenshot(payload, "live-shot"),
      });
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
      this.bindHangs();
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
      document.getElementById("ui-up")?.addEventListener("click", () => {
        const id = state.selectedView ? String(state.selectedView.id) : "";
        const parent = state.hierarchyParents.get(id);
        if (parent) this.selectView(parent, true);
      });
      document.getElementById("ui-branch")?.addEventListener("click", () => this.isolateBranch());
      document.getElementById("ui-branch-clear")?.addEventListener("click", () => {
        state.treeRoot = "";
        this.drawHierarchy();
      });
      document.getElementById("ui-match-next")?.addEventListener("click", () => this.stepMatch(1));
      document.getElementById("ui-match-prev")?.addEventListener("click", () => this.stepMatch(-1));
      document.getElementById("ui-zoom-in").addEventListener("click", () => {
        state.hierarchyZoom = Math.min(6, state.hierarchyZoom * 1.25);
        this.drawHierarchy3D();
      });
      document.getElementById("ui-zoom-out").addEventListener("click", () => {
        state.hierarchyZoom = Math.max(0.25, state.hierarchyZoom / 1.25);
        this.drawHierarchy3D();
      });
      this.bindLayoutToggles();
      this.bindHierarchyCanvas();
      await this.loadClients();
      await this.loadProfiles();
      await this.loadWaterfall();
      await this.loadAnalyticsTraffic();
      this.connectWebSocket();
      this.ensureAnalyticsFlowSocket();
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
        if (button.dataset.debugTab === "memory") window.ViewliftMemory?.onShow();
        window.ViewliftLive?.setActive(button.dataset.debugTab === "live");
        if (button.dataset.debugTab === "ott") {
          state.playbackDirty = false;
          this.paintPlaybackSummary();
          this.paintPlayback();
        }
        if (button.dataset.debugTab === "analytics") this.renderAnalyticsTraffic();
        if (button.dataset.debugTab === "hangs") this.markCrashTab(false);
      });
      tabs.querySelector("[data-debug-tab]")?.classList.add("active");
      const openHashedTab = () => {
        const name = location.hash.replace(/^#/, "");
        if (!name) return;
        const button = tabs.querySelector(`[data-debug-tab="${CSS.escape(name)}"]`);
        if (button) button.click();
      };
      openHashedTab();
      window.addEventListener("hashchange", openHashedTab);
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
      const deeplinkForm = document.getElementById("deeplink-form");
      if (deeplinkForm) {
        window.ViewliftNetwork?.fillTargetSelect(document.getElementById("deeplink-udid"));
        deeplinkForm.addEventListener("submit", async (event) => {
          event.preventDefault();
          const choice = window.ViewliftNetwork
            ? window.ViewliftNetwork.targetChoice(document.getElementById("deeplink-udid"))
            : { udid: "", kind: "" };
          const response = await fetch("/api/debug/deeplink", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              udid: choice.udid,
              kind: choice.kind,
              url: document.getElementById("deeplink-url").value.trim(),
              client_id: state.clientId || null,
            }),
          });
          const data = await response.json();
          document.getElementById("deeplink-status").textContent = data.detail || (data.ok ? "Opened." : "Failed");
        });
      }
      const locationForm = document.getElementById("location-form");
      if (locationForm && document.getElementById("loc-lat")) {
        window.ViewliftNetwork?.fillTargetSelect(document.getElementById("loc-udid"));
        locationForm.addEventListener("submit", (event) => {
          event.preventDefault();
          this.sendLocation(false);
        });
        const clear = document.getElementById("loc-clear");
        if (clear) clear.addEventListener("click", () => this.sendLocation(true));
      }
    },

    async sendLocation(clear) {
      const choice = window.ViewliftNetwork
        ? window.ViewliftNetwork.targetChoice(document.getElementById("loc-udid"))
        : { udid: "", kind: "" };
      const body = {
        udid: choice.udid,
        kind: choice.kind,
        clear,
        client_id: state.clientId || null,
      };
      if (!clear) {
        body.latitude = Number(document.getElementById("loc-lat").value);
        body.longitude = Number(document.getElementById("loc-lon").value);
      }
      const response = await fetch("/api/debug/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      const status = document.getElementById("location-status");
      if (status) status.textContent = data.detail || (data.ok ? "Location updated." : "Failed");
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
      if (online.length === 1) return online[0].id;
      if (online.length) return "";
      // Nothing is running. Show the app that just crashed so its stack is not lost with it.
      const crashed = Array.from(scope.byId.values())
        .filter((app) => app.crashed && scope.matches(app.id))
        .sort((a, b) => (b.crashed_at || 0) - (a.crashed_at || 0));
      return crashed.length ? crashed[0].id : "";
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
      const storageOpen = document.querySelector('[data-panel="storage"]:not(.hidden)');
      if (next !== previous) {
        if (state.clientId) await this.loadEvents();
        await this.loadSpm();
        if (storageOpen && state.clientId) this.openStorage("");
      } else if (storageOpen && state.storagePending && state.client?.state === "online") {
        this.openStorage(state.storagePath);
      }
      window.ViewliftLive?.onClient();
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
        viewQuery: "",
        matchIndex: 0,
        treeRoot: "",
        visibleRows: [],
        swiftUINames: new Map(),
        describeId: "",
        describeDetail: null,
        macro: null,
        bugImage: null,
        analytics: null,
        analyticsBeaconUrls: { user: "", player: "" },
        selectedAnalyticsId: "",
        storagePath: "",
        storageDbPath: "",
        selectedStoragePath: "",
        storageRead: "",
        playbackEvents: [],
        playbackDigest: {},
        playbackContentId: "",
        playbackPlaybackId: "",
        playbackDirty: false,
        focusPresses: [],
        crashes: [],
      });
      clearTimeout(state.describeTimer);
      state.describeTimer = 0;
      window.ViewliftExplorer?.reset();
      if (state.previewUrl) {
        URL.revokeObjectURL(state.previewUrl);
        state.previewUrl = "";
      }
      const clearIds = [
        "ott-summary", "ott-sections", "ott-spm", "ott-frameworks", "analytics-clients",
        "playback-summary", "playback-timeline", "playback-drm", "playback-waterfall", "ui-focus",
        "hangs-result", "crashes-live", "performance-metrics", "ui-tree", "ui-path", "ui-properties",
        "swiftui-result", "assets-result", "accessibility-result", "storage-inspector",
        "storage-files", "flags-result", "bug-result", "macro-result", "runtime-result", "push-result",
      ];
      clearIds.forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.innerHTML = "";
      });
      const shot = document.getElementById("ui-shot");
      if (shot) {
        shot.hidden = true;
        shot.replaceChildren();
      }
      const filter = document.getElementById("ui-filter");
      if (filter) filter.value = "";
      ["performance-chart", "ui-3d", "bug-canvas"].forEach((id) => {
        const canvas = document.getElementById(id);
        if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
      });
      window.ViewliftMemory?.reset();
      window.ViewliftLive?.reset();
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

    async syncTargetSelect(select) {
      if (!select || !window.ViewliftNetwork) return;
      await window.ViewliftNetwork.fillTargetSelect(select);
      const client = state.client;
      if (!client) return;
      const options = [...select.options];
      if (client.is_simulator && client.device_key && options.some((option) => option.value === client.device_key)) {
        select.value = client.device_key;
        return;
      }
      const model = client.device_model || "";
      const matches = options.filter((option) => (
        option.dataset.kind === "device" && model && option.dataset.model === model
      ));
      if (matches.length === 1) select.value = matches[0].value;
    },

    renderClientStatus() {
      const node = document.getElementById("debug-client-status");
      const name = document.getElementById("debug-client-name");
      this.renderIdentity();
      window.ViewliftMemory?.setClient(state.client);
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
      this.syncTargetSelect(document.getElementById("deeplink-udid"));
      this.syncTargetSelect(document.getElementById("loc-udid"));
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

    /* Native confirm() and alert() return false or do nothing in embedded browsers, so use the page. */
    confirmAction(message) {
      return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.className = "debug-confirm";
        overlay.setAttribute("role", "alertdialog");
        overlay.setAttribute("aria-modal", "true");
        const box = document.createElement("div");
        box.className = "debug-confirm-box";
        const text = document.createElement("p");
        text.textContent = message;
        const actions = document.createElement("div");
        actions.className = "debug-confirm-actions";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "btn ghost";
        cancel.textContent = "Cancel";
        const accept = document.createElement("button");
        accept.type = "button";
        accept.className = "btn";
        accept.textContent = "Run";
        actions.append(cancel, accept);
        box.append(text, actions);
        overlay.append(box);
        const finish = (value) => {
          document.removeEventListener("keydown", onKey, true);
          overlay.remove();
          resolve(value);
        };
        const onKey = (event) => {
          if (event.key === "Escape") finish(false);
          if (event.key === "Enter") finish(true);
        };
        cancel.addEventListener("click", () => finish(false));
        accept.addEventListener("click", () => finish(true));
        overlay.addEventListener("click", (event) => {
          if (event.target === overlay) finish(false);
        });
        document.addEventListener("keydown", onKey, true);
        document.body.append(overlay);
        accept.focus();
      });
    },

    notify(message) {
      let bar = document.getElementById("debug-notice");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "debug-notice";
        bar.className = "debug-notice";
        bar.setAttribute("role", "status");
        document.body.append(bar);
      }
      bar.textContent = message;
      bar.hidden = false;
      window.clearTimeout(state.noticeTimer);
      state.noticeTimer = window.setTimeout(() => { bar.hidden = true; }, 6000);
    },

    async sendCommand(name, payload) {
      if (!state.clientId) {
        this.notify("Choose one app in Scope first. Several apps may be running on this device.");
        return false;
      }
      let confirmation = null;
      if (MUTATING.has(name)) {
        const approved = await this.confirmAction(`Run ${name} on ${state.client?.app_name || "the app"}?`);
        if (!approved) return false;
        const tokenResponse = await fetch("/api/debug/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: state.clientId, command: name }),
        });
        const token = await tokenResponse.json().catch(() => ({}));
        if (!tokenResponse.ok) {
          this.notify(token.detail || "Confirmation failed");
          return false;
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
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
        this.notify(detail || "Command failed");
      }
      return response.ok;
    },

    connectWebSocket() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/ws/debug`);
      socket.addEventListener("message", (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (data.type === "debug-client" && data.client?.id === state.clientId) this.loadClients();
        if (data.type === "debug-event" && data.event?.client_id === state.clientId) {
          this.handleEvent(data.event, true);
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
      await this.loadCrashes();
    },

    /* Crashes are fetched on their own: a busy app buries them under performance samples. */
    async loadCrashes() {
      if (!state.clientId) return;
      try {
        const response = await fetch(`/api/debug/crashes?client_id=${encodeURIComponent(state.clientId)}&limit=20`);
        if (!response.ok) return;
        for (const row of (await response.json()).events || []) this.mergeCrash(row);
      } catch (_) {
        return;
      }
      this.renderCrashes();
    },

    handleEvent(event, live = false) {
      if (event.module === "crash") {
        this.onCrash(event, live);
        return;
      }
      if (event.module === "hang" && event.kind === "detected") {
        this.mergeHang(event.payload || {});
        return;
      }
      if (event.module === "performance" && event.kind === "sample") {
        state.samples.push(event.payload);
        if (state.samples.length > 120) state.samples.shift();
        this.renderPerformance();
        return;
      }
      if (event.module === "memory" && (event.kind === "live" || event.kind === "leak")) {
        window.ViewliftMemory?.onEvent(event.kind, event.payload);
        return;
      }
      if (event.module === "activity" && event.kind === "live") {
        window.ViewliftLive?.onLive(event.payload || {});
        return;
      }
      if (event.module === "activity" && event.kind === "frame") {
        window.ViewliftLive?.onFrame(event.payload || {});
        return;
      }
      if (event.module === "playback" && event.kind === "digest") {
        this.applyPlaybackDigest(event.payload || {});
        return;
      }
      if (event.module === "playback" && event.kind === "event") {
        this.pushPlayback(event.payload || {});
        return;
      }
      if (event.module === "ui" && event.kind === "remote") {
        this.pushRemote(event.payload || {});
        return;
      }
      if (event.module === "ui" && event.kind === "focus") {
        this.renderFocus({ focused: event.payload || {}, presses: state.focusPresses });
        return;
      }
      if (event.kind !== "command-result") return;
      const data = event.payload || {};
      if (!data.ok) {
        if (event.module === "memory" && window.ViewliftMemory) {
          window.ViewliftMemory.showError(data.error || "Command failed", data.name);
          return;
        }
        if (data.name === "ui.describe") {
          this.renderDescribe({ found: false, error: data.error || "Command failed" });
          return;
        }
        if (data.name === "ui.layers") {
          state.layerPending = false;
          this.paintLayerStatus("Layer images need an app rebuild with this SDK. Using the window screenshot.");
          return;
        }
        if (data.name === "ui.screenshot" && window.ViewliftLive?.takeScreenshotTarget()) {
          window.ViewliftLive.showError(data.error || "Screenshot failed");
          return;
        }
        if (String(data.name || "").startsWith("activity.")) {
          window.ViewliftLive?.onCommandError(data.name, data.error || "Command failed");
          return;
        }
        if (data.name === "storage.files") {
          clearTimeout(state.storageTimer);
          this.storageMessage(data.error || "Could not list this folder.", true);
          return;
        }
        if (event.module === "storage") clearTimeout(state.storageInfoTimer);
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
      if (name === "playback.snapshot") return this.renderPlayback(result);
      if (name === "playback.drm") return this.renderPlaybackDRM(result);
      if (name === "playback.waterfall") return this.renderPlaybackWaterfall(result);
      if (name === "ui.focus") return this.renderFocus(result);
      if (name === "nav.open_url") {
        const node = document.getElementById("nav-status");
        if (node) {
          node.textContent = result.opened ? `Opened ${result.url || ""}` : `Could not open ${result.url || ""}`;
        }
        return;
      }
      if (name === "hang.list") return this.renderHangs(result);
      if (name === "crash.list") return this.renderStacks(name, result);
      if (name.startsWith("memory.")) return window.ViewliftMemory?.onResult(name, result);
      if (window.ViewliftExplorer?.onResult(name, result)) return;
      if (name === "ui.hierarchy") return this.renderHierarchy(result);
      if (name === "ui.layers") return this.applyLayers(result);
      if (name === "ui.describe") return this.renderDescribe(result);
      if (name === "ui.screenshot") {
        const liveTarget = window.ViewliftLive?.takeScreenshotTarget();
        return this.renderScreenshot(result, liveTarget || "ui-shot");
      }
      if (name.startsWith("activity.")) return window.ViewliftLive?.onResult(name, result);
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
          target.innerHTML = `<tr><td colspan="7" class="muted">No matching analytics traffic yet.</td></tr>`;
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
          host.innerHTML = window.ViewliftNetwork.copyBlockHtml(JSON.stringify(flow, null, 2), "Copy JSON");
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
      if (on) this.followAnalytics();
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

    analyticsBucket(category) {
      if (category === "user_beacon" || category === "player_beacon" || category === "beacon") return category;
      return "clients";
    },

    recountAnalytics(data) {
      const rows = [
        ...(data.user_beacon || []),
        ...(data.player_beacon || []),
        ...(data.beacon || []),
        ...(data.clients || []),
      ];
      data.counts = {};
      for (const row of rows) {
        data.counts[row.category] = (data.counts[row.category] || 0) + 1;
      }
      data.total = rows.length;
    },

    ingestAnalyticsFlow(flow) {
      if (!flow || !flow.id) return;
      if (window.VLScope && !window.VLScope.matches(flow.client_id)) return;
      const category = classifyAnalytics(flow, state.analyticsBeaconUrls);
      if (!category) return;
      const data = state.analytics || {
        counts: {},
        user_beacon: [],
        player_beacon: [],
        beacon: [],
        clients: [],
        total: 0,
      };
      const row = {
        id: flow.id,
        category,
        method: flow.method,
        host: flow.host,
        path: flow.path,
        query: flow.query || "",
        status: flow.status,
        created_at: flow.created_at,
        params: {},
        event_hint: analyticsEventHint(flow, category),
      };
      const bucket = this.analyticsBucket(category);
      const list = data[bucket] || [];
      const index = list.findIndex((item) => item.id === row.id);
      if (index >= 0) {
        const prev = list[index];
        const hint = row.event_hint && row.event_hint !== category
          ? row.event_hint
          : (prev.event_hint || row.event_hint);
        list[index] = { ...prev, ...row, params: prev.params || {}, event_hint: hint };
      } else {
        list.unshift(row);
      }
      data[bucket] = list.slice(0, ANALYTICS_CAP);
      this.recountAnalytics(data);
      state.analytics = data;
      this.scheduleAnalyticsRender();
    },

    scheduleAnalyticsRender() {
      if (state.analyticsFrame) return;
      state.analyticsFrame = requestAnimationFrame(() => {
        state.analyticsFrame = 0;
        this.renderAnalyticsTraffic();
        this.followAnalytics();
      });
    },

    ensureAnalyticsFlowSocket() {
      const current = state.analyticsFlowWs;
      if (current && (current.readyState === 0 || current.readyState === 1)) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/ws/flows`);
      state.analyticsFlowWs = socket;
      socket.addEventListener("message", (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch (_) { return; }
        if (msg.type === "flow" && msg.flow) this.ingestAnalyticsFlow(msg.flow);
      });
      socket.addEventListener("close", () => {
        if (state.analyticsFlowWs !== socket) return;
        state.analyticsFlowWs = null;
        setTimeout(() => this.ensureAnalyticsFlowSocket(), 1500);
      });
    },

    paramsHtml(params) {
      const entries = Object.entries(params || {});
      if (!entries.length) return `<p class="muted">No parsed params</p>`;
      return `<dl class="ott-kv">${entries.map(([key, value]) => {
        const text = value && typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
        const body = value && typeof value === "object"
          ? window.ViewliftNetwork.copyBlockHtml(text, "Copy JSON")
          : escapeHtml(text);
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
      const player = snapshot.player && typeof snapshot.player === "object" ? snapshot.player : {};
      if (player.content_id) state.playbackContentId = String(player.content_id);
      if (player.playback_id) state.playbackPlaybackId = String(player.playback_id);
      this.paintPlayback();
    },

    renderPlayback(result) {
      if (result.content_id) state.playbackContentId = String(result.content_id);
      if (result.playback_id) state.playbackPlaybackId = String(result.playback_id);
      state.playbackEvents = result.events || [];
      state.playbackDigest = {
        status: result.status,
        waiting_reason: result.waiting_reason,
        stalled: result.stalled,
        live_edge_lag_sec: result.live_edge_lag_sec,
        observed_bps: result.observed_bps,
        indicated_bps: result.indicated_bps,
        presentation_width: result.presentation_width,
        presentation_height: result.presentation_height,
        dropped_frames: result.dropped_frames,
        external_playback: result.external_playback,
        error_domain: result.error_domain,
        error_code: result.error_code,
      };
      state.playbackDirty = false;
      this.paintPlaybackSummary();
      this.paintPlayback();
    },

    applyPlaybackDigest(payload) {
      const next = { ...(state.playbackDigest || {}) };
      [
        "status", "waiting_reason", "stalled", "live_edge_lag_sec", "observed_bps",
        "indicated_bps", "presentation_width", "presentation_height", "dropped_frames",
        "external_playback", "error_domain", "error_code",
      ].forEach((key) => {
        if (payload[key] !== undefined && payload[key] !== null) next[key] = payload[key];
      });
      if (payload.content_id) state.playbackContentId = String(payload.content_id);
      if (payload.playback_id) state.playbackPlaybackId = String(payload.playback_id);
      state.playbackDigest = next;
      this.schedulePlaybackPaint();
    },

    applyPlaybackEvent(payload) {
      const next = { ...(state.playbackDigest || {}) };
      const kind = payload.kind;
      if (kind === "status") {
        if (payload.status) next.status = payload.status;
        if (payload.waiting_reason !== undefined) next.waiting_reason = payload.waiting_reason;
      } else if (kind === "live_edge" && payload.lag_sec !== undefined) {
        next.live_edge_lag_sec = payload.lag_sec;
      } else if (kind === "bitrate") {
        if (payload.observed_bps !== undefined) next.observed_bps = payload.observed_bps;
        if (payload.indicated_bps !== undefined) next.indicated_bps = payload.indicated_bps;
      } else if (kind === "variant") {
        next.presentation_width = payload.width;
        next.presentation_height = payload.height;
      } else if (kind === "dropped_frames") {
        next.dropped_frames = payload.total;
      } else if (kind === "stall_start") {
        next.stalled = true;
      } else if (kind === "stall_end") {
        next.stalled = false;
      } else if (kind === "external_playback") {
        next.external_playback = payload.active;
      } else if (kind === "item_failed" || kind === "item_error_log") {
        next.error_domain = payload.error_domain || "";
        next.error_code = payload.error_code;
      }
      state.playbackDigest = next;
    },

    pushPlayback(payload) {
      const contentId = payload.content_id || "";
      if (contentId) state.playbackContentId = contentId;
      if (payload.playback_id) state.playbackPlaybackId = String(payload.playback_id);
      state.playbackEvents = [payload, ...state.playbackEvents].slice(0, 120);
      this.applyPlaybackEvent(payload);
      this.schedulePlaybackPaint();
    },

    schedulePlaybackPaint() {
      if (state.playbackPaint) return;
      state.playbackPaint = window.requestAnimationFrame(() => {
        state.playbackPaint = 0;
        const panel = document.querySelector('[data-panel="ott"]');
        if (panel?.classList.contains("hidden")) {
          state.playbackDirty = true;
          return;
        }
        state.playbackDirty = false;
        this.paintPlaybackSummary();
        this.paintPlayback();
      });
    },

    paintPlaybackSummary() {
      const summary = document.getElementById("playback-summary");
      if (!summary) return;
      const result = state.playbackDigest || {};
      const variant = result.presentation_width && result.presentation_height
        ? `${formatMetric(result.presentation_width)}×${formatMetric(result.presentation_height)}`
        : "";
      const error = result.error_domain
        ? `${result.error_domain} ${formatMetric(result.error_code)}`.trim()
        : "";
      const rows = [
        ["Status", result.status],
        ["Waiting", result.waiting_reason],
        ["Stalled", result.stalled],
        ["Live edge lag", formatMetric(result.live_edge_lag_sec)],
        ["Observed bps", formatMetric(result.observed_bps)],
        ["Variant", variant],
        ["Dropped frames", formatMetric(result.dropped_frames)],
        ["External", result.external_playback],
        ["Error", error],
      ].filter(([, value]) => value !== undefined && value !== null && value !== "" && value !== false);
      summary.innerHTML = rows
        .map(([label, value]) => `<div class="metric"><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`)
        .join("");
    },

    paintPlayback() {
      const align = document.getElementById("playback-align");
      const contentId = state.playbackContentId;
      const playbackId = state.playbackPlaybackId;
      if (align) {
        const label = contentId
          ? `Aligned to content ${contentId}${playbackId ? ` · playback ${playbackId}` : ""}.`
          : "No content id yet. Events still show until the player reports one.";
        align.textContent = `${label} Segment bytes and license material stay on the device.`;
      }
      const target = document.getElementById("playback-timeline");
      if (!target) return;
      const rows = state.playbackEvents.filter((row) => {
        if (!contentId || !row.content_id) return true;
        return row.content_id === contentId;
      }).slice(0, 40);
      if (!rows.length) {
        target.innerHTML = `<p class="muted">No playback events yet. Play a title, then refresh.</p>`;
        return;
      }
      target.innerHTML = rows.map((row) => {
        const bad = ["stall_start", "buffer_empty", "item_failed", "item_error_log"].includes(row.kind)
          || (row.kind === "drm" && row.phase === "failed");
        const when = row.timestamp ? new Date(row.timestamp * 1000).toISOString().slice(11, 19) : "";
        return `<div class="playback-row${bad ? " is-bad" : ""}"><span>${escapeHtml(when)}</span><span class="playback-kind">${escapeHtml(row.kind || "")}</span><span>${escapeHtml(this.playbackDetail(row))}</span></div>`;
      }).join("");
    },

    playbackDetail(row) {
      const skip = new Set(["kind", "timestamp", "content_id", "playback_id", "secrets_exposed"]);
      return Object.entries(row)
        .filter(([key, value]) => !skip.has(key) && value !== null && value !== "")
        .map(([key, value]) => `${key}=${typeof value === "number" ? formatMetric(value) : (typeof value === "object" ? JSON.stringify(value) : value)}`)
        .join(" · ");
    },

    renderPlaybackDRM(result) {
      const target = document.getElementById("playback-drm");
      if (!target) return;
      const events = result.events || [];
      if (!events.length) {
        target.innerHTML = `<p class="muted">No FairPlay steps yet. The host reports timing with noteDRM. SPC and CKC stay on the device.</p>`;
        return;
      }
      const head = ["time", "phase", "persistent", "duration_ms", "error_domain", "error_code"];
      target.innerHTML = `<table class="playback-table"><thead><tr>${head.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr></thead><tbody>${events.map((row) => `<tr>${head.map((key) => `<td>${escapeHtml(key === "time" ? this.playbackClock(row.timestamp) : row[key] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    },

    renderPlaybackWaterfall(result) {
      const target = document.getElementById("playback-waterfall");
      if (!target) return;
      const entries = result.entries || [];
      if (!entries.length) {
        target.innerHTML = `<p class="muted">No playlist or segment requests yet.</p>`;
        return;
      }
      const head = ["time", "kind", "status", "bytes", "ttfb_ms", "duration_ms", "host", "path"];
      target.innerHTML = `<table class="playback-table"><thead><tr>${head.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr></thead><tbody>${entries.map((row) => `<tr>${head.map((key) => `<td>${escapeHtml(key === "time" ? this.playbackClock(row.timestamp) : row[key] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    },

    playbackClock(timestamp) {
      if (!timestamp) return "";
      return new Date(timestamp * 1000).toISOString().slice(11, 19);
    },

    renderFocus(result) {
      const target = document.getElementById("ui-focus");
      if (!target) return;
      if (result.focused && result.focused.found) state.focusView = result.focused;
      const focused = result.focused && result.focused.found ? result.focused : (state.focusView || result.focused || {});
      const rejection = result.last_rejection === undefined ? state.focusRejection : result.last_rejection;
      const presses = result.presses || state.focusPresses;
      state.focusPresses = presses;
      const lines = [];
      if (focused.found) {
        lines.push(`<p>Focused <strong>${escapeHtml(focused.class || "")}</strong> id ${escapeHtml(focused.id || "")} ${escapeHtml(focused.identifier || "")}</p>`);
        lines.push(`<p class="muted">${Math.round(focused.x || 0)}, ${Math.round(focused.y || 0)} · ${Math.round(focused.width || 0)}×${Math.round(focused.height || 0)} ${escapeHtml(focused.heading || "")}</p>`);
        if (focused.id) {
          lines.push(`<button type="button" class="btn ghost" id="ui-focus-highlight">Highlight</button>`);
        }
      } else {
        lines.push(`<p class="muted">Nothing focused.</p>`);
      }
      if (rejection && typeof rejection === "object") {
        state.focusRejection = rejection;
        lines.push(`<p class="warn-text">Last focus rejection: ${escapeHtml(rejection.class || "")} ${escapeHtml(rejection.heading || "")}</p>`);
      }
      if (presses.length) {
        lines.push(`<p class="muted">Remote: ${escapeHtml(presses.map((item) => item.button).filter(Boolean).join(", "))}</p>`);
      }
      target.innerHTML = lines.join("");
      document.getElementById("ui-focus-highlight")?.addEventListener("click", () => {
        this.sendCommand("ui.highlight", { id: focused.id });
      });
    },

    pushRemote(payload) {
      state.focusPresses = [payload, ...state.focusPresses].slice(0, 40);
      const current = document.getElementById("ui-focus");
      if (!current) return;
      this.renderFocus({
        focused: state.focusView || { found: false },
        presses: state.focusPresses,
        last_rejection: state.focusRejection || null,
      });
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
          + window.ViewliftNetwork.copyBlockHtml(JSON.stringify(item, null, 2), "Copy JSON")
          + `</details>`;
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

    bindHangs() {
      document.getElementById("crashes-live")?.addEventListener("click", async (event) => {
        const seek = event.target.closest("[data-crash-seek]");
        if (seek) {
          const video = Array.from(document.querySelectorAll("[data-crash-video]")).find((item) => item.dataset.crashVideo === seek.dataset.crashSeek);
          if (video) {
            video.currentTime = Number(seek.dataset.time || 0);
            video.pause();
            video.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
          return;
        }
        const ai = event.target.closest("[data-crash-ai]");
        if (ai) {
          const entry = state.crashes[Number(ai.dataset.crashAi)];
          try {
            const response = await fetch(this.crashUrl(entry, "report.md"));
            if (!response.ok) throw new Error(String(response.status));
            await navigator.clipboard.writeText(await response.text());
            ai.textContent = "Copied for AI";
          } catch (error) {
            ai.textContent = "Copy failed";
          }
          return;
        }
        const copy = event.target.closest("[data-crash-copy]");
        if (!copy) return;
        const text = this.crashReportText(state.crashes[Number(copy.dataset.crashCopy)]);
        try {
          await navigator.clipboard.writeText(text);
          copy.textContent = "Copied";
        } catch (error) {
          copy.textContent = "Copy failed";
        }
      });
      document.getElementById("hangs-result")?.addEventListener("click", async (event) => {
        const copy = event.target.closest("[data-hang-copy]");
        if (copy) {
          const index = Number(copy.dataset.hangCopy);
          const text = this.hangReportText(state.hangs?.[index]);
          try {
            await navigator.clipboard.writeText(text);
            copy.textContent = "Copied";
          } catch (error) {
            copy.textContent = "Copy failed";
          }
          return;
        }
        const shot = event.target.closest(".hang-shot");
        if (shot) shot.classList.toggle("is-open");
      });
    },

    mergeHang(payload) {
      if (!payload || typeof payload !== "object") return;
      if (!state.hangs) state.hangs = [];
      const id = String(payload.id || "");
      const index = id ? state.hangs.findIndex((item) => item.id === id) : -1;
      if (index >= 0) state.hangs[index] = payload;
      else state.hangs.unshift(payload);
      if (state.hangs.length > 12) state.hangs.length = 12;
      this.paintHangs();
    },

    renderHangs(result) {
      state.hangs = [...(result.events || [])].reverse();
      this.paintHangs(result.note);
    },

    paintHangs(note) {
      const events = state.hangs || [];
      const target = document.getElementById("hangs-result");
      if (!target) return;
      if (!events.length) {
        target.innerHTML = `<p class="muted">No hangs yet. A main-thread stall over 400 ms is recorded with the screen name, a screenshot from when the UI recovered, taps and scrolls before the stall, and console lines. Reproduce the hitch, then press Hang stacks.</p>`;
        return;
      }
      target.innerHTML = `<p class="muted">${events.length} hang(s). Newest first. ${escapeHtml(note || "")}</p>`
        + events.map((event, index) => this.hangCard(event, index)).join("");
    },

    hangCard(event, index) {
      const duration = Math.round(event.duration_ms || 0);
      const screen = event.screen_class || "Unknown screen";
      const title = event.screen_title ? `“${event.screen_title}”` : "";
      const severe = duration >= 1000 ? " hang-long" : "";
      return `<article class="hang-card${severe}">`
        + `<header class="hang-head"><h3>${duration} ms · ${escapeHtml(screen)} ${escapeHtml(title)}</h3>`
        + `<span class="muted">${escapeHtml(this.hangClock(event.timestamp))} · ${escapeHtml(this.hangStackNote(event))}</span>`
        + `<button type="button" class="btn ghost" data-hang-copy="${index}">Copy report</button></header>`
        + `<div class="hang-layout">${this.hangShot(event)}${this.hangFacts(event)}</div>`
        + this.hangStack(event)
        + `</article>`;
    },

    hangStackNote(event) {
      if (event.stack_source === "blocked") return "Stack sampled while the main thread was stuck.";
      return "Stack taken after the main thread resumed. The blocking frame may already be gone.";
    },

    hangClock(timestamp) {
      const value = Number(timestamp);
      if (!value) return "";
      return new Date(value * 1000).toLocaleTimeString();
    },

    hangShot(event) {
      const shot = event.screenshot || {};
      const mime = shot.mime === "image/jpeg" || shot.mime === "image/png" ? shot.mime : "";
      const raw = String(shot.base64 || "").replace(/[^A-Za-z0-9+/=]/g, "");
      if (!mime || !raw) {
        const note = event.screenshot_note || "No screenshot. The window was not ready when the stall ended.";
        return `<p class="hang-shot-missing muted">${escapeHtml(note)}</p>`;
      }
      const label = [event.screen_class, event.screen_title].filter(Boolean).join(" ");
      return `<img class="hang-shot" alt="${escapeAttr(label || "Screen when the hang ended")}" width="200" src="data:${mime};base64,${raw}">`;
    },

    hangFacts(event) {
      return `<div class="hang-facts">${this.hangSuspect(event)}${this.hangActions(event)}${this.hangConsole(event)}</div>`;
    },

    hangSuspect(event) {
      const frame = event.suspect;
      if (!frame) {
        return `<p class="hang-suspect">No file or app frame in the stack. Start from the screen name and the last tap.</p>`;
      }
      const loc = frame.file && frame.line != null ? `${frame.file}:${frame.line}` : (frame.file || "");
      const symbol = frame.symbol || frame.class || frame.raw || "";
      return `<p class="hang-suspect">${escapeHtml(frame.class || "App frame")}</p>`
        + `<p class="mono">${escapeHtml(symbol)}${loc ? ` · ${escapeHtml(loc)}` : ""}</p>`;
    },

    hangActions(event) {
      const actions = event.actions || [];
      if (!actions.length) {
        return `<h4>User actions</h4><p class="muted">No tap or scroll in the last few seconds.</p>`;
      }
      const items = actions.map((action) => {
        const bits = [
          action.kind || "touch",
          action.direction || "",
          action.label ? `“${action.label}”` : "",
          action.target_class || "",
          action.screen_class || "",
          this.hangAgo(action.ago_ms),
        ].filter(Boolean);
        return `<li>${escapeHtml(bits.join(" · "))}</li>`;
      }).join("");
      return `<h4>User actions</h4><ol class="hang-actions">${items}</ol>`;
    },

    hangConsole(event) {
      const lines = event.console || [];
      if (!lines.length) {
        return `<h4>Console</h4><p class="muted">No print, stderr, os_log, or SDK log in the window around this hang.</p>`;
      }
      const text = lines.map((line) => {
        const when = this.hangAgo(line.ago_ms);
        return `${line.source || "log"} · ${when} · ${line.message || ""}`;
      }).join("\n");
      return `<h4>Console</h4><pre class="hang-console">${escapeHtml(text)}</pre>`;
    },

    hangStack(event) {
      const frames = event.frames || [];
      if (!frames.length) return "";
      const executable = event.executable || "";
      const rows = frames.map((frame) => {
        const loc = frame.file && frame.line != null ? `${frame.file}:${frame.line}` : (frame.file || "");
        const app = executable && frame.image === executable;
        return `<div class="stack-frame${app ? " is-app" : ""}"><span class="loc">${escapeHtml(loc)}</span> `
          + `<span class="sym">${escapeHtml(frame.class ? `${frame.class} · ` : "")}${escapeHtml(frame.symbol || frame.raw || "")}</span></div>`;
      }).join("");
      return `<details class="hang-stack"><summary>Stack · ${frames.length} frames · app frames highlighted</summary>${rows}</details>`;
    },

    hangAgo(value) {
      const ms = Number(value);
      if (!Number.isFinite(ms)) return "";
      if (ms < 1000) return `${Math.round(ms)} ms before`;
      return `${(ms / 1000).toFixed(1)} s before`;
    },

    hangReportText(event) {
      if (!event) return "";
      const lines = [
        `Hang ${Math.round(event.duration_ms || 0)} ms`,
        `When ${this.hangClock(event.timestamp)}`,
        `Screen ${event.screen_class || "unknown"} ${event.screen_title || ""}`.trim(),
        this.hangStackNote(event),
      ];
      const frame = event.suspect || {};
      if (frame.symbol || frame.class) {
        const loc = frame.file && frame.line != null ? `${frame.file}:${frame.line}` : "";
        lines.push(`Suspect ${frame.class || ""} ${frame.symbol || ""} ${loc}`.trim());
      }
      lines.push("", "User actions");
      for (const action of event.actions || []) {
        lines.push([
          action.kind, action.direction, action.label, action.target_class, action.screen_class, this.hangAgo(action.ago_ms),
        ].filter(Boolean).join(" · "));
      }
      if (!(event.actions || []).length) lines.push("(none)");
      lines.push("", "Console");
      for (const line of event.console || []) {
        lines.push(`${line.source || "log"} · ${this.hangAgo(line.ago_ms)} · ${line.message || ""}`);
      }
      if (!(event.console || []).length) lines.push("(none)");
      lines.push("", "Stack");
      for (const frameLine of event.frames || []) {
        const loc = frameLine.file && frameLine.line != null ? `${frameLine.file}:${frameLine.line}` : "";
        lines.push(`${frameLine.image || ""} ${frameLine.class || ""} ${frameLine.symbol || frameLine.raw || ""} ${loc}`.trim());
      }
      if (event.screenshot) lines.push("", "Screenshot is on the Hangs card.");
      return lines.join("\n");
    },

    /* crash.list answers with the device's own events. They merge into the same cards as stored crashes. */
    renderStacks(name, result) {
      for (const event of result.events || []) this.mergeCrash({ payload: event });
      this.renderCrashes();
      const target = document.getElementById("hangs-result");
      if (target) {
        target.innerHTML = `<p class="muted">${escapeHtml(name)} · ${(result.events || []).length} event(s) from this run. ${escapeHtml(result.note || "")}</p>`;
      }
    },

    crashKey(payload, id) {
      const pid = Number(payload.pid);
      return pid > 0 ? `pid:${pid}` : String(id || payload.id || "");
    },

    /* One card per process death. The macOS report beats the SDK record, and a symbolicated copy beats a raw one. */
    mergeCrash(row) {
      const payload = row.payload || row;
      if (!payload || typeof payload !== "object") return;
      const id = row.id || payload.id;
      const key = this.crashKey(payload, id);
      const stored = String(id || "").startsWith("crash-");
      const entry = { key, id, payload, stored, client_id: row.client_id || state.clientId, created_at: row.created_at || "" };
      const index = state.crashes.findIndex((item) => item.key === key);
      if (index < 0) {
        state.crashes.push(entry);
      } else {
        // The console merges every source into the stored card, so a stored copy always wins.
        const current = state.crashes[index];
        const old = current.payload;
        const keep = (current.stored && !stored)
          || (!stored && old.source === "crash-report" && payload.source !== "crash-report")
          || (!stored && old.symbolication && !payload.symbolication && old.source === payload.source);
        if (!keep) state.crashes[index] = entry;
      }
      state.crashes.sort((a, b) => Number(b.payload.timestamp || 0) - Number(a.payload.timestamp || 0));
    },

    onCrash(event, live) {
      this.mergeCrash(event);
      this.renderCrashes();
      // The page-wide toast in shell.js announces a live crash. Here only the Hangs tab is marked.
      if (live && !(event.payload || {}).previous_session) this.markCrashTab(true);
    },

    markCrashTab(on) {
      document.querySelector('[data-debug-tab="hangs"]')?.classList.toggle("has-crash", on);
    },

    crashFrameLoc(frame) {
      if (frame.file) return frame.line != null ? `${frame.file}:${frame.line}` : frame.file;
      if (frame.image) return `${frame.image}${frame.offset != null ? ` +${frame.offset}` : ""}`;
      return frame.address || "";
    },

    crashIsApp(frame, executable) {
      return !!executable && frame.image === executable;
    },

    crashSuspect(payload) {
      const frames = payload.frames || [];
      const frame = frames.find((item) => this.crashIsApp(item, payload.executable) && item.file) || frames.find((item) => item.file);
      return frame ? this.crashFrameLoc(frame) : "";
    },

    crashSourceLabel(payload) {
      return {
        "sdk-signal": "Fatal signal (SDK)",
        "sdk-exception": "Uncaught exception (SDK)",
        "crash-report": "macOS crash report",
      }[payload.source] || "Crash";
    },

    crashClock(payload) {
      const value = Number(payload.timestamp);
      return value ? new Date(value * 1000).toLocaleString() : "";
    },

    crashFrameRows(frames, executable) {
      return (frames || []).map((frame) => {
        const app = this.crashIsApp(frame, executable);
        const tag = frame.inlined ? ` <span class="muted">(inlined)</span>` : "";
        const image = frame.image ? ` <span class="muted">${escapeHtml(frame.image)}</span>` : "";
        const symbol = frame.symbol || frame.raw || frame.address || "";
        return `<div class="stack-frame${app ? " is-app" : ""}"><span class="idx">${escapeHtml(frame.index ?? "")}</span> `
          + `<span class="loc">${escapeHtml(this.crashFrameLoc(frame))}</span> `
          + `<span class="sym">${escapeHtml(symbol)}</span>${tag}${image}</div>`;
      }).join("");
    },

    crashSymbolNote(payload) {
      const info = payload.symbolication;
      if (!info) {
        return payload.source === "crash-report"
          ? ""
          : `<p class="muted crash-note">Resolving file and line from the dSYM…</p>`;
      }
      if (info.status === "missing_dsym" || info.status === "partial") {
        const names = (info.missing_dsym || []).join(", ");
        return `<p class="crash-note crash-warn">No dSYM found for ${escapeHtml(names)}. Build this app in Debug on this Mac, then reopen the crash, so file and line numbers resolve.</p>`;
      }
      return "";
    },

    crashUrl(entry, suffix) {
      return `/api/debug/crashes/${encodeURIComponent(entry.id)}/${suffix}${suffix.includes("?") ? "&" : "?"}client_id=${encodeURIComponent(entry.client_id || state.clientId)}`;
    },

    crashCode(site) {
      const rows = site.snippet || [];
      if (!rows.length) return site.snippet_note ? `<p class="muted crash-note">${escapeHtml(site.snippet_note)}</p>` : "";
      return `<pre class="crash-code">${rows.map((row) => `<span class="crash-code-line${row.crash ? " is-crash" : ""}">`
        + `<span class="crash-code-num">${escapeHtml(row.line)}</span>${escapeHtml(row.code)}</span>`).join("")}</pre>`;
    },

    crashWhat(payload) {
      const diagnosis = payload.diagnosis || {};
      const site = diagnosis.crash_site || {};
      const where = site.file
        ? `${site.file}${site.line != null ? `:${site.line}` : ""}`
        : (this.crashSuspect(payload) || "No file or line yet.");
      return `<section class="crash-what">`
        + `<p class="crash-headline">${escapeHtml(diagnosis.headline || payload.reason || payload.name || "The app crashed.")}</p>`
        + `<p class="hang-suspect">${escapeHtml(where)}${site.function ? ` <span class="muted">in ${escapeHtml(site.function)}</span>` : ""}</p>`
        + this.crashCode(site)
        + (diagnosis.root_cause ? `<p class="crash-cause"><strong>Likely cause.</strong> ${escapeHtml(diagnosis.root_cause)}</p>` : "")
        + (diagnosis.suggested_fix_area ? `<p class="crash-cause"><strong>Where to fix.</strong> ${escapeHtml(diagnosis.suggested_fix_area)}</p>` : "")
        + `</section>`;
    },

    /* Seconds into the video for an event that happened `before` seconds ahead of the crash. */
    crashVideoTime(replay, before) {
      const first = (replay.timeline || [])[0];
      if (!first || before == null) return null;
      const t = Number(first.before_crash_ms || 0) / 1000 - Number(before);
      return t >= 0 ? t : null;
    },

    crashReplay(entry) {
      const replay = entry.payload.replay;
      if (!replay) {
        return `<p class="muted crash-note">No replay. Turn on the Crash replay module in Device settings to record the seconds before a crash.</p>`;
      }
      if (replay.status === "ready" && entry.stored) {
        const video = this.crashUrl(entry, "replay.mp4");
        const seconds = Number(replay.duration_ms || 0) / 1000;
        return `<section class="crash-replay">`
          + `<video controls preload="metadata" playsinline data-crash-video="${escapeHtml(entry.key)}" poster="${escapeHtml(this.crashUrl(entry, "poster.jpg"))}" src="${escapeHtml(video)}"></video>`
          + `<p class="crash-replay-meta"><span class="muted">${escapeHtml(replay.frames || 0)} frames · ${seconds.toFixed(1)} s before the crash · taps drawn</span>`
          + ` <a class="btn ghost" href="${escapeHtml(this.crashUrl(entry, "replay.mp4?download=1"))}" download>Download video</a></p>`
          + `</section>`;
      }
      const text = {
        pending: "Replay recorded. It uploads on the app's next launch.",
        uploading: `Uploading replay… ${replay.received || 0} of ${replay.total || "?"} parts.`,
        failed: `Replay could not be encoded: ${replay.error || "unknown error"}.`,
      }[replay.status] || "Replay pending.";
      return `<p class="muted crash-note">${escapeHtml(text)}</p>`;
    },

    crashTimeline(entry) {
      const rows = (entry.payload.diagnosis || {}).timeline || [];
      if (!rows.length) return "";
      const replay = entry.payload.replay || {};
      const seekable = replay.status === "ready";
      const items = rows.map((row) => {
        const t = seekable ? this.crashVideoTime(replay, row.before_crash_s) : null;
        const when = `-${Number(row.before_crash_s || 0).toFixed(1)}s`;
        const label = `<span class="crash-tl-when">${escapeHtml(when)}</span><span class="crash-tl-kind kind-${escapeHtml(row.kind)}">${escapeHtml(row.kind)}</span>${escapeHtml(row.text)}`;
        return t == null
          ? `<li>${label}</li>`
          : `<li><button type="button" class="crash-tl-seek" data-crash-seek="${escapeHtml(entry.key)}" data-time="${t.toFixed(2)}">${label}</button></li>`;
      }).join("");
      return `<details class="hang-stack" open><summary>Timeline · ${rows.length} events${seekable ? " · click to jump in the video" : ""}</summary><ol class="crash-timeline">${items}</ol></details>`;
    },

    crashContext(payload) {
      const diagnosis = payload.diagnosis || {};
      const context = payload.context || {};
      const parts = [];
      const steps = diagnosis.steps_to_reproduce || [];
      if (steps.length) {
        parts.push(`<details class="hang-stack" open><summary>Steps to reproduce</summary><ol class="crash-steps">${steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol></details>`);
      }
      const requests = context.requests || [];
      if (requests.length) {
        const rows = requests.map((item) => {
          const bad = item.error || Number(item.status) >= 400;
          return `<div class="stack-frame${bad ? " is-app" : ""}"><span class="loc">${escapeHtml(item.status ?? item.error ?? "-")}</span> `
            + `<span class="sym">${escapeHtml(`${item.method || ""} ${item.host || ""}${item.path || ""}`)}</span>`
            + (item.duration_ms != null ? ` <span class="muted">${Math.round(item.duration_ms)} ms</span>` : "") + `</div>`;
        }).join("");
        parts.push(`<details class="hang-stack"><summary>Last requests · ${requests.length}</summary>${rows}</details>`);
      }
      const consoleRows = context.console || [];
      if (consoleRows.length) {
        parts.push(`<details class="hang-stack"><summary>Console · ${consoleRows.length} lines</summary><pre class="crash-console">${consoleRows.map((line) => escapeHtml(line.message || "")).join("\n")}</pre></details>`);
      }
      const env = diagnosis.environment || {};
      if (Object.keys(env).length) {
        parts.push(`<details class="hang-stack"><summary>Environment</summary>${this.kvHtml(env)}</details>`);
      }
      return parts.join("");
    },

    crashCard(entry, index) {
      const payload = entry.payload;
      const frames = payload.frames || [];
      const badges = [this.crashSourceLabel(payload)];
      if (payload.previous_session) badges.push("Last session");
      if (payload.thread) badges.push(payload.thread);
      const threads = (payload.threads || []).map((thread) => `<details class="hang-stack"><summary>${escapeHtml(thread.label || "Thread")} · ${(thread.frames || []).length} frames</summary>${this.crashFrameRows(thread.frames, payload.executable)}</details>`).join("");
      const stackTitle = `Stack · ${frames.length} frame${frames.length === 1 ? "" : "s"} · app frames highlighted`;
      const aiButtons = entry.stored
        ? `<button type="button" class="btn" data-crash-ai="${index}">Copy for AI</button>`
          + `<a class="btn ghost" href="${escapeHtml(this.crashUrl(entry, "report.md?download=1"))}" download>Download report</a>`
        : "";
      return `<article class="hang-card crash-card">`
        + `<header class="hang-head"><h3>${escapeHtml(payload.name || "Crash")}</h3>`
        + `<span class="muted">${escapeHtml(this.crashClock(payload))}</span>`
        + `<span class="crash-actions">${aiButtons}<button type="button" class="btn ghost" data-crash-copy="${index}">Copy stack</button></span></header>`
        + `<p class="crash-badges">${badges.map((item) => `<span class="crash-badge">${escapeHtml(item)}</span>`).join("")}</p>`
        + (payload.reason ? `<p class="crash-reason">${escapeHtml(payload.reason)}</p>` : "")
        + `<div class="crash-layout"><div class="crash-main">${this.crashWhat(payload)}</div>`
        + `<div class="crash-side">${this.crashReplay(entry)}</div></div>`
        + this.crashTimeline(entry)
        + this.crashContext(payload)
        + this.crashSymbolNote(payload)
        + `<details class="hang-stack" open><summary>${escapeHtml(stackTitle)}</summary>${this.crashFrameRows(frames, payload.executable) || `<p class="muted">No frames were recorded.</p>`}</details>`
        + threads
        + (payload.report_file ? `<p class="muted">Report ${escapeHtml(payload.report_file)}</p>` : "")
        + `</article>`;
    },

    renderCrashes() {
      const target = document.getElementById("crashes-live");
      if (!target) return;
      if (!state.crashes.length) {
        target.innerHTML = "";
        return;
      }
      const html = `<h3 class="crash-title">Crashes · ${state.crashes.length}</h3>`
        + state.crashes.map((entry, index) => this.crashCard(entry, index)).join("");
      // Rewriting identical markup would restart a replay the user is watching.
      if (html === this.crashHtml) return;
      this.crashHtml = html;
      target.innerHTML = html;
    },

    crashReportText(entry) {
      if (!entry) return "";
      const payload = entry.payload;
      const lines = [
        `Crash ${payload.name || ""}`.trim(),
        `When ${this.crashClock(payload)}`,
        `Source ${this.crashSourceLabel(payload)}${payload.previous_session ? " (last session)" : ""}`,
        `Thread ${payload.thread || "unknown"}`,
        `Reason ${payload.reason || ""}`.trim(),
        `At ${this.crashSuspect(payload) || "unknown"}`,
        "",
        "Stack",
      ];
      for (const frame of payload.frames || []) {
        lines.push(`${frame.index ?? ""}  ${this.crashFrameLoc(frame)}  ${frame.symbol || frame.raw || ""}${frame.inlined ? " (inlined)" : ""}  ${frame.image || ""}`.trimEnd());
      }
      for (const thread of payload.threads || []) {
        lines.push("", `Thread ${thread.label || ""}`);
        for (const frame of thread.frames || []) {
          lines.push(`${frame.index ?? ""}  ${this.crashFrameLoc(frame)}  ${frame.symbol || frame.raw || ""}`.trimEnd());
        }
      }
      return lines.join("\n");
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
      state.describeDetail = null;
      state.treeRoot = "";
      state.matchIndex = 0;
      const flat = this.hierarchyNodes();
      state.hierarchyParents = new Map();
      state.hierarchyById = new Map();
      flat.forEach(({ node }) => {
        state.hierarchyById.set(String(node.id), node);
        (node.children || []).forEach((child) => state.hierarchyParents.set(String(child.id), String(node.id)));
      });
      const swiftUIDepth = new Map();
      flat.forEach(({ node }) => {
        const parent = state.hierarchyParents.get(String(node.id));
        const above = parent ? (swiftUIDepth.get(parent) || 0) : 0;
        swiftUIDepth.set(String(node.id), node.swiftui_node ? above + 1 : 0);
      });
      const collapsed = new Set();
      flat.forEach(({ node, depth }) => {
        if (!(node.children || []).length) return;
        if (node.swiftui_node) {
          // SwiftUI debug trees run deep. Open the first levels, like Xcode after a capture.
          if (swiftUIDepth.get(String(node.id)) >= 4) collapsed.add(String(node.id));
          return;
        }
        if (this.hostsSwiftUI(node)) return;
        const name = node.class || "";
        const wrapper = !node.text && !node.accessibility_identifier && !node.accessibility_label;
        if (name.startsWith("_") || (depth >= 2 && this.isSystemNode(node) && wrapper)) {
          collapsed.add(String(node.id));
        }
      });
      this.rebuildSwiftUINames();
      flat.forEach(({ node }) => {
        if (node.swiftui_node) return;
        if (this.isSystemNode(node) && !this.hostsSwiftUI(node)) return;
        let parent = state.hierarchyParents.get(String(node.id));
        while (parent) {
          collapsed.delete(parent);
          parent = state.hierarchyParents.get(parent);
        }
      });
      state.collapsed = collapsed;
      state.hierarchyShot = null;
      state.layerCache = null;
      state.layerImages = new Map();
      state.layerEmpty = new Set();
      state.layerCrop = new Set();
      state.layerProgress = { loaded: 0, total: Number(result.layers_available) || 0 };
      state.layerPending = false;
      if (state.layerProgress.total > 0) this.requestLayers(0);
      this.paintLayerStatus();
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
      const cached = state.hierarchyById.get(String(id));
      if (cached) return cached;
      const match = this.hierarchyNodes().find(({ node }) => String(node.id) === String(id));
      return match ? match.node : null;
    },

    async requestLayers(offset) {
      state.layerPending = true;
      const ok = await this.sendCommand("ui.layers", { offset, scale: Math.min(2, window.devicePixelRatio || 1) });
      if (!ok) {
        state.layerPending = false;
        this.paintLayerStatus("Layer images unavailable. Using the window screenshot.");
      }
    },

    applyLayers(result) {
      (result.layers || []).forEach((layer) => {
        if (!layer || !layer.base64) return;
        const image = new Image();
        image.onload = () => this.scheduleDraw3D();
        image.src = `data:${layer.mime || "image/png"};base64,${layer.base64}`;
        state.layerImages.set(String(layer.id), image);
      });
      (result.empty || []).forEach((id) => state.layerEmpty.add(String(id)));
      (result.crop || []).forEach((id) => state.layerCrop.add(String(id)));
      const next = Number(result.next_offset) || 0;
      state.layerProgress = { loaded: next, total: Number(result.total) || state.layerProgress.total };
      const more = !result.done && next > (Number(result.offset) || 0);
      if (state.layerPending && more) {
        this.requestLayers(next);
      } else {
        state.layerPending = false;
      }
      this.paintLayerStatus();
      this.scheduleDraw3D();
    },

    scheduleDraw3D() {
      if (state.draw3DFrame) return;
      state.draw3DFrame = requestAnimationFrame(() => {
        state.draw3DFrame = 0;
        this.drawHierarchy3D();
      });
    },

    paintLayerStatus(message) {
      const status = document.getElementById("ui-layer-status");
      if (!status) return;
      if (message) {
        status.textContent = message;
        return;
      }
      const { loaded, total } = state.layerProgress;
      if (!total) {
        status.textContent = "";
        return;
      }
      status.textContent = loaded >= total ? `Layers ${total}` : `Layers ${loaded}/${total}`;
    },

    selectView(id, reveal) {
      const node = this.findHierarchyNode(id);
      if (!node) return;
      if (state.treeRoot && String(id) !== state.treeRoot && !this.isUnderRoot(id, state.treeRoot)) {
        state.treeRoot = "";
      }
      state.selectedView = node;
      const matches = this.matchingIds();
      const at = matches.indexOf(String(id));
      if (at >= 0) state.matchIndex = at;
      if (reveal) {
        let parent = state.hierarchyParents.get(String(id));
        while (parent) {
          state.collapsed.delete(parent);
          parent = state.hierarchyParents.get(parent);
        }
      }
      this.drawHierarchy();
      document.querySelector(`#ui-tree [data-view-id="${CSS.escape(String(id))}"]`)
        ?.scrollIntoView({ block: "nearest" });
      if (node.synthetic && !node.describe_id) {
        clearTimeout(state.describeTimer);
        state.describeId = String(id);
        state.describeDetail = null;
        this.paintViewSheet(node, null, "");
        return;
      }
      const target = String(node.describe_id || id);
      const changed = String(state.describeId) !== target;
      if (changed) {
        state.explorerTab = "layout";
        state.describeDetail = null;
      }
      this.paintViewSheet(node, changed ? null : state.describeDetail, "");
      if (changed) this.scheduleDescribe(target);
    },

    scheduleDescribe(id) {
      state.describeId = String(id);
      clearTimeout(state.describeTimer);
      state.describeTimer = setTimeout(() => {
        if (state.describeId === String(id)) this.sendCommand("ui.describe", { id });
      }, 160);
    },

    isUnderRoot(id, rootId) {
      let parent = state.hierarchyParents.get(String(id));
      const guard = new Set();
      while (parent && !guard.has(parent)) {
        if (parent === String(rootId)) return true;
        guard.add(parent);
        parent = state.hierarchyParents.get(parent);
      }
      return false;
    },

    matchingIds() {
      const query = state.viewQuery.trim().toLowerCase();
      if (!query) return [];
      return this.hierarchyNodes()
        .filter(({ node }) => this.viewMatchesQuery(node, query))
        .map(({ node }) => String(node.id));
    },

    stepMatch(delta) {
      const ids = this.matchingIds();
      if (!ids.length) return;
      state.matchIndex = (state.matchIndex + delta + ids.length) % ids.length;
      this.selectView(ids[state.matchIndex], true);
    },

    isolateBranch() {
      if (!state.selectedView) return;
      state.treeRoot = String(state.selectedView.id);
      state.collapsed.delete(state.treeRoot);
      this.drawHierarchy();
    },

    onTreeKey(event) {
      const rows = state.visibleRows || [];
      if (!rows.length) return;
      if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = state.selectedView ? String(state.selectedView.id) : "";
      let index = rows.findIndex((row) => String(row.node.id) === current);
      if (event.key === "Home") {
        this.selectView(String(rows[0].node.id), false);
        return;
      }
      if (event.key === "End") {
        this.selectView(String(rows[rows.length - 1].node.id), false);
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (index < 0) index = 0;
        else index += event.key === "ArrowDown" ? 1 : -1;
        index = Math.max(0, Math.min(rows.length - 1, index));
        this.selectView(String(rows[index].node.id), false);
        return;
      }
      if (index < 0) {
        this.selectView(String(rows[0].node.id), false);
        return;
      }
      const row = rows[index];
      const id = String(row.node.id);
      if (event.key === "ArrowRight") {
        if (row.hasChildren && row.collapsed) {
          state.collapsed.delete(id);
          this.drawHierarchy();
          return;
        }
        const child = rows[index + 1];
        if (child && child.depth > row.depth) this.selectView(String(child.node.id), false);
        return;
      }
      if (row.hasChildren && !row.collapsed) {
        state.collapsed.add(id);
        this.drawHierarchy();
        return;
      }
      const parent = state.hierarchyParents.get(id);
      if (parent) this.selectView(parent, false);
    },

    renderDescribe(result) {
      if (!state.describeId) return;
      if (result.id && String(result.id) !== String(state.describeId)) return;
      const node = this.findHierarchyNode(state.describeId);
      if (!node) return;
      state.describeDetail = result.found ? result : null;
      this.paintViewSheet(node, state.describeDetail, result.error || "");
    },

    bindLayoutToggles() {
      const sync = (id, key, redraw) => {
        const input = document.getElementById(id);
        if (!input) return;
        const paint = () => input.closest(".ui-check")?.classList.toggle("is-on", input.checked);
        paint();
        input.addEventListener("change", (event) => {
          state[key] = event.target.checked;
          paint();
          if (redraw === "tree") this.drawHierarchy();
          else this.drawHierarchy3D();
          if (!state.hoverInspect) this.showHover(null);
        });
      };
      sync("ui-system", "showSystem", "tree");
      sync("ui-guides", "layoutGuides", "canvas");
      sync("ui-baselines", "layoutBaselines", "canvas");
      sync("ui-oversized", "layoutOversized", "canvas");
      sync("ui-hover", "hoverInspect", "canvas");
      ["ui-slow", "ui-device-guides", "ui-device-baselines", "ui-device-images", "ui-device-repaint"]
        .forEach((id) => {
          const input = document.getElementById(id);
          const paint = () => input?.closest(".ui-check")?.classList.toggle("is-on", Boolean(input?.checked));
          paint();
          input?.addEventListener("change", (event) => {
            paint();
            this.pushDeviceVisual(event);
          });
        });
      document.getElementById("ui-filter")?.addEventListener("input", (event) => {
        state.viewQuery = event.target.value || "";
        state.matchIndex = 0;
        const ids = this.matchingIds();
        if (ids.length) this.selectView(ids[0], true);
        else this.drawHierarchy();
      });
      document.getElementById("ui-path")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-path-id]");
        if (button) this.selectView(button.dataset.pathId, true);
      });
      document.getElementById("ui-tree")?.addEventListener("keydown", (event) => this.onTreeKey(event));
      document.getElementById("ui-properties")?.addEventListener("click", (event) => {
        const tab = event.target.closest("[data-explorer-tab]");
        if (tab) {
          state.explorerTab = tab.dataset.explorerTab;
          const node = state.selectedView;
          if (node) this.paintViewSheet(node, state.describeDetail, "");
          return;
        }
        const child = event.target.closest("[data-flex-id]");
        if (child) this.selectView(child.dataset.flexId, true);
      });
    },

    deviceVisualPayload() {
      const on = (id) => Boolean(document.getElementById(id)?.checked);
      return {
        slow: on("ui-slow"),
        guidelines: on("ui-device-guides"),
        baselines: on("ui-device-baselines"),
        oversized: on("ui-device-images"),
        repaint: on("ui-device-repaint"),
      };
    },

    async pushDeviceVisual(event) {
      if (state.visualLock) return;
      const input = event.target;
      const note = document.getElementById("ui-device-note");
      if (note) note.textContent = "Waiting for the app…";
      const ok = await this.sendCommand("ui.visual", this.deviceVisualPayload());
      if (!ok && input) {
        state.visualLock = true;
        input.checked = !input.checked;
        input.closest(".ui-check")?.classList.toggle("is-on", input.checked);
        state.visualLock = false;
        if (note) note.textContent = "On device change cancelled.";
        return;
      }
      if (note) note.textContent = ok ? "On device updated." : "";
    },

    isSystemNode(node) {
      if (node.app === true) return false;
      if (node.system === true) return true;
      const name = node.class || "";
      const moduleName = node.module || "";
      if (["UIKit", "UIKitCore", "SwiftUI", "SwiftUICore", "QuartzCore"].includes(moduleName)) return true;
      if (name.startsWith("_")) return true;
      return !moduleName && (name.startsWith("UI") || name.startsWith("NS"));
    },

    hostStem(name) {
      const leaf = String(name || "").split(".").pop() || "";
      const head = leaf.split("<")[0];
      for (const suffix of ["HostingController", "HostingView"]) {
        if (!head.endsWith(suffix)) continue;
        const stem = head.slice(0, -suffix.length);
        if (stem.length > 2 && !stem.startsWith("_") && !stem.startsWith("UI")) return stem;
      }
      return "";
    },

    hostsSwiftUI(node) {
      return node.role === "swiftui" || Boolean(node.swiftui) || Boolean(this.hostStem(node.class || ""));
    },

    rebuildSwiftUINames() {
      const map = new Map();
      const walk = (node, inherited) => {
        const own = node.swiftui || this.hostStem(node.class || "");
        const role = node.role || "uikit";
        const name = own || (role !== "uikit" ? inherited : "");
        map.set(String(node.id), name);
        const next = role === "uikit" ? "" : (name || inherited);
        (node.children || []).forEach((child) => walk(child, next));
      };
      state.hierarchy.forEach((node) => walk(node, ""));
      state.swiftUINames = map;
    },

    swiftUIOf(node) {
      if (!node) return "";
      return state.swiftUINames.get(String(node.id)) || node.swiftui || this.hostStem(node.class || "");
    },

    displayName(node) {
      if (!node) return "View";
      if (node.display) return node.display;
      const swiftui = this.swiftUIOf(node);
      if ((node.role === "swiftui" || this.hostStem(node.class || "")) && swiftui) return swiftui;
      return node.text || node.accessibility_label || node.class || "View";
    },

    bridgeNote(node) {
      const role = node.role || (this.hostStem(node.class || "") ? "swiftui" : "uikit");
      const swiftui = this.swiftUIOf(node);
      const uikit = node.class || "";
      if (role === "swiftui" || (swiftui && uikit.endsWith("HostingView"))) {
        return uikit && uikit !== swiftui ? uikit : "";
      }
      if (role === "uikit-in-swiftui") return swiftui ? `in ${swiftui}` : "in SwiftUI";
      return "";
    },

    viewMatchesQuery(node, query) {
      const hay = [
        node.class, node.display, node.type, node.text, node.accessibility_identifier,
        node.accessibility_label, node.swiftui, this.swiftUIOf(node), node.controller, node.module,
      ].join(" ").toLowerCase();
      return hay.includes(query);
    },

    keepView(node, query) {
      const queryHit = !query || this.viewMatchesQuery(node, query)
        || (node.children || []).some((child) => this.keepView(child, query));
      if (!queryHit) return false;
      if (state.showSystem || !this.isSystemNode(node)) return true;
      return (node.children || []).some((child) => this.keepView(child, query));
    },

    visibleChildren(node) {
      const query = state.viewQuery.trim().toLowerCase();
      return (node.children || []).filter((child) => this.keepView(child, query));
    },

    showHover(node, clientX, clientY) {
      const tip = document.getElementById("ui-hover-tip");
      if (!tip) return;
      if (!node || !state.hoverInspect) {
        tip.hidden = true;
        return;
      }
      const issues = (node.issues || []).join(", ");
      const size = `${Math.round(node.width)} × ${Math.round(node.height)}`;
      tip.hidden = false;
      const bridge = this.bridgeNote(node);
      tip.innerHTML = `<strong>${escapeHtml(this.displayName(node))}</strong><span>${size}</span>${bridge ? `<span>${escapeHtml(bridge)}</span>` : ""}${issues ? `<em>${escapeHtml(issues)}</em>` : ""}`;
      const stage = tip.parentElement.getBoundingClientRect();
      tip.style.left = `${Math.max(8, clientX - stage.left + 12)}px`;
      tip.style.top = `${Math.max(8, clientY - stage.top + 12)}px`;
    },

    viewFacts(node, detail) {
      const role = (detail && detail.role) || node.role || (this.hostStem(node.class || "") ? "swiftui" : "uikit");
      const swiftui = (detail && detail.swiftui) || this.swiftUIOf(node) || "";
      const uikit = (detail && detail.uikit) || node.class || "";
      const typeName = (detail && detail.type) || node.type || "";
      const title = node.display
        || (detail && detail.title)
        || (role === "swiftui" && swiftui ? swiftui : "")
        || node.text
        || node.accessibility_label
        || uikit
        || "View";
      let bridge = node.controller ? `UIKit view · ${node.controller}` : "UIKit view";
      if (node.kind === "controller") {
        bridge = `View controller ${title}. Its root view is the child row`;
      } else if (node.synthetic) {
        bridge = swiftui ? `${uikit || "Control"} in SwiftUI ${swiftui}` : `${uikit || "Control"} in SwiftUI`;
      } else if (role === "swiftui") {
        bridge = uikit && uikit !== title
          ? `SwiftUI ${title}, drawn by UIKit ${uikit}`
          : `SwiftUI view hosted in UIKit`;
      } else if (role === "uikit-in-swiftui") {
        bridge = swiftui ? `UIKit ${uikit || title} inside SwiftUI ${swiftui}` : `UIKit ${uikit || title} inside SwiftUI`;
      }
      const rows = [
        ["SwiftUI view", role === "uikit" ? "" : swiftui],
        ["UIKit class", uikit && uikit !== title ? uikit : ""],
        ["Full type", typeName && typeName !== uikit && typeName !== title ? typeName : ""],
        ["Module", node.module || ""],
        ["Screen", node.controller || ""],
      ].filter(([, value]) => value);
      return { role, title, bridge, rows };
    },

    paintViewSheet(node, detail, error) {
      const props = document.getElementById("ui-properties");
      if (!props) return;
      const facts = this.viewFacts(node, detail);
      const role = facts.role;
      const labels = { uikit: "UIKit", swiftui: "SwiftUI in UIKit", "uikit-in-swiftui": "UIKit in SwiftUI" };
      if (node.kind === "controller") labels[role] = "View controller";
      else if (node.swiftui_node) labels[role] = "SwiftUI view";
      const title = facts.title;
      const summary = (detail && detail.summary) || `${facts.bridge}. Reading the rest from the app…`;
      const sections = (detail && detail.sections) || this.fallbackSections(node);
      const properties = sections.map((section) => {
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
      const explorer = (detail && detail.explorer) || {};
      const tabs = [["layout", "Layout"], ["constraints", "Constraints"], ["properties", "Properties"]];
      if (explorer.flex) tabs.splice(2, 0, ["flex", "Flex"]);
      if (!tabs.some(([id]) => id === state.explorerTab)) state.explorerTab = "layout";
      const tabBar = `<div class="explorer-tabs">${tabs.map(([id, label]) =>
        `<button type="button" data-explorer-tab="${id}" class="${state.explorerTab === id ? "active" : ""}">${label}</button>`
      ).join("")}</div>`;
      const pane = state.explorerTab === "properties"
        ? properties
        : state.explorerTab === "constraints"
          ? this.constraintPane(explorer)
          : state.explorerTab === "flex"
            ? this.flexPane(explorer.flex)
            : this.layoutPane(node, explorer);
      const identity = facts.rows.map(([label, value]) =>
        `<div class="view-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
      ).join("");
      const highlight = node.synthetic
        ? ""
        : `<button class="btn ghost" id="ui-highlight" type="button">Highlight</button>`;
      props.innerHTML = `<div class="view-sheet"><div class="view-sheet-head"><span class="view-role">${escapeHtml(labels[role] || role)}</span><h3>${escapeHtml(title)}</h3>${highlight}</div><p class="view-bridge">${escapeHtml(facts.bridge)}</p><p>${escapeHtml(summary)}</p>${note ? `<p class="warn-text">${escapeHtml(note)}</p>` : ""}${identity ? `<div class="view-identity">${identity}</div>` : ""}${tabBar}${pane}</div>`;
      document.getElementById("ui-highlight")?.addEventListener("click", () =>
        this.sendCommand("ui.highlight", { id: state.describeId }));
    },

    layoutPane(node, explorer) {
      const box = explorer.box || this.boxFromNode(node);
      const issues = explorer.issues || (node.issues || []).map((kind) => ({
        kind,
        message: kind === "overflow" ? "A child extends past this view."
          : kind === "oversized" ? "Image uses more memory than its display size."
            : kind === "zero" ? "Bounds are empty."
              : kind,
      }));
      const issueHtml = issues.length
        ? issues.map((issue) => `<div class="ui-issue is-${escapeHtml(issue.kind)}">${escapeHtml(issue.message)}</div>`).join("")
        : `<p class="muted">No layout issues on this view.</p>`;
      const safe = box.safe_area
        ? `<p class="muted">Safe area ${box.safe_area.map((value) => this.edgeText(value)).join(", ")} (top, left, bottom, right).</p>`
        : "";
      return `${this.boxModel(box)}<p class="muted">Padding is the view's layout margins. Margin is the gap to the parent. Constraints come from the parent. Size goes back up.</p>${safe}${issueHtml}`;
    },

    boxFromNode(node) {
      const padding = node.pad || [0, 0, 0, 0];
      const border = Number(node.border_width) || 0;
      return {
        width: node.width,
        height: node.height,
        margin: [0, 0, 0, 0],
        border,
        padding,
        content_width: Math.max(0, (node.width || 0) - padding[1] - padding[3] - border * 2),
        content_height: Math.max(0, (node.height || 0) - padding[0] - padding[2] - border * 2),
      };
    },

    edgeText(value) {
      const number = Number(value) || 0;
      const rounded = Math.round(number * 10) / 10;
      return String(rounded);
    },

    edgeTag(className, value) {
      const negative = Number(value) < -0.5 ? " is-negative" : "";
      return `<b class="box-edge ${className}${negative}">${this.edgeText(value)}</b>`;
    },

    boxModel(box) {
      const margin = box.margin || [0, 0, 0, 0];
      const padding = box.padding || [0, 0, 0, 0];
      const size = `${this.edgeText(box.content_width)} × ${this.edgeText(box.content_height)}`;
      return `<div class="box-margin" aria-label="Layout box">${this.edgeTag("edge-t", margin[0])}${this.edgeTag("edge-r", margin[3])}${this.edgeTag("edge-b", margin[2])}${this.edgeTag("edge-l", margin[1])}<span>margin</span><div class="box-border"><span>border ${this.edgeText(box.border)}</span><div class="box-pad">${this.edgeTag("edge-t", padding[0])}${this.edgeTag("edge-r", padding[3])}${this.edgeTag("edge-b", padding[2])}${this.edgeTag("edge-l", padding[1])}<span>padding</span><div class="box-content">${size}</div></div></div></div>`;
    },

    constraintPane(explorer) {
      const items = explorer.constraints || [];
      if (!items.length) {
        return `<p class="muted">No constraints on this view yet. Capture again after the app rebuilds with this SDK.</p>`;
      }
      return items.map((item) => {
        const optional = Number(item.priority) < 1000 ? " is-optional" : "";
        const off = item.active === false ? " is-off" : "";
        const title = item.title || item.summary || "Constraint";
        const summary = item.summary || item.equation || "";
        const note = [item.note, item.identifier].filter(Boolean).join(" · ");
        const formula = item.summary && item.equation ? `<code>${escapeHtml(item.equation)}</code>` : "";
        return `<div class="constraint-row${optional}${off}"><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(summary)}</p>${note ? `<p class="muted">${escapeHtml(note)}</p>` : ""}${formula}</div></div>`;
      }).join("");
    },

    flexPane(flex) {
      if (!flex) return `<p class="muted">This view is not a stack and not arranged in one.</p>`;
      const children = flex.children || [];
      const horizontal = flex.axis !== "vertical";
      const total = children.reduce((sum, child) => sum + Math.max(1, horizontal ? Number(child.width) : Number(child.height)), 0);
      const bars = children.map((child) => {
        const span = Math.max(1, horizontal ? Number(child.width) : Number(child.height));
        const selected = String(child.id) === String(flex.selected) ? " is-selected" : "";
        const basis = `${(span / total) * 100}%`;
        const named = this.findHierarchyNode(child.id);
        const label = named ? this.displayName(named) : child.class;
        return `<button type="button" class="flex-child${selected}" data-flex-id="${escapeAttr(child.id)}" style="flex-basis:${basis}"><strong>${escapeHtml(label)}</strong><span>${Math.round(child.width)}×${Math.round(child.height)} · hug ${Math.round(child.hugging || 0)}</span></button>`;
      }).join("");
      const more = flex.truncated ? `<p class="muted">Showing the first 12 arranged views.</p>` : "";
      return `<p>Main axis ${escapeHtml(flex.axis)}. Alignment ${escapeHtml(flex.alignment)}. Distribution ${escapeHtml(flex.distribution)}. Spacing ${escapeHtml(flex.spacing)}.</p><div class="flex-track ${horizontal ? "is-row" : "is-column"}">${bars}</div>${more}<p class="muted">Bar size follows the laid-out size on the main axis. Click a child to select it.</p>`;
    },

    fallbackSections(node) {
      const frame = `x ${Math.round(node.x || 0)}, y ${Math.round(node.y || 0)}, ${Math.round(node.width || 0)} × ${Math.round(node.height || 0)}`;
      const rows = [
        ["UIKit class", node.class || ""],
        ["SwiftUI view", this.swiftUIOf(node) || ""],
        ["Full type", node.type || ""],
        ["Module", node.module || ""],
        ["Screen", node.controller || ""],
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
        this.showHover(null);
        drag = { x: event.clientX, y: event.clientY, moved: false, pan: event.shiftKey || event.button === 2 };
        canvas.setPointerCapture(event.pointerId);
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!drag) {
          const { x, y } = point(event);
          this.showHover(this.hitTestHierarchy(x, y), event.clientX, event.clientY);
          return;
        }
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
      canvas.addEventListener("pointerleave", () => this.showHover(null));
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
      document.getElementById("ui-3d-depth")?.addEventListener("change", (event) => {
        state.depthMode = event.target.value === "tree" ? "tree" : "overlap";
        state.layerCache = null;
        this.drawHierarchy3D();
      });
      const low = document.getElementById("ui-3d-zlow");
      const high = document.getElementById("ui-3d-zhigh");
      const syncRange = (moved) => {
        let lowValue = Number(low?.value) || 0;
        let highValue = Number(high?.value ?? 100);
        if (lowValue > highValue) {
          if (moved === low) highValue = lowValue;
          else lowValue = highValue;
          if (low) low.value = String(lowValue);
          if (high) high.value = String(highValue);
        }
        state.zRange = { low: lowValue, high: highValue };
        this.drawHierarchy3D();
      };
      low?.addEventListener("input", () => syncRange(low));
      high?.addEventListener("input", () => syncRange(high));
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
      const cacheKey = `${state.depthMode}`;
      if (state.layerCache && state.layerCache.key === cacheKey && state.layerCache.source === state.hierarchy) {
        return state.layerCache.value;
      }
      const root = state.hierarchy.find((window) => window.key_window) || state.hierarchy[0];
      const layers = [];
      const walk = (node, depth, controller) => {
        if (node.hidden || Number(node.alpha ?? 1) <= 0.01) return;
        if (node.kind === "controller") {
          // A controller has no layer of its own. Xcode labels its root view instead.
          (node.children || []).forEach((child) => walk(child, depth, this.displayName(node)));
          return;
        }
        const drawable = node.width > 0 && node.height > 0;
        const index = layers.length;
        if (drawable) layers.push({ node, depth, leaf: true, controller });
        (node.children || []).forEach((child) => walk(child, depth + 1, ""));
        if (drawable) layers[index].leaf = layers.length === index + 1;
      };
      if (root) walk(root, 0, "");
      this.assignLayerDepth(layers);
      const maxZ = layers.reduce((max, layer) => Math.max(max, layer.z), 0);
      const value = { root, layers, maxZ };
      state.layerCache = { key: cacheKey, source: state.hierarchy, value };
      return value;
    },

    // Overlap mode matches Xcode: a layer sits one step above the highest earlier layer it overlaps,
    // so siblings side by side share a plane and only real stacking adds depth.
    assignLayerDepth(layers) {
      if (state.depthMode === "tree") {
        layers.forEach((layer) => { layer.z = layer.depth; });
        return;
      }
      const placed = [];
      layers.forEach((layer) => {
        const { node } = layer;
        let z = 0;
        for (let i = placed.length - 1; i >= 0; i -= 1) {
          const other = placed[i];
          if (other.z + 1 <= z) continue;
          const o = other.node;
          if (o.x < node.x + node.width && node.x < o.x + o.width
            && o.y < node.y + node.height && node.y < o.y + o.height) {
            z = other.z + 1;
          }
        }
        layer.z = z;
        placed.push(layer);
      });
    },

    layerTexture(node, leaf, layerShot) {
      const id = String(node.id);
      const own = state.layerImages.get(id);
      if (own && own.complete && own.naturalWidth) return { image: own, sx: 0, sy: 0, sw: own.naturalWidth, sh: own.naturalHeight };
      // SwiftUI draws into its host's layer. Leaf SwiftUI views lift their pixels from the host image.
      if (node.swiftui_node && node.host_id && leaf) {
        const hostImage = state.layerImages.get(String(node.host_id));
        const host = this.findHierarchyNode(String(node.host_id));
        if (hostImage && hostImage.complete && hostImage.naturalWidth && host && host.width > 0 && host.height > 0) {
          const rx = hostImage.naturalWidth / host.width;
          const ry = hostImage.naturalHeight / host.height;
          return {
            image: hostImage,
            sx: (node.x - host.x) * rx,
            sy: (node.y - host.y) * ry,
            sw: node.width * rx,
            sh: node.height * ry,
          };
        }
      }
      return layerShot(node);
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

    paintLayerChrome() {
      const label = document.getElementById("ui-zoom-label");
      if (label) label.textContent = `${Math.round(state.hierarchyZoom * 100)}%`;
      const flat = Math.abs(state.hierarchyView.yaw) < 0.02 && Math.abs(state.hierarchyView.pitch) < 0.02;
      const flatBtn = document.getElementById("ui-3d-flat");
      const resetBtn = document.getElementById("ui-3d-reset");
      flatBtn?.classList.toggle("active", flat);
      flatBtn?.setAttribute("aria-pressed", flat ? "true" : "false");
      resetBtn?.classList.toggle("active", !flat);
      resetBtn?.setAttribute("aria-pressed", flat ? "false" : "true");
    },

    fitCanvas(root) {
      const canvas = document.getElementById("ui-3d");
      if (!canvas || !root || !(root.width > 0) || !(root.height > 0)) return;
      const column = canvas.parentElement?.clientWidth || canvas.clientWidth || 800;
      const fitted = column * root.height / root.width;
      const height = Math.round(Math.min(window.innerHeight * 0.72, Math.max(280, fitted)));
      const next = `${height}px`;
      if (canvas.style.height !== next) canvas.style.height = next;
    },

    drawHierarchy3D() {
      this.paintLayerChrome();
      const canvas = document.getElementById("ui-3d");
      if (!canvas) return;
      this.fitCanvas(this.hierarchyLayers().root);
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
      const { root, layers, maxZ } = this.hierarchyLayers();
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
      const lowZ = Math.floor((state.zRange.low / 100) * maxZ);
      const highZ = Math.ceil((state.zRange.high / 100) * maxZ);
      const shown = layers.filter((layer) => layer.z >= lowZ && layer.z <= highZ);
      const stack = maxZ * view.spacing;
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
      const picked = state.selectedView;
      const selectedId = picked ? String(picked.kind === "controller" ? picked.describe_id || picked.id : picked.id) : "";
      let selected = null;
      const oversizedNotes = [];
      const controllerTabs = [];
      const haveLayers = state.layerImages.size > 0;
      const shotCrop = (node) => {
        if (!shotReady) return null;
        const x0 = Math.max(0, node.x - root.x);
        const y0 = Math.max(0, node.y - root.y);
        const x1 = Math.min(shot.width, node.x - root.x + node.width);
        const y1 = Math.min(shot.height || root.height, node.y - root.y + node.height);
        if (x1 <= x0 || y1 <= y0) return null;
        return {
          image: shot.image,
          sx: x0 * ratio,
          sy: y0 * ratio,
          sw: (x1 - x0) * ratio,
          sh: (y1 - y0) * ratio,
          dx: root.x + x0,
          dy: root.y + y0,
          dw: x1 - x0,
          dh: y1 - y0,
        };
      };
      const roleStroke = (node, depth) => {
        if (depth === 0) return "rgba(255, 176, 32, 0.9)";
        if (node.swiftui_node) return "rgba(92, 160, 255, 0.7)";
        if (node.role === "swiftui") return "rgba(120, 140, 255, 0.75)";
        if (node.role === "uikit-in-swiftui") return "rgba(64, 196, 170, 0.7)";
        return "rgba(170, 182, 196, 0.55)";
      };
      shown.forEach((layer) => {
        const { node, depth, leaf } = layer;
        const m = matrix(layer.z);
        state.hierarchyDrawn.push({ node, m });
        apply(m);
        ctx.globalAlpha = Math.max(0.15, Math.min(1, Number(node.alpha ?? 1)));
        const id = String(node.id);
        if (haveLayers) {
          // Per-view images carry their own background, so the color fill is only a fallback.
          const texture = state.layerEmpty.has(id) ? null
            : state.layerCrop.has(id) ? shotCrop(node)
              : this.layerTexture(node, leaf, (target) => (target.swiftui_node && leaf ? shotCrop(target) : null));
          if (texture) {
            ctx.drawImage(texture.image, texture.sx, texture.sy, texture.sw, texture.sh,
              texture.dx ?? node.x, texture.dy ?? node.y, texture.dw ?? node.width, texture.dh ?? node.height);
          } else if (node.background && !state.layerEmpty.has(id) && !state.layerImages.has(id)) {
            ctx.fillStyle = node.background;
            ctx.fillRect(node.x, node.y, node.width, node.height);
          }
        } else {
          if (node.background) {
            ctx.fillStyle = node.background;
            ctx.fillRect(node.x, node.y, node.width, node.height);
          }
          const wantsTexture = depth === 0 || (leaf && (textured.has(node.kind) || node.background
            || node.width * node.height < rootArea * 0.5));
          if (shotReady && wantsTexture) drawCrop(node);
        }
        ctx.globalAlpha = 1;
        ctx.strokeStyle = roleStroke(node, depth);
        ctx.lineWidth = 1 / scale;
        ctx.strokeRect(node.x, node.y, node.width, node.height);
        this.paintLayoutMarks(ctx, node, scale);
        if (layer.controller) controllerTabs.push({ node, m, name: layer.controller });
        if (state.layoutOversized && Number(node.extra_kb) >= 128) oversizedNotes.push({ node, m });
        if (id === selectedId) selected = { node, m };
      });
      controllerTabs.forEach(({ node, m, name }) => {
        apply(m);
        const size = 11 / scale;
        ctx.font = `600 ${size}px sans-serif`;
        const width = Math.min(node.width, ctx.measureText(name).width + 14 / scale);
        ctx.fillStyle = "rgba(38, 40, 44, 0.92)";
        ctx.fillRect(node.x, node.y - size * 1.6, width, size * 1.6);
        ctx.fillStyle = "#f2c94c";
        ctx.beginPath();
        ctx.arc(node.x + 5 / scale, node.y - size * 0.8, 3 / scale, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#e8e8e8";
        ctx.fillText(name, node.x + 11 / scale, node.y - size * 0.45, Math.max(1, width - 12 / scale));
      });
      ctx.font = "12px sans-serif";
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
        ctx.fillText(`${this.displayName(node)} ${Math.round(node.width)}×${Math.round(node.height)}`,
          Math.max(4, px + 4), Math.max(14, py - 6));
      }
      if (oversizedNotes.length) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.fillStyle = "#d24b8a";
        oversizedNotes.forEach(({ node, m }) => {
          const pixels = node.pixels || [];
          const px = m[0] * node.x + m[2] * node.y + m[4];
          const py = m[1] * node.x + m[3] * node.y + m[5];
          ctx.fillText(`${pixels[0] || "?"}×${pixels[1] || "?"} +${Math.round(node.extra_kb)} KB`,
            Math.max(4, px + 4), Math.max(14, py + 14));
        });
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    },

    paintLayoutMarks(ctx, node, scale) {
      if (state.layoutGuides) {
        ctx.save();
        ctx.strokeStyle = "rgba(56, 140, 220, 0.95)";
        ctx.lineWidth = 1 / scale;
        ctx.strokeRect(node.x, node.y, node.width, node.height);
        const pad = Array.isArray(node.pad) ? node.pad : null;
        if (pad && pad.some((value) => Number(value) > 0)) {
          const top = Number(pad[0]) || 0;
          const left = Number(pad[1]) || 0;
          const bottom = Number(pad[2]) || 0;
          const right = Number(pad[3]) || 0;
          const innerWidth = node.width - left - right;
          const innerHeight = node.height - top - bottom;
          if (innerWidth > 0 && innerHeight > 0) {
            ctx.fillStyle = "rgba(56, 140, 220, 0.18)";
            ctx.beginPath();
            ctx.rect(node.x, node.y, node.width, node.height);
            ctx.rect(node.x + left, node.y + top, innerWidth, innerHeight);
            ctx.fill("evenodd");
          }
        }
        if (node.clips) {
          ctx.setLineDash([4 / scale, 3 / scale]);
          ctx.strokeStyle = "rgba(214, 92, 158, 0.95)";
          ctx.strokeRect(node.x, node.y, node.width, node.height);
          ctx.setLineDash([]);
        }
        if (node.kind === "scroll") {
          ctx.fillStyle = "rgba(80, 180, 120, 0.95)";
          const tipX = node.x + node.width - 8;
          const tipY = node.y + 6;
          ctx.beginPath();
          ctx.moveTo(tipX, tipY);
          ctx.lineTo(tipX + 5, tipY + 8);
          ctx.lineTo(tipX - 5, tipY + 8);
          ctx.fill();
        }
        ctx.restore();
      }
      if (state.layoutBaselines && Number.isFinite(Number(node.baseline))) {
        const baseline = node.y + Number(node.baseline);
        ctx.save();
        ctx.strokeStyle = "rgba(70, 180, 110, 0.95)";
        ctx.lineWidth = 1 / scale;
        ctx.beginPath();
        ctx.moveTo(node.x, baseline);
        ctx.lineTo(node.x + node.width, baseline);
        ctx.stroke();
        ctx.restore();
      }
      if (state.layoutOversized && Number(node.extra_kb) >= 128) {
        ctx.save();
        ctx.strokeStyle = "#d24b8a";
        ctx.lineWidth = 3 / scale;
        ctx.strokeRect(node.x, node.y, node.width, node.height);
        ctx.restore();
      }
    },

    paintTreeChrome() {
      const ids = this.matchingIds();
      const count = document.getElementById("ui-match-count");
      const query = state.viewQuery.trim();
      if (count) {
        count.textContent = query
          ? `${ids.length ? state.matchIndex + 1 : 0} / ${ids.length}`
          : (state.visibleRows.length ? `${state.visibleRows.length} shown` : "");
      }
      const prev = document.getElementById("ui-match-prev");
      const next = document.getElementById("ui-match-next");
      if (prev) prev.disabled = ids.length < 2;
      if (next) next.disabled = ids.length < 2;
      const selectedId = state.selectedView ? String(state.selectedView.id) : "";
      const up = document.getElementById("ui-up");
      if (up) up.disabled = !selectedId || !state.hierarchyParents.get(selectedId);
      const branch = document.getElementById("ui-branch");
      if (branch) branch.disabled = !selectedId;
      const clear = document.getElementById("ui-branch-clear");
      if (clear) clear.hidden = !state.treeRoot;
      const nav = document.getElementById("ui-path");
      if (!nav) return;
      if (!selectedId) {
        nav.innerHTML = "";
        return;
      }
      const chain = [];
      let id = selectedId;
      const guard = new Set();
      while (id && !guard.has(id)) {
        guard.add(id);
        const node = this.findHierarchyNode(id);
        if (!node) break;
        chain.push(node);
        id = state.hierarchyParents.get(id);
      }
      chain.reverse();
      nav.innerHTML = chain.map((node, index) => {
        const current = index === chain.length - 1;
        const sep = index ? `<span class="tree-sep">/</span>` : "";
        const label = this.displayName(node);
        const detail = node.class && node.class !== label ? `${label} · ${node.class}` : label;
        return `${sep}<button type="button" data-path-id="${escapeAttr(node.id)}" aria-current="${current ? "true" : "false"}" title="${escapeAttr(detail)}">${escapeHtml(label)}</button>`;
      }).join("");
      const current = nav.querySelector('[aria-current="true"]');
      if (current) nav.scrollLeft = Math.max(0, current.offsetLeft - 8);
    },

    drawHierarchy() {
      this.rebuildSwiftUINames();
      const visible = [];
      const query = state.viewQuery.trim().toLowerCase();
      const walk = (node, depth) => {
        if (!this.keepView(node, query)) return;
        const children = this.visibleChildren(node);
        const collapsed = query ? false : state.collapsed.has(String(node.id));
        visible.push({
          node,
          depth,
          collapsed,
          hasChildren: children.length > 0,
          system: this.isSystemNode(node),
        });
        if (!collapsed) children.forEach((child) => walk(child, depth + 1));
      };
      let roots = state.hierarchy;
      if (state.treeRoot) {
        const root = this.findHierarchyNode(state.treeRoot);
        if (root) roots = [root];
        else state.treeRoot = "";
      }
      roots.forEach((node) => walk(node, 0));
      state.visibleRows = visible;
      this.paintTreeChrome();
      const tree = document.getElementById("ui-tree");
      if (!tree) return;
      const scroll = tree.scrollTop;
      const selectedId = state.selectedView ? String(state.selectedView.id) : "";
      if (!visible.length) {
        tree.innerHTML = state.hierarchy.length
          ? `<p class="muted tree-empty">${state.showSystem ? "No views match." : "No app views in this tree. Turn on System views."}</p>`
          : `<p class="muted tree-empty">Capture hierarchy to list views. Then ↑↓ to move, ← for the parent, → to open.</p>`;
        if (!state.hierarchy.length) this.drawHierarchy3D();
        return;
      }
      tree.innerHTML = visible.map(({ node, depth, collapsed, hasChildren, system }) => {
        const marker = hasChildren ? (collapsed ? "▸" : "▾") : "";
        const active = String(node.id) === selectedId ? " active" : "";
        const dim = node.hidden || Number(node.alpha ?? 1) <= 0.01 ? " dim" : "";
        const hosted = this.hostsSwiftUI(node);
        const systemClass = system && !hosted ? " is-system" : "";
        const rootClass = depth === 0 ? " is-root" : "";
        const match = query && this.viewMatchesQuery(node, query) ? " is-match" : "";
        const title = this.displayName(node);
        const hint = node.text || node.accessibility_identifier || node.accessibility_label || "";
        const bridge = this.bridgeNote(node);
        const noteText = [bridge, hint && hint !== bridge && hint !== title ? String(hint) : ""].filter(Boolean).join(" · ");
        const note = noteText ? `<span class="tree-note">${escapeHtml(String(noteText).slice(0, 48))}</span>` : "";
        const role = node.kind === "controller"
          ? `<i class="role-tag is-controller">Controller</i>`
          : node.role === "uikit-in-swiftui"
            ? `<i class="role-tag is-bridge">UIKit</i>`
            : hosted
              ? `<i class="role-tag is-swiftui">SwiftUI</i>`
              : "";
        const issue = (node.issues || [])[0];
        const issueTag = issue ? `<i class="role-tag is-issue">${escapeHtml(issue)}</i>` : "";
        const axis = node.stack_axis ? `<i class="role-tag">${escapeHtml(node.stack_axis)}</i>` : "";
        const expanded = hasChildren ? ` aria-expanded="${collapsed ? "false" : "true"}"` : "";
        const kindClass = node.kind === "controller" ? " is-controller" : node.swiftui_node ? " is-swiftui-node" : "";
        return `<div class="tree-row${active}${dim}${systemClass}${rootClass}${match}${kindClass}" role="treeitem" data-view-id="${escapeAttr(node.id)}" style="--depth:${depth}"${expanded}><button type="button" class="tree-toggle" data-toggle="${escapeAttr(node.id)}" ${hasChildren ? "" : "disabled"} aria-label="${collapsed ? "Expand" : "Collapse"}">${marker}</button><span class="tree-name">${escapeHtml(title)}</span>${role}${axis}${issueTag}${note}<span class="tree-size">${Math.round(node.width)}×${Math.round(node.height)}</span></div>`;
      }).join("");
      tree.scrollTop = scroll;
      tree.onclick = (event) => {
        if (document.activeElement !== tree) tree.focus({ preventScroll: true });
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
      tree.ondblclick = (event) => {
        if (event.target.closest("[data-toggle]")) return;
        const row = event.target.closest("[data-view-id]");
        if (!row) return;
        const id = row.dataset.viewId;
        const node = this.findHierarchyNode(id);
        if (!(node?.children || []).length) return;
        if (state.collapsed.has(id)) state.collapsed.delete(id);
        else state.collapsed.add(id);
        this.drawHierarchy();
      };
      this.drawHierarchy3D();
    },

    screenForm(width, height) {
      const long = Math.max(width, height);
      const short = Math.min(width, height);
      const ratio = short > 0 ? long / short : 1;
      let form = { id: "phone", name: "iPhone" };
      if (ratio < 1.55) form = { id: "pad", name: "iPad" };
      else if (ratio < 1.95 && long >= 900) form = { id: "tv", name: "Apple TV" };
      else if (ratio >= 2.3 && short >= 600) form = { id: "wide", name: "Wide" };
      const known = [[16, 9], [4, 3], [3, 2], [19.5, 9], [2, 1]];
      const hit = known.find(([wide, tall]) => Math.abs(ratio - wide / tall) < 0.04);
      const ratioText = hit ? `${hit[0]}:${hit[1]}` : `${ratio.toFixed(2)}:1`;
      const landscape = width > height ? " landscape" : "";
      return {
        ...form,
        label: `${form.name}${landscape} · ${Math.round(width)} × ${Math.round(height)} · ${ratioText}`,
      };
    },

    renderScreenshot(result, targetId) {
      const target = document.getElementById(targetId);
      if (!target || !result.base64) return;
      const width = Number(result.width) || 0;
      const height = Number(result.height) || 0;
      const url = `data:${result.mime || "image/jpeg"};base64,${result.base64}`;
      const current = target.querySelector("img");
      const sameSize = current
        && target.dataset.shotW === String(width)
        && target.dataset.shotH === String(height);
      if (sameSize) {
        this.swapShot(target, current, url);
        return;
      }
      const image = document.createElement("img");
      image.alt = "App screenshot";
      const frame = document.createElement("div");
      frame.className = "device-frame";
      if (width > 0 && height > 0) {
        const form = this.screenForm(width, height);
        frame.classList.add(`is-${form.id}`);
        frame.style.aspectRatio = `${width} / ${height}`;
        frame.style.width = `min(100%, calc(68vh * ${width} / ${height}))`;
        image.alt = form.label;
        const caption = document.createElement("figcaption");
        caption.className = "device-caption";
        caption.textContent = form.label;
        target.hidden = false;
        target.dataset.shotW = String(width);
        target.dataset.shotH = String(height);
        target.replaceChildren(frame, caption);
        frame.append(image);
        this.swapShot(target, image, url);
        return;
      }
      frame.append(image);
      target.hidden = false;
      target.dataset.shotW = "";
      target.dataset.shotH = "";
      target.replaceChildren(frame);
      this.swapShot(target, image, url);
    },

    swapShot(target, image, url) {
      const token = String((Number(target.dataset.shotToken) || 0) + 1);
      target.dataset.shotToken = token;
      const incoming = new Image();
      incoming.onload = () => {
        if (target.dataset.shotToken !== token) return;
        image.src = url;
      };
      incoming.src = url;
    },

    renderStorage(name, result) {
      if (name === "storage.files") return this.renderFileBrowser(result);
      if (name === "storage.file_read") return this.renderFilePreview(result);
      const targetId = "storage-inspector";
      clearTimeout(state.storageInfoTimer);
      if (name === "storage.defaults") return this.renderDefaults(result);
      if (name === "storage.keychain_metadata") return this.renderKeychain(result);
      if (name === "storage.cookies" || name === "storage.cookie_delete") {
        return window.ViewliftExplorer?.renderCookies(name, result);
      }
      if (name === "storage.defaults_set" || name === "storage.defaults_delete") {
        this.notify(name === "storage.defaults_delete" ? `Removed ${result.key || "key"}` : `Saved ${result.key || "key"}`);
        return this.runStorageCommand("storage.defaults");
      }
      if (name === "storage.file_rename" || name === "storage.file_delete") {
        this.notify(name === "storage.file_delete" ? `Deleted ${result.path || "item"}` : `Renamed to ${result.path || ""}`);
        this.inspectorMessage("");
        return this.openStorage(state.storagePath);
      }
      for (const key of ["items", "files", "rows"]) {
        if (Array.isArray(result[key])) return this.renderTable(targetId, result[key]);
      }
      const target = document.getElementById(targetId);
      if (target) target.innerHTML = window.ViewliftNetwork.copyBlockHtml(JSON.stringify(result, null, 2), "Copy JSON");
    },

    inspectorMessage(text, retryCommand) {
      const target = document.getElementById("storage-inspector");
      if (!target) return;
      const retry = retryCommand
        ? ` <button type="button" class="btn ghost storage-retry" data-storage-command="${escapeAttr(retryCommand)}">Retry</button>`
        : "";
      target.innerHTML = `<p class="muted">${escapeHtml(text)}${retry}</p>`;
    },

    runStorageCommand(name) {
      clearTimeout(state.storageInfoTimer);
      if (!state.clientId) {
        this.inspectorMessage("Choose one app in Scope first.");
        return;
      }
      const presence = state.client?.state || "online";
      const appName = state.client?.app_name || "The app";
      if (presence !== "online") {
        const reason = presence === "background" ? "is in the background" : "is offline";
        this.inspectorMessage(`${appName} ${reason}. Open it on the device, then try again.`, name);
        return;
      }
      this.inspectorMessage("Loading…");
      state.storageInfoTimer = setTimeout(() => {
        this.inspectorMessage(`${appName} did not answer.`, name);
      }, 10000);
      this.sendCommand(name, {});
    },

    renderDefaults(result) {
      const target = document.getElementById("storage-inspector");
      if (!target) return;
      const values = result.values && typeof result.values === "object" ? result.values : {};
      const rows = Object.keys(values).sort((a, b) => a.localeCompare(b)).map((key) => {
        const value = values[key];
        const kind = Array.isArray(value) ? "array" : value === null ? "null" : typeof value;
        const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
        return { key, kind, text: String(text ?? "") };
      });
      target.innerHTML = `<div class="defaults-view">
        <div class="defaults-bar">
          <input type="search" id="defaults-filter" placeholder="Filter ${rows.length} keys" autocomplete="off" aria-label="Filter UserDefaults keys">
          <span class="muted" id="defaults-count"></span>
          <button type="button" class="btn ghost" data-storage-command="storage.defaults">Refresh</button>
        </div>
        <form class="defaults-add" id="defaults-add">
          <input name="key" placeholder="Key" autocomplete="off" aria-label="New key" required>
          <input name="value" placeholder='Value as JSON, for example "text", 3, true, [1,2]' autocomplete="off" aria-label="New value">
          <button type="submit" class="btn ghost">Set key</button>
        </form>
        <table class="plist-table defaults-table"><tbody id="defaults-rows"></tbody></table>
      </div>`;
      target.querySelector("#defaults-add").addEventListener("submit", (event) => {
        event.preventDefault();
        const form = event.target;
        const key = form.elements.key.value.trim();
        const raw = form.elements.value.value;
        if (!key) return;
        let value = raw;
        try { value = JSON.parse(raw); } catch (_) { value = raw; }
        this.sendCommand("storage.defaults_set", { key, value });
      });
      const body = target.querySelector("#defaults-rows");
      const count = target.querySelector("#defaults-count");
      const paint = (query) => {
        const needle = query.trim().toLowerCase();
        const shown = needle
          ? rows.filter((row) => row.key.toLowerCase().includes(needle) || row.text.toLowerCase().includes(needle))
          : rows;
        count.textContent = needle ? `${shown.length} of ${rows.length}` : `${rows.length} keys`;
        body.innerHTML = shown.length
          ? shown.slice(0, 500).map((row) => `<tr><th>${escapeHtml(row.key)}</th><td class="plist-type">${escapeHtml(row.kind)}</td><td><pre class="defaults-value">${escapeHtml(row.text.length > 4000 ? `${row.text.slice(0, 4000)}…` : row.text)}</pre></td><td class="defaults-actions"><button type="button" class="btn ghost" data-defaults-delete="${escapeAttr(row.key)}" aria-label="Delete ${escapeAttr(row.key)}">Delete</button></td></tr>`).join("")
          : `<tr><td colspan="4" class="muted">${rows.length ? "No keys match." : "UserDefaults has no keys."}</td></tr>`;
      };
      paint("");
      target.querySelector("#defaults-filter").addEventListener("input", (event) => paint(event.target.value));
      body.addEventListener("click", (event) => {
        const button = event.target.closest("[data-defaults-delete]");
        if (button) this.sendCommand("storage.defaults_delete", { key: button.dataset.defaultsDelete });
      });
    },

    renderKeychain(result) {
      const target = document.getElementById("storage-inspector");
      if (!target) return;
      const labels = {
        genp: "Generic password",
        inet: "Internet password",
        cert: "Certificate",
        keys: "Key",
        idnt: "Identity",
      };
      const items = Array.isArray(result.items) ? result.items : [];
      const note = `<p class="muted">Metadata only. Secret values never leave the device.</p>`;
      if (!items.length) {
        target.innerHTML = `${note}<p class="muted">The app has no keychain items. A simulator keychain starts empty after a reset.</p>`;
        return;
      }
      const cell = (value) => {
        const text = String(value ?? "");
        return text ? escapeHtml(text) : `<span class="muted">—</span>`;
      };
      const body = items.map((item) => `<tr>
        <td>${escapeHtml(labels[item.class] || item.class || "")}</td>
        <td>${cell(item.service)}</td>
        <td>${cell(item.account)}</td>
        <td>${cell(item.access_group)}</td>
        <td>${cell(item.accessible)}</td>
      </tr>`).join("");
      target.innerHTML = `${note}<table class="debug-table"><thead><tr><th>Class</th><th>Service</th><th>Account</th><th>Access group</th><th>Accessible</th></tr></thead><tbody>${body}</tbody></table>`;
    },

    bindStorage() {
      document.getElementById("storage-inspector")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-storage-command]");
        if (button) this.runStorageCommand(button.dataset.storageCommand);
      });
      document.querySelectorAll("[data-storage-run]").forEach((button) => {
        button.addEventListener("click", () => this.runStorageCommand(button.dataset.storageRun));
      });
      document.getElementById("storage-crumb")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-storage-path]");
        if (button) this.openStorage(button.dataset.storagePath || "");
      });
      document.getElementById("storage-files")?.addEventListener("click", (event) => {
        if (event.target.closest("[data-storage-retry]")) {
          this.openStorage(state.storagePath);
          return;
        }
        const schema = event.target.closest("[data-storage-schema]");
        if (schema) {
          const path = schema.dataset.storageSchema || "";
          this.selectStorageFile(path, path);
          this.sendCommand("storage.sqlite_schema", { path });
          return;
        }
        const rename = event.target.closest("[data-file-rename]");
        if (rename) {
          const path = rename.dataset.fileRename || "";
          const current = path.split("/").pop() || "";
          const name = window.prompt(`Rename ${current} to`, current);
          if (name && name.trim() && name.trim() !== current) {
            this.sendCommand("storage.file_rename", { path, name: name.trim() });
          }
          return;
        }
        const remove = event.target.closest("[data-file-delete]");
        if (remove) {
          this.sendCommand("storage.file_delete", { path: remove.dataset.fileDelete || "" });
          return;
        }
        if (event.target.closest("input")) return;
        const row = event.target.closest("tr[data-path]");
        if (!row) return;
        if (row.dataset.directory === "true") {
          this.openStorage(row.dataset.path || "");
          return;
        }
        const path = row.dataset.path || "";
        this.selectStorageFile(path, row.dataset.name || "");
        state.storageRead = path;
        const inspector = document.getElementById("storage-inspector");
        if (inspector) {
          inspector.innerHTML = `<p class="muted">Reading ${escapeHtml(row.dataset.name || "file")}…</p>`;
        }
        this.sendCommand("storage.file_read", { path });
      });
      document.getElementById("storage-snapshot")?.addEventListener("click", () => {
        const paths = [...document.querySelectorAll("#storage-files input[data-storage-check]:checked")]
          .map((input) => input.value)
          .filter(Boolean);
        if (!paths.length) {
          this.notify("Select one or more files first.");
          return;
        }
        this.sendCommand("storage.snapshot_create", { paths });
      });
      document.getElementById("storage-sql")?.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!state.storageDbPath) {
          this.notify("Select a database first.");
          return;
        }
        const query = document.getElementById("storage-sql-query")?.value.trim() || "";
        if (!query) return;
        this.sendCommand("storage.sqlite_query", { path: state.storageDbPath, query });
      });
      document.getElementById("storage-sql-schema")?.addEventListener("click", () => {
        if (!state.storageDbPath) {
          this.notify("Select a database first.");
          return;
        }
        this.sendCommand("storage.sqlite_schema", { path: state.storageDbPath });
      });
    },

    openStorage(path) {
      state.storagePath = String(path || "").replace(/^\/+|\/+$/g, "");
      this.renderStorageCrumb();
      clearTimeout(state.storageTimer);
      state.storagePending = false;
      if (!state.clientId) {
        this.storageMessage("Choose one app in Scope first.");
        return;
      }
      const presence = state.client?.state || "online";
      const appName = state.client?.app_name || "The app";
      if (presence !== "online") {
        state.storagePending = true;
        const reason = presence === "background" ? "is in the background" : "is offline";
        this.storageMessage(`${appName} ${reason}. Open it on the device. The sandbox loads when it reconnects.`);
        return;
      }
      this.storageMessage("Loading sandbox…");
      state.storageTimer = setTimeout(() => {
        state.storagePending = true;
        this.storageMessage(`${appName} did not answer.`, true);
      }, 10000);
      this.sendCommand("storage.files", { path: state.storagePath });
    },

    storageMessage(text, retry = false) {
      const target = document.getElementById("storage-files");
      if (!target) return;
      const button = retry
        ? ` <button type="button" class="btn ghost storage-retry" data-storage-retry>Retry</button>`
        : "";
      target.innerHTML = `<tr class="static-row"><td colspan="4" class="muted">${escapeHtml(text)}${button}</td></tr>`;
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
      clearTimeout(state.storageTimer);
      state.storagePending = false;
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
        rows.push(`<tr data-path="${escapeAttr(parent)}" data-directory="true"><td class="col-check"></td><td class="col-name file-dir">..</td><td class="col-size"></td><td class="col-time"></td></tr>`);
      }
      if (!files.length && !state.storagePath) {
        rows.push(`<tr class="static-row"><td colspan="4" class="muted">Sandbox is empty.</td></tr>`);
      } else if (!files.length) {
        rows.push(`<tr class="static-row"><td colspan="4" class="muted">Empty folder.</td></tr>`);
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
        const actions = PROTECTED_STORAGE.has(path)
          ? ""
          : `<span class="file-actions"><button type="button" class="btn ghost" data-file-rename="${escapeAttr(path)}" aria-label="Rename ${escapeAttr(name)}">Rename</button><button type="button" class="btn ghost" data-file-delete="${escapeAttr(path)}" aria-label="Delete ${escapeAttr(name)}">Delete</button></span>`;
        rows.push(`<tr data-path="${escapeAttr(path)}" data-directory="${directory ? "true" : "false"}" data-name="${escapeAttr(name)}"${active}>
          <td class="col-check">${check}</td>
          <td class="col-name${directory ? " file-dir" : ""}" title="${escapeAttr(name)}">${escapeHtml(directory ? `${name}/` : name)}${schema}${actions}</td>
          <td class="col-size">${directory ? "" : escapeHtml(this.fmtBytes(file.bytes))}</td>
          <td class="col-time">${escapeHtml(this.fmtTime(file.modified))}</td>
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
      if (!target || !window.VLFilePreview) return;
      const payload = result || {};
      if (payload.path && state.storageRead && payload.path !== state.storageRead) return;
      if (state.previewUrl) {
        URL.revokeObjectURL(state.previewUrl);
        state.previewUrl = "";
      }
      const preview = window.VLFilePreview.prepare(payload);
      target.innerHTML = preview.html;
      const image = target.querySelector(".img-preview");
      const dims = target.querySelector("[data-file-dims]");
      if (image && dims) {
        image.addEventListener("load", () => {
          if (image.naturalWidth) dims.textContent = `${image.naturalWidth} × ${image.naturalHeight}`;
        });
        image.addEventListener("error", () => {
          dims.textContent = "Browser could not decode this image.";
        });
      }
      const media = target.querySelector("[data-file-media]");
      if (media && preview.bytes && preview.bytes.length) {
        const blob = new Blob([preview.bytes], { type: preview.mime || "application/octet-stream" });
        state.previewUrl = URL.createObjectURL(blob);
        media.src = state.previewUrl;
      }
      target.querySelector("[data-file-download]")?.addEventListener("click", () => {
        const blob = new Blob([preview.bytes], { type: preview.mime || "application/octet-stream" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = preview.filename || "file";
        link.click();
        URL.revokeObjectURL(url);
      });
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
        target.innerHTML = window.ViewliftNetwork.copyBlockHtml(JSON.stringify(result, null, 2), "Copy JSON");
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
      document.getElementById("bug-result").innerHTML = window.ViewliftNetwork.copyBlockHtml(
        JSON.stringify({ performance: result.performance, memory: result.memory, app: result.app }, null, 2),
        "Copy JSON"
      );
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
        memory: "memory-status", ui: "ui-properties", swiftui: "swiftui-result",
        assets: "assets-result", accessibility: "accessibility-result", storage: "storage-inspector",
        feature_flags: "flags-result", bug: "bug-result", macro: "macro-result",
        runtime: "runtime-result", push: "push-result",
        ott: "ott-sections", hang: "hangs-result", crash: "hangs-result",
        playback: "playback-timeline", nav: "nav-status",
        activity: "live-note", explorer: "explorer-result", settings: "settings-result",
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
