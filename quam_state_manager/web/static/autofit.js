/* Autofit page (docs/56) — plan bar, live board, review queue, report.
 * Server state is the truth: everything renders from GET /autofit/status.
 * House style: vanilla JS, no framework; survives htmx re-swaps via
 * autofitInit() re-entry (interval handles are window-scoped + cleared). */
"use strict";

(function () {
  var POLL_MS = 1500;
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;"}[c];
    });
  };

  function $(id) { return document.getElementById(id); }

  function fetchJSON(url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (body) {
        return {status: r.status, body: body};
      });
    }).catch(function (e) { return {status: 0, body: {error: String(e)}}; });
  }

  // ---------------- init / poll lifecycle ----------------
  window.autofitInit = function () {
    if (window._autofitPoll) { clearInterval(window._autofitPoll); }
    if (!$("autofit-board")) return;          // navigated away
    fillTargets();
    onPresetOrBackendChange();
    var preset = $("autofit-preset"), backend = $("autofit-backend");
    if (preset) preset.onchange = onPresetOrBackendChange;
    if (backend) backend.onchange = onPresetOrBackendChange;
    poll();
    window._autofitPoll = setInterval(poll, POLL_MS);
  };

  function readiness() {
    var el = $("autofit-readiness");
    try { return JSON.parse(el.dataset.readiness || "{}"); }
    catch (e) { return {}; }
  }

  function fillTargets() {
    var sel = $("autofit-targets");
    if (!sel) return;
    var r = readiness();
    var preset = ($("autofit-preset") || {}).value || "";
    var isPairs = preset.indexOf("cz") === 0;
    var names = isPairs ? (r.qubit_pairs || []) : (r.qubits || []);
    sel.innerHTML = names.map(function (n) {
      return '<option value="' + esc(n) + '">' + esc(n) + "</option>";
    }).join("");
  }

  function onPresetOrBackendChange() {
    fillTargets();
    var backend = ($("autofit-backend") || {}).value;
    var resDiv = $("autofit-resolution");
    if (backend === "real") { resolveSteps(); }
    else if (resDiv) { resDiv.hidden = true; resDiv.innerHTML = ""; }
  }

  // ---------------- step→file resolution table ----------------
  function resolveSteps() {
    var resDiv = $("autofit-resolution");
    if (!resDiv) return;
    var preset = ($("autofit-preset") || {}).value;
    fetchJSON("/autofit/resolve", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({preset: preset}),
    }).then(function (r) {
      if (r.status !== 200 || !r.body.ok) {
        resDiv.hidden = false;
        resDiv.innerHTML = '<p class="autofit-bad">' +
          esc(r.body.error || "resolution failed") + "</p>";
        return;
      }
      var res = r.body.resolution, plan = r.body.plan;
      var rows = (plan.steps || []).map(function (s) {
        var e = res[s.id] || {};
        var cls = e.status === "resolved" ? "ok"
          : e.status === "ambiguous" ? "warn" : "bad";
        var cell;
        if (e.status === "missing") {
          cell = '<span class="autofit-bad">no matching node file</span>';
        } else if ((e.candidates || []).length > 1) {
          cell = '<select data-step="' + esc(s.id) + '" class="autofit-filepick">' +
            e.candidates.map(function (c) {
              return '<option value="' + esc(c) + '"' +
                (c === e.path ? " selected" : "") + ">" +
                esc(c.split(/[\\/]/).pop()) + "</option>";
            }).join("") + "</select>";
        } else {
          cell = "<code>" + esc((e.path || "").split(/[\\/]/).pop()) + "</code>";
        }
        return "<tr><td>" + esc(s.label || s.id) + "</td><td>" + cell +
          '</td><td class="autofit-' + cls + '">' + esc(e.status || "?") +
          "</td></tr>";
      }).join("");
      resDiv.hidden = false;
      resDiv.innerHTML = '<table class="data-table autofit-restable">' +
        "<thead><tr><th>Step</th><th>Node file</th><th></th></tr></thead>" +
        "<tbody>" + rows + "</tbody></table>";
    });
  }

  function pickedFiles() {
    var out = {};
    document.querySelectorAll(".autofit-filepick").forEach(function (sel) {
      out[sel.dataset.step] = sel.value;
    });
    return out;
  }

  // ---------------- run / abort ----------------
  window.autofitRun = function () {
    var msg = $("autofit-planbar-msg");
    var targets = Array.prototype.map.call(
      ($("autofit-targets") || {selectedOptions: []}).selectedOptions,
      function (o) { return o.value; });
    var payload = {
      preset: ($("autofit-preset") || {}).value,
      autonomy: ($("autofit-autonomy") || {}).value,
      backend: ($("autofit-backend") || {}).value,
      targets: targets.length ? targets : null,
      step_files: pickedFiles(),
    };
    if (payload.targets === null) delete payload.targets;
    msg.textContent = "starting…";
    fetchJSON("/autofit/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (r.status === 200 && r.body.ok) {
        msg.textContent = "running (" + esc(r.body.backend) + ")";
      } else if (r.body && r.body.reason === "preflight") {
        msg.innerHTML = '<span class="autofit-bad">preflight failed</span> ' +
          '<button class="btn-sm outline" onclick="autofitForce()">Run anyway</button>';
        window._autofitLastPayload = payload;
      } else {
        msg.innerHTML = '<span class="autofit-bad">' +
          esc((r.body || {}).error || "start failed") + "</span>";
      }
      poll();
    });
  };

  window.autofitForce = function () {
    var payload = window._autofitLastPayload || {};
    payload.force = true;
    fetchJSON("/autofit/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    }).then(function () { poll(); });
  };

  window.autofitAbort = function () {
    fetchJSON("/autofit/abort", {method: "POST"}).then(poll);
  };

  // ---------------- status poll + rendering ----------------
  var CELL_ICON = {
    pass: "✓", applied: "✓", corrected: "↻", retrying: "…",
    running: "●", reverted: "⤺", deferred: "?", aborted: "✕", skipped: "–",
  };

  function poll() {
    if (!$("autofit-board")) {                 // page swapped away
      clearInterval(window._autofitPoll);
      window._autofitPoll = null;
      return;
    }
    fetchJSON("/autofit/status").then(function (r) {
      if (r.status !== 200) return;
      var st = r.body.state;
      var active = !!r.body.active;
      var runBtn = $("autofit-run"), abortBtn = $("autofit-abort");
      if (runBtn) runBtn.disabled = active;
      if (abortBtn) abortBtn.hidden = !active;
      if (!st) return;
      renderBoard(st, active);
      renderReview(st);
      if (!active && st.status && st.status !== "running") {
        renderReport(st.plan_run_id || "");
      }
    });
  }

  function renderBoard(st, active) {
    var board = $("autofit-board");
    if (!board) return;
    var plan = st.plan || {};
    var steps = (plan.steps || []).filter(function (s) { return s.enabled !== false; });
    var targets = st.targets || [];
    if (!steps.length) return;
    var cur = st.current || {};
    var head = "<tr><th></th>" + targets.map(function (t) {
      var halted = (st.halted || {})[t];
      return "<th" + (halted ? ' class="autofit-halted" title="' +
        esc(halted) + '"' : "") + ">" + esc(t) + "</th>";
    }).join("") + "</tr>";
    var rows = steps.map(function (s) {
      var cells = targets.map(function (t) {
        var c = ((st.board || {})[s.id] || {})[t];
        if (!c) return '<td class="autofit-cell-pending">·</td>';
        var cls = "autofit-cell-" + c.state;
        var icon = CELL_ICON[c.state] || c.state;
        var tip = (c.detail || c.state) +
          (c.attempts > 1 ? " (attempt " + c.attempts + ")" : "");
        return '<td class="' + cls + '" title="' + esc(tip) + '">' + icon +
          (c.attempts > 1 ? '<sub>' + c.attempts + "</sub>" : "") + "</td>";
      }).join("");
      var mark = cur.step_id === s.id ? " ▸" : "";
      return "<tr><td>" + esc(s.label || s.id) + mark + "</td>" + cells + "</tr>";
    }).join("");
    var status = st.status +
      (cur.step_id ? " — " + esc(cur.step_id) +
        " (attempt " + ((cur.attempt || 0) + 1) + ")" : "") +
      (st.llm_calls ? " · LLM calls: " + st.llm_calls : "");
    board.innerHTML = '<p class="autofit-runline autofit-run-' +
      esc(st.status) + '">' + esc(st.autonomy || "") + " · " + status +
      "</p><table class='data-table autofit-board-table'>" +
      "<thead>" + head + "</thead><tbody>" + rows + "</tbody></table>";
  }

  function renderReview(st) {
    var div = $("autofit-review"), count = $("autofit-review-count");
    if (!div) return;
    var q = st.review_queue || [];
    if (count) count.textContent = q.length ? "(" + q.length + ")" : "";
    if (!q.length) {
      div.innerHTML = '<p class="muted">Nothing deferred.</p>';
      return;
    }
    div.innerHTML = q.map(function (item) {
      var v = item.verdict || {};
      return '<div class="autofit-review-card">' +
        "<strong>" + esc(item.step_id) + " · " + esc(item.target) + "</strong> " +
        '<span class="autofit-chip">' + esc(item.failure_mode || "?") + "</span>" +
        (item.reverted ? ' <span class="autofit-chip autofit-chip-rev">reverted</span>' : "") +
        '<div class="muted">' + esc(item.reason || "") + "</div>" +
        ((v.reasons || []).slice(0, 3).map(function (rr) {
          return '<div class="autofit-verdict-line">· ' + esc(rr) + "</div>";
        }).join("")) + "</div>";
    }).join("");
  }

  function renderReport(runId) {
    var div = $("autofit-report");
    // keyed by plan_run_id so run #2's completion re-renders (never sticky)
    if (!div || div.dataset.rendered === runId) return;
    fetchJSON("/autofit/ledger").then(function (r) {
      if (r.status !== 200 || !r.body.ok) return;
      var writes = [], reverts = [], llm = [];
      (r.body.events || []).forEach(function (e) {
        if (e.event === "write_applied" && e.ok) writes.push(e);
        if (e.event === "revert_applied" && e.ok) reverts.push(e);
        if (e.event === "llm_verdict") llm.push(e);
      });
      function pathRows(evts) {
        return evts.map(function (e) {
          return (e.paths || []).map(function (p) {
            return "<tr><td>" + esc(e.step || "") + "</td><td><code>" +
              esc(p.path) + "</code></td><td>" + esc(fmt(p.old)) +
              " → <strong>" + esc(fmt(p.new)) + "</strong></td><td>" +
              esc(e.group_id || "") + "</td></tr>";
          }).join("");
        }).join("");
      }
      div.dataset.rendered = runId;
      div.innerHTML =
        "<h6>Applied (" + writes.length + ")</h6>" +
        (writes.length ? "<table class='data-table'><thead><tr><th>step</th>" +
          "<th>path</th><th>old → new</th><th>group</th></tr></thead><tbody>" +
          pathRows(writes) + "</tbody></table>" : '<p class="muted">none</p>') +
        "<h6>Reverted (" + reverts.length + ")</h6>" +
        (reverts.length ? "<table class='data-table'><tbody>" +
          pathRows(reverts) + "</tbody></table>" : '<p class="muted">none</p>') +
        "<h6>LLM verdicts (" + llm.length + ")</h6>" +
        (llm.length ? llm.map(function (e) {
          return '<div class="autofit-verdict-line">' + esc(e.target) + ": " +
            "<strong>" + esc(e.verdict) + "</strong> — " + esc(e.reason || "") +
            "</div>";
        }).join("") : '<p class="muted">none (deterministic gates only)</p>');
    });
  }

  function fmt(v) {
    if (typeof v === "number" && isFinite(v)) {
      if (Math.abs(v) >= 1e6) return (v / 1e6).toPrecision(8) / 1 + "M";
      return Number(v.toPrecision(8));
    }
    return v;
  }
})();
