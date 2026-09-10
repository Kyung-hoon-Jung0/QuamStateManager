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
 *
 * Customer feedback 2026-09-08 ("hard to read"): ONE column. A one-line status
 * STRIP replaces the side column, the feed is a TIMELINE (time gutter + content,
 * boxes only where there are actions), consecutive tool events fold into one
 * row, a long answer is clamped behind "show more", and the composer is pinned
 * at the bottom of the pane. Same markup in the home and the compact float.
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
  // docs/173 S8: the name picker in front of the keyboard. One key, `quam_actor_name`,
  // is the person the door records (armed by / Stop by / mode by / "I ran it"). Empty =
  // the routes fall back to a plain "human".
  function actorName() { try { return localStorage.getItem("quam_actor_name") || ""; } catch (e) { return ""; } }
  function actorRecents() { try { return JSON.parse(localStorage.getItem("quam_actor_recents") || "[]"); } catch (e) { return []; } }
  function setActor(v) {
    v = String(v || "").trim();
    try {
      localStorage.setItem("quam_actor_name", v);
      if (v) {
        var r = actorRecents().filter(function (x) { return x !== v; });
        r.unshift(v);
        localStorage.setItem("quam_actor_recents", JSON.stringify(r.slice(0, 8)));
      }
    } catch (e) { /* ignore */ }
    S.mounts.forEach(function (m) { var d = m.root.querySelector("#ag-actor-list"); if (d) d.innerHTML = actorRecents().map(function (x) { return '<option value="' + esc(x) + '">'; }).join(""); });
  }
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
    syncExpanded(el);
    confirmClamp(el);        // the rendered height, not the character count, decides the clamp
    return true;
  }
  // customer feedback 2026-09-08: the feed is a TIMELINE -- every row is a monospace
  // time gutter and its content; only plan / run / approval cards keep a frame
  function clockHtml(ts) { return esc(fmtClock(ts)).replace(" ", "<br>"); }    // another day: the date above the time
  function gutter(ts) { return '<span class="ag-t">' + clockHtml(ts) + "</span>"; }
  function row(ts, body) { return gutter(ts) + '<div class="ag-body">' + body + "</div>"; }
  // a long answer is clamped with a fade and a show-more link; the open/closed state
  // lives on the CARD (data-expanded), which a re-render of its innerHTML never touches
  var CLAMP_CHARS = 1100, CLAMP_LINES = 14;
  function isLong(c) {
    var t = String(c.text || "");
    if (!t && c.html) t = String(c.html).replace(/<[^>]+>/g, "");
    return t.length > CLAMP_CHARS || t.split("\n").length > CLAMP_LINES;
  }
  function syncExpanded(el) {
    var b = el.querySelector ? el.querySelector(".ag-more") : null;
    if (b) b.textContent = el.getAttribute("data-expanded") === "1" ? "show less" : "show more";
  }
  // round 2, measured in Chrome: the clamp was decided by CHARACTER count while the
  // clamp itself is a HEIGHT, so a 1,243-char paragraph that rendered 174px tall --
  // nothing hidden -- still wore the fade and a "show more" that revealed nothing.
  // isLong() is only the candidate now; the rendered box decides. No layout (jsdom,
  // a detached node) leaves the candidate verdict exactly as it was.
  function confirmClamp(el) {
    var md = el.querySelector ? el.querySelector(".ag-md.ag-clamp") : null;
    if (!md || el.getAttribute("data-expanded") === "1") return;
    if (!md.clientHeight) return;                       // nothing is laid out: keep the heuristic
    if (md.scrollHeight > md.clientHeight + 4) return;  // genuinely cut off: the clamp stands
    md.classList.remove("ag-clamp");
    var b = el.querySelector(".ag-more");
    if (b) b.remove();
  }
  function toggleMore(btn) {
    var card = btn && btn.closest ? btn.closest(".ag-card") : null;
    if (!card) return false;
    if (card.getAttribute("data-expanded") === "1") card.removeAttribute("data-expanded"); else card.setAttribute("data-expanded", "1");
    syncExpanded(card);
    return false;
  }
  function renderChatCard(m, c) {
    var el = cardFor(m, c.kind, c.n, c.ts);
    if (!el) return;
    var html;
    if (c.kind === "user") {
      html = '<div class="ag-bubble ag-user"><span class="ag-who">' + esc(c.who || "you") + '</span><p class="ag-user-text">' + esc(c.text) + "</p></div>";
    } else if (c.kind === "answer") {
      var long = isLong(c);
      html = '<div class="ag-answer"><span class="ag-who">' + esc(c.backend ? "by_" + c.backend : "agent") + "</span>" +
        '<div class="ag-md' + (long ? " ag-clamp" : "") + '">' + (c.html || "<p>" + esc(c.text) + "</p>") + "</div>" +
        (long ? '<button type="button" class="ag-more" onclick="return AgentPanel.toggleMore(this)">show more</button>' : "") + "</div>";
    } else if (c.kind === "tool") {
      var t = String(c.tool || "").replace(/^mcp__sm__/, "sm.");
      var sum = String(c.summary || "").trim();
      if (sum === "{}" || sum === "[]") sum = "";                 // review R2-minor: an empty argument list says nothing
      html = '<div class="ag-tool' + (c.failed ? " ag-failed" : "") + '"><code>' + esc(t) + "</code>" + (sum ? ' <span class="muted">' + esc(sum.slice(0, 140)) + "</span>" : "") +
        (c.failed ? ' <span class="ag-err">' + esc((c.error || "failed").slice(0, 160)) + "</span>" : "") + "</div>";
    } else if (c.kind === "error") {
      html = '<div class="ag-tool ag-failed"><strong>agent exited</strong> <span class="ag-err">' + esc((c.error || "").slice(0, 200)) + "</span></div>";
    } else if (c.kind === "limited") {
      html = '<div class="ag-tool ag-limited"><strong>usage limit</strong> ' + esc(c.text || "") + "</div>";
    } else if (c.kind === "stop") {
      html = '<div class="ag-tool ag-stopped"><strong>Stopped</strong> ' + esc(c.text || "") + "</div>";
    } else {
      html = '<div class="ag-tool">' + esc(c.kind) + " " + esc(c.text || c.summary || "") + "</div>";
    }
    setHtml(el, row(c.ts, html));
  }
  // Consecutive TOOL events fold into ONE muted row -- "⚙ 5 tool calls · sm.take_live, …" --
  // that opens to the lines. The member cards stay direct children of the feed (the
  // time-order insertion in cardFor keeps working; nothing is moved), hidden while the
  // group is closed. A FAILED tool breaks the run and stands on its own, in the error
  // colour. Group elements are reused by their first member, so open/closed survives a poll.
  function toolName(el) { var c = el.querySelector("code"); return c ? c.textContent : ""; }
  function groupMembers(g) {
    var out = [], n = g.nextElementSibling;
    while (n && n.classList.contains("ag-in-group")) { out.push(n); n = n.nextElementSibling; }
    return out;
  }
  function toggleGroup(s) {
    var g = s && s.closest ? s.closest(".ag-toolgroup") : null;
    if (!g) return false;
    var open = !g.open;
    if (open) g.setAttribute("open", ""); else g.removeAttribute("open");
    groupMembers(g).forEach(function (el) { el.hidden = !open; });
    return false;
  }
  function groupTools(host) {
    if (!host) return;
    var groups = host.__agGroups || (host.__agGroups = {});
    var used = {}, run = [];
    function flush() {
      if (run.length >= 2) {
        var key = run[0].getAttribute("data-card");
        var g = groups[key];
        if (!g) {
          g = document.createElement("details");
          g.className = "ag-card ag-toolgroup";
          g.setAttribute("data-group", key);
          g.innerHTML = '<summary onclick="return AgentPanel.toggleGroup(this)"><span class="ag-t"></span><span class="ag-toolgroup-sum"></span></summary>';
          groups[key] = g;
        }
        g.setAttribute("data-ts", run[0].getAttribute("data-ts") || "0");
        var names = [], seen = {};
        run.forEach(function (el) { var n = toolName(el); if (n && !seen[n]) { seen[n] = 1; names.push(n); } });
        g.querySelector(".ag-t").innerHTML = clockHtml(Number(run[0].getAttribute("data-ts")) || 0);
        g.querySelector(".ag-toolgroup-sum").textContent = "⚙ " + run.length + " tool calls · " + names.slice(0, 3).join(", ") + (names.length > 3 ? ", …" : "");
        if (g.parentNode !== host || g.nextElementSibling !== run[0]) host.insertBefore(g, run[0]);
        run.forEach(function (el) { el.classList.add("ag-in-group"); el.hidden = !g.open; });
        used[key] = 1;
      } else {
        run.forEach(function (el) { if (el.classList.contains("ag-in-group")) { el.classList.remove("ag-in-group"); el.hidden = false; } });
      }
      run = [];
    }
    Array.prototype.slice.call(host.children).forEach(function (el) {
      if (el.classList.contains("ag-toolgroup")) return;          // a summary from the last pass: judged by `used` below
      if (el.classList.contains("ag-tool") && !el.querySelector(".ag-failed")) run.push(el); else flush();
    });
    flush();
    Object.keys(groups).forEach(function (k) { if (!used[k]) { var g = groups[k]; if (g.parentNode) g.parentNode.removeChild(g); delete groups[k]; } });
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
    // compact step rows: glyph · node · targets · run · writes · why (a div, not a table)
    var rows = (p.steps || []).map(function (s) {
      return '<div class="ag-step">' + stepBadge(s) + " <code>" + esc(s.node) + "</code>" + simBadge(s.simulated) +
        ' <span class="ag-step-t">' + esc((s.targets || []).join(" ")) + "</span>" +
        (s.run_id ? " " + runLink(s.run_id) : "") + (s.classification && s.classification !== "ok" ? ' <span class="ag-err">' + esc(s.classification) + "</span>" : "") +
        (s.n_writes ? ' <span class="ag-step-w">' + s.n_writes + (s.applied ? " applied" : (s.approval ? " waiting" : "")) + "</span>" : "") +
        (s.why ? ' <span class="muted ag-step-why">' + esc(s.why) + "</span>" : "") + "</div>";
    }).join("");
    // "q1 · amplitude   now 0.12" -- the dot path rides the title
    var mayHtml = may.length
      ? "<ul class=\"ag-may\">" + may.map(function (x) {
          return "<li><strong title=\"" + esc(x.path) + "\">" + esc(x.target ? x.target + " · " : "") + esc(x.label || pathLabel(x.path)) + "</strong>" +
            (x.now !== undefined && x.now !== null ? ' <span class="muted">now ' + esc(fmtNum(x.now)) + "</span>" : ' <span class="muted">now: not set</span>') +
            (x.note ? ' <span class="muted">(' + esc(x.note) + ")</span>" : "") +
            (x.last ? ' <span class="muted">· last ' + esc(x.last.actor || "?") + " " + esc(x.last.when || "") + "</span>" : "") + "</li>";
        }).join("") + "</ul>"
      : '<p class="muted ag-may-none">no earlier run of these nodes on these targets -- what changes is not known in advance</p>';
    var origin = p.source === "run_cmd" ? "/run typed by " + (p.created_by || "a person") : "proposed by " + (p.created_by || "the agent");
    var head = '<div class="ag-plan-head"><span class="ag-plan-st ag-st-' + esc(p.status) + '">' + esc(PLAN_STATUS_TEXT[p.status] || p.status) + "</span>" +
      " <strong>" + esc(p.title) + "</strong>" + ' <span class="muted">' + esc(origin) + " · " + esc(fmtClock(p.created)) + "</span></div>";
    var prog = "";
    if (p.status !== "draft") {
      prog = '<div class="ag-plan-prog">' + (c.done || 0) + " / " + (c.total || 0) + " done" + (c.failed ? " · ✗ " + c.failed : "") + (c.skipped ? " · skipped " + c.skipped : "") +
        (c.waiting ? " · waiting " + c.waiting : "") + (p.ended ? " · " + (p.status === "done" ? "finished " : "ended ") + fmtClock(p.ended) + (p.ended_by ? " by " + esc(p.ended_by) : "") : "") + "</div>";
    }
    var mode = p.mode || (S.now && S.now.mode) || "ask-writes";
    var modeSel = p.status === "draft"
      ? '<label class="ag-mode">mode <select' + (S.observer ? " disabled" : "") + ' onchange="AgentPanel.setPlanMode(\'' + esc(p.id) + '\', this.value)">' +
        ["auto", "ask-writes", "ask-all"].map(function (x) { return '<option value="' + x + '"' + (x === mode ? " selected" : "") + ">" + x + "</option>"; }).join("") + "</select></label>"
      : '<span class="muted ag-mode-ro">mode ' + esc(mode) + "</span>";
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
    var html = head + '<div class="ag-steps">' + rows + "</div>" +
      '<details class="ag-may-wrap"' + (p.status === "draft" ? " open" : "") + "><summary>values that may change" + (may.length ? " (" + may.length + ")" : "") + "</summary>" + mayHtml + "</details>" +
      prog + '<div class="ag-plan-acts">' + modeSel + " " + acts + "</div>";
    setHtml(el, row(p.created, html), force);
  }

  function renderRun(m, r, force) {
    var el = cardFor(m, "run", r.key, r.since);
    if (!el) return;
    var res = r.result || {};
    var st = r.status;
    var stTxt = st === "ended" ? (res.status || "ended") : st;
    var line = '<span class="ag-step-st ag-st-' + esc(stTxt) + '">' + esc(stTxt) + "</span>" +
      " <strong><code>" + esc(r.node) + "</code></strong> " + esc((r.targets || []).join(" ")) +
      simBadge(res.simulated || r.simulated) +
      (res.classification && res.classification !== "ok" ? ' <span class="ag-err">' + esc(res.classification) + "</span>" : "") +
      (res.run_id ? " " + runLink(res.run_id) : "") + ' <span class="muted">' + esc(fmtClock(r.since)) + "</span>";
    var writes = "";
    if (res.writes && res.writes.length) {
      writes = '<details class="ag-writes"><summary>' + res.writes.length + " write(s) " + (res.applied ? "applied to the chip" : (res.approval ? "waiting for approval" : "not staged")) + "</summary><div class=\"ag-tbl\"><table>" +
        res.writes.slice(0, 40).map(function (w) { return "<tr><td title=\"" + esc(w.path) + "\">" + esc(w.path) + "</td><td>" + esc(fmtNum(w.old)) + " → " + esc(fmtNum(w.new)) + "</td></tr>"; }).join("") +
        (res.writes.length > 40 ? "<tr><td colspan=2 class=muted>… " + (res.writes.length - 40) + " more</td></tr>" : "") + "</table></div></details>";
    }
    var err = res.error ? '<div class="ag-err">' + esc(String(res.error).slice(0, 300)) + "</div>" : "";
    var how = r.how ? '<div class="muted ag-how">' + esc(r.how) + "</div>" : "";
    var log = res.log_tail ? '<details class="ag-log"><summary>log tail</summary><pre>' + esc(res.log_tail.slice(-1500)) + "</pre></details>" : "";
    setHtml(el, row(r.since, '<div class="ag-run-line">' + line + "</div>" + err + writes + how + log), force);
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
    var html = '<div class="ag-ap-head"><span class="ag-plan-st ag-st-waiting">' + (isRun ? "run request" : "approval") + "</span> <strong><code>" + esc(a.node || "") + "</code></strong> " + esc((a.targets || []).join(" ")) +
      (a.run_id ? " " + runLink(a.run_id) : "") + ' <span class="muted">' + esc(a.why_held || "") + " · " + esc(fmtClock(a.created)) + "</span></div>" +
      (isRun ? '<p class="muted ag-ap-note">the agent asks to RUN this node on these targets (mode ask-all); nothing runs before Allow</p>' :
        '<div class="ag-tbl"><table class="ag-ap-rows"><thead><tr><th>value</th><th>now</th><th>proposed (editable)</th></tr></thead><tbody>' + rows + "</tbody></table></div>") +
      (a.reason ? '<p class="ag-because">because: ' + esc(a.reason) + "</p>" : "") + '<div class="ag-ap-acts">' + acts + "</div>";
    setHtml(el, row(a.created, html), force);
  }

  // ---------------------------------------------------------- the "now"
  // review round 1 (2026-09-08): the strip used to be rebuilt wholesale on EVERY poll
  // (4 s while a run is active), so the focus on Stop now / the observer box dropped
  // to <body> exactly while a person was tabbing to it, and -- with role=status on the
  // whole strip -- every poll re-announced state, counts and button labels. Now each
  // part is replaced only when its HTML changed, a control that held the focus gets it
  // back on its successor (matched by what it IS, then by position), and the one live
  // region is the visually-hidden .ag-now-sr, whose text is the state alone and is
  // written only when that text changes.
  var STRIP_FOCUSABLE = "button, input, select, a[href]";
  function focusKeyOf(el) { return el.tagName + "|" + (el.className || "") + "|" + (el.textContent || "").trim(); }
  function swapHtml(el, html) {
    if (!el || el.__agHtml === html) return false;
    var act = document.activeElement, key = null, idx = -1;
    if (act && act !== document.body && el.contains(act)) {
      key = focusKeyOf(act);
      idx = Array.prototype.indexOf.call(el.querySelectorAll(STRIP_FOCUSABLE), act);
    }
    el.innerHTML = html;
    el.__agHtml = html;
    if (key !== null) {
      var all = Array.prototype.slice.call(el.querySelectorAll(STRIP_FOCUSABLE)), again = null;
      for (var i = 0; i < all.length; i++) { if (focusKeyOf(all[i]) === key) { again = all[i]; break; } }
      if (!again && all.length) again = all[Math.min(idx < 0 ? 0 : idx, all.length - 1)];   // the control went away (Disarm after a disarm): its successor at the same place
      if (again) { try { again.focus(); } catch (e) { /* ignore */ } }
    }
    return true;
  }
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
    // customer feedback 2026-09-08: the "now" column became a ONE-LINE status strip --
    // state · session · today's counts on the left, the doors + links on the right; a
    // second row only for a run in progress (and on a narrow pane, where the strip wraps)
    var seg = [];
    seg.push('<span class="ag-now-state ag-' + esc(v.state) + '"' + (v.title ? ' title="' + esc(v.title) + '"' : "") + '><span class="agent-pill-dot"></span>' + esc(String(v.text || "").replace(/^Agent: /, "")) + "</span>");
    if (file && file.owner) {
      seg.push('<span class="ag-now-line">' + esc(file.backend || "") + " · " + esc(file.owner) + (file.until ? " → " + esc(fmtClock(file.until)) : "") + (file.stopped ? ' · <span class="ag-err">stopped</span>' : "") +
        (armed ? ' · <span class="ag-armed" title="a person pressed Arm: the agent may start hardware runs">armed</span>' : ' · <span class="muted">not armed</span>') + "</span>");
    } else {
      seg.push('<span class="ag-now-line muted">no agent session on this chip</span>');
    }
    seg.push('<span class="ag-now-line">today ' + (d.events_today || 0) + " events" + (d.failures_today ? ' · <span class="ag-err">' + d.failures_today + " failed</span>" : "") + (d.waiting ? ' · <strong>waiting ' + d.waiting + "</strong>" : "") + "</span>");
    // customer feedback 2026-09-08 (round 2, measured in Chrome): the strip read
    // "human ran X · 32s ago · … · human ran X 32s ago" -- the pill's own state text
    // ALREADY says it, so the separate line is a duplicate that pushed the strip to
    // three rows. It renders only when the state is about something else.
    if (d.human_ran && v.state !== "human-ran") {
      seg.push('<span class="ag-now-line">human ran <code>' + esc(d.human_ran.node || "") + "</code> " + esc(fmtAgo(d.human_ran.ts)) + "</span>");
    }
    if (S.unreachable) seg.push('<span class="ag-now-line ag-err ag-unreachable">' + esc(UNREACHABLE) + "</span>");
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
    acts.push('<span class="ag-now-links"><a href="/journal" hx-get="/journal" hx-target="#table-pane" hx-push-url="true">Calibration log →</a>' +
      ' · <a class="ag-setup-link" href="/agent/setup" hx-get="/agent/setup" hx-target="#table-pane" hx-push-url="true" title="connect Claude / Codex to SM, the journal folder, the lab context file">Setup →</a></span>');
    var runHtml = d.running ? "▶ <code>" + esc(d.running.node || d.running.tool || "") + "</code> " + esc(fmtAgo(d.running.since)) +
      (d.running.typical_s ? ' <span class="muted">usually ~' + Math.round(d.running.typical_s / 60) + "m</span>" : "") : "";
    if (!host.__agParts) {
      // the three parts are made ONCE; from then on each is swapped only when it changed
      host.innerHTML = '<span class="ag-now-sr visually-hidden" role="status"></span><div class="ag-now-main"></div><div class="ag-now-acts"></div>';
      host.__agParts = true;
    }
    var sr = host.querySelector(".ag-now-sr");
    // the announced text is the state WITHOUT its ticking duration ("· 32s", "· 3m ago"),
    // so a run in progress is said once, not every poll
    var srText = String(v.text || "").replace(/^Agent: /, "").replace(/ (· )?\d+(\.\d+)?[smh]\b( ago)?/g, "") + (S.unreachable ? " · " + UNREACHABLE : "");
    if (sr && sr.textContent !== srText) sr.textContent = srText;            // written only when the STATE changes
    swapHtml(host.querySelector(".ag-now-main"), seg.join('<span class="ag-now-sep">·</span>'));
    swapHtml(host.querySelector(".ag-now-acts"), acts.join(" "));
    var runEl = host.querySelector(".ag-now-run");
    if (runHtml) {
      if (!runEl) { runEl = document.createElement("div"); runEl.className = "ag-now-run"; host.appendChild(runEl); }
      swapHtml(runEl, runHtml);                                            // the ticking "32s ago" touches only this row, which holds no control
    } else if (runEl) {
      runEl.remove();
    }
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
    S.mounts.forEach(function (m) { groupTools(cardsHost(m)); });   // after every insert: runs of tool rows fold
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
    var done = function (ok) { ta.disabled = false; if (ok) { ta.value = ""; grow(ta); } ta.focus(); poll(true); };
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
    if (ta) { ta.value = pr[1]; grow(ta); ta.focus(); ta.setSelectionRange(pr[1].indexOf("<"), pr[1].indexOf(">") + 1); }
  }
  function togglePresets(btn) {
    // compact (the float): the preset chips sit behind one small toggle
    var g = btn && btn.closest ? btn.closest(".ag-presets") : null;
    if (!g) return;
    var open = !g.classList.contains("open");
    g.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function grow(ta) {
    // the composer is ONE row and grows with the draft to ~6 rows, then scrolls inside
    if (!ta || !ta.style) return;
    ta.style.height = "auto";
    var cs = window.getComputedStyle ? window.getComputedStyle(ta) : null;
    var lh = (cs && parseFloat(cs.lineHeight)) || 22;
    var pad = cs ? ((parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0)) : 14;
    var max = Math.round(lh * 6 + pad);
    var h = ta.scrollHeight || 0;
    if (h > max) { ta.style.height = max + "px"; ta.style.overflowY = "auto"; }
    else { ta.style.height = h ? h + "px" : ""; ta.style.overflowY = "hidden"; }
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
    // customer feedback 2026-09-08: ONE column -- a muted lead, the status STRIP, the feed
    // (scrolls inside the pane), the COMPOSER pinned at the bottom. Same markup for both
    // mounts; the compact float only changes what the CSS does with it.
    return '<div class="ag-root' + (compact ? " ag-compact" : "") + '">' +
      '<div class="ag-head"><span class="ag-chip"></span> <span class="ag-qubits"></span></div>' +
      '<div class="ag-now"></div>' +
      '<div class="ag-cards" aria-live="polite"></div>' +
      '<form class="ag-form ag-composer" onsubmit="return AgentPanel.submit(event)">' +
      '<textarea class="ag-input" rows="1" onkeydown="return AgentPanel.key(event)" oninput="AgentPanel.grow(this)" placeholder="Ask, or tell the agent what to do…  (Enter sends · Shift+Enter newline · /run <node> <targets>)"></textarea>' +
      '<div class="ag-form-row"><select class="ag-backend" title="which CLI drives"></select>' +
      '<label class="ag-actor-wrap" title="who is at the keyboard — the person SM records for Arm / Stop / mode / “I ran it”">⌨ <input class="ag-actor" list="ag-actor-list" placeholder="your name" autocomplete="off" spellcheck="false" oninput="AgentPanel.setActor(this.value)"><datalist id="ag-actor-list"></datalist></label>' +
      '<span class="ag-presets" title="a preset fills a draft; nothing starts before a plan card\'s Start">' +
      '<button type="button" class="btn-sm ag-presets-toggle" onclick="AgentPanel.togglePresets(this)" aria-expanded="false">presets ▾</button>' +
      PRESETS.map(function (p, i) { return '<button type="button" class="btn-sm ag-preset" onclick="AgentPanel.preset(' + i + ', this.closest(\'.ag-root\'))">' + esc(p[0]) + "</button>"; }).join("") + "</span>" +
      '<button type="submit" class="btn-sm ag-send">Send</button></div></form>' +
      '<div id="ag-toast" class="ag-toast" hidden></div></div>';
  }

  /* ── the wiring strip ───────────────────────────────────────────────────
   *
   * "어떻게 이게 MCP처럼 작동하지?" — the mechanism is the answer, so the strip
   * states it: SM registers itself as an MCP server in the CLI's own config,
   * and a hook reports each run back.
   *
   * Three words, and their order is the whole design:
   *   installed   the binary answered `--version`
   *   registered  SM's entry is in that CLI's config file
   *   answered    a real read-only call from SM to it succeeded — PAST TENSE
   *
   * There is deliberately no fourth word. SM's whole probe is a `--version`
   * exit code, which succeeds on a logged-out CLI, and nothing in this app
   * reads a credential — so it cannot know whether anyone is logged in, and
   * saying so would be exactly the kind of confidently-wrong light the report
   * was about. On a failure it does not classify either: an auth failure and a
   * network failure look identical from here, so the CLI's own words are shown
   * verbatim instead of a guess.
   */
  var WIRE = { data: null, inflight: null, setup: null };

  function wireEls() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-ag-wire]"));
  }

  /* What the CLI printed, as a version. The two say it differently --
     claude: "2.1.267 (Claude Code)", codex: "codex-cli 0.153.4" -- so the
     NUMBER is taken and prefixed, and anything with no number in it is shown
     verbatim rather than dressed up as one. (Prefixing "v" onto the raw string
     gave `vcodex-cli 0.153.4`, seen in the browser.) */
  function shortVersion(v) {
    var m = /\d+(?:\.\d+)+/.exec(String(v || ""));
    return m ? "v" + m[0] : String(v || "");
  }

  function wireBadge(el, text, kind) {
    var b = el.querySelector(".ag-wire-badge");
    if (!b) return;
    b.textContent = text;
    b.className = "ag-wire-badge ag-wire-" + kind;
  }

  /* One backend's line. `reg` is the registration this CLI needs; `b` is what
     the probe found; `t` is the record of the last real call. */
  function wireCli(name, b, reg, t, compact) {
    var found = !!(b && b.found);
    if (!found) {
      return '<span class="ag-wire-cli ag-wire-off" title="install it, then log in once in a terminal — SM never sees your login">'
           + esc(name) + " — not on PATH</span>";
    }
    var bits = [];
    if (!compact && b.version) bits.push(esc(shortVersion(b.version)));
    if (reg.mcp) bits.push('<span class="ag-wire-ok" title="SM is registered as an MCP server named quam-state-manager in this CLI\'s own config">MCP \u2713</span>');
    if (reg.hooks) bits.push('<span class="ag-wire-ok" title="a hook in the CLI\'s settings reports each run back to SM">hooks \u2713</span>');
    if (reg.allow === true) bits.push('<span class="ag-wire-ok" title="SM\'s tools are pre-allowed in this calibrations folder">allow \u2713</span>');
    var line = '<span class="ag-wire-cli"><b>' + esc(name) + "</b> " + bits.join(" \u00b7 ");
    if (!reg.mcp) {
      line += ' <span class="ag-wire-warn">not registered as an MCP server</span>'
            + ' <a class="ag-wire-fix" href="/agent/setup" hx-get="/agent/setup" hx-target="#table-pane" hx-push-url="true">Connect \u2192</a>';
    } else if (t && t.ok) {
      // past tense, and the title says why it is past tense
      line += ' \u00b7 <span class="ag-wire-tested" title="a real read-only call from SM to this CLI succeeded then — not a live login check">answered SM'
            + (t.elapsed_s ? " in " + fmtNum(t.elapsed_s) + " s" : "")
            + (t.at ? " \u00b7 " + esc(fmtClock(t.at)) : "") + "</span>";
    } else if (t && t.at) {
      line += ' \u00b7 <span class="ag-wire-warn" title="' + esc(String(t.error || "no reason recorded"))
            + '">last test failed</span>';
    } else if (!compact) {
      line += ' \u00b7 <span class="muted">never tested</span>';
    }
    return line + "</span>";
  }

  function wirePaint() {
    var els = wireEls();
    if (!els.length) return;
    var d = WIRE.data, su = WIRE.setup;
    els.forEach(function (el) {
      var compact = el.classList.contains("ag-wire-compact");
      // the server's own reading, when this copy of the strip carries one
      var reg = {
        claude: { mcp: el.getAttribute("data-claude-mcp") === "1",
                  hooks: el.getAttribute("data-claude-hooks") === "1",
                  allow: el.getAttribute("data-claude-allow") === ""
                         ? null : el.getAttribute("data-claude-allow") === "1" },
        codex: { mcp: el.getAttribute("data-codex-mcp") === "1", hooks: false, allow: null },
      };
      if (su) {
        reg.claude = { mcp: !!su.claude.mcp, hooks: !!su.claude.hooks,
                       allow: su.calibrations_folder ? !!su.claude.allow : null };
        reg.codex = { mcp: !!su.codex.mcp, hooks: false, allow: null };
      }
      if (!d) {
        // no probe yet: say only what the files said, never a CLI claim
        wireBadge(el, "CHECKING", "static");
        return;
      }
      var names = Object.keys(d.backends || {});
      if (!names.length) names = ["claude", "codex"];
      var anyFound = names.some(function (n) { return d.backends[n] && d.backends[n].found; });
      var anyReg = names.some(function (n) { return reg[n] && reg[n].mcp; });
      wireBadge(el, !anyFound ? "NO CLI" : (anyReg ? "CONNECTED" : "NOT CONNECTED"),
                !anyFound ? "static" : (anyReg ? "ok" : "warn"));
      var tested = ((su && su.record && su.record.tested) || {});
      var html = names.map(function (n) {
        var r = reg[n] || { mcp: false, hooks: false, allow: null };
        if (compact && n !== d["default"]) {
          var b = d.backends[n];
          return '<span class="ag-wire-cli' + (b && b.found && r.mcp ? "" : " ag-wire-off") + '">'
               + esc(n) + (b && b.found && r.mcp ? " \u2713" : " \u2014") + "</span>";
        }
        return wireCli(n, d.backends[n], r, tested[n], compact);
      }).join('<span class="ag-wire-sep" aria-hidden="true">\u2502</span>');
      // The wrapper FIRST: after the first paint the placeholder selector
      // also matches a line inside it, and replacing that nests one wrapper in
      // the next -- every repaint then duplicated every backend line.
      var wrap = el.querySelector(".ag-wire-clis");
      if (wrap) {
        wrap.innerHTML = html;
      } else {
        var host = el.querySelector(".ag-wire-cli");
        if (host) {
          wrap = document.createElement("span");
          wrap.className = "ag-wire-clis";
          wrap.innerHTML = html;
          host.parentNode.replaceChild(wrap, host);
        }
      }
      // The setup door counts what is left to do — from the server's own list,
      // never re-derived here.
      var link = el.querySelector(".ag-wire-setup");
      if (link && su && su.todo) {
        link.textContent = su.todo.length
          ? (su.todo.length + (su.todo.length === 1 ? " setup step \u2192" : " setup steps \u2192"))
          : "Setup \u2192";
        link.classList.toggle("ag-wire-todo", su.todo.length > 0);
      }
    });
  }

  /* One request per session for the setup record, shared by every mount and
     re-used across htmx swaps: a re-mount repaints from WIRE, and only an
     explicit refresh probes again. */
  function wireLoad(force) {
    if (WIRE.inflight && !force) return WIRE.inflight;
    // No `?refresh=1` here: only /api/agent/chat/backends honours it, and a
    // query parameter this route ignores would read as a refresh that is not
    // happening. Both routes serve the same _detect_all cache, so they cannot
    // disagree; re-probing is the setup page's own button.
    WIRE.inflight = api("GET", "/api/agent/setup").then(function (r) {
      if (r.status === 200) { WIRE.setup = r.body; if (r.body.clis) WIRE.data = { backends: r.body.clis, "default": (WIRE.data || {})["default"] }; }
      wirePaint();
      return r;
    });
    return WIRE.inflight;
  }

  function wireHelp(btn) {
    var p = document.getElementById("ag-wire-help-pop");
    if (p && p.parentNode) { p.parentNode.removeChild(p); return; }
    p = document.createElement("div");
    p.id = "ag-wire-help-pop";
    p.className = "ag-wire-pop";
    p.innerHTML =
      "<p>SM registers itself as an MCP server named <code>quam-state-manager</code> "
      + "in <code>~/.claude.json</code>, and a hook in <code>~/.claude/settings.json</code> "
      + "reports each run back.</p>"
      + "<p>The CLI runs on your machine under your own login \u2014 SM never sees your "
      + "credentials, so it cannot show a login state.</p>";
    document.body.appendChild(p);
    if (window._anchorPopover) { try { window._anchorPopover(p, btn); } catch (e) {} }
    var off = function (ev) {
      if (p.contains(ev.target) || ev.target === btn) return;
      if (p.parentNode) p.parentNode.removeChild(p);
      document.removeEventListener("pointerdown", off, true);
    };
    document.addEventListener("pointerdown", off, true);
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
      // The strip's CLI half rides this same response — no extra request for
      // the thing the customer wants to see immediately.
      WIRE.data = { backends: S.backends, "default": S.defaultBackend };
      wirePaint();
      wireLoad(false);
    });
    var actorEl = m.root.querySelector(".ag-actor");
    if (actorEl) { actorEl.value = actorName(); }
    if (m.compact) {
      // one row in a narrow float cannot hold the whole hint; the /run form is in the home's box
      var taEl = m.root.querySelector(".ag-input");
      if (taEl) taEl.placeholder = "Ask, or tell the agent what to do…  (Enter sends)";
    }
    var dl = m.root.querySelector("#ag-actor-list");
    if (dl) dl.innerHTML = actorRecents().map(function (x) { return '<option value="' + esc(x) + '">'; }).join("");
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
  document.addEventListener("htmx:afterSwap", function () { init(); wirePaint(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  return { mount: mount, poll: poll, submit: submit, key: key, preset: preset, startPlan: startPlan, cancelPlan: cancelPlan,
           wireHelp: wireHelp, wirePaint: wirePaint, wireLoad: wireLoad, _wire: WIRE,
           shortVersion: shortVersion,
           setPlanMode: setPlanMode, approve: approve, reject: reject, stop: stop, arm: arm, disarm: disarm,
           endSession: endSession, setObserver: setObserver, setActor: setActor, actorName: actorName,
           toggleFloat: toggleFloat, init: init, absorb: absorb, _state: S, fmtNum: fmtNum, fmtClock: fmtClock,
           grow: grow, toggleMore: toggleMore, toggleGroup: toggleGroup, togglePresets: togglePresets };
})();
