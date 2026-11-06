/* Database & SQL */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  const KEYWORDS = new Set(("select from where and or not in is null like ilike between exists as on join left right inner outer full cross natural using group by order having limit offset union all intersect except distinct insert into values update set delete create table alter drop add column index view primary key foreign references unique default check constraint if case when then else end asc desc returning with recursive over partition window rows range preceding following current row unbounded fetch first next only true false cast collate escape any some top truncate begin commit rollback transaction grant revoke on conflict do nothing replace temporary temp materialized lateral filter within interval explain analyze vacuum serial bigserial integer int bigint smallint text varchar char boolean bool numeric decimal real double precision float date time timestamp timestamptz uuid json jsonb blob bytea auto_increment autoincrement nulls")
    .split(" "));
  const CLAUSES = new Set(["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "UNION", "UNION ALL", "INTERSECT", "EXCEPT", "VALUES", "SET", "UPDATE", "INSERT INTO", "DELETE FROM", "WITH", "WITH RECURSIVE", "RETURNING", "WINDOW", "ON CONFLICT", "PARTITION BY", "CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "CREATE VIEW", "SELECT DISTINCT"]);
  const INLINE_CLAUSES = new Set(["LIMIT", "OFFSET", "FETCH", "UPDATE", "DELETE FROM", "INSERT INTO", "CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "CREATE VIEW", "ON CONFLICT"]);
  const JOINS = new Set(["JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN", "CROSS JOIN", "NATURAL JOIN", "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN", "JOIN LATERAL", "LEFT JOIN LATERAL"]);
  const COMBOS = [...CLAUSES, ...JOINS, "ORDER BY", "IS NOT", "NOT NULL", "PRIMARY KEY", "FOREIGN KEY", "DO NOTHING", "DO UPDATE"]
    .filter((c) => c.includes(" "))
    .map((c) => c.split(" "))
    .sort((a, b) => b.length - a.length);

  function tokenize(text) {
    const src = String(text);
    const tokens = [];
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      const rest = src.slice(i);
      let m;
      if (/\s/.test(c)) {
        m = /^\s+/.exec(rest);
        tokens.push({ type: "ws", value: m[0] });
      } else if (rest.startsWith("--") || (c === "#" && !/^#>/.test(rest))) {
        m = /^(--|#)[^\n]*/.exec(rest);
        tokens.push({ type: "line-comment", value: m[0] });
      } else if (rest.startsWith("/*")) {
        const end = src.indexOf("*/", i + 2);
        if (end < 0) throw new Error("Unterminated /* comment.");
        tokens.push({ type: "comment", value: src.slice(i, end + 2) });
      } else if (c === "'" || c === '"' || c === "`") {
        let j = i + 1;
        while (j < src.length) {
          if (src[j] === c && src[j + 1] === c) j += 2;
          else if (src[j] === "\\" && c === "'" ) j += 2;
          else if (src[j] === c) break;
          else j += 1;
        }
        if (j >= src.length) throw new Error("Unterminated " + c + " string starting at offset " + i + ".");
        tokens.push({ type: c === "'" ? "string" : "ident", value: src.slice(i, j + 1) });
      } else if (c === "[" && /^\[[^\]\s,]+\]/.test(rest)) {
        m = /^\[[^\]]+\]/.exec(rest);
        tokens.push({ type: "ident", value: m[0] });
      } else if (c === "$" && (m = /^\$([A-Za-z_]*)\$/.exec(rest))) {
        const end = src.indexOf(m[0], i + m[0].length);
        if (end < 0) throw new Error("Unterminated dollar-quoted string.");
        tokens.push({ type: "string", value: src.slice(i, end + m[0].length) });
      } else if ((m = /^(\d+\.?\d*(e[+-]?\d+)?|\.\d+)/i.exec(rest))) {
        tokens.push({ type: "number", value: m[0] });
      } else if ((m = /^[A-Za-z_][\w$]*/.exec(rest))) {
        tokens.push({ type: "word", value: m[0] });
      } else if ((m = /^[:@$?][A-Za-z_\d]+/.exec(rest)) && c !== ":") {
        tokens.push({ type: "param", value: m[0] });
      } else if ((m = /^(::|<>|!=|<=|>=|\|\||->>|->|#>>|#>|@>|<@|:=|=>)/.exec(rest))) {
        tokens.push({ type: m[0] === "::" ? "cast" : "op", value: m[0] });
      } else if ("(),;.".includes(c)) {
        tokens.push({ type: "punc", value: c });
      } else {
        tokens.push({ type: "op", value: c });
      }
      i += tokens[tokens.length - 1].value.length;
    }
    return tokens;
  }

  function merged(tokens) {
    const out = [];
    const words = tokens.filter((t) => t.type !== "ws");
    for (let i = 0; i < words.length; i += 1) {
      const token = words[i];
      if (token.type === "word") {
        const combo = COMBOS.find((parts) => parts.every((part, k) => words[i + k] && words[i + k].type === "word" && words[i + k].value.toUpperCase() === part));
        if (combo) {
          out.push({ type: "word", value: words.slice(i, i + combo.length).map((t) => t.value).join(" "), upper: combo.join(" ") });
          i += combo.length - 1;
          continue;
        }
        out.push({ ...token, upper: token.value.toUpperCase() });
      } else out.push(token);
    }
    return out;
  }

  function caseWord(token, mode) {
    if (token.type !== "word") return token.value;
    const isKeyword = token.upper.split(" ").every((w) => KEYWORDS.has(w.toLowerCase()));
    if (!isKeyword || mode === "preserve") return token.value;
    return mode === "lower" ? token.upper.toLowerCase() : token.upper;
  }

  function formatSql(text, options) {
    const indentUnit = options.indent;
    const tokens = merged(tokenize(text));
    const lines = [];
    let line = "";
    let level = 0;
    const stack = [];
    let between = false;
    let clause = "";
    let prev = null;

    const base = () => {
      for (let k = stack.length - 1; k >= 0; k -= 1) if (stack[k].block) return stack[k].base;
      return 0;
    };
    const inline = () => stack.length && !stack[stack.length - 1].block;
    const newline = (lvl) => {
      if (line.trim()) lines.push(line.replace(/\s+$/, ""));
      level = lvl;
      line = indentUnit.repeat(lvl);
    };
    const write = (value, spaceBefore) => {
      if (spaceBefore && line.trim() && !/[\s(]$/.test(line) && !line.endsWith("::") && !line.endsWith(".")) line += " ";
      line += value;
    };
    const nextSignificant = (index) => {
      for (let k = index + 1; k < tokens.length; k += 1) if (!/comment/.test(tokens[k].type)) return tokens[k];
      return null;
    };

    tokens.forEach((token, index) => {
      const upper = token.upper || "";
      const value = caseWord(token, options.keywordCase);
      if (token.type === "line-comment") {
        write(token.value, true);
        newline(level);
      } else if (token.type === "comment") {
        write(token.value, true);
      } else if (token.type === "word" && CLAUSES.has(upper) && !inline()) {
        clause = upper;
        between = false;
        newline(base());
        write(value);
        if (!INLINE_CLAUSES.has(upper)) newline(base() + 1);
      } else if (token.type === "word" && (upper === "LIMIT" || upper === "OFFSET" || upper === "FETCH") && !inline()) {
        newline(base());
        write(value);
      } else if (token.type === "word" && JOINS.has(upper) && !inline()) {
        newline(base() + 1);
        write(value);
      } else if (token.type === "word" && (upper === "AND" || upper === "OR") && !inline()) {
        if (between && upper === "AND") {
          between = false;
          write(value, true);
        } else {
          newline(base() + 1);
          write(value);
        }
      } else if (token.type === "word" && upper === "BETWEEN") {
        between = true;
        write(value, true);
      } else if (token.type === "word" && upper === "CASE") {
        write(value, true);
        stack.push({ block: true, base: level + 1, kind: "case", saved: level });
      } else if (token.type === "word" && (upper === "WHEN" || upper === "ELSE") && stack.length && stack[stack.length - 1].kind === "case") {
        newline(stack[stack.length - 1].base);
        write(value);
      } else if (token.type === "word" && upper === "END" && stack.length && stack[stack.length - 1].kind === "case") {
        const entry = stack.pop();
        newline(entry.saved);
        write(value);
      } else if (token.value === "(") {
        const next = nextSignificant(index);
        const sub = next && next.type === "word" && /^(SELECT|WITH|SELECT DISTINCT)$/.test(next.upper);
        const createBody = /^CREATE TABLE/.test(clause) && !stack.length;
        const spaced = prev && (prev.type === "word" ? KEYWORDS.has(prev.value.toLowerCase()) && !/^(COUNT|SUM|AVG|MIN|MAX|COALESCE|CAST)$/.test(prev.upper) : prev.type === "op" || prev.value === ",");
        write("(", spaced);
        if (sub || createBody) {
          stack.push({ block: true, base: base() + (sub ? 2 : 1), kind: sub ? "sub" : "create", saved: level });
          if (createBody) newline(base());
        } else stack.push({ block: false });
      } else if (token.value === ")") {
        const entry = stack.pop();
        if (entry && entry.block) {
          newline(entry.base - 1);
          clause = "";
        }
        write(")");
      } else if (token.value === ",") {
        write(",");
        if (!inline()) newline(base() + (stack.length && stack[stack.length - 1].kind === "create" ? 0 : 1));
      } else if (token.value === ";") {
        write(";");
        stack.length = 0;
        lines.push(line.replace(/\s+$/, ""));
        lines.push("");
        line = "";
        level = 0;
        clause = "";
      } else if (token.value === "." || token.type === "cast") {
        line = line.replace(/\s+$/, "");
        write(token.value);
      } else {
        const glue = prev && (prev.value === "." || prev.type === "cast" || prev.value === "(");
        write(value, !glue);
      }
      prev = token;
    });
    if (line.trim()) lines.push(line.replace(/\s+$/, ""));
    return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  function minifySql(text, keepComments) {
    const tokens = tokenize(text).filter((t) => t.type !== "ws" && (keepComments ? true : !/comment/.test(t.type)));
    let out = "";
    let prev = null;
    for (const token of tokens) {
      const value = token.type === "line-comment" ? "/*" + token.value.replace(/^(--|#)/, "").replace(/\*\//g, "* /") + " */" : token.value;
      if (prev) {
        const wordy = (t) => ["word", "number", "string", "ident", "param"].includes(t.type);
        if ((wordy(prev) && wordy(token)) || (prev.type === "op" && token.type === "op")) out += " ";
      }
      out += value;
      prev = token;
    }
    return out;
  }

  function splitStatements(tokens) {
    const statements = [];
    let current = [];
    for (const token of tokens) {
      if (token.value === ";") {
        if (current.length) statements.push(current);
        current = [];
      } else current.push(token);
    }
    if (current.length) statements.push(current);
    return statements;
  }

  const VERBS = /^(SELECT|SELECT DISTINCT|WITH|WITH RECURSIVE|INSERT INTO|UPDATE|DELETE FROM|CREATE|CREATE TABLE|CREATE INDEX|CREATE VIEW|ALTER|ALTER TABLE|DROP|DROP TABLE|TRUNCATE|BEGIN|COMMIT|ROLLBACK|EXPLAIN|GRANT|REVOKE|VACUUM|ANALYZE|REPLACE|MERGE|PRAGMA|SHOW|USE|SET|VALUES)$/;

  function validateSql(text) {
    const issues = [];
    let tokens;
    try {
      tokens = merged(tokenize(text)).filter((t) => !/comment/.test(t.type));
    } catch (error) {
      return [["error", "-", error.message]];
    }
    splitStatements(tokens).forEach((statement, index) => {
      const at = "Statement " + (index + 1);
      const first = statement[0];
      if (!first || first.type !== "word" || !VERBS.test(first.upper)) issues.push(["error", at, "Starts with '" + (first ? first.value : "") + "', which is not a SQL statement keyword."]);
      let depth = 0;
      for (const token of statement) {
        if (token.value === "(") depth += 1;
        if (token.value === ")") depth -= 1;
        if (depth < 0) {
          issues.push(["error", at, "A ) has no matching (."]);
          depth = 0;
        }
      }
      if (depth > 0) issues.push(["error", at, depth + " unclosed ( ."]);
      statement.forEach((token, i) => {
        const next = statement[i + 1];
        const prevToken = statement[i - 1];
        if (token.value === "," && next && (next.value === "," || next.value === ")" || (next.type === "word" && /^(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|VALUES)$/.test(next.upper)))) issues.push(["error", at, "Stray comma before '" + next.value + "'."]);
        if (token.type === "word" && next && next.type === "word" && token.upper === next.upper && CLAUSES.has(token.upper)) issues.push(["error", at, token.upper + " appears twice in a row."]);
        if ((token.value === "=" || token.value === "!=" || token.value === "<>") && next && next.upper === "NULL") issues.push(["warn", at, "'" + token.value + " NULL' is never true. Use IS NULL or IS NOT NULL."]);
        if (token.type === "word" && JOINS.has(token.upper) && !/CROSS|NATURAL/.test(token.upper)) {
          const rest = statement.slice(i + 1);
          const stop = rest.findIndex((t) => t.type === "word" && (CLAUSES.has(t.upper) || JOINS.has(t.upper)));
          const scope = stop < 0 ? rest : rest.slice(0, stop);
          if (!scope.some((t) => t.type === "word" && (t.upper === "ON" || t.upper === "USING"))) issues.push(["warn", at, token.upper + " has no ON or USING. This is a cross join."]);
        }
        if (token.value === "*" && prevToken && prevToken.type === "word" && /^SELECT/.test(prevToken.upper)) issues.push(["info", at, "SELECT * fetches every column. List the columns you need."]);
      });
      if (first && first.type === "word" && /^(UPDATE|DELETE FROM)$/.test(first.upper) && !statement.some((t) => t.upper === "WHERE")) issues.push(["warn", at, first.upper + " without WHERE changes every row."]);
      if (first && /^SELECT/.test(first.upper || "") && statement.some((t) => t.upper === "GROUP BY") === false && statement.some((t) => t.upper === "HAVING")) issues.push(["warn", at, "HAVING without GROUP BY."]);
    });
    return issues;
  }

  /* Generation */

  const DIALECTS = {
    postgres: { quote: (n) => '"' + n.replace(/"/g, '""') + '"', types: { integer: "BIGINT", real: "DOUBLE PRECISION", boolean: "BOOLEAN", date: "DATE", datetime: "TIMESTAMPTZ", text: "TEXT", json: "JSONB" }, bool: (b) => (b ? "TRUE" : "FALSE") },
    mysql: { quote: (n) => "`" + n.replace(/`/g, "``") + "`", types: { integer: "BIGINT", real: "DOUBLE", boolean: "BOOLEAN", date: "DATE", datetime: "DATETIME", text: "TEXT", json: "JSON" }, bool: (b) => (b ? "TRUE" : "FALSE") },
    sqlite: { quote: (n) => '"' + n.replace(/"/g, '""') + '"', types: { integer: "INTEGER", real: "REAL", boolean: "INTEGER", date: "TEXT", datetime: "TEXT", text: "TEXT", json: "TEXT" }, bool: (b) => (b ? "1" : "0") },
    sqlserver: { quote: (n) => "[" + n.replace(/]/g, "]]") + "]", types: { integer: "BIGINT", real: "FLOAT", boolean: "BIT", date: "DATE", datetime: "DATETIME2", text: "NVARCHAR(MAX)", json: "NVARCHAR(MAX)" }, bool: (b) => (b ? "1" : "0") },
  };
  const DIALECT_OPTIONS = [["postgres", "PostgreSQL"], ["mysql", "MySQL"], ["sqlite", "SQLite"], ["sqlserver", "SQL Server"]];

  function valueKind(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "boolean") return "boolean";
    if (typeof value === "number") return Number.isInteger(value) ? "integer" : "real";
    if (typeof value === "object") return "json";
    const text = String(value).trim();
    if (/^-?\d{1,18}$/.test(text) && !/^-?0\d/.test(text)) return "integer";
    if (/^-?\d*\.\d+(e[+-]?\d+)?$/i.test(text)) return "real";
    if (/^(true|false)$/i.test(text)) return "boolean";
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return "date";
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.test(text)) return "datetime";
    return "text";
  }

  function columnKind(values) {
    const kinds = new Set(values.map(valueKind).filter(Boolean));
    if (!kinds.size) return "text";
    if (kinds.size === 1) return [...kinds][0];
    if ([...kinds].every((k) => k === "integer" || k === "real")) return "real";
    if ([...kinds].every((k) => k === "date" || k === "datetime")) return "datetime";
    return "text";
  }

  function literal(value, kind, dialect) {
    if (value === null || value === undefined || (value === "" && kind !== "text")) return "NULL";
    if (kind === "boolean") return dialect.bool(typeof value === "boolean" ? value : /^true$/i.test(String(value)));
    if ((kind === "integer" || kind === "real") && !Number.isNaN(Number(value))) return String(Number(value));
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    return (dialect === DIALECTS.sqlserver && kind === "text" ? "N" : "") + "'" + text.replace(/'/g, "''") + "'";
  }

  function buildSql(table, columns, rows, opts) {
    const dialect = DIALECTS[opts.dialect];
    const kinds = columns.map((column, i) => columnKind(rows.map((row) => row[i])));
    const q = dialect.quote;
    const out = [];
    if (opts.create) {
      out.push("CREATE TABLE " + q(table) + " (\n" + columns.map((c, i) => "  " + q(c) + " " + dialect.types[kinds[i]]).join(",\n") + "\n);");
      out.push("");
    }
    const head = "INSERT INTO " + q(table) + " (" + columns.map(q).join(", ") + ") VALUES";
    const tuples = rows.map((row) => "(" + columns.map((c, i) => literal(row[i], kinds[i], dialect)).join(", ") + ")");
    if (opts.batch) {
      const size = opts.dialect === "sqlserver" ? 1000 : 500;
      for (let i = 0; i < tuples.length; i += size) out.push(head + "\n  " + tuples.slice(i, i + size).join(",\n  ") + ";");
    } else for (const tuple of tuples) out.push(head + " " + tuple + ";");
    return { sql: out.join("\n"), kinds };
  }

  function unquoteIdent(value) {
    const v = String(value);
    if (/^["`]/.test(v)) return v.slice(1, -1).replace(/""|``/g, (m) => m[0]);
    if (v.startsWith("[")) return v.slice(1, -1);
    return v;
  }

  function sqlValue(tokens) {
    if (!tokens.length) return null;
    if (tokens.length === 1) {
      const t = tokens[0];
      if (t.type === "string") {
        const raw = t.value;
        if (raw.startsWith("$")) return raw.replace(/^\$[^$]*\$|\$[^$]*\$$/g, "");
        return raw.slice(1, -1).replace(/''/g, "'").replace(/\\'/g, "'");
      }
      if (t.type === "number") return Number(t.value);
      if (t.type === "word") {
        if (t.upper === "NULL") return null;
        if (t.upper === "TRUE") return true;
        if (t.upper === "FALSE") return false;
      }
    }
    if (tokens.length === 2 && tokens[0].value === "-" && tokens[1].type === "number") return -Number(tokens[1].value);
    if (tokens.length === 2 && tokens[0].type === "word" && tokens[0].upper === "N" && tokens[1].type === "string") return sqlValue([tokens[1]]);
    const first = tokens[0];
    if (first.type === "string" && tokens[1] && tokens[1].type === "cast") return sqlValue([first]);
    return tokens.map((t) => t.value).join("");
  }

  function parseInserts(text) {
    const tokens = merged(tokenize(text)).filter((t) => !/comment/.test(t.type));
    const tables = {};
    let i = 0;
    while (i < tokens.length) {
      const t = tokens[i];
      if (!(t.type === "word" && (t.upper === "INSERT INTO" || t.upper === "REPLACE"))) {
        i += 1;
        continue;
      }
      i += 1;
      if (tokens[i] && tokens[i].upper === "INTO") i += 1;
      let name = "";
      while (tokens[i] && ((tokens[i].type === "word" && !/^(VALUES|SELECT|DEFAULT VALUES|SET)$/.test(tokens[i].upper)) || tokens[i].type === "ident" || tokens[i].value === ".")) {
        name += tokens[i].value === "." ? "." : unquoteIdent(tokens[i].value);
        i += 1;
      }
      let columns = null;
      if (tokens[i] && tokens[i].value === "(") {
        columns = [];
        i += 1;
        while (tokens[i] && tokens[i].value !== ")") {
          if (tokens[i].value !== ",") columns.push(unquoteIdent(tokens[i].value));
          i += 1;
        }
        i += 1;
      }
      if (!tokens[i] || tokens[i].upper !== "VALUES") throw new Error("INSERT into " + name + " has no VALUES list (INSERT ... SELECT is not supported).");
      i += 1;
      const rows = tables[name] || (tables[name] = []);
      while (tokens[i] && tokens[i].value === "(") {
        i += 1;
        const values = [];
        let current = [];
        let depth = 0;
        while (tokens[i] && !(depth === 0 && tokens[i].value === ")")) {
          const tok = tokens[i];
          if (tok.value === "(") depth += 1;
          if (tok.value === ")") depth -= 1;
          if (depth === 0 && tok.value === ",") {
            values.push(sqlValue(current));
            current = [];
          } else current.push(tok);
          i += 1;
        }
        values.push(sqlValue(current));
        i += 1;
        const keys = columns || values.map((v, k) => "column" + (k + 1));
        if (columns && columns.length !== values.length) throw new Error(name + ": " + columns.length + " columns but a row has " + values.length + " values.");
        rows.push(Object.fromEntries(keys.map((k, n) => [k, values[n]])));
        if (tokens[i] && tokens[i].value === ",") i += 1;
      }
    }
    return tables;
  }

  function csvTable(text, delimiter, header) {
    const sep = delimiter === "auto" ? util.detectDelimiter(text) : delimiter === "tab" ? "\t" : delimiter;
    const rows = util.parseCSV(text, sep).filter((row) => row.length > 1 || (row[0] || "").trim() !== "");
    if (!rows.length) return { columns: [], rows: [], sep };
    const width = Math.max(...rows.map((r) => r.length));
    const columns = header ? rows[0].map((c, i) => String(c).trim() || "column" + (i + 1)) : Array.from({ length: width }, (_, i) => "column" + (i + 1));
    while (columns.length < width) columns.push("column" + (columns.length + 1));
    const body = (header ? rows.slice(1) : rows).map((r) => columns.map((_, i) => (r[i] === undefined ? "" : r[i])));
    return { columns, rows: body, sep };
  }

  const SAMPLE_SQL = "select u.id, u.email, count(o.id) as orders, sum(o.total) total from users u left join orders o on o.user_id = u.id and o.status = 'paid' where u.created_at between '2026-01-01' and '2026-12-31' and (u.country = 'IN' or u.country = 'US') and u.id in (select user_id from subscriptions where active = true) group by u.id, u.email having count(o.id) > 2 order by total desc limit 20;";
  const SAMPLE_CSV = "id,title,duration,premium,published\nvl-1,\"Match Highlights, Day 1\",5400,true,2026-11-01\nvl-2,Studio Show,1800,false,2026-11-03\nvl-3,Behind the Scenes,960,true,";

  register("sql", [
    {
      slug: "sql-formatter",
      name: "SQL formatter",
      description: "Indent SQL with one clause per line, subqueries nested and keyword case normalized.",
      keywords: "sql pretty beautify query",
      inputs: [
        { id: "text", label: "SQL", type: "textarea", rows: 12, value: SAMPLE_SQL },
        { id: "keywordCase", label: "Keywords", type: "select", options: [["upper", "UPPERCASE"], ["lower", "lowercase"], ["preserve", "Keep as typed"]], value: "upper" },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
      ],
      run(v) {
        return { type: "text", label: "Formatted SQL", value: formatSql(v.text, { keywordCase: v.keywordCase, indent: v.indent === "tab" ? "\t" : " ".repeat(Number(v.indent)) }), download: "query.sql" };
      },
    },
    {
      slug: "sql-minifier",
      name: "SQL minifier",
      description: "Collapse SQL onto one line and strip comments, without touching string literals.",
      keywords: "sql compress one line minify",
      inputs: [
        { id: "text", label: "SQL", type: "textarea", rows: 12, value: "-- Active premium users\nSELECT\n  id,\n  email\nFROM users\nWHERE plan = 'premium  annual' /* keep spacing in strings */\n  AND active = TRUE;" },
        { id: "comments", label: "Keep comments", type: "checkbox", value: false },
      ],
      run(v) {
        const out = minifySql(v.text, v.comments);
        return [{ type: "text", label: "Minified", value: out }, { type: "note", text: String(v.text).length + " to " + out.length + " characters." }];
      },
    },
    {
      slug: "sql-validator",
      name: "SQL validator",
      description: "Catch unbalanced parentheses, unterminated strings, stray commas, = NULL and UPDATE without WHERE.",
      keywords: "sql lint check syntax",
      inputs: [{ id: "text", label: "SQL", type: "textarea", rows: 12, value: "SELECT id, title, FROM videos WHERE deleted_at = NULL;\nUPDATE videos SET premium = true;\nSELECT * FROM a JOIN b WHERE (a.x = 1;" }],
      run(v) {
        const issues = validateSql(v.text);
        const errors = issues.filter((i) => i[0] === "error").length;
        return [
          { type: "note", level: errors ? "error" : issues.length ? "warn" : "ok", text: errors ? errors + " errors found." : issues.length ? "No errors. " + issues.length + " suggestions." : "No problems found." },
          { type: "table", label: "Issues", columns: ["Level", "Where", "Problem"], rows: issues, rowClass: (r) => (r[0] === "error" ? "err" : r[0] === "warn" ? "warn" : "") },
          { type: "note", text: "This is a structural check, not a full dialect parser. Run the query against your database to be sure." },
        ];
      },
    },
    {
      slug: "json-to-sql",
      name: "JSON to SQL",
      description: "Turn an array of JSON objects into CREATE TABLE and INSERT statements.",
      keywords: "json sql insert create table import",
      inputs: [
        { id: "text", label: "JSON array", type: "textarea", rows: 10, value: '[{"id":1,"title":"Match Highlights","premium":true,"tags":["sports"],"published":"2026-11-01T10:00:00Z"},{"id":2,"title":"O\'Brien Interview","premium":false,"tags":[],"published":null}]' },
        { id: "table", label: "Table", type: "text", value: "videos" },
        { id: "dialect", label: "Dialect", type: "select", options: DIALECT_OPTIONS, value: "postgres" },
        { id: "create", label: "Include CREATE TABLE", type: "checkbox", value: true },
        { id: "batch", label: "Multi-row INSERT", type: "checkbox", value: true },
      ],
      run(v) {
        let data = util.parseJSON(v.text);
        if (util.isPlainObject(data)) data = [data];
        if (!Array.isArray(data) || !data.every(util.isPlainObject)) throw new Error("Input must be an object or an array of objects.");
        const columns = [...new Set(data.flatMap((row) => Object.keys(row)))];
        const rows = data.map((row) => columns.map((c) => row[c]));
        const { sql } = buildSql(v.table || "data", columns, rows, v);
        return { type: "text", label: data.length + " rows", value: sql, download: (v.table || "data") + ".sql" };
      },
    },
    {
      slug: "sql-to-json",
      name: "SQL to JSON",
      description: "Read INSERT ... VALUES statements and return the rows as JSON.",
      keywords: "sql insert json export parse dump",
      inputs: [{ id: "text", label: "INSERT statements", type: "textarea", rows: 12, value: "INSERT INTO videos (id, title, premium, rating) VALUES\n  (1, 'Match Highlights', TRUE, 4.5),\n  (2, 'O''Brien Interview', FALSE, NULL);\nINSERT INTO tags VALUES (1, 'sports');" }],
      run(v) {
        const tables = parseInserts(v.text);
        const names = Object.keys(tables);
        if (!names.length) throw new Error("No INSERT ... VALUES statements found.");
        const data = names.length === 1 ? tables[names[0]] : tables;
        return [
          { type: "note", level: "ok", text: names.map((n) => n + ": " + tables[n].length + " rows").join(", ") },
          { type: "text", label: "JSON", value: JSON.stringify(data, null, 2), download: "rows.json", mime: "application/json" },
        ];
      },
    },
    {
      slug: "csv-to-sql",
      name: "CSV to SQL",
      description: "Turn CSV into CREATE TABLE and INSERT statements, with column types inferred.",
      keywords: "csv sql import insert",
      inputs: [
        { id: "text", label: "CSV", type: "textarea", rows: 10, value: SAMPLE_CSV },
        { id: "file", label: "Or open a CSV file", type: "file", accept: ".csv,.tsv,text/csv" },
        { id: "delimiter", label: "Delimiter", type: "select", options: [["auto", "Detect"], [",", "Comma"], [";", "Semicolon"], ["tab", "Tab"], ["|", "Pipe"]], value: "auto" },
        { id: "header", label: "First row is header", type: "checkbox", value: true },
        { id: "table", label: "Table", type: "text", value: "videos" },
        { id: "dialect", label: "Dialect", type: "select", options: DIALECT_OPTIONS, value: "postgres" },
        { id: "create", label: "Include CREATE TABLE", type: "checkbox", value: true },
        { id: "batch", label: "Multi-row INSERT", type: "checkbox", value: true },
      ],
      run(v) {
        const text = v.file ? util.fromUtf8(v.file.bytes) : v.text;
        const { columns, rows } = csvTable(text, v.delimiter, v.header);
        if (!rows.length) throw new Error("No data rows found.");
        const { sql, kinds } = buildSql(v.table || "data", columns, rows, v);
        return [
          { type: "table", label: "Columns", columns: ["Column", "Detected type", "SQL type"], rows: columns.map((c, i) => [c, kinds[i], DIALECTS[v.dialect].types[kinds[i]]]) },
          { type: "text", label: rows.length + " rows", value: sql, download: (v.table || "data") + ".sql" },
        ];
      },
    },
    {
      slug: "mongodb-query-formatter",
      name: "MongoDB query formatter",
      description: "Indent MongoDB shell queries and aggregation pipelines.",
      keywords: "mongo mongodb aggregate pipeline find pretty",
      inputs: [
        { id: "text", label: "Query", type: "textarea", rows: 12, value: 'db.views.aggregate([{$match:{createdAt:{$gte:ISODate("2026-11-01T00:00:00Z")},platform:{$in:["ios","tvos"]}}},{$group:{_id:"$videoId",plays:{$sum:1},avgWatch:{$avg:"$seconds"}}},{$sort:{plays:-1}},{$limit:10}])' },
        { id: "indent", label: "Indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"]], value: "2" },
        { id: "expand", label: "One array item per line", type: "checkbox", value: true },
      ],
      run(v) {
        return { type: "text", label: "Formatted", value: util.formatBraces(v.text, { mode: "js", indent: " ".repeat(Number(v.indent)), expandArrays: v.expand }) };
      },
    },
    {
      slug: "postgresql-uuid-generator",
      name: "PostgreSQL UUID generator",
      description: "Generate UUIDs as uuid literals, an array or INSERT rows, ready for psql.",
      keywords: "postgres uuid gen_random_uuid uuidv7 insert",
      inputs: [
        { id: "count", label: "How many", type: "number", value: 5, min: 1, max: 1000 },
        { id: "version", label: "Version", type: "select", options: [["v4", "v4 (random)"], ["v7", "v7 (time-ordered)"]], value: "v7" },
        { id: "format", label: "Format", type: "select", options: [["lines", "One per line"], ["literals", "'…'::uuid literals"], ["array", "ARRAY[…]::uuid[]"], ["insert", "INSERT rows"]], value: "literals" },
        { id: "table", label: "Table", type: "text", value: "devices", show: (v) => v.format === "insert" },
        { id: "column", label: "Column", type: "text", value: "id", show: (v) => v.format === "insert" },
        { id: "upper", label: "Uppercase", type: "checkbox", value: false },
      ],
      run(v) {
        const count = util.clampInt(v.count, 1, 1000);
        let ids = Array.from({ length: count }, () => (v.version === "v7" ? util.uuidV7() : util.uuidV4()));
        if (v.upper) ids = ids.map((id) => id.toUpperCase());
        let value;
        if (v.format === "lines") value = ids.join("\n");
        else if (v.format === "literals") value = ids.map((id) => "'" + id + "'::uuid").join(",\n");
        else if (v.format === "array") value = "ARRAY[\n  " + ids.map((id) => "'" + id + "'").join(",\n  ") + "\n]::uuid[]";
        else value = 'INSERT INTO "' + (v.table || "t").replace(/"/g, '""') + '" ("' + (v.column || "id").replace(/"/g, '""') + '") VALUES\n  ' + ids.map((id) => "('" + id + "')").join(",\n  ") + ";";
        return [
          { type: "text", label: count + " UUIDs", value },
          { type: "note", text: v.version === "v7" ? "PostgreSQL 18 has uuidv7(). Older versions need an extension or app-side generation." : "In SQL: gen_random_uuid() (PostgreSQL 13+, or the pgcrypto extension)." },
        ];
      },
    },
    {
      slug: "csv-viewer",
      name: "CSV viewer",
      description: "Show CSV or TSV as a table, with filtering and per-column type detection.",
      keywords: "csv tsv table spreadsheet view",
      inputs: [
        { id: "text", label: "CSV", type: "textarea", rows: 8, value: SAMPLE_CSV },
        { id: "file", label: "Or open a file", type: "file", accept: ".csv,.tsv,text/csv,text/tab-separated-values" },
        { id: "delimiter", label: "Delimiter", type: "select", options: [["auto", "Detect"], [",", "Comma"], [";", "Semicolon"], ["tab", "Tab"], ["|", "Pipe"]], value: "auto" },
        { id: "header", label: "First row is header", type: "checkbox", value: true },
        { id: "filter", label: "Filter rows", type: "text", value: "" },
      ],
      run(v) {
        const text = v.file ? util.fromUtf8(v.file.bytes) : v.text;
        const { columns, rows, sep } = csvTable(text, v.delimiter, v.header);
        const q = String(v.filter || "").toLowerCase();
        const shown = q ? rows.filter((r) => r.some((c) => String(c).toLowerCase().includes(q))) : rows;
        const limit = 1000;
        return [
          { type: "kv", label: "Summary", rows: [["Rows", rows.length], ["Columns", columns.length], ["Delimiter", sep === "\t" ? "tab" : sep], ["Matching", shown.length]] },
          { type: "table", label: shown.length > limit ? "First " + limit + " rows" : "Rows", columns, rows: shown.slice(0, limit) },
          { type: "table", label: "Columns", columns: ["Column", "Type", "Empty", "Distinct"], rows: columns.map((c, i) => [c, columnKind(rows.map((r) => r[i])), rows.filter((r) => r[i] === "").length, new Set(rows.map((r) => r[i])).size]) },
        ];
      },
    },
  ]);
})();
