/* Collapsible JSON tree. Object and array text becomes a tree. Copy stays available. */
(() => {
  const MAX_CHILDREN = 100;
  const MAX_MATCHES = 150;
  const MAX_EXPAND = 2000;
  const MAX_STRING = 180;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseJson(text) {
    if (typeof text !== "string") return null;
    const trimmed = text.trim();
    if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
    try {
      const value = JSON.parse(trimmed);
      if (value === null || typeof value !== "object") return null;
      return value;
    } catch {
      return null;
    }
  }

  function at(value, path) {
    let current = value;
    for (const key of path) {
      if (current === null || typeof current !== "object") return undefined;
      current = current[key];
    }
    return current;
  }

  function entriesOf(value) {
    return Array.isArray(value)
      ? value.map((item, index) => [index, item])
      : Object.entries(value);
  }

  function scalarHtml(value) {
    if (value === null) return `<span class="jt-null">null</span>`;
    if (typeof value === "string") {
      const shown = value.length > MAX_STRING ? `${value.slice(0, MAX_STRING)}…` : value;
      return `<span class="jt-string">${escapeHtml(JSON.stringify(shown))}</span>`;
    }
    if (typeof value === "number") return `<span class="jt-number">${escapeHtml(String(value))}</span>`;
    if (typeof value === "boolean") return `<span class="jt-boolean">${value ? "true" : "false"}</span>`;
    return `<span class="jt-null">${escapeHtml(String(value))}</span>`;
  }

  function copyButton(path, label) {
    return `<button type="button" class="jt-copy" data-json-node-copy data-json-path="${escapeHtml(JSON.stringify(path))}" aria-label="${escapeHtml(label)}">Copy</button>`;
  }

  function childrenHtml(entries, path, depth, openDepth) {
    const shown = entries.slice(0, MAX_CHILDREN);
    let html = "";
    for (const [key, item] of shown) {
      html += nodeHtml(key, item, path.concat(key), depth + 1, openDepth);
    }
    if (entries.length > shown.length) {
      html += `<p class="jt-more">${entries.length - shown.length} more. Copy JSON for the full value.</p>`;
    }
    return html;
  }

  function nodeHtml(key, value, path, depth, openDepth) {
    const pathAttr = escapeHtml(JSON.stringify(path));
    const label = key == null ? "Copy JSON" : `Copy ${key}`;
    const copy = copyButton(path, label);
    const keyHtml = key == null ? "" : `<span class="jt-key">${escapeHtml(String(key))}</span>`;
    if (value === null || typeof value !== "object") {
      return `<div class="jt-leaf" data-json-path="${pathAttr}">${keyHtml}${scalarHtml(value)}${copy}</div>`;
    }
    const entries = entriesOf(value);
    const meta = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;
    const summary = `<summary>${keyHtml}<span class="jt-meta">${meta}</span>${copy}</summary>`;
    const open = depth < openDepth;
    if (!open) {
      return `<details class="jt-node" data-jt-depth="${depth}" data-jt-default-open="0" data-json-lazy="1" data-json-path="${pathAttr}">${summary}</details>`;
    }
    return `<details class="jt-node" open data-jt-depth="${depth}" data-jt-default-open="1" data-json-path="${pathAttr}">${summary}${childrenHtml(entries, path, depth, openDepth)}</details>`;
  }

  function html(text, options) {
    const value = parseJson(text);
    if (!value) return "";
    const settings = options || {};
    const openDepth = Number.isFinite(settings.openDepth) ? settings.openDepth : 2;
    const toolbar = settings.toolbar !== false;
    const bar = toolbar
      ? `<div class="json-tree-bar">
          <input type="search" data-json-filter placeholder="Filter keys and values" aria-label="Filter JSON" autocomplete="off">
          <button type="button" data-json-expand>Expand</button>
          <button type="button" data-json-collapse>Collapse</button>
          ${settings.copyBar ? `<button type="button" data-json-copy-all>Copy JSON</button>` : ""}
          <span class="jt-filter-note muted" hidden></span>
        </div>`
      : "";
    return `<div class="json-tree" data-json-tree data-json-open="${openDepth}">
      <textarea hidden readonly class="json-tree-source">${escapeHtml(JSON.stringify(value))}</textarea>
      ${bar}
      <div class="json-tree-body">${nodeHtml(null, value, [], 0, openDepth)}</div>
    </div>`;
  }

  function sourceOf(tree) {
    if (Object.prototype.hasOwnProperty.call(tree, "_vlJson")) return tree._vlJson;
    const box = tree.querySelector(".json-tree-source");
    tree._vlJson = box ? JSON.parse(box.value || box.textContent || "null") : null;
    return tree._vlJson;
  }

  function fill(details) {
    if (!details || details.dataset.jsonBuilt === "1" || details.dataset.jsonLazy !== "1") return;
    const tree = details.closest("[data-json-tree]");
    if (!tree) return;
    const path = JSON.parse(details.dataset.jsonPath || "[]");
    const value = at(sourceOf(tree), path);
    if (value === null || typeof value !== "object") return;
    const depth = Number(details.dataset.jtDepth || 0);
    const openDepth = Number(tree.dataset.jsonOpen || 2);
    details.insertAdjacentHTML("beforeend", childrenHtml(entriesOf(value), path, depth, openDepth));
    details.dataset.jsonBuilt = "1";
    details.dataset.jsonLazy = "0";
  }

  function expandAll(tree) {
    if (!tree) return;
    const queue = [...tree.querySelectorAll("details.jt-node")];
    let guard = 0;
    while (queue.length && guard < MAX_EXPAND) {
      const node = queue.shift();
      fill(node);
      node.open = true;
      node.querySelectorAll(":scope > details.jt-node").forEach((child) => queue.push(child));
      guard += 1;
    }
  }

  function collapseAll(tree) {
    if (!tree) return;
    tree.querySelectorAll("details.jt-node").forEach((node) => {
      node.open = false;
    });
    const root = tree.querySelector(".json-tree-body > details.jt-node");
    if (root && tree.dataset.jsonOpen !== "0") root.open = true;
  }

  function collect(value, key, path, query, found) {
    if (found.length >= MAX_MATCHES) return;
    const keyHit = key != null && String(key).toLowerCase().includes(query);
    if (value !== null && typeof value === "object") {
      if (keyHit) found.push(path);
      for (const [childKey, child] of entriesOf(value)) {
        collect(child, childKey, path.concat(childKey), query, found);
        if (found.length >= MAX_MATCHES) return;
      }
      return;
    }
    const text = value === null ? "null" : String(value).toLowerCase();
    if (keyHit || text.includes(query)) found.push(path);
  }

  function reveal(tree, path) {
    let parent = tree.querySelector(".json-tree-body > .jt-node, .json-tree-body > .jt-leaf");
    if (!parent) return;
    parent.hidden = false;
    const acc = [];
    for (const segment of path) {
      acc.push(segment);
      const want = JSON.stringify(acc);
      if (parent.tagName === "DETAILS") fill(parent);
      let node = [...parent.children].find((el) => el.dataset && el.dataset.jsonPath === want);
      if (!node) {
        const value = at(sourceOf(tree), acc);
        if (value === undefined) return;
        const openDepth = Number(tree.dataset.jsonOpen || 2);
        parent.insertAdjacentHTML("beforeend", nodeHtml(acc[acc.length - 1], value, acc.slice(), acc.length, openDepth));
        node = [...parent.children].find((el) => el.dataset && el.dataset.jsonPath === want);
        if (!node) return;
      }
      node.hidden = false;
      if (node.tagName === "DETAILS") {
        fill(node);
        node.open = true;
        parent = node;
      }
    }
    if (parent.dataset && parent.dataset.jsonPath === JSON.stringify(path)) {
      [...parent.children].forEach((el) => {
        if (el.classList && (el.classList.contains("jt-leaf") || el.classList.contains("jt-node"))) {
          el.hidden = false;
        }
      });
    }
  }

  function applyFilter(tree, query) {
    if (!tree) return;
    const note = tree.querySelector(".jt-filter-note");
    const q = String(query || "").trim().toLowerCase();
    const nodes = () => tree.querySelectorAll(".jt-leaf, .jt-node");
    if (!q) {
      nodes().forEach((node) => { node.hidden = false; });
      tree.querySelectorAll("details.jt-node").forEach((node) => {
        node.open = node.dataset.jtDefaultOpen === "1";
      });
      if (note) note.hidden = true;
      return;
    }
    const found = [];
    collect(sourceOf(tree), null, [], q, found);
    nodes().forEach((node) => { node.hidden = true; });
    const root = tree.querySelector(".json-tree-body > .jt-node, .json-tree-body > .jt-leaf");
    if (root) root.hidden = false;
    found.forEach((path) => reveal(tree, path));
    if (note) {
      note.hidden = false;
      if (!found.length) note.textContent = "No matches.";
      else if (found.length >= MAX_MATCHES) note.textContent = `First ${MAX_MATCHES} matches.`;
      else note.textContent = `${found.length} match${found.length === 1 ? "" : "es"}.`;
    }
  }

  function flash(button, text) {
    const label = button.dataset.label || button.textContent;
    button.dataset.label = label;
    button.textContent = text;
    window.setTimeout(() => { button.textContent = label; }, 1200);
  }

  async function write(button, text) {
    try {
      await navigator.clipboard.writeText(text);
      flash(button, "Copied");
    } catch {
      flash(button, "Copy failed");
    }
  }

  document.addEventListener("toggle", (event) => {
    const details = event.target;
    if (!details || !details.classList || !details.classList.contains("jt-node") || !details.open) return;
    fill(details);
  }, true);

  document.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target : null;
    if (!target || !target.closest) return;
    const copyNode = target.closest("[data-json-node-copy]");
    if (copyNode) {
      event.preventDefault();
      event.stopPropagation();
      const tree = copyNode.closest("[data-json-tree]");
      if (!tree) return;
      const path = JSON.parse(copyNode.dataset.jsonPath || "[]");
      write(copyNode, JSON.stringify(at(sourceOf(tree), path), null, 2));
      return;
    }
    const copyAll = target.closest("[data-json-copy-all]");
    if (copyAll) {
      const tree = copyAll.closest("[data-json-tree]");
      if (!tree) return;
      write(copyAll, JSON.stringify(sourceOf(tree), null, 2));
      return;
    }
    const expand = target.closest("[data-json-expand]");
    if (expand) {
      expandAll(expand.closest("[data-json-tree]"));
      return;
    }
    const collapse = target.closest("[data-json-collapse]");
    if (collapse) collapseAll(collapse.closest("[data-json-tree]"));
  });

  document.addEventListener("input", (event) => {
    const input = event.target && event.target.closest && event.target.closest("[data-json-filter]");
    if (!input) return;
    applyFilter(input.closest("[data-json-tree]"), input.value);
  });

  window.VLJsonTree = { html };
})();
