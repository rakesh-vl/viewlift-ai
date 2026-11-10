// QA tabs: accessibility scan, bug reporter, macro recorder, local notifications, and the
// network and device tools. Debug commands go through ViewliftDebug; results come back here.
(() => {
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const $ = (id) => document.getElementById(id);
  const downloadBlob = (blob, name) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  };
  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      return false;
    }
  };
  const fmtBytes = (value) => {
    const n = Number(value) || 0;
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
    if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
    return `${Math.round(n / 1024)} KB`;
  };
  const metric = (label, value, tone = "") => `<div class="metric${tone ? ` ${tone}` : ""}">`
    + `<span class="muted">${escapeHtml(label)}</span><strong class="metric-text">${escapeHtml(value)}</strong></div>`;

  // Mirrors NetworkConditions.presets in the SDK.
  const APP_PROFILES = {
    wifi: "40 ms · 40 Mbps",
    "4g": "80 ms · 12 Mbps",
    "3g": "200 ms · 780 kbps",
    edge: "400 ms · 240 kbps",
    gprs: "500 ms · 50 kbps · 1% loss",
    offline: "Every request fails",
  };
  const SEVERITY_LABEL = { error: "Error", warning: "Warning", info: "Suggestion" };
  const MAX_PUSH_IMAGE = 40_000;
  const MAX_PUSH_ACTIONS = 4;

  const state = {
    send: async () => false,
    notify: () => {},
    client: () => null,
    a11y: null,
    a11ySelected: "",
    a11yHidden: new Set(),
    bug: null,
    bugMarks: [],
    bugTool: "pen",
    bugColor: "#ff3b30",
    bugDraft: null,
    macro: { actions: [], recording: false, results: [] },
    macroPoll: 0,
    pushImage: null,
    pushStatusTimer: 0,
    profiles: [],
    activeProfile: { app: "off", proxy: "off" },
  };

  const QA = {
    init({ send, notify, client }) {
      state.send = send;
      state.notify = notify || state.notify;
      state.client = client || state.client;
      this.bindAccessibility();
      this.bindBug();
      this.bindMacro();
      this.bindPush();
      this.bindNetworkTools();
      this.renderSavedMacros();
    },

    /** Another app was selected. */
    reset() {
      state.a11y = null;
      state.bug = null;
      state.bugMarks = [];
      state.macro = { actions: [], recording: false, results: [] };
      clearInterval(state.macroPoll);
      state.macroPoll = 0;
      state.activeProfile.app = "off";
      ["a11y-summary", "accessibility-result", "a11y-shot", "bug-result", "macro-result", "push-permission"]
        .forEach((id) => { const node = $(id); if (node) node.innerHTML = ""; });
      const shot = $("a11y-shot");
      if (shot) shot.hidden = true;
      const filters = $("a11y-filters");
      if (filters) filters.hidden = true;
      this.paintBugEmpty();
      this.renderMacro();
      this.renderSavedMacros();
      this.renderProfiles();
    },

    onClient(client) {
      const hints = $("deeplink-hints");
      if (hints) {
        const recent = this.recentLinks();
        const schemes = (client?.url_schemes || []).map((scheme) => `${scheme}://`);
        hints.innerHTML = [...new Set([...recent, ...schemes])]
          .map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
      }
      const target = $("profile-target");
      if (target && !target.dataset.touched) target.value = client ? "app" : "proxy";
      this.renderSavedMacros();
      this.renderProfiles();
    },

    onTabShown(tab) {
      if (tab === "network-tools") this.loadWaterfall();
      if (tab === "push" && state.client()) state.send("push.status", {});
      if (tab === "macro" && state.client()) state.send("macro.list", {});
    },

    /** Returns true when this module rendered the result. */
    onResult(name, result) {
      const handlers = {
        "accessibility.audit": () => this.renderAccessibility(result),
        "bug.capture": () => this.renderBug(result),
        "macro.record_start": () => this.onMacroState(result),
        "macro.record_stop": () => this.onMacroState(result),
        "macro.list": () => this.onMacroState(result),
        "macro.replay": () => this.onMacroReplay(result),
        "push.local": () => this.onPushScheduled(result),
        "push.status": () => this.renderPushStatus(result),
        "push.clear": () => {
          state.notify(`Removed ${result.removed || 0} test notification(s).`);
          state.send("push.status", {});
        },
        "net.profile": () => this.onAppProfile(result),
      };
      const handler = handlers[name];
      if (!handler) return false;
      handler();
      return true;
    },

    /** Returns true when the error was shown here. */
    onError(module, name, message) {
      const text = /unsupportedCommand/.test(message)
        ? "This app runs an older ViewliftNetworkCapture. Update the package and rebuild to use this tool."
        : /moduleDisabled/.test(message)
          ? "This module is turned off for the app. Turn it on in the Device settings tab."
          : /payloadTooLarge/.test(message)
            ? "The screenshot was too large to send. Update ViewliftNetworkCapture and rebuild."
            : message;
      const targets = {
        accessibility: "accessibility-result", bug: "bug-status", macro: "macro-status",
        push: "push-status", net: "profile-status",
      };
      const target = $(targets[module]);
      if (!target) return false;
      if (module === "macro") {
        state.macro.recording = false;
        this.renderMacro();
      }
      target.innerHTML = `<span class="warn-text">${escapeHtml(text)}</span>`;
      return true;
    },

    // ---------- Accessibility ----------

    bindAccessibility() {
      $("a11y-filters")?.addEventListener("change", (event) => {
        const box = event.target.closest("[data-a11y-severity]");
        if (!box) return;
        box.closest(".ui-check")?.classList.toggle("is-on", box.checked);
        if (box.checked) state.a11yHidden.delete(box.dataset.a11ySeverity);
        else state.a11yHidden.add(box.dataset.a11ySeverity);
        this.paintAccessibility();
      });
      $("a11y-copy")?.addEventListener("click", async () => {
        const ok = await copyText(this.accessibilityMarkdown());
        state.notify(ok ? "Accessibility report copied." : "Could not copy. Allow clipboard access.");
      });
      const select = (event) => {
        const item = event.target.closest("[data-finding]");
        if (!item) return;
        state.a11ySelected = state.a11ySelected === item.dataset.finding ? "" : item.dataset.finding;
        this.paintAccessibility();
        if (event.currentTarget.id === "a11y-shot") {
          $("accessibility-result")?.querySelector(`[data-finding="${CSS.escape(state.a11ySelected)}"]`)
            ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      };
      $("accessibility-result")?.addEventListener("click", select);
      $("a11y-shot")?.addEventListener("click", select);
    },

    /** Older SDKs sent {kind, path, message}; map them so the list still reads. */
    normalizeFindings(result) {
      return (result.findings || []).map((item, index) => ({
        id: item.id || `f${index}`,
        severity: item.severity || (item.kind === "missing-label" ? "error" : "warning"),
        rule: item.rule || item.kind || "",
        title: item.title || item.message || item.kind || "Finding",
        detail: item.detail || item.path || "",
        fix: item.fix || "",
        element: item.element || {},
        frame: item.frame || null,
      }));
    },

    renderAccessibility(result) {
      state.a11y = { ...result, findings: this.normalizeFindings(result) };
      state.a11ySelected = "";
      const findings = state.a11y.findings;
      const count = (severity) => findings.filter((item) => item.severity === severity).length;
      const counts = result.counts || {};
      $("a11y-summary").innerHTML = [
        metric("Screen", result.screen || "Current screen"),
        metric("Errors", counts.error ?? count("error"), (counts.error ?? count("error")) ? "metric-bad" : ""),
        metric("Warnings", counts.warning ?? count("warning")),
        metric("Suggestions", counts.info ?? count("info")),
        result.elements != null ? metric("Elements checked", result.elements) : "",
      ].join("");
      $("a11y-filters").hidden = !findings.length;
      const shot = $("a11y-shot");
      if (result.screenshot?.base64) {
        shot.hidden = false;
        shot.innerHTML = `<div class="a11y-shot-inner"><img alt="Screen that was scanned" src="data:${escapeHtml(result.screenshot.mime || "image/jpeg")};base64,${result.screenshot.base64}">`
          + `<div class="a11y-boxes" id="a11y-boxes"></div></div>`;
      } else {
        shot.hidden = true;
        shot.innerHTML = "";
      }
      this.paintAccessibility();
    },

    paintAccessibility() {
      const data = state.a11y;
      const host = $("accessibility-result");
      if (!data || !host) return;
      const shown = data.findings.filter((item) => !state.a11yHidden.has(item.severity));
      if (!data.findings.length) {
        host.innerHTML = data.elements === 0
          ? `<p class="muted">No accessibility elements were found. Bring the app to the foreground and scan again.</p>`
          : `<p class="ok-text">No issues found on this screen${data.elements ? ` (${data.elements} elements checked)` : ""}.</p>`;
      } else {
        const groups = ["error", "warning", "info"].map((severity) => {
          const rows = shown.filter((item) => item.severity === severity);
          if (!rows.length) return "";
          return `<h3 class="a11y-group">${SEVERITY_LABEL[severity]}s <span class="muted">${rows.length}</span></h3>`
            + rows.map((item) => this.findingHtml(item)).join("");
        }).join("");
        host.innerHTML = groups || `<p class="muted">All findings are filtered out.</p>`;
        if (data.truncated) host.insertAdjacentHTML("beforeend", `<p class="muted">More findings exist. Fix these and scan again.</p>`);
      }
      const boxes = $("a11y-boxes");
      const win = data.window || {};
      if (boxes && win.width && win.height) {
        boxes.innerHTML = shown.filter((item) => item.frame).map((item) => {
          const frame = item.frame;
          const style = `left:${frame.x / win.width * 100}%;top:${frame.y / win.height * 100}%;`
            + `width:${Math.max(frame.width / win.width * 100, 0.6)}%;height:${Math.max(frame.height / win.height * 100, 0.6)}%`;
          const selected = item.id === state.a11ySelected ? " selected" : "";
          return `<button type="button" class="a11y-box sev-${escapeHtml(item.severity)}${selected}" style="${style}" data-finding="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}" aria-label="${escapeHtml(item.title)}"></button>`;
        }).join("");
        boxes.classList.toggle("has-selection", Boolean(state.a11ySelected));
      }
    },

    findingHtml(item) {
      const element = item.element || {};
      const who = [
        element.role && element.role !== "element" ? element.role : "",
        element.label ? `"${element.label}"` : "",
        element.identifier ? `#${element.identifier}` : "",
      ].filter(Boolean).join(" · ");
      const selected = item.id === state.a11ySelected ? " selected" : "";
      return `<button type="button" class="a11y-item sev-${escapeHtml(item.severity)}${selected}" data-finding="${escapeHtml(item.id)}">`
        + `<span class="a11y-sev">${escapeHtml(SEVERITY_LABEL[item.severity] || item.severity)}</span>`
        + `<strong>${escapeHtml(item.title)}</strong>`
        + (who ? `<span class="mono muted">${escapeHtml(who)}</span>` : "")
        + (item.detail ? `<span>${escapeHtml(item.detail)}</span>` : "")
        + (item.fix ? `<span class="a11y-fix"><b>Fix:</b> ${escapeHtml(item.fix)}</span>` : "")
        + `</button>`;
    },

    accessibilityMarkdown() {
      const data = state.a11y;
      if (!data) return "";
      const app = state.client();
      const lines = [`# Accessibility scan: ${data.screen || "screen"}`, ""];
      if (app) lines.push(`App: ${app.app_name} ${app.app_version || ""} (${app.build || ""}) on ${app.device_name || app.platform || ""}`, "");
      for (const severity of ["error", "warning", "info"]) {
        const rows = data.findings.filter((item) => item.severity === severity);
        if (!rows.length) continue;
        lines.push(`## ${SEVERITY_LABEL[severity]}s (${rows.length})`, "");
        for (const item of rows) {
          const element = item.element || {};
          const who = [element.role, element.label && `"${element.label}"`, element.identifier && `#${element.identifier}`]
            .filter(Boolean).join(" ");
          lines.push(`- **${item.title}**${who ? ` (${who})` : ""}`);
          if (item.fix) lines.push(`  - Fix: ${item.fix}`);
        }
        lines.push("");
      }
      if (!data.findings.length) lines.push("No issues found.");
      return lines.join("\n");
    },

    // ---------- Bug reporter ----------

    bindBug() {
      const canvas = $("bug-canvas");
      if (!canvas) return;
      const point = (event) => {
        const rect = canvas.getBoundingClientRect();
        return {
          x: (event.clientX - rect.left) * canvas.width / rect.width,
          y: (event.clientY - rect.top) * canvas.height / rect.height,
        };
      };
      canvas.addEventListener("pointerdown", (event) => {
        if (!state.bug) return;
        canvas.setPointerCapture(event.pointerId);
        const p = point(event);
        state.bugDraft = { tool: state.bugTool, color: state.bugColor, points: [p], from: p, to: p };
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!state.bugDraft) return;
        const p = point(event);
        if (state.bugDraft.tool === "pen") state.bugDraft.points.push(p);
        state.bugDraft.to = p;
        this.drawBug();
      });
      const finish = () => {
        if (!state.bugDraft) return;
        state.bugMarks.push(state.bugDraft);
        state.bugDraft = null;
        this.drawBug();
      };
      canvas.addEventListener("pointerup", finish);
      canvas.addEventListener("pointercancel", finish);
      document.querySelectorAll("[data-bug-tool]").forEach((button) => {
        button.addEventListener("click", () => {
          state.bugTool = button.dataset.bugTool;
          document.querySelectorAll("[data-bug-tool]").forEach((item) => {
            const on = item === button;
            item.classList.toggle("active", on);
            item.setAttribute("aria-pressed", String(on));
          });
        });
      });
      document.querySelectorAll("[data-bug-color]").forEach((button) => {
        button.style.background = button.dataset.bugColor;
        button.addEventListener("click", () => {
          state.bugColor = button.dataset.bugColor;
          document.querySelectorAll("[data-bug-color]").forEach((item) => item.classList.toggle("active", item === button));
        });
      });
      $("bug-undo").addEventListener("click", () => { state.bugMarks.pop(); this.drawBug(); });
      $("bug-clear").addEventListener("click", () => { state.bugMarks = []; this.drawBug(); });
      $("bug-download").addEventListener("click", () => {
        canvas.toBlob((blob) => blob && downloadBlob(blob, `${this.bugFileStem()}.png`), "image/png");
      });
      $("bug-download-md").addEventListener("click", async () => {
        const markdown = await this.bugMarkdown(`${this.bugFileStem()}.png`);
        downloadBlob(new Blob([markdown], { type: "text/markdown" }), `${this.bugFileStem()}.md`);
        canvas.toBlob((blob) => blob && downloadBlob(blob, `${this.bugFileStem()}.png`), "image/png");
      });
      $("bug-copy").addEventListener("click", () => this.copyBugReport());
    },

    renderBug(result) {
      const mime = result.mime || result.screenshot_mime;
      const base64 = result.base64 || result.screenshot_base64;
      if (!base64) {
        $("bug-status").innerHTML = `<span class="warn-text">The app sent no screenshot.</span>`;
        return;
      }
      const image = new Image();
      image.onload = () => {
        state.bug = { ...result, image };
        state.bugMarks = [];
        const canvas = $("bug-canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.hidden = false;
        $("bug-empty").hidden = true;
        ["bug-copy", "bug-download", "bug-download-md"].forEach((id) => { $(id).disabled = false; });
        const form = $("bug-form");
        if (form && !form.elements.actual.value && result.screen) form.elements.actual.placeholder = `On ${result.screen}: …`;
        this.drawBug();
        this.renderBugContext();
        $("bug-status").textContent = "Captured. Draw on the screenshot, then copy or download the report.";
      };
      image.src = `data:${mime || "image/png"};base64,${base64}`;
    },

    paintBugEmpty() {
      const canvas = $("bug-canvas");
      if (canvas) canvas.hidden = true;
      const empty = $("bug-empty");
      if (empty) empty.hidden = false;
      ["bug-copy", "bug-download", "bug-download-md", "bug-undo", "bug-clear"].forEach((id) => {
        const node = $(id);
        if (node) node.disabled = true;
      });
      const status = $("bug-status");
      if (status) status.textContent = "";
    },

    drawBug() {
      const canvas = $("bug-canvas");
      if (!state.bug || !canvas) return;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(state.bug.image, 0, 0);
      const width = Math.max(3, Math.round(canvas.width / 220));
      const marks = state.bugDraft ? [...state.bugMarks, state.bugDraft] : state.bugMarks;
      for (const mark of marks) {
        ctx.strokeStyle = mark.color;
        ctx.fillStyle = mark.color;
        ctx.lineWidth = width;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        if (mark.tool === "pen") {
          mark.points.forEach((p, index) => (index ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
          ctx.stroke();
        } else if (mark.tool === "box") {
          ctx.strokeRect(mark.from.x, mark.from.y, mark.to.x - mark.from.x, mark.to.y - mark.from.y);
        } else {
          const angle = Math.atan2(mark.to.y - mark.from.y, mark.to.x - mark.from.x);
          const head = width * 5;
          ctx.moveTo(mark.from.x, mark.from.y);
          ctx.lineTo(mark.to.x, mark.to.y);
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(mark.to.x, mark.to.y);
          ctx.lineTo(mark.to.x - head * Math.cos(angle - 0.45), mark.to.y - head * Math.sin(angle - 0.45));
          ctx.lineTo(mark.to.x - head * Math.cos(angle + 0.45), mark.to.y - head * Math.sin(angle + 0.45));
          ctx.closePath();
          ctx.fill();
        }
      }
      $("bug-undo").disabled = !state.bugMarks.length;
      $("bug-clear").disabled = !state.bugMarks.length;
    },

    renderBugContext() {
      const bug = state.bug;
      const perf = bug.performance || {};
      const app = bug.app && typeof bug.app === "object" ? bug.app : {};
      const device = bug.device || {};
      $("bug-result").innerHTML = `<div class="metric-grid">${[
        metric("Screen", bug.screen || "—"),
        metric("App", [app.name, app.version, app.build && `(${app.build})`].filter(Boolean).join(" ") || String(bug.app || "")),
        metric("Device", [device.name, device.system].filter(Boolean).join(" · ") || "—"),
        perf.memory_bytes != null ? metric("Memory", fmtBytes(perf.memory_bytes)) : "",
        perf.cpu_percent != null ? metric("CPU", `${Math.round(perf.cpu_percent)}%`) : "",
        perf.fps != null ? metric("FPS", Math.round(perf.fps)) : "",
      ].join("")}</div>`;
    },

    bugFileStem() {
      const title = $("bug-form")?.elements.title.value.trim() || "bug";
      return `viewlift-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "bug"}`;
    },

    async recentFailures() {
      const client = state.client();
      if (!client) return [];
      try {
        const params = new URLSearchParams({ limit: "200", client_id: client.id });
        const flows = (await (await fetch(`/api/flows?${params}`)).json()).flows || [];
        return flows.filter((flow) => flow.error || Number(flow.status) >= 400).slice(0, 8);
      } catch (_) {
        return [];
      }
    },

    async bugMarkdown(imageName = "") {
      const bug = state.bug || {};
      const form = $("bug-form").elements;
      const app = bug.app && typeof bug.app === "object" ? bug.app : {};
      const device = bug.device || {};
      const perf = bug.performance || {};
      const section = (title, text) => (text.trim() ? [`## ${title}`, "", text.trim(), ""] : []);
      const lines = [`# ${form.title.value.trim() || "Bug report"}`, ""];
      lines.push(...section("Steps to reproduce", form.steps.value));
      lines.push(...section("Expected", form.expected.value));
      lines.push(...section("Actual", form.actual.value));
      lines.push("## Environment", "");
      lines.push(`- App: ${[app.name, app.version, app.build && `(${app.build})`, app.bundle_id].filter(Boolean).join(" ")}`);
      lines.push(`- Device: ${[device.name, device.model, device.system, device.simulator ? "Simulator" : ""].filter(Boolean).join(", ")}`);
      if (device.locale) lines.push(`- Locale: ${device.locale}`);
      if (bug.screen) lines.push(`- Screen: ${bug.screen}`);
      if (bug.captured_at) lines.push(`- Captured: ${new Date(bug.captured_at * 1000).toLocaleString()}`);
      if (perf.memory_bytes != null) {
        lines.push(`- Memory ${fmtBytes(perf.memory_bytes)}, CPU ${Math.round(perf.cpu_percent || 0)}%, FPS ${Math.round(perf.fps || 0)}, thermal ${perf.thermal_state || "?"}`);
      }
      lines.push("");
      const actions = bug.actions || [];
      if (actions.length) {
        lines.push("## Last interactions", "");
        actions.forEach((action) => {
          const what = [action.kind, action.direction, action.label && `"${action.label}"`, action.target_class && `on ${action.target_class}`]
            .filter(Boolean).join(" ");
          lines.push(`- ${what}${action.screen_class ? ` (${action.screen_class})` : ""}, ${Math.round((action.ago_ms || 0) / 1000)} s before capture`);
        });
        lines.push("");
      }
      const failures = await this.recentFailures();
      if (failures.length) {
        lines.push("## Recent failed requests", "");
        failures.forEach((flow) => {
          lines.push(`- ${flow.method} ${flow.host}${flow.path} → ${flow.error || flow.status}`);
        });
        lines.push("");
      }
      if (imageName) lines.push(`![Screenshot](${imageName})`, "");
      return lines.join("\n");
    },

    async copyBugReport() {
      const markdown = await this.bugMarkdown();
      const canvas = $("bug-canvas");
      try {
        const png = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
        await navigator.clipboard.write([new ClipboardItem({
          "text/plain": new Blob([markdown], { type: "text/plain" }),
          "image/png": png,
        })]);
        state.notify("Report and annotated screenshot copied.");
        return;
      } catch (_) {
        // Some browsers refuse mixed clipboard items; text alone still helps.
      }
      state.notify(await copyText(markdown) ? "Report copied. Download the PNG for the image." : "Could not copy. Allow clipboard access.");
    },

    // ---------- Macro recorder ----------

    bindMacro() {
      $("macro-record")?.addEventListener("click", () => {
        const name = state.macro.recording ? "macro.record_stop" : "macro.record_start";
        state.send(name, {}, { quiet: true });
      });
      $("macro-replay")?.addEventListener("click", () => {
        if (!state.macro.actions.length) return;
        state.macro.results = [];
        state.macro.replaying = true;
        this.renderMacro();
        state.send("macro.replay", { actions: state.macro.actions }).then((sent) => {
          if (!sent) {
            state.macro.replaying = false;
            this.renderMacro();
          }
        });
      });
      $("macro-save")?.addEventListener("click", () => {
        const input = $("macro-name");
        const name = input.value.trim() || `Macro ${new Date().toLocaleString()}`;
        const saved = this.savedMacros().filter((item) => item.name !== name);
        saved.unshift({ name, actions: state.macro.actions, saved_at: Date.now() });
        this.storeMacros(saved.slice(0, 50));
        input.value = "";
        state.notify(`Saved "${name}".`);
      });
      $("macro-export")?.addEventListener("click", () => {
        const body = JSON.stringify({ app: state.client()?.bundle_id || "", actions: state.macro.actions }, null, 2);
        downloadBlob(new Blob([body], { type: "application/json" }), "viewlift-macro.json");
      });
      $("macro-import")?.addEventListener("change", async (event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        try {
          const data = JSON.parse(await file.text());
          const actions = Array.isArray(data) ? data : data.actions;
          if (!Array.isArray(actions)) throw new Error("no actions");
          this.loadMacro(actions);
          state.notify(`Imported ${actions.length} steps.`);
        } catch (_) {
          state.notify("That file is not a macro export.");
        }
      });
      $("macro-saved")?.addEventListener("click", (event) => {
        const load = event.target.closest("[data-macro-load]");
        const remove = event.target.closest("[data-macro-delete]");
        const saved = this.savedMacros();
        if (load) {
          const item = saved[Number(load.dataset.macroLoad)];
          if (item) this.loadMacro(item.actions);
        } else if (remove) {
          saved.splice(Number(remove.dataset.macroDelete), 1);
          this.storeMacros(saved);
        }
      });
      $("macro-steps")?.addEventListener("click", (event) => {
        const remove = event.target.closest("[data-step-delete]");
        if (!remove || state.macro.recording) return;
        state.macro.actions.splice(Number(remove.dataset.stepDelete), 1);
        state.macro.results = [];
        this.renderMacro();
      });
    },

    macroKey() {
      return `vl.macros.${state.client()?.bundle_id || "any"}`;
    },

    savedMacros() {
      try {
        return JSON.parse(localStorage.getItem(this.macroKey()) || "[]");
      } catch (_) {
        return [];
      }
    },

    storeMacros(rows) {
      localStorage.setItem(this.macroKey(), JSON.stringify(rows));
      this.renderSavedMacros();
    },

    renderSavedMacros() {
      const host = $("macro-saved");
      if (!host) return;
      const rows = this.savedMacros();
      host.innerHTML = rows.length
        ? rows.map((item, index) => `<li><button type="button" class="explorer-link" data-macro-load="${index}">${escapeHtml(item.name)}</button>`
          + `<span class="muted">${(item.actions || []).length} steps</span>`
          + `<button type="button" class="btn ghost" data-macro-delete="${index}" aria-label="Delete ${escapeHtml(item.name)}">Delete</button></li>`).join("")
        : `<li class="muted">None yet. Record a macro and press Save.</li>`;
    },

    loadMacro(actions) {
      state.macro.actions = actions.slice(0, 500);
      state.macro.results = [];
      this.renderMacro();
    },

    onMacroState(result) {
      const wasRecording = state.macro.recording;
      state.macro.recording = Boolean(result.recording);
      const actions = result.actions || [];
      if (state.macro.recording || actions.length || wasRecording) state.macro.actions = actions;
      state.macro.results = [];
      clearInterval(state.macroPoll);
      state.macroPoll = state.macro.recording
        ? setInterval(() => state.send("macro.list", {}), 2000)
        : 0;
      this.renderMacro();
    },

    onMacroReplay(result) {
      state.macro.replaying = false;
      state.macro.results = result.steps || [];
      const total = result.total ?? state.macro.actions.length;
      $("macro-result").innerHTML = "";
      this.renderMacro();
      $("macro-status").textContent = `Replayed ${result.replayed || 0} of ${total} steps.`;
    },

    stepText(action) {
      if (action.selector) {
        return `Control action ${action.selector}${action.identifier ? ` on #${action.identifier}` : ""}${action.label ? ` "${action.label}"` : ""}`;
      }
      const target = action.identifier ? `#${action.identifier}` : (action.label ? `"${action.label}"` : "");
      if (action.kind === "press") return `Press ${action.press} on the remote`;
      if (action.kind === "swipe") return `Swipe ${action.direction}${target ? ` on ${target}` : ""}`;
      const role = action.role && action.role !== "element" ? ` ${action.role}` : "";
      return target ? `Tap${role} ${target}` : `Tap at ${Math.round(action.x || 0)}, ${Math.round(action.y || 0)}`;
    },

    renderMacro() {
      const macro = state.macro;
      const pill = $("macro-pill");
      const record = $("macro-record");
      if (!pill || !record) return;
      pill.textContent = macro.recording ? "Recording" : (macro.replaying ? "Replaying" : "Idle");
      pill.classList.toggle("is-live", macro.recording);
      pill.classList.toggle("is-busy", Boolean(macro.replaying));
      record.textContent = macro.recording ? "Stop" : "Start recording";
      const has = macro.actions.length > 0;
      $("macro-replay").disabled = !has || macro.recording || Boolean(macro.replaying);
      $("macro-save").disabled = !has || macro.recording;
      $("macro-export").disabled = !has || macro.recording;
      $("macro-status").textContent = macro.recording
        ? `Recording… ${macro.actions.length} step${macro.actions.length === 1 ? "" : "s"} so far. Use the app, then press Stop.`
        : macro.replaying
          ? "Replaying on the device…"
          : has ? `${macro.actions.length} steps. Return to the starting screen, then press Replay.` : "Nothing recorded yet.";
      const results = new Map((macro.results || []).map((row) => [row.index, row]));
      $("macro-steps").innerHTML = macro.actions.map((action, index) => {
        const outcome = results.get(index);
        const badge = outcome
          ? `<span class="macro-outcome ${outcome.ok ? "ok" : "bad"}">${outcome.ok ? "Done" : escapeHtml(outcome.detail || "Failed")}</span>`
          : "";
        const when = action.offset != null ? `<span class="muted">${Number(action.offset).toFixed(1)} s</span>` : "";
        const screen = action.screen ? `<span class="muted mono">${escapeHtml(action.screen)}</span>` : "";
        const remove = macro.recording ? "" : `<button type="button" class="btn ghost" data-step-delete="${index}" aria-label="Remove step ${index + 1}">Remove</button>`;
        return `<li><span class="macro-text">${escapeHtml(this.stepText(action))}</span>${screen}${when}${badge}${remove}</li>`;
      }).join("");
    },

    // ---------- Local notifications ----------

    bindPush() {
      const form = $("push-form");
      if (!form) return;
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const payload = this.pushPayload(form);
        if (payload) state.send("push.local", payload);
      });
      $("push-action-add").addEventListener("click", () => this.addPushAction());
      $("push-actions").addEventListener("click", (event) => {
        const remove = event.target.closest("[data-action-remove]");
        if (!remove) return;
        remove.closest(".push-action")?.remove();
        $("push-action-add").disabled = false;
      });
      $("push-image-file").addEventListener("change", async (event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) await this.loadPushImage(file);
      });
      $("push-image-clear").addEventListener("click", () => this.clearPushImage());
      form.elements.image_url.addEventListener("input", (event) => {
        if (event.target.value.trim()) this.clearPushImage(false);
        const preview = $("push-image-preview");
        const url = event.target.value.trim();
        preview.hidden = !/^https?:\/\//.test(url);
        if (!preview.hidden) preview.src = url;
      });
      $("push-clear").addEventListener("click", () => state.send("push.clear", {}));
    },

    addPushAction() {
      const host = $("push-actions");
      const count = host.querySelectorAll(".push-action").length;
      if (count >= MAX_PUSH_ACTIONS) return;
      host.insertAdjacentHTML("beforeend", `<div class="push-action">
          <input name="action_title" placeholder="Button title, for example Watch now" aria-label="Action title" autocomplete="off">
          <label class="ui-check"><input type="checkbox" name="action_foreground" checked> Opens app</label>
          <label class="ui-check"><input type="checkbox" name="action_destructive"> Destructive</label>
          <label class="ui-check"><input type="checkbox" name="action_input"> Text reply</label>
          <button type="button" class="btn ghost" data-action-remove>Remove</button>
        </div>`);
      host.lastElementChild.querySelector("input")?.focus();
      $("push-action-add").disabled = count + 1 >= MAX_PUSH_ACTIONS;
    },

    pushPayload(form) {
      const text = (name) => form.elements[name].value.trim();
      const payload = {
        title: text("title"),
        subtitle: text("subtitle"),
        body: text("body"),
        delay: Math.max(1, Number(text("delay")) || 1),
        sound: form.elements.sound.checked,
      };
      if (!payload.title && !payload.body) {
        state.notify("Add a title or a body.");
        return null;
      }
      if (text("badge") !== "") payload.badge = Number(text("badge"));
      if (text("thread_id")) payload.thread_id = text("thread_id");
      if (text("user_info")) {
        try {
          const info = JSON.parse(text("user_info"));
          if (!info || typeof info !== "object" || Array.isArray(info)) throw new Error("not an object");
          payload.user_info = info;
        } catch (_) {
          state.notify("User info must be a JSON object, for example {\"id\": 1}.");
          return null;
        }
      }
      if (state.pushImage) {
        payload.image_base64 = state.pushImage.base64;
        payload.image_type = state.pushImage.type;
      } else if (text("image_url")) {
        payload.image_url = text("image_url");
      }
      const actions = [...$("push-actions").querySelectorAll(".push-action")].map((row, index) => ({
        id: `action-${index + 1}`,
        title: row.querySelector('[name="action_title"]').value.trim(),
        foreground: row.querySelector('[name="action_foreground"]').checked,
        destructive: row.querySelector('[name="action_destructive"]').checked,
        input: row.querySelector('[name="action_input"]').checked,
      })).filter((row) => row.title);
      if (actions.length) payload.actions = actions;
      return payload;
    },

    /** Debug commands are capped at 64 KB, so the image is re-encoded as a small JPEG. */
    async loadPushImage(file) {
      const url = URL.createObjectURL(file);
      try {
        const image = await new Promise((resolve, reject) => {
          const item = new Image();
          item.onload = () => resolve(item);
          item.onerror = reject;
          item.src = url;
        });
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        for (const edge of [900, 720, 560, 420, 320]) {
          const scale = Math.min(1, edge / Math.max(image.naturalWidth, image.naturalHeight));
          canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
          canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
          ctx.fillStyle = "#fff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
          for (const quality of [0.8, 0.65, 0.5]) {
            const data = canvas.toDataURL("image/jpeg", quality);
            const base64 = data.slice(data.indexOf(",") + 1);
            if (base64.length * 0.75 <= MAX_PUSH_IMAGE) {
              state.pushImage = { base64, type: "image/jpeg" };
              const preview = $("push-image-preview");
              preview.src = data;
              preview.hidden = false;
              $("push-image-clear").hidden = false;
              $("push-form").elements.image_url.value = "";
              $("push-image-note").textContent = `${file.name} attached as a ${canvas.width}×${canvas.height} JPEG (${Math.round(base64.length * 0.75 / 1024)} KB).`;
              return;
            }
          }
        }
        state.notify("That image could not be made small enough. Use an image URL instead.");
      } catch (_) {
        state.notify("That file is not an image this browser can read.");
      } finally {
        URL.revokeObjectURL(url);
      }
    },

    clearPushImage(hidePreview = true) {
      state.pushImage = null;
      $("push-image-clear").hidden = true;
      if (hidePreview) $("push-image-preview").hidden = true;
      $("push-image-note").textContent = "The device downloads the URL. A chosen file is shrunk to fit the command size limit.";
    },

    onPushScheduled(result) {
      const status = $("push-status");
      if (result.scheduled === false) {
        status.innerHTML = `<span class="warn-text">${escapeHtml(result.detail || "Not scheduled.")}</span>`;
        return;
      }
      const notes = (result.notes || []).map((note) => ` ${note}`).join("");
      const delay = Number(result.delay || 1);
      status.innerHTML = `<span class="ok-text">Scheduled. It appears in ${delay} s.</span>`
        + (result.badge_only ? " Apple TV shows only the badge." : "")
        + (notes ? `<span class="warn-text">${escapeHtml(notes)}</span>` : "");
      clearTimeout(state.pushStatusTimer);
      state.pushStatusTimer = setTimeout(() => state.send("push.status", {}), (delay + 2) * 1000);
    },

    renderPushStatus(result) {
      const auth = result.authorization || "unknown";
      const label = {
        authorized: "Allowed", denied: "Denied in Settings", provisional: "Provisional (quiet)",
        not_determined: "Not asked yet", ephemeral: "App Clip",
      }[auth] || auth;
      const yesNo = (value) => (value == null ? "—" : (value ? "On" : "Off"));
      $("push-permission").innerHTML = [
        metric("Permission", label, auth === "denied" ? "metric-bad" : ""),
        metric("Banners", yesNo(result.alert)),
        metric("Sound", yesNo(result.sound)),
        metric("Badge", yesNo(result.badge)),
        metric("Waiting to fire", (result.pending || []).length),
        result.delivered != null ? metric("In Notification Center", result.delivered) : "",
      ].join("");
      const responses = result.responses || [];
      const note = result.responses_tracked === false
        ? `<p class="muted">The app has its own notification delegate, so taps go to the app and are not listed here.</p>`
        : "";
      $("push-result").innerHTML = note + (responses.length
        ? `<table class="debug-table"><thead><tr><th>Time</th><th>Tapped</th><th>Notification</th><th>Reply</th></tr></thead><tbody>${
          responses.map((row) => `<tr><td>${escapeHtml(new Date(row.time * 1000).toLocaleTimeString())}</td>`
            + `<td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.title)}</td><td>${escapeHtml(row.text || "")}</td></tr>`).join("")
        }</tbody></table>`
        : `<p class="muted">No taps yet. Open the notification or press one of its buttons.</p>`);
    },

    // ---------- Network and device tools ----------

    bindNetworkTools() {
      $("profile-target")?.addEventListener("change", (event) => {
        event.target.dataset.touched = "1";
        this.renderProfiles();
      });
      $("profile-buttons")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-profile]");
        if (button) this.applyProfile(button.dataset.profile);
      });
      $("loc-preset")?.addEventListener("change", (event) => {
        const [lat, lon] = event.target.value.split(",");
        if (!lat) return;
        $("loc-lat").value = lat;
        $("loc-lon").value = lon;
      });
      $("location-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        this.sendLocation(false);
      });
      $("loc-clear")?.addEventListener("click", () => this.sendLocation(true));
      $("deeplink-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        this.openLink();
      });
      $("waterfall-refresh")?.addEventListener("click", () => this.loadWaterfall());
      $("waterfall")?.addEventListener("click", (event) => {
        const row = event.target.closest("[data-flow]");
        if (row) window.location.href = `/flows/${encodeURIComponent(row.dataset.flow)}`;
      });
      this.loadProfiles();
    },

    async loadProfiles() {
      try {
        state.profiles = (await (await fetch("/api/debug/network-profiles")).json()).profiles || [];
      } catch (_) {
        state.profiles = [];
      }
      this.renderProfiles();
    },

    profileTarget() {
      return $("profile-target")?.value === "proxy" || !state.client() ? "proxy" : "app";
    },

    renderProfiles() {
      const host = $("profile-buttons");
      if (!host) return;
      const target = this.profileTarget();
      const describe = (row) => {
        if (target === "app") return APP_PROFILES[row.id] || "";
        if (row.offline) return "Every request fails";
        const speed = row.download_kbps ? `${row.download_kbps >= 1000 ? `${row.download_kbps / 1000} Mbps` : `${row.download_kbps} kbps`}` : "full speed";
        return `${row.latency_ms} ms · ${speed}`;
      };
      const active = state.activeProfile[target];
      const rows = [...state.profiles, { id: "off" }];
      host.innerHTML = rows.map((row) => `<button type="button" class="profile-card${row.id === active ? " active" : ""}" data-profile="${escapeHtml(row.id)}" aria-pressed="${row.id === active}">`
        + `<strong>${escapeHtml(row.id === "off" ? "Normal" : row.id.toUpperCase())}</strong>`
        + `<span class="muted">${escapeHtml(row.id === "off" ? "No shaping" : describe(row))}</span></button>`).join("");
      const option = $("profile-target")?.querySelector('option[value="app"]');
      if (option) option.disabled = !state.client();
    },

    async applyProfile(profile) {
      const status = $("profile-status");
      if (this.profileTarget() === "app") {
        status.textContent = "Sending to the app…";
        await state.send("net.profile", { profile });
        return;
      }
      const response = await fetch("/api/debug/network-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        status.innerHTML = `<span class="warn-text">${escapeHtml(result.detail || "Could not apply.")}</span>`;
        return;
      }
      state.activeProfile.proxy = result.profile;
      this.renderProfiles();
      status.textContent = result.profile === "off"
        ? "Proxy shaping removed."
        : `${result.profile} applied to every app that uses this Mac's proxy. Apps that only use the SDK are not affected; pick Selected app for those.`;
    },

    onAppProfile(result) {
      const status = $("profile-status");
      state.activeProfile.app = result.profile || "off";
      this.renderProfiles();
      const app = state.client()?.app_name || "the app";
      if (result.profile === "off") {
        status.textContent = `Normal network restored in ${app}.`;
      } else {
        const parts = result.offline ? ["every request fails"] : [`${result.latency_ms} ms added`, result.loss_percent ? `${result.loss_percent}% loss` : ""];
        status.textContent = `${result.profile} applied in ${app}: ${parts.filter(Boolean).join(", ")}.`;
      }
      if (result.capturing === false) {
        status.insertAdjacentHTML("beforeend", ` <span class="warn-text">Network capture is off in the app, so nothing is shaped. Turn on Capture requests in Device settings.</span>`);
      }
    },

    async sendLocation(clear) {
      const status = $("location-status");
      const choice = window.ViewliftNetwork?.targetChoice($("loc-udid")) || { udid: "", kind: "" };
      const body = { udid: choice.udid, kind: choice.kind, clear, client_id: state.client()?.id || null };
      if (!clear) {
        const lat = Number($("loc-lat").value);
        const lon = Number($("loc-lon").value);
        if ($("loc-lat").value === "" || $("loc-lon").value === "" || Number.isNaN(lat) || Number.isNaN(lon)) {
          status.innerHTML = `<span class="warn-text">Enter a latitude and longitude, or pick a preset.</span>`;
          return;
        }
        body.latitude = lat;
        body.longitude = lon;
      }
      if (!body.udid && !body.client_id) {
        status.innerHTML = `<span class="warn-text">Select an app at the top, or pick a target.</span>`;
        return;
      }
      status.textContent = clear ? "Clearing…" : "Setting location…";
      const response = await fetch("/api/debug/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      status.innerHTML = response.ok
        ? `<span class="ok-text">${escapeHtml(data.detail || "Location updated.")}</span>`
        : `<span class="warn-text">${escapeHtml(data.detail || "Location failed.")}</span>`;
    },

    recentLinks() {
      try {
        return JSON.parse(localStorage.getItem("vl.deeplinks") || "[]");
      } catch (_) {
        return [];
      }
    },

    async openLink() {
      const status = $("deeplink-status");
      const url = $("deeplink-url").value.trim();
      if (!/^[a-z][a-z0-9+.-]*:\/\/\S+$/i.test(url)) {
        status.innerHTML = `<span class="warn-text">Enter a full URL such as ${escapeHtml(state.client()?.url_schemes?.[0] || "myapp")}://path.</span>`;
        return;
      }
      localStorage.setItem("vl.deeplinks", JSON.stringify([url, ...this.recentLinks().filter((item) => item !== url)].slice(0, 10)));
      this.onClient(state.client());
      if ($("deeplink-via").value === "app") {
        if (!state.client()) {
          status.innerHTML = `<span class="warn-text">Select an app at the top first.</span>`;
          return;
        }
        status.textContent = "Asking the app to open it…";
        if (await state.send("nav.open_url", { url })) status.textContent = "";
        return;
      }
      const choice = window.ViewliftNetwork?.targetChoice($("deeplink-udid")) || { udid: "", kind: "" };
      if (!choice.udid && !state.client()) {
        status.innerHTML = `<span class="warn-text">Select an app at the top, or pick a target.</span>`;
        return;
      }
      status.textContent = "Opening…";
      const response = await fetch("/api/debug/deeplink", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ udid: choice.udid, kind: choice.kind, url, client_id: state.client()?.id || null }),
      });
      const data = await response.json().catch(() => ({}));
      status.innerHTML = response.ok
        ? `<span class="ok-text">${escapeHtml(data.detail || "Opened.")}</span>`
        : `<span class="warn-text">${escapeHtml(data.detail || "Could not open the link.")}</span>`;
    },

    async loadWaterfall() {
      const host = $("waterfall");
      if (!host) return;
      const params = new URLSearchParams({ limit: "80" });
      if (window.VLScope) window.VLScope.applyTo(params);
      let flows = [];
      try {
        flows = (await (await fetch(`/api/flows?${params}`)).json()).flows || [];
      } catch (_) {
        host.innerHTML = `<p class="warn-text">Could not load requests.</p>`;
        return;
      }
      if (!flows.length) {
        host.innerHTML = `<p class="muted">No requests in this scope yet.</p>`;
        return;
      }
      flows.reverse();
      const start = Math.min(...flows.map((flow) => Date.parse(flow.created_at)));
      const finish = Math.max(...flows.map((flow) => Date.parse(flow.created_at) + (flow.duration_ms || 1)));
      const span = Math.max(1, finish - start);
      host.innerHTML = flows.map((flow) => {
        const left = (Date.parse(flow.created_at) - start) / span * 100;
        const width = Math.max(0.4, (flow.duration_ms || 1) / span * 100);
        const failed = flow.error || Number(flow.status) >= 400;
        const label = `${flow.method} ${flow.host}${flow.path}`;
        return `<div class="fall-row" role="link" tabindex="0" data-flow="${escapeHtml(flow.id)}" title="${escapeHtml(label)}">`
          + `<span class="fall-label"><span class="fall-status${failed ? " bad" : ""}">${escapeHtml(flow.error ? "ERR" : flow.status ?? "…")}</span> ${escapeHtml(label)}</span>`
          + `<span class="fall-track"><span class="fall-bar${failed ? " bad" : ""}" style="left:${left}%;width:${Math.min(width, 100 - left)}%"></span></span>`
          + `<span class="fall-ms muted">${Math.round(flow.duration_ms || 0)} ms</span></div>`;
      }).join("");
    },
  };

  window.ViewliftQA = QA;
})();
