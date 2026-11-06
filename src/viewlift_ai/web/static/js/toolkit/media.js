/* Image & media */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  const OUTPUT_TYPES = [["image/png", "PNG"], ["image/jpeg", "JPEG"], ["image/webp", "WebP"]];
  const EXT = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp", "image/tiff": "tiff" };

  function needImage(file) {
    if (!file) throw new Error("Choose an image file.");
    return file;
  }

  function canvasOf(width, height) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width));
    canvas.height = Math.max(1, Math.round(height));
    return canvas;
  }

  function draw(image, width, height, options) {
    const canvas = canvasOf(width, height);
    const ctx2d = canvas.getContext("2d");
    if (options && options.background) {
      ctx2d.fillStyle = options.background;
      ctx2d.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx2d.imageSmoothingEnabled = true;
    ctx2d.imageSmoothingQuality = "high";
    if (options && options.source) {
      const [sx, sy, sw, sh] = options.source;
      ctx2d.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    } else ctx2d.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function blobFromBase64(data, mime) {
    return new Blob([util.fromBase64(data)], { type: mime });
  }

  function sizeNote(before, after) {
    const delta = before ? Math.round((1 - after / before) * 100) : 0;
    return util.formatBytes(before) + " → " + util.formatBytes(after) + (delta > 0 ? " (" + delta + "% smaller)" : delta < 0 ? " (" + -delta + "% larger)" : "");
  }

  function fitSize(w, h, maxW, maxH, mode) {
    if (mode === "exact") return [maxW || w, maxH || h];
    const scale = Math.min(maxW ? maxW / w : Infinity, maxH ? maxH / h : Infinity);
    const s = Number.isFinite(scale) ? (mode === "shrink" ? Math.min(1, scale) : scale) : 1;
    return [Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))];
  }

  /* Color extraction: bucket pixels, then merge near buckets. */

  function extractColors(image, count) {
    const scale = Math.min(1, 160 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = draw(image, image.naturalWidth * scale, image.naturalHeight * scale);
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const buckets = new Map();
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 128) continue;
      const key = ((data[i] >> 3) << 10) | ((data[i + 1] >> 3) << 5) | (data[i + 2] >> 3);
      const b = buckets.get(key) || { r: 0, g: 0, b: 0, n: 0 };
      b.r += data[i];
      b.g += data[i + 1];
      b.b += data[i + 2];
      b.n += 1;
      buckets.set(key, b);
    }
    const sorted = [...buckets.values()].map((b) => ({ r: b.r / b.n, g: b.g / b.n, b: b.b / b.n, n: b.n })).sort((a, b) => b.n - a.n);
    const picked = [];
    const total = sorted.reduce((s, c) => s + c.n, 0) || 1;
    for (const color of sorted) {
      const near = picked.find((p) => Math.hypot(p.r - color.r, p.g - color.g, p.b - color.b) < 38);
      if (near) near.n += color.n;
      else picked.push({ ...color });
      if (picked.length >= count * 3) break;
    }
    return picked.sort((a, b) => b.n - a.n).slice(0, count).map((c) => ({ r: Math.round(c.r), g: Math.round(c.g), b: Math.round(c.b), share: c.n / total }));
  }

  /* SVG */

  function parseSvg(text) {
    const doc = new DOMParser().parseFromString(String(text), "image/svg+xml");
    const error = doc.querySelector("parsererror");
    if (error) throw new Error("Invalid SVG: " + error.textContent.split("\n")[0]);
    const root = doc.documentElement;
    if (!root || root.localName !== "svg") throw new Error("The root element must be <svg>.");
    return doc;
  }

  const EDITOR_NS = /inkscape|sodipodi|sketch|adobe|figma|serif|bx|illustrator/i;

  function roundNumbers(value, precision) {
    return value.replace(/-?\d*\.\d+(?:e[+-]?\d+)?/gi, (m) => {
      const n = Number(Number(m).toFixed(precision));
      return String(n).replace(/^(-?)0\./, "$1.");
    });
  }

  function optimizeSvg(text, opts) {
    const doc = parseSvg(text);
    const root = doc.documentElement;
    const removed = { comments: 0, metadata: 0, editorAttrs: 0, empty: 0, ids: 0 };
    const walker = doc.createTreeWalker(root, 128);
    const comments = [];
    while (walker.nextNode()) comments.push(walker.currentNode);
    comments.forEach((c) => c.remove());
    removed.comments = comments.length;
    for (const node of [...root.querySelectorAll("metadata, title, desc")]) {
      if (!opts.keepTitle || node.localName === "metadata") {
        node.remove();
        removed.metadata += 1;
      }
    }
    for (const node of [...root.querySelectorAll("*")]) {
      if (EDITOR_NS.test(node.namespaceURI || "") || /^(sodipodi|inkscape):/.test(node.nodeName)) {
        node.remove();
        removed.metadata += 1;
      }
    }
    const usedIds = new Set();
    const serialized = new XMLSerializer().serializeToString(root);
    for (const m of serialized.matchAll(/(?:url\(#|href="#)([^)"]+)/g)) usedIds.add(m[1]);
    for (const node of [root, ...root.querySelectorAll("*")]) {
      for (const attr of [...node.attributes]) {
        const name = attr.name;
        if (/^xmlns:/.test(name) && EDITOR_NS.test(attr.value)) node.removeAttribute(name);
        else if (/^(sodipodi|inkscape|sketch|serif|data-name):?/.test(name) || /^xml:space$/.test(name)) {
          node.removeAttribute(name);
          removed.editorAttrs += 1;
        } else if (name === "id" && opts.removeIds && !usedIds.has(attr.value)) {
          node.removeAttribute(name);
          removed.ids += 1;
        } else if (opts.precision >= 0 && /^(d|points|transform|viewBox|x|y|x1|x2|y1|y2|cx|cy|r|rx|ry|width|height|stroke-width|offset|opacity)$/.test(name)) {
          node.setAttribute(name, roundNumbers(attr.value, opts.precision).replace(/\s+/g, " ").trim());
        }
      }
      if (node.hasAttribute("style") && !node.getAttribute("style").trim()) node.removeAttribute("style");
    }
    let changed = true;
    while (changed) {
      changed = false;
      for (const node of [...root.querySelectorAll("g, defs")]) {
        if (!node.children.length && !node.textContent.trim()) {
          node.remove();
          removed.empty += 1;
          changed = true;
        }
      }
    }
    if (root.hasAttribute("xmlns:xlink") && !/xlink:/.test(new XMLSerializer().serializeToString(root).replace(/xmlns:xlink="[^"]*"/, ""))) root.removeAttribute("xmlns:xlink");
    let out = new XMLSerializer().serializeToString(root);
    out = out.replace(/>\s+</g, "><").replace(/\s{2,}/g, " ").trim();
    if (opts.pretty) out = out.replace(/></g, ">\n<");
    return { out, removed };
  }

  function svgInfo(text) {
    const root = parseSvg(text).documentElement;
    const viewBox = root.getAttribute("viewBox");
    const parts = viewBox ? viewBox.split(/[\s,]+/).map(Number) : null;
    const width = parseFloat(root.getAttribute("width")) || (parts ? parts[2] : 300);
    const height = parseFloat(root.getAttribute("height")) || (parts ? parts[3] : 150);
    const scripts = root.querySelectorAll("script").length + [...root.querySelectorAll("*")].filter((n) => [...n.attributes].some((a) => /^on/i.test(a.name))).length;
    return { root, width, height, viewBox, elements: root.querySelectorAll("*").length, scripts, hasForeign: root.querySelectorAll("foreignObject").length > 0 };
  }

  async function svgImage(text, width, height) {
    const info = svgInfo(text);
    const root = info.root.cloneNode(true);
    if (!root.getAttribute("xmlns")) root.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    root.setAttribute("width", String(width || info.width));
    root.setAttribute("height", String(height || info.height));
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(root)], { type: "image/svg+xml" }));
    try {
      return { image: await util.loadImage(url), info };
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }

  /* Icons */

  const IOS_ICONS = [
    ["iphone", "20x20", "2x", 40], ["iphone", "20x20", "3x", 60], ["iphone", "29x29", "2x", 58], ["iphone", "29x29", "3x", 87],
    ["iphone", "40x40", "2x", 80], ["iphone", "40x40", "3x", 120], ["iphone", "60x60", "2x", 120], ["iphone", "60x60", "3x", 180],
    ["ipad", "20x20", "1x", 20], ["ipad", "20x20", "2x", 40], ["ipad", "29x29", "1x", 29], ["ipad", "29x29", "2x", 58],
    ["ipad", "40x40", "1x", 40], ["ipad", "40x40", "2x", 80], ["ipad", "76x76", "1x", 76], ["ipad", "76x76", "2x", 152],
    ["ipad", "83.5x83.5", "2x", 167], ["ios-marketing", "1024x1024", "1x", 1024],
  ];

  function makeIco(pngs) {
    const header = 6 + 16 * pngs.length;
    const total = header + pngs.reduce((n, p) => n + p.bytes.length, 0);
    const out = new Uint8Array(total);
    const view = new DataView(out.buffer);
    view.setUint16(2, 1, true);
    view.setUint16(4, pngs.length, true);
    let offset = header;
    pngs.forEach((p, i) => {
      const at = 6 + i * 16;
      out[at] = p.size >= 256 ? 0 : p.size;
      out[at + 1] = p.size >= 256 ? 0 : p.size;
      view.setUint16(at + 4, 1, true);
      view.setUint16(at + 6, 32, true);
      view.setUint32(at + 8, p.bytes.length, true);
      view.setUint32(at + 12, offset, true);
      out.set(p.bytes, offset);
      offset += p.bytes.length;
    });
    return new Blob([out], { type: "image/x-icon" });
  }

  async function pngAt(image, size, background, padding) {
    const canvas = canvasOf(size, size);
    const c = canvas.getContext("2d");
    if (background) {
      c.fillStyle = background;
      c.fillRect(0, 0, size, size);
    }
    const inset = Math.round(size * (padding || 0));
    const box = size - inset * 2;
    const ratio = Math.min(box / image.naturalWidth, box / image.naturalHeight);
    const w = image.naturalWidth * ratio;
    const h = image.naturalHeight * ratio;
    c.imageSmoothingQuality = "high";
    c.drawImage(image, (size - w) / 2, (size - h) / 2, w, h);
    return util.canvasToBlob(canvas, "image/png");
  }

  async function emojiImage(text, color) {
    const canvas = canvasOf(512, 512);
    const c = canvas.getContext("2d");
    c.fillStyle = color || "#111111";
    c.fillRect(0, 0, 512, 512);
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillStyle = "#ffffff";
    const chars = [...String(text || "V")].slice(0, 2).join("");
    c.font = "700 " + (chars.length > 1 && !/\p{Extended_Pictographic}/u.test(chars) ? 260 : 340) + "px -apple-system, system-ui, sans-serif";
    c.fillText(chars, 256, 276);
    const blob = await util.canvasToBlob(canvas, "image/png");
    return util.imageFromFile({ bytes: new Uint8Array(await blob.arrayBuffer()), type: "image/png" });
  }

  const SAMPLE_SVG = '<?xml version="1.0" encoding="UTF-8"?>\n<!-- Generator: Sketch 99 -->\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:sketch="http://www.bohemiancoding.com/sketch/ns" width="120" height="120" viewBox="0 0 120 120">\n  <title>Play</title>\n  <desc>Created with Sketch.</desc>\n  <defs></defs>\n  <g id="Page-1" sketch:type="MSPage">\n    <circle cx="60.000000" cy="60.000000" r="56.123456" fill="#e5484d"/>\n    <path d="M48.500000,38.250000 L86.750000,60.000000 L48.500000,81.750000 Z" fill="#ffffff"/>\n    <g></g>\n  </g>\n</svg>';

  register("media", [
    {
      slug: "image-format-converter",
      name: "Image format converter",
      description: "Convert between PNG, JPEG, WebP, BMP, GIF and TIFF with Pillow on this Mac. Transparency is flattened for JPEG and BMP.",
      keywords: "image convert png jpeg webp gif tiff bmp format",
      live: false,
      action: "Convert",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*,.tif,.tiff,.bmp" },
        { id: "format", label: "To", type: "select", options: ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"], value: "WEBP" },
        { id: "quality", label: "Quality (JPEG, WebP)", type: "range", value: 85, min: 1, max: 100, show: (v) => ["JPEG", "WEBP"].includes(v.format) },
      ],
      async run(v, ctx) {
        const file = needImage(v.file);
        const r = await ctx.server("image-convert", { data: util.toBase64(file.bytes), format: v.format, quality: Number(v.quality) });
        const blob = blobFromBase64(r.data, r.mime);
        return { type: "image", label: r.format + " " + r.width + "×" + r.height, blob, download: util.baseName(file.name) + "." + (EXT[r.mime] || r.format.toLowerCase()), meta: sizeNote(r.before_bytes, r.after_bytes) };
      },
    },
    {
      slug: "image-compressor",
      name: "Image compressor",
      description: "Re-encode images as JPEG or WebP at a chosen quality, optionally capping the longest side, in the browser.",
      keywords: "image compress optimize jpeg webp quality smaller",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*" },
        { id: "type", label: "Output", type: "select", options: [["image/jpeg", "JPEG"], ["image/webp", "WebP"]], value: "image/webp" },
        { id: "quality", label: "Quality", type: "range", value: 75, min: 5, max: 100 },
        { id: "max", label: "Longest side (px, 0 = keep)", type: "number", value: 2048, min: 0 },
      ],
      async run(v) {
        const file = needImage(v.file);
        const image = await util.imageFromFile(file);
        const max = Number(v.max) || 0;
        const [w, h] = max ? fitSize(image.naturalWidth, image.naturalHeight, max, max, "shrink") : [image.naturalWidth, image.naturalHeight];
        const canvas = draw(image, w, h, v.type === "image/jpeg" ? { background: "#ffffff" } : null);
        const blob = await util.canvasToBlob(canvas, v.type, Number(v.quality) / 100);
        return { type: "image", label: w + "×" + h + " " + (v.type === "image/jpeg" ? "JPEG" : "WebP") + " at " + v.quality + "%", blob, download: util.baseName(file.name) + "-compressed." + EXT[v.type], meta: sizeNote(file.bytes.length, blob.size) };
      },
    },
    {
      slug: "image-resizer",
      name: "Image resizer",
      description: "Resize to exact or bounded sizes, or export @1x, @2x and @3x for an asset catalog from an @3x source.",
      keywords: "image resize scale asset catalog @2x @3x thumbnail",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*" },
        { id: "mode", label: "Mode", type: "select", options: [["fit", "Fit inside box"], ["exact", "Exact size (stretch)"], ["percent", "Percent"], ["scales", "@1x / @2x / @3x from @3x"]], value: "fit" },
        { id: "width", label: "Width", type: "number", value: 1280, min: 1, show: (v) => ["fit", "exact"].includes(v.mode) },
        { id: "height", label: "Height", type: "number", value: 720, min: 1, show: (v) => ["fit", "exact"].includes(v.mode) },
        { id: "percent", label: "Percent", type: "range", value: 50, min: 1, max: 400, show: (v) => v.mode === "percent" },
        { id: "name", label: "Asset name", type: "text", value: "poster", show: (v) => v.mode === "scales" },
        { id: "type", label: "Format", type: "select", options: OUTPUT_TYPES, value: "image/png" },
      ],
      async run(v) {
        const file = needImage(v.file);
        const image = await util.imageFromFile(file);
        const w0 = image.naturalWidth;
        const h0 = image.naturalHeight;
        const opts = v.type === "image/jpeg" ? { background: "#ffffff" } : null;
        if (v.mode === "scales") {
          const name = String(v.name || util.baseName(file.name)).replace(/[^\w-]+/g, "-");
          const ext = EXT[v.type];
          const files = [];
          for (const [scale, suffix] of [[1 / 3, ""], [2 / 3, "@2x"], [1, "@3x"]]) {
            const blob = await util.canvasToBlob(draw(image, w0 * scale, h0 * scale, opts), v.type, 0.92);
            files.push({ name: name + ".imageset/" + name + suffix + "." + ext, blob });
          }
          const contents = { images: ["1x", "2x", "3x"].map((scale, i) => ({ idiom: "universal", filename: name + ["", "@2x", "@3x"][i] + "." + ext, scale })), info: { author: "xcode", version: 1 } };
          files.push({ name: name + ".imageset/Contents.json", blob: new Blob([JSON.stringify(contents, null, 2)], { type: "application/json" }) });
          return [{ type: "files", label: "Asset catalog image set", files, zipName: name + ".imageset.zip" }, { type: "note", text: "@3x source " + w0 + "×" + h0 + " gives @1x " + Math.round(w0 / 3) + "×" + Math.round(h0 / 3) + ". Unzip into Assets.xcassets." }];
        }
        let size;
        if (v.mode === "percent") size = [w0 * Number(v.percent) / 100, h0 * Number(v.percent) / 100];
        else size = fitSize(w0, h0, Number(v.width), Number(v.height), v.mode === "exact" ? "exact" : "fit");
        const canvas = draw(image, size[0], size[1], opts);
        const blob = await util.canvasToBlob(canvas, v.type, 0.92);
        return { type: "image", label: canvas.width + "×" + canvas.height + " (from " + w0 + "×" + h0 + ")", blob, download: util.baseName(file.name) + "-" + canvas.width + "x" + canvas.height + "." + EXT[v.type], meta: sizeNote(file.bytes.length, blob.size) };
      },
    },
    {
      slug: "image-cropper",
      name: "Image cropper",
      description: "Crop by aspect ratio (centered, with an offset) or by exact pixel box.",
      keywords: "image crop aspect ratio square poster thumbnail",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*" },
        { id: "aspect", label: "Crop", type: "select", options: [["1:1", "Square 1:1"], ["16:9", "16:9 (hero, tvOS top shelf)"], ["4:3", "4:3"], ["2:3", "2:3 (poster)"], ["9:16", "9:16 (vertical)"], ["custom", "Pixel box"]], value: "16:9" },
        { id: "focusX", label: "Horizontal position %", type: "range", value: 50, min: 0, max: 100, show: (v) => v.aspect !== "custom" },
        { id: "focusY", label: "Vertical position %", type: "range", value: 50, min: 0, max: 100, show: (v) => v.aspect !== "custom" },
        { id: "x", label: "X", type: "number", value: 0, min: 0, show: (v) => v.aspect === "custom" },
        { id: "y", label: "Y", type: "number", value: 0, min: 0, show: (v) => v.aspect === "custom" },
        { id: "w", label: "Width", type: "number", value: 500, min: 1, show: (v) => v.aspect === "custom" },
        { id: "h", label: "Height", type: "number", value: 500, min: 1, show: (v) => v.aspect === "custom" },
        { id: "type", label: "Format", type: "select", options: OUTPUT_TYPES, value: "image/png" },
      ],
      async run(v) {
        const file = needImage(v.file);
        const image = await util.imageFromFile(file);
        const W = image.naturalWidth;
        const H = image.naturalHeight;
        let box;
        if (v.aspect === "custom") {
          const x = Math.min(util.clampInt(v.x, 0, W - 1), W - 1);
          const y = Math.min(util.clampInt(v.y, 0, H - 1), H - 1);
          box = [x, y, Math.min(util.clampInt(v.w, 1, W), W - x), Math.min(util.clampInt(v.h, 1, H), H - y)];
        } else {
          const [a, b] = v.aspect.split(":").map(Number);
          let w = W;
          let h = Math.round((W * b) / a);
          if (h > H) {
            h = H;
            w = Math.round((H * a) / b);
          }
          box = [Math.round((W - w) * Number(v.focusX) / 100), Math.round((H - h) * Number(v.focusY) / 100), w, h];
        }
        const canvas = draw(image, box[2], box[3], { source: box, background: v.type === "image/jpeg" ? "#ffffff" : null });
        const blob = await util.canvasToBlob(canvas, v.type, 0.92);
        return { type: "image", label: box[2] + "×" + box[3] + " at (" + box[0] + ", " + box[1] + ")", blob, download: util.baseName(file.name) + "-crop." + EXT[v.type], meta: "Source " + W + "×" + H + ". " + util.formatBytes(blob.size) };
      },
    },
    {
      slug: "image-metadata-viewer",
      name: "Image metadata viewer",
      description: "Read EXIF, GPS, IPTC, XMP, ICC and DPI from photos and screenshots.",
      keywords: "exif metadata gps location camera iptc xmp photo",
      inputs: [{ id: "file", label: "Image", type: "file", accept: "image/*,.tif,.tiff" }],
      async run(v, ctx) {
        if (!v.file) return { type: "note", text: "Choose an image to inspect. Files are read on this Mac only." };
        const r = await ctx.server("image-metadata", { data: util.toBase64(v.file.bytes) });
        const exif = Object.entries(r.exif || {});
        const out = [{ type: "kv", label: v.file.name, rows: [["Format", r.format], ["Size", r.width + "×" + r.height], ["Mode", r.mode], ["Frames", r.frames], ["File size", util.formatBytes(r.size_bytes)], ["DPI", r.dpi ? r.dpi.join(" × ") : ""], ["ICC profile", r.icc_profile ? "yes" : "no"], ["EXIF tags", exif.length]] }];
        const gps = r.exif || {};
        const toDeg = (value, ref) => {
          if (!Array.isArray(value) || value.length < 3) return null;
          const deg = Number(value[0]) + Number(value[1]) / 60 + Number(value[2]) / 3600;
          return /[SW]/.test(String(ref)) ? -deg : deg;
        };
        const lat = toDeg(gps["GPS.GPSLatitude"], gps["GPS.GPSLatitudeRef"]);
        const lon = toDeg(gps["GPS.GPSLongitude"], gps["GPS.GPSLongitudeRef"]);
        if (lat !== null && lon !== null) out.push({ type: "note", level: "warn", text: "This photo has a GPS location: " + lat.toFixed(6) + ", " + lon.toFixed(6) + ". Strip it before sharing." });
        out.push({ type: "table", label: "EXIF", columns: ["Tag", "Value"], rows: exif.map(([k, val]) => [k, typeof val === "object" ? JSON.stringify(val) : String(val)]), empty: "No EXIF data." });
        if (r.iptc && Object.keys(r.iptc).length) out.push({ type: "table", label: "IPTC", columns: ["Field", "Value"], rows: Object.entries(r.iptc).map(([k, val]) => [k, String(val)]) });
        if (r.xmp) out.push({ type: "text", label: "XMP", value: r.xmp });
        return out;
      },
    },
    {
      slug: "image-metadata-remover",
      name: "Image metadata remover",
      description: "Remove EXIF, GPS, XMP and IPTC from an image, keeping pixels and the color profile, with Pillow.",
      keywords: "exif strip remove metadata gps privacy clean",
      live: false,
      action: "Strip metadata",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*" },
        { id: "icc", label: "Keep color profile (ICC)", type: "checkbox", value: true },
      ],
      async run(v, ctx) {
        const file = needImage(v.file);
        const r = await ctx.server("image-strip", { data: util.toBase64(file.bytes), keep_icc: v.icc });
        const blob = blobFromBase64(r.data, r.mime);
        return [
          { type: "note", level: "ok", text: "Metadata removed. " + sizeNote(r.before_bytes, r.after_bytes) + "." + (r.format === "JPEG" ? " JPEG was re-encoded at quality 95." : "") },
          { type: "image", label: "Clean " + r.format, blob, download: util.baseName(file.name) + "-clean." + (EXT[r.mime] || "png") },
        ];
      },
    },
    {
      slug: "image-color-extractor",
      name: "Image color extractor",
      description: "Find the dominant colors in an image, with share percentages and CSS and SwiftUI output.",
      keywords: "dominant color palette extract image average",
      inputs: [
        { id: "file", label: "Image", type: "file", accept: "image/*" },
        { id: "count", label: "Colors", type: "range", value: 6, min: 1, max: 12 },
      ],
      async run(v) {
        if (!v.file) return { type: "note", text: "Choose an image." };
        const image = await util.imageFromFile(v.file);
        const colors = extractColors(image, util.clampInt(v.count, 1, 12)).map((c) => ({ ...util.colorFormats({ ...c, a: 1 }), share: c.share }));
        return [
          { type: "swatches", label: "Dominant colors", colors: colors.map((c) => ({ hex: c.hex, label: c.hex, sub: Math.round(c.share * 100) + "%" })) },
          { type: "table", label: "Values", columns: ["Hex", "RGB", "HSL", "SwiftUI", "Share"], rows: colors.map((c) => [c.hex, c.rgb, c.hsl, c.swiftui, (c.share * 100).toFixed(1) + "%"]) },
        ];
      },
    },
    {
      slug: "svg-optimizer",
      name: "SVG optimizer",
      description: "Strip comments, editor metadata, empty groups and unused IDs, and round coordinates.",
      keywords: "svg optimize minify clean sketch figma illustrator",
      inputs: [
        { id: "text", label: "SVG", type: "textarea", rows: 12, value: SAMPLE_SVG },
        { id: "precision", label: "Decimal places", type: "range", value: 2, min: 0, max: 6 },
        { id: "removeIds", label: "Remove unused IDs", type: "checkbox", value: true },
        { id: "keepTitle", label: "Keep <title> and <desc> (accessibility)", type: "checkbox", value: false },
        { id: "pretty", label: "One element per line", type: "checkbox", value: false },
      ],
      run(v) {
        const before = util.utf8(v.text).length;
        const { out, removed } = optimizeSvg(v.text, { precision: Number(v.precision), removeIds: v.removeIds, keepTitle: v.keepTitle, pretty: v.pretty });
        const after = util.utf8(out).length;
        return [
          { type: "note", level: "ok", text: sizeNote(before, after) + ". Removed " + removed.comments + " comments, " + removed.metadata + " metadata nodes, " + removed.editorAttrs + " editor attributes, " + removed.empty + " empty groups and " + removed.ids + " unused IDs." },
          { type: "image", label: "Preview", blob: new Blob([out], { type: "image/svg+xml" }), maxWidth: "240px" },
          { type: "text", label: "Optimized SVG", value: out, download: "optimized.svg", mime: "image/svg+xml" },
        ];
      },
    },
    {
      slug: "svg-viewer",
      name: "SVG viewer",
      description: "Preview SVG markup safely as an image (scripts never run) and show its size, viewBox and element count.",
      keywords: "svg view preview render inspect",
      inputs: [
        { id: "text", label: "SVG", type: "textarea", rows: 12, value: SAMPLE_SVG },
        { id: "file", label: "Or open an .svg file", type: "file", accept: ".svg,image/svg+xml" },
      ],
      run(v) {
        const text = v.file ? util.fromUtf8(v.file.bytes) : v.text;
        const info = svgInfo(text);
        const out = [
          { type: "image", label: "Preview", blob: new Blob([text], { type: "image/svg+xml" }), maxWidth: "100%" },
          { type: "kv", label: "Details", rows: [["Width", String(info.width)], ["Height", String(info.height)], ["viewBox", info.viewBox || "(none)"], ["Elements", String(info.elements)], ["Size", util.formatBytes(util.utf8(text).length)]] },
        ];
        if (info.scripts) out.push({ type: "note", level: "warn", text: "This SVG contains " + info.scripts + " script tags or event handlers. They do not run in this preview, but would run if the SVG were inlined into a page." });
        if (!info.viewBox) out.push({ type: "note", text: "No viewBox, so the SVG will not scale cleanly. Add viewBox=\"0 0 " + info.width + " " + info.height + "\"." });
        return out;
      },
    },
    {
      slug: "svg-to-png-converter",
      name: "SVG to PNG converter",
      description: "Rasterize SVG to PNG at any width, or as an @1x/@2x/@3x set.",
      keywords: "svg png rasterize export icon @2x @3x",
      inputs: [
        { id: "text", label: "SVG", type: "textarea", rows: 10, value: SAMPLE_SVG },
        { id: "file", label: "Or open an .svg file", type: "file", accept: ".svg,image/svg+xml" },
        { id: "mode", label: "Output", type: "select", options: [["width", "Single PNG at width"], ["scales", "@1x, @2x, @3x"]], value: "width" },
        { id: "width", label: "Width (px)", type: "number", value: 512, min: 1, max: 8192, show: (v) => v.mode === "width" },
        { id: "base", label: "@1x width (pt)", type: "number", value: 44, min: 1, max: 2048, show: (v) => v.mode === "scales" },
        { id: "background", label: "Background", type: "select", options: [["", "Transparent"], ["#ffffff", "White"], ["#000000", "Black"]], value: "" },
      ],
      async run(v) {
        const text = v.file ? util.fromUtf8(v.file.bytes) : v.text;
        const info = svgInfo(text);
        const ratio = info.height / info.width;
        const render = async (w) => {
          const width = Math.round(w);
          const height = Math.round(w * ratio);
          const { image } = await svgImage(text, width, height);
          return util.canvasToBlob(draw(image, width, height, { background: v.background || null }), "image/png");
        };
        if (v.mode === "scales") {
          const base = util.clampInt(v.base, 1, 2048);
          const files = [];
          for (const [scale, suffix] of [[1, ""], [2, "@2x"], [3, "@3x"]]) files.push({ name: "icon" + suffix + ".png", blob: await render(base * scale) });
          return { type: "files", label: "PNG set (" + base + "pt)", files, zipName: "icon-png.zip" };
        }
        const blob = await render(util.clampInt(v.width, 1, 8192));
        return { type: "image", label: util.clampInt(v.width, 1, 8192) + "×" + Math.round(util.clampInt(v.width, 1, 8192) * ratio) + " PNG", blob, download: "image.png", meta: util.formatBytes(blob.size) };
      },
    },
    {
      slug: "favicon-generator",
      name: "Favicon generator",
      description: "Make favicon.ico, Apple touch icons, Android and PWA icons and an Xcode AppIcon set from an image or emoji.",
      keywords: "favicon ico apple-touch-icon app icon appiconset pwa manifest",
      live: false,
      action: "Generate icons",
      inputs: [
        { id: "file", label: "Source image (square, at least 1024 px)", type: "file", accept: "image/*,.svg" },
        { id: "emoji", label: "Or a letter or emoji", type: "text", value: "🎬" },
        { id: "color", label: "Background", type: "color", value: "#111111" },
        { id: "padding", label: "Padding %", type: "range", value: 0, min: 0, max: 30 },
        { id: "ios", label: "Include Xcode AppIcon.appiconset", type: "checkbox", value: true },
        { id: "legacy", label: "Include every legacy iOS size (pre-Xcode 14)", type: "checkbox", value: false, show: (v) => v.ios },
        { id: "name", label: "App name (manifest)", type: "text", value: "Viewlift" },
      ],
      async run(v) {
        let image;
        if (v.file && /svg/.test(v.file.type || v.file.name)) image = (await svgImage(util.fromUtf8(v.file.bytes), 1024, 1024)).image;
        else if (v.file) image = await util.imageFromFile(v.file);
        else image = await emojiImage(v.emoji, v.color);
        const pad = Number(v.padding) / 100;
        const opaque = v.color || "#ffffff";
        const files = [];
        const icoParts = [];
        for (const size of [16, 32, 48]) {
          const blob = await pngAt(image, size, null, pad);
          icoParts.push({ size, bytes: new Uint8Array(await blob.arrayBuffer()) });
          if (size !== 48) files.push({ name: "favicon-" + size + "x" + size + ".png", blob });
        }
        files.unshift({ name: "favicon.ico", blob: makeIco(icoParts) });
        files.push({ name: "apple-touch-icon.png", blob: await pngAt(image, 180, opaque, pad) });
        files.push({ name: "android-chrome-192x192.png", blob: await pngAt(image, 192, null, pad) });
        files.push({ name: "android-chrome-512x512.png", blob: await pngAt(image, 512, null, pad) });
        const manifest = { name: v.name || "App", short_name: v.name || "App", icons: [{ src: "/android-chrome-192x192.png", sizes: "192x192", type: "image/png" }, { src: "/android-chrome-512x512.png", sizes: "512x512", type: "image/png" }], theme_color: opaque, background_color: opaque, display: "standalone" };
        files.push({ name: "site.webmanifest", blob: new Blob([JSON.stringify(manifest, null, 2)], { type: "application/manifest+json" }) });
        if (v.ios) {
          const set = v.legacy ? IOS_ICONS : [["universal", "1024x1024", "1x", 1024]];
          const images = [];
          const rendered = new Map();
          for (const [idiom, size, scale, px] of set) {
            const filename = "Icon-" + px + ".png";
            if (!rendered.has(px)) {
              rendered.set(px, true);
              files.push({ name: "AppIcon.appiconset/" + filename, blob: await pngAt(image, px, opaque, pad) });
            }
            images.push(idiom === "universal" ? { filename, idiom, platform: "ios", size } : { filename, idiom, scale, size });
          }
          files.push({ name: "AppIcon.appiconset/Contents.json", blob: new Blob([JSON.stringify({ images, info: { author: "xcode", version: 1 } }, null, 2)], { type: "application/json" }) });
        }
        const html = '<link rel="icon" href="/favicon.ico" sizes="48x48">\n<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">\n<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">\n<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">\n<link rel="manifest" href="/site.webmanifest">';
        const preview = files.find((f) => f.name === "apple-touch-icon.png").blob;
        return [
          { type: "image", label: "Apple touch icon (180×180)", blob: preview, maxWidth: "120px" },
          { type: "files", label: files.length + " files", files, zipName: "icons.zip" },
          { type: "text", label: "HTML", value: html },
          { type: "note", text: "App Store icons must be opaque, so iOS and Apple touch icons get the background color. Xcode 14+ needs only the 1024 px icon." },
        ];
      },
    },
  ]);
})();
