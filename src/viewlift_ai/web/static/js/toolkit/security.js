/* Security & encoding */
(() => {
  const { register, util } = window.VLKit;

  const SAMPLE_SECRET = "viewlift-debug-secret";
  const SAMPLE_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTQyIiwibmFtZSI6IlZpZXdsaWZ0IFRlc3RlciIsInJvbGUiOiJzdWJzY3JpYmVyIiwiaWF0IjoxNzYyNDEyMDAwLCJleHAiOjE3NjI0MTU2MDB9.QYCr4NY8ODIGwoKrAGuvxwEJU1GQBd1QHqChP37VKYc";

  const JWT_ALGS = {
    HS256: { kind: "hmac", hash: "SHA-256" },
    HS384: { kind: "hmac", hash: "SHA-384" },
    HS512: { kind: "hmac", hash: "SHA-512" },
    RS256: { kind: "rsa", name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    RS384: { kind: "rsa", name: "RSASSA-PKCS1-v1_5", hash: "SHA-384" },
    RS512: { kind: "rsa", name: "RSASSA-PKCS1-v1_5", hash: "SHA-512" },
    PS256: { kind: "rsa", name: "RSA-PSS", hash: "SHA-256", salt: 32 },
    PS384: { kind: "rsa", name: "RSA-PSS", hash: "SHA-384", salt: 48 },
    PS512: { kind: "rsa", name: "RSA-PSS", hash: "SHA-512", salt: 64 },
    ES256: { kind: "ec", curve: "P-256", hash: "SHA-256" },
    ES384: { kind: "ec", curve: "P-384", hash: "SHA-384" },
    ES512: { kind: "ec", curve: "P-521", hash: "SHA-512" },
    EdDSA: { kind: "ed" },
  };

  function derLength(length) {
    if (length < 0x80) return [length];
    const bytes = [];
    let n = length;
    while (n) {
      bytes.unshift(n & 0xff);
      n >>= 8;
    }
    return [0x80 | bytes.length, ...bytes];
  }

  function der(tag, content) {
    return new Uint8Array([tag, ...derLength(content.length), ...content]);
  }

  const RSA_ALGORITHM_ID = new Uint8Array([0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00]);

  function pkcs1PublicToSpki(pkcs1) {
    const bits = der(0x03, new Uint8Array([0x00, ...pkcs1]));
    return der(0x30, new Uint8Array([...RSA_ALGORITHM_ID, ...bits]));
  }

  function pkcs1PrivateToPkcs8(pkcs1) {
    const version = new Uint8Array([0x02, 0x01, 0x00]);
    return der(0x30, new Uint8Array([...version, ...RSA_ALGORITHM_ID, ...der(0x04, pkcs1)]));
  }

  function pemBody(text) {
    const match = /-----BEGIN ([A-Z0-9 ]+)-----([\s\S]+?)-----END \1-----/.exec(String(text));
    if (!match) return null;
    return { label: match[1], bytes: util.fromBase64(match[2]) };
  }

  function importParams(spec) {
    if (spec.kind === "hmac") return { name: "HMAC", hash: spec.hash };
    if (spec.kind === "rsa") return { name: spec.name, hash: spec.hash };
    if (spec.kind === "ec") return { name: "ECDSA", namedCurve: spec.curve };
    return { name: "Ed25519" };
  }

  function signParams(spec) {
    if (spec.kind === "hmac") return { name: "HMAC" };
    if (spec.kind === "rsa") return spec.name === "RSA-PSS" ? { name: "RSA-PSS", saltLength: spec.salt } : { name: spec.name };
    if (spec.kind === "ec") return { name: "ECDSA", hash: spec.hash };
    return { name: "Ed25519" };
  }

  function secretBytes(secret, encoding) {
    if (encoding === "base64") return util.fromBase64(secret);
    if (encoding === "hex") return util.fromHex(secret);
    return util.utf8(secret);
  }

  async function importKey(spec, keyText, usage, encoding) {
    const text = String(keyText || "").trim();
    if (!text) return null;
    const subtle = crypto.subtle;
    if (!subtle) throw new Error("WebCrypto is unavailable. Open the console on http://127.0.0.1.");
    if (text.startsWith("{")) {
      const jwk = util.parseJSON(text, "JWK");
      return subtle.importKey("jwk", jwk, importParams(spec), false, [usage]);
    }
    if (spec.kind === "hmac") {
      return subtle.importKey("raw", secretBytes(text, encoding), importParams(spec), false, [usage]);
    }
    const pem = pemBody(text);
    if (!pem) throw new Error("Paste a PEM key (-----BEGIN …-----) or a JWK.");
    try {
      if (usage === "verify") {
        if (pem.label === "PUBLIC KEY") return await subtle.importKey("spki", pem.bytes, importParams(spec), false, ["verify"]);
        if (pem.label === "RSA PUBLIC KEY") return await subtle.importKey("spki", pkcs1PublicToSpki(pem.bytes), importParams(spec), false, ["verify"]);
        if (pem.label === "CERTIFICATE") throw new Error("Paste the public key, not the certificate. Run: openssl x509 -pubkey -noout -in cert.pem");
        throw new Error("Verification needs a public key (BEGIN PUBLIC KEY).");
      }
      if (pem.label === "PRIVATE KEY") return await subtle.importKey("pkcs8", pem.bytes, importParams(spec), false, ["sign"]);
      if (pem.label === "RSA PRIVATE KEY") return await subtle.importKey("pkcs8", pkcs1PrivateToPkcs8(pem.bytes), importParams(spec), false, ["sign"]);
      if (pem.label === "EC PRIVATE KEY") throw new Error("Convert to PKCS#8 first: openssl pkcs8 -topk8 -nocrypt -in key.pem");
      throw new Error("Signing needs a private key (BEGIN PRIVATE KEY).");
    } catch (error) {
      if (error instanceof DOMException || error.name === "DataError" || error.name === "NotSupportedError") {
        throw new Error("This key does not match " + (spec.curve || spec.name || "the algorithm") + ": " + error.message);
      }
      throw error;
    }
  }

  function decodeSegment(segment, label) {
    try {
      return util.parseJSON(util.fromUtf8(util.fromBase64(segment), true), label);
    } catch (error) {
      throw new Error("The " + label.toLowerCase() + " is not base64url JSON.");
    }
  }

  function claimTime(value) {
    if (typeof value !== "number") return String(value);
    const date = new Date(value * 1000);
    return date.toISOString() + "  (" + util.relativeTime(date.getTime()) + ")";
  }

  register("security", [
    {
      slug: "jwt-decoder",
      name: "JWT debugger",
      description: "Decode a JSON Web Token, check its time claims, and verify the signature with a secret or public key.",
      keywords: "json web token decode verify jws bearer auth hs256 rs256 es256",
      inputs: [
        { id: "token", label: "Token", type: "textarea", rows: 5, value: SAMPLE_JWT, hint: "A leading 'Bearer ' is ignored." },
        { id: "key", label: "Secret or public key (optional, for verification)", type: "textarea", rows: 4, value: SAMPLE_SECRET, secret: true, hint: "HMAC secret, PEM public key, or JWK. Not saved." },
        { id: "encoding", label: "Secret encoding", type: "select", options: [["utf8", "UTF-8 text"], ["base64", "Base64 / base64url"], ["hex", "Hex"]], value: "utf8" },
      ],
      async run(v) {
        const token = String(v.token).trim().replace(/^Bearer\s+/i, "");
        if (!token) return { type: "note", text: "Paste a JWT." };
        const parts = token.split(".");
        if (parts.length === 5) return { type: "note", level: "warn", text: "This is a JWE (encrypted token). Only the header can be read without the decryption key: " + JSON.stringify(decodeSegment(parts[0], "Header")) };
        if (parts.length !== 3) throw new Error("A JWT has three dot-separated parts. Found " + parts.length + ".");
        const header = decodeSegment(parts[0], "Header");
        const payload = decodeSegment(parts[1], "Payload");
        const out = [];
        const now = Math.floor(Date.now() / 1000);
        const claims = [];
        const names = { iss: "Issuer", sub: "Subject", aud: "Audience", exp: "Expires", nbf: "Not before", iat: "Issued at", jti: "JWT ID", scope: "Scope", azp: "Authorized party", nonce: "Nonce" };
        for (const [key, label] of Object.entries(names)) {
          if (payload[key] === undefined) continue;
          const value = ["exp", "nbf", "iat", "auth_time"].includes(key) ? claimTime(payload[key]) : Array.isArray(payload[key]) ? payload[key].join(", ") : String(payload[key]);
          claims.push([label + " (" + key + ")", value]);
        }
        if (typeof payload.exp === "number") {
          if (payload.exp < now) out.push({ type: "note", level: "error", text: "Expired " + util.relativeTime(payload.exp * 1000) + "." });
          else out.push({ type: "note", level: "ok", text: "Valid for " + util.formatDuration((payload.exp - now) * 1000) + " more." });
        } else out.push({ type: "note", level: "warn", text: "No exp claim. This token never expires." });
        if (typeof payload.nbf === "number" && payload.nbf > now) out.push({ type: "note", level: "warn", text: "Not valid yet. nbf is " + util.relativeTime(payload.nbf * 1000) + "." });
        if (typeof payload.iat === "number" && typeof payload.exp === "number") claims.push(["Lifetime", util.formatDuration((payload.exp - payload.iat) * 1000)]);

        const alg = header.alg;
        const spec = JWT_ALGS[alg];
        if (alg === "none" || !alg) {
          out.push({ type: "note", level: "error", text: "alg is '" + alg + "'. Unsigned tokens must be rejected." });
        } else if (!spec) {
          out.push({ type: "note", level: "warn", text: "Algorithm " + alg + " is not supported for verification here." });
        } else if (!String(v.key || "").trim()) {
          out.push({ type: "note", text: "Add the " + (spec.kind === "hmac" ? "shared secret" : "public key") + " to verify the " + alg + " signature." });
        } else if (!parts[2]) {
          out.push({ type: "note", level: "error", text: "The token has no signature part." });
        } else {
          const key = await importKey(spec, v.key, "verify", v.encoding);
          const ok = await crypto.subtle.verify(signParams(spec), key, util.fromBase64(parts[2]), util.utf8(parts[0] + "." + parts[1]));
          out.push({ type: "note", level: ok ? "ok" : "error", text: ok ? "Signature verified (" + alg + ")." : "Invalid signature. The key does not match, or the token was changed." });
        }
        out.push({ type: "text", label: "Header", value: JSON.stringify(header, null, 2) });
        out.push({ type: "text", label: "Payload", value: JSON.stringify(payload, null, 2) });
        if (claims.length) out.push({ type: "kv", label: "Registered claims", rows: claims });
        out.push({ type: "kv", label: "Signature", rows: [["Algorithm", alg || "(none)"], ["Key ID (kid)", header.kid || "(none)"], ["Signature bytes", parts[2] ? util.fromBase64(parts[2]).length : 0]] });
        return out;
      },
    },
    {
      slug: "jwt-generator",
      name: "JWT generator",
      description: "Sign a JWT with HS, RS, PS, ES or EdDSA. Works with App Store Connect and APNs .p8 keys (ES256).",
      keywords: "json web token sign create apns app store connect p8 es256 hs256",
      inputs: [
        { id: "alg", label: "Algorithm", type: "select", options: Object.keys(JWT_ALGS), value: "HS256" },
        { id: "kid", label: "Key ID (kid, optional)", type: "text", value: "" },
        { id: "payload", label: "Payload (JSON)", type: "textarea", rows: 8, value: '{\n  "sub": "user-42",\n  "role": "subscriber",\n  "aud": "viewlift-api"\n}' },
        { id: "iat", label: "Add iat = now", type: "checkbox", value: true },
        { id: "expMinutes", label: "Expires in (minutes, 0 = no exp)", type: "number", value: 60, min: 0 },
        { id: "key", label: "Secret or private key", type: "textarea", rows: 4, value: SAMPLE_SECRET, secret: true, hint: "HMAC secret, PKCS#8 PEM (BEGIN PRIVATE KEY), or JWK. Not saved." },
        { id: "encoding", label: "Secret encoding", type: "select", options: [["utf8", "UTF-8 text"], ["base64", "Base64"], ["hex", "Hex"]], value: "utf8", show: (v) => String(v.alg).startsWith("HS") },
      ],
      async run(v) {
        const spec = JWT_ALGS[v.alg];
        const payload = util.parseJSON(v.payload || "{}", "Payload");
        if (!util.isPlainObject(payload)) throw new Error("Payload must be a JSON object.");
        const now = Math.floor(Date.now() / 1000);
        if (v.iat) payload.iat = now;
        if (Number(v.expMinutes) > 0) payload.exp = now + Math.round(Number(v.expMinutes) * 60);
        const header = { alg: v.alg, typ: "JWT" };
        if (v.kid) header.kid = v.kid;
        const signingInput = util.toBase64Url(util.utf8(JSON.stringify(header))) + "." + util.toBase64Url(util.utf8(JSON.stringify(payload)));
        if (!String(v.key || "").trim()) throw new Error("Add a " + (spec.kind === "hmac" ? "secret" : "private key") + " to sign.");
        const key = await importKey(spec, v.key, "sign", v.encoding);
        const signature = new Uint8Array(await crypto.subtle.sign(signParams(spec), key, util.utf8(signingInput)));
        const token = signingInput + "." + util.toBase64Url(signature);
        return [
          { type: "text", label: "Token", value: token },
          { type: "text", label: "Authorization header", value: "Authorization: Bearer " + token },
          { type: "text", label: "Header", value: JSON.stringify(header, null, 2) },
          { type: "text", label: "Payload", value: JSON.stringify(payload, null, 2) },
        ];
      },
    },
    {
      slug: "base64-encoder-decoder",
      name: "Base64 encoder & decoder",
      description: "Encode or decode Base64 and base64url, for text or files.",
      keywords: "b64 encode decode file data url",
      inputs: [
        { id: "mode", label: "Mode", type: "select", options: [["encode", "Encode"], ["decode", "Decode"]], value: "encode" },
        { id: "text", label: "Text", type: "textarea", rows: 8, value: "Viewlift AI · tvOS ✓" },
        { id: "file", label: "Or encode a file", type: "file", show: (v) => v.mode === "encode" },
        { id: "urlSafe", label: "URL-safe (base64url)", type: "checkbox", value: false },
        { id: "wrap", label: "Wrap at 76 characters", type: "checkbox", value: false, show: (v) => v.mode === "encode" },
        { id: "dataUrl", label: "Output a data: URL", type: "checkbox", value: false, show: (v) => v.mode === "encode" && v.file },
      ],
      run(v) {
        if (v.mode === "decode") {
          const bytes = util.fromBase64(v.text);
          try {
            const text = util.fromUtf8(bytes, true);
            return [{ type: "text", label: "Decoded text", value: text }, { type: "note", text: bytes.length + " bytes." }];
          } catch (error) {
            return [
              { type: "note", level: "warn", text: "Decoded " + bytes.length + " bytes of binary data." },
              { type: "text", label: "Hex", value: util.toHex(bytes.slice(0, 4096)).replace(/(.{2})/g, "$1 ").trim() + (bytes.length > 4096 ? " …" : "") },
              { type: "files", label: "File", files: [{ name: "decoded.bin", blob: new Blob([bytes]) }] },
            ];
          }
        }
        const bytes = v.file ? v.file.bytes : util.utf8(v.text);
        let out = v.urlSafe ? util.toBase64Url(bytes) : util.toBase64(bytes);
        if (v.file && v.dataUrl) out = "data:" + (v.file.type || "application/octet-stream") + ";base64," + util.toBase64(bytes);
        else if (v.wrap) out = out.replace(/(.{76})/g, "$1\n").trim();
        return [{ type: "text", label: "Base64", value: out }, { type: "note", text: bytes.length + " bytes to " + out.length + " characters." }];
      },
    },
    {
      slug: "url-encoder-decoder",
      name: "URL encoder & decoder",
      description: "Percent-encode or decode a URL, a component, or form data.",
      keywords: "percent encode decode uri component query",
      inputs: [
        { id: "mode", label: "Mode", type: "select", options: [["encode", "Encode"], ["decode", "Decode"]], value: "encode" },
        { id: "scope", label: "Scope", type: "select", options: [["component", "Component (encodeURIComponent)"], ["uri", "Full URL (encodeURI)"], ["form", "Form (spaces as +)"]], value: "component" },
        { id: "text", label: "Text", type: "textarea", rows: 6, value: "title=Match Highlights & more/2026?lang=fr" },
      ],
      run(v) {
        const text = String(v.text);
        try {
          if (v.mode === "decode") {
            const source = v.scope === "form" ? text.replace(/\+/g, " ") : text;
            return { type: "text", label: "Decoded", value: v.scope === "uri" ? decodeURI(source) : decodeURIComponent(source) };
          }
          if (v.scope === "uri") return { type: "text", label: "Encoded", value: encodeURI(text) };
          const encoded = encodeURIComponent(text).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
          return { type: "text", label: "Encoded", value: v.scope === "form" ? encoded.replace(/%20/g, "+") : encoded };
        } catch (error) {
          throw new Error("Malformed percent-encoding: " + error.message);
        }
      },
    },
    {
      slug: "html-encoder-decoder",
      name: "HTML encoder & decoder",
      description: "Escape and unescape HTML entities.",
      keywords: "entities escape unescape amp lt gt",
      inputs: [
        { id: "mode", label: "Mode", type: "select", options: [["encode", "Encode"], ["decode", "Decode"]], value: "encode" },
        { id: "text", label: "Text", type: "textarea", rows: 6, value: '<a href="/watch?id=1&t=30">Café “Live”</a>' },
        { id: "nonAscii", label: "Encode non-ASCII as numeric entities", type: "checkbox", value: false, show: (v) => v.mode === "encode" },
      ],
      run(v) {
        const text = String(v.text);
        if (v.mode === "decode") {
          if (typeof DOMParser !== "undefined") {
            const doc = new DOMParser().parseFromString("<!doctype html><body>" + text.replace(/</g, "&lt;"), "text/html");
            return { type: "text", label: "Decoded", value: doc.body.textContent };
          }
          const map = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" };
          return { type: "text", label: "Decoded", value: text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => (e[0] === "#" ? String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : map[e] || m)) };
        }
        let out = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
        if (v.nonAscii) out = Array.from(out).map((c) => (c.codePointAt(0) > 126 ? "&#" + c.codePointAt(0) + ";" : c)).join("");
        return { type: "text", label: "Encoded", value: out };
      },
    },
    {
      slug: "hash-generator",
      name: "Hash generator",
      description: "MD5, SHA-1, SHA-2 and SHA-3 digests of text or a file. Hashing runs on this Mac.",
      keywords: "md5 sha1 sha256 sha512 sha3 digest checksum",
      action: "Hash",
      live: false,
      autorun: true,
      inputs: [
        { id: "text", label: "Text", type: "textarea", rows: 6, value: "Viewlift AI" },
        { id: "file", label: "Or hash a file (up to 25 MB)", type: "file" },
        { id: "upper", label: "Uppercase hex", type: "checkbox", value: false },
      ],
      async run(v, ctx) {
        const payload = v.file ? { data: util.toBase64(v.file.bytes) } : { text: String(v.text) };
        payload.algorithms = ["md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "sha3_512"];
        const result = await ctx.server("hash", payload);
        const rows = Object.entries(result.digests).map(([name, hex]) => {
          const value = v.upper ? hex.toUpperCase() : hex;
          return [name.toUpperCase().replace("_", "-"), value, util.toBase64(util.fromHex(hex))];
        });
        return [
          { type: "note", text: (v.file ? v.file.name + ", " : "") + util.formatBytes(result.size) + "." },
          { type: "table", label: "Digests", columns: ["Algorithm", "Hex", "Base64"], rows },
        ];
      },
    },
    {
      slug: "bcrypt-generator",
      name: "bcrypt generator",
      description: "Hash a password with bcrypt, or check a password against a hash. Runs on this Mac.",
      keywords: "password hash verify salt cost",
      live: false,
      action: "Run",
      inputs: [
        { id: "mode", label: "Mode", type: "select", options: [["hash", "Hash"], ["verify", "Verify"]], value: "hash" },
        { id: "password", label: "Password", type: "password", value: "", secret: true },
        { id: "cost", label: "Cost (rounds)", type: "range", min: 4, max: 15, step: 1, value: 12, show: (v) => v.mode === "hash" },
        { id: "hash", label: "bcrypt hash", type: "text", value: "", show: (v) => v.mode === "verify", wide: true },
      ],
      async run(v, ctx) {
        if (v.mode === "verify") {
          const result = await ctx.server("bcrypt-verify", { password: v.password || "", hash: v.hash || "" });
          return { type: "note", level: result.match ? "ok" : "error", text: result.match ? "Password matches the hash." : "Password does not match." };
        }
        const result = await ctx.server("bcrypt-hash", { password: v.password || "", cost: Number(v.cost) });
        return [
          { type: "text", label: "Hash", value: result.hash },
          { type: "note", text: "Cost " + result.cost + " took " + result.ms + " ms on this Mac." },
        ];
      },
    },
    {
      slug: "uuid-generator",
      name: "UUID generator",
      description: "Generate v4, v7 or v1 UUIDs, and inspect an existing UUID.",
      keywords: "guid uuid4 uuid7 random unique identifier nsuuid",
      live: true,
      action: "Generate",
      inputs: [
        { id: "version", label: "Version", type: "select", options: [["v4", "v4 random"], ["v7", "v7 time-ordered"], ["v1", "v1 time + node"], ["nil", "Nil"], ["max", "Max"]], value: "v4" },
        { id: "count", label: "How many", type: "number", value: 5, min: 1, max: 10000 },
        { id: "upper", label: "Uppercase (NSUUID style)", type: "checkbox", value: false },
        { id: "noHyphens", label: "No hyphens", type: "checkbox", value: false },
        { id: "inspect", label: "Inspect a UUID (optional)", type: "text", value: "", wide: true },
      ],
      run(v) {
        const out = [];
        if (String(v.inspect || "").trim()) {
          const hex = String(v.inspect).trim().replace(/[{}-]/g, "").toLowerCase();
          if (!/^[0-9a-f]{32}$/.test(hex)) throw new Error("Not a UUID.");
          const version = parseInt(hex[12], 16);
          const variantBits = parseInt(hex[16], 16);
          const variant = variantBits >= 0xe ? "reserved" : variantBits >= 0xc ? "Microsoft" : variantBits >= 0x8 ? "RFC 9562" : "NCS";
          const rows = [["Version", version], ["Variant", variant]];
          if (version === 7) {
            const ms = parseInt(hex.slice(0, 12), 16);
            rows.push(["Timestamp", new Date(ms).toISOString()]);
          }
          if (version === 1) {
            const ticks = BigInt("0x" + hex.slice(13, 16) + hex.slice(8, 12) + hex.slice(0, 8));
            const ms = Number((ticks - 0x01b21dd213814000n) / 10000n);
            rows.push(["Timestamp", new Date(ms).toISOString()], ["Node", hex.slice(20).replace(/(..)(?!$)/g, "$1:")]);
          }
          out.push({ type: "kv", label: "Inspect", rows });
        }
        const count = util.clampInt(v.count, 1, 10000, 5);
        const make = { v4: util.uuidV4, v7: util.uuidV7, v1: util.uuidV1, nil: () => "00000000-0000-0000-0000-000000000000", max: () => "ffffffff-ffff-ffff-ffff-ffffffffffff" }[v.version] || util.uuidV4;
        const ids = Array.from({ length: count }, () => {
          let id = make();
          if (v.noHyphens) id = id.replace(/-/g, "");
          return v.upper ? id.toUpperCase() : id;
        });
        out.unshift({ type: "text", label: ids.length + " UUIDs", value: ids.join("\n") });
        return out;
      },
    },
    {
      slug: "nanoid-generator",
      name: "NanoID generator",
      description: "Compact, URL-safe random IDs with a custom size and alphabet.",
      keywords: "nano id short random",
      action: "Generate",
      inputs: [
        { id: "size", label: "Size", type: "number", value: 21, min: 2, max: 256 },
        { id: "count", label: "How many", type: "number", value: 10, min: 1, max: 10000 },
        { id: "alphabet", label: "Alphabet", type: "select", options: [["url", "URL-safe A-Za-z0-9_-"], ["alnum", "Alphanumeric"], ["lower", "Lowercase + digits"], ["digits", "Digits"], ["hex", "Hex"], ["nolook", "No look-alikes"], ["custom", "Custom"]], value: "url" },
        { id: "custom", label: "Custom alphabet", type: "text", value: "abcdef123456", show: (v) => v.alphabet === "custom" },
      ],
      run(v) {
        const sets = {
          url: "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict",
          alnum: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
          lower: "abcdefghijklmnopqrstuvwxyz0123456789",
          digits: "0123456789",
          hex: "0123456789abcdef",
          nolook: "6789BCDFGHJKLMNPQRTWbcdfghjkmnpqrtwz",
        };
        const alphabet = v.alphabet === "custom" ? [...new Set(Array.from(String(v.custom || "")))].join("") : sets[v.alphabet];
        if (!alphabet || alphabet.length < 2) throw new Error("The alphabet needs at least 2 distinct characters.");
        const size = util.clampInt(v.size, 2, 256, 21);
        const ids = Array.from({ length: util.clampInt(v.count, 1, 10000, 10) }, () => util.randomFrom(Array.from(alphabet), size));
        const bits = size * Math.log2(alphabet.length);
        return [
          { type: "text", label: "IDs", value: ids.join("\n") },
          { type: "note", text: bits.toFixed(1) + " bits of randomness per ID (UUID v4 has 122)." },
        ];
      },
    },
    {
      slug: "password-generator",
      name: "Password generator",
      description: "Strong random passwords, including the iCloud Keychain xxxxxx-xxxxxx-xxxxxx style.",
      keywords: "random secure passphrase keychain",
      action: "Generate",
      inputs: [
        { id: "style", label: "Style", type: "select", options: [["random", "Random characters"], ["apple", "Apple style (xxxxxx-xxxxxx-xxxxxx)"]], value: "random" },
        { id: "length", label: "Length", type: "range", min: 8, max: 128, step: 1, value: 24, show: (v) => v.style === "random" },
        { id: "upper", label: "A-Z", type: "checkbox", value: true, show: (v) => v.style === "random" },
        { id: "lower", label: "a-z", type: "checkbox", value: true, show: (v) => v.style === "random" },
        { id: "digits", label: "0-9", type: "checkbox", value: true, show: (v) => v.style === "random" },
        { id: "symbols", label: "Symbols", type: "checkbox", value: true, show: (v) => v.style === "random" },
        { id: "noAmbiguous", label: "Avoid look-alikes (l 1 I O 0)", type: "checkbox", value: true, show: (v) => v.style === "random" },
        { id: "count", label: "How many", type: "number", value: 5, min: 1, max: 100 },
      ],
      run(v) {
        const count = util.clampInt(v.count, 1, 100, 5);
        if (v.style === "apple") {
          const lower = "abcdefghijkmnopqrstuvwxyz";
          const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
          const digits = "23456789";
          const make = () => {
            const chars = Array.from({ length: 18 }, () => util.pick(lower));
            const spots = new Set();
            while (spots.size < 2) spots.add(util.randomInt(18));
            const [u, d] = [...spots];
            chars[u] = util.pick(upper);
            chars[d] = util.pick(digits);
            const s = chars.join("");
            return s.slice(0, 6) + "-" + s.slice(6, 12) + "-" + s.slice(12);
          };
          return [{ type: "text", label: "Passwords", value: Array.from({ length: count }, make).join("\n") }, { type: "note", text: "About 71 bits of entropy each." }];
        }
        const strip = (s) => (v.noAmbiguous ? s.replace(/[lI1O0o|]/g, "") : s);
        const groups = [];
        if (v.upper) groups.push(strip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"));
        if (v.lower) groups.push(strip("abcdefghijklmnopqrstuvwxyz"));
        if (v.digits) groups.push(strip("0123456789"));
        if (v.symbols) groups.push(strip("!@#$%^&*()-_=+[]{};:,.?/~"));
        if (!groups.length) throw new Error("Pick at least one character set.");
        const length = util.clampInt(v.length, groups.length, 128, 24);
        const all = groups.join("");
        const make = () => {
          const chars = groups.map((g) => util.pick(g));
          while (chars.length < length) chars.push(util.pick(all));
          for (let i = chars.length - 1; i > 0; i -= 1) {
            const j = util.randomInt(i + 1);
            [chars[i], chars[j]] = [chars[j], chars[i]];
          }
          return chars.join("");
        };
        return [
          { type: "text", label: "Passwords", value: Array.from({ length: count }, make).join("\n") },
          { type: "note", text: (length * Math.log2(all.length)).toFixed(0) + " bits of entropy each." },
        ];
      },
    },
    {
      slug: "password-strength-checker",
      name: "Password strength checker",
      description: "Estimate entropy and crack time, and flag common patterns. Nothing leaves the page.",
      keywords: "entropy zxcvbn security audit",
      inputs: [{ id: "password", label: "Password", type: "password", value: "", secret: true }],
      run(v) {
        const pw = String(v.password || "");
        if (!pw) return { type: "note", text: "Type a password. It is checked in this page only and is not saved." };
        const common = ["password", "123456", "qwerty", "letmein", "welcome", "admin", "iloveyou", "monkey", "dragon", "football", "baseball", "abc123", "111111", "sunshine", "master", "shadow", "trustno1", "passw0rd", "superman", "princess", "starwars", "login", "viewlift"];
        let pool = 0;
        if (/[a-z]/.test(pw)) pool += 26;
        if (/[A-Z]/.test(pw)) pool += 26;
        if (/\d/.test(pw)) pool += 10;
        if (/[^A-Za-z0-9]/.test(pw)) pool += 33;
        let bits = pw.length * Math.log2(Math.max(pool, 1));
        const findings = [];
        const lower = pw.toLowerCase().replace(/[@4]/g, "a").replace(/3/g, "e").replace(/[1!]/g, "i").replace(/0/g, "o").replace(/\$/g, "s");
        if (common.some((word) => lower.includes(word))) {
          findings.push(["error", "Contains a very common password or word."]);
          bits = Math.min(bits, 20);
        }
        if (/(.)\1{2,}/.test(pw)) {
          findings.push(["warn", "Repeats the same character three or more times."]);
          bits -= 8;
        }
        if (/(?:abc|bcd|cde|def|123|234|345|456|567|678|789|890|qwe|wer|ert|asd|sdf|zxc)/i.test(pw)) {
          findings.push(["warn", "Contains a keyboard or alphabet sequence."]);
          bits -= 8;
        }
        if (/(19|20)\d{2}/.test(pw)) {
          findings.push(["warn", "Contains a year."]);
          bits -= 4;
        }
        if (pw.length < 12) findings.push(["warn", "Shorter than 12 characters."]);
        if (pool <= 26) findings.push(["warn", "Uses only one kind of character."]);
        bits = Math.max(0, bits);
        const score = bits < 28 ? 0 : bits < 36 ? 1 : bits < 60 ? 2 : bits < 80 ? 3 : 4;
        const labels = ["Very weak", "Weak", "Fair", "Strong", "Very strong"];
        const guesses = Math.pow(2, bits);
        const fmt = (seconds) => (seconds > 3.15e7 * 1e6 ? "millions of years" : seconds > 3.15e7 ? (seconds / 3.15e7).toFixed(0) + " years" : util.formatDuration(seconds * 1000));
        return [
          { type: "note", level: score >= 3 ? "ok" : score === 2 ? "warn" : "error", text: labels[score] + " (" + bits.toFixed(0) + " bits)." },
          { type: "kv", label: "Estimated time to crack", rows: [["Online, 100 guesses/s", fmt(guesses / 2 / 100)], ["Offline slow hash (bcrypt), 10k/s", fmt(guesses / 2 / 1e4)], ["Offline fast hash, 10 billion/s", fmt(guesses / 2 / 1e10)]] },
          { type: "table", label: "Findings", columns: ["Level", "Finding"], rows: findings.length ? findings : [["ok", "No common patterns found."]] },
        ];
      },
    },
    {
      slug: "hmac-generator",
      name: "HMAC generator",
      description: "HMAC-SHA1, SHA-256, SHA-384 and SHA-512 signatures for webhooks and signed URLs.",
      keywords: "signature webhook signing sha256 mac",
      inputs: [
        { id: "algorithm", label: "Algorithm", type: "select", options: [["SHA-256", "HMAC-SHA256"], ["SHA-1", "HMAC-SHA1"], ["SHA-384", "HMAC-SHA384"], ["SHA-512", "HMAC-SHA512"]], value: "SHA-256" },
        { id: "key", label: "Secret key", type: "text", value: SAMPLE_SECRET, secret: true },
        { id: "keyEncoding", label: "Key encoding", type: "select", options: [["utf8", "UTF-8"], ["hex", "Hex"], ["base64", "Base64"]], value: "utf8" },
        { id: "message", label: "Message", type: "textarea", rows: 6, value: '{"event":"playback.start","id":"vl-123"}' },
        { id: "expected", label: "Compare with signature (optional)", type: "text", value: "", wide: true },
      ],
      async run(v) {
        if (!crypto.subtle) throw new Error("WebCrypto is unavailable. Open the console on http://127.0.0.1.");
        const key = await crypto.subtle.importKey("raw", secretBytes(v.key || "", v.keyEncoding), { name: "HMAC", hash: v.algorithm }, false, ["sign"]);
        const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, util.utf8(v.message || "")));
        const hex = util.toHex(mac);
        const b64 = util.toBase64(mac);
        const out = [{ type: "kv", label: "HMAC-" + v.algorithm.replace("-", ""), rows: [["Hex", hex], ["Base64", b64], ["Base64url", util.toBase64Url(mac)]] }];
        const expected = String(v.expected || "").trim().replace(/^sha\d+=/i, "");
        if (expected) {
          const ok = [hex, b64, util.toBase64Url(mac)].includes(expected) || expected.toLowerCase() === hex;
          out.unshift({ type: "note", level: ok ? "ok" : "error", text: ok ? "Signature matches." : "Signature does not match." });
        }
        return out;
      },
    },
  ]);
})();
