// Server-side limits and timeouts, loaded once so pages never keep their own copies.
(() => {
  const fetchLimits = () => fetch("/api/config/limits", { headers: { Accept: "application/json" } })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);

  const holder = {
    values: null,
    ready: null,
    get(key) {
      return holder.values ? holder.values[key] : undefined;
    },
    /** Fetches the limits again, for pages that started while the console was unreachable. */
    reload() {
      holder.ready = fetchLimits().then((values) => {
        if (values) holder.values = values;
        return holder.values;
      });
      return holder.ready;
    },
  };
  holder.ready = fetchLimits().then((values) => {
    holder.values = values;
    return values;
  });
  window.ViewliftLimits = holder;
})();
