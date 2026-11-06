/* APIs & HTTP */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;
  kit.data = kit.data || {};

  const STATUS = [
    [100, "Continue", "The server got the request headers. Send the body."],
    [101, "Switching Protocols", "The connection is switching protocol, usually to WebSocket."],
    [103, "Early Hints", "Preload hints sent before the final response."],
    [200, "OK", "The request succeeded."],
    [201, "Created", "A new resource was created. Location usually points to it."],
    [202, "Accepted", "Accepted for processing, not finished yet."],
    [203, "Non-Authoritative Information", "A proxy changed the origin's response."],
    [204, "No Content", "Success with no body."],
    [205, "Reset Content", "Success. The client should reset the form or view."],
    [206, "Partial Content", "A Range request succeeded. Used for video segments and resumable downloads."],
    [207, "Multi-Status", "WebDAV: several status codes in the body."],
    [208, "Already Reported", "WebDAV: members already listed."],
    [226, "IM Used", "Delta encoding applied."],
    [300, "Multiple Choices", "Several representations are available."],
    [301, "Moved Permanently", "Use the Location URL from now on. Method may change to GET."],
    [302, "Found", "Temporary redirect. Method may change to GET."],
    [303, "See Other", "Fetch the result at Location with GET."],
    [304, "Not Modified", "Cached copy is still valid (ETag or Last-Modified matched)."],
    [307, "Temporary Redirect", "Temporary redirect that keeps the method and body."],
    [308, "Permanent Redirect", "Permanent redirect that keeps the method and body."],
    [400, "Bad Request", "The request is malformed or fails validation."],
    [401, "Unauthorized", "Authentication is missing or invalid. Check the token."],
    [402, "Payment Required", "Reserved. Some APIs use it for billing or entitlement failures."],
    [403, "Forbidden", "Authenticated but not allowed. Check entitlements, geo-blocking or roles."],
    [404, "Not Found", "No resource at this URL."],
    [405, "Method Not Allowed", "The resource does not support this method. See Allow."],
    [406, "Not Acceptable", "The server cannot match the Accept header."],
    [407, "Proxy Authentication Required", "The proxy needs credentials."],
    [408, "Request Timeout", "The server gave up waiting for the request."],
    [409, "Conflict", "Conflicts with the current state, such as a version mismatch."],
    [410, "Gone", "The resource was removed for good."],
    [411, "Length Required", "Content-Length is required."],
    [412, "Precondition Failed", "If-Match or If-Unmodified-Since did not match."],
    [413, "Content Too Large", "The request body is too big."],
    [414, "URI Too Long", "The URL is too long."],
    [415, "Unsupported Media Type", "The server does not accept this Content-Type."],
    [416, "Range Not Satisfiable", "The requested byte range is outside the file."],
    [417, "Expectation Failed", "The Expect header cannot be met."],
    [418, "I'm a teapot", "An April Fools' joke from RFC 2324."],
    [421, "Misdirected Request", "This server cannot answer for this host. Often an HTTP/2 connection reuse issue."],
    [422, "Unprocessable Content", "The syntax is fine but the data fails validation."],
    [423, "Locked", "WebDAV: the resource is locked."],
    [424, "Failed Dependency", "WebDAV: a dependent request failed."],
    [425, "Too Early", "The server will not process a request that might be replayed (TLS early data)."],
    [426, "Upgrade Required", "Switch to the protocol in the Upgrade header."],
    [428, "Precondition Required", "Send a conditional request (If-Match)."],
    [429, "Too Many Requests", "Rate limited. Check Retry-After."],
    [431, "Request Header Fields Too Large", "Headers or cookies are too big."],
    [451, "Unavailable For Legal Reasons", "Blocked for legal reasons, such as content licensing by region."],
    [500, "Internal Server Error", "Unhandled error on the server."],
    [501, "Not Implemented", "The server does not support this feature."],
    [502, "Bad Gateway", "A proxy or CDN got an invalid response from the origin."],
    [503, "Service Unavailable", "Overloaded or down for maintenance. Check Retry-After."],
    [504, "Gateway Timeout", "A proxy or CDN timed out waiting for the origin."],
    [505, "HTTP Version Not Supported", "The HTTP version is not supported."],
    [506, "Variant Also Negotiates", "Content negotiation misconfiguration."],
    [507, "Insufficient Storage", "WebDAV: no space to store the resource."],
    [508, "Loop Detected", "WebDAV: infinite loop while processing."],
    [510, "Not Extended", "More extensions are required."],
    [511, "Network Authentication Required", "A captive portal needs sign-in (hotel or airport Wi-Fi)."],
    [520, "Unknown Error (Cloudflare)", "The origin returned something Cloudflare did not expect."],
    [521, "Web Server Is Down (Cloudflare)", "The origin refused the connection."],
    [522, "Connection Timed Out (Cloudflare)", "Cloudflare could not reach the origin."],
    [524, "A Timeout Occurred (Cloudflare)", "The origin accepted the connection but did not answer in time."],
    [-1001, "NSURLErrorTimedOut", "URLSession: the request timed out."],
    [-1003, "NSURLErrorCannotFindHost", "URLSession: DNS lookup failed."],
    [-1004, "NSURLErrorCannotConnectToHost", "URLSession: connection refused or proxy down."],
    [-1005, "NSURLErrorNetworkConnectionLost", "URLSession: the connection dropped mid-request."],
    [-1009, "NSURLErrorNotConnectedToInternet", "URLSession: the device is offline."],
    [-1022, "NSURLErrorAppTransportSecurityRequiresSecureConnection", "ATS blocked a plain http:// load. Add an exception or use https."],
    [-1200, "NSURLErrorSecureConnectionFailed", "TLS handshake failed. Often a proxy certificate that is not trusted."],
    [-1202, "NSURLErrorServerCertificateUntrusted", "The server certificate is not trusted. Install and trust the proxy CA."],
    [-999, "NSURLErrorCancelled", "URLSession: the task was cancelled, or certificate pinning rejected the server."],
  ];

  const HEADERS = {
    "accept": ["request", "Media types the client can handle."],
    "accept-encoding": ["request", "Compression the client accepts (gzip, br, zstd)."],
    "accept-language": ["request", "Preferred languages."],
    "accept-ranges": ["response", "Whether byte-range requests are supported."],
    "access-control-allow-origin": ["response", "CORS: which origins may read the response."],
    "access-control-allow-credentials": ["response", "CORS: whether cookies and auth may be sent."],
    "access-control-allow-headers": ["response", "CORS: request headers allowed in the real request."],
    "access-control-allow-methods": ["response", "CORS: allowed methods."],
    "access-control-expose-headers": ["response", "CORS: response headers scripts may read."],
    "access-control-max-age": ["response", "CORS: how long the preflight result may be cached."],
    "age": ["response", "Seconds the response has been in a shared cache."],
    "allow": ["response", "Methods the resource supports."],
    "alt-svc": ["response", "Advertises HTTP/3 or other endpoints for this origin."],
    "authorization": ["request", "Credentials, such as Bearer tokens or Basic auth. Treat as secret."],
    "cache-control": ["both", "Caching rules: max-age, no-store, private, must-revalidate."],
    "connection": ["both", "Hop-by-hop connection options. Not used in HTTP/2."],
    "content-disposition": ["response", "Show inline or download as a file with a name."],
    "content-encoding": ["both", "Compression applied to the body."],
    "content-language": ["response", "Language of the content."],
    "content-length": ["both", "Body size in bytes."],
    "content-range": ["response", "Which part of the file a 206 response contains."],
    "content-security-policy": ["response", "Allowed sources for scripts, styles, frames and more."],
    "content-type": ["both", "Media type of the body, with charset or boundary."],
    "cookie": ["request", "Cookies sent to the server. Treat as secret."],
    "date": ["response", "When the response was generated."],
    "etag": ["response", "Version tag for caching. Sent back in If-None-Match."],
    "expect": ["request", "Usually 100-continue before a large upload."],
    "expires": ["response", "Absolute expiry date. Cache-Control max-age wins."],
    "forwarded": ["request", "Proxy information (standard form of X-Forwarded-*)."],
    "host": ["request", "Target host and port. :authority in HTTP/2."],
    "if-match": ["request", "Only proceed if the ETag matches."],
    "if-modified-since": ["request", "Return 304 if unchanged since this date."],
    "if-none-match": ["request", "Return 304 if the ETag still matches."],
    "if-range": ["request", "Send the range only if unchanged."],
    "keep-alive": ["both", "Keep-alive timing hints."],
    "last-modified": ["response", "When the resource last changed."],
    "link": ["response", "Related links, such as preload or pagination."],
    "location": ["response", "Redirect target or URL of a created resource."],
    "origin": ["request", "Origin of a cross-site or POST request."],
    "permissions-policy": ["response", "Which browser features the page may use."],
    "pragma": ["request", "Legacy HTTP/1.0 cache control."],
    "proxy-authorization": ["request", "Credentials for a proxy."],
    "range": ["request", "Ask for part of the file (bytes=0-1023)."],
    "referer": ["request", "The page that linked to this request."],
    "referrer-policy": ["response", "How much referrer information to send."],
    "retry-after": ["response", "When to retry after 429 or 503."],
    "server": ["response", "Server software."],
    "server-timing": ["response", "Server-side timing metrics."],
    "set-cookie": ["response", "Sets a cookie. Check Secure, HttpOnly and SameSite."],
    "strict-transport-security": ["response", "HSTS: only use HTTPS for this host."],
    "te": ["request", "Transfer codings accepted. trailers in gRPC."],
    "timing-allow-origin": ["response", "Origins allowed to read Resource Timing details."],
    "trailer": ["response", "Headers sent after the body."],
    "transfer-encoding": ["both", "chunked body framing in HTTP/1.1."],
    "upgrade": ["both", "Switch protocol, such as websocket."],
    "user-agent": ["request", "Client software. iOS apps send AppName/Build CFNetwork/x Darwin/y."],
    "vary": ["response", "Request headers that change the response. Matters for caching."],
    "via": ["both", "Proxies the message went through."],
    "www-authenticate": ["response", "How to authenticate after a 401."],
    "x-content-type-options": ["response", "nosniff: do not guess the content type."],
    "x-frame-options": ["response", "Legacy clickjacking protection (DENY, SAMEORIGIN)."],
    "x-forwarded-for": ["request", "Client IP chain through proxies."],
    "x-forwarded-proto": ["request", "Original protocol before a proxy."],
    "x-request-id": ["both", "Correlation ID for logs."],
    "x-cache": ["response", "CDN cache HIT or MISS."],
    "x-amz-cf-pop": ["response", "CloudFront edge location."],
    "x-amz-cf-id": ["response", "CloudFront request ID."],
    "cf-ray": ["response", "Cloudflare request ID and data center."],
    "cf-cache-status": ["response", "Cloudflare cache result."],
    "apns-push-type": ["request", "APNs: alert, background, voip, liveactivity and more."],
    "apns-topic": ["request", "APNs: the app bundle ID, with a suffix for some push types."],
    "apns-priority": ["request", "APNs: 10 immediate, 5 power-friendly, 1 lowest."],
    "apns-expiration": ["request", "APNs: when to stop retrying delivery."],
    "apns-collapse-id": ["request", "APNs: replace an earlier notification with the same id."],
    "apns-id": ["both", "APNs: notification UUID."],
  };
  kit.data.headers = HEADERS;
  kit.data.statuses = STATUS;

  function parseHeaderText(text) {
    const headers = {};
    for (const [name, value] of util.parseHeaderLines(text)) headers[name] = value;
    return headers;
  }

  /* cURL parsing and code generation */

  function parseCurl(command) {
    const args = util.shellSplit(String(command).trim());
    if (!args.length || !/curl$/.test(args[0])) throw new Error("Start the command with curl.");
    const req = { method: "", url: "", headers: [], data: [], form: [], flags: [], user: "", get: false };
    for (let i = 1; i < args.length; i += 1) {
      const arg = args[i];
      const next = () => {
        i += 1;
        if (i >= args.length) throw new Error(arg + " needs a value.");
        return args[i];
      };
      if (arg === "-X" || arg === "--request") req.method = next().toUpperCase();
      else if (arg.startsWith("-X") && arg.length > 2) req.method = arg.slice(2).toUpperCase();
      else if (arg === "-H" || arg === "--header") {
        const raw = next();
        const index = raw.indexOf(":");
        if (index > 0) req.headers.push([raw.slice(0, index).trim(), raw.slice(index + 1).trim()]);
      } else if (["-d", "--data", "--data-raw", "--data-binary", "--data-ascii"].includes(arg)) req.data.push(next());
      else if (arg === "--data-urlencode") {
        const raw = next();
        const eq = raw.indexOf("=");
        req.data.push(eq >= 0 ? raw.slice(0, eq + 1) + encodeURIComponent(raw.slice(eq + 1)) : encodeURIComponent(raw));
      } else if (arg === "--json") {
        req.data.push(next());
        req.headers.push(["Content-Type", "application/json"], ["Accept", "application/json"]);
      } else if (arg === "-F" || arg === "--form") req.form.push(next());
      else if (arg === "-u" || arg === "--user") req.user = next();
      else if (arg === "-A" || arg === "--user-agent") req.headers.push(["User-Agent", next()]);
      else if (arg === "-b" || arg === "--cookie") req.headers.push(["Cookie", next()]);
      else if (arg === "-e" || arg === "--referer") req.headers.push(["Referer", next()]);
      else if (arg === "--url") req.url = next();
      else if (arg === "-G" || arg === "--get") req.get = true;
      else if (arg === "-I" || arg === "--head") req.method = "HEAD";
      else if (["-o", "--output", "--connect-timeout", "-m", "--max-time", "--retry", "-w", "--write-out", "--cacert", "--cert", "--key", "-x", "--proxy", "--resolve"].includes(arg)) req.flags.push(arg + " " + next());
      else if (arg.startsWith("-")) req.flags.push(arg);
      else if (!req.url) req.url = arg;
    }
    if (!req.url) throw new Error("No URL found in the command.");
    if (!/^[a-z]+:\/\//i.test(req.url)) req.url = "http://" + req.url;
    if (req.user) req.headers.push(["Authorization", "Basic " + util.toBase64(util.utf8(req.user))]);
    let body = req.data.join("&");
    if (req.get && body) {
      req.url += (req.url.includes("?") ? "&" : "?") + body;
      body = "";
    }
    if (!req.method) req.method = body || req.form.length ? "POST" : "GET";
    if (body && !req.headers.some(([k]) => k.toLowerCase() === "content-type")) req.headers.push(["Content-Type", "application/x-www-form-urlencoded"]);
    return { ...req, body };
  }

  function jsString(value) {
    return JSON.stringify(String(value));
  }

  function toFetch(req) {
    const lines = ["const response = await fetch(" + jsString(req.url) + ", {", "  method: " + jsString(req.method) + ","];
    const headers = req.headers.filter(([k]) => !(req.form.length && k.toLowerCase() === "content-type"));
    if (headers.length) {
      lines.push("  headers: {");
      headers.forEach(([k, v], i) => lines.push("    " + jsString(k) + ": " + jsString(v) + (i < headers.length - 1 ? "," : "")));
      lines.push("  },");
    }
    let prelude = "";
    if (req.form.length) {
      prelude = "const form = new FormData();\n" + req.form.map((f) => {
        const eq = f.indexOf("=");
        const key = f.slice(0, eq);
        const value = f.slice(eq + 1);
        return value.startsWith("@") ? "form.append(" + jsString(key) + ", fileInput.files[0]); // " + value.slice(1) : "form.append(" + jsString(key) + ", " + jsString(value) + ");";
      }).join("\n") + "\n\n";
      lines.push("  body: form,");
    } else if (req.body) {
      let json = null;
      try {
        json = JSON.parse(req.body);
      } catch (error) {
        json = null;
      }
      lines.push("  body: " + (json !== null && typeof json === "object" ? "JSON.stringify(" + JSON.stringify(json, null, 2).replace(/\n/g, "\n  ") + ")" : jsString(req.body)) + ",");
    }
    if (lines[lines.length - 1].endsWith(",")) lines[lines.length - 1] = lines[lines.length - 1].slice(0, -1);
    lines.push("});", "const data = await response.text();");
    return prelude + lines.join("\n");
  }

  function swiftString(value) {
    const text = String(value);
    if (!/["\\\n]/.test(text)) return '"' + text + '"';
    return '#"""\n' + text + '\n"""#';
  }

  function toSwift(req) {
    const lines = [
      "var request = URLRequest(url: URL(string: " + swiftString(req.url) + ")!)",
      'request.httpMethod = "' + req.method + '"',
    ];
    for (const [k, v] of req.headers) lines.push("request.setValue(" + swiftString(v) + ', forHTTPHeaderField: "' + k + '")');
    if (req.form.length) lines.push("// Multipart form fields: " + req.form.join(", ") + ". Build the body with a boundary.");
    else if (req.body) lines.push("request.httpBody = Data(" + swiftString(req.body) + ".utf8)");
    lines.push("", "let (data, response) = try await URLSession.shared.data(for: request)", "let status = (response as? HTTPURLResponse)?.statusCode", "print(status ?? -1, String(decoding: data, as: UTF8.self))");
    return lines.join("\n");
  }

  function toCurl(req, multiline) {
    const parts = ["curl"];
    if (req.method && req.method !== "GET" && !(req.method === "POST" && req.body)) parts.push("-X " + req.method);
    else if (req.method === "HEAD") parts.push("-I");
    parts.push(util.shellQuote(req.url));
    for (const [k, v] of req.headers) parts.push("-H " + util.shellQuote(k + ": " + v));
    if (req.body) parts.push("--data-raw " + util.shellQuote(req.body));
    return parts.join(multiline ? " \\\n  " : " ");
  }

  function matchBalanced(text, start) {
    const open = text[start];
    const close = { "{": "}", "(": ")", "[": "]" }[open];
    let depth = 0;
    let quote = null;
    for (let i = start; i < text.length; i += 1) {
      const c = text[i];
      if (quote) {
        if (c === "\\") i += 1;
        else if (c === quote) quote = null;
        continue;
      }
      if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === open) depth += 1;
      else if (c === close) {
        depth -= 1;
        if (depth === 0) return text.slice(start, i + 1);
      }
    }
    throw new Error("Unbalanced " + open + " in the fetch call.");
  }

  function relaxedJson(text) {
    const converted = String(text)
      .replace(/'((?:\\.|[^'\\])*)'/g, (m, inner) => JSON.stringify(inner.replace(/\\'/g, "'")))
      .replace(/([{,]\s*)([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":')
      .replace(/,\s*([}\]])/g, "$1");
    return JSON.parse(converted);
  }

  function parseFetch(code) {
    const text = String(code);
    const at = text.indexOf("fetch(");
    if (at < 0) throw new Error("No fetch( call found.");
    const urlMatch = /fetch\(\s*(["'`])((?:\\.|(?!\1).)*)\1/.exec(text.slice(at));
    if (!urlMatch) throw new Error("The first fetch argument must be a string URL.");
    const req = { method: "GET", url: urlMatch[2], headers: [], body: "", form: [] };
    const afterUrl = text.indexOf(urlMatch[0], at) + urlMatch[0].length;
    const brace = text.indexOf("{", afterUrl);
    const closeParen = text.indexOf(")", afterUrl);
    if (brace < 0 || (closeParen >= 0 && closeParen < brace)) return req;
    const options = matchBalanced(text, brace);
    const method = /method\s*:\s*["'`](\w+)["'`]/i.exec(options);
    if (method) req.method = method[1].toUpperCase();
    const headersAt = options.search(/headers\s*:\s*\{/);
    if (headersAt >= 0) {
      const block = matchBalanced(options, options.indexOf("{", headersAt));
      const pair = /["'`]?([\w-]+)["'`]?\s*:\s*(["'`])((?:\\.|(?!\2).)*)\2/g;
      let m;
      while ((m = pair.exec(block))) req.headers.push([m[1], m[3]]);
    }
    const stringify = /body\s*:\s*JSON\.stringify\(/.exec(options);
    if (stringify) {
      const inner = matchBalanced(options, stringify.index + stringify[0].length - 1).slice(1, -1).trim();
      try {
        req.body = JSON.stringify(relaxedJson(inner));
      } catch (error) {
        req.body = inner;
      }
    } else {
      const bodyString = /body\s*:\s*(["'`])((?:\\.|(?!\1)[\s\S])*)\1/.exec(options);
      if (bodyString) req.body = bodyString[2].replace(/\\(["'`\\])/g, "$1").replace(/\\n/g, "\n");
    }
    if (req.body && req.method === "GET") req.method = "POST";
    return req;
  }

  /* JSON Schema inference */

  function detectFormat(value) {
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/.test(value)) return "date-time";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return "date";
    if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) return "email";
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return "uuid";
    if (/^https?:\/\/\S+$/.test(value)) return "uri";
    if (/^(\d{1,3}\.){3}\d{1,3}$/.test(value)) return "ipv4";
    return null;
  }

  function inferSchema(value, opts) {
    if (value === null) return { type: "null" };
    if (Array.isArray(value)) {
      if (!value.length) return { type: "array", items: {} };
      return { type: "array", items: mergeSchemas(value.map((item) => inferSchema(item, opts)), opts) };
    }
    if (typeof value === "object") {
      const properties = {};
      for (const [key, child] of Object.entries(value)) properties[key] = inferSchema(child, opts);
      const schema = { type: "object", properties };
      if (opts.required) schema.required = Object.keys(value);
      if (opts.strict) schema.additionalProperties = false;
      return schema;
    }
    if (typeof value === "number") return { type: Number.isInteger(value) ? "integer" : "number" };
    if (typeof value === "boolean") return { type: "boolean" };
    const format = opts.formats ? detectFormat(value) : null;
    return format ? { type: "string", format } : { type: "string" };
  }

  function mergeSchemas(schemas, opts) {
    const types = [...new Set(schemas.map((s) => s.type))];
    if (types.length === 2 && types.includes("integer") && types.includes("number")) return { type: "number" };
    if (types.length > 1) {
      const nonNull = schemas.filter((s) => s.type !== "null");
      if (nonNull.length && types.length === 2 && types.includes("null")) {
        const merged = mergeSchemas(nonNull, opts);
        return { ...merged, type: [merged.type, "null"] };
      }
      return { anyOf: types.map((t) => schemas.find((s) => s.type === t)) };
    }
    const first = schemas[0];
    if (first.type === "object") {
      const properties = {};
      const counts = {};
      for (const schema of schemas) {
        for (const [key, child] of Object.entries(schema.properties || {})) {
          counts[key] = (counts[key] || 0) + 1;
          properties[key] = properties[key] ? mergeSchemas([properties[key], child], opts) : child;
        }
      }
      const merged = { type: "object", properties };
      if (opts.required) merged.required = Object.keys(counts).filter((k) => counts[k] === schemas.length);
      if (opts.strict) merged.additionalProperties = false;
      return merged;
    }
    if (first.type === "array") return { type: "array", items: mergeSchemas(schemas.map((s) => s.items || {}).filter((s) => s.type || s.anyOf), opts) || {} };
    const formats = [...new Set(schemas.map((s) => s.format || null))];
    return formats.length === 1 && formats[0] ? { type: first.type, format: formats[0] } : { type: first.type };
  }

  /* OpenAPI helpers */

  function resolveRef(root, ref) {
    if (typeof ref !== "string" || !ref.startsWith("#/")) return undefined;
    return ref.slice(2).split("/").reduce((node, part) => (node == null ? undefined : node[part.replace(/~1/g, "/").replace(/~0/g, "~")]), root);
  }

  function deref(root, node) {
    let current = node;
    let guard = 0;
    while (current && current.$ref && guard < 20) {
      current = resolveRef(root, current.$ref);
      guard += 1;
    }
    return current;
  }

  function sampleFromSchema(schema, root, depth) {
    const level = depth || 0;
    const node = deref(root, schema) || {};
    if (level > 8) return null;
    if (node.example !== undefined) return node.example;
    if (Array.isArray(node.examples) && node.examples.length) return node.examples[0];
    if (node.default !== undefined) return node.default;
    if (node.const !== undefined) return node.const;
    if (Array.isArray(node.enum) && node.enum.length) return node.enum[0];
    if (node.allOf) return Object.assign({}, ...node.allOf.map((s) => sampleFromSchema(s, root, level + 1)).filter(util.isPlainObject));
    if (node.oneOf || node.anyOf) return sampleFromSchema((node.oneOf || node.anyOf)[0], root, level + 1);
    const type = Array.isArray(node.type) ? node.type.find((t) => t !== "null") : node.type || (node.properties ? "object" : node.items ? "array" : "string");
    if (type === "object") {
      const out = {};
      for (const [key, child] of Object.entries(node.properties || {})) out[key] = sampleFromSchema(child, root, level + 1);
      return out;
    }
    if (type === "array") return [sampleFromSchema(node.items || {}, root, level + 1)];
    if (type === "integer") return node.minimum !== undefined ? node.minimum : 0;
    if (type === "number") return node.minimum !== undefined ? node.minimum : 0;
    if (type === "boolean") return true;
    if (type === "null") return null;
    const formats = { "date-time": new Date(0).toISOString(), date: "1970-01-01", email: "user@example.com", uuid: "00000000-0000-4000-8000-000000000000", uri: "https://example.com", ipv4: "192.0.2.1" };
    return formats[node.format] || "string";
  }
  util.sampleFromSchema = sampleFromSchema;
  util.inferSchema = inferSchema;
  util.resolveRef = resolveRef;

  const METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"];

  function operations(spec) {
    const out = [];
    for (const [path, item] of Object.entries(spec.paths || {})) {
      if (!util.isPlainObject(item)) continue;
      const pathItem = deref(spec, item) || item;
      for (const method of METHODS) {
        if (pathItem[method]) out.push({ path, method, op: pathItem[method], pathItem });
      }
    }
    return out;
  }

  function serverUrl(spec) {
    if (spec.swagger) return (spec.schemes ? spec.schemes[0] : "https") + "://" + (spec.host || "localhost") + (spec.basePath || "");
    const server = (spec.servers || [])[0];
    if (!server || !server.url) return "";
    let url = server.url;
    for (const [name, variable] of Object.entries(server.variables || {})) url = url.replace("{" + name + "}", variable.default);
    return url;
  }

  const SAMPLE_OPENAPI = JSON.stringify({
    openapi: "3.0.3",
    info: { title: "Viewlift Content API", version: "1.2.0" },
    servers: [{ url: "https://api.example.com/v1" }],
    tags: [{ name: "content" }, { name: "user" }],
    paths: {
      "/content/{id}": {
        get: {
          tags: ["content"],
          operationId: "getContent",
          summary: "Fetch one video",
          parameters: [{ name: "id", in: "path", required: true, schema: { type: "string" } }, { name: "lang", in: "query", schema: { type: "string", default: "en" } }],
          responses: { 200: { description: "Video", content: { "application/json": { schema: { $ref: "#/components/schemas/Video" } } } }, 404: { description: "Not found" } },
        },
      },
      "/user/history": {
        post: {
          tags: ["user"],
          operationId: "addHistory",
          summary: "Save watch progress",
          security: [{ bearer: [] }],
          requestBody: { required: true, content: { "application/json": { schema: { type: "object", properties: { videoId: { type: "string" }, position: { type: "integer", example: 120 } } } } } },
          responses: { 204: { description: "Saved" } },
        },
      },
    },
    components: {
      securitySchemes: { bearer: { type: "http", scheme: "bearer" } },
      schemas: { Video: { type: "object", required: ["id", "title"], properties: { id: { type: "string" }, title: { type: "string" }, duration: { type: "integer" }, hls: { type: "string", format: "uri" } } } },
    },
  }, null, 2);

  const SAMPLE_POSTMAN = JSON.stringify({
    info: { name: "Viewlift API", schema: "https://schema.getpostman.com/json/collection/v2.1.0/collection.json" },
    variable: [{ key: "baseUrl", value: "https://api.example.com/v1" }],
    auth: { type: "bearer", bearer: [{ key: "token", value: "{{token}}", type: "string" }] },
    item: [
      {
        name: "Content",
        item: [
          { name: "Get content", request: { method: "GET", url: { raw: "{{baseUrl}}/content/:id?lang=en", host: ["{{baseUrl}}"], path: ["content", ":id"], query: [{ key: "lang", value: "en" }], variable: [{ key: "id", value: "vl-123" }] } } },
        ],
      },
      { name: "Save history", request: { method: "POST", header: [{ key: "Content-Type", value: "application/json" }], body: { mode: "raw", raw: '{"videoId":"vl-123","position":120}', options: { raw: { language: "json" } } }, url: "{{baseUrl}}/user/history" } },
    ],
  }, null, 2);

  function postmanUrl(url) {
    if (typeof url === "string") {
      const raw = url;
      const [base, query = ""] = raw.split("?");
      const match = /^(\{\{[^}]+\}\}|[a-z]+:\/\/[^/]+)?(.*)$/i.exec(base);
      return {
        host: match[1] || "",
        path: (match[2] || "").split("/").filter(Boolean),
        query: query ? query.split("&").map((pair) => {
          const [key, value = ""] = pair.split("=");
          return { key: decodeURIComponent(key), value: decodeURIComponent(value) };
        }) : [],
        variable: [],
      };
    }
    const host = Array.isArray(url.host) ? url.host.join(".") : url.host || "";
    const protocol = url.protocol ? url.protocol + "://" : "";
    return {
      host: protocol + host + (url.port ? ":" + url.port : ""),
      path: Array.isArray(url.path) ? url.path : String(url.path || "").split("/").filter(Boolean),
      query: (url.query || []).filter((q) => !q.disabled),
      variable: url.variable || [],
    };
  }

  function camel(text) {
    const parts = String(text).replace(/[^A-Za-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
    return parts.map((p, i) => (i ? p.charAt(0).toUpperCase() + p.slice(1).toLowerCase() : p.toLowerCase())).join("") || "operation";
  }

  function exampleSchema(value) {
    return inferSchema(value, { required: false, strict: false, formats: true });
  }

  function postmanToOpenApi(collection, version) {
    if (!collection || !collection.info || !Array.isArray(collection.item)) throw new Error("This is not a Postman collection (needs info and item).");
    const variables = Object.fromEntries((collection.variable || []).map((v) => [v.key, v.value]));
    const spec = {
      openapi: version,
      info: { title: collection.info.name || "Converted collection", version: (collection.info.version && String(collection.info.version.major !== undefined ? [collection.info.version.major, collection.info.version.minor, collection.info.version.patch].join(".") : collection.info.version)) || "1.0.0" },
      servers: [],
      tags: [],
      paths: {},
      components: { securitySchemes: {} },
    };
    if (collection.info.description) spec.info.description = typeof collection.info.description === "string" ? collection.info.description : collection.info.description.content;
    const servers = new Set();
    const usedIds = new Set();
    const warnings = [];
    const addAuth = (auth) => {
      if (!auth || !auth.type || auth.type === "noauth") return null;
      if (auth.type === "bearer") spec.components.securitySchemes.bearerAuth = { type: "http", scheme: "bearer" };
      else if (auth.type === "basic") spec.components.securitySchemes.basicAuth = { type: "http", scheme: "basic" };
      else if (auth.type === "apikey") {
        const fields = Object.fromEntries((auth.apikey || []).map((f) => [f.key, f.value]));
        spec.components.securitySchemes.apiKeyAuth = { type: "apiKey", name: fields.key || "X-API-Key", in: fields.in === "query" ? "query" : "header" };
      } else if (auth.type === "oauth2") spec.components.securitySchemes.oauth2 = { type: "oauth2", flows: { clientCredentials: { tokenUrl: "https://example.com/oauth/token", scopes: {} } } };
      else {
        warnings.push("Auth type " + auth.type + " has no OpenAPI equivalent and was skipped.");
        return null;
      }
      return { bearer: "bearerAuth", basic: "basicAuth", apikey: "apiKeyAuth", oauth2: "oauth2" }[auth.type];
    };
    const collectionAuth = addAuth(collection.auth);
    if (collectionAuth) spec.security = [{ [collectionAuth]: [] }];
    const walk = (items, tag) => {
      for (const item of items) {
        if (Array.isArray(item.item)) {
          if (!spec.tags.some((t) => t.name === item.name)) spec.tags.push({ name: item.name, ...(item.description ? { description: typeof item.description === "string" ? item.description : item.description.content } : {}) });
          walk(item.item, item.name);
          continue;
        }
        const request = typeof item.request === "string" ? { method: "GET", url: item.request } : item.request;
        if (!request) continue;
        const url = postmanUrl(request.url || "");
        if (url.host) {
          const hostValue = url.host.replace(/\{\{([^}]+)\}\}/g, (m, name) => variables[name] || m);
          servers.add(hostValue);
        }
        const parameters = [];
        const segments = url.path.map((segment) => {
          if (segment.startsWith(":")) {
            const name = segment.slice(1);
            const variable = url.variable.find((v) => v.key === name);
            parameters.push({ name, in: "path", required: true, schema: { type: "string" }, ...(variable && variable.value ? { example: variable.value } : {}) });
            return "{" + name + "}";
          }
          return segment.replace(/\{\{([^}]+)\}\}/g, (m, name) => {
            parameters.push({ name, in: "path", required: true, schema: { type: "string" } });
            return "{" + name + "}";
          });
        });
        const path = "/" + segments.join("/");
        for (const q of url.query) parameters.push({ name: q.key, in: "query", schema: { type: "string" }, ...(q.value ? { example: q.value } : {}), ...(q.description ? { description: typeof q.description === "string" ? q.description : q.description.content } : {}) });
        for (const h of request.header || []) {
          if (h.disabled || /^(content-type|authorization|accept)$/i.test(h.key)) continue;
          parameters.push({ name: h.key, in: "header", schema: { type: "string" }, ...(h.value ? { example: h.value } : {}) });
        }
        const method = String(request.method || "GET").toLowerCase();
        let operationId = camel(item.name);
        while (usedIds.has(operationId)) operationId += "_";
        usedIds.add(operationId);
        const op = { summary: item.name, operationId, ...(tag ? { tags: [tag] } : {}), responses: {} };
        if (request.description) op.description = typeof request.description === "string" ? request.description : request.description.content;
        if (parameters.length) op.parameters = parameters;
        const body = request.body;
        if (body && body.mode === "raw" && body.raw) {
          const contentType = ((request.header || []).find((h) => /^content-type$/i.test(h.key)) || {}).value || (body.options && body.options.raw && body.options.raw.language === "json" ? "application/json" : "text/plain");
          let example = body.raw;
          try {
            example = JSON.parse(body.raw);
          } catch (error) {
            /* keep raw text */
          }
          op.requestBody = { content: { [contentType]: { schema: typeof example === "string" ? { type: "string" } : exampleSchema(example), example } } };
        } else if (body && (body.mode === "urlencoded" || body.mode === "formdata")) {
          const fields = (body[body.mode] || []).filter((f) => !f.disabled);
          const properties = Object.fromEntries(fields.map((f) => [f.key, f.type === "file" ? { type: "string", format: "binary" } : { type: "string", ...(f.value ? { example: f.value } : {}) }]));
          op.requestBody = { content: { [body.mode === "urlencoded" ? "application/x-www-form-urlencoded" : "multipart/form-data"]: { schema: { type: "object", properties } } } };
        } else if (body && body.mode === "graphql") {
          op.requestBody = { content: { "application/json": { schema: { type: "object", properties: { query: { type: "string" }, variables: { type: "object" } } }, example: { query: body.graphql.query, variables: body.graphql.variables } } } };
        }
        const requestAuth = addAuth(request.auth);
        if (requestAuth) op.security = [{ [requestAuth]: [] }];
        else if (request.auth && request.auth.type === "noauth") op.security = [];
        for (const response of item.response || []) {
          const code = String(response.code || 200);
          let example = response.body;
          try {
            example = JSON.parse(response.body);
          } catch (error) {
            /* raw */
          }
          op.responses[code] = { description: response.name || response.status || "Response", ...(response.body ? { content: { "application/json": { schema: typeof example === "string" ? { type: "string" } : exampleSchema(example), example } } } : {}) };
        }
        if (!Object.keys(op.responses).length) op.responses["200"] = { description: "Successful response" };
        spec.paths[path] = spec.paths[path] || {};
        if (spec.paths[path][method]) warnings.push(method.toUpperCase() + " " + path + " appears twice. The later request won.");
        spec.paths[path][method] = op;
      }
    };
    walk(collection.item, null);
    spec.servers = [...servers].map((url) => ({ url }));
    if (!spec.servers.length) delete spec.servers;
    if (!spec.tags.length) delete spec.tags;
    if (!Object.keys(spec.components.securitySchemes).length) delete spec.components;
    return { spec, warnings };
  }

  function openApiToPostman(spec) {
    if (!spec || (!spec.openapi && !spec.swagger)) throw new Error("This is not an OpenAPI document (needs an openapi or swagger field).");
    const folders = new Map();
    const loose = [];
    for (const { path, method, op, pathItem } of operations(spec)) {
      const params = [...(pathItem.parameters || []), ...(op.parameters || [])].map((p) => deref(spec, p)).filter(Boolean);
      const query = params.filter((p) => p.in === "query").map((p) => ({ key: p.name, value: String(p.example ?? (p.schema ? sampleFromSchema(p.schema, spec) : "") ?? ""), ...(p.required ? {} : { disabled: true }), ...(p.description ? { description: p.description } : {}) }));
      const header = params.filter((p) => p.in === "header").map((p) => ({ key: p.name, value: String(p.example ?? "") }));
      const variable = params.filter((p) => p.in === "path").map((p) => ({ key: p.name, value: String(p.example ?? "") }));
      const segments = path.split("/").filter(Boolean).map((s) => s.replace(/^\{(.+)\}$/, ":$1"));
      const raw = "{{baseUrl}}/" + segments.join("/") + (query.length ? "?" + query.filter((q) => !q.disabled).map((q) => q.key + "=" + q.value).join("&") : "");
      const request = { method: method.toUpperCase(), header, url: { raw, host: ["{{baseUrl}}"], path: segments, ...(query.length ? { query } : {}), ...(variable.length ? { variable } : {}) } };
      const bodyDef = deref(spec, op.requestBody);
      if (bodyDef && bodyDef.content) {
        const [type, media] = Object.entries(bodyDef.content)[0];
        const example = media.example !== undefined ? media.example : media.schema ? sampleFromSchema(media.schema, spec) : "";
        header.push({ key: "Content-Type", value: type });
        request.body = type.includes("json")
          ? { mode: "raw", raw: JSON.stringify(example, null, 2), options: { raw: { language: "json" } } }
          : type.includes("x-www-form-urlencoded")
            ? { mode: "urlencoded", urlencoded: Object.entries(util.isPlainObject(example) ? example : {}).map(([k, v]) => ({ key: k, value: String(v) })) }
            : { mode: "raw", raw: typeof example === "string" ? example : JSON.stringify(example) };
      }
      if (op.description) request.description = op.description;
      const item = { name: op.summary || op.operationId || method.toUpperCase() + " " + path, request, response: [] };
      const tag = (op.tags || [])[0];
      if (tag) {
        if (!folders.has(tag)) folders.set(tag, []);
        folders.get(tag).push(item);
      } else loose.push(item);
    }
    const collection = {
      info: { _postman_id: util.uuidV4(), name: (spec.info && spec.info.title) || "OpenAPI import", ...(spec.info && spec.info.description ? { description: spec.info.description } : {}), schema: "https://schema.getpostman.com/json/collection/v2.1.0/collection.json" },
      item: [...[...folders.entries()].map(([name, item]) => ({ name, item })), ...loose],
      variable: [{ key: "baseUrl", value: serverUrl(spec) || "https://example.com", type: "string" }],
    };
    const schemes = Object.values((spec.components && spec.components.securitySchemes) || spec.securityDefinitions || {});
    if (schemes.some((s) => s.type === "http" && s.scheme === "bearer") || schemes.some((s) => s.type === "oauth2")) {
      collection.auth = { type: "bearer", bearer: [{ key: "token", value: "{{token}}", type: "string" }] };
      collection.variable.push({ key: "token", value: "", type: "string" });
    } else if (schemes.some((s) => s.type === "apiKey")) {
      const s = schemes.find((x) => x.type === "apiKey");
      collection.auth = { type: "apikey", apikey: [{ key: "key", value: s.name }, { key: "value", value: "{{apiKey}}" }, { key: "in", value: s.in === "query" ? "query" : "header" }] };
      collection.variable.push({ key: "apiKey", value: "", type: "string" });
    }
    return collection;
  }

  function validateOpenApi(spec) {
    const issues = [];
    const add = (level, where, message) => issues.push([level, where, message]);
    if (!util.isPlainObject(spec)) {
      add("error", "(root)", "The document must be an object.");
      return issues;
    }
    const isSwagger = spec.swagger !== undefined;
    if (isSwagger) {
      if (spec.swagger !== "2.0") add("error", "swagger", "swagger must be \"2.0\".");
      add("warn", "swagger", "Swagger 2.0 is legacy. Consider converting to OpenAPI 3.");
    } else if (typeof spec.openapi !== "string" || !/^3\.[0-2]\.\d+/.test(spec.openapi)) {
      add("error", "openapi", "openapi must be a version string such as \"3.0.3\" or \"3.1.0\".");
    }
    if (!util.isPlainObject(spec.info)) add("error", "info", "info is required.");
    else {
      if (typeof spec.info.title !== "string" || !spec.info.title) add("error", "info.title", "title is required.");
      if (typeof spec.info.version !== "string") add("error", "info.version", "version is required and must be a string (quote numbers in YAML).");
    }
    const v31 = /^3\.[12]/.test(spec.openapi || "");
    if (!spec.paths && !(v31 && (spec.webhooks || spec.components))) add("error", "paths", "paths is required.");
    if (!isSwagger && Array.isArray(spec.servers)) spec.servers.forEach((s, i) => !s.url && add("error", "servers[" + i + "]", "url is required."));
    if (!isSwagger && !spec.servers) add("warn", "servers", "No servers. Clients will assume \"/\".");
    const ids = new Map();
    const declaredTags = new Set((spec.tags || []).map((t) => t.name));
    for (const [path, rawItem] of Object.entries(spec.paths || {})) {
      if (!path.startsWith("/")) add("error", "paths." + path, "Path keys must start with /.");
      const item = deref(spec, rawItem);
      if (!util.isPlainObject(item)) {
        add("error", "paths." + path, "Path item must be an object.");
        continue;
      }
      for (const key of Object.keys(item)) {
        if (!METHODS.includes(key) && !["parameters", "summary", "description", "servers", "$ref"].includes(key) && !key.startsWith("x-")) add("warn", "paths." + path, "Unknown key '" + key + "'.");
      }
      const templateParams = [...path.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
      for (const method of METHODS) {
        const op = item[method];
        if (!op) continue;
        const where = method.toUpperCase() + " " + path;
        if (op.operationId) {
          if (ids.has(op.operationId)) add("error", where, "operationId '" + op.operationId + "' is also used by " + ids.get(op.operationId) + ".");
          ids.set(op.operationId, where);
        } else add("warn", where, "No operationId. Code generators will invent one.");
        const params = [...(item.parameters || []), ...(op.parameters || [])].map((p) => deref(spec, p));
        const seen = new Set();
        params.forEach((p, i) => {
          if (!p) {
            add("error", where, "parameters[" + i + "] has a broken $ref.");
            return;
          }
          if (!p.name || !p.in) add("error", where, "Each parameter needs name and in.");
          else if (!["query", "header", "path", "cookie", "body", "formData"].includes(p.in)) add("error", where, "Parameter '" + p.name + "' has invalid in: " + p.in + ".");
          if (p.in === "path" && p.required !== true) add("error", where, "Path parameter '" + p.name + "' must have required: true.");
          if (p.in === "path" && !templateParams.includes(p.name)) add("error", where, "Path parameter '" + p.name + "' is not in the path template.");
          if (!isSwagger && !p.schema && !p.content) add("warn", where, "Parameter '" + p.name + "' has no schema.");
          const key = p.in + ":" + p.name;
          if (seen.has(key)) add("error", where, "Parameter '" + p.name + "' is listed twice.");
          seen.add(key);
        });
        for (const name of templateParams) {
          if (!params.some((p) => p && p.in === "path" && p.name === name)) add("error", where, "Path template {" + name + "} has no matching path parameter.");
        }
        if (!util.isPlainObject(op.responses) || !Object.keys(op.responses).length) add("error", where, "responses is required and must not be empty.");
        else {
          for (const [code, raw] of Object.entries(op.responses)) {
            if (code !== "default" && !/^[1-5](\d\d|XX)$/.test(code)) add("error", where, "Response key '" + code + "' must be a status code, 1XX-5XX, or default.");
            const response = deref(spec, raw);
            if (!response) add("error", where, "Response " + code + " has a broken $ref.");
            else if (typeof response.description !== "string") add("error", where, "Response " + code + " needs a description.");
          }
          if (!Object.keys(op.responses).some((c) => /^2/.test(c))) add("warn", where, "No 2xx response.");
        }
        if (op.requestBody) {
          const body = deref(spec, op.requestBody);
          if (!body || !util.isPlainObject(body.content) || !Object.keys(body.content).length) add("error", where, "requestBody needs content.");
        }
        if (["get", "head", "delete"].includes(method) && op.requestBody) add("warn", where, "A request body on " + method.toUpperCase() + " is ignored by many clients.");
        for (const tag of op.tags || []) if (declaredTags.size && !declaredTags.has(tag)) add("warn", where, "Tag '" + tag + "' is not declared in tags.");
        for (const requirement of op.security || []) {
          for (const name of Object.keys(requirement)) {
            const schemes = (spec.components && spec.components.securitySchemes) || spec.securityDefinitions || {};
            if (!schemes[name]) add("error", where, "Security scheme '" + name + "' is not defined.");
          }
        }
      }
    }
    const refs = [];
    const collect = (node, at) => {
      if (Array.isArray(node)) node.forEach((child, i) => collect(child, at + "[" + i + "]"));
      else if (util.isPlainObject(node)) {
        for (const [key, child] of Object.entries(node)) {
          if (key === "$ref" && typeof child === "string") refs.push([at, child]);
          else collect(child, at ? at + "." + key : key);
        }
      }
    };
    collect(spec, "");
    for (const [at, ref] of refs) {
      if (ref.startsWith("#/") && resolveRef(spec, ref) === undefined) add("error", at || "(root)", "$ref " + ref + " does not resolve.");
      else if (!ref.startsWith("#")) add("warn", at, "External $ref " + ref + " was not checked.");
    }
    return issues;
  }

  async function loadSpec(ctx, text) {
    return util.parseJsonOrYaml(ctx, text);
  }

  async function emit(ctx, data, format, name) {
    if (format === "yaml") return { type: "text", label: name + " (YAML)", value: await util.yamlDump(ctx, data), download: name.toLowerCase().replace(/\s+/g, "-") + ".yaml" };
    return { type: "text", label: name + " (JSON)", value: JSON.stringify(data, null, 2), download: name.toLowerCase().replace(/\s+/g, "-") + ".json", mime: "application/json" };
  }

  register("http", [
    {
      slug: "http-status-code-explorer",
      name: "HTTP status code explorer",
      description: "Search HTTP status codes, plus common URLSession (NSURLError) codes.",
      keywords: "status code 404 500 nsurlerror urlsession error",
      inputs: [{ id: "query", label: "Search", type: "text", value: "", placeholder: "404, redirect, timeout, -1200", wide: true }],
      run(v) {
        const q = String(v.query || "").trim().toLowerCase();
        const rows = STATUS.filter(([code, name, text]) => !q || String(code).startsWith(q) || name.toLowerCase().includes(q) || text.toLowerCase().includes(q) || (q.length === 3 && /^\dxx$/.test(q) && String(code)[0] === q[0]));
        return { type: "table", label: rows.length + " codes", columns: ["Code", "Name", "Meaning"], rows, rowClass: (row) => (row[0] >= 500 || row[0] < 0 ? "err" : row[0] >= 400 ? "warn" : "") };
      },
    },
    {
      slug: "http-header-viewer",
      name: "HTTP header viewer",
      description: "Paste raw request or response headers and see what each one does.",
      keywords: "headers inspect explain request response",
      inputs: [{ id: "text", label: "Headers", type: "textarea", rows: 12, value: "HTTP/2 200\ncontent-type: application/vnd.apple.mpegurl\ncache-control: max-age=2, public\nage: 1\nx-cache: Hit from cloudfront\nx-amz-cf-pop: BOM78-P1\naccess-control-allow-origin: *\nvary: Origin\nstrict-transport-security: max-age=31536000" }],
      run(v) {
        const rows = util.parseHeaderLines(v.text).map(([name, value]) => {
          const info = HEADERS[name.toLowerCase()];
          return [name, value, info ? info[0] : name.toLowerCase().startsWith("x-") ? "custom" : "?", info ? info[1] : "Custom or uncommon header."];
        });
        return { type: "table", label: rows.length + " headers", columns: ["Header", "Value", "Used in", "What it does"], rows, empty: "Paste Name: value lines." };
      },
    },
    {
      slug: "rest-request-builder",
      name: "REST request builder",
      description: "Send an HTTP request from this Mac and inspect the status, headers, timing and body.",
      keywords: "http client postman api test request send",
      live: false,
      action: "Send request",
      inputs: [
        { id: "method", label: "Method", type: "select", options: ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], value: "GET" },
        { id: "url", label: "URL", type: "text", value: "https://httpbin.org/get?source=viewlift", wide: true },
        { id: "headers", label: "Headers (Name: value per line)", type: "textarea", rows: 4, value: "Accept: application/json" },
        { id: "body", label: "Body", type: "textarea", rows: 6, value: "", show: (v) => !["GET", "HEAD"].includes(v.method) },
        { id: "timeout", label: "Timeout (s)", type: "number", value: 20, min: 1, max: 60 },
        { id: "follow", label: "Follow redirects", type: "checkbox", value: true },
      ],
      async run(v, ctx) {
        const headers = parseHeaderText(v.headers);
        const result = await ctx.server("http-request", { method: v.method, url: String(v.url).trim(), headers, body: ["GET", "HEAD"].includes(v.method) ? "" : v.body, timeout: Number(v.timeout) || 20, follow_redirects: Boolean(v.follow) });
        let body = result.text;
        if (body !== undefined) {
          try {
            body = JSON.stringify(JSON.parse(body), null, 2);
          } catch (error) {
            /* not JSON */
          }
        }
        const level = result.status >= 500 ? "error" : result.status >= 400 ? "warn" : "ok";
        return [
          { type: "note", level, text: result.status + " " + result.reason + " in " + result.ms + " ms, " + util.formatBytes(result.size) + (result.truncated ? " (truncated at 2 MB)" : "") + "." },
          result.url !== String(v.url).trim() ? { type: "note", text: "Final URL after redirects: " + result.url } : null,
          { type: "table", label: "Response headers", columns: ["Header", "Value"], rows: result.headers },
          body !== undefined ? { type: "text", label: "Body", value: body } : { type: "note", level: "warn", text: "Binary body (" + util.formatBytes(result.size) + "). Base64 starts: " + String(result.base64).slice(0, 120) },
          { type: "text", label: "As cURL", value: toCurl({ method: v.method, url: String(v.url).trim(), headers: Object.entries(headers), body: ["GET", "HEAD"].includes(v.method) ? "" : v.body }, true) },
        ];
      },
    },
    {
      slug: "curl-generator",
      name: "cURL generator",
      description: "Build a cURL command from a method, URL, headers and body.",
      keywords: "curl command build",
      inputs: [
        { id: "method", label: "Method", type: "select", options: ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], value: "POST" },
        { id: "url", label: "URL", type: "text", value: "https://api.example.com/v1/user/history", wide: true },
        { id: "headers", label: "Headers", type: "textarea", rows: 4, value: "Content-Type: application/json\nAuthorization: Bearer <token>" },
        { id: "body", label: "Body", type: "textarea", rows: 5, value: '{"videoId":"vl-123","position":120}' },
        { id: "multiline", label: "One option per line", type: "checkbox", value: true },
        { id: "extra", label: "Add -sS --compressed", type: "checkbox", value: false },
      ],
      run(v) {
        const req = { method: v.method, url: v.url, headers: util.parseHeaderLines(v.headers), body: ["GET", "HEAD"].includes(v.method) ? "" : v.body };
        let curl = toCurl(req, v.multiline);
        if (v.extra) curl = curl.replace(/^curl/, "curl -sS --compressed");
        return [{ type: "text", label: "cURL", value: curl }, { type: "text", label: "Swift URLSession", value: toSwift({ ...req, form: [] }) }];
      },
    },
    {
      slug: "curl-fetch-converter",
      name: "cURL ↔ Fetch converter",
      description: "Convert cURL to JavaScript fetch or Swift URLSession, and fetch back to cURL.",
      keywords: "curl fetch urlsession swift convert",
      inputs: [
        { id: "direction", label: "Direction", type: "select", options: [["curl", "cURL to fetch + URLSession"], ["fetch", "fetch to cURL"]], value: "curl" },
        { id: "text", label: "Input", type: "textarea", rows: 10, value: "curl -X POST 'https://api.example.com/v1/user/history' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Authorization: Bearer abc123' \\\n  --data-raw '{\"videoId\":\"vl-123\",\"position\":120}'" },
      ],
      run(v) {
        if (v.direction === "fetch") {
          const req = parseFetch(v.text);
          return [{ type: "text", label: "cURL", value: toCurl(req, true) }, { type: "text", label: "Swift URLSession", value: toSwift(req) }];
        }
        const req = parseCurl(v.text);
        const out = [{ type: "text", label: "JavaScript fetch", value: toFetch(req) }, { type: "text", label: "Swift URLSession", value: toSwift(req) }];
        if (req.flags.length) out.push({ type: "note", level: "warn", text: "Not converted: " + req.flags.join(" ") });
        return out;
      },
    },
    {
      slug: "json-schema-generator",
      name: "JSON Schema generator",
      description: "Infer a JSON Schema from sample JSON, including formats like date-time, uuid and uri.",
      keywords: "schema infer draft 2020 validation",
      inputs: [
        { id: "text", label: "Sample JSON", type: "textarea", rows: 12, value: '[{"id":"6f1c2b9e-2b4a-4c1e-9a8f-0c6f1d2a3b4c","title":"Match Highlights","duration":5400,"publishedAt":"2026-11-06T10:00:00Z","tags":["sports"],"rating":4.5},{"id":"0c6f1d2a-3b4c-4c1e-9a8f-6f1c2b9e2b4a","title":"Studio Show","duration":1800,"publishedAt":"2026-11-05T18:30:00Z","tags":[],"rating":null}]' },
        { id: "draft", label: "Draft", type: "select", options: [["https://json-schema.org/draft/2020-12/schema", "2020-12"], ["http://json-schema.org/draft-07/schema#", "draft-07"]], value: "https://json-schema.org/draft/2020-12/schema" },
        { id: "required", label: "Mark keys required", type: "checkbox", value: true },
        { id: "strict", label: "additionalProperties: false", type: "checkbox", value: false },
        { id: "formats", label: "Detect formats", type: "checkbox", value: true },
        { id: "title", label: "Title", type: "text", value: "Video" },
      ],
      run(v) {
        const schema = inferSchema(util.parseJSON(v.text), { required: v.required, strict: v.strict, formats: v.formats });
        const out = { $schema: v.draft, ...(v.title ? { title: v.title } : {}), ...schema };
        return { type: "text", label: "Schema", value: JSON.stringify(out, null, 2), download: "schema.json" };
      },
    },
    {
      slug: "openapi-viewer",
      name: "OpenAPI viewer",
      description: "List the endpoints, parameters and schemas in an OpenAPI or Swagger document (JSON or YAML).",
      keywords: "swagger openapi spec endpoints browse",
      inputs: [
        { id: "text", label: "OpenAPI (JSON or YAML)", type: "textarea", rows: 14, value: SAMPLE_OPENAPI },
        { id: "filter", label: "Filter endpoints", type: "text", value: "" },
      ],
      async run(v, ctx) {
        const spec = await loadSpec(ctx, v.text);
        if (!spec || (!spec.openapi && !spec.swagger)) throw new Error("This is not an OpenAPI document.");
        const q = String(v.filter || "").toLowerCase();
        const rows = operations(spec)
          .filter(({ path, method, op }) => !q || (method + " " + path + " " + (op.summary || "") + " " + (op.operationId || "") + " " + (op.tags || []).join(" ")).toLowerCase().includes(q))
          .map(({ path, method, op, pathItem }) => {
            const params = [...(pathItem.parameters || []), ...(op.parameters || [])].map((p) => deref(spec, p)).filter(Boolean);
            return [method.toUpperCase(), path, op.summary || "", op.operationId || "", (op.tags || []).join(", "), params.map((p) => p.name + (p.in ? " (" + p.in + ")" : "")).join(", "), Object.keys(op.responses || {}).join(" ")];
          });
        const schemas = Object.entries((spec.components && spec.components.schemas) || spec.definitions || {}).map(([name, schema]) => [name, schema.type || (schema.allOf ? "allOf" : schema.oneOf ? "oneOf" : ""), Object.keys(schema.properties || {}).join(", "), (schema.required || []).join(", ")]);
        const info = spec.info || {};
        return [
          { type: "kv", label: "API", rows: [["Title", info.title || ""], ["Version", info.version || ""], ["Spec", spec.openapi ? "OpenAPI " + spec.openapi : "Swagger " + spec.swagger], ["Server", serverUrl(spec) || "(none)"], ["Endpoints", rows.length], ["Schemas", schemas.length]] },
          { type: "table", label: "Endpoints", columns: ["Method", "Path", "Summary", "operationId", "Tags", "Parameters", "Responses"], rows },
          { type: "table", label: "Schemas", columns: ["Name", "Type", "Properties", "Required"], rows: schemas },
        ];
      },
    },
    {
      slug: "openapi-validator",
      name: "OpenAPI validator",
      description: "Check an OpenAPI 3.x or Swagger 2.0 document for structural errors and broken $refs.",
      keywords: "swagger lint validate spec",
      inputs: [{ id: "text", label: "OpenAPI (JSON or YAML)", type: "textarea", rows: 16, value: SAMPLE_OPENAPI.replace('"operationId": "addHistory"', '"operationId": "getContent"').replace('"$ref": "#/components/schemas/Video"', '"$ref": "#/components/schemas/Movie"') }],
      async run(v, ctx) {
        const spec = await loadSpec(ctx, v.text);
        const issues = validateOpenApi(spec);
        const errors = issues.filter((i) => i[0] === "error").length;
        const warnings = issues.length - errors;
        return [
          { type: "note", level: errors ? "error" : warnings ? "warn" : "ok", text: errors ? errors + " errors, " + warnings + " warnings." : warnings ? "No errors, " + warnings + " warnings." : "No problems found." },
          { type: "table", label: "Issues", columns: ["Level", "Where", "Problem"], rows: issues, rowClass: (row) => (row[0] === "error" ? "err" : "warn") },
        ];
      },
    },
    {
      slug: "postman-to-openapi",
      name: "Postman to OpenAPI",
      description: "Convert a Postman Collection (v2.0 or v2.1) into OpenAPI 3.0, 3.1 or 3.2.",
      keywords: "postman collection openapi convert swagger export",
      inputs: [
        { id: "text", label: "Postman collection JSON", type: "textarea", rows: 14, value: SAMPLE_POSTMAN },
        { id: "file", label: "Or open a collection file", type: "file", accept: ".json,application/json" },
        { id: "version", label: "OpenAPI version", type: "select", options: ["3.0.3", "3.1.0", "3.2.0"], value: "3.0.3" },
        { id: "format", label: "Output", type: "select", options: [["json", "JSON"], ["yaml", "YAML"]], value: "yaml" },
      ],
      async run(v, ctx) {
        const text = v.file ? util.fromUtf8(v.file.bytes) : v.text;
        const { spec, warnings } = postmanToOpenApi(util.parseJSON(text, "Collection"), v.version);
        const out = [await emit(ctx, spec, v.format, "OpenAPI")];
        out.unshift({ type: "note", level: warnings.length ? "warn" : "ok", text: operations(spec).length + " operations converted." + (warnings.length ? " " + warnings.join(" ") : "") });
        return out;
      },
    },
    {
      slug: "openapi-to-postman",
      name: "OpenAPI to Postman",
      description: "Turn an OpenAPI 3.x or Swagger 2.0 document into an importable Postman v2.1 collection.",
      keywords: "openapi postman collection import convert",
      inputs: [
        { id: "text", label: "OpenAPI (JSON or YAML)", type: "textarea", rows: 14, value: SAMPLE_OPENAPI },
        { id: "file", label: "Or open a spec file", type: "file", accept: ".json,.yaml,.yml" },
      ],
      async run(v, ctx) {
        const spec = await loadSpec(ctx, v.file ? util.fromUtf8(v.file.bytes) : v.text);
        const collection = openApiToPostman(spec);
        const count = collection.item.reduce((n, item) => n + (item.item ? item.item.length : 1), 0);
        return [
          { type: "note", level: "ok", text: count + " requests in " + collection.item.filter((i) => i.item).length + " folders." },
          { type: "text", label: "Postman collection", value: JSON.stringify(collection, null, 2), download: (collection.info.name || "collection").replace(/\W+/g, "-") + ".postman_collection.json" },
        ];
      },
    },
    {
      slug: "postman-collection-merger",
      name: "Postman collection merger",
      description: "Combine several Postman collections into one, as folders or merged by name.",
      keywords: "postman merge combine collections",
      inputs: [
        { id: "a", label: "Collection A", type: "textarea", rows: 8, value: SAMPLE_POSTMAN },
        { id: "b", label: "Collection B", type: "textarea", rows: 8, value: JSON.stringify({ info: { name: "Analytics", schema: "https://schema.getpostman.com/json/collection/v2.1.0/collection.json" }, variable: [{ key: "baseUrl", value: "https://beacon.example.com" }], item: [{ name: "Send beacon", request: { method: "POST", url: "{{baseUrl}}/event", body: { mode: "raw", raw: '{"event":"play"}' } } }] }, null, 2) },
        { id: "files", label: "More collection files", type: "files", accept: ".json" },
        { id: "mode", label: "Merge mode", type: "select", options: [["folders", "One folder per collection"], ["flat", "Merge items, same-name folders combined"]], value: "folders" },
        { id: "name", label: "Merged name", type: "text", value: "Merged collection" },
      ],
      run(v) {
        const sources = [];
        if (String(v.a || "").trim()) sources.push(util.parseJSON(v.a, "Collection A"));
        if (String(v.b || "").trim()) sources.push(util.parseJSON(v.b, "Collection B"));
        for (const file of v.files || []) sources.push(util.parseJSON(util.fromUtf8(file.bytes), file.name));
        if (sources.length < 2) throw new Error("Add at least two collections.");
        const merged = { info: { _postman_id: util.uuidV4(), name: v.name || "Merged collection", schema: "https://schema.getpostman.com/json/collection/v2.1.0/collection.json" }, item: [], variable: [] };
        const conflicts = [];
        const variables = new Map();
        for (const source of sources) {
          if (!source.info || !Array.isArray(source.item)) throw new Error("One input is not a Postman collection.");
          const name = source.info.name || "Collection";
          for (const variable of source.variable || []) {
            if (variables.has(variable.key) && variables.get(variable.key).value !== variable.value) {
              conflicts.push("Variable {{" + variable.key + "}} differs in " + name + "; kept the first value.");
            } else if (!variables.has(variable.key)) variables.set(variable.key, variable);
          }
          if (v.mode === "folders") {
            const folder = { name, item: source.item };
            if (source.auth) folder.auth = source.auth;
            if (source.event) folder.event = source.event;
            if (source.info.description) folder.description = source.info.description;
            merged.item.push(folder);
          } else {
            for (const item of source.item) {
              const existing = item.item && merged.item.find((i) => i.item && i.name === item.name);
              if (existing) existing.item.push(...item.item);
              else merged.item.push(item);
            }
            if (source.auth && !merged.auth) merged.auth = source.auth;
            if (source.event) merged.event = (merged.event || []).concat(source.event);
          }
        }
        merged.variable = [...variables.values()];
        return [
          { type: "note", level: conflicts.length ? "warn" : "ok", text: sources.length + " collections merged." + (conflicts.length ? " " + conflicts.join(" ") : "") },
          { type: "text", label: "Merged collection", value: JSON.stringify(merged, null, 2), download: "merged.postman_collection.json" },
        ];
      },
    },
    {
      slug: "graphql-formatter",
      name: "GraphQL formatter",
      description: "Indent GraphQL queries, mutations and fragments.",
      keywords: "graphql query mutation pretty",
      inputs: [
        { id: "text", label: "GraphQL", type: "textarea", rows: 12, value: 'query Home($id: ID!, $first: Int = 10){ page(id:$id){ title modules(first:$first){ edges{ node{ id ...Tile ... on Rail { items { id title } } } } } } } fragment Tile on Card { id title image(size: LARGE) @include(if: true) }' },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"]], value: "2" },
      ],
      run(v) {
        return { type: "text", label: "Formatted", value: util.formatBraces(v.text, { mode: "graphql", indent: " ".repeat(Number(v.indent)), hashComments: true, noSlashComments: true, blockStrings: true, blankBetweenBlocks: true }) };
      },
    },
  ]);
})();
