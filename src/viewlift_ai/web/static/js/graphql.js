/* GraphQL playground. Saved operations stay in this browser. Passwords stay on Setup. */
(() => {
  const PREFILL_KEY = "vl-graphql-prefill";
  const STORAGE_PREFIX = "vl-graphql-v1:";
  const HOP = new Set([
    "content-length", "host", "connection", "transfer-encoding", "keep-alive",
  ]);

  const state = {
    endpoints: [],
    schema: null,
    schemaKey: "",
    storageKey: "",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function originOf(url) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
      return `${parsed.protocol}//${parsed.hostname.toLowerCase()}:${port}`;
    } catch {
      return "";
    }
  }

  function selectedEndpoint() {
    const id = $("gql-endpoint").value;
    return state.endpoints.find((item) => item.id === id) || null;
  }

  function storageKey() {
    const endpoint = selectedEndpoint();
    if (endpoint) return STORAGE_PREFIX + endpoint.id;
    return STORAGE_PREFIX + "url:" + (originOf($("gql-url").value) || "custom");
  }

  function readStore(key) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return { draft: null, operations: [] };
      const parsed = JSON.parse(raw);
      const operations = Array.isArray(parsed.operations) ? parsed.operations : [];
      return { draft: parsed.draft || null, operations };
    } catch {
      return { draft: null, operations: [] };
    }
  }

  function writeStore(key, record) {
    try {
      localStorage.setItem(key, JSON.stringify(record));
    } catch {
      /* This browser refused the write. The request can still run. */
    }
  }

  function currentDocument() {
    return {
      query: $("gql-query").value,
      variables: $("gql-variables").value,
      headers: $("gql-headers").value,
      operationName: $("gql-operation").value,
    };
  }

  function applyDocument(doc) {
    if (!doc) return;
    $("gql-query").value = doc.query || "";
    $("gql-variables").value = doc.variables || "{}";
    $("gql-headers").value = doc.headers || "";
    $("gql-operation").value = doc.operationName || "";
  }

  function saveDraft(key) {
    if (!key) return;
    const record = readStore(key);
    record.draft = currentDocument();
    writeStore(key, record);
  }

  function setNote(text) {
    $("gql-note").textContent = text || "";
  }

  function parseHeaders(text) {
    const headers = {};
    for (const line of String(text || "").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const splitAt = trimmed.indexOf(":");
      if (splitAt <= 0) throw new Error("Each header needs a name and a value.");
      const key = trimmed.slice(0, splitAt).trim();
      const value = trimmed.slice(splitAt + 1).trim();
      if (!key || HOP.has(key.toLowerCase())) continue;
      headers[key] = value;
    }
    return headers;
  }

  function parseVariables(text) {
    const source = String(text || "").trim() || "{}";
    let variables;
    try {
      variables = JSON.parse(source);
    } catch {
      throw new Error("Variables must be JSON.");
    }
    if (variables === null || typeof variables !== "object" || Array.isArray(variables)) {
      throw new Error("Variables must be a JSON object.");
    }
    return variables;
  }

  function hostedPlaygroundUrl(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const withScheme = text.includes("://") ? text : `https://${text}`;
    try {
      const parsed = new URL(withScheme);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (parsed.username || parsed.password) return "";
      return `${parsed.protocol}//${parsed.host}/playground`;
    } catch {
      return "";
    }
  }

  function updateHostedLink() {
    const link = $("gql-hosted");
    const url = hostedPlaygroundUrl($("gql-url").value);
    if (!url) {
      link.hidden = true;
      link.removeAttribute("href");
      return;
    }
    link.hidden = false;
    link.href = url;
  }

  async function detectedGraphqlUrl() {
    const params = new URLSearchParams();
    if (window.VLScope) window.VLScope.applyTo(params);
    const query = params.toString();
    try {
      const res = await fetch(query ? `/api/graphql/bases?${query}` : "/api/graphql/bases");
      const data = await res.json();
      const first = res.ok && data.bases && data.bases[0];
      if (!first || !first.base) return "";
      return `${String(first.base).replace(/\/$/, "")}/graphql`;
    } catch {
      return "";
    }
  }

  function fillEndpointSelect() {
    const select = $("gql-endpoint");
    const current = select.value;
    const options = [`<option value="">Custom URL</option>`].concat(
      state.endpoints.map((item) => {
        const label = item.name || item.url;
        return `<option value="${escapeHtml(item.id)}">${escapeHtml(label)}</option>`;
      }),
    );
    select.innerHTML = options.join("");
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  function renderSaved(operations, selectedId) {
    const select = $("gql-saved");
    const options = [`<option value="">Saved operations</option>`].concat(
      operations.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || "Untitled")}</option>`),
    );
    select.innerHTML = options.join("");
    if (selectedId) select.value = selectedId;
  }

  function loadSavedList() {
    const record = readStore(storageKey());
    renderSaved(record.operations, $("gql-saved").value);
  }

  function showTab(name) {
    const tabs = [
      ["query", "gql-query"],
      ["variables", "gql-variables"],
      ["headers", "gql-headers"],
    ];
    for (const [tab, editorId] of tabs) {
      const on = tab === name;
      $(`gql-tab-${tab}`).setAttribute("aria-selected", on ? "true" : "false");
      $(editorId).hidden = !on;
    }
    $("gql-header-note").hidden = name !== "headers";
  }

  function showSchema(on) {
    $("gql-schema").setAttribute("aria-pressed", on ? "true" : "false");
    $("gql-schema-panel").hidden = !on;
    $("gql-output").hidden = on;
    $("gql-schema-reload").hidden = !on;
    $("gql-status").textContent = on ? "Schema" : ($("gql-status").dataset.status || "Response");
  }

  async function loadEndpoints() {
    const res = await fetch("/api/graphql/endpoints");
    const data = await res.json();
    if (!res.ok) {
      setNote(data.detail || "Could not load endpoints.");
      state.endpoints = [];
    } else {
      state.endpoints = data.endpoints || [];
    }
    fillEndpointSelect();
    if (!state.endpoints.length) {
      setNote("Add an endpoint on Setup to store a username and password. A custom URL still runs.");
    }
    updateHostedLink();
  }

  function chooseEndpoint(id, { fillUrl, loadDraft }) {
    saveDraft(state.storageKey);
    $("gql-endpoint").value = id || "";
    const endpoint = selectedEndpoint();
    if (fillUrl && endpoint) $("gql-url").value = endpoint.url;
    updateHostedLink();
    state.storageKey = storageKey();
    if (loadDraft) {
      const record = readStore(state.storageKey);
      if (record.draft) applyDocument(record.draft);
      renderSaved(record.operations, "");
      $("gql-save-name").value = "";
    } else {
      loadSavedList();
    }
    state.schema = null;
    state.schemaKey = "";
  }

  async function applyPrefill() {
    let raw = null;
    try {
      raw = sessionStorage.getItem(PREFILL_KEY);
      if (raw) sessionStorage.removeItem(PREFILL_KEY);
    } catch {
      raw = null;
    }
    if (!raw) {
      const first = state.endpoints[0];
      if (first) chooseEndpoint(first.id, { fillUrl: true, loadDraft: true });
      else {
        chooseEndpoint("", { fillUrl: false, loadDraft: true });
        if (!$("gql-url").value.trim()) {
          const detected = await detectedGraphqlUrl();
          if (detected) $("gql-url").value = detected;
        }
      }
      updateHostedLink();
      return;
    }
    let prefill;
    try {
      prefill = JSON.parse(raw);
    } catch {
      return;
    }
    const target = originOf(prefill.url || "");
    const match = state.endpoints.find((item) => originOf(item.url) === target);
    chooseEndpoint(match ? match.id : "", { fillUrl: false, loadDraft: false });
    $("gql-url").value = prefill.url || "";
    applyDocument({
      query: prefill.query || "",
      variables: prefill.variables || "{}",
      headers: prefill.headers || "",
      operationName: prefill.operation || "",
    });
    updateHostedLink();
    state.storageKey = storageKey();
    setNote(match ? "Filled from the captured request." : "Filled from the captured request. No saved endpoint matches this host.");
  }

  function requestBody() {
    return {
      endpoint_id: $("gql-endpoint").value,
      url: $("gql-url").value.trim(),
      query: $("gql-query").value,
      variables: parseVariables($("gql-variables").value),
      operation_name: $("gql-operation").value.trim(),
      headers: parseHeaders($("gql-headers").value),
    };
  }

  function showResult(data, fallback) {
    const status = $("gql-status");
    const code = Number(data && data.status);
    status.dataset.status = Number.isFinite(code) ? String(code) : "Response";
    if ($("gql-schema").getAttribute("aria-pressed") !== "true") status.textContent = status.dataset.status;
    status.classList.toggle("ok", code >= 200 && code < 300 && !(data && data.graphql_errors));
    status.classList.toggle("err", code >= 400 || !!(data && data.graphql_errors) || !data);
    const elapsed = data && Number.isFinite(data.elapsed_ms) ? `${data.elapsed_ms} ms` : "";
    $("gql-time").textContent = elapsed;
    const notes = [];
    if (data && data.auth_note) notes.push(data.auth_note);
    if (data && data.html_hint) notes.push(data.html_hint);
    if (data && data.truncated) notes.push("Response truncated at 2 MB.");
    if (data && data.graphql_errors) notes.push("GraphQL returned errors.");
    if (fallback) notes.push(fallback);
    setNote(notes.join(" "));
    let body = data && typeof data.body === "string" ? data.body : "";
    if (data && data.html_page && body.length > 1500) body = body.slice(0, 1500) + "\n…";
    if (body && !data.html_page) {
      try {
        body = JSON.stringify(JSON.parse(body), null, 2);
      } catch {
        /* Leave the body as the server returned it. */
      }
    }
    const output = $("gql-output");
    const tree = body && !(data && data.html_page) && window.VLJsonTree
      ? window.VLJsonTree.html(body, { toolbar: true, copyBar: true, openDepth: 2 })
      : "";
    if (tree) {
      output.classList.add("is-tree");
      output.innerHTML = tree;
    } else {
      output.classList.remove("is-tree");
      output.textContent = body || "Empty response.";
    }
  }

  async function runQuery() {
    const button = $("gql-run");
    let payload;
    try {
      payload = requestBody();
    } catch (err) {
      setNote(err.message || "Could not build the request.");
      return;
    }
    if (!payload.url) {
      setNote("GraphQL URL is required.");
      return;
    }
    button.disabled = true;
    saveDraft(state.storageKey);
    try {
      const res = await fetch("/api/graphql/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        showResult({ status: res.status, body: data.detail || "Run failed." });
        return;
      }
      showSchema(false);
      showResult(data);
    } catch {
      showResult(null, "The console did not answer.");
    } finally {
      button.disabled = false;
    }
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }

  function typeRef(type) {
    if (!type) return "";
    if (type.kind === "NON_NULL") return `${typeRef(type.ofType)}!`;
    if (type.kind === "LIST") return `[${typeRef(type.ofType)}]`;
    return type.name || "";
  }

  function namedType(type) {
    let current = type;
    while (current && (current.kind === "NON_NULL" || current.kind === "LIST")) current = current.ofType;
    return current;
  }

  function isComposite(type) {
    const named = namedType(type);
    return !!named && (named.kind === "OBJECT" || named.kind === "INTERFACE" || named.kind === "UNION");
  }

  function schemaTypes(schema) {
    return new Map((schema.types || []).filter((item) => item && item.name).map((item) => [item.name, item]));
  }

  function rootNames(schema) {
    return new Set([
      schema.queryType && schema.queryType.name,
      schema.mutationType && schema.mutationType.name,
      schema.subscriptionType && schema.subscriptionType.name,
    ].filter(Boolean));
  }

  function operationTitle(name) {
    const clean = String(name || "").replace(/[^A-Za-z0-9_]/g, "");
    const base = clean || "Field";
    return base.charAt(0).toUpperCase() + base.slice(1);
  }

  function visibleFields(type) {
    return (type && type.fields || []).filter((field) => field && field.name && !String(field.name).startsWith("__"));
  }

  function selectionLines(typeName, types, stack, indent) {
    if (!typeName || stack.has(typeName)) return null;
    const type = types.get(typeName);
    if (!type) return null;
    const next = new Set(stack);
    next.add(typeName);
    const lines = [];
    for (const field of visibleFields(type)) {
      const line = fieldLine(field, types, next, indent);
      if (line) lines.push(line);
    }
    if (type.kind === "UNION" || type.kind === "INTERFACE") {
      for (const possible of type.possibleTypes || []) {
        if (!possible || !possible.name || next.has(possible.name)) continue;
        const inner = selectionLines(possible.name, types, next, indent + 2);
        if (!inner) continue;
        const pad = " ".repeat(indent);
        lines.push(`${pad}... on ${possible.name} {\n${inner}\n${pad}}`);
      }
    }
    return lines.join("\n");
  }

  function fieldLine(field, types, stack, indent) {
    const pad = " ".repeat(indent);
    const named = namedType(field.type);
    if (!isComposite(field.type) || !named || !named.name) return `${pad}${field.name}`;
    const inner = selectionLines(named.name, types, stack, indent + 2);
    if (!inner) return "";
    return `${pad}${field.name} {\n${inner}\n${pad}}`;
  }

  function operationDocument(schema, operationType, field) {
    const types = schemaTypes(schema);
    const args = field.args || [];
    const defs = args.map((arg) => `$${arg.name}: ${typeRef(arg.type)}`).join(", ");
    const title = operationTitle(field.name);
    const head = defs ? `${operationType} ${title}(${defs})` : `${operationType} ${title}`;
    const argLines = args.map((arg) => `    ${arg.name}: $${arg.name}`).join("\n");
    const call = args.length ? `${field.name}(\n${argLines}\n  )` : field.name;
    const named = namedType(field.type);
    let body = "";
    if (named && isComposite(field.type)) {
      const inner = selectionLines(named.name, types, new Set(), 4);
      if (inner) body = ` {\n${inner}\n  }`;
    }
    return `${head} {\n  ${call}${body}\n}\n`;
  }

  function fieldDocument(schema, field) {
    return `${fieldLine(field, schemaTypes(schema), new Set(), 0)}\n`;
  }

  function typeDocument(schema, typeName) {
    const type = schemaTypes(schema).get(typeName);
    if (!type) return "";
    if (type.kind === "ENUM") {
      const values = (type.enumValues || []).map((item) => item && item.name).filter(Boolean);
      return values.length ? `${values.join("\n")}\n` : "";
    }
    if (type.kind !== "OBJECT" && type.kind !== "INTERFACE" && type.kind !== "UNION") return "";
    const inner = selectionLines(type.name, schemaTypes(schema), new Set(), 2);
    return inner ? `{\n${inner}\n}\n` : "";
  }

  function documentFromRow(schema, row) {
    if (row.dataset.schemaKind === "type") return typeDocument(schema, row.dataset.schemaType || "");
    const owner = schemaTypes(schema).get(row.dataset.schemaOwner || "");
    const field = owner && visibleFields(owner).find((item) => item.name === row.dataset.schemaField);
    if (!field) return "";
    if (rootNames(schema).has(owner.name)) {
      const op = row.dataset.schemaOp === "mutation" || row.dataset.schemaOp === "subscription"
        ? row.dataset.schemaOp
        : "query";
      return operationDocument(schema, op, field);
    }
    return fieldDocument(schema, field);
  }

  function schemaActions(enabled) {
    if (!enabled) return "";
    return `<span class="gql-schema-actions"><button type="button" data-schema-act="copy">Copy</button><button type="button" data-schema-act="use">Use</button></span>`;
  }

  function fieldRowHtml(op, owner, field) {
    const deprecated = field.isDeprecated ? " is-deprecated" : "";
    const desc = field.description ? `<span class="gql-schema-desc">${escapeHtml(field.description)}</span>` : "";
    const tag = field.isDeprecated ? `<span class="gql-schema-req">deprecated</span>` : "";
    return `<details class="gql-schema-row${deprecated}" data-schema-kind="field" data-schema-op="${escapeAttr(op)}" data-schema-owner="${escapeAttr(owner)}" data-schema-field="${escapeAttr(field.name)}">
      <summary>
        <span class="gql-schema-id"><span class="gql-schema-name">${escapeHtml(field.name)}</span><span class="gql-schema-type">${escapeHtml(typeRef(field.type))}</span>${tag}</span>
        ${desc}
        ${schemaActions(true)}
      </summary>
      <div class="gql-schema-detail"></div>
    </details>`;
  }

  function typeRowHtml(type) {
    if (!type || !type.name) return "";
    const copyable = type.kind === "ENUM" || type.kind === "OBJECT" || type.kind === "INTERFACE" || type.kind === "UNION";
    const desc = type.description ? `<span class="gql-schema-desc">${escapeHtml(type.description)}</span>` : "";
    return `<details class="gql-schema-row" data-schema-kind="type" data-schema-type="${escapeAttr(type.name)}">
      <summary>
        <span class="gql-schema-id"><span class="gql-schema-name">${escapeHtml(type.name)}</span><span class="gql-schema-type">${escapeHtml(type.kind || "")}</span></span>
        ${desc}
        ${schemaActions(copyable)}
      </summary>
      <div class="gql-schema-detail"></div>
    </details>`;
  }

  function argsHtml(field) {
    const args = field.args || [];
    if (!args.length) return "";
    const rows = args.map((arg) => {
      const required = typeRef(arg.type).endsWith("!");
      const desc = arg.description ? `<span class="gql-schema-desc">${escapeHtml(arg.description)}</span>` : "";
      return `<li><span class="gql-schema-name">${escapeHtml(arg.name)}</span><span class="gql-schema-type">${escapeHtml(typeRef(arg.type))}</span>${required ? `<span class="gql-schema-req">required</span>` : ""}${desc}</li>`;
    }).join("");
    return `<p class="gql-schema-meta">Arguments</p><ul class="gql-schema-args">${rows}</ul>`;
  }

  function inputList(type) {
    const rows = (type.inputFields || []).map((field) => {
      const required = typeRef(field.type).endsWith("!");
      return `<li><span class="gql-schema-name">${escapeHtml(field.name)}</span><span class="gql-schema-type">${escapeHtml(typeRef(field.type))}</span>${required ? `<span class="gql-schema-req">required</span>` : ""}</li>`;
    }).join("");
    return rows ? `<ul class="gql-schema-args">${rows}</ul>` : `<p class="muted">No input fields.</p>`;
  }

  function returnFieldsHtml(type) {
    const named = namedType(type);
    if (!named || !named.name || !state.schema) return "";
    const full = schemaTypes(state.schema).get(named.name);
    if (!full) return `<p class="gql-schema-meta">${escapeHtml(typeRef(type))}</p>`;
    if (full.kind === "ENUM") {
      const values = (full.enumValues || []).map((item) => escapeHtml(item.name)).join(", ");
      return `<p class="gql-schema-meta">Returns ${escapeHtml(typeRef(type))}</p><p class="gql-schema-values">${values}</p>`;
    }
    if (full.kind === "INPUT_OBJECT") return `<p class="gql-schema-meta">${escapeHtml(typeRef(type))}</p>${inputList(full)}`;
    if (full.kind !== "OBJECT" && full.kind !== "INTERFACE" && full.kind !== "UNION") {
      return `<p class="gql-schema-meta">${escapeHtml(typeRef(type))}</p>`;
    }
    const rows = visibleFields(full).map((field) => fieldRowHtml("query", full.name, field)).join("");
    const extras = (full.kind === "UNION" || full.kind === "INTERFACE")
      ? (full.possibleTypes || []).map((item) => typeRowHtml(schemaTypes(state.schema).get(item.name) || item)).join("")
      : "";
    return `<p class="gql-schema-meta">Returns ${escapeHtml(typeRef(type))}</p>${rows}${extras}`;
  }

  function fillSchemaRow(details) {
    if (details.dataset.schemaBuilt === "1" || !state.schema) return;
    const box = details.querySelector(".gql-schema-detail");
    if (!box) return;
    if (details.dataset.schemaKind === "type") {
      const type = schemaTypes(state.schema).get(details.dataset.schemaType || "");
      if (!type) return;
      if (type.kind === "ENUM") {
        box.innerHTML = `<ul class="gql-schema-args">${(type.enumValues || []).map((item) => `<li><span class="gql-schema-name">${escapeHtml(item.name)}</span></li>`).join("")}</ul>`;
      } else if (type.kind === "INPUT_OBJECT") {
        box.innerHTML = inputList(type);
      } else if (type.kind === "OBJECT" || type.kind === "INTERFACE" || type.kind === "UNION") {
        const rows = visibleFields(type).map((field) => fieldRowHtml("query", type.name, field)).join("");
        const extras = (type.kind === "UNION" || type.kind === "INTERFACE")
          ? (type.possibleTypes || []).map((item) => typeRowHtml(schemaTypes(state.schema).get(item.name) || item)).join("")
          : "";
        box.innerHTML = `${rows}${extras}` || `<p class="muted">No fields.</p>`;
      } else {
        box.innerHTML = `<p class="muted">${escapeHtml(type.kind || "Scalar")}</p>`;
      }
    } else {
      const owner = schemaTypes(state.schema).get(details.dataset.schemaOwner || "");
      const field = owner && visibleFields(owner).find((item) => item.name === details.dataset.schemaField);
      box.innerHTML = field ? `${argsHtml(field)}${returnFieldsHtml(field.type)}` : "";
    }
    details.dataset.schemaBuilt = "1";
  }

  function insertDocument(text) {
    const query = $("gql-query");
    const next = text.endsWith("\n") ? text : `${text}\n`;
    query.value = query.value.trim() ? `${query.value.replace(/\s+$/, "")}\n\n${next}` : next;
    showTab("query");
    saveDraft(state.storageKey);
  }

  async function copySchemaText(button, text) {
    const label = button.dataset.label || button.textContent;
    button.dataset.label = label;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    window.setTimeout(() => { button.textContent = label; }, 1200);
  }

  function renderSchema(schema, query) {
    const body = $("gql-schema-body");
    if (!schema) {
      body.innerHTML = `<p class="muted">No schema loaded.</p>`;
      return;
    }
    const types = schemaTypes(schema);
    const needle = String(query || "").trim().toLowerCase();
    const groups = [];
    const root = (typeName, title, operationType) => {
      const type = types.get(typeName);
      if (!type) return;
      const fields = visibleFields(type).filter((field) => {
        if (!needle) return true;
        const named = namedType(field.type);
        return String(field.name).toLowerCase().includes(needle)
          || !!(named && named.name && named.name.toLowerCase().includes(needle));
      });
      if (!fields.length) return;
      groups.push(`<section class="gql-schema-group"><h3>${escapeHtml(title)}</h3>${fields.map((field) => fieldRowHtml(operationType, type.name, field)).join("")}</section>`);
    };
    if (schema.queryType) root(schema.queryType.name, "Query", "query");
    if (schema.mutationType) root(schema.mutationType.name, "Mutation", "mutation");
    if (schema.subscriptionType) root(schema.subscriptionType.name, "Subscription", "subscription");
    const roots = rootNames(schema);
    const named = (schema.types || []).filter((item) => {
      if (!item || !item.name || item.name.startsWith("__") || roots.has(item.name)) return false;
      if (!needle) return true;
      if (item.name.toLowerCase().includes(needle)) return true;
      return visibleFields(item).some((field) => field.name.toLowerCase().includes(needle));
    });
    const visible = named.slice(0, needle ? 80 : 40);
    if (visible.length) {
      const more = named.length > visible.length ? `<p class="muted">${named.length - visible.length} more. Search to narrow the list.</p>` : "";
      groups.push(`<section class="gql-schema-group"><h3>Types</h3>${visible.map((item) => typeRowHtml(item)).join("")}${more}</section>`);
    }
    body.innerHTML = groups.length ? groups.join("") : `<p class="muted">No matches.</p>`;
  }

  async function loadSchema(refresh) {
    const body = $("gql-schema-body");
    let payload;
    try {
      payload = requestBody();
    } catch (err) {
      body.innerHTML = `<p class="muted">${escapeHtml(err.message || "Could not load the schema.")}</p>`;
      return;
    }
    if (!payload.url) {
      body.innerHTML = `<p class="muted">GraphQL URL is required.</p>`;
      return;
    }
    const key = `${payload.endpoint_id}|${payload.url}`;
    if (!refresh && state.schema && state.schemaKey === key) {
      renderSchema(state.schema, $("gql-schema-search").value);
      return;
    }
    body.innerHTML = `<p class="muted">Loading schema…</p>`;
    delete payload.query;
    delete payload.variables;
    delete payload.operation_name;
    if (refresh) payload.refresh = true;
    try {
      const res = await fetch("/api/graphql/schema", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        body.innerHTML = `<p class="muted">${escapeHtml(data.detail || "Schema request failed.")}</p>`;
        return;
      }
      showResult(data);
      if (data.html_page) {
        body.innerHTML = `<p class="muted">${escapeHtml(data.html_hint || "This URL returned a web page.")}</p>`;
        return;
      }
      let parsed = null;
      try {
        parsed = JSON.parse(data.body || "");
      } catch {
        parsed = null;
      }
      const schema = parsed && parsed.data && parsed.data.__schema;
      if (!schema) {
        const message = parsed && parsed.errors && parsed.errors[0] && parsed.errors[0].message;
        body.innerHTML = `<p class="muted">${escapeHtml(message || "This API did not return a schema. Introspection may be disabled.")}</p>`;
        return;
      }
      state.schema = schema;
      state.schemaKey = key;
      renderSchema(schema, $("gql-schema-search").value);
      if (data.cached) {
        const prior = $("gql-note").textContent;
        const extra = "Schema from the last 10 minutes on this Mac.";
        setNote(prior ? `${prior} ${extra}` : extra);
      }
    } catch {
      body.innerHTML = `<p class="muted">The console did not answer.</p>`;
    }
  }

  function prettify() {
    const active = $("gql-tab-variables").getAttribute("aria-selected") === "true"
      ? "variables"
      : "query";
    if (active === "variables") {
      try {
        $("gql-variables").value = JSON.stringify(parseVariables($("gql-variables").value), null, 2);
      } catch (err) {
        setNote(err.message || "Variables must be JSON.");
      }
      return;
    }
    const formatter = window.VLKit && window.VLKit.util && window.VLKit.util.formatBraces;
    if (!formatter) {
      setNote("Formatter is not loaded.");
      return;
    }
    $("gql-query").value = formatter($("gql-query").value, {
      mode: "graphql",
      indent: "  ",
      hashComments: true,
      noSlashComments: true,
      blockStrings: true,
      blankBetweenBlocks: true,
    });
  }

  function saveOperation() {
    const name = $("gql-save-name").value.trim();
    if (!name) {
      setNote("Name the operation before saving.");
      return;
    }
    const key = state.storageKey || storageKey();
    const record = readStore(key);
    const id = `op${Date.now().toString(36)}`;
    record.operations = record.operations.filter((item) => item.name !== name).concat([{
      id,
      name,
      ...currentDocument(),
    }]).slice(-30);
    record.draft = currentDocument();
    writeStore(key, record);
    renderSaved(record.operations, id);
    setNote(`Saved “${name}” in this browser.`);
  }

  function deleteOperation() {
    const id = $("gql-saved").value;
    if (!id) {
      setNote("Choose a saved operation to delete.");
      return;
    }
    const key = state.storageKey || storageKey();
    const record = readStore(key);
    record.operations = record.operations.filter((item) => item.id !== id);
    writeStore(key, record);
    renderSaved(record.operations, "");
    $("gql-save-name").value = "";
    setNote("Deleted the saved operation from this browser.");
  }

  function bind() {
    $("gql-endpoint").addEventListener("change", () => {
      chooseEndpoint($("gql-endpoint").value, { fillUrl: true, loadDraft: true });
    });
    $("gql-url").addEventListener("input", () => updateHostedLink());
    $("gql-url").addEventListener("change", () => {
      saveDraft(state.storageKey);
      state.storageKey = storageKey();
      state.schema = null;
      updateHostedLink();
    });
    for (const name of ["query", "variables", "headers"]) {
      $(`gql-tab-${name}`).addEventListener("click", () => showTab(name));
    }
    $("gql-run").addEventListener("click", () => runQuery());
    $("gql-pretty").addEventListener("click", () => prettify());
    $("gql-save").addEventListener("click", () => saveOperation());
    $("gql-delete").addEventListener("click", () => deleteOperation());
    $("gql-schema").addEventListener("click", () => {
      const on = $("gql-schema").getAttribute("aria-pressed") !== "true";
      showSchema(on);
      if (on) loadSchema(false);
    });
    $("gql-schema-reload").addEventListener("click", () => loadSchema(true));
    $("gql-schema-search").addEventListener("input", () => {
      if (state.schema) renderSchema(state.schema, $("gql-schema-search").value);
    });
    document.addEventListener("toggle", (event) => {
      const details = event.target;
      if (!details || !details.classList || !details.classList.contains("gql-schema-row") || !details.open) return;
      fillSchemaRow(details);
    }, true);
    $("gql-schema-body").addEventListener("click", (event) => {
      const button = event.target && event.target.closest && event.target.closest("[data-schema-act]");
      if (!button || !state.schema) return;
      event.preventDefault();
      event.stopPropagation();
      const row = button.closest(".gql-schema-row");
      const text = row ? documentFromRow(state.schema, row) : "";
      if (!text) return;
      if (button.dataset.schemaAct === "copy") copySchemaText(button, text);
      else insertDocument(text);
    });
    $("gql-saved").addEventListener("change", () => {
      const id = $("gql-saved").value;
      const record = readStore(storageKey());
      const found = record.operations.find((item) => item.id === id);
      if (!found) return;
      applyDocument(found);
      $("gql-save-name").value = found.name || "";
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        runQuery();
      }
    });
    let draftTimer = 0;
    for (const id of ["gql-query", "gql-variables", "gql-headers", "gql-operation"]) {
      $(id).addEventListener("input", () => {
        window.clearTimeout(draftTimer);
        draftTimer = window.setTimeout(() => saveDraft(state.storageKey || storageKey()), 300);
      });
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    bind();
    showTab("query");
    await loadEndpoints();
    await applyPrefill();
  });

  window.VLGraphQLDoc = {
    operation: operationDocument,
    selection: typeDocument,
  };
})();
