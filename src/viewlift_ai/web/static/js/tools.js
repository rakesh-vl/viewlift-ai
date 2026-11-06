/* Scratchpad. Scripts run in a worker in this browser. The server only stores them. */
(() => {
  const PAD_KEY = "vl-scratch-text";
  const SELECT_KEY = "vl-scratch-script";
  const RUN_MS = 1500;
  const TEMPLATE = `/**
{
  "api": 1,
  "name": "New script",
  "description": "What this script does to the text.",
  "author": "you",
  "tags": "debug"
}
**/

function main(state) {
  // state.text is the pad, or the selection if there is one.
  // state.fullText is always the whole pad.
  // state.postInfo("note");
  // state.postError("problem");
  state.text = state.text;
}
`;

  const pad = document.getElementById("scratch-pad");
  const sourceBox = document.getElementById("scratch-source");
  const editor = document.getElementById("scratch-editor");
  const list = document.getElementById("scratch-list");
  const filter = document.getElementById("scratch-filter");
  const nameEl = document.getElementById("scratch-script-name");
  const descEl = document.getElementById("scratch-script-desc");
  const statusEl = document.getElementById("scratch-status");
  const padActions = document.getElementById("scratch-pad-actions");
  const editActions = document.getElementById("scratch-edit-actions");
  const runBtn = document.getElementById("scratch-run");
  const editBtn = document.getElementById("scratch-edit");
  const duplicateBtn = document.getElementById("scratch-duplicate");
  const deleteBtn = document.getElementById("scratch-delete");
  const deleteEditBtn = document.getElementById("scratch-delete-edit");
  const saveBtn = document.getElementById("scratch-save");

  if (!pad || !list) return;

  let scripts = [];
  let selectedId = "";
  let editingId = null;
  let editing = false;
  let deleteArmed = false;
  let deleteTimer = 0;

  try {
    pad.value = localStorage.getItem(PAD_KEY) || "";
    selectedId = localStorage.getItem(SELECT_KEY) || "";
  } catch (error) {
    /* private mode: the pad still works for this tab */
  }

  function showStatus(messages, fallback) {
    const items = messages && messages.length ? messages : fallback ? [fallback] : [];
    if (!items.length) {
      statusEl.textContent = "";
      statusEl.className = "scratch-status";
      return;
    }
    const level = items.some((item) => item.level === "error")
      ? "error"
      : items.some((item) => item.level === "warn")
        ? "warn"
        : "info";
    statusEl.className = "scratch-status " + level;
    statusEl.textContent = items.map((item) => item.text).join(" · ");
  }

  function errorText(body, fallback) {
    return body && typeof body.detail === "string" ? body.detail : fallback;
  }

  function selected() {
    return scripts.find((item) => item.id === selectedId) || null;
  }

  function persistPad() {
    try {
      localStorage.setItem(PAD_KEY, pad.value);
    } catch (error) {
      /* ignore quota */
    }
  }

  function setHidden(node, hidden) {
    if (!node) return;
    node.hidden = hidden;
  }

  function showEditor(on) {
    editing = on;
    setHidden(pad, on);
    setHidden(editor, !on);
    setHidden(padActions, on);
    setHidden(editActions, !on);
    if (!on) disarmDelete();
  }

  function disarmDelete() {
    deleteArmed = false;
    window.clearTimeout(deleteTimer);
    if (deleteBtn) deleteBtn.textContent = "Delete";
    if (deleteEditBtn) deleteEditBtn.textContent = "Delete";
  }

  function syncToolbar() {
    const item = selected();
    if (editing) {
      nameEl.textContent = editingId ? "Edit script" : "New script";
      descEl.textContent = editingId ? editingId : "Saved on this Mac";
      setHidden(deleteEditBtn, !editingId);
      return;
    }
    nameEl.textContent = item ? item.name : "No script selected";
    descEl.textContent = item ? item.description || item.id : "Pick a script, or write one.";
    runBtn.disabled = !item || Boolean(item.error);
    setHidden(duplicateBtn, !item);
    setHidden(editBtn, !item || item.builtin);
    setHidden(deleteBtn, !item || item.builtin);
    if (!item || item.builtin) disarmDelete();
  }

  function renderList() {
    const query = (filter.value || "").trim().toLowerCase();
    list.replaceChildren();
    const groups = [
      ["Built-in", scripts.filter((item) => item.builtin)],
      ["Yours", scripts.filter((item) => !item.builtin)],
    ];
    let shown = 0;
    for (const [label, items] of groups) {
      const visible = items.filter((item) => {
        if (!query) return true;
        const haystack = [item.name, item.description, item.id, ...(item.tags || [])]
          .join(" ")
          .toLowerCase();
        return haystack.includes(query);
      });
      if (!visible.length) continue;
      shown += visible.length;
      list.append(el("div", "scratch-group", label));
      for (const item of visible) {
        const button = el("button", "scratch-item" + (item.id === selectedId ? " active" : ""));
        button.type = "button";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", item.id === selectedId ? "true" : "false");
        button.append(el("span", "scratch-item-name", item.name));
        const tags = (item.tags || []).join(" ");
        if (item.error) button.append(el("span", "scratch-item-tags", item.error));
        else if (tags) button.append(el("span", "scratch-item-tags", tags));
        button.title = item.description || item.name;
        button.addEventListener("click", () => choose(item.id));
        button.addEventListener("dblclick", () => {
          choose(item.id);
          runSelected();
        });
        list.append(button);
      }
    }
    if (!shown) {
      const empty = scripts.length
        ? "No scripts match."
        : "No scripts yet. New script saves one on this Mac.";
      list.append(el("p", "scratch-empty", empty));
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  async function load() {
    const response = await fetch("/api/tools/scripts");
    if (!response.ok) {
      showStatus([], { level: "error", text: "Could not load scripts." });
      return;
    }
    const body = await response.json();
    scripts = Array.isArray(body.scripts) ? body.scripts : [];
    if (selectedId && !scripts.some((item) => item.id === selectedId)) selectedId = "";
    renderList();
    syncToolbar();
  }

  function choose(id) {
    if (editing) showEditor(false);
    selectedId = id;
    try {
      localStorage.setItem(SELECT_KEY, id);
    } catch (error) {
      /* ignore */
    }
    renderList();
    syncToolbar();
    const item = selected();
    if (item && !item.error) {
      showStatus([], { level: "info", text: "Run " + item.name + ". Command-Enter runs it." });
    }
  }

  async function scriptSource(id) {
    const response = await fetch("/api/tools/scripts/" + encodeURIComponent(id));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorText(body, "Could not load the script."));
    return body.source || "";
  }

  function runScript(source, text, selection) {
    return new Promise((resolve) => {
      const workerSource = [
        "self.onmessage = (event) => {",
        "  const data = event.data || {};",
        "  const messages = [];",
        "  const state = {",
        "    text: data.selection ? data.selection : String(data.text ?? ''),",
        "    fullText: String(data.fullText ?? ''),",
        "    selection: String(data.selection ?? ''),",
        "    postInfo(message) { messages.push({ level: 'info', text: String(message) }); },",
        "    postError(message) { messages.push({ level: 'error', text: String(message) }); },",
        "    postWarning(message) { messages.push({ level: 'warn', text: String(message) }); },",
        "  };",
        "  try {",
        "    const run = new Function('state', String(data.source || '') + '\\n;if (typeof main === \"function\") main(state);');",
        "    run(state);",
        "    const next = state.text == null ? '' : String(state.text);",
        "    self.postMessage({ ok: true, text: next, messages });",
        "  } catch (error) {",
        "    self.postMessage({ ok: false, error: error && error.message ? String(error.message) : String(error), messages });",
        "  }",
        "};",
      ].join("\n");
      const blob = new Blob([workerSource], { type: "text/javascript" });
      const url = URL.createObjectURL(blob);
      const worker = new Worker(url);
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        worker.terminate();
        URL.revokeObjectURL(url);
        resolve(result);
      };
      const timer = window.setTimeout(() => {
        finish({ ok: false, error: "Script ran longer than 1.5 seconds.", messages: [] });
      }, RUN_MS);
      worker.onmessage = (event) => finish(event.data || { ok: false, error: "Script returned nothing.", messages: [] });
      worker.onerror = () => finish({ ok: false, error: "Script failed.", messages: [] });
      worker.postMessage({ source, text, fullText: text, selection });
    });
  }

  function applyResult(next) {
    const start = pad.selectionStart;
    const end = pad.selectionEnd;
    if (start !== end) {
      pad.value = pad.value.slice(0, start) + next + pad.value.slice(end);
      pad.selectionStart = start;
      pad.selectionEnd = start + next.length;
    } else {
      pad.value = next;
    }
    persistPad();
  }

  async function runSelected() {
    const item = selected();
    if (!item || editing) return;
    runBtn.disabled = true;
    try {
      const source = await scriptSource(item.id);
      const selection = pad.selectionStart !== pad.selectionEnd
        ? pad.value.slice(pad.selectionStart, pad.selectionEnd)
        : "";
      const result = await runScript(source, pad.value, selection);
      if (!result || !result.ok) {
        showStatus(result && result.messages, {
          level: "error",
          text: (result && result.error) || "Script failed.",
        });
        return;
      }
      applyResult(result.text);
      const messages = result.messages || [];
      showStatus(messages, messages.length ? null : { level: "info", text: "Ran " + item.name + "." });
    } catch (error) {
      showStatus([], { level: "error", text: error.message || "Could not run the script." });
    } finally {
      syncToolbar();
    }
  }

  function openEditor(id, source) {
    editingId = id;
    sourceBox.value = source;
    showEditor(true);
    syncToolbar();
    sourceBox.focus();
  }

  async function editSelected() {
    const item = selected();
    if (!item || item.builtin) return;
    try {
      openEditor(item.id, await scriptSource(item.id));
    } catch (error) {
      showStatus([], { level: "error", text: error.message || "Could not open the script." });
    }
  }

  async function duplicateSelected() {
    const item = selected();
    if (!item) return;
    try {
      const source = await scriptSource(item.id);
      const name = "Copy of " + item.name;
      const next = source.replace(
        /"name"\s*:\s*"(?:\\.|[^"\\])*"/,
        '"name": ' + JSON.stringify(name),
      );
      openEditor(null, next === source ? source : next);
      showStatus([], { level: "info", text: "Save to keep this copy." });
    } catch (error) {
      showStatus([], { level: "error", text: error.message || "Could not duplicate the script." });
    }
  }

  async function saveScript() {
    saveBtn.disabled = true;
    const creating = !editingId;
    const url = creating
      ? "/api/tools/scripts"
      : "/api/tools/scripts/" + encodeURIComponent(editingId);
    try {
      const response = await fetch(url, {
        method: creating ? "POST" : "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source: sourceBox.value }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        showStatus([], { level: "error", text: errorText(body, "Could not save the script.") });
        return;
      }
      selectedId = body.id || selectedId;
      try {
        localStorage.setItem(SELECT_KEY, selectedId);
      } catch (error) {
        /* ignore */
      }
      showEditor(false);
      await load();
      showStatus([], { level: "info", text: creating ? "Script saved." : "Script updated." });
    } catch (error) {
      showStatus([], { level: "error", text: "Could not save the script." });
    } finally {
      saveBtn.disabled = false;
    }
  }

  async function removeScript(id) {
    const response = await fetch("/api/tools/scripts/" + encodeURIComponent(id), { method: "DELETE" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus([], { level: "error", text: errorText(body, "Could not delete the script.") });
      return;
    }
    if (selectedId === id) selectedId = "";
    if (editingId === id) {
      editingId = null;
      showEditor(false);
    }
    await load();
    showStatus([], { level: "info", text: "Script deleted." });
  }

  function armDelete(button, id) {
    if (!deleteArmed) {
      deleteArmed = true;
      button.textContent = "Confirm delete";
      deleteTimer = window.setTimeout(() => {
        deleteArmed = false;
        button.textContent = "Delete";
      }, 3000);
      return;
    }
    disarmDelete();
    removeScript(id);
  }

  document.getElementById("scratch-new").addEventListener("click", () => {
    openEditor(null, TEMPLATE);
    showStatus([], { level: "info", text: "Name it in the header, then save." });
  });
  document.getElementById("scratch-run").addEventListener("click", runSelected);
  document.getElementById("scratch-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pad.value);
      showStatus([], { level: "info", text: "Copied." });
    } catch (error) {
      pad.focus();
      pad.select();
      showStatus([], { level: "warn", text: "Clipboard blocked. The pad is selected." });
    }
  });
  document.getElementById("scratch-clear").addEventListener("click", () => {
    pad.value = "";
    persistPad();
    showStatus([], { level: "info", text: "Pad cleared." });
  });
  editBtn.addEventListener("click", editSelected);
  duplicateBtn.addEventListener("click", duplicateSelected);
  deleteBtn.addEventListener("click", () => {
    if (selectedId) armDelete(deleteBtn, selectedId);
  });
  deleteEditBtn.addEventListener("click", () => {
    if (editingId) armDelete(deleteEditBtn, editingId);
  });
  document.getElementById("scratch-cancel").addEventListener("click", () => {
    showEditor(false);
    syncToolbar();
    showStatus([], { level: "info", text: "Edit discarded." });
  });
  saveBtn.addEventListener("click", saveScript);
  filter.addEventListener("input", renderList);
  filter.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runSelected();
    }
  });
  pad.addEventListener("input", persistPad);
  pad.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      runSelected();
    }
  });

  showEditor(false);
  syncToolbar();
  load();
})();
