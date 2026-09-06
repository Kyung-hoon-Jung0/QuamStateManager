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
 */
window.AgentPanel = (function () {
  "use strict";
  var S = {
    after: 0, seq: -1, chip: null, session: null, now: null, backends: null, defaultBackend: "claude",
    plans: {}, runs: {}, approvals: {}, mounts: [], timer: null, inflight: false, observer: false,
    seenCards: {}, lastPoll: 0
  };
  var PRESETS = [
    ["1Q bringup", "1Q bringup on <targets>: resonator spectroscopy -> qubit spectroscopy -> power rabi -> ramsey. Propose the plan with plan_propose (one step per node and target) and wait for Start."],
    ["readout tuneup", "Readout tuneup on <targets>: resonator spectroscopy vs power -> readout frequency/amplitude optimization -> IQ blobs. Propose the plan with plan_propose and wait for Start."],
    ["CZ tuneup", "CZ tuneup on <pair>: coupler flux -> CZ chevron -> CZ phase calibration -> 2Q RB. Propose the plan with plan_propose and wait for Start."]
  ];

  // ------------------------------------------------------------ helpers
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmtClock(ts) { if (!ts) return ""; var d = new Date(ts * 1000); return d.toTimeString().slice(0, 5); }
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
    var h = { "Accept": "application/json" };
    if (body !== undefined) h["Content-Type"] = "application/json";
    var who = actorName();
    if (who) h["X-SM-Actor"] = who;
    return fetch(path, { method: method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; }); });
  }
  function toast(msg, level) {
    if (window.showToast) { try { window.showToast(msg, level || "info"); return; } catch (e) { /* fall through */ } }
    var el = document.getElementById("ag-toast");
    if (el) { el.textContent = msg; el.hidden = false; setTimeout(function () { el.hidden = true; }, 5000); }
  }
  function observer() { try { return localStorage.getItem("quam_agent_observer") === "1"; } catch (e) { return false; } }
  function setObserver(on) { try { localStorage.setItem("quam_agent_observer", on ? "1" : "0"); } catch (e) { /* ignore */ } S.observer = !!on; renderAll(); }
  function pathLabel(p) { var parts = String(p || "").split("."); return parts[parts.length - 1]; }
  function runLink(rid) { return rid ? '<a class="ag-run" href="/dataset/by-run/' + rid + '" hx-get="/dataset/by-run/' + rid + '" hx-target="#table-pane" hx-push-url="true">#' + rid + "</a>" : ""; }

  // ------------------------------------------------------------- cards
  function cardsHost(m) { return m.root.querySelector(".ag-cards"); }
  function cardFor(m, kind, id) {
    var host = cardsHost(m);
    if (!host) return null;
    var el = host.querySelector('[data-card="' + kind + ":" + id + '"]');
    if (!el) {
      el = document.createElement("article");
      el.className = "ag-card ag-" + kind;
      el.setAttribute("data-card", kind + ":" + id);
      host.appendChild(el);
    }
    return el;
  }
  function renderChatCard(m, c) {
    var el = cardFor(m, c.kind, c.n);
    if (!el) return;
    var when = '<span class="ag-when">' + esc(fmtClock(c.ts)) + "</span>";
    if (c.kind === "user") {
      el.innerHTML = '<div class="ag-bubble ag-user"><span class="ag-who">' + esc(c.who || "you") + "</span>" + when + "<p>" + esc(c.text) + "</p></div>";
    } else if (c.kind === "answer") {
      el.innerHTML = '<div class="ag-bubble ag-answer"><span class="ag-who">' + esc(c.backend ? "by_" + c.backend : "agent") + "</span>" + when +
        '<div class="ag-md">' + (c.html || "<p>" + esc(c.text) + "</p>") + "</div></div>";
    } else if (c.kind === "tool") {
      var t = String(c.tool || "").replace(/^mcp__sm__/, "sm.");
      el.innerHTML = '<div class="ag-tool' + (c.failed ? " ag-failed" : "") + '"><code>' + esc(t) + "</code> <span class=\"muted\">" + esc((c.summary || "").slice(0, 140)) + "</span>" +
        (c.failed ? ' <span class="ag-err">' + esc((c.error || "failed").slice(0, 160)) + "</span>" : "") + when + "</div>";
    } else if (c.kind === "error") {
      el.innerHTML = '<div class="ag-tool ag-failed"><strong>agent exited</strong> <span class="ag-err">' + esc((c.error || "").slice(0, 200)) + "</span>" + when + "</div>";
    } else if (c.kind === "limited") {
      el.innerHTML = '<div class="ag-tool ag-limited"><strong>usage limit</strong> <span class="muted">' + esc(c.text || "") + "</span>" + when + "</div>";
    } else if (c.kind === "stop") {
      el.innerHTML = '<div class="ag-tool ag-stopped"><strong>Stopped</strong> <span class="muted">' + esc(c.text || "") + "</span>" + when + "</div>";
    } else {
      el.innerHTML = '<div class="ag-tool muted">' + esc(c.kind) + " " + esc(c.text || c.summary || "") + when + "</div>";
    }
  }

  function stepBadge(s) {
    var st = s.status || "pending";
    var txt = { pending: "·", running: "▶", done: "✓", failed: "✗", skipped: "skip", cancelled: "—" }[st] || st;
    return '<span class="ag-step-st ag-st-' + esc(st) + '" title="' + esc(st) + '">' + txt + "</span>";
  }
  function renderPlan(m, p) {
    var el = cardFor(m, "plan", p.id);
    if (!el) return;
    var c = p.counts || {};
    var may = p.may_change || [];
    var rows = (p.steps || []).map(function (s) {
      return "<tr>" + "<td>" + stepBadge(s) + "</td><td><code>" + esc(s.node) + "</code></td><td>" + esc((s.targets || []).join(" ")) + "</td>" +
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
    var head = '<div class="ag-plan-head"><strong>' + esc(p.title) + '</strong> <span class="ag-plan-st ag-st-' + esc(p.status) + '">' + esc(p.status) + "</span>" +
      ' <span class="muted">' + esc(p.source === "run_cmd" ? "/run" : "proposed by " + (p.created_by || "agent")) + " · " + esc(fmtClock(p.created)) + "</span></div>";
    var prog = "";
    if (p.status !== "draft") {
      prog = '<div class="ag-plan-prog">' + (c.done || 0) + " / " + (c.total || 0) + " done" + (c.failed ? " · ✗ " + c.failed : "") + (c.skipped ? " · skipped " + c.skipped : "") +
        (c.waiting ? " · waiting " + c.waiting : "") + (p.ended ? " · " + (p.status === "done" ? "finished " : "ended ") + fmtClock(p.ended) + (p.ended_by ? " by " + esc(p.ended_by) : "") : "") + "</div>";
    }
    var mode = p.mode || (S.now && S.now.mode) || "ask-writes";
    var modeSel = p.status === "draft"
      ? '<label class="ag-mode">mode <select onchange="AgentPanel.setPlanMode(\'' + esc(p.id) + '\', this.value)">' +
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
      }
    }
    if (p.pre_ts) {
      acts += ' <a class="btn-sm ag-revert" href="/state-history" hx-get="/state-history" hx-target="#table-pane" hx-push-url="true" title="the chip as it was right before this plan started (State History → restore)">state before this plan</a>';
    }
    el.innerHTML = head + '<table class="ag-steps"><thead><tr><th></th><th>node</th><th>targets</th><th>run</th><th>writes</th><th>why</th></tr></thead><tbody>' + rows + "</tbody></table>" +
      '<details class="ag-may-wrap"' + (p.status === "draft" ? " open" : "") + "><summary>values that may change</summary>" + mayHtml + "</details>" +
      prog + '<div class="ag-plan-acts">' + modeSel + " " + acts + "</div>";
  }

  function renderRun(m, r) {
    var el = cardFor(m, "run", r.key);
    if (!el) return;
    var res = r.result || {};
    var st = r.status;
    var line = "<strong><code>" + esc(r.node) + "</code></strong> " + esc((r.targets || []).join(" ")) +
      ' <span class="ag-step-st ag-st-' + esc(st) + '">' + esc(st === "ended" ? (res.status || "ended") : st) + "</span>" +
      (res.classification && res.classification !== "ok" ? ' <span class="ag-err">' + esc(res.classification) + "</span>" : "") +
      (res.run_id ? " " + runLink(res.run_id) : "") + ' <span class="ag-when">' + esc(fmtClock(r.since)) + "</span>";
    var writes = "";
    if (res.writes && res.writes.length) {
      writes = '<details class="ag-writes"><summary>' + res.writes.length + " write(s) " + (res.applied ? "applied to the chip" : (res.approval ? "waiting for approval" : "not staged")) + "</summary><table>" +
        res.writes.slice(0, 40).map(function (w) { return "<tr><td title=\"" + esc(w.path) + "\">" + esc(w.path) + "</td><td>" + esc(fmtNum(w.old)) + " → " + esc(fmtNum(w.new)) + "</td></tr>"; }).join("") +
        (res.writes.length > 40 ? "<tr><td colspan=2 class=muted>… " + (res.writes.length - 40) + " more</td></tr>" : "") + "</table></details>";
    }
    var err = res.error ? '<div class="ag-err">' + esc(String(res.error).slice(0, 300)) + "</div>" : "";
    var how = r.how ? '<div class="muted ag-how">' + esc(r.how) + "</div>" : "";
    var log = res.log_tail ? '<details class="ag-log"><summary>log tail</summary><pre>' + esc(res.log_tail.slice(-1500)) + "</pre></details>" : "";
    el.innerHTML = '<div class="ag-run-line">' + line + "</div>" + err + writes + how + log;
  }

  function renderApproval(m, a) {
    var el = cardFor(m, "approval", a.id);
    if (!el) return;
    var rows = (a.writes || []).map(function (w, i) {
      return "<tr><td title=\"" + esc(w.path) + "\">" + esc(w.path) + "</td><td>" + esc(fmtNum(w.old)) + "</td><td>" +
        (S.observer ? esc(fmtNum(w.new)) : '<input class="ag-ap-new" data-i="' + i + '" value="' + esc(typeof w.new === "object" ? JSON.stringify(w.new) : w.new) + '">') + "</td></tr>";
    }).join("");
    var acts = S.observer ? '<span class="muted">observing</span>' :
      '<button type="button" class="btn-sm ag-approve" onclick="AgentPanel.approve(\'' + esc(a.id) + '\', this)">Write to chip</button> ' +
      '<button type="button" class="btn-sm ag-reject" onclick="AgentPanel.reject(\'' + esc(a.id) + '\')">Reject</button>';
    el.innerHTML = '<div class="ag-ap-head"><strong>approval</strong> <code>' + esc(a.node || "") + "</code> " + esc((a.targets || []).join(" ")) +
      (a.run_id ? " " + runLink(a.run_id) : "") + ' <span class="muted">' + esc(a.why_held || "") + " · " + esc(fmtClock(a.created)) + "</span></div>" +
      (a.kind === "run" ? '<p class="muted">the agent asks to RUN this node (mode ask-all)</p>' :
        '<table class="ag-ap-rows"><thead><tr><th>value</th><th>now</th><th>proposed (editable)</th></tr></thead><tbody>' + rows + "</tbody></table>") +
      (a.reason ? '<p class="ag-because">because: ' + esc(a.reason) + "</p>" : "") + '<div class="ag-ap-acts">' + acts + "</div>";
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
    lines.push('<div class="ag-now-line"><a href="/journal" hx-get="/journal" hx-target="#table-pane" hx-push-url="true">Calibration log →</a></div>');
    host.innerHTML = lines.join("");
  }

  function renderAll() {
    S.mounts.forEach(function (m) {
      Object.keys(S.plans).forEach(function (k) { renderPlan(m, S.plans[k]); });
      Object.keys(S.runs).forEach(function (k) { renderRun(m, S.runs[k]); });
      Object.keys(S.approvals).forEach(function (k) { renderApproval(m, S.approvals[k]); });
      renderNow(m);
    });
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
    S.mounts.forEach(function (m) {
      (d.cards || []).forEach(function (c) {
        if (c.n > S.after || !S.seenCards[m.id + ":" + c.n]) { renderChatCard(m, c); S.seenCards[m.id + ":" + c.n] = 1; }
      });
    });
    if (typeof d.last === "number" && d.last > S.after) S.after = d.last;
    renderAll();
    S.mounts.forEach(function (m) { var host = cardsHost(m); if (host && m.autoscroll !== false) host.scrollTop = host.scrollHeight; });
    S.mounts.forEach(function (m) { var q = m.root.querySelector(".ag-qubits"); if (q && d.qubits != null) q.textContent = d.qubits + " qubits"; });
    if (window.htmx) S.mounts.forEach(function (m) { try { window.htmx.process(m.root); } catch (e) { /* ignore */ } });
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
      if (Date.now() - S.lastPoll >= gap) poll();
    }, 1000);
  }
  document.addEventListener("sm:runs-changed", function (e) {
    var seq = e && e.detail && e.detail.agent_seq;
    if (typeof seq === "number" && seq === S.seq) return;
    poll();
  });

  // ------------------------------------------------------------ actions
  function submit(ev) {
    if (ev) ev.preventDefault();
    var m = S.mounts[0];
    var root = (ev && ev.target && ev.target.closest && ev.target.closest(".ag-root")) || (m && m.root);
    var ta = root && root.querySelector(".ag-input");
    if (!ta) return false;
    var text = ta.value.trim();
    if (!text) return false;
    var sel = root.querySelector(".ag-backend");
    var backend = (sel && sel.value) || S.defaultBackend;
    ta.disabled = true;
    var done = function () { ta.disabled = false; ta.value = ""; ta.focus(); poll(true); };
    if (text.indexOf("/run") === 0) {
      api("POST", "/api/agent/plans", { run_line: text }).then(function (r) {
        if (r.status !== 200) toast(r.body.error || "could not make the plan", "error"); else toast("plan card ready — press Start when you mean it");
        done();
      });
      return false;
    }
    var live = S.session && S.session.session;
    var p = (live && live.alive && !live.ended) ? api("POST", "/api/agent/chat/send", { text: text })
      : api("POST", "/api/agent/chat/start", { prompt: text, backend: backend });
    p.then(function (r) {
      if (r.status !== 200) toast((r.body && (r.body.error || r.body.message)) || "the agent did not start", "error");
      done();
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
      if (r.status !== 200) toast((r.body && r.body.error) || "could not start", "error"); else toast("started — the agent is running the plan");
      poll(true);
    });
  }
  function cancelPlan(id) { api("POST", "/api/agent/plans/" + id + "/cancel", {}).then(function () { poll(true); }); }
  function setPlanMode(id, mode) {
    api("POST", "/api/agent/plans/" + id + "/mode", { mode: mode }).then(function (r) {
      if (r.status !== 200) toast((r.body && r.body.error) || "mode not set", "error");
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
    api("POST", "/api/agent/approvals/" + id + "/approve", writes ? { writes: writes } : {}).then(function (r) {
      if (r.status !== 200) toast((r.body && (r.body.error || r.body.how)) || "not applied", "error"); else toast("written to the chip");
      poll(true);
    });
  }
  function reject(id) {
    var note = window.prompt ? (window.prompt("Reject — a note for the journal (optional):") || "") : "";
    api("POST", "/api/agent/approvals/" + id + "/reject", { note: note }).then(function () { poll(true); });
  }
  function stop(mode) {
    if (S.observer) return;
    if (mode === "now" && window.confirm && !window.confirm("Stop now: the agent process is killed and the running node is cancelled. The OPX finishes its current sequence; SM's chip writes are atomic; the current run folder may be incomplete. Continue?")) return;
    api("POST", "/api/agent/session/stop", { mode: mode }).then(function (r) {
      if (r.status !== 200) toast((r.body && r.body.error) || "no session", "error");
      poll(true);
    });
  }
  function arm() { api("POST", "/api/agent/session/arm", {}).then(function (r) { if (r.status !== 200) toast((r.body && r.body.error) || "not armed", "error"); poll(true); }); }
  function disarm() { api("POST", "/api/agent/session/disarm", {}).then(function () { poll(true); }); }
  function endSession() { api("POST", "/api/agent/chat/end", {}).then(function () { poll(true); }); }

  // -------------------------------------------------------------- mount
  function skeleton(compact) {
    return '<div class="ag-root' + (compact ? " ag-compact" : "") + '">' +
      '<div class="ag-left"><div class="ag-head"><strong class="ag-chip"></strong> <span class="ag-qubits muted"></span></div>' +
      '<div class="ag-cards" aria-live="polite"></div>' +
      '<form class="ag-form" onsubmit="return AgentPanel.submit(event)">' +
      '<textarea class="ag-input" rows="2" placeholder="What do you want to know, or what should the agent do?   (/run <node> <targets> k=v makes a plan card directly)"></textarea>' +
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

  return { mount: mount, poll: poll, submit: submit, preset: preset, startPlan: startPlan, cancelPlan: cancelPlan,
           setPlanMode: setPlanMode, approve: approve, reject: reject, stop: stop, arm: arm, disarm: disarm,
           endSession: endSession, setObserver: setObserver, toggleFloat: toggleFloat, init: init, absorb: absorb,
           _state: S, fmtNum: fmtNum };
})();
