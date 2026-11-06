/* Text & strings */
(() => {
  const { register, util } = window.VLKit;

  function words(text) {
    return String(text)
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
      .split(/[^A-Za-z0-9]+/)
      .filter(Boolean);
  }

  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();

  const REGEX_TOKENS = [
    [/^\\d/, "digit 0-9"],
    [/^\\D/, "not a digit"],
    [/^\\w/, "word character (letter, digit, underscore)"],
    [/^\\W/, "not a word character"],
    [/^\\s/, "whitespace"],
    [/^\\S/, "not whitespace"],
    [/^\\b/, "word boundary"],
    [/^\\B/, "not a word boundary"],
    [/^\\n/, "newline"],
    [/^\\t/, "tab"],
    [/^\\u[0-9a-fA-F]{4}/, "Unicode code unit"],
    [/^\\p\{[^}]+\}/, "Unicode property (needs u flag)"],
    [/^\\[1-9]/, "back-reference to a group"],
    [/^\\k<[^>]+>/, "back-reference to a named group"],
    [/^\\./, "escaped literal"],
    [/^\(\?<=/, "lookbehind"],
    [/^\(\?<!/, "negative lookbehind"],
    [/^\(\?<[A-Za-z_]\w*>/, "named capture group"],
    [/^\(\?:/, "non-capturing group"],
    [/^\(\?=/, "lookahead"],
    [/^\(\?!/, "negative lookahead"],
    [/^\(/, "capture group"],
    [/^\)/, "end of group"],
    [/^\[\^(?:\\.|[^\]])*\]/, "none of these characters"],
    [/^\[(?:\\.|[^\]])*\]/, "one of these characters"],
    [/^\{\d+,\d*\}\??/, "repeat a range of times"],
    [/^\{\d+\}/, "repeat exactly"],
    [/^\*\?/, "zero or more, as few as possible"],
    [/^\+\?/, "one or more, as few as possible"],
    [/^\?\?/, "optional, lazy"],
    [/^\*/, "zero or more"],
    [/^\+/, "one or more"],
    [/^\?/, "optional"],
    [/^\^/, "start of input (or line with m)"],
    [/^\$/, "end of input (or line with m)"],
    [/^\./, "any character (newline only with s)"],
    [/^\|/, "or"],
  ];

  function explainRegex(pattern) {
    const rows = [];
    let rest = pattern;
    while (rest.length && rows.length < 200) {
      let matched = false;
      for (const [re, meaning] of REGEX_TOKENS) {
        const m = re.exec(rest);
        if (m) {
          rows.push([m[0], meaning]);
          rest = rest.slice(m[0].length);
          matched = true;
          break;
        }
      }
      if (!matched) {
        let literal = "";
        while (rest.length && !/^[\\()[\]{}*+?^$.|]/.test(rest)) {
          literal += rest[0];
          rest = rest.slice(1);
        }
        if (!literal) {
          literal = rest[0];
          rest = rest.slice(1);
        }
        rows.push([literal, "literal text"]);
      }
    }
    return rows;
  }

  register("text", [
    {
      slug: "case-converter",
      name: "Case converter",
      description: "camelCase, PascalCase, snake_case, kebab-case, CONSTANT_CASE, Title Case and more.",
      keywords: "camel pascal snake kebab constant title upper lower dot path swift",
      inputs: [{ id: "text", label: "Text", type: "textarea", rows: 4, value: "viewlift player beaconURL request" }],
      run(v) {
        const list = util.lines(v.text).filter((line) => line.trim());
        const convert = (fn) => list.map((line) => fn(words(line))).join("\n");
        const rows = [
          ["camelCase", convert((w) => w.map((x, i) => (i ? cap(x) : x.toLowerCase())).join(""))],
          ["PascalCase", convert((w) => w.map(cap).join(""))],
          ["snake_case", convert((w) => w.map((x) => x.toLowerCase()).join("_"))],
          ["kebab-case", convert((w) => w.map((x) => x.toLowerCase()).join("-"))],
          ["CONSTANT_CASE", convert((w) => w.map((x) => x.toUpperCase()).join("_"))],
          ["Title Case", convert((w) => w.map(cap).join(" "))],
          ["Sentence case", convert((w) => w.map((x, i) => (i ? x.toLowerCase() : cap(x))).join(" "))],
          ["dot.case", convert((w) => w.map((x) => x.toLowerCase()).join("."))],
          ["path/case", convert((w) => w.map((x) => x.toLowerCase()).join("/"))],
          ["Train-Case", convert((w) => w.map(cap).join("-"))],
          ["lower case", list.map((l) => l.toLowerCase()).join("\n")],
          ["UPPER CASE", list.map((l) => l.toUpperCase()).join("\n")],
          ["iNVERSE cASE", list.map((l) => l.replace(/./g, (c) => (c === c.toUpperCase() ? c.toLowerCase() : c.toUpperCase()))).join("\n")],
        ];
        return { type: "kv", label: "Conversions", rows };
      },
    },
    {
      slug: "regex-tester",
      name: "Regex tester",
      description: "Test JavaScript regular expressions with live highlighting, groups and a token breakdown.",
      keywords: "regexp pattern match groups nsregularexpression",
      inputs: [
        { id: "pattern", label: "Pattern", type: "text", value: "(?<scheme>https?)://(?<host>[^/\\s]+)(?<path>/\\S*)?", wide: true },
        { id: "flags", label: "Flags", type: "text", value: "g", hint: "g i m s u y d" },
        { id: "replace", label: "Replace with (optional)", type: "text", value: "", hint: "$1, $<name>" },
        { id: "text", label: "Test text", type: "textarea", rows: 6, value: "Beacon https://beacon.viewlift.com/v1/event?id=42\nManifest http://cdn.example.com/hls/master.m3u8 and ftp://skip.me" },
      ],
      run(v) {
        if (!v.pattern) return { type: "note", text: "Enter a pattern." };
        const flags = String(v.flags || "").replace(/[^dgimsuyv]/g, "");
        let re;
        try {
          re = new RegExp(v.pattern, flags.includes("g") ? flags : flags + "g");
        } catch (error) {
          throw new Error("Invalid pattern: " + error.message);
        }
        const text = String(v.text);
        const rows = [];
        const ranges = [];
        let match;
        let guard = 0;
        while ((match = re.exec(text)) && guard < 5000) {
          guard += 1;
          ranges.push([match.index, match.index + match[0].length]);
          const groups = match.slice(1).map((g, i) => "$" + (i + 1) + "=" + (g === undefined ? "∅" : g));
          if (match.groups) for (const [name, value] of Object.entries(match.groups)) groups.push(name + "=" + (value === undefined ? "∅" : value));
          rows.push([rows.length + 1, match.index, match[0], groups.join("  ")]);
          if (match[0] === "") re.lastIndex += 1;
          if (!flags.includes("g")) break;
        }
        const out = [
          { type: "note", level: rows.length ? "info" : "warn", text: rows.length + " match" + (rows.length === 1 ? "" : "es") + "." },
          { type: "highlight", label: "Matches", text, ranges },
          { type: "table", label: "Match list", columns: ["#", "Index", "Match", "Groups"], rows },
        ];
        if (v.replace) {
          out.push({ type: "text", label: "Replaced", value: text.replace(new RegExp(v.pattern, flags), v.replace) });
        }
        out.push({ type: "table", label: "Pattern breakdown", columns: ["Token", "Meaning"], rows: explainRegex(v.pattern) });
        out.push({ type: "text", label: "Swift (NSRegularExpression)", value: 'let regex = try NSRegularExpression(pattern: #"' + v.pattern + '"#' + (flags.includes("i") ? ", options: [.caseInsensitive]" : "") + ")" });
        return out;
      },
    },
    {
      slug: "text-diff-checker",
      name: "Text diff checker",
      description: "Compare two blocks of text line by line and see additions and deletions.",
      keywords: "compare diff patch changes",
      inputs: [
        { id: "a", label: "Original", type: "textarea", rows: 8, value: "{\n  \"bitrate\": 3000,\n  \"codec\": \"h264\",\n  \"drm\": \"fairplay\"\n}" },
        { id: "b", label: "Changed", type: "textarea", rows: 8, value: "{\n  \"bitrate\": 4500,\n  \"codec\": \"hevc\",\n  \"drm\": \"fairplay\",\n  \"hdr\": true\n}" },
        { id: "ignoreWhitespace", label: "Ignore whitespace", type: "checkbox", value: false },
        { id: "ignoreCase", label: "Ignore case", type: "checkbox", value: false },
        { id: "changesOnly", label: "Changes only", type: "checkbox", value: false },
      ],
      run(v) {
        const ops = util.lineDiff(v.a, v.b, { ignoreWhitespace: v.ignoreWhitespace, ignoreCase: v.ignoreCase });
        const added = ops.filter((o) => o.op === "+").length;
        const removed = ops.filter((o) => o.op === "-").length;
        const lines = v.changesOnly ? ops.filter((o) => o.op !== " ") : ops;
        return [
          { type: "note", level: added || removed ? "info" : "ok", text: added || removed ? added + " added, " + removed + " removed." : "No differences." },
          { type: "diff", label: "Diff", lines },
        ];
      },
    },
    {
      slug: "word-character-counter",
      name: "Word & character counter",
      description: "Count words, characters, bytes, lines, paragraphs and reading time.",
      keywords: "count length words characters bytes utf8 utf16 reading time",
      inputs: [{ id: "text", label: "Text", type: "textarea", rows: 10, value: "Viewlift AI captures traffic from iOS and tvOS debug builds.\n\nEvery request stays on this Mac." }],
      run(v) {
        const text = String(v.text);
        const wordList = text.trim() ? text.trim().split(/\s+/) : [];
        const graphemes = typeof Intl !== "undefined" && Intl.Segmenter
          ? Array.from(new Intl.Segmenter().segment(text)).length
          : Array.from(text).length;
        const freq = new Map();
        for (const w of wordList) {
          const key = w.toLowerCase().replace(/[^\p{L}\p{N}'-]/gu, "");
          if (key.length > 2) freq.set(key, (freq.get(key) || 0) + 1);
        }
        const top = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
        return [
          {
            type: "kv",
            label: "Counts",
            rows: [
              ["Words", wordList.length],
              ["Characters (what you see)", graphemes],
              ["Characters without spaces", text.replace(/\s/g, "").length],
              ["UTF-16 units (String.count in JS, NSString.length)", text.length],
              ["Unicode scalars (Swift unicodeScalars.count)", Array.from(text).length],
              ["UTF-8 bytes", util.utf8(text).length],
              ["Lines", text ? util.lines(text).length : 0],
              ["Paragraphs", text.trim() ? text.trim().split(/\n\s*\n/).length : 0],
              ["Sentences", (text.match(/[^.!?]+[.!?]+/g) || []).length],
              ["Reading time (238 wpm)", util.formatDuration(Math.round((wordList.length / 238) * 60000))],
            ],
          },
          { type: "table", label: "Most used words", columns: ["Word", "Count"], rows: top },
        ];
      },
    },
    {
      slug: "slug-generator",
      name: "Slug generator",
      description: "Make clean, URL-friendly slugs from titles.",
      keywords: "url seo permalink",
      inputs: [
        { id: "text", label: "Titles (one per line)", type: "textarea", rows: 5, value: "Live Sports: Week 12 Highlights!\nCafé & Crème — Behind the Scenes" },
        { id: "sep", label: "Separator", type: "select", options: [["-", "Hyphen -"], ["_", "Underscore _"], [".", "Dot ."]], value: "-" },
        { id: "lower", label: "Lowercase", type: "checkbox", value: true },
        { id: "max", label: "Max length", type: "number", value: 80, min: 0, max: 500 },
      ],
      run(v) {
        const sep = v.sep || "-";
        const max = Number(v.max) || 0;
        const out = util.lines(v.text).filter((l) => l.trim()).map((line) => {
          let slug = line.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/&/g, " and ");
          if (v.lower) slug = slug.toLowerCase();
          slug = slug.replace(/[^A-Za-z0-9]+/g, sep).replace(new RegExp("^" + util.escapeRegExp(sep) + "+|" + util.escapeRegExp(sep) + "+$", "g"), "");
          if (max && slug.length > max) slug = slug.slice(0, max).replace(new RegExp(util.escapeRegExp(sep) + "[^" + util.escapeRegExp(sep) + "]*$"), "");
          return slug;
        });
        return { type: "text", label: "Slugs", value: out.join("\n") };
      },
    },
    {
      slug: "find-replace",
      name: "Find & replace",
      description: "Search and replace text, with optional regular expressions.",
      keywords: "search substitute sed",
      inputs: [
        { id: "text", label: "Text", type: "textarea", rows: 8, value: "GET https://staging.api.viewlift.com/content\nGET https://staging.api.viewlift.com/user" },
        { id: "find", label: "Find", type: "text", value: "staging." },
        { id: "replace", label: "Replace with", type: "text", value: "" },
        { id: "regex", label: "Regular expression", type: "checkbox", value: false },
        { id: "caseSensitive", label: "Match case", type: "checkbox", value: true },
        { id: "wholeWord", label: "Whole word", type: "checkbox", value: false },
      ],
      run(v) {
        if (!v.find) return { type: "text", label: "Result", value: v.text };
        let source = v.regex ? v.find : util.escapeRegExp(v.find);
        if (v.wholeWord) source = "\\b(?:" + source + ")\\b";
        let re;
        try {
          re = new RegExp(source, "g" + (v.caseSensitive ? "" : "i") + "u");
        } catch (error) {
          throw new Error("Invalid pattern: " + error.message);
        }
        const count = (String(v.text).match(re) || []).length;
        const result = String(v.text).replace(re, v.regex ? v.replace : () => v.replace);
        return [
          { type: "note", text: count + " replacement" + (count === 1 ? "" : "s") + "." },
          { type: "text", label: "Result", value: result },
        ];
      },
    },
    {
      slug: "remove-duplicate-lines",
      name: "Remove duplicate lines",
      description: "Drop repeated lines and keep the first occurrence in order.",
      keywords: "unique dedupe distinct uniq",
      inputs: [
        { id: "text", label: "Lines", type: "textarea", rows: 10, value: "com.viewlift.app\ncom.viewlift.app.tv\ncom.viewlift.app\n\ncom.viewlift.APP\ncom.viewlift.app.widget" },
        { id: "trim", label: "Trim each line", type: "checkbox", value: true },
        { id: "ignoreCase", label: "Ignore case", type: "checkbox", value: false },
        { id: "dropEmpty", label: "Remove empty lines", type: "checkbox", value: true },
      ],
      run(v) {
        const seen = new Set();
        const out = [];
        let removed = 0;
        for (const raw of util.lines(v.text)) {
          const line = v.trim ? raw.trim() : raw;
          if (v.dropEmpty && !line.trim()) continue;
          const key = v.ignoreCase ? line.toLowerCase() : line;
          if (seen.has(key)) {
            removed += 1;
            continue;
          }
          seen.add(key);
          out.push(line);
        }
        return [
          { type: "note", text: removed + " duplicate" + (removed === 1 ? "" : "s") + " removed, " + out.length + " lines kept." },
          { type: "text", label: "Unique lines", value: out.join("\n") },
        ];
      },
    },
    {
      slug: "sort-lines",
      name: "Sort lines",
      description: "Sort lines alphabetically, numerically, naturally, by length, or shuffle.",
      keywords: "order alphabetical numeric natural reverse shuffle",
      inputs: [
        { id: "text", label: "Lines", type: "textarea", rows: 10, value: "build 10\nbuild 2\nBuild 1\nbuild 21\nalpha" },
        { id: "mode", label: "Order", type: "select", options: [["natural", "Natural (2 before 10)"], ["alpha", "Alphabetical"], ["numeric", "Numeric"], ["length", "Length"], ["shuffle", "Shuffle"]], value: "natural" },
        { id: "reverse", label: "Reverse", type: "checkbox", value: false },
        { id: "ignoreCase", label: "Ignore case", type: "checkbox", value: true },
        { id: "unique", label: "Unique", type: "checkbox", value: false },
      ],
      run(v) {
        let list = util.lines(v.text).filter((l) => l !== "");
        if (v.unique) list = [...new Set(list)];
        const collator = new Intl.Collator(undefined, { numeric: v.mode === "natural", sensitivity: v.ignoreCase ? "base" : "variant" });
        if (v.mode === "shuffle") {
          for (let i = list.length - 1; i > 0; i -= 1) {
            const j = util.randomInt(i + 1);
            [list[i], list[j]] = [list[j], list[i]];
          }
        } else if (v.mode === "numeric") {
          const num = (s) => {
            const m = /-?\d+(\.\d+)?/.exec(s);
            return m ? Number(m[0]) : Number.POSITIVE_INFINITY;
          };
          list.sort((a, b) => num(a) - num(b) || collator.compare(a, b));
        } else if (v.mode === "length") list.sort((a, b) => a.length - b.length || collator.compare(a, b));
        else list.sort(collator.compare);
        if (v.reverse) list.reverse();
        return { type: "text", label: "Sorted", value: list.join("\n") };
      },
    },
  ]);
})();
