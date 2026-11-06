/* Date & time */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  const APPLE_EPOCH = 978307200;
  const FALLBACK_ZONES = ["UTC", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Sao_Paulo", "Europe/London", "Europe/Paris", "Europe/Berlin", "Africa/Johannesburg", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland"];
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  function zoneList() {
    let zones = FALLBACK_ZONES;
    try {
      if (Intl.supportedValuesOf) zones = Intl.supportedValuesOf("timeZone");
    } catch (error) {
      zones = FALLBACK_ZONES;
    }
    const set = new Set(["UTC", localZone, ...zones]);
    return [[localZone, "Local (" + localZone + ")"], ...[...set].filter((z) => z !== localZone).map((z) => [z, z])];
  }

  function checkZone(zone) {
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: zone });
      return zone;
    } catch (error) {
      throw new Error("Unknown time zone '" + zone + "'.");
    }
  }

  function zoneParts(date, zone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short", timeZoneName: "longOffset",
    }).formatToParts(date);
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
    const name = get("timeZoneName");
    const m = /GMT([+-])(\d{1,2})(?::?(\d{2}))?/.exec(name);
    const offset = m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0)) : 0;
    return {
      year: Number(get("year")), month: Number(get("month")), day: Number(get("day")),
      hour: Number(get("hour")) % 24, minute: Number(get("minute")), second: Number(get("second")),
      weekday: get("weekday"), offset,
    };
  }

  function offsetText(minutes) {
    const sign = minutes < 0 ? "-" : "+";
    const abs = Math.abs(minutes);
    return sign + String(Math.floor(abs / 60)).padStart(2, "0") + ":" + String(abs % 60).padStart(2, "0");
  }

  const pad = (n, w) => String(n).padStart(w || 2, "0");

  function isoInZone(date, zone) {
    const p = zoneParts(date, zone);
    const ms = date.getUTCMilliseconds();
    return p.year + "-" + pad(p.month) + "-" + pad(p.day) + "T" + pad(p.hour) + ":" + pad(p.minute) + ":" + pad(p.second) + (ms ? "." + pad(ms, 3) : "") + (p.offset === 0 && zone === "UTC" ? "Z" : offsetText(p.offset));
  }

  function humanInZone(date, zone) {
    const p = zoneParts(date, zone);
    return p.weekday + " " + p.year + "-" + pad(p.month) + "-" + pad(p.day) + " " + pad(p.hour) + ":" + pad(p.minute) + ":" + pad(p.second) + " (UTC" + offsetText(p.offset) + ")";
  }

  function zonedToUtc(y, mo, d, h, mi, s, zone) {
    const guess = Date.UTC(y, mo - 1, d, h, mi, s);
    let utc = guess - zoneParts(new Date(guess), zone).offset * 60000;
    const second = guess - zoneParts(new Date(utc), zone).offset * 60000;
    if (second !== utc) utc = second;
    return new Date(utc);
  }

  function parseWallClock(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(String(text).trim());
    if (!m) return null;
    return [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4] || 0), Number(m[5] || 0), Number(m[6] || 0)];
  }

  function localInputNow() {
    const now = new Date();
    return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate()) + "T" + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
  }

  const UNIT_NS = { s: 1000000000n, ms: 1000000n, us: 1000n, ns: 1n };

  function parseInstant(raw, unit, zone) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text || /^now$/i.test(text)) {
      const ms = Date.now();
      return { date: new Date(ms), ns: BigInt(ms) * 1000000n, detected: "current time" };
    }
    if (/^-?\d+(\.\d+)?$/.test(text)) {
      if (unit === "apple") {
        const ms = (Number(text) + APPLE_EPOCH) * 1000;
        return { date: new Date(ms), ns: BigInt(Math.round(ms * 1e3)) * 1000n, detected: "Apple reference seconds (since 2001-01-01)" };
      }
      let chosen = unit;
      const digits = text.replace(/^-/, "").split(".")[0].length;
      if (unit === "auto") chosen = digits <= 11 ? "s" : digits <= 14 ? "ms" : digits <= 17 ? "us" : "ns";
      const factor = UNIT_NS[chosen];
      const ns = text.includes(".") ? BigInt(Math.round(Number(text) * Number(factor))) : BigInt(text) * factor;
      const ms = Number(ns / 1000000n);
      if (!Number.isFinite(ms) || Math.abs(ms) > 8.64e15) throw new Error("That timestamp is outside the supported date range.");
      const names = { s: "seconds", ms: "milliseconds", us: "microseconds", ns: "nanoseconds" };
      return { date: new Date(ms), ns, detected: names[chosen] + (unit === "auto" ? " (" + digits + " digits)" : "") };
    }
    const wall = parseWallClock(text);
    if (wall) {
      const date = zonedToUtc(...wall, zone);
      return { date, ns: BigInt(date.getTime()) * 1000000n, detected: "date and time in " + zone };
    }
    const ms = Date.parse(text);
    if (Number.isNaN(ms)) throw new Error("Could not read '" + text + "'. Use a number, ISO 8601 or an RFC 2822 date.");
    return { date: new Date(ms), ns: BigInt(ms) * 1000000n, detected: "date string" };
  }

  function isoWeek(date) {
    const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const yearStart = Date.UTC(d.getUTCFullYear(), 0, 1);
    return { year: d.getUTCFullYear(), week: Math.ceil(((d - yearStart) / 86400000 + 1) / 7), day };
  }

  function dayOfYear(date) {
    return Math.floor((Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) - Date.UTC(date.getUTCFullYear(), 0, 1)) / 86400000) + 1;
  }

  function trimDecimal(value, places) {
    return Number(value).toFixed(places).replace(/\.?0+$/, "");
  }

  function calendarDiff(a, b) {
    let start = a;
    let end = b;
    const sign = end < start ? -1 : 1;
    if (sign < 0) [start, end] = [end, start];
    let years = end.getUTCFullYear() - start.getUTCFullYear();
    let months = end.getUTCMonth() - start.getUTCMonth();
    let days = end.getUTCDate() - start.getUTCDate();
    let ms = (end.getUTCHours() * 3600000 + end.getUTCMinutes() * 60000 + end.getUTCSeconds() * 1000) - (start.getUTCHours() * 3600000 + start.getUTCMinutes() * 60000 + start.getUTCSeconds() * 1000);
    if (ms < 0) {
      ms += 86400000;
      days -= 1;
    }
    if (days < 0) {
      months -= 1;
      days += new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 0)).getUTCDate();
    }
    if (months < 0) {
      years -= 1;
      months += 12;
    }
    return { sign, years, months, days, hours: Math.floor(ms / 3600000), minutes: Math.floor((ms % 3600000) / 60000), seconds: Math.floor((ms % 60000) / 1000) };
  }

  function businessDays(a, b) {
    let start = new Date(Date.UTC(a.getUTCFullYear(), a.getUTCMonth(), a.getUTCDate()));
    let end = new Date(Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate()));
    if (end < start) [start, end] = [end, start];
    const total = Math.round((end - start) / 86400000);
    let count = Math.floor(total / 7) * 5;
    for (let i = 0; i < total % 7; i += 1) {
      const day = (start.getUTCDay() + Math.floor(total / 7) * 7 + i) % 7;
      if (day !== 0 && day !== 6) count += 1;
    }
    return count;
  }

  function runsTable(cron, count, utc, zone) {
    const runs = util.cronNext(cron, new Date(), count, utc);
    return {
      type: "table",
      label: "Next " + runs.length + " runs (" + (utc ? "UTC" : zone) + ")",
      columns: ["#", "When", "In"],
      rows: runs.map((d, i) => [i + 1, utc ? humanInZone(d, "UTC") : humanInZone(d, localZone), util.relativeTime(d.getTime())]),
    };
  }

  const ZONE_INPUT = { id: "zone", label: "Time zone", type: "select", options: () => zoneList(), value: localZone };

  register("time", [
    {
      slug: "unix-timestamp-converter",
      name: "Unix time converter",
      description: "Convert Unix timestamps (s, ms, µs, ns) and Apple reference dates to readable dates, and back.",
      keywords: "epoch unix timestamp time converter apple reference date nsdate cfabsolutetime timeIntervalSince1970",
      tick: 1000,
      inputs: [
        { id: "value", label: "Timestamp or date", type: "text", value: "", placeholder: "1762425600, 1762425600000, 2026-11-06T10:00:00Z, or empty for now", wide: true, hint: "Leave empty to follow the current time." },
        { id: "unit", label: "Input unit", type: "select", options: [["auto", "Detect from digits"], ["s", "Seconds"], ["ms", "Milliseconds"], ["us", "Microseconds"], ["ns", "Nanoseconds"], ["apple", "Apple reference (2001)"]], value: "auto" },
        ZONE_INPUT,
      ],
      run(v) {
        const zone = checkZone(v.zone || localZone);
        const { date, ns, detected } = parseInstant(v.value, v.unit, zone);
        const ms = date.getTime();
        const seconds = Math.floor(ms / 1000);
        const appleSeconds = ms / 1000 - APPLE_EPOCH;
        const week = isoWeek(date);
        return [
          { type: "kv", label: "Read as " + detected, rows: [
            ["Unix seconds", String(seconds)],
            ["Milliseconds", String(ms)],
            ["Microseconds", (ns / 1000n).toString()],
            ["Nanoseconds", ns.toString()],
            ["Apple reference seconds", trimDecimal(appleSeconds, 3)],
            ["ISO 8601 (UTC)", date.toISOString()],
            ["ISO 8601 (" + zone + ")", isoInZone(date, zone)],
            [zone, humanInZone(date, zone)],
            ["RFC 2822 / HTTP date", date.toUTCString()],
            ["Relative", util.relativeTime(ms)],
            ["Day of year / ISO week", dayOfYear(date) + " / " + week.year + "-W" + pad(week.week) + "-" + week.day],
          ] },
          { type: "text", label: "Swift", value: "let date = Date(timeIntervalSince1970: " + trimDecimal(ms / 1000, 3) + ")\nlet sameDate = Date(timeIntervalSinceReferenceDate: " + trimDecimal(appleSeconds, 3) + ")\nlet iso = ISO8601DateFormatter().string(from: date) // " + date.toISOString().replace(/\.\d{3}Z$/, "Z") },
          { type: "note", text: "Apple reference time (CFAbsoluteTime, Date.timeIntervalSinceReferenceDate, Core Data dates) counts from 2001-01-01 00:00 UTC, which is 978307200 seconds after the Unix epoch." },
        ];
      },
    },
    {
      slug: "timezone-converter",
      name: "Timezone converter",
      description: "Show a wall-clock time from one zone in other zones, with DST handled.",
      keywords: "time zone convert meeting utc offset dst",
      inputs: [
        { id: "when", label: "Date and time", type: "datetime", value: localInputNow },
        { ...ZONE_INPUT, id: "from", label: "In time zone" },
        { id: "targets", label: "Show in (one zone per line)", type: "textarea", rows: 8, value: "UTC\nAmerica/Los_Angeles\nAmerica/New_York\nEurope/London\nAsia/Kolkata\nAsia/Tokyo\nAustralia/Sydney" },
      ],
      run(v) {
        const from = checkZone(v.from || localZone);
        const wall = parseWallClock(v.when);
        if (!wall) throw new Error("Pick a date and time.");
        const date = zonedToUtc(...wall, from);
        const source = zoneParts(date, from);
        const sourceDay = Date.UTC(source.year, source.month - 1, source.day);
        const rows = util.nonEmptyLines(v.targets).map((line) => {
          const zone = line.trim();
          try {
            checkZone(zone);
          } catch (error) {
            return [zone, "Unknown zone", "", ""];
          }
          const p = zoneParts(date, zone);
          const dayShift = Math.round((Date.UTC(p.year, p.month - 1, p.day) - sourceDay) / 86400000);
          return [zone, p.weekday + " " + p.year + "-" + pad(p.month) + "-" + pad(p.day) + " " + pad(p.hour) + ":" + pad(p.minute), "UTC" + offsetText(p.offset), dayShift ? (dayShift > 0 ? "+" : "") + dayShift + " day" : ""];
        });
        return [
          { type: "kv", label: "Instant", rows: [["UTC", date.toISOString()], ["Unix seconds", String(Math.floor(date.getTime() / 1000))], [from, humanInZone(date, from)]] },
          { type: "table", label: "Converted", columns: ["Zone", "Local time", "Offset", "Day"], rows },
        ];
      },
    },
    {
      slug: "cron-expression-builder",
      name: "Cron expression builder",
      description: "Build a five-field cron schedule from presets, see it in words and preview the next runs.",
      keywords: "cron crontab schedule build",
      inputs: [
        { id: "preset", label: "Schedule", type: "select", options: [["minutes", "Every N minutes"], ["hourly", "Every hour at minute"], ["daily", "Every day at"], ["weekly", "Every week on"], ["weekdays", "Weekdays at"], ["monthly", "Every month on day"], ["custom", "Custom fields"]], value: "daily" },
        { id: "every", label: "Every N minutes", type: "number", value: 15, min: 1, max: 59, show: (v) => v.preset === "minutes" },
        { id: "minute", label: "Minute", type: "number", value: 0, min: 0, max: 59, show: (v) => v.preset === "hourly" },
        { id: "time", label: "Time (HH:MM)", type: "text", value: "02:30", show: (v) => ["daily", "weekly", "weekdays", "monthly"].includes(v.preset) },
        { id: "weekday", label: "Day of week", type: "select", options: util.DAY_NAMES.map((d, i) => [String(i), d]), value: "1", show: (v) => v.preset === "weekly" },
        { id: "dom", label: "Day of month", type: "number", value: 1, min: 1, max: 31, show: (v) => v.preset === "monthly" },
        { id: "f0", label: "Minute", type: "text", value: "*/5", show: (v) => v.preset === "custom" },
        { id: "f1", label: "Hour", type: "text", value: "9-17", show: (v) => v.preset === "custom" },
        { id: "f2", label: "Day of month", type: "text", value: "*", show: (v) => v.preset === "custom" },
        { id: "f3", label: "Month", type: "text", value: "*", show: (v) => v.preset === "custom" },
        { id: "f4", label: "Day of week", type: "text", value: "1-5", show: (v) => v.preset === "custom" },
        { id: "command", label: "Command", type: "text", value: "/usr/local/bin/backup.sh", wide: true },
        { id: "utc", label: "Preview in UTC", type: "checkbox", value: false },
      ],
      run(v) {
        const [hh, mm] = String(v.time || "0:0").split(":").map((n) => util.clampInt(n, 0, 59));
        const hour = Math.min(hh, 23);
        const presets = {
          minutes: () => "*/" + util.clampInt(v.every, 1, 59) + " * * * *",
          hourly: () => util.clampInt(v.minute, 0, 59) + " * * * *",
          daily: () => mm + " " + hour + " * * *",
          weekly: () => mm + " " + hour + " * * " + v.weekday,
          weekdays: () => mm + " " + hour + " * * 1-5",
          monthly: () => mm + " " + hour + " " + util.clampInt(v.dom, 1, 31) + " * *",
          custom: () => [v.f0, v.f1, v.f2, v.f3, v.f4].map((f) => String(f || "*").trim()).join(" "),
        };
        const expression = presets[v.preset]();
        const cron = util.parseCron(expression);
        return [
          { type: "kv", label: "Schedule", rows: [["Expression", expression], ["Meaning", util.describeCron(expression)]] },
          { type: "text", label: "crontab line", value: expression + " " + (v.command || "command") },
          { type: "text", label: "GitHub Actions (always UTC)", value: "on:\n  schedule:\n    - cron: \"" + expression + "\"" },
          { type: "text", label: "Kubernetes CronJob", value: "spec:\n  schedule: \"" + expression + "\"\n  timeZone: \"" + (v.utc ? "Etc/UTC" : localZone) + "\"" },
          runsTable(cron, 8, v.utc, localZone),
        ];
      },
    },
    {
      slug: "cron-expression-reader",
      name: "Cron expression reader",
      description: "Explain a cron expression in plain words, field by field, with upcoming run times.",
      keywords: "cron explain crontab parse human readable",
      inputs: [
        { id: "expression", label: "Cron expression", type: "text", value: "*/15 9-17 * * 1-5", wide: true, hint: "Five fields, or macros like @daily and @hourly." },
        { id: "count", label: "Runs to show", type: "number", value: 10, min: 1, max: 50 },
        { id: "utc", label: "Use UTC", type: "checkbox", value: false },
      ],
      run(v) {
        const cron = util.parseCron(v.expression);
        const sets = [cron.minutes, cron.hours, cron.days, cron.months, cron.weekdays];
        const fieldRows = cron.parts.map((part, i) => {
          const values = [...sets[i]].sort((a, b) => a - b);
          const named = i === 3 ? values.map((x) => util.MONTH_NAMES[x - 1].slice(0, 3)) : i === 4 ? values.map((x) => util.DAY_NAMES[x].slice(0, 3)) : values;
          const all = values.length === (i === 4 ? 7 : util.CRON_FIELDS[i].max - util.CRON_FIELDS[i].min + 1);
          return [util.CRON_FIELDS[i].name, part, all ? "every value" : named.length > 16 ? named.slice(0, 16).join(", ") + ", … (" + named.length + ")" : named.join(", ")];
        });
        const out = [
          { type: "note", level: "ok", text: util.describeCron(v.expression) },
          { type: "table", label: "Fields", columns: ["Field", "Raw", "Matches"], rows: fieldRows },
          runsTable(cron, util.clampInt(v.count, 1, 50), v.utc, localZone),
        ];
        if (!cron.domStar && !cron.dowStar) out.push({ type: "note", level: "warn", text: "Both day of month and day of week are set. Cron runs when either one matches, not both." });
        return out;
      },
    },
    {
      slug: "date-difference-calculator",
      name: "Date difference calculator",
      description: "Find the time between two dates in calendar units, totals and business days.",
      keywords: "date diff between days duration age",
      inputs: [
        { id: "start", label: "Start", type: "datetime", value: () => localInputNow().slice(0, 10) + "T00:00:00" },
        { id: "end", label: "End", type: "datetime", value: () => (new Date().getFullYear() + 1) + "-01-01T00:00:00" },
        { id: "inclusive", label: "Count the end day", type: "checkbox", value: false },
      ],
      run(v) {
        const a = parseWallClock(v.start);
        const b = parseWallClock(v.end);
        if (!a || !b) throw new Error("Pick both dates.");
        const start = new Date(Date.UTC(...[a[0], a[1] - 1, ...a.slice(2)]));
        const end = new Date(Date.UTC(...[b[0], b[1] - 1, ...b.slice(2)]) + (v.inclusive ? 86400000 : 0));
        const d = calendarDiff(start, end);
        const ms = Math.abs(end - start);
        const parts = [["year", d.years], ["month", d.months], ["day", d.days], ["hour", d.hours], ["minute", d.minutes], ["second", d.seconds]].filter(([, n]) => n).map(([u, n]) => n + " " + u + (n === 1 ? "" : "s"));
        return [
          { type: "note", level: "ok", text: (d.sign < 0 ? "End is before start: " : "") + (parts.join(", ") || "Same moment") + "." },
          { type: "kv", label: "Totals", rows: [
            ["Days", trimDecimal(ms / 86400000, 2)],
            ["Weeks", trimDecimal(ms / 604800000, 2)],
            ["Hours", trimDecimal(ms / 3600000, 2)],
            ["Minutes", String(Math.round(ms / 60000))],
            ["Seconds", String(Math.round(ms / 1000))],
            ["Business days (Mon-Fri)", String(businessDays(start, end))],
          ] },
          { type: "note", text: "Wall-clock times, with no time zone or DST shifts applied." },
        ];
      },
    },
    {
      slug: "iso-date-converter",
      name: "ISO date converter",
      description: "Show a date in ISO 8601, RFC 3339, RFC 2822, ISO week and other formats, with Swift DateFormatter patterns.",
      keywords: "iso 8601 rfc 3339 2822 date format dateformatter",
      inputs: [
        { id: "value", label: "Date", type: "text", value: "2026-11-06T15:30:45.123+05:30", wide: true, placeholder: "ISO, RFC 2822, Unix seconds, or empty for now" },
        ZONE_INPUT,
      ],
      run(v) {
        const zone = checkZone(v.zone || localZone);
        const { date } = parseInstant(v.value, "auto", zone);
        const p = zoneParts(date, zone);
        const week = isoWeek(date);
        const off = offsetText(p.offset);
        const local = p.year + "-" + pad(p.month) + "-" + pad(p.day) + "T" + pad(p.hour) + ":" + pad(p.minute) + ":" + pad(p.second);
        const ms = pad(date.getUTCMilliseconds(), 3);
        const iso = date.toISOString();
        const rows = [
          ["ISO 8601 UTC", iso.replace(/\.\d{3}Z$/, "Z"), "yyyy-MM-dd'T'HH:mm:ssXXXXX (UTC)"],
          ["ISO 8601 UTC, fractional", iso, "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX"],
          ["ISO 8601 with offset", local + off, "yyyy-MM-dd'T'HH:mm:ssXXXXX"],
          ["ISO 8601 basic", local.replace(/[-:]/g, "") + off.replace(":", ""), "yyyyMMdd'T'HHmmssXX"],
          ["RFC 3339", local + "." + ms + off, "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX"],
          ["Date only", local.slice(0, 10), "yyyy-MM-dd"],
          ["ISO week date", week.year + "-W" + pad(week.week) + "-" + week.day, "YYYY-'W'ww-e"],
          ["Ordinal date", p.year + "-" + pad(dayOfYear(zonedToUtc(p.year, p.month, p.day, 0, 0, 0, "UTC")), 3), "yyyy-DDD"],
          ["RFC 2822", p.weekday + ", " + pad(p.day) + " " + util.MONTH_NAMES[p.month - 1].slice(0, 3) + " " + p.year + " " + pad(p.hour) + ":" + pad(p.minute) + ":" + pad(p.second) + " " + off.replace(":", ""), "EEE, dd MMM yyyy HH:mm:ss Z"],
          ["HTTP date", date.toUTCString(), "EEE, dd MMM yyyy HH:mm:ss 'GMT' (UTC)"],
          ["SQL datetime", local.replace("T", " "), "yyyy-MM-dd HH:mm:ss"],
          ["Unix seconds", String(Math.floor(date.getTime() / 1000)), "timeIntervalSince1970"],
          ["Apple reference", trimDecimal(date.getTime() / 1000 - APPLE_EPOCH, 3), "timeIntervalSinceReferenceDate"],
        ];
        return [
          { type: "table", label: "Formats (" + zone + ")", columns: ["Format", "Value", "DateFormatter pattern"], rows },
          { type: "note", text: "In Swift, set DateFormatter.locale to en_US_POSIX for fixed formats. ISO8601DateFormatter needs .withFractionalSeconds to read the fractional form." },
        ];
      },
    },
  ]);
})();
