/* Sandbox file preview for the Storage and state panel. */
(function (root) {
  const MAX_ITEMS = 200;
  const MAX_DEPTH = 12;
  const MAX_TEXT = 8000;
  const HEX_BYTES = 4096;

  const IMAGE_MIME = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    gif: "image/gif",
    webp: "image/webp",
    bmp: "image/bmp",
    ico: "image/x-icon",
    heic: "image/heic",
    heif: "image/heif",
    tif: "image/tiff",
    tiff: "image/tiff",
    svg: "image/svg+xml",
  };
  const HEIF_BRANDS = new Set(["heic", "heix", "hevc", "heif", "mif1", "msf1", "avif", "avis"]);
  const AUDIO_BRANDS = new Set(["M4A", "M4B", "M4P", "mp4a", "aac"]);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }

  function nameOf(path) {
    const parts = String(path || "").split("/");
    return parts[parts.length - 1] || "file";
  }

  function extOf(path) {
    const name = nameOf(path);
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  }

  function formatBytes(size) {
    if (!Number.isFinite(size)) return "";
    if (size < 1024) return `${Math.round(size)} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function base64ToBytes(value) {
    const clean = String(value || "").replace(/\s+/g, "");
    if (!clean) return new Uint8Array();
    const binary = atob(clean);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function bytesToBase64(bytes) {
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function ascii(bytes, offset, length) {
    let text = "";
    const end = Math.min(bytes.length, offset + length);
    for (let i = offset; i < end; i += 1) text += String.fromCharCode(bytes[i]);
    return text;
  }

  function starts(bytes, signature, offset = 0) {
    if (bytes.length < offset + signature.length) return false;
    for (let i = 0; i < signature.length; i += 1) {
      if (bytes[offset + i] !== signature[i]) return false;
    }
    return true;
  }

  function readUInt(bytes, offset, size) {
    let value = 0;
    for (let i = 0; i < size; i += 1) value = value * 256 + bytes[offset + i];
    return value;
  }

  function readSigned(bytes, offset, size) {
    let value = 0n;
    for (let i = 0; i < size; i += 1) value = (value << 8n) | BigInt(bytes[offset + i]);
    const bits = BigInt(size * 8);
    const sign = 1n << (bits - 1n);
    if (value >= sign) value -= 1n << bits;
    const max = BigInt(Number.MAX_SAFE_INTEGER);
    if (value <= max && value >= -max) return String(Number(value));
    return value.toString();
  }

  function readFloat(bytes, offset, size) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + offset, size);
    if (size === 4) return view.getFloat32(0, false);
    if (size === 8) return view.getFloat64(0, false);
    throw new Error("Unsupported real size");
  }

  function appleDate(seconds) {
    if (!Number.isFinite(seconds)) return String(seconds);
    const date = new Date(Date.UTC(2001, 0, 1) + seconds * 1000);
    if (Number.isNaN(date.getTime())) return String(seconds);
    return date.toISOString();
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return `${date.toLocaleString()} · ${value}`;
  }

  function contentBytes(result) {
    const content = result.content == null ? "" : String(result.content);
    if (result.encoding === "base64") return base64ToBytes(content);
    if (content.startsWith("bplist") && [...content].every((char) => char.charCodeAt(0) < 256)) {
      return Uint8Array.from(content, (char) => char.charCodeAt(0) & 255);
    }
    return new TextEncoder().encode(content);
  }

  function readableText(bytes) {
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (_error) {
      return null;
    }
    const sample = text.slice(0, 4000);
    let controls = 0;
    for (const char of sample) {
      const code = char.codePointAt(0);
      if (code === 9 || code === 10 || code === 13) continue;
      if (code < 32) controls += 1;
    }
    if (sample.length && controls > sample.length * 0.02) return null;
    return text;
  }

  function decodeBytes(bytes) {
    if (bytes.length >= 2 && bytes[0] === 0xFF && bytes[1] === 0xFE) {
      return new TextDecoder("utf-16le").decode(bytes);
    }
    if (bytes.length >= 2 && bytes[0] === 0xFE && bytes[1] === 0xFF) {
      return new TextDecoder("utf-16be").decode(bytes);
    }
    return readableText(bytes);
  }

  function ftypBrand(bytes) {
    if (ascii(bytes, 4, 4) !== "ftyp") return "";
    return ascii(bytes, 8, 4).replace(/\0/g, "").trim();
  }

  function imageMagic(bytes) {
    if (starts(bytes, [0x89, 0x50, 0x4E, 0x47])) return "image/png";
    if (starts(bytes, [0xFF, 0xD8, 0xFF])) return "image/jpeg";
    if (starts(bytes, [0x47, 0x49, 0x46, 0x38])) return "image/gif";
    if (ascii(bytes, 0, 4) === "RIFF" && ascii(bytes, 8, 4) === "WEBP") return "image/webp";
    if (starts(bytes, [0x42, 0x4D])) return "image/bmp";
    if (starts(bytes, [0x00, 0x00, 0x01, 0x00])) return "image/x-icon";
    if (starts(bytes, [0x49, 0x49, 0x2A, 0x00]) || starts(bytes, [0x4D, 0x4D, 0x00, 0x2A])) return "image/tiff";
    const brand = ftypBrand(bytes);
    if (brand === "avif" || brand === "avis") return "image/avif";
    if (HEIF_BRANDS.has(brand)) return "image/heic";
    return "";
  }

  function audioMagic(bytes) {
    if (ascii(bytes, 0, 3) === "ID3") return "audio/mpeg";
    if (ascii(bytes, 0, 4) === "fLaC") return "audio/flac";
    if (ascii(bytes, 0, 4) === "OggS") return "audio/ogg";
    if (ascii(bytes, 0, 4) === "RIFF" && ascii(bytes, 8, 4) === "WAVE") return "audio/wav";
    if (ascii(bytes, 0, 4) === "caff") return "audio/x-caf";
    if (ascii(bytes, 0, 4) === "FORM" && (ascii(bytes, 8, 4) === "AIFF" || ascii(bytes, 8, 4) === "AIFC")) {
      return "audio/aiff";
    }
    if (AUDIO_BRANDS.has(ftypBrand(bytes))) return "audio/mp4";
    return "";
  }

  function videoMagic(bytes) {
    const brand = ftypBrand(bytes);
    if (brand && !HEIF_BRANDS.has(brand) && !AUDIO_BRANDS.has(brand)) return "video/mp4";
    return "";
  }

  function extensionFallback(extension) {
    if (IMAGE_MIME[extension]) {
      return { kind: "image", mime: IMAGE_MIME[extension], label: extension === "svg" ? "SVG" : "Image" };
    }
    if (extension === "mp3") return { kind: "audio", mime: "audio/mpeg", label: "Audio" };
    if (extension === "wav") return { kind: "audio", mime: "audio/wav", label: "Audio" };
    if (extension === "caf") return { kind: "audio", mime: "audio/x-caf", label: "Audio" };
    if (extension === "aiff") return { kind: "audio", mime: "audio/aiff", label: "Audio" };
    if (extension === "m4a" || extension === "aac") return { kind: "audio", mime: "audio/mp4", label: "Audio" };
    if (extension === "mp4" || extension === "mov" || extension === "m4v") {
      return { kind: "video", mime: "video/mp4", label: "Video" };
    }
    if (extension === "pdf") return { kind: "pdf", mime: "application/pdf", label: "PDF" };
    return null;
  }

  function isXmlPlist(text) {
    return text.includes("<plist") && (text.includes("<dict") || text.includes("<array") || text.includes("<string") || text.includes("<true") || text.includes("<false") || text.includes("<integer") || text.includes("<real") || text.includes("<data") || text.includes("<date"));
  }

  function looksJson(text) {
    if (!text.startsWith("{") && !text.startsWith("[")) return false;
    try {
      JSON.parse(text);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function classify(bytes, text, path) {
    const extension = extOf(path);
    if (!bytes.length) return { kind: "empty", label: "Empty file" };
    if (ascii(bytes, 0, 6) === "bplist") {
      const supported = ascii(bytes, 0, 8) === "bplist00";
      return {
        kind: "plist",
        format: supported ? "binary" : "binary-unsupported",
        label: "Binary property list",
      };
    }
    if (ascii(bytes, 0, 15) === "SQLite format 3") return { kind: "sqlite", label: "SQLite database" };
    const image = imageMagic(bytes);
    if (image) return { kind: "image", mime: image, label: "Image" };
    if (ascii(bytes, 0, 4) === "%PDF") return { kind: "pdf", mime: "application/pdf", label: "PDF" };
    const audio = audioMagic(bytes);
    if (audio) return { kind: "audio", mime: audio, label: "Audio" };
    const video = videoMagic(bytes);
    if (video) return { kind: "video", mime: video, label: "Video" };
    if (text != null) {
      const trimmed = text.replace(/^\uFEFF/, "").trim();
      if (isXmlPlist(trimmed)) return { kind: "plist", format: "xml", label: "XML property list" };
      if (/<svg[\s>/]/i.test(trimmed)) return { kind: "image", mime: "image/svg+xml", label: "SVG" };
      if (looksJson(trimmed)) return { kind: "json", label: "JSON" };
      if (trimmed.startsWith("<")) return { kind: "xml", label: "XML" };
      return { kind: "text", label: "Text" };
    }
    return extensionFallback(extension) || { kind: "binary", label: "Binary" };
  }

  function hexDump(bytes, limit) {
    const count = Math.min(bytes.length, limit);
    const lines = [];
    for (let offset = 0; offset < count; offset += 16) {
      const chunk = bytes.subarray(offset, Math.min(count, offset + 16));
      const hex = [...chunk].map((byte) => byte.toString(16).padStart(2, "0")).join(" ");
      const glyphs = [...chunk].map((byte) => (byte >= 32 && byte < 127 ? String.fromCharCode(byte) : ".")).join("");
      lines.push(`${offset.toString(16).padStart(8, "0")}  ${hex.padEnd(47, " ")}  ${glyphs}`);
    }
    if (bytes.length > count) lines.push(`… ${bytes.length - count} more bytes`);
    return lines.join("\n");
  }

  function readCount(bytes, offset, info) {
    if (info !== 0x0f) return { count: info, next: offset };
    const marker = bytes[offset];
    const size = 1 << (marker & 0x0f);
    if ((marker >> 4) !== 0x1 || size > 8) throw new Error("Bad property list length");
    const count = readUInt(bytes, offset + 1, size);
    if (count > bytes.length) throw new Error("Property list length is out of range");
    return { count, next: offset + 1 + size };
  }

  function parseBinaryPlist(bytes) {
    if (ascii(bytes, 0, 8) !== "bplist00") throw new Error("Only bplist00 can be previewed");
    if (bytes.length < 40) throw new Error("Property list is truncated");
    const trailer = bytes.length - 32;
    const offsetSize = bytes[trailer + 6];
    const refSize = bytes[trailer + 7];
    const numObjects = readUInt(bytes, trailer + 8, 8);
    const top = readUInt(bytes, trailer + 16, 8);
    const table = readUInt(bytes, trailer + 24, 8);
    if (![1, 2, 4, 8].includes(offsetSize) || ![1, 2, 4, 8].includes(refSize)) {
      throw new Error("Property list trailer is invalid");
    }
    if (numObjects <= 0 || numObjects > 200000 || top >= numObjects) throw new Error("Property list is invalid");
    if (table + numObjects * offsetSize > bytes.length) throw new Error("Property list offset table is invalid");

    function objectOffset(index) {
      return readUInt(bytes, table + index * offsetSize, offsetSize);
    }

    const cache = new Map();
    const stack = new Set();

    function readIndex(index) {
      if (cache.has(index)) return cache.get(index);
      if (stack.has(index)) return { t: "string", v: `#${index}` };
      stack.add(index);
      const node = readAt(objectOffset(index));
      stack.delete(index);
      cache.set(index, node);
      return node;
    }

    function readRef(offset) {
      return readIndex(readUInt(bytes, offset, refSize));
    }

    function readAt(offset) {
      if (offset < 0 || offset >= bytes.length) throw new Error("Property list object is out of range");
      const marker = bytes[offset];
      const type = marker >> 4;
      const info = marker & 0x0f;
      let cursor = offset + 1;
      if (type === 0x0) {
        if (info === 0x8) return { t: "bool", v: false };
        if (info === 0x9) return { t: "bool", v: true };
        return { t: "null" };
      }
      if (type === 0x1) {
        const size = 1 << info;
        if (size > 16 || cursor + size > bytes.length) throw new Error("Property list integer is invalid");
        return { t: "int", v: readSigned(bytes, cursor, size) };
      }
      if (type === 0x2) return { t: "real", v: String(readFloat(bytes, cursor, 1 << info)) };
      if (type === 0x3) return { t: "date", v: appleDate(readFloat(bytes, cursor, 1 << info)) };
      if (type === 0x8) {
        const size = info + 1;
        if (cursor + size > bytes.length) throw new Error("Property list UID is invalid");
        return { t: "uid", v: readUInt(bytes, cursor, size) };
      }
      const sized = readCount(bytes, cursor, info);
      cursor = sized.next;
      const count = sized.count;
      if (count < 0) throw new Error("Property list length is invalid");
      if (type === 0x4) {
        if (cursor + count > bytes.length) throw new Error("Property list data is truncated");
        return { t: "data", v: bytes.slice(cursor, cursor + count) };
      }
      if (type === 0x5) {
        if (cursor + count > bytes.length) throw new Error("Property list string is truncated");
        return { t: "string", v: ascii(bytes, cursor, count) };
      }
      if (type === 0x6) {
        if (cursor + count * 2 > bytes.length) throw new Error("Property list string is truncated");
        return { t: "string", v: new TextDecoder("utf-16be").decode(bytes.subarray(cursor, cursor + count * 2)) };
      }
      if (type === 0xA || type === 0xC) {
        const items = [];
        for (let i = 0; i < count; i += 1) items.push(readRef(cursor + i * refSize));
        return { t: type === 0xC ? "set" : "array", v: items };
      }
      if (type === 0xD) {
        const entries = [];
        const values = cursor + count * refSize;
        for (let i = 0; i < count; i += 1) {
          const key = readRef(cursor + i * refSize);
          const value = readRef(values + i * refSize);
          entries.push([key.t === "string" ? key.v : `#${i}`, value]);
        }
        return { t: "dict", v: entries };
      }
      throw new Error("Unsupported property list object");
    }

    return readIndex(top);
  }

  function decodeEntities(value) {
    return value.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (all, entity) => {
      if (entity === "amp") return "&";
      if (entity === "lt") return "<";
      if (entity === "gt") return ">";
      if (entity === "quot") return '"';
      if (entity === "apos") return "'";
      if (entity[0] !== "#") return all;
      const code = entity[1] === "x" || entity[1] === "X"
        ? Number.parseInt(entity.slice(2), 16)
        : Number.parseInt(entity.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : all;
    });
  }

  function parseXml(text) {
    let index = 0;
    function parseNodes() {
      const nodes = [];
      while (index < text.length) {
        if (text.startsWith("</", index)) break;
        if (text.startsWith("<?", index)) {
          const end = text.indexOf("?>", index);
          index = end < 0 ? text.length : end + 2;
          continue;
        }
        if (text.startsWith("<!--", index)) {
          const end = text.indexOf("-->", index);
          index = end < 0 ? text.length : end + 3;
          continue;
        }
        if (text.startsWith("<!", index)) {
          const end = text.indexOf(">", index);
          index = end < 0 ? text.length : end + 1;
          continue;
        }
        if (text[index] === "<") {
          const start = index;
          nodes.push(parseElement());
          if (index === start) index += 1;
          continue;
        }
        const start = index;
        while (index < text.length && text[index] !== "<") index += 1;
        const raw = text.slice(start, index);
        if (raw.trim()) nodes.push({ text: decodeEntities(raw) });
      }
      return nodes;
    }
    function parseElement() {
      index += 1;
      const nameStart = index;
      while (index < text.length && /[A-Za-z0-9:_-]/.test(text[index])) index += 1;
      const name = text.slice(nameStart, index);
      while (index < text.length && text[index] !== ">" && text[index] !== "/") index += 1;
      if (text[index] === "/") {
        index += 1;
        if (text[index] === ">") index += 1;
        return { name, children: [] };
      }
      if (text[index] === ">") index += 1;
      const children = parseNodes();
      if (text.startsWith("</", index)) {
        const end = text.indexOf(">", index);
        index = end < 0 ? text.length : end + 1;
      }
      return { name, children };
    }
    return { name: "#doc", children: parseNodes() };
  }

  function elementChildren(node) {
    return (node.children || []).filter((child) => child.name);
  }

  function textOf(node) {
    return (node.children || []).map((child) => child.text || "").join("");
  }

  function xmlNode(node) {
    switch (node.name) {
      case "dict": {
        const kids = elementChildren(node);
        const entries = [];
        for (let i = 0; i < kids.length; i += 2) {
          if (!kids[i + 1]) break;
          entries.push([textOf(kids[i]), xmlNode(kids[i + 1])]);
        }
        return { t: "dict", v: entries };
      }
      case "array":
        return { t: "array", v: elementChildren(node).map(xmlNode) };
      case "string":
        return { t: "string", v: textOf(node) };
      case "integer":
        return { t: "int", v: textOf(node).trim() };
      case "real":
        return { t: "real", v: textOf(node).trim() };
      case "true":
        return { t: "bool", v: true };
      case "false":
        return { t: "bool", v: false };
      case "date":
        return { t: "date", v: textOf(node).trim() };
      case "data":
        return { t: "data", v: base64ToBytes(textOf(node)) };
      default:
        return { t: "string", v: textOf(node) };
    }
  }

  function parseXmlPlist(text) {
    const doc = parseXml(text);
    const plist = (doc.children || []).find((child) => child.name === "plist");
    const body = plist && elementChildren(plist)[0];
    if (!body) throw new Error("Property list has no root");
    return xmlNode(body);
  }

  function jsonNode(value) {
    if (value === null) return { t: "null" };
    if (Array.isArray(value)) return { t: "array", v: value.map(jsonNode) };
    if (typeof value === "object") {
      return { t: "dict", v: Object.entries(value).map(([key, item]) => [key, jsonNode(item)]) };
    }
    if (typeof value === "string") return { t: "string", v: value };
    if (typeof value === "boolean") return { t: "bool", v: value };
    if (typeof value === "number") return { t: Number.isInteger(value) ? "int" : "real", v: String(value) };
    return { t: "string", v: String(value) };
  }

  function uidValue(node) {
    if (!node) return null;
    if (node.t === "uid") return node.v;
    if (node.t === "dict" && node.v.length === 1 && node.v[0][0] === "CF$UID") {
      const inner = node.v[0][1];
      if (inner && (inner.t === "int" || inner.t === "real")) return Number(inner.v);
    }
    return null;
  }

  function classNameOf(node) {
    if (!node) return "";
    if (node.t === "string") return node.v;
    if (node.t === "object" && node.className) return node.className;
    if (node.t === "dict") {
      const hit = node.v.find(([key]) => key === "$classname");
      if (hit && hit[1].t === "string") return hit[1].v;
    }
    return "";
  }

  function expand(node, objects, trail) {
    if (!node || trail.size > 40) return node;
    const uid = uidValue(node);
    if (uid != null) {
      if (uid === 0) return { t: "null" };
      if (uid < 0 || uid >= objects.length) return { t: "string", v: `bad uid ${uid}` };
      if (trail.has(uid)) return { t: "string", v: `#${uid}` };
      const next = new Set(trail);
      next.add(uid);
      return expand(objects[uid], objects, next);
    }
    if (node.t === "array" || node.t === "set") {
      return { t: node.t, v: node.v.map((item) => expand(item, objects, trail)) };
    }
    if (node.t !== "dict") return node;
    let className = "";
    const entries = [];
    for (const [key, value] of node.v) {
      if (key === "$class") {
        className = classNameOf(expand(value, objects, trail));
        continue;
      }
      entries.push([key, expand(value, objects, trail)]);
    }
    if ((className === "NSArray" || className === "NSMutableArray") && entries.length === 1 && entries[0][0] === "NS.objects") {
      return entries[0][1];
    }
    if ((className === "NSDictionary" || className === "NSMutableDictionary")) {
      const keys = entries.find(([key]) => key === "NS.keys");
      const values = entries.find(([key]) => key === "NS.objects");
      if (keys && values && keys[1].t === "array" && values[1].t === "array") {
        return {
          t: "dict",
          v: keys[1].v.map((keyNode, index) => [
            keyNode.t === "string" ? keyNode.v : `#${index}`,
            values[1].v[index] || { t: "null" },
          ]),
        };
      }
    }
    if ((className === "NSString" || className === "NSMutableString")) {
      const string = entries.find(([key]) => key === "NS.string");
      if (string) return string[1];
    }
    if ((className === "NSData" || className === "NSMutableData")) {
      const data = entries.find(([key]) => key === "NS.data");
      if (data) return data[1];
    }
    if (className === "NSDate") {
      const time = entries.find(([key]) => key === "NS.time");
      if (time) return { t: "date", v: appleDate(Number(time[1].v)) };
    }
    if (className) return { t: "object", className, v: entries };
    return { t: "dict", v: entries };
  }

  function presentPlist(node) {
    if (!node || node.t !== "dict") return { node, banner: "" };
    const map = new Map(node.v);
    const archiver = map.get("$archiver");
    const objects = map.get("$objects");
    const top = map.get("$top");
    if (!archiver || archiver.t !== "string" || archiver.v !== "NSKeyedArchiver") return { node, banner: "" };
    if (!objects || objects.t !== "array" || !top || top.t !== "dict") return { node, banner: "" };
    const entries = top.v.map(([key, value]) => [key, expand(value, objects.v, new Set())]);
    const expanded = entries.length === 1 ? entries[0][1] : { t: "dict", v: entries };
    const className = expanded && expanded.className ? ` · ${expanded.className}` : "";
    return { node: expanded, banner: `NSKeyedArchiver${className}` };
  }

  function plain(node) {
    if (!node) return null;
    if (node.t === "object") {
      const value = { __class: node.className || "" };
      for (const [key, item] of node.v) value[key] = plain(item);
      return value;
    }
    if (node.t === "dict") return Object.fromEntries(node.v.map(([key, item]) => [key, plain(item)]));
    if (node.t === "array" || node.t === "set") return node.v.map(plain);
    if (node.t === "data") return { __data: bytesToBase64(node.v) };
    if (node.t === "date") return { __date: node.v };
    if (node.t === "bool") return node.v;
    if (node.t === "null") return null;
    if (node.t === "int" || node.t === "real" || node.t === "string") return node.v;
    return null;
  }

  function typeName(node) {
    if (!node) return "";
    if (node.t === "object") return node.className || "object";
    if (node.t === "int") return "integer";
    return node.t || "";
  }

  function summaryOf(node) {
    if (!node) return "";
    if (node.t === "dict" || node.t === "object") {
      return `${node.v.length} ${node.v.length === 1 ? "key" : "keys"}`;
    }
    if (node.t === "array" || node.t === "set") {
      return `${node.v.length} ${node.v.length === 1 ? "item" : "items"}`;
    }
    return "";
  }

  function renderData(bytes) {
    const head = [...bytes.subarray(0, 24)].map((byte) => byte.toString(16).padStart(2, "0")).join(" ");
    let note = "";
    const text = readableText(bytes);
    if (text && text.trim()) note = ` <span class="muted">${escapeHtml(text.slice(0, 80))}</span>`;
    return `<span class="plist-data">${bytes.length} bytes</span> <span class="muted">${escapeHtml(head)}</span>${note}`;
  }

  function renderScalar(node) {
    if (!node) return "";
    if (node.t === "string") {
      const value = node.v.length > MAX_TEXT ? `${node.v.slice(0, MAX_TEXT)}…` : node.v;
      return `<span class="plist-string">${escapeHtml(value)}</span>`;
    }
    if (node.t === "int" || node.t === "real") return `<span class="plist-num">${escapeHtml(node.v)}</span>`;
    if (node.t === "bool") return `<span class="plist-bool">${node.v ? "true" : "false"}</span>`;
    if (node.t === "date") return `<span class="plist-date">${escapeHtml(formatDate(node.v))}</span>`;
    if (node.t === "data") return renderData(node.v);
    if (node.t === "null") return `<span class="muted">null</span>`;
    return escapeHtml(String(node.v ?? ""));
  }

  function renderNode(node, depth) {
    if (!node) return "";
    if (depth > MAX_DEPTH) return `<span class="muted">…</span>`;
    if (node.t === "dict" || node.t === "object") return renderTable(node, depth);
    if (node.t === "array" || node.t === "set") return renderList(node, depth);
    return renderScalar(node);
  }

  function renderTable(node, depth) {
    if (!node.v.length) {
      const empty = node.t === "object" ? "empty object" : "empty dictionary";
      const caption = node.className ? `<div class="plist-class">${escapeHtml(node.className)}</div>` : "";
      return `${caption}<span class="muted">${empty}</span>`;
    }
    const rows = node.v.slice(0, MAX_ITEMS).map(([key, value]) => {
      const nested = value && (value.t === "dict" || value.t === "object" || value.t === "array" || value.t === "set");
      return `<tr><th>${escapeHtml(key)}</th><td class="plist-type">${escapeHtml(typeName(value))}</td><td>${nested ? renderNode(value, depth + 1) : renderScalar(value)}</td></tr>`;
    });
    if (node.v.length > MAX_ITEMS) {
      rows.push(`<tr><td colspan="3" class="muted">${node.v.length - MAX_ITEMS} more</td></tr>`);
    }
    const caption = node.className ? `<div class="plist-class">${escapeHtml(node.className)}</div>` : "";
    return `${caption}<table class="plist-table"><tbody>${rows.join("")}</tbody></table>`;
  }

  function renderList(node, depth) {
    if (!node.v.length) return `<span class="muted">empty ${node.t === "set" ? "set" : "array"}</span>`;
    const rows = node.v.slice(0, MAX_ITEMS).map((value, index) => {
      const nested = value && (value.t === "dict" || value.t === "object" || value.t === "array" || value.t === "set");
      return `<tr><th>${index}</th><td class="plist-type">${escapeHtml(typeName(value))}</td><td>${nested ? renderNode(value, depth + 1) : renderScalar(value)}</td></tr>`;
    });
    if (node.v.length > MAX_ITEMS) {
      rows.push(`<tr><td colspan="3" class="muted">${node.v.length - MAX_ITEMS} more</td></tr>`);
    }
    return `<table class="plist-table"><tbody>${rows.join("")}</tbody></table>`;
  }

  function renderSqlite(bytes) {
    if (bytes.length < 100) return `<p class="muted">SQLite header is shorter than 100 bytes.</p>`;
    const page = readUInt(bytes, 16, 2);
    const pageSize = page === 1 ? 65536 : page;
    const encoding = readUInt(bytes, 56, 4);
    const encodings = { 1: "UTF-8", 2: "UTF-16 LE", 3: "UTF-16 BE" };
    const rows = [
      ["Page size", `${pageSize} B`],
      ["Pages", String(readUInt(bytes, 28, 4))],
      ["Text encoding", encodings[encoding] || String(encoding)],
      ["User version", String(readUInt(bytes, 60, 4))],
      ["Schema cookie", String(readUInt(bytes, 40, 4))],
      ["Application ID", `0x${readUInt(bytes, 68, 4).toString(16).padStart(8, "0")}`],
    ];
    const body = rows.map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td colspan="2">${escapeHtml(value)}</td></tr>`).join("");
    return `<table class="plist-table"><tbody>${body}</tbody></table><p class="muted">Read-only SQL above runs against this file.</p>`;
  }

  function shell(options) {
    const source = options.source
      ? `<label class="file-preview-source-label"><input class="file-preview-source-toggle" type="checkbox"> Source</label>`
      : "";
    const download = options.bytes && options.bytes.length
      ? `<button type="button" class="btn ghost" data-file-download>Download</button>`
      : "";
    const sourceBlock = options.source ? `<pre class="pre file-preview-source">${escapeHtml(options.source)}</pre>` : "";
    return `<div class="file-preview">
      <div class="file-preview-bar">
        <div class="file-preview-id">
          <strong title="${escapeAttr(options.path)}">${escapeHtml(options.path || "File")}</strong>
          <span class="muted">${escapeHtml(options.meta)}</span>
        </div>
        <div class="file-preview-actions">${source}${download}</div>
      </div>
      <div class="file-preview-view">${options.view}</div>
      ${sourceBlock}
    </div>`;
  }

  function build(result) {
    const path = result.path || "";
    const bytes = contentBytes(result);
    const text = decodeBytes(bytes);
    const info = classify(bytes, text, path);
    let view = "";
    let source = "";
    let plainValue = null;
    let extra = "";
    let mime = info.mime || "application/octet-stream";
    const filename = nameOf(path);

    if (info.kind === "empty") {
      view = `<p class="muted">This file is empty.</p>`;
    } else if (info.kind === "plist") {
      if (info.format === "binary-unsupported") {
        view = `<p class="warn-text">This binary property list version is not bplist00.</p><pre class="pre">${escapeHtml(hexDump(bytes, HEX_BYTES))}</pre>`;
      } else {
        try {
          const parsed = info.format === "xml" ? parseXmlPlist(text) : parseBinaryPlist(bytes);
          const presented = presentPlist(parsed);
          plainValue = plain(presented.node);
          extra = [summaryOf(presented.node), presented.banner].filter(Boolean).join(" · ");
          view = renderNode(presented.node, 0);
          source = info.format === "xml" ? text : hexDump(bytes, HEX_BYTES);
        } catch (error) {
          view = `<p class="warn-text">${escapeHtml(error.message || "Could not read this property list.")}</p><pre class="pre">${escapeHtml(hexDump(bytes, HEX_BYTES))}</pre>`;
        }
      }
    } else if (info.kind === "json") {
      const parsed = jsonNode(JSON.parse(text));
      plainValue = plain(parsed);
      extra = summaryOf(parsed);
      view = renderNode(parsed, 0);
      source = JSON.stringify(JSON.parse(text), null, 2);
    } else if (info.kind === "image") {
      view = `<figure class="file-preview-media"><img class="img-preview" alt="${escapeAttr(filename)}" src="data:${mime};base64,${bytesToBase64(bytes)}"><figcaption class="muted" data-file-dims></figcaption></figure>`;
      extra = mime;
    } else if (info.kind === "pdf") {
      view = `<iframe class="file-preview-frame" title="PDF preview" data-file-media></iframe>`;
    } else if (info.kind === "audio") {
      view = `<audio class="file-preview-player" controls data-file-media></audio>`;
      extra = mime;
    } else if (info.kind === "video") {
      view = `<video class="file-preview-player" controls data-file-media></video>`;
      extra = mime;
    } else if (info.kind === "sqlite") {
      view = renderSqlite(bytes);
      source = hexDump(bytes, 512);
    } else if (info.kind === "xml") {
      view = `<pre class="pre file-preview-text">${escapeHtml(text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text)}</pre>`;
    } else if (info.kind === "text") {
      const shown = text.length > 100000 ? `${text.slice(0, 100000)}\n… truncated` : text;
      view = `<pre class="pre file-preview-text">${escapeHtml(shown)}</pre>`;
      plainValue = text;
    } else {
      view = `<pre class="pre file-preview-text">${escapeHtml(hexDump(bytes, HEX_BYTES))}</pre>`;
      mime = "application/octet-stream";
    }

    const meta = [info.label, formatBytes(bytes.length), extra].filter(Boolean).join(" · ");
    return {
      html: shell({ path, meta, view, source, bytes }),
      bytes,
      mime,
      filename,
      kind: info.kind,
      plain: plainValue,
      label: info.label,
    };
  }

  function prepare(result) {
    try {
      return build(result || {});
    } catch (error) {
      const path = (result && result.path) || "";
      const bytes = new Uint8Array();
      return {
        html: shell({
          path,
          meta: "Unreadable file",
          view: `<p class="warn-text">${escapeHtml(error.message || "Could not preview this file.")}</p>`,
          source: "",
          bytes,
        }),
        bytes,
        mime: "application/octet-stream",
        filename: nameOf(path),
        kind: "binary",
        plain: null,
        label: "Unreadable file",
      };
    }
  }

  root.VLFilePreview = { prepare };
})(typeof globalThis !== "undefined" ? globalThis : this);
