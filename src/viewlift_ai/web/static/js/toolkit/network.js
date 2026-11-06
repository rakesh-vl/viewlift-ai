/* Network & DNS */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  /* IP addresses as BigInt so IPv4 and IPv6 share one code path. */

  function parseIPv4(text) {
    const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(text);
    if (!m) return null;
    const octets = m.slice(1).map(Number);
    if (octets.some((o) => o > 255)) return null;
    return octets.reduce((acc, o) => (acc << 8n) + BigInt(o), 0n);
  }

  function parseIPv6(raw) {
    let text = raw.replace(/^\[|\]$/g, "").replace(/%.*$/, "").toLowerCase();
    if (!/^[0-9a-f:.]+$/.test(text) || (text.match(/::/g) || []).length > 1) return null;
    let tail = [];
    const v4 = /(\d{1,3}(?:\.\d{1,3}){3})$/.exec(text);
    if (v4) {
      const n = parseIPv4(v4[1]);
      if (n === null) return null;
      tail = [Number(n >> 16n), Number(n & 0xffffn)];
      text = text.slice(0, -v4[1].length).replace(/:$/, text.endsWith("::" + v4[1]) ? ":" : "");
      if (text === "") text = "::";
    }
    const [head, rest] = text.includes("::") ? text.split("::") : [text, null];
    const left = head ? head.split(":") : [];
    const right = rest ? rest.split(":") : [];
    if ([...left, ...right].some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
    const total = left.length + right.length + tail.length;
    if (rest === null ? total !== 8 : total > 7) return null;
    const groups = [...left.map((g) => parseInt(g, 16)), ...Array(8 - total).fill(0), ...right.map((g) => parseInt(g, 16)), ...tail];
    if (rest === null && groups.length !== 8) return null;
    return groups.reduce((acc, g) => (acc << 16n) + BigInt(g), 0n);
  }

  function parseIP(text) {
    const value = String(text || "").trim();
    const v4 = parseIPv4(value);
    if (v4 !== null) return { version: 4, bits: 32, value: v4 };
    const v6 = parseIPv6(value);
    if (v6 !== null) return { version: 6, bits: 128, value: v6 };
    return null;
  }

  function formatIP(version, value) {
    if (version === 4) return [24n, 16n, 8n, 0n].map((s) => String((value >> s) & 255n)).join(".");
    if (value >> 32n === 0xffffn) return "::ffff:" + formatIP(4, value & 0xffffffffn);
    const groups = [];
    for (let i = 7; i >= 0; i -= 1) groups.push(Number((value >> BigInt(i * 16)) & 0xffffn));
    let best = [-1, 0];
    for (let i = 0; i < 8;) {
      if (groups[i] !== 0) {
        i += 1;
        continue;
      }
      let j = i;
      while (j < 8 && groups[j] === 0) j += 1;
      if (j - i > best[1] && j - i > 1) best = [i, j - i];
      i = j;
    }
    const hex = groups.map((g) => g.toString(16));
    if (best[0] < 0) return hex.join(":");
    return hex.slice(0, best[0]).join(":") + "::" + hex.slice(best[0] + best[1]).join(":");
  }

  function parseCidr(text) {
    const [addr, prefixText] = String(text || "").trim().split("/");
    const ip = parseIP(addr);
    if (!ip) throw new Error("'" + addr + "' is not an IPv4 or IPv6 address.");
    const prefix = prefixText === undefined || prefixText === "" ? ip.bits : Number(prefixText);
    if (!Number.isInteger(prefix) || prefix < 0 || prefix > ip.bits) throw new Error("Prefix must be 0-" + ip.bits + ".");
    const size = 1n << BigInt(ip.bits - prefix);
    const network = (ip.value / size) * size;
    return { ...ip, prefix, size, network, last: network + size - 1n };
  }

  function maskOf(bits, prefix) {
    return ((1n << BigInt(bits)) - 1n) ^ ((1n << BigInt(bits - prefix)) - 1n);
  }

  function inCidr(ip, cidr) {
    return ip.version === cidr.version && ip.value >= cidr.network && ip.value <= cidr.last;
  }

  const RANGES = [
    ["0.0.0.0/8", "This network (unspecified)"],
    ["10.0.0.0/8", "Private (RFC 1918)"],
    ["17.0.0.0/8", "Apple Inc."],
    ["100.64.0.0/10", "Carrier-grade NAT (RFC 6598)"],
    ["127.0.0.0/8", "Loopback"],
    ["169.254.0.0/16", "Link-local (no DHCP lease)"],
    ["172.16.0.0/12", "Private (RFC 1918)"],
    ["192.0.0.0/24", "IETF protocol assignments"],
    ["192.0.2.0/24", "Documentation (TEST-NET-1)"],
    ["192.88.99.0/24", "6to4 relay (deprecated)"],
    ["192.168.0.0/16", "Private (RFC 1918)"],
    ["198.18.0.0/15", "Benchmarking"],
    ["198.51.100.0/24", "Documentation (TEST-NET-2)"],
    ["203.0.113.0/24", "Documentation (TEST-NET-3)"],
    ["224.0.0.251/32", "mDNS / Bonjour multicast"],
    ["224.0.0.0/4", "Multicast"],
    ["240.0.0.0/4", "Reserved"],
    ["255.255.255.255/32", "Limited broadcast"],
    ["::/128", "Unspecified"],
    ["::1/128", "Loopback"],
    ["::ffff:0:0/96", "IPv4-mapped"],
    ["64:ff9b::/96", "NAT64 (IPv6-only networks, as tested in App Review)"],
    ["2001::/32", "Teredo"],
    ["2001:db8::/32", "Documentation"],
    ["2002::/16", "6to4"],
    ["fc00::/7", "Unique local (private)"],
    ["fe80::/10", "Link-local"],
    ["ff02::fb/128", "mDNS / Bonjour multicast"],
    ["ff00::/8", "Multicast"],
    ["2000::/3", "Global unicast"],
  ].map(([cidr, label]) => ({ cidr: parseCidr(cidr), label })).sort((a, b) => b.cidr.prefix - a.cidr.prefix);

  function classify(ip) {
    const match = RANGES.find((r) => inCidr(ip, r.cidr));
    return match ? match.label + " (" + formatIP(match.cidr.version, match.cidr.network) + "/" + match.cidr.prefix + ")" : "Public";
  }

  /* Ports */

  const PORTS = [
    [20, "TCP", "FTP data"], [21, "TCP", "FTP control"], [22, "TCP", "SSH / SFTP / git over SSH"], [23, "TCP", "Telnet"],
    [25, "TCP", "SMTP"], [53, "TCP/UDP", "DNS"], [67, "UDP", "DHCP server"], [68, "UDP", "DHCP client"], [69, "UDP", "TFTP"],
    [80, "TCP", "HTTP"], [88, "TCP/UDP", "Kerberos"], [110, "TCP", "POP3"], [123, "UDP", "NTP (time.apple.com)"], [137, "UDP", "NetBIOS name"],
    [143, "TCP", "IMAP"], [161, "UDP", "SNMP"], [389, "TCP", "LDAP"], [443, "TCP/UDP", "HTTPS, and HTTP/3 over QUIC (UDP). Also APNs and iCloud"],
    [445, "TCP", "SMB file sharing"], [465, "TCP", "SMTP over TLS"], [500, "UDP", "IKE (IPsec VPN)"], [514, "UDP", "Syslog"],
    [548, "TCP", "AFP (Apple File Protocol, legacy)"], [554, "TCP/UDP", "RTSP"], [587, "TCP", "SMTP submission"], [623, "UDP", "IPMI"],
    [631, "TCP", "IPP / CUPS printing (AirPrint)"], [636, "TCP", "LDAPS"], [853, "TCP", "DNS over TLS"], [873, "TCP", "rsync"],
    [993, "TCP", "IMAPS"], [995, "TCP", "POP3S"], [1080, "TCP", "SOCKS proxy"], [1194, "UDP", "OpenVPN"], [1433, "TCP", "SQL Server"],
    [1521, "TCP", "Oracle DB"], [1723, "TCP", "PPTP"], [1883, "TCP", "MQTT"], [2049, "TCP", "NFS"], [2195, "TCP", "APNs legacy binary provider (retired)"],
    [2197, "TCP", "APNs HTTP/2 provider API (alternate to 443)"], [2375, "TCP", "Docker API (unencrypted)"], [2376, "TCP", "Docker API (TLS)"],
    [3000, "TCP", "Node, Grafana and other dev servers"], [3283, "TCP/UDP", "Apple Remote Desktop"], [3306, "TCP", "MySQL"],
    [3389, "TCP", "Microsoft RDP"], [3478, "UDP", "STUN / TURN. FaceTime and Game Center use 3478-3497"], [3689, "TCP", "DAAP (iTunes / Music sharing)"],
    [4369, "TCP", "Erlang port mapper"], [4500, "UDP", "IPsec NAT traversal"], [5000, "TCP", "AirPlay Receiver on macOS 12+ (conflicts with Flask and dev servers)"],
    [5060, "TCP/UDP", "SIP"], [5223, "TCP", "APNs device connection (fallback to 443)"], [5228, "TCP", "Google FCM"], [5349, "TCP", "TURN over TLS"],
    [5353, "UDP", "mDNS / Bonjour"], [5432, "TCP", "PostgreSQL"], [5672, "TCP", "AMQP (RabbitMQ)"], [5900, "TCP", "VNC / macOS Screen Sharing"],
    [5984, "TCP", "CouchDB"], [6379, "TCP", "Redis"], [6443, "TCP", "Kubernetes API server"], [7000, "TCP", "AirPlay (macOS 12+ Control Center)"],
    [7100, "TCP", "AirPlay screen mirroring"], [8000, "TCP", "Dev HTTP servers"], [8080, "TCP", "HTTP alternate, mitmproxy default"], [8443, "TCP", "HTTPS alternate"],
    [8741, "TCP", "Viewlift AI dashboard (this app)"], [8888, "TCP", "Charles Proxy default, Jupyter"], [9000, "TCP", "PHP-FPM, SonarQube, MinIO"],
    [9090, "TCP", "Proxyman default proxy port, Prometheus"], [9092, "TCP", "Kafka"], [9200, "TCP", "Elasticsearch"], [9418, "TCP", "git protocol"],
    [11211, "TCP", "Memcached"], [16384, "UDP", "FaceTime / iMessage audio and video (16384-16472)"], [27017, "TCP", "MongoDB"],
    [49152, "TCP/UDP", "Start of the dynamic (ephemeral) port range"], [51820, "UDP", "WireGuard"], [62078, "TCP", "iOS lockdownd (Xcode, Finder sync, usbmuxd over Wi-Fi)"],
  ];

  /* MIME types */

  const MIME = [
    ["ipa", "application/octet-stream", "iOS app archive (a ZIP). Use this type for OTA installs."],
    ["mobileprovision", "application/x-apple-aspen-mobileprovision", "Provisioning profile (CMS-signed plist)"],
    ["mobileconfig", "application/x-apple-aspen-config", "Configuration profile"],
    ["plist", "application/x-plist", "Property list (XML or binary)"],
    ["pkpass", "application/vnd.apple.pkpass", "Apple Wallet pass"],
    ["m3u8", "application/vnd.apple.mpegurl", "HLS playlist (audio/mpegurl also accepted)"],
    ["m3u", "audio/mpegurl", "Playlist"],
    ["ts", "video/mp2t", "MPEG-TS segment (HLS)"],
    ["m4s", "video/iso.segment", "fMP4 / CMAF segment"],
    ["mpd", "application/dash+xml", "MPEG-DASH manifest"],
    ["mp4", "video/mp4", "MPEG-4 video"],
    ["m4v", "video/x-m4v", "Apple MPEG-4 video"],
    ["mov", "video/quicktime", "QuickTime movie"],
    ["webm", "video/webm", "WebM video"],
    ["mkv", "video/x-matroska", "Matroska video"],
    ["m4a", "audio/mp4", "AAC or ALAC audio in MP4"],
    ["aac", "audio/aac", "AAC audio"],
    ["mp3", "audio/mpeg", "MP3 audio"],
    ["caf", "audio/x-caf", "Core Audio Format"],
    ["wav", "audio/wav", "WAV audio"],
    ["aiff", "audio/aiff", "AIFF audio"],
    ["flac", "audio/flac", "FLAC audio"],
    ["ogg", "audio/ogg", "Ogg audio"],
    ["vtt", "text/vtt", "WebVTT subtitles"],
    ["srt", "application/x-subrip", "SubRip subtitles"],
    ["ttml", "application/ttml+xml", "TTML subtitles"],
    ["key", "application/octet-stream", "HLS AES-128 key (also Keynote: application/vnd.apple.keynote)"],
    ["heic", "image/heic", "HEIF image (iPhone photos)"],
    ["heif", "image/heif", "HEIF image"],
    ["png", "image/png", "PNG image"],
    ["jpg", "image/jpeg", "JPEG image"],
    ["jpeg", "image/jpeg", "JPEG image"],
    ["gif", "image/gif", "GIF image"],
    ["webp", "image/webp", "WebP image"],
    ["avif", "image/avif", "AVIF image"],
    ["svg", "image/svg+xml", "SVG image"],
    ["ico", "image/vnd.microsoft.icon", "Icon"],
    ["icns", "image/icns", "macOS icon"],
    ["tiff", "image/tiff", "TIFF image"],
    ["bmp", "image/bmp", "Bitmap"],
    ["pdf", "application/pdf", "PDF"],
    ["usdz", "model/vnd.usdz+zip", "AR Quick Look model"],
    ["reality", "model/vnd.reality", "Reality Composer file"],
    ["json", "application/json", "JSON"],
    ["apple-app-site-association", "application/json", "Universal Links / Associated Domains file (served without extension)"],
    ["geojson", "application/geo+json", "GeoJSON"],
    ["xml", "application/xml", "XML"],
    ["html", "text/html", "HTML"],
    ["css", "text/css", "CSS"],
    ["js", "text/javascript", "JavaScript"],
    ["mjs", "text/javascript", "JavaScript module"],
    ["wasm", "application/wasm", "WebAssembly"],
    ["txt", "text/plain", "Plain text"],
    ["csv", "text/csv", "CSV"],
    ["md", "text/markdown", "Markdown"],
    ["yaml", "application/yaml", "YAML"],
    ["yml", "application/yaml", "YAML"],
    ["strings", "text/plain", "Localizable.strings (UTF-16 or UTF-8)"],
    ["xcstrings", "application/json", "Xcode string catalog"],
    ["zip", "application/zip", "ZIP archive"],
    ["gz", "application/gzip", "gzip"],
    ["tar", "application/x-tar", "tar archive"],
    ["7z", "application/x-7z-compressed", "7-Zip"],
    ["dmg", "application/x-apple-diskimage", "macOS disk image"],
    ["pkg", "application/octet-stream", "macOS installer package"],
    ["woff", "font/woff", "Web font"],
    ["woff2", "font/woff2", "Web font"],
    ["ttf", "font/ttf", "TrueType font"],
    ["otf", "font/otf", "OpenType font"],
    ["ics", "text/calendar", "Calendar"],
    ["vcf", "text/vcard", "Contact card"],
    ["webmanifest", "application/manifest+json", "Web app manifest"],
    ["p8", "application/pkcs8", "Private key (APNs / App Store Connect API key)"],
    ["p12", "application/x-pkcs12", "Certificate with private key"],
    ["cer", "application/pkix-cert", "DER certificate"],
    ["pem", "application/x-pem-file", "PEM certificate or key"],
    ["crt", "application/x-x509-ca-cert", "Certificate"],
    ["bin", "application/octet-stream", "Binary data"],
    ["form", "multipart/form-data", "HTML form upload"],
    ["urlencoded", "application/x-www-form-urlencoded", "HTML form body"],
    ["ndjson", "application/x-ndjson", "Newline-delimited JSON"],
    ["proto", "application/x-protobuf", "Protocol Buffers"],
    ["grpc", "application/grpc", "gRPC"],
    ["sse", "text/event-stream", "Server-sent events"],
  ];

  const MAGIC = [
    [[0x89, 0x50, 0x4e, 0x47], "PNG", "image/png"],
    [[0xff, 0xd8, 0xff], "JPEG", "image/jpeg"],
    [[0x47, 0x49, 0x46, 0x38], "GIF", "image/gif"],
    [[0x25, 0x50, 0x44, 0x46], "PDF", "application/pdf"],
    [[0x50, 0x4b, 0x03, 0x04], "ZIP (also .ipa, .pkpass, .usdz, .docx)", "application/zip"],
    [[0x1f, 0x8b], "gzip", "application/gzip"],
    [[0x62, 0x70, 0x6c, 0x69, 0x73, 0x74, 0x30, 0x30], "Binary plist", "application/x-plist"],
    [[0x30, 0x82], "DER / CMS (e.g. .mobileprovision, .cer)", "application/pkcs7-mime"],
    [[0x23, 0x45, 0x58, 0x54, 0x4d, 0x33, 0x55], "M3U playlist (HLS)", "application/vnd.apple.mpegurl"],
    [[0x47], "MPEG-TS (sync byte 0x47)", "video/mp2t"],
    [[0x00, 0x61, 0x73, 0x6d], "WebAssembly", "application/wasm"],
    [[0x49, 0x44, 0x33], "MP3 with ID3", "audio/mpeg"],
    [[0x63, 0x61, 0x66, 0x66], "Core Audio Format", "audio/x-caf"],
  ];

  function sniff(bytes) {
    if (bytes.length >= 12 && util.fromUtf8(bytes.slice(4, 8)) === "ftyp") {
      const brand = util.fromUtf8(bytes.slice(8, 12));
      const map = { heic: ["HEIC", "image/heic"], heix: ["HEIC", "image/heic"], mif1: ["HEIF", "image/heif"], avif: ["AVIF", "image/avif"], qt: ["QuickTime", "video/quicktime"], "M4A ": ["M4A audio", "audio/mp4"], "M4V ": ["M4V video", "video/x-m4v"] };
      const hit = map[brand.trim()] || map[brand];
      return hit ? [hit[0] + " (ftyp " + brand + ")", hit[1]] : ["MP4 family (ftyp " + brand + ")", "video/mp4"];
    }
    if (bytes.length >= 12 && util.fromUtf8(bytes.slice(0, 4)) === "RIFF") {
      const kind = util.fromUtf8(bytes.slice(8, 12));
      return kind === "WEBP" ? ["WebP", "image/webp"] : kind === "WAVE" ? ["WAV", "audio/wav"] : ["RIFF " + kind, "application/octet-stream"];
    }
    for (const [sig, name, mime] of MAGIC) {
      if (sig.every((b, i) => bytes[i] === b)) {
        if (name.startsWith("MPEG-TS") && !(bytes.length > 188 && bytes[188] === 0x47)) continue;
        return [name, mime];
      }
    }
    const head = util.fromUtf8(bytes.slice(0, 256)).trimStart();
    if (head.startsWith("<?xml") && head.includes("plist")) return ["XML plist", "application/x-plist"];
    if (head.startsWith("<svg") || (head.startsWith("<?xml") && head.includes("<svg"))) return ["SVG", "image/svg+xml"];
    if (/^<(!doctype html|html)/i.test(head)) return ["HTML", "text/html"];
    if (/^[{[]/.test(head)) return ["Probably JSON", "application/json"];
    if (head.startsWith("WEBVTT")) return ["WebVTT", "text/vtt"];
    return ["Unknown", "application/octet-stream"];
  }

  /* User agents */

  const IOS_BY_DARWIN = { 17: 11, 18: 12, 19: 13, 20: 14, 21: 15, 22: 16, 23: 17, 24: 18, 25: 26 };
  const MACOS_BY_DARWIN = { 17: "10.13", 18: "10.14", 19: "10.15", 20: "11", 21: "12", 22: "13", 23: "14", 24: "15", 25: "26" };
  const IOS_BY_BUILD = { 15: 9, 16: 10, 17: 11, 18: 12, 19: 14, 20: 16, 21: 17, 22: 18, 23: 26 };

  function iosFromBuild(build) {
    const m = /^(\d{2})([A-H])\d+/.exec(build || "");
    if (!m) return null;
    const major = Number(m[1]) === 17 ? 13 : IOS_BY_BUILD[Number(m[1])];
    if (Number(m[1]) === 18) return "12 or 13." + (m[2].charCodeAt(0) - 65);
    if (Number(m[1]) === 19) return "14 (or 15 betas)";
    return major ? major + "." + (m[2].charCodeAt(0) - 65) : null;
  }

  function parseUA(ua) {
    const text = String(ua || "").trim();
    const rows = [];
    const add = (k, v) => v && !rows.some((r) => r[0] === k && r[1] === v) && rows.push([k, v]);
    let m;
    const cf = /CFNetwork\/([\d.]+)/.exec(text);
    const darwin = /Darwin\/(\d+)\.(\d+)(?:\.\d+)?/.exec(text);
    if ((m = /AppleCoreMedia\/([\d.]+?)\.(\w+)\s*\(([^)]*)\)/.exec(text))) {
      add("Client", "AppleCoreMedia (AVPlayer / AVFoundation media loading)");
      add("Device", (m[3].split(";")[0] || "").trim());
      add("iOS / tvOS build", m[2] + (iosFromBuild(m[2]) ? " (≈ " + iosFromBuild(m[2]) + ")" : ""));
    }
    if (cf && !/Mozilla/.test(text)) {
      const app = /^([^/\s]+)\/([^\s]+)/.exec(text);
      if (app && !/^(CFNetwork|AppleCoreMedia)$/.test(app[1])) add("App", decodeURIComponent(app[1]) + " " + app[2]);
      add("Networking", "CFNetwork " + cf[1] + " (URLSession)");
    }
    if (darwin) {
      const major = Number(darwin[1]);
      const minor = darwin[2];
      add("Darwin", darwin[1] + "." + minor);
      if (IOS_BY_DARWIN[major]) add("OS (from Darwin)", "iOS / iPadOS / tvOS " + IOS_BY_DARWIN[major] + "." + minor + " or macOS " + MACOS_BY_DARWIN[major] + (MACOS_BY_DARWIN[major].includes(".") ? "" : "." + minor) + " (approximate)");
    }
    if ((m = /Alamofire\/([\d.]+)/.exec(text))) {
      add("Networking", "Alamofire " + m[1]);
      const meta = /^([^/]+)\/([^\s]+) \(([^;]+); build:([^;]+); ([^)]+)\)/.exec(text);
      if (meta) {
        add("App", meta[1] + " " + meta[2] + " (build " + meta[4] + ")");
        add("Bundle ID", meta[3]);
        add("OS", meta[5]);
      }
    }
    if ((m = /okhttp\/([\d.]+)/i.exec(text))) add("Networking", "OkHttp " + m[1] + " (Android or JVM)");
    if ((m = /(iPhone|iPad|iPod|AppleTV|Apple TV|Macintosh|Watch)/.exec(text)) && !rows.some((r) => r[0] === "Device")) add("Device", m[1] === "Macintosh" && /Mobile\//.test(text) ? "iPad (desktop-class Safari)" : m[1]);
    if ((m = /(?:iPhone )?OS (\d+)[_.](\d+)(?:[_.](\d+))? like Mac OS X/.exec(text))) add("OS", (/iPad/.test(text) ? "iPadOS " : "iOS ") + [m[1], m[2], m[3]].filter(Boolean).join("."));
    else if ((m = /Mac OS X (\d+)[_.](\d+)(?:[_.](\d+))?/.exec(text))) add("OS", "macOS " + [m[1], m[2], m[3]].filter(Boolean).join(".") + (m[1] === "10" && m[2] === "15" && /Safari/.test(text) ? " (frozen value; real version may be newer)" : ""));
    else if ((m = /Android (\d+(?:\.\d+)*)/.exec(text))) add("OS", "Android " + m[1]);
    else if ((m = /Windows NT (\d+\.\d+)/.exec(text))) add("OS", "Windows " + ({ "10.0": "10 or 11", "6.3": "8.1", "6.2": "8", "6.1": "7" }[m[1]] || "NT " + m[1]));
    else if ((m = /(tvOS|watchOS|visionOS)[ /](\d+(?:\.\d+)*)/.exec(text))) add("OS", m[1] + " " + m[2]);
    else if (/Linux/.test(text)) add("OS", "Linux");
    const browsers = [
      [/Edg(?:A|iOS)?\/([\d.]+)/, "Microsoft Edge"], [/OPR\/([\d.]+)/, "Opera"], [/SamsungBrowser\/([\d.]+)/, "Samsung Internet"],
      [/CriOS\/([\d.]+)/, "Chrome for iOS (WebKit)"], [/FxiOS\/([\d.]+)/, "Firefox for iOS (WebKit)"], [/Firefox\/([\d.]+)/, "Firefox"],
      [/Chrome\/([\d.]+)/, "Chrome"], [/Version\/([\d.]+).*Safari\//, "Safari"],
    ];
    for (const [re, name] of browsers) {
      if ((m = re.exec(text))) {
        add("Browser", name + " " + m[1]);
        break;
      }
    }
    if (/Mozilla/.test(text) && /Mobile\/\w+/.test(text) && !/Safari\//.test(text) && /AppleWebKit/.test(text)) add("Browser", "In-app WKWebView (no Safari token)");
    if ((m = /AppleWebKit\/([\d.]+)/.exec(text))) add("Engine", "WebKit " + m[1]);
    const bots = [[/Googlebot/i, "Googlebot"], [/bingbot/i, "Bingbot"], [/Applebot/i, "Applebot"], [/curl\/([\d.]+)/, "curl"], [/python-requests|python-urllib|aiohttp/i, "Python HTTP client"], [/PostmanRuntime\/([\d.]+)/, "Postman"], [/Wget/i, "Wget"], [/facebookexternalhit|Slackbot|Twitterbot|WhatsApp/i, "Link preview bot"]];
    for (const [re, name] of bots) if (re.test(text)) add("Bot / tool", name);
    return rows;
  }

  /* Header analysis */

  function analyzeHeaders(pairs) {
    const headers = new Map();
    const cookies = [];
    for (const [name, value] of pairs) {
      const key = name.toLowerCase();
      if (key === "set-cookie") cookies.push(value);
      headers.set(key, headers.has(key) ? headers.get(key) + ", " + value : value);
    }
    const findings = [];
    const add = (level, area, text) => findings.push([level, area, text]);
    const hsts = headers.get("strict-transport-security");
    if (!hsts) add("warn", "Security", "No Strict-Transport-Security. Browsers may try http:// first.");
    else {
      const age = Number((/max-age=(\d+)/i.exec(hsts) || [])[1] || 0);
      if (age < 15552000) add("warn", "Security", "HSTS max-age is " + age + "s. Use at least 6 months (15552000).");
      else add("ok", "Security", "HSTS max-age " + age + "s" + (/includesubdomains/i.test(hsts) ? " with includeSubDomains." : "."));
    }
    const csp = headers.get("content-security-policy");
    if (!csp) add("warn", "Security", "No Content-Security-Policy.");
    else {
      add("ok", "Security", "Content-Security-Policy present.");
      if (/'unsafe-inline'|'unsafe-eval'/.test(csp)) add("warn", "Security", "CSP allows unsafe-inline or unsafe-eval.");
    }
    if ((headers.get("x-content-type-options") || "").toLowerCase() !== "nosniff") add("warn", "Security", "X-Content-Type-Options: nosniff is missing.");
    else add("ok", "Security", "nosniff set.");
    if (!headers.get("x-frame-options") && !/frame-ancestors/.test(csp || "")) add("warn", "Security", "No clickjacking protection (X-Frame-Options or CSP frame-ancestors).");
    if (!headers.get("referrer-policy")) add("info", "Privacy", "No Referrer-Policy. Browsers default to strict-origin-when-cross-origin.");
    const powered = headers.get("x-powered-by");
    if (powered) add("warn", "Disclosure", "X-Powered-By reveals '" + powered + "'.");
    const server = headers.get("server");
    if (server && /\d/.test(server)) add("info", "Disclosure", "Server header includes a version: " + server + ".");
    const acao = headers.get("access-control-allow-origin");
    if (acao === "*" && (headers.get("access-control-allow-credentials") || "").toLowerCase() === "true") add("error", "CORS", "Allow-Origin * with credentials is invalid. Browsers reject it.");
    else if (acao === "*") add("info", "CORS", "Any origin may read this response.");
    else if (acao) add("ok", "CORS", "Allowed origin: " + acao + (headers.get("vary") && /origin/i.test(headers.get("vary")) ? "." : ". Add Vary: Origin if this changes per request."));
    for (const cookie of cookies) {
      const name = cookie.split("=")[0];
      const missing = ["Secure", "HttpOnly", "SameSite"].filter((flag) => !new RegExp(";\\s*" + flag, "i").test(cookie));
      if (missing.length) add("warn", "Cookies", "Cookie " + name + " is missing " + missing.join(", ") + ".");
      else add("ok", "Cookies", "Cookie " + name + " has Secure, HttpOnly and SameSite.");
    }
    const cc = headers.get("cache-control");
    const type = (headers.get("content-type") || "").toLowerCase();
    if (!cc && !headers.get("expires")) add("warn", "Caching", "No Cache-Control. Caches will guess with heuristics.");
    else if (cc) {
      const age = Number((/(?:s-)?max-age=(\d+)/.exec(cc) || [])[1]);
      add("info", "Caching", "Cache-Control: " + cc);
      if (/mpegurl/.test(type) && age > 10) add("warn", "Streaming", "Live HLS playlists should cache for about half a segment duration or less. This one caches for " + age + "s.");
      if (/no-store/.test(cc) && /private/.test(cc) === false && /set-cookie/.test([...headers.keys()].join(" "))) add("info", "Caching", "no-store with Set-Cookie: fine for authenticated pages.");
    }
    if (!headers.get("etag") && !headers.get("last-modified")) add("info", "Caching", "No ETag or Last-Modified, so clients cannot revalidate cheaply.");
    if (/^(text\/|application\/(json|javascript|xml|vnd\.apple\.mpegurl))/.test(type) && !headers.get("content-encoding")) add("info", "Performance", "Text response without Content-Encoding (gzip or br).");
    if (headers.get("alt-svc") && /h3/.test(headers.get("alt-svc"))) add("ok", "Performance", "HTTP/3 is advertised.");
    const score = Math.max(0, 100 - findings.filter((f) => f[0] === "warn").length * 10 - findings.filter((f) => f[0] === "error").length * 25);
    return { findings, score, grade: score >= 90 ? "A" : score >= 75 ? "B" : score >= 60 ? "C" : score >= 40 ? "D" : "F" };
  }

  function cidrRows(c) {
    const fmt = (v) => formatIP(c.version, v);
    const mask = maskOf(c.bits, c.prefix);
    const rows = [
      ["Network", fmt(c.network) + "/" + c.prefix],
      ["Prefix length", "/" + c.prefix],
    ];
    if (c.version === 4) {
      const usable = c.prefix >= 31 ? c.size : c.size - 2n;
      rows.push(
        ["Netmask", fmt(mask)],
        ["Wildcard", fmt(((1n << 32n) - 1n) ^ mask)],
        ["Broadcast", c.prefix >= 31 ? "(none)" : fmt(c.last)],
        ["First host", fmt(c.prefix >= 31 ? c.network : c.network + 1n)],
        ["Last host", fmt(c.prefix >= 31 ? c.last : c.last - 1n)],
        ["Addresses", c.size.toLocaleString()],
        ["Usable hosts", usable.toLocaleString()],
        ["Binary netmask", [24n, 16n, 8n, 0n].map((s) => ((mask >> s) & 255n).toString(2).padStart(8, "0")).join(".")],
        ["Type", classify(c)],
      );
    } else {
      rows.push(["First address", fmt(c.network)], ["Last address", fmt(c.last)], ["Addresses", c.prefix >= 64 ? c.size.toLocaleString() : "2^" + (128 - c.prefix)], ["/64 subnets", c.prefix <= 64 ? (1n << BigInt(64 - c.prefix)).toLocaleString() : "(smaller than /64)"], ["Type", classify(c)]);
    }
    return rows;
  }

  register("network", [
    {
      slug: "ip-address-lookup",
      name: "IP address lookup",
      description: "Classify an IPv4 or IPv6 address (private, CGNAT, NAT64, Apple and more) and look up reverse DNS.",
      keywords: "ip address ptr reverse private public ipv6 nat64",
      live: false,
      autorun: true,
      action: "Look up",
      inputs: [
        { id: "ip", label: "IP address", type: "text", value: "17.253.144.10", wide: true },
        { id: "reverse", label: "Reverse DNS (PTR)", type: "checkbox", value: true },
      ],
      async run(v, ctx) {
        const ip = parseIP(v.ip);
        if (!ip) throw new Error("Enter an IPv4 or IPv6 address.");
        const info = await ctx.server("ip-info", { ip: String(v.ip).trim(), reverse: v.reverse });
        const flags = ["private", "global", "loopback", "link_local", "multicast", "reserved", "unspecified"].filter((k) => info[k]).map((k) => k.replace("_", "-"));
        const rows = [
          ["Address", info.address],
          ["Version", "IPv" + info.version],
          ["Range", classify(ip)],
          ["Flags", flags.join(", ") || "none"],
          ["Integer", info.integer],
          ["Hex", info.hex],
        ];
        if (info.binary) rows.push(["Binary", info.binary]);
        if (info.exploded) rows.push(["Expanded", info.exploded]);
        if (info.ipv4_mapped) rows.push(["IPv4 mapped", info.ipv4_mapped]);
        rows.push(["Reverse name", info.reverse_pointer]);
        if (v.reverse) rows.push(["PTR", info.ptr ? info.ptr.join(", ") || "(no record)" : info.ptr_error || ""]);
        return [{ type: "kv", label: "Address", rows }, { type: "note", text: "This tool works offline and has no geolocation database. Only reverse DNS uses the network." }];
      },
    },
    {
      slug: "cidr-calculator",
      name: "CIDR calculator",
      description: "Network, netmask, broadcast, host range and size for an IPv4 or IPv6 CIDR, plus a membership check.",
      keywords: "cidr subnet mask range ipv4 ipv6",
      inputs: [
        { id: "cidr", label: "CIDR", type: "text", value: "10.20.0.0/22", wide: true },
        { id: "check", label: "Does this IP belong to it?", type: "text", value: "10.20.3.17" },
      ],
      run(v) {
        const c = parseCidr(v.cidr);
        const out = [{ type: "kv", label: "IPv" + c.version, rows: cidrRows(c) }];
        const address = String(v.check || "").trim();
        if (address) {
          const ip = parseIP(address);
          out.push(!ip ? { type: "note", level: "warn", text: "'" + address + "' is not an IP address." } : inCidr(ip, c) ? { type: "note", level: "ok", text: address + " is inside " + formatIP(c.version, c.network) + "/" + c.prefix + "." } : { type: "note", level: "error", text: address + " is outside this network." });
        }
        return out;
      },
    },
    {
      slug: "subnet-calculator",
      name: "Subnet calculator",
      description: "Split a network into equal subnets by prefix, subnet count or hosts per subnet.",
      keywords: "subnet split vlsm network plan",
      inputs: [
        { id: "cidr", label: "Network", type: "text", value: "192.168.0.0/24" },
        { id: "by", label: "Split by", type: "select", options: [["count", "Number of subnets"], ["hosts", "Hosts per subnet"], ["prefix", "New prefix length"]], value: "count" },
        { id: "count", label: "Subnets", type: "number", value: 4, min: 1, max: 65536, show: (v) => v.by === "count" },
        { id: "hosts", label: "Hosts each", type: "number", value: 50, min: 1, show: (v) => v.by === "hosts" },
        { id: "prefix", label: "New prefix", type: "number", value: 26, min: 0, max: 128, show: (v) => v.by === "prefix" },
      ],
      run(v) {
        const c = parseCidr(v.cidr);
        let prefix;
        if (v.by === "prefix") prefix = util.clampInt(v.prefix, 0, c.bits);
        else if (v.by === "count") prefix = c.prefix + Math.ceil(Math.log2(Math.max(1, util.clampInt(v.count, 1, 65536))));
        else {
          const needed = BigInt(util.clampInt(v.hosts, 1, Number.MAX_SAFE_INTEGER)) + (c.version === 4 ? 2n : 0n);
          let host = 0;
          while ((1n << BigInt(host)) < needed) host += 1;
          prefix = c.bits - host;
        }
        if (prefix < c.prefix) throw new Error("The subnets would be bigger than the network (/" + prefix + " < /" + c.prefix + ").");
        if (prefix > c.bits) throw new Error("Too many subnets for this network.");
        const size = 1n << BigInt(c.bits - prefix);
        const total = 1n << BigInt(prefix - c.prefix);
        const limit = 512n;
        const rows = [];
        for (let i = 0n; i < total && i < limit; i += 1n) {
          const network = c.network + i * size;
          const last = network + size - 1n;
          const fmt = (x) => formatIP(c.version, x);
          const v4small = c.version === 4 && prefix >= 31;
          rows.push([String(i + 1n), fmt(network) + "/" + prefix, c.version === 4 && !v4small ? fmt(network + 1n) + " - " + fmt(last - 1n) : fmt(network) + " - " + fmt(last), c.version === 4 ? (v4small ? "(none)" : fmt(last)) : "", (c.version === 4 && !v4small ? size - 2n : size).toLocaleString()]);
        }
        return [
          { type: "note", level: "ok", text: total.toLocaleString() + " subnets of /" + prefix + (total > limit ? ". Showing the first " + limit + "." : ".") },
          { type: "table", label: "Subnets", columns: ["#", "Subnet", "Hosts", "Broadcast", "Usable"], rows },
        ];
      },
    },
    {
      slug: "dns-lookup",
      name: "DNS lookup",
      description: "Query A, AAAA, CNAME, MX, TXT, NS, SRV, CAA and other records with dig, optionally against a chosen resolver.",
      keywords: "dns dig nslookup records resolver",
      live: false,
      action: "Look up",
      inputs: [
        { id: "name", label: "Name", type: "text", value: "apple.com", wide: true },
        { id: "type", label: "Record type", type: "select", options: [["COMMON", "Common (A, AAAA, CNAME, MX, NS, TXT)"], "A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "CAA", "PTR"], value: "COMMON" },
        { id: "server", label: "DNS server (optional)", type: "select", options: [["", "System resolver"], ["1.1.1.1", "Cloudflare 1.1.1.1"], ["8.8.8.8", "Google 8.8.8.8"], ["9.9.9.9", "Quad9 9.9.9.9"]], value: "" },
      ],
      async run(v, ctx) {
        const name = String(v.name || "").trim().replace(/^https?:\/\//, "").split("/")[0];
        const types = v.type === "COMMON" ? ["A", "AAAA", "CNAME", "MX", "NS", "TXT"] : [v.type];
        const rows = [];
        let ms = 0;
        const raws = [];
        for (const type of types) {
          const result = await ctx.server("dns", { name, type, server: v.server });
          ms += result.ms;
          raws.push(";; " + type + "\n" + result.raw.trim());
          for (const r of result.records) if (v.type !== "COMMON" || r.type === type) rows.push([r.type, r.name, r.ttl, r.data]);
        }
        return [
          { type: "note", level: rows.length ? "ok" : "warn", text: rows.length + " records in " + ms + " ms." },
          { type: "table", label: "Records", columns: ["Type", "Name", "TTL", "Data"], rows, empty: "No records." },
          { type: "text", label: "dig output", value: raws.join("\n\n") },
        ];
      },
    },
    {
      slug: "whois-lookup",
      name: "WHOIS lookup",
      description: "Registrar, dates, name servers and network owner for a domain or IP, via the system whois command.",
      keywords: "whois domain registrar expiry owner",
      live: false,
      action: "Look up",
      inputs: [{ id: "query", label: "Domain or IP", type: "text", value: "viewlift.com", wide: true }],
      async run(v, ctx) {
        const result = await ctx.server("whois", { query: String(v.query || "").trim().replace(/^https?:\/\//, "").split("/")[0] });
        const rows = Object.entries(result.fields).map(([k, list]) => [k, list.join("\n")]);
        return [
          { type: "kv", label: "Summary", rows, empty: "No common fields found. See the raw output." },
          { type: "text", label: "Raw WHOIS", value: result.raw },
        ];
      },
    },
    {
      slug: "port-reference",
      name: "Port reference",
      description: "Common TCP and UDP ports, including Apple services (APNs, AirPlay, Bonjour, lockdownd) and proxy defaults.",
      keywords: "port tcp udp service apns airplay bonjour",
      inputs: [{ id: "query", label: "Search port or service", type: "text", value: "", placeholder: "443, apns, airplay, 5000", wide: true }],
      run(v) {
        const q = String(v.query || "").trim().toLowerCase();
        const rows = PORTS.filter(([port, proto, text]) => !q || String(port) === q || (q.length > 1 && (String(port).startsWith(q) || text.toLowerCase().includes(q) || proto.toLowerCase() === q)));
        return { type: "table", label: rows.length + " ports", columns: ["Port", "Protocol", "Service"], rows, empty: "No match." };
      },
    },
    {
      slug: "user-agent-parser",
      name: "User agent parser",
      description: "Parse browser, WKWebView, CFNetwork, Alamofire and AppleCoreMedia user agents, and map Darwin versions to iOS and macOS.",
      keywords: "user agent ua cfnetwork darwin ios version avplayer",
      inputs: [{ id: "ua", label: "User-Agent", type: "textarea", rows: 4, value: "ViewliftOTT/5.12.0 (com.viewlift.ott; build:812; iOS 18.1.0) Alamofire/5.9.1\nViewliftOTT/812 CFNetwork/1568.200.51 Darwin/24.1.0\nAppleCoreMedia/1.0.0.22B83 (iPhone; U; CPU OS 18_1 like Mac OS X; en_us)\nMozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1", hint: "One user agent per line." }],
      run(v) {
        return util.nonEmptyLines(v.ua).slice(0, 20).map((line) => {
          const rows = parseUA(line);
          return { type: "kv", label: line.length > 90 ? line.slice(0, 90) + "…" : line, rows: rows.length ? rows : [["Result", "Not recognized"]] };
        });
      },
    },
    {
      slug: "mime-type-lookup",
      name: "MIME type lookup",
      description: "Look up MIME types by extension or type, including Apple formats (ipa, mobileprovision, pkpass, m3u8), or sniff a file.",
      keywords: "mime content-type extension file type magic",
      inputs: [
        { id: "query", label: "Extension or MIME type", type: "text", value: "", placeholder: "m3u8, heic, application/json", wide: true },
        { id: "file", label: "Or detect from a file", type: "file" },
      ],
      run(v) {
        const out = [];
        if (v.file) {
          const [name, mime] = sniff(v.file.bytes);
          const ext = (v.file.name.split(".").pop() || "").toLowerCase();
          const byExt = MIME.find((m) => m[0] === ext);
          out.push({ type: "kv", label: v.file.name, rows: [["Detected", name], ["MIME from content", mime], ["MIME from extension", byExt ? byExt[1] : "(unknown extension)"], ["Size", util.formatBytes(v.file.bytes.length)], ["First bytes", util.toHex(v.file.bytes.slice(0, 16)).replace(/(..)/g, "$1 ").trim()]] });
        }
        const q = String(v.query || "").trim().toLowerCase().replace(/^\./, "");
        const rows = MIME.filter(([ext, mime, text]) => !q || ext === q || ext.startsWith(q) || mime.includes(q) || text.toLowerCase().includes(q)).map(([ext, mime, text]) => ["." + ext, mime, text]);
        out.push({ type: "table", label: rows.length + " types", columns: ["Extension", "MIME type", "Notes"], rows });
        return out;
      },
    },
    {
      slug: "url-parser",
      name: "URL parser",
      description: "Split a URL, deep link or universal link into scheme, host, path, query parameters and fragment.",
      keywords: "url parse query string deep link scheme",
      inputs: [{ id: "url", label: "URL", type: "textarea", rows: 3, value: "https://user:pass@www.example.com:8443/watch/vl%20123/play?autoplay=1&t=90&tags=a&tags=b&utm_source=push#chapter-2" }],
      run(v) {
        const raw = String(v.url || "").trim();
        let url;
        try {
          url = new URL(raw);
        } catch (error) {
          throw new Error("Not an absolute URL. Include the scheme (https:// or myapp://).");
        }
        const special = ["http:", "https:", "ws:", "wss:", "ftp:", "file:"].includes(url.protocol);
        const defaults = { "http:": "80", "https:": "443", "ws:": "80", "wss:": "443", "ftp:": "21" };
        const rows = [
          ["Scheme", url.protocol.replace(/:$/, "") + (special ? "" : " (custom scheme / deep link)")],
          ["Username", decodeURIComponent(url.username)],
          ["Password", url.password ? "•".repeat(Math.min(url.password.length, 8)) : ""],
          ["Host", url.hostname],
          ["Port", url.port || (defaults[url.protocol] ? defaults[url.protocol] + " (default)" : "")],
          ["Origin", url.origin !== "null" ? url.origin : ""],
          ["Path", url.pathname],
          ["Decoded path", decodeURIComponent(url.pathname)],
          ["Query", url.search],
          ["Fragment", url.hash.replace(/^#/, "")],
        ].filter(([, value]) => value !== "");
        const params = [...url.searchParams.entries()].map(([k, val], i) => [i + 1, k, val]);
        const segments = url.pathname.split("/").filter(Boolean).map((s, i) => [i, decodeURIComponent(s)]);
        return [
          { type: "kv", label: "Parts", rows },
          { type: "table", label: "Query parameters", columns: ["#", "Name", "Value (decoded)"], rows: params, empty: "No query string." },
          { type: "table", label: "Path segments", columns: ["#", "Segment"], rows: segments, empty: "No path." },
          { type: "text", label: "Swift URLComponents", value: 'let components = URLComponents(string: "' + raw.replace(/"/g, '\\"') + '")\nlet items = components?.queryItems // ' + params.length + " items" },
        ];
      },
    },
    {
      slug: "http-header-analyzer",
      name: "HTTP header analyzer",
      description: "Grade response headers for security, CORS, cookies, caching and compression. Paste them or fetch a URL.",
      keywords: "security headers hsts csp cors cache audit",
      inputs: [
        { id: "mode", label: "Source", type: "select", options: [["paste", "Paste headers"], ["fetch", "Fetch a URL"]], value: "paste" },
        { id: "text", label: "Response headers", type: "textarea", rows: 12, show: (v) => v.mode === "paste", value: "HTTP/2 200\ncontent-type: application/vnd.apple.mpegurl\ncache-control: public, max-age=60\naccess-control-allow-origin: *\nserver: nginx/1.25.3\nx-powered-by: Express\nset-cookie: session=abc; Path=/; HttpOnly\nstrict-transport-security: max-age=86400" },
        { id: "url", label: "URL", type: "text", value: "https://www.apple.com/", show: (v) => v.mode === "fetch", wide: true },
      ],
      async run(v, ctx) {
        let pairs;
        let status = "";
        if (v.mode === "fetch") {
          const result = await ctx.server("http-request", { method: "GET", url: String(v.url).trim(), headers: { "User-Agent": "ViewliftAI-HeaderAnalyzer" }, timeout: 20, follow_redirects: true });
          pairs = result.headers;
          status = result.status + " " + result.reason + " from " + result.url;
        } else pairs = util.parseHeaderLines(v.text);
        if (!pairs.length) throw new Error("No headers found.");
        const { findings, score, grade } = analyzeHeaders(pairs);
        return [
          { type: "note", level: score >= 75 ? "ok" : score >= 50 ? "warn" : "error", text: "Grade " + grade + " (" + score + "/100)" + (status ? ". " + status : "") + "." },
          { type: "table", label: "Findings", columns: ["Level", "Area", "Finding"], rows: findings, rowClass: (r) => (r[0] === "error" ? "err" : r[0] === "warn" ? "warn" : r[0] === "ok" ? "ok" : "") },
          { type: "table", label: "Headers", columns: ["Header", "Value"], rows: pairs },
        ];
      },
    },
  ]);
})();
