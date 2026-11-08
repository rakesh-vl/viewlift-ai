// Devices page: simulators and paired devices through POST /api/host/call.
// Buttons stay hidden until the selected target lists that capability.
// Mutating tools ask for confirmation here and send confirm: true; the server checks it again.
(() => {
  "use strict";

  const POLL_SECONDS = 20;
  const $ = (id) => document.getElementById(id);

  const state = {
    mutating: new Map(),
    elements: [],
    targets: [],
    jobId: null,
  };

  const CONFIRM = {
    boot_sim: "Boot the selected simulator?",
    launch_app: "Launch the app on the selected target?",
    stop_app: "Stop the app on the selected target?",
    build_run: "Build and run the scheme on the selected target? This can take minutes.",
    open_url: "Open this URL on the selected target?",
    set_location: "Set the simulated location on the selected target?",
    clear_location: "Clear the simulated location on the selected target?",
    record_video: "Start a screen recording of the selected target?",
    stop_video: "Stop the screen recording?",
    ui_runner_start: "Build if needed and start the UI runner on the selected target?",
    ui_runner_stop: "Stop the UI runner on the selected target?",
    sim_ui_action: "Send this input to the selected simulator? Type test data only.",
  };

  async function callTool(tool, args = {}, prompt = "") {
    const mutating = state.mutating.get(tool) === true;
    if (mutating && prompt && !window.confirm(prompt)) {
      return null;
    }
    const response = await fetch("/api/host/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool, args: cleanArgs(args), confirm: mutating }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, detail: data.detail || response.statusText || "Request failed." };
    }
    return data;
  }

  function cleanArgs(args) {
    const out = {};
    Object.entries(args).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") out[key] = value;
    });
    return out;
  }

  function selected() {
    return state.targets.find((row) => row.udid === $("sim-select").value) || null;
  }

  function targetArgs(extra = {}) {
    const current = selected();
    if (!current) return extra;
    const args = { udid: current.udid, ...extra };
    if (current.kind === "simulator") args.simulatorId = current.udid;
    return args;
  }

  function showOutput(value) {
    const text = JSON.stringify(
      value,
      (key, val) => (key === "base64" && typeof val === "string" ? "[image data omitted]" : val),
      2,
    );
    $("sim-output").textContent = text || "";
  }

  function showBanner(message, isError) {
    const banner = $("sim-banner");
    banner.hidden = !message;
    banner.textContent = message || "";
    banner.classList.toggle("is-error", Boolean(isError));
  }

  function report(result) {
    if (!result) return;
    showOutput(result);
    if (result.ok === false) {
      showBanner(result.detail || "The tool failed. See the last result.", true);
    } else {
      showBanner("", false);
    }
  }

  async function run(tool, args, prompt) {
    const result = await callTool(tool, args, prompt);
    report(result);
    return result;
  }

  function applyCapabilities() {
    const current = selected();
    const caps = new Set(current ? current.capabilities || [] : []);
    document.querySelectorAll("[data-cap], [data-kind]").forEach((node) => {
      const need = (node.dataset.cap || "").split(/\s+/).filter(Boolean);
      const kindOk = !node.dataset.kind || (current && current.kind === node.dataset.kind);
      const capOk = need.every((name) => caps.has(name));
      node.hidden = !current || !kindOk || !capOk;
    });
    const note = $("sim-caps");
    if (!current) {
      note.textContent = "";
      return;
    }
    const where = [current.kind, current.platform, current.transport, current.state]
      .filter(Boolean)
      .join(" · ");
    note.textContent = `${where}. ${[...caps].sort().join(", ")}`;
  }

  async function loadDevices() {
    const response = await fetch("/api/host/targets");
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) {
      report(result.detail ? { ok: false, detail: result.detail } : result);
      return;
    }
    state.targets = Array.isArray(result.targets) ? result.targets : [];
    const select = $("sim-select");
    const previous = select.value;
    select.replaceChildren();
    if (!state.targets.length) {
      select.append(new Option("No simulators or paired devices", ""));
      applyCapabilities();
      return;
    }
    state.targets.forEach((row) => {
      const kind = row.kind === "device" ? "Device" : "Simulator";
      const label = `${row.name} · ${kind} · ${row.platform} · ${row.state || row.transport || ""}`;
      select.append(new Option(label, row.udid));
    });
    const still = state.targets.some((row) => row.udid === previous);
    if (still) {
      select.value = previous;
    } else {
      const ready = state.targets.find((row) => row.state === "Booted" || row.state === "connected");
      select.value = (ready || state.targets[0]).udid;
    }
    applyCapabilities();
    if (Array.isArray(result.problems) && result.problems.length) {
      showBanner(result.problems.join(" "), true);
    }
  }

  async function loadDefaults() {
    const result = await callTool("session_show_defaults", {});
    if (!result || result.ok === false) return;
    const current = result.defaults || {};
    if (current.projectPath || current.workspacePath) {
      $("sim-project-path").value = current.workspacePath || current.projectPath || "";
    }
    if (current.scheme) $("sim-scheme").value = current.scheme;
    if (current.bundleId) $("sim-bundle").value = current.bundleId;
    if (current.developmentTeam) $("sim-team").value = current.developmentTeam;
    const saved = current.deviceId || current.simulatorId;
    if (saved && state.targets.some((row) => row.udid === saved)) {
      $("sim-select").value = saved;
      applyCapabilities();
    }
  }

  async function saveDefaults(event) {
    event.preventDefault();
    const path = $("sim-project-path").value.trim();
    const values = {
      scheme: $("sim-scheme").value.trim(),
      bundleId: $("sim-bundle").value.trim(),
      developmentTeam: $("sim-team").value.trim(),
    };
    if (path.endsWith(".xcworkspace")) values.workspacePath = path;
    else if (path) values.projectPath = path;
    const current = selected();
    if (current && current.kind === "simulator") values.simulatorId = current.udid;
    if (current && current.kind === "device") values.deviceId = current.udid;
    await run("session_set_defaults", cleanArgs(values));
  }

  function renderJob(job) {
    $("sim-job").hidden = false;
    const finished = job.state && job.state !== "running";
    const duration = typeof job.duration_s === "number" ? ` · ${job.duration_s}s` : "";
    $("sim-job-state").textContent = `${job.title || job.kind || "Job"}: ${job.state || "unknown"}${duration}`;
    $("sim-job-log").textContent = job.log_tail || "";
    return finished;
  }

  async function pollJob(jobId) {
    state.jobId = jobId;
    for (;;) {
      const waited = await callTool("wait_job", { job_id: jobId, timeout_s: POLL_SECONDS });
      if (!waited || waited.ok === false) {
        report(waited);
        break;
      }
      const job = await callTool("get_job", { job_id: jobId, log_tail: 60 });
      if (!job || job.ok === false) {
        report(job);
        break;
      }
      const finished = renderJob(job);
      if (finished) {
        showOutput(job);
        showBanner(job.state === "failed" ? job.error || "The job failed. See the log." : "", job.state === "failed");
        break;
      }
    }
    state.jobId = null;
  }

  async function followJob(result) {
    if (!result || result.ok === false || !result.job_id) return;
    renderJob({ ...result, state: result.state || "running" });
    await pollJob(result.job_id);
  }

  async function buildAndRun() {
    const result = await run("build_run", targetArgs(), CONFIRM.build_run);
    await followJob(result);
  }

  async function screenshot() {
    const result = await callTool("screenshot", targetArgs({ maxDimension: 900 }));
    if (!result || result.ok === false) {
      report(result);
      return;
    }
    if (result.base64) {
      const img = $("sim-shot");
      img.src = `data:${result.mime || "image/png"};base64,${result.base64}`;
      img.hidden = false;
      $("sim-shot-note").textContent = `${result.width}×${result.height} · ${Math.round(result.bytes / 1024)} KB`;
    }
    showOutput({ ...result, base64: undefined });
  }

  async function readScreen() {
    const result = await callTool("sim_ui_describe", targetArgs({ limit: 300 }));
    if (!result || result.ok === false) {
      report(result);
      return;
    }
    state.elements = Array.isArray(result.elements) ? result.elements : [];
    renderElements();
    showOutput({ count: result.count, driver: result.driver, udid: result.udid });
  }

  async function screenMap() {
    await run("sim_ui_describe", targetArgs({ summary: true }));
  }

  function elementTarget(element) {
    if (element.identifier) return { identifier: element.identifier };
    if (element.label) return { label: element.label };
    return null;
  }

  function renderElements() {
    const list = $("sim-element-list");
    const filter = $("sim-filter").value.trim().toLowerCase();
    const shown = state.elements.filter((element) => {
      if (!filter) return true;
      return [element.label, element.identifier, element.value, element.role]
        .some((part) => String(part || "").toLowerCase().includes(filter));
    });
    list.replaceChildren();
    shown.slice(0, 200).forEach((element) => {
      const item = document.createElement("li");
      item.className = "sim-element";
      const text = document.createElement("div");
      text.className = "sim-element-text";
      const title = document.createElement("strong");
      title.textContent = element.label || element.identifier || element.role || "(unlabeled)";
      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = [element.role, element.identifier && element.label ? element.identifier : ""]
        .filter(Boolean)
        .join(" · ");
      text.append(title, meta);
      const tap = document.createElement("button");
      tap.type = "button";
      tap.className = "btn ghost";
      tap.textContent = "Tap";
      tap.disabled = !elementTarget(element);
      tap.addEventListener("click", () => tapElement(element));
      item.append(text, tap);
      list.append(item);
    });
    $("sim-element-count").textContent = `${shown.length} of ${state.elements.length} elements`;
  }

  async function tapElement(element) {
    const target = elementTarget(element);
    if (!target) return;
    const name = element.label || element.identifier || "element";
    const result = await run(
      "sim_ui_action",
      targetArgs({ action: "tap", ...target }),
      `Tap "${name}" on the simulator?`,
    );
    if (result && result.ok !== false) await screenshot();
  }

  async function typeText(event) {
    event.preventDefault();
    const text = $("sim-type-text").value;
    if (!text) return;
    const result = await run(
      "sim_ui_action",
      targetArgs({ action: "type_text", text }),
      CONFIRM.sim_ui_action,
    );
    if (result && result.ok !== false) $("sim-type-text").value = "";
  }

  async function openUrl(event) {
    event.preventDefault();
    const url = $("sim-url").value.trim();
    if (!url) return;
    await run("open_url", targetArgs({ url, bundleId: $("sim-bundle").value.trim() }), CONFIRM.open_url);
  }

  async function setLocation(event, clear) {
    if (event) event.preventDefault();
    const args = targetArgs({ clear });
    if (!clear) {
      args.latitude = Number($("sim-lat").value);
      args.longitude = Number($("sim-lon").value);
    }
    await run("set_location", args, clear ? CONFIRM.clear_location : CONFIRM.set_location);
  }

  async function startRunner() {
    const result = await run(
      "ui_runner_start",
      targetArgs({
        bundleId: $("sim-bundle").value.trim(),
        developmentTeam: $("sim-team").value.trim(),
      }),
      CONFIRM.ui_runner_start,
    );
    await followJob(result);
  }

  async function loadTools() {
    const response = await fetch("/api/host/tools");
    if (!response.ok) return;
    const data = await response.json();
    (data.tools || []).forEach((tool) => state.mutating.set(tool.name, Boolean(tool.mutating)));
  }

  function bind() {
    $("sim-refresh").addEventListener("click", loadDevices);
    $("sim-select").addEventListener("change", applyCapabilities);
    $("sim-defaults-form").addEventListener("submit", saveDefaults);
    $("sim-build-run").addEventListener("click", buildAndRun);
    $("sim-boot").addEventListener("click", () => run("boot_sim", targetArgs(), CONFIRM.boot_sim));
    $("sim-screenshot").addEventListener("click", screenshot);
    $("sim-launch").addEventListener("click", () => run("launch_app", targetArgs(), CONFIRM.launch_app));
    $("sim-stop").addEventListener("click", () => run("stop_app", targetArgs(), CONFIRM.stop_app));
    $("sim-record").addEventListener("click", () => run("record_video", targetArgs({ action: "start" }), CONFIRM.record_video));
    $("sim-record-stop").addEventListener("click", () => run("record_video", targetArgs({ action: "stop" }), CONFIRM.stop_video));
    $("sim-runner").addEventListener("click", startRunner);
    $("sim-runner-stop").addEventListener("click", () => run("ui_runner_stop", targetArgs(), CONFIRM.ui_runner_stop));
    $("sim-url-form").addEventListener("submit", openUrl);
    $("sim-location-form").addEventListener("submit", (event) => setLocation(event, false));
    $("sim-location-clear").addEventListener("click", () => setLocation(null, true));
    $("sim-read-screen").addEventListener("click", readScreen);
    $("sim-screen-map").addEventListener("click", screenMap);
    $("sim-filter").addEventListener("input", renderElements);
    $("sim-type-form").addEventListener("submit", typeText);
    $("sim-output-clear").addEventListener("click", () => {
      $("sim-output").textContent = "";
    });
  }

  async function init() {
    if (!$("sim-page")) return;
    bind();
    await loadTools();
    await loadDevices();
    await loadDefaults();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
