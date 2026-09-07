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
  function actorName() { try { return localStorage.getItem("quam_actor_name") || ""; } catch (e) { return ""; } }
  function api(method, path, body) {
    var h = { "Accept": "application/json" };
    if (body !== undefined) h["Content-Type"] = "application/json";
    var who = actorName();
    if (who) h["X-SM-Actor"] = who;
    return fetch(path, { method: method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; }); });
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

  function render() {
    var d = S.data, root = S.root;
    if (!d || !root) return;
    var clis = d.clis || {};
    var parts = [];
    // 1. the agent CLIs
    var cliRows = ["claude", "codex"].map(function (k) {
      var c = clis[k] || {};
      return "<li><code>" + k + "</code>: " + (c.found ? "installed · " + esc(c.version || "") : '<span class="ag-err">not found on PATH</span> — install it, then log in once in a terminal (<code>' + k + "</code>)") + "</li>";
    }).join("");
    parts.push(sec("clis", "1. Agent CLIs", !!(clis.claude && clis.claude.found) || !!(clis.codex && clis.codex.found),
      "<ul>" + cliRows + '</ul><p class="muted">Login is done in the terminal on this PC\'s own account; SM never sees a key.</p>'));
    // 2. connect
    ["claude", "codex"].forEach(function (k) {
      if (!(clis[k] && clis[k].found)) return;
      var c = k === "claude" ? d.claude : d.codex;
      var isDone = k === "claude" ? (c.mcp && c.hooks) : c.mcp;
      var lines = [];
      lines.push("<p>" + (c.mcp ? done() + " SM registered as an MCP server" : "SM is not registered as an MCP server") + " in <code>" + esc(k === "claude" ? c.json : c.config) + "</code></p>");
      if (k === "claude") {
        lines.push("<p>" + (c.hooks ? done() + " the live-strip hook is registered" : "the hook (what the pill and the Calibration log follow when you use the terminal) is not registered") + " in <code>" + esc(c.settings) + "</code></p>");
        lines.push('<p class="muted">hook line: <code>' + esc(d.hook_command || "") + "</code></p>");
        if (d.calibrations_folder) lines.push("<p>" + (c.allow ? done() + " allow rules in the calibrations folder" : "allow rules (no permission prompt for SM's tools and <code>python</code>) not yet in <code>" + esc(d.calibrations_folder) + "\\.claude\\settings.local.json</code>") + "</p>");
      }
      lines.push('<div class="as-acts"><button type="button" class="btn-sm" onclick="AgentSetup.preview(\'' + k + '\')">Preview what SM would write</button> ' +
        (isDone ? '<button type="button" class="btn-sm" onclick="AgentSetup.disconnect(\'' + k + '\')">Disconnect (remove SM\'s entries)</button>' : "") + "</div>");
      lines.push('<div id="as-prev-' + k + '"></div>');
      parts.push(sec("connect-" + k, "2. Connect " + k + " to SM", isDone, lines.join("")));
    });
    // 3. run environment
    parts.push(sec("env", "3. Run environment", !!d.calibrations_folder,
      "<p>calibrations folder: " + (d.calibrations_folder ? "<code>" + esc(d.calibrations_folder) + "</code>" : '<span class="ag-err">not set</span>') +
      ' <span class="muted">(Experiment Runner settings hold it: env, calibrations folder, Dry run, timeout — <a href="/scheduler" hx-get="/scheduler" hx-target="#table-pane" hx-push-url="true">open</a>)</span></p>'));
    // 3b. hardware -- dry run. The value is the Runner's global_simulate as the
    // server read it; OFF wears ● (runs touch the OPX) and the card never folds.
    var dry = d.global_simulate !== false;
    parts.push(sec("dryrun", "3b. Hardware — Dry run", dry,
      '<label class="ag-observer"><input type="checkbox" id="as-dryrun-box"' + (dry ? " checked" : "") + ' onchange="AgentSetup.dryRun(this)"> Dry run (<code>simulate=True</code>) — no hardware</label>' +
      '<p class="muted">ON: the agent\'s runs are simulated and their values are never written to the chip. OFF: runs touch the OPX.</p>' +
      '<div id="as-dryrun-msg"></div>', true));
    // 4. journal
    var j = d.journal || {};
    parts.push(sec("journal", "4. Journal folder", !!j.configured,
      "<p>" + (j.configured ? done() + " " : "") + "root: <code>" + esc(j.root) + "</code>" + (j.configured ? "" : ' <span class="muted">(the instance folder — a fallback; choose the lab\'s own folder)</span>') + "</p>" +
      '<div class="as-acts"><input id="as-jroot" type="text" value="' + esc(j.configured ? j.root : (j.suggested || "")) + '" placeholder="D:\\data\\<project>\\journal" style="min-width:28rem"> ' +
      '<label class="ag-observer"><input type="checkbox" id="as-jsays"' + (j.claude_says ? " checked" : "") + '> also record what the agent SAYS at the end of a turn</label> ' +
      '<button type="button" class="btn-sm" onclick="AgentSetup.journal()">Use this folder</button></div>' +
      '<p class="muted">Suggested: beside the project data folder (shared by everyone who uses this PC account; survives a reinstall). Obsidian: embed with <code>![[&lt;chip&gt;/&lt;date&gt;]]</code>; never type into SM\'s file directly.</p>'));
    // 5. lab context
    var ctxDone = d.context && Object.keys(d.context).length > 0;
    parts.push(sec("context", "5. Lab context (what the agent must know about this device)", !!ctxDone,
      '<div id="as-ctx">' + (d.calibrations_folder ? '<button type="button" class="btn-sm" onclick="AgentSetup.loadContext()">Show the questions</button>' : '<p class="muted">needs the calibrations folder first</p>') + "</div>" +
      (ctxDone ? '<p class="muted">written: ' + Object.keys(d.context).map(function (k) { return esc(k) + " → <code>" + esc(d.context[k]) + "</code>"; }).join(", ") + "</p>" : "")));
    // 6. test
    var tested = (d.record && d.record.tested) || {};
    parts.push(sec("test", "6. Test", !!(tested.claude && tested.claude.ok) || !!(tested.codex && tested.codex.ok),
      '<p class="muted">A real read-only question through the CLI: the time it took and the answer, verbatim.</p>' +
      '<div class="as-acts">' + ["claude", "codex"].filter(function (k) { return clis[k] && clis[k].found; }).map(function (k) { return '<button type="button" class="btn-sm" onclick="AgentSetup.test(\'' + k + '\')">Test ' + k + "</button>"; }).join(" ") + "</div>" +
      '<div id="as-test">' + (S.lastTest || "") + "</div>"));
    root.innerHTML = parts.join("");
    if (window.htmx) { try { window.htmx.process(root); } catch (e) { /* ignore */ } }
  }

  function load() {
    return api("GET", "/api/agent/setup").then(function (r) { if (r.status === 200) { S.data = r.body; render(); } });
  }

  function preview(k) {
    api("POST", "/api/agent/setup/connect", { backend: k }).then(function (r) {
      var host = document.getElementById("as-prev-" + k);
      if (!host) return;
      if (r.status !== 200) { host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      var pv = r.body.previews || {};
      var html = Object.keys(pv).map(function (name) {
        var p = pv[name];
        return "<h5>" + esc(name) + " → <code>" + esc(p.file) + "</code>" + (p.exists ? "" : ' <span class="muted">(new file)</span>') + "</h5>" + diffHtml(p.before, p.after);
      }).join("");
      html += '<div class="as-acts"><button type="button" class="btn-sm ag-start" onclick="AgentSetup.connect(\'' + k + '\')">Write these (with backups)</button></div>';
      host.innerHTML = html;
    });
  }
  function connect(k) {
    api("POST", "/api/agent/setup/connect", { backend: k, apply: true }).then(function (r) {
      var host = document.getElementById("as-prev-" + k);
      if (r.status !== 200) { if (host) host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      var w = r.body.writes || {};
      if (host) host.innerHTML = "<p>written: " + Object.keys(w).map(function (n) { return esc(n) + (w[n].backup ? " (backup <code>" + esc(w[n].backup) + "</code>)" : ""); }).join(", ") + "</p>";
      load();
    });
  }
  function disconnect(k) {
    if (window.confirm && !window.confirm("Remove SM's entries from " + k + "'s configuration? (backups are kept)")) return;
    api("POST", "/api/agent/setup/disconnect", { backend: k }).then(function () { load(); });
  }
  function journal() {
    var root = (document.getElementById("as-jroot") || {}).value || "";
    var says = !!(document.getElementById("as-jsays") || {}).checked;
    api("POST", "/api/agent/setup/journal", { root: root, claude_says: says }).then(function (r) {
      if (r.status !== 200) { alertErr(r.body.error); return; }
      load();
    });
  }
  function dryRun(el) {
    // POST the one key; the reply is shown inline. On a refusal (409 while a
    // queue runs, or anything else) the server's error is shown VERBATIM and
    // the box goes back to what is persisted (re-read, not assumed).
    var want = !!el.checked, msg = document.getElementById("as-dryrun-msg");
    function mark(v) {
      // the card's summary marker follows the SAVED value (✓ ON / ● OFF) without a re-render
      var m = document.querySelector("#as-dryrun > summary .as-done, #as-dryrun > summary .as-todo");
      if (m) m.outerHTML = v ? done() : '<span class="as-todo">●</span>';
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
  function alertErr(msg) { var el = document.getElementById("as-body"); var p = document.createElement("p"); p.className = "ag-err"; p.textContent = msg || "failed"; el.insertBefore(p, el.firstChild); setTimeout(function () { p.remove(); }, 6000); }
  function loadContext() {
    api("GET", "/api/agent/setup/context").then(function (r) {
      var host = document.getElementById("as-ctx");
      if (!host) return;
      if (r.status !== 200) { host.innerHTML = '<p class="ag-err">' + esc(r.body.error || "failed") + "</p>"; return; }
      S.context = r.body;
      var f = r.body.facts || {};
      var html = ['<p class="muted">SM sees: ' + f.n_qubits + " qubits, " + f.n_pairs + " pairs; bias sources " + esc(JSON.stringify(f.bias_modes || {})) + (f.nodes && f.nodes.length ? "; " + f.nodes.length + " node files" : "") + ". It cannot see what follows — please confirm.</p>"];
      (r.body.questions || []).forEach(function (q) {
        S.answers[q.id] = S.answers[q.id] !== undefined ? S.answers[q.id] : q.detected;
        html.push('<div class="as-q"><label>' + esc(q.question) + (q.why ? ' <span class="muted" title="' + esc(q.why) + '">(why?)</span>' : "") + "</label>");
        if (q.kind === "choice") {
          html.push('<select data-q="' + esc(q.id) + '" onchange="AgentSetup.answer(this)">' + q.options.map(function (o) { return '<option value="' + esc(o) + '"' + (o === S.answers[q.id] ? " selected" : "") + ">" + esc(o) + (o === q.detected ? " (detected)" : "") + "</option>"; }).join("") + "</select>");
        } else {
          html.push('<textarea data-q="' + esc(q.id) + '" rows="3" onchange="AgentSetup.answer(this)">' + esc(S.answers[q.id] || "") + "</textarea>");
        }
        html.push("</div>");
      });
      html.push('<div class="as-acts"><label class="ag-observer"><input type="checkbox" id="as-ctx-local" checked> write to the .local.md files (not shared through git)</label> ' +
        '<label class="ag-observer"><input type="checkbox" id="as-ctx-claude" checked> CLAUDE</label> <label class="ag-observer"><input type="checkbox" id="as-ctx-codex" checked> AGENTS</label> ' +
        '<button type="button" class="btn-sm" onclick="AgentSetup.previewContext()">Preview the block</button></div><div id="as-ctx-prev"></div>');
      host.innerHTML = html.join("");
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
      host.innerHTML = Object.keys(pv).map(function (t) { var p = pv[t]; return "<h5><code>" + esc(p.file) + "</code>" + (p.exists ? "" : ' <span class="muted">(new file)</span>') + "</h5>" + diffHtml(p.before, p.after); }).join("") +
        '<div class="as-acts"><button type="button" class="btn-sm ag-start" onclick="AgentSetup.writeContext()">Write (with backups)</button></div>';
    });
  }
  function writeContext() {
    api("POST", "/api/agent/setup/context", ctxBody(true)).then(function (r) {
      if (r.status !== 200) { alertErr(r.body.error); return; }
      load();
    });
  }
  function test(k) {
    var host = document.getElementById("as-test");
    if (host) host.innerHTML = '<p class="muted">asking ' + esc(k) + " one read-only question…</p>";
    api("POST", "/api/agent/setup/test", { backend: k }).then(function (r) {
      if (!host) return;
      var b = r.body || {};
      if (r.status !== 200) { host.innerHTML = '<p class="ag-err">' + esc(b.error || "failed") + "</p>"; return; }
      S.lastTest = "<p><strong>" + esc(k) + "</strong> answered in <strong>" + esc(b.elapsed_s) + " s</strong>" + (b.failed ? ' — <span class="ag-err">failed: ' + esc(b.error) + "</span>" : "") + (b.tools && b.tools.length ? ' <span class="muted">(tools: ' + esc(b.tools.join(", ")) + ")</span>" : "") + "</p>" +
        (b.answer ? '<blockquote class="as-answer">' + esc(b.answer) + "</blockquote>" : '<p class="ag-err">' + esc(b.error || "no answer") + "</p>");
      host.innerHTML = S.lastTest;
      load();                                   // the section's ✓ follows the record; the result stays
    });
  }

  function init() {
    var root = document.getElementById("as-body");
    if (!root || root.getAttribute("data-as-mounted")) return;
    root.setAttribute("data-as-mounted", "1");
    S.root = root;
    load();
  }
  document.addEventListener("htmx:afterSwap", function () { init(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  return { init: init, load: load, preview: preview, connect: connect, disconnect: disconnect, journal: journal,
           loadContext: loadContext, answer: answer, previewContext: previewContext, writeContext: writeContext,
           test: test, dryRun: dryRun, diffLines: diffLines, _state: S };
})();
