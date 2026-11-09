/* Local-only dashboard. No external network calls. */
(() => {
  const FLOW_FILTER_IDS = ["f-method", "f-host", "f-status", "f-ctype", "f-kind", "f-operation", "f-text"];
  const LOG_FILTER_IDS = ["lf-level", "lf-source", "lf-subsystem", "lf-text"];
  // App console only: stdout and stderr from the SDK tee. os_log and syslog are not shown.
  const CONSOLE_SOURCES = "stdout,stderr";
  const CONSOLE_SOURCE_SET = new Set(["stdout", "stderr"]);

  /* control-state */
  function commitButtonPlan(draft, applied) {
    const dirty = Object.keys(draft).some((key) => draft[key] !== (applied[key] || ""));
    const active = Object.keys(applied).some((key) => applied[key] !== "");
    return { showApply: !active || dirty, showClear: active };
  }

  /* /control-state */

  function fieldValues(ids) {
    const values = {};
    for (const id of ids) {
      const el = document.getElementById(id);
      values[id] = el ? el.value.trim() : "";
    }
    return values;
  }

  function applyCommitPlan(applyBtn, clearBtn, draft, applied) {
    if (!applyBtn || !clearBtn) return;
    const plan = commitButtonPlan(draft, applied);
    applyBtn.hidden = !plan.showApply;
    clearBtn.hidden = !plan.showClear;
  }

  const ViewliftNetwork = {
    selectedId: null,
    selectedPendingId: null,
    flows: [],
    pending: [],
    ws: null,
    revealUnlocked: false,
    flowView: "list",
    openGroupKey: null,
    openGroupLabel: null,
    detailTab: "overview",
    followLatest: false,
    followSig: "",

    initDashboard() {
      this.restoreUiMemory();
      this.appliedFlowFilters = fieldValues(FLOW_FILTER_IDS);
      const apply = () => {
        this.appliedFlowFilters = fieldValues(FLOW_FILTER_IDS);
        this.syncFlowFilterButtons();
        return this.reloadFlows();
      };
      document.getElementById("btn-follow-latest")?.addEventListener("click", () => {
        this.setFollowLatest(!this.followLatest);
      });
      document.getElementById("btn-flow-delete")?.addEventListener("click", () => this.deleteFlows());
      document.getElementById("btn-reveal-unlock")?.addEventListener("click", () => this.unlockReveal());
      document.getElementById("btn-reveal-lock")?.addEventListener("click", () => this.lockReveal());
      this.refreshRevealStatus();
      document.getElementById("btn-clear").addEventListener("click", () => {
        FLOW_FILTER_IDS.forEach((id) => {
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
      FLOW_FILTER_IDS.forEach((id) => {
        const el = document.getElementById(id);
        if (!el || el.type === "hidden") return;
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter") apply();
        });
        el.addEventListener("input", () => this.syncFlowFilterButtons());
        if (el.tagName === "SELECT") el.addEventListener("change", apply);
      });
      document.querySelectorAll("#kind-switch [data-kind]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const input = document.getElementById("f-kind");
          if (!input) return;
          input.value = btn.dataset.kind || "";
          this.syncKindButtons();
          apply();
        });
      });
      document.getElementById("view-list")?.addEventListener("click", () => this.setFlowView("list"));
      document.getElementById("view-urls")?.addEventListener("click", () => this.setFlowView("urls"));
      document.getElementById("group-back")?.addEventListener("click", () => {
        this.openGroupKey = null;
        this.openGroupLabel = null;
        this.renderRows();
      });
      document.addEventListener("vl:scope-change", () => this.reloadFlows());
      document.addEventListener("vl:apps-change", () => this.renderRows());
      this.syncFlowFilterButtons();
      this.reloadFlows();
      this.reloadPending();
      this.pollCaptureStatus();
      this.connectWs();
    },

    syncFlowFilterButtons() {
      this.syncKindButtons();
      applyCommitPlan(
        document.getElementById("btn-apply"),
        document.getElementById("btn-clear"),
        fieldValues(FLOW_FILTER_IDS),
        this.appliedFlowFilters || {}
      );
    },

    syncKindButtons() {
      const value = document.getElementById("f-kind")?.value || "";
      document.querySelectorAll("#kind-switch [data-kind]").forEach((btn) => {
        const on = (btn.dataset.kind || "") === value;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    },

    filterQuery() {
      const params = new URLSearchParams();
      const method = document.getElementById("f-method").value.trim();
      const host = document.getElementById("f-host").value.trim();
      const status = document.getElementById("f-status").value.trim();
      const ctype = document.getElementById("f-ctype").value.trim();
      const kind = document.getElementById("f-kind").value.trim();
      const operation = document.getElementById("f-operation").value.trim();
      const text = document.getElementById("f-text").value.trim();
      if (method) params.set("method", method);
      if (host) params.set("host", host);
      if (status) params.set("status", status);
      if (ctype) params.set("content-type", ctype);
      if (kind) params.set("kind", kind);
      if (operation) params.set("operation", operation);
      if (text) params.set("text", text);
      if (window.VLScope) window.VLScope.applyTo(params);
      return params.toString();
    },

    async deleteFlows() {
      const ok = window.confirm(
        "Delete every captured network request on this Mac? Device logs, debug events, and rules stay."
      );
      if (!ok) return;
      const res = await fetch("/api/flows/delete", { method: "POST" });
      if (!res.ok) {
        toast("Could not delete flows");
        return;
      }
      const data = await res.json();
      this.detailGen = (this.detailGen || 0) + 1;
      this.flows = [];
      this.selectedId = null;
      const curl = document.getElementById("btn-curl");
      const har = document.getElementById("btn-har-one");
      if (curl) curl.disabled = true;
      if (har) har.disabled = true;
      const open = document.getElementById("btn-open");
      if (open) {
        open.href = "#";
        open.classList.add("disabled");
      }
      const title = document.getElementById("detail-title");
      if (title) title.textContent = "Detail";
      const body = document.getElementById("detail-body");
      if (body) {
        body.innerHTML = `<p class="muted empty">Select a request to inspect its headers, body, and timing.</p>`;
      }
      this.renderRows();
      if (window.VLScope) window.VLScope.refresh();
      toast(data.deleted ? `Deleted ${data.deleted} flows` : "No flows to delete");
    },

    async reloadFlows() {
      const q = this.filterQuery();
      const res = await fetch(`/api/flows${q ? `?${q}` : ""}`);
      const data = await res.json();
      this.flows = data.flows || [];
      this.renderRows();
      if (this.followLatest) {
        this.followLatestFlow();
        return;
      }
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
            `<p class="muted empty">Select a request to inspect its headers, body, and timing.</p>`;
        }
      }
    },

    restoreUiMemory() {
      try {
        const view = localStorage.getItem("vl-flow-view");
        if (view === "urls" || view === "list") this.flowView = view;
        const tab = localStorage.getItem("vl-detail-tab");
        if (tab) this.detailTab = tab;
      } catch {
        /* private mode */
      }
    },

    rememberDetailTab(tab) {
      this.detailTab = tab;
      try {
        localStorage.setItem("vl-detail-tab", tab);
      } catch {
        /* private mode */
      }
    },

    setFlowView(view) {
      const next = view === "urls" ? "urls" : "list";
      if (next === "list" || this.flowView === "urls") {
        this.openGroupKey = null;
        this.openGroupLabel = null;
      }
      this.flowView = next;
      try {
        localStorage.setItem("vl-flow-view", next);
      } catch {
        /* private mode */
      }
      this.renderRows();
      if (this.followLatest) this.followLatestFlow();
    },

    baseUrlKey(item) {
      return String((item && item.host) || "").toLowerCase();
    },

    collectUrlGroups() {
      const map = new Map();
      const order = [];
      const ensure = (item, pending) => {
        const key = this.baseUrlKey(item);
        let group = map.get(key);
        if (!group) {
          const host = String(item.host || "");
          group = {
            key,
            host,
            label: host || "(no host)",
            count: 0,
            methodSet: new Set(),
            status: null,
            sawFlow: false,
            paused: false,
          };
          map.set(key, group);
          order.push(group);
        }
        group.count += 1;
        if (item.method) group.methodSet.add(String(item.method).toUpperCase());
        if (pending) group.paused = true;
        else if (!group.sawFlow) {
          group.sawFlow = true;
          group.status = item.status;
        }
        return group;
      };
      for (const flow of this.flows) ensure(flow, false);
      const pendingOnly = [];
      for (const item of this.pending) {
        const existed = map.has(this.baseUrlKey(item));
        const group = ensure(item, true);
        if (!existed) pendingOnly.push(group);
      }
      const pendingKeys = new Set(pendingOnly.map((group) => group.key));
      const groups = [...pendingOnly, ...order.filter((group) => !pendingKeys.has(group.key))];
      const rank = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
      for (const group of groups) {
        const methods = [...group.methodSet];
        methods.sort((a, b) => {
          const ia = rank.indexOf(a);
          const ib = rank.indexOf(b);
          if (ia === -1 && ib === -1) return a.localeCompare(b);
          if (ia === -1) return 1;
          if (ib === -1) return -1;
          return ia - ib;
        });
        group.methods = methods.join(" ");
      }
      return groups;
    },

    renderRows() {
      const drilling = this.flowView === "urls" && !!this.openGroupKey;
      const showGroups = this.flowView === "urls" && !this.openGroupKey;
      const groupWrap = document.getElementById("group-wrap");
      const flowWrap = document.getElementById("flow-wrap");
      if (groupWrap) groupWrap.hidden = !showGroups;
      if (flowWrap) flowWrap.hidden = showGroups;
      const back = document.getElementById("group-back");
      if (back) back.hidden = !drilling;
      const heading = document.getElementById("flow-heading");
      if (heading) {
        if (drilling) {
          heading.textContent = this.openGroupLabel || this.openGroupKey;
          heading.title = heading.textContent;
        } else if (showGroups) {
          heading.textContent = "Base URLs";
          heading.title = "One row per host. Open a row for every request to that host.";
        } else {
          heading.textContent = "Flows";
          heading.title = "";
        }
      }
      document.querySelectorAll("[data-flow-view]").forEach((btn) => {
        const on = btn.dataset.flowView === this.flowView;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (showGroups) {
        this.renderGroupRows();
        return;
      }
      this.renderFlowRows(drilling);
    },

    renderGroupRows() {
      const tbody = document.getElementById("group-rows");
      if (!tbody) return;
      const groups = this.collectUrlGroups();
      const count = document.getElementById("flow-count");
      if (count) count.textContent = String(groups.length);
      tbody.replaceChildren();
      if (!groups.length) {
        const tr = document.createElement("tr");
        tr.className = "static-row";
        tr.innerHTML = `<td colspan="4" class="muted">No requests match these filters.</td>`;
        tbody.appendChild(tr);
        return;
      }
      for (const group of groups) {
        const tr = document.createElement("tr");
        tr.dataset.groupKey = group.key;
        if (group.paused && group.status == null) tr.classList.add("paused");
        const statusClass = group.status == null ? "" : `status-${String(group.status)[0]}`;
        const statusText = group.status == null ? (group.paused ? "PAUSED" : "—") : String(group.status);
        tr.innerHTML = `
          <td title="${escapeAttr(group.label)}"><span class="url-host">${escapeHtml(group.label)}</span></td>
          <td class="col-count">${group.count}</td>
          <td class="col-status ${statusClass}">${escapeHtml(statusText)}</td>
          <td class="col-type">${escapeHtml(group.methods)}</td>
        `;
        if (this.followLatest && this.flows[0] && group.key === this.baseUrlKey(this.flows[0])) {
          tr.classList.add("active");
        }
        tr.addEventListener("click", () => {
          this.setFollowLatest(false);
          this.openGroupKey = group.key;
          this.openGroupLabel = group.label;
          this.renderRows();
        });
        tbody.appendChild(tr);
      }
    },

    renderFlowRows(drilling) {
      const tbody = document.getElementById("flow-rows");
      if (!tbody) return;
      const match = (item) => !drilling || this.baseUrlKey(item) === this.openGroupKey;
      const pending = drilling ? this.pending.filter(match) : this.pending;
      const flows = drilling ? this.flows.filter(match) : this.flows;
      const count = document.getElementById("flow-count");
      if (count) count.textContent = String(pending.length + flows.length);
      tbody.replaceChildren();
      if (drilling && !pending.length && !flows.length) {
        const tr = document.createElement("tr");
        tr.className = "static-row";
        tr.innerHTML = `<td colspan="8" class="muted">No requests for this URL.</td>`;
        tbody.appendChild(tr);
        return;
      }
      for (const item of pending) {
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
        tr.addEventListener("click", () => {
          this.setFollowLatest(false);
          this.selectPending(item.id);
        });
        tbody.appendChild(tr);
      }
      for (const flow of flows) {
        const tr = document.createElement("tr");
        tr.dataset.id = flow.id;
        if (flow.id === this.selectedId) tr.classList.add("active");
        if (flow.error === "breakpoint timed out") tr.classList.add("bp-timeout");
        const statusClass = flow.status == null ? "" : `status-${String(flow.status)[0]}`;
        tr.innerHTML = `
          <td class="col-method method ${escapeAttr(flow.method)}">${escapeHtml(flow.method)}</td>
          <td>${appBadge(flow.client_id)}${escapeHtml(flow.host)}</td>
          <td title="${escapeAttr(flowPathTitle(flow))}">${flowPathCell(flow)}</td>
          <td class="col-status ${statusClass}">${flow.status == null ? "—" : escapeHtml(String(flow.status))}</td>
          <td class="col-type">${escapeHtml(flow.graphql ? "graphql" : shortType(flow.content_type))}</td>
          <td class="col-time">${fmtMs(flow.duration_ms)}</td>
          <td class="col-size">${fmtBytes(flow.size_bytes)}</td>
          <td class="col-stage muted">${flow.error === "breakpoint timed out" ? "timeout" : "—"}</td>
        `;
        tr.addEventListener("click", () => {
          this.setFollowLatest(false);
          this.selectFlow(flow.id, true);
        });
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
      const gen = (this.detailGen = (this.detailGen || 0) + 1);
      this.selectedId = id;
      this.selectedPendingId = null;
      this.renderRows();
      this.scrollFlowRow(id);
      document.getElementById("btn-curl").disabled = false;
      document.getElementById("btn-har-one").disabled = false;
      const open = document.getElementById("btn-open");
      open.href = `/flows/${encodeURIComponent(id)}`;
      open.classList.remove("disabled");
      if (!fetchDetail) return;
      const q = this.revealUnlocked ? "?reveal=true" : "";
      const res = await fetch(`/api/flows/${encodeURIComponent(id)}${q}`);
      if (this.detailGen !== gen || this.selectedId !== id) return;
      if (!res.ok) {
        if (this.revealUnlocked && res.status === 404) {
          const fallback = await fetch(`/api/flows/${encodeURIComponent(id)}`);
          if (this.detailGen !== gen || this.selectedId !== id) return;
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
      if (this.detailGen !== gen || this.selectedId !== id) return;
      document.getElementById("detail-title").textContent =
        `${detail.method} ${detail.host}${detail.path}${detail.revealed ? " (revealed)" : ""}`;
      this.renderDetail(detail, document.getElementById("detail-body"));
    },

    setFollowLatest(on) {
      const was = this.followLatest;
      this.followLatest = on;
      const button = document.getElementById("btn-follow-latest");
      if (button) {
        button.classList.toggle("active", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.textContent = on ? "Following" : "Follow latest";
      }
      if (!on) {
        this.followSig = "";
        if (was) this.renderRows();
        return;
      }
      this.followLatestFlow();
    },

    followPool() {
      if (this.flowView === "urls" && this.openGroupKey) {
        return this.flows.filter((flow) => this.baseUrlKey(flow) === this.openGroupKey);
      }
      return this.flows;
    },

    followLatestFlow() {
      if (!this.followLatest) return;
      const overview = this.flowView === "urls" && !this.openGroupKey;
      const latest = this.followPool()[0];
      if (!latest) return;
      const sig = `${latest.id}|${latest.status}|${latest.size_bytes}|${latest.duration_ms}|${latest.error || ""}`;
      if (sig === this.followSig && latest.id === this.selectedId) {
        if (overview) this.scrollGroupRow(this.baseUrlKey(latest));
        else this.scrollFlowRow(latest.id);
        return;
      }
      this.followSig = sig;
      this.selectFlow(latest.id, true);
      if (overview) this.scrollGroupRow(this.baseUrlKey(latest));
    },

    scrollFlowRow(id) {
      const row = document.querySelector(`#flow-rows tr[data-id="${CSS.escape(id)}"]`);
      if (row) row.scrollIntoView({ block: "nearest", inline: "nearest" });
    },

    scrollGroupRow(key) {
      const row = document.querySelector(`#group-rows tr[data-group-key="${CSS.escape(key)}"]`);
      if (row) row.scrollIntoView({ block: "nearest", inline: "nearest" });
    },

    renderDetail(detail, root) {
      this.restoreUiMemory();
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
        ["edit", "Edit"],
      ];
      const active = tabs.some(([id]) => id === this.detailTab) ? this.detailTab : "overview";
      const on = (id) => (id === active ? " active" : "");
      root.innerHTML = `
        <div class="tabs">
          ${tabs.map(([id, label]) =>
            `<button type="button" class="tab${on(id)}" data-tab="${id}">${label}</button>`
          ).join("")}
        </div>
        <div class="tab-panel${on("overview")}" data-panel="overview">${renderOverview(detail)}</div>
        <div class="tab-panel${on("request")}" data-panel="request">${renderSide(detail.request)}</div>
        <div class="tab-panel${on("response")}" data-panel="response">${renderSide(detail.response)}</div>
        <div class="tab-panel${on("query")}" data-panel="query">${renderPairs(detail.query)}</div>
        <div class="tab-panel${on("headers")}" data-panel="headers">${renderHeaders(detail)}</div>
        <div class="tab-panel${on("cookies")}" data-panel="cookies">${renderCookies(detail)}</div>
        <div class="tab-panel${on("body")}" data-panel="body">${renderBodies(detail)}</div>
        <div class="tab-panel${on("timing")}" data-panel="timing">${renderTiming(detail.timing, detail.duration_ms)}</div>
        <div class="tab-panel${on("ws")}" data-panel="ws">${renderWs(detail.websocket_messages || [])}</div>
        <div class="tab-panel${on("edit")}" data-panel="edit">${renderEdit(detail)}</div>
      `;
      wireEdit(root, detail);
      root.querySelectorAll(".tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.rememberDetailTab(btn.dataset.tab);
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
      let ws;
      try {
        ws = new WebSocket(url);
      } catch {
        setTimeout(() => this.connectWs(), 1500);
        return;
      }
      this.ws = ws;
      ws.onclose = () => {
        setTimeout(() => this.connectWs(), 1500);
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
            `<p class="muted empty">Select a request to inspect its headers, body, and timing.</p>`;
        }
        this.renderRows();
        if (kind === "timed_out") this.reloadFlows();
      }
    },

    upsertFlow(flow) {
      if (!this.matchesFilters(flow)) {
        this.flows = this.flows.filter((f) => f.id !== flow.id);
        this.renderRows();
        this.followLatestFlow();
        return;
      }
      const idx = this.flows.findIndex((f) => f.id === flow.id);
      if (idx >= 0) this.flows[idx] = flow;
      else this.flows.unshift(flow);
      this.renderRows();
      this.followLatestFlow();
    },

    matchesFilters(flow) {
      const method = document.getElementById("f-method")?.value.trim().toUpperCase();
      const host = document.getElementById("f-host")?.value.trim().toLowerCase();
      const status = document.getElementById("f-status")?.value.trim();
      const ctype = document.getElementById("f-ctype")?.value.trim().toLowerCase();
      const kind = document.getElementById("f-kind")?.value.trim().toLowerCase();
      const operation = document.getElementById("f-operation")?.value.trim().toLowerCase();
      const text = document.getElementById("f-text")?.value.trim().toLowerCase();
      if (window.VLScope && !window.VLScope.matches(flow.client_id)) return false;
      if (method && String(flow.method || "").toUpperCase() !== method) return false;
      if (host && !String(flow.host || "").toLowerCase().includes(host)) return false;
      if (status && String(flow.status) !== status) return false;
      if (ctype && !String(flow.content_type || "").toLowerCase().includes(ctype)) return false;
      if (kind === "graphql" && !flow.graphql) return false;
      if (kind === "http" && flow.graphql) return false;
      if (operation && !String(flow.operation || "").toLowerCase().includes(operation)) return false;
      if (text) {
        const hay = [
          flow.method, flow.host, flow.path, flow.query, flow.operation,
          flow.content_type, flow.error, flow.protocol,
        ].join(" ").toLowerCase();
        if (!hay.includes(text)) return false;
      }
      return true;
    },
  };

  function flowPathTitle(flow) {
    const path = `${flow.path || ""}${flow.query ? `?${flow.query}` : ""}`;
    return flow.graphql && flow.operation ? `${flow.operation} ${path}` : path;
  }

  function flowPathCell(flow) {
    const path = escapeHtml(flow.path || "");
    const query = flow.query ? '<span class="muted">?…</span>' : "";
    if (flow.graphql && flow.operation) {
      return `<span class="op-name">${escapeHtml(flow.operation)}</span> <span class="muted">${path}</span>`;
    }
    return `${path}${query}`;
  }

  function renderOverview(d) {
    const o = d.overview || {};
    const rows = [
      ["URL", o.url],
      ["Method", o.method],
      ["Status", o.status],
      ["Protocol", o.protocol],
      ["Source", o.source],
      ["App", appLabel(d.client_id || o.client_id)],
      ["Duration", fmtMs(o.duration_ms)],
      ["Size", fmtBytes(o.size_bytes)],
      ["Content-Type", d.content_type],
    ];
    if (d.graphql) rows.push(["Operation", d.operation || "anonymous"]);
    rows.push(
      ["Request ID", o.request_id],
      ["TLS failed", o.tls_failed ? "yes" : "no"],
      ["Error", o.error],
    );
    return kvTable(rows);
  }

  const EDIT_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"];
  const EDIT_SKIP_HEADERS = new Set([
    "content-length", "host", "connection", "transfer-encoding", "keep-alive",
  ]);

  function renderEdit(detail) {
    const model = editModel(detail);
    const method = String(detail.method || "GET").toUpperCase();
    const methods = EDIT_METHODS.includes(method) ? EDIT_METHODS : EDIT_METHODS.concat(method);
    const note = detail.revealed
      ? ""
      : `<p class="muted">Cookies and secret fields stay redacted until reveal is unlocked and this flow is reopened.</p>`;
    const binary = model.binary
      ? `<p class="muted">Body is not text. Type a replacement or leave it empty.</p>`
      : "";
    const fields = model.mode === "graphql"
      ? `<label>Operation <input id="edit-operation" type="text" autocomplete="off" spellcheck="false"></label>
         <label>Query <textarea id="edit-query" rows="10" spellcheck="false"></textarea></label>
         <label>Variables <textarea id="edit-variables" rows="6" spellcheck="false"></textarea></label>`
      : `<label>Body <textarea id="edit-body" rows="10" spellcheck="false"></textarea></label>`;
    return `
      <form class="edit-request" id="edit-form" data-mode="${model.mode}" autocomplete="off">
        ${note}
        ${binary}
        <label>Method
          <select id="edit-method">
            ${methods.map((item) => `<option value="${item}">${item}</option>`).join("")}
          </select>
        </label>
        <label>URL <input id="edit-url" type="text" autocomplete="off" spellcheck="false"></label>
        <label>Headers <textarea id="edit-headers" rows="6" spellcheck="false"></textarea></label>
        ${fields}
        <div class="edit-actions">
          <button type="button" id="edit-copy" class="btn ghost">Copy cURL</button>
          <button type="button" id="edit-send" class="btn">Send</button>
        </div>
      </form>
      <div id="edit-result"></div>
    `;
  }

  function editModel(detail) {
    const view = (detail.request && detail.request.body) || {};
    const raw = typeof view.raw === "string" ? view.raw : "";
    if (detail.graphql && raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          return { mode: "body", body: view.pretty || raw, extras: null, binary: false };
        }
        if (parsed && typeof parsed === "object") {
          const operation = typeof parsed.operationName === "string" ? parsed.operationName : "";
          const query = typeof parsed.query === "string" ? parsed.query : "";
          const hasVariables = Object.prototype.hasOwnProperty.call(parsed, "variables");
          const variables = hasVariables ? JSON.stringify(parsed.variables, null, 2) : "{}";
          return { mode: "graphql", operation, query, variables, extras: parsed, binary: false };
        }
      } catch {
        const named = detail.operation && detail.operation !== "anonymous" ? detail.operation : "";
        return { mode: "graphql", operation: named, query: raw, variables: "{}", extras: null, binary: false };
      }
    }
    if (detail.graphql) {
      return { mode: "graphql", operation: "", query: "", variables: "{}", extras: null, binary: view.kind === "binary" };
    }
    return {
      mode: "body",
      body: view.kind === "binary" ? "" : (view.pretty || raw),
      extras: null,
      binary: view.kind === "binary",
    };
  }

  function wireEdit(root, detail) {
    const form = root.querySelector("#edit-form");
    if (!form) return;
    const model = editModel(detail);
    const method = form.querySelector("#edit-method");
    const current = String(detail.method || "GET").toUpperCase();
    if (method) method.value = current;
    const url = form.querySelector("#edit-url");
    if (url) url.value = (detail.overview && detail.overview.url) || "";
    const headers = form.querySelector("#edit-headers");
    if (headers) headers.value = headerLines(detail.request && detail.request.headers);
    if (model.mode === "graphql") {
      const operation = form.querySelector("#edit-operation");
      const query = form.querySelector("#edit-query");
      const variables = form.querySelector("#edit-variables");
      if (operation) operation.value = model.operation;
      if (query) query.value = model.query;
      if (variables) variables.value = model.variables;
    } else {
      const body = form.querySelector("#edit-body");
      if (body) body.value = model.body;
    }
    form.querySelector("#edit-copy")?.addEventListener("click", async () => {
      let command;
      try {
        command = editedCurl(form, model.extras);
      } catch (err) {
        toast(err.message || "Could not build cURL");
        return;
      }
      try {
        await navigator.clipboard.writeText(command);
        toast("cURL copied");
      } catch {
        toast(command.slice(0, 120));
      }
    });
    form.querySelector("#edit-send")?.addEventListener("click", () => sendEdited(form, model.extras));
  }

  function headerLines(headers) {
    return Object.entries(headers || {})
      .filter(([key]) => !EDIT_SKIP_HEADERS.has(String(key).toLowerCase()))
      .map(([key, value]) => `${key}: ${value}`)
      .join("\n");
  }

  function parseHeaderLines(text) {
    const headers = [];
    for (const line of String(text || "").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const splitAt = trimmed.indexOf(":");
      if (splitAt <= 0) continue;
      const key = trimmed.slice(0, splitAt).trim();
      const value = trimmed.slice(splitAt + 1).trim();
      if (!key || EDIT_SKIP_HEADERS.has(key.toLowerCase())) continue;
      headers.push([key, value]);
    }
    return headers;
  }

  function editedRequest(form, extras) {
    const method = form.querySelector("#edit-method").value;
    const url = form.querySelector("#edit-url").value.trim();
    if (!url) throw new Error("URL is required.");
    const headers = parseHeaderLines(form.querySelector("#edit-headers").value);
    let body = "";
    if (form.dataset.mode === "graphql") {
      const operation = form.querySelector("#edit-operation").value.trim();
      const query = form.querySelector("#edit-query").value;
      const variablesText = form.querySelector("#edit-variables").value.trim() || "{}";
      let variables;
      try {
        variables = JSON.parse(variablesText);
      } catch {
        throw new Error("Variables must be JSON.");
      }
      const variablesOk = variables === null || (typeof variables === "object" && !Array.isArray(variables));
      if (!variablesOk) throw new Error("Variables must be a JSON object.");
      if (extras && typeof extras === "object" && !Array.isArray(extras)) {
        const next = { ...extras, query, variables };
        if (operation) next.operationName = operation;
        else delete next.operationName;
        body = JSON.stringify(next);
      } else if (operation || variablesText !== "{}") {
        const next = { query, variables };
        if (operation) next.operationName = operation;
        body = JSON.stringify(next);
        if (!headers.some(([key]) => key.toLowerCase() === "content-type")) {
          headers.push(["Content-Type", "application/json"]);
        }
      } else {
        body = query;
      }
    } else {
      body = form.querySelector("#edit-body").value;
    }
    return { method, url, headers, body };
  }

  function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'";
  }

  function editedCurl(form, extras) {
    const request = editedRequest(form, extras);
    const parts = ["curl", "-X", shellQuote(request.method), shellQuote(request.url)];
    for (const [key, value] of request.headers) {
      parts.push("-H", shellQuote(`${key}: ${value}`));
    }
    if (request.body && request.method !== "GET" && request.method !== "HEAD") {
      parts.push("--data-raw", shellQuote(request.body));
    }
    return parts.map((part, index) => (index === parts.length - 1 ? part : `${part} \\`)).join("\n");
  }

  async function sendEdited(form, extras) {
    const result = document.getElementById("edit-result");
    let request;
    try {
      request = editedRequest(form, extras);
    } catch (err) {
      if (result) {
        result.innerHTML = `<p class="warn-text">${escapeHtml(err.message || "Invalid request")}</p>`;
      }
      return;
    }
    if (result) result.innerHTML = `<p class="muted">Sending…</p>`;
    const send = form.querySelector("#edit-send");
    if (send) send.disabled = true;
    try {
      const res = await fetch("/api/flows/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          method: request.method,
          url: request.url,
          headers: Object.fromEntries(request.headers),
          body: request.body,
        }),
      });
      const data = await res.json();
      if (!result) return;
      if (!res.ok) {
        result.innerHTML = `<p class="warn-text">${escapeHtml(data.detail || "Send failed")}</p>`;
        return;
      }
      const headerText = Object.entries(data.headers || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join("\n");
      const pretty = prettyJson(data.body || "");
      const bodyHtml = pretty
        ? copyBlockHtml(pretty, "Copy JSON")
        : (data.body ? copyBlockHtml(data.body, "Copy") : "");
      const headerHtml = headerText ? `<pre class="pre">${escapeHtml(headerText)}</pre>` : "";
      result.innerHTML = `<h3>Response ${escapeHtml(String(data.status))}</h3>${headerHtml}${bodyHtml}`;
    } catch {
      if (result) result.innerHTML = `<p class="warn-text">Send failed</p>`;
    } finally {
      if (send) send.disabled = false;
    }
  }

  function renderSide(side) {
    if (!side) return `<p class="muted">—</p>`;
    return `
      ${kvTable(Object.entries(side.headers || {}).map(([k, v]) => [k, v]))}
      ${labeledBody("Body", side.body)}
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
      ${labeledBody("Request", d.request && d.request.body)}
      ${labeledBody("Response", d.response && d.response.body)}
    `;
  }

  function prettyJson(value) {
    if (typeof value !== "string") return "";
    const text = value.trim();
    if (!text || (text[0] !== "{" && text[0] !== "[")) return "";
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return "";
    }
  }

  function copyBlockHtml(text, label, title) {
    const heading = title ? `<h4 class="muted">${escapeHtml(title)}</h4>` : "";
    return `<div class="copy-block">
      <div class="copy-block-bar">${heading}<button type="button" class="btn ghost" data-copy-text data-copy-label="${escapeAttr(label)}">${escapeHtml(label)}</button></div>
      <pre class="pre">${escapeHtml(text)}</pre>
    </div>`;
  }

  function logMessageBlock(message) {
    const text = message == null ? "" : String(message);
    if (!text) return `<p class="muted">empty</p>`;
    const pretty = prettyJson(text);
    return copyBlockHtml(pretty || text, pretty ? "Copy JSON" : "Copy");
  }

  function labeledBody(title, body) {
    const heading = `<h4 class="muted">${escapeHtml(title)}</h4>`;
    if (!body || body.kind === "empty") {
      return `<div class="copy-block"><div class="copy-block-bar">${heading}</div><p class="muted">empty</p></div>`;
    }
    if (body.kind === "json" && body.pretty) return copyBlockHtml(body.pretty, "Copy JSON", title);
    if (body.raw != null && body.raw !== "") return copyBlockHtml(String(body.raw), "Copy", title);
    if (body.hex_preview) return copyBlockHtml(body.hex_preview, "Copy", title);
    return `<div class="copy-block"><div class="copy-block-bar">${heading}</div>${renderBody(body)}</div>`;
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
    return messages.map((m, i) => {
      const raw = m.content != null ? String(m.content) : (m.hex_preview || "");
      const pretty = m.is_text ? prettyJson(raw) : "";
      const text = pretty || raw;
      const meta = `#${i + 1} ${m.from_client ? "client→server" : "server→client"} · ${m.is_text ? "text" : "binary"} · ${fmtBytes(m.size_bytes)}`;
      if (!text) return `<p class="muted">${escapeHtml(meta)}</p>`;
      return copyBlockHtml(text, pretty ? "Copy JSON" : "Copy", meta);
    }).join("");
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

  function renderAppsTree(tree) {
    const host = document.getElementById("apps-tree");
    if (!host) return;
    const devices = tree.devices || [];
    const note = document.getElementById("apps-shared-note");
    const shared = devices.filter((device) => (device.apps || []).filter((app) => app.online).length > 1);
    if (note) {
      note.hidden = !shared.length;
      note.textContent = shared.length
        ? `${shared.map((d) => d.device_name).join(", ")} run more than one capture-enabled app. All apps view mixes their traffic; choose one app in Scope to isolate it.`
        : "";
    }
    const proxyHtml = renderProxyApps(tree.proxy_clients || []);
    if (!devices.length) {
      host.innerHTML = `<p class="muted">No app has registered yet.</p>${proxyHtml}`;
      return;
    }
    host.innerHTML = devices.map((device) => {
      const kind = device.is_simulator ? "Simulator" : "Device";
      const rows = (device.apps || []).map((app) => {
        const state = window.VLScope
          ? window.VLScope.stateOf(app)
          : app.superseded_by ? "reinstalled" : app.online ? "online" : "offline";
        const counts = app.counts || { flows: 0, logs: 0 };
        return `<tr>
          <th>${appBadge(app.id)}${escapeHtml(app.app_name)} <span class="muted">${escapeHtml(app.app_version || "")}</span></th>
          <td>${escapeHtml(app.bundle_id || "")}</td>
          <td class="${app.online ? "" : "muted"}">${escapeHtml(state)}${app.via_proxy ? " · also via proxy" : ""}</td>
          <td>${counts.flows} flows · ${counts.logs} logs</td>
          <td class="muted">${escapeHtml(app.sdk_version ? `SDK ${app.sdk_version}` : "SDK version not reported")}</td>
        </tr>`;
      }).join("");
      return `<h3>${escapeHtml(device.device_name)} <span class="muted">${escapeHtml(
        [kind, device.platform, device.os_version].filter(Boolean).join(" · ")
      )}</span></h3><table class="kv">${rows}</table>`;
    }).join("") + proxyHtml;
  }

  function renderProxyApps(apps) {
    if (!apps.length) return "";
    const rows = apps.map((app) => `<tr>
        <th>${escapeHtml(app.app_name)} <span class="muted">${escapeHtml(app.app_version || "")}</span></th>
        <td>${escapeHtml(app.bundle_id || "")}</td>
        <td class="${app.online ? "" : "muted"}">${app.online ? "active" : "idle"}</td>
        <td>${app.requests} requests</td>
        <td class="muted">${escapeHtml(app.last_host || "")}</td>
      </tr>`).join("");
    const peers = Array.from(new Set(apps.map((app) => app.peer)));
    return `<h3>Proxy only <span class="muted">${escapeHtml(peers.join(", "))} · no SDK linked, traffic seen through the proxy</span></h3><table class="kv">${rows}</table>`;
  }

  function appBadge(clientId) {
    return window.VLScope ? window.VLScope.badge(clientId) : "";
  }

  function appLabel(clientId) {
    return window.VLScope ? window.VLScope.label(clientId) : clientId || "";
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
    const scope = window.VLScope;
    const selected = scope ? scope.clientId() : null;
    const perApp = act.clients || {};
    const mine = selected ? perApp[selected] : null;
    const lastAt = mine ? mine.last_at : act.last_seen_at;
    const ago = lastAt ? Math.max(0, Math.round((Date.now() - Date.parse(lastAt)) / 1000)) : null;
    const appCount = Object.keys(perApp).length;
    const who = selected
      ? (scope.app(selected) || { app_name: "selected app" }).app_name
      : `${appCount || 1} app${appCount === 1 ? "" : "s"}`;
    const parts = [
      `SDK ${who}`,
      `${mine ? mine.flows : act.flows} flows`,
      `${mine ? mine.logs : act.logs} logs`,
      ago == null ? "no traffic yet" : `last ${ago}s ago`,
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
    this.logSubsystems = new Set();
    document.getElementById("log-rows").addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (tr) this.selectLog(tr.dataset.id);
    });
    document.getElementById("btn-log-pause").addEventListener("click", () => {
      this.setLogPaused(!this.logPaused);
    });
    this.appliedLogFilters = fieldValues(LOG_FILTER_IDS);
    document.getElementById("btn-log-apply").addEventListener("click", () => this.applyLogFilter());
    document.getElementById("btn-log-delete").addEventListener("click", () => this.deleteLogs());
    document.getElementById("btn-log-clear").addEventListener("click", () => {
      LOG_FILTER_IDS.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
      });
      this.applyLogFilter();
    });
    LOG_FILTER_IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.applyLogFilter();
      });
      const onEdit = () => {
        this.syncLogFilterButtons();
        this.scheduleLogFilter();
      };
      if (el.tagName === "SELECT") el.addEventListener("change", onEdit);
      else el.addEventListener("input", onEdit);
    });
    document.addEventListener("vl:scope-change", () => {
      this.reloadLogs();
      this.applyPairedAppHints();
    });
    document.addEventListener("vl:apps-change", () => {
      this.renderLogRows();
      this.applyPairedAppHints();
    });
    this.syncLogFilterButtons();
    this.reloadLogs();
    this.connectLogWs();
    const focusLog = new URLSearchParams(location.search).get("id");
    if (focusLog) this.selectLog(focusLog);
    this.applyPairedAppHints();
  };

  ViewliftNetwork.syncLogFilterButtons = function syncLogFilterButtons() {
    applyCommitPlan(
      document.getElementById("btn-log-apply"),
      document.getElementById("btn-log-clear"),
      fieldValues(LOG_FILTER_IDS),
      this.appliedLogFilters || {}
    );
  };

  /* Logs come from the SDK over ingest. Nothing is started from this page. */
  ViewliftNetwork.applyPairedAppHints = async function applyPairedAppHints() {
    const scope = window.VLScope;
    let client = scope ? scope.selectedApp() : null;
    if (!client) {
      try {
        const id = scope ? scope.clientId() : null;
        const res = await fetch(`/api/debug/app${id ? `?client_id=${encodeURIComponent(id)}` : ""}`);
        if (res.ok) client = (await res.json()).client;
      } catch (_) {
        /* several apps online or none paired: no subsystem hint */
      }
    }
    if (client && client.bundle_id) this.noteLogSubsystem(client.bundle_id);
  };

  ViewliftNetwork.logFilterQuery = function logFilterQuery() {
    const params = new URLSearchParams();
    const level = document.getElementById("lf-level").value.trim();
    const source = document.getElementById("lf-source").value.trim();
    const subsystem = document.getElementById("lf-subsystem").value.trim();
    const text = document.getElementById("lf-text").value.trim();
    // Default view is the app console only: stdout and stderr, never explicit sdk log calls.
    params.set("source", source || CONSOLE_SOURCES);
    if (level) params.set("level", level);
    if (subsystem) params.set("subsystem", subsystem);
    if (text) params.set("text", text);
    if (window.VLScope) window.VLScope.applyTo(params);
    return params.toString();
  };

  ViewliftNetwork.deleteLogs = async function deleteLogs() {
    const ok = window.confirm("Delete every stored log on this Mac? Captured requests stay until you close the last tab.");
    if (!ok) return;
    const res = await fetch("/api/logs/delete", { method: "POST" });
    if (!res.ok) {
      toast("Could not delete logs");
      return;
    }
    const data = await res.json();
    this.logs = [];
    this.logPending = [];
    this.selectedLogId = null;
    this.renderLogRows();
    const body = document.getElementById("log-detail-body");
    if (body) body.innerHTML = `<p class="muted empty">Select a log line to see its full message and linked request.</p>`;
    const title = document.getElementById("log-detail-title");
    if (title) title.textContent = "Detail";
    toast(data.deleted ? `Deleted ${data.deleted} logs` : "No logs to delete");
  };

  ViewliftNetwork.scheduleLogFilter = function scheduleLogFilter() {
    this.renderLogRows();
    clearTimeout(this.logFilterTimer);
    this.logFilterTimer = setTimeout(() => this.applyLogFilter(), 250);
  };

  ViewliftNetwork.applyLogFilter = function applyLogFilter() {
    this.appliedLogFilters = fieldValues(LOG_FILTER_IDS);
    this.syncLogFilterButtons();
    this.reloadLogs();
  };

  ViewliftNetwork.reloadLogs = async function reloadLogs() {
    const gen = (this.logLoadGen = (this.logLoadGen || 0) + 1);
    const q = this.logFilterQuery();
    const res = await fetch(`/api/logs${q ? `?${q}` : ""}`);
    if (gen !== this.logLoadGen) return;
    const data = await res.json();
    if (gen !== this.logLoadGen) return;
    this.logs = data.logs || [];
    this.absorbLogSubsystems(this.logs);
    this.logPending = [];
    this.renderLogRows();
    this.followLiveLog();
  };

  ViewliftNetwork.noteLogSubsystem = function noteLogSubsystem(subsystem) {
    const value = String(subsystem || "").trim();
    if (!value) return;
    if (!this.logSubsystems) this.logSubsystems = new Set();
    if (this.logSubsystems.has(value)) return;
    this.logSubsystems.add(value);
    this.renderLogSubsystems();
  };

  ViewliftNetwork.absorbLogSubsystems = function absorbLogSubsystems(logs) {
    if (!this.logSubsystems) this.logSubsystems = new Set();
    let added = false;
    (logs || []).forEach((log) => {
      const value = String(log.subsystem || "").trim();
      if (!value || this.logSubsystems.has(value)) return;
      this.logSubsystems.add(value);
      added = true;
    });
    if (added) this.renderLogSubsystems();
  };

  ViewliftNetwork.renderLogSubsystems = function renderLogSubsystems() {
    const el = document.getElementById("lf-subsystem");
    if (!el) return;
    const current = el.value;
    const values = [...(this.logSubsystems || [])].sort((a, b) => a.localeCompare(b));
    el.innerHTML = [`<option value="">Any</option>`]
      .concat(values.map((value) => `<option value="${escapeAttr(value)}">${escapeHtml(value)}</option>`))
      .join("");
    el.value = values.includes(current) ? current : "";
  };

  ViewliftNetwork.logMatchesFilters = function logMatchesFilters(log) {
    const level = document.getElementById("lf-level").value.trim().toLowerCase();
    const source = document.getElementById("lf-source").value.trim();
    const subsystem = document.getElementById("lf-subsystem").value.trim().toLowerCase();
    const text = document.getElementById("lf-text").value.trim().toLowerCase();
    if (window.VLScope && !window.VLScope.matches(log.client_id)) return false;
    if (level && (log.level || "").toLowerCase() !== level) return false;
    if (!CONSOLE_SOURCE_SET.has(log.source || "")) return false;
    if (source && (log.source || "") !== source) return false;
    const sub = (log.subsystem || "").toLowerCase();
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
    this.noteLogSubsystem(log.subsystem);
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
    if (this.logPending.length) {
      const fresh = this.logPending.reverse();
      this.logPending = [];
      this.logs = fresh.concat(this.logs);
      if (this.logs.length > LOG_ROW_CAP) this.logs.length = LOG_ROW_CAP;
      this.renderLogRows();
    }
    this.followLiveLog();
  };

  ViewliftNetwork.followLiveLog = function followLiveLog() {
    if (this.logPaused) return null;
    const latest = (this.logs || []).find((log) => this.logMatchesFilters(log));
    if (!latest || latest.id === this.selectedLogId) return null;
    return this.selectLog(latest.id, true, false);
  };

  ViewliftNetwork.scrollLogRow = function scrollLogRow(id) {
    const row = document.querySelector(`#log-rows tr[data-id="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  function formatLocalDateTime(value, withFraction) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const pad = (n, width = 2) => String(n).padStart(width, "0");
    const datePart = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    const timePart = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    if (!withFraction) return `${datePart} ${timePart}`;
    const offsetMin = -date.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMin);
    const offset = `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
    return `${datePart} ${timePart}.${pad(date.getMilliseconds(), 3)} ${offset}`;
  }

  ViewliftNetwork.paintLogDetail = function paintLogDetail(d) {
    const title = document.getElementById("log-detail-title");
    const body = document.getElementById("log-detail-body");
    if (!title || !body) return;
    title.textContent = d.subsystem || "Detail";
    const flowLink = d.linked_flow_id
      ? `<a href="/flows/${encodeURIComponent(d.linked_flow_id)}">Open network flow</a>`
      : (d.request_id ? `<span class="muted">No matching flow for requestId</span>` : `<span class="muted">—</span>`);
    body.innerHTML = `
      <table class="kv">
        <tr><th>Time</th><td>${escapeHtml(formatLocalDateTime(d.created_at, true))}</td></tr>
        <tr><th>Level</th><td>${escapeHtml(d.level || "")}</td></tr>
        <tr><th>Source</th><td>${escapeHtml(d.source || "")}${d.udid ? ` <span class="muted">${escapeHtml(d.udid)}</span>` : ""}</td></tr>
        <tr><th>App</th><td>${appBadge(d.client_id)}${escapeHtml(appLabel(d.client_id))}</td></tr>
        <tr><th>Subsystem</th><td>${escapeHtml(d.subsystem || "")}</td></tr>
        <tr><th>Request ID</th><td>${escapeHtml(d.request_id || "—")}</td></tr>
        <tr><th>Linked flow</th><td>${flowLink}</td></tr>
      </table>
      <h3>Message</h3>
      ${logMessageBlock(d.message)}
      <p class="muted">${escapeHtml(d.console_note || "")}</p>
    `;
  };

  ViewliftNetwork.renderLogRows = function renderLogRows() {
    const tbody = document.getElementById("log-rows");
    const visible = this.logs.filter((log) => this.logMatchesFilters(log));
    document.getElementById("log-count").textContent = String(visible.length);
    const html = visible.map((log) => `
      <tr data-id="${escapeAttr(log.id)}"${log.id === this.selectedLogId ? ' class="active"' : ""}>
        <td class="col-time" title="${escapeAttr(formatLocalDateTime(log.created_at, true))}">${escapeHtml(formatLocalDateTime(log.created_at))}</td>
        <td>${escapeHtml(log.level || "")}</td>
        <td>${appBadge(log.client_id)}${escapeHtml(log.source || "")}</td>
        <td title="${escapeAttr(log.subsystem || "")}">${escapeHtml(log.subsystem || "")}</td>
        <td class="log-message" title="${escapeAttr(log.message || "")}">${escapeHtml(log.message || "")}</td>
      </tr>`);
    tbody.innerHTML = html.join("");
  };

  ViewliftNetwork.selectLog = async function selectLog(id, fetchDetail = true, userPick = true) {
    const gen = (this.logDetailGen = (this.logDetailGen || 0) + 1);
    this.selectedLogId = id;
    document.querySelectorAll("#log-rows tr.active").forEach((tr) => tr.classList.remove("active"));
    const row = document.querySelector(`#log-rows tr[data-id="${CSS.escape(id)}"]`);
    if (row) row.classList.add("active");
    if (userPick && !this.logPaused) this.setLogPaused(true);
    this.scrollLogRow(id);
    const known = (this.logs || []).find((log) => log.id === id);
    if (known) this.paintLogDetail(known);
    if (!fetchDetail) return;
    let res;
    try {
      res = await fetch(`/api/logs/${encodeURIComponent(id)}`);
    } catch (_) {
      return;
    }
    if (this.logDetailGen !== gen || this.selectedLogId !== id) return;
    if (!res.ok) {
      const missing = document.getElementById("log-detail-body");
      if (missing) missing.innerHTML = `<p class="muted empty">Log not found</p>`;
      return;
    }
    const d = await res.json();
    if (this.logDetailGen !== gen || this.selectedLogId !== id) return;
    this.paintLogDetail(d);
  };

  ViewliftNetwork.connectLogWs = function connectLogWs() {
    if (this.logWs) {
      try { this.logWs.close(); } catch (_) {}
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
    this.logWs = ws;
    const status = document.getElementById("log-stream-status");
    ws.addEventListener("open", () => {
      if (status) status.textContent = "live";
    });
    ws.addEventListener("close", () => {
      if (status) status.textContent = "reconnecting";
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
    const tunnelStart = document.getElementById("btn-tunnel-start");
    if (tunnelStart) {
      tunnelStart.addEventListener("click", () => this.startDeviceTunnel());
      document.getElementById("btn-tunnel-stop").addEventListener("click", () => this.stopDeviceTunnel());
      document.getElementById("btn-pair-browse").addEventListener("click", () => this.browsePairing());
      document.getElementById("btn-pair-start").addEventListener("click", () => this.startPairing());
      document.getElementById("btn-pair-pin").addEventListener("click", () => this.sendPairingPin());
      this.refreshTunnelStatus();
    }
    document.getElementById("btn-test").addEventListener("click", () => this.runSetupTest());
    const rootsBtn = document.getElementById("btn-save-roots");
    if (rootsBtn) {
      rootsBtn.addEventListener("click", () => this.saveProjectRoots());
      this.loadProjectRoots();
    }
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
    if (data.proxy && data.proxy.port) document.getElementById("proxy-port").value = data.proxy.port;
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
    const vpnBox = document.getElementById("sdk-vpn");
    if (vpnBox && sdk.enabled) vpnBox.checked = !!sdk.include_vpn;
    document.getElementById("sdk-url").textContent = (sdk.receiver_urls || []).length
      ? sdk.receiver_urls.join("  ·  ")
      : sdk.receiver_url || "Not enabled";
    document.getElementById("sdk-token").textContent = sdk.pairing_token || "Not enabled";
    document.getElementById("sdk-fp").textContent = sdk.pinned_fingerprint || "Not enabled";
    document.getElementById("sdk-note").textContent = sdk.note || "";
    const activity = document.getElementById("sdk-activity");
    if (activity) activity.textContent = describeSdkActivity(sdk);
    renderAppsTree(data.apps || { devices: [] });
    const snippet = document.getElementById("sdk-snippet");
    if (snippet) {
      snippet.textContent = this.buildSdkSnippet(sdk);
    }
    if (data.last_test) {
      document.getElementById("test-msg").textContent = JSON.stringify(data.last_test, null, 2);
    }
  };

  ViewliftNetwork.buildSdkSnippet = function buildSdkSnippet(sdk) {
    return (sdk.integration && sdk.integration.snippet) || sdk.note || "";
  };

  ViewliftNetwork.enableSdkReceiver = async function enableSdkReceiver() {
    const iface = document.getElementById("sdk-ip").value;
    const port = Number(document.getElementById("sdk-port").value);
    const res = await fetch("/api/setup/sdk/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        iface,
        port,
        include_vpn: !!document.getElementById("sdk-vpn")?.checked,
      }),
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

  ViewliftNetwork.targets = async function targets() {
    if (!this._targets) {
      const res = await fetch("/api/host/targets");
      const data = await res.json().catch(() => ({}));
      this._targets = Array.isArray(data.targets) ? data.targets : [];
    }
    return this._targets;
  };

  ViewliftNetwork.fillTargetSelect = async function fillTargetSelect(select) {
    if (!select || select.dataset.filled === "1") return;
    if (select._filling) return select._filling;
    select._filling = this._fillTargetSelect(select);
    return select._filling;
  };

  ViewliftNetwork._fillTargetSelect = async function _fillTargetSelect(select) {
    const capability = select.dataset.cap || "";
    const rows = (await this.targets()).filter((row) => {
      return !capability || (row.capabilities || []).includes(capability);
    });
    const previous = select.value;
    select.replaceChildren(new Option("Selected app", ""));
    rows.forEach((row) => {
      const kind = row.kind === "device" ? "Device" : "Simulator";
      const option = new Option(
        `${row.name} · ${kind} · ${row.platform} · ${row.state || row.transport || ""}`,
        row.udid,
      );
      option.dataset.kind = row.kind;
      option.dataset.model = row.product_type || row.model || "";
      select.append(option);
    });
    select.dataset.filled = "1";
    if (previous) select.value = previous;
  };

  ViewliftNetwork.targetChoice = function targetChoice(select) {
    const option = select && select.selectedOptions ? select.selectedOptions[0] : null;
    return {
      udid: select ? select.value.trim() : "",
      kind: option && option.dataset.kind ? option.dataset.kind : "",
    };
  };

  ViewliftNetwork.hostTool = async function hostTool(tool, args) {
    const res = await fetch("/api/host/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool, args, confirm: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, detail: data.detail || "Request failed." };
    return data;
  };

  ViewliftNetwork.refreshTunnelStatus = async function refreshTunnelStatus() {
    const data = await this.hostTool("device_tunnel_status", {});
    const node = document.getElementById("tunnel-msg");
    if (node) node.textContent = data.detail || data.address || JSON.stringify(data);
  };

  ViewliftNetwork.startDeviceTunnel = async function startDeviceTunnel() {
    const ok = window.confirm(
      "Start the iOS 17+ developer tunnel? macOS will ask for an administrator password. The console itself stays unprivileged.",
    );
    if (!ok) return;
    const data = await this.hostTool("device_tunnel_start", {});
    document.getElementById("tunnel-msg").textContent = data.detail || JSON.stringify(data, null, 2);
  };

  ViewliftNetwork.stopDeviceTunnel = async function stopDeviceTunnel() {
    if (!window.confirm("Stop the developer tunnel?")) return;
    const data = await this.hostTool("device_tunnel_stop", {});
    document.getElementById("tunnel-msg").textContent = data.detail || JSON.stringify(data, null, 2);
  };

  ViewliftNetwork.browsePairing = async function browsePairing() {
    const data = await this.hostTool("device_pair_browse", {});
    const select = document.getElementById("pair-device");
    select.replaceChildren();
    (data.devices || []).forEach((row) => {
      const option = new Option(row.name || row.identifier, row.identifier || "");
      select.append(option);
    });
    document.getElementById("pair-msg").textContent = data.detail
      || `${(data.devices || []).length} device(s) advertising. On Apple TV open Settings, Remotes and Devices.`;
  };

  ViewliftNetwork.startPairing = async function startPairing() {
    const identifier = document.getElementById("pair-device").value;
    if (!identifier) return;
    if (!window.confirm("Start pairing? The Apple TV will show a PIN.")) return;
    const data = await this.hostTool("device_pair_start", { identifier });
    document.getElementById("pair-msg").textContent = data.detail || data.state || JSON.stringify(data);
  };

  ViewliftNetwork.sendPairingPin = async function sendPairingPin() {
    const pin = document.getElementById("pair-pin").value.trim();
    const data = await this.hostTool("device_pair_pin", { pin });
    document.getElementById("pair-pin").value = "";
    document.getElementById("pair-msg").textContent = data.detail || data.state || "PIN sent.";
  };

  ViewliftNetwork.renderProjectRoots = function renderProjectRoots(data) {
    document.getElementById("project-roots").value = (data.saved || []).join("\n");
    const env = (data.env || []).length ? ` Env adds: ${data.env.join(", ")}.` : "";
    document.getElementById("roots-msg").textContent =
      `${(data.active || []).length} active root(s).${env}`;
  };

  ViewliftNetwork.loadProjectRoots = async function loadProjectRoots() {
    const res = await fetch("/api/host/roots");
    if (res.ok) this.renderProjectRoots(await res.json());
  };

  ViewliftNetwork.saveProjectRoots = async function saveProjectRoots() {
    const roots = document.getElementById("project-roots").value
      .split("\n").map((line) => line.trim()).filter(Boolean);
    const res = await fetch("/api/host/roots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roots }),
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("roots-msg").textContent = data.detail || "Could not save roots";
      return;
    }
    this.renderProjectRoots(data);
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
      const pretty = prettyJson(data.body || "");
      const bodyHtml = pretty
        ? copyBlockHtml(pretty, "Copy JSON")
        : (data.body ? copyBlockHtml(data.body, "Copy") : "");
      const headerHtml = headers ? `<pre class="pre">${escapeHtml(headers)}</pre>` : "";
      block.innerHTML = `<h2>Replay ${escapeHtml(String(data.status))}</h2>${headerHtml}${bodyHtml}`;
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

    await this.fillTargetSelect(document.getElementById("deeplink-udid"));
    await this.fillTargetSelect(document.getElementById("loc-udid"));
    document.getElementById("deeplink-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const choice = this.targetChoice(document.getElementById("deeplink-udid"));
      const res = await fetch("/api/debug/deeplink", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          udid: choice.udid,
          kind: choice.kind,
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
    const choice = this.targetChoice(document.getElementById("loc-udid"));
    const body = {
      udid: choice.udid,
      kind: choice.kind,
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

  ViewliftNetwork.copyBlockHtml = copyBlockHtml;

  document.addEventListener("click", async (event) => {
    const btn = event.target && event.target.closest && event.target.closest("[data-copy-text]");
    if (!btn) return;
    const pre = btn.closest(".copy-block") && btn.closest(".copy-block").querySelector("pre");
    const text = pre ? pre.textContent : "";
    if (!text) return;
    const label = btn.dataset.copyLabel || btn.textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch {
      btn.textContent = "Copy failed";
    }
    window.setTimeout(() => { btn.textContent = label; }, 1200);
  });

  window.ViewliftNetwork = ViewliftNetwork;
})();
