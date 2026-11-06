/* Toolkit page: index, generic form renderer and output blocks. All output uses textContent. */
(() => {
  const kit = window.VLKit;
  const page = document.getElementById("tools-page");
  if (!kit || !page) return;

  const STORE_PREFIX = "vl-kit-v1:";
  const LAST_KEY = "vl-kit-last";
  const MODE_KEY = "vl-tools-mode";
  const MAX_SAVED_CHARS = 100000;

  const list = document.getElementById("kit-list");
  const filter = document.getElementById("kit-filter");
  const countEl = document.getElementById("kit-count");
  const nameEl = document.getElementById("kit-name");
  const descEl = document.getElementById("kit-desc");
  const form = document.getElementById("kit-form");
  const output = document.getElementById("kit-output");
  const runBtn = document.getElementById("kit-run");
  const resetBtn = document.getElementById("kit-reset");
  const kitStage = document.getElementById("kit-stage");
  const scratchStage = document.getElementById("scratch-stage");
  const kitIndex = document.getElementById("kit-index");
  const scratchIndex = document.getElementById("scratch-index");
  const modeButtons = Array.from(document.querySelectorAll("[data-tools-mode]"));

  let current = null;
  let values = {};
  let runSerial = 0;
  let debounceTimer = 0;
  let tickTimer = 0;
  const objectUrls = [];

  const ctx = {
    async server(action, payload) {
      const response = await fetch("/api/tools/action/" + encodeURIComponent(action), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof body.detail === "string" ? body.detail : "Request failed (" + response.status + ").");
      }
      return body;
    },
    objectUrl(blob) {
      const url = URL.createObjectURL(blob);
      objectUrls.push(url);
      return url;
    },
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function button(label, onClick, className) {
    const node = el("button", className || "btn ghost", label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function storage(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (error) {
      /* private mode or quota: state stays in memory */
    }
    return null;
  }

  /* Index */

  function matches(tool, query) {
    if (!query) return true;
    const haystack = [tool.name, tool.description, tool.slug, tool.keywords || ""].join(" ").toLowerCase();
    return query.split(/\s+/).every((word) => haystack.includes(word));
  }

  function renderIndex() {
    const query = (filter.value || "").trim().toLowerCase();
    list.replaceChildren();
    let shown = 0;
    for (const category of kit.categories) {
      const items = kit.tools.filter((tool) => tool.category === category.id && matches(tool, query));
      if (!items.length) continue;
      shown += items.length;
      const head = el("div", "scratch-group", category.name);
      head.append(el("span", "kit-group-count", items.length));
      list.append(head);
      for (const tool of items) {
        const item = el("a", "scratch-item" + (current && current.slug === tool.slug ? " active" : ""));
        item.href = "#tool/" + tool.slug;
        item.dataset.slug = tool.slug;
        if (current && current.slug === tool.slug) item.setAttribute("aria-current", "true");
        item.append(el("span", "scratch-item-name", tool.name));
        item.title = tool.description;
        list.append(item);
      }
    }
    if (!shown) list.append(el("p", "scratch-empty", "No tools match. Try a shorter word."));
    countEl.textContent = query ? shown + " of " + kit.tools.length : String(kit.tools.length);
  }

  /* Form */

  function defaultValue(input) {
    return typeof input.value === "function" ? input.value() : input.value;
  }

  function defaults(tool) {
    const result = {};
    for (const input of tool.inputs || []) {
      if (input.type === "file" || input.type === "files") result[input.id] = null;
      else if (input.type === "checkbox") result[input.id] = Boolean(defaultValue(input));
      else {
        const value = defaultValue(input);
        result[input.id] = value == null ? "" : value;
      }
    }
    return result;
  }

  function persistable(input) {
    return !input.secret && input.type !== "file" && input.type !== "files" && !input.transient;
  }

  function saveValues() {
    if (!current) return;
    const saved = {};
    for (const input of current.inputs || []) {
      if (!persistable(input)) continue;
      const value = values[input.id];
      if (typeof value === "string" && value.length > MAX_SAVED_CHARS) continue;
      saved[input.id] = value;
    }
    storage(STORE_PREFIX + current.slug, JSON.stringify(saved));
  }

  function loadValues(tool) {
    const base = defaults(tool);
    const raw = storage(STORE_PREFIX + tool.slug);
    if (!raw) return base;
    try {
      const saved = JSON.parse(raw);
      for (const input of tool.inputs || []) {
        if (persistable(input) && Object.prototype.hasOwnProperty.call(saved, input.id)) {
          base[input.id] = saved[input.id];
        }
      }
    } catch (error) {
      storage(STORE_PREFIX + tool.slug, null);
    }
    return base;
  }

  function options(input) {
    const source = typeof input.options === "function" ? input.options(values) : input.options || [];
    return source.map((option) => (Array.isArray(option) ? option : [option, option]));
  }

  function control(input) {
    const id = "kit-in-" + input.id;
    const field = el("div", "kit-field kit-field-" + input.type + (input.wide || input.type === "textarea" || input.type === "files" ? " wide" : ""));
    field.dataset.input = input.id;
    const label = el("label", "kit-label", input.label);
    label.htmlFor = id;
    let node;
    const value = values[input.id];
    switch (input.type) {
      case "textarea":
        node = el("textarea", "kit-textarea");
        node.rows = input.rows || 8;
        node.spellcheck = false;
        node.value = value == null ? "" : String(value);
        if (input.placeholder) node.placeholder = input.placeholder;
        break;
      case "select":
        node = el("select", "kit-select");
        for (const [optionValue, optionLabel] of options(input)) {
          const option = el("option", null, optionLabel);
          option.value = String(optionValue);
          node.append(option);
        }
        node.value = String(value);
        break;
      case "checkbox":
        node = el("input");
        node.type = "checkbox";
        node.checked = Boolean(value);
        field.classList.add("inline");
        break;
      case "file":
      case "files":
        node = el("input", "kit-file");
        node.type = "file";
        if (input.accept) node.accept = input.accept;
        if (input.type === "files") node.multiple = true;
        break;
      case "range":
        node = el("input", "kit-range");
        node.type = "range";
        break;
      case "color":
        node = el("input", "kit-color");
        node.type = "color";
        node.value = String(value || "#000000");
        break;
      case "number":
        node = el("input", "kit-input");
        node.type = "number";
        break;
      case "datetime":
        node = el("input", "kit-input");
        node.type = "datetime-local";
        node.step = "1";
        break;
      case "date":
        node = el("input", "kit-input");
        node.type = "date";
        break;
      case "password":
        node = el("input", "kit-input");
        node.type = "password";
        node.autocomplete = "off";
        break;
      default:
        node = el("input", "kit-input");
        node.type = "text";
        node.autocomplete = "off";
        node.spellcheck = false;
    }
    if (["range", "number"].includes(input.type)) {
      if (input.min != null) node.min = input.min;
      if (input.max != null) node.max = input.max;
      if (input.step != null) node.step = input.step;
    }
    if (!["textarea", "select", "checkbox", "file", "files", "color"].includes(input.type)) {
      node.value = value == null ? "" : String(value);
      if (input.placeholder) node.placeholder = input.placeholder;
    }
    node.id = id;
    if (input.type === "range") {
      const readout = el("span", "kit-range-value", value);
      label.append(" ", readout);
      node.addEventListener("input", () => {
        readout.textContent = node.value;
      });
    }
    const onChange = async () => {
      if (input.type === "checkbox") values[input.id] = node.checked;
      else if (input.type === "file" || input.type === "files") {
        const picked = Array.from(node.files || []);
        const loaded = await Promise.all(
          picked.map(async (file) => ({
            name: file.name,
            type: file.type,
            size: file.size,
            bytes: new Uint8Array(await file.arrayBuffer()),
          })),
        );
        values[input.id] = input.type === "files" ? loaded : loaded[0] || null;
      } else if (input.type === "number" || input.type === "range") {
        values[input.id] = node.value === "" ? "" : Number(node.value);
      } else values[input.id] = node.value;
      saveValues();
      syncVisibility();
      if (input.type === "select" && typeof input.onSelect === "function") input.onSelect(values, setValue);
      if (current.live !== false) scheduleRun(input.type === "textarea" || input.type === "text" ? 260 : 0);
    };
    node.addEventListener(input.type === "checkbox" || input.type === "select" || input.type === "file" || input.type === "files" ? "change" : "input", onChange);
    if (input.type === "textarea") {
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          run();
        } else if (event.key === "Tab" && !event.shiftKey && input.tabs !== false) {
          event.preventDefault();
          const start = node.selectionStart;
          node.setRangeText("  ", start, node.selectionEnd, "end");
          onChange();
        }
      });
    }
    if (input.type === "checkbox") {
      field.append(node, label);
    } else {
      field.append(label, node);
    }
    if (input.hint) field.append(el("span", "kit-hint", input.hint));
    if (input.type === "file" || input.type === "files") {
      const picked = values[input.id];
      if (picked) field.append(el("span", "kit-hint", Array.isArray(picked) ? picked.length + " files" : picked.name));
    }
    return field;
  }

  function setValue(id, value) {
    values[id] = value;
    const node = document.getElementById("kit-in-" + id);
    if (!node) return;
    if (node.type === "checkbox") node.checked = Boolean(value);
    else if (node.type !== "file") node.value = value == null ? "" : String(value);
  }

  function refreshOptions(input) {
    const node = document.getElementById("kit-in-" + input.id);
    if (!node) return;
    const next = options(input);
    const signature = next.map(([value]) => String(value)).join("\u0000");
    if (node.dataset.signature === signature) return;
    node.dataset.signature = signature;
    node.replaceChildren(...next.map(([optionValue, optionLabel]) => {
      const option = el("option", null, optionLabel);
      option.value = String(optionValue);
      return option;
    }));
    const wanted = String(values[input.id]);
    const match = next.find(([value]) => String(value) === wanted);
    values[input.id] = match ? match[0] : next.length ? next[0][0] : "";
    node.value = String(values[input.id]);
  }

  function syncVisibility() {
    for (const input of current.inputs || []) {
      if (input.type === "select" && typeof input.options === "function") refreshOptions(input);
      if (typeof input.show !== "function") continue;
      const field = form.querySelector('[data-input="' + input.id + '"]');
      if (field) field.hidden = !input.show(values);
    }
  }

  function buildForm() {
    form.replaceChildren();
    for (const input of current.inputs || []) form.append(control(input));
    syncVisibility();
  }

  /* Outputs */

  function clearObjectUrls() {
    while (objectUrls.length) URL.revokeObjectURL(objectUrls.pop());
  }

  function blockHead(block, actions) {
    const head = el("div", "kit-block-head");
    head.append(el("span", "kit-block-label", block.label || ""));
    const tools = el("div", "kit-block-actions");
    for (const action of actions) tools.append(action);
    head.append(tools);
    return head;
  }

  async function copyText(text, trigger) {
    try {
      await navigator.clipboard.writeText(text);
      flash(trigger, "Copied");
    } catch (error) {
      flash(trigger, "Copy blocked");
    }
  }

  function flash(trigger, text) {
    if (!trigger) return;
    const original = trigger.dataset.label || trigger.textContent;
    trigger.dataset.label = original;
    trigger.textContent = text;
    setTimeout(() => {
      trigger.textContent = original;
    }, 1200);
  }

  function textBlock(block) {
    const wrap = el("section", "kit-block");
    const value = block.value == null ? "" : String(block.value);
    const actions = [];
    const copy = button("Copy", () => copyText(value, copy));
    actions.push(copy);
    if (block.download) {
      actions.push(button("Download", () => kit.util.download(new Blob([value], { type: block.mime || "text/plain" }), block.download)));
    }
    wrap.append(blockHead(block, actions));
    const pre = el("pre", "kit-pre" + (block.wrap === false ? " nowrap" : ""), value || block.empty || "");
    if (!value && block.empty) pre.classList.add("muted");
    wrap.append(pre);
    return wrap;
  }

  function tableBlock(block) {
    const wrap = el("section", "kit-block");
    const rows = block.rows || [];
    const actions = [];
    if (rows.length && block.type === "table") {
      const csv = kit.util.toCSV([block.columns || [], ...rows]);
      const copy = button("Copy CSV", () => copyText(csv, copy));
      actions.push(copy);
    }
    if (block.type === "kv" && rows.length) {
      const textValue = rows.map((row) => row[0] + ": " + row[1]).join("\n");
      const copy = button("Copy", () => copyText(textValue, copy));
      actions.push(copy);
    }
    wrap.append(blockHead(block, actions));
    if (!rows.length) {
      wrap.append(el("p", "kit-empty", block.empty || "Nothing to show."));
      return wrap;
    }
    const scroller = el("div", "kit-table-wrap");
    const table = el("table", "kit-table" + (block.type === "kv" ? " kv" : ""));
    if (block.columns && block.type === "table") {
      const thead = el("thead");
      const tr = el("tr");
      for (const column of block.columns) tr.append(el("th", null, column));
      thead.append(tr);
      table.append(thead);
    }
    const tbody = el("tbody");
    const limit = block.limit || 2000;
    for (const row of rows.slice(0, limit)) {
      const tr = el("tr");
      if (block.rowClass) {
        const cls = block.rowClass(row);
        if (cls) tr.className = cls;
      }
      row.forEach((cell, index) => {
        const td = el(block.type === "kv" && index === 0 ? "th" : "td", null, cell == null ? "" : cell);
        if (typeof cell === "number") td.classList.add("num");
        tr.append(td);
      });
      tbody.append(tr);
    }
    table.append(tbody);
    scroller.append(table);
    wrap.append(scroller);
    if (rows.length > limit) wrap.append(el("p", "kit-empty", "Showing " + limit + " of " + rows.length + " rows."));
    return wrap;
  }

  function treeNode(key, value, depth) {
    const isObject = value !== null && typeof value === "object";
    if (!isObject) {
      const row = el("div", "kit-tree-leaf");
      if (key !== null) row.append(el("span", "kit-tree-key", key + ": "));
      const type = value === null ? "null" : typeof value;
      row.append(el("span", "kit-tree-" + type, type === "string" ? JSON.stringify(value) : String(value)));
      return row;
    }
    const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
    const details = el("details", "kit-tree-node");
    details.open = depth < 2;
    const summary = el("summary");
    if (key !== null) summary.append(el("span", "kit-tree-key", key + ": "));
    summary.append(el("span", "kit-tree-meta", Array.isArray(value) ? "[" + entries.length + "]" : "{" + entries.length + "}"));
    details.append(summary);
    let built = false;
    const build = () => {
      if (built) return;
      built = true;
      const shown = entries.slice(0, 500);
      for (const [childKey, childValue] of shown) details.append(treeNode(childKey, childValue, depth + 1));
      if (entries.length > shown.length) details.append(el("div", "kit-tree-leaf muted", "… " + (entries.length - shown.length) + " more"));
    };
    if (details.open) build();
    details.addEventListener("toggle", build);
    return details;
  }

  function treeBlock(block) {
    const wrap = el("section", "kit-block");
    const json = JSON.stringify(block.value, null, 2);
    const copy = button("Copy JSON", () => copyText(json, copy));
    wrap.append(blockHead(block, [copy]));
    const tree = el("div", "kit-tree");
    tree.append(treeNode(null, block.value, 0));
    wrap.append(tree);
    return wrap;
  }

  function diffBlock(block) {
    const wrap = el("section", "kit-block");
    const textValue = (block.lines || []).map((line) => line.op + " " + line.text).join("\n");
    const copy = button("Copy", () => copyText(textValue, copy));
    wrap.append(blockHead(block, [copy]));
    const pre = el("pre", "kit-pre kit-diff");
    for (const line of block.lines || []) {
      const row = el("span", line.op === "+" ? "add" : line.op === "-" ? "del" : "same");
      const gutter = el("span", "gutter", (line.a || "") + "\t" + (line.b || ""));
      row.append(gutter, document.createTextNode(line.op + " " + line.text + "\n"));
      pre.append(row);
    }
    wrap.append(pre);
    return wrap;
  }

  function highlightBlock(block) {
    const wrap = el("section", "kit-block");
    wrap.append(blockHead(block, []));
    const pre = el("pre", "kit-pre");
    const textValue = String(block.text || "");
    let cursor = 0;
    for (const [start, end] of block.ranges || []) {
      if (start < cursor) continue;
      pre.append(document.createTextNode(textValue.slice(cursor, start)));
      pre.append(el("mark", null, textValue.slice(start, Math.max(end, start))));
      cursor = Math.max(end, start);
      if (end === start) cursor = start;
    }
    pre.append(document.createTextNode(textValue.slice(cursor)));
    wrap.append(pre);
    return wrap;
  }

  function previewBlock(block) {
    const wrap = el("section", "kit-block");
    wrap.append(blockHead(block, []));
    const stage = el("div", "kit-preview" + (block.checker ? " checker" : ""));
    const box = el("div", "kit-preview-box");
    box.style.cssText = block.css || "";
    const count = block.items || 0;
    for (let i = 0; i < count; i += 1) {
      const item = el("div", "kit-preview-item", block.itemLabel ? block.itemLabel(i) : i + 1);
      if (block.itemCss) item.style.cssText = typeof block.itemCss === "function" ? block.itemCss(i) : block.itemCss;
      box.append(item);
    }
    if (block.text) box.textContent = block.text;
    stage.append(box);
    wrap.append(stage);
    return wrap;
  }

  function swatchBlock(block) {
    const wrap = el("section", "kit-block");
    wrap.append(blockHead(block, []));
    const grid = el("div", "kit-swatches");
    for (const color of block.colors || []) {
      const cell = el("button", "kit-swatch");
      cell.type = "button";
      const chip = el("span", "kit-swatch-chip");
      chip.style.background = color.css || color.hex;
      cell.append(chip, el("span", "kit-swatch-label", color.label || color.hex));
      if (color.sub) cell.append(el("span", "kit-swatch-sub", color.sub));
      cell.title = "Copy " + (color.copy || color.hex);
      cell.addEventListener("click", () => copyText(color.copy || color.hex, cell.querySelector(".kit-swatch-label")));
      grid.append(cell);
    }
    wrap.append(grid);
    return wrap;
  }

  function imageBlock(block) {
    const wrap = el("section", "kit-block");
    const actions = [];
    if (block.blob && block.download) {
      actions.push(button("Download", () => kit.util.download(block.blob, block.download)));
    }
    wrap.append(blockHead(block, actions));
    const frame = el("div", "kit-image checker");
    const img = el("img");
    img.alt = block.alt || block.label || "Result";
    img.src = block.src || (block.blob ? ctx.objectUrl(block.blob) : "");
    if (block.maxWidth) img.style.maxWidth = block.maxWidth;
    frame.append(img);
    wrap.append(frame);
    if (block.meta) wrap.append(el("p", "kit-empty", block.meta));
    return wrap;
  }

  function filesBlock(block) {
    const wrap = el("section", "kit-block");
    const files = block.files || [];
    const actions = [];
    if (files.length > 1) {
      actions.push(button("Download all (.zip)", async () => {
        const entries = await Promise.all(files.map(async (file) => ({ name: file.name, bytes: new Uint8Array(await file.blob.arrayBuffer()) })));
        kit.util.download(kit.util.makeZip(entries), block.zipName || "files.zip");
      }));
    }
    wrap.append(blockHead(block, actions));
    const table = el("table", "kit-table");
    const tbody = el("tbody");
    for (const file of files) {
      const tr = el("tr");
      tr.append(el("td", null, file.name), el("td", "num", kit.util.formatBytes(file.blob.size)));
      const cell = el("td");
      cell.append(button("Download", () => kit.util.download(file.blob, file.name.split("/").pop())));
      tr.append(cell);
      tbody.append(tr);
    }
    table.append(tbody);
    wrap.append(table);
    return wrap;
  }

  function noteBlock(block) {
    return el("p", "kit-note " + (block.level || "info"), block.text);
  }

  function renderOutputs(result) {
    clearObjectUrls();
    output.replaceChildren();
    const blocks = result == null ? [] : Array.isArray(result) ? result : typeof result === "string" ? [{ type: "text", value: result }] : [result];
    for (const block of blocks) {
      if (!block) continue;
      const type = block.type || "text";
      let node;
      if (type === "text") node = textBlock(block);
      else if (type === "table" || type === "kv") node = tableBlock(block);
      else if (type === "tree") node = treeBlock(block);
      else if (type === "diff") node = diffBlock(block);
      else if (type === "highlight") node = highlightBlock(block);
      else if (type === "preview") node = previewBlock(block);
      else if (type === "swatches") node = swatchBlock(block);
      else if (type === "image") node = imageBlock(block);
      else if (type === "files") node = filesBlock(block);
      else node = noteBlock(block);
      output.append(node);
    }
    if (!output.childNodes.length) output.append(el("p", "kit-empty", "Nothing to show yet."));
  }

  function renderError(error) {
    clearObjectUrls();
    output.replaceChildren(el("p", "kit-note error", error && error.message ? error.message : String(error)));
  }

  function scheduleRun(delay) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(run, delay || 0);
  }

  async function run() {
    if (!current) return;
    const serial = ++runSerial;
    const tool = current;
    const slow = setTimeout(() => {
      if (serial === runSerial) output.replaceChildren(el("p", "kit-empty", tool.busy || "Working…"));
    }, 150);
    runBtn.disabled = true;
    try {
      const result = await tool.run({ ...values }, ctx);
      if (serial !== runSerial || tool !== current) return;
      renderOutputs(result);
    } catch (error) {
      if (serial !== runSerial || tool !== current) return;
      renderError(error);
    } finally {
      clearTimeout(slow);
      if (serial === runSerial) runBtn.disabled = false;
    }
  }

  function select(slug, options) {
    const tool = kit.bySlug.get(slug) || kit.tools[0];
    if (!tool) return;
    current = tool;
    values = loadValues(tool);
    storage(LAST_KEY, tool.slug);
    nameEl.textContent = tool.name;
    descEl.textContent = tool.description;
    runBtn.textContent = tool.action || (tool.live === false ? "Run" : "Run again");
    document.title = tool.name + " · Tools · Viewlift AI";
    buildForm();
    renderIndex();
    output.replaceChildren();
    clearInterval(tickTimer);
    if (tool.tick) tickTimer = setInterval(() => document.visibilityState === "visible" && current === tool && run(), tool.tick);
    if (tool.live !== false || tool.autorun) run();
    else output.append(el("p", "kit-empty", "Set the options, then " + (tool.action || "Run").toLowerCase() + "."));
    if (!(options && options.keepScroll)) {
      const active = list.querySelector(".scratch-item.active");
      if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
    }
  }

  function setMode(mode) {
    const scratch = mode === "scratch";
    kitStage.hidden = scratch;
    kitIndex.hidden = scratch;
    scratchStage.hidden = !scratch;
    scratchIndex.hidden = !scratch;
    page.dataset.mode = scratch ? "scratch" : "kit";
    for (const node of modeButtons) {
      const active = node.dataset.toolsMode === mode;
      node.classList.toggle("active", active);
      node.setAttribute("aria-pressed", active ? "true" : "false");
    }
    storage(MODE_KEY, mode);
    if (scratch) {
      clearInterval(tickTimer);
      document.title = "Scratchpad · Tools · Viewlift AI";
    } else if (current) {
      select(current.slug, { keepScroll: true });
    }
  }

  function route() {
    const hash = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (hash === "scratchpad") {
      setMode("scratch");
      return;
    }
    const slug = hash.startsWith("tool/") ? hash.slice(5) : "";
    if (slug && kit.bySlug.has(slug)) {
      if (page.dataset.mode !== "kit") setMode("kit");
      if (!current || current.slug !== slug) select(slug);
      return;
    }
    if (!current) select(storage(LAST_KEY) || "json-formatter");
  }

  list.addEventListener("click", (event) => {
    const item = event.target.closest(".scratch-item");
    if (!item) return;
    event.preventDefault();
    if (location.hash !== "#tool/" + item.dataset.slug) history.replaceState(null, "", "#tool/" + item.dataset.slug);
    select(item.dataset.slug, { keepScroll: true });
  });
  filter.addEventListener("input", renderIndex);
  filter.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const first = list.querySelector(".scratch-item");
      if (first) {
        event.preventDefault();
        history.replaceState(null, "", "#tool/" + first.dataset.slug);
        select(first.dataset.slug);
      }
    }
  });
  runBtn.addEventListener("click", run);
  resetBtn.addEventListener("click", () => {
    if (!current) return;
    storage(STORE_PREFIX + current.slug, null);
    select(current.slug, { keepScroll: true });
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run();
  });
  for (const node of modeButtons) {
    node.addEventListener("click", () => {
      const mode = node.dataset.toolsMode;
      history.replaceState(null, "", mode === "scratch" ? "#scratchpad" : "#tool/" + (current ? current.slug : "json-formatter"));
      setMode(mode);
    });
  }
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
    if (event.key === "/" && !typing && page.dataset.mode === "kit") {
      event.preventDefault();
      filter.focus();
      filter.select();
    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && page.dataset.mode === "kit" && target && target.tagName !== "TEXTAREA") {
      event.preventDefault();
      run();
    }
  });
  window.addEventListener("hashchange", route);

  renderIndex();
  const savedMode = storage(MODE_KEY);
  if (location.hash === "#scratchpad" || (!location.hash && savedMode === "scratch")) {
    route();
    if (!current) {
      current = kit.bySlug.get(storage(LAST_KEY) || "json-formatter") || kit.tools[0];
      renderIndex();
    }
    setMode("scratch");
  } else {
    setMode("kit");
    route();
  }
})();
