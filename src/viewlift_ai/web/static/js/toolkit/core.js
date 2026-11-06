/* Toolkit registry and shared helpers. Tools run in this browser unless they call ctx.server. */
(() => {
  const categories = [
    { id: "text", name: "Text & strings" },
    { id: "data", name: "JSON, XML & YAML" },
    { id: "security", name: "Security & encoding" },
    { id: "web", name: "Web development" },
    { id: "http", name: "APIs & HTTP" },
    { id: "sql", name: "Database & SQL" },
    { id: "time", name: "Date & time" },
    { id: "network", name: "Networking" },
    { id: "calc", name: "Calculators & converters" },
    { id: "generators", name: "Generators" },
    { id: "media", name: "Files & media" },
    { id: "devops", name: "DevOps & infrastructure" },
  ];
  const tools = [];
  const bySlug = new Map();

  function register(category, list) {
    for (const tool of list) {
      if (bySlug.has(tool.slug)) throw new Error("Duplicate tool slug: " + tool.slug);
      tool.category = category;
      tools.push(tool);
      bySlug.set(tool.slug, tool);
    }
  }

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function utf8(text) {
    return encoder.encode(String(text));
  }

  function fromUtf8(bytes, strict) {
    if (strict) return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return decoder.decode(bytes);
  }

  function toHex(bytes) {
    let out = "";
    for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
    return out;
  }

  function fromHex(text) {
    const clean = String(text).replace(/^0x/i, "").replace(/[\s:-]/g, "");
    if (clean.length % 2 || /[^0-9a-f]/i.test(clean)) throw new Error("Not valid hex.");
    const out = new Uint8Array(clean.length / 2);
    for (let i = 0; i < out.length; i += 1) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    return out;
  }

  function toBase64(bytes) {
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function fromBase64(text) {
    let clean = String(text).trim().replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
    if (clean.startsWith("data:")) clean = clean.slice(clean.indexOf(",") + 1);
    if (/[^A-Za-z0-9+/=]/.test(clean)) throw new Error("Not valid base64.");
    clean += "=".repeat((4 - (clean.length % 4)) % 4);
    const binary = atob(clean);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  }

  function toBase64Url(bytes) {
    return toBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function randomBytes(count) {
    const out = new Uint8Array(count);
    crypto.getRandomValues(out);
    return out;
  }

  function randomInt(max) {
    if (max <= 0) return 0;
    const limit = Math.floor(0x100000000 / max) * max;
    const buffer = new Uint32Array(1);
    for (;;) {
      crypto.getRandomValues(buffer);
      if (buffer[0] < limit) return buffer[0] % max;
    }
  }

  function randomFrom(alphabet, length) {
    let out = "";
    for (let i = 0; i < length; i += 1) out += alphabet[randomInt(alphabet.length)];
    return out;
  }

  function pick(list) {
    return list[randomInt(list.length)];
  }

  function parseJSON(text, label) {
    const source = String(text);
    try {
      return JSON.parse(source);
    } catch (error) {
      const message = error && error.message ? error.message : "Invalid JSON";
      const match = /position (\d+)/.exec(message);
      const prefix = label ? label + ": " : "";
      if (match && !/line \d+/.test(message)) {
        const position = Number(match[1]);
        const before = source.slice(0, position);
        const line = before.split("\n").length;
        const column = position - before.lastIndexOf("\n");
        throw new Error(prefix + message + " (line " + line + ", column " + column + ")");
      }
      throw new Error(prefix + message);
    }
  }

  function formatBytes(bytes, iec) {
    const base = iec ? 1024 : 1000;
    const units = iec ? ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] : ["B", "KB", "MB", "GB", "TB", "PB"];
    let value = Number(bytes);
    let unit = 0;
    while (Math.abs(value) >= base && unit < units.length - 1) {
      value /= base;
      unit += 1;
    }
    return (unit ? value.toFixed(2).replace(/\.?0+$/, "") : String(value)) + " " + units[unit];
  }

  function escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function lines(text) {
    return String(text).replace(/\r\n?/g, "\n").split("\n");
  }

  function nonEmptyLines(text) {
    return lines(text).map((line) => line.trim()).filter(Boolean);
  }

  function clampInt(value, low, high, fallback) {
    const number = Math.trunc(Number(value));
    if (!Number.isFinite(number)) return fallback;
    return Math.min(high, Math.max(low, number));
  }

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  /* YAML runs on the console server. */
  async function yamlParse(ctx, text) {
    const result = await ctx.server("yaml-parse", { text: String(text) });
    return result.documents || [];
  }

  async function yamlParseOne(ctx, text) {
    const docs = await yamlParse(ctx, text);
    if (docs.length > 1) throw new Error("Expected one YAML document, found " + docs.length + ".");
    return docs.length ? docs[0] : null;
  }

  async function yamlDump(ctx, data, options) {
    const opts = options || {};
    const result = await ctx.server("yaml-dump", {
      data,
      indent: opts.indent || 2,
      multi: Boolean(opts.multi),
    });
    return result.text;
  }

  async function parseJsonOrYaml(ctx, text) {
    const trimmed = String(text).trim();
    if (!trimmed) throw new Error("Paste JSON or YAML.");
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) return parseJSON(trimmed);
    return yamlParseOne(ctx, trimmed);
  }

  /* CSV */
  function detectDelimiter(text) {
    const sample = String(text).split("\n").slice(0, 5).join("\n");
    const candidates = [",", "\t", ";", "|"];
    let best = ",";
    let bestCount = -1;
    for (const candidate of candidates) {
      const count = sample.split(candidate).length - 1;
      if (count > bestCount) {
        best = candidate;
        bestCount = count;
      }
    }
    return best;
  }

  function parseCSV(text, delimiter) {
    const source = String(text).replace(/\r\n?/g, "\n");
    const sep = delimiter || detectDelimiter(source);
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    for (let i = 0; i < source.length; i += 1) {
      const char = source[i];
      if (quoted) {
        if (char === '"') {
          if (source[i + 1] === '"') {
            field += '"';
            i += 1;
          } else {
            quoted = false;
          }
        } else {
          field += char;
        }
        continue;
      }
      if (char === '"' && field === "") quoted = true;
      else if (char === sep) {
        row.push(field);
        field = "";
      } else if (char === "\n") {
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
      } else field += char;
    }
    if (quoted) throw new Error("CSV has an unclosed quote.");
    if (field !== "" || row.length) {
      row.push(field);
      rows.push(row);
    }
    return rows.filter((r) => r.length > 1 || r[0] !== "");
  }

  function toCSV(rows) {
    return rows
      .map((row) =>
        row
          .map((cell) => {
            const text = cell == null ? "" : String(cell);
            return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
          })
          .join(","),
      )
      .join("\n");
  }

  /* Structural diff for parsed JSON or YAML. */
  function deepDiff(before, after, path, out) {
    const results = out || [];
    const here = path || "";
    if (Array.isArray(before) && Array.isArray(after)) {
      const max = Math.max(before.length, after.length);
      for (let i = 0; i < max; i += 1) {
        const child = here + "[" + i + "]";
        if (i >= before.length) results.push({ op: "added", path: child, after: after[i] });
        else if (i >= after.length) results.push({ op: "removed", path: child, before: before[i] });
        else deepDiff(before[i], after[i], child, results);
      }
      return results;
    }
    if (isPlainObject(before) && isPlainObject(after)) {
      const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
      for (const key of keys) {
        const child = here ? here + "." + key : key;
        if (!(key in before)) results.push({ op: "added", path: child, after: after[key] });
        else if (!(key in after)) results.push({ op: "removed", path: child, before: before[key] });
        else deepDiff(before[key], after[key], child, results);
      }
      return results;
    }
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      results.push({ op: "changed", path: here || "(root)", before, after });
    }
    return results;
  }

  function diffRows(changes) {
    const short = (value) => {
      if (value === undefined) return "";
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return text.length > 160 ? text.slice(0, 157) + "…" : text;
    };
    return changes.map((change) => [change.op, change.path, short(change.before), short(change.after)]);
  }

  /* Line diff: trims shared ends, then LCS on the middle. */
  function lineDiff(beforeText, afterText, options) {
    const opts = options || {};
    const norm = (line) => {
      let value = line;
      if (opts.ignoreWhitespace) value = value.replace(/\s+/g, " ").trim();
      if (opts.ignoreCase) value = value.toLowerCase();
      return value;
    };
    const a = lines(beforeText);
    const b = lines(afterText);
    const na = a.map(norm);
    const nb = b.map(norm);
    let start = 0;
    while (start < a.length && start < b.length && na[start] === nb[start]) start += 1;
    let endA = a.length;
    let endB = b.length;
    while (endA > start && endB > start && na[endA - 1] === nb[endB - 1]) {
      endA -= 1;
      endB -= 1;
    }
    const ops = [];
    for (let i = 0; i < start; i += 1) ops.push({ op: " ", text: a[i], a: i + 1, b: i + 1 });
    const midA = endA - start;
    const midB = endB - start;
    if (midA * midB > 4_000_000) {
      for (let i = start; i < endA; i += 1) ops.push({ op: "-", text: a[i], a: i + 1 });
      for (let j = start; j < endB; j += 1) ops.push({ op: "+", text: b[j], b: j + 1 });
    } else {
      const table = Array.from({ length: midA + 1 }, () => new Uint32Array(midB + 1));
      for (let i = midA - 1; i >= 0; i -= 1) {
        for (let j = midB - 1; j >= 0; j -= 1) {
          table[i][j] = na[start + i] === nb[start + j]
            ? table[i + 1][j + 1] + 1
            : Math.max(table[i + 1][j], table[i][j + 1]);
        }
      }
      let i = 0;
      let j = 0;
      while (i < midA || j < midB) {
        if (i < midA && j < midB && na[start + i] === nb[start + j]) {
          ops.push({ op: " ", text: a[start + i], a: start + i + 1, b: start + j + 1 });
          i += 1;
          j += 1;
        } else if (j < midB && (i >= midA || table[i][j + 1] >= table[i + 1][j])) {
          ops.push({ op: "+", text: b[start + j], b: start + j + 1 });
          j += 1;
        } else {
          ops.push({ op: "-", text: a[start + i], a: start + i + 1 });
          i += 1;
        }
      }
    }
    for (let i = endA, j = endB; i < a.length; i += 1, j += 1) {
      ops.push({ op: " ", text: a[i], a: i + 1, b: j + 1 });
    }
    return ops;
  }

  /* Cron: five fields or an @macro. */
  const CRON_FIELDS = [
    { name: "minute", min: 0, max: 59 },
    { name: "hour", min: 0, max: 23 },
    { name: "day of month", min: 1, max: 31 },
    { name: "month", min: 1, max: 12, names: ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"] },
    { name: "day of week", min: 0, max: 7, names: ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"] },
  ];
  const CRON_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
  };
  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function cronValue(raw, field) {
    const upper = raw.toUpperCase();
    if (field.names) {
      const index = field.names.indexOf(upper);
      if (index >= 0) return index + field.min;
    }
    if (!/^\d+$/.test(raw)) throw new Error(field.name + ": '" + raw + "' is not a number.");
    const value = Number(raw);
    if (value < field.min || value > field.max) {
      throw new Error(field.name + ": " + value + " is outside " + field.min + "-" + field.max + ".");
    }
    return value;
  }

  function parseCronField(raw, field) {
    const values = new Set();
    if (!raw) throw new Error(field.name + " is empty.");
    for (const part of raw.split(",")) {
      if (!part) throw new Error(field.name + ": empty list item.");
      const [rangePart, stepPart, extra] = part.split("/");
      if (extra !== undefined) throw new Error(field.name + ": '" + part + "' has two steps.");
      let step = 1;
      if (stepPart !== undefined) {
        if (!/^\d+$/.test(stepPart) || Number(stepPart) === 0) {
          throw new Error(field.name + ": step '" + stepPart + "' must be a positive number.");
        }
        step = Number(stepPart);
      }
      let low;
      let high;
      if (rangePart === "*" || rangePart === "?") {
        low = field.min;
        high = field.name === "day of week" ? 6 : field.max;
      } else if (rangePart.includes("-")) {
        const [a, b] = rangePart.split("-");
        low = cronValue(a, field);
        high = cronValue(b, field);
        if (low > high) throw new Error(field.name + ": range '" + rangePart + "' runs backwards.");
      } else {
        low = cronValue(rangePart, field);
        high = stepPart !== undefined ? (field.name === "day of week" ? 6 : field.max) : low;
      }
      for (let v = low; v <= high; v += step) values.add(field.name === "day of week" && v === 7 ? 0 : v);
    }
    return values;
  }

  function parseCron(expression) {
    let text = String(expression).trim().replace(/\s+/g, " ");
    if (!text) throw new Error("Enter a cron expression.");
    const macro = CRON_MACROS[text.toLowerCase()];
    if (macro) text = macro;
    else if (text.startsWith("@")) throw new Error("Unknown macro " + text + ".");
    const parts = text.split(" ");
    if (parts.length !== 5) {
      throw new Error("Expected 5 fields (minute hour day-of-month month day-of-week), found " + parts.length + ".");
    }
    const sets = parts.map((part, index) => parseCronField(part, CRON_FIELDS[index]));
    return {
      expression: text,
      parts,
      minutes: sets[0],
      hours: sets[1],
      days: sets[2],
      months: sets[3],
      weekdays: sets[4],
      domStar: parts[2] === "*" || parts[2] === "?",
      dowStar: parts[4] === "*" || parts[4] === "?",
    };
  }

  function cronMatchesDay(cron, date, utc) {
    const dom = utc ? date.getUTCDate() : date.getDate();
    const dow = utc ? date.getUTCDay() : date.getDay();
    const domOk = cron.days.has(dom);
    const dowOk = cron.weekdays.has(dow);
    if (cron.domStar && cron.dowStar) return true;
    if (cron.domStar) return dowOk;
    if (cron.dowStar) return domOk;
    return domOk || dowOk;
  }

  function cronNext(cron, from, count, utc) {
    const results = [];
    const date = new Date(from.getTime());
    if (utc) date.setUTCSeconds(0, 0);
    else date.setSeconds(0, 0);
    const step = (unit) => {
      if (utc) {
        if (unit === "month") date.setUTCMonth(date.getUTCMonth() + 1, 1), date.setUTCHours(0, 0);
        else if (unit === "day") date.setUTCDate(date.getUTCDate() + 1), date.setUTCHours(0, 0);
        else if (unit === "hour") date.setUTCHours(date.getUTCHours() + 1, 0);
        else date.setUTCMinutes(date.getUTCMinutes() + 1);
      } else if (unit === "month") date.setMonth(date.getMonth() + 1, 1), date.setHours(0, 0);
      else if (unit === "day") date.setDate(date.getDate() + 1), date.setHours(0, 0);
      else if (unit === "hour") date.setHours(date.getHours() + 1, 0);
      else date.setMinutes(date.getMinutes() + 1);
    };
    step("minute");
    let guard = 0;
    while (results.length < count && guard < 600000) {
      guard += 1;
      const month = (utc ? date.getUTCMonth() : date.getMonth()) + 1;
      if (!cron.months.has(month)) {
        step("month");
        continue;
      }
      if (!cronMatchesDay(cron, date, utc)) {
        step("day");
        continue;
      }
      if (!cron.hours.has(utc ? date.getUTCHours() : date.getHours())) {
        step("hour");
        continue;
      }
      if (!cron.minutes.has(utc ? date.getUTCMinutes() : date.getMinutes())) {
        step("minute");
        continue;
      }
      results.push(new Date(date.getTime()));
      step("minute");
    }
    return results;
  }

  function listWords(items) {
    if (items.length <= 1) return items.join("");
    return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
  }

  function describeCronPart(raw, field, namer) {
    if (raw === "*" || raw === "?") return null;
    const named = (value) => (namer ? namer(cronValue(value, field)) : String(cronValue(value, field)));
    const pieces = raw.split(",").map((part) => {
      const [range, step] = part.split("/");
      if (step) {
        const base = range === "*" ? "" : " from " + range.split("-").map(named).join(" through ");
        return "every " + step + " " + field.unit + (Number(step) === 1 ? "" : "s") + base;
      }
      if (range.includes("-")) {
        const [a, b] = range.split("-");
        return named(a) + " through " + named(b);
      }
      return named(range);
    });
    return listWords(pieces);
  }

  function describeCron(expression) {
    const cron = parseCron(expression);
    const [m, h, dom, mon, dow] = cron.parts;
    const pad = (n) => String(n).padStart(2, "0");
    const simpleNumber = (raw) => /^\d+$/.test(raw);
    let time;
    if (simpleNumber(m) && simpleNumber(h)) {
      time = "At " + pad(h) + ":" + pad(m);
    } else if (simpleNumber(m) && h.split(",").every(simpleNumber)) {
      time = "At " + listWords(h.split(",").map((hour) => pad(hour) + ":" + pad(m)));
    } else {
      const minutePart = m === "*"
        ? "every minute"
        : m.startsWith("*/")
          ? "every " + m.slice(2) + " minutes"
          : "at minute " + describeCronPart(m, { ...CRON_FIELDS[0], unit: "minute" });
      const hourPart = h === "*"
        ? ""
        : h.startsWith("*/")
          ? ", every " + h.slice(2) + " hours"
          : ", during hour " + describeCronPart(h, { ...CRON_FIELDS[1], unit: "hour" });
      time = minutePart.charAt(0).toUpperCase() + minutePart.slice(1) + hourPart;
    }
    const segments = [time];
    const domText = describeCronPart(dom, { ...CRON_FIELDS[2], unit: "day" });
    const dowText = describeCronPart(dow, { ...CRON_FIELDS[4], unit: "day" }, (v) => DAY_NAMES[v % 7]);
    if (domText && dowText) segments.push("on day " + domText + " of the month or on " + dowText);
    else if (domText) segments.push("on day " + domText + " of the month");
    else if (dowText) segments.push("on " + dowText);
    const monText = describeCronPart(mon, { ...CRON_FIELDS[3], unit: "month" }, (v) => MONTH_NAMES[v - 1]);
    if (monText) segments.push("in " + monText);
    return segments.join(", ") + ".";
  }

  /* Semantic versions (semver 2.0.0). */
  const SEMVER = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

  function parseSemver(text) {
    const match = SEMVER.exec(String(text).trim());
    if (!match) throw new Error("'" + text + "' is not a semantic version (MAJOR.MINOR.PATCH).");
    return {
      major: Number(match[1]),
      minor: Number(match[2]),
      patch: Number(match[3]),
      prerelease: match[4] ? match[4].split(".") : [],
      build: match[5] || "",
    };
  }

  function compareSemver(a, b) {
    for (const key of ["major", "minor", "patch"]) {
      if (a[key] !== b[key]) return a[key] > b[key] ? 1 : -1;
    }
    if (!a.prerelease.length && b.prerelease.length) return 1;
    if (a.prerelease.length && !b.prerelease.length) return -1;
    const max = Math.max(a.prerelease.length, b.prerelease.length);
    for (let i = 0; i < max; i += 1) {
      const x = a.prerelease[i];
      const y = b.prerelease[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      const xn = /^\d+$/.test(x);
      const yn = /^\d+$/.test(y);
      if (xn && yn && Number(x) !== Number(y)) return Number(x) > Number(y) ? 1 : -1;
      if (xn !== yn) return xn ? -1 : 1;
      if (x !== y) return x > y ? 1 : -1;
    }
    return 0;
  }

  function formatSemver(v) {
    return v.major + "." + v.minor + "." + v.patch
      + (v.prerelease.length ? "-" + v.prerelease.join(".") : "")
      + (v.build ? "+" + v.build : "");
  }

  /* Identifiers */
  function uuidFromBytes(bytes) {
    const hex = toHex(bytes);
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
  }

  function uuidV4() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = randomBytes(16);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    return uuidFromBytes(bytes);
  }

  let lastV7 = 0;
  function uuidV7() {
    let ms = Date.now();
    if (ms <= lastV7) ms = lastV7 + 1;
    lastV7 = ms;
    const bytes = randomBytes(16);
    let time = BigInt(ms);
    for (let i = 5; i >= 0; i -= 1) {
      bytes[i] = Number(time & 0xffn);
      time >>= 8n;
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x70;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    return uuidFromBytes(bytes);
  }

  let v1Clock = randomInt(0x4000);
  let v1Last = 0n;
  const v1Node = (() => {
    const node = randomBytes(6);
    node[0] |= 0x01;
    return node;
  })();
  function uuidV1() {
    let ticks = BigInt(Date.now()) * 10000n + 0x01b21dd213814000n;
    if (ticks <= v1Last) ticks = v1Last + 1n;
    v1Last = ticks;
    const low = ticks & 0xffffffffn;
    const mid = (ticks >> 32n) & 0xffffn;
    const high = (ticks >> 48n) & 0x0fffn;
    v1Clock = (v1Clock + 1) & 0x3fff;
    const hex = low.toString(16).padStart(8, "0")
      + mid.toString(16).padStart(4, "0")
      + (high | 0x1000n).toString(16).padStart(4, "0")
      + ((v1Clock | 0x8000) >>> 0).toString(16).padStart(4, "0")
      + toHex(v1Node);
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
  }

  const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  function ulid(time) {
    let ms = time == null ? Date.now() : time;
    let head = "";
    for (let i = 0; i < 10; i += 1) {
      head = CROCKFORD[ms % 32] + head;
      ms = Math.floor(ms / 32);
    }
    return head + randomFrom(CROCKFORD, 16);
  }

  function ulidTime(id) {
    const text = String(id).trim().toUpperCase();
    if (!/^[0-9A-HJKMNP-TV-Z]{26}$/.test(text)) throw new Error("Not a ULID.");
    let ms = 0;
    for (const char of text.slice(0, 10)) ms = ms * 32 + CROCKFORD.indexOf(char);
    return ms;
  }

  /* Colors */
  function clamp01(value) {
    return Math.min(1, Math.max(0, value));
  }

  function rgbToHsl(r, g, b) {
    const rn = r / 255;
    const gn = g / 255;
    const bn = b / 255;
    const max = Math.max(rn, gn, bn);
    const min = Math.min(rn, gn, bn);
    const l = (max + min) / 2;
    let h = 0;
    let s = 0;
    if (max !== min) {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0);
      else if (max === gn) h = (bn - rn) / d + 2;
      else h = (rn - gn) / d + 4;
      h *= 60;
    }
    return { h, s: s * 100, l: l * 100 };
  }

  function hslToRgb(h, s, l) {
    const hn = (((h % 360) + 360) % 360) / 360;
    const sn = clamp01(s / 100);
    const ln = clamp01(l / 100);
    if (sn === 0) {
      const v = Math.round(ln * 255);
      return { r: v, g: v, b: v };
    }
    const q = ln < 0.5 ? ln * (1 + sn) : ln + sn - ln * sn;
    const p = 2 * ln - q;
    const hue = (t) => {
      let x = t;
      if (x < 0) x += 1;
      if (x > 1) x -= 1;
      if (x < 1 / 6) return p + (q - p) * 6 * x;
      if (x < 1 / 2) return q;
      if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
      return p;
    };
    return {
      r: Math.round(hue(hn + 1 / 3) * 255),
      g: Math.round(hue(hn) * 255),
      b: Math.round(hue(hn - 1 / 3) * 255),
    };
  }

  function rgbToHsv(r, g, b) {
    const rn = r / 255;
    const gn = g / 255;
    const bn = b / 255;
    const max = Math.max(rn, gn, bn);
    const min = Math.min(rn, gn, bn);
    const d = max - min;
    let h = 0;
    if (d) {
      if (max === rn) h = ((gn - bn) / d) % 6;
      else if (max === gn) h = (bn - rn) / d + 2;
      else h = (rn - gn) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h, s: max ? (d / max) * 100 : 0, v: max * 100 };
  }

  function hsvToRgb(h, s, v) {
    const sn = clamp01(s / 100);
    const vn = clamp01(v / 100);
    const c = vn * sn;
    const hp = ((((h % 360) + 360) % 360)) / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    let rgb = [0, 0, 0];
    if (hp < 1) rgb = [c, x, 0];
    else if (hp < 2) rgb = [x, c, 0];
    else if (hp < 3) rgb = [0, c, x];
    else if (hp < 4) rgb = [0, x, c];
    else if (hp < 5) rgb = [x, 0, c];
    else rgb = [c, 0, x];
    const m = vn - c;
    return { r: Math.round((rgb[0] + m) * 255), g: Math.round((rgb[1] + m) * 255), b: Math.round((rgb[2] + m) * 255) };
  }

  function parseColor(input) {
    const text = String(input).trim().toLowerCase();
    if (!text) throw new Error("Enter a color.");
    let match = /^#?([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(text);
    if (match) {
      let hex = match[1];
      if (hex.length <= 4) hex = hex.split("").map((c) => c + c).join("");
      const a = hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1;
      return { r: parseInt(hex.slice(0, 2), 16), g: parseInt(hex.slice(2, 4), 16), b: parseInt(hex.slice(4, 6), 16), a };
    }
    match = /^0x([0-9a-f]{6})$/.exec(text);
    if (match) return parseColor("#" + match[1]);
    const nums = (body) => body.split(/[\s,/]+/).filter(Boolean);
    const alpha = (raw) => (raw === undefined ? 1 : raw.endsWith("%") ? parseFloat(raw) / 100 : parseFloat(raw));
    match = /^rgba?\((.+)\)$/.exec(text);
    if (match) {
      const p = nums(match[1]);
      const channel = (raw) => (raw.endsWith("%") ? Math.round(parseFloat(raw) * 2.55) : Math.round(parseFloat(raw)));
      const color = { r: channel(p[0]), g: channel(p[1]), b: channel(p[2]), a: alpha(p[3]) };
      if ([color.r, color.g, color.b].some((v) => !Number.isFinite(v) || v < 0 || v > 255)) throw new Error("RGB values must be 0-255.");
      return color;
    }
    match = /^hsla?\((.+)\)$/.exec(text);
    if (match) {
      const p = nums(match[1]);
      const rgb = hslToRgb(parseFloat(p[0]), parseFloat(p[1]), parseFloat(p[2]));
      return { ...rgb, a: alpha(p[3]) };
    }
    match = /^hsva?\((.+)\)$/.exec(text) || /^hsb\((.+)\)$/.exec(text);
    if (match) {
      const p = nums(match[1]);
      return { ...hsvToRgb(parseFloat(p[0]), parseFloat(p[1]), parseFloat(p[2])), a: alpha(p[3]) };
    }
    if (typeof document !== "undefined") {
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.fillStyle = "#010203";
      ctx.fillStyle = text;
      const normalized = ctx.fillStyle;
      if (normalized !== "#010203" || text === "#010203") {
        if (normalized.startsWith("#")) return parseColor(normalized);
        return parseColor(normalized);
      }
    }
    throw new Error("Unrecognized color '" + input + "'. Try #ff8800, rgb(), hsl(), or a CSS name.");
  }

  function hex2(value) {
    return Math.round(value).toString(16).padStart(2, "0");
  }

  function colorFormats(color) {
    const { r, g, b, a } = color;
    const hsl = rgbToHsl(r, g, b);
    const hsv = rgbToHsv(r, g, b);
    const round = (n, d) => Number(n.toFixed(d === undefined ? 1 : d));
    const alphaText = a < 1 ? round(a, 3) : null;
    const hex = "#" + hex2(r) + hex2(g) + hex2(b);
    const f = (n) => round(n / 255, 3);
    return {
      hex,
      hex8: hex + hex2(a * 255),
      rgb: alphaText === null ? "rgb(" + r + ", " + g + ", " + b + ")" : "rgba(" + r + ", " + g + ", " + b + ", " + alphaText + ")",
      hsl: "hsl(" + round(hsl.h, 0) + ", " + round(hsl.s) + "%, " + round(hsl.l) + "%" + (alphaText === null ? ")" : ", " + alphaText + ")"),
      hsv: "hsv(" + round(hsv.h, 0) + ", " + round(hsv.s) + "%, " + round(hsv.v) + "%)",
      cssModern: "rgb(" + r + " " + g + " " + b + (alphaText === null ? "" : " / " + alphaText) + ")",
      uicolor: "UIColor(red: " + f(r) + ", green: " + f(g) + ", blue: " + f(b) + ", alpha: " + round(a, 3) + ")",
      swiftui: "Color(red: " + f(r) + ", green: " + f(g) + ", blue: " + f(b) + (a < 1 ? ", opacity: " + round(a, 3) : "") + ")",
      nscolor: "NSColor(srgbRed: " + f(r) + ", green: " + f(g) + ", blue: " + f(b) + ", alpha: " + round(a, 3) + ")",
      android: "#" + hex2(a * 255) + hex2(r) + hex2(g) + hex2(b),
      luminance: relativeLuminance(r, g, b),
    };
  }

  function relativeLuminance(r, g, b) {
    const lin = (v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }

  function contrastRatio(l1, l2) {
    const hi = Math.max(l1, l2);
    const lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  /* ZIP (stored, no compression) for multi-file downloads. */
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i += 1) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function makeZip(files) {
    const chunks = [];
    const central = [];
    let offset = 0;
    const now = new Date();
    const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | Math.floor(now.getSeconds() / 2);
    const dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    for (const file of files) {
      const name = utf8(file.name);
      const data = file.bytes;
      const crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true);
      local.setUint16(4, 20, true);
      local.setUint16(6, 0x0800, true);
      local.setUint16(8, 0, true);
      local.setUint16(10, dosTime, true);
      local.setUint16(12, dosDate, true);
      local.setUint32(14, crc, true);
      local.setUint32(18, data.length, true);
      local.setUint32(22, data.length, true);
      local.setUint16(26, name.length, true);
      local.setUint16(28, 0, true);
      chunks.push(new Uint8Array(local.buffer), name, data);
      const entry = new DataView(new ArrayBuffer(46));
      entry.setUint32(0, 0x02014b50, true);
      entry.setUint16(4, 20, true);
      entry.setUint16(6, 20, true);
      entry.setUint16(8, 0x0800, true);
      entry.setUint16(10, 0, true);
      entry.setUint16(12, dosTime, true);
      entry.setUint16(14, dosDate, true);
      entry.setUint32(16, crc, true);
      entry.setUint32(20, data.length, true);
      entry.setUint32(24, data.length, true);
      entry.setUint16(28, name.length, true);
      entry.setUint32(42, offset, true);
      central.push(new Uint8Array(entry.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const centralSize = central.reduce((sum, part) => sum + part.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, files.length, true);
    end.setUint16(10, files.length, true);
    end.setUint32(12, centralSize, true);
    end.setUint32(16, offset, true);
    return new Blob([...chunks, ...central, new Uint8Array(end.buffer)], { type: "application/zip" });
  }

  /* Lexer shared by the brace-language formatters (JS, CSS, GraphQL, Mongo). */
  const WORD_CHARS = {
    js: /[A-Za-z0-9_$.#@]/,
    css: /[A-Za-z0-9_\-.#%!@]/,
    graphql: /[A-Za-z0-9_$@!]/,
  };

  function lexCode(source, options) {
    const opts = options || {};
    const wordChar = WORD_CHARS[opts.mode || "js"];
    const tokens = [];
    let i = 0;
    const text = String(source).replace(/\r\n?/g, "\n");
    let newlineSeen = false;
    let spaceSeen = false;
    const push = (type, value) => {
      tokens.push({ type, value, nl: newlineSeen, sp: spaceSeen || newlineSeen });
      newlineSeen = false;
      spaceSeen = false;
    };
    while (i < text.length) {
      const char = text[i];
      if (char === "\n") {
        newlineSeen = true;
        i += 1;
        continue;
      }
      if (/\s/.test(char)) {
        spaceSeen = true;
        i += 1;
        continue;
      }
      if (opts.hashComments && char === "#") {
        const end = text.indexOf("\n", i);
        push("comment", text.slice(i, end < 0 ? text.length : end));
        i = end < 0 ? text.length : end;
        continue;
      }
      if (!opts.noSlashComments && char === "/" && text[i + 1] === "/") {
        const end = text.indexOf("\n", i);
        push("comment", text.slice(i, end < 0 ? text.length : end));
        i = end < 0 ? text.length : end;
        continue;
      }
      if (char === "/" && text[i + 1] === "*") {
        const end = text.indexOf("*/", i + 2);
        const stop = end < 0 ? text.length : end + 2;
        push("block", text.slice(i, stop));
        i = stop;
        continue;
      }
      if (char === '"' && opts.blockStrings && text.startsWith('"""', i)) {
        const end = text.indexOf('"""', i + 3);
        const stop = end < 0 ? text.length : end + 3;
        push("string", text.slice(i, stop));
        i = stop;
        continue;
      }
      if (char === '"' || char === "'" || char === "`") {
        let j = i + 1;
        while (j < text.length && text[j] !== char) {
          if (text[j] === "\\") j += 1;
          else if (text[j] === "\n" && char !== "`") break;
          j += 1;
        }
        push("string", text.slice(i, j + 1));
        i = j + 1;
        continue;
      }
      if (opts.regex && char === "/") {
        const prev = tokens.length ? tokens[tokens.length - 1] : null;
        const allowsRegex = !prev || (prev.type === "punct" && !/[)\]}]/.test(prev.value)) || (prev.type === "word" && /^(return|typeof|case|do|else|in|of|new|delete|void|throw|yield|await)$/.test(prev.value));
        if (allowsRegex) {
          let j = i + 1;
          let inClass = false;
          while (j < text.length && text[j] !== "\n") {
            if (text[j] === "\\") j += 1;
            else if (text[j] === "[") inClass = true;
            else if (text[j] === "]") inClass = false;
            else if (text[j] === "/" && !inClass) break;
            j += 1;
          }
          if (text[j] === "/") {
            j += 1;
            while (/[a-z]/i.test(text[j] || "")) j += 1;
            push("string", text.slice(i, j));
            i = j;
            continue;
          }
        }
      }
      if (wordChar.test(char) && !(char === "." && text.startsWith("...", i))) {
        let j = i;
        while (j < text.length && wordChar.test(text[j]) && !(text[j] === "." && text.startsWith("...", j))) j += 1;
        push("word", text.slice(i, j));
        i = j;
        continue;
      }
      const three = text.slice(i, i + 3);
      const two = text.slice(i, i + 2);
      if (["===", "!==", "...", ">>>", "**=", "<<=", ">>=", "&&=", "||=", "??="].includes(three)) {
        push("punct", three);
        i += 3;
      } else if (["=>", "==", "!=", "<=", ">=", "&&", "||", "??", "++", "--", "+=", "-=", "*=", "/=", "%=", "<<", ">>", "?.", "**", "::"].includes(two)) {
        push("punct", two);
        i += 2;
      } else {
        push("punct", char);
        i += 1;
      }
    }
    return tokens;
  }

  /*
   * Re-indents brace languages. Not a full parser: it keeps strings, comments and
   * source line breaks, then puts blocks, statements and object members on their own lines.
   */
  function formatBraces(source, options) {
    const opts = options || {};
    const mode = opts.mode || "js";
    const css = mode === "css";
    const graphql = mode === "graphql";
    const unit = opts.indent || "  ";
    const tokens = lexCode(source, opts);
    const out = [];
    const stack = [];
    let line = "";
    let depth = 0;
    let prev = null;
    let unary = false;
    const flush = () => {
      const trimmed = line.trim();
      if (trimmed) out.push(unit.repeat(Math.max(0, depth)) + trimmed);
      line = "";
    };
    const blank = () => {
      if (out.length && out[out.length - 1] !== "") out.push("");
    };
    const noSpaceBefore = new Set([",", ";", ")", "]", ".", "?.", ":"]);
    const noSpaceAfter = new Set(["(", "[", ".", "?.", "!", "~", "...", "@"]);
    const spacedCalls = new Set(["if", "for", "while", "switch", "catch", "return", "typeof", "await", "and", "or", "not"]);
    const cssSelectorColon = (index) => {
      for (let k = index + 1; k < tokens.length; k += 1) {
        const v = tokens[k].value;
        if (v === "{") return true;
        if (v === ";" || v === "}") return false;
      }
      return false;
    };
    const needsSpace = (token, index) => {
      if (!line.trim() || !prev) return false;
      const v = token.value;
      if (css) {
        if (prev.value === ":" && stack[stack.length - 1] === "{" && !cssSelectorColon(index - 1)) return true;
        if (v === ":" || prev.value === ":") return token.sp && v !== ":";
        if (v === "," || v === ";" || v === ")") return false;
        if (prev.value === "(" ) return false;
        return token.sp || prev.value === ",";
      }
      if (unary) return false;
      if (graphql && prev.value === "..." && v === "on") return true;
      if (token.type === "word" && v.startsWith(".") && (prev.type !== "punct" || /[)\]}]/.test(prev.value))) return false;
      if (token.type === "punct" && noSpaceBefore.has(v)) return false;
      if (prev.type === "punct" && noSpaceAfter.has(prev.value)) return false;
      if ((v === "(" || v === "[") && (prev.type === "word" || prev.type === "string" || /[)\]]/.test(prev.value))) {
        return prev.type === "word" && spacedCalls.has(prev.value);
      }
      if ((v === "++" || v === "--") && (prev.type === "word" || /[)\]]/.test(prev.value))) return false;
      if (graphql && v === "!" ) return false;
      return true;
    };
    for (let index = 0; index < tokens.length; index += 1) {
      const token = tokens[index];
      const next = tokens[index + 1];
      const top = stack[stack.length - 1];
      const v = token.value;
      if (!css && token.nl && (top === "{" || top === "[*" || top === undefined) && line.trim()) {
        const ended = prev && (prev.type !== "punct" || /[)\]}]/.test(prev.value) || prev.value === "++" || prev.value === "--");
        const continues = token.type === "punct" && /^[.?:+\-*/%&|^=<>,]/.test(v) && v !== "++" && v !== "--";
        if (ended && !continues) flush();
      }
      if (token.type === "comment" || token.type === "block") {
        if (token.nl || !line.trim()) flush();
        else line += " ";
        line += v;
        if (token.type === "comment" || token.nl) flush();
        prev = token;
        unary = false;
        continue;
      }
      const opensArray = v === "[" && opts.expandArrays && next && (next.value === "{" || next.value === "[");
      if (v === "{" || opensArray) {
        if (line.trim() && !/[([]$/.test(line)) line += " ";
        line += v;
        stack.push(v === "{" ? "{" : "[*");
        flush();
        depth += 1;
        prev = token;
        unary = false;
        continue;
      }
      if (v === "}" || (v === "]" && top === "[*")) {
        flush();
        depth = Math.max(0, depth - 1);
        stack.pop();
        line = v;
        const following = next ? next.value : "";
        const joins = [",", ";", ")", "]", ".", "?.", "("].includes(following)
          || (v === "}" && ["else", "catch", "finally", "while"].includes(following) && !css);
        if (!joins) {
          flush();
          if (depth === 0 && opts.blankBetweenBlocks) blank();
        }
        prev = token;
        unary = false;
        continue;
      }
      if (needsSpace(token, index)) line += " ";
      line += v;
      const wasUnary = (v === "-" || v === "+" || v === "!" || v === "~")
        && (!prev || (prev.type === "punct" && !/[)\]}]/.test(prev.value)) || (prev.type === "word" && spacedCalls.has(prev.value)));
      if (v === "(" || v === "[") {
        stack.push(v);
      } else if (v === ")" || v === "]") {
        if (stack[stack.length - 1] === (v === ")" ? "(" : "[")) stack.pop();
      }
      const nowTop = stack[stack.length - 1];
      const inBlock = nowTop === "{" || nowTop === undefined;
      const startsField = next && ((next.type === "word" && !next.value.startsWith("@")) || next.value === "...");
      if (v === ";" && inBlock) flush();
      else if (!css && v === "," && (nowTop === "{" || nowTop === "[*")) flush();
      else if (graphql && nowTop === "{" && startsField) {
        const joiner = ["on", "fragment", "query", "mutation", "subscription"].includes(v);
        if ((token.type === "word" || v === ")") && !joiner && !v.startsWith("@")) flush();
        else if (token.type === "word" && v.startsWith("@") && next.value !== "(") flush();
      }
      unary = wasUnary;
      prev = token;
    }
    flush();
    return out.join("\n").replace(/\n{3,}/g, "\n\n").replace(/\s+$/, "") + "\n";
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function bytesFromFile(file) {
    if (!file || !file.bytes) throw new Error("Choose a file.");
    return file.bytes;
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("This browser could not decode the image."));
      image.src = src;
    });
  }

  async function imageFromFile(file) {
    if (!file || !file.bytes) throw new Error("Choose an image.");
    const url = URL.createObjectURL(new Blob([file.bytes], { type: file.type || "image/png" }));
    try {
      return await loadImage(url);
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }

  function canvasToBlob(canvas, type, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Could not encode " + type + "."))), type, quality);
    });
  }

  function baseName(name) {
    const text = String(name || "image");
    const dot = text.lastIndexOf(".");
    return dot > 0 ? text.slice(0, dot) : text;
  }

  function shellSplit(command) {
    const args = [];
    let current = "";
    let quote = null;
    let has = false;
    const text = String(command).replace(/\\\r?\n/g, " ");
    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      if (quote) {
        if (char === quote) quote = null;
        else if (char === "\\" && quote === '"' && /["\\$`]/.test(text[i + 1] || "")) current += text[++i];
        else current += char;
        continue;
      }
      if (char === "'" || char === '"') {
        quote = char;
        has = true;
      } else if (char === "$" && text[i + 1] === "'") {
        quote = "'";
        has = true;
        i += 1;
      } else if (char === "\\") {
        current += text[++i] || "";
        has = true;
      } else if (/\s/.test(char)) {
        if (has || current) args.push(current);
        current = "";
        has = false;
      } else {
        current += char;
        has = true;
      }
    }
    if (quote) throw new Error("Command has an unclosed quote.");
    if (has || current) args.push(current);
    return args;
  }

  function shellQuote(value) {
    const text = String(value);
    if (/^[A-Za-z0-9_\/:.,=@%+-]+$/.test(text)) return text;
    return "'" + text.replace(/'/g, "'\\''") + "'";
  }

  function parseHeaderLines(text) {
    const headers = [];
    for (const raw of lines(text)) {
      const line = raw.trim();
      if (!line || /^HTTP\/\d/i.test(line) || /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /i.test(line)) continue;
      const index = line.indexOf(":");
      if (index <= 0) continue;
      headers.push([line.slice(0, index).trim(), line.slice(index + 1).trim()]);
    }
    return headers;
  }

  function formatDuration(ms) {
    let rest = Math.abs(ms);
    const parts = [];
    const units = [["d", 86400000], ["h", 3600000], ["m", 60000], ["s", 1000]];
    for (const [label, size] of units) {
      if (rest >= size) {
        parts.push(Math.floor(rest / size) + label);
        rest %= size;
      }
    }
    if (!parts.length) parts.push(Math.round(rest) + "ms");
    return (ms < 0 ? "-" : "") + parts.join(" ");
  }

  function relativeTime(ms) {
    const diff = ms - Date.now();
    const abs = Math.abs(diff);
    const units = [["year", 31536000000], ["month", 2592000000], ["day", 86400000], ["hour", 3600000], ["minute", 60000], ["second", 1000]];
    for (const [unit, size] of units) {
      if (abs >= size || unit === "second") {
        const value = Math.round(diff / size);
        if (typeof Intl !== "undefined" && Intl.RelativeTimeFormat) {
          return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(value, unit);
        }
        return value + " " + unit + "s";
      }
    }
    return "now";
  }

  window.VLKit = {
    categories,
    tools,
    bySlug,
    register,
    util: {
      utf8,
      fromUtf8,
      toHex,
      fromHex,
      toBase64,
      fromBase64,
      toBase64Url,
      randomBytes,
      randomInt,
      randomFrom,
      pick,
      parseJSON,
      formatBytes,
      escapeRegExp,
      lines,
      nonEmptyLines,
      clampInt,
      isPlainObject,
      yamlParse,
      yamlParseOne,
      yamlDump,
      parseJsonOrYaml,
      detectDelimiter,
      parseCSV,
      toCSV,
      deepDiff,
      diffRows,
      lineDiff,
      parseCron,
      cronNext,
      describeCron,
      CRON_FIELDS,
      MONTH_NAMES,
      DAY_NAMES,
      parseSemver,
      compareSemver,
      formatSemver,
      uuidV1,
      uuidV4,
      uuidV7,
      uuidFromBytes,
      ulid,
      ulidTime,
      rgbToHsl,
      hslToRgb,
      rgbToHsv,
      hsvToRgb,
      parseColor,
      colorFormats,
      relativeLuminance,
      contrastRatio,
      crc32,
      makeZip,
      lexCode,
      formatBraces,
      download,
      bytesFromFile,
      loadImage,
      imageFromFile,
      canvasToBlob,
      baseName,
      shellSplit,
      shellQuote,
      parseHeaderLines,
      formatDuration,
      relativeTime,
    },
  };
})();
