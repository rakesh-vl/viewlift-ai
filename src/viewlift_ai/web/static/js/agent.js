(() => {
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  async function copyText(text, button) {
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    setTimeout(() => { button.textContent = label; }, 1400);
  }

  function wireCopy() {
    document.querySelectorAll("[data-copy]").forEach((button) => {
      button.addEventListener("click", () => {
        const source = document.getElementById(button.dataset.copy);
        if (source) copyText(source.textContent, button);
      });
    });
    document.querySelectorAll(".prompts li").forEach((row) => {
      const quote = row.querySelector("q");
      if (!quote) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn ghost";
      button.textContent = "Copy";
      button.setAttribute("aria-label", `Copy prompt: ${quote.textContent}`);
      button.addEventListener("click", () => copyText(quote.textContent, button));
      row.appendChild(button);
    });
  }

  async function loadApps() {
    const body = document.getElementById("apps-body");
    const count = document.getElementById("apps-online");
    try {
      const response = await fetch("/api/debug/clients");
      if (!response.ok) throw new Error(String(response.status));
      const clients = (await response.json()).clients || [];
      const live = clients.filter((client) => !client.superseded_by);
      count.textContent = String(live.filter((client) => client.online).length);
      if (!live.length) {
        body.innerHTML = '<tr><td colspan="4" class="muted">No app has connected yet. Run a debug build that links ViewliftNetworkCapture.</td></tr>';
        return;
      }
      body.innerHTML = live.map((client) => `
        <tr>
          <td>${escapeHtml(client.app_name)}<br><span class="muted">${escapeHtml(client.bundle_id)}</span></td>
          <td>${escapeHtml(client.device_name || client.device_model || "Unknown device")}</td>
          <td><span class="app-id"><code>${escapeHtml(client.id)}</code><button type="button" class="btn ghost" data-id="${escapeHtml(client.id)}">Copy</button></span></td>
          <td class="${client.online ? "online" : "offline"}">${client.state === "background" ? "Background" : client.online ? "Online" : "Offline"}</td>
        </tr>`).join("");
      body.querySelectorAll("button[data-id]").forEach((button) => {
        button.setAttribute("aria-label", `Copy client_id ${button.dataset.id}`);
        button.addEventListener("click", () => copyText(button.dataset.id, button));
      });
    } catch {
      count.textContent = "?";
      body.innerHTML = '<tr><td colspan="4" class="muted">Could not load apps. Check that the console is running, then refresh.</td></tr>';
    }
  }

  async function loadStatus() {
    try {
      const response = await fetch("/api/agent/status");
      if (!response.ok) return;
      const { mcp } = await response.json();
      document.getElementById("mcp-dot").classList.toggle("on", Boolean(mcp.listening));
      document.getElementById("mcp-state").textContent = mcp.listening ? "Listening" : "Not running";
      document.getElementById("mcp-url").textContent = mcp.url;
    } catch {
      /* keep the server-rendered status */
    }
  }

  function trackSections() {
    const links = new Map(
      Array.from(document.querySelectorAll(".agent-toc a")).map((link) => [link.hash.slice(1), link]),
    );
    if (!("IntersectionObserver" in window) || !links.size) return;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((link) => link.classList.remove("current"));
        links.get(entry.target.id)?.classList.add("current");
      });
    }, { root: document.querySelector(".agent-page"), rootMargin: "0px 0px -70% 0px" });
    links.forEach((_link, id) => {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireCopy();
    trackSections();
    loadApps();
    loadStatus();
    document.getElementById("apps-refresh")?.addEventListener("click", loadApps);
    setInterval(loadStatus, 10000);
  });
})();
