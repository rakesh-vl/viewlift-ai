/* Converters & calculators */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  const DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz";

  function parseBig(text, base) {
    let raw = String(text || "").trim().toLowerCase().replace(/[_\s,]/g, "");
    if (!raw) throw new Error("Enter a number.");
    let negative = false;
    if (raw.startsWith("-")) {
      negative = true;
      raw = raw.slice(1);
    }
    let radix = Number(base);
    if (base === "auto") {
      radix = 10;
      if (/^0x/.test(raw)) [radix, raw] = [16, raw.slice(2)];
      else if (/^0b/.test(raw)) [radix, raw] = [2, raw.slice(2)];
      else if (/^0o/.test(raw)) [radix, raw] = [8, raw.slice(2)];
    } else raw = raw.replace(radix === 16 ? /^0x/ : radix === 2 ? /^0b/ : radix === 8 ? /^0o/ : /^$/, "");
    if (!raw) throw new Error("Enter digits after the prefix.");
    let value = 0n;
    for (const char of raw) {
      const digit = DIGITS.indexOf(char);
      if (digit < 0 || digit >= radix) throw new Error("'" + char + "' is not a base-" + radix + " digit.");
      value = value * BigInt(radix) + BigInt(digit);
    }
    return { value: negative ? -value : value, radix };
  }

  function toBase(value, radix) {
    if (value === 0n) return "0";
    const negative = value < 0n;
    let rest = negative ? -value : value;
    let out = "";
    const big = BigInt(radix);
    while (rest > 0n) {
      out = DIGITS[Number(rest % big)] + out;
      rest /= big;
    }
    return (negative ? "-" : "") + out;
  }

  function group(text, size, sep) {
    const negative = text.startsWith("-");
    const body = negative ? text.slice(1) : text;
    const parts = [];
    for (let i = body.length; i > 0; i -= size) parts.unshift(body.slice(Math.max(0, i - size), i));
    return (negative ? "-" : "") + parts.join(sep);
  }

  function twos(value, bits) {
    const size = 1n << BigInt(bits);
    return ((value % size) + size) % size;
  }

  function signed(value, bits) {
    const unsigned = twos(value, bits);
    return unsigned >= 1n << BigInt(bits - 1) ? unsigned - (1n << BigInt(bits)) : unsigned;
  }

  /* Bytes */

  const BYTE_UNITS = [
    ["bit", 1 / 8], ["B", 1],
    ["kB", 1e3], ["MB", 1e6], ["GB", 1e9], ["TB", 1e12], ["PB", 1e15],
    ["KiB", 1024], ["MiB", 1024 ** 2], ["GiB", 1024 ** 3], ["TiB", 1024 ** 4], ["PiB", 1024 ** 5],
    ["kbit", 1e3 / 8], ["Mbit", 1e6 / 8], ["Gbit", 1e9 / 8],
  ];
  const BYTE_FACTOR = Object.fromEntries(BYTE_UNITS);

  function nice(value) {
    if (!Number.isFinite(value)) return String(value);
    if (value === 0) return "0";
    const abs = Math.abs(value);
    if (abs >= 1e15 || abs < 1e-6) return value.toExponential(6).replace(/\.?0+e/, "e");
    return Number(value.toPrecision(12)).toLocaleString("en-US", { maximumFractionDigits: 10 });
  }

  function number(value, label) {
    const n = Number(String(value).replace(/,/g, ""));
    if (!Number.isFinite(n)) throw new Error((label || "Value") + " must be a number.");
    return n;
  }

  /* Units */

  const UNITS = {
    length: { base: "m", units: { nm: 1e-9, "µm": 1e-6, mm: 1e-3, cm: 0.01, m: 1, km: 1000, in: 0.0254, ft: 0.3048, yd: 0.9144, mi: 1609.344, nmi: 1852 } },
    mass: { base: "kg", units: { mg: 1e-6, g: 1e-3, kg: 1, t: 1000, oz: 0.028349523125, lb: 0.45359237, st: 6.35029318 } },
    time: { base: "s", units: { ns: 1e-9, "µs": 1e-6, ms: 1e-3, s: 1, min: 60, h: 3600, d: 86400, wk: 604800, yr: 31557600 } },
    speed: { base: "m/s", units: { "m/s": 1, "km/h": 1 / 3.6, mph: 0.44704, kn: 0.514444, "ft/s": 0.3048 } },
    area: { base: "m²", units: { "mm²": 1e-6, "cm²": 1e-4, "m²": 1, ha: 1e4, "km²": 1e6, "in²": 0.00064516, "ft²": 0.09290304, ac: 4046.8564224, "mi²": 2589988.110336 } },
    volume: { base: "L", units: { mL: 1e-3, L: 1, "m³": 1000, tsp: 0.00492892, tbsp: 0.0147868, "fl oz": 0.0295735, cup: 0.236588, pt: 0.473176, qt: 0.946353, gal: 3.785411784 } },
    display: { base: "pt", units: { pt: 1, "px @1x": 1, "px @2x": 0.5, "px @3x": 1 / 3, "dp (Android)": 1, "px xxhdpi (3x)": 1 / 3, "in @163ppi (1x)": 163 } },
    angle: { base: "deg", units: { deg: 1, rad: 180 / Math.PI, grad: 0.9, turn: 360 } },
    frequency: { base: "Hz", units: { Hz: 1, kHz: 1e3, MHz: 1e6, GHz: 1e9, rpm: 1 / 60 } },
  };

  function convertTemperature(value, from) {
    const c = from === "°C" ? value : from === "°F" ? (value - 32) * 5 / 9 : value - 273.15;
    return [["°C", c], ["°F", c * 9 / 5 + 32], ["K", c + 273.15]];
  }

  /* Semver ranges, npm style plus CocoaPods ~> */

  function coerce(text) {
    const m = /^v?(\d+|[x*])(?:\.(\d+|[x*]))?(?:\.(\d+|[x*]))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/i.exec(String(text).trim());
    if (!m) throw new Error("'" + text + "' is not a version.");
    const part = (x) => (x === undefined || /^[x*]$/i.test(x) ? null : Number(x));
    return { major: part(m[1]), minor: part(m[2]), patch: part(m[3]), prerelease: m[4] ? m[4].split(".") : [] };
  }

  const full = (p) => ({ major: p.major || 0, minor: p.minor || 0, patch: p.patch || 0, prerelease: p.prerelease || [], build: "" });

  function comparatorsFor(token) {
    let m;
    if ((m = /^~>\s*(.+)$/.exec(token))) {
      const p = coerce(m[1]);
      const low = full(p);
      const high = p.patch !== null ? { major: p.major, minor: p.minor + 1, patch: 0 } : p.minor !== null ? { major: p.major + 1, minor: 0, patch: 0 } : { major: p.major + 1, minor: 0, patch: 0 };
      return [[">=", low], ["<", full(high)]];
    }
    if ((m = /^\^(.+)$/.exec(token))) {
      const p = coerce(m[1]);
      const low = full(p);
      let high;
      if ((p.major || 0) > 0 || p.minor === null) high = { major: (p.major || 0) + 1, minor: 0, patch: 0 };
      else if ((p.minor || 0) > 0 || p.patch === null) high = { major: 0, minor: p.minor + 1, patch: 0 };
      else high = { major: 0, minor: 0, patch: p.patch + 1 };
      return [[">=", low], ["<", full(high)]];
    }
    if ((m = /^~(?!>)(.+)$/.exec(token))) {
      const p = coerce(m[1]);
      const high = p.minor === null ? { major: p.major + 1, minor: 0, patch: 0 } : { major: p.major, minor: p.minor + 1, patch: 0 };
      return [[">=", full(p)], ["<", full(high)]];
    }
    if ((m = /^(>=|<=|>|<|=)?\s*(.+)$/.exec(token))) {
      const op = m[1] || "=";
      const p = coerce(m[2]);
      if (p.major === null) return [];
      if (op === "=" && (p.minor === null || p.patch === null)) {
        const high = p.minor === null ? { major: p.major + 1, minor: 0, patch: 0 } : { major: p.major, minor: p.minor + 1, patch: 0 };
        return [[">=", full(p)], ["<", full(high)]];
      }
      if (op === "<=" && (p.minor === null || p.patch === null)) {
        const high = p.minor === null ? { major: p.major + 1, minor: 0, patch: 0 } : { major: p.major, minor: p.minor + 1, patch: 0 };
        return [["<", full(high)]];
      }
      if (op === ">" && (p.minor === null || p.patch === null)) {
        const high = p.minor === null ? { major: p.major + 1, minor: 0, patch: 0 } : { major: p.major, minor: p.minor + 1, patch: 0 };
        return [[">=", full(high)]];
      }
      return [[op, full(p)]];
    }
    throw new Error("Cannot read '" + token + "'.");
  }

  function satisfies(version, range) {
    const v = util.parseSemver(version);
    return String(range).split("||").some((set) => {
      let text = set.trim().replace(/~>\s+/g, "~>").replace(/(>=|<=|>|<|=)\s+/g, "$1").replace(/,/g, " ");
      const hyphen = /^(\S+)\s+-\s+(\S+)$/.exec(text);
      if (hyphen) text = ">=" + hyphen[1] + " <=" + hyphen[2];
      if (!text || text === "*") return true;
      return text.split(/\s+/).flatMap(comparatorsFor).every(([op, target]) => {
        const c = util.compareSemver(v, target);
        return op === ">=" ? c >= 0 : op === ">" ? c > 0 : op === "<=" ? c <= 0 : op === "<" ? c < 0 : c === 0;
      });
    });
  }

  function adler32(bytes) {
    let a = 1;
    let b = 0;
    for (let i = 0; i < bytes.length; i += 1) {
      a = (a + bytes[i]) % 65521;
      b = (b + a) % 65521;
    }
    return ((b << 16) | a) >>> 0;
  }

  async function checksums(bytes, ctx, withServer) {
    const rows = [["CRC-32", util.crc32(bytes).toString(16).padStart(8, "0")], ["Adler-32", adler32(bytes).toString(16).padStart(8, "0")]];
    for (const name of ["SHA-1", "SHA-256", "SHA-384", "SHA-512"]) {
      rows.push([name, util.toHex(new Uint8Array(await crypto.subtle.digest(name, bytes)))]);
    }
    if (withServer) {
      if (bytes.length > 25 * 1024 * 1024) rows.push(["MD5 / SHA-3", "Skipped: files over 25 MB are hashed in the browser only."]);
      else {
        const result = await ctx.server("hash", { data: util.toBase64(bytes), algorithms: ["md5", "sha3_256", "sha3_512"] });
        rows.splice(2, 0, ["MD5", result.digests.md5]);
        rows.push(["SHA3-256", result.digests.sha3_256], ["SHA3-512", result.digests.sha3_512]);
      }
    }
    return rows;
  }

  register("calc", [
    {
      slug: "number-base-converter",
      name: "Number base converter",
      description: "Convert big integers between binary, octal, decimal, hex, base 36 or any base, with two's complement views.",
      keywords: "binary hex octal decimal radix base convert twos complement",
      inputs: [
        { id: "value", label: "Number", type: "text", value: "0xFF7A", wide: true, hint: "Prefixes 0x, 0b and 0o work in Detect mode." },
        { id: "base", label: "Input base", type: "select", options: [["auto", "Detect"], ["2", "Binary"], ["8", "Octal"], ["10", "Decimal"], ["16", "Hex"], ["36", "Base 36"]], value: "auto" },
        { id: "custom", label: "Also show base", type: "number", value: 32, min: 2, max: 36 },
      ],
      run(v) {
        const { value, radix } = parseBig(v.value, v.base);
        const custom = util.clampInt(v.custom, 2, 36);
        const rows = [
          ["Decimal", group(toBase(value, 10), 3, ",")],
          ["Hex", (value < 0n ? "-0x" : "0x") + toBase(value < 0n ? -value : value, 16).toUpperCase()],
          ["Octal", (value < 0n ? "-0o" : "0o") + toBase(value < 0n ? -value : value, 8)],
          ["Binary", group(toBase(value, 2), 4, " ")],
          ["Base 36", toBase(value, 36)],
          ["Base " + custom, toBase(value, custom)],
          ["Bit length", String(toBase(value < 0n ? -value : value, 2).length)],
        ];
        const widths = [8, 16, 32, 64].map((bits) => {
          const u = twos(value, bits);
          return ["Int" + bits, String(signed(value, bits)), "UInt" + bits + " " + String(u), "0x" + toBase(u, 16).toUpperCase().padStart(bits / 4, "0")];
        });
        return [
          { type: "kv", label: "Read as base " + radix, rows },
          { type: "table", label: "Fixed-width (Swift Int8…Int64 truncation)", columns: ["Signed", "Value", "Unsigned", "Hex"], rows: widths },
        ];
      },
    },
    {
      slug: "byte-converter",
      name: "Byte converter",
      description: "Convert between bits, bytes, decimal units (kB, MB, GB) and binary units (KiB, MiB, GiB).",
      keywords: "bytes kb mb gb kib mib size convert",
      inputs: [
        { id: "value", label: "Value", type: "text", value: "1.5" },
        { id: "unit", label: "Unit", type: "select", options: BYTE_UNITS.map(([u]) => u), value: "GB" },
      ],
      run(v) {
        const bytes = number(v.value) * BYTE_FACTOR[v.unit];
        return [
          { type: "table", label: "Decimal (SI, what macOS Finder shows)", columns: ["Unit", "Value"], rows: ["B", "kB", "MB", "GB", "TB", "PB"].map((u) => [u, nice(bytes / BYTE_FACTOR[u])]) },
          { type: "table", label: "Binary (IEC)", columns: ["Unit", "Value"], rows: ["KiB", "MiB", "GiB", "TiB", "PiB"].map((u) => [u, nice(bytes / BYTE_FACTOR[u])]) },
          { type: "table", label: "Bits", columns: ["Unit", "Value"], rows: ["bit", "kbit", "Mbit", "Gbit"].map((u) => [u, nice(bytes / BYTE_FACTOR[u])]) },
        ];
      },
    },
    {
      slug: "percentage-calculator",
      name: "Percentage calculator",
      description: "Percent of a value, what percent one number is of another, percent change, and increase or decrease by a percent.",
      keywords: "percent percentage change increase decrease ratio",
      inputs: [
        { id: "a", label: "A", type: "text", value: "15" },
        { id: "b", label: "B", type: "text", value: "240" },
      ],
      run(v) {
        const a = number(v.a, "A");
        const b = number(v.b, "B");
        const div = (x, y) => (y === 0 ? "undefined (divide by zero)" : nice(x / y));
        return {
          type: "table",
          label: "Results",
          columns: ["Question", "Answer"],
          rows: [
            [a + "% of " + b, nice((a / 100) * b)],
            [a + " is what % of " + b, b === 0 ? "undefined" : nice((a / b) * 100) + "%"],
            ["% change from " + a + " to " + b, a === 0 ? "undefined" : nice(((b - a) / Math.abs(a)) * 100) + "%"],
            [b + " increased by " + a + "%", nice(b * (1 + a / 100))],
            [b + " decreased by " + a + "%", nice(b * (1 - a / 100))],
            ["Ratio A : B", div(a, b)],
            [b + " is " + a + "% of what", a === 0 ? "undefined" : nice(b / (a / 100))],
          ],
        };
      },
    },
    {
      slug: "binary-calculator",
      name: "Binary calculator",
      description: "Bitwise AND, OR, XOR, NOT and shifts, plus arithmetic, on binary, hex or decimal input with an optional bit width.",
      keywords: "bitwise and or xor not shift mask flags option set",
      inputs: [
        { id: "a", label: "A", type: "text", value: "0b1011_0110" },
        { id: "op", label: "Operation", type: "select", options: [["and", "A AND B"], ["or", "A OR B"], ["xor", "A XOR B"], ["nand", "A NAND B"], ["not", "NOT A"], ["shl", "A << B"], ["shr", "A >> B"], ["add", "A + B"], ["sub", "A − B"], ["mul", "A × B"], ["div", "A ÷ B"], ["mod", "A mod B"]], value: "and" },
        { id: "b", label: "B", type: "text", value: "0x0F", show: (v) => v.op !== "not" },
        { id: "bits", label: "Bit width", type: "select", options: [["0", "Unbounded"], ["8", "8"], ["16", "16"], ["32", "32"], ["64", "64"]], value: "8" },
      ],
      run(v) {
        const a = parseBig(v.a, "auto").value;
        const b = v.op === "not" ? 0n : parseBig(v.b, "auto").value;
        const bits = Number(v.bits);
        if (["div", "mod"].includes(v.op) && b === 0n) throw new Error("Division by zero.");
        if (["shl", "shr"].includes(v.op) && (b < 0n || b > 4096n)) throw new Error("Shift must be 0-4096.");
        if (v.op === "not" && !bits) throw new Error("NOT needs a bit width.");
        const ops = { and: () => a & b, or: () => a | b, xor: () => a ^ b, nand: () => ~(a & b), not: () => ~a, shl: () => a << b, shr: () => a >> b, add: () => a + b, sub: () => a - b, mul: () => a * b, div: () => a / b, mod: () => a % b };
        if (!ops[v.op]) throw new Error("Pick an operation.");
        let result = ops[v.op]();
        if (bits) result = twos(result, bits);
        const width = bits || Math.max(toBase(a < 0n ? -a : a, 2).length, toBase(b < 0n ? -b : b, 2).length, toBase(result < 0n ? -result : result, 2).length);
        const pad = (x) => (bits ? toBase(twos(x, bits), 2).padStart(bits, "0") : toBase(x, 2).padStart(width, "0"));
        return [
          { type: "text", label: "Bits", value: "A      " + group(pad(a), 4, " ") + (v.op === "not" ? "" : "\nB      " + group(pad(b), 4, " ")) + "\nResult " + group(pad(result), 4, " "), wrap: false },
          { type: "kv", label: "Result", rows: [["Decimal", toBase(result, 10)], ["Signed" + (bits ? " (Int" + bits + ")" : ""), bits ? String(signed(result, bits)) : toBase(result, 10)], ["Hex", "0x" + toBase(result < 0n ? -result : result, 16).toUpperCase()], ["Set bits", String([...toBase(result < 0n ? -result : result, 2)].filter((c) => c === "1").length)]] },
        ];
      },
    },
    {
      slug: "unit-converter",
      name: "Unit converter",
      description: "Length, mass, temperature, time, speed, area, volume, angle, frequency and iOS points to pixels.",
      keywords: "unit convert metric imperial temperature points pixels",
      inputs: [
        { id: "category", label: "Category", type: "select", options: [["length", "Length"], ["mass", "Mass"], ["temperature", "Temperature"], ["time", "Time"], ["speed", "Speed"], ["area", "Area"], ["volume", "Volume"], ["display", "Display (pt ↔ px)"], ["angle", "Angle"], ["frequency", "Frequency"]], value: "length" },
        { id: "value", label: "Value", type: "text", value: "1" },
        { id: "unit", label: "From", type: "select", options: (v) => (v.category === "temperature" ? ["°C", "°F", "K"] : Object.keys(UNITS[v.category || "length"].units)), value: "m" },
      ],
      run(v) {
        const value = number(v.value);
        if (v.category === "temperature") {
          const from = ["°C", "°F", "K"].includes(v.unit) ? v.unit : "°C";
          return { type: "table", label: "Temperature", columns: ["Unit", "Value"], rows: convertTemperature(value, from).map(([u, x]) => [u, nice(x)]) };
        }
        const cat = UNITS[v.category] || UNITS.length;
        const from = cat.units[v.unit] !== undefined ? v.unit : cat.base;
        const base = value * cat.units[from];
        return [
          { type: "table", label: value + " " + from, columns: ["Unit", "Value"], rows: Object.entries(cat.units).map(([u, f]) => [u, nice(base / f)]) },
          v.category === "display" ? { type: "note", text: "iOS layout uses points. iPhones render at 2x or 3x; check UIScreen.main.scale." } : null,
        ];
      },
    },
    {
      slug: "data-transfer-calculator",
      name: "Data transfer calculator",
      description: "Download time from size and speed, the speed needed for a deadline, or the data a stream uses at a bitrate.",
      keywords: "bandwidth download time speed bitrate streaming data usage",
      inputs: [
        { id: "mode", label: "Calculate", type: "select", options: [["time", "Transfer time"], ["speed", "Required speed"], ["stream", "Streaming data usage"]], value: "time" },
        { id: "size", label: "Size", type: "text", value: "4.2", show: (v) => v.mode !== "stream" },
        { id: "sizeUnit", label: "Size unit", type: "select", options: ["MB", "GB", "TB", "MiB", "GiB"], value: "GB", show: (v) => v.mode !== "stream" },
        { id: "speed", label: "Speed", type: "text", value: "50", show: (v) => v.mode !== "speed" },
        { id: "speedUnit", label: "Speed unit", type: "select", options: [["kbit", "kbps"], ["Mbit", "Mbps"], ["Gbit", "Gbps"], ["MB", "MB/s"]], value: "Mbit", show: (v) => v.mode !== "speed" },
        { id: "seconds", label: "Duration (minutes)", type: "text", value: "120", show: (v) => v.mode !== "time" },
        { id: "overhead", label: "Protocol overhead %", type: "number", value: 5, min: 0, max: 50, show: (v) => v.mode !== "stream" },
      ],
      run(v) {
        const efficiency = 1 - util.clampInt(v.overhead, 0, 50) / 100;
        if (v.mode === "time") {
          const bytes = number(v.size, "Size") * BYTE_FACTOR[v.sizeUnit];
          const rate = number(v.speed, "Speed") * BYTE_FACTOR[v.speedUnit] * efficiency;
          if (rate <= 0) throw new Error("Speed must be above zero.");
          const seconds = bytes / rate;
          return { type: "kv", label: "Transfer time", rows: [["Time", util.formatDuration(seconds * 1000)], ["Seconds", nice(seconds)], ["Effective rate", nice(rate / 1e6) + " MB/s"]] };
        }
        if (v.mode === "speed") {
          const bytes = number(v.size, "Size") * BYTE_FACTOR[v.sizeUnit];
          const seconds = number(v.seconds, "Duration") * 60;
          if (seconds <= 0) throw new Error("Duration must be above zero.");
          const rate = bytes / seconds / efficiency;
          return { type: "kv", label: "Required speed", rows: [["Mbps", nice((rate * 8) / 1e6)], ["MB/s", nice(rate / 1e6)], ["Gbps", nice((rate * 8) / 1e9)]] };
        }
        const rate = number(v.speed, "Bitrate") * BYTE_FACTOR[v.speedUnit];
        const seconds = number(v.seconds, "Duration") * 60;
        const bytes = rate * seconds;
        const ladder = [["4K HDR (HLS)", 16], ["1080p", 6], ["720p", 3], ["480p", 1.5], ["360p", 0.8], ["Audio only 128k", 0.128]].map(([label, mbps]) => [label, mbps + " Mbps", util.formatBytes((mbps * 1e6 / 8) * 3600) + " / hour"]);
        return [
          { type: "kv", label: "Data used", rows: [["Total", util.formatBytes(bytes)], ["Per hour", util.formatBytes(rate * 3600)], ["GB", nice(bytes / 1e9)]] },
          { type: "table", label: "Typical bitrate ladder", columns: ["Rendition", "Bitrate", "Data"], rows: ladder },
        ];
      },
    },
    {
      slug: "file-size-calculator",
      name: "File size calculator",
      description: "Measure files or text (UTF-8, UTF-16, Base64), or estimate raw image and media sizes.",
      keywords: "file size text bytes utf8 base64 image uncompressed estimate",
      inputs: [
        { id: "mode", label: "Measure", type: "select", options: [["files", "Files"], ["text", "Text"], ["image", "Uncompressed image"], ["media", "Media at a bitrate"]], value: "text" },
        { id: "files", label: "Files", type: "files", show: (v) => v.mode === "files" },
        { id: "text", label: "Text", type: "textarea", rows: 6, value: "Viewlift 🎬 streaming — नमस्ते", show: (v) => v.mode === "text" },
        { id: "width", label: "Width (px)", type: "number", value: 3840, show: (v) => v.mode === "image" },
        { id: "height", label: "Height (px)", type: "number", value: 2160, show: (v) => v.mode === "image" },
        { id: "channels", label: "Channels", type: "select", options: [["1", "Gray"], ["3", "RGB"], ["4", "RGBA"]], value: "4", show: (v) => v.mode === "image" },
        { id: "depth", label: "Bits per channel", type: "select", options: ["8", "10", "16", "32"], value: "8", show: (v) => v.mode === "image" },
        { id: "kbps", label: "Bitrate (kbps)", type: "text", value: "6000", show: (v) => v.mode === "media" },
        { id: "minutes", label: "Duration (minutes)", type: "text", value: "90", show: (v) => v.mode === "media" },
      ],
      run(v) {
        if (v.mode === "files") {
          const files = v.files || [];
          if (!files.length) return { type: "note", text: "Pick one or more files." };
          const total = files.reduce((n, f) => n + f.bytes.length, 0);
          return { type: "table", label: files.length + " files, " + util.formatBytes(total), columns: ["File", "Bytes", "Size", "Base64 size"], rows: files.map((f) => [f.name, f.bytes.length.toLocaleString(), util.formatBytes(f.bytes.length), util.formatBytes(Math.ceil(f.bytes.length / 3) * 4)]) };
        }
        if (v.mode === "text") {
          const text = String(v.text || "");
          const utf8 = util.utf8(text).length;
          const utf16 = text.length * 2;
          return { type: "kv", label: "Text", rows: [["Characters (grapheme-ish)", [...text].length.toLocaleString()], ["UTF-16 code units (NSString.length)", text.length.toLocaleString()], ["UTF-8 bytes", utf8.toLocaleString()], ["UTF-16 bytes", utf16.toLocaleString()], ["Base64 of UTF-8", (Math.ceil(utf8 / 3) * 4).toLocaleString()], ["Percent-encoded", encodeURIComponent(text).length.toLocaleString()]] };
        }
        if (v.mode === "image") {
          const pixels = util.clampInt(v.width, 1, 100000) * util.clampInt(v.height, 1, 100000);
          const bytes = (pixels * Number(v.channels) * Number(v.depth)) / 8;
          return { type: "kv", label: "Uncompressed", rows: [["Pixels", pixels.toLocaleString() + " (" + nice(pixels / 1e6) + " MP)"], ["Memory", util.formatBytes(bytes)], ["Bytes", Math.round(bytes).toLocaleString()], ["Note", "This is roughly the decoded size a UIImage takes in memory."]] };
        }
        const bytes = (number(v.kbps, "Bitrate") * 1000 / 8) * number(v.minutes, "Duration") * 60;
        return { type: "kv", label: "Media", rows: [["Size", util.formatBytes(bytes)], ["Bytes", Math.round(bytes).toLocaleString()]] };
      },
    },
    {
      slug: "semantic-version-comparator",
      name: "Semantic version comparator",
      description: "Compare and sort semantic versions, and check them against npm, CocoaPods (~>) or SPM-style ranges.",
      keywords: "semver compare version range cocoapods spm npm sort",
      inputs: [
        { id: "versions", label: "Versions (one per line)", type: "textarea", rows: 7, value: "5.12.0\n5.9.1\n5.12.0-beta.2\n6.0.0\n5.12.1+build.812\n4.19.3" },
        { id: "range", label: "Range to check", type: "text", value: "~> 5.9", hint: "Examples: ^5.9.0, >=5.0 <6, 5.x, ~> 5.12.0, 1.2 - 2.0" },
      ],
      run(v) {
        const lines = util.nonEmptyLines(v.versions).map((l) => l.trim());
        const parsed = lines.map((text) => {
          try {
            return { text, v: util.parseSemver(text) };
          } catch (error) {
            return { text, error: error.message };
          }
        });
        const good = parsed.filter((p) => p.v).sort((a, b) => util.compareSemver(a.v, b.v));
        const range = String(v.range || "").trim();
        const rows = good.map((p, i) => {
          let ok = "";
          if (range) {
            try {
              ok = satisfies(p.text, range) ? "yes" : "no";
            } catch (error) {
              ok = error.message;
            }
          }
          return [i + 1, p.text, p.v.prerelease.length ? "pre-release" : "release", ok];
        });
        const out = [{ type: "table", label: "Sorted, lowest first", columns: ["#", "Version", "Kind", range ? "Matches " + range : "Range"], rows, rowClass: (r) => (r[3] === "yes" ? "ok" : "") }];
        if (good.length >= 2) {
          const [a, b] = [good[0], good[good.length - 1]];
          const diff = a.v.major !== b.v.major ? "major" : a.v.minor !== b.v.minor ? "minor" : a.v.patch !== b.v.patch ? "patch" : "pre-release";
          out.push({ type: "note", text: "Highest is " + b.text + ". Lowest is " + a.text + ". They differ at the " + diff + " level. Build metadata (+…) is ignored when comparing." });
        }
        for (const p of parsed.filter((x) => x.error)) out.push({ type: "note", level: "warn", text: p.error });
        return out;
      },
    },
    {
      slug: "checksum-calculator",
      name: "Checksum calculator",
      description: "CRC-32, Adler-32, MD5, SHA-1, SHA-2 and SHA-3 checksums for files or text, with an expected-value check.",
      keywords: "checksum crc32 md5 sha256 verify file integrity",
      inputs: [
        { id: "file", label: "File", type: "file" },
        { id: "text", label: "Or text", type: "textarea", rows: 4, value: "viewlift" },
        { id: "server", label: "Include MD5 and SHA-3 (local server)", type: "checkbox", value: true },
        { id: "expected", label: "Expected checksum (optional)", type: "text", value: "", wide: true },
      ],
      async run(v, ctx) {
        const bytes = v.file ? v.file.bytes : util.utf8(v.text || "");
        const rows = await checksums(bytes, ctx, v.server);
        const out = [{ type: "kv", label: (v.file ? v.file.name : "Text") + " (" + util.formatBytes(bytes.length) + ")", rows }];
        const expected = String(v.expected || "").trim().toLowerCase().replace(/^sha256:|\s/g, "");
        if (expected) {
          const hit = rows.find(([, digest]) => digest === expected);
          out.unshift(hit ? { type: "note", level: "ok", text: "Matches " + hit[0] + "." } : { type: "note", level: "error", text: "No checksum matches the expected value." });
        }
        return out;
      },
    },
  ]);
})();
