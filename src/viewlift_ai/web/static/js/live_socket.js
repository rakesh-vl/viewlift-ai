/* Reconnecting WebSocket for every live dashboard feed.
 *
 * - Retries with backoff (0.5 s doubling to 5 s) until the console answers again.
 * - The server sends a heartbeat on idle sockets. A socket that stays silent for three
 *   heartbeats is treated as dead (Mac sleep, Wi-Fi change, console restart) and replaced.
 * - `onResync` runs after every reconnect so the page reloads what it missed while offline.
 * - Coming back online or to a visible tab reconnects at once instead of waiting for backoff.
 */
(function () {
  const MIN_RETRY_MS = 500;
  const MAX_RETRY_MS = 5000;
  const DEFAULT_HEARTBEAT_SECONDS = 10;
  const MISSED_BEATS = 3;
  const feeds = new Set();

  function connect(path, handlers = {}) {
    const { onMessage, onOpen, onResync, onState } = handlers;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let socket = null;
    let retryMs = MIN_RETRY_MS;
    let retryTimer = 0;
    let watchdog = 0;
    let silenceMs = DEFAULT_HEARTBEAT_SECONDS * MISSED_BEATS * 1000;
    let everOpened = false;
    let stopped = false;

    const report = (value) => {
      if (typeof onState === "function") onState(value);
    };

    function arm() {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(() => {
        drop();
        report("reconnecting");
        schedule();
      }, silenceMs);
    }

    function drop() {
      const current = socket;
      socket = null;
      window.clearTimeout(watchdog);
      if (!current) return;
      current.onopen = current.onmessage = current.onclose = current.onerror = null;
      try { current.close(); } catch (_) { /* already closed */ }
    }

    function schedule() {
      if (stopped) return;
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(start, retryMs);
      retryMs = Math.min(MAX_RETRY_MS, retryMs * 2);
    }

    function start() {
      window.clearTimeout(retryTimer);
      if (stopped) return;
      let next;
      try {
        next = new WebSocket(`${proto}://${location.host}${path}`);
      } catch (_) {
        report("offline");
        schedule();
        return;
      }
      socket = next;
      next.onopen = () => {
        retryMs = MIN_RETRY_MS;
        arm();
        report("live");
        if (typeof onOpen === "function") onOpen();
        if (everOpened && typeof onResync === "function") onResync();
        everOpened = true;
      };
      next.onmessage = (event) => {
        arm();
        let msg;
        try { msg = JSON.parse(event.data); } catch (_) { return; }
        if (!msg) return;
        if (msg.type === "heartbeat") {
          const seconds = Number(msg.interval);
          if (seconds > 0) silenceMs = seconds * MISSED_BEATS * 1000;
          return;
        }
        if (typeof onMessage === "function") onMessage(msg);
      };
      next.onclose = () => {
        if (socket !== next) return;
        socket = null;
        window.clearTimeout(watchdog);
        report("reconnecting");
        schedule();
      };
    }

    const feed = {
      get socket() { return socket; },
      get connected() { return Boolean(socket && socket.readyState === WebSocket.OPEN); },
      revive() {
        if (stopped) return;
        if (socket && socket.readyState <= WebSocket.OPEN) return;
        drop();
        retryMs = MIN_RETRY_MS;
        start();
      },
      close() {
        stopped = true;
        window.clearTimeout(retryTimer);
        drop();
        feeds.delete(feed);
      },
    };
    feeds.add(feed);
    start();
    return feed;
  }

  function reviveAll() {
    feeds.forEach((feed) => feed.revive());
  }

  window.addEventListener("online", reviveAll);
  window.addEventListener("pageshow", reviveAll);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reviveAll();
  });

  window.VLLive = { connect };
})();
