// Devices page: simulators and paired devices through POST /api/host/call.
// Buttons stay hidden until the selected target lists that capability.
// Mutating tools ask for confirmation here and send confirm: true; the server checks it again.
// When an app with the SDK runs on the target, screen reads and taps go through
// /api/debug/invoke with a one-use token from /api/debug/confirm.
(() => {
  "use strict";

  const POLL_SECONDS = 20;
  const CLIENT_REFRESH_MS = 10000;
  const SAVE_DELAY_MS = 600;
  const URL_HISTORY_KEY = "vl.devices.urls";
  const URL_HISTORY_MAX = 10;
  const OTHER_PATH = "__other__";
  const BUILD_KEYS = ["PRODUCT_BUNDLE_IDENTIFIER", "DEVELOPMENT_TEAM", "WRAPPER_EXTENSION"];
  const LOCATIONS = [
    ["Cupertino, US", 37.3349, -122.009],
    ["New York, US", 40.7128, -74.006],
    ["London, UK", 51.5072, -0.1276],
    ["Mumbai, IN", 19.076, 72.8777],
    ["Bengaluru, IN", 12.9716, 77.5946],
    ["Sydney, AU", -33.8688, 151.2093],
  ];
  const $ = (id) => document.getElementById(id);

  const state = {
    mutating: new Map(),
    elements: [],
    targets: [],
    clients: [],
    matched: [],
    jobId: null,
    saveTimer: null,
    deviceApps: new Map(),
    loadingDefaults: false,
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

  function detailText(detail, fallback) {
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    return detail.message || JSON.stringify(detail);
  }

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
      return { ok: false, detail: detailText(data.detail, response.statusText || "Request failed.") };
    }
    return data;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, detail: detailText(data.detail, response.statusText || "Request failed.") };
    }
    return data;
  }

  // One SDK command on the selected app. Returns { ok, result } or { ok: false, detail }.
  async function sdkInvoke(name, payload = {}, prompt = "") {
    const client = selectedClient();
    if (!client) return { ok: false, detail: "No app with the SDK is online on this target." };
    const body = { client_id: client.id, name, payload };
    if (prompt) {
      if (!window.confirm(prompt)) return null;
      const token = await postJson("/api/debug/confirm", { client_id: client.id, command: name });
      if (!token || token.ok === false || !token.token) return token;
      body.confirmation = token.token;
    }
    const data = await postJson("/api/debug/invoke", body);
    if (!data || data.ok === false) return data;
    const event = data.event && data.event.payload ? data.event.payload : {};
    if (event.ok === false) return { ok: false, detail: event.error || `${name} failed in the app.` };
    return { ok: true, result: event.result || {} };
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

  function selectedClient() {
    return state.matched.find((row) => row.id === $("sim-client").value) || null;
  }

  function targetArgs(extra = {}) {
    const current = selected();
    if (!current) return extra;
    const args = { udid: current.udid, ...extra };
    if (current.kind === "simulator") args.simulatorId = current.udid;
    else args.deviceId = current.udid;
    return args;
  }

  function projectPath() {
    const choice = $("sim-project").value;
    if (choice === OTHER_PATH) return $("sim-project-path").value.trim();
    return choice;
  }

  function projectArgs() {
    const path = projectPath();
    const out = {};
    if (path.endsWith(".xcworkspace")) out.workspacePath = path;
    else if (path) out.projectPath = path;
    return out;
  }

  function bundleId() {
    const typed = $("sim-bundle").value.trim();
    if (typed) return typed;
    const client = selectedClient();
    return client ? client.bundle_id || "" : "";
  }

  // The form as tool args, so actions never depend on having saved first.
  function formArgs(extra = {}) {
    return targetArgs({
      ...projectArgs(),
      scheme: $("sim-scheme").value.trim(),
      bundleId: bundleId(),
      developmentTeam: $("sim-team").value.trim(),
      ...extra,
    });
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

  function note(message) {
    $("sim-project-note").textContent = message || "";
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

  // ---- target and SDK app --------------------------------------------------------------------

  function uiMode() {
    const current = selected();
    if (!current) return "";
    if (selectedClient()) return "sdk";
    if ((current.capabilities || []).includes("ui_host")) return "host";
    return "";
  }

  function pill(text, tone) {
    const item = document.createElement("li");
    item.className = `sim-pill${tone ? ` is-${tone}` : ""}`;
    item.textContent = text;
    return item;
  }

  function renderPills() {
    const list = $("sim-pills");
    list.replaceChildren();
    const current = selected();
    if (!current) return;
    const live = current.state === "Booted" || current.state === "connected";
    list.append(
      pill(current.kind === "device" ? "Device" : "Simulator"),
      pill([current.platform, current.os_version].filter(Boolean).join(" ")),
    );
    if (current.transport && current.transport !== "local") list.append(pill(current.transport));
    if (current.state) list.append(pill(current.state, live ? "ok" : ""));
    const client = selectedClient();
    if (client) list.append(pill(`SDK: ${client.app_name || client.bundle_id}`, "ok"));
    else list.append(pill("No SDK app online", "faint"));
  }

  function matchClients(current) {
    if (!current) return [];
    const rows = state.clients.filter((row) => row.online && !row.superseded_by);
    const hits = rows.filter((row) => {
      if (current.kind === "simulator") {
        return row.is_simulator && (row.device_key === current.udid || row.device_key === `runner:${current.udid}`);
      }
      if (row.is_simulator) return false;
      return row.device_name === current.name || (current.product_type && row.device_model === current.product_type);
    });
    return hits.sort((a, b) => (a.source === "runner") - (b.source === "runner"));
  }

  function renderClients() {
    const previous = $("sim-client").value;
    state.matched = matchClients(selected());
    const select = $("sim-client");
    select.replaceChildren();
    state.matched.forEach((row) => {
      const kind = row.source === "runner" ? " (UI runner)" : "";
      select.append(new Option(`${row.app_name || row.bundle_id}${kind}`, row.id));
    });
    if (state.matched.some((row) => row.id === previous)) select.value = previous;
    $("sim-client-field").hidden = state.matched.length < 2;
  }

  async function loadClients() {
    const response = await fetch("/api/debug/clients").catch(() => null);
    if (!response || !response.ok) return;
    const data = await response.json().catch(() => ({}));
    state.clients = Array.isArray(data.clients) ? data.clients : [];
    renderClients();
    applyCapabilities();
    renderUrlSuggestions();
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
    const mode = uiMode();
    document.querySelectorAll("[data-ui]").forEach((node) => {
      node.hidden = !mode;
    });
    $("sim-gestures").hidden = mode !== "sdk";
    $("sim-remote").hidden = !(mode === "sdk" && current && current.platform === "tvos");
    const uiNote = $("sim-ui-note");
    if (!current) uiNote.textContent = "";
    else if (mode === "sdk") uiNote.textContent = "Reads and taps go through the SDK in the app.";
    else if (mode === "host") uiNote.textContent = "Reads and taps use the simulator UI driver (AXe or idb).";
    else uiNote.textContent = "Run an app with the SDK, or start the UI runner, to read and tap this screen.";
    renderPills();
  }

  async function loadDevices() {
    const response = await fetch("/api/host/targets");
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) {
      report(result.detail ? { ok: false, detail: detailText(result.detail, "") } : result);
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
      const os = [row.platform, row.os_version].filter(Boolean).join(" ");
      const status = row.state === "Booted" || row.state === "connected" ? " ●" : "";
      select.append(new Option(`${row.name} · ${kind} · ${os}${status}`, row.udid));
    });
    const still = state.targets.some((row) => row.udid === previous);
    if (still) {
      select.value = previous;
    } else {
      const ready = state.targets.find((row) => row.state === "Booted" || row.state === "connected");
      select.value = (ready || state.targets[0]).udid;
    }
    renderClients();
    applyCapabilities();
    if (Array.isArray(result.problems) && result.problems.length) {
      showBanner(result.problems.join(" "), true);
    }
  }

  function onTargetChange() {
    renderClients();
    applyCapabilities();
    renderUrlSuggestions();
    scheduleSave();
  }

  // ---- project, scheme, bundle id, team ------------------------------------------------------

  function shortPath(path) {
    const parts = path.split("/").filter(Boolean);
    return parts.slice(-2).join("/");
  }

  async function loadProjects() {
    const select = $("sim-project");
    const keep = projectPath();
    const result = await callTool("discover_projs", {});
    select.replaceChildren();
    if (!result || result.ok === false) {
      select.append(new Option("No project roots. Add one on Setup, or pick Other path", ""));
      note("Add your app repository as a project root on Setup, then press Rescan.");
    } else {
      // A workspace inside an .xcodeproj is Xcode's own; the project itself is the real choice.
      const workspaces = (result.workspaces || []).filter((path) => !path.includes(".xcodeproj/"));
      const paths = [...workspaces, ...(result.projects || [])];
      select.append(new Option(paths.length ? "Pick a project" : "No projects found in the roots", ""));
      paths.forEach((path) => {
        const option = new Option(shortPath(path), path);
        option.title = path;
        select.append(option);
      });
    }
    select.append(new Option("Other path…", OTHER_PATH));
    selectProject(keep);
  }

  function selectProject(path) {
    const select = $("sim-project");
    const known = [...select.options].some((option) => option.value === path);
    if (path && !known) {
      select.value = OTHER_PATH;
      $("sim-project-path").value = path;
    } else {
      select.value = path || "";
    }
    $("sim-project-path-field").hidden = select.value !== OTHER_PATH;
  }

  function fillList(listId, values) {
    const list = $(listId);
    list.replaceChildren();
    [...new Set(values.filter(Boolean))].forEach((value) => list.append(new Option(value, value)));
  }

  function pickScheme(schemes) {
    const apps = schemes.filter((name) => !/tests?$/i.test(name));
    const pool = apps.length ? apps : schemes;
    const current = selected();
    if (current && current.platform === "tvos") {
      const tv = pool.find((name) => /tv/i.test(name));
      if (tv) return tv;
    }
    return pool[0] || "";
  }

  async function onProjectChange({ fill = true } = {}) {
    $("sim-project-path-field").hidden = $("sim-project").value !== OTHER_PATH;
    const args = projectArgs();
    if (!Object.keys(args).length) return;
    note("Reading schemes…");
    const result = await callTool("list_schemes", args);
    if (!result || result.ok === false) {
      note(result ? result.detail : "");
      return;
    }
    const schemes = Array.isArray(result.schemes) ? result.schemes : [];
    fillList("sim-scheme-list", schemes);
    const scheme = $("sim-scheme").value.trim();
    if (fill || !scheme || !schemes.includes(scheme)) {
      $("sim-scheme").value = pickScheme(schemes);
    }
    note(`${schemes.length} scheme${schemes.length === 1 ? "" : "s"} found.`);
    await onSchemeChange({ fill });
  }

  async function onSchemeChange({ fill = true } = {}) {
    const scheme = $("sim-scheme").value.trim();
    const args = projectArgs();
    if (!scheme || !Object.keys(args).length) {
      scheduleSave();
      return;
    }
    note("Reading build settings…");
    const result = await callTool("show_build_settings", { ...args, scheme, keys: BUILD_KEYS });
    if (!result || result.ok === false) {
      note(result ? result.detail : "");
      scheduleSave();
      return;
    }
    const rows = (result.targets || []).filter((row) => row.settings && row.settings.WRAPPER_EXTENSION === "app");
    const app = rows.find((row) => row.target === scheme) || rows[0];
    if (app) {
      const bundle = app.settings.PRODUCT_BUNDLE_IDENTIFIER || "";
      const team = app.settings.DEVELOPMENT_TEAM || "";
      if (bundle && (fill || !$("sim-bundle").value.trim())) $("sim-bundle").value = bundle;
      if (team && (fill || !$("sim-team").value.trim())) $("sim-team").value = team;
      note(`App target ${app.target}.`);
    } else {
      note("No app target in this scheme.");
    }
    scheduleSave();
  }

  async function loadBundleSuggestions() {
    const current = selected();
    const values = state.matched.map((row) => row.bundle_id);
    state.clients.filter((row) => row.online).forEach((row) => values.push(row.bundle_id));
    if (current && current.kind === "device" && (current.capabilities || []).includes("apps")) {
      if (!state.deviceApps.has(current.udid)) {
        state.deviceApps.set(current.udid, []);
        const result = await callTool("list_device_apps", targetArgs());
        const apps = result && Array.isArray(result.apps) ? result.apps : [];
        state.deviceApps.set(
          current.udid,
          apps.filter((row) => row.removable !== false).map((row) => row.bundleId),
        );
      }
      values.push(...state.deviceApps.get(current.udid));
    }
    fillList("sim-bundle-list", values);
  }

  async function loadDefaults({ fill = false } = {}) {
    const result = await callTool("session_show_defaults", {});
    if (!result || result.ok === false) return;
    const current = result.defaults || {};
    state.loadingDefaults = true;
    selectProject(current.workspacePath || current.projectPath || "");
    if (current.scheme) $("sim-scheme").value = current.scheme;
    if (current.bundleId) $("sim-bundle").value = current.bundleId;
    if (current.developmentTeam) $("sim-team").value = current.developmentTeam;
    const saved = current.deviceId || current.simulatorId;
    if (!fill && saved && state.targets.some((row) => row.udid === saved)) {
      $("sim-select").value = saved;
      renderClients();
      applyCapabilities();
    }
    state.loadingDefaults = false;
    const projects = [...$("sim-project").options].filter((option) => option.value && option.value !== OTHER_PATH);
    if (!projectPath() && projects.length === 1) {
      $("sim-project").value = projects[0].value;
      await onProjectChange({ fill: true });
    } else if (projectPath()) {
      await onProjectChange({ fill });
    }
  }

  function scheduleSave() {
    if (state.loadingDefaults) return;
    window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(saveDefaults, SAVE_DELAY_MS);
  }

  async function saveDefaults() {
    const values = {
      ...projectArgs(),
      scheme: $("sim-scheme").value.trim(),
      bundleId: $("sim-bundle").value.trim(),
      developmentTeam: $("sim-team").value.trim(),
    };
    const current = selected();
    if (current && current.kind === "simulator") values.simulatorId = current.udid;
    if (current && current.kind === "device") values.deviceId = current.udid;
    const result = await callTool("session_set_defaults", cleanArgs(values));
    if (result && result.ok === false) showBanner(result.detail, true);
  }

  async function syncXcode() {
    note("Looking for the last Xcode build…");
    const result = await callTool("sync_xcode_defaults", {});
    if (!result || result.ok === false) {
      note(result ? result.detail : "");
      return;
    }
    await loadProjects();
    await loadDefaults({ fill: true });
  }

  // ---- jobs ----------------------------------------------------------------------------------

  function renderJob(job) {
    $("sim-job").hidden = false;
    const finished = job.state && job.state !== "running";
    const duration = typeof job.duration_s === "number" ? ` · ${job.duration_s}s` : "";
    const stateText = $("sim-job-state");
    stateText.textContent = `${job.title || job.kind || "Job"}: ${job.state || "unknown"}${duration}`;
    stateText.dataset.state = job.state || "";
    $("sim-job-cancel").hidden = Boolean(finished);
    const log = $("sim-job-log");
    log.textContent = job.log_tail || "";
    log.scrollTop = log.scrollHeight;
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
        showBanner(job.state === "failed" ? job.error || "The job failed. See the build log." : "", job.state === "failed");
        break;
      }
    }
    state.jobId = null;
    $("sim-job-cancel").hidden = true;
  }

  async function cancelJob() {
    if (!state.jobId) return;
    report(await callTool("cancel_job", { job_id: state.jobId }));
  }

  async function followJob(result) {
    if (!result || result.ok === false || !result.job_id) return;
    renderJob({ ...result, state: result.state || "running" });
    await pollJob(result.job_id);
  }

  async function buildAndRun() {
    const result = await run("build_run", formArgs(), CONFIRM.build_run);
    await followJob(result);
    loadClients();
  }

  async function startRunner() {
    const result = await run("ui_runner_start", formArgs(), CONFIRM.ui_runner_start);
    await followJob(result);
    loadClients();
  }

  // ---- screenshot and screen -----------------------------------------------------------------

  function showShot(result) {
    const img = $("sim-shot");
    img.src = `data:${result.mime || "image/png"};base64,${result.base64}`;
    img.hidden = false;
    const size = result.width && result.height ? `${result.width}×${result.height}` : "";
    const bytes = result.bytes ? `${Math.round(result.bytes / 1024)} KB` : "";
    const time = new Date().toLocaleTimeString();
    $("sim-shot-note").textContent = [size, bytes, time].filter(Boolean).join(" · ");
  }

  async function screenshot() {
    let result = await callTool("screenshot", targetArgs({ maxDimension: 900 }));
    if ((!result || result.ok === false || !result.base64) && selectedClient()) {
      const sdk = await sdkInvoke("ui.screenshot", {});
      if (sdk && sdk.ok !== false && sdk.result.base64) result = { ok: true, ...sdk.result, via: "sdk" };
    }
    if (!result || result.ok === false) {
      report(result);
      return;
    }
    if (result.base64) showShot(result);
    showOutput({ ...result, base64: undefined });
    showBanner("", false);
  }

  async function fetchElements() {
    if (uiMode() === "sdk") {
      const sdk = await sdkInvoke("ui.accessibility_tree", { limit: 300 });
      if (!sdk || sdk.ok === false) return sdk;
      const inner = sdk.result || {};
      return { ok: true, elements: inner.elements || [], screen: inner.screen || "", driver: "sdk" };
    }
    const result = await callTool("sim_ui_describe", targetArgs({ limit: 300 }));
    if (!result || result.ok === false) return result;
    return { ok: true, elements: result.elements || [], screen: "", driver: result.driver };
  }

  async function readScreen() {
    const result = await fetchElements();
    if (!result || result.ok === false) {
      report(result);
      return;
    }
    state.elements = Array.isArray(result.elements) ? result.elements : [];
    renderElements();
    showOutput({ count: state.elements.length, driver: result.driver, screen: result.screen });
    showBanner("", false);
    await screenshot();
  }

  function summarize(elements, screen) {
    const groups = { Tappable: [], Inputs: [], Text: [], Other: [] };
    const tappable = new Set(["button", "link", "tab", "cell", "switch", "toggle", "menuitem", "segmentedcontrol"]);
    const inputs = new Set(["textfield", "securetextfield", "searchfield", "textview"]);
    const texts = new Set(["statictext", "text", "label", "heading"]);
    elements.forEach((item) => {
      const name = item.label || item.identifier || item.value;
      if (!name) return;
      const role = String(item.role || "").toLowerCase().replace(/\s/g, "");
      const key = tappable.has(role) ? "Tappable" : inputs.has(role) ? "Inputs" : texts.has(role) ? "Text" : "Other";
      groups[key].push(String(name).slice(0, 40));
    });
    const lines = screen ? [`Screen: ${screen}`] : [];
    Object.entries(groups).forEach(([key, names]) => {
      if (names.length) lines.push(`${key} (${names.length}): ${[...new Set(names)].slice(0, 15).join(", ")}`);
    });
    return lines.join("\n") || "No labelled elements.";
  }

  async function screenMap() {
    const result = await fetchElements();
    if (!result || result.ok === false) {
      report(result);
      return;
    }
    $("sim-output").textContent = summarize(result.elements || [], result.screen);
    $("sim-output").closest("details").open = true;
    showBanner("", false);
  }

  function elementTarget(element) {
    if (uiMode() === "sdk" && element.view_id) return { view_id: element.view_id };
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
      title.textContent = element.label || element.identifier || element.value || element.role || "(unlabeled)";
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
    $("sim-element-count").textContent = state.elements.length
      ? `${shown.length} of ${state.elements.length} elements`
      : "Read screen to list elements.";
  }

  async function sendInput(sdkName, payload, hostAction, prompt) {
    const result = uiMode() === "sdk"
      ? await sdkInvoke(sdkName, payload, prompt)
      : await callTool("sim_ui_action", targetArgs({ action: hostAction, ...payload }), prompt);
    report(result);
    return result && result.ok !== false;
  }

  async function tapElement(element) {
    const target = elementTarget(element);
    if (!target) return;
    const name = element.label || element.identifier || "element";
    if (await sendInput("ui.tap", target, "tap", `Tap "${name}" on the selected target?`)) {
      await readScreen();
    }
  }

  async function typeText(event) {
    event.preventDefault();
    const text = $("sim-type-text").value;
    if (!text) return;
    if (await sendInput("ui.type_text", { text }, "type_text", CONFIRM.sim_ui_action)) {
      $("sim-type-text").value = "";
    }
  }

  async function scroll(direction) {
    const done = await sdkInvoke("ui.scroll", { direction }, `Scroll ${direction} on the selected target?`);
    report(done);
    if (done && done.ok !== false) await readScreen();
  }

  async function remote(press) {
    const done = await sdkInvoke("ui.remote", { press }, `Press ${press} on the Apple TV remote?`);
    report(done);
    if (done && done.ok !== false) await screenshot();
  }

  // ---- quick actions -------------------------------------------------------------------------

  function urlHistory() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(URL_HISTORY_KEY) || "[]");
      return Array.isArray(saved) ? saved.filter((item) => typeof item === "string") : [];
    } catch {
      return [];
    }
  }

  function rememberUrl(url) {
    const next = [url, ...urlHistory().filter((item) => item !== url)].slice(0, URL_HISTORY_MAX);
    try {
      window.localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(next));
    } catch {
      // Private windows can refuse storage; the history is a convenience only.
    }
    renderUrlSuggestions();
  }

  function renderUrlSuggestions() {
    const history = urlHistory();
    // Apps also register OAuth and SDK callback schemes; only short lowercase names are deeplinks.
    const schemes = state.matched
      .flatMap((row) => row.url_schemes || [])
      .filter((scheme) => /^[a-z][a-z0-9-]{1,23}$/.test(scheme) && !/^fb\d/.test(scheme))
      .map((scheme) => `${scheme}://`);
    fillList("sim-url-list", [...history, ...schemes]);
    const chips = $("sim-url-recent");
    chips.replaceChildren();
    [...new Set([...history.slice(0, 5), ...schemes])].slice(0, 8).forEach((url) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sim-chip";
      button.textContent = url;
      button.title = url.endsWith("://") ? "Fill this scheme" : "Open this URL";
      button.addEventListener("click", () => {
        $("sim-url").value = url;
        if (url.endsWith("://")) $("sim-url").focus();
        else $("sim-url-form").requestSubmit();
      });
      item.append(button);
      chips.append(item);
    });
    chips.hidden = !chips.children.length;
  }

  async function openUrl(event) {
    event.preventDefault();
    const url = $("sim-url").value.trim();
    if (!url) return;
    const result = await run("open_url", targetArgs({ url, bundleId: bundleId() }), CONFIRM.open_url);
    if (result && result.ok !== false) rememberUrl(url);
  }

  function renderLocationPresets() {
    const select = $("sim-location-preset");
    LOCATIONS.forEach(([name, lat, lon], index) => {
      select.append(new Option(`${name} (${lat}, ${lon})`, String(index)));
    });
    select.append(new Option("Custom coordinates…", "custom"));
  }

  function onPresetChange() {
    $("sim-location-custom").hidden = $("sim-location-preset").value !== "custom";
  }

  async function setLocation(event, clear) {
    if (event) event.preventDefault();
    const args = targetArgs({ clear });
    if (!clear) {
      const choice = $("sim-location-preset").value;
      if (choice === "custom") {
        args.latitude = Number($("sim-lat").value);
        args.longitude = Number($("sim-lon").value);
        if (!$("sim-lat").value || !$("sim-lon").value) {
          showBanner("Enter a latitude and a longitude.", true);
          return;
        }
      } else {
        const [, lat, lon] = LOCATIONS[Number(choice)];
        args.latitude = lat;
        args.longitude = lon;
      }
    }
    await run("set_location", args, clear ? CONFIRM.clear_location : CONFIRM.set_location);
  }

  async function loadTools() {
    const response = await fetch("/api/host/tools");
    if (!response.ok) return;
    const data = await response.json();
    (data.tools || []).forEach((tool) => state.mutating.set(tool.name, Boolean(tool.mutating)));
  }

  function bind() {
    $("sim-refresh").addEventListener("click", async () => {
      await loadDevices();
      await loadClients();
    });
    $("sim-select").addEventListener("change", onTargetChange);
    $("sim-client").addEventListener("change", () => {
      applyCapabilities();
      renderUrlSuggestions();
    });
    $("sim-defaults-form").addEventListener("submit", (event) => event.preventDefault());
    $("sim-project").addEventListener("change", () => onProjectChange({ fill: true }));
    $("sim-project-path").addEventListener("change", () => onProjectChange({ fill: true }));
    $("sim-scheme").addEventListener("change", () => onSchemeChange({ fill: true }));
    $("sim-bundle").addEventListener("change", scheduleSave);
    $("sim-bundle").addEventListener("focus", loadBundleSuggestions);
    $("sim-team").addEventListener("change", scheduleSave);
    $("sim-sync-xcode").addEventListener("click", syncXcode);
    $("sim-rescan").addEventListener("click", loadProjects);
    $("sim-build-run").addEventListener("click", buildAndRun);
    $("sim-boot").addEventListener("click", () => run("boot_sim", targetArgs(), CONFIRM.boot_sim));
    $("sim-screenshot").addEventListener("click", screenshot);
    $("sim-launch").addEventListener("click", () => run("launch_app", formArgs(), CONFIRM.launch_app));
    $("sim-stop").addEventListener("click", () => run("stop_app", formArgs(), CONFIRM.stop_app));
    $("sim-record").addEventListener("click", () => run("record_video", targetArgs({ action: "start" }), CONFIRM.record_video));
    $("sim-record-stop").addEventListener("click", () => run("record_video", targetArgs({ action: "stop" }), CONFIRM.stop_video));
    $("sim-runner").addEventListener("click", startRunner);
    $("sim-runner-stop").addEventListener("click", () => run("ui_runner_stop", formArgs(), CONFIRM.ui_runner_stop));
    $("sim-job-cancel").addEventListener("click", cancelJob);
    $("sim-url-form").addEventListener("submit", openUrl);
    $("sim-location-preset").addEventListener("change", onPresetChange);
    $("sim-location-form").addEventListener("submit", (event) => setLocation(event, false));
    $("sim-location-clear").addEventListener("click", () => setLocation(null, true));
    $("sim-read-screen").addEventListener("click", readScreen);
    $("sim-screen-map").addEventListener("click", screenMap);
    $("sim-filter").addEventListener("input", renderElements);
    $("sim-type-form").addEventListener("submit", typeText);
    document.querySelectorAll("[data-scroll]").forEach((button) => {
      button.addEventListener("click", () => scroll(button.dataset.scroll));
    });
    document.querySelectorAll("[data-press]").forEach((button) => {
      button.addEventListener("click", () => remote(button.dataset.press));
    });
    $("sim-output-clear").addEventListener("click", (event) => {
      event.preventDefault();
      $("sim-output").textContent = "";
    });
  }

  async function init() {
    if (!$("sim-page")) return;
    renderLocationPresets();
    bind();
    renderUrlSuggestions();
    renderElements();
    await loadTools();
    await loadDevices();
    await loadClients();
    await loadProjects();
    await loadDefaults();
    window.setInterval(loadClients, CLIENT_REFRESH_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
