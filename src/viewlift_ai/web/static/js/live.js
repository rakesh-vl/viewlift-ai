// Live tab: current screen and playback, screenshot, and this app's log tail.
(() => {
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  const PLAYBACK = {
    playing: "Playing",
    paused: "Paused",
    buffering: "Buffering",
    none: "Not playing",
  };

  const state = {
    send: null,
    clientId: () => "",
    active: false,
    tracking: false,
    follow: false,
    userPaused: false,
    followSince: 0,
    lastFrame: 0,
    paint: null,
    pendingShots: 0,
    shotBusy: false,
    shotToken: 0,
    lastLive: 0,
    lastCaptured: 0,
    startSent: 0,
    startFails: 0,
    boundClient: "",
    logsOpen: false,
    logGen: 0,
    logs: [],
    logFrame: 0,
    socket: null,
    staleTimer: 0,
    capturing: false,
    captureBusy: false,
  };

  const $ = (id) => document.getElementById(id);

  function clock(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const mm = String(minutes).padStart(2, "0");
    const ss = String(secs).padStart(2, "0");
    return hours ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`;
  }

  function playbackLine(playback) {
    const name = PLAYBACK[playback.state] || playback.state || "Not playing";
    if (!playback.state || playback.state === "none") return "Not playing";
    const kind = playback.is_live ? "Live" : "On demand";
    const title = playback.title ? ` · ${playback.title}` : "";
    return `${name} · ${kind}${title}`;
  }

  function metric(label, value, textual) {
    const strong = textual ? "metric-text" : "";
    return `<div class="metric"><span class="muted">${escapeHtml(label)}</span><strong class="${strong}">${escapeHtml(value || "—")}</strong></div>`;
  }

  const Live = {
    init({ send, clientId, paint }) {
      state.send = send;
      state.clientId = clientId;
      state.paint = paint || null;
      state.active = document.querySelector("[data-debug-tab].active")?.dataset.debugTab === "live";
      $("live-screenshot")?.addEventListener("click", () => this.requestShot());
      $("live-follow")?.addEventListener("click", () => this.setFollow(!state.follow));
      $("live-log-start")?.addEventListener("click", () => this.startCapture());
      $("live-log-stop")?.addEventListener("click", () => this.stopCapture());
      document.addEventListener("visibilitychange", () => {
        if (!state.active || !state.tracking || !state.clientId()) return;
        this.pushPreview(state.follow && !document.hidden);
      });
      state.staleTimer = window.setInterval(() => {
        this.nudgeTracking();
        this.renderPill();
      }, 2000);
      this.syncButtons();
      this.refreshCapture();
      this.renderPill();
    },

    setActive(active) {
      state.active = active;
      if (!active) {
        state.follow = false;
        this.stopTracking();
        this.closeLogs();
        this.renderPill();
        this.syncButtons();
        return;
      }
      if (!state.userPaused) state.follow = Boolean(state.clientId());
      this.openLogs();
      if (state.clientId()) this.startTracking();
      this.syncButtons();
    },

    onClient() {
      const id = state.clientId() || "";
      const same = Boolean(id) && id === state.boundClient && state.logsOpen;
      state.boundClient = id;
      if (state.active && !state.userPaused) state.follow = Boolean(id);
      this.syncButtons();
      if (!state.active) return;
      if (same) this.refreshCapture();
      else this.openLogs();
      if (id && !state.tracking) this.startTracking();
      else if (id && state.tracking) this.pushPreview(state.follow && !document.hidden);
    },

    reset() {
      state.userPaused = false;
      state.follow = false;
      state.lastFrame = 0;
      this.stopTracking();
      state.boundClient = "";
      this.closeLogs();
      this.clearView();
    },

    onFrame(payload) {
      if (!state.follow || !payload || !payload.base64) return;
      state.lastFrame = Date.now();
      state.lastLive = state.lastFrame;
      if (state.paint) state.paint(payload);
    },

    onLive(payload) {
      const captured = Number(payload.captured_at) || 0;
      const repeat = captured > 0 && captured === state.lastCaptured;
      if (!repeat) {
        state.lastCaptured = captured;
        state.lastLive = Date.now();
      }
      state.tracking = payload.tracking !== false;
      if (state.tracking) {
        state.startFails = 0;
        const note = $("live-note");
        if (note) note.innerHTML = "";
      }
      this.renderNow(payload);
      this.renderPill();
    },

    onResult(name, result) {
      if (name === "activity.live_stop") state.tracking = false;
      if (name === "activity.live_start") state.tracking = true;
      if (result && (result.screen_class !== undefined || result.playback)) {
        state.lastLive = Date.now();
        this.renderNow(result);
      }
      const note = $("live-note");
      if (note) note.innerHTML = "";
      this.renderPill();
    },

    onCommandError(name, message) {
      if (name === "activity.live_start") {
        state.tracking = false;
        state.startFails += 1;
        this.renderPill();
        if (state.startFails < 3) return;
        this.showError(message || "Live activity did not start.");
        return;
      }
      this.showError(message || "Command failed");
    },

    takeScreenshotTarget() {
      if (state.pendingShots <= 0) return "";
      state.pendingShots -= 1;
      if (state.pendingShots === 0) {
        state.shotBusy = false;
        this.syncButtons();
      }
      return "live-shot";
    },

    showError(message) {
      const note = $("live-note");
      if (note) note.innerHTML = `<p class="warn-text">${escapeHtml(message)}</p>`;
    },

    async requestShot() {
      if (!state.clientId() || state.shotBusy) return;
      state.shotBusy = true;
      state.pendingShots += 1;
      const token = ++state.shotToken;
      this.syncButtons();
      window.setTimeout(() => {
        if (!state.shotBusy || state.shotToken !== token) return;
        state.shotBusy = false;
        this.syncButtons();
      }, 15000);
      let queued = false;
      try {
        queued = await state.send("ui.screenshot", { preview: true });
      } catch (_) {
        queued = false;
      }
      if (!queued && state.shotToken === token) {
        state.pendingShots = Math.max(0, state.pendingShots - 1);
        state.shotBusy = false;
        this.syncButtons();
      }
    },

    setFollow(enabled) {
      state.userPaused = !enabled;
      state.follow = Boolean(enabled) && state.active && Boolean(state.clientId());
      if (state.follow) state.followSince = Date.now();
      this.syncButtons();
      if (!state.clientId()) return;
      if (state.tracking) this.pushPreview(state.follow && !document.hidden);
      else if (state.follow) this.startTracking();
    },

    pushPreview(enabled) {
      state.send("activity.live_start", { preview: Boolean(enabled) });
    },

    startTracking() {
      if (!state.clientId() || !state.active) return;
      state.tracking = true;
      state.startSent = Date.now();
      if (state.follow) state.followSince = Date.now();
      state.send("activity.live_start", { preview: state.follow && !document.hidden });
      this.renderPill();
    },

    stopTracking() {
      if (!state.tracking) return;
      state.tracking = false;
      state.lastCaptured = 0;
      if (state.clientId()) state.send("activity.live_stop", {});
      this.renderPill();
    },

    nudgeTracking() {
      if (!state.active || !state.clientId()) return;
      const now = Date.now();
      const stale = !state.lastLive || now - state.lastLive > 8000;
      if (!(state.tracking && !stale)) {
        if (now - state.startSent >= 4000) this.startTracking();
      }
      this.nudgePreview();
    },

    nudgePreview() {
      if (!state.follow || document.hidden || state.shotBusy || !state.clientId()) return;
      if (state.lastFrame && Date.now() - state.lastFrame < 3000) return;
      if (Date.now() - state.followSince < 1500) return;
      this.requestShot();
    },

    renderPill() {
      const pill = $("live-activity-pill");
      if (!pill) return;
      const fresh = state.tracking && state.lastLive && Date.now() - state.lastLive < 6000;
      const waiting = state.tracking && !fresh;
      pill.textContent = fresh ? "Live" : waiting ? "Waiting" : "Off";
      pill.className = `memory-pill${fresh ? " is-live" : waiting ? " is-waiting" : ""}`;
    },

    syncButtons() {
      const hasApp = Boolean(state.clientId());
      const follow = $("live-follow");
      if (follow) {
        follow.disabled = !hasApp;
        follow.setAttribute("aria-pressed", state.follow ? "true" : "false");
        follow.classList.toggle("active", state.follow);
      }
      const shot = $("live-screenshot");
      if (shot) shot.disabled = !hasApp || state.shotBusy;
      const start = $("live-log-start");
      if (start) {
        start.disabled = !hasApp || state.captureBusy;
        start.setAttribute("aria-pressed", state.capturing ? "true" : "false");
        start.classList.toggle("active", state.capturing);
      }
      const stop = $("live-log-stop");
      if (stop) stop.disabled = !state.capturing || state.captureBusy;
    },

    renderNow(payload) {
      const playback = payload.playback || {};
      const screen = payload.screen_title || payload.screen_class || "—";
      const rows = [
        metric("Screen", screen, true),
        metric("Class", payload.screen_class || "—", true),
        metric("App", payload.lifecycle || "—", true),
        metric("Playback", playbackLine(playback), true),
        metric("Position", clock(playback.position_seconds)),
        metric("Rate", playback.rate === undefined || playback.rate === null ? "—" : String(playback.rate)),
      ];
      if (playback.content_id) rows.push(metric("Content", playback.content_id, true));
      const host = $("live-now");
      if (host) host.innerHTML = rows.join("");
      const updated = $("live-updated");
      if (updated) {
        const when = payload.captured_at ? new Date(payload.captured_at * 1000) : new Date();
        updated.textContent = Number.isNaN(when.getTime())
          ? ""
          : `Updated ${when.toLocaleTimeString([], { hour12: false })}`;
      }
    },

    clearView() {
      const now = $("live-now");
      if (now) now.innerHTML = "";
      const updated = $("live-updated");
      if (updated) updated.textContent = "";
      const note = $("live-note");
      if (note) note.innerHTML = "";
      const shot = $("live-shot");
      if (shot) shot.innerHTML = '<p class="muted">No screenshot yet.</p>';
      state.logs = [];
      this.renderLogs();
      this.renderPill();
    },

    async openLogs() {
      const id = state.clientId();
      const gen = ++state.logGen;
      state.logsOpen = true;
      this.closeLogSocket();
      if (!id) {
        state.logs = [];
        this.renderLogs();
        this.setLogStatus("Choose an app in Scope to see its logs.");
        return;
      }
      try {
        const response = await fetch(`/api/logs?client_id=${encodeURIComponent(id)}&limit=80`);
        const data = await response.json();
        if (gen !== state.logGen) return;
        state.logs = (data.logs || []).slice().reverse();
        this.renderLogs();
        this.setLogStatus(state.capturing
          ? "Capturing device logs."
          : "Stored lines for this app. Start capture to stream the simulator or device.");
      } catch (_) {
        if (gen === state.logGen) this.setLogStatus("Could not load logs.");
      }
      if (gen === state.logGen) {
        this.connectLogSocket(id, gen);
        await this.refreshCapture();
        if (gen === state.logGen && state.capturing) this.setLogStatus("Capturing device logs.");
      }
    },

    closeLogs() {
      state.logGen += 1;
      state.logsOpen = false;
      this.closeLogSocket();
    },

    connectLogSocket(id, gen) {
      this.closeLogSocket();
      state.socket = window.VLLive.connect(`/ws/logs?client_id=${encodeURIComponent(id)}`, {
        onResync: () => this.reloadLogTail(id, gen),
        onMessage: (data) => {
          if (gen !== state.logGen) return;
          if (data.type !== "log" || !data.log) return;
          if (data.log.client_id && data.log.client_id !== id) return;
          state.logs.push(data.log);
          if (state.logs.length > 80) state.logs.shift();
          this.renderLogs();
        },
      });
    },

    async reloadLogTail(id, gen) {
      try {
        const response = await fetch(`/api/logs?client_id=${encodeURIComponent(id)}&limit=80`);
        const data = await response.json();
        if (gen !== state.logGen) return;
        state.logs = (data.logs || []).slice().reverse();
        this.renderLogs();
      } catch (_) {
        /* The next reconnect tries again. */
      }
    },

    closeLogSocket() {
      const feed = state.socket;
      state.socket = null;
      if (feed) feed.close();
    },

    async refreshCapture() {
      try {
        const response = await fetch("/api/logs/status");
        const data = await response.json();
        state.capturing = Boolean(data.running);
      } catch (_) {
        state.capturing = false;
      }
      this.syncButtons();
    },

    async startCapture() {
      const id = state.clientId();
      if (!id) {
        this.setLogStatus("Choose an app in Scope first.");
        this.syncButtons();
        return;
      }
      if (state.capturing || state.captureBusy) return;
      state.captureBusy = true;
      this.syncButtons();
      this.setLogStatus("Starting capture…");
      try {
        const response = await fetch("/api/debug/logs/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: id }),
        });
        const data = await response.json().catch(() => ({}));
        state.capturing = response.ok;
        this.setLogStatus(response.ok ? "Capturing device logs." : (data.detail || "Could not start capture."));
      } catch (_) {
        state.capturing = false;
        this.setLogStatus("Could not start capture.");
      } finally {
        state.captureBusy = false;
        this.syncButtons();
      }
    },

    async stopCapture() {
      if (!state.capturing || state.captureBusy) return;
      state.captureBusy = true;
      this.syncButtons();
      this.setLogStatus("Stopping capture…");
      try {
        const response = await fetch("/api/debug/logs/stop", { method: "POST" });
        const data = await response.json().catch(() => ({}));
        if (response.ok) state.capturing = false;
        this.setLogStatus(response.ok ? "Capture stopped." : (data.detail || "Could not stop capture."));
      } catch (_) {
        this.setLogStatus("Could not stop capture.");
      } finally {
        state.captureBusy = false;
        this.syncButtons();
      }
    },

    setLogStatus(text) {
      const node = $("live-log-status");
      if (node) node.textContent = text;
    },

    renderLogs() {
      if (state.logFrame) return;
      state.logFrame = window.requestAnimationFrame(() => {
        state.logFrame = 0;
        this.paintLogs();
      });
    },

    paintLogs() {
      const list = $("live-log-list");
      if (!list) return;
      const stick = list.scrollHeight - list.scrollTop - list.clientHeight < 64;
      list.innerHTML = state.logs.map((log) => {
        const level = ["info", "warning", "error"].includes(log.level) ? log.level : "info";
        const when = new Date(log.created_at);
        const stamp = Number.isNaN(when.getTime()) ? "" : when.toLocaleTimeString([], { hour12: false });
        const subsystem = log.subsystem ? `${log.subsystem} ` : "";
        return `<li><time>${escapeHtml(stamp)}</time><span class="lvl-${level}">${escapeHtml(level)}</span> ${escapeHtml(subsystem)}${escapeHtml(log.message || "")}</li>`;
      }).join("");
      if (stick) list.scrollTop = list.scrollHeight;
    },
  };

  window.ViewliftLive = Live;
})();
