// Server-side limits and timeouts, loaded once so pages never keep their own copies.
(() => {
  const holder = {
    values: null,
    ready: null,
    get(key) {
      return holder.values ? holder.values[key] : undefined;
    },
  };
  holder.ready = fetch("/api/config/limits", { headers: { Accept: "application/json" } })
    .then((response) => (response.ok ? response.json() : null))
    .then((values) => {
      holder.values = values;
      return values;
    })
    .catch(() => null);
  window.ViewliftLimits = holder;
})();
