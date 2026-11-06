// Canvas visuals for the Memory tab: force-directed object graph, leak replay player, live timeline.
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  // Canvases sit on the --scope surface, which stays dark in both themes, so ink colors are fixed.
  const palette = () => ({
    text: "#e8e4dc",
    strong: "#f7f4ef",
    muted: "#9b978f",
    faint: "#6f6c66",
    line: css("--scope-line", "#23282e"),
    accent: css("--accent", "#f2a93b"),
    ok: css("--ok", "#6fcf97"),
    err: css("--err", "#f07a6e"),
    blue: css("--m-get", "#7db4e8"),
    violet: css("--m-patch", "#b9a3e3"),
    mono: css("--font-mono", "monospace"),
  });

  /** Sizes a canvas to its CSS box at device pixel ratio and returns a context in CSS pixels. */
  function fitCanvas(canvas, height) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || Number(canvas.getAttribute("width")) || 600;
    const tall = height || canvas.clientHeight || Number(canvas.getAttribute("height")) || 300;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(tall * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(tall * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height: tall };
  }

  /** Edges whose ends share a strongly connected component, that is, edges on a reference cycle. */
  function cycleEdges(nodes, edges) {
    const adjacency = new Map(nodes.map((node) => [node.id, []]));
    edges.forEach((edge) => adjacency.get(edge.from)?.push(edge.to));
    const index = new Map(), low = new Map(), onStack = new Set(), component = new Map();
    const stack = [];
    let counter = 0, group = 0;
    const visit = (id) => {
      index.set(id, counter); low.set(id, counter); counter += 1;
      stack.push(id); onStack.add(id);
      for (const next of adjacency.get(id) || []) {
        if (!index.has(next)) { visit(next); low.set(id, Math.min(low.get(id), low.get(next))); }
        else if (onStack.has(next)) low.set(id, Math.min(low.get(id), index.get(next)));
      }
      if (low.get(id) === index.get(id)) {
        const members = [];
        let item;
        do { item = stack.pop(); onStack.delete(item); members.push(item); } while (item !== id);
        members.forEach((member) => component.set(member, members.length > 1 ? group : -1));
        group += 1;
      }
    };
    nodes.forEach((node) => { if (!index.has(node.id)) visit(node.id); });
    return new Set(edges
      .filter((edge) => edge.cycle || (component.get(edge.from) >= 0 && component.get(edge.from) === component.get(edge.to)))
      .map((edge) => `${edge.from}>${edge.to}`));
  }

  /** Animated force-directed graph. Leaked nodes pulse, cycle edges march. */
  class ForceGraph {
    constructor(canvas, { height = 480, onSelect = null, tooltip = null } = {}) {
      this.canvas = canvas;
      this.height = height;
      this.onSelect = onSelect;
      this.tooltip = tooltip;
      this.nodes = [];
      this.edges = [];
      this.byId = new Map();
      this.cycles = new Set();
      this.hover = null;
      this.selected = null;
      this.drag = null;
      this.alpha = 0;
      this.frame = 0;
      this.running = false;
      this.bindPointer();
    }

    setData(nodes, edges) {
      const { width, height } = fitCanvas(this.canvas, this.height);
      const previous = this.byId;
      this.byId = new Map();
      this.nodes = nodes.map((raw, index) => {
        const old = previous.get(raw.id);
        const angle = index / Math.max(1, nodes.length) * Math.PI * 2;
        const node = old ? Object.assign(old, { data: raw }) : {
          id: raw.id,
          data: raw,
          x: width / 2 + Math.cos(angle) * Math.min(width, height) * 0.3 + (Math.random() - 0.5) * 20,
          y: height / 2 + Math.sin(angle) * Math.min(width, height) * 0.3 + (Math.random() - 0.5) * 20,
          vx: 0,
          vy: 0,
          born: performance.now(),
        };
        this.byId.set(raw.id, node);
        return node;
      });
      this.edges = edges.filter((edge) => this.byId.has(edge.from) && this.byId.has(edge.to));
      this.cycles = cycleEdges(nodes, this.edges);
      const degree = new Map();
      this.edges.forEach((edge) => {
        degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
        degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
      });
      this.nodes.forEach((node) => { node.radius = 6 + Math.min(10, Math.sqrt(degree.get(node.id) || 0) * 2.5); });
      if (this.selected && !this.byId.has(this.selected.id)) this.selected = null;
      this.alpha = 1;
      this.start();
    }

    hasPulse() {
      return !reduceMotion && (this.cycles.size > 0 || this.nodes.some((node) => node.data.leaked || node.data.root));
    }

    start() {
      if (this.running) return;
      this.running = true;
      const loop = () => {
        if (!this.canvas.isConnected) { this.running = false; return; }
        const visible = this.canvas.offsetParent !== null;
        if (visible) {
          if (this.alpha > 0.01) this.tick();
          this.draw();
        }
        this.frame += 1;
        if (this.alpha > 0.01 || this.hasPulse() || this.drag) {
          requestAnimationFrame(loop);
        } else {
          this.running = false;
        }
      };
      requestAnimationFrame(loop);
    }

    tick() {
      const { width, height } = { width: this.canvas.clientWidth || 600, height: this.height };
      const nodes = this.nodes;
      const strength = reduceMotion ? 1 : this.alpha;
      for (let i = 0; i < nodes.length; i += 1) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j += 1) {
          const b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 < 1) { dx = Math.random(); dy = Math.random(); dist2 = 1; }
          const force = 5200 / dist2 * strength;
          const dist = Math.sqrt(dist2);
          a.vx += dx / dist * force; a.vy += dy / dist * force;
          b.vx -= dx / dist * force; b.vy -= dy / dist * force;
        }
      }
      for (const edge of this.edges) {
        const a = this.byId.get(edge.from), b = this.byId.get(edge.to);
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = (dist - 130) * 0.02 * strength;
        a.vx += dx / dist * pull; a.vy += dy / dist * pull;
        b.vx -= dx / dist * pull; b.vy -= dy / dist * pull;
      }
      for (const node of nodes) {
        node.vx += (width / 2 - node.x) * 0.004 * strength;
        node.vy += (height / 2 - node.y) * 0.006 * strength;
        if (node === this.drag?.node) { node.vx = 0; node.vy = 0; continue; }
        node.vx *= 0.82; node.vy *= 0.82;
        node.x = Math.max(node.radius + 2, Math.min(width - node.radius - 2, node.x + node.vx));
        node.y = Math.max(node.radius + 2, Math.min(height - node.radius - 2, node.y + node.vy));
      }
      this.alpha *= reduceMotion ? 0.9 : 0.985;
    }

    color(node, colors) {
      if (node.data.leaked || node.data.root) return colors.err;
      if (node.data.tracked) return colors.accent;
      if (node.data.system) return colors.faint;
      return colors.blue;
    }

    neighbors(node) {
      const ids = new Set([node.id]);
      this.edges.forEach((edge) => {
        if (edge.from === node.id) ids.add(edge.to);
        if (edge.to === node.id) ids.add(edge.from);
      });
      return ids;
    }

    draw() {
      const { ctx, width, height } = fitCanvas(this.canvas, this.height);
      const colors = palette();
      ctx.clearRect(0, 0, width, height);
      const focus = this.hover || this.selected;
      const near = focus ? this.neighbors(focus) : null;
      const now = performance.now();
      ctx.lineWidth = 1.2;
      for (const edge of this.edges) {
        const a = this.byId.get(edge.from), b = this.byId.get(edge.to);
        const onCycle = this.cycles.has(`${edge.from}>${edge.to}`);
        const dim = near && !(near.has(a.id) && near.has(b.id));
        ctx.globalAlpha = dim ? 0.12 : onCycle ? 0.95 : 0.5;
        ctx.strokeStyle = onCycle ? colors.err : colors.muted;
        ctx.setLineDash(onCycle ? [6, 4] : []);
        ctx.lineDashOffset = onCycle && !reduceMotion ? -this.frame * 0.6 : 0;
        this.arrow(ctx, a, b);
        if (!dim && (onCycle || (focus && near?.has(a.id) && near?.has(b.id)))) {
          ctx.setLineDash([]);
          ctx.fillStyle = onCycle ? colors.err : colors.text;
          ctx.font = `10px ${colors.mono}`;
          const dist = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y));
          const offX = -(b.y - a.y) / dist * 10, offY = (b.x - a.x) / dist * 10;
          ctx.textAlign = "center";
          ctx.fillText(edge.label || "", (a.x + b.x) / 2 + offX, (a.y + b.y) / 2 + offY);
          ctx.textAlign = "start";
        }
      }
      ctx.setLineDash([]);
      for (const node of this.nodes) {
        const dim = near && !near.has(node.id);
        const grow = reduceMotion ? 1 : Math.min(1, (now - node.born) / 350);
        const radius = node.radius * (0.4 + 0.6 * grow);
        const color = this.color(node, colors);
        ctx.globalAlpha = dim ? 0.2 : 1;
        if ((node.data.leaked || node.data.root) && !reduceMotion) {
          const phase = (now % 1600) / 1600;
          ctx.strokeStyle = colors.err;
          ctx.globalAlpha = (dim ? 0.1 : 0.6) * (1 - phase);
          ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(node.x, node.y, radius + 4 + phase * 14, 0, Math.PI * 2); ctx.stroke();
          ctx.globalAlpha = dim ? 0.2 : 1;
        }
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, Math.PI * 2); ctx.fill();
        if (node === this.selected) {
          ctx.strokeStyle = colors.strong; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(node.x, node.y, radius + 3, 0, Math.PI * 2); ctx.stroke();
        }
        const labelled = this.nodes.length <= 36 || node.data.leaked || node.data.root || node.data.tracked
          || (near && near.has(node.id));
        if (labelled && !dim) {
          ctx.fillStyle = node.data.system ? colors.muted : colors.strong;
          ctx.font = `${node.data.leaked || node.data.root ? "600 " : ""}11px ${colors.mono}`;
          ctx.fillText(node.data.class || "Object", node.x + radius + 4, node.y + 4);
        }
      }
      ctx.globalAlpha = 1;
    }

    arrow(ctx, a, b) {
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(1, Math.hypot(dx, dy));
      const ux = dx / dist, uy = dy / dist;
      const endX = b.x - ux * (b.radius + 3), endY = b.y - uy * (b.radius + 3);
      ctx.beginPath(); ctx.moveTo(a.x + ux * a.radius, a.y + uy * a.radius); ctx.lineTo(endX, endY); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(endX, endY);
      ctx.lineTo(endX - ux * 7 - uy * 4, endY - uy * 7 + ux * 4);
      ctx.lineTo(endX - ux * 7 + uy * 4, endY - uy * 7 - ux * 4);
      ctx.closePath();
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fill();
    }

    nodeAt(event) {
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      let best = null, bestDist = Infinity;
      for (const node of this.nodes) {
        const dist = Math.hypot(node.x - x, node.y - y);
        if (dist < node.radius + 6 && dist < bestDist) { best = node; bestDist = dist; }
      }
      return { node: best, x, y };
    }

    bindPointer() {
      const canvas = this.canvas;
      canvas.addEventListener("pointerdown", (event) => {
        const hit = this.nodeAt(event);
        if (!hit.node) return;
        this.drag = { node: hit.node, moved: false };
        canvas.setPointerCapture(event.pointerId);
        this.start();
      });
      canvas.addEventListener("pointermove", (event) => {
        const hit = this.nodeAt(event);
        if (this.drag) {
          this.drag.moved = true;
          this.drag.node.x = hit.x; this.drag.node.y = hit.y;
          this.alpha = Math.max(this.alpha, 0.3);
          return;
        }
        if (hit.node !== this.hover) {
          this.hover = hit.node;
          canvas.style.cursor = hit.node ? "pointer" : "default";
          this.start();
          if (this.alpha <= 0.01) this.draw();
        }
        this.showTip(hit);
      });
      canvas.addEventListener("pointerleave", () => {
        this.hover = null;
        if (this.tooltip) this.tooltip.hidden = true;
        this.draw();
      });
      canvas.addEventListener("pointerup", () => {
        if (this.drag && !this.drag.moved) this.select(this.drag.node);
        this.drag = null;
      });
    }

    select(node) {
      this.selected = node;
      this.draw();
      if (this.onSelect) this.onSelect(node?.data || null, node ? this.linksOf(node) : null);
    }

    linksOf(node) {
      return {
        outgoing: this.edges.filter((edge) => edge.from === node.id)
          .map((edge) => ({ label: edge.label, class: this.byId.get(edge.to)?.data.class, cycle: this.cycles.has(`${edge.from}>${edge.to}`) })),
        incoming: this.edges.filter((edge) => edge.to === node.id)
          .map((edge) => ({ label: edge.label, class: this.byId.get(edge.from)?.data.class, cycle: this.cycles.has(`${edge.from}>${edge.to}`) })),
      };
    }

    showTip(hit) {
      if (!this.tooltip) return;
      if (!hit.node) { this.tooltip.hidden = true; return; }
      const data = hit.node.data;
      const bits = [data.class, data.context, data.screen ? `on ${data.screen}` : "",
        data.age_seconds != null ? `${Math.round(data.age_seconds)} s old` : "",
        data.leaked || data.root ? "leaked" : ""].filter(Boolean);
      this.tooltip.textContent = bits.join(" · ");
      this.tooltip.style.left = `${Math.min(hit.x + 14, this.canvas.clientWidth - 220)}px`;
      this.tooltip.style.top = `${hit.y + 12}px`;
      this.tooltip.hidden = false;
    }
  }

  /** Plays a leak replay clip with touch overlays and exports it as a WebM video. */
  class ReplayPlayer {
    constructor(host, replay, { title = "", onDone = null } = {}) {
      this.frames = replay.frames || [];
      this.interval = Number(replay.frame_interval_ms) || 500;
      this.title = title;
      this.index = 0;
      this.timer = null;
      this.onDone = onDone;
      this.images = this.frames.map((frame) => {
        const image = new Image();
        const raw = String(frame.base64 || "").replace(/[^A-Za-z0-9+/=]/g, "");
        image.src = `data:image/jpeg;base64,${raw}`;
        return image;
      });
      this.build(host);
      Promise.all(this.images.map((image) => image.decode().catch(() => null))).then(() => this.show(this.frames.length - 1));
    }

    build(host) {
      const first = this.frames[0] || { width: 200, height: 400 };
      host.innerHTML = "";
      this.canvas = document.createElement("canvas");
      this.canvas.className = "replay-canvas";
      this.canvas.width = first.width;
      this.canvas.height = first.height;
      this.canvas.setAttribute("role", "img");
      this.canvas.setAttribute("aria-label", `Screen replay before the leak of ${this.title}`);
      const controls = document.createElement("div");
      controls.className = "replay-controls";
      controls.innerHTML = `<button type="button" class="btn ghost" data-act="play" aria-label="Play replay">Play</button>`
        + `<input type="range" min="0" max="${Math.max(0, this.frames.length - 1)}" value="${Math.max(0, this.frames.length - 1)}" aria-label="Replay position">`
        + `<span class="replay-time mono"></span>`
        + `<button type="button" class="btn ghost" data-act="video">Download video</button>`;
      host.append(this.canvas, controls);
      this.playButton = controls.querySelector('[data-act="play"]');
      this.slider = controls.querySelector("input");
      this.timeLabel = controls.querySelector(".replay-time");
      this.videoButton = controls.querySelector('[data-act="video"]');
      if (!window.MediaRecorder || !this.canvas.captureStream) {
        this.videoButton.disabled = true;
        this.videoButton.title = "This browser cannot record canvas video";
      }
      this.playButton.addEventListener("click", () => (this.timer ? this.pause() : this.play()));
      this.slider.addEventListener("input", () => { this.pause(); this.show(Number(this.slider.value)); });
      this.videoButton.addEventListener("click", () => this.exportVideo());
    }

    play() {
      if (this.index >= this.frames.length - 1) this.index = -1;
      this.playButton.textContent = "Pause";
      this.timer = setInterval(() => {
        if (this.index >= this.frames.length - 1) { this.pause(); return; }
        this.show(this.index + 1);
      }, this.interval);
    }

    pause() {
      clearInterval(this.timer);
      this.timer = null;
      this.playButton.textContent = "Play";
    }

    show(index) {
      if (!this.frames.length) return;
      this.index = Math.max(0, Math.min(this.frames.length - 1, index));
      this.slider.value = String(this.index);
      this.paint(this.canvas.getContext("2d"), this.index);
      const frame = this.frames[this.index];
      const ago = Number(frame.ago_ms || 0) / 1000;
      this.timeLabel.textContent = `${ago.toFixed(1)} s before · ${frame.screen_class || ""}`;
    }

    paint(ctx, index) {
      const colors = palette();
      const frame = this.frames[index];
      const width = this.canvas.width, height = this.canvas.height;
      ctx.clearRect(0, 0, width, height);
      const image = this.images[index];
      if (image?.complete && image.naturalWidth) ctx.drawImage(image, 0, 0, width, height);
      for (let back = 2; back >= 0; back -= 1) {
        const touches = this.frames[index - back]?.touches || [];
        const fade = 1 - back * 0.35;
        let start = null;
        for (const touch of touches) {
          const x = touch.x * width, y = touch.y * height;
          if (touch.phase === "began") {
            start = { x, y };
            ctx.globalAlpha = fade;
            ctx.fillStyle = "rgba(242, 169, 59, 0.35)";
            ctx.strokeStyle = colors.accent;
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(x, y, 11 + back * 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
          } else if (start && Math.hypot(x - start.x, y - start.y) > 8) {
            ctx.globalAlpha = fade;
            ctx.strokeStyle = colors.accent;
            ctx.lineWidth = 3;
            ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.lineTo(x, y); ctx.stroke();
            start = null;
          }
        }
      }
      ctx.globalAlpha = 1;
      if (index === this.frames.length - 1) {
        ctx.strokeStyle = colors.err;
        ctx.lineWidth = 4;
        ctx.strokeRect(2, 2, width - 4, height - 4);
        ctx.fillStyle = "rgba(16, 18, 20, 0.82)";
        ctx.fillRect(0, height - 26, width, 26);
        ctx.fillStyle = colors.err;
        ctx.font = `600 11px ${colors.mono}`;
        ctx.fillText(`Leak: ${this.title} still alive`, 8, height - 9);
      }
    }

    async exportVideo() {
      this.pause();
      this.videoButton.disabled = true;
      this.videoButton.textContent = "Recording…";
      const stream = this.canvas.captureStream(Math.max(2, Math.round(1000 / this.interval)));
      const type = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"]
        .find((item) => MediaRecorder.isTypeSupported(item)) || "";
      const recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
      const chunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      const finished = new Promise((resolve) => { recorder.onstop = resolve; });
      recorder.start();
      for (let index = 0; index < this.frames.length; index += 1) {
        this.show(index);
        await new Promise((resolve) => setTimeout(resolve, this.interval));
      }
      await new Promise((resolve) => setTimeout(resolve, this.interval * 2));
      recorder.stop();
      await finished;
      const extension = type.includes("mp4") ? "mp4" : "webm";
      const blob = new Blob(chunks, { type: type || "video/webm" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `leak-${this.title.replace(/[^A-Za-z0-9_-]+/g, "_") || "replay"}.${extension}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 4000);
      this.videoButton.disabled = false;
      this.videoButton.textContent = "Download video";
    }
  }

  /** Scrolling footprint chart with markers for loads, releases, and leaks. */
  class LiveTimeline {
    constructor(canvas, tooltip) {
      this.canvas = canvas;
      this.tooltip = tooltip;
      this.samples = [];
      this.marks = [];
      this.windowSeconds = 120;
      this.running = false;
      this.pointer = null;
      canvas.addEventListener("pointermove", (event) => {
        const rect = canvas.getBoundingClientRect();
        this.pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        this.start();
      });
      canvas.addEventListener("pointerleave", () => { this.pointer = null; this.tooltip.hidden = true; });
    }

    reset() {
      this.samples = [];
      this.marks = [];
      this.draw();
    }

    push(sample, activity) {
      this.samples.push({ t: sample.timestamp * 1000, footprint: sample.footprint_bytes || 0, tracked: sample.tracked_count || 0 });
      for (const item of activity || []) this.marks.push({ t: item.timestamp * 1000, kind: item.kind, cls: item.class });
      const cutoff = Date.now() - this.windowSeconds * 1000 * 1.5;
      this.samples = this.samples.filter((item) => item.t >= cutoff);
      this.marks = this.marks.filter((item) => item.t >= cutoff).slice(-400);
      this.start();
    }

    start() {
      if (this.running) return;
      this.running = true;
      const loop = () => {
        if (this.canvas.offsetParent !== null) this.draw();
        const fresh = this.samples.length && Date.now() - this.samples.at(-1).t < 5000;
        if (fresh && !reduceMotion) requestAnimationFrame(loop);
        else { this.running = false; this.draw(); }
      };
      requestAnimationFrame(loop);
    }

    draw() {
      const { ctx, width, height } = fitCanvas(this.canvas, 220);
      const colors = palette();
      ctx.clearRect(0, 0, width, height);
      const pad = { top: 14, bottom: 26, left: 8, right: 64 };
      const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
      const end = this.samples.length ? Math.max(Date.now() - 1000, this.samples.at(-1).t) : Date.now();
      const begin = end - this.windowSeconds * 1000;
      const xOf = (t) => pad.left + (t - begin) / (end - begin) * plotW;
      const visible = this.samples.filter((item) => item.t >= begin - 2000);
      ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
      ctx.fillStyle = colors.faint; ctx.font = `10px ${colors.mono}`;
      for (let step = 0; step <= 4; step += 1) {
        const y = pad.top + plotH * step / 4;
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + plotW, y); ctx.stroke();
      }
      for (let s = 0; s <= this.windowSeconds; s += 30) {
        const x = xOf(end - s * 1000);
        ctx.fillText(s ? `-${s}s` : "now", x - 10, height - 8);
      }
      if (!visible.length) {
        ctx.fillStyle = colors.muted; ctx.font = `12px ${colors.mono}`;
        ctx.fillText("Start live tracking to stream memory from the app.", pad.left + 12, pad.top + plotH / 2);
        return;
      }
      const values = visible.map((item) => item.footprint);
      const max = Math.max(...values) * 1.08, min = Math.min(...values) * 0.92;
      const yOf = (value) => pad.top + plotH - (value - min) / Math.max(1, max - min) * plotH;
      const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
      gradient.addColorStop(0, "rgba(111, 207, 151, 0.35)");
      gradient.addColorStop(1, "rgba(111, 207, 151, 0)");
      ctx.beginPath();
      visible.forEach((item, index) => (index ? ctx.lineTo(xOf(item.t), yOf(item.footprint)) : ctx.moveTo(xOf(item.t), yOf(item.footprint))));
      ctx.strokeStyle = colors.ok; ctx.lineWidth = 2; ctx.stroke();
      ctx.lineTo(xOf(visible.at(-1).t), pad.top + plotH); ctx.lineTo(xOf(visible[0].t), pad.top + plotH); ctx.closePath();
      ctx.fillStyle = gradient; ctx.fill();
      const trackedMax = Math.max(...visible.map((item) => item.tracked), 1);
      ctx.beginPath();
      visible.forEach((item, index) => {
        const y = pad.top + plotH - item.tracked / trackedMax * plotH * 0.9;
        index ? ctx.lineTo(xOf(item.t), y) : ctx.moveTo(xOf(item.t), y);
      });
      ctx.strokeStyle = colors.violet; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
      const last = visible.at(-1);
      ctx.fillStyle = colors.ok; ctx.font = `600 11px ${colors.mono}`;
      ctx.fillText(formatBytes(last.footprint), pad.left + plotW + 6, yOf(last.footprint) + 4);
      if (!reduceMotion) {
        const pulse = (Date.now() % 1200) / 1200;
        ctx.globalAlpha = 1 - pulse;
        ctx.beginPath(); ctx.arc(xOf(last.t), yOf(last.footprint), 3 + pulse * 8, 0, Math.PI * 2); ctx.strokeStyle = colors.ok; ctx.stroke();
        ctx.globalAlpha = 1;
      }
      this.drawMarks(ctx, xOf, begin, pad, plotH, colors);
      this.drawHover(ctx, visible, xOf, yOf, pad, plotH, colors);
    }

    drawMarks(ctx, xOf, begin, pad, plotH, colors) {
      const base = pad.top + plotH;
      for (const mark of this.marks) {
        if (mark.t < begin) continue;
        const x = xOf(mark.t);
        if (mark.kind === "leak") {
          ctx.strokeStyle = colors.err; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, base); ctx.stroke();
          ctx.fillStyle = colors.err;
          ctx.beginPath(); ctx.arc(x, pad.top + 4, 4, 0, Math.PI * 2); ctx.fill();
        } else {
          ctx.strokeStyle = mark.kind === "loaded" ? colors.accent : mark.kind === "released_late" ? colors.err : colors.blue;
          ctx.lineWidth = 2;
          const up = mark.kind === "loaded" ? -8 : 8;
          ctx.beginPath(); ctx.moveTo(x, base); ctx.lineTo(x, base + (up < 0 ? up : -up / 2)); ctx.stroke();
        }
      }
    }

    drawHover(ctx, visible, xOf, yOf, pad, plotH, colors) {
      if (!this.pointer) return;
      const target = visible.reduce((best, item) => (Math.abs(xOf(item.t) - this.pointer.x) < Math.abs(xOf(best.t) - this.pointer.x) ? item : best), visible[0]);
      const x = xOf(target.t);
      ctx.strokeStyle = colors.muted; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + plotH); ctx.stroke();
      const nearby = this.marks.filter((mark) => Math.abs(mark.t - target.t) < 1000).slice(-4)
        .map((mark) => `${mark.kind.replace("_", " ")} ${mark.cls}`);
      this.tooltip.textContent = [new Date(target.t).toLocaleTimeString(), formatBytes(target.footprint),
        `${target.tracked} live objects`, ...nearby].join(" · ");
      this.tooltip.style.left = `${Math.min(x + 12, this.canvas.clientWidth - 260)}px`;
      this.tooltip.style.top = `${pad.top}px`;
      this.tooltip.hidden = false;
    }
  }

  function formatBytes(value) {
    const number = Number(value) || 0;
    if (number < 1024) return `${number} B`;
    if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`;
    if (number < 1024 ** 3) return `${(number / 1024 ** 2).toFixed(1)} MB`;
    return `${(number / 1024 ** 3).toFixed(2)} GB`;
  }

  window.ViewliftMemoryVisuals = { ForceGraph, ReplayPlayer, LiveTimeline, formatBytes, reduceMotion };
})();
