/* Agent setup page (docs/173 S7). Renders GET /api/agent/setup: only the undone
 * items expanded; every write is a PREVIEW (a diff) then a click; the one live
 * check is a real read-only question with its time and answer verbatim.
 * The Dry-run switch (3b) is the Runner's global_simulate: the Experiment
 * Runner page that owned it is hidden since docs/172, so this is where a
 * person using the cockpit sees and flips it (POST /scheduler/settings; a
 * refusal shows the server's words verbatim and the box goes back).
 * Bundle 'agent_setup' (base.html). */
window.AgentSetup = (function () {
  "use strict";
  var S = { data: null, root: null, previews: {}, context: null, answers: {} };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  // English only, and for the same reason as in agent.js: this value goes
  // into an HTTP header, and a non-ISO-8859-1 one makes the browser refuse
  // the request before it is sent — which blanked this very page.
  // docs/272 (C-23): THIS tab's name -- the one the Agent composer shows and
  // records -- not the key every tab shares (agent.js owns it; core script).
  function actorName() {
    if (window.AgentPanel && typeof window.AgentPanel.actorName === "function") return window.AgentPanel.actorName();
    try { return String(localStorage.getItem("quam_actor_name") || "").replace(/[^\x20-\x7E]/g, "").trim(); }
    catch (e) { return ""; }
  }
  function api(method, path, body) {
    var h = { "Accept": "application/json" };
    if (body !== undefined) h["Content-Type"] = "application/json";
    var who = actorName();
    if (who) h["X-SM-Actor"] = who;
    // A rejected fetch answers like a refused request (status 0), the way
    // agent.js's api() already does. Without this the page threw an uncaught
    // TypeError out of load() and rendered NOTHING — and this is the page a
    // person comes to when something is already wrong.
    var p;
    try {
      p = fetch(path, { method: method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
    } catch (e) { p = Promise.reject(e); }
    return p.then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; });
    }, function (e) {
      return { status: 0, body: { error: "SM could not be reached: " + (e && e.message || e) } };
    });
  }
  function pretty(v) { return typeof v === "string" ? v : JSON.stringify(v, null, 2); }
  // a line diff (LCS) -- enough to show what a click will change
  function diffLines(a, b) {
    var A = String(a || "").split("\n"), B = String(b || "").split("\n");
    var n = A.length, m = B.length, L = [];
    for (var i = 0; i <= n; i++) { L.push(new Array(m + 1).fill(0)); }
    for (i = n - 1; i >= 0; i--) for (var j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    var out = []; i = 0; j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { out.push(["=", A[i]]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) { out.push(["-", A[i]]); i++; }
      else { out.push(["+", B[j]]); j++; }
    }
    while (i < n) out.push(["-", A[i++]]);
    while (j < m) out.push(["+", B[j++]]);
    return out;
  }
  function diffHtml(before, after) {
    var rows = diffLines(pretty(before), pretty(after));
    var changed = rows.filter(function (r) { return r[0] !== "="; }).length;
    if (!changed) return '<p class="muted">no change</p>';
    // context: keep 2 unchanged lines around each change
    var keep = rows.map(function (r, k) { if (r[0] !== "=") return true; for (var d = -2; d <= 2; d++) { if (rows[k + d] && rows[k + d][0] !== "=") return true; } return false; });
    var html = [], gap = false;
    rows.forEach(function (r, k) {
      if (!keep[k]) { gap = true; return; }
      if (gap) { html.push('<div class="as-gap">…</div>'); gap = false; }
      html.push('<div class="as-line as-' + (r[0] === "+" ? "add" : r[0] === "-" ? "del" : "eq") + '">' + esc(r[0]) + " " + esc(r[1]) + "</div>");
    });
    return '<pre class="as-diff">' + html.join("") + "</pre>";
  }
  function done(x) { return '<span class="as-done" title="done">✓</span>'; }
  function sec(id, title, doneFlag, body, keepOpen) {
    return '<details class="as-sec" id="as-' + id + '"' + (doneFlag && !keepOpen ? "" : " open") + '><summary>' + (doneFlag ? done() : '<span class="as-todo">●</span>') + " " + esc(title) + "</summary><div class=\"as-body\">" + body + "</div></details>";
  }

  /* docs/288: the setup page is a short list of CARDS a person can read once:
     what is ready, what is left, one primary button per thing. Paths, hook
     command lines and the exact file changes stay one click away ("Technical
     details", "Show exact changes"), never in the first read. */
  var NAMES = { claude: "Claude Code", codex: "Codex" };
  // the hardware switch's words follow the SAVED value (render and dryRun share them)
  function dryText(on) { return on ? "Dry run — the agent's runs are simulated, nothing touches the OPX" : "Live — the agent's runs use the OPX"; }
  var MARKS = { claude: "CC", codex: "CX" };
  function chk(ok, label, title) {
    return '<li class="asx-chk ' + (ok ? "asx-ok" : "asx-no") + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">"
      + (ok ? "✓" : "○") + " " + esc(label) + "</li>";
  }
  function state(cls, text) { return '<span class="asx-state asx-' + cls + '">' + esc(text) + "</span>"; }
  function card(id, n, title, isDone, body, opts) {
    opts = opts || {};
    return '<section class="asx-card' + (isDone ? " asx-card-done" : "") + '" id="as-' + id + '">'
      + '<div class="asx-card-head"><span class="asx-step' + (isDone ? " asx-step-done" : "") + '">' + (isDone ? "✓" : esc(n)) + "</span>"
      + "<h3>" + esc(title) + "</h3>" + (opts.tag ? '<span class="asx-tag">' + esc(opts.tag) + "</span>" : "") + "</div>"
      + '<div class="asx-card-body">' + body + "</div></section>";
  }
  // "codex-cli 0.159.2" / "2.1.289 (Claude Code)" -> the version number itself
  function verOf(v) { var m = /\d+\.\d+(?:\.\d+)?/.exec(String(v || "")); return m ? "v" + m[0] : String(v || ""); }
  function tile(k, d) {
    var clis = d.clis || {}, c = (k === "claude" ? d.claude : d.codex) || {}, cli = clis[k] || {};
    if (!cli.found) {
      return '<div class="asx-tile asx-tile-off" id="as-connect-' + k + '">'
        + '<div class="asx-tile-top"><span class="asx-logo asx-logo-' + k + '">' + esc(MARKS[k]) + "</span><div><b>" + esc(NAMES[k]) + "</b></div>" + state("off", "Not installed") + "</div>"
        + '<p class="asx-small">Install it, then log in once in a terminal on this PC. SM never sees your login.</p></div>';
    }
    var connected = k === "claude" ? (c.mcp && c.hooks) : c.mcp;
    var checks = [chk(c.mcp, "SM's tools", "SM is registered as an MCP server named quam-state-manager in " + NAMES[k] + "'s own settings")];
    if (k === "claude") {
      checks.push(chk(c.hooks, "Runs reported to SM", "a hook in Claude Code's settings tells SM about each run it starts"));
      if (d.calibrations_folder) checks.push(chk(c.allow, "No permission prompts", "SM's tools are pre-allowed in the calibrations folder"));
    }
    var tested = ((d.record || {}).tested || {})[k];
    var acts = connected
      ? '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.test(\'' + k + '\')">Confirm</button>'
        + '<button type="button" class="asx-link" onclick="AgentSetup.disconnect(\'' + k + '\')">Disconnect</button>'
      : '<button type="button" class="btn-sm asx-btn" onclick="AgentSetup.preview(\'' + k + '\')">Connect</button>';
    var warn = "";
    if (k === "claude" && d.calibrations_folder && c.allow_python && c.allow_python.length) {
      warn = '<p class="ag-err asx-small">' + esc(c.allow_python.join(", ")) + " is allowed in the calibrations folder: a terminal agent can run a node script past SM's checks. Disconnect removes it if SM wrote it.</p>";
    }
    return '<div class="asx-tile ' + (connected ? "asx-tile-ok" : "asx-tile-todo") + '" id="as-connect-' + k + '">'
      + '<div class="asx-tile-top"><span class="asx-logo asx-logo-' + k + '">' + esc(MARKS[k]) + "</span><div><b>" + esc(NAMES[k]) + "</b>"
      + (cli.version ? ' <span class="muted asx-ver">' + esc(verOf(cli.version)) + "</span>" : "") + "</div>"
      + (connected ? state("ok", "Connected") : state("todo", "Not connected")) + "</div>"
      + '<ul class="asx-checks">' + checks.join("") + "</ul>"
      + (tested && tested.ok ? '<p class="asx-small muted">answered a test question' + (tested.elapsed_s ? " in " + esc(tested.elapsed_s) + " s" : "") + "</p>" : "")
      + warn
      + '<div class="asx-tile-acts">' + acts + "</div>"
      // the pin is an option OF Connect (it is read once, by preview): on a
      // connected tile an unchecked box would claim a state SM never read
      + (connected ? "" : '<label class="asx-opt" title="Without this, a terminal agent asks which SM window and chip to use when several are open."><input type="checkbox" id="as-pin-' + k + '"> Always use this window and chip</label>')
      + '<div id="as-prev-' + k + '" class="asx-prev"></div></div>';
  }

  /* ---- Step 2 rows (decisions D-1..D-11) ---- */
  /* ---- Confirm by agent (D-11..D-15, Q-6: it absorbs the old Test) ---- */
  var ROW_LABEL = { chip_name: "Chip (sm_status)", n_qubits: "Qubits", cwd: "Working folder", config_file: "Config file",
                    state_path_in_config: "state_path in the config", calibrations_folder_in_config: "calibration_library.folder in the config",
                    storage_in_config: "storage.location in the config", node_files: "Node files it sees", run_folders: "Run folders it sees" };
  function short(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (Array.isArray(v)) return v.length ? v.slice(0, 5).join(", ") + (v.length > 5 ? " … (" + v.length + ")" : "") : "—";
    return String(v);
  }
  function agentCell(row) {
    if (row.agent === "agrees") return "agrees";
    if (row.agent === "disagrees") return "disagrees — agent saw: " + short(row.agent_value);
    if (row.agent === "unverified") return "unverified — its citation did not check out" + (row.agent_value ? " (said: " + short(row.agent_value) + ")" : "");
    if (row.agent === "reported") return "reported: " + short(row.agent_value);
    return "not reported";
  }
  function staleLine(rec, P) {
    if (!rec || rec.current) return "";
    var when = rec.at ? new Date(rec.at * 1000).toLocaleTimeString() : "";
    return '<p class="asx-small ag-err" data-stale="1">Result for project ' + esc((rec.key || {}).project || "none") + " · " + esc(when) + " — run again</p>";
  }
  function askFail(rec) {
    var who = NAMES[rec.backend] || rec.backend;
    var why = String(rec.error || rec.parse_error || "no answer");
    // D-14: the CLI's own words; SM does not classify a failure
    var login = /not logged in|\/login|please log ?in/i.test(why);
    return '<div class="asx-result asx-result-bad"><p><b>' + esc(who) + "</b> did not answer" + (rec.elapsed_s ? " (" + esc(rec.elapsed_s) + " s)" : "")
      + ': <span class="ag-err">' + esc(why) + "</span></p>"
      + (login ? '<p class="asx-small">Open a terminal on this PC, run <code>' + esc(rec.backend) + "</code> once and log in there; then press Confirm again. SM never sees your login.</p>" : "")
      + (rec.raw ? '<details class="asx-tech"><summary>Show the raw answer</summary><pre class="as-diff">' + esc(rec.raw) + "</pre></details>" : "") + "</div>";
  }
  function confirmResult(rec, P) {
    if (!rec) return "";
    var who = NAMES[rec.backend] || rec.backend;
    if (rec.failed || !rec.done || rec.parse_error) {
      if (rec.done && !rec.failed && rec.parse_error) {
        return staleLine(rec, P) + '<div class="asx-result asx-result-bad"><p><b>' + esc(who) + "</b> answered in " + esc(rec.elapsed_s) + " s, but the answer was not in the expected form.</p>"
          + (rec.raw ? '<details class="asx-tech"><summary>Show the raw answer</summary><pre class="as-diff">' + esc(rec.raw) + "</pre></details>" : "") + "</div>";
      }
      return staleLine(rec, P) + askFail(rec);
    }
    var rows = (rec.rows || []).map(function (r) {
      var mine = r.sm === null || r.sm === undefined || (Array.isArray(r.sm) && !r.sm.length) ? "—" : short(r.sm);
      var cls = r.agent === "agrees" ? "asx-ok" : "asx-no";
      return '<li class="asx-chk ' + cls + '" data-row="' + esc(r.key) + '" data-agent="' + esc(r.agent) + '">' + esc(ROW_LABEL[r.key] || r.key)
        + ': <span class="muted">SM: ' + esc(mine) + "</span> · " + esc(agentCell(r)) + "</li>";
    }).join("");
    return staleLine(rec, P) + '<div class="asx-result asx-result-ok" id="as-confirm-result"><p><b>' + esc(who) + "</b> answered in <b>" + esc(rec.elapsed_s) + " s</b>"
      + (rec.tools && rec.tools.length ? ' <span class="muted">(used ' + esc(rec.tools.join(", ")) + ")</span>" : "")
      + ' <span class="muted">· ' + esc(new Date((rec.at || 0) * 1000).toLocaleTimeString()) + "</span></p>"
      + '<ul class="asx-checks">' + rows + "</ul>"
      + (rec.evidence_dropped ? '<p class="asx-small muted">' + esc(rec.evidence_dropped) + " extra citations were dropped</p>" : "")
      + (rec.notes ? '<p class="asx-small muted">agent notes: ' + esc(rec.notes) + "</p>" : "") + "</div>";
  }
  function confirmHtml(d) {
    var clis = d.clis || {};
    var ks = ["claude", "codex"].filter(function (k) { return clis[k] && clis[k].found; });
    var running = (S.asks || {}).running;
    var btns = ks.map(function (k) {
      return '<button type="button" class="btn-sm outline asx-btn-2" data-confirm="' + k + '"' + (running ? " disabled" : "")
        + ' onclick="AgentSetup.confirmBy(\'' + k + '\')">Confirm by ' + esc(NAMES[k]) + "</button>";
    }).join("");
    return '<div class="asx-row" id="as-confirm"><span class="asx-k">Second opinion</span><span class="asx-v asx-grow">'
      + (ks.length ? btns : '<span class="muted">no agent CLI on this PC</span>') + "</span></div>"
      + '<p class="asx-small muted asx-indent">The agent reports, read-only, what it sees from its own seat; SM compares it with the rows above and checks every citation. Only when pressed.</p>'
      + '<div id="as-test">' + (S.confirmMsg || confirmResult((S.asks || {}).confirm, d.paths || {})) + "</div>";
  }
  function confirmBy(k) {
    if (S.asks && S.asks.running) return;
    S.asks = S.asks || {};
    S.asks.running = "confirm";
    S.confirmMsg = '<p class="muted">asking ' + esc(NAMES[k] || k) + " to report what it sees (read-only, up to 2 min)…</p>";
    render();
    api("POST", "/api/agent/setup/confirm", { backend: k }).then(function (r) {
      S.asks.running = null;
      S.confirmMsg = null;
      var b = r.body || {};
      if (r.status !== 200) S.confirmMsg = '<p class="ag-err" role="alert">' + esc(b.error || ("failed (HTTP " + r.status + ")")) + "</p>";
      else S.asks.confirm = b;
      load();
    });
  }
  function fileName(P, which) {
    var f = (P.files || {})[which] || {};
    return f.path || (which === "root" ? "~/.qualibrate/config.toml" : "projects/<project>/config.toml");
  }
  // D-2: the chip names the file a value came from
  function prov(entry, P) {
    if (!entry) return "";
    if (entry.source === "own") return "from project " + P.project + " · " + fileName(P, "project");
    if (entry.source === "inherited") return "global config · " + fileName(P, "root");
    if (entry.source === "default") return "qualibrate default";
    if (entry.source === "empty") return "set empty in " + fileName(P, "project");
    return "";
  }
  function dataProv(P) {
    return { project: P.storage ? prov(P.storage, P) : "the project's storage", chip: "the chip's extras.data_folder",
             dataset: "the first dataset folder" }[P.data_source] || "";
  }
  function pathRow(id, label, value, provText, extra) {
    return '<div class="asx-row" id="' + id + '"><span class="asx-k">' + esc(label) + '</span><span class="asx-v asx-grow">'
      + (value ? '<code class="asx-path">' + esc(value) + "</code>" : '<span class="muted">not found</span>') + (extra || "") + "</span>"
      + (provText ? '<span class="asx-tag">' + esc(provText) + "</span>" : "") + "</div>";
  }
  var CAL_KEY = "[qualibrate.calibration_library] folder";
  var REASONS = { unset: "is not set", empty: "is set to an empty value", no_config: "cannot be read (no config file)",
                  unmapped: "cannot be mapped to a folder on this PC", missing: "names a folder that does not exist",
                  override_missing: "your override names a folder that does not exist" };
  function calibRow(P, d) {
    var c = P.calibrations || (d.calibrations_folder ? { folder: d.calibrations_folder, source: "legacy" } : {});
    var pick = '<input type="hidden" id="as-calroot" value="" onchange="AgentSetup.calibPicked(this)">'
      + '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.browseCalib()">Choose…</button>';
    var head = '<div class="asx-row" id="as-calib"><span class="asx-k">Calibrations folder</span><span class="asx-v asx-grow">';
    var legacyDoor = c.legacy_ok ? '<p class="asx-small">previous SM setting: <code class="asx-path">' + esc(c.legacy) + '</code> '
      + '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.useLegacy()">Use it</button></p>' : "";
    if (!P.project) {
      // D-5: no project -- an honest line, the legacy value in force if any, and the browse door
      return head + (c.folder ? '<code class="asx-path">' + esc(c.folder) + "</code>" : '<span class="muted">not set</span>') + pick + "</span>"
        + (c.folder ? '<span class="asx-tag">previous SM setting</span>' + state("ok", "In use") : state("todo", "Not set")) + "</div>"
        + '<p class="asx-small asx-indent" id="as-calib-noproj">No QUAlibrate project open — <a href="/?landing=1">open one on the landing page</a>.</p>'
        + '<span id="as-calib-msg"></span>';
    }
    if (c.folder) {
      var ov = c.source === "override";
      var e = c.entry || {};
      var t = ov ? "your override (SM setting)" : prov(e, P);
      return head + '<code class="asx-path">' + esc(c.folder) + "</code>" + pick
        + (ov ? '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.useProject()">Use project value</button>' : "") + "</span>"
        + '<span class="asx-tag" id="as-calib-prov">' + esc(t) + "</span>" + state("ok", "In use") + "</div>"
        + (!ov && e.overridden ? '<p class="asx-small muted asx-indent">global config ' + esc(e.overridden) + " — overridden by project</p>" : "")
        + (ov ? '<p class="asx-small muted asx-indent">A graph refuses to run when the env\'s own calibration library differs from this folder.</p>' : "")
        + '<span id="as-calib-msg"></span>';
    }
    // D-6: amber -- the raw value, the missing key and its file, two explicit doors, nothing picked
    var e2 = c.entry || {};
    var file = e2.source === "inherited" ? fileName(P, "root") : fileName(P, "project");
    return head + '<span class="ag-err" id="as-calib-amber">' + esc(CAL_KEY) + " " + esc(REASONS[c.reason] || "is not usable")
      + (e2.raw ? ": <code>" + esc(e2.raw) + "</code>" : "") + " — in " + esc(file) + "</span>" + pick + "</span>"
      + state("todo", "Not set") + "</div>" + legacyDoor + '<span id="as-calib-msg"></span>';
  }
  function checksHtml(P) {
    var cs = P.checks || [];
    if (!cs.length) return "";
    return '<div class="asx-row" id="as-checks"><span class="asx-k">SM checks</span><ul class="asx-checks">'
      + cs.map(function (c) { return '<li class="asx-chk ' + (c.ok ? "asx-ok" : "asx-no") + '" data-check="' + esc(c.key) + '" title="' + esc(c.detail || "") + '">'
        + (c.ok === true ? "✓" : c.ok === false ? "✗" : "○") + " " + esc(c.label) + ' <span class="muted">' + esc(c.detail || "") + "</span></li>"; }).join("")
      + "</ul></div>";
  }

  function render() {
    var d = S.data, root = S.root;
    if (!d || !root) return;
    var clis = d.clis || {};
    var anyCli = !!(clis.claude && clis.claude.found) || !!(clis.codex && clis.codex.found);
    var connectedAny = (clis.claude && clis.claude.found && d.claude && d.claude.mcp) || (clis.codex && clis.codex.found && d.codex && d.codex.mcp);
    var j = d.journal || {};
    var dry = d.global_simulate !== false;
    var tested = (d.record && d.record.tested) || {};
    var ctxUnread = d.context_unread || [];
    var ctxDone = d.context && Object.keys(d.context).length > 0 && !ctxUnread.length;
    var testedOk = !!(tested.claude && tested.claude.ok) || !!(tested.codex && tested.codex.ok);
    var envDone = !!d.calibrations_folder && !!j.configured;
    var steps = [connectedAny, envDone, !!ctxDone];
    var nDone = steps.filter(Boolean).length;
    var parts = [];
    parts.push('<div class="asx-progress"><span class="asx-bar"><span style="width:' + Math.round(100 * nDone / steps.length) + '%"></span></span>'
      // D-122: a count of steps, never a bare "0 of 4"
      + (nDone ? "<b>" + nDone + " of " + steps.length + " steps</b> done" : "<b>Not set up yet</b> · " + steps.length + " short steps")
      + '<span class="muted asx-small"> · the agent inside SM works without any of this · nothing here starts hardware · every file SM changes keeps a backup</span></div>');

    // 1. connect
    parts.push(card("connect", 1, "Connect a terminal agent", connectedAny,
      '<p class="asx-lead">Lets a <b>Claude Code</b> or <b>Codex</b> you start in a terminal use SM: read the chip, propose plans you approve here, and report its runs to the Calibration log.</p>'
      + (anyCli ? "" : '<p class="ag-err">Neither CLI was found on this PC.</p>')
      + '<div class="asx-tiles">' + tile("claude", d) + tile("codex", d) + "</div>", { tag: "optional" }));

    // 2. where runs live -- decisions D-1..D-11: the values come from the open
    // QUAlibrate project through SM's one resolver, each with where it came
    // from; an override is chosen in the folder browser only
    var P = d.paths || {};
    var env = [calibRow(P, d)];
    env.push(pathRow("as-storage", "Data folder", P.data_folder, dataProv(P), null));
    if (P.project) env.push(pathRow("as-statepath", "State path", P.state_path && P.state_path.native, prov(P.state_path, P), null));
    env.push('<div class="asx-row" id="as-dryrun"><span class="asx-k">Hardware</span><span class="asx-v">'
      + '<label class="asx-switch"><input type="checkbox" id="as-dryrun-box"' + (dry ? " checked" : "") + ' onchange="AgentSetup.dryRun(this)"><span class="asx-knob"></span>'
      + '<span class="asx-dry-text">' + dryText(dry) + "</span></label></span>"
      + '<span class="asx-tag" title="the Runner\'s global_simulate, one value for this machine">SM setting, this machine</span>'
      + '<span id="as-dryrun-msg"></span></div>');
    var jVal = j.configured ? j.root : (j.suggested || "");
    env.push('<div class="asx-row" id="as-journal"><span class="asx-k">Journal folder</span><span class="asx-v asx-grow">'
      + '<code class="asx-path" id="as-jpath">' + esc(jVal || "not chosen") + "</code>"
      + '<input type="hidden" id="as-jroot" value="' + esc(jVal) + '" onchange="AgentSetup.journal()">'
      + '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.browseJournal()">Choose…</button>'
      + (!j.configured && j.suggested ? '<button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.journal()">Use this folder</button>' : "")
      + "</span>" + '<span class="asx-tag">' + esc(j.configured ? "your choice (SM setting)" : "beside the data folder") + "</span>"
      + (j.configured ? state("ok", "Saved") : state("todo", "Not saved yet")) + "</div>"
      + '<label class="asx-opt asx-indent"><input type="checkbox" id="as-jsays"' + (j.claude_says ? " checked" : "") + "> Also record what the agent says at the end of a turn</label>");
    env.push(checksHtml(P));
    env.push(confirmHtml(d));
    parts.push(card("env", 2, "Where runs and notes live", envDone, env.join("")));

    // 3. lab context
    parts.push(card("context", 3, "Tell the agent about this device", !!ctxDone,
      (ctxUnread.length ? '<p class="ag-err asx-small">Codex never reads ' + esc(ctxUnread.join(", ")) + ": write the notes again to move them into AGENTS.md.</p>" : "")
      + (ctxDone ? '<p class="asx-small muted">Written for ' + Object.keys(d.context).map(esc).join(" and ") + ". You can update the answers any time.</p>" : '<p class="asx-lead">A few questions SM cannot read from the chip (how flux is biased, what must never be touched). The agent reads the answers before it acts.</p>')
      + '<div id="as-ctx">' + (d.calibrations_folder ? '<button type="button" class="btn-sm ' + (ctxDone ? "outline asx-btn-2" : "asx-btn") + '" onclick="AgentSetup.loadContext()">' + (ctxDone ? "Edit the answers" : "Answer the questions") + "</button>"
        : '<p class="muted asx-small">Set the calibrations folder first (step 2).</p>') + "</div>"));

    // limits -- the PERSON's (docs/252): SM refuses an agent's request to change them
    if (d.chip && S.limits && S.limits.limits) {
      var lim = S.limits.limits, modes = S.limits.modes || ["auto", "ask-writes", "ask-all"];
      var v = function (x) { return esc(x === undefined || x === null ? "" : String(x)); };
      var delta = lim.max_delta && Object.keys(lim.max_delta).length ? JSON.stringify(lim.max_delta) : "";
      parts.push('<details class="asx-card asx-fold" id="as-limits"' + (S.limitsMsg ? " open" : "") + '><summary><span class="asx-step asx-step-done">⚙</span><h3>Safety limits for ' + esc(d.chip) + '</h3><span class="asx-tag">' + esc(lim.mode || "") + "</span></summary>"
        + '<div class="asx-card-body"><p class="asx-small muted">What SM enforces on the agent, whatever it is told. Only a person changes these, here; every change is recorded in the journal.</p>'
        + '<div class="as-limits asx-limits">'
        + '<label>Default mode <select id="as-lim-mode">' + modes.map(function (m) { return '<option value="' + esc(m) + '"' + (m === lim.mode ? " selected" : "") + ">" + esc(m) + "</option>"; }).join("") + "</select></label>"
        + '<label>Max writes per plan <input id="as-lim-maxw" type="number" min="0" value="' + v(lim.max_writes_per_plan) + '"> <span class="muted">0 = no cap</span></label>'
        + '<label>Refuse a run within <input id="as-lim-recent" type="number" min="0" value="' + v(lim.human_recent_min) + '"> min of a person\'s run</label>'
        + '<label>Stop by <input id="as-lim-stop" type="text" placeholder="HH:MM" value="' + v(lim.stop_by) + '"></label>'
        + '<label>Webhook <input id="as-lim-hook" type="text" placeholder="https://..." value="' + v(lim.webhook_url) + '"></label>'
        + '<label>Max |&Delta;| per family <input id="as-lim-delta" type="text" placeholder=\'{"ramsey": 2e6}\' value="' + v(delta) + '"></label>'
        + '</div><div class="asx-tile-acts"><button type="button" class="btn-sm outline asx-btn-2" onclick="AgentSetup.saveLimits()">Save limits</button></div>'
        + '<div id="as-lim-msg"></div></div></details>');
    }

    // technical details: every path and command line, for whoever needs them
    var tech = [];
    if (d.claude) tech.push("<li>Claude Code MCP list: <code>" + esc(d.claude.json) + "</code></li><li>Claude Code hooks: <code>" + esc(d.claude.settings) + "</code></li>");
    if (d.codex) tech.push("<li>Codex settings: <code>" + esc(d.codex.config) + "</code></li>");
    if (d.calibrations_folder) tech.push("<li>Allowed tools: <code>" + esc(d.calibrations_folder) + "\\.claude\\settings.local.json</code></li>");
    tech.push("<li>Journal: <code>" + esc(j.root || "") + "</code></li>");
    if (d.hook_command) tech.push("<li>Hook command: <code>" + esc(d.hook_command) + "</code></li>");
    parts.push('<details class="asx-tech"><summary>Technical details</summary><ul>' + tech.join("") + "</ul>"
      + '<p class="asx-small muted">Each file SM changes gets a dated backup beside it (<code>*.sm-backup-YYYYMMDD-HHMMSS</code>). Login stays in the terminal on this PC\'s own account.</p></details>');

    root.innerHTML = '<div class="asx">' + parts.join("") + "</div>";
    var lm = document.getElementById("as-lim-msg");
    if (lm && S.limitsMsg) lm.innerHTML = S.limitsMsg;
    if (window.htmx) { try { window.htmx.process(root); } catch (e) { /* ignore */ } }
  }

  function loadLimits() {
    return api("GET", "/api/agent/limits").then(function (r) {
      S.limits = r.status === 200 && r.body && r.body.limits ? r.body : null;
    });
  }
  function loadAsks() {
    return api("GET", "/api/agent/setup/asks").then(function (r) {
      if (r.status === 200 && r.body) S.asks = r.body;
    });
  }
  function load() {
    var lims = loadLimits();
    var asks = loadAsks();
    lims = Promise.all([lims, asks]);
    return api("GET", "/api/agent/setup").then(function (r) {
      if (r.status === 200) { S.data = r.body; return lims.then(render, render); }
      // Say so in the page rather than leaving it empty.
      var root = S.root || document.getElementById("agent-setup-root");
      if (root) {
        root.innerHTML = '<p class="ag-err">Could not load the setup status — '
          + esc((r.body && r.body.error) || ("HTTP " + r.status))
          + '</p><p class="muted">The page is otherwise fine; reload once the '
          + 'reason above is gone.</p>';
      }
    });
  }

  /* docs/288: Connect is ONE decision. The press asks the server what it would
     write (nothing is written yet) and says it in a sentence -- which settings
     of which program, with a backup each; the exact line-by-line changes are a
     disclosure, not the first thing a person reads. */
  var FILE_WORDS = { mcp: "add SM to its list of tools", hooks: "report the runs it starts to SM", allow: "stop asking permission for SM's tools in the calibrations folder" };
  function preview(k) {
    var box = document.getElementById("as-pin-" + k);
    var pinned = !!(box && box.checked);
    var host = document.getElementById("as-prev-" + k);
    // while the question is open the tile's own Connect + pin step aside: one
    // Connect button on screen, and the pin already read cannot be changed
    asking(k, true);
    if (host) host.innerHTML = '<p class="muted asx-small">checking what would change…</p>';
    api("POST", "/api/agent/setup/connect", { backend: k, pinned: pinned }).then(function (r) {
      host = document.getElementById("as-prev-" + k);
      if (!host) return;
      if (r.status !== 200) { asking(k, false); host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      S.previews[k] = { pinned: pinned, expected_pin: r.body.pin };
      var pv = r.body.previews || {};
      var names = Object.keys(pv);
      var what = names.map(function (n) { return "<li>" + esc(FILE_WORDS[n] || n) + "</li>"; }).join("");
      var diffs = names.map(function (name) {
        var p = pv[name];
        return '<div class="asx-diffhead"><code>' + esc(p.file) + "</code>" + (p.exists ? "" : ' <span class="muted">(new file)</span>') + "</div>" + diffHtml(p.before, p.after);
      }).join("");
      host.innerHTML = '<div class="asx-confirm"><p>SM will change ' + esc(NAMES[k]) + "'s settings to:</p><ul>" + what + "</ul>"
        + '<p class="asx-small muted">A backup of each file is kept beside it; Disconnect takes the changes back.</p>'
        + '<div class="asx-tile-acts"><button type="button" class="btn-sm asx-btn ag-start" onclick="AgentSetup.connect(\'' + k + '\')">Connect ' + esc(NAMES[k]) + "</button>"
        + '<button type="button" class="asx-link" onclick="AgentSetup.cancel(\'' + k + '\')">Cancel</button></div>'
        + '<details class="asx-tech"><summary>Show exact changes</summary>' + diffs + "</details></div>";
    });
  }
  function asking(k, on) {
    var t = document.getElementById("as-connect-" + k);
    if (t) t.classList.toggle("asx-asking", !!on);
  }
  function cancel(k) {
    var host = document.getElementById("as-prev-" + k);
    if (host) host.innerHTML = "";
    asking(k, false);
    delete S.previews[k];
  }
  function connect(k) {
    var pin = S.previews[k] || {};
    var host = document.getElementById("as-prev-" + k);
    if (host) host.innerHTML = '<p class="muted asx-small">connecting…</p>';
    api("POST", "/api/agent/setup/connect", { backend: k, apply: true, pinned: !!pin.pinned, expected_pin: pin.expected_pin }).then(function (r) {
      host = document.getElementById("as-prev-" + k);
      if (r.status !== 200) { if (host) host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      S.flash = S.flash || {};
      S.flash[k] = true;
      load().then(function () {
        var h = document.getElementById("as-prev-" + k);
        if (h) h.innerHTML = '<p class="asx-small asx-good">' + esc(NAMES[k]) + " is connected. Press <b>Test</b> to check it answers through SM.</p>";
      });
    });
  }
  function disconnect(k) {
    if (window.confirm && !window.confirm("Disconnect " + (NAMES[k] || k) + " from SM? SM's entries are removed from its settings; backups are kept.")) return;
    api("POST", "/api/agent/setup/disconnect", { backend: k }).then(function () { load(); });
  }
  function journal() {
    var root = (document.getElementById("as-jroot") || {}).value || "";
    var says = !!(document.getElementById("as-jsays") || {}).checked;
    api("POST", "/api/agent/setup/journal", { root: root, claude_says: says }).then(function (r) {
      if (r.status !== 200) { alertErr(r.body.error, "#as-journal"); return; }
      load();
    });
  }
  // D-4: a folder is chosen in the folder browser (its own path field takes a
  // typed or pasted path); the browser starts near the other folder or the chip
  function browseFrom(id, near) {
    if (!window.openFolderBrowser) return;
    var chip = document.getElementById("load-path-input");
    var start = near || (chip && chip.value.trim());
    window.openFolderBrowser(id, "", { autoSubmit: false });
    if (start && window.navigateBrowser) window.navigateBrowser(start);
  }
  function _p() { return (S.data && S.data.paths) || {}; }
  function browseCalib() { var P = _p(); browseFrom("as-calroot", (P.calibrations || {}).folder || P.data_folder); }
  function browseJournal() { var P = _p(); browseFrom("as-jroot", (S.data && S.data.journal && S.data.journal.root) || P.data_folder); }
  function calMsg(r) {
    var msg = document.getElementById("as-calib-msg");
    var b = r.body || {};
    if (r.status === 200 && b.ok !== false) { load(); return; }
    if (msg) msg.innerHTML = '<p class="ag-err" role="alert">' + esc(b.error || ("not saved (HTTP " + r.status + ")")) + "</p>";
  }
  function calibPicked(el) {
    var v = String((el && el.value) || "").trim();
    if (!v) return;
    var P = _p();
    if (!P.project) {
      // no project scoped: the legacy machine setting is the only place a folder can live (D-5)
      api("POST", "/scheduler/settings", { calibrations_folder: v }).then(calMsg);
      return;
    }
    api("POST", "/api/agent/setup/calibrations", { folder: v }).then(calMsg);
  }
  function useProject() { api("POST", "/api/agent/setup/calibrations", { clear: true }).then(calMsg); }
  function useLegacy() { api("POST", "/api/agent/setup/calibrations", { use_legacy: true }).then(calMsg); }
  function dryRun(el) {
    // POST the one key; the reply is shown inline. On a refusal (409 while a
    // queue runs, or anything else) the server's error is shown VERBATIM and
    // the box goes back to what is persisted (re-read, not assumed).
    var want = !!el.checked, msg = document.getElementById("as-dryrun-msg");
    function mark(v) {
      // docs/288: the switch's words follow the SAVED value without a re-render
      var t = document.querySelector("#as-dryrun .asx-dry-text");
      if (t) t.textContent = dryText(v);
    }
    function revert() {
      // back to what is PERSISTED (re-read, not assumed); a server that cannot
      // answer the re-read either -> the last value the page knew
      var fallback = S.data ? S.data.global_simulate !== false : true;
      api("GET", "/scheduler/settings").then(function (g) {
        return g.status === 200 && g.body && g.body.global_simulate !== undefined ? g.body.global_simulate !== false : fallback;
      }, function () { return fallback; }).then(function (persisted) {
        el.checked = persisted;
        el.disabled = false;
        if (S.data) S.data.global_simulate = persisted;
      });
    }
    el.disabled = true;
    api("POST", "/scheduler/settings", { global_simulate: want }).then(function (r) {
      var b = r.body || {};
      if (r.status === 200 && b.ok !== false) {
        el.disabled = false;
        var v = b.settings && b.settings.global_simulate !== undefined ? !!b.settings.global_simulate : want;
        el.checked = v;
        if (S.data) S.data.global_simulate = v;     // a later render() keeps the saved value
        mark(v);
        if (msg) msg.innerHTML = '<p class="muted">Saved — dry run ' + (v ? "ON" : "OFF") + "</p>";
        return;
      }
      if (msg) msg.innerHTML = '<p class="ag-err">' + esc(b.error || ("not saved (HTTP " + r.status + ")")) + "</p>";
      revert();
    }, function (e) {
      // the fetch itself failed (server restarting, offline): say so, never
      // leave the box disabled, and go back to the persisted value
      if (msg) msg.innerHTML = '<p class="ag-err">' + esc("not saved — " + ((e && e.message) || String(e))) + "</p>";
      revert();
    });
  }
  /* Say it where it happened.
   *
   * This used to prepend into #as-body — the root of the page — so a failure
   * from step 4 rendered 792 px above the top of the scroller and removed
   * itself after 6 s: the press was indistinguishable from doing nothing
   * (measured twice, and the message itself was a good one). Seven of the
   * page's nine error paths already answer beside their own control; this is
   * the eighth and ninth.
   *
   * `near` is a selector for the section that failed. With none — or when it
   * is not on screen — the message goes to the top AND the page is scrolled to
   * it, because an unseen error is the whole defect.
   */
  function alertErr(msg, near) {
    var text = msg || "failed";
    var host = near && document.querySelector(near);
    var p = document.createElement("p");
    p.className = "ag-err";
    p.setAttribute("role", "alert");
    p.textContent = text;
    if (host) {
      var old = host.querySelector(":scope > .ag-err");
      if (old) old.remove();
      host.appendChild(p);
    } else {
      var el = document.getElementById("as-body");
      var prev = el.querySelector(":scope > .ag-err");
      if (prev) prev.remove();
      el.insertBefore(p, el.firstChild);
    }
    // It stays until the next attempt: a message that deletes itself after six
    // seconds is one a person can miss entirely, and this one names the exact
    // path that could not be used.
    try { p.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) {}
  }
  function loadContext() {
    api("GET", "/api/agent/setup/context").then(function (r) {
      var host = document.getElementById("as-ctx");
      if (!host) return;
      if (r.status !== 200) { host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      S.context = r.body;
      var f = r.body.facts || {};
      var html = ['<p class="muted">SM sees: ' + f.n_qubits + " qubits, " + f.n_pairs + " pairs; bias sources " + esc(JSON.stringify(f.bias_modes || {})) + (f.nodes && f.nodes.length ? "; " + f.nodes.length + " node files" : "") + ". It cannot see what follows — please confirm.</p>"];
      // what the lab answered last time comes before what SM detects: writing the context again
      // must never drop a note the lab wrote (measured: "Never retry hardware" vanished on a re-write)
      var saved = r.body.saved || {};
      (r.body.questions || []).forEach(function (q) {
        S.answers[q.id] = S.answers[q.id] !== undefined ? S.answers[q.id] : (saved[q.id] !== undefined ? saved[q.id] : q.detected);
        html.push('<div class="as-q"><label>' + esc(q.question) + (q.why ? ' <span class="muted" title="' + esc(q.why) + '">(why?)</span>' : "") + "</label>");
        if (q.kind === "choice") {
          html.push('<select data-q="' + esc(q.id) + '" onchange="AgentSetup.answer(this)">' + q.options.map(function (o) { return '<option value="' + esc(o) + '"' + (o === S.answers[q.id] ? " selected" : "") + ">" + esc(o) + (o === q.detected ? " (detected)" : "") + "</option>"; }).join("") + "</select>");
        } else {
          html.push('<textarea data-q="' + esc(q.id) + '" rows="3" onchange="AgentSetup.answer(this)">' + esc(S.answers[q.id] || "") + "</textarea>");
        }
        html.push("</div>");
      });
      html.push('<div class="as-acts"><label class="ag-observer"><input type="checkbox" id="as-ctx-local" checked> Claude: CLAUDE.local.md (not shared through git)</label> ' +
        '<span class="muted" title="Codex reads one project file per folder: AGENTS.md. It ignores AGENTS.local.md.">Codex: always AGENTS.md</span> ' +
        '<label class="ag-observer"><input type="checkbox" id="as-ctx-claude" checked> CLAUDE</label> <label class="ag-observer"><input type="checkbox" id="as-ctx-codex" checked> AGENTS</label> ' +
        '<button type="button" class="btn-sm" onclick="AgentSetup.previewContext()">Preview the block</button></div><div id="as-ctx-prev"></div>');
      host.innerHTML = html.join("");
    });
  }
  function saveLimits() {
    var g = function (id) { return String((document.getElementById(id) || {}).value || "").trim(); };
    var body = { mode: g("as-lim-mode"), max_writes_per_plan: g("as-lim-maxw"), human_recent_min: g("as-lim-recent"),
                 stop_by: g("as-lim-stop"), webhook_url: g("as-lim-hook"), max_delta: g("as-lim-delta") || "{}" };
    var msg = document.getElementById("as-lim-msg");
    api("POST", "/api/agent/limits", body).then(function (r) {
      var b = r.body || {};
      if (r.status !== 200 || b.ok === false) {
        S.limitsMsg = '<p class="ag-err" role="alert">Not saved: ' + esc(b.error || ("HTTP " + r.status)) + "</p>";
        if (msg) msg.innerHTML = S.limitsMsg;
        return;
      }
      S.limits = { limits: b.limits, modes: (S.limits || {}).modes };
      S.limitsMsg = '<p class="muted">Saved; any change is in the journal.</p>';
      render();
    });
  }
  function answer(el) { S.answers[el.getAttribute("data-q")] = el.value; }
  function ctxBody(apply) {
    var targets = [];
    if ((document.getElementById("as-ctx-claude") || {}).checked) targets.push("claude");
    if ((document.getElementById("as-ctx-codex") || {}).checked) targets.push("codex");
    return { answers: S.answers, local: !!(document.getElementById("as-ctx-local") || { checked: true }).checked, targets: targets, apply: !!apply };
  }
  function previewContext() {
    api("POST", "/api/agent/setup/context", ctxBody(false)).then(function (r) {
      var host = document.getElementById("as-ctx-prev");
      if (!host) return;
      if (r.status !== 200) { host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      var pv = r.body.previews || {};
      host.innerHTML = Object.keys(pv).map(function (t) { var p = pv[t]; return "<h5><code>" + esc(p.file) + "</code>" + (p.exists ? "" : ' <span class="muted">(new file)</span>') + "</h5>" + (p.moves_from ? '<p class="muted">and takes SM\'s block out of <code>' + esc(p.moves_from) + "</code>, which Codex never reads (a backup stays beside it)</p>" : "") + diffHtml(p.before, p.after); }).join("") +
        '<div class="as-acts"><button type="button" class="btn-sm ag-start" onclick="AgentSetup.writeContext()">Write (with backups)</button></div>';
    });
  }
  function writeContext() {
    api("POST", "/api/agent/setup/context", ctxBody(true)).then(function (r) {
      if (r.status !== 200) { alertErr(r.body.error, "#as-ctx"); return; }
      load();
    });
  }
  function test(k) { return confirmBy(k); }

  function init() {
    var root = document.getElementById("as-body");
    // The attribute rides htmx's history snapshot, so after a browser Back it
    // says "mounted" on a node this script never saw: every later load() then
    // rendered into the DETACHED old root and the visible page never moved.
    // Mounted means S.root IS the visible node.
    if (!root || (root === S.root && root.getAttribute("data-as-mounted"))) return;
    root.setAttribute("data-as-mounted", "1");
    S.root = root;
    load();
  }
  // D-8: a Setup page open in another window re-reads when it regains focus
  // (the project or chip may have changed there)
  window.addEventListener("focus", function () {
    if (S.root && document.body && document.body.contains(S.root)) load();
  });
  document.addEventListener("htmx:afterSwap", function () { init(); });
  document.addEventListener("htmx:historyRestore", function () { init(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  return { init: init, load: load, preview: preview, connect: connect, cancel: cancel, disconnect: disconnect, journal: journal,
           loadContext: loadContext, answer: answer, previewContext: previewContext, writeContext: writeContext,
           test: test, confirmBy: confirmBy, dryRun: dryRun, calibPicked: calibPicked, useProject: useProject, useLegacy: useLegacy, browseCalib: browseCalib, browseJournal: browseJournal, saveLimits: saveLimits, diffLines: diffLines, _state: S };
})();
