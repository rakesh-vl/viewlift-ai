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
    hierarchyZoom: 0.45,
    macro: null,
    bugImage: null,
    drawing: false,
    analytics: null,
    analyticsFilter: "all",
    analyticsBeaconUrls: { user: "", player: "" },
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  const Debug = {
    async init() {
      this.bindTabs();
      this.bindCommands();
      this.bindForms();
      this.bindBugCanvas();
      document.getElementById("debug-refresh").addEventListener("click", () => this.loadClients());
      document.getElementById("debug-client").addEventListener("change", (event) => {
        state.clientId = event.target.value;
        state.client = JSON.parse(event.target.selectedOptions[0]?.dataset.client || "null");
        this.renderClientStatus();
        this.loadEvents();
      });
      document.getElementById("macro-replay").addEventListener("click", () => {
        this.sendCommand("macro.replay", state.macro || { actions: [] });
      });
      document.getElementById("ott-spm-refresh")?.addEventListener("click", () => this.loadSpm());
      document.getElementById("analytics-refresh")?.addEventListener("click", () => this.loadAnalyticsTraffic());
      document.querySelectorAll("[data-analytics-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          document.querySelectorAll("[data-analytics-filter]").forEach((item) => item.classList.remove("active"));
          button.classList.add("active");
          state.analyticsFilter = button.dataset.analyticsFilter || "all";
          this.renderAnalyticsTraffic();
        });
      });
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
        state.hierarchyZoom = Math.min(2, state.hierarchyZoom * 1.25);
        this.drawHierarchy();
      });
      document.getElementById("ui-zoom-out").addEventListener("click", () => {
        state.hierarchyZoom = Math.max(0.12, state.hierarchyZoom / 1.25);
        this.drawHierarchy();
      });
      await this.loadClients();
      await this.loadProfiles();
      await this.loadWaterfall();
      await this.loadSpm();
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
          }),
        });
        const data = await response.json();
        document.getElementById("deeplink-status").textContent = data.detail || "Failed";
      });
    },

    async loadClients() {
      const data = await (await fetch("/api/debug/clients")).json();
      const select = document.getElementById("debug-client");
      const previous = state.clientId;
      select.innerHTML = (data.clients || []).map((client) => {
        const label = `${client.app_name} · ${client.platform} ${client.os_version} · ${client.online ? "online" : "offline"}`;
        return `<option value="${escapeHtml(client.id)}" data-client="${escapeHtml(JSON.stringify(client))}">${escapeHtml(label)}</option>`;
      }).join("");
      if (previous && [...select.options].some((option) => option.value === previous)) {
        select.value = previous;
      }
      state.clientId = select.value || "";
      state.client = select.selectedOptions[0]
        ? JSON.parse(select.selectedOptions[0].dataset.client)
        : null;
      this.renderClientStatus();
      if (state.clientId) await this.loadEvents();
    },

    renderClientStatus() {
      const node = document.getElementById("debug-client-status");
      if (!state.client) {
        node.textContent = "No debug app is paired. Build and run AppCMS Debug.";
        return;
      }
      const capabilities = Object.entries(state.client.capabilities || {})
        .map(([name, status]) => `<span class="capability ${escapeHtml(status)}">${escapeHtml(name)}: ${escapeHtml(status)}</span>`)
        .join(" · ");
      node.innerHTML = capabilities;
    },

    async sendCommand(name, payload) {
      if (!state.clientId) {
        alert("No paired debug app.");
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
        if (data.type === "debug-client") this.loadClients();
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
      host.innerHTML = `<h3>Device-registered VLAnalytics clients</h3>`
        + this.kvHtml({
          configured: analytics.configured,
          clients: (clients || []).join(", "),
          user_beacon_url: beacons.user_beacon_url || "",
          player_beacon_url: beacons.player_beacon_url || "",
          clever_tap: analytics.flags?.clever_tap,
          apps_flyer: analytics.flags?.apps_flyer,
          braze: analytics.flags?.braze,
          adobe: analytics.flags?.adobe,
          comscore: analytics.flags?.comscore,
          conviva: analytics.flags?.conviva,
          airship: analytics.flags?.airship,
          data_dog: analytics.flags?.data_dog,
          facebook: analytics.flags?.facebook,
        });
      this.loadAnalyticsTraffic();
    },

    async loadAnalyticsTraffic() {
      const params = new URLSearchParams({ limit: "400" });
      if (state.analyticsBeaconUrls.user) params.set("user_beacon_url", state.analyticsBeaconUrls.user);
      if (state.analyticsBeaconUrls.player) params.set("player_beacon_url", state.analyticsBeaconUrls.player);
      try {
        state.analytics = await (await fetch(`/api/debug/analytics/traffic?${params}`)).json();
        this.renderAnalyticsTraffic();
      } catch (error) {
        const target = document.getElementById("analytics-rows");
        if (target) target.textContent = String(error);
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
      let rows = [];
      if (state.analyticsFilter === "user_beacon") rows = data.user_beacon || [];
      else if (state.analyticsFilter === "player_beacon") rows = data.player_beacon || [];
      else if (state.analyticsFilter === "clients") rows = data.clients || [];
      else {
        rows = [
          ...(data.user_beacon || []),
          ...(data.player_beacon || []),
          ...(data.beacon || []),
          ...(data.clients || []),
        ];
      }
      rows = rows.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      const target = document.getElementById("analytics-rows");
      if (!target) return;
      if (!rows.length) {
        target.innerHTML = `<p class="muted">No matching analytics traffic in captured flows. Play content / navigate, then refresh.</p>`;
        return;
      }
      target.innerHTML = rows.map((row) => {
        const params = row.params || {};
        const paramHtml = Object.keys(params).length
          ? `<dl class="ott-kv">${Object.entries(params).map(([key, value]) => {
            const text = typeof value === "object" ? JSON.stringify(value) : String(value);
            return `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(text)}</dd>`;
          }).join("")}</dl>`
          : `<p class="muted">No parsed params</p>`;
        return `<div class="stack-event">
          <h4>${escapeHtml(row.category)} · ${escapeHtml(row.event_hint || "")} · ${escapeHtml(row.method)} ${escapeHtml(row.status ?? "")}</h4>
          <p><a href="/flows/${encodeURIComponent(row.id)}">${escapeHtml(row.host)}${escapeHtml(row.path || "")}</a>
          <span class="muted"> · ${escapeHtml(row.source || "")} · ${escapeHtml(row.created_at || "")}</span></p>
          ${paramHtml}
        </div>`;
      }).join("");
      // #region agent log
      fetch('http://127.0.0.1:7666/ingest/82fbf0b7-a054-4463-8681-d068ac48fa59',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'93a126'},body:JSON.stringify({sessionId:'93a126',location:'debug.js:renderAnalyticsTraffic',message:'analytics rows rendered',data:{filter:state.analyticsFilter,rows:rows.length,total:data.total||0,counts},timestamp:Date.now(),hypothesisId:'H1-analytics'})}).catch(()=>{});
      // #endregion
    },

    async loadSpm() {
      const target = document.getElementById("ott-spm");
      if (!target) return;
      try {
        const data = await (await fetch("/api/debug/ott/spm")).json();
        if (!data.ok) {
          target.textContent = data.error || "SPM load failed";
          return;
        }
        const rows = (data.packages || []).map((pkg) => ({
          identity: pkg.identity,
          version: pkg.version || pkg.revision,
          location: pkg.location,
        }));
        target.innerHTML = `<p class="muted">${escapeHtml(data.count)} packages · ${escapeHtml(data.path)}</p>`
          + this.tableHtml(rows);
      } catch (error) {
        target.textContent = String(error);
      }
    },

    renderOtt(result) {
      const flow = result.flow || {};
      const session = result.session || {};
      const user = result.user || {};
      const parental = result.parental || {};
      const sub = result.subscription || {};
      const entitlements = result.entitlements_v2 || {};
      const entitlementCache = result.entitlement_cache || {};
      const tve = result.tve || {};
      const page = result.page || {};
      const player = result.player || {};
      const geo = result.geo || {};
      const site = result.site_features || {};
      const lists = result.user_lists || {};
      const summary = [
        ["Platform", result.platform || result.device?.platform || "—"],
        ["Flow", flow.name || (user.logged_in ? "logged_in" : "guest")],
        ["Guest", String(flow.is_guest ?? !(user.logged_in))],
        ["Logged in", String(flow.is_logged_in ?? user.logged_in ?? false)],
        ["Subscribed", String(entitlements.is_subscribed ?? sub.is_user_subscribed_effective ?? sub.is_subscribed ?? false)],
        ["SVOD", String(entitlements.has_svod ?? false)],
        ["Plan", entitlements.svod_plan_id || sub.plan_id || sub.subscription_plan || "—"],
        ["TVOD buys", String(entitlements.purchased_content_count ?? sub.purchased_content_ids_count ?? 0)],
        ["TVE", String(tve.signed_in_via_tve ?? false)],
        ["MVPD", tve.selected_provider_title || tve.mvpd_provider || "—"],
        ["Player", player.visible ? (player.title || player.content_id || "on") : "off"],
        ["Entitlement API", player.entitlement?.is_success != null ? String(player.entitlement.is_success) : `cache ${entitlementCache.bulk_key_count ?? 0}`],
        ["Country", geo.country_code || user.country_code || "—"],
        ["Auth token", session.auth_token_present ? "present" : "missing"],
        ["Parental", parental.user_enabled ? `on (${parental.rating || "no rating"})` : "off"],
        ["Page modules", String(page.module_count ?? 0)],
        ["Watchlist", String(lists.watchlist_count ?? 0)],
      ];
      document.getElementById("ott-summary").innerHTML = summary
        .map(([label, value]) => `<div class="metric"><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
        .join("");
      const sections = [
        ["Flow (guest / login / TVE)", flow],
        ["Session (tokens metadata only)", session],
        ["User", user],
        ["Geo / locale", geo],
        ["Parental", parental],
        ["Subscription / plan", sub],
        ["Entitlements v2 (SVOD/TVOD/TVE)", entitlements],
        ["Entitlement API cache (/v3/entitlement)", entitlementCache],
        ["Plan feature gates", result.plan_features || {}],
        ["TVE", tve],
        ["Profiles", result.profiles || {}],
        ["Personalization", result.personalization || {}],
        ["Devices", result.devices || {}],
        ["Watchlist / history", lists],
        ["Live player / video", player],
        ["Player prefs", result.player_prefs || {}],
        ["Content filter", result.content_filter || {}],
        ["Theme", result.theme || {}],
        ["Config", result.config || {}],
        ["Site features", site],
        ["DRM", result.drm || {}],
        ["SSO", result.sso || {}],
        ["Navigation", result.navigation || {}],
        ["Navigation catalog", result.navigation_catalog || {}],
        ["Device / app", result.device || result.bundle || {}],
        ["Analytics clients", result.analytics || {}],
        ["Page", {
          page_id: page.page_id,
          page_path: page.page_path,
          page_title: page.page_title,
          module_count: page.module_count,
          visible: page.visible,
          source: page.source,
          platform: page.platform,
          top_vc: page.top_vc,
          note: page.note,
        }],
      ];
      let html = sections.map(([title, data]) => this.ottCard(title, data)).join("");
      const modules = page.modules || [];
      if (modules.length) {
        html += `<div class="ott-card"><h3>Modules (${modules.length})</h3>`;
        html += modules.map((mod, index) => {
          const meta = mod.metadata_map || {};
          const layout = mod.layout || {};
          return `<details><summary>${escapeHtml(index + 1)}. ${escapeHtml(mod.module_type || "?")} · ${escapeHtml(mod.module_title || mod.module_id || "")}</summary>`
            + this.kvHtml({
              module_id: mod.module_id,
              content_type: mod.content_type,
              items: mod.item_count,
              layout_view: layout.view,
              layout_type: layout.type,
              block_name: layout.block_name,
              metadata_keys: Object.keys(meta).join(", "),
            })
            + (Object.keys(meta).length ? `<h4>metadataMap</h4>${this.kvHtml(meta)}` : "")
            + (layout.settings ? `<h4>layout.settings</h4><pre>${escapeHtml(JSON.stringify(layout.settings, null, 2))}</pre>` : "")
            + (layout.font_settings ? `<h4>font_settings</h4><pre>${escapeHtml(JSON.stringify(layout.font_settings, null, 2))}</pre>` : "")
            + `</details>`;
        }).join("");
        html += `</div>`;
      }
      if (player.visible && player.stream) {
        html += `<div class="ott-card"><h3>Player stream (no secrets)</h3>${this.kvHtml(player.stream)}`
          + (player.entitlement ? `<h4>Entitlement response</h4>${this.kvHtml(player.entitlement)}` : "")
          + `</div>`;
      }
      if (Array.isArray(result.frameworks) && result.frameworks.length) {
        html += `<div class="ott-card"><h3>Linked frameworks (${result.frameworks.length})</h3>`
          + `<pre>${escapeHtml(result.frameworks.join("\n"))}</pre></div>`;
      }
      document.getElementById("ott-sections").innerHTML = html;
      // #region agent log
      fetch('http://127.0.0.1:7666/ingest/82fbf0b7-a054-4463-8681-d068ac48fa59',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'93a126'},body:JSON.stringify({sessionId:'93a126',location:'debug.js:renderOtt',message:'ott snapshot rendered',data:{flow:flow.name,guest:!!flow.is_guest,logged_in:!!flow.is_logged_in,modules:modules.length,subscribed:!!(sub.is_user_subscribed_effective||sub.is_subscribed),has_svod:!!entitlements.has_svod,player_visible:!!player.visible,entitlement_cache:entitlementCache.bulk_key_count||0,tve:!!tve.signed_in_via_tve,auth_present:!!session.auth_token_present,pin_exposed:parental.pin_value_exposed===true,token_exposed:session.token_values_exposed===true,platform:result.platform||null},timestamp:Date.now(),hypothesisId:'H-expand'})}).catch(()=>{});
      // #endregion
    },

    ottCard(title, data) {
      return `<div class="ott-card"><h3>${escapeHtml(title)}</h3>${this.kvHtml(data)}</div>`;
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
      // #region agent log
      const withLine = events.reduce((n, ev) => n + (ev.frames || []).filter((f) => f.line != null).length, 0);
      fetch('http://127.0.0.1:7666/ingest/82fbf0b7-a054-4463-8681-d068ac48fa59',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'93a126'},body:JSON.stringify({sessionId:'93a126',location:'debug.js:renderStacks',message:'stacks rendered',data:{name,events:events.length,withLine},timestamp:Date.now(),hypothesisId:'H1/H3'})}).catch(()=>{});
      // #endregion
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
      state.collapsed = new Set(this.hierarchyNodes()
        .filter(({ node, depth }) => depth >= 2 && (node.children || []).length)
        .map(({ node }) => String(node.id)));
      this.drawHierarchy();
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
        return `<div class="tree-row${active}" data-view-id="${escapeHtml(node.id)}" style="padding-left:${depth * 14 + 4}px"><button type="button" class="tree-toggle" data-toggle="${escapeHtml(node.id)}" ${hasChildren ? "" : "disabled"}>${marker}</button><span>${escapeHtml(node.class)} ${Math.round(node.width)}×${Math.round(node.height)}</span></div>`;
      }).join("");
      tree.scrollTop = scroll;
      const byId = new Map(visible.map(({ node }) => [String(node.id), node]));
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
        if (!row) return;
        state.selectedView = byId.get(row.dataset.viewId);
        this.drawHierarchy();
        document.getElementById("ui-properties").innerHTML =
          `<button class="btn ghost" id="ui-highlight">Highlight</button><pre>${escapeHtml(JSON.stringify(state.selectedView, null, 2))}</pre>`;
        document.getElementById("ui-highlight").onclick = () =>
          this.sendCommand("ui.highlight", { id: row.dataset.viewId });
      };
      const canvas = document.getElementById("ui-3d");
      const ctx = canvas.getContext("2d");
      const scale = state.hierarchyZoom;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const frame = (node, depth) => ({
        x: node.x * scale + depth * 6,
        y: node.y * scale - depth * 4,
        w: Math.max(2, node.width * scale),
        h: Math.max(2, node.height * scale),
      });
      visible.slice(0, 800).forEach(({ node, depth }) => {
        if (String(node.id) === selectedId) return;
        const box = frame(node, depth);
        ctx.strokeStyle = `hsl(${(depth * 37) % 360} 60% 60%)`;
        ctx.lineWidth = 1;
        ctx.strokeRect(box.x, box.y, box.w, box.h);
      });
      const selected = visible.find(({ node }) => String(node.id) === selectedId);
      if (selected) {
        const box = frame(selected.node, selected.depth);
        ctx.fillStyle = "rgba(255, 176, 32, 0.28)";
        ctx.fillRect(box.x, box.y, box.w, box.h);
        ctx.strokeStyle = "#ffb020";
        ctx.lineWidth = 3;
        ctx.strokeRect(box.x, box.y, box.w, box.h);
        ctx.lineWidth = 1;
        ctx.font = "12px sans-serif";
        ctx.fillText(
          `${selected.node.class} ${Math.round(selected.node.width)}×${Math.round(selected.node.height)}`,
          box.x + 4,
          Math.max(14, box.y - 6)
        );
      }
    },

    renderScreenshot(result, targetId) {
      document.getElementById(targetId).innerHTML =
        `<img alt="App screenshot" style="max-width:100%" src="data:${escapeHtml(result.mime)};base64,${result.base64}">`;
    },

    renderStorage(name, result) {
      const target = document.getElementById("storage-result");
      if (result.values) {
        const rows = Object.entries(result.values).map(([key, value]) => ({ key, value: JSON.stringify(value) }));
        return this.renderTable("storage-result", rows);
      }
      for (const key of ["items", "files", "rows"]) {
        if (Array.isArray(result[key])) return this.renderTable("storage-result", result[key]);
      }
      target.textContent = JSON.stringify(result, null, 2);
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
        assets: "assets-result", accessibility: "accessibility-result", storage: "storage-result",
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
      const flows = (await (await fetch("/api/flows?limit=80")).json()).flows || [];
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
