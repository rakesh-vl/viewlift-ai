/* JSON, XML & YAML */
(() => {
  const { register, util } = window.VLKit;

  const SAMPLE_JSON = '{"id":"vl-123","title":"Match Highlights","duration":5400,"live":false,"tags":["sports","hd"],"stream":{"hls":"https://cdn.example.com/master.m3u8","drm":{"type":"fairplay","certificate":null}}}';
  const SAMPLE_YAML = "service: player-api\nreplicas: 3\nimage: ghcr.io/viewlift/player:1.8.2\nenv:\n  LOG_LEVEL: info\n  CDN_HOST: cdn.example.com\nports:\n  - 8080\n  - 9090\n";
  const SAMPLE_XML = '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.viewlift.app</string><key>UIBackgroundModes</key><array><string>audio</string><string>fetch</string></array></dict></plist>';

  function indentOf(value) {
    if (value === "tab") return "\t";
    return Number(value) || 2;
  }

  function sortKeys(value) {
    if (Array.isArray(value)) return value.map(sortKeys);
    if (util.isPlainObject(value)) {
      const out = {};
      for (const key of Object.keys(value).sort()) out[key] = sortKeys(value[key]);
      return out;
    }
    return value;
  }

  function stats(value) {
    let keys = 0;
    let depth = 0;
    const walk = (node, level) => {
      depth = Math.max(depth, level);
      if (Array.isArray(node)) node.forEach((child) => walk(child, level + 1));
      else if (util.isPlainObject(node)) {
        for (const key of Object.keys(node)) {
          keys += 1;
          walk(node[key], level + 1);
        }
      }
    };
    walk(value, 0);
    return { keys, depth };
  }

  function parseXml(text) {
    if (typeof DOMParser === "undefined") throw new Error("XML needs a browser.");
    const doc = new DOMParser().parseFromString(String(text).trim(), "application/xml");
    const error = doc.getElementsByTagName("parsererror")[0];
    if (error) {
      const message = error.textContent.replace(/\s+/g, " ").trim();
      throw new Error("XML error: " + message.replace(/^This page contains the following errors:\s*/i, "").replace(/Below is a rendering.*$/i, ""));
    }
    return doc;
  }

  function escapeXml(text, attr) {
    let out = String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    if (attr) out = out.replace(/"/g, "&quot;");
    return out;
  }

  function serializeXml(node, unit, depth) {
    const pad = unit.repeat(depth);
    const lines = [];
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 1) {
        const attrs = Array.from(child.attributes).map((a) => " " + a.name + '="' + escapeXml(a.value, true) + '"').join("");
        const kids = Array.from(child.childNodes).filter((k) => !(k.nodeType === 3 && !k.nodeValue.trim()));
        if (!kids.length) lines.push(pad + "<" + child.tagName + attrs + "/>");
        else if (kids.length === 1 && kids[0].nodeType === 3) {
          lines.push(pad + "<" + child.tagName + attrs + ">" + escapeXml(kids[0].nodeValue.trim()) + "</" + child.tagName + ">");
        } else {
          lines.push(pad + "<" + child.tagName + attrs + ">");
          lines.push(...serializeXml(child, unit, depth + 1));
          lines.push(pad + "</" + child.tagName + ">");
        }
      } else if (child.nodeType === 3) {
        const text = child.nodeValue.trim();
        if (text) lines.push(pad + escapeXml(text));
      } else if (child.nodeType === 4) lines.push(pad + "<![CDATA[" + child.nodeValue + "]]>");
      else if (child.nodeType === 8) lines.push(pad + "<!--" + child.nodeValue + "-->");
      else if (child.nodeType === 7) lines.push(pad + "<?" + child.target + " " + child.data + "?>");
      else if (child.nodeType === 10) lines.push(pad + "<!DOCTYPE " + child.name + (child.publicId ? ' PUBLIC "' + child.publicId + '"' : "") + (child.systemId ? ' "' + child.systemId + '"' : "") + ">");
    }
    return lines;
  }

  function xmlDeclaration(text) {
    const match = /^\s*<\?xml[^?]*\?>/.exec(text);
    return match ? match[0].trim() : "";
  }

  function xmlToJson(element) {
    const obj = {};
    for (const attr of Array.from(element.attributes || [])) obj["@" + attr.name] = attr.value;
    const kids = Array.from(element.childNodes).filter((k) => k.nodeType === 1 || ((k.nodeType === 3 || k.nodeType === 4) && k.nodeValue.trim()));
    if (!kids.some((k) => k.nodeType === 1)) {
      const text = kids.map((k) => k.nodeValue).join("").trim();
      if (!Object.keys(obj).length) return text;
      if (text) obj["#text"] = text;
      return obj;
    }
    for (const kid of kids) {
      if (kid.nodeType !== 1) {
        obj["#text"] = ((obj["#text"] || "") + kid.nodeValue.trim()).trim();
        continue;
      }
      const value = xmlToJson(kid);
      if (Object.prototype.hasOwnProperty.call(obj, kid.tagName)) {
        if (!Array.isArray(obj[kid.tagName])) obj[kid.tagName] = [obj[kid.tagName]];
        obj[kid.tagName].push(value);
      } else obj[kid.tagName] = value;
    }
    return obj;
  }

  function xmlName(name) {
    const clean = String(name).replace(/[^A-Za-z0-9_.-]/g, "_");
    return /^[A-Za-z_]/.test(clean) ? clean : "_" + clean;
  }

  function jsonToXml(name, value, unit, depth) {
    const pad = unit.repeat(depth);
    const tag = xmlName(name);
    if (Array.isArray(value)) return value.map((item) => jsonToXml(name, item, unit, depth)).join("\n");
    if (value === null || value === undefined) return pad + "<" + tag + "/>";
    if (typeof value !== "object") return pad + "<" + tag + ">" + escapeXml(value) + "</" + tag + ">";
    const attrs = Object.entries(value).filter(([k]) => k.startsWith("@")).map(([k, v]) => " " + xmlName(k.slice(1)) + '="' + escapeXml(v, true) + '"').join("");
    const children = Object.entries(value).filter(([k]) => !k.startsWith("@") && k !== "#text");
    const text = value["#text"];
    if (!children.length) {
      return text === undefined ? pad + "<" + tag + attrs + "/>" : pad + "<" + tag + attrs + ">" + escapeXml(text) + "</" + tag + ">";
    }
    const inner = children.map(([k, v]) => jsonToXml(k, v, unit, depth + 1));
    if (text !== undefined) inner.unshift(unit.repeat(depth + 1) + escapeXml(text));
    return pad + "<" + tag + attrs + ">\n" + inner.join("\n") + "\n" + pad + "</" + tag + ">";
  }

  register("data", [
    {
      slug: "json-formatter",
      name: "JSON formatter",
      description: "Beautify JSON with your choice of indent and optional key sorting.",
      keywords: "pretty print beautify indent",
      inputs: [
        { id: "text", label: "JSON", type: "textarea", rows: 14, value: SAMPLE_JSON },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
        { id: "sort", label: "Sort keys", type: "checkbox", value: false },
      ],
      run(v) {
        let data = util.parseJSON(v.text);
        if (v.sort) data = sortKeys(data);
        const { keys, depth } = stats(data);
        return [
          { type: "text", label: "Formatted", value: JSON.stringify(data, null, indentOf(v.indent)), download: "formatted.json", mime: "application/json" },
          { type: "note", text: keys + " keys, depth " + depth + "." },
        ];
      },
    },
    {
      slug: "json-validator",
      name: "JSON validator",
      description: "Check JSON syntax and point to the line and column of the first error.",
      keywords: "lint check syntax error",
      inputs: [{ id: "text", label: "JSON", type: "textarea", rows: 14, value: '{\n  "title": "Match Highlights",\n  "duration": 5400,\n  "tags": ["sports", "hd",]\n}' }],
      run(v) {
        if (!String(v.text).trim()) return { type: "note", level: "warn", text: "Paste JSON to validate." };
        try {
          const data = util.parseJSON(v.text);
          const { keys, depth } = stats(data);
          const type = Array.isArray(data) ? "array" : data === null ? "null" : typeof data;
          return { type: "note", level: "ok", text: "Valid JSON. Top level is " + type + ", " + keys + " keys, depth " + depth + "." };
        } catch (error) {
          const out = [{ type: "note", level: "error", text: error.message }];
          const m = /line (\d+)/.exec(error.message);
          if (m) {
            const n = Number(m[1]);
            const all = util.lines(v.text);
            const rows = [];
            for (let i = Math.max(1, n - 2); i <= Math.min(all.length, n + 2); i += 1) rows.push([i === n ? "→ " + i : String(i), all[i - 1]]);
            out.push({ type: "table", label: "Around the error", columns: ["Line", "Text"], rows });
          }
          return out;
        }
      },
    },
    {
      slug: "json-minifier",
      name: "JSON minifier",
      description: "Remove all whitespace from JSON.",
      keywords: "compress compact one line",
      inputs: [{ id: "text", label: "JSON", type: "textarea", rows: 14, value: JSON.stringify(JSON.parse(SAMPLE_JSON), null, 2) }],
      run(v) {
        const before = util.utf8(v.text).length;
        const min = JSON.stringify(util.parseJSON(v.text));
        const after = util.utf8(min).length;
        return [
          { type: "text", label: "Minified", value: min, download: "minified.json" },
          { type: "note", text: util.formatBytes(before) + " to " + util.formatBytes(after) + " (" + (before ? Math.round((1 - after / before) * 100) : 0) + "% smaller)." },
        ];
      },
    },
    {
      slug: "json-tree-viewer",
      name: "JSON tree viewer",
      description: "Browse nested JSON as a collapsible tree.",
      keywords: "explorer inspect nested",
      inputs: [{ id: "text", label: "JSON", type: "textarea", rows: 14, value: SAMPLE_JSON }],
      run(v) {
        return { type: "tree", label: "Tree", value: util.parseJSON(v.text) };
      },
    },
    {
      slug: "json-yaml-converter",
      name: "JSON ↔ YAML converter",
      description: "Convert between JSON and YAML. YAML is parsed on this Mac.",
      keywords: "yml convert kubernetes compose",
      inputs: [
        { id: "direction", label: "Direction", type: "select", options: [["json-yaml", "JSON to YAML"], ["yaml-json", "YAML to JSON"]], value: "json-yaml" },
        { id: "text", label: "Input", type: "textarea", rows: 14, value: SAMPLE_JSON },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"]], value: "2" },
      ],
      async run(v, ctx) {
        if (v.direction === "yaml-json") {
          const docs = await util.yamlParse(ctx, v.text);
          const data = docs.length === 1 ? docs[0] : docs;
          return { type: "text", label: "JSON", value: JSON.stringify(data, null, Number(v.indent)), download: "converted.json" };
        }
        const data = util.parseJSON(v.text);
        return { type: "text", label: "YAML", value: await util.yamlDump(ctx, data, { indent: Number(v.indent) }), download: "converted.yaml" };
      },
    },
    {
      slug: "json-xml-converter",
      name: "JSON ↔ XML converter",
      description: "Convert JSON to XML and back. Attributes map to @name keys.",
      keywords: "plist convert soap",
      inputs: [
        { id: "direction", label: "Direction", type: "select", options: [["json-xml", "JSON to XML"], ["xml-json", "XML to JSON"]], value: "json-xml" },
        { id: "text", label: "Input", type: "textarea", rows: 14, value: '{"video":{"@id":"vl-123","title":"Match Highlights","tag":["sports","hd"]}}' },
        { id: "root", label: "Root element (when JSON has several keys)", type: "text", value: "root" },
      ],
      run(v) {
        if (v.direction === "xml-json") {
          const doc = parseXml(v.text);
          const root = doc.documentElement;
          return { type: "text", label: "JSON", value: JSON.stringify({ [root.tagName]: xmlToJson(root) }, null, 2) };
        }
        const data = util.parseJSON(v.text);
        let body;
        if (util.isPlainObject(data) && Object.keys(data).length === 1 && !Array.isArray(Object.values(data)[0])) {
          const [name, value] = Object.entries(data)[0];
          body = jsonToXml(name, value, "  ", 0);
        } else body = jsonToXml(v.root || "root", Array.isArray(data) ? { item: data } : data, "  ", 0);
        return { type: "text", label: "XML", value: '<?xml version="1.0" encoding="UTF-8"?>\n' + body, download: "converted.xml" };
      },
    },
    {
      slug: "yaml-formatter",
      name: "YAML formatter",
      description: "Re-indent YAML consistently. Comments are not kept.",
      keywords: "yml beautify indent",
      inputs: [
        { id: "text", label: "YAML", type: "textarea", rows: 14, value: "service:   player-api\nreplicas: 3\nenv: {LOG_LEVEL: info, CDN_HOST: cdn.example.com}\nports: [8080, 9090]\n" },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"]], value: "2" },
      ],
      async run(v, ctx) {
        const docs = await util.yamlParse(ctx, v.text);
        const text = await util.yamlDump(ctx, docs.length > 1 ? docs : docs[0] ?? null, { indent: Number(v.indent), multi: docs.length > 1 });
        return { type: "text", label: "Formatted", value: text, download: "formatted.yaml" };
      },
    },
    {
      slug: "yaml-validator",
      name: "YAML validator",
      description: "Check YAML syntax and show the parsed structure.",
      keywords: "yml lint check syntax",
      inputs: [{ id: "text", label: "YAML", type: "textarea", rows: 14, value: SAMPLE_YAML }],
      async run(v, ctx) {
        try {
          const docs = await util.yamlParse(ctx, v.text);
          return [
            { type: "note", level: "ok", text: "Valid YAML. " + docs.length + " document" + (docs.length === 1 ? "" : "s") + "." },
            { type: "tree", label: "Parsed", value: docs.length === 1 ? docs[0] : docs },
          ];
        } catch (error) {
          return { type: "note", level: "error", text: error.message };
        }
      },
    },
    {
      slug: "xml-formatter",
      name: "XML formatter",
      description: "Pretty-print XML, including plist and Info.plist files.",
      keywords: "plist beautify indent pretty",
      inputs: [
        { id: "text", label: "XML", type: "textarea", rows: 14, value: SAMPLE_XML },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
      ],
      run(v) {
        const doc = parseXml(v.text);
        const unit = v.indent === "tab" ? "\t" : " ".repeat(Number(v.indent));
        const decl = xmlDeclaration(v.text);
        const body = serializeXml(doc, unit, 0).join("\n");
        return { type: "text", label: "Formatted", value: (decl ? decl + "\n" : "") + body, download: "formatted.xml" };
      },
    },
    {
      slug: "xml-validator",
      name: "XML validator",
      description: "Check that XML is well-formed.",
      keywords: "plist lint check well formed",
      inputs: [{ id: "text", label: "XML", type: "textarea", rows: 14, value: '<config>\n  <cdn host="cdn.example.com">\n    <path>/hls</path>\n  </cdn>\n</config>' }],
      run(v) {
        try {
          const doc = parseXml(v.text);
          const count = doc.getElementsByTagName("*").length;
          return { type: "note", level: "ok", text: "Well-formed XML. Root <" + doc.documentElement.tagName + ">, " + count + " elements." };
        } catch (error) {
          return { type: "note", level: "error", text: error.message };
        }
      },
    },
  ]);
})();
