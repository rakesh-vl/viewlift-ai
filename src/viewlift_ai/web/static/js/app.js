/* Local-only dashboard. No external network calls. */
(() => {
  const ViewliftNetwork = {
    selectedId: null,
    selectedPendingId: null,
    flows: [],
    pending: [],
    ws: null,
    revealUnlocked: false,

    initDashboard() {
      const apply = () => this.reloadFlows();
      document.getElementById("btn-apply").addEventListener("click", apply);
      document.getElementById("btn-reveal-unlock")?.addEventListener("click", () => this.unlockReveal());
      document.getElementById("btn-reveal-lock")?.addEventListener("click", () => this.lockReveal());
      this.refreshRevealStatus();
      document.getElementById("btn-clear").addEventListener("click", () => {
        ["f-method", "f-host", "f-status", "f-ctype", "f-text"].forEach((id) => {
          document.getElementById(id).value = "";
        });
        apply();
      });
      document.getElementById("btn-har").addEventListener("click", () => {
        window.location.href = `/api/export/har?${this.filterQuery()}`;
      });
      document.getElementById("btn-curl").addEventListener("click", () => {
        if (this.selectedId) this.copyCurl(this.selectedId);
      });
      document.getElementById("btn-har-one").addEventListener("click", () => {
        if (this.selectedId) {
          window.location.href = `/api/flows/${encodeURIComponent(this.selectedId)}/har`;
        }
      });
      ["f-method", "f-host", "f-status", "f-ctype", "f-text"].forEach((id) => {
        document.getElementById(id).addEventListener("keydown", (e) => {
          if (e.key === "Enter") apply();
        });
      });
      this.reloadFlows();
      this.reloadPending();
      this.pollCaptureStatus();
      this.connectWs();
    },

    filterQuery() {
      const params = new URLSearchParams();
      const method = document.getElementById("f-method").value.trim();
      const host = document.getElementById("f-host").value.trim();
      const status = document.getElementById("f-status").value.trim();
      const ctype = document.getElementById("f-ctype").value.trim();
      const text = document.getElementById("f-text").value.trim();
      if (method) params.set("method", method);
      if (host) params.set("host", host);
      if (status) params.set("status", status);
      if (ctype) params.set("content-type", ctype);
      if (text) params.set("text", text);
      return params.toString();
    },

    async reloadFlows() {
      const q = this.filterQuery();
      const res = await fetch(`/api/flows${q ? `?${q}` : ""}`);
      const data = await res.json();
      this.flows = data.flows || [];
      this.renderRows();
      if (this.selectedId) {
        const still = this.flows.some((f) => f.id === this.selectedId);
        if (still) this.selectFlow(this.selectedId, false);
      }
    },

    async reloadPending() {
      const res = await fetch("/api/breakpoints/pending");
      if (!res.ok) return;
      const data = await res.json();
      this.pending = data.pending || [];
      this.renderRows();
      if (this.selectedPendingId) {
        const still = this.pending.some((p) => p.id === this.selectedPendingId);
        if (still) this.selectPending(this.selectedPendingId);
        else {
          this.selectedPendingId = null;
          document.getElementById("detail-body").innerHTML =
            `<p class="muted empty">Select a flow</p>`;
        }
      }
    },

    renderRows() {
      const tbody = document.getElementById("flow-rows");
      const total = this.pending.length + this.flows.length;
      document.getElementById("flow-count").textContent = String(total);
      tbody.innerHTML = "";
      for (const item of this.pending) {
        const tr = document.createElement("tr");
        tr.dataset.pendingId = item.id;
        tr.classList.add("paused");
        if (item.id === this.selectedPendingId) tr.classList.add("active");
        tr.innerHTML = `
          <td class="col-method method ${escapeAttr(item.method)}">${escapeHtml(item.method)}</td>
          <td>${escapeHtml(item.host)}</td>
          <td title="${escapeAttr(item.path)}">${escapeHtml(item.path)}</td>
          <td class="col-status">${item.status == null ? "PAUSED" : escapeHtml(String(item.status))}</td>
          <td class="col-type">paused</td>
          <td class="col-time">—</td>
          <td class="col-size">${fmtBytes(item.body_bytes)}</td>
          <td class="col-stage"><span class="stage-badge ${escapeAttr(item.stage)}">${escapeHtml(item.stage)}</span></td>
        `;
        tr.addEventListener("click", () => this.selectPending(item.id));
        tbody.appendChild(tr);
      }
      for (const flow of this.flows) {
        const tr = document.createElement("tr");
        tr.dataset.id = flow.id;
        if (flow.id === this.selectedId) tr.classList.add("active");
        if (flow.error === "breakpoint timed out") tr.classList.add("bp-timeout");
        const statusClass = flow.status == null ? "" : `status-${String(flow.status)[0]}`;
        tr.innerHTML = `
          <td class="col-method method ${escapeAttr(flow.method)}">${escapeHtml(flow.method)}</td>
          <td>${escapeHtml(flow.host)}</td>
          <td title="${escapeAttr(flow.path)}${flow.query ? "?" + escapeAttr(flow.query) : ""}">${escapeHtml(flow.path)}${flow.query ? '<span class="muted">?…</span>' : ""}</td>
          <td class="col-status ${statusClass}">${flow.status == null ? "—" : escapeHtml(String(flow.status))}</td>
          <td class="col-type">${escapeHtml(shortType(flow.content_type))}</td>
          <td class="col-time">${fmtMs(flow.duration_ms)}</td>
          <td class="col-size">${fmtBytes(flow.size_bytes)}</td>
          <td class="col-stage muted">${flow.error === "breakpoint timed out" ? "timeout" : "—"}</td>
        `;
        tr.addEventListener("click", () => this.selectFlow(flow.id, true));
        tbody.appendChild(tr);
      }
    },

    selectPending(id) {
      this.selectedPendingId = id;
      this.selectedId = null;
      this.renderRows();
      document.getElementById("btn-curl").disabled = true;
      document.getElementById("btn-har-one").disabled = true;
      const open = document.getElementById("btn-open");
      open.href = "#";
      open.classList.add("disabled");
      const item = this.pending.find((p) => p.id === id);
      if (!item) return;
      document.getElementById("detail-title").textContent =
        `Paused ${item.stage}: ${item.method} ${item.host}${item.path}`;
      this.renderPendingDetail(item, document.getElementById("detail-body"));
    },

    renderPendingDetail(item, root) {
      const headersText = Object.entries(item.headers || {})
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n");
      const statusBlock =
        item.stage === "response"
          ? `<label>Status <input id="bp-edit-status" type="number" value="${item.status == null ? "" : escapeAttr(String(item.status))}"></label>`
          : `<p class="muted">Request stage: status edit disabled</p>`;
      root.innerHTML = `
        <div class="bp-editor">
          <p><span class="stage-badge ${escapeAttr(item.stage)}">${escapeHtml(item.stage)}</span>
             pause id <code>${escapeHtml(item.id)}</code></p>
          ${statusBlock}
          <label>Headers (Name: value per line)
            <textarea id="bp-edit-headers" class="rules-textarea" rows="8">${escapeHtml(headersText)}</textarea>
          </label>
          <label>Body
            <textarea id="bp-edit-body" class="rules-textarea mono" rows="10">${escapeHtml(item.body || "")}</textarea>
          </label>
          <div class="row-actions">
            <button type="button" class="btn" id="bp-resume">Resume</button>
            <button type="button" class="btn ghost" id="bp-drop">Drop</button>
          </div>
        </div>
      `;
      document.getElementById("bp-resume").addEventListener("click", () => this.resumePending(item.id));
      document.getElementById("bp-drop").addEventListener("click", () => this.dropPending(item.id));
    },

    parseHeadersText(text) {
      const headers = {};
      for (const line of String(text || "").split("\n")) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const idx = trimmed.indexOf(":");
        if (idx < 0) continue;
        headers[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
      }
      return headers;
    },

    async resumePending(id) {
      const body = {
        headers: this.parseHeadersText(document.getElementById("bp-edit-headers").value),
        body: document.getElementById("bp-edit-body").value,
      };
      const statusEl = document.getElementById("bp-edit-status");
      if (statusEl && statusEl.value !== "") body.status = Number(statusEl.value);
      const res = await fetch(`/api/breakpoints/${encodeURIComponent(id)}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Resume failed");
        return;
      }
      toast("Resumed");
      this.selectedPendingId = null;
      await this.reloadPending();
      await this.reloadFlows();
    },

    async dropPending(id) {
      const res = await fetch(`/api/breakpoints/${encodeURIComponent(id)}/drop`, {
        method: "POST",
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Drop failed");
        return;
      }
      toast("Dropped");
      this.selectedPendingId = null;
      await this.reloadPending();
    },

    async refreshRevealStatus() {
      try {
        const res = await fetch("/api/reveal/status");
        if (!res.ok) return;
        const data = await res.json();
        this.revealUnlocked = !!data.unlocked;
        const el = document.getElementById("reveal-state");
        if (el) {
          el.textContent = this.revealUnlocked
            ? "unlocked · raw headers/bodies in UI only"
            : "locked · HAR/cURL stay redacted";
        }
      } catch {
        /* ignore */
      }
    },

    async unlockReveal() {
      const token = document.getElementById("reveal-token")?.value.trim() || "";
      const res = await fetch("/api/reveal/unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Unlock failed");
        return;
      }
      toast("Reveal unlocked (session memory)");
      await this.refreshRevealStatus();
      if (this.selectedId) this.selectFlow(this.selectedId, true);
    },

    async lockReveal() {
      await fetch("/api/reveal/lock", { method: "POST" });
      await this.refreshRevealStatus();
      if (this.selectedId) this.selectFlow(this.selectedId, true);
      toast("Reveal locked");
    },

    async selectFlow(id, fetchDetail) {
      this.selectedId = id;
      this.selectedPendingId = null;
      this.renderRows();
      document.getElementById("btn-curl").disabled = false;
      document.getElementById("btn-har-one").disabled = false;
      const open = document.getElementById("btn-open");
      open.href = `/flows/${encodeURIComponent(id)}`;
      open.classList.remove("disabled");
      if (!fetchDetail) return;
      const q = this.revealUnlocked ? "?reveal=true" : "";
      const res = await fetch(`/api/flows/${encodeURIComponent(id)}${q}`);
      if (!res.ok) {
        if (this.revealUnlocked && res.status === 404) {
          const fallback = await fetch(`/api/flows/${encodeURIComponent(id)}`);
          if (fallback.ok) {
            const detail = await fallback.json();
            document.getElementById("detail-title").textContent =
              `${detail.method} ${detail.host}${detail.path}`;
            this.renderDetail(detail, document.getElementById("detail-body"));
            return;
          }
        }
        document.getElementById("detail-body").innerHTML = `<p class="muted">Flow not found</p>`;
        return;
      }
      const detail = await res.json();
      document.getElementById("detail-title").textContent =
        `${detail.method} ${detail.host}${detail.path}${detail.revealed ? " (revealed)" : ""}`;
      this.renderDetail(detail, document.getElementById("detail-body"));
    },

    renderDetail(detail, root) {
      const tabs = [
        ["overview", "Overview"],
        ["request", "Request"],
        ["response", "Response"],
        ["query", "Query"],
        ["headers", "Headers"],
        ["cookies", "Cookies"],
        ["body", "Body"],
        ["timing", "Timing"],
        ["ws", "WebSocket"],
      ];
      root.innerHTML = `
        <div class="tabs">
          ${tabs.map(([id, label], i) =>
            `<button type="button" class="tab${i === 0 ? " active" : ""}" data-tab="${id}">${label}</button>`
          ).join("")}
        </div>
        <div class="tab-panel active" data-panel="overview">${renderOverview(detail)}</div>
        <div class="tab-panel" data-panel="request">${renderSide(detail.request)}</div>
        <div class="tab-panel" data-panel="response">${renderSide(detail.response)}</div>
        <div class="tab-panel" data-panel="query">${renderPairs(detail.query)}</div>
        <div class="tab-panel" data-panel="headers">${renderHeaders(detail)}</div>
        <div class="tab-panel" data-panel="cookies">${renderCookies(detail)}</div>
        <div class="tab-panel" data-panel="body">${renderBodies(detail)}</div>
        <div class="tab-panel" data-panel="timing">${renderTiming(detail.timing, detail.duration_ms)}</div>
        <div class="tab-panel" data-panel="ws">${renderWs(detail.websocket_messages || [])}</div>
      `;
      root.querySelectorAll(".tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          root.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
          root.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
          btn.classList.add("active");
          root.querySelector(`[data-panel="${btn.dataset.tab}"]`).classList.add("active");
        });
      });
    },

    async copyCurl(flowId) {
      const res = await fetch(`/api/flows/${encodeURIComponent(flowId)}/curl`);
      const text = await res.text();
      try {
        await navigator.clipboard.writeText(text);
        toast("cURL copied (redacted)");
      } catch {
        toast(text.slice(0, 120) + "…");
      }
    },

    connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws/flows`;
      const dot = document.getElementById("live-dot");
      const label = document.getElementById("live-label");
      let ws;
      try {
        ws = new WebSocket(url);
      } catch {
        if (dot) { dot.classList.add("off"); }
        if (label) label.textContent = "offline";
        return;
      }
      this.ws = ws;
      ws.onopen = () => {
        if (dot) { dot.classList.remove("off"); dot.classList.add("on"); }
        if (label) label.textContent = "live";
      };
      ws.onclose = () => {
        if (dot) { dot.classList.remove("on"); dot.classList.add("off"); }
        if (label) label.textContent = "reconnecting";
        setTimeout(() => this.connectWs(), 1500);
      };
      ws.onerror = () => {
        if (dot) { dot.classList.add("off"); }
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (!msg) return;
        if (msg.type === "flow" && msg.flow) {
          this.upsertFlow(msg.flow);
          return;
        }
        if (msg.type === "breakpoint") {
          this.onBreakpointEvent(msg);
        }
      };
    },

    onBreakpointEvent(msg) {
      const kind = msg.kind;
      const pending = msg.pending;
      if (kind === "paused" && pending) {
        const idx = this.pending.findIndex((p) => p.id === pending.id);
        if (idx >= 0) this.pending[idx] = pending;
        else this.pending.unshift(pending);
        this.renderRows();
        return;
      }
      if (
        (kind === "resumed" || kind === "dropped" || kind === "timed_out" || kind === "cleared") &&
        pending
      ) {
        this.pending = this.pending.filter((p) => p.id !== pending.id);
        if (this.selectedPendingId === pending.id) {
          this.selectedPendingId = null;
          document.getElementById("detail-body").innerHTML =
            `<p class="muted empty">Select a flow</p>`;
        }
        this.renderRows();
        if (kind === "timed_out") this.reloadFlows();
      }
    },

    upsertFlow(flow) {
      if (!this.matchesFilters(flow)) {
        this.flows = this.flows.filter((f) => f.id !== flow.id);
        this.renderRows();
        return;
      }
      const idx = this.flows.findIndex((f) => f.id === flow.id);
      if (idx >= 0) this.flows[idx] = flow;
      else this.flows.unshift(flow);
      this.renderRows();
    },

    matchesFilters(flow) {
      const method = document.getElementById("f-method")?.value.trim().toUpperCase();
      const host = document.getElementById("f-host")?.value.trim().toLowerCase();
      const status = document.getElementById("f-status")?.value.trim();
      const ctype = document.getElementById("f-ctype")?.value.trim().toLowerCase();
      const text = document.getElementById("f-text")?.value.trim().toLowerCase();
      if (method && String(flow.method || "").toUpperCase() !== method) return false;
      if (host && !String(flow.host || "").toLowerCase().includes(host)) return false;
      if (status && String(flow.status) !== status) return false;
      if (ctype && !String(flow.content_type || "").toLowerCase().includes(ctype)) return false;
      if (text) {
        const hay = [
          flow.method, flow.host, flow.path, flow.query, flow.content_type, flow.error, flow.protocol,
        ].join(" ").toLowerCase();
        if (!hay.includes(text)) return false;
      }
      return true;
    },
  };

  function renderOverview(d) {
    const o = d.overview || {};
    return kvTable([
      ["URL", o.url],
      ["Method", o.method],
      ["Status", o.status],
      ["Protocol", o.protocol],
      ["Source", o.source],
      ["Duration", fmtMs(o.duration_ms)],
      ["Size", fmtBytes(o.size_bytes)],
      ["Content-Type", d.content_type],
      ["Request ID", o.request_id],
      ["TLS failed", o.tls_failed ? "yes" : "no"],
      ["Error", o.error],
    ]);
  }

  function renderSide(side) {
    if (!side) return `<p class="muted">—</p>`;
    return `
      ${kvTable(Object.entries(side.headers || {}).map(([k, v]) => [k, v]))}
      <h4 class="muted" style="margin:12px 0 6px">Body</h4>
      ${renderBody(side.body)}
    `;
  }

  function renderHeaders(d) {
    return `
      <h4 class="muted">Request</h4>
      ${kvTable(Object.entries((d.request && d.request.headers) || {}))}
      <h4 class="muted" style="margin-top:12px">Response</h4>
      ${kvTable(Object.entries((d.response && d.response.headers) || {}))}
    `;
  }

  function renderCookies(d) {
    return `
      <h4 class="muted">Request cookies</h4>
      ${renderPairs((d.request && d.request.cookies) || [])}
      <h4 class="muted" style="margin-top:12px">Set-Cookie</h4>
      ${renderPairs((d.response && d.response.cookies) || [])}
    `;
  }

  function renderBodies(d) {
    return `
      <h4 class="muted">Request</h4>
      ${renderBody(d.request && d.request.body)}
      <h4 class="muted" style="margin-top:12px">Response</h4>
      ${renderBody(d.response && d.response.body)}
    `;
  }

  function renderBody(body) {
    if (!body || body.kind === "empty") return `<p class="muted">empty</p>`;
    if (body.kind === "image" && body.image) {
      const meta = body.image;
      const img = meta.data_url
        ? `<img class="img-preview" alt="preview" src="${meta.data_url}">`
        : `<p class="muted">image too large to preview (${fmtBytes(meta.size_bytes)})</p>`;
      return `
        ${kvTable([
          ["Type", meta.content_type],
          ["Size", fmtBytes(meta.size_bytes)],
          ["Width", meta.width],
          ["Height", meta.height],
        ])}
        ${img}
      `;
    }
    if (body.kind === "json" && body.pretty) {
      return `<pre class="pre">${escapeHtml(body.pretty)}</pre>`;
    }
    if (body.raw != null) {
      return `<pre class="pre">${escapeHtml(body.raw)}</pre>`;
    }
    return `<pre class="pre">${escapeHtml(body.hex_preview || "")}</pre>`;
  }

  function renderTiming(t, total) {
    t = t || {};
    return kvTable([
      ["Connect", fmtMs(t.connect_ms)],
      ["TLS", fmtMs(t.tls_ms)],
      ["TTFB", fmtMs(t.ttfb_ms)],
      ["Download", fmtMs(t.download_ms)],
      ["Total", fmtMs(total)],
    ]);
  }

  function renderWs(messages) {
    if (!messages.length) return `<p class="muted">No WebSocket messages</p>`;
    return messages.map((m, i) => `
      <div style="margin-bottom:8px">
        <div class="muted">#${i + 1} ${m.from_client ? "client→server" : "server→client"} · ${m.is_text ? "text" : "binary"} · ${fmtBytes(m.size_bytes)}</div>
        <pre class="pre">${escapeHtml(m.content != null ? m.content : (m.hex_preview || ""))}</pre>
      </div>
    `).join("");
  }

  function renderPairs(pairs) {
    if (!pairs || !pairs.length) return `<p class="muted">—</p>`;
    return kvTable(pairs.map((p) => [p.name, p.value]));
  }

  function kvTable(rows) {
    const body = rows.map(([k, v]) =>
      `<tr><th>${escapeHtml(String(k))}</th><td>${escapeHtml(v == null || v === "" ? "—" : String(v))}</td></tr>`
    ).join("");
    return `<table class="kv">${body}</table>`;
  }

  function fmtMs(v) {
    if (v == null || Number.isNaN(v)) return "—";
    return `${Number(v).toFixed(1)} ms`;
  }

  function fmtBytes(v) {
    if (v == null) return "—";
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    return `${(v / (1024 * 1024)).toFixed(1)} MB`;
  }

  function shortType(v) {
    if (!v) return "—";
    return String(v).split(";")[0].trim();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function toast(msg) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), 1600);
  }

  ViewliftNetwork.setMutatingBanner = function setMutatingBanner(on) {
    const banner = document.getElementById("mutating-banner");
    const main = document.querySelector("main.main");
    if (!banner) return;
    banner.classList.toggle("show", !!on);
    if (main) main.classList.toggle("with-banner", !!on);
  };

  ViewliftNetwork.initRules = function initRules() {
    const reload = () => this.reloadRules();
    document.getElementById("btn-save-allowlist").addEventListener("click", async () => {
      const text = document.getElementById("allowlist-text").value;
      const patterns = text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
      const res = await fetch("/api/rules/allowlist", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patterns }),
      });
      if (!res.ok) {
        toast("Allowlist save failed");
        return;
      }
      toast("Allowlist saved");
      reload();
    });
    document.getElementById("btn-save-root").addEventListener("click", async () => {
      const root = document.getElementById("map-root").value.trim();
      const res = await fetch("/api/rules/map-local/root", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ map_local_root: root }),
      });
      if (!res.ok) {
        toast("Root save failed");
        return;
      }
      toast("Map local root saved");
      reload();
    });
    document.getElementById("btn-add-ml").addEventListener("click", async () => {
      const body = {
        enabled: false,
        match: {
          method: document.getElementById("ml-method").value.trim() || "*",
          host: document.getElementById("ml-host").value.trim() || "*",
          path: document.getElementById("ml-path").value.trim() || "*",
        },
        relative_path: document.getElementById("ml-rel").value.trim(),
      };
      const res = await fetch("/api/rules/map-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Add failed");
        return;
      }
      document.getElementById("ml-rel").value = "";
      reload();
    });
    document.getElementById("btn-add-mr").addEventListener("click", async () => {
      const body = {
        enabled: false,
        match: {
          method: document.getElementById("mr-method").value.trim() || "*",
          host: document.getElementById("mr-host").value.trim() || "*",
          path: document.getElementById("mr-path").value.trim() || "*",
        },
        target_url: document.getElementById("mr-target").value.trim(),
      };
      const res = await fetch("/api/rules/map-remote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Add failed");
        return;
      }
      document.getElementById("mr-target").value = "";
      reload();
    });
    document.getElementById("btn-add-rw").addEventListener("click", async () => {
      const kind = document.getElementById("rw-kind").value;
      const a = document.getElementById("rw-a").value;
      const b = document.getElementById("rw-b").value;
      const action = { kind };
      if (kind === "set_header") {
        action.header_name = a;
        action.header_value = b;
      } else if (kind === "remove_header") {
        action.header_name = a;
      } else if (kind === "set_status") {
        action.status = Number(a);
      } else {
        action.find = a;
        action.replace = b;
      }
      const body = {
        enabled: false,
        match: {
          method: document.getElementById("rw-method").value.trim() || "*",
          host: document.getElementById("rw-host").value.trim() || "*",
          path: document.getElementById("rw-path").value.trim() || "*",
        },
        actions: [action],
      };
      const res = await fetch("/api/rules/rewrite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Add failed");
        return;
      }
      reload();
    });
    document.getElementById("btn-add-th").addEventListener("click", async () => {
      const up = document.getElementById("th-up").value;
      const down = document.getElementById("th-down").value;
      const body = {
        enabled: false,
        host: document.getElementById("th-host").value.trim() || "*",
        latency_ms: Number(document.getElementById("th-latency").value || 0),
        upload_kbps: up === "" ? null : Number(up),
        download_kbps: down === "" ? null : Number(down),
        offline: document.getElementById("th-offline").checked,
      };
      const res = await fetch("/api/rules/throttle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Add failed");
        return;
      }
      reload();
    });
    document.getElementById("btn-add-bp").addEventListener("click", async () => {
      const body = {
        enabled: false,
        match: {
          method: document.getElementById("bp-method").value.trim() || "*",
          host: document.getElementById("bp-host").value.trim() || "*",
          path: document.getElementById("bp-path").value.trim() || "*",
        },
        request_enabled: document.getElementById("bp-req").checked,
        response_enabled: document.getElementById("bp-res").checked,
      };
      const res = await fetch("/api/rules/breakpoints", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        toast((await res.json()).detail || "Add failed");
        return;
      }
      reload();
    });
    this.reloadRules();
  };

  ViewliftNetwork.reloadRules = async function reloadRules() {
    const res = await fetch("/api/rules");
    if (!res.ok) return;
    const data = await res.json();
    this.setMutatingBanner(data.mutating_enabled);
    document.getElementById("allowlist-text").value = (data.allowlist || []).join("\n");
    document.getElementById("map-root").value = data.map_local_root || "";

    const matchLabel = (m) => `${m.method} ${m.host}${m.path}`;

    const mlBody = document.getElementById("ml-rows");
    mlBody.innerHTML = (data.map_local || [])
      .map(
        (r) => `<tr>
        <td><input type="checkbox" data-toggle="map-local" data-id="${escapeAttr(r.id)}" ${r.enabled ? "checked" : ""}></td>
        <td>${r.order}</td>
        <td>${escapeHtml(matchLabel(r.match))}</td>
        <td>${escapeHtml(r.relative_path)}</td>
        <td><button type="button" class="btn ghost" data-del="map-local" data-id="${escapeAttr(r.id)}">Del</button></td>
      </tr>`
      )
      .join("");

    const mrBody = document.getElementById("mr-rows");
    mrBody.innerHTML = (data.map_remote || [])
      .map(
        (r) => `<tr>
        <td><input type="checkbox" data-toggle="map-remote" data-id="${escapeAttr(r.id)}" ${r.enabled ? "checked" : ""}></td>
        <td>${r.order}</td>
        <td>${escapeHtml(matchLabel(r.match))}</td>
        <td>${escapeHtml(r.target_url)}</td>
        <td><button type="button" class="btn ghost" data-del="map-remote" data-id="${escapeAttr(r.id)}">Del</button></td>
      </tr>`
      )
      .join("");

    const rwBody = document.getElementById("rw-rows");
    rwBody.innerHTML = (data.rewrite || [])
      .map((r) => {
        const acts = (r.actions || []).map((a) => a.kind).join(", ");
        return `<tr>
        <td><input type="checkbox" data-toggle="rewrite" data-id="${escapeAttr(r.id)}" ${r.enabled ? "checked" : ""}></td>
        <td>${r.order}</td>
        <td>${escapeHtml(matchLabel(r.match))}</td>
        <td>${escapeHtml(acts)}</td>
        <td><button type="button" class="btn ghost" data-del="rewrite" data-id="${escapeAttr(r.id)}">Del</button></td>
      </tr>`;
      })
      .join("");

    const thBody = document.getElementById("th-rows");
    thBody.innerHTML = (data.throttle || [])
      .map((r) => {
        const limits = [
          r.latency_ms ? `${r.latency_ms}ms` : null,
          r.upload_kbps != null ? `↑${r.upload_kbps}` : null,
          r.download_kbps != null ? `↓${r.download_kbps}` : null,
          r.offline ? "offline" : null,
        ]
          .filter(Boolean)
          .join(" ");
        return `<tr>
        <td><input type="checkbox" data-toggle="throttle" data-id="${escapeAttr(r.id)}" ${r.enabled ? "checked" : ""}></td>
        <td>${r.order}</td>
        <td>${escapeHtml(r.host)}</td>
        <td>${escapeHtml(limits || "—")}</td>
        <td><button type="button" class="btn ghost" data-del="throttle" data-id="${escapeAttr(r.id)}">Del</button></td>
      </tr>`;
      })
      .join("");

    const bpBody = document.getElementById("bp-rows");
    if (bpBody) {
      bpBody.innerHTML = (data.breakpoints || [])
        .map((r) => {
          const stages = [
            r.request_enabled ? "request" : null,
            r.response_enabled ? "response" : null,
          ]
            .filter(Boolean)
            .join("+");
          return `<tr>
          <td><input type="checkbox" data-toggle="breakpoints" data-id="${escapeAttr(r.id)}" ${r.enabled ? "checked" : ""}></td>
          <td>${r.order}</td>
          <td>${escapeHtml(matchLabel(r.match))}</td>
          <td>${escapeHtml(stages || "—")}</td>
          <td><button type="button" class="btn ghost" data-del="breakpoints" data-id="${escapeAttr(r.id)}">Del</button></td>
        </tr>`;
        })
        .join("");
    }

    document.querySelectorAll("[data-toggle]").forEach((el) => {
      el.addEventListener("change", async () => {
        const kind = el.getAttribute("data-toggle");
        const id = el.getAttribute("data-id");
        const q = new URLSearchParams({ enabled: String(el.checked) });
        await fetch(
          `/api/rules/${kind}/${encodeURIComponent(id)}/toggle?${q}`,
          { method: "POST" }
        );
        this.reloadRules();
      });
    });
    document.querySelectorAll("[data-del]").forEach((el) => {
      el.addEventListener("click", async () => {
        const kind = el.getAttribute("data-del");
        const id = el.getAttribute("data-id");
        await fetch(`/api/rules/${kind}/${encodeURIComponent(id)}`, { method: "DELETE" });
        this.reloadRules();
      });
    });
  };

  const LOG_ROW_CAP = 500;
  const LOG_PENDING_CAP = 5000;
  const LOG_FLUSH_MS = 400;

  function describeCaptureHint(sdk) {
    if (!sdk.enabled) {
      return "SDK receiver is off. Enable it on Setup, then run the debug app.";
    }
    const act = sdk.activity || {};
    if (!act.pairings && !act.flows) {
      return `No app has paired with ${sdk.receiver_url}. Network stays empty until the debug build connects and Local Network is allowed. HTTP lines in device logs are not captured requests.`;
    }
    return describeSdkActivity(sdk);
  }

  function describeSdkActivity(sdk) {
    if (!sdk.enabled) return "SDK receiver off (enable on Setup)";
    const act = sdk.activity || {};
    if (!act.last_seen_at) return `SDK receiver ${sdk.receiver_url} · no app connected yet`;
    const ago = Math.max(0, Math.round((Date.now() - Date.parse(act.last_seen_at)) / 1000));
    const parts = [
      `SDK ${act.last_client}`,
      `${act.flows} flows`,
      `${act.logs} logs`,
      `last ${ago}s ago`,
    ];
    if (act.rejected) parts.push(`${act.rejected} rejected (${act.last_error})`);
    return parts.join(" · ");
  }

  ViewliftNetwork.pollCaptureStatus = async function pollCaptureStatus() {
    const el = document.getElementById("capture-status");
    if (!el) return;
    try {
      const res = await fetch("/api/setup/status");
      const data = await res.json();
      const proxy = data.proxy || {};
      const proxyText = proxy.host ? `proxy ${proxy.host}:${proxy.port}` : "proxy off";
      el.textContent = `${describeSdkActivity(data.sdk_receiver || {})} · ${proxyText}`;
      const hint = document.getElementById("capture-hint");
      if (hint) hint.textContent = describeCaptureHint(data.sdk_receiver || {});
    } catch (_) {
      el.textContent = "status unavailable";
    }
    setTimeout(() => this.pollCaptureStatus(), 5000);
  };

  ViewliftNetwork.initLogs = function initLogs() {
    this.selectedLogId = null;
    this.logs = [];
    this.logPending = [];
    this.logPaused = false;
    this.logFlushTimer = null;
    this.logWs = null;
    document.getElementById("log-rows").addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (tr) this.selectLog(tr.dataset.id);
    });
    document.getElementById("btn-log-pause").addEventListener("click", () => {
      this.setLogPaused(!this.logPaused);
    });
    document.getElementById("btn-log-start").addEventListener("click", () => this.startLogStream());
    document.getElementById("btn-log-stop").addEventListener("click", () => this.stopLogStream());
    document.getElementById("btn-log-refresh-sources").addEventListener("click", () => this.reloadLogSources());
    document.getElementById("btn-log-apply").addEventListener("click", () => this.applyLogFilter());
    document.getElementById("btn-log-clear").addEventListener("click", () => {
      ["lf-level", "lf-source", "lf-subsystem", "lf-text", "log-process"].forEach((id) => {
        document.getElementById(id).value = "";
      });
      this.applyLogFilter();
    });
    document.getElementById("log-source").addEventListener("change", () => this.reloadLogSources());
    ["lf-level", "lf-source", "lf-subsystem", "lf-text", "log-process"].forEach((id) => {
      const el = document.getElementById(id);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.applyLogFilter();
      });
      el.addEventListener("input", () => this.scheduleLogFilter());
    });
    this.reloadLogSources();
    this.reloadLogs();
    this.connectLogWs();
  };

  ViewliftNetwork.logFilterQuery = function logFilterQuery() {
    const params = new URLSearchParams();
    const level = document.getElementById("lf-level").value.trim();
    const source = document.getElementById("lf-source").value.trim();
    const subsystem = document.getElementById("lf-subsystem").value.trim();
    const text = document.getElementById("lf-text").value.trim();
    const process = document.getElementById("log-process").value.trim();
    if (level) params.set("level", level);
    if (source) params.set("source", source);
    if (subsystem) params.set("subsystem", subsystem);
    if (text) params.set("text", text);
    else if (process && !subsystem) params.set("text", process);
    return params.toString();
  };

  ViewliftNetwork.reloadLogSources = async function reloadLogSources() {
    const res = await fetch("/api/logs/sources");
    const data = await res.json();
    const note = document.getElementById("log-private-note");
    if (note && data.testflight_note) note.textContent = data.testflight_note;
    const kind = document.getElementById("log-source").value;
    const sel = document.getElementById("log-udid");
    const rows =
      kind === "device" ? data.devices || [] : data.simulators || [];
    const options = rows
      .filter((r) => r.udid)
      .map((r) => {
        const label = `${r.name || r.udid}${r.connection ? " · " + r.connection : ""}${
          r.platform ? " · " + r.platform : ""
        }`;
        return `<option value="${escapeAttr(r.udid)}">${escapeHtml(label)}</option>`;
      });
    if (!options.length) {
      options.push(`<option value="">${escapeHtml(
        kind === "device" ? "No paired device" : "No booted simulator"
      )}</option>`);
    }
    sel.innerHTML = options.join("");
    const active = data.active || {};
    const status = document.getElementById("log-stream-status");
    status.textContent = active.running
      ? `streaming ${active.source} ${active.udid || ""}`.trim()
      : "idle";
  };

  ViewliftNetwork.startLogStream = async function startLogStream() {
    const source = document.getElementById("log-source").value;
    const udid = document.getElementById("log-udid").value.trim();
    const process = document.getElementById("log-process").value.trim();
    this.pushLogFilter();
    const res = await fetch("/api/logs/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, udid: udid || null, process: process || null }),
    });
    const data = await res.json();
    document.getElementById("log-stream-status").textContent = data.detail || (res.ok ? "started" : "failed");
    this.reloadLogSources();
  };

  ViewliftNetwork.stopLogStream = async function stopLogStream() {
    const res = await fetch("/api/logs/stop", { method: "POST" });
    const data = await res.json();
    document.getElementById("log-stream-status").textContent = data.detail || "stopped";
    this.reloadLogSources();
  };

  ViewliftNetwork.scheduleLogFilter = function scheduleLogFilter() {
    this.renderLogRows();
    clearTimeout(this.logFilterTimer);
    this.logFilterTimer = setTimeout(() => this.applyLogFilter(), 250);
  };

  ViewliftNetwork.applyLogFilter = function applyLogFilter() {
    this.pushLogFilter();
    this.reloadLogs();
  };

  ViewliftNetwork.pushLogFilter = function pushLogFilter() {
    const process = document.getElementById("log-process").value.trim();
    const subsystem = document.getElementById("lf-subsystem").value.trim();
    fetch("/api/logs/filter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ process, subsystem }),
    }).catch(() => {});
  };

  ViewliftNetwork.reloadLogs = async function reloadLogs() {
    const gen = (this.logLoadGen = (this.logLoadGen || 0) + 1);
    const q = this.logFilterQuery();
    const res = await fetch(`/api/logs${q ? `?${q}` : ""}`);
    if (gen !== this.logLoadGen) return;
    const data = await res.json();
    if (gen !== this.logLoadGen) return;
    this.logs = data.logs || [];
    this.logPending = [];
    this.renderLogRows();
  };

  ViewliftNetwork.logMatchesFilters = function logMatchesFilters(log) {
    const level = document.getElementById("lf-level").value.trim().toLowerCase();
    const source = document.getElementById("lf-source").value.trim();
    const subsystem = document.getElementById("lf-subsystem").value.trim().toLowerCase();
    const text = document.getElementById("lf-text").value.trim().toLowerCase();
    if (level && (log.level || "").toLowerCase() !== level) return false;
    if (source && (log.source || "") !== source) return false;
    const sub = (log.subsystem || "").toLowerCase();
    const process = document.getElementById("log-process").value.trim().toLowerCase();
    if (process && !sub.includes(process) && !(log.message || "").toLowerCase().includes(process)) {
      return false;
    }
    if (subsystem && !sub.includes(subsystem) && !(log.message || "").toLowerCase().includes(subsystem)) {
      return false;
    }
    if (text) {
      const hay = `${log.message || ""}\n${sub}\n${log.request_id || ""}`.toLowerCase();
      if (!hay.includes(text)) return false;
    }
    return true;
  };

  ViewliftNetwork.setLogPaused = function setLogPaused(paused) {
    this.logPaused = paused;
    document.getElementById("btn-log-pause").textContent = paused ? "Resume live" : "Pause";
    if (!paused) this.flushLogPending();
    this.updateLogPendingLabel();
  };

  ViewliftNetwork.updateLogPendingLabel = function updateLogPendingLabel() {
    const el = document.getElementById("log-pending");
    if (!el) return;
    const n = this.logPending.length;
    el.textContent = this.logPaused ? `paused${n ? ` · ${n} new` : ""}` : "";
  };

  ViewliftNetwork.queueLiveLog = function queueLiveLog(log) {
    if (!this.logMatchesFilters(log)) return;
    this.logPending.push(log);
    if (this.logPending.length > LOG_PENDING_CAP) {
      this.logPending.splice(0, this.logPending.length - LOG_PENDING_CAP);
    }
    if (this.logPaused) {
      this.updateLogPendingLabel();
      return;
    }
    if (this.logFlushTimer) return;
    this.logFlushTimer = setTimeout(() => {
      this.logFlushTimer = null;
      if (!this.logPaused) this.flushLogPending();
    }, LOG_FLUSH_MS);
  };

  ViewliftNetwork.flushLogPending = function flushLogPending() {
    if (!this.logPending.length) return;
    const fresh = this.logPending.reverse();
    this.logPending = [];
    this.logs = fresh.concat(this.logs);
    if (this.logs.length > LOG_ROW_CAP) this.logs.length = LOG_ROW_CAP;
    this.renderLogRows();
  };

  ViewliftNetwork.renderLogRows = function renderLogRows() {
    const tbody = document.getElementById("log-rows");
    const visible = this.logs.filter((log) => this.logMatchesFilters(log));
    document.getElementById("log-count").textContent = String(visible.length);
    const html = visible.map((log) => `
      <tr data-id="${escapeAttr(log.id)}"${log.id === this.selectedLogId ? ' class="active"' : ""}>
        <td class="col-time">${escapeHtml((log.created_at || "").replace("T", " ").slice(0, 19))}</td>
        <td>${escapeHtml(log.level || "")}</td>
        <td>${escapeHtml(log.source || "")}</td>
        <td title="${escapeAttr(log.subsystem || "")}">${escapeHtml(log.subsystem || "")}</td>
        <td title="${escapeAttr(log.message || "")}">${escapeHtml(log.message || "")}</td>
      </tr>`);
    tbody.innerHTML = html.join("");
  };

  ViewliftNetwork.selectLog = async function selectLog(id, fetchDetail = true) {
    this.selectedLogId = id;
    document.querySelectorAll("#log-rows tr.active").forEach((tr) => tr.classList.remove("active"));
    const row = document.querySelector(`#log-rows tr[data-id="${CSS.escape(id)}"]`);
    if (row) row.classList.add("active");
    if (!this.logPaused) this.setLogPaused(true);
    if (!fetchDetail) return;
    const res = await fetch(`/api/logs/${encodeURIComponent(id)}`);
    if (!res.ok) {
      document.getElementById("log-detail-body").innerHTML =
        `<p class="muted empty">Log not found</p>`;
      return;
    }
    const d = await res.json();
    document.getElementById("log-detail-title").textContent = d.subsystem || "Detail";
    const flowLink = d.linked_flow_id
      ? `<a href="/flows/${encodeURIComponent(d.linked_flow_id)}">Open network flow</a>`
      : (d.request_id ? `<span class="muted">No matching flow for requestId</span>` : `<span class="muted">—</span>`);
    document.getElementById("log-detail-body").innerHTML = `
      <table class="kv">
        <tr><th>Time</th><td>${escapeHtml(d.created_at || "")}</td></tr>
        <tr><th>Level</th><td>${escapeHtml(d.level || "")}</td></tr>
        <tr><th>Source</th><td>${escapeHtml(d.source || "")}</td></tr>
        <tr><th>Subsystem</th><td>${escapeHtml(d.subsystem || "")}</td></tr>
        <tr><th>Request ID</th><td>${escapeHtml(d.request_id || "—")}</td></tr>
        <tr><th>Linked flow</th><td>${flowLink}</td></tr>
      </table>
      <h3>Message</h3>
      <pre class="pre">${escapeHtml(d.message || "")}</pre>
      <p class="muted">${escapeHtml(d.testflight_private_note || "")}</p>
    `;
  };

  ViewliftNetwork.connectLogWs = function connectLogWs() {
    if (this.logWs) {
      try { this.logWs.close(); } catch (_) {}
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
    this.logWs = ws;
    const dot = document.getElementById("live-dot");
    const label = document.getElementById("live-label");
    ws.addEventListener("open", () => {
      if (dot) { dot.classList.add("on"); dot.classList.remove("off"); }
      if (label) label.textContent = "live";
    });
    ws.addEventListener("close", () => {
      if (dot) { dot.classList.remove("on"); dot.classList.add("off"); }
      if (label) label.textContent = "reconnect";
      setTimeout(() => this.connectLogWs(), 1500);
    });
    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (msg.type === "log" && msg.log) this.queueLiveLog(msg.log);
    });
  };

  ViewliftNetwork.initSetup = function initSetup() {
    document.getElementById("btn-ensure-ca").addEventListener("click", () => this.ensureCa());
    document.getElementById("btn-dl-ca").addEventListener("click", () => this.downloadCa());
    document.getElementById("btn-dl-mobileconfig").addEventListener("click", () => this.downloadMobileconfig());
    document.getElementById("btn-apply-proxy").addEventListener("click", () => this.applyProxyBind());
    document.getElementById("btn-sim-ca").addEventListener("click", () => this.simInstallCa());
    document.getElementById("btn-sim-proxy").addEventListener("click", () => this.simConfigureProxy());
    document.getElementById("btn-test").addEventListener("click", () => this.runSetupTest());
    const enableBtn = document.getElementById("btn-sdk-enable");
    if (enableBtn) {
      enableBtn.addEventListener("click", () => this.enableSdkReceiver());
      document.getElementById("btn-sdk-disable").addEventListener("click", () => this.disableSdkReceiver());
      document.getElementById("btn-sdk-token").addEventListener("click", () => this.mintSdkPairingToken());
    }
    this.reloadSetup();
  };

  ViewliftNetwork.reloadSetup = async function reloadSetup() {
    const res = await fetch("/api/setup/status");
    const data = await res.json();
    document.getElementById("ca-status").textContent = data.ca_ready ? "ready" : "missing";
    document.getElementById("ca-mode").textContent = data.private_key_mode
      ? `private key mode ${data.private_key_mode}`
      : "";
    document.getElementById("keychain-fallback").textContent = data.keychain_fallback_active
      ? "Keychain fallback file in use"
      : "";
    const sel = document.getElementById("proxy-ip");
    const previous = sel.value;
    sel.innerHTML = (data.interfaces || [])
      .map((row) => {
        const label = `${row.iface} — ${row.ip} (${row.scope})`;
        return `<option value="${escapeAttr(row.ip)}">${escapeHtml(label)}</option>`;
      })
      .join("");
    const preferred = (data.proxy && data.proxy.host) || previous || "127.0.0.1";
    if ([...sel.options].some((o) => o.value === preferred)) {
      sel.value = preferred;
    }
    document.getElementById("proxy-port").value = (data.proxy && data.proxy.port) || 8080;
    document.getElementById("enable-lan").checked = !!(data.proxy && data.proxy.lan_enabled);
    this.renderProxyAuth(data.proxy && data.proxy.auth);
    const sdk = data.sdk_receiver || {};
    const sdkIp = document.getElementById("sdk-ip");
    if (sdkIp) {
      const previousSdk = sdkIp.value;
      sdkIp.innerHTML = (data.interfaces || [])
        .filter((row) => row.scope !== "link-local" && row.scope !== "public")
        .map((row) => {
          const label = `${row.iface} — ${row.ip} (${row.scope})`;
          return `<option value="${escapeAttr(row.iface)}">${escapeHtml(label)}</option>`;
        })
        .join("");
      const preferredSdk = sdk.iface || previousSdk || "lo0";
      if (preferredSdk && [...sdkIp.options].some((o) => o.value === preferredSdk)) {
        sdkIp.value = preferredSdk;
      }
      if (sdk.port) document.getElementById("sdk-port").value = sdk.port;
    }
    document.getElementById("sdk-url").textContent = sdk.receiver_url || "Not enabled";
    document.getElementById("sdk-token").textContent = sdk.pairing_token || "Not enabled";
    document.getElementById("sdk-fp").textContent = sdk.pinned_fingerprint || "Not enabled";
    document.getElementById("sdk-note").textContent = sdk.note || "";
    const activity = document.getElementById("sdk-activity");
    if (activity) activity.textContent = describeSdkActivity(sdk);
    const snippet = document.getElementById("sdk-snippet");
    if (snippet) {
      snippet.textContent = this.buildSdkSnippet(sdk);
    }
    if (data.last_test) {
      document.getElementById("test-msg").textContent = JSON.stringify(data.last_test, null, 2);
    }
  };

  ViewliftNetwork.buildSdkSnippet = function buildSdkSnippet(sdk) {
    const url = sdk.receiver_url || "receiver off";
    const fp = sdk.pinned_fingerprint || "enable the receiver";
    return [
      "DEBUG app reads host, port, fingerprint, and debug key from this dashboard.",
      "No values are compiled into the app.",
      `Receiver: ${url}`,
      `Fingerprint: ${fp}`,
      "Enable the receiver, then run the DEBUG app on the same network or VPN.",
    ].join("\n");
  };

  ViewliftNetwork.enableSdkReceiver = async function enableSdkReceiver() {
    const iface = document.getElementById("sdk-ip").value;
    const port = Number(document.getElementById("sdk-port").value);
    const res = await fetch("/api/setup/sdk/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ iface, port }),
    });
    const data = await res.json();
    document.getElementById("sdk-note").textContent = res.ok
      ? (data.note || "SDK receiver enabled")
      : (data.detail || "Enable failed");
    this.reloadSetup();
  };

  ViewliftNetwork.disableSdkReceiver = async function disableSdkReceiver() {
    const res = await fetch("/api/setup/sdk/disable", { method: "POST" });
    const data = await res.json();
    document.getElementById("sdk-note").textContent = data.note || "SDK receiver disabled";
    this.reloadSetup();
  };

  ViewliftNetwork.mintSdkPairingToken = async function mintSdkPairingToken() {
    const res = await fetch("/api/setup/sdk/pairing-token", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("sdk-note").textContent = data.detail || "Mint failed";
      return;
    }
    document.getElementById("sdk-token").textContent = data.pairing_token || "";
    document.getElementById("sdk-note").textContent = "New one-use pairing token minted.";
    this.reloadSetup();
  };

  ViewliftNetwork.renderProxyAuth = function renderProxyAuth(auth) {
    const box = document.getElementById("proxy-auth-box");
    const cred = document.getElementById("proxy-auth-cred");
    if (!auth) {
      box.hidden = true;
      cred.textContent = "";
      return;
    }
    box.hidden = false;
    cred.textContent = `username: ${auth.username}\npassword: ${auth.password}`;
  };

  ViewliftNetwork.ensureCa = async function ensureCa() {
    const res = await fetch("/api/setup/ca/ensure", { method: "POST" });
    const data = await res.json();
    document.getElementById("ca-msg").textContent = res.ok
      ? `CA ready (private key mode ${data.private_key_mode || "?"})`
      : data.detail || "CA ensure failed";
    this.reloadSetup();
  };

  ViewliftNetwork.downloadCa = async function downloadCa() {
    const res = await fetch("/api/setup/ca/token", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("ca-msg").textContent = data.detail || "Token mint failed";
      return;
    }
    window.location.href = `/api/setup/ca/cert?token=${encodeURIComponent(data.token)}`;
    document.getElementById("ca-msg").textContent = "Public CA download started (one-use token).";
  };

  ViewliftNetwork.downloadMobileconfig = async function downloadMobileconfig() {
    const host = document.getElementById("proxy-ip").value;
    const port = Number(document.getElementById("proxy-port").value);
    const res = await fetch("/api/setup/mobileconfig/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port }),
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("ca-msg").textContent = data.detail || "mobileconfig token failed";
      return;
    }
    window.location.href = `/api/setup/mobileconfig?token=${encodeURIComponent(data.token)}`;
    document.getElementById("ca-msg").textContent = "Profile download started (one-use token).";
  };

  ViewliftNetwork.applyProxyBind = async function applyProxyBind() {
    const host = document.getElementById("proxy-ip").value;
    const port = Number(document.getElementById("proxy-port").value);
    const enable_lan = document.getElementById("enable-lan").checked;
    const res = await fetch("/api/setup/proxy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port, enable_lan }),
    });
    const data = await res.json();
    const msg = document.getElementById("proxy-msg");
    if (!res.ok) {
      msg.textContent = data.detail || "Proxy bind failed";
      return;
    }
    msg.textContent = `Proxy bound to ${data.host}:${data.port}` +
      (data.auth_required ? " (basic auth required)" : "");
    this.renderProxyAuth(data.auth);
    this.reloadSetup();
  };

  ViewliftNetwork.simInstallCa = async function simInstallCa() {
    const res = await fetch("/api/setup/simulator/install-ca", { method: "POST" });
    const data = await res.json();
    document.getElementById("sim-msg").textContent = JSON.stringify(data, null, 2);
  };

  ViewliftNetwork.simConfigureProxy = async function simConfigureProxy() {
    const res = await fetch("/api/setup/simulator/configure-proxy", { method: "POST" });
    const data = await res.json();
    document.getElementById("sim-msg").textContent = JSON.stringify(data, null, 2);
  };

  ViewliftNetwork.runSetupTest = async function runSetupTest() {
    const url = document.getElementById("test-url").value.trim() || "http://example.com/";
    const res = await fetch("/api/setup/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    document.getElementById("test-msg").textContent = JSON.stringify(data, null, 2);
  };

  ViewliftNetwork.replayFlow = async function replayFlow(id) {
    const res = await fetch(`/api/flows/${encodeURIComponent(id)}/replay`, { method: "POST" });
    const data = await res.json();
    const pane = document.getElementById("detail-body");
    const block = document.createElement("div");
    block.className = "rules-block";
    if (!res.ok) {
      block.innerHTML = `<h2>Replay</h2><p class="warn-text">${escapeHtml(data.detail || "Replay failed")}</p>`;
    } else {
      const headers = Object.entries(data.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
      block.innerHTML = `<h2>Replay ${escapeHtml(String(data.status))}</h2><pre class="pre">${escapeHtml(headers)}\n\n${escapeHtml(data.body || "")}</pre>`;
    }
    pane.prepend(block);
  };

  ViewliftNetwork.initDebug = async function initDebug() {
    const modules = await (await fetch("/api/debug/modules")).json();
    const grid = document.getElementById("debug-modules");
    const groups = {};
    for (const item of modules.modules || []) {
      (groups[item.group] ||= []).push(item);
    }
    grid.innerHTML = Object.entries(groups).map(([group, items]) => `
      <section class="module-group">
        <h2>${escapeHtml(group)}</h2>
        ${items.map((item) => {
          const title = item.href ? `<a href="${escapeAttr(item.href)}">${escapeHtml(item.title)}</a>` : escapeHtml(item.title);
          return `<article class="module-card">
            <span class="tag ${item.status === "ready" ? "ready" : "app-only"}">${escapeHtml(item.status)}</span>
            <h3>${title}</h3>
            <p class="muted">${escapeHtml(item.summary)}</p>
            ${item.note ? `<p class="muted">${escapeHtml(item.note)}</p>` : ""}
          </article>`;
        }).join("")}
      </section>`).join("");

    const profiles = await (await fetch("/api/debug/network-profiles")).json();
    const bar = document.getElementById("profile-buttons");
    const names = (profiles.profiles || []).map((p) => p.id).concat(["off"]);
    bar.innerHTML = names.map((name) => `<button type="button" class="btn ghost" data-profile="${escapeAttr(name)}">${escapeHtml(name)}</button>`).join("");
    bar.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-profile]");
      if (!button) return;
      const res = await fetch("/api/debug/network-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: button.dataset.profile }),
      });
      const data = await res.json();
      const status = document.getElementById("profile-status");
      status.textContent = res.ok
        ? `${data.profile} applied. ${data.packet_loss || ""}`.trim()
        : (data.detail || "Failed");
    });

    const flows = await (await fetch("/api/flows?limit=80")).json();
    this.renderWaterfall(flows.flows || []);
    this.renderAnalytics(flows.flows || []);
    this.renderDebugSecurity();

    document.getElementById("diagnostics").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-diag]");
      if (!button) return;
      const res = await fetch(`/api/logs?text=${encodeURIComponent(button.dataset.diag)}&limit=30`);
      const data = await res.json();
      const rows = data.logs || [];
      document.getElementById("diag-rows").innerHTML = rows.length
        ? rows.map((log) => `<p><span class="muted">${escapeHtml(log.subsystem || "")}</span> ${escapeHtml(log.message || "")}</p>`).join("")
        : `<p class="muted">No log lines match.</p>`;
    });

    document.getElementById("deeplink-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const res = await fetch("/api/debug/deeplink", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          udid: document.getElementById("deeplink-udid").value.trim(),
          url: document.getElementById("deeplink-url").value.trim(),
        }),
      });
      const data = await res.json();
      document.getElementById("deeplink-status").textContent = data.detail || "Failed";
    });

    document.getElementById("location-form").addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendLocation(false);
    });
    document.getElementById("loc-clear").addEventListener("click", () => this.sendLocation(true));
  };

  ViewliftNetwork.renderWaterfall = function renderWaterfall(flows) {
    const host = document.getElementById("waterfall");
    if (!flows.length) {
      host.innerHTML = `<p class="muted">No captured requests yet.</p>`;
      return;
    }
    const start = Math.min(...flows.map((flow) => Date.parse(flow.created_at) || 0));
    const ends = flows.map((flow) => (Date.parse(flow.created_at) || start) + (flow.duration_ms || 1));
    const span = Math.max(1, Math.max(...ends) - start);
    host.innerHTML = flows.slice(0, 40).map((flow) => {
      const left = (((Date.parse(flow.created_at) || start) - start) / span) * 100;
      const width = Math.max(1.5, ((flow.duration_ms || 1) / span) * 100);
      const label = `${flow.method} ${flow.host}${flow.path}`;
      return `<a class="fall-row" href="/flows/${encodeURIComponent(flow.id)}" title="${escapeAttr(label)}">
        <span class="fall-label">${escapeHtml(label)}</span>
        <span class="fall-track"><span class="fall-bar" style="left:${left}%;width:${Math.min(width, 100 - left)}%"></span></span>
      </a>`;
    }).join("");
  };

  ViewliftNetwork.renderAnalytics = function renderAnalytics(flows) {
    const markers = ["firebase", "amplitude", "mixpanel", "segment.io", "google-analytics", "app-measurement"];
    const hits = flows.filter((flow) => markers.some((mark) => (flow.host || "").toLowerCase().includes(mark)));
    document.getElementById("analytics-rows").innerHTML = hits.length
      ? hits.map((flow) => `<p><a href="/flows/${encodeURIComponent(flow.id)}">${escapeHtml(flow.method)} ${escapeHtml(flow.host)}${escapeHtml(flow.path)}</a></p>`).join("")
      : `<p class="muted">No analytics hosts in the latest flows.</p>`;
  };

  ViewliftNetwork.renderDebugSecurity = async function renderDebugSecurity() {
    const setup = await (await fetch("/api/setup/status")).json();
    const reveal = await (await fetch("/api/reveal/status")).json();
    const proxy = setup.proxy || {};
    const rows = [
      `Proxy ${proxy.host || "?"}:${proxy.port || "?"} · LAN ${proxy.lan_enabled ? "on" : "off"}`,
      `SDK receiver ${setup.sdk_receiver && setup.sdk_receiver.enabled ? setup.sdk_receiver.receiver_url : "off"}`,
      `Reveal ${reveal.unlocked ? "unlocked" : "locked"}`,
      "Allowlist still defaults to deny until a host is added on Rules.",
    ];
    document.getElementById("security-rows").innerHTML = rows.map((row) => `<p>${escapeHtml(row)}</p>`).join("");
  };

  ViewliftNetwork.sendLocation = async function sendLocation(clear) {
    const body = {
      udid: document.getElementById("loc-udid").value.trim(),
      clear,
    };
    if (!clear) {
      body.latitude = Number(document.getElementById("loc-lat").value);
      body.longitude = Number(document.getElementById("loc-lon").value);
    }
    const res = await fetch("/api/debug/location", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    document.getElementById("location-status").textContent = data.detail || "Failed";
  };

  window.ViewliftNetwork = ViewliftNetwork;
})();
