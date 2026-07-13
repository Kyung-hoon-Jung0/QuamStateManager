/* Experiment Scheduler — front-end (setup · pre-flight · queue · runner).
 *
 * Reuses the Generate-Config env endpoints (/generate/envs|probe|select-env)
 * and the Scheduler endpoints (/scheduler/{effective-config,preflight,settings,
 * register-storage,scan,scan-params,status,log,start,pause,cancel} +
 * /scheduler/queue/{add,remove,reorder,toggle,targets,duplicate,expand,params,
 * clear-finished}). Idempotent init guarded by `_schedInit`, wired to both
 * DOMContentLoaded and htmx:afterSwap so it survives HTMX page swaps (same
 * pattern as generate.js). See docs/40_scheduler.md.
 */
(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }

  var _activePoll = null;   // module-level so a swap-away can clear the prior timer

  function fetchJSON(url, opts) {
    // Resolve (never reject) so callers' .then always runs — clears spinners and
    // avoids unhandled-rejection spam when the server is unreachable. A network
    // failure yields {__neterr:true}; the read-only callers (config/preflight/
    // scan) gate on r.ok/r.checks/r.items so they no-op, and renderQueue early-
    // returns on the sentinel so a dropped poll never blanks the live queue.
    return fetch(url, opts)
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .catch(function () { return { __neterr: true }; });
  }
  function postJSON(url, body) {
    return fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  var STATUS_ICON = { pass: "✓", fail: "✗", warn: "⚠", skip: "·" };

  function SchedulerUI(root) {
    this.root = root;
    this.settings = {};
    try {
      var boot = el("scheduler-bootstrap");
      if (boot) this.settings = JSON.parse(boot.textContent) || {};
    } catch (e) { this.settings = {}; }
  }

  SchedulerUI.prototype.start = function () {
    var self = this;
    this.loadEnvs();
    this.wireInputs();
    this.wirePresets();

    var readBtn = el("sched-read-config");
    if (readBtn) readBtn.addEventListener("click", function () { self.readConfig(); });
    var preBtn = el("sched-preflight-btn");
    if (preBtn) preBtn.addEventListener("click", function () { self.preflight(); });
    var probeBtn = el("sched-env-probe-btn");
    if (probeBtn) probeBtn.addEventListener("click", function () { self.probeTypedEnv(); });
    var useOpen = el("sched-quam-use-open");
    if (useOpen) useOpen.addEventListener("click", function () {
      var v = self.root.getAttribute("data-open-chip") || "";
      var inp = el("sched-quam-state");
      if (inp) { inp.value = v; self.persist(); }
    });

    this.roster = { qubits: [], pairs: [] };
    this._lastQueue = [];
    this.paramSchemas = {};   // node name -> {parameters, targets_name, description}
    this._dragging = false;
    var scanBtn = el("sched-scan-btn");
    if (scanBtn) scanBtn.addEventListener("click", function () { self.scanLibrary(); });
    var loadParamsBtn = el("sched-loadparams-btn");
    if (loadParamsBtn) loadParamsBtn.addEventListener("click", function () { self.scanParams(); });
    [["sched-start", "start"], ["sched-pause", "pause"], ["sched-cancel", "cancel"]].forEach(function (pair) {
      var b = el(pair[0]);
      if (b) b.addEventListener("click", function () { self.runControl(pair[1]); });
    });
    var clearBtn = el("sched-clear");
    if (clearBtn) clearBtn.addEventListener("click", function () { self.queueAction("clear-finished", {}); });
    this.updateDryNote();
    var libFilter = el("sched-lib-filter");
    if (libFilter) libFilter.addEventListener("input", function () { self.renderLibrary(); });

    if ((el("sched-cal-folder") || {}).value) this.scanLibrary();
    this._installGlobalHandlers();
    this._observeQueueWidth();
    this.startPolling();
  };

  // Installed once at document level (survives HTMX re-inits via a static flag):
  // dismiss the ⋯ overflow menu on an outside-click / Escape (+ single-open).
  SchedulerUI.prototype._installGlobalHandlers = function () {
    if (SchedulerUI._globalsInstalled) return;
    SchedulerUI._globalsInstalled = true;
    function closeMenus(except) {
      document.querySelectorAll("details.sched-overflow[open]").forEach(function (d) {
        if (!except || !d.contains(except)) d.removeAttribute("open");
      });
    }
    document.addEventListener("click", function (e) { closeMenus(e.target); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenus(null); });
  };

  // Re-measure name-truncation tooltips whenever the queue's WIDTH changes — a
  // window resize, a sidebar collapse, or a Split.js pane drag — robustly via
  // ResizeObserver (no coupling to those specific events).
  SchedulerUI.prototype._observeQueueWidth = function () {
    var self = this, box = el("sched-queue");
    if (!box || !window.ResizeObserver) return;
    if (this._ro) this._ro.disconnect();
    this._ro = new ResizeObserver(debounce(function () { self._applyNameTooltips(); }, 100));
    this._ro.observe(box);
  };

  // Instant full-name tooltip (Pico) only where the name actually truncates.
  SchedulerUI.prototype._applyNameTooltips = function (box) {
    box = box || el("sched-queue");
    if (!box) return;
    box.querySelectorAll(".sched-item-name-text").forEach(function (n) {
      if (n.scrollWidth > n.clientWidth + 1) n.setAttribute("data-tooltip", n.textContent);
      else n.removeAttribute("data-tooltip");
    });
  };

  SchedulerUI.prototype.updateDryNote = function () {
    var dn = el("sched-dry-note");
    if (!dn) return;
    var dry = !!(el("sched-simulate") || {}).checked;
    dn.textContent = dry ? "Dry run ON — no hardware" : "⚠ LIVE — will drive real hardware";
    dn.className = "sched-dry-note " + (dry ? "sched-dry-on" : "sched-dry-live");
  };

  /* --- library (scan) --------------------------------------------------- */

  SchedulerUI.prototype.scanLibrary = function () {
    var self = this;
    if (self._scanning) return;                       // ignore a double-click
    var folder = (el("sched-cal-folder") || {}).value || "";
    var info = el("sched-scan-info");
    if (!folder) { if (info) info.textContent = "Set a calibrations folder first."; return; }
    self._scanning = true;
    var btn = el("sched-scan-btn");
    if (btn) btn.disabled = true;
    if (info) info.textContent = "Scanning…";
    var finish = function () { self._scanning = false; if (btn) btn.disabled = false; };
    fetchJSON("/scheduler/scan?folder=" + encodeURIComponent(folder)).then(function (r) {
      if (r && r.__neterr) { if (info) info.textContent = "Scan failed — server unreachable."; return; }
      self.roster = { qubits: (r && r.qubits) || [], pairs: (r && r.pairs) || [] };
      var items = (r && r.items) || [];
      if (info) info.textContent = items.length + " file(s) · " + self.roster.qubits.length + " qubits / "
        + self.roster.pairs.length + " pairs in open chip";
      self.renderLibrary(items);
    }).then(finish, finish);                          // re-enable on success OR error
  };

  SchedulerUI.prototype.renderLibrary = function (items) {
    var self = this;
    if (items) self._libItems = items;            // cache so the filter can re-render
    items = self._libItems || [];
    var box = el("sched-library");
    if (!box) return;
    var q = ((el("sched-lib-filter") || {}).value || "").toLowerCase().trim();
    var shown = items.filter(function (it) {
      if (it.kind === "other") return false;
      if (!q) return true;
      return (it.name + " " + (it.description || "")).toLowerCase().indexOf(q) >= 0;
    });
    if (!shown.length) {
      box.innerHTML = '<p class="muted">' + (items.length ? "No matching .py files." : "No .py files found.") + "</p>";
      return;
    }
    box.innerHTML = "";
    shown.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "sched-lib-row";
      var hook = (it.kind === "node" && !it.has_hook)
        ? '<span class="sched-badge sched-badge-warn" title="no custom_param hook — runs with defaults">no-override</span>' : "";
      row.innerHTML =
        '<span class="sched-badge sched-badge-' + esc(it.kind) + '">' + esc(it.kind) + "</span>" + hook +
        '<span class="sched-lib-name">' + esc(it.name) + "</span>" +
        '<span class="sched-lib-desc muted">' + esc(it.description || "") + "</span>" +
        '<button type="button" class="btn-sm sched-lib-add" aria-label="Add ' + esc(it.name)
          + ' to queue">+ Add</button>';
      row.querySelector(".sched-lib-add").addEventListener("click", function (e) {
        self.addToQueue(it, e.currentTarget);
      });
      box.appendChild(row);
    });
  };

  SchedulerUI.prototype.addToQueue = function (it, btn) {
    var self = this;
    if (btn) btn.disabled = true;                     // no double-add while in flight
    var done = function () { if (btn) btn.disabled = false; };
    var afterId = self._insertAfterId || null;        // armed by "Insert node after…"
    self._insertAfterId = null;                       // one-shot
    postJSON("/scheduler/queue/add", {
      file: it.file, name: it.name, kind: it.kind,
      has_hook: it.has_hook, targets_name: it.targets_name, targets: [],
      after_id: afterId,
    }).then(function (r) {
      if (r && r.state) { self._queueSig = null; self.renderQueue(r.state); }
      if (afterId && window.showToast) window.showToast("Inserted after the marked step.", "info");
    }).then(done, done);
  };

  /* --- queue + polling -------------------------------------------------- */

  SchedulerUI.prototype.startPolling = function () {
    var self = this;
    // Clear any interval orphaned by a prior /scheduler visit (HTMX swaps in a
    // fresh #sched-root + a new SchedulerUI, leaving the old timer running).
    if (_activePoll) { clearInterval(_activePoll); _activePoll = null; }
    function tick() {
      if (!document.getElementById("sched-root")) {       // page swapped away
        clearInterval(_activePoll); _activePoll = null; return;
      }
      if (document.hidden) return;   // hidden tab: skip — the base.html global
                                     // poll (2.5s) still carries the heartbeat
      fetchJSON("/scheduler/status").then(function (state) {
        if (document.getElementById("sched-root")) self.renderQueue(state);
      });
    }
    tick();
    _activePoll = setInterval(tick, 1500);
  };

  SchedulerUI.prototype.runControl = function (which) {
    var self = this;
    if (which === "start") {
      var enabled = (self._lastQueue || []).filter(function (i) {
        return i.enabled && i.status === "queued";
      }).length;
      if (!enabled) { alert("No enabled, queued experiments to run."); return; }
      var dry = !!(el("sched-simulate") || {}).checked;
      var headless = !!(el("sched-continue-ui") || {}).checked;
      // Confirm whenever it's a LIVE run OR could silently pause overnight; a
      // dry run with tmux mode can start without friction.
      if (!dry || !headless) {
        var msg = (dry ? "Dry run — no hardware.\n"
                       : "⚠ LIVE RUN — this will drive REAL hardware on the chip.\n")
          + enabled + " enabled experiment(s) will run.\n";
        if (!headless) {
          msg += "\nNote: the queue PAUSES if this browser/tab closes or the "
            + "computer sleeps (tmux mode is off — enable it in Run options to run "
            + "unattended).\n";
        }
        if (!window.confirm(msg + "\nStart?")) return;
      }
    }
    // For Start, send the live form values so the server-side preflight checks
    // exactly what the user sees (not stale persisted settings).
    var body = which === "start" ? self.collect() : {};
    postJSON("/scheduler/" + which, body).then(function (r) {
      // Strict gate: the server refuses Start when the safety preflight fails.
      // Surface the failing checks and let the user force past them.
      if (which === "start" && r && r.ok === false && r.reason === "preflight") {
        var pre = r.preflight || {};
        var fails = (pre.checks || []).filter(function (c) {
          return c.status === "fail";
        }).map(function (c) { return "  • " + (c.label || c.key) + ": " + (c.detail || ""); });
        var txt = "⚠ Preflight failed — the run was NOT started:\n\n"
          + (fails.join("\n") || "  • (see the Verify panel)")
          + "\n\nStart ANYWAY (override the safety checks)?";
        if (window.confirm(txt)) {
          body.force = true;
          postJSON("/scheduler/start", body).then(function (r2) {
            if (r2 && r2.state) self.renderQueue(r2.state);
          });
        }
        return;
      }
      if (r && r.state) self.renderQueue(r.state);
    });
  };

  // h:mm:ss / m:ss elapsed since an ISO timestamp (local single-user clock).
  SchedulerUI.prototype._fmtElapsed = function (iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return "";
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return h > 0 ? h + ":" + p(m) + ":" + p(ss) : m + ":" + p(ss);
  };

  SchedulerUI.prototype.queueAction = function (action, body) {
    var self = this;
    return postJSON("/scheduler/queue/" + action, body || {}).then(function (r) {
      if (r && r.state) self.renderQueue(r.state);
      return r;   // callers (e.g. the param-editor Save) inspect ok/state
    });
  };

  SchedulerUI.prototype.moveItem = function (idx, delta) {
    var ids = (this._lastQueue || []).map(function (x) { return x.id; });
    var j = idx + delta;
    if (j < 0 || j >= ids.length) return;
    var t = ids[idx]; ids[idx] = ids[j]; ids[j] = t;
    this.queueAction("reorder", { order: ids });
  };

  SchedulerUI.prototype.itemAction = function (it, idx, act) {
    var self = this;
    if (act === "remove") return self.queueAction("remove", { id: it.id });
    if (act === "dup") return self.queueAction("duplicate", { id: it.id });
    if (act === "params") return self.openParamEditor(it);
    if (act === "result") {
      if (it.result_ref && it.result_ref.uid) window.location.href = "/dataset/" + it.result_ref.uid;
      return;
    }
    if (act === "up") return self.moveItem(idx, -1);
    if (act === "down") return self.moveItem(idx, 1);
    if (act === "insert") {
      // Arm insert-after mode: the next "+ Add" from the library lands right
      // after this row instead of at the end. Visually park a marker on the row.
      self._insertAfterId = it.id;
      var box = el("sched-queue");
      if (box) box.querySelectorAll(".sched-item-insert-anchor").forEach(function (r) {
        r.classList.remove("sched-item-insert-anchor");
      });
      var rowEl = box && box.querySelector('[data-id="' + it.id + '"]');
      if (rowEl) rowEl.classList.add("sched-item-insert-anchor");
      var lib = el("sched-lib");
      if (lib) lib.scrollIntoView({ behavior: "smooth", block: "center" });
      if (window.showToast) window.showToast(
        "Insert mode: the next “+ Add” will land after “" + (it.label || it.name) + "”.", "info");
      return;
    }
    if (act === "rules") return self.openRulesEditor(it);
    if (act === "label") {
      var v = window.prompt("Step label (empty to clear):", it.label || "");
      if (v === null) return;
      return self.queueAction("set-label", { id: it.id, label: v.trim() });
    }
    if (act === "expand") {
      var roster = it.targets_name === "qubit_pairs" ? self.roster.pairs : self.roster.qubits;
      if (!roster || !roster.length) { alert("No " + it.targets_name + " in the open chip to expand over."); return; }
      if (it.kind === "graph" && !window.confirm(
            "Expand will run the FULL graph " + roster.length + " times (once per " +
            it.targets_name + "), not once across all of them. Continue?")) return;
      return self.queueAction("expand", { id: it.id, targets: roster });
    }
    if (act === "log") return self.viewLog(it.id);
  };

  SchedulerUI.prototype._showLog = function (id, placeholder) {
    fetchJSON("/scheduler/log?id=" + encodeURIComponent(id)).then(function (r) {
      var logEl = el("sched-log");
      if (!logEl) return;
      logEl.hidden = false;
      logEl.textContent = (r && r.log) || placeholder;
    });
  };

  SchedulerUI.prototype.viewLog = function (id) {
    this._logPinId = id;   // pin so the running-node auto-follow won't clobber it
    this._showLog(id, "(no output)");
  };

  SchedulerUI.prototype.renderQueue = function (state) {
    var self = this;
    state = state || {};
    // A dropped poll (fetchJSON network sentinel) must NOT blank the live UI —
    // keep the last-good render until the next good poll (self-heals in 1.5s).
    if (state.__neterr) return;
    var q = state.queue || [];
    var run = state.run || {};
    self._lastQueue = q;

    var running = run.status === "running";
    // The currently-executing item (for live progress on the run-state line).
    var cur = running && run.current_id
      ? q.filter(function (i) { return i.id === run.current_id; })[0] : null;

    // run-state line + controls — cheap, update every poll
    var rs = el("sched-run-state");
    if (rs) {
      var msg = run.status || "idle";
      if (cur) {
        var done = (run.completed_count || 0);
        var totalEnabled = q.filter(function (i) { return i.enabled; }).length;
        // Denominator clamps so disabling queued items mid-run can't read "5/4".
        var denom = Math.max(totalEnabled, done + 1);
        msg += " — " + (cur.name || "node")
          + (cur.targets && cur.targets.length ? " [" + cur.targets.join(",") + "]" : "")
          + " · " + self._fmtElapsed(cur.started_at)
          + " · " + (done + 1) + "/" + denom;
      } else if (run.message) {
        msg += " — " + run.message;
      }
      rs.textContent = msg;
      // A failure-stop is visually distinct from an ordinary pause.
      var failed = /fail|stopped/i.test(run.message || "");
      rs.className = "sched-run-state sched-run-" + (run.status || "idle")
        + (failed ? " sched-run-failed" : "");
    }
    if (el("sched-start")) el("sched-start").disabled = running;
    if (el("sched-pause")) el("sched-pause").disabled = !running;
    if (el("sched-cancel")) el("sched-cancel").disabled = !(running || run.status === "paused");
    // Lock the hardware/chip-affecting settings while a queue runs (the server
    // also 409s them). Un-ticking Dry run or re-pointing the chip/env mid-run
    // would flip the rest of the queue to LIVE hardware with no Strict-gate check.
    ["sched-simulate", "sched-quam-state", "sched-env-path", "sched-cal-folder"]
        .forEach(function (id) { var e = el(id); if (e) e.disabled = running; });

    // list — skip the rebuild while dragging, while a queue field is focused
    // (rebuild would clobber the edit + caret), or when structurally unchanged.
    var box = el("sched-queue");
    var focused = box && document.activeElement && box.contains(document.activeElement);
    var menuOpen = box && box.querySelector("details.sched-overflow[open]");
    if (box && !self._dragging && !focused && !menuOpen) {
      var sig = JSON.stringify(q.map(function (i) {
        return [i.id, i.status, i.order, i.enabled, (i.targets || []).join(","),
                i.param_overrides ? Object.keys(i.param_overrides).length : 0,
                i.result_ref ? 1 : 0,
                i.label || "", (i.on_outcome || []).length,
                i.inserted_by ? 1 : 0, i.outcome_note ? 1 : 0];
      }));
      if (sig !== self._queueSig) {
        self._queueSig = sig;
        if (!q.length) {
          box.innerHTML = '<p class="muted">Queue is empty — add experiments above.</p>';
        } else {
          box.innerHTML = "";
          q.forEach(function (it, idx) { box.appendChild(self.renderItemRow(it, idx, q.length)); });
          self._applyNameTooltips(box);
        }
      }
    }

    // Auto-follow the running node's log — unless the user pinned another via ▤,
    // or the tab is hidden (no point fetching up to 16KB nobody's watching).
    if (el("sched-log") && run.current_id && !document.hidden
        && (!self._logPinId || self._logPinId === run.current_id)) {
      self._showLog(run.current_id, "(no output yet)");
    }
  };

  // VSCode-clean gutter: one mono glyph + a 2px left edge per status
  // (docs/43 — letter-in-gutter, never a filled pill).
  var GUTTER = {
    queued: "·", running: "R", done: "✓", failed: "✗",
    skipped: "–", cancelled: "–",
  };

  SchedulerUI.prototype.renderItemRow = function (it, idx, total) {
    var self = this;
    var row = document.createElement("div");
    row.className = "sched-item sched-item-" + (it.status || "queued");
    row.setAttribute("data-id", it.id);
    var canOverride = it.kind === "node" && it.has_hook;   // node param/target editing
    var canTarget = canOverride || it.kind === "graph";    // graphs: targets only
    var nOver = it.param_overrides ? Object.keys(it.param_overrides).length : 0;
    var draggable = it.status === "queued";   // only reorder pending items
    row.draggable = draggable;
    var nm = esc(it.name);
    var hasTargets = (it.targets || []).length > 0;
    // targets: a compact click-to-edit chip (default "all active" is the common case)
    var targetCell = canTarget
      ? '<span class="sched-item-targets-chip' + (hasTargets ? "" : " is-default")
          + '" tabindex="0" role="button"'
          + ' title="Click to edit targets — default: all active ' + esc(it.targets_name) + '"'
          + ' aria-label="Edit targets for ' + nm + '">'
          + (hasTargets ? esc((it.targets || []).join(", ")) : "all active") + "</span>"
      : '<span class="sched-item-targets-na">defaults</span>';

    // inline icon button (title + aria-label so screen readers announce it)
    function btn(act, label, glyph, extra) {
      return '<button type="button" class="sched-mini" data-act="' + act + '" title="'
        + esc(label) + '" aria-label="' + esc(label) + '"' + (extra || "") + ">" + glyph + "</button>";
    }
    // overflow-menu item — a text label (better a11y than a glyph)
    function menuItem(act, label, extra) {
      return '<button type="button" data-act="' + act + '"' + (extra || "")
        + ' aria-label="' + esc(label) + '">' + esc(label) + "</button>";
    }
    var inlineActions =
      (canOverride ? btn("params", "Edit parameters", "⚙") : "") +
      (it.result_ref && it.result_ref.uid
        ? '<button type="button" class="sched-mini sched-result-link" data-act="result" title="View run #'
          + esc(it.result_ref.run_id) + ' in Datasets" aria-label="View run '
          + esc(it.result_ref.run_id) + ' in Datasets">↗</button>' : "") +
      // No Remove on the running item — its subprocess is driving hardware; the
      // server no-ops it too (removing the row wouldn't kill the run).
      (it.status === "running" ? "" : btn("remove", "Remove", "✕"));
    var overflowMenu =
      '<details class="sched-overflow"><summary class="sched-mini" title="More actions"'
        + ' aria-label="More actions for ' + nm + '">⋯</summary>'
      + '<div class="sched-overflow-menu" role="menu">'
        + menuItem("insert", "Insert node after…")
        // No Expand on the running item — it would delete the live row and enqueue
        // per-target copies that RE-RUN on hardware (server no-ops it too).
        + (canTarget && it.status !== "running" ? menuItem("expand", "Expand per qubit/pair") : "")
        + menuItem("dup", "Duplicate")
        + (canOverride ? menuItem("rules", "On-failure rules…") : "")
        + menuItem("label", it.label ? "Rename label…" : "Add label…")
        + menuItem("log", "View log")
        + menuItem("up", "Move up", idx === 0 ? " disabled" : "")
        + menuItem("down", "Move down", idx === total - 1 ? " disabled" : "")
      + "</div></details>";

    var glyph = GUTTER[it.status || "queued"] || "·";
    var autoChip = it.inserted_by
      ? '<span class="sched-chip-auto" title="Auto-inserted by an on-failure rule of item '
        + esc((it.inserted_by || {}).parent_item || "?") + '">↳ auto</span>' : "";
    var ruleChip = (it.on_outcome || []).length
      ? '<span class="sched-chip-rule" title="Has on-failure rules (⋯ → On-failure rules)">rule</span>' : "";
    var noteChip = it.outcome_note
      ? '<span class="sched-chip-note" title="' + esc(it.outcome_note) + '">note</span>' : "";
    var labelHtml = it.label
      ? '<span class="sched-item-label" title="' + esc(it.label) + '">' + esc(it.label) + "</span>" : "";

    row.innerHTML =
      '<span class="sched-item-gutter" aria-hidden="true">' + glyph + "</span>" +
      '<span class="sched-item-grip" aria-hidden="true" title="Drag to reorder">' + (draggable ? "⠿" : "") + "</span>" +
      '<input type="checkbox" class="sched-item-enabled" ' + (it.enabled ? "checked" : "")
        + ' title="Enable / disable" aria-label="Enable / disable ' + nm + '">' +
      '<span class="sched-item-name"><span class="sched-item-name-text">' + nm + "</span>" +
        labelHtml + autoChip + ruleChip + noteChip +
        (nOver ? '<span class="sched-item-overbadge" title="' + esc(nOver) + ' parameter override(s)">●' + esc(nOver) + "</span>" : "") +
      "</span>" +
      targetCell +
      '<span class="sched-item-status">' + esc(it.status || "queued") + "</span>" +
      '<span class="sched-item-actions">' + inlineActions + overflowMenu + "</span>";

    var en = row.querySelector(".sched-item-enabled");
    if (en) en.addEventListener("change", function () {
      self.queueAction("toggle", { id: it.id, enabled: en.checked });
    });
    if (canTarget) self.wireTargetsChip(row, it);
    // delegate every action (inline + overflow); close the menu after a click
    row.querySelectorAll("[data-act]").forEach(function (b) {
      b.addEventListener("click", function () {
        var d = b.closest("details.sched-overflow"); if (d) d.removeAttribute("open");
        self.itemAction(it, idx, b.getAttribute("data-act"));
      });
    });
    // Flip the ⋯ menu upward when opening it down would clip below the scroll pane.
    var ov = row.querySelector("details.sched-overflow");
    if (ov) ov.addEventListener("toggle", function () {
      if (!ov.open) return;
      ov.classList.remove("sched-overflow-up");
      var menu = ov.querySelector(".sched-overflow-menu");
      var pane = el("table-pane");
      var limit = pane ? pane.getBoundingClientRect().bottom : (window.innerHeight || 1e9);
      if (menu && menu.getBoundingClientRect().bottom > limit) ov.classList.add("sched-overflow-up");
    });
    if (draggable) self.wireDrag(row, it.id);
    return row;
  };

  // Click-to-edit the targets chip: swap to an inline input, save on Enter/blur,
  // cancel on Escape. The server re-render rebuilds the row from authoritative state.
  SchedulerUI.prototype.wireTargetsChip = function (row, it) {
    var self = this;
    var chip = row.querySelector(".sched-item-targets-chip");
    if (!chip) return;
    function openEditor() {
      var input = document.createElement("input");
      input.type = "text";
      input.className = "sched-item-targets-edit";
      input.value = (it.targets || []).join(", ");
      input.placeholder = "all active (" + it.targets_name + ")";
      chip.replaceWith(input);
      input.focus();
      input.select();
      var done = false;
      function commit(save) {
        if (done) return;
        done = true;
        var list = input.value.split(",").map(function (s) { return s.trim(); })
          .filter(function (s) { return s !== ""; });
        var changed = JSON.stringify(list) !== JSON.stringify(it.targets || []);
        input.replaceWith(chip);   // restore the chip → focus leaves, rebuild can proceed
        if (save && changed) {     // skip a redundant POST/flicker when nothing changed
          self.queueAction("targets", { id: it.id, targets: list }).then(function (r) {
            if (!(r && r.state) && window.showToast) {
              window.showToast("Couldn't save targets — try again.", "error");
            }
          });
        }
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); commit(true); }
        else if (e.key === "Escape") { e.preventDefault(); commit(false); }
      });
      input.addEventListener("blur", function () { commit(true); });
    }
    chip.addEventListener("click", openEditor);
    chip.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openEditor(); }
    });
  };

  SchedulerUI.prototype.wireDrag = function (row, id) {
    var self = this;
    row.addEventListener("dragstart", function (e) {
      self._dragging = true; self._dragId = id;
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", id); } catch (err) {}
      row.classList.add("sched-item-dragging");
    });
    row.addEventListener("dragend", function () {
      self._dragging = false; self._dragId = null;
      row.classList.remove("sched-item-dragging");
      var box = el("sched-queue");
      if (box) box.querySelectorAll(".sched-item-dragover").forEach(function (r) {
        r.classList.remove("sched-item-dragover");
      });
    });
    row.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      row.classList.add("sched-item-dragover");
    });
    row.addEventListener("dragleave", function () { row.classList.remove("sched-item-dragover"); });
    row.addEventListener("drop", function (e) {
      e.preventDefault();
      row.classList.remove("sched-item-dragover");
      var dragId = self._dragId || (e.dataTransfer && e.dataTransfer.getData("text/plain"));
      if (!dragId || dragId === id) { self._dragging = false; return; }
      var ids = (self._lastQueue || []).map(function (x) { return x.id; });
      var from = ids.indexOf(dragId), to = ids.indexOf(id);
      if (from < 0 || to < 0) { self._dragging = false; return; }
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      self._dragging = false;
      self.queueAction("reorder", { order: ids });
    });
  };

  /* --- settings persistence --------------------------------------------- */

  SchedulerUI.prototype.collect = function () {
    function val(id) { var e = el(id); return e ? e.value : ""; }
    function chk(id) { var e = el(id); return !!(e && e.checked); }
    return {
      calibrations_folder: val("sched-cal-folder").trim(),
      env_python: (val("sched-env-path") || this.settings.env_python || "").trim(),
      quam_state_path: val("sched-quam-state").trim(),
      failure_policy: val("sched-failure") || "stop",
      global_simulate: chk("sched-simulate"),
      continue_without_ui: chk("sched-continue-ui"),
      default_timeout_s: parseInt(val("sched-timeout"), 10) || 0,
    };
  };

  SchedulerUI.prototype.persist = function () {
    var s = this.collect();
    this.settings.env_python = s.env_python;
    postJSON("/scheduler/settings", s);
  };

  SchedulerUI.prototype.wireInputs = function () {
    var self = this;
    var save = debounce(function () { self.persist(); }, 400);
    ["sched-cal-folder", "sched-quam-state", "sched-env-path", "sched-timeout"].forEach(function (id) {
      var e = el(id); if (e) e.addEventListener("input", save);
    });
    ["sched-failure", "sched-simulate", "sched-continue-ui"].forEach(function (id) {
      var e = el(id);
      if (e) e.addEventListener("change", function () { self.persist(); self.updateDryNote(); });
    });
  };

  /* --- environment picker ----------------------------------------------- */

  SchedulerUI.prototype.loadEnvs = function () {
    var self = this;
    var list = el("sched-env-list");
    fetchJSON("/generate/envs").then(function (d) {
      var envs = (d && d.envs) || [];
      if (!list) return;
      if (!envs.length) {
        list.innerHTML = "";
        var empty = el("sched-env-empty"); if (empty) empty.hidden = false;
        return;
      }
      list.innerHTML = "";
      envs.forEach(function (env) {
        var row = self.renderEnvRow(env);
        list.appendChild(row);
        self.probeEnvRow(env.python, row);
      });
    });
  };

  SchedulerUI.prototype.renderEnvRow = function (env) {
    var self = this;
    var row = document.createElement("div");
    row.className = "sched-env-row";
    row.setAttribute("data-python", env.python);
    if (this.settings.env_python && this.settings.env_python === env.python) {
      row.classList.add("selected");
    }
    row.innerHTML =
      '<button type="button" class="sched-env-pick"></button>' +
      '<span class="sched-env-name">' + esc(env.name) + "</span>" +
      '<span class="sched-env-path muted">' + esc(env.python) + "</span>" +
      '<span class="sched-env-status" data-state="checking">probing…</span>';
    row.querySelector(".sched-env-pick").addEventListener("click", function () {
      self.selectEnv(env.python);
    });
    return row;
  };

  SchedulerUI.prototype.probeEnvRow = function (python, row) {
    var badge = row.querySelector(".sched-env-status");
    fetchJSON("/generate/probe?python=" + encodeURIComponent(python)).then(function (p) {
      if (!badge) return;
      if (p && p.usable) {
        badge.setAttribute("data-state", "ok");
        var v = p.versions || {};
        badge.textContent = "✓ quam " + (v.quam || "?") + " · qm " + (v.qm || "?");
      } else {
        badge.setAttribute("data-state", "bad");
        var miss = (p && p.missing) || [];
        badge.textContent = miss.length ? "✗ missing: " + miss.join(", ")
          : "✗ " + ((p && p.error) || "unusable");
      }
    });
  };

  SchedulerUI.prototype.selectEnv = function (python) {
    var self = this;
    this.settings.env_python = python;
    var inp = el("sched-env-path"); if (inp) inp.value = python;
    var list = el("sched-env-list");
    if (list) {
      list.querySelectorAll(".sched-env-row").forEach(function (r) {
        r.classList.toggle("selected", r.getAttribute("data-python") === python);
      });
    }
    postJSON("/generate/select-env", { python: python });
    this.persist();
  };

  SchedulerUI.prototype.probeTypedEnv = function () {
    var self = this;
    var inp = el("sched-env-path");
    var out = el("sched-env-probe-out");
    var python = inp ? inp.value.trim() : "";
    if (!python) { if (out) out.textContent = "Enter a python path first."; return; }
    if (out) out.textContent = "Probing…";
    fetchJSON("/generate/probe?python=" + encodeURIComponent(python)).then(function (p) {
      if (!out) return;
      if (p && p.usable) {
        var v = p.versions || {};
        out.textContent = "✓ usable — quam " + (v.quam || "?") + ", quam_builder "
          + (v.quam_builder || "?") + ", qualang_tools " + (v.qualang_tools || "?");
        self.selectEnv(python);
      } else {
        var miss = (p && p.missing) || [];
        out.textContent = miss.length ? "✗ missing: " + miss.join(", ")
          : "✗ " + ((p && p.error) || "not usable");
      }
    });
  };

  /* --- effective config ------------------------------------------------- */

  SchedulerUI.prototype.busy = function (on) {
    var b = el("sched-busy"); if (b) b.hidden = !on;
  };

  SchedulerUI.prototype.readConfig = function () {
    var self = this;
    var python = this.collect().env_python;
    var out = el("sched-config-out");
    if (!python) { if (out) out.innerHTML = '<p class="sched-warn">Select an environment first.</p>'; return; }
    this.busy(true);
    fetchJSON("/scheduler/effective-config?python=" + encodeURIComponent(python)).then(function (r) {
      self.busy(false);
      if (!out) return;
      out.innerHTML = self.renderConfig(r);
    });
  };

  SchedulerUI.prototype.renderConfig = function (r) {
    if (!r || !r.ok) {
      return '<p class="sched-warn">Could not read config: ' + esc((r && r.error) || "unknown error") + "</p>";
    }
    var c = r.config || {};
    var ed = r.editable_install || {};
    var rows = [
      ["Config file", c.config_file],
      ["Project", c.project],
      ["state_path (chip)", c.state_path],
      ["storage.location (results)", c.storage_location],
      ["calibration_library.folder", c.calibration_library_folder],
      ["editable install", ed.path || ed.url || (ed.dist ? "(no direct_url)" : "(none)")],
    ];
    var html = '<table class="sched-config-table"><tbody>';
    rows.forEach(function (kv) {
      html += "<tr><th>" + esc(kv[0]) + "</th><td><code>" + esc(kv[1] || "—") + "</code></td></tr>";
    });
    html += "</tbody></table>";
    var v = r.versions || {};
    html += '<p class="muted" style="margin:.4rem 0 0">qualibrate ' + esc(v.qualibrate || "?")
      + " · quam " + esc(v.quam || "?") + " · merged from <code>" + esc(c.source || "?") + "</code></p>";
    return html;
  };

  /* --- pre-flight ------------------------------------------------------- */

  SchedulerUI.prototype.preflight = function () {
    var self = this;
    var s = this.collect();
    var out = el("sched-preflight-out");
    this.busy(true);
    postJSON("/scheduler/preflight", {
      calibrations_folder: s.calibrations_folder,
      env_python: s.env_python,
      quam_state_path: s.quam_state_path,
    }).then(function (r) {
      self.busy(false);
      if (out) out.innerHTML = self.renderPreflight(r);
      self.wireRegisterButtons();
    });
  };

  SchedulerUI.prototype.renderPreflight = function (r) {
    if (!r || !r.checks) {
      return '<p class="sched-warn">Pre-flight failed to run.</p>';
    }
    var banner = r.ok
      ? '<div class="sched-banner sched-banner-ok">✓ All blocking checks pass — ready to run.</div>'
      : '<div class="sched-banner sched-banner-bad">✗ Blocking issues must be fixed before running.</div>';
    var html = banner + '<ul class="sched-checks">';
    r.checks.forEach(function (c) {
      html += '<li class="sched-check sched-check-' + esc(c.status) + '">'
        + '<span class="sched-check-icon">' + (STATUS_ICON[c.status] || "?") + "</span>"
        + '<span class="sched-check-label">' + esc(c.label) + "</span>"
        + (c.detail ? '<span class="sched-check-detail muted">' + esc(c.detail) + "</span>" : "")
        + "</li>";
    });
    html += "</ul>";

    // Offer to register dataset roots when the storage check isn't a pass.
    var storage = (r.checks || []).filter(function (c) { return c.key === "storage"; })[0];
    var roots = r.dataset_roots || [];
    if (storage && storage.status !== "pass" && roots.length) {
      html += '<div class="sched-register">';
      roots.forEach(function (root) {
        html += '<button type="button" class="btn-sm outline sched-register-btn" data-folder="'
          + esc(root) + '">Register ' + esc(root) + "</button>";
      });
      html += "</div>";
    }
    return html;
  };

  SchedulerUI.prototype.wireRegisterButtons = function () {
    var self = this;
    var out = el("sched-preflight-out");
    if (!out) return;
    out.querySelectorAll(".sched-register-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var folder = btn.getAttribute("data-folder");
        btn.disabled = true;
        btn.textContent = "Registering…";
        postJSON("/scheduler/register-storage", { folder: folder }).then(function (res) {
          if (res && res.ok) { self.preflight(); }
          else { btn.disabled = false; btn.textContent = "Register failed — retry"; }
        });
      });
    });
  };

  /* --- parameter schemas (inspection scan) + per-node editor ------------ */

  SchedulerUI.prototype.scanParams = function () {
    var self = this;
    var info = el("sched-scan-info");
    if (info) info.textContent = "Loading parameter schemas… (inspection scan, ~10–30 s)";
    postJSON("/scheduler/scan-params", {}).then(function (r) {
      if (!r || !r.ok) {
        if (info) info.textContent = "Param load failed: " + ((r && r.error) || "error");
        return;
      }
      var items = (r && r.items) || [];
      self.paramSchemas = {};
      items.forEach(function (it) { self.paramSchemas[it.name] = it; });
      var withp = items.filter(function (i) {
        return i.parameters && Object.keys(i.parameters).length;
      }).length;
      if (info) info.textContent = "Loaded parameters for " + withp + " of " + items.length
        + " — click ⚙ on a node to edit.";
      fetchJSON("/scheduler/status").then(function (st) { self.renderQueue(st); });
    });
  };

  SchedulerUI.prototype.coerceParam = function (raw, ptype) {
    if (ptype === "boolean") return raw === "true";
    if (ptype === "integer") { var i = parseInt(raw, 10); return isNaN(i) ? raw : i; }
    if (ptype === "number") { var f = parseFloat(raw); return isNaN(f) ? raw : f; }
    if (ptype === "array") {
      // Only accept a JSON value that's actually an array; a bare scalar like
      // "5" parses to 5, which must NOT be stored for an array-typed field.
      try { var v = JSON.parse(raw); if (Array.isArray(v)) return v; } catch (e) {}
      // accept a comma list ("q1, q2, 3") as a fallback
      return raw.split(",").map(function (s) { return s.trim(); })
        .filter(function (s) { return s !== ""; });
    }
    if (raw && (raw.charAt(0) === "[" || raw.charAt(0) === "{")) {
      try { return JSON.parse(raw); } catch (e) {}
    }
    return raw;
  };

  SchedulerUI.prototype.paramFieldHtml = function (name, schema, current) {
    var ptype = schema.type || (schema.enum ? "string" : "string");
    var def = schema.default;
    var defStr = (def === null || def === undefined) ? "" : String(def);
    var val = (current === undefined) ? def : current;
    var valStr = (val === null || val === undefined) ? ""
      : (Array.isArray(val) ? JSON.stringify(val) : String(val));
    var common = ' data-param="' + esc(name) + '" data-ptype="' + esc(ptype)
      + '" data-default="' + esc(defStr) + '"';
    var input;
    if (ptype === "boolean") {
      input = '<input type="checkbox"' + common + (String(val) === "true" ? " checked" : "") + ">";
    } else if (schema.enum) {
      input = '<select' + common + ">" + schema.enum.map(function (o) {
        return '<option value="' + esc(String(o)) + '"' + (String(val) === String(o) ? " selected" : "")
          + ">" + esc(String(o)) + "</option>";
      }).join("") + "</select>";
    } else if (ptype === "integer" || ptype === "number") {
      input = '<input type="number" step="' + (ptype === "integer" ? "1" : "any") + '"'
        + common + ' value="' + esc(valStr) + '">';
    } else {
      input = '<input type="text"' + common + ' value="' + esc(valStr) + '">';
    }
    var desc = schema.description
      ? '<span class="sched-pdesc muted">' + esc(String(schema.description).split("\n")[0]) + "</span>" : "";
    return '<label class="sched-pfield"><span class="sched-pname">' + esc(schema.title || name)
      + "</span>" + input + desc + "</label>";
  };

  SchedulerUI.prototype.openParamEditor = function (it) {
    var self = this;
    var schema = self.paramSchemas[it.name];
    var overrides = it.param_overrides || {};
    var fields = [];
    var note = "";
    if (schema && schema.parameters) {
      Object.keys(schema.parameters).forEach(function (pname) {
        var p = schema.parameters[pname];
        if (p.is_targets) return;          // targets handled by the row input
        if (pname === "simulate") return;  // global Dry-run toggle owns simulate
        fields.push({ name: pname, schema: p });
      });
    } else {
      // No full schema loaded — build a minimal editor from the existing
      // overrides so they are always reviewable / removable without a rescan.
      Object.keys(overrides).forEach(function (pname) {
        var v = overrides[pname];
        var t = typeof v === "number" ? "number"
          : (typeof v === "boolean" ? "boolean" : (Array.isArray(v) ? "array" : "string"));
        fields.push({ name: pname, schema: { type: t, title: pname } });
      });
      note = '<p class="muted">Full parameter list not loaded — click ' +
        "“Load parameter forms” to edit all parameters. Showing current overrides only.</p>";
    }

    var overlay = document.createElement("div");
    overlay.className = "sched-modal-overlay";
    var card = document.createElement("div");
    card.className = "sched-modal-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "sched-modal-title");
    var body = note + (fields.map(function (f) {
      return self.paramFieldHtml(f.name, f.schema, overrides[f.name]);
    }).join("") || '<p class="muted">No editable parameters.</p>');
    card.innerHTML =
      '<header class="sched-modal-head"><strong id="sched-modal-title">Parameters — ' + esc(it.name) + "</strong>" +
      '<button type="button" class="sched-modal-close" aria-label="Close">&times;</button></header>' +
      '<div class="sched-modal-body">' + body + "</div>" +
      '<footer class="sched-modal-foot">' +
        '<button type="button" class="btn-sm outline sched-modal-reset">Reset all</button>' +
        '<button type="button" class="btn-sm primary sched-modal-save">Save</button>' +
        '<button type="button" class="btn-sm outline sched-modal-cancel">Cancel</button></footer>';
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    var dirty = false;
    card.addEventListener("input", function () { dirty = true; });
    var release = null;
    function close() {
      if (release) { release(); release = null; }
      try { document.body.removeChild(overlay); } catch (e) {}
    }
    function tryClose() {
      if (dirty && !window.confirm("Discard unsaved parameter changes?")) return;
      close();
    }
    // Escape-to-close + focus-trap + focus-restore, like the app's other modals.
    release = (window.trapFocus || function () { return function () {}; })(overlay, tryClose);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) tryClose(); });
    card.querySelector(".sched-modal-close").addEventListener("click", tryClose);
    card.querySelector(".sched-modal-cancel").addEventListener("click", tryClose);
    card.querySelector(".sched-modal-reset").addEventListener("click", function () {
      card.querySelectorAll("[data-param]").forEach(function (inp) {
        var d = inp.getAttribute("data-default") || "";
        if (inp.type === "checkbox") inp.checked = d === "true";
        else inp.value = d;
      });
      dirty = true;
    });
    card.querySelector(".sched-modal-save").addEventListener("click", function () {
      var newOver = {};
      card.querySelectorAll("[data-param]").forEach(function (inp) {
        var pname = inp.getAttribute("data-param");
        var ptype = inp.getAttribute("data-ptype");
        var def = inp.getAttribute("data-default");
        var raw = inp.type === "checkbox" ? (inp.checked ? "true" : "false") : inp.value;
        if (raw === def) return;                       // unchanged → no override
        if (raw === "" && ptype !== "string") return;  // blank non-string → skip
        newOver[pname] = self.coerceParam(raw, ptype);
      });
      // Close only if the write actually landed (M4) — otherwise keep edits.
      self.queueAction("params", { id: it.id, param_overrides: newOver }).then(function (r) {
        if (r && r.state) { dirty = false; close(); }
        else if (window.showToast) window.showToast("Could not save parameters — try again.", "error");
      });
    });
  };

  /* --- on-failure rules editor (sequence chaining, v1) ------------------- */
  // One built-in condition: "a target's fit_results.success == false in the
  // attributed run" → insert the chosen nodes right after this item, targeted
  // at the failed qubits (or inheriting the item's targets). Auto-inserted
  // children never inherit rules (server-side loop guard, depth ≤ 2).
  SchedulerUI.prototype.openRulesEditor = function (it) {
    var self = this;
    var lib = (self._libItems || []).filter(function (l) {
      return l.kind === "node";
    });
    var rule = (it.on_outcome || [])[0] || null;
    var chosen = {};
    (rule ? rule.insert || [] : []).forEach(function (i) { chosen[i.name] = true; });

    var overlay = document.createElement("div");
    overlay.className = "sched-modal-overlay";
    var card = document.createElement("div");
    card.className = "sched-modal-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    var libHtml = lib.length ? lib.map(function (l) {
      return '<label class="sched-rule-node"><input type="checkbox" data-rule-node="'
        + esc(l.name) + '"' + (chosen[l.name] ? " checked" : "") + "> "
        + esc(l.name) + "</label>";
    }).join("") : '<p class="muted">Scan a calibrations folder first — the node list is empty.</p>';
    card.innerHTML =
      '<header class="sched-modal-head"><strong>On-failure rule — ' + esc(it.name) + "</strong>"
      + '<button type="button" class="sched-modal-close" aria-label="Close">&times;</button></header>'
      + '<div class="sched-modal-body">'
      + '<label class="sched-pfield"><input type="checkbox" id="sched-rule-on"'
        + (rule ? " checked" : "") + "> "
        + "<span>When any target's fit FAILS (fit_results.success == false), insert:</span></label>"
      + '<div class="sched-rule-nodes">' + libHtml + "</div>"
      + '<label class="sched-pfield"><span class="sched-pname">Targets of inserted steps</span>'
      + '<select id="sched-rule-mode">'
        + '<option value="failed_only"' + (!rule || rule.targets_mode !== "inherit" ? " selected" : "")
        + ">only the failed qubits</option>"
        + '<option value="inherit"' + (rule && rule.targets_mode === "inherit" ? " selected" : "")
        + ">same targets as this step</option>"
      + "</select></label>"
      + '<p class="muted sched-rule-hint">Inserted steps land directly after this one, run in the listed order, '
        + "and never inherit rules themselves (loop-safe, depth ≤ 2). If the run can't be attributed "
        + "unambiguously, the rule no-ops and leaves a note on the step.</p>"
      + "</div>"
      + '<footer class="sched-modal-foot">'
        + '<button type="button" class="btn-sm primary sched-modal-save">Save rule</button>'
        + '<button type="button" class="btn-sm outline sched-modal-cancel">Cancel</button></footer>';
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    var release = (window.trapFocus || function () { return function () {}; })(overlay, close);
    function close() {
      if (release) { var r = release; release = null; r(); }
      try { document.body.removeChild(overlay); } catch (e) {}
    }
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    card.querySelector(".sched-modal-close").addEventListener("click", close);
    card.querySelector(".sched-modal-cancel").addEventListener("click", close);
    card.querySelector(".sched-modal-save").addEventListener("click", function () {
      var on = card.querySelector("#sched-rule-on").checked;
      var rules = [];
      if (on) {
        var inserts = [];
        card.querySelectorAll("[data-rule-node]:checked").forEach(function (cb) {
          var name = cb.getAttribute("data-rule-node");
          var l = lib.filter(function (x) { return x.name === name; })[0];
          if (l) inserts.push({
            source_file: l.file, name: l.name, kind: l.kind,
            has_hook: l.has_hook, targets_name: l.targets_name,
          });
        });
        if (!inserts.length) { alert("Pick at least one node to insert, or untick the rule."); return; }
        rules.push({
          when: "fit_fail",
          targets_mode: card.querySelector("#sched-rule-mode").value,
          insert: inserts,
        });
      }
      self.queueAction("rules", { id: it.id, on_outcome: rules }).then(function (r) {
        if (r && r.ok !== false) { self._queueSig = null; close(); }
        else if (window.showToast) window.showToast(
          "Could not save rule: " + ((r && r.error) || "error"), "error");
      });
    });
  };

  /* --- sequence presets --------------------------------------------------- */
  SchedulerUI.prototype.refreshPresets = function () {
    var self = this;
    var sel = el("sched-preset-select");
    if (!sel) return;
    fetchJSON("/scheduler/presets").then(function (r) {
      var ps = (r && r.presets) || [];
      var cur = sel.value;
      sel.innerHTML = '<option value="">presets…</option>' + ps.map(function (p) {
        return '<option value="' + esc(p.id) + '">' + esc(p.name)
          + " (" + esc(p.n_items) + ")</option>";
      }).join("");
      if (cur) sel.value = cur;
      self._presets = ps;
    });
  };

  SchedulerUI.prototype.wirePresets = function () {
    var self = this;
    var sel = el("sched-preset-select");
    if (!sel) return;
    self.refreshPresets();
    function picked() { return sel.value || null; }
    var loadBtn = el("sched-preset-load"), repBtn = el("sched-preset-replace"),
        saveBtn = el("sched-preset-save"), delBtn = el("sched-preset-delete");
    function loadPreset(mode) {
      var id = picked(); if (!id) { alert("Pick a preset first."); return; }
      if (mode === "replace" && !window.confirm(
        "Replace the current queue with this preset? (a running step is kept)")) return;
      postJSON("/scheduler/presets/" + id + "/load", { mode: mode }).then(function (r) {
        if (r && r.state) { self._queueSig = null; self.renderQueue(r.state); }
        (r && r.warnings || []).forEach(function (w) {
          if (window.showToast) window.showToast("Preset: " + w, "warn");
        });
      });
    }
    if (loadBtn) loadBtn.addEventListener("click", function () { loadPreset("append"); });
    if (repBtn) repBtn.addEventListener("click", function () { loadPreset("replace"); });
    if (saveBtn) saveBtn.addEventListener("click", function () {
      var name = window.prompt("Preset name (snapshots the current queue):");
      if (!name || !name.trim()) return;
      postJSON("/scheduler/presets", { name: name.trim() }).then(function (r) {
        if (r && r.ok) self.refreshPresets();
        else if (window.showToast) window.showToast("Could not save preset.", "error");
      });
    });
    if (delBtn) delBtn.addEventListener("click", function () {
      var id = picked(); if (!id) { alert("Pick a preset first."); return; }
      var p = (self._presets || []).filter(function (x) { return x.id === id; })[0];
      if (!window.confirm("Delete preset “" + ((p && p.name) || id) + "”?")) return;
      fetch("/scheduler/presets/" + id, { method: "DELETE" }).then(function () {
        self.refreshPresets();
      });
    });
  };

  function init() {
    var root = el("sched-root");
    if (!root || root._schedInit) return;
    root._schedInit = true;
    new SchedulerUI(root).start();
  }

  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("htmx:afterSwap", init);
})();
