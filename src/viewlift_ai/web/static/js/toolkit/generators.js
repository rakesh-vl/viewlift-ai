/* Generators */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  const FIRST = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Ananya", "Ethan", "Zara", "Lucas", "Priya", "Mateo", "Emma", "Kenji", "Amara", "Leo", "Isla", "Omar", "Chloe", "Ravi", "Nina"];
  const LAST = ["Sharma", "Garcia", "Smith", "Kim", "Okafor", "Nguyen", "Rossi", "Müller", "Silva", "Patel", "Brown", "Tanaka", "Haddad", "Novak", "Kowalski", "Singh", "Lopez", "Chen", "Dubois", "Ivanova"];
  const WORDS = ["stream", "match", "highlight", "studio", "live", "replay", "season", "episode", "trailer", "premiere", "final", "classic", "behind", "scenes", "documentary", "series", "league", "cup", "night", "special"];
  const COUNTRIES = ["IN", "US", "GB", "CA", "AU", "DE", "FR", "BR", "JP", "SG", "AE", "ZA"];
  const CITIES = ["Mumbai", "New York", "London", "Toronto", "Sydney", "Berlin", "Paris", "São Paulo", "Tokyo", "Singapore", "Dubai", "Cape Town"];
  const DOMAINS = ["example.com", "example.org", "example.net", "test.dev"];

  const pick = util.pick;
  const int = (min, max) => min + util.randomInt(max - min + 1);
  const chance = (p) => util.randomInt(1000000) < p * 1000000;
  const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const pad = (n, w) => String(n).padStart(w || 2, "0");

  function fakeName() {
    return pick(FIRST) + " " + pick(LAST);
  }

  function fakeEmail(name) {
    const base = (name || fakeName()).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z]+/g, ".");
    return base.replace(/^\.|\.$/g, "") + int(1, 99) + "@" + pick(DOMAINS);
  }

  function fakeDate(daysBack) {
    return new Date(Date.now() - util.randomInt(daysBack * 86400) * 1000);
  }

  function fakeByKey(key, type) {
    const k = String(key || "").toLowerCase();
    if (type === "integer" || type === "number") {
      if (/price|amount|total|cost/.test(k)) return type === "integer" ? int(1, 999) : Number((int(99, 99999) / 100).toFixed(2));
      if (/rating|score/.test(k)) return type === "integer" ? int(1, 5) : Number((int(10, 50) / 10).toFixed(1));
      if (/duration|seconds|runtime/.test(k)) return int(60, 7200);
      if (/year/.test(k)) return int(1990, 2026);
      if (/age/.test(k)) return int(18, 80);
      if (/lat/.test(k)) return Number((int(-9000, 9000) / 100).toFixed(4));
      if (/lon|lng/.test(k)) return Number((int(-18000, 18000) / 100).toFixed(4));
      if (/count|total|views|plays/.test(k)) return int(0, 100000);
      if (/^id$|_id$|id$/.test(k)) return int(1, 99999);
      return type === "integer" ? int(0, 1000) : Number((Math.random() * 1000).toFixed(2));
    }
    if (type === "boolean") return chance(0.5);
    if (/uuid|guid/.test(k) || /^id$/.test(k)) return util.uuidV4();
    if (/email/.test(k)) return fakeEmail();
    if (/first.?name/.test(k)) return pick(FIRST);
    if (/last.?name|surname/.test(k)) return pick(LAST);
    if (/name|author|user/.test(k)) return fakeName();
    if (/title|headline/.test(k)) return titleCase(pick(WORDS)) + " " + titleCase(pick(WORDS)) + " " + int(1, 30);
    if (/desc|summary|bio|body|text|comment/.test(k)) return Array.from({ length: int(6, 14) }, () => pick(WORDS)).join(" ").replace(/^./, (c) => c.toUpperCase()) + ".";
    if (/image|thumb|poster|avatar|photo/.test(k)) return "https://img.example.com/" + util.randomFrom("abcdef0123456789", 12) + ".jpg";
    if (/hls|m3u8|stream/.test(k)) return "https://cdn.example.com/hls/" + util.randomFrom("abcdef0123456789", 10) + "/master.m3u8";
    if (/url|link|href|website/.test(k)) return "https://www." + pick(DOMAINS) + "/" + pick(WORDS);
    if (/phone|mobile/.test(k)) return "+1-555-" + pad(int(0, 999), 3) + "-" + pad(int(0, 9999), 4);
    if (/country/.test(k)) return pick(COUNTRIES);
    if (/city/.test(k)) return pick(CITIES);
    if (/status|state/.test(k)) return pick(["active", "pending", "archived"]);
    if (/currency/.test(k)) return pick(["USD", "INR", "EUR", "GBP"]);
    if (/lang|locale/.test(k)) return pick(["en", "hi", "es", "fr", "de", "ja"]);
    if (/color|colour/.test(k)) return "#" + util.toHex(util.randomBytes(3));
    if (/token|key|secret/.test(k)) return util.randomFrom("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", 32);
    if (/ip/.test(k)) return "192.0.2." + int(1, 254);
    if (/date|time|_at$|at$/.test(k)) return fakeDate(365).toISOString();
    if (/tag|genre|category/.test(k)) return pick(WORDS);
    return pick(WORDS) + "-" + int(1, 999);
  }

  function fakeFromSchema(schema, root, key, depth) {
    const node = schema && schema.$ref ? util.resolveRef(root, schema.$ref) || {} : schema || {};
    if (depth > 8) return null;
    if (Array.isArray(node.enum) && node.enum.length) return pick(node.enum);
    if (node.const !== undefined) return node.const;
    if (node.oneOf || node.anyOf) return fakeFromSchema(pick(node.oneOf || node.anyOf), root, key, depth + 1);
    if (node.allOf) return Object.assign({}, ...node.allOf.map((s) => fakeFromSchema(s, root, key, depth + 1)));
    let type = Array.isArray(node.type) ? node.type.filter((t) => t !== "null")[0] : node.type;
    if (!type) type = node.properties ? "object" : node.items ? "array" : "string";
    if (type === "object") {
      const out = {};
      for (const [name, child] of Object.entries(node.properties || {})) out[name] = fakeFromSchema(child, root, name, depth + 1);
      return out;
    }
    if (type === "array") {
      const min = node.minItems || 1;
      const max = Math.max(min, Math.min(node.maxItems || 3, 5));
      return Array.from({ length: int(min, max) }, () => fakeFromSchema(node.items || {}, root, key, depth + 1));
    }
    if (type === "string") {
      const formats = { "date-time": () => fakeDate(365).toISOString(), date: () => fakeDate(365).toISOString().slice(0, 10), email: () => fakeEmail(), uuid: () => util.uuidV4(), uri: () => fakeByKey(/image|thumb|poster|avatar|photo|hls|m3u8|stream/i.test(key) ? key : "url"), ipv4: () => "192.0.2." + int(1, 254) };
      if (formats[node.format]) return formats[node.format]();
      return fakeByKey(key, "string");
    }
    if (type === "integer" || type === "number") {
      if (node.minimum !== undefined || node.maximum !== undefined) {
        const lo = node.minimum ?? 0;
        const hi = node.maximum ?? lo + 1000;
        return type === "integer" ? int(Math.ceil(lo), Math.floor(hi)) : Number((lo + Math.random() * (hi - lo)).toFixed(2));
      }
      return fakeByKey(key, type);
    }
    if (type === "boolean") return chance(0.5);
    return null;
  }

  /* Snowflake IDs */

  const EPOCHS = { twitter: 1288834974657n, discord: 1420070400000n, unix: 0n };

  function snowflake(ms, epoch, dc, worker, seq) {
    return ((BigInt(ms) - epoch) << 22n) | (BigInt(dc & 31) << 17n) | (BigInt(worker & 31) << 12n) | BigInt(seq & 4095);
  }

  /* Cards */

  const CARD_BRANDS = {
    visa: { name: "Visa", prefixes: ["4"], length: 16, cvv: 3 },
    mastercard: { name: "Mastercard", prefixes: ["51", "52", "53", "54", "55", "2221", "2720"], length: 16, cvv: 3 },
    amex: { name: "American Express", prefixes: ["34", "37"], length: 15, cvv: 4 },
    discover: { name: "Discover", prefixes: ["6011", "65"], length: 16, cvv: 3 },
    jcb: { name: "JCB", prefixes: ["3528", "3589"], length: 16, cvv: 3 },
    diners: { name: "Diners Club", prefixes: ["36"], length: 14, cvv: 3 },
    unionpay: { name: "UnionPay", prefixes: ["62"], length: 16, cvv: 3 },
  };

  function luhnDigit(partial) {
    let sum = 0;
    for (let i = 0; i < partial.length; i += 1) {
      let d = Number(partial[partial.length - 1 - i]);
      if (i % 2 === 0) {
        d *= 2;
        if (d > 9) d -= 9;
      }
      sum += d;
    }
    return String((10 - (sum % 10)) % 10);
  }

  function cardNumber(brand) {
    let number = pick(brand.prefixes);
    while (number.length < brand.length - 1) number += String(util.randomInt(10));
    return number + luhnDigit(number);
  }

  function groupCard(number) {
    if (number.length === 15) return number.slice(0, 4) + " " + number.slice(4, 10) + " " + number.slice(10);
    if (number.length === 14) return number.slice(0, 4) + " " + number.slice(4, 10) + " " + number.slice(10);
    return number.replace(/(.{4})/g, "$1 ").trim();
  }

  /* Palettes */

  function paletteHues(mode, base, count) {
    const rgb = util.parseColor(base);
    const hsl = util.rgbToHsl(rgb.r, rgb.g, rgb.b);
    const s = Math.max(hsl.s, 35);
    const l = Math.min(Math.max(hsl.l, 30), 70);
    const out = [];
    for (let i = 0; i < count; i += 1) {
      const t = count === 1 ? 0 : i / (count - 1);
      if (mode === "analogous") out.push([hsl.h - 40 + 80 * t, s, l]);
      else if (mode === "complementary") out.push([hsl.h + (i % 2) * 180, s, 30 + 45 * t]);
      else if (mode === "triadic") out.push([hsl.h + (i % 3) * 120, s, 35 + 30 * t]);
      else if (mode === "monochrome") out.push([hsl.h, s, 15 + 75 * t]);
      else if (mode === "pastel") out.push([util.randomInt(360), 55 + util.randomInt(30), 78 + util.randomInt(10)]);
      else out.push([util.randomInt(360), 45 + util.randomInt(50), 35 + util.randomInt(35)]);
    }
    return out.map(([h, sat, light]) => util.hslToRgb(h, sat, light));
  }

  /* Logs */

  const PATHS = ["/api/v1/content/vl-" + 123, "/api/v1/user/history", "/hls/master.m3u8", "/hls/1080p/segment_042.ts", "/api/v1/search?q=match", "/login", "/healthz", "/api/v1/entitlements", "/images/poster.jpg"];
  const UAS = ["ViewliftOTT/812 CFNetwork/1568.200.51 Darwin/24.1.0", "AppleCoreMedia/1.0.0.22B83 (iPhone; U; CPU OS 18_1 like Mac OS X; en_us)", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15", "okhttp/4.12.0", "curl/8.7.1"];
  const MESSAGES = { info: ["Request completed", "Cache hit", "User signed in", "Playback started", "Token refreshed"], warn: ["Slow upstream response", "Retrying request", "Bitrate downgraded", "Deprecated API used"], error: ["Upstream timeout", "Database connection reset", "Entitlement check failed", "DRM license request failed"], debug: ["Parsed manifest", "Resolved CDN edge", "Opened socket"] };

  function nginxTime(d) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return pad(d.getUTCDate()) + "/" + months[d.getUTCMonth()] + "/" + d.getUTCFullYear() + ":" + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " +0000";
  }

  function logLine(format, d, errorRate) {
    const failed = chance(errorRate);
    const status = failed ? pick([500, 502, 503, 504, 401, 403, 404, 429]) : pick([200, 200, 200, 200, 201, 204, 206, 304]);
    const level = failed ? (status >= 500 ? "error" : "warn") : chance(0.08) ? "debug" : "info";
    const method = pick(["GET", "GET", "GET", "POST", "PUT", "DELETE"]);
    const path = pick(PATHS);
    const ip = "198.51.100." + int(1, 254);
    const ms = failed ? int(800, 30000) : int(3, 450);
    const size = status === 204 || status === 304 ? 0 : int(120, 900000);
    const msg = pick(MESSAGES[level]);
    const rid = util.randomFrom("abcdef0123456789", 16);
    if (format === "nginx") return ip + ' - - [' + nginxTime(d) + '] "' + method + " " + path + ' HTTP/1.1" ' + status + " " + size + ' "-" "' + pick(UAS) + '" ' + (ms / 1000).toFixed(3);
    if (format === "apache") return ip + " - - [" + nginxTime(d) + '] "' + method + " " + path + ' HTTP/1.1" ' + status + " " + (size || "-");
    if (format === "json") return JSON.stringify({ ts: d.toISOString(), level, msg, service: pick(["api", "auth", "playback", "search"]), method, path, status, latency_ms: ms, request_id: rid });
    if (format === "logfmt") return "ts=" + d.toISOString() + " level=" + level + ' msg="' + msg + '" method=' + method + " path=" + path + " status=" + status + " duration=" + ms + "ms request_id=" + rid;
    if (format === "syslog") return "<" + (16 * 8 + ({ error: 3, warn: 4, info: 6, debug: 7 })[level]) + ">1 " + d.toISOString() + " web-" + int(1, 4) + " api " + int(1000, 9999) + " - - " + msg + " status=" + status + " path=" + path;
    const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().replace("T", " ").replace("Z", "");
    const types = { error: "Error", warn: "Default", info: "Default", debug: "Debug" };
    const subsystem = pick(["com.viewlift.ott:network", "com.viewlift.ott:player", "com.apple.CFNetwork:Summary", "com.apple.avfoundation:playback"]);
    return local + "000+0000  0x" + int(4096, 999999).toString(16) + "   " + types[level].padEnd(10) + " 0x0   " + int(300, 9999) + "    0    ViewliftOTT: (" + (subsystem.startsWith("com.apple") ? subsystem.split(".")[2].split(":")[0] : "ViewliftOTT") + ") [" + subsystem + "] " + msg + " status=" + status;
  }

  function keyFiles(entries) {
    return entries.filter(([, value]) => value).map(([label, value, filename]) => ({ type: "text", label, value, download: filename }));
  }

  register("generators", [
    {
      slug: "ulid-generator",
      name: "ULID generator",
      description: "Generate sortable ULIDs, now or for a chosen time, and decode a ULID's timestamp.",
      keywords: "ulid sortable id generate decode",
      inputs: [
        { id: "count", label: "How many", type: "number", value: 10, min: 1, max: 1000 },
        { id: "when", label: "Timestamp", type: "select", options: [["now", "Now"], ["custom", "Custom date"]], value: "now" },
        { id: "date", label: "Date", type: "datetime", value: "2026-11-06T10:00:00", show: (v) => v.when === "custom" },
        { id: "lower", label: "Lowercase", type: "checkbox", value: false },
        { id: "decode", label: "Decode a ULID", type: "text", value: "", placeholder: "01JC…", wide: true },
      ],
      run(v) {
        const time = v.when === "custom" ? new Date(v.date).getTime() : Date.now();
        if (!Number.isFinite(time) || time < 0) throw new Error("Pick a valid date.");
        let ids = Array.from({ length: util.clampInt(v.count, 1, 1000) }, () => util.ulid(time)).sort();
        if (v.lower) ids = ids.map((id) => id.toLowerCase());
        const out = [{ type: "text", label: ids.length + " ULIDs", value: ids.join("\n") }];
        if (String(v.decode || "").trim()) {
          const ms = util.ulidTime(v.decode);
          out.unshift({ type: "kv", label: "Decoded", rows: [["Timestamp (ms)", String(ms)], ["UTC", new Date(ms).toISOString()], ["Relative", util.relativeTime(ms)]] });
        }
        return out;
      },
    },
    {
      slug: "cuid2-generator",
      name: "CUID2 generator",
      description: "Collision-resistant, URL-safe IDs in the CUID2 style (SHA3-based), generated on the local server.",
      keywords: "cuid cuid2 id generate collision",
      inputs: [
        { id: "count", label: "How many", type: "number", value: 10, min: 1, max: 1000 },
        { id: "length", label: "Length", type: "range", value: 24, min: 4, max: 32 },
      ],
      async run(v, ctx) {
        const { ids } = await ctx.server("cuid2", { count: util.clampInt(v.count, 1, 1000), length: util.clampInt(v.length, 4, 32) });
        return { type: "text", label: ids.length + " IDs", value: ids.join("\n") };
      },
    },
    {
      slug: "snowflake-id-generator",
      name: "Snowflake ID generator",
      description: "Generate and decode 64-bit Snowflake IDs (Twitter, Discord or custom epoch) with worker and sequence bits.",
      keywords: "snowflake twitter discord id 64-bit decode",
      inputs: [
        { id: "epoch", label: "Epoch", type: "select", options: [["twitter", "Twitter (2010-11-04)"], ["discord", "Discord (2015-01-01)"], ["unix", "Unix (1970)"], ["custom", "Custom (ms)"]], value: "twitter" },
        { id: "customEpoch", label: "Custom epoch (ms)", type: "text", value: "1704067200000", show: (v) => v.epoch === "custom" },
        { id: "datacenter", label: "Datacenter (0-31)", type: "number", value: 1, min: 0, max: 31 },
        { id: "worker", label: "Worker (0-31)", type: "number", value: 1, min: 0, max: 31 },
        { id: "count", label: "How many", type: "number", value: 5, min: 1, max: 1000 },
        { id: "decode", label: "Decode an ID", type: "text", value: "1854376402837417984", wide: true },
      ],
      run(v) {
        const epoch = v.epoch === "custom" ? BigInt(String(v.customEpoch || "0").replace(/\D/g, "") || "0") : EPOCHS[v.epoch];
        const now = Date.now();
        const count = util.clampInt(v.count, 1, 1000);
        const ids = Array.from({ length: count }, (_, i) => snowflake(now + Math.floor(i / 4096), epoch, util.clampInt(v.datacenter, 0, 31), util.clampInt(v.worker, 0, 31), i % 4096).toString());
        const out = [{ type: "text", label: count + " IDs", value: ids.join("\n") }];
        const raw = String(v.decode || "").trim();
        if (raw) {
          if (!/^\d+$/.test(raw)) throw new Error("A Snowflake ID is a decimal number.");
          const id = BigInt(raw);
          const ms = Number((id >> 22n) + epoch);
          out.unshift({ type: "kv", label: "Decoded", rows: [["Time (UTC)", new Date(ms).toISOString()], ["Unix ms", String(ms)], ["Datacenter", String((id >> 17n) & 31n)], ["Worker", String((id >> 12n) & 31n)], ["Sequence", String(id & 4095n)], ["Binary", id.toString(2).padStart(64, "0").replace(/^(.)(.{41})(.{5})(.{5})(.{12})$/, "$1 $2 $3 $4 $5")]] });
        }
        return out;
      },
    },
    {
      slug: "rsa-key-pair-generator",
      name: "RSA key pair generator",
      description: "Generate an RSA private and public key in PEM (PKCS#8 or PKCS#1) on this Mac.",
      keywords: "rsa key pair pem pkcs8 pkcs1 generate public private",
      live: false,
      action: "Generate",
      busy: "Generating RSA key…",
      inputs: [
        { id: "bits", label: "Key size", type: "select", options: [["2048", "2048 bits"], ["3072", "3072 bits"], ["4096", "4096 bits"]], value: "2048" },
        { id: "format", label: "Private key format", type: "select", options: [["pkcs8", "PKCS#8 (BEGIN PRIVATE KEY)"], ["pkcs1", "PKCS#1 (BEGIN RSA PRIVATE KEY)"]], value: "pkcs8" },
      ],
      async run(v, ctx) {
        const r = await ctx.server("rsa-keypair", { bits: Number(v.bits), private_format: v.format });
        return [
          { type: "note", level: "warn", text: "Generated locally and never stored. Keep the private key secret." },
          ...keyFiles([["Private key", r.private_pem, "private.pem"], ["Public key (SPKI)", r.public_pem, "public.pem"], ["Public key (PKCS#1)", r.public_pkcs1_pem, "public-pkcs1.pem"]]),
        ];
      },
    },
    {
      slug: "ed25519-key-generator",
      name: "Ed25519 key generator",
      description: "Generate an Ed25519 key pair as PEM, raw Base64 and hex. Works with CryptoKit Curve25519.Signing.",
      keywords: "ed25519 curve25519 eddsa key generate cryptokit",
      live: false,
      action: "Generate",
      inputs: [],
      async run(v, ctx) {
        const r = await ctx.server("ed25519-keypair", {});
        return [
          { type: "note", level: "warn", text: "Generated locally and never stored. Keep the private key secret." },
          ...keyFiles([["Private key (PKCS#8)", r.private_pem, "ed25519-private.pem"], ["Public key (SPKI)", r.public_pem, "ed25519-public.pem"]]),
          { type: "kv", label: "Raw keys", rows: [["Private (Base64, 32 bytes)", r.private_raw_base64], ["Public (Base64, 32 bytes)", r.public_raw_base64], ["Public (hex)", r.public_hex]] },
          { type: "text", label: "CryptoKit", value: 'import CryptoKit\nimport Foundation\n\nlet privateKey = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(base64Encoded: "<private raw base64>")!)\nlet publicKey = try Curve25519.Signing.PublicKey(rawRepresentation: Data(base64Encoded: "' + r.public_raw_base64 + '")!)' },
        ];
      },
    },
    {
      slug: "ssh-key-generator",
      name: "SSH key generator",
      description: "Generate an OpenSSH key pair (Ed25519, RSA or ECDSA), optionally passphrase-protected, with its SHA256 fingerprint.",
      keywords: "ssh key openssh ed25519 rsa ecdsa authorized_keys github",
      live: false,
      action: "Generate",
      inputs: [
        { id: "type", label: "Type", type: "select", options: [["ed25519", "Ed25519 (recommended)"], ["rsa", "RSA"], ["ecdsa", "ECDSA P-256"]], value: "ed25519" },
        { id: "bits", label: "RSA size", type: "select", options: ["2048", "3072", "4096"], value: "4096", show: (v) => v.type === "rsa" },
        { id: "comment", label: "Comment", type: "text", value: "you@your-mac" },
        { id: "passphrase", label: "Passphrase (optional)", type: "password", value: "", secret: true },
      ],
      async run(v, ctx) {
        const r = await ctx.server("ssh-keypair", { type: v.type, bits: Number(v.bits), comment: v.comment, passphrase: v.passphrase });
        const base = "id_" + (v.type === "ecdsa" ? "ecdsa" : v.type);
        return [
          { type: "kv", label: "Key", rows: [["Fingerprint", r.fingerprint_sha256], ["Encrypted", r.encrypted ? "yes" : "no (no passphrase)"]] },
          ...keyFiles([["Private key (" + base + ")", r.private_openssh, base], ["Public key (" + base + ".pub)", r.public_openssh, base + ".pub"]]),
          { type: "text", label: "Install", value: "mv ~/Downloads/" + base + " ~/Downloads/" + base + ".pub ~/.ssh/\nchmod 600 ~/.ssh/" + base + "\nssh-add --apple-use-keychain ~/.ssh/" + base },
        ];
      },
    },
    {
      slug: "csr-generator",
      name: "CSR generator",
      description: "Create a certificate signing request and private key with subject fields and SANs.",
      keywords: "csr certificate signing request x509 san tls ssl",
      live: false,
      action: "Generate CSR",
      inputs: [
        { id: "cn", label: "Common name", type: "text", value: "api.example.com" },
        { id: "o", label: "Organization", type: "text", value: "Example Inc." },
        { id: "ou", label: "Unit", type: "text", value: "" },
        { id: "l", label: "City", type: "text", value: "" },
        { id: "st", label: "State", type: "text", value: "" },
        { id: "c", label: "Country (2 letters)", type: "text", value: "US" },
        { id: "email", label: "Email", type: "text", value: "" },
        { id: "sans", label: "Subject alternative names (one per line)", type: "textarea", rows: 4, value: "api.example.com\n*.api.example.com" },
        { id: "key", label: "Key", type: "select", options: [["rsa", "RSA 2048"], ["rsa4096", "RSA 4096"], ["ec", "ECDSA P-256"]], value: "rsa" },
      ],
      async run(v, ctx) {
        const r = await ctx.server("csr", {
          common_name: v.cn, organization: v.o, org_unit: v.ou, locality: v.l, state: v.st, country: v.c, email: v.email,
          sans: util.nonEmptyLines(v.sans).map((s) => s.trim()), key_type: v.key === "ec" ? "ec" : "rsa", bits: v.key === "rsa4096" ? 4096 : 2048,
        });
        return [
          { type: "kv", label: "Subject", rows: [["Subject", r.subject]] },
          ...keyFiles([["CSR", r.csr_pem, "request.csr"], ["Private key", r.private_key_pem, "private.key"]]),
          { type: "note", level: "warn", text: "Keep private.key secret. Send only the CSR to the certificate authority." },
        ];
      },
    },
    {
      slug: "random-color-palette-generator",
      name: "Random color palette generator",
      description: "Random, pastel, analogous, complementary, triadic or monochrome palettes, exported as CSS, SwiftUI and JSON.",
      keywords: "palette color scheme random swiftui css theme",
      inputs: [
        { id: "mode", label: "Style", type: "select", options: [["random", "Random"], ["pastel", "Pastel"], ["analogous", "Analogous"], ["complementary", "Complementary"], ["triadic", "Triadic"], ["monochrome", "Monochrome"]], value: "analogous" },
        { id: "base", label: "Base color", type: "color", value: "#e5484d", show: (v) => !["random", "pastel"].includes(v.mode) },
        { id: "count", label: "Colors", type: "range", value: 5, min: 2, max: 10 },
        { id: "prefix", label: "Name prefix", type: "text", value: "brand" },
      ],
      run(v) {
        const colors = paletteHues(v.mode, v.base || "#e5484d", util.clampInt(v.count, 2, 10)).map((rgb, i) => ({ ...util.colorFormats({ ...rgb, a: 1 }), name: (v.prefix || "color") + (i + 1) * 100 }));
        const swift = "import SwiftUI\n\nextension Color {\n" + colors.map((c) => "    static let " + c.name.replace(/[^A-Za-z0-9]/g, "") + " = " + c.swiftui).join("\n") + "\n}";
        return [
          { type: "swatches", label: "Palette (click to copy)", colors: colors.map((c) => ({ hex: c.hex, label: c.hex, sub: c.name })) },
          { type: "text", label: "CSS variables", value: ":root {\n" + colors.map((c) => "  --" + c.name + ": " + c.hex + ";").join("\n") + "\n}" },
          { type: "text", label: "SwiftUI", value: swift },
          { type: "text", label: "JSON", value: JSON.stringify(Object.fromEntries(colors.map((c) => [c.name, c.hex])), null, 2) },
        ];
      },
    },
    {
      slug: "mock-api-response-generator",
      name: "Mock API response generator",
      description: "Generate realistic fake JSON from a JSON Schema or an example, with optional pagination wrapping.",
      keywords: "mock api fake json response schema stub fixture",
      inputs: [
        { id: "source", label: "Source", type: "select", options: [["example", "Example JSON"], ["schema", "JSON Schema"]], value: "example" },
        { id: "text", label: "Example or schema", type: "textarea", rows: 10, value: '{"id":"6f1c2b9e-2b4a-4c1e-9a8f-0c6f1d2a3b4c","title":"Match Highlights","duration":5400,"rating":4.5,"premium":true,"posterImage":"https://img.example.com/a.jpg","publishedAt":"2026-11-06T10:00:00Z","tags":["sports"]}' },
        { id: "count", label: "Items", type: "number", value: 3, min: 1, max: 500 },
        { id: "wrap", label: "Wrap", type: "select", options: [["array", "Plain array"], ["page", "{ data, meta } page"], ["single", "Single object"]], value: "page" },
      ],
      run(v) {
        const parsed = util.parseJSON(v.text);
        const source = Array.isArray(parsed) && v.source === "example" ? parsed[0] : parsed;
        const schema = v.source === "schema" ? source : util.inferSchema(source, { required: false, strict: false, formats: true });
        const count = v.wrap === "single" ? 1 : util.clampInt(v.count, 1, 500);
        const items = Array.from({ length: count }, () => fakeFromSchema(schema, source, "", 0));
        const body = v.wrap === "single" ? items[0] : v.wrap === "page" ? { data: items, meta: { page: 1, perPage: count, total: count * int(3, 20), nextCursor: util.randomFrom("abcdefghijklmnopqrstuvwxyz0123456789", 16) } } : items;
        return { type: "text", label: "Mock response", value: JSON.stringify(body, null, 2), download: "mock.json", mime: "application/json" };
      },
    },
    {
      slug: "fake-log-generator",
      name: "Fake log generator",
      description: "Sample nginx, Apache, JSON, logfmt, syslog or Apple unified-log lines for testing parsers and dashboards.",
      keywords: "log fake sample nginx apache json syslog logfmt os_log",
      inputs: [
        { id: "format", label: "Format", type: "select", options: [["nginx", "nginx combined"], ["apache", "Apache common"], ["json", "JSON lines"], ["logfmt", "logfmt"], ["syslog", "Syslog (RFC 5424)"], ["oslog", "Apple unified log (log show)"]], value: "nginx" },
        { id: "count", label: "Lines", type: "number", value: 25, min: 1, max: 5000 },
        { id: "minutes", label: "Spread over (minutes)", type: "number", value: 15, min: 1, max: 10080 },
        { id: "errors", label: "Error rate %", type: "range", value: 10, min: 0, max: 100 },
      ],
      run(v) {
        const count = util.clampInt(v.count, 1, 5000);
        const span = util.clampInt(v.minutes, 1, 10080) * 60000;
        const now = Date.now();
        const times = Array.from({ length: count }, () => now - util.randomInt(span)).sort((a, b) => a - b);
        const lines = times.map((t) => logLine(v.format, new Date(t), util.clampInt(v.errors, 0, 100) / 100));
        return { type: "text", label: count + " lines", value: lines.join("\n"), download: "sample." + (v.format === "json" ? "jsonl" : "log"), wrap: false };
      },
    },
    {
      slug: "random-uuid-dataset-generator",
      name: "Random UUID dataset generator",
      description: "Build a dataset with UUID keys and optional fake columns, exported as CSV, JSON, NDJSON or SQL.",
      keywords: "uuid dataset fixture seed csv json sql bulk",
      inputs: [
        { id: "count", label: "Rows", type: "number", value: 20, min: 1, max: 10000 },
        { id: "version", label: "UUID version", type: "select", options: [["v4", "v4"], ["v7", "v7 (time-ordered)"]], value: "v4" },
        { id: "columns", label: "Extra columns", type: "select", options: [["none", "None"], ["user", "name, email, country, created_at"], ["device", "device_id, platform, app_version, last_seen"], ["order", "user_id, amount, currency, status"]], value: "user" },
        { id: "format", label: "Format", type: "select", options: [["csv", "CSV"], ["json", "JSON"], ["ndjson", "NDJSON"], ["sql", "SQL INSERT"]], value: "csv" },
      ],
      run(v) {
        const count = util.clampInt(v.count, 1, 10000);
        const rows = Array.from({ length: count }, () => {
          const row = { id: v.version === "v7" ? util.uuidV7() : util.uuidV4() };
          if (v.columns === "user") {
            const name = fakeName();
            Object.assign(row, { name, email: fakeEmail(name), country: pick(COUNTRIES), created_at: fakeDate(730).toISOString() });
          } else if (v.columns === "device") {
            Object.assign(row, { device_id: util.uuidV4().toUpperCase(), platform: pick(["iOS", "iPadOS", "tvOS", "Android", "Web"]), app_version: "5." + int(0, 12) + "." + int(0, 9), last_seen: fakeDate(30).toISOString() });
          } else if (v.columns === "order") {
            Object.assign(row, { user_id: util.uuidV4(), amount: Number((int(99, 49999) / 100).toFixed(2)), currency: pick(["USD", "INR", "EUR"]), status: pick(["paid", "refunded", "pending"]) });
          }
          return row;
        });
        const keys = Object.keys(rows[0]);
        let value;
        let ext = v.format;
        if (v.format === "csv") value = util.toCSV([keys, ...rows.map((r) => keys.map((k) => r[k]))]);
        else if (v.format === "json") value = JSON.stringify(rows, null, 2);
        else if (v.format === "ndjson") value = rows.map((r) => JSON.stringify(r)).join("\n");
        else {
          ext = "sql";
          const lit = (x) => (typeof x === "number" ? String(x) : "'" + String(x).replace(/'/g, "''") + "'");
          value = "INSERT INTO dataset (" + keys.join(", ") + ") VALUES\n  " + rows.map((r) => "(" + keys.map((k) => lit(r[k])).join(", ") + ")").join(",\n  ") + ";";
        }
        return { type: "text", label: count + " rows", value, download: "dataset." + ext, wrap: false };
      },
    },
    {
      slug: "test-credit-card-generator",
      name: "Test credit card generator",
      description: "Luhn-valid card numbers for testing payment forms, plus well-known gateway test cards. They are not real cards.",
      keywords: "credit card test luhn visa mastercard amex stripe sandbox payment",
      inputs: [
        { id: "brand", label: "Brand", type: "select", options: Object.entries(CARD_BRANDS).map(([k, b]) => [k, b.name]), value: "visa" },
        { id: "count", label: "How many", type: "number", value: 5, min: 1, max: 100 },
        { id: "validate", label: "Check a number (Luhn)", type: "text", value: "", wide: true },
      ],
      run(v) {
        const brand = CARD_BRANDS[v.brand] || CARD_BRANDS.visa;
        const year = new Date().getFullYear();
        const rows = Array.from({ length: util.clampInt(v.count, 1, 100) }, () => [groupCard(cardNumber(brand)), pad(int(1, 12)) + "/" + String(year + int(1, 5)).slice(2), Array.from({ length: brand.cvv }, () => util.randomInt(10)).join("")]);
        const out = [
          { type: "note", level: "warn", text: "For testing only. These numbers pass the Luhn check but are not real accounts and cannot be charged." },
          { type: "table", label: brand.name, columns: ["Number", "Expiry", "CVV"], rows },
          { type: "table", label: "Stripe test-mode cards", columns: ["Brand", "Number", "Result"], rows: [["Visa", "4242 4242 4242 4242", "Succeeds"], ["Mastercard", "5555 5555 5555 4444", "Succeeds"], ["Amex", "3782 822463 10005", "Succeeds"], ["Visa", "4000 0000 0000 9995", "Declined (insufficient funds)"], ["Visa", "4000 0025 0000 3155", "Requires 3-D Secure"]] },
        ];
        const check = String(v.validate || "").replace(/[\s-]/g, "");
        if (check) {
          const valid = /^\d{12,19}$/.test(check) && luhnDigit(check.slice(0, -1)) === check.slice(-1);
          out.unshift({ type: "note", level: valid ? "ok" : "error", text: valid ? "Passes the Luhn check." : "Fails the Luhn check." });
        }
        return out;
      },
    },
  ]);
})();
