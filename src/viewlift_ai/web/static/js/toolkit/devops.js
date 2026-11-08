/* DevOps & infrastructure */
(() => {
  const kit = window.VLKit;
  const { register, util } = kit;

  function show(value) {
    if (value === undefined) return "";
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return text.length > 180 ? text.slice(0, 177) + "…" : text;
  }

  function diffBlocks(before, after, label) {
    const changes = util.deepDiff(before, after);
    const ops = util.lineDiff(
      typeof before === "string" ? before : JSON.stringify(before, null, 2),
      typeof after === "string" ? after : JSON.stringify(after, null, 2),
    );
    const added = ops.filter((o) => o.op === "+").length;
    const removed = ops.filter((o) => o.op === "-").length;
    return [
      { type: "note", level: changes.length ? "info" : "ok", text: changes.length ? changes.length + " structural changes. " + added + " lines added, " + removed + " removed." : "No differences." },
      { type: "table", label: label + " structure", columns: ["Change", "Path", "Before", "After"], rows: util.diffRows(changes), empty: "Documents match." },
      { type: "diff", label: label + " text", lines: ops.filter((o) => o.op !== " ") },
    ];
  }

  function docsOf(parsed) {
    const list = Array.isArray(parsed) ? parsed : [parsed];
    return list.filter((doc) => doc && typeof doc === "object");
  }

  function resourceKey(doc) {
    const meta = doc.metadata || {};
    return [doc.kind || "?", meta.namespace || "default", meta.name || "(unnamed)"].join("/");
  }

  function parseCpu(text) {
    const raw = String(text == null ? "" : text).trim();
    if (!raw) return 0;
    if (raw.endsWith("m")) return Number(raw.slice(0, -1)) / 1000;
    if (raw.endsWith("n")) return Number(raw.slice(0, -1)) / 1e9;
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error("Bad CPU quantity '" + raw + "'.");
    return n;
  }

  function parseBytes(text) {
    const raw = String(text == null ? "" : text).trim();
    if (!raw) return 0;
    const match = /^([0-9]*\.?[0-9]+)([KMGTPE]i?)?$/.exec(raw);
    if (!match) throw new Error("Bad memory quantity '" + raw + "'.");
    const n = Number(match[1]);
    const binary = { Ki: 1024, Mi: 1024 ** 2, Gi: 1024 ** 3, Ti: 1024 ** 4, Pi: 1024 ** 5, Ei: 1024 ** 6 };
    const decimal = { K: 1e3, M: 1e6, G: 1e9, T: 1e12, P: 1e15, E: 1e18 };
    return n * (binary[match[2]] || decimal[match[2]] || 1);
  }

  function podSpec(doc) {
    const spec = doc.spec || {};
    if (doc.kind === "Pod") return { pod: spec, replicas: 1, perNode: false };
    if (doc.kind === "CronJob") {
      const job = ((spec.jobTemplate || {}).spec) || {};
      return { pod: ((job.template || {}).spec) || null, replicas: job.parallelism || 1, perNode: false };
    }
    if (["Deployment", "ReplicaSet", "ReplicationController", "StatefulSet", "Job"].includes(doc.kind)) {
      return { pod: ((spec.template || {}).spec) || null, replicas: spec.replicas == null ? 1 : spec.replicas, perNode: false };
    }
    if (doc.kind === "DaemonSet") return { pod: ((spec.template || {}).spec) || null, replicas: 1, perNode: true };
    return null;
  }

  function containersOf(pod) {
    return [...(pod.containers || []), ...(pod.initContainers || []).map((c) => ({ ...c, init: true }))];
  }

  function cpuText(cores) {
    if (!cores) return "0";
    if (cores < 1) return Math.round(cores * 1000) + "m";
    return String(Number(cores.toFixed(3)));
  }

  /* Config generators */

  function indentBlock(text, spaces) {
    const pad = " ".repeat(spaces);
    return String(text).trim().split("\n").map((line) => (line ? pad + line : line)).join("\n");
  }

  function nginxConfig(v) {
    const names = String(v.names || "example.com").trim();
    const port = util.clampInt(v.port, 1, 65535);
    const lines = ["server {"];
    if (v.tls) {
      lines.push("    listen 443 ssl;", "    listen [::]:443 ssl;", "    http2 on;");
      lines.push("    ssl_certificate     /etc/ssl/certs/" + names.split(/\s+/)[0] + ".crt;");
      lines.push("    ssl_certificate_key /etc/ssl/private/" + names.split(/\s+/)[0] + ".key;");
    } else lines.push("    listen " + port + ";", "    listen [::]:" + port + ";");
    lines.push("    server_name " + names + ";");
    if (v.mode === "proxy") {
      lines.push("    location / {", "        proxy_pass " + (v.upstream || "http://127.0.0.1:8080") + ";", "        proxy_set_header Host $host;", "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", "        proxy_set_header X-Forwarded-Proto $scheme;", "    }");
    } else if (v.mode === "hls") {
      lines.push("    root " + (v.root || "/var/www/hls") + ";", "    location / {", "        types {", "            application/vnd.apple.mpegurl m3u8;", "            video/mp2t ts;", "        }", "        add_header Access-Control-Allow-Origin * always;", "        add_header Cache-Control \"public, max-age=2\";", "    }");
    } else {
      lines.push("    root " + (v.root || "/var/www/html") + ";", "    index index.html;", "    location / {", "        try_files $uri $uri/ /index.html;", "    }");
    }
    if (v.gzip) lines.push("    gzip on;", "    gzip_types text/plain text/css application/json application/javascript image/svg+xml;");
    lines.push("}");
    if (v.tls) lines.push("", "server {", "    listen 80;", "    listen [::]:80;", "    server_name " + names + ";", "    return 301 https://$host$request_uri;", "}");
    return lines.join("\n");
  }

  function apacheConfig(v) {
    const names = String(v.names || "example.com").trim();
    const port = v.tls ? 443 : util.clampInt(v.port, 1, 65535);
    const lines = ["<VirtualHost *:" + port + ">", "    ServerName " + names.split(/\s+/)[0]];
    const aliases = names.split(/\s+/).slice(1);
    if (aliases.length) lines.push("    ServerAlias " + aliases.join(" "));
    if (v.tls) lines.push("    SSLEngine on", "    SSLCertificateFile /etc/ssl/certs/" + names.split(/\s+/)[0] + ".crt", "    SSLCertificateKeyFile /etc/ssl/private/" + names.split(/\s+/)[0] + ".key");
    if (v.mode === "proxy") {
      lines.push("    ProxyPreserveHost On", "    ProxyPass / " + (v.upstream || "http://127.0.0.1:8080") + "/", "    ProxyPassReverse / " + (v.upstream || "http://127.0.0.1:8080") + "/", "    RequestHeader set X-Forwarded-Proto \"https\"");
    } else if (v.mode === "hls") {
      lines.push("    DocumentRoot " + (v.root || "/var/www/hls"), "    <Directory " + (v.root || "/var/www/hls") + ">", "        Require all granted", "        AddType application/vnd.apple.mpegurl .m3u8", "        AddType video/mp2t .ts", "        Header set Access-Control-Allow-Origin \"*\"", "    </Directory>");
    } else {
      lines.push("    DocumentRoot " + (v.root || "/var/www/html"), "    <Directory " + (v.root || "/var/www/html") + ">", "        Require all granted", "        FallbackResource /index.html", "    </Directory>");
    }
    lines.push("</VirtualHost>");
    return lines.join("\n");
  }

  function parseServiceLine(line) {
    const parts = line.trim().split(/\s+/);
    const name = parts[0];
    const fields = { image: "", ports: "", env: "" };
    for (const part of parts.slice(1)) {
      const eq = part.indexOf("=");
      if (eq > 0) fields[part.slice(0, eq)] = part.slice(eq + 1);
      else if (!fields.image) fields.image = part;
    }
    return { name, ...fields };
  }

  const COMPOSE_PRESETS = {
    api: "api ghcr.io/example/api:1.4.0 port=8080:8080 env=LOG_LEVEL=info\npostgres postgres:16 port=5432 env=POSTGRES_PASSWORD=change-me",
    web: "web nginx:1.27 port=8080:80",
    hls: "packager linuxserver/ffmpeg:latest\norigin nginx:1.27 port=8080:80",
  };

  function composeFile(text) {
    const services = {};
    for (const line of util.nonEmptyLines(text)) {
      const svc = parseServiceLine(line);
      if (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(svc.name)) throw new Error("Bad service name '" + svc.name + "'.");
      const spec = { image: svc.image || svc.name + ":latest" };
      if (svc.port) spec.ports = svc.port.split(",").map((p) => p.includes(":") ? p : p + ":" + p);
      if (svc.env) spec.environment = Object.fromEntries(svc.env.split(",").map((pair) => {
        const eq = pair.indexOf("=");
        return [pair.slice(0, eq), pair.slice(eq + 1)];
      }));
      services[svc.name] = spec;
    }
    if (!Object.keys(services).length) throw new Error("Add at least one service: name image port=8080:8080");
    return { services };
  }

  function k8sManifest(v) {
    const name = String(v.name || "app").trim();
    const ns = String(v.namespace || "default").trim();
    const port = util.clampInt(v.port, 1, 65535);
    const labels = { app: name };
    const deployment = {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: { name, namespace: ns, labels },
      spec: {
        replicas: util.clampInt(v.replicas, 1, 100),
        selector: { matchLabels: labels },
        template: {
          metadata: { labels },
          spec: {
            containers: [{
              name,
              image: v.image || "ghcr.io/example/app:1.0.0",
              ports: [{ containerPort: port }],
              resources: {
                requests: { cpu: v.cpu || "100m", memory: v.memory || "128Mi" },
                limits: { cpu: v.cpuLimit || "500m", memory: v.memoryLimit || "512Mi" },
              },
              readinessProbe: { httpGet: { path: v.health || "/healthz", port }, initialDelaySeconds: 5, periodSeconds: 10 },
            }],
          },
        },
      },
    };
    const service = {
      apiVersion: "v1",
      kind: "Service",
      metadata: { name, namespace: ns, labels },
      spec: { selector: labels, ports: [{ port, targetPort: port }] },
    };
    return [deployment, service];
  }

  /* Ignore files */

  const GITIGNORE = {
    ios: [
      "# Xcode",
      "build/",
      "DerivedData/",
      "*.xcodeproj/xcuserdata/",
      "*.xcworkspace/xcuserdata/",
      "*.xcodeproj/project.xcworkspace/xcuserdata/",
      "*.xcuserstate",
      "*.moved-aside",
      "*.hmap",
      "*.ipa",
      "*.dSYM.zip",
      "*.dSYM",
      "timeline.xctimeline",
      "playground.xcworkspace",
      ".build/",
      "Packages/",
      "Package.resolved",
      "Pods/",
      "Carthage/Build/",
      "fastlane/report.xml",
      "fastlane/Preview.html",
      "fastlane/screenshots/**/*.png",
      "fastlane/test_output",
      "*.mobileprovision",
      "*.p12",
      "*.cer",
      "AuthKey_*.p8",
    ],
    macos: [".DS_Store", ".AppleDouble", ".LSOverride", "Icon\r", "._*", ".Spotlight-V100", ".Trashes", "*.pem"],
    node: ["node_modules/", "npm-debug.log*", "dist/", ".env", ".env.*", "!.env.example"],
    python: ["__pycache__/", "*.py[cod]", ".venv/", "venv/", ".pytest_cache/", ".mypy_cache/", "*.egg-info/"],
    go: ["bin/", "vendor/", "*.test", "*.out"],
    terraform: [".terraform/", "*.tfstate", "*.tfstate.*", "*.tfvars", "crash.log", "override.tf"],
    secrets: [".env", ".env.*", "!.env.example", "*.pem", "*.p12", "*.key", "credentials.json", "secrets.yaml"],
  };

  function gitignoreFile(keys, extra) {
    const chunks = keys.map((key) => GITIGNORE[key].join("\n"));
    const more = util.nonEmptyLines(extra);
    if (more.length) chunks.push(more.join("\n"));
    return chunks.filter(Boolean).join("\n\n") + "\n";
  }

  function compileIgnore(text) {
    const rules = [];
    for (let raw of String(text).split("\n")) {
      raw = raw.replace(/\r$/, "").replace(/(?<!\\)\s+$/, "");
      if (!raw || raw.startsWith("#")) continue;
      let negate = false;
      if (raw.startsWith("!")) {
        negate = true;
        raw = raw.slice(1);
      }
      raw = raw.replace(/\\ /g, " ");
      let dirOnly = false;
      if (raw.endsWith("/")) {
        dirOnly = true;
        raw = raw.slice(0, -1);
      }
      const anchored = raw.startsWith("/");
      if (anchored) raw = raw.slice(1);
      const anyDir = !anchored && !raw.includes("/");
      let body = "";
      for (let i = 0; i < raw.length; i += 1) {
        const c = raw[i];
        if (c === "*" && raw[i + 1] === "*") {
          if (raw[i + 2] === "/") {
            body += "(?:.*/)?";
            i += 2;
          } else {
            body += ".*";
            i += 1;
          }
        } else if (c === "*") body += "[^/]*";
        else if (c === "?") body += "[^/]";
        else body += /[.+^${}()|[\]\\]/.test(c) ? "\\" + c : c;
      }
      const source = (anyDir ? "(?:^|.*/)" : "^") + body + "(?:/|$)";
      rules.push({ negate, dirOnly, re: new RegExp(source) });
    }
    return rules;
  }

  function pathIgnored(rules, path) {
    const clean = String(path).trim().replace(/^\.?\//, "").replace(/\/+$/, "");
    if (!clean) return false;
    let ignored = false;
    for (const rule of rules) {
      const target = rule.dirOnly ? clean + "/" : clean;
      if (rule.re.test(target) || rule.re.test(clean + "/")) ignored = !rule.negate;
    }
    return ignored;
  }

  /* Env */

  const SECRET_KEY = /secret|password|passwd|token|api[_-]?key|private|credential|auth|database_url|_dsn$/i;

  function parseEnv(text) {
    const rows = [];
    for (const raw of String(text).split("\n")) {
      const line = raw.replace(/\r$/, "");
      if (!line.trim() || /^\s*#/.test(line)) {
        rows.push({ raw: line, kind: "plain" });
        continue;
      }
      const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
      if (!match) {
        rows.push({ raw: line, kind: "plain" });
        continue;
      }
      let value = match[2];
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
      rows.push({ kind: "pair", key: match[1], value, secret: SECRET_KEY.test(match[1]) });
    }
    return rows;
  }

  /* Versions */

  function bumpSemver(version, part, label) {
    const next = { ...version, prerelease: version.prerelease.slice(), build: "" };
    if (part === "major") {
      next.major += 1;
      next.minor = 0;
      next.patch = 0;
      next.prerelease = [];
    } else if (part === "minor") {
      next.minor += 1;
      next.patch = 0;
      next.prerelease = [];
    } else if (part === "patch") {
      next.patch += 1;
      next.prerelease = [];
    } else if (part === "pre") {
      const name = label || "rc";
      if (!next.prerelease.length) {
        next.patch += 1;
        next.prerelease = [name, "1"];
      } else if (next.prerelease[0] !== name) next.prerelease = [name, "1"];
      else {
        const n = next.prerelease.length > 1 && /^\d+$/.test(next.prerelease[next.prerelease.length - 1]) ? Number(next.prerelease.pop()) + 1 : 1;
        if (next.prerelease[0] !== name) next.prerelease = [name];
        next.prerelease.push(String(n));
      }
    } else if (part === "release") next.prerelease = [];
    return next;
  }

  /* GitHub Actions */

  function workflowIssues(doc) {
    const issues = [];
    const add = (level, where, message) => issues.push([level, where, message]);
    if (!util.isPlainObject(doc)) {
      add("error", "(root)", "A workflow file must be a mapping.");
      return issues;
    }
    const trigger = doc.on !== undefined ? doc.on : doc.true;
    if (trigger === undefined) add("error", "on", "Missing 'on'. YAML 1.1 reads bare 'on' as true, so quote it or use a mapping.");
    if (!util.isPlainObject(doc.jobs) || !Object.keys(doc.jobs).length) add("error", "jobs", "jobs must list at least one job.");
    for (const [name, job] of Object.entries(doc.jobs || {})) {
      if (!util.isPlainObject(job)) {
        add("error", "jobs." + name, "Job must be a mapping.");
        continue;
      }
      if (!job["runs-on"] && !job.uses) add("error", "jobs." + name, "Set runs-on or uses.");
      const steps = job.steps || [];
      if (!job.uses && !Array.isArray(steps)) add("error", "jobs." + name + ".steps", "steps must be a list.");
      (Array.isArray(steps) ? steps : []).forEach((step, index) => {
        const where = "jobs." + name + ".steps[" + index + "]";
        if (!util.isPlainObject(step)) {
          add("error", where, "Step must be a mapping.");
          return;
        }
        if (!step.uses && step.run == null) add("error", where, "Step needs uses or run.");
        if (typeof step.uses === "string") {
          if (!step.uses.startsWith("./") && !/@[0-9a-f]{40}$/.test(step.uses)) add("warn", where, "Action " + step.uses + " is not pinned to a commit SHA.");
          if (/@v?\d+$/.test(step.uses) || step.uses.endsWith("@main") || step.uses.endsWith("@master")) add("info", where, "Floating tag. A SHA pin survives a moved tag.");
        }
        if (typeof step.run === "string" && /curl[^|]*\|\s*(sh|bash)/.test(step.run)) add("error", where, "Piping a download into a shell runs unreviewed code.");
      });
      if (trigger && (trigger.pull_request_target || (util.isPlainObject(trigger) && trigger.pull_request_target))) {
        const checksOut = (job.steps || []).some((s) => s && typeof s.uses === "string" && s.uses.startsWith("actions/checkout"));
        if (checksOut) add("error", "jobs." + name, "pull_request_target plus checkout runs fork code with this repo's secrets.");
      }
    }
    if (typeof doc.name !== "string") add("warn", "name", "Name the workflow so the Actions tab stays readable.");
    const permissions = doc.permissions;
    if (!permissions) add("warn", "permissions", "No top-level permissions. The default token can write to the repo.");
    else if (permissions !== "read-all" && permissions !== "none" && !(util.isPlainObject(permissions) && permissions.contents === "read")) add("info", "permissions", "Review the token scopes.");
    return issues;
  }

  /** Picks the newest available simulator of a platform on the CI runner, so no device name is baked in. */
  function pickSimulatorStep(platform) {
    const script = [
      "id=$(xcrun simctl list devices available -j | python3 -c \"",
      "import json,sys",
      "rows=[(r,d) for r,ds in json.load(sys.stdin)['devices'].items() if '" + platform + "' in r for d in ds]",
      "rows.sort(key=lambda x: [int(p) for p in x[0].rsplit('-',2)[-2:] if p.isdigit()], reverse=True)",
      "print(rows[0][1]['udid'] if rows else '')\")",
      "test -n \"$id\" || { echo 'No " + platform + " simulator on this runner'; exit 1; }",
      "echo \"SIM_ID=$id\" >> \"$GITHUB_ENV\"",
    ].join("\n");
    return { name: "Pick " + platform + " simulator", run: script };
  }

  function ghaWorkflow(v) {
    const xcode = String(v.xcode || "").trim();
    const scheme = String(v.scheme || "").trim();
    const runner = v.runner || "macos-latest";
    const checkout = "actions/checkout@v4";
    const trigger = { push: { branches: ["main"] }, pull_request: {} };
    if (v.preset === "spm") {
      return {
        name: "Swift package",
        on: trigger,
        permissions: { contents: "read" },
        jobs: { test: { "runs-on": runner, steps: [{ uses: checkout }, { name: "Test", run: "swift test" }] } },
      };
    }
    const platform = v.preset === "tvos" ? "tvOS" : "iOS";
    const custom = String(v.destination || "").trim();
    const steps = [{ uses: checkout }];
    if (xcode) steps.push({ name: "Select Xcode", run: "sudo xcode-select -s /Applications/Xcode_" + xcode + ".app" });
    if (!custom) steps.push(pickSimulatorStep(platform));
    const destination = custom || "id=$SIM_ID";
    const schemeArg = scheme ? " -scheme " + scheme : "";
    steps.push({ name: "Build and test", run: "xcodebuild test" + schemeArg + " -destination \"" + destination + "\" -quiet" });
    return {
      name: platform,
      on: trigger,
      permissions: { contents: "read" },
      jobs: { test: { "runs-on": runner, steps } },
    };
  }

  /* Security scanners */

  function dockerfileIssues(text) {
    const issues = [];
    const add = (level, line, message) => issues.push([level, String(line), message]);
    const lines = String(text).split("\n");
    let user = "";
    let hasHealth = false;
    lines.forEach((raw, index) => {
      const lineNo = index + 1;
      const line = raw.replace(/#.*$/, "").trim();
      if (!line) return;
      const cmd = (line.split(/\s+/)[0] || "").toUpperCase();
      if (cmd === "FROM") {
        const image = line.split(/\s+/)[1] || "";
        if (!image.includes("@") && (image.endsWith(":latest") || !image.includes(":"))) add("warn", lineNo, "Base image " + image + " is not pinned. Use a digest or a version tag.");
      } else if (cmd === "USER") user = line.split(/\s+/)[1] || "";
      else if (cmd === "ADD" && !/^ADD\s+https?:/i.test(line) === false) add("info", lineNo, "ADD of a URL unpacks archives. COPY plus a checksum is easier to review.");
      else if (cmd === "ADD") add("info", lineNo, "ADD can unpack archives. COPY is enough for plain files.");
      else if (cmd === "ENV" && SECRET_KEY.test(line)) add("error", lineNo, "ENV bakes a secret into the image. Pass it at runtime.");
      else if ((cmd === "RUN") && /curl[^|]*\|\s*(sh|bash)|wget[^|]*\|\s*(sh|bash)/.test(line)) add("error", lineNo, "Piping a download into a shell.");
      else if (cmd === "RUN" && /\bapt(-get)?\s+install\b/.test(line) && !/rm\s+-rf\s+\/var\/lib\/apt\/lists/.test(line)) add("warn", lineNo, "apt install without cleaning /var/lib/apt/lists leaves cache in the layer.");
      else if (cmd === "HEALTHCHECK") hasHealth = true;
      else if (cmd === "EXPOSE" && /\b22\b/.test(line)) add("warn", lineNo, "SSH in a container is rarely what you want.");
    });
    if (!user) add("warn", "-", "No USER. The container runs as root.");
    else if (user === "root" || user === "0") add("warn", "-", "USER is root.");
    if (!hasHealth) add("info", "-", "No HEALTHCHECK.");
    return issues;
  }

  function k8sSecurityIssues(docs) {
    const issues = [];
    const add = (level, where, message) => issues.push([level, where, message]);
    for (const doc of docs) {
      if (!doc || !doc.kind) continue;
      const where = resourceKey(doc);
      const found = podSpec(doc);
      if (!found || !found.pod) continue;
      const pod = found.pod;
      if (pod.hostNetwork) add("error", where, "hostNetwork shares the node network.");
      if (pod.hostPID) add("error", where, "hostPID sees host processes.");
      if (pod.hostIPC) add("warn", where, "hostIPC shares host memory.");
      const sc = pod.securityContext || {};
      if (sc.runAsUser === 0) add("error", where, "Pod runs as UID 0.");
      if (!sc.runAsNonRoot) add("warn", where, "runAsNonRoot is not set.");
      for (const volume of pod.volumes || []) {
        if (volume.hostPath) add("warn", where, "hostPath volume " + (volume.name || "") + " mounts " + (volume.hostPath.path || "a host path") + ".");
      }
      for (const container of containersOf(pod)) {
        const at = where + " / " + (container.name || "container");
        const image = String(container.image || "");
        if (!image) add("error", at, "Container has no image.");
        else if (!image.includes("@") && (image.endsWith(":latest") || !image.includes(":"))) add("warn", at, "Image " + image + " is not pinned.");
        if (container.securityContext && container.securityContext.privileged) add("error", at, "privileged: true.");
        if (!container.securityContext || container.securityContext.allowPrivilegeEscalation !== false) add("warn", at, "allowPrivilegeEscalation is not false.");
        if (!container.securityContext || container.securityContext.readOnlyRootFilesystem !== true) add("info", at, "Root filesystem is writable.");
        const caps = ((container.securityContext || {}).capabilities || {}).add || [];
        if (caps.map(String).some((c) => c === "ALL" || c === "SYS_ADMIN")) add("error", at, "Adds capability " + caps.join(", ") + ".");
        const resources = container.resources || {};
        if (!resources.requests) add("warn", at, "No resource requests. The scheduler cannot place this well.");
        if (!resources.limits) add("info", at, "No resource limits.");
      }
    }
    return issues;
  }

  const SAMPLE_COMPOSE_A = "services:\n  api:\n    image: ghcr.io/example/api:1.3.0\n    ports:\n      - 8080:8080\n";
  const SAMPLE_COMPOSE_B = "services:\n  api:\n    image: ghcr.io/example/api:1.4.0\n    ports:\n      - 8080:8080\n    environment:\n      LOG_LEVEL: info\n";
  const SAMPLE_K8S_A = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n  namespace: media\nspec:\n  replicas: 2\n  template:\n    spec:\n      containers:\n        - name: api\n          image: ghcr.io/example/api:1.3.0\n          resources:\n            requests:\n              cpu: 100m\n              memory: 128Mi\n";
  const SAMPLE_K8S_B = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n  namespace: media\nspec:\n  replicas: 3\n  template:\n    spec:\n      containers:\n        - name: api\n          image: ghcr.io/example/api:1.4.0\n          securityContext:\n            privileged: true\n          resources:\n            requests:\n              cpu: 250m\n              memory: 256Mi\n            limits:\n              memory: 512Mi\n";
  const SAMPLE_DOCKERFILE = "FROM python:3.12\nENV API_TOKEN=super-secret\nRUN apt-get update && apt-get install -y curl\nRUN curl https://example.com/install.sh | bash\nCOPY . /app\nWORKDIR /app\nEXPOSE 8080\nCMD [\"python\", \"app.py\"]\n";
  const SAMPLE_WORKFLOW = "name: iOS\non:\n  pull_request_target:\njobs:\n  test:\n    runs-on: macos-15\n    steps:\n      - uses: actions/checkout@v4\n      - run: xcodebuild test -scheme App\n";

  register("devops", [
    {
      slug: "docker-compose-diff",
      name: "Docker Compose diff",
      description: "Compare two Compose files by structure and by line.",
      keywords: "docker compose diff yaml services",
      inputs: [
        { id: "before", label: "Before", type: "textarea", rows: 10, value: SAMPLE_COMPOSE_A },
        { id: "after", label: "After", type: "textarea", rows: 10, value: SAMPLE_COMPOSE_B },
      ],
      async run(v, ctx) {
        const before = await util.yamlParseOne(ctx, v.before);
        const after = await util.yamlParseOne(ctx, v.after);
        return diffBlocks(before, after, "Compose");
      },
    },
    {
      slug: "kubernetes-manifest-diff",
      name: "Kubernetes manifest diff",
      description: "Match resources by kind, namespace and name, then show what changed.",
      keywords: "kubernetes k8s manifest diff yaml",
      inputs: [
        { id: "before", label: "Before", type: "textarea", rows: 12, value: SAMPLE_K8S_A },
        { id: "after", label: "After", type: "textarea", rows: 12, value: SAMPLE_K8S_B },
      ],
      async run(v, ctx) {
        const before = docsOf(await util.yamlParse(ctx, v.before));
        const after = docsOf(await util.yamlParse(ctx, v.after));
        const map = new Map(before.map((doc) => [resourceKey(doc), doc]));
        const rows = [];
        const seen = new Set();
        for (const doc of after) {
          const key = resourceKey(doc);
          seen.add(key);
          const prior = map.get(key);
          if (!prior) rows.push(["added", key, "", doc.kind]);
          else {
            const changes = util.deepDiff(prior, doc);
            if (changes.length) rows.push(["changed", key, changes.length + " fields", changes.slice(0, 4).map((c) => c.path).join(", ")]);
          }
        }
        for (const doc of before) {
          const key = resourceKey(doc);
          if (!seen.has(key)) rows.push(["removed", key, doc.kind, ""]);
        }
        return { type: "table", label: "Resources", columns: ["Change", "Resource", "Before", "After"], rows, empty: "No resource changes.", rowClass: (row) => (row[0] === "removed" ? "err" : row[0] === "added" ? "ok" : "") };
      },
    },
    {
      slug: "kubernetes-resource-calculator",
      name: "Kubernetes resource calculator",
      description: "Sum CPU and memory requests and limits across Pods, Deployments, Jobs and CronJobs. DaemonSets are counted once.",
      keywords: "kubernetes resources requests limits cpu memory quota",
      inputs: [{ id: "text", label: "Manifests (YAML)", type: "textarea", rows: 16, value: SAMPLE_K8S_B }],
      async run(v, ctx) {
        const docs = docsOf(await util.yamlParse(ctx, v.text));
        const rows = [];
        let cpuReq = 0;
        let cpuLim = 0;
        let memReq = 0;
        let memLim = 0;
        for (const doc of docs) {
          const found = podSpec(doc);
          if (!found || !found.pod) continue;
          const scale = found.replicas;
          for (const container of containersOf(found.pod)) {
            const requests = (container.resources || {}).requests || {};
            const limits = (container.resources || {}).limits || {};
            const cr = parseCpu(requests.cpu) * scale;
            const cl = parseCpu(limits.cpu) * scale;
            const mr = parseBytes(requests.memory) * scale;
            const ml = parseBytes(limits.memory) * scale;
            cpuReq += cr;
            cpuLim += cl;
            memReq += mr;
            memLim += ml;
            rows.push([resourceKey(doc) + (found.perNode ? " (per node)" : " ×" + scale), (container.init ? "init " : "") + (container.name || ""), cpuText(parseCpu(requests.cpu)), cpuText(parseCpu(limits.cpu)), requests.memory || "", limits.memory || ""]);
          }
        }
        if (!rows.length) throw new Error("No Pod, Deployment, StatefulSet, DaemonSet, Job or CronJob with containers.");
        return [
          { type: "kv", label: "Cluster total", rows: [["CPU requests", cpuText(cpuReq)], ["CPU limits", cpuText(cpuLim)], ["Memory requests", util.formatBytes(memReq)], ["Memory limits", util.formatBytes(memLim)]] },
          { type: "table", label: "Containers", columns: ["Workload", "Container", "CPU req", "CPU lim", "Mem req", "Mem lim"], rows },
        ];
      },
    },
    {
      slug: "nginx-config-generator",
      name: "nginx config generator",
      description: "Write a server block for static files, a reverse proxy, or an HLS origin.",
      keywords: "nginx config server reverse proxy hls",
      inputs: [
        { id: "mode", label: "Site", type: "select", options: [["static", "Static site"], ["proxy", "Reverse proxy"], ["hls", "HLS origin"]], value: "proxy" },
        { id: "names", label: "Server names", type: "text", value: "example.com www.example.com", wide: true },
        { id: "port", label: "Port", type: "number", value: 80, min: 1, max: 65535, show: (v) => !v.tls },
        { id: "upstream", label: "Upstream", type: "text", value: "http://127.0.0.1:8080", show: (v) => v.mode === "proxy", wide: true },
        { id: "root", label: "Root", type: "text", value: "/var/www/html", show: (v) => v.mode !== "proxy" },
        { id: "tls", label: "TLS and HTTP redirect", type: "checkbox", value: true },
        { id: "gzip", label: "gzip", type: "checkbox", value: true },
      ],
      run(v) {
        return { type: "text", label: "nginx", value: nginxConfig(v), download: "site.conf" };
      },
    },
    {
      slug: "apache-config-generator",
      name: "Apache config generator",
      description: "Write an Apache virtual host for static files, a reverse proxy, or an HLS origin.",
      keywords: "apache httpd vhost config proxy",
      inputs: [
        { id: "mode", label: "Site", type: "select", options: [["static", "Static site"], ["proxy", "Reverse proxy"], ["hls", "HLS origin"]], value: "static" },
        { id: "names", label: "Server names", type: "text", value: "example.com www.example.com", wide: true },
        { id: "port", label: "Port", type: "number", value: 80, min: 1, max: 65535, show: (v) => !v.tls },
        { id: "upstream", label: "Upstream", type: "text", value: "http://127.0.0.1:8080", show: (v) => v.mode === "proxy", wide: true },
        { id: "root", label: "Document root", type: "text", value: "/var/www/html", show: (v) => v.mode !== "proxy" },
        { id: "tls", label: "TLS", type: "checkbox", value: false },
      ],
      run(v) {
        return { type: "text", label: "Apache", value: apacheConfig(v), download: "site.conf" };
      },
    },
    {
      slug: "docker-compose-generator",
      name: "Docker Compose generator",
      description: "Build a Compose file from a preset or from lines like: api image:tag port=8080:8080 env=KEY=value.",
      keywords: "docker compose generate yaml services",
      inputs: [
        { id: "preset", label: "Preset", type: "select", options: [["api", "API and Postgres"], ["web", "nginx"], ["hls", "Packager and origin"], ["custom", "Custom only"]], value: "api" },
        { id: "extra", label: "Services", type: "textarea", rows: 6, value: "", placeholder: "worker ghcr.io/example/worker:1.0" },
      ],
      async run(v, ctx) {
        const body = [v.preset === "custom" ? "" : COMPOSE_PRESETS[v.preset], v.extra].filter(Boolean).join("\n");
        const file = composeFile(body);
        return { type: "text", label: "compose.yaml", value: await util.yamlDump(ctx, file), download: "compose.yaml" };
      },
    },
    {
      slug: "kubernetes-yaml-generator",
      name: "Kubernetes YAML generator",
      description: "Write a Deployment and Service with requests, limits and a readiness probe.",
      keywords: "kubernetes deployment service yaml generate",
      inputs: [
        { id: "name", label: "Name", type: "text", value: "api" },
        { id: "namespace", label: "Namespace", type: "text", value: "media" },
        { id: "image", label: "Image", type: "text", value: "ghcr.io/example/api:1.4.0", wide: true },
        { id: "replicas", label: "Replicas", type: "number", value: 2, min: 1, max: 100 },
        { id: "port", label: "Port", type: "number", value: 8080, min: 1, max: 65535 },
        { id: "health", label: "Health path", type: "text", value: "/healthz" },
        { id: "cpu", label: "CPU request", type: "text", value: "100m" },
        { id: "cpuLimit", label: "CPU limit", type: "text", value: "500m" },
        { id: "memory", label: "Memory request", type: "text", value: "128Mi" },
        { id: "memoryLimit", label: "Memory limit", type: "text", value: "512Mi" },
      ],
      async run(v, ctx) {
        return { type: "text", label: "manifest.yaml", value: await util.yamlDump(ctx, k8sManifest(v), { multi: true }), download: "manifest.yaml" };
      },
    },
    {
      slug: "gitignore-generator",
      name: "gitignore generator",
      description: "A curated gitignore. The iOS template covers Xcode user data, build products, Swift packages, CocoaPods, Carthage, fastlane and signing files.",
      keywords: "gitignore xcode ios swift pods carthage fastlane",
      inputs: [
        { id: "ios", label: "iOS and Xcode", type: "checkbox", value: true },
        { id: "macos", label: "macOS junk", type: "checkbox", value: true },
        { id: "node", label: "Node", type: "checkbox", value: false },
        { id: "python", label: "Python", type: "checkbox", value: false },
        { id: "go", label: "Go", type: "checkbox", value: false },
        { id: "terraform", label: "Terraform", type: "checkbox", value: false },
        { id: "secrets", label: "Secret files", type: "checkbox", value: true },
        { id: "extra", label: "Extra patterns", type: "textarea", rows: 4, value: "" },
      ],
      run(v) {
        const keys = ["ios", "macos", "node", "python", "go", "terraform", "secrets"].filter((key) => v[key]);
        if (!keys.length && !String(v.extra || "").trim()) throw new Error("Pick at least one template.");
        return [
          { type: "text", label: ".gitignore", value: gitignoreFile(keys, v.extra), download: ".gitignore" },
          { type: "note", text: "Curated set, not the full github/gitignore catalog. Add project-specific paths in Extra patterns." },
        ];
      },
    },
    {
      slug: "dockerignore-generator",
      name: "dockerignore generator",
      description: "Keep build context small. Drops VCS, dependencies, secrets and local Apple build folders.",
      keywords: "dockerignore docker context xcode",
      inputs: [
        { id: "kind", label: "Project", type: "select", options: [["app", "App server"], ["ios", "Repo that also has an iOS app"], ["node", "Node"], ["python", "Python"]], value: "app" },
      ],
      run(v) {
        const common = [".git", ".github", ".DS_Store", "*.md", ".env", ".env.*", "!.env.example", "**/*_test.go", "coverage/", ".idea/", ".vscode/"];
        const extra = {
          app: ["tmp/", "tests/"],
          ios: ["*.xcodeproj/xcuserdata", "*.xcworkspace/xcuserdata", "DerivedData/", "build/", "Pods/"],
          node: ["node_modules/", "npm-debug.log", "dist/"],
          python: [".venv/", "venv/", "__pycache__/", ".pytest_cache/", "*.pyc"],
        };
        return { type: "text", label: ".dockerignore", value: common.concat(extra[v.kind] || []).join("\n") + "\n", download: ".dockerignore" };
      },
    },
    {
      slug: "editorconfig-generator",
      name: "EditorConfig generator",
      description: "EditorConfig for mixed Swift, web and Python trees.",
      keywords: "editorconfig indent charset swift",
      inputs: [
        { id: "indent", label: "Default indent", type: "select", options: [["2", "2 spaces"], ["4", "4 spaces"], ["tab", "Tab"]], value: "2" },
        { id: "swift", label: "Swift and Objective-C at 4 spaces", type: "checkbox", value: true },
        { id: "trim", label: "Trim trailing whitespace", type: "checkbox", value: true },
        { id: "newline", label: "Final newline", type: "checkbox", value: true },
      ],
      run(v) {
        const root = v.indent === "tab" ? "indent_style = tab\nindent_size = 4" : "indent_style = space\nindent_size = " + v.indent;
        const lines = ["root = true", "", "[*]", "charset = utf-8", root, "end_of_line = lf"];
        if (v.trim) lines.push("trim_trailing_whitespace = true");
        if (v.newline) lines.push("insert_final_newline = true");
        if (v.swift) lines.push("", "[*.{swift,m,h}]", "indent_style = space", "indent_size = 4");
        lines.push("", "[*.{md,markdown}]", "trim_trailing_whitespace = false");
        return { type: "text", label: ".editorconfig", value: lines.join("\n") + "\n", download: ".editorconfig" };
      },
    },
    {
      slug: "env-template-generator",
      name: "Env template generator",
      description: "Turn a .env into a committable example. Values whose keys look like secrets are blanked.",
      keywords: "env example dotenv secrets template",
      inputs: [
        { id: "text", label: ".env", type: "textarea", rows: 10, value: "API_BASE=https://api.example.com\nAPI_TOKEN=live-secret-value\nDATABASE_URL=postgres://app:s3cret@db:5432/app\nLOG_LEVEL=info\n" },
        { id: "blank", label: "Blank secret-looking values", type: "checkbox", value: true },
      ],
      run(v) {
        const rows = parseEnv(v.text);
        const secrets = rows.filter((row) => row.secret).map((row) => row.key);
        const example = rows.map((row) => {
          if (row.kind !== "pair") return row.raw;
          const value = v.blank && row.secret ? "" : row.value;
          return row.key + "=" + value;
        }).join("\n");
        return [
          secrets.length ? { type: "note", level: "warn", text: "Treated as secrets: " + secrets.join(", ") + "." } : { type: "note", level: "ok", text: "No secret-looking keys." },
          { type: "text", label: ".env.example", value: example.endsWith("\n") ? example : example + "\n", download: ".env.example" },
        ];
      },
    },
    {
      slug: "cron-job-simulator",
      name: "Cron job simulator",
      description: "Show the next run times for a five-field cron expression, in local time or UTC.",
      keywords: "cron simulate next run schedule",
      inputs: [
        { id: "expression", label: "Expression", type: "text", value: "*/15 9-17 * * 1-5", wide: true },
        { id: "count", label: "Runs", type: "number", value: 8, min: 1, max: 50 },
        { id: "utc", label: "UTC", type: "checkbox", value: false },
      ],
      run(v) {
        const cron = util.parseCron(v.expression);
        const runs = util.cronNext(cron, new Date(), util.clampInt(v.count, 1, 50), Boolean(v.utc));
        return [
          { type: "note", level: "ok", text: util.describeCron(v.expression) },
          { type: "table", label: "Next runs", columns: ["#", "When", "In"], rows: runs.map((date, index) => [index + 1, v.utc ? date.toISOString() : date.toString(), util.relativeTime(date.getTime())]) },
        ];
      },
    },
    {
      slug: "cron-expression-validator",
      name: "Cron expression validator",
      description: "Check a cron expression or @macro and explain each field.",
      keywords: "cron validate crontab syntax",
      inputs: [{ id: "expression", label: "Expression", type: "text", value: "0 9 * * MON-FRI", wide: true }],
      run(v) {
        const cron = util.parseCron(v.expression);
        const sets = [cron.minutes, cron.hours, cron.days, cron.months, cron.weekdays];
        const rows = cron.parts.map((part, index) => {
          const values = [...sets[index]].sort((a, b) => a - b);
          const shown = values.length > 12 ? values.slice(0, 12).join(", ") + "…" : values.join(", ");
          return [util.CRON_FIELDS[index].name, part, shown];
        });
        const out = [
          { type: "note", level: "ok", text: util.describeCron(v.expression) },
          { type: "table", label: "Fields", columns: ["Field", "Raw", "Matches"], rows },
        ];
        if (!cron.domStar && !cron.dowStar) out.push({ type: "note", level: "warn", text: "Day of month and day of week are both restricted. Standard cron fires when either matches." });
        return out;
      },
    },
    {
      slug: "semantic-version-bumper",
      name: "Semantic version bumper",
      description: "Bump major, minor, patch or a pre-release tag. Build metadata is dropped.",
      keywords: "semver bump version major minor patch prerelease",
      inputs: [
        { id: "version", label: "Version", type: "text", value: "1.4.2" },
        { id: "part", label: "Bump", type: "select", options: [["major", "Major"], ["minor", "Minor"], ["patch", "Patch"], ["pre", "Pre-release"], ["release", "Drop pre-release"]], value: "patch" },
        { id: "label", label: "Pre-release name", type: "text", value: "rc", show: (v) => v.part === "pre" },
      ],
      run(v) {
        const current = util.parseSemver(v.version);
        const next = bumpSemver(current, v.part, String(v.label || "rc").trim() || "rc");
        return { type: "kv", label: "Version", rows: [["Current", util.formatSemver(current)], ["Next", util.formatSemver(next)], ["Tag", "v" + util.formatSemver(next)]] };
      },
    },
    {
      slug: "git-ignore-matcher",
      name: "Git ignore matcher",
      description: "Test paths against gitignore rules. Last matching rule wins, including negations.",
      keywords: "gitignore match test pattern ignore",
      inputs: [
        { id: "rules", label: ".gitignore", type: "textarea", rows: 8, value: "build/\n*.log\n!keep.log\nDerivedData/\n*.xcodeproj/xcuserdata/\n" },
        { id: "paths", label: "Paths", type: "textarea", rows: 8, value: "build/App.app\nnotes.log\nkeep.log\nApp.xcodeproj/xcuserdata/me.xcuserdatad\nsrc/main.swift\n" },
      ],
      run(v) {
        const rules = compileIgnore(v.rules);
        const rows = util.nonEmptyLines(v.paths).map((path) => [path.trim(), pathIgnored(rules, path) ? "ignored" : "kept"]);
        return { type: "table", label: rows.length + " paths", columns: ["Path", "Result"], rows, rowClass: (row) => (row[1] === "ignored" ? "warn" : "ok") };
      },
    },
    {
      slug: "github-actions-workflow-validator",
      name: "GitHub Actions workflow validator",
      description: "Check a workflow for jobs, steps, unpinned actions and pull_request_target checkout.",
      keywords: "github actions workflow validate gha ci",
      inputs: [{ id: "text", label: "Workflow YAML", type: "textarea", rows: 14, value: SAMPLE_WORKFLOW }],
      async run(v, ctx) {
        const doc = await util.yamlParseOne(ctx, v.text);
        const issues = workflowIssues(doc);
        const errors = issues.filter((row) => row[0] === "error").length;
        return [
          { type: "note", level: errors ? "error" : issues.length ? "warn" : "ok", text: errors ? errors + " errors." : issues.length ? "No errors. " + issues.length + " notes." : "No problems found." },
          { type: "table", label: "Issues", columns: ["Level", "Where", "Problem"], rows: issues, rowClass: (row) => (row[0] === "error" ? "err" : row[0] === "warn" ? "warn" : "") },
        ];
      },
    },
    {
      slug: "github-actions-generator",
      name: "GitHub Actions generator",
      description: "A starter workflow for an iOS scheme, a tvOS scheme, or a Swift package.",
      keywords: "github actions xcode swift ios tvos ci generate",
      inputs: [
        { id: "preset", label: "Workflow", type: "select", options: [["ios", "iOS test"], ["tvos", "tvOS test"], ["spm", "Swift package"]], value: "ios" },
        { id: "runner", label: "Runner", type: "select", options: ["macos-latest", "macos-15", "macos-14"], value: "macos-latest" },
        { id: "scheme", label: "Scheme", type: "text", value: "", show: (v) => v.preset !== "spm", hint: "Leave empty when the project has one scheme." },
        { id: "xcode", label: "Xcode", type: "text", value: "", show: (v) => v.preset !== "spm", hint: "Optional. Selects /Applications/Xcode_<version>.app; empty uses the runner default." },
        { id: "destination", label: "Destination", type: "text", value: "", show: (v) => v.preset !== "spm", wide: true, hint: "Optional. Empty picks the newest simulator installed on the runner." },
      ],
      async run(v, ctx) {
        const doc = ghaWorkflow(v);
        return [
          { type: "text", label: ".github/workflows/ci.yml", value: await util.yamlDump(ctx, doc), download: "ci.yml" },
          { type: "note", text: "Checkout uses a version tag. Pin it to a commit SHA before this runs on a protected branch." },
        ];
      },
    },
    {
      slug: "dockerfile-security-analyzer",
      name: "Dockerfile security analyzer",
      description: "Flag unpinned bases, root users, secrets in ENV, and install scripts piped to a shell.",
      keywords: "dockerfile security lint root latest secret",
      inputs: [{ id: "text", label: "Dockerfile", type: "textarea", rows: 14, value: SAMPLE_DOCKERFILE }],
      run(v) {
        const issues = dockerfileIssues(v.text);
        const errors = issues.filter((row) => row[0] === "error").length;
        return [
          { type: "note", level: errors ? "error" : issues.length ? "warn" : "ok", text: errors ? errors + " errors." : issues.length ? issues.length + " notes." : "No problems found." },
          { type: "table", label: "Findings", columns: ["Level", "Line", "Problem"], rows: issues, rowClass: (row) => (row[0] === "error" ? "err" : row[0] === "warn" ? "warn" : "") },
        ];
      },
    },
    {
      slug: "kubernetes-manifest-security-analyzer",
      name: "Kubernetes manifest security analyzer",
      description: "Flag privileged containers, host namespaces, hostPath, root, and images that are not pinned.",
      keywords: "kubernetes security pod privileged hostpath runasroot",
      inputs: [{ id: "text", label: "Manifests", type: "textarea", rows: 16, value: SAMPLE_K8S_B }],
      async run(v, ctx) {
        const docs = docsOf(await util.yamlParse(ctx, v.text));
        const issues = k8sSecurityIssues(docs);
        const errors = issues.filter((row) => row[0] === "error").length;
        return [
          { type: "note", level: errors ? "error" : issues.length ? "warn" : "ok", text: errors ? errors + " errors across " + docs.length + " documents." : issues.length ? "No errors. " + issues.length + " notes." : "No problems found." },
          { type: "table", label: "Findings", columns: ["Level", "Where", "Problem"], rows: issues, empty: "Nothing to flag.", rowClass: (row) => (row[0] === "error" ? "err" : row[0] === "warn" ? "warn" : "") },
        ];
      },
    },
  ]);
})();
