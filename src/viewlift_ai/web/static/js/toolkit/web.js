/* Web development */
(() => {
  const { register, util } = window.VLKit;

  function colorOutputs(color) {
    const f = util.colorFormats(color);
    const onWhite = util.contrastRatio(f.luminance, 1);
    const onBlack = util.contrastRatio(f.luminance, 0);
    const grade = (ratio) => (ratio >= 7 ? "AAA" : ratio >= 4.5 ? "AA" : ratio >= 3 ? "AA large text" : "fails");
    return [
      { type: "swatches", label: "Color", colors: [{ hex: f.hex, css: f.rgb, label: f.hex, sub: f.rgb }] },
      {
        type: "kv",
        label: "Formats",
        rows: [
          ["HEX", f.hex],
          ["HEX with alpha", f.hex8],
          ["RGB", f.rgb],
          ["CSS Color 4", f.cssModern],
          ["HSL", f.hsl],
          ["HSV / HSB", f.hsv],
          ["UIColor (UIKit)", f.uicolor],
          ["Color (SwiftUI)", f.swiftui],
          ["NSColor (AppKit)", f.nscolor],
          ["Android ARGB", f.android],
        ],
      },
      {
        type: "kv",
        label: "Contrast (WCAG 2)",
        rows: [
          ["On white", onWhite.toFixed(2) + ":1, " + grade(onWhite)],
          ["On black", onBlack.toFixed(2) + ":1, " + grade(onBlack)],
          ["Relative luminance", f.luminance.toFixed(4)],
        ],
      },
    ];
  }

  function cssBlock(label, css) {
    return { type: "text", label, value: css };
  }

  const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  const RAW_TAGS = new Set(["pre", "script", "style", "textarea"]);
  const INLINE_TAGS = new Set(["a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "em", "i", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var"]);

  function formatHtml(source, unit) {
    const text = String(source);
    const out = [];
    let depth = 0;
    let i = 0;
    let inlineBuffer = "";
    const pushLine = (line) => {
      const trimmed = line.trim();
      if (trimmed) out.push(unit.repeat(Math.max(0, depth)) + trimmed);
    };
    const flushInline = () => {
      if (inlineBuffer.trim()) pushLine(inlineBuffer.replace(/\s+/g, " "));
      inlineBuffer = "";
    };
    while (i < text.length) {
      if (text.startsWith("<!--", i)) {
        const end = text.indexOf("-->", i + 4);
        const stop = end < 0 ? text.length : end + 3;
        flushInline();
        pushLine(text.slice(i, stop));
        i = stop;
        continue;
      }
      if (text[i] === "<") {
        const end = text.indexOf(">", i);
        if (end < 0) {
          inlineBuffer += text.slice(i);
          break;
        }
        const tag = text.slice(i, end + 1);
        const nameMatch = /^<\/?\s*([A-Za-z][\w:-]*)/.exec(tag);
        const name = nameMatch ? nameMatch[1].toLowerCase() : "";
        const closing = tag.startsWith("</");
        if (INLINE_TAGS.has(name)) {
          inlineBuffer += tag;
          i = end + 1;
          continue;
        }
        flushInline();
        if (tag.startsWith("<!") || tag.startsWith("<?")) {
          pushLine(tag);
        } else if (closing) {
          depth -= 1;
          pushLine(tag);
        } else if (RAW_TAGS.has(name) && !tag.endsWith("/>")) {
          const close = text.toLowerCase().indexOf("</" + name, end + 1);
          const stop = close < 0 ? text.length : close;
          const closeEnd = close < 0 ? text.length : text.indexOf(">", close) + 1;
          const body = text.slice(end + 1, stop);
          if (name === "pre" || name === "textarea") {
            out.push(unit.repeat(depth) + tag + body + text.slice(stop, closeEnd));
          } else {
            pushLine(tag);
            depth += 1;
            for (const line of body.split("\n")) pushLine(line);
            depth -= 1;
            pushLine(text.slice(stop, closeEnd));
          }
          i = closeEnd;
          continue;
        } else {
          pushLine(tag);
          if (!VOID_TAGS.has(name) && !tag.endsWith("/>")) depth += 1;
        }
        i = end + 1;
        continue;
      }
      const next = text.indexOf("<", i);
      const stop = next < 0 ? text.length : next;
      inlineBuffer += text.slice(i, stop);
      i = stop;
    }
    flushInline();
    return out.join("\n") + "\n";
  }

  register("web", [
    {
      slug: "color-picker",
      name: "Color picker",
      description: "Pick a color and copy it as HEX, RGB, HSL, HSV, UIColor, SwiftUI or NSColor.",
      keywords: "colour hex rgb hsl uicolor swiftui nscolor picker",
      inputs: [
        { id: "color", label: "Color", type: "color", value: "#f2a93b" },
        { id: "alpha", label: "Opacity %", type: "range", min: 0, max: 100, step: 1, value: 100 },
      ],
      run(v) {
        const color = util.parseColor(v.color);
        color.a = Number(v.alpha) / 100;
        return colorOutputs(color);
      },
    },
    {
      slug: "color-converter",
      name: "Color converter",
      description: "Paste any CSS color, hex, rgb(), hsl() or hsv() and get every other format.",
      keywords: "colour convert hex rgb hsl uicolor",
      inputs: [{ id: "text", label: "Color", type: "text", value: "rgba(29, 122, 70, 0.85)", wide: true, hint: "#1d7a46, 0x1d7a46, rgb(29 122 70), hsl(146 62% 30%), rebeccapurple" }],
      run(v) {
        return colorOutputs(util.parseColor(v.text));
      },
    },
    {
      slug: "css-gradient-generator",
      name: "CSS gradient generator",
      description: "Build linear, radial and conic gradients with a live preview.",
      keywords: "linear radial conic background",
      inputs: [
        { id: "kind", label: "Type", type: "select", options: [["linear", "Linear"], ["radial", "Radial"], ["conic", "Conic"]], value: "linear" },
        { id: "angle", label: "Angle °", type: "range", min: 0, max: 360, step: 1, value: 135, show: (v) => v.kind !== "radial" },
        { id: "shape", label: "Shape", type: "select", options: ["circle", "ellipse"], value: "circle", show: (v) => v.kind === "radial" },
        { id: "c1", label: "Color 1", type: "color", value: "#15171a" },
        { id: "p1", label: "Stop 1 %", type: "range", min: 0, max: 100, step: 1, value: 0 },
        { id: "c2", label: "Color 2", type: "color", value: "#9a5200" },
        { id: "p2", label: "Stop 2 %", type: "range", min: 0, max: 100, step: 1, value: 60 },
        { id: "use3", label: "Third color", type: "checkbox", value: true },
        { id: "c3", label: "Color 3", type: "color", value: "#f2a93b", show: (v) => v.use3 },
        { id: "p3", label: "Stop 3 %", type: "range", min: 0, max: 100, step: 1, value: 100, show: (v) => v.use3 },
      ],
      run(v) {
        const stops = [v.c1 + " " + v.p1 + "%", v.c2 + " " + v.p2 + "%"];
        if (v.use3) stops.push(v.c3 + " " + v.p3 + "%");
        const head = v.kind === "linear" ? v.angle + "deg" : v.kind === "radial" ? v.shape + " at center" : "from " + v.angle + "deg at 50% 50%";
        const value = v.kind + "-gradient(" + head + ", " + stops.join(", ") + ")";
        const swiftStops = [[v.c1, v.p1], [v.c2, v.p2]].concat(v.use3 ? [[v.c3, v.p3]] : [])
          .map(([c, p]) => {
            const f = util.colorFormats(util.parseColor(c));
            return "    .init(color: " + f.swiftui + ", location: " + Number(p) / 100 + ")";
          }).join(",\n");
        const rad = (Number(v.angle) - 90) * (Math.PI / 180);
        const sx = (0.5 - Math.cos(rad) / 2).toFixed(2);
        const sy = (0.5 - Math.sin(rad) / 2).toFixed(2);
        const ex = (0.5 + Math.cos(rad) / 2).toFixed(2);
        const ey = (0.5 + Math.sin(rad) / 2).toFixed(2);
        return [
          { type: "preview", label: "Preview", css: "width:100%;height:180px;border-radius:8px;background:" + value },
          cssBlock("CSS", "background: " + value + ";"),
          { type: "text", label: "SwiftUI (linear)", value: "LinearGradient(\n  stops: [\n" + swiftStops + "\n  ],\n  startPoint: UnitPoint(x: " + sx + ", y: " + sy + "),\n  endPoint: UnitPoint(x: " + ex + ", y: " + ey + ")\n)" },
        ];
      },
    },
    {
      slug: "box-shadow-generator",
      name: "Box shadow generator",
      description: "Design a CSS box-shadow with offsets, blur, spread and opacity.",
      keywords: "shadow elevation drop",
      inputs: [
        { id: "x", label: "Offset X", type: "range", min: -60, max: 60, step: 1, value: 0 },
        { id: "y", label: "Offset Y", type: "range", min: -60, max: 60, step: 1, value: 12 },
        { id: "blur", label: "Blur", type: "range", min: 0, max: 120, step: 1, value: 32 },
        { id: "spread", label: "Spread", type: "range", min: -40, max: 60, step: 1, value: -8 },
        { id: "color", label: "Color", type: "color", value: "#000000" },
        { id: "opacity", label: "Opacity %", type: "range", min: 0, max: 100, step: 1, value: 35 },
        { id: "inset", label: "Inset", type: "checkbox", value: false },
      ],
      run(v) {
        const c = util.parseColor(v.color);
        const a = Number(v.opacity) / 100;
        const value = (v.inset ? "inset " : "") + v.x + "px " + v.y + "px " + v.blur + "px " + v.spread + "px rgba(" + c.r + ", " + c.g + ", " + c.b + ", " + a + ")";
        return [
          { type: "preview", label: "Preview", css: "width:180px;height:120px;margin:32px auto;border-radius:10px;background:#f8f9fa;box-shadow:" + value },
          cssBlock("CSS", "box-shadow: " + value + ";"),
          { type: "text", label: "SwiftUI", value: ".shadow(color: Color.black.opacity(" + a + "), radius: " + Number(v.blur) / 2 + ", x: " + v.x + ", y: " + v.y + ")" },
          { type: "text", label: "CALayer", value: "layer.shadowColor = UIColor.black.cgColor\nlayer.shadowOpacity = " + a + "\nlayer.shadowRadius = " + Number(v.blur) / 2 + "\nlayer.shadowOffset = CGSize(width: " + v.x + ", height: " + v.y + ")" },
        ];
      },
    },
    {
      slug: "border-radius-generator",
      name: "Border radius generator",
      description: "Set each corner radius and copy the CSS.",
      keywords: "corner rounded radius",
      inputs: [
        { id: "linked", label: "All corners the same", type: "checkbox", value: false },
        { id: "tl", label: "Top left", type: "range", min: 0, max: 150, step: 1, value: 24 },
        { id: "tr", label: "Top right", type: "range", min: 0, max: 150, step: 1, value: 4, show: (v) => !v.linked },
        { id: "br", label: "Bottom right", type: "range", min: 0, max: 150, step: 1, value: 24, show: (v) => !v.linked },
        { id: "bl", label: "Bottom left", type: "range", min: 0, max: 150, step: 1, value: 4, show: (v) => !v.linked },
        { id: "unit", label: "Unit", type: "select", options: ["px", "%", "rem"], value: "px" },
      ],
      run(v) {
        const u = v.unit;
        const corners = v.linked ? [v.tl, v.tl, v.tl, v.tl] : [v.tl, v.tr, v.br, v.bl];
        const value = corners.every((c) => c === corners[0]) ? corners[0] + u : corners.map((c) => c + u).join(" ");
        return [
          { type: "preview", label: "Preview", css: "width:220px;height:140px;margin:24px auto;background:#f2a93b;border-radius:" + value },
          cssBlock("CSS", "border-radius: " + value + ";"),
          { type: "text", label: "SwiftUI (iOS 16+)", value: "UnevenRoundedRectangle(cornerRadii: .init(topLeading: " + corners[0] + ", bottomLeading: " + corners[3] + ", bottomTrailing: " + corners[2] + ", topTrailing: " + corners[1] + "))" },
        ];
      },
    },
    {
      slug: "flexbox-generator",
      name: "Flexbox generator",
      description: "Lay out items with flexbox and copy the container CSS.",
      keywords: "flex layout align justify",
      inputs: [
        { id: "direction", label: "flex-direction", type: "select", options: ["row", "row-reverse", "column", "column-reverse"], value: "row" },
        { id: "justify", label: "justify-content", type: "select", options: ["flex-start", "center", "flex-end", "space-between", "space-around", "space-evenly"], value: "space-between" },
        { id: "align", label: "align-items", type: "select", options: ["stretch", "flex-start", "center", "flex-end", "baseline"], value: "center" },
        { id: "wrap", label: "flex-wrap", type: "select", options: ["nowrap", "wrap", "wrap-reverse"], value: "wrap" },
        { id: "gap", label: "gap px", type: "range", min: 0, max: 48, step: 1, value: 12 },
        { id: "items", label: "Items", type: "range", min: 1, max: 12, step: 1, value: 5 },
      ],
      run(v) {
        const css = "display: flex;\nflex-direction: " + v.direction + ";\njustify-content: " + v.justify + ";\nalign-items: " + v.align + ";\nflex-wrap: " + v.wrap + ";\ngap: " + v.gap + "px;";
        return [
          { type: "preview", label: "Preview", css: css.replace(/\n/g, "") + "min-height:220px;padding:12px;border:1px dashed currentColor;border-radius:6px", items: Number(v.items), itemCss: (i) => "min-width:56px;padding:" + (8 + (i % 3) * 6) + "px 14px;background:#f2a93b;color:#1d1406;border-radius:4px;font:600 12px ui-monospace,monospace" },
          cssBlock("Container CSS", ".container {\n  " + css.replace(/\n/g, "\n  ") + "\n}"),
        ];
      },
    },
    {
      slug: "css-grid-generator",
      name: "CSS grid generator",
      description: "Define grid columns, rows and gaps with a live preview.",
      keywords: "grid layout template columns rows",
      inputs: [
        { id: "columns", label: "Columns", type: "range", min: 1, max: 12, step: 1, value: 3 },
        { id: "rows", label: "Rows", type: "range", min: 1, max: 8, step: 1, value: 2 },
        { id: "colSize", label: "Column size", type: "select", options: [["1fr", "1fr (equal)"], ["minmax(120px, 1fr)", "minmax(120px, 1fr)"], ["auto", "auto"], ["auto-fill", "auto-fill, minmax(140px, 1fr)"]], value: "1fr" },
        { id: "rowSize", label: "Row height", type: "text", value: "80px" },
        { id: "gap", label: "Gap px", type: "range", min: 0, max: 48, step: 1, value: 12 },
      ],
      run(v) {
        const cols = v.colSize === "auto-fill" ? "repeat(auto-fill, minmax(140px, 1fr))" : "repeat(" + v.columns + ", " + v.colSize + ")";
        const rows = "repeat(" + v.rows + ", " + (v.rowSize || "auto") + ")";
        const css = "display: grid;\ngrid-template-columns: " + cols + ";\ngrid-template-rows: " + rows + ";\ngap: " + v.gap + "px;";
        return [
          { type: "preview", label: "Preview", css: css.replace(/\n/g, "") + "padding:12px;border:1px dashed currentColor;border-radius:6px", items: Number(v.columns) * Number(v.rows), itemCss: "display:grid;place-items:center;background:#f2a93b;color:#1d1406;border-radius:4px;font:600 12px ui-monospace,monospace" },
          cssBlock("Container CSS", ".grid {\n  " + css.replace(/\n/g, "\n  ") + "\n}"),
        ];
      },
    },
    {
      slug: "html-formatter",
      name: "HTML formatter",
      description: "Indent HTML. pre, script, style and textarea contents are kept.",
      keywords: "beautify prettify markup indent",
      inputs: [
        { id: "text", label: "HTML", type: "textarea", rows: 14, value: '<!doctype html><html><head><meta charset="utf-8"><title>Player</title><style>body{margin:0}</style></head><body><main id="app"><h1>Now <em>playing</em></h1><video src="/hls/master.m3u8" controls></video><ul><li>HD</li><li>Subtitles</li></ul></main></body></html>' },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
      ],
      run(v) {
        const unit = v.indent === "tab" ? "\t" : " ".repeat(Number(v.indent));
        return { type: "text", label: "Formatted", value: formatHtml(v.text, unit), download: "formatted.html" };
      },
    },
    {
      slug: "css-formatter",
      name: "CSS formatter",
      description: "Indent CSS, one declaration per line.",
      keywords: "beautify stylesheet scss indent",
      inputs: [
        { id: "text", label: "CSS", type: "textarea", rows: 14, value: ".player{display:flex;gap:8px}.player:hover .controls,.player:focus-within .controls{opacity:1}@media (max-width:600px){.player{flex-direction:column}}/* end */" },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
        { id: "minify", label: "Minify instead", type: "checkbox", value: false },
      ],
      run(v) {
        if (v.minify) {
          const min = String(v.text).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").replace(/\s*([{}:;,>~+])\s*/g, "$1").replace(/;}/g, "}").trim();
          return { type: "text", label: "Minified", value: min };
        }
        const unit = v.indent === "tab" ? "\t" : " ".repeat(Number(v.indent));
        return { type: "text", label: "Formatted", value: util.formatBraces(v.text, { mode: "css", indent: unit, noSlashComments: true, blankBetweenBlocks: true }), download: "formatted.css" };
      },
    },
    {
      slug: "javascript-formatter",
      name: "JavaScript formatter",
      description: "Re-indent JavaScript and JSON-like code. A layout pass, not a full parser.",
      keywords: "beautify prettify js indent",
      inputs: [
        { id: "text", label: "JavaScript", type: "textarea", rows: 14, value: 'async function load(id){const res=await fetch(`/api/content/${id}`,{headers:{Accept:"application/json"}});if(!res.ok){throw new Error("HTTP "+res.status)}const data=await res.json();return data.items.filter((x)=>x.live).map((x)=>({id:x.id,title:x.title}))}' },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
      ],
      run(v) {
        const unit = v.indent === "tab" ? "\t" : " ".repeat(Number(v.indent));
        return { type: "text", label: "Formatted", value: util.formatBraces(v.text, { mode: "js", indent: unit, regex: true }), download: "formatted.js" };
      },
    },
  ]);
})();
