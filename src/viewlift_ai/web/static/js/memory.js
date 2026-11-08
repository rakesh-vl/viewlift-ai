// Memory tab: live leak tracker, leak reports with replay, memory graph, heap snapshot.
(() => {
  const { ForceGraph, ReplayPlayer, LiveTimeline, formatBytes } = window.ViewliftMemoryVisuals;

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  const KIND_LABEL = {
    loaded: "Loaded",
    released: "Released",
    leak: "Leak",
    released_late: "Released late",
  };

  // The app polls every 500 ms; a command the server gave up on means it is not answering.
  const commandTimeoutMs = () => Number(window.ViewliftLimits?.get("command_timeout_seconds") || 0) * 1000;
  const STALE_SAMPLE_MS = 5000;
  const TRACKED = new Set(["memory.live_start", "memory.live_stop", "memory.snapshot", "memory.object"]);

  const state = {
    send: null,
    // idle | syncing | starting | live | stopping
    phase: "idle",
    client: null,
    synced: false,
    lastLive: 0,
    peak: 0,
    lastFootprint: 0,
    pending: new Map(),
    leaks: new Map(),
    objects: new Map(),
    objectFilter: "",
    inspectId: "",
    detail: null,
    feed: [],
    heapFilter: "",
    heap: null,
    timeline: null,
    graph: null,
    players: new Map(),
    staleTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const formatAge = (seconds) => {
    const value = Math.max(0, Number(seconds) || 0);
    if (value < 60) return `${Math.floor(value)} s`;
    if (value < 3600) return `${Math.floor(value / 60)} m ${Math.floor(value % 60)} s`;
    return `${Math.floor(value / 3600)} h ${Math.floor((value % 3600) / 60)} m`;
  };

  /** Counts a metric from its last value to the next one so changes are easy to follow. */
  const animateValue = (node, to, format) => {
    const from = Number(node.dataset.value || 0);
    node.dataset.value = String(to);
    if (reduceMotion || !Number.isFinite(from) || from === to) {
      node.textContent = format(to);
      return;
    }
    const started = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - started) / 450);
      const eased = 1 - (1 - t) ** 3;
      node.textContent = format(from + (to - from) * eased);
      if (t < 1 && node.dataset.value === String(to)) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  const Memory = {
    init({ send }) {
      state.send = send;
      state.timeline = new LiveTimeline($("memory-timeline"), $("memory-timeline-tip"));
      state.graph = new ForceGraph($("memory-graph"), {
        height: 520,
        tooltip: $("memory-graph-tip"),
        onSelect: (node, links) => this.renderNode(node, links),
      });
      $("memory-live-toggle").addEventListener("click", () => this.toggleLive());
      $("memory-heap-filter").addEventListener("input", (event) => {
        state.heapFilter = event.target.value.trim().toLowerCase();
        this.renderHeap();
      });
      $("memory-object-filter").addEventListener("input", (event) => {
        state.objectFilter = event.target.value.trim().toLowerCase();
        this.renderObjects();
      });
      $("memory-objects").addEventListener("click", (event) => {
        const row = event.target.closest("[data-object]");
        if (row) this.inspect(row.dataset.object);
      });
      $("memory-classes").addEventListener("click", (event) => {
        const row = event.target.closest("[data-class]");
        if (!row) return;
        $("memory-object-filter").value = row.dataset.class;
        state.objectFilter = row.dataset.class.toLowerCase();
        this.renderObjects();
        $("memory-objects").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
      });
      $("memory-node").addEventListener("click", (event) => {
        const button = event.target.closest("[data-inspect]");
        if (button) this.inspect(button.dataset.inspect);
      });
      $("memory-leaks").addEventListener("click", (event) => this.onLeakClick(event));
      $("memory-inspector-close").addEventListener("click", () => this.closeInspector());
      $("memory-inspector-scrim").addEventListener("click", () => this.closeInspector());
      $("memory-inspector-refresh").addEventListener("click", () => {
        if (state.inspectId) this.inspect(state.inspectId);
      });
      $("memory-inspector-copy").addEventListener("click", (event) => this.copyDetail(event.currentTarget));
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.inspectId) this.closeInspector();
      });
      state.staleTimer = setInterval(() => this.tick(), 1000);
      this.reset();
    },

    reset() {
      state.pending.forEach((timer) => clearTimeout(timer));
      state.pending.clear();
      state.phase = "idle";
      state.synced = false;
      state.lastLive = 0;
      state.peak = 0;
      state.lastFootprint = 0;
      state.leaks.clear();
      state.objects.clear();
      state.feed = [];
      state.heap = null;
      state.players.forEach((player) => player.pause());
      state.players.clear();
      state.timeline?.reset();
      state.graph?.setData([], []);
      ["memory-feed", "memory-classes", "memory-leaks", "memory-heap", "memory-node", "memory-status",
        "memory-metrics", "memory-objects"]
        .forEach((id) => { if ($(id)) $(id).innerHTML = ""; });
      this.closeInspector();
      this.renderMetrics({});
      this.renderObjects();
      this.renderLeaksEmpty();
      this.renderLiveState();
    },

    /** Called by the debug page whenever the selected app or its presence changes. */
    setClient(client) {
      const wasOnline = this.online();
      state.client = client || null;
      if (!this.online()) {
        state.pending.forEach((timer, name) => {
          clearTimeout(timer);
          this.failed(name, this.offlineText());
        });
        state.pending.clear();
      }
      this.renderLiveState();
      if (this.online() && (!wasOnline || !state.synced) && this.visible()) this.sync();
    },

    onShow() {
      if (this.online() && !state.synced) this.sync();
      this.renderLiveState();
    },

    online() {
      return state.client?.state === "online";
    },

    visible() {
      return !document.querySelector('[data-panel="memory"]')?.classList.contains("hidden");
    },

    appName() {
      return state.client?.app_name || "The app";
    },

    offlineText() {
      if (!state.client) return "Choose one app in Scope first.";
      if (state.client.state === "background") {
        return `${this.appName()} is in the background. Bring it to the foreground to use memory tools.`;
      }
      return `${this.appName()} is offline. Open it on the device; this page reconnects on its own.`;
    },

    /** Reads the real live state from the app. The app remembers live mode across launches. */
    sync() {
      if (state.pending.has("memory.snapshot")) return;
      if (state.phase === "idle") state.phase = "syncing";
      this.request("memory.snapshot", {});
    },

    toggleLive() {
      if (state.phase === "starting" || state.phase === "stopping" || state.phase === "syncing") return;
      const name = state.phase === "live" ? "memory.live_stop" : "memory.live_start";
      if (!this.online()) {
        this.status(this.offlineText(), true);
        this.renderLiveState();
        return;
      }
      state.phase = name === "memory.live_start" ? "starting" : "stopping";
      this.status(name === "memory.live_start" ? `Asking ${this.appName()} to start tracking…` : "Stopping live tracking…");
      this.request(name, {});
    },

    /** Sends a command and tracks it so the UI never waits forever for an app that went quiet. */
    async request(name, payload) {
      await window.ViewliftLimits?.ready;
      const timeoutMs = commandTimeoutMs();
      if (TRACKED.has(name) && timeoutMs > 0) {
        clearTimeout(state.pending.get(name));
        state.pending.set(name, setTimeout(() => {
          state.pending.delete(name);
          this.failed(name, this.online()
            ? `${this.appName()} did not answer ${name} within ${timeoutMs / 1000} s. Make sure it is in the foreground, then try again.`
            : this.offlineText());
        }, timeoutMs));
      }
      this.renderLiveState();
      const sent = await state.send(name, payload);
      if (!sent) {
        clearTimeout(state.pending.get(name));
        state.pending.delete(name);
        this.failed(name, "");
      }
    },

    settle(name) {
      clearTimeout(state.pending.get(name));
      state.pending.delete(name);
    },

    failed(name, message) {
      if (name === "memory.live_start" && state.phase === "starting") state.phase = "idle";
      if (name === "memory.live_stop" && state.phase === "stopping") state.phase = "live";
      if (name === "memory.snapshot" && state.phase === "syncing") state.phase = "idle";
      if (name === "memory.object") this.renderInspectorError(message || "Could not load this object.");
      if (message) this.status(message, true);
      this.renderLiveState();
    },

    onEvent(kind, payload) {
      if (kind === "live") this.onLive(payload || {});
      if (kind === "leak") this.addLeak(payload || {}, true);
    },

    onResult(name, result) {
      this.settle(name);
      if (name === "memory.live_start" || name === "memory.live_stop") {
        state.phase = result.live ? "live" : "idle";
        state.synced = true;
        if (!result.live) state.lastLive = 0;
        this.status(result.live
          ? "Live tracking is on. Reproduce the flow in the app. Leaks appear here with a replay."
          : "Live tracking is off. The replay buffer was cleared.");
        this.renderLiveState();
        return;
      }
      if (name === "memory.leaks") {
        (result.events || []).slice().reverse().forEach((event) => this.addLeak(event, false));
        if (!(result.events || []).length) this.status(result.note || "No leaks recorded.");
        return;
      }
      if (name === "memory.graph") return this.renderGraph(result);
      if (name === "memory.heap") {
        state.heap = result;
        return this.renderHeap();
      }
      if (name === "memory.object") return this.renderInspector(result);
      if (name === "memory.snapshot") {
        state.synced = true;
        if (state.phase === "syncing" || state.phase === "idle" || state.phase === "live") {
          state.phase = result.live ? "live" : "idle";
        }
        this.renderMetrics({ footprint_bytes: result.footprint_bytes, tracked_count: result.tracked_count,
          leak_count: result.leak_count });
        this.setObjects(result.tracked || []);
        this.renderLiveState();
        this.status(`${result.tracked_count || 0} tracked objects, ${result.leak_count || 0} suspected leaks.`
          + (result.live ? " Live tracking is on." : ""));
      }
    },

    status(text, warn = false) {
      const node = $("memory-status");
      if (!node) return;
      node.innerHTML = text ? `<p class="${warn ? "warn-text" : "muted"}">${escapeHtml(text)}</p>` : "";
    },

    showError(message, name = "") {
      if (name) this.settle(name);
      this.failed(name, message);
    },

    onLive(sample) {
      if (state.phase !== "stopping") state.phase = "live";
      state.synced = true;
      state.lastLive = Date.now();
      state.peak = Math.max(state.peak, sample.footprint_bytes || 0);
      state.timeline.push(sample, sample.activity || []);
      this.renderMetrics(sample);
      this.renderClasses(sample.classes || []);
      if (Array.isArray(sample.objects)) this.setObjects(sample.objects);
      this.pushFeed(sample.activity || []);
      this.renderLiveState();
    },

    tick() {
      this.renderLiveState();
      this.renderAges();
    },

    liveView() {
      if (!state.client) return { pill: "No app", tone: "", label: "Start live tracking", busy: false, disabled: true };
      if (!this.online()) {
        const away = state.client.state === "background" ? "App in background" : "App offline";
        return { pill: away, tone: "is-offline", label: state.phase === "live" ? "Stop live tracking" : "Start live tracking",
          busy: false, disabled: true };
      }
      if (state.phase === "syncing") return { pill: "Checking app…", tone: "is-busy", label: "Checking…", busy: true, disabled: true };
      if (state.phase === "starting") return { pill: "Starting…", tone: "is-busy", label: "Starting…", busy: true, disabled: true };
      if (state.phase === "stopping") return { pill: "Stopping…", tone: "is-busy", label: "Stopping…", busy: true, disabled: true };
      if (state.phase === "live") {
        const streaming = Date.now() - state.lastLive < STALE_SAMPLE_MS;
        return {
          pill: streaming ? "Live · recording replay" : "Waiting for samples",
          tone: streaming ? "is-live" : "is-waiting",
          label: "Stop live tracking",
          busy: false,
          disabled: false,
        };
      }
      return { pill: "Not tracking", tone: "", label: "Start live tracking", busy: false, disabled: false };
    },

    renderLiveState() {
      const button = $("memory-live-toggle");
      const pill = $("memory-live-pill");
      if (!button || !pill) return;
      const view = this.liveView();
      const label = button.querySelector(".memory-toggle-label");
      if (label) label.textContent = view.label; else button.textContent = view.label;
      button.disabled = view.disabled;
      button.classList.toggle("is-busy", view.busy);
      button.classList.toggle("is-stop", state.phase === "live");
      button.setAttribute("aria-pressed", String(state.phase === "live"));
      button.setAttribute("aria-busy", String(view.busy));
      button.title = view.disabled && !view.busy ? this.offlineText() : "";
      pill.className = `memory-pill ${view.tone}`;
      if (pill.textContent !== view.pill) pill.textContent = view.pill;
    },

    metricNodes() {
      const host = $("memory-metrics");
      if (!host.querySelector("[data-metric]")) {
        host.innerHTML = [
          ["footprint", "Footprint"],
          ["peak", "Peak this session"],
          ["tracked", "Live tracked objects"],
          ["leaks", "Open leaks"],
        ].map(([key, label]) => `<div class="metric memory-metric" data-metric="${key}"><span class="muted">${label}</span>`
          + `<strong>–</strong><span class="memory-trend" aria-hidden="true"></span></div>`).join("");
      }
      return Object.fromEntries([...host.querySelectorAll("[data-metric]")].map((node) => [node.dataset.metric, node]));
    },

    renderMetrics(sample) {
      const nodes = this.metricNodes();
      const openLeaks = [...state.leaks.values()].filter((leak) => leak.status === "alive").length;
      const bytes = (value) => formatBytes(value);
      const count = (value) => String(Math.round(value));
      const set = (key, value, format) => {
        const strong = nodes[key].querySelector("strong");
        if (value == null || value === 0 && key !== "leaks" && key !== "tracked") {
          strong.textContent = "–";
          strong.dataset.value = "0";
          return;
        }
        animateValue(strong, Number(value), format);
      };
      set("footprint", sample.footprint_bytes, bytes);
      set("peak", state.peak || null, bytes);
      set("tracked", sample.tracked_count, count);
      const leaks = sample.leak_count != null ? sample.leak_count : openLeaks;
      set("leaks", leaks, count);
      nodes.leaks.classList.toggle("is-alert", leaks > 0);
      const trend = nodes.footprint.querySelector(".memory-trend");
      if (sample.footprint_bytes && state.lastFootprint) {
        const delta = sample.footprint_bytes - state.lastFootprint;
        trend.textContent = Math.abs(delta) < 1024 ? "" : `${delta > 0 ? "▲" : "▼"} ${formatBytes(Math.abs(delta))}`;
        trend.className = `memory-trend ${delta > 0 ? "is-up" : "is-down"}`;
      }
      if (sample.footprint_bytes) state.lastFootprint = sample.footprint_bytes;
    },

    setObjects(rows) {
      const receivedAt = Date.now();
      const next = new Map();
      rows.forEach((row) => {
        if (!row.id) return;
        next.set(row.id, { ...row, receivedAt });
      });
      state.objects = next;
      this.renderObjects();
    },

    renderObjects() {
      const host = $("memory-objects");
      if (!host) return;
      const all = [...state.objects.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
      const filter = state.objectFilter;
      const rows = filter
        ? all.filter((row) => [row.class, row.screen, row.context].some((value) => String(value || "").toLowerCase().includes(filter)))
        : all;
      const count = $("memory-object-count");
      if (count) count.textContent = all.length ? (filter ? `${rows.length} of ${all.length}` : String(all.length)) : "";
      if (!rows.length) {
        host.innerHTML = `<p class="muted memory-empty">${all.length
          ? "No objects match this filter."
          : "No objects yet. Start live tracking, or press Refresh, to list every tracked view controller and registered object."}</p>`;
        return;
      }
      host.querySelector(".memory-empty")?.remove();
      const existing = new Map([...host.querySelectorAll("[data-object]")].map((node) => [node.dataset.object, node]));
      const nodes = rows.map((row) => {
        let node = existing.get(row.id);
        existing.delete(row.id);
        if (!node) {
          node = document.createElement("button");
          node.type = "button";
          node.className = "memory-object is-new";
          node.dataset.object = row.id;
          node.setAttribute("role", "listitem");
          node.innerHTML = `<span class="memory-object-dot" aria-hidden="true"></span>`
            + `<span class="memory-object-main"><span class="memory-object-class mono"></span>`
            + `<span class="memory-object-meta muted"></span></span>`
            + `<span class="memory-object-age mono muted"></span>`
            + `<span class="memory-object-badge"></span>`;
          node.addEventListener("animationend", () => node.classList.remove("is-new"), { once: true });
        }
        node.classList.toggle("is-leaked", Boolean(row.leaked));
        node.classList.toggle("is-selected", row.id === state.inspectId);
        node.querySelector(".memory-object-class").textContent = row.class || "Object";
        node.querySelector(".memory-object-meta").textContent = [this.contextLabel(row.context),
          row.screen ? `on ${row.screen}` : "", row.id].filter(Boolean).join(" · ");
        node.querySelector(".memory-object-badge").textContent = row.leaked ? "Leaked" : "Alive";
        node.dataset.age = String(row.age_seconds || 0);
        node.dataset.received = String(row.receivedAt || Date.now());
        node.setAttribute("aria-label", `${row.class}, ${row.leaked ? "leaked" : "alive"}. Show details.`);
        return node;
      });
      existing.forEach((node) => node.remove());
      host.replaceChildren(...nodes);
      this.renderAges();
    },

    renderAges() {
      const now = Date.now();
      document.querySelectorAll("#memory-objects [data-object]").forEach((node) => {
        const age = Number(node.dataset.age || 0) + (now - Number(node.dataset.received || now)) / 1000;
        const label = node.querySelector(".memory-object-age");
        if (label) label.textContent = formatAge(age);
      });
      const detailAge = $("memory-detail-age");
      if (detailAge && state.detail) {
        detailAge.textContent = formatAge(Number(state.detail.age_seconds || 0) + (now - state.detail.receivedAt) / 1000);
      }
    },

    contextLabel(context) {
      return {
        "view-controller": "View controller",
        "dismissed-view-controller": "Dismissed view controller",
        registered: "Registered object",
        "expected-release": "Expected to release",
      }[context] || context || "";
    },

    inspect(id) {
      if (!id) return;
      state.inspectId = id;
      state.detail = null;
      const panel = $("memory-inspector");
      $("memory-inspector-scrim").hidden = false;
      panel.classList.add("is-open");
      panel.setAttribute("aria-hidden", "false");
      const row = state.objects.get(id);
      $("memory-inspector-body").innerHTML = `<div class="memory-skeleton" aria-busy="true">`
        + `<h4 class="mono">${escapeHtml(row?.class || "Loading object")}</h4>`
        + `<span></span><span></span><span></span><span class="is-wide"></span><span></span></div>`;
      document.querySelectorAll("#memory-objects [data-object]").forEach((node) => {
        node.classList.toggle("is-selected", node.dataset.object === id);
      });
      panel.focus({ preventScroll: true });
      if (!this.online()) {
        this.renderInspectorError(this.offlineText());
        return;
      }
      this.request("memory.object", { id });
    },

    closeInspector() {
      const panel = $("memory-inspector");
      if (!panel) return;
      state.inspectId = "";
      state.detail = null;
      panel.classList.remove("is-open");
      panel.setAttribute("aria-hidden", "true");
      $("memory-inspector-scrim").hidden = true;
      document.querySelectorAll("#memory-objects .is-selected").forEach((node) => node.classList.remove("is-selected"));
    },

    renderInspectorError(message) {
      const body = $("memory-inspector-body");
      if (body && state.inspectId) body.innerHTML = `<p class="warn-text">${escapeHtml(message)}</p>`;
    },

    renderInspector(detail) {
      if (!state.inspectId || (detail.id && detail.id !== state.inspectId)) return;
      const body = $("memory-inspector-body");
      if (!detail.found) {
        state.detail = null;
        body.innerHTML = `<p class="warn-text">${escapeHtml(detail.error || "Object not found.")}</p>`;
        return;
      }
      state.detail = { ...detail, receivedAt: Date.now() };
      const retention = detail.retention || {};
      const holds = (retention.edges || []).filter((edge) => edge.from === detail.id);
      const nodesById = new Map((retention.nodes || []).map((node) => [node.id, node]));
      const created = detail.created_at ? new Date(detail.created_at * 1000).toLocaleTimeString() : "";
      const facts = [
        ["Address", detail.id],
        ["Module", detail.module],
        ["Kind", this.contextLabel(detail.context)],
        ["Created on screen", detail.screen || "unknown"],
        ["Created at", created],
        ["Age", `<span id="memory-detail-age">${escapeHtml(formatAge(detail.age_seconds))}</span>`, true],
        ["Instance size", detail.instance_bytes ? formatBytes(detail.instance_bytes) : ""],
        ["Holds", `${holds.length} references`],
      ].filter(([, value]) => value);
      const vc = detail.view_controller;
      const vcFacts = vc ? [
        ["Title", vc.title || "none"],
        ["View loaded", vc.view_loaded ? "Yes" : "No"],
        ["On screen", vc.in_window ? "Yes, in a window" : "No, not in any window"],
        ["Parent", vc.parent || "none"],
        ["Presented by", vc.presented_by || "none"],
        ["Child controllers", String(vc.children ?? 0)],
      ] : [];
      const factGrid = (rows) => `<dl class="memory-facts">${rows.map(([label, value, raw]) =>
        `<div><dt>${escapeHtml(label)}</dt><dd class="mono">${raw ? value : escapeHtml(value)}</dd></div>`).join("")}</dl>`;
      const chain = [detail.class, ...(detail.superclasses || [])];
      const suspect = detail.suspect;
      const suspectLoc = suspect?.file && suspect.line != null ? `${suspect.file}:${suspect.line}` : "";
      const leakedNote = detail.leaked
        ? `<p class="memory-callout is-leak">This object is still alive after its screen closed. Check “Held by” and the retain cycle candidates below.</p>`
        : vc && !vc.in_window
          ? `<p class="memory-callout">Not in any window right now. If its screen is closed and it stays alive, it is leaking.</p>`
          : "";
      body.innerHTML = `<div class="memory-detail">`
        + `<div class="memory-detail-head"><h4 class="mono">${escapeHtml(detail.class)}</h4>`
        + `<span class="memory-pill ${detail.leaked ? "is-leak" : "is-live"}">${detail.leaked ? "Leaked" : "Alive"}</span></div>`
        + `<p class="memory-chain mono">${chain.map((name) => `<span>${escapeHtml(name)}</span>`).join("")}</p>`
        + leakedNote
        + factGrid(facts)
        + (vcFacts.length ? `<h5>View controller</h5>${factGrid(vcFacts)}` : "")
        + (suspect ? `<h5>Created at</h5><p class="mono leak-suspect">${escapeHtml(suspect.symbol || suspect.raw || "")}`
          + `${suspectLoc ? ` · ${escapeHtml(suspectLoc)}` : ""}</p>` : "")
        + `<h5>Holds</h5>${holds.length ? `<ul class="leak-list">${holds.slice(0, 30).map((edge) =>
          `<li class="mono${edge.cycle ? " is-cycle" : ""}">.${escapeHtml(edge.label)} → ${escapeHtml(nodesById.get(edge.to)?.class || "Object")}</li>`).join("")}</ul>`
          : `<p class="muted">No stored references to other objects.</p>`}`
        + this.rootCause({}, retention)
        + ((retention.nodes || []).length > 1
          ? `<h5>Reference graph · ${(retention.nodes || []).length} objects${retention.truncated ? " (truncated)" : ""}</h5>`
            + `<div class="memory-graph-wrap"><canvas class="leak-graph" height="240"></canvas><div class="memory-tip leak-graph-tip" hidden></div></div>`
          : "")
        + this.stackHtml({ creation_frames: detail.creation_frames, executable: detail.executable })
        + `</div>`;
      const canvas = body.querySelector(".leak-graph");
      if (canvas) {
        new ForceGraph(canvas, { height: 240, tooltip: body.querySelector(".leak-graph-tip") })
          .setData(retention.nodes, retention.edges || []);
      }
    },

    detailText(detail) {
      if (!detail) return "";
      const retention = detail.retention || {};
      const nodesById = new Map((retention.nodes || []).map((node) => [node.id, node]));
      const lines = [
        `${detail.class} (${detail.leaked ? "leaked" : "alive"})`,
        `Address ${detail.id} · module ${detail.module || ""}`,
        `Superclasses ${(detail.superclasses || []).join(" < ") || "none"}`,
        `Kind ${this.contextLabel(detail.context)} · created on ${detail.screen || "unknown"} · ${formatAge(detail.age_seconds)} old`,
      ];
      if (detail.view_controller) {
        const vc = detail.view_controller;
        lines.push(`View controller: title "${vc.title || ""}", loaded ${vc.view_loaded}, on screen ${vc.in_window}, parent ${vc.parent || "none"}`);
      }
      if (detail.suspect) lines.push(`Created at ${detail.suspect.symbol || ""} ${detail.suspect.file ? `${detail.suspect.file}:${detail.suspect.line}` : ""}`.trim());
      lines.push("", "Holds");
      (retention.edges || []).filter((edge) => edge.from === detail.id)
        .forEach((edge) => lines.push(`.${edge.label} -> ${nodesById.get(edge.to)?.class || "Object"}`));
      lines.push("", "Held by");
      (retention.retained_by || []).forEach((row) => lines.push(`${row.class}.${row.property}`));
      lines.push("", "Retain cycle candidates");
      (retention.cycles || []).forEach((path) => lines.push(path.join(" -> ")));
      lines.push("", "Creation stack");
      (detail.creation_frames || []).forEach((frame) => lines.push(`${frame.image || ""} ${frame.symbol || frame.raw || ""}`.trim()));
      return lines.join("\n");
    },

    async copyDetail(button) {
      if (!state.detail) return;
      try {
        await navigator.clipboard.writeText(this.detailText(state.detail));
        button.textContent = "Copied";
      } catch (_) {
        button.textContent = "Copy failed";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 1500);
    },

    renderClasses(classes) {
      const host = $("memory-classes");
      if (!classes.length) {
        host.innerHTML = `<p class="muted">No tracked objects yet.</p>`;
        return;
      }
      const max = Math.max(...classes.map((item) => item.count), 1);
      const existing = new Map([...host.querySelectorAll("[data-class]")].map((row) => [row.dataset.class, row]));
      const leaked = new Set([...state.leaks.values()].filter((leak) => leak.status === "alive").map((leak) => leak.class));
      const top = classes.slice(0, 14);
      const rows = top.map((item) => {
        let row = existing.get(item.class);
        if (!row) {
          row = document.createElement("div");
          row.className = "memory-bar";
          row.dataset.class = item.class;
          row.title = "Show these objects in Live objects";
          row.innerHTML = `<span class="memory-bar-name mono"></span><span class="memory-bar-track"><span class="memory-bar-fill"></span></span><span class="memory-bar-count mono"></span>`;
        }
        row.classList.toggle("is-leaked", leaked.has(item.class));
        row.querySelector(".memory-bar-name").textContent = item.class;
        row.querySelector(".memory-bar-count").textContent = String(item.count);
        row.querySelector(".memory-bar-fill").style.transform = `scaleX(${item.count / max})`;
        return row;
      });
      host.replaceChildren(...rows);
    },

    pushFeed(activity) {
      if (!activity.length) return;
      const host = $("memory-feed");
      const fresh = activity.slice(-20).reverse().map((item) => {
        const li = document.createElement("li");
        li.className = `memory-feed-item kind-${item.kind}`;
        li.innerHTML = `<span class="memory-kind">${escapeHtml(KIND_LABEL[item.kind] || item.kind)}</span>`
          + `<span class="mono">${escapeHtml(item.class)}</span>`
          + `<time class="muted">${escapeHtml(new Date(item.timestamp * 1000).toLocaleTimeString())}</time>`;
        return li;
      });
      host.prepend(...fresh);
      while (host.children.length > 60) host.lastElementChild.remove();
    },

    renderLeaksEmpty() {
      const host = $("memory-leaks");
      if (host && !state.leaks.size) {
        host.innerHTML = `<p class="muted">No leaks yet. Start live tracking, open and close the screen you suspect, then wait about 3 seconds. A leaked screen shows here with a replay of your taps, the class, its creation stack, and what still holds it.</p>`;
      }
      const count = $("memory-leak-count");
      if (count) count.textContent = state.leaks.size ? `${state.leaks.size}` : "";
    },

    addLeak(leak, live) {
      if (!leak.id) return;
      const announce = live && Date.now() - Number(leak.timestamp || 0) * 1000 < 30000;
      const known = state.leaks.get(leak.id);
      if (known && !leak.replay && known.replay) leak.replay = known.replay;
      state.leaks.set(leak.id, leak);
      const host = $("memory-leaks");
      host.querySelector("p.muted")?.remove();
      const existing = host.querySelector(`[data-leak="${CSS.escape(leak.id)}"]`);
      const card = document.createElement("article");
      card.className = `leak-card${leak.status === "alive" ? "" : " is-released"}${announce ? " is-new" : ""}`;
      card.dataset.leak = leak.id;
      card.innerHTML = this.leakHtml(leak);
      if (existing) existing.replaceWith(card); else host.prepend(card);
      const replayHost = card.querySelector(".leak-replay");
      if (leak.replay?.frames?.length) {
        state.players.set(leak.id, new ReplayPlayer(replayHost, leak.replay, { title: leak.class || "object" }));
      }
      const retention = leak.retention || {};
      const miniCanvas = card.querySelector(".leak-graph");
      if (miniCanvas && (retention.nodes || []).length > 1) {
        new ForceGraph(miniCanvas, { height: 220, tooltip: card.querySelector(".leak-graph-tip") })
          .setData(retention.nodes, retention.edges || []);
      }
      this.renderLeaksEmpty();
      if (announce) this.status(`Leak detected: ${leak.class} is still alive after its screen closed.`, true);
    },

    leakHtml(leak) {
      const retention = leak.retention || {};
      const statusText = leak.status === "alive" ? "Still alive"
        : `Released late, after ${Math.round(leak.released_after_seconds || 0)} s`;
      const when = leak.timestamp ? new Date(leak.timestamp * 1000).toLocaleTimeString() : "";
      return `<header class="leak-head">`
        + `<h4>${escapeHtml(leak.class || "Object")}</h4>`
        + `<span class="memory-pill ${leak.status === "alive" ? "is-leak" : ""}">${escapeHtml(statusText)}</span>`
        + `<span class="muted">${escapeHtml(when)} · ${escapeHtml(leak.context || "")} · created on ${escapeHtml(leak.created_on_screen || "unknown screen")} · ${Math.round(leak.age_seconds || 0)} s old · footprint ${escapeHtml(formatBytes(leak.footprint_bytes))}</span>`
        + (leak.status === "alive" && leak.object_id
          ? `<button type="button" class="btn ghost" data-leak-inspect="${escapeHtml(leak.object_id)}">Inspect object</button>` : "")
        + `<button type="button" class="btn ghost" data-leak-copy="${escapeHtml(leak.id)}">Copy report</button>`
        + `</header>`
        + `<div class="leak-layout">`
        + `<div class="leak-replay">${this.replayFallback(leak)}</div>`
        + `<div class="leak-facts">${this.rootCause(leak, retention)}${this.actionsHtml(leak)}</div>`
        + `</div>`
        + this.retentionGraphHtml(retention)
        + this.consoleHtml(leak)
        + this.stackHtml(leak);
    },

    replayFallback(leak) {
      const shot = leak.screenshot || {};
      const raw = String(shot.base64 || "").replace(/[^A-Za-z0-9+/=]/g, "");
      const image = raw ? `<img class="hang-shot" width="200" alt="Screen when the leak was found" src="data:image/jpeg;base64,${raw}">` : "";
      return `${image}<p class="muted">${escapeHtml(leak.replay_note || "No replay for this leak.")}</p>`;
    },

    rootCause(leak, retention) {
      const parts = [];
      const suspect = leak.suspect;
      if (suspect) {
        const loc = suspect.file && suspect.line != null ? `${suspect.file}:${suspect.line}` : "";
        parts.push(`<h5>Created at</h5><p class="mono leak-suspect">${escapeHtml(suspect.symbol || suspect.raw || "")}${loc ? ` · ${escapeHtml(loc)}` : ""}</p>`);
      }
      const retainers = retention.retained_by || [];
      if (retainers.length) {
        parts.push(`<h5>Held by</h5><ul class="leak-list">${retainers.map((row) =>
          `<li class="mono">${escapeHtml(row.class)}.${escapeHtml(row.property)}</li>`).join("")}</ul>`);
      }
      const cycles = retention.cycles || [];
      if (cycles.length) {
        parts.push(`<h5>Retain cycle candidates</h5>${cycles.slice(0, 4).map((path) => `<p class="leak-cycle">${path.map((step, index) =>
          `<span class="${index % 2 ? "leak-edge" : "leak-node"}">${escapeHtml(index % 2 ? `.${step}` : step)}</span>`).join("")}</p>`).join("")}`);
      }
      const hints = retention.hints || [];
      if (hints.length) {
        parts.push(`<h5>Likely causes</h5><ul class="leak-list">${[...new Set(hints)].slice(0, 6).map((hint) =>
          `<li>${escapeHtml(hint)}</li>`).join("")}</ul>`);
      }
      if (!parts.length) {
        parts.push(`<p class="muted">No strong reference back to this object was found in its stored properties. Check closures passed to other objects, NotificationCenter, and singletons.</p>`);
      }
      return parts.join("");
    },

    actionsHtml(leak) {
      const actions = leak.actions || [];
      if (!actions.length) return `<h5>Steps before the leak</h5><p class="muted">No taps or scrolls were recorded.</p>`;
      return `<h5>Steps before the leak</h5><ol class="hang-actions">${actions.map((action) => {
        const bits = [action.kind, action.direction, action.label ? `“${action.label}”` : "", action.target_class,
          action.screen_class, this.ago(action.ago_ms)].filter(Boolean);
        return `<li>${escapeHtml(bits.join(" · "))}</li>`;
      }).join("")}</ol>`;
    },

    retentionGraphHtml(retention) {
      if ((retention.nodes || []).length <= 1) return "";
      return `<details class="leak-section" open><summary>What this object holds · ${(retention.nodes || []).length} objects${retention.truncated ? " (truncated)" : ""}</summary>`
        + `<div class="memory-graph-wrap"><canvas class="leak-graph" height="220"></canvas><div class="memory-tip leak-graph-tip" hidden></div></div></details>`;
    },

    consoleHtml(leak) {
      const lines = leak.console || [];
      if (!lines.length) return "";
      const text = lines.map((line) => `${line.source || "log"} · ${this.ago(line.ago_ms)} · ${line.message || ""}`).join("\n");
      return `<details class="leak-section"><summary>Console · ${lines.length} lines</summary><pre class="hang-console">${escapeHtml(text)}</pre></details>`;
    },

    stackHtml(leak) {
      const frames = leak.creation_frames || [];
      if (!frames.length) return "";
      const rows = frames.map((frame) => {
        const loc = frame.file && frame.line != null ? `${frame.file}:${frame.line}` : (frame.file || "");
        const app = leak.executable && frame.image === leak.executable;
        return `<div class="stack-frame${app ? " is-app" : ""}"><span class="loc">${escapeHtml(loc)}</span> `
          + `<span class="sym">${escapeHtml(frame.class ? `${frame.class} · ` : "")}${escapeHtml(frame.symbol || frame.raw || "")}</span></div>`;
      }).join("");
      return `<details class="leak-section"><summary>Creation stack · ${frames.length} frames · app frames highlighted</summary>${rows}</details>`;
    },

    ago(value) {
      const ms = Number(value);
      if (!Number.isFinite(ms)) return "";
      return ms < 1000 ? `${Math.round(ms)} ms before` : `${(ms / 1000).toFixed(1)} s before`;
    },

    async onLeakClick(event) {
      const inspect = event.target.closest("[data-leak-inspect]");
      if (inspect) {
        this.inspect(inspect.dataset.leakInspect);
        return;
      }
      const button = event.target.closest("[data-leak-copy]");
      if (!button) return;
      const leak = state.leaks.get(button.dataset.leakCopy);
      try {
        await navigator.clipboard.writeText(this.reportText(leak));
        button.textContent = "Copied";
      } catch (_) {
        button.textContent = "Copy failed";
      }
      setTimeout(() => { button.textContent = "Copy report"; }, 1500);
    },

    reportText(leak) {
      if (!leak) return "";
      const retention = leak.retention || {};
      const lines = [
        `Memory leak: ${leak.class}`,
        `Status ${leak.status} · ${leak.context} · created on ${leak.created_on_screen || "unknown"} · ${Math.round(leak.age_seconds || 0)} s old`,
        `Footprint ${formatBytes(leak.footprint_bytes)}`,
      ];
      if (leak.suspect) lines.push(`Created at ${leak.suspect.symbol || ""} ${leak.suspect.file ? `${leak.suspect.file}:${leak.suspect.line}` : ""}`.trim());
      lines.push("", "Held by");
      (retention.retained_by || []).forEach((row) => lines.push(`${row.class}.${row.property}`));
      if (!(retention.retained_by || []).length) lines.push("(none found)");
      lines.push("", "Retain cycle candidates");
      (retention.cycles || []).forEach((path) => lines.push(path.join(" → ")));
      if (!(retention.cycles || []).length) lines.push("(none found)");
      lines.push("", "Likely causes", ...new Set(retention.hints || []));
      lines.push("", "Steps to reproduce");
      (leak.actions || []).forEach((action, index) => lines.push(`${index + 1}. ${[action.kind, action.direction,
        action.label, action.target_class, action.screen_class, this.ago(action.ago_ms)].filter(Boolean).join(" · ")}`));
      lines.push("", "Creation stack");
      (leak.creation_frames || []).forEach((frame) => lines.push(`${frame.image || ""} ${frame.symbol || frame.raw || ""}`.trim()));
      return lines.join("\n");
    },

    renderGraph(result) {
      const nodes = result.nodes || [];
      $("memory-graph-note").textContent = nodes.length
        ? `${nodes.length} objects, ${(result.edges || []).length} references${result.truncated ? ", truncated" : ""}. ${result.coverage || ""}`
        : "No tracked objects. Open a few screens in the app, then refresh the graph.";
      state.graph.setData(nodes, result.edges || []);
      $("memory-node").innerHTML = `<p class="muted">Click a node to see what it holds and what holds it. Drag to rearrange.</p>`;
    },

    renderNode(node, links) {
      const host = $("memory-node");
      if (!node) { host.innerHTML = ""; return; }
      const list = (title, rows) => `<h5>${title}</h5>` + (rows.length
        ? `<ul class="leak-list">${rows.map((row) => `<li class="mono${row.cycle ? " is-cycle" : ""}">.${escapeHtml(row.label)} → ${escapeHtml(row.class)}</li>`).join("")}</ul>`
        : `<p class="muted">None</p>`);
      host.innerHTML = `<h4 class="mono">${escapeHtml(node.class)}</h4>`
        + `<p class="muted">${escapeHtml([node.leaked ? "Leaked" : "", node.tracked ? "Tracked" : "Referenced", node.context,
          node.screen ? `on ${node.screen}` : "", node.age_seconds != null ? `${Math.round(node.age_seconds)} s old` : ""].filter(Boolean).join(" · "))}</p>`
        + (node.tracked ? `<button type="button" class="btn ghost" data-inspect="${escapeHtml(node.id)}">Inspect object</button>` : "")
        + list("Holds", links.outgoing)
        + list("Held by", links.incoming.map((row) => ({ ...row, label: row.label, class: row.class })));
    },

    renderHeap() {
      const host = $("memory-heap");
      const heap = state.heap;
      if (!heap) return;
      const rows = (heap.classes || []).filter((row) => !state.heapFilter || row.class.toLowerCase().includes(state.heapFilter));
      const max = Math.max(...rows.map((row) => row.count), 1);
      const summary = `${heap.instance_count || 0} instances across ${heap.class_count || 0} classes · walked in ${Math.round(heap.duration_ms || 0)} ms · footprint ${formatBytes(heap.footprint_bytes)}`;
      const baseline = heap.baseline ? `<p class="muted">Baseline taken. Repeat the flow in the app, then take another snapshot. Classes that grow are your leak suspects.</p>` : "";
      host.innerHTML = `<p class="muted">${escapeHtml(summary)}</p>${baseline}`
        + `<table class="debug-table heap-table"><thead><tr><th scope="col">Class</th><th scope="col">Live</th><th scope="col">Change</th><th scope="col">Instance bytes</th><th scope="col" aria-label="Share of live instances"></th></tr></thead><tbody>`
        + rows.map((row) => {
          const delta = Number(row.delta || 0);
          const tone = delta > 0 ? "is-up" : delta < 0 ? "is-down" : "";
          return `<tr class="${tone}"><td class="mono">${escapeHtml(row.class)} <button type="button" class="btn ghost heap-explore" data-explore-class="${escapeHtml(row.class)}" aria-label="Explore live ${escapeHtml(row.class)} instances">Explore</button></td><td class="mono">${row.count}</td>`
            + `<td class="mono heap-delta">${heap.baseline ? "" : delta > 0 ? `+${delta}` : String(delta)}</td>`
            + `<td class="mono">${escapeHtml(formatBytes(row.bytes))}</td>`
            + `<td class="heap-bar-cell"><span class="heap-bar" style="--share:${row.count / max}"></span></td></tr>`;
        }).join("")
        + `</tbody></table><p class="muted">${escapeHtml(heap.note || "")}</p>`;
    },
  };

  window.ViewliftMemory = Memory;
})();
