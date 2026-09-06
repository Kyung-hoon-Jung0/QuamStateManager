/* Agent home + the floating Agent panel (docs/173 S6).
 *
 * One renderer, two mounts: the landing's Agent home (#agent-home, when a chip
 * is open) and the body-level floating panel (#agent-popover, any page). Both
 * read the SAME feed -- GET /api/agent/chat/cards -- which is SM's own record
 * (chat events on disk, plans, runs, approvals), never the model's claim.
 *
 * Rule 0 is visible here: a sentence makes a plan CARD; only the card's
 * [Start] (a person's click) arms the session and tells the agent to go.
 * Bundle 'agent' (base.html); core deps: AgentPill (describe), FloatPanel.
 *
 * Review round 1 (docs/173 §11, R2): cards sit in TIME order whatever order
 * they arrive in; a card holding the focus is never re-rendered under the
 * person's fingers; an unchanged card is not re-rendered at all; the feed
 * autoscrolls only when the reader is already at the bottom; Enter sends and
 * Shift+Enter breaks a line; a refused /run keeps its text; a server that
 * cannot be reached says so instead of failing silently.
 */
window.AgentPanel = (function () {
  "use strict";
  var S = {
    after: 0, seq: -1, chip: null, session: null, now: null, backends: null, defaultBackend: "claude",
    plans: {}, runs: {}, approvals: {}, mounts: [], timer: null, inflight: false, observer: false,
    seenCards: {}, lastPoll: 0, unreachable: false
  };
  var PRESETS = [
    ["1Q bringup", "1Q bringup on <targets>: resonator spectroscopy -> qubit spectroscopy -> power rabi -> ramsey. Propose the plan with plan_propose (one step per node and target) and wait for Start."],
    ["readout tuneup", "Readout tuneup on <targets>: resonator spectroscopy vs power -> readout frequency/amplitude optimization -> IQ blobs. Propose the plan with plan_propose and wait for Start."],
    ["CZ tuneup", "CZ tuneup on <pair>: coupler flux -> CZ chevron -> CZ phase calibration -> 2Q RB. Propose the plan with plan_propose and wait for Start."]
  ];
  var UNREACHABLE = "SM cannot be reached (is the window still open?)";

  // ------------------------------------------------------------ helpers
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtClock(ts) {
    // review R2-minor: a card from another day says which day
    if (!ts) return "";
    var d = new Date(ts * 1000), now = new Date();
    var hm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) return hm;
    return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + hm;
  }
  function fmtAgo(ts) {
    if (!ts) return "";
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    return Math.round(s / 360) / 10 + "h ago";
  }
  function fmtNum(v) {
    if (typeof v === "number") {
      if (Number.isInteger(v)) return String(v);
      var a = Math.abs(v);
      if (a >= 1e9) return (v / 1e9).toPrecision(6).replace(/\.?0+$/, "") + " G";
      if (a >= 1e6) return (v / 1e6).toPrecision(6).replace(/\.?0+$/, "") + " M";
      if (a >= 1e3) return (v / 1e3).toPrecision(6).replace(/\.?0+$/, "") + " k";
      if (a < 1e-3 && a > 0) return v.toExponential(3);
      return String(+v.toPrecision(7));
    }
    if (v === null) return "null";
    if (typeof v === "object") return JSON.stringify(v).slice(0, 60);
    return String(v);
  }
  function actorName() { try { return localStorage.getItem("quam_actor_name") || ""; } catch (e) { return ""; } }
  function api(method, path, body) {
    // review R2-3: a rejected fetch (SM gone, network) answers like a refused
    // request -- status 0 and a message -- so no caller ever throws or hangs
    var h = { "Accept": "application/json" };
    if (body !== undefined) h["Content-Type"] = "application/json";
    var who = actorName();
    if (who) h["X-SM-Actor"] = who;
    var p;
    try {
      p = fetch(path, { method: method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
    } catch (e) { p = Promise.reject(e); }
    return p.then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { setUnreachable(false); return { status: r.status, body: j || {} }; });
    }, function () { setUnreachable(true); return { status: 0, body: { error: UNREACHABLE, unreachable: true } }; });
  }
  function errText(r, fallback) { return (r && r.body && (r.body.error || r.body.message || r.body.how)) || fallback; }
  function setUnreachable(on) {
    if (S.unreachable === !!on) return;
    S.unreachable = !!on;
    S.mounts.forEach(function (m) { renderNow(m); });
  }
  function toast(msg, level) {
    if (window.showToast) { try { window.showToast(msg, level || "info"); return; } catch (e) { /* fall through */ } }
    var el = document.getElementById("ag-toast");
    if (el) { el.textContent = msg; el.hidden = false; setTimeout(function () { el.hidden = true; }, 5000); }
  }
  function observer() { try { return localStorage.getItem("quam_agent_observer") === "1"; } catch (e) { return false; } }
  function setObserver(on) { try { localStorage.setItem("quam_agent_observer", on ? "1" : "0"); } catch (e) { /* ignore */ } S.observer = !!on; renderAll(true); }
  function pathLabel(p) { var parts = String(p || "").split("."); return parts[parts.length - 1]; }
  function runLink(rid) { return rid ? '<a class="ag-run" href="/dataset/by-run/' + rid + '" hx-get="/dataset/by-run/' + rid + '" hx-target="#table-pane" hx-push-url="true">#' + rid + "</a>" : ""; }
  function simBadge(on) { return on ? ' <span class="ag-sim" title="Dry run was ON in the Experiment Runner settings: the node ran against the simulator; its values are never applied to the chip">simulated</span>' : ""; }

  // ------------------------------------------------------------- cards
  function cardsHost(m) { return m.root.querySelector(".ag-cards"); }
  function cardFor(m, kind, id, ts) {
    // review R2-14: a card takes its place by TIME, not by arrival -- the
    // live objects (plans, runs, approvals) come with every poll, the chat
    // cards only once, so appending would put an old plan under a new answer
    var host = cardsHost(m);
    if (!host) return null;
    var el = host.querySelector('[data-card="' + kind + ":" + id + '"]');
    if (!el) {
      el = document.createElement("article");
      el.className = "ag-card ag-" + kind;
      el.setAttribute("data-card", kind + ":" + id);
      var t = Number(ts) || 0;
      el.setAttribute("data-ts", String(t));
      var before = null;
      var kids = host.children;
      for (var i = 0; i < kids.length; i++) {
        var kt = Number(kids[i].getAttribute("data-ts")) || 0;
        if (kt > t) { before = kids[i]; break; }
      }
      if (before) host.insertBefore(el, before); else host.appendChild(el);
    }
    return el;
  }
  function setHtml(el, html, force) {
    // review R2-1: an unchanged card is left alone (no flicker, no lost
    // <details> state); a card the person is typing in is never replaced
    if (!force && el.__agHtml === html) return false;
    if (!force && el.contains(document.activeElement) && document.activeElement !== document.body) {
      el.__agStale = html;                              // re-rendered on the next poll after the focus leaves
      return false;
    }
    el.innerHTML = html;
    el.__agHtml = html;
    el.__agStale = null;
    return true;
  }
  function renderChatCard(m, c) {
    var el = cardFor(m, c.kind, c.n, c.ts);
    if (!el) return;
    var when = '<span class="ag-when">' + esc(fmtClock(c.ts)) + "</span>";
    var html;
    if (c.kind === "user") {
      html = '<div class="ag-bubble ag-user"><span class="ag-who">' + esc(c.who || "you") + "</span>" + when + '<p class="ag-user-text">' + esc(c.text) + "</p></div>";
    } else if (c.kind === "answer") {
      html = '<div class="ag-bubble ag-answer"><span class="ag-who">' + esc(c.backend ? "by_" + c.backend : "agent") + "</span>" + when +
        '<div class="ag-md">' + (c.html || "<p>" + esc(c.text) + "</p>") + "</div></div>";
    } else if (c.kind === "tool") {
      var t = String(c.tool || "").replace(/^mcp__sm__/, "sm.");
      var sum = String(c.summary || "").trim();
      if (sum === "{}" || sum === "[]") sum = "";                 // review R2-minor: an empty argument list says nothing
      html = '<div class="ag-tool' + (c.failed ? " ag-failed" : "") + '"><code>' + esc(t) + "</code>" + (sum ? ' <span class="muted">' + esc(sum.slice(0, 140)) + "</span>" : "") +
        (c.failed ? ' <span class="ag-err">' + esc((c.error || "failed").slice(0, 160)) + "</span>" : "") + when + "</div>";
    } else if (c.kind === "error") {
      html = '<div class="ag-tool ag-failed"><strong>agent exited</strong> <span class="ag-err">' + esc((c.error || "").slice(0, 200)) + "</span>" + when + "</div>";
    } else if (c.kind === "limited") {
      html = '<div class="ag-tool ag-limited"><strong>usage limit</strong> <span class="muted">' + esc(c.text || "") + "</span>" + when + "</div>";
    } else if (c.kind === "stop") {
      html = '<div class="ag-tool ag-stopped"><strong>Stopped</strong> <span class="muted">' + esc(c.text || "") + "</span>" + when + "</div>";
    } else {
      html = '<div class="ag-tool muted">' + esc(c.kind) + " " + esc(c.text || c.summary || "") + when + "</div>";
    }
    setHtml(el, html);
  }

  var STEP_GLYPH = { pending: "·", running: "▶", done: "✓", failed: "✗", skipped: "skip", cancelled: "—", interrupted: "⏹" };
  function stepBadge(s) {
    var st = s.status || "pending";
    var txt = STEP_GLYPH[st] || st;
    var title = st + (st === "interrupted" ? " (SM restarted while it ran)" : "");
    return '<span class="ag-step-st ag-st-' + esc(st) + '" title="' + esc(title) + '">' + txt + "</span>";
  }
  var PLAN_STATUS_TEXT = { draft: "draft", running: "running", stopping: "stopping — finishes the current run", done: "done", failed: "failed",
                           stopped: "stopped", cancelled: "cancelled", skipped: "skipped — nothing ran" };
  function renderPlan(m, p, force) {
    var el = cardFor(m, "plan", p.id, p.created);
    if (!el) return;
    var c = p.counts || {};
    var may = p.may_change || [];
    var rows = (p.steps || []).map(function (s) {
      return "<tr>" + "<td>" + stepBadge(s) + "</td><td><code>" + esc(s.node) + "</code>" + simBadge(s.simulated) + "</td><td>" + esc((s.targets || []).join(" ")) + "</td>" +
        "<td>" + (s.run_id ? runLink(s.run_id) : "") + (s.classification && s.classification !== "ok" ? ' <span class="ag-err">' + esc(s.classification) + "</span>" : "") + "</td>" +
        "<td>" + (s.n_writes ? s.n_writes + (s.applied ? " applied" : (s.approval ? " waiting" : "")) : "") + "</td>" +
        "<td class=\"muted\">" + esc(s.why || "") + "</td></tr>";
    }).join("");
    var mayHtml = may.length
      ? "<ul class=\"ag-may\">" + may.map(function (x) {
          return "<li><strong title=\"" + esc(x.path) + "\">" + esc(x.target ? x.target + " · " : "") + esc(x.label || pathLabel(x.path)) + "</strong>" +
            ' <span class="muted ag-may-path">' + esc(x.path) + "</span>" +
            (x.now !== undefined && x.now !== null ? ' <span class="muted">now ' + esc(fmtNum(x.now)) + "</span>" : ' <span class="muted">now: not set</span>') +
            (x.note ? ' <span class="muted">(' + esc(x.note) + ")</span>" : "") +
            (x.last ? ' <span class="muted">· last ' + esc(x.last.actor || "?") + " " + esc(x.last.when || "") + "</span>" : "") + "</li>";
        }).join("") + "</ul>"
      : '<p class="muted ag-may-none">no earlier run of these nodes on these targets -- what changes is not known in advance</p>';
    var origin = p.source === "run_cmd" ? "/run typed by " + (p.created_by || "a person") : "proposed by " + (p.created_by || "the agent");
    var head = '<div class="ag-plan-head"><strong>' + esc(p.title) + '</strong> <span class="ag-plan-st ag-st-' + esc(p.status) + '">' + esc(PLAN_STATUS_TEXT[p.status] || p.status) + "</span>" +
      ' <span class="muted">' + esc(origin) + " · " + esc(fmtClock(p.created)) + "</span></div>";
    var prog = "";
    if (p.status !== "draft") {
      prog = '<div class="ag-plan-prog">' + (c.done || 0) + " / " + (c.total || 0) + " done" + (c.failed ? " · ✗ " + c.failed : "") + (c.skipped ? " · skipped " + c.skipped : "") +
        (c.waiting ? " · waiting " + c.waiting : "") + (p.ended ? " · " + (p.status === "done" ? "finished " : "ended ") + fmtClock(p.ended) + (p.ended_by ? " by " + esc(p.ended_by) : "") : "") + "</div>";
    }
    var mode = p.mode || (S.now && S.now.mode) || "ask-writes";
    var modeSel = p.status === "draft"
      ? '<label class="ag-mode">mode <select' + (S.observer ? " disabled" : "") + ' onchange="AgentPanel.setPlanMode(\'' + esc(p.id) + '\', this.value)">' +
        ["auto", "ask-writes", "ask-all"].map(function (x) { return '<option value="' + x + '"' + (x === mode ? " selected" : "") + ">" + x + "</option>"; }).join("") + "</select></label>"
      : '<span class="muted">mode ' + esc(mode) + "</span>";
    var acts = "";
    if (!S.observer) {
      if (p.status === "draft") {
        acts = '<button type="button" class="btn-sm ag-start" onclick="AgentPanel.startPlan(\'' + esc(p.id) + '\')">Start — ' + (may.length ? may.length + " value(s) may change" : "values may change") + "</button> " +
          '<button type="button" class="btn-sm ag-cancel" onclick="AgentPanel.cancelPlan(\'' + esc(p.id) + '\')">Cancel</button>';
      } else if (p.status === "running") {
        acts = '<button type="button" class="btn-sm ag-stop" onclick="AgentPanel.stop(\'after_run\')">Stop after this run</button> ' +
          '<button type="button" class="btn-sm ag-stop ag-stop-now" onclick="AgentPanel.stop(\'now\')">Stop now</button>';
      } else if (p.status === "stopping") {
        acts = '<button type="button" class="btn-sm ag-stop ag-stop-now" onclick="AgentPanel.stop(\'now\')">Stop now</button>';
      }
    }
    if (p.pre_ts) {
      acts += ' <a class="btn-sm ag-revert" href="/state-history" hx-get="/state-history" hx-target="#table-pane" hx-push-url="true" title="the chip as it was right before this plan started (State History → restore)">state before this plan</a>';
    }
    var html = head + '<div class="ag-tbl"><table class="ag-steps"><thead><tr><th></th><th>node</th><th>targets</th><th>run</th><th>writes</th><th>why</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      '<details class="ag-may-wrap"' + (p.status === "draft" ? " open" : "") + "><summary>values that may change</summary>" + mayHtml + "</details>" +
      prog + '<div class="ag-plan-acts">' + modeSel + " " + acts + "</div>";
    setHtml(el, html, force);
  }

  function renderRun(m, r, force) {
    var el = cardFor(m, "run", r.key, r.since);
    if (!el) return;
    var res = r.result || {};
    var st = r.status;
    var line = "<strong><code>" + esc(r.node) + "</code></strong> " + esc((r.targets || []).join(" ")) +
      ' <span class="ag-step-st ag-st-' + esc(st === "ended" ? (res.status || "ended") : st) + '">' + esc(st === "ended" ? (res.status || "ended") : st) + "</span>" +
      simBadge(res.simulated || r.simulated) +
      (res.classification && res.classification !== "ok" ? ' <span class="ag-err">' + esc(res.classification) + "</span>" : "") +
      (res.run_id ? " " + runLink(res.run_id) : "") + ' <span class="ag-when">' + esc(fmtClock(r.since)) + "</span>";
    var writes = "";
    if (res.writes && res.writes.length) {
      writes = '<details class="ag-writes"><summary>' + res.writes.length + " write(s) " + (res.applied ? "applied to the chip" : (res.approval ? "waiting for approval" : "not staged")) + "</summary><div class=\"ag-tbl\"><table>" +
        res.writes.slice(0, 40).map(function (w) { return "<tr><td title=\"" + esc(w.path) + "\">" + esc(w.path) + "</td><td>" + esc(fmtNum(w.old)) + " → " + esc(fmtNum(w.new)) + "</td></tr>"; }).join("") +
        (res.writes.length > 40 ? "<tr><td colspan=2 class=muted>… " + (res.writes.length - 40) + " more</td></tr>" : "") + "</table></div></details>";
    }
    var err = res.error ? '<div class="ag-err">' + esc(String(res.error).slice(0, 300)) + "</div>" : "";
    var how = r.how ? '<div class="muted ag-how">' + esc(r.how) + "</div>" : "";
    var log = res.log_tail ? '<details class="ag-log"><summary>log tail</summary><pre>' + esc(res.log_tail.slice(-1500)) + "</pre></details>" : "";
    setHtml(el, '<div class="ag-run-line">' + line + "</div>" + err + writes + how + log, force);
  }

  function renderApproval(m, a, force) {
    var el = cardFor(m, "approval", a.id, a.created);
    if (!el) return;
    var isRun = a.kind === "run";
    var rows = (a.writes || []).map(function (w, i) {
      return "<tr><td title=\"" + esc(w.path) + "\">" + esc(w.path) + "</td><td>" + esc(fmtNum(w.old)) + "</td><td>" +
        (S.observer ? esc(fmtNum(w.new)) : '<input class="ag-ap-new" data-i="' + i + '" value="' + esc(typeof w.new === "object" ? JSON.stringify(w.new) : w.new) + '">') + "</td></tr>";
    }).join("");
    // review R2-11: a RUN request is allowed, not written
    var acts = S.observer ? '<span class="muted">observing</span>' :
      '<button type="button" class="btn-sm ag-approve" onclick="AgentPanel.approve(\'' + esc(a.id) + '\', this)">' + (isRun ? "Allow run" : "Write to chip") + "</button> " +
      '<button type="button" class="btn-sm ag-reject" onclick="AgentPanel.reject(\'' + esc(a.id) + '\')">Reject</button>';
    var html = '<div class="ag-ap-head"><strong>' + (isRun ? "run request" : "approval") + "</strong> <code>" + esc(a.node || "") + "</code> " + esc((a.targets || []).join(" ")) +
      (a.run_id ? " " + runLink(a.run_id) : "") + ' <span class="muted">' + esc(a.why_held || "") + " · " + esc(fmtClock(a.created)) + "</span></div>" +
      (isRun ? '<p class="muted">the agent asks to RUN this node on these targets (mode ask-all); nothing runs before Allow</p>' :
        '<div class="ag-tbl"><table class="ag-ap-rows"><thead><tr><th>value</th><th>now</th><th>proposed (editable)</th></tr></thead><tbody>' + rows + "</tbody></table></div>") +
      (a.reason ? '<p class="ag-because">because: ' + esc(a.reason) + "</p>" : "") + '<div class="ag-ap-acts">' + acts + "</div>";
    setHtml(el, html, force);
  }

  // ---------------------------------------------------------- the "now"
  function renderNow(m) {
    var host = m.root.querySelector(".ag-now");
    if (!host) return;
    var d = S.now || {};
    var v = window.AgentPill ? window.AgentPill.describe(d) : { state: d.state || "idle", text: "Agent" };
    var s = S.session || {};
    var file = s.file || null;
    var live = s.session || null;
    var alive = !!(live && live.alive);
    var armed = !!(file && file.armed);
    var lines = [];
    if (S.unreachable) lines.push('<div class="ag-now-line ag-err ag-unreachable">' + esc(UNREACHABLE) + "</div>");
    lines.push('<div class="ag-now-state ag-' + esc(v.state) + '"><span class="agent-pill-dot"></span> ' + esc(v.text) + "</div>");
    if (file && file.owner) {
      lines.push('<div class="ag-now-line">' + esc(file.backend || "") + " session · " + esc(file.owner) + (file.until ? " → " + esc(fmtClock(file.until)) : "") + (file.stopped ? ' · <span class="ag-err">stopped</span>' : "") +
        (armed ? ' · <span class="ag-armed" title="a person pressed Arm: the agent may start hardware runs">armed</span>' : ' · <span class="muted">not armed</span>') + "</div>");
    } else {
      lines.push('<div class="ag-now-line muted">no agent session on this chip</div>');
    }
    if (d.running) lines.push('<div class="ag-now-line">▶ <code>' + esc(d.running.node || d.running.tool || "") + "</code> " + esc(fmtAgo(d.running.since)) + (d.running.typical_s ? ' <span class="muted">usually ~' + Math.round(d.running.typical_s / 60) + "m</span>" : "") + "</div>");
    lines.push('<div class="ag-now-line">today: ' + (d.events_today || 0) + " events" + (d.failures_today ? ' · <span class="ag-err">✗ ' + d.failures_today + "</span>" : "") + (d.waiting ? ' · <strong>waiting ' + d.waiting + "</strong>" : "") + "</div>");
    if (d.human_ran) lines.push('<div class="ag-now-line">human ran <code>' + esc(d.human_ran.node || "") + "</code> " + esc(fmtAgo(d.human_ran.ts)) + "</div>");
    var acts = [];
    if (!S.observer) {
      if (file && file.owner && !armed) acts.push('<button type="button" class="btn-sm ag-arm" onclick="AgentPanel.arm()" title="rule 0: hardware starts only by this click">Arm</button>');
      if (armed) acts.push('<button type="button" class="btn-sm" onclick="AgentPanel.disarm()">Disarm</button>');
      if (alive || (file && file.owner && !file.stopped)) {
        acts.push('<button type="button" class="btn-sm ag-stop" onclick="AgentPanel.stop(\'after_run\')">Stop after this run</button>');
        acts.push('<button type="button" class="btn-sm ag-stop ag-stop-now" onclick="AgentPanel.stop(\'now\')">Stop now</button>');
      }
      if (alive) acts.push('<button type="button" class="btn-sm" onclick="AgentPanel.endSession()">End session</button>');
    }
    acts.push('<label class="ag-observer" title="observer: this window shows but never starts, stops or approves (an accident guard, not a permission)"><input type="checkbox" ' + (S.observer ? "checked" : "") + ' onchange="AgentPanel.setObserver(this.checked)"> observer' + (S.observer ? ' <span class="ag-observing">— observing</span>' : "") + "</label>");
    lines.push('<div class="ag-now-acts">' + acts.join(" ") + "</div>");
    lines.push('<div class="ag-now-line"><a href="/journal" hx-get="/journal" hx-target="#table-pane" hx-push-url="true">Calibration log →</a>' +
      ' · <a class="ag-setup-link" href="/agent/setup" hx-get="/agent/setup" hx-target="#table-pane" hx-push-url="true" title="connect Claude / Codex to SM, the journal folder, the lab context file">Setup →</a></div>');
    host.innerHTML = lines.join("");
  }

  function renderAll(force) {
    S.mounts.forEach(function (m) {
      Object.keys(S.plans).forEach(function (k) { renderPlan(m, S.plans[k], force); });
      Object.keys(S.runs).forEach(function (k) { renderRun(m, S.runs[k], force); });
      Object.keys(S.approvals).forEach(function (k) { renderApproval(m, S.approvals[k], force); });
      renderNow(m);
    });
  }
  function nearBottom(host) {
    // review R2-12: the feed follows new cards only when the reader is
    // already at the bottom; a person reading an old plan is not dragged down
    if (!host) return true;
    if (!host.__agScrolledOnce) return true;                      // the first render lands at the bottom
    return host.scrollHeight - host.scrollTop - host.clientHeight < 48;
  }

  // ------------------------------------------------------------ polling
  function absorb(d) {
    if (!d || !d.ok) return;
    S.chip = d.chip;
    S.session = { session: d.session, file: d.file };
    S.now = d.now;
    if (typeof d.agent_seq === "number") S.seq = d.agent_seq;
    var live = d.live || {};
    var stale = {};
    (live.approvals || []).forEach(function (a) { S.approvals[a.id] = a; });
    Object.keys(S.approvals).forEach(function (k) { if (!(live.approvals || []).some(function (a) { return a.id === k; })) stale[k] = 1; });
    Object.keys(stale).forEach(function (k) {
      delete S.approvals[k];
      S.mounts.forEach(function (m) { var el = m.root.querySelector('[data-card="approval:' + k + '"]'); if (el) el.remove(); });
    });
    (live.plans || []).forEach(function (p) { S.plans[p.id] = p; });
    (live.runs || []).forEach(function (r) { S.runs[r.key] = r; });
    var follow = S.mounts.map(function (m) { return nearBottom(cardsHost(m)); });
    S.mounts.forEach(function (m) {
      (d.cards || []).forEach(function (c) {
        if (c.n > S.after || !S.seenCards[m.id + ":" + c.n]) { renderChatCard(m, c); S.seenCards[m.id + ":" + c.n] = 1; }
      });
    });
    if (typeof d.last === "number" && d.last > S.after) S.after = d.last;
    renderAll();
    S.mounts.forEach(function (m, i) {
      var host = cardsHost(m);
      if (!host) return;
      if (m.autoscroll !== false && follow[i]) host.scrollTop = host.scrollHeight;
      host.__agScrolledOnce = true;
    });
    S.mounts.forEach(function (m) { var q = m.root.querySelector(".ag-qubits"); if (q && d.qubits != null) q.textContent = d.qubits + " qubits"; });
    if (window.htmx) S.mounts.forEach(function (m) { try { window.htmx.process(m.root); } catch (e) { /* ignore */ } });
    if (d.more) setTimeout(function () { poll(true); }, 0);   // review R2-13: the feed is capped from the front; keep draining
  }
  function poll(force) {
    if (!S.mounts.length) return Promise.resolve();
    if (S.inflight && !force) return Promise.resolve();
    S.inflight = true;
    S.lastPoll = Date.now();
    return api("GET", "/api/agent/chat/cards?after=" + S.after).then(function (r) {
      S.inflight = false;
      if (r.status === 200) absorb(r.body);
    }, function () { S.inflight = false; });
  }
  function schedule() {
    if (S.timer) clearInterval(S.timer);
    S.timer = setInterval(function () {
      if (!S.mounts.length) return;
      var busy = S.session && S.session.session && S.session.session.busy;
      var active = S.now && (S.now.state === "running" || S.now.state === "between");
      var gap = (busy || active) ? 4000 : 30000;
      if (S.unreachable) gap = 10000;
      if (Date.now() - S.lastPoll >= gap) poll();
    }, 1000);
  }
  document.addEventListener("sm:runs-changed", function (e) {
    var seq = e && e.detail && e.detail.agent_seq;
    if (typeof seq === "number" && seq === S.seq) return;
    poll();
  });
  document.addEventListener("focusout", function (e) {
    // a card that waited while the person typed in it catches up now
    var card = e && e.target && e.target.closest && e.target.closest(".ag-card");
    if (!card || !card.__agStale) return;
    setTimeout(function () {
      if (card.__agStale && !(card.contains(document.activeElement) && document.activeElement !== document.body)) setHtml(card, card.__agStale, true);
    }, 0);
  }, true);

  // ------------------------------------------------------------ actions
  function key(ev) {
    // review R2-5: Enter sends, Shift+Enter breaks a line (an IME composition is left alone)
    if (!ev || ev.key !== "Enter" || ev.shiftKey || ev.isComposing || ev.keyCode === 229) return true;
    ev.preventDefault();
    submit(ev);
    return false;
  }
  function submit(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    var m = S.mounts[0];
    var root = (ev && ev.target && ev.target.closest && ev.target.closest(".ag-root")) || (m && m.root);
    var ta = root && root.querySelector(".ag-input");
    if (!ta) return false;
    var text = ta.value.trim();
    if (!text) return false;
    var sel = root.querySelector(".ag-backend");
    var backend = (sel && sel.value) || S.defaultBackend;
    ta.disabled = true;
    // review R2-16: a refused line stays in the box for the person to fix
    var done = function (ok) { ta.disabled = false; if (ok) ta.value = ""; ta.focus(); poll(true); };
    if (text.indexOf("/run") === 0) {
      api("POST", "/api/agent/plans", { run_line: text }).then(function (r) {
        var ok = r.status === 200;
        if (!ok) toast(errText(r, "could not make the plan"), "error"); else toast("plan card ready — press Start when you mean it");
        done(ok);
      });
      return false;
    }
    var live = S.session && S.session.session;
    var p = (live && live.alive && !live.ended) ? api("POST", "/api/agent/chat/send", { text: text })
      : api("POST", "/api/agent/chat/start", { prompt: text, backend: backend });
    p.then(function (r) {
      var ok = r.status === 200;
      if (!ok) toast(errText(r, "the agent did not start"), "error");
      done(ok);
    });
    return false;
  }
  function preset(i, root) {
    var pr = PRESETS[i];
    if (!pr) return;
    var ta = root.querySelector(".ag-input");
    if (ta) { ta.value = pr[1]; ta.focus(); ta.setSelectionRange(pr[1].indexOf("<"), pr[1].indexOf(">") + 1); }
  }
  function startPlan(id) {
    if (S.observer) return;
    api("POST", "/api/agent/plans/" + id + "/start", {}).then(function (r) {
      if (r.status !== 200) toast(errText(r, "could not start"), "error"); else toast("started — the agent is running the plan");
      poll(true);
    });
  }
  function cancelPlan(id) {
    if (S.observer) return;
    api("POST", "/api/agent/plans/" + id + "/cancel", {}).then(function (r) { if (r.status !== 200) toast(errText(r, "not cancelled"), "error"); poll(true); });
  }
  function setPlanMode(id, mode) {
    if (S.observer) return;
    api("POST", "/api/agent/plans/" + id + "/mode", { mode: mode }).then(function (r) {
      if (r.status !== 200) toast(errText(r, "mode not set"), "error");
      poll(true);
    });
  }
  function approve(id, btn) {
    if (S.observer) return;
    var card = btn && btn.closest(".ag-card");
    var a = S.approvals[id] || {};
    var writes = null;
    if (card && a.writes) {
      writes = a.writes.map(function (w, i) {
        var inp = card.querySelector('.ag-ap-new[data-i="' + i + '"]');
        var v = w.new;
        if (inp) { var t = inp.value.trim(); try { v = JSON.parse(t); } catch (e) { v = t; } }
        return { path: w.path, old: w.old, new: v };
      });
    }
    if (btn) btn.disabled = true;
    api("POST", "/api/agent/approvals/" + id + "/approve", writes ? { writes: writes } : {}).then(function (r) {
      if (btn) btn.disabled = false;
      // review R2-2: "written" only when the door said applied; a refusal keeps the card and says why
      var applied = r.status === 200 && r.body && r.body.ok !== false;
      if (!applied) toast(errText(r, "not applied"), "error");
      else toast(a.kind === "run" ? "run allowed" : "written to the chip");
      poll(true);
    });
  }
  function reject(id) {
    if (S.observer) return;
    var note = window.prompt ? (window.prompt("Reject — a note for the journal (optional):") || "") : "";
    api("POST", "/api/agent/approvals/" + id + "/reject", { note: note }).then(function (r) { if (r.status !== 200) toast(errText(r, "not rejected"), "error"); poll(true); });
  }
  function stop(mode) {
    if (S.observer) return;
    if (mode === "now" && window.confirm && !window.confirm("Stop now: the agent process is killed and the running node is cancelled. The OPX finishes its current sequence; SM's chip writes are atomic; the current run folder may be incomplete. Continue?")) return;
    api("POST", "/api/agent/session/stop", { mode: mode }).then(function (r) {
      if (r.status !== 200) toast(errText(r, "no session"), "error");
      poll(true);
    });
  }
  function arm() { if (S.observer) return; api("POST", "/api/agent/session/arm", {}).then(function (r) { if (r.status !== 200) toast(errText(r, "not armed"), "error"); poll(true); }); }
  function disarm() { if (S.observer) return; api("POST", "/api/agent/session/disarm", {}).then(function (r) { if (r.status !== 200) toast(errText(r, "not disarmed"), "error"); poll(true); }); }
  function endSession() { if (S.observer) return; api("POST", "/api/agent/chat/end", {}).then(function (r) { if (r.status !== 200) toast(errText(r, "no session"), "error"); poll(true); }); }

  // -------------------------------------------------------------- mount
  function skeleton(compact) {
    return '<div class="ag-root' + (compact ? " ag-compact" : "") + '">' +
      '<div class="ag-left"><div class="ag-head"><strong class="ag-chip"></strong> <span class="ag-qubits muted"></span></div>' +
      '<div class="ag-cards" aria-live="polite"></div>' +
      '<form class="ag-form" onsubmit="return AgentPanel.submit(event)">' +
      '<textarea class="ag-input" rows="2" onkeydown="return AgentPanel.key(event)" placeholder="What do you want to know, or what should the agent do?   Enter sends, Shift+Enter breaks a line   (/run <node> <targets> k=v makes a plan card directly)"></textarea>' +
      '<div class="ag-form-row"><select class="ag-backend" title="which CLI drives"></select> <button type="submit" class="btn-sm ag-send">Send</button> ' +
      '<span class="ag-presets">' + PRESETS.map(function (p, i) { return '<button type="button" class="btn-sm ag-preset" onclick="AgentPanel.preset(' + i + ', this.closest(\'.ag-root\'))">' + esc(p[0]) + "</button>"; }).join(" ") + "</span>" +
      '<span class="muted ag-hint">a preset fills a draft; nothing starts before a plan card\'s Start</span></div></form></div>' +
      '<aside class="ag-now"></aside><div id="ag-toast" class="ag-toast" hidden></div></div>';
  }
  function mount(root, opts) {
    opts = opts || {};
    if (!root || root.getAttribute("data-ag-mounted")) return;
    root.setAttribute("data-ag-mounted", "1");
    root.innerHTML = skeleton(!!opts.compact);
    var m = { id: opts.id || ("m" + S.mounts.length), root: root.querySelector(".ag-root"), compact: !!opts.compact, autoscroll: true };
    S.mounts.push(m);
    S.observer = observer();
    api("GET", "/api/agent/chat/backends").then(function (r) {
      if (r.status !== 200) return;
      S.backends = r.body.backends || {};
      S.defaultBackend = r.body["default"] || "claude";
      var sel = m.root.querySelector(".ag-backend");
      if (sel) {
        sel.innerHTML = Object.keys(S.backends).map(function (k) {
          var b = S.backends[k];
          return '<option value="' + esc(k) + '"' + (k === S.defaultBackend ? " selected" : "") + (b.found ? "" : " disabled") + ">" + esc(k) + (b.found ? "" : " (not installed)") + "</option>";
        }).join("");
      }
      var chipEl = m.root.querySelector(".ag-chip");
      if (chipEl) chipEl.textContent = r.body.chip || "no chip open";
    });
    S.after = 0;
    poll(true);
    schedule();
    return m;
  }
  function unmountMissing() {
    S.mounts = S.mounts.filter(function (m) { return document.body.contains(m.root); });
  }
  function toggleFloat(trigger) {
    var pop = document.getElementById("agent-popover");
    if (!pop) return;
    var home = document.getElementById("agent-home");
    if (home && document.body.contains(home)) {
      var ta = home.querySelector(".ag-input");
      if (ta) ta.focus();
      return;
    }
    var open = !pop.classList.contains("agent-hidden");
    if (open) { pop.classList.add("agent-hidden"); return; }
    pop.classList.remove("agent-hidden");
    var body = pop.querySelector(".agent-body");
    if (body && !body.getAttribute("data-ag-mounted")) {
      mount(body, { compact: true, id: "float" });
      var head = pop.querySelector(".agent-header");
      if (head && window.FloatPanel) {
        window.FloatPanel.drag(pop, { handle: head, tools: ".agent-header-tools", floatClass: "agent-floating" });
        if (window.FloatPanel.resize) window.FloatPanel.resize(pop, { floatClass: "agent-floating" });
      }
    } else {
      poll(true);
    }
  }
  window.toggleAgentPanel = toggleFloat;
  window.addEventListener("beforeunload", function (e) {
    var live = S.session && S.session.session;
    if (live && live.alive && live.busy) { e.preventDefault(); e.returnValue = "The agent is mid-turn on this chip."; return e.returnValue; }
  });
  function init() {
    unmountMissing();
    var home = document.getElementById("agent-home");
    if (home) mount(home, { id: "home" });
  }
  document.addEventListener("htmx:afterSwap", function () { init(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  return { mount: mount, poll: poll, submit: submit, key: key, preset: preset, startPlan: startPlan, cancelPlan: cancelPlan,
           setPlanMode: setPlanMode, approve: approve, reject: reject, stop: stop, arm: arm, disarm: disarm,
           endSession: endSession, setObserver: setObserver, toggleFloat: toggleFloat, init: init, absorb: absorb,
           _state: S, fmtNum: fmtNum, fmtClock: fmtClock };
})();
