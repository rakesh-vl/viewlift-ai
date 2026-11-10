// Runtime tab: search classes, browse loaded images, and read one class at a time.
(() => {
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const $ = (id) => document.getElementById(id);
  const shortImage = (path) => String(path || "").split("/").pop();

  const state = {
    send: async () => false,
    notify: () => {},
    searchTimer: 0,
    classes: [],
    appClasses: new Set(),
    listTitle: "",
    history: [],
    current: null,
    filter: "",
    libraries: [],
    libraryFilter: "",
  };

  const Runtime = {
    init({ send, notify }) {
      state.send = send;
      state.notify = notify || state.notify;
      $("runtime-search")?.addEventListener("submit", (event) => {
        event.preventDefault();
        this.search();
      });
      $("runtime-query")?.addEventListener("input", () => {
        clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(() => this.search(), 300);
      });
      $("runtime-app-only")?.addEventListener("change", () => this.search());
      $("runtime-app-classes")?.addEventListener("click", () => {
        $("runtime-app-only").checked = true;
        $("runtime-query").value = "";
        this.search(true);
      });
      $("runtime-classes")?.addEventListener("click", (event) => {
        const item = event.target.closest("[data-class]");
        if (item) this.describe(item.dataset.class);
      });
      $("runtime-classes")?.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
        const items = [...$("runtime-classes").querySelectorAll("[data-class]")];
        const index = items.indexOf(document.activeElement);
        const next = items[index + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) {
          event.preventDefault();
          next.focus();
        }
      });
      $("runtime-history")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-history]");
        if (button) this.describe(button.dataset.history);
      });
      $("runtime-filter")?.addEventListener("input", (event) => {
        state.filter = event.target.value.trim().toLowerCase();
        this.applyFilter();
      });
      $("runtime-result")?.addEventListener("click", (event) => this.onClick(event));
    },

    reset() {
      state.classes = [];
      state.appClasses = new Set();
      state.history = [];
      state.current = null;
      ["runtime-classes", "runtime-history", "runtime-count"].forEach((id) => {
        const node = $(id);
        if (node) node.innerHTML = "";
      });
      const result = $("runtime-result");
      if (result) result.innerHTML = `<p class="muted">Type at least two letters to search, or open App classes.</p>`;
      const filter = $("runtime-filter");
      if (filter) filter.hidden = true;
    },

    search(force = false) {
      const query = $("runtime-query")?.value.trim() || "";
      const appOnly = Boolean($("runtime-app-only")?.checked);
      if (query.length < 2 && !appOnly && !force) {
        $("runtime-count").textContent = query ? "Type one more letter." : "";
        return;
      }
      $("runtime-count").textContent = "Searching…";
      state.listTitle = "";
      state.send("runtime.search", { query, app_only: appOnly });
    },

    describe(className) {
      if (!className) return;
      this.showTab();
      $("runtime-result").innerHTML = `<p class="muted">Loading ${escapeHtml(className)}…</p>`;
      state.send("runtime.describe", { class: className });
    },

    showTab() {
      const tab = document.querySelector('[data-debug-tab="runtime"]');
      if (tab && !tab.classList.contains("active")) tab.click();
    },

    onClick(event) {
      const describe = event.target.closest("[data-describe-class]");
      if (describe) {
        this.describe(describe.dataset.describeClass);
        return;
      }
      const image = event.target.closest("[data-image-classes]");
      if (image) {
        state.listTitle = shortImage(image.dataset.imageClasses);
        $("runtime-count").textContent = "Loading…";
        state.send("runtime.image_classes", { image: image.dataset.imageClasses });
        return;
      }
      const instances = event.target.closest("[data-runtime-instances]");
      if (instances) {
        window.ViewliftExplorer?.findInstances(instances.dataset.runtimeInstances);
        return;
      }
      if (event.target.closest("[data-runtime-copy]") && state.current) {
        navigator.clipboard.writeText(this.header(state.current))
          .then(() => state.notify("Interface copied."))
          .catch(() => state.notify("Could not copy. Allow clipboard access."));
      }
    },

    /** Returns true when this module rendered the result. */
    onResult(name, result) {
      const handlers = {
        "runtime.search": () => this.renderClasses(result.classes || [], result.app_classes || [], result.truncated),
        "runtime.image_classes": () => this.renderClasses(result.classes || [], [], result.truncated),
        "runtime.libraries": () => this.renderLibraries(result),
        "runtime.describe": () => this.renderDescribe(result),
      };
      const handler = handlers[name];
      if (!handler) return false;
      handler();
      return true;
    },

    renderClasses(classes, appClasses, truncated) {
      state.appClasses = new Set(appClasses);
      const ranked = [...classes].sort((left, right) => {
        const app = Number(state.appClasses.has(right)) - Number(state.appClasses.has(left));
        return app || left.localeCompare(right);
      });
      state.classes = ranked;
      const title = state.listTitle ? `${state.listTitle}: ` : "";
      $("runtime-count").textContent = `${title}${ranked.length}${truncated ? "+" : ""} classes`
        + (state.appClasses.size ? `, ${state.appClasses.size} from the app` : "");
      $("runtime-classes").innerHTML = ranked.length
        ? ranked.map((name) => `<li><button type="button" class="runtime-class-item${name === state.current?.class ? " active" : ""}" data-class="${escapeHtml(name)}" role="option">`
          + `<span class="mono">${escapeHtml(name)}</span>${state.appClasses.has(name) ? `<span class="capability available">app</span>` : ""}</button></li>`).join("")
        : `<li class="muted">No classes match.</li>`;
      window.ViewliftExplorer?.setClassHints(ranked.slice(0, 200));
    },

    renderLibraries(result) {
      state.libraries = result.images || [];
      $("runtime-filter").hidden = true;
      const target = $("runtime-result");
      target.innerHTML = `<h3>Loaded libraries</h3><p class="muted">Click a class count to list that image's classes on the left.</p>`
        + `<div class="defaults-bar"><input type="search" id="library-filter" placeholder="Filter ${state.libraries.length} images" autocomplete="off" aria-label="Filter loaded libraries"><span class="muted" id="library-count"></span></div>`
        + `<table class="debug-table"><thead><tr><th scope="col">Image</th><th scope="col">Classes</th><th scope="col">Path</th></tr></thead><tbody id="library-rows"></tbody></table>`;
      const paint = () => {
        const needle = state.libraryFilter;
        const shown = state.libraries.filter((row) => !needle || row.path.toLowerCase().includes(needle));
        $("library-count").textContent = `${shown.length} of ${state.libraries.length}`;
        $("library-rows").innerHTML = shown.map((row) => `<tr>
            <td>${escapeHtml(row.name)}${row.app ? ` <span class="capability available">app</span>` : ""}</td>
            <td>${row.classes ? `<button type="button" class="btn ghost" data-image-classes="${escapeHtml(row.path)}">${row.classes}</button>` : `<span class="muted">0</span>`}</td>
            <td class="mono muted">${escapeHtml(row.path)}</td>
          </tr>`).join("");
      };
      $("library-filter").addEventListener("input", (event) => {
        state.libraryFilter = event.target.value.trim().toLowerCase();
        paint();
      });
      state.libraryFilter = "";
      paint();
    },

    renderDescribe(result) {
      const target = $("runtime-result");
      if (result.error) {
        target.innerHTML = `<p class="warn-text">${escapeHtml(result.error)}</p>`;
        return;
      }
      state.current = result;
      state.history = [result.class, ...state.history.filter((name) => name !== result.class)].slice(0, 8);
      $("runtime-history").innerHTML = state.history.length > 1
        ? `<span class="muted">Recent:</span>` + state.history.map((name, index) => (index === 0
          ? `<span>${escapeHtml(name)}</span>`
          : `<button type="button" data-history="${escapeHtml(name)}">${escapeHtml(name)}</button>`)).join(`<span class="muted">·</span>`)
        : "";
      document.querySelectorAll("#runtime-classes [data-class]").forEach((item) => {
        item.classList.toggle("active", item.dataset.class === result.class);
      });
      const chain = [result.class, ...(result.superclasses || [])];
      const chainHtml = chain.map((cls, index) => (index === 0
        ? `<span class="explorer-chip current mono">${escapeHtml(cls)}</span>`
        : `<button type="button" class="btn ghost explorer-chip" data-describe-class="${escapeHtml(cls)}">${escapeHtml(cls)}</button>`))
        .join(`<span class="muted" aria-hidden="true">›</span>`);
      const protocols = (result.protocols || []).map((name) => `<span class="runtime-chip">${escapeHtml(name)}</span>`).join("");
      const flags = (row) => [row.readonly && "readonly", row.weak && "weak"].filter(Boolean).join(", ");
      const section = (title, rows, line, open = true) => (rows.length
        ? `<details class="explorer-section" ${open ? "open" : ""}><summary>${escapeHtml(title)} <span class="muted" data-count>${rows.length}</span></summary>`
          + `<table class="plist-table explorer-table"><tbody>${rows.map(line).join("")}</tbody></table></details>`
        : "");
      $("runtime-filter").hidden = false;
      $("runtime-filter").value = state.filter;
      target.innerHTML = `<header class="explorer-head">
          <h3 class="mono">${escapeHtml(result.class)}</h3>
          <span class="muted">${escapeHtml(shortImage(result.image))} · ${result.instance_size || 0} bytes per instance</span>
          <button type="button" class="btn" data-runtime-instances="${escapeHtml(result.class)}">Live instances</button>
          <button type="button" class="btn ghost" data-runtime-copy>Copy interface</button>
        </header>
        <div class="explorer-chain" aria-label="Class hierarchy">${chainHtml}</div>
        ${protocols ? `<div class="runtime-protocols"><span class="muted">Conforms to</span> ${protocols}</div>` : ""}
        ${section("Properties", result.properties || [], (row) => `<tr data-filter="${escapeHtml(`${row.name} ${row.type}`.toLowerCase())}"><th class="mono">${escapeHtml(row.name)}</th><td class="plist-type">${escapeHtml(row.type)}</td><td class="muted">${escapeHtml(flags(row))}</td></tr>`)}
        ${section("Instance variables", result.ivars || [], (row) => `<tr data-filter="${escapeHtml(`${row.name} ${row.type}`.toLowerCase())}"><th class="mono">${escapeHtml(row.name)}</th><td class="plist-type">${escapeHtml(row.type)}</td><td class="muted">offset ${escapeHtml(row.offset)}</td></tr>`, false)}
        ${section("Instance methods", result.methods || [], (row) => `<tr data-filter="${escapeHtml(row.selector.toLowerCase())}"><th class="mono">- ${escapeHtml(row.selector)}</th><td class="plist-type">${escapeHtml(row.returns)}</td></tr>`, false)}
        ${section("Class methods", result.class_methods || [], (row) => `<tr data-filter="${escapeHtml(row.selector.toLowerCase())}"><th class="mono">+ ${escapeHtml(row.selector)}</th><td class="plist-type">${escapeHtml(row.returns)}</td></tr>`, false)}`;
      this.applyFilter();
    },

    applyFilter() {
      const needle = state.filter;
      document.querySelectorAll("#runtime-result details.explorer-section").forEach((section) => {
        let shown = 0;
        section.querySelectorAll("[data-filter]").forEach((row) => {
          row.hidden = Boolean(needle) && !row.dataset.filter.includes(needle);
          if (!row.hidden) shown += 1;
        });
        const count = section.querySelector("[data-count]");
        const total = section.querySelectorAll("[data-filter]").length;
        if (count) count.textContent = needle ? `${shown} of ${total}` : String(total);
        if (needle && shown) section.open = true;
      });
    },

    /** An Objective-C style interface, handy to paste into a bug or a chat. */
    header(result) {
      const lines = [`// ${shortImage(result.image)}`];
      const protocols = (result.protocols || []).length ? ` <${result.protocols.join(", ")}>` : "";
      lines.push(`@interface ${result.class} : ${result.superclass || "NSObject"}${protocols}`);
      (result.properties || []).forEach((row) => {
        const attrs = ["nonatomic", row.readonly && "readonly", row.weak && "weak"].filter(Boolean).join(", ");
        lines.push(`@property (${attrs}) ${row.type} ${row.name};`);
      });
      (result.class_methods || []).forEach((row) => lines.push(`+ (${row.returns})${row.selector};`));
      (result.methods || []).forEach((row) => lines.push(`- (${row.returns})${row.selector};`));
      lines.push("@end");
      return lines.join("\n");
    },
  };

  window.ViewliftRuntime = Runtime;
})();
