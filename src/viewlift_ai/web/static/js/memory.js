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

  const state = {
    send: null,
    live: false,
    lastLive: 0,
    peak: 0,
    leaks: new Map(),
    feed: [],
    heapFilter: "",
    heap: null,
    timeline: null,
    graph: null,
    players: new Map(),
    staleTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  const Memory = {
    init({ send }) {
      state.send = send;
      state.timeline = new LiveTimeline($("memory-timeline"), $("memory-timeline-tip"));
      state.graph = new ForceGraph($("memory-graph"), {
        height: 520,
        tooltip: $("memory-graph-tip"),
        onSelect: (node, links) => this.renderNode(node, links),
      });
      $("memory-live-toggle").addEventListener("click", () => {
        state.send(state.live ? "memory.live_stop" : "memory.live_start", {});
      });
      $("memory-heap-filter").addEventListener("input", (event) => {
        state.heapFilter = event.target.value.trim().toLowerCase();
        this.renderHeap();
      });
      $("memory-leaks").addEventListener("click", (event) => this.onLeakClick(event));
      state.staleTimer = setInterval(() => this.renderLiveState(), 2000);
      this.reset();
    },

    reset() {
      state.live = false;
      state.lastLive = 0;
      state.peak = 0;
      state.leaks.clear();
      state.feed = [];
      state.heap = null;
      state.players.forEach((player) => player.pause());
      state.players.clear();
      state.timeline?.reset();
      state.graph?.setData([], []);
      ["memory-feed", "memory-classes", "memory-leaks", "memory-heap", "memory-node", "memory-status"]
        .forEach((id) => { if ($(id)) $(id).innerHTML = ""; });
      this.renderMetrics({});
      this.renderLeaksEmpty();
      this.renderLiveState();
    },

    onEvent(kind, payload) {
      if (kind === "live") this.onLive(payload || {});
      if (kind === "leak") this.addLeak(payload || {}, true);
    },

    onResult(name, result) {
      if (name === "memory.live_start" || name === "memory.live_stop") {
        state.live = Boolean(result.live);
        if (!state.live) state.lastLive = 0;
        this.status(state.live
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
      if (name === "memory.snapshot") {
        state.live = Boolean(result.live);
        this.renderMetrics({ footprint_bytes: result.footprint_bytes, tracked_count: result.tracked_count,
          leak_count: result.leak_count });
        this.renderLiveState();
        this.status(`${result.tracked_count || 0} tracked objects, ${result.leak_count || 0} suspected leaks.`);
      }
    },

    status(text, warn = false) {
      const node = $("memory-status");
      if (node) node.innerHTML = text ? `<p class="${warn ? "warn-text" : "muted"}">${escapeHtml(text)}</p>` : "";
    },

    showError(message) {
      this.status(message, true);
    },

    onLive(sample) {
      state.live = true;
      state.lastLive = Date.now();
      state.peak = Math.max(state.peak, sample.footprint_bytes || 0);
      state.timeline.push(sample, sample.activity || []);
      this.renderMetrics(sample);
      this.renderClasses(sample.classes || []);
      this.pushFeed(sample.activity || []);
      this.renderLiveState();
    },

    renderLiveState() {
      const button = $("memory-live-toggle");
      const pill = $("memory-live-pill");
      if (!button || !pill) return;
      const streaming = state.live && Date.now() - state.lastLive < 5000;
      button.textContent = state.live ? "Stop live tracking" : "Start live tracking";
      button.setAttribute("aria-pressed", String(state.live));
      pill.className = `memory-pill ${streaming ? "is-live" : state.live ? "is-waiting" : ""}`;
      pill.textContent = streaming ? "Live · recording replay" : state.live ? "Waiting for the app" : "Not tracking";
    },

    renderMetrics(sample) {
      const openLeaks = [...state.leaks.values()].filter((leak) => leak.status === "alive").length;
      const rows = [
        ["Footprint", sample.footprint_bytes ? formatBytes(sample.footprint_bytes) : "–"],
        ["Peak this session", state.peak ? formatBytes(state.peak) : "–"],
        ["Live tracked objects", sample.tracked_count != null ? String(sample.tracked_count) : "–"],
        ["Open leaks", String(sample.leak_count != null ? sample.leak_count : openLeaks)],
      ];
      $("memory-metrics").innerHTML = rows
        .map(([label, value]) => `<div class="metric"><span class="muted">${label}</span><strong>${escapeHtml(value)}</strong></div>`)
        .join("");
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
          return `<tr class="${tone}"><td class="mono">${escapeHtml(row.class)}</td><td class="mono">${row.count}</td>`
            + `<td class="mono heap-delta">${heap.baseline ? "" : delta > 0 ? `+${delta}` : String(delta)}</td>`
            + `<td class="mono">${escapeHtml(formatBytes(row.bytes))}</td>`
            + `<td class="heap-bar-cell"><span class="heap-bar" style="--share:${row.count / max}"></span></td></tr>`;
        }).join("")
        + `</tbody></table><p class="muted">${escapeHtml(heap.note || "")}</p>`;
    },
  };

  window.ViewliftMemory = Memory;
})();
