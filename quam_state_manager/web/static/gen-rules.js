/* Generate Config -- addition RULES: the Apply dialog (a gate for many pairs),
 * the keyboard menu, the Rules card, and the triggers that keep them honest.
 *
 * window.GenRules:
 *   rulesOf(spec) / ruleById(spec, id)   the rules the spec carries (spec.addition_rules)
 *   openApply(opts)                      the Apply dialog; opts = {ctx, rule, opener, ...}
 *   resetRuleRuntime()                   strand every in-flight expand/apply (content replaced)
 *   specChanged(reason, spec)            the wizard's one funnel for "the spec moved"
 *   menu(btn, items, opts)               the shared keyboard menu (role=menu)
 *
 * The browser renders and asks; the SERVER decides (POST /generate/additions/rules/
 * expand and /apply). Nothing here judges a pair.
 */
(function () {
  "use strict";

  var EXPAND_URL = "/generate/additions/rules/expand";
  var APPLY_URL = "/generate/additions/rules/apply";
  var G_READY = "Ready", G_LOOK = "Needs a look", G_CANT = "Can't", G_HAS = "Already has it";
  var GROUPS = [G_READY, G_LOOK, G_CANT, G_HAS];
  // The spec keys the server's rule_sig reads (core/addition_rules.expand):
  // a change in any of them can change an answer, so any of them makes an open dialog stale.
  var WATCHED = ["qubit_pairs", "populate", "lines", "qubit_class", "qubit_class_overrides",
                 "quam_class", "pair_gate", "qubits", "additions"];
  var RECHECK_MS = 150;

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "className") e.className = attrs[k];
      else if (k === "hidden") e.hidden = !!attrs[k];
      else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    });
    if (text != null) e.textContent = text;
    return e;
  }
  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function toast(msg, level) { if (typeof window.showToast === "function") window.showToast(msg, level || "info"); }
  function shortName(p) { return String(p || "").split(".").pop(); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }

  /* ---- the model (pure) ------------------------------------------------ */

  function rulesOf(spec) { return Array.isArray(spec && spec.addition_rules) ? spec.addition_rules : []; }
  function ruleById(spec, id) {
    return rulesOf(spec).filter(function (r) { return r && r.id === id; })[0] || null;
  }
  // run_build._norm_index + add_names.unordered_key (parity-pinned in test_rules_js_parity).
  function normIndex(q) {
    var s = String(q);
    if (s.charAt(0) === "q" || s.charAt(0) === "Q") s = s.slice(1);
    return /^\d+$/.test(s) ? String(parseInt(s, 10)) : s;
  }
  function unorderedKey(c, t) {
    return [normIndex(c), normIndex(t)].sort().join("-");
  }

  function watchedOf(spec) {
    var o = {};
    WATCHED.forEach(function (k) { o[k] = spec ? spec[k] : undefined; });
    return o;
  }
  // The client mirror of ruleSig: only what is needed to know "the answer may have changed".
  function specSig(spec, rule, choices) {
    return JSON.stringify([watchedOf(spec), rule || null, choices || null]);
  }

  var FIELD_WORDS = { RF_freq: "RF frequency", f_01: "f_01", anharmonicity: "anharmonicity" };
  function describeChange(before, spec) {
    var now = watchedOf(spec);
    for (var i = 0; i < WATCHED.length; i++) {
      var k = WATCHED[i];
      if (JSON.stringify(before[k]) === JSON.stringify(now[k])) continue;
      if (k === "populate") {
        var bq = (before.populate || {}).qubit || {}, nq = (now.populate || {}).qubit || {};
        var ids = Object.keys(nq).concat(Object.keys(bq));
        for (var j = 0; j < ids.length; j++) {
          var q = ids[j];
          var a = bq[q] || {}, b = nq[q] || {};
          var fs = Object.keys(a).concat(Object.keys(b));
          for (var m = 0; m < fs.length; m++) {
            if (JSON.stringify(a[fs[m]]) !== JSON.stringify(b[fs[m]])) {
              return q + " " + (FIELD_WORDS[fs[m]] || fs[m]);
            }
          }
        }
        return "populate values";
      }
      return { qubit_pairs: "the qubit pairs", lines: "the lines", additions: "the additions",
               qubits: "the qubits" }[k] || k;
    }
    return "the chip";
  }

  /* ---- runtime guards (C5) --------------------------------------------- */

  var _seq = 0;            // bumped when the wizard's CONTENT is replaced
  var _ctl = null;         // AbortController of the in-flight request
  var _open = null;        // the open Apply dialog state, if any
  var _bg = { timer: null, ctl: null };

  function abortInFlight() {
    if (_ctl) { try { _ctl.abort(); } catch (e) { /* done */ } _ctl = null; }
  }

  function resetRuleRuntime() {
    _seq++;
    abortInFlight();
    if (_bg.timer) { clearTimeout(_bg.timer); _bg.timer = null; }
    if (_bg.ctl) { try { _bg.ctl.abort(); } catch (e) { /* done */ } _bg.ctl = null; }
    if (_open) markStale(_open, "the wizard content");
  }

  /* ---- requests -------------------------------------------------------- */

  function post(url, body, signal) {
    var opts = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    if (signal) opts.signal = signal;
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }

  /* ---- the Apply dialog ------------------------------------------------- */

  function dialogOf(d) { return d.dlg; }

  function fieldsLine(numbers, sourceLabel) {
    var groups = {}, order = [], n = 0;
    Object.keys(numbers || {}).forEach(function (k) {
      var dot = k.indexOf("."), role = dot < 0 ? "" : k.slice(0, dot), f = k.slice(dot + 1), v = numbers[k];
      if (!groups[role]) { groups[role] = []; order.push(role); }
      groups[role].push(f + " " + v + (/(^|_)length$/.test(f) ? " ns" : ""));
      n++;
    });
    if (!n) return "";
    var shown = order.map(function (r) { return (r ? r + ": " : "") + groups[r].join(", "); }).join("; ");
    return sourceLabel
      ? "Numbers copied from " + sourceLabel + " (current values) as initial values: " + shown
      : "Initial values from the form: " + shown;
  }

  function classLine(rule) {
    var cls = (rule.gate && rule.gate.macro_class) || "";
    var info = window.GenAdditions && window.GenAdditions.classInfo ? window.GenAdditions.classInfo(cls) : null;
    var label = info && info.label ? info.label : shortName(cls);
    var isLab = !!(info && info.origin === "lab");
    var tag = label === shortName(cls) ? (isLab ? "lab class" : "") : shortName(cls) + (isLab ? ", lab class" : "");
    var line = label + (tag ? " (" + tag + ")" : "") + " · gate " +
      ((rule.gate && rule.gate.name) || "") + (rule.source ? " · from " + rule.source : "");
    return line;
  }

  function rowOfPid(d, pid) {
    var rows = (d.answer && d.answer.pairs) || [];
    for (var i = 0; i < rows.length; i++) if (rows[i].pair === pid) return rows[i];
    return null;
  }

  function chooseText(row) {
    var role = Object.keys(row.choices || {}).filter(function (r) { return row.choices[r] == null; })[0] || "";
    if (role === "moving" || role === "pulse") {
      return { text: "no moving qubit set for this pair — which qubit moves?", labels: ["control ", "target "] };
    }
    return { text: "idle frequencies not typed yet — which qubit is higher?", labels: ["", ""] };
  }

  function rowSummary(row) {
    var ch = row.choices || {};
    var roles = Object.keys(ch).filter(function (r) { return ch[r]; }).map(function (r) {
      return r + " → " + (ch[r] === "coupler" ? "coupler" : ch[r] + " z");
    });
    var names = (row.additions || []).filter(function (a) { return a.kind === "pulse"; })
      .map(function (a) { return a.name; });
    var out = roles.join(" · ");
    if (names.length) out += (out ? " · " : "") + names.join(", ");
    return out;
  }

  function reasonTail(row) {
    var m = /\bby (Populate frequency|chip f_01)$/.exec(row.reason || "");
    return m ? "by " + m[1] : "";
  }

  function tickable(row) { return row.status === G_READY || row.status === G_LOOK; }

  function adoptTicks(d, answer) {
    var next = {};
    (answer.pairs || []).forEach(function (row) {
      if (!tickable(row)) return;
      if (Object.prototype.hasOwnProperty.call(d.ticks, row.pair)) next[row.pair] = d.ticks[row.pair];
      else next[row.pair] = row.status === G_READY;
    });
    d.ticks = next;
  }

  function tickedPids(d) {
    return ((d.answer && d.answer.pairs) || []).filter(function (r) { return tickable(r) && d.ticks[r.pair]; })
      .map(function (r) { return r.pair; });
  }

  function feedlineGroups(d) {
    var map = (d.ctx.feedlines && d.ctx.feedlines()) || {};
    var groups = {}, order = [];
    ((d.answer && d.answer.pairs) || []).forEach(function (row) {
      if (row.status !== G_READY) return;
      var g = map[row.control] || "feedline1";
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(row.pair);
    });
    return order.map(function (g) { return { name: g, pairs: groups[g] }; });
  }

  function setsEqual(a, b) {
    if (a.length !== b.length) return false;
    var s = {};
    a.forEach(function (x) { s[x] = 1; });
    return b.every(function (x) { return s[x]; });
  }

  /* -- render -------------------------------------------------------------- */

  function buildDialog(d) {
    var dlg = el("dialog", { className: "gen-rule-dlg", "aria-labelledby": "gen-rule-title" });
    var head = el("div", { className: "gen-rule-head" });
    d.titleEl = el("h3", { id: "gen-rule-title" });
    d.xBtn = el("button", { type: "button", className: "gen-rule-x secondary", "aria-label": "Close" }, "✕");
    head.appendChild(d.titleEl); head.appendChild(d.xBtn);
    dlg.appendChild(head);
    d.subEl = el("p", { className: "gen-rule-sub" });
    d.numsEl = el("p", { className: "gen-rule-nums muted" });
    d.noteEl = el("p", { className: "gen-rule-ready-note muted" }, "Ready = SM's checks pass. Your lab code runs at Build.");
    dlg.appendChild(d.subEl); dlg.appendChild(d.numsEl); dlg.appendChild(d.noteEl);
    d.selectEl = el("div", { className: "gen-rule-select", role: "group", "aria-label": "Select pairs" });
    dlg.appendChild(d.selectEl);
    d.statusEl = el("p", { className: "gen-rule-status", role: "status" });
    dlg.appendChild(d.statusEl);
    d.bodyEl = el("div", { className: "gen-rule-body" });
    dlg.appendChild(d.bodyEl);
    var foot = el("div", { className: "gen-rule-foot" });
    var lab = el("label", { className: "gen-rule-later" });
    d.laterEl = el("input", { type: "checkbox", className: "gen-rule-later-cb" });
    lab.appendChild(d.laterEl);
    lab.appendChild(document.createTextNode(" Also add to pairs created later"));
    d.laterLabel = lab;
    d.cancelBtn = el("button", { type: "button", className: "gen-rule-cancel secondary" }, "Cancel");
    d.applyBtn = el("button", { type: "button", className: "gen-rule-apply" }, "Add to … pairs");
    foot.appendChild(lab); foot.appendChild(d.cancelBtn); foot.appendChild(d.applyBtn);
    dlg.appendChild(foot);
    d.footEl = foot;
    d.dlg = dlg;
    d.laterEl.checked = !!d.rule.auto_new;
    return dlg;
  }

  function chipBtn(label, pressed, onClick, extra) {
    var b = el("button", { type: "button", className: "gen-rule-chip secondary" + (extra || ""),
                           "aria-pressed": pressed ? "true" : "false" }, label);
    b.addEventListener("click", onClick);
    return b;
  }

  function renderSelect(d) {
    d.selectEl.innerHTML = "";
    d.selectEl.appendChild(el("span", { className: "gen-rule-select-label" }, "Select:"));
    var ready = ((d.answer && d.answer.pairs) || []).filter(function (r) { return r.status === G_READY; })
      .map(function (r) { return r.pair; });
    var ticked = tickedPids(d);
    var enabled = d.status === "ready" && !d.pending && !!d.answer;
    var allReady = enabled && ready.length > 0 && setsEqual(ticked, ready);
    var none = enabled && ticked.length === 0;
    var groups = enabled ? feedlineGroups(d) : [];
    var feedOn = false;
    if (d.feedline) {
      var g = groups.filter(function (x) { return x.name === d.feedline; })[0];
      feedOn = !!g && setsEqual(ticked, g.pairs);
    }
    var cAll = chipBtn("All ready", allReady, function () { selectPairs(d, ready); });
    var cFeed = chipBtn("Feedline ▾", feedOn, function () {
      var items = groups.map(function (gr) {
        return { label: gr.name + " — " + plural(gr.pairs.length, "ready pair"),
                 onSelect: function () { d.feedline = gr.name; selectPairs(d, gr.pairs); } };
      });
      if (!items.length) items = [{ label: "No ready pairs", disabled: true }];
      menu(cFeed, items, {});
    }, " gen-rule-chip-feed");
    cFeed.setAttribute("aria-haspopup", "menu");
    var cNone = chipBtn("None", none, function () { d.feedline = null; selectPairs(d, []); });
    [cAll, cFeed, cNone].forEach(function (b) { b.disabled = !enabled; d.selectEl.appendChild(b); });
  }

  function selectPairs(d, pids) {
    var on = {};
    pids.forEach(function (p) { on[p] = true; });
    Object.keys(d.ticks).forEach(function (p) { d.ticks[p] = !!on[p]; });
    if (!pids.length) d.feedline = null;
    renderBody(d);
  }

  function groupHead(label, n, total, open) {
    var sum = el("summary", { className: "gen-rule-sum" });
    sum.appendChild(el("span", { className: "gen-rule-sum-label" }, label + " — " + n + (total != null ? " of " + total + " pairs" : "")));
    sum.appendChild(el("span", { className: "gen-rule-sum-toggle muted" }, open ? "▾ collapse" : "▸ expand"));
    return sum;
  }

  function bindSummary(det, sum) {
    var t = sum.querySelector(".gen-rule-sum-toggle");
    det.addEventListener("toggle", function () { t.textContent = det.open ? "▾ collapse" : "▸ expand"; });
    sum.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight" || ev.key === "Enter") { ev.preventDefault(); det.open = true; }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); det.open = false; }
    });
  }

  function groupCheckbox(d, rows, label) {
    var cb = el("input", { type: "checkbox", className: "gen-rule-grp-cb", "aria-label": "Select all: " + label });
    var on = rows.filter(function (r) { return d.ticks[r.pair]; }).length;
    cb.checked = on === rows.length && rows.length > 0;
    cb.indeterminate = on > 0 && on < rows.length;
    cb.addEventListener("change", function () {
      rows.forEach(function (r) { d.ticks[r.pair] = cb.checked; });
      d.feedline = null;
      renderBody(d);
    });
    return cb;
  }

  function rowEl(d, row) {
    var li = el("div", { className: "gen-rule-row gen-rule-row-" + (tickable(row) ? "tick" : "plain"), "data-pair": row.pair });
    if (tickable(row)) {
      var cb = el("input", { type: "checkbox", className: "gen-rule-row-cb", "aria-label": row.pair, "data-pair": row.pair });
      cb.checked = !!d.ticks[row.pair];
      cb.addEventListener("change", function () {
        d.ticks[row.pair] = cb.checked; d.feedline = null; renderBody(d);
        var again = d.bodyEl.querySelector('.gen-rule-row-cb[data-pair="' + row.pair + '"]');
        if (again) again.focus();
      });
      li.appendChild(cb);
    }
    li.appendChild(el("span", { className: "gen-rule-pid" }, row.pair));
    var needsChoice = row.status === G_LOOK && /choose/.test(row.reason || "");
    var chosen = Object.prototype.hasOwnProperty.call(d.choices, row.pair);
    if (needsChoice || chosen) {
      var ct = chooseText(row);
      li.appendChild(el("span", { className: "gen-rule-reason" }, needsChoice ? ct.text : rowSummary(row)));
      var qs = [String(row.control), String((row.long || "").split("-").pop() || "")];
      var sel = el("select", { className: "gen-rule-choice", "aria-label": "Choice for " + row.pair, "data-pair": row.pair });
      sel.appendChild(el("option", { value: "control" }, ct.labels[0] + qs[0]));
      sel.appendChild(el("option", { value: "target" }, ct.labels[1] + qs[1]));
      sel.value = d.choices[row.pair] || "control";
      sel.addEventListener("change", function () {
        d.choices[row.pair] = sel.value;
        d.ticks[row.pair] = true;
        recheck(d, "choice", 0);
      });
      li.appendChild(sel);
      if (needsChoice) li.appendChild(el("span", { className: "gen-rule-hint muted" },
        "tick to add with that choice; or type RF freq in Populate and come back"));
    } else if (row.status === G_READY) {
      li.appendChild(el("span", { className: "gen-rule-reason" }, rowSummary(row)));
      var tail = reasonTail(row);
      if (tail) li.appendChild(el("span", { className: "gen-rule-tail muted" }, tail));
    } else {
      li.appendChild(el("span", { className: "gen-rule-reason" }, row.reason || ""));
    }
    return li;
  }

  function cantByReason(rows) {
    var by = {}, order = [];
    rows.forEach(function (r) {
      var k = r.reason || "";
      if (!by[k]) { by[k] = []; order.push(k); }
      by[k].push(r.pair);
    });
    return order.map(function (k) { return { reason: k, pairs: by[k] }; });
  }

  function renderBody(d) {
    var active = document.activeElement;
    var restore = active && d.bodyEl.contains(active) && active.getAttribute ? active.getAttribute("data-pair") : null;
    d.bodyEl.innerHTML = "";
    var answer = d.answer;
    if (d.status === "loading" || !answer) {
      var sk = el("div", { className: "gen-rule-skel", "aria-hidden": "true" });
      sk.appendChild(el("div", { className: "gen-rule-skel-row" })); sk.appendChild(el("div", { className: "gen-rule-skel-row" }));
      d.bodyEl.appendChild(sk);
      renderSelect(d); renderFoot(d);
      return;
    }
    var rows = answer.pairs || [];
    var total = rows.length;
    var frag = document.createDocumentFragment();
    var by = {};
    GROUPS.forEach(function (g) { by[g] = rows.filter(function (r) { return r.status === g; }); });
    var allCant = total > 0 && by[G_READY].length === 0 && by[G_LOOK].length === 0;
    if (allCant) frag.appendChild(el("p", { className: "gen-rule-allcant" }, "No other pair can take this gate."));
    GROUPS.forEach(function (g) {
      var list = by[g];
      if (!list.length) return;
      var wrap = el("div", { className: "gen-rule-grpwrap", "data-group": g });
      var open = g !== G_HAS;
      var det = el("details", { className: "gen-rule-grp" });
      det.open = open;
      var sum = groupHead(g, list.length, (g === G_READY || (g === G_CANT && allCant)) ? total : null, open);
      det.appendChild(sum);
      bindSummary(det, sum);
      if (tickable(list[0])) wrap.appendChild(groupCheckbox(d, list, g));
      else wrap.appendChild(el("span", { className: "gen-rule-grp-mark", "aria-hidden": "true" },
        g === G_CANT ? "✕" : "○"));
      if (g === G_CANT) {
        cantByReason(list).forEach(function (grp) {
          var line = el("div", { className: "gen-rule-cant", "data-pair": grp.pairs[0] });
          line.appendChild(el("span", { className: "gen-rule-cant-n" }, String(grp.pairs.length) + " — " + grp.reason));
          line.appendChild(el("span", { className: "gen-rule-cant-pairs muted" }, grp.pairs.join(", ")));
          det.appendChild(line);
        });
      } else {
        list.forEach(function (r) { det.appendChild(rowEl(d, r)); });
      }
      wrap.appendChild(det);
      frag.appendChild(wrap);
    });
    d.bodyEl.appendChild(frag);
    if (restore) {
      var back = d.bodyEl.querySelector('[data-pair="' + restore + '"]');
      var target = back && (back.matches("input,select") ? back : back.querySelector("input,select"));
      if (target) target.focus();
    }
    renderSelect(d); renderFoot(d);
  }

  function renderFoot(d) {
    var answer = d.answer;
    var n = tickedPids(d).length;
    var rows = (answer && answer.pairs) || [];
    var allCant = answer && rows.length > 0 && !rows.some(tickable);
    d.laterLabel.hidden = !!allCant;
    d.cancelBtn.textContent = allCant ? "Close" : "Cancel";
    d.applyBtn.textContent = allCant ? "Nothing to add" : (answer && d.status !== "loading" ? "Add to " + plural(n, "pair") : "Add to … pairs");
    d.applyBtn.disabled = !(d.status === "ready" && !d.pending && !(d.answer && d.answer.pending) && !d.applying && n > 0);
    d.dlg.classList.toggle("gen-rule-allcant-state", !!allCant);
  }

  function renderHeader(d) {
    var rule = d.rule;
    d.titleEl.textContent = "Add " + ((rule.gate && rule.gate.name) || "") + " to other pairs";
    d.subEl.textContent = classLine(rule);
    var rows = (d.answer && d.answer.pairs) || [];
    var nums = rows.length ? rows[0].numbers : null;
    d.numsEl.textContent = nums ? fieldsLine(nums, rule.source) : "";
    d.numsEl.hidden = !d.numsEl.textContent;
  }

  function render(d) {
    renderHeader(d);
    var st = d.statusEl;
    st.className = "gen-rule-status";
    st.textContent = "";
    st.innerHTML = "";
    var dim = false;
    if (d.status === "loading") {
      st.appendChild(el("span", { className: "gen-rule-spin", "aria-hidden": "true" }, "◌ "));
      st.appendChild(document.createTextNode("Checking " + plural(d.count || 0, "pair") + "…" + (d.slow ? " (a 60Q chip takes a moment)" : "")));
    } else if (d.status === "stale") {
      dim = true;
      st.classList.add("gen-rule-status-stale");
      st.textContent = "⟳ " + (d.staleReason ? d.staleReason + " — rechecking " : "The chip changed while this was open (" + (d.changeText || "the chip") + ") — rechecking ") +
        plural(d.count || 0, "pair") + "…";
    } else if (d.status === "error") {
      st.classList.add("gen-rule-status-error");
      st.appendChild(document.createTextNode(d.error || "Could not check the pairs."));
      var retry = el("button", { type: "button", className: "gen-rule-retry secondary" }, "Retry");
      retry.addEventListener("click", function () { d.status = "loading"; recheck(d, "retry", 0); });
      st.appendChild(retry);
    } else if (d.answer && d.answer.pending) {
      st.classList.add("gen-rule-status-note");
      st.textContent = "The environment's classes are still being read — they are checked at Build.";
    }
    d.bodyEl.toggleAttribute("inert", dim);
    d.bodyEl.classList.toggle("gen-rule-dim", dim);
    d.selectEl.toggleAttribute("inert", dim);
    d.dlg.setAttribute("data-state", d.status);
    renderBody(d);
  }

  /* -- state machine ------------------------------------------------------- */

  function currentSig(d) { return specSig(d.ctx.spec(), d.rule, effectiveChoices(d)); }
  function effectiveChoices(d) { return Object.keys(d.choices).length ? d.choices : null; }

  function recheck(d, why, delay) {
    if (d.closed) return;
    d.pending = true;
    if (d.timer) clearTimeout(d.timer);
    var go = function () {
      d.timer = null;
      sendExpand(d, why);
    };
    renderFoot(d); renderSelect(d);
    if (delay === 0) go(); else d.timer = setTimeout(go, delay == null ? RECHECK_MS : delay);
  }

  function sendExpand(d, why) {
    if (d.closed) return;
    abortInFlight();
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    _ctl = ctl;
    var seq = _seq, sig = currentSig(d), n = ++d.reqN;
    d.pending = true;
    var spec = d.ctx.spec();
    d.count = (spec.qubit_pairs || []).length;
    d.reqSig = sig;
    post(EXPAND_URL, { spec: spec, rule: d.rule, choices: effectiveChoices(d) || undefined }, ctl && ctl.signal)
      .then(function (res) {
        if (d.closed) return;
        // Adopt only an answer to THIS question about THIS content (C5).
        if (seq !== _seq || n !== d.reqN || sig !== currentSig(d)) return;
        d.pending = false;
        if (!res.body || res.body.ok === false) {
          d.status = "error"; d.error = (res.body && res.body.error) || "Could not check the pairs.";
          render(d); return;
        }
        d.answer = res.body; d.status = "ready"; d.staleReason = null;
        adoptTicks(d, res.body);
        d.sig = sig;
        if (d.cache) d.cache(res.body, sig);
        render(d);
        landFocus(d);
      })
      .catch(function (e) {
        if (d.closed || (e && e.name === "AbortError")) return;
        if (seq !== _seq || n !== d.reqN) return;
        d.pending = false; d.status = "error"; d.error = "Could not check the pairs: " + String(e && e.message || e);
        render(d);
      });
  }

  function markStale(d, because) {
    if (d.closed) return;
    var wasFocused = d.bodyEl && d.bodyEl.contains(document.activeElement);
    if (d.status === "ready" || d.status === "stale") {
      d.status = "stale";
      d.changeText = because && typeof because === "string" && because !== "the wizard content" ? because : d.changeText;
      if (because === "the wizard content") d.changeText = "the wizard content";
    }
    d.specSnap = watchedOf(d.ctx.spec());
    render(d);
    if (wasFocused) d.cancelBtn.focus();
    recheck(d, "stale", RECHECK_MS);
  }

  function landFocus(d) {
    if (d.landed) return;
    if (document.activeElement && document.activeElement !== d.cancelBtn && d.dlg.contains(document.activeElement)) { d.landed = true; return; }
    d.landed = true;
    var rows = (d.answer && d.answer.pairs) || [];
    if (rows.length && !rows.some(tickable)) { d.cancelBtn.focus(); return; }
    var cb = d.bodyEl.querySelector(".gen-rule-grp-cb");
    (cb || d.cancelBtn).focus();
  }

  function closeApply(d, how) {
    if (d.closed) return;
    d.closed = true;
    if (d.timer) clearTimeout(d.timer);
    if (d.slowTimer) clearTimeout(d.slowTimer);
    abortInFlight();
    if (_open === d) _open = null;
    try { if (typeof d.dlg.close === "function") d.dlg.close(); } catch (e) { /* closed */ }
    d.dlg.removeAttribute("open");
    if (d.dlg.parentNode) d.dlg.parentNode.removeChild(d.dlg);
    document.removeEventListener("keydown", d.onDocKey, true);
    if (how !== "applied" && d.opener && d.opener.isConnected && typeof d.opener.focus === "function") d.opener.focus();
  }

  function replaceInPlace(spec, next) {
    Object.keys(spec).forEach(function (k) { delete spec[k]; });
    Object.keys(next).forEach(function (k) { spec[k] = next[k]; });
  }

  function addedText(rule, added) {
    var names = added.slice();
    var span = names.length > 2 ? names[0] + " … " + names[names.length - 1] : names.join(", ");
    return "Added " + ((rule.gate && rule.gate.name) || "") + " to " + plural(names.length, "pair") +
      (names.length ? " (" + span + ")" : "") + ". Ctrl+Z undoes it.";
  }

  function pushUndo(spec, label, snapshot, refresh) {
    var u = window._wizUndo;
    if (!u || typeof u.pushRestore !== "function") return;
    u.pushRestore(label, function () {
      resetRuleRuntime();
      replaceInPlace(spec, clone(snapshot));
      refresh();
    });
  }

  function refreshViews() {
    if (window.GenAdditions && window.GenAdditions.refresh) window.GenAdditions.refresh();
    renderCards();
  }

  /* One apply, shared by the dialog, Reset to rule and the review dialog: the server
   * re-expands and answers with the whole new spec; the client swaps it in as ONE undo entry.
   * Resolves {ok:true, out} or {ok:false, stale, reason} (never re-sent). */
  function commitApply(o) {
    var ctx = o.ctx, spec = ctx.spec(), seq = _seq, sig = specSig(spec, o.rule, o.choices && Object.keys(o.choices).length ? o.choices : null);
    var body = { spec: spec, rule: o.rule, rule_sig: o.answer.rule_sig, pairs: o.pids };
    if (o.choices && Object.keys(o.choices).length) body.choices = o.choices;
    return post(APPLY_URL, body).then(function (res) {
      if (seq !== _seq || sig !== specSig(ctx.spec(), o.rule, o.choices && Object.keys(o.choices).length ? o.choices : null)) {
        return { ok: false, stale: true, reason: "The chip changed while this was open" };
      }
      if (res.status === 409 || !res.body || res.body.ok === false) {
        return { ok: false, stale: true, reason: (res.body && res.body.error) || "The chip changed while this was open" };
      }
      var out = res.body, before = clone(spec);
      replaceInPlace(spec, out.spec);
      var rid = (out.rule && out.rule.id) || o.rule.id;
      if (o.after) o.after(spec, rid, out);
      pushUndo(spec, o.label, before, function () { ctx.changed(); refreshViews(); });
      resetRuleRuntime();
      ctx.changed();
      noteExpand({ id: rid }, null);
      refreshViews();
      return { ok: true, out: out, rid: rid };
    });
  }

  function doApply(d) {
    if (d.applyBtn.disabled) return;
    var pids = tickedPids(d);
    if (!pids.length) return;
    d.applying = true; renderFoot(d);
    var spec = d.ctx.spec(), seq = _seq, sig = currentSig(d);
    // The preview signature includes every choice, including unticked rows.
    var choices = clone(d.choices);
    var gname = (d.rule.gate && d.rule.gate.name) || "gate";
    commitApply({ ctx: d.ctx, rule: d.rule, answer: d.answer, pids: pids, choices: choices,
      label: "add " + gname + " to " + plural(pids.length, "pair"),
      after: function (sp, rid) {
        var stored = ruleById(sp, rid);
        if (stored && stored.auto_new !== d.laterEl.checked) stored.auto_new = d.laterEl.checked;
      } }).then(function (res) {
      d.applying = false;
      if (d.closed) return;
      if (!res.ok) {
        if (seq !== _seq || sig !== currentSig(d)) {
          markStale(d, describeChange(d.specSnap || {}, d.ctx.spec()));
          return;
        }
        d.status = "stale";
        d.staleReason = res.reason;
        d.specSnap = watchedOf(spec);
        render(d);
        d.cancelBtn.focus();
        recheck(d, "conflict", 0);   // ask again for the current answer; never re-send this apply
        return;
      }
      var out = res.out;
      closeApply(d, "applied");
      var msg = addedText(d.rule, out.added || []);
      if ((out.skipped || []).length) {
        msg += " " + plural(out.skipped.length, "pair") + " skipped: " + out.skipped.map(function (x) { return x.pair + " — " + x.reason; }).join("; ") + ".";
      }
      toast(msg, "info");
      focusCard(res.rid);
    }).catch(function (e) {
      d.applying = false;
      d.status = "error"; d.error = "Could not add the gate: " + String(e && e.message || e);
      render(d);
    });
  }

  function openApply(opts) {
    if (_open) closeApply(_open, "replaced");
    var d = { ctx: opts.ctx, rule: opts.rule, opener: opts.opener || document.activeElement, status: "loading",
              answer: null, ticks: {}, choices: {}, reqN: 0, pending: true, closed: false, applying: false,
              feedline: null, cache: null, landed: false };
    d.cache = opts.cache || function (answer, sig) { noteExpand(d.rule, answer, sig); };
    d.specSnap = watchedOf(d.ctx.spec());
    var dlg = buildDialog(d);
    document.body.appendChild(dlg);
    d.cancelBtn.addEventListener("click", function () { closeApply(d, "cancel"); });
    d.xBtn.addEventListener("click", function () { closeApply(d, "cancel"); });
    d.applyBtn.addEventListener("click", function () { doApply(d); });
    dlg.addEventListener("cancel", function (ev) { ev.preventDefault(); closeApply(d, "cancel"); });
    d.onDocKey = function (ev) {
      if (ev.key === "Escape" && !ev.defaultPrevented && !_menu && d.dlg.contains(ev.target || document.activeElement)) {
        ev.preventDefault(); closeApply(d, "esc");
      }
    };
    document.addEventListener("keydown", d.onDocKey, true);
    d.laterEl.addEventListener("change", function () { renderFoot(d); });
    _open = d;
    if (typeof dlg.showModal === "function") { try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }
    else dlg.setAttribute("open", "");
    d.count = (d.ctx.spec().qubit_pairs || []).length;
    render(d);
    d.cancelBtn.focus();
    d.slowTimer = setTimeout(function () { if (d.status === "loading") { d.slow = true; render(d); } }, 1000);
    sendExpand(d, "open");
    return d;
  }

  /* ---- the shared keyboard menu (C8 / C9) ------------------------------ */

  var _menu = null;
  function closeMenu(refocus) {
    var m = _menu;
    if (!m) return;
    _menu = null;
    document.removeEventListener("mousedown", m.onOutside, true);
    if (m.node.parentNode) m.node.parentNode.removeChild(m.node);
    m.btn.setAttribute("aria-expanded", "false");
    if (refocus) m.btn.focus();
  }

  function menu(btn, items, opts) {
    closeMenu(false);
    var node = el("div", { className: "gen-rule-menu", role: "menu", "aria-label": (opts && opts.label) || btn.getAttribute("aria-label") || "Menu" });
    var buttons = [];
    items.forEach(function (it) {
      if (it.separator) { node.appendChild(el("div", { className: "gen-rule-menu-sep", role: "separator" })); return; }
      var b = el("button", { type: "button", role: "menuitem", className: "gen-rule-menuitem", tabindex: "-1" }, it.label);
      if (it.checkbox) { b.setAttribute("role", "menuitemcheckbox"); b.setAttribute("aria-checked", it.checked ? "true" : "false"); }
      if (it.disabled) { b.setAttribute("aria-disabled", "true"); b.disabled = true; }
      else {
        b.addEventListener("click", function () { closeMenu(false); if (it.onSelect) it.onSelect(); });
      }
      node.appendChild(b);
      buttons.push(b);
    });
    var live = function () { return buttons.filter(function (b) { return !b.disabled; }); };
    node.addEventListener("keydown", function (ev) {
      var list = live(), i = list.indexOf(document.activeElement), k = ev.key;
      if (k === "ArrowDown") { ev.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (k === "ArrowUp") { ev.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      else if (k === "Home") { ev.preventDefault(); list[0].focus(); }
      else if (k === "End") { ev.preventDefault(); list[list.length - 1].focus(); }
      else if (k === "Enter" || k === " ") { ev.preventDefault(); if (document.activeElement && document.activeElement.click) document.activeElement.click(); }
      else if (k === "Escape") { ev.preventDefault(); ev.stopPropagation(); closeMenu(true); }
      else if (k === "Tab") { var m = _menu; closeMenu(false); if (m) m.btn.focus(); }   // default Tab then moves on from the button
    });
    var host = btn.parentNode;
    host.classList.add("gen-rule-menuhost");
    host.insertBefore(node, btn.nextSibling);
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "true");
    var onOutside = function (ev) { if (!node.contains(ev.target) && ev.target !== btn) closeMenu(false); };
    document.addEventListener("mousedown", onOutside, true);
    _menu = { node: node, btn: btn, onOutside: onOutside };
    var first = live()[0];
    if (first) first.focus();
    return node;
  }

  /* ---- what the last answers said, per rule (feeds badges and the card) ---- */

  var _status = {};          // rule id -> {rows: {pid: row}, sig}
  function noteExpand(rule, answer, sig) {
    if (!rule || !rule.id) return;
    if (!answer) { delete _status[rule.id]; return; }
    var rows = {};
    (answer.pairs || []).forEach(function (r) { rows[r.pair] = r; });
    _status[rule.id] = { rows: rows, sig: sig || null, rule_sig: answer.rule_sig };
    if (window.GenAdditions && window.GenAdditions.refresh) {
      Promise.resolve().then(function () { window.GenAdditions.refresh(); renderCards(); });
    }
  }
  function statusRow(ruleId, pid) {
    var st = _status[ruleId];
    return st && st.rows[pid] || null;
  }

  /* ---- model readers used by the Gates cell -------------------------------- */

  function pairParts(pid) {
    var s = String(pid), i = s.indexOf("-");
    return i < 0 ? [s, ""] : [s.slice(0, i), s.slice(i + 1)];
  }
  function sameKey(a, b) {
    var x = pairParts(a), y = pairParts(b);
    return unorderedKey(x[0], x[1]) === unorderedKey(y[0], y[1]);
  }
  function pidOfTarget(target) {
    var m = /^qubit_pairs\.([^.]+)\.macros$/.exec(String(target || ""));
    return m ? m[1] : null;
  }

  function gateInfo(spec, pid, add) {
    var rule = add && add.rule_id ? ruleById(spec, add.rule_id) : null;
    if (!rule) {
      rule = rulesOf(spec).filter(function (r) {
        return r.source && sameKey(r.source, pid) && r.gate && r.gate.name === add.name;
      })[0] || null;
      if (!rule) return { kind: "hand", rule: null };
    }
    if (rule.source && sameKey(rule.source, pid)) {
      // The person's own gate is still the source; a flip can still ask for a look.
      var srow = rule.id ? statusRow(rule.id, pid) : null;
      return { kind: "source", rule: rule, row: srow, look: !!(srow && srow.status === G_LOOK) };
    }
    var row = statusRow(rule.id, pid);
    var kind = row && row.status === G_LOOK ? "needs" : (row && row.edited ? "edited" : "rule");
    return { kind: kind, rule: rule, row: row };
  }

  // The pairs a rule is on: its instances, plus the source gate where that still exists.
  function countOn(spec, rule) {
    // Only gates on a pair the spec has count: a gate left on a pair id the spec
    // no longer names is not "on" anything.
    var live = {};
    (spec.qubit_pairs || []).forEach(function (p) {
      if (Array.isArray(p) && p.length === 2) live[pairIdOf(p)] = 1;
    });
    var seen = {};
    (spec.additions || []).forEach(function (a) {
      if (a.kind !== "macro" || a.name !== (rule.gate && rule.gate.name)) return;
      var pid = pidOfTarget(a.target);
      if (!pid || !live[pid]) return;
      if (a.rule_id === rule.id || (rule.source && sameKey(rule.source, pid))) {
        var p = pairParts(pid);
        seen[unorderedKey(p[0], p[1])] = 1;
      }
    });
    return Object.keys(seen).length;
  }

  /* ---- a rule from a gate that already exists --------------------------- */

  var ROLE_OF_FIELD = { flux_pulse_qubit: "moving", flux_pulse_high: "high", flux_pulse_low: "low",
                        flux_pulse_control: "control", flux_pulse_target: "target", coupler_flux_pulse: "coupler" };
  function pulseByPointer(spec, ptr) {
    var m = /^#\/(.+)\/([^/]+)$/.exec(String(ptr));
    if (!m) return null;
    var target = m[1].split("/").join(".");
    return (spec.additions || []).filter(function (a) { return a.kind === "pulse" && a.target === target && a.name === m[2]; })[0] || null;
  }
  function ruleFromGate(spec, pid, add) {
    var cz = (window.GenAdditions && window.GenAdditions.czItems && window.GenAdditions.czItems(add["class"])) || null;
    var items = [], names = {}, rest = {};
    Object.keys(add.fields || {}).forEach(function (fn) {
      var v = add.fields[fn];
      var pulse = typeof v === "string" && v.charAt(0) === "#" ? pulseByPointer(spec, v) : null;
      var role = null;
      if (cz) cz.forEach(function (c) { if (c.field === fn) role = c.on; });
      if (!role) role = ROLE_OF_FIELD[fn] || null;
      if (pulse && role) {
        var struct = {};
        Object.keys(pulse.fields || {}).forEach(function (k) {
          if (typeof pulse.fields[k] !== "number") struct[k] = clone(pulse.fields[k]);
        });
        items.push({ role: role, field: fn, enabled: true, pulse_class: pulse["class"], fields_struct: struct, on_choice: "auto" });
        names[role] = pulse.name;
      } else rest[fn] = clone(v);
    });
    return { kind: "pair_gate", source: pid, gate: { name: add.name, macro_class: add["class"], as_default: false },
             items: items, numbers: {}, fields: rest, source_names: items.length ? names : null,
             auto_new: false, excluded: [], reviewed: {} };
  }

  function applyToOthers(ctx, pid, add, opener) {
    var spec = ctx.spec();
    var info = gateInfo(spec, pid, add);
    var rule = info.rule ? clone(info.rule) : ruleFromGate(spec, pid, add);
    openApply({ ctx: ctx, rule: rule, opener: opener });
  }

  /* ---- the ✕ confirm of a rule's gate: two radios (4.3) ---------------------- */

  function confirmRemove(o, done) {
    var dlg = el("dialog", { className: "gen-rule-dlg gen-rule-mini", "aria-labelledby": "gen-rule-rm-title" });
    dlg.appendChild(el("p", { id: "gen-rule-rm-title", className: "gen-rule-rm-title" },
      "Remove " + o.gate + " from " + o.pid + "?  Its " + plural(o.pulses, "pulse") + " go with it."));
    var radios = {};
    [["this", "This pair only — exclude it from the rule"], ["all", "All " + o.total + " pairs"]].forEach(function (r) {
      var lab = el("label", { className: "gen-rule-rm-opt" });
      var inp = el("input", { type: "radio", name: "gen-rule-rm", value: r[0] });
      inp.checked = (o.preset || "this") === r[0];
      radios[r[0]] = inp;
      lab.appendChild(inp); lab.appendChild(document.createTextNode(" " + r[1]));
      dlg.appendChild(lab);
    });
    var foot = el("div", { className: "gen-rule-foot" });
    var rm = el("button", { type: "button", className: "gen-rule-rm-ok" }, "Remove");
    var cancel = el("button", { type: "button", className: "secondary gen-rule-rm-cancel" }, "Cancel");
    foot.appendChild(rm); foot.appendChild(cancel);
    dlg.appendChild(foot);
    document.body.appendChild(dlg);
    var finished = false;
    function finish(how) {
      if (finished) return;
      finished = true;
      document.removeEventListener("keydown", onKey, true);
      try { if (typeof dlg.close === "function") dlg.close(); } catch (e) { /* closed */ }
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (!how && o.opener && o.opener.isConnected) o.opener.focus();
      done(how);
    }
    function onKey(ev) { if (ev.key === "Escape") { ev.preventDefault(); finish(null); } }
    document.addEventListener("keydown", onKey, true);
    rm.addEventListener("click", function () { finish(radios.all.checked ? "all" : "this"); });
    cancel.addEventListener("click", function () { finish(null); });
    dlg.addEventListener("cancel", function (ev) { ev.preventDefault(); finish(null); });
    if (typeof dlg.showModal === "function") { try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }
    else dlg.setAttribute("open", "");
    (radios[o.preset || "this"] || radios.this).focus();
    return dlg;
  }

  /* ---- a rule-level undo entry (rules, additions, populate pairs) ----------- */

  function rulesTxn(spec, label, refresh) {
    var u = window._wizUndo;
    if (!u || typeof u.pushRestore !== "function") return;
    var snap = { additions: clone(spec.additions), rules: clone(spec.addition_rules),
                 pairs: spec.populate && spec.populate.pairs ? clone(spec.populate.pairs) : undefined };
    u.pushRestore(label, function () {
      if (snap.additions) spec.additions = clone(snap.additions); else delete spec.additions;
      if (snap.rules) spec.addition_rules = clone(snap.rules); else delete spec.addition_rules;
      if (snap.pairs) { spec.populate = spec.populate || {}; spec.populate.pairs = clone(snap.pairs); }
      else if (spec.populate) delete spec.populate.pairs;
      (refresh || refreshViews)();
    });
  }

  function ruleMenuItems(ctx, ruleId, btn) {
    var spec = ctx.spec(), rule = ruleById(spec, ruleId);
    if (!rule) return [{ label: "This rule is gone", disabled: true }];
    var n = countOn(spec, rule);
    var after = function () { ctx.changed(); refreshViews(); };
    return [
      { label: "Stop as a rule — keep the " + n + " gates", onSelect: function () {
          rulesTxn(spec, "stop rule " + rule.gate.name);
          (spec.additions || []).forEach(function (a) { if (a.rule_id === rule.id) { delete a.rule_id; delete a.copied_value; delete a.choice_here; } });
          spec.addition_rules = spec.addition_rules.filter(function (r) { return r.id !== rule.id; });
          if (!spec.addition_rules.length) delete spec.addition_rules;
          delete _status[rule.id];
          after();
          toast("Stopped rule " + rule.gate.name + " — its " + n + " gates stay as they are. Ctrl+Z undoes it.");
          focusCard(null, btn);
        } },
      { label: "Remove the rule and its " + n + " gates…", onSelect: function () {
          var ok = typeof window.confirm !== "function" ||
            window.confirm("Remove rule " + rule.gate.name + " and its " + n + " gates (with the pulses only they play)?");
          if (!ok) return;
          rulesTxn(spec, "remove rule " + rule.gate.name);
          if (window.GenAdditions && window.GenAdditions.removeRuleInstances) window.GenAdditions.removeRuleInstances(spec, rule);
          spec.addition_rules = spec.addition_rules.filter(function (r) { return r.id !== rule.id; });
          if (!spec.addition_rules.length) delete spec.addition_rules;
          delete _status[rule.id];
          after();
          toast("Removed rule " + rule.gate.name + " and its " + n + " gates. Ctrl+Z undoes it.");
          focusCard(null, btn);
        } },
      { separator: true },
      { label: "Also add to pairs created later", checkbox: true, checked: !!rule.auto_new, onSelect: function () {
          rulesTxn(spec, "auto-add " + rule.gate.name);
          rule.auto_new = !rule.auto_new;
          after();
          focusCard(rule.id, btn);
        } },
    ];
  }

  /* ---- Reset to rule and the review dialog (4.11) ----------------------------- */

  function fieldDiffHtml(d) {
    var pre = (d.name || d.kind) + "  " + d.field + "  ";
    var o = d.old == null ? "—" : String(d.old), n = d.new == null ? "—" : String(d.new);
    var s = pre + o + " → " + n;
    return s;
  }

  // "#/qubits/q2/z/operations/x" or "qubits.q2.z.operations" -> "q2 z" (also qubit_pairs). [derived]
  function placeOf(v) {
    if (typeof v !== "string") return null;
    var m = /^#\/(qubits|qubit_pairs)\/([^\/]+)\/([^\/]+)/.exec(v) || /^(qubits|qubit_pairs)\.([^.]+)\.([^.]+)/.exec(v);
    return m ? m[2] + " " + m[3] : null;
  }
  // The role token of an item name "<gate>_<role>_<control>_<target>", from the rule's own roles.
  function roleOfName(rule, name) {
    var g = ((rule.gate && rule.gate.name) || "") + "_";
    var rest = typeof name === "string" && name.indexOf(g) === 0 ? name.slice(g.length) : null;
    var roles = (rule.items || []).map(function (it) { return it.role; }).filter(Boolean)
      .sort(function (a, b) { return b.length - a.length; });
    for (var i = 0; rest && i < roles.length; i++) if (rest === roles[i] || rest.indexOf(roles[i] + "_") === 0) return roles[i];
    return null;
  }
  // One plain sentence per change kind for a review row: pulses that move to another
  // element, pulses renamed, and anything else as "name field: old -> new". The raw
  // diff stays available under Details. [derived]
  function reviewSummary(rule, diff, isSource) {
    var moves = [], seenMove = {}, renames = [], other = [];
    var removed = [], added = [], cls = {};
    diff.forEach(function (d) {
      if (d.field === "class_") { cls[d.name] = 1; if (d.old != null && d.new == null) removed.push(d.name); else if (d.old == null && d.new != null) added.push(d.name); }
    });
    var gone = {}; removed.concat(added).forEach(function (n) { gone[n] = 1; });
    function addMove(who, a, b) {
      if (!a || !b || a === b || seenMove[who]) return;
      seenMove[who] = 1; moves.push(who + " moves " + a + " → " + b);
    }
    diff.forEach(function (d) {
      if (d.field === "class_") return;
      var a = placeOf(d.old), b = placeOf(d.new);
      if (d.kind === "macro" && a && b) {
        var nm = String(d.new).split("/").pop(), role = roleOfName(rule, nm);
        addMove(role ? role + " pulse" : d.field, a, b);
        return;
      }
      if (d.field === "target" && gone[d.name]) return;
      if (d.field === "target" && a && b) { var r0 = roleOfName(rule, d.name); addMove(r0 ? r0 + " pulse" : d.name, a, b); return; }
      if (gone[d.name]) return;
      other.push(d.name + " " + d.field + ": " + (d.old == null ? "—" : d.old) + " → " + (d.new == null ? "—" : d.new));
    });
    var used = {};
    removed.forEach(function (o) {
      var ro = roleOfName(rule, o);
      var n = added.filter(function (x) { return !used[x] && roleOfName(rule, x) === ro; })[0];
      if (n) { used[n] = 1; renames.push(o + " → " + n); }
      else other.push("pulse " + o + " removed");
    });
    added.forEach(function (n) { if (!used[n]) other.push("pulse " + n + " added"); });
    var parts = moves.slice();
    if (renames.length) parts.push("pulses renamed " + renames.join(", "));
    parts = parts.concat(other);
    var kept = isSource && !renames.length && moves.length;
    return { parts: parts, kept: kept };
  }

  // mode "review": every Needs-a-look pair of the rule; mode "reset": one pair back to the rule.
  function openReview(opts) {
    var ctx = opts.ctx, mode = opts.mode || "review";
    var dlg = el("dialog", { className: "gen-rule-dlg gen-rule-review", "aria-labelledby": "gen-rule-rv-title" });
    var title = el("h3", { id: "gen-rule-rv-title" });
    var head = el("div", { className: "gen-rule-head" });
    var xb = el("button", { type: "button", className: "gen-rule-x secondary", "aria-label": "Close" }, "✕");
    head.appendChild(title); head.appendChild(xb);
    dlg.appendChild(head);
    var sub = el("p", { className: "gen-rule-sub" }, mode === "reset"
      ? "These fields go back to the rule's values. Nothing is written until you choose."
      : "Recalculated with the pairs' current facts. Nothing is written until you choose.");
    dlg.appendChild(sub);
    var list = el("div", { className: "gen-rule-rv-list" });
    dlg.appendChild(list);
    var foot = el("div", { className: "gen-rule-foot" });
    var keepAll = el("button", { type: "button", className: "secondary gen-rule-rv-keepall" }, "Keep all as is");
    var applyAll = el("button", { type: "button", className: "gen-rule-rv-applyall" }, "Apply all");
    if (mode !== "reset") { foot.appendChild(keepAll); foot.appendChild(applyAll); }
    dlg.appendChild(foot);
    document.body.appendChild(dlg);
    var r = { closed: false, answer: null, busy: false };
    function ruleNow() { return ruleById(ctx.spec(), opts.rule.id) || opts.rule; }
    function pending() {
      var rows = (r.answer && r.answer.pairs) || [];
      return rows.filter(function (row) {
        return mode === "reset" ? row.pair === opts.pid : row.status === G_LOOK;
      });
    }
    function close(refocus) {
      if (r.closed) return; r.closed = true;
      document.removeEventListener("keydown", onKey, true);
      abortRv();
      try { if (typeof dlg.close === "function") dlg.close(); } catch (e) { /* closed */ }
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (refocus && opts.opener && opts.opener.isConnected) opts.opener.focus();
    }
    var rvCtl = null;
    function abortRv() { if (rvCtl) { try { rvCtl.abort(); } catch (e) { /* done */ } rvCtl = null; } }
    function onKey(ev) { if (ev.key === "Escape" && !_menu) { ev.preventDefault(); close(true); } }
    document.addEventListener("keydown", onKey, true);
    xb.addEventListener("click", function () { close(true); });
    dlg.addEventListener("cancel", function (ev) { ev.preventDefault(); close(true); });

    function draw() {
      var rows = pending();
      list.innerHTML = "";
      title.textContent = mode === "reset"
        ? "Reset " + opts.pid + " to rule " + ruleNow().gate.name
        : ruleNow().gate.name + " — " + plural(rows.length, "pair") + " would change";
      applyAll.textContent = "Apply all " + plural(rows.length, "change");
      keepAll.disabled = applyAll.disabled = !rows.length || r.busy;
      if (!r.answer) { list.appendChild(el("p", { className: "muted" }, "Checking…")); return; }
      if (!rows.length) {
        list.appendChild(el("p", { className: "gen-rule-rv-none" }, mode === "reset" ? "Nothing differs from the rule here." : "Nothing is waiting for a look."));
        if (mode !== "reset") { dlg.setAttribute("data-empty", "1"); }
        return;
      }
      rows.forEach(function (row) {
        var box = el("div", { className: "gen-rule-rv-pair", "data-pair": row.pair });
        box.appendChild(el("p", { className: "gen-rule-rv-head" }, row.pair + " — " +
          (mode === "reset" ? "edited here" : (row.reason || "facts changed"))));
        var rnow = ruleNow(), diff = row.diff_vs_current || [];
        var sm = reviewSummary(rnow, diff, !!(rnow.source && sameKey(rnow.source, row.pair)));
        sm.parts.forEach(function (t) { box.appendChild(el("div", { className: "gen-rule-rv-plain" }, t)); });
        if (sm.kept) box.appendChild(el("div", { className: "gen-rule-rv-kept muted" },
          "Pulse names you chose for this pair are kept; only where the pulses play changes."));
        var det = el("details", { className: "gen-rule-rv-details" });
        det.appendChild(el("summary", {}, "Details"));
        if (diff.length) box.appendChild(det);
        diff.forEach(function (d) {
          var line = el("div", { className: "gen-rule-rv-line" }, fieldDiffHtml(d));
          if (typeof d.old === "number" && typeof d.new === "number" && window.ValueDelta && window.ValueDelta.chipHtml) {
            var chip = el("span", { className: "gen-rule-rv-delta" });
            chip.innerHTML = window.ValueDelta.chipHtml(d.old, d.new);
            line.appendChild(chip);
          }
          det.appendChild(line);
        });
        var btns = el("div", { className: "gen-rule-rv-btns" });
        var keep = el("button", { type: "button", className: "secondary gen-rule-rv-keep", "data-pair": row.pair },
          mode === "reset" ? "Cancel" : "Keep as is");
        var app = el("button", { type: "button", className: "gen-rule-rv-apply", "data-pair": row.pair },
          mode === "reset" ? "Reset to rule" : "Apply change");
        btns.appendChild(app); btns.appendChild(keep);
        box.appendChild(btns);
        keep.addEventListener("click", function () {
          if (mode === "reset") { close(true); return; }
          keepPairs([row]);
        });
        app.addEventListener("click", function () { applyPairs([row]); });
        list.appendChild(box);
      });
    }

    function load(why) {
      abortRv();
      r.busy = true;
      var ctl = typeof AbortController === "function" ? new AbortController() : null;
      rvCtl = ctl;
      var seq = _seq, spec = ctx.spec(), rule = ruleNow(), sig = specSig(spec, rule, null);
      post(EXPAND_URL, { spec: spec, rule: rule }, ctl && ctl.signal).then(function (res) {
        if (r.closed || seq !== _seq || sig !== specSig(ctx.spec(), ruleNow(), null)) return;
        r.busy = false;
        if (!res.body || res.body.ok === false) { r.answer = null; list.innerHTML = ""; list.appendChild(el("p", { className: "gen-rule-status-error" }, (res.body && res.body.error) || "Could not check the pairs.")); return; }
        r.answer = res.body;
        noteExpand(rule, res.body, sig);
        draw();
        if (why === "open") {
          var first = list.querySelector(mode === "reset" ? ".gen-rule-rv-apply" : ".gen-rule-rv-keep");
          (first || xb).focus();
        } else if (!pending().length) { close(true); }
      }).catch(function (e) { if (!(e && e.name === "AbortError")) { r.busy = false; } });
    }

    function applyPairs(rows) {
      if (r.busy || !rows.length) return;
      r.busy = true; draw();
      var rule = ruleNow();
      commitApply({ ctx: ctx, rule: rule, answer: r.answer, pids: rows.map(function (x) { return x.pair; }),
                    label: mode === "reset" ? "reset " + opts.pid + " to rule " + rule.gate.name
                      : (rows.length === 1 ? "apply change to " + rows[0].pair : "apply " + rows.length + " changes") })
        .then(function (res) {
          r.busy = false;
          if (!res.ok) { r.answer = null; draw(); load("again"); return; }
          toast((mode === "reset" ? "Reset " + opts.pid + " to rule " + rule.gate.name : "Applied " + plural(rows.length, "change")) + ". Ctrl+Z undoes it.");
          load("again");
        });
    }
    function keepPairs(rows) {
      var spec = ctx.spec(), rule = ruleById(spec, opts.rule.id);
      if (!rule) return;
      rulesTxn(spec, "keep " + rows.length + " pair" + (rows.length === 1 ? "" : "s") + " as is");
      rule.reviewed = rule.reviewed || {};
      rows.forEach(function (row) { rule.reviewed[row.key] = row.instance_sig; });
      ctx.changed();
      resetRuleRuntimeKeepOpen();
      renderCards();
      if (window.GenAdditions && window.GenAdditions.refresh) window.GenAdditions.refresh();
      load("again");
    }
    keepAll.addEventListener("click", function () { keepPairs(pending()); });
    applyAll.addEventListener("click", function () { applyPairs(pending()); });
    if (typeof dlg.showModal === "function") { try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }
    else dlg.setAttribute("open", "");
    draw();
    xb.focus();
    load("open");
    return { dlg: dlg, close: close };
  }
  // A rule edit (reviewed) changes the question, not the content: bump nothing else.
  function resetRuleRuntimeKeepOpen() { abortInFlight(); }

  function resetToRule(ctx, rule, pid, opener) {
    openReview({ ctx: ctx, rule: rule, mode: "reset", pid: pid, opener: opener });
  }

  /* ---- the Rules card (4.12) and the Review lines (4.13) -------------------- */

  var _cardCtx = null, _cardTable = null;
  var _statusErr = null;       // the server's words when the last status refresh failed

  function bindCards(ctx, table) {
    _cardCtx = ctx; _cardTable = table;
    renderCards();
    refreshStatuses(ctx);
  }

  function pairIdOf(p) {
    return window.GenAdditions && window.GenAdditions.pairId ? window.GenAdditions.pairId(String(p[0]), String(p[1])) : String(p[0]) + "-" + String(p[1]);
  }

  function statusFresh(spec, rule) {
    var st = rule.id && _status[rule.id];
    return !!st && st.sig === specSig(spec, rule, null);
  }

  // Ask the server again for every rule whose last answer is not for the current content.
  function refreshStatuses(ctx, force, ids) {
    var spec = ctx.spec();
    var todo = rulesOf(spec).filter(function (r) { return r.id && (!ids || ids.indexOf(r.id) >= 0) && (force || !statusFresh(spec, r)); });
    if (!todo.length) return Promise.resolve([]);
    var seq = _seq;
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    if (_bg.ctl) { try { _bg.ctl.abort(); } catch (e) { /* done */ } }
    _bg.ctl = ctl;
    return Promise.all(todo.map(function (rule) {
      var sig = specSig(spec, rule, null);
      return post(EXPAND_URL, { spec: spec, rule: rule }, ctl && ctl.signal).then(function (res) {
        if (seq !== _seq || sig !== specSig(ctx.spec(), ruleById(ctx.spec(), rule.id) || rule, null)) return null;
        if (!res.body || res.body.ok === false) {
          _statusErr = (res.body && res.body.error) || "the server did not answer";
          return null;
        }
        _statusErr = null;
        noteExpand(rule, res.body, sig);
        return res.body;
      }).catch(function (e) {
        if (!(e && e.name === "AbortError")) _statusErr = String(e && e.message || e);
        return null;
      });
    }));
  }

  function newReadyRows(rule) {
    var st = _status[rule.id];
    if (!st) return [];
    return Object.keys(st.rows).map(function (k) { return st.rows[k]; })
      .filter(function (r) { return r.status === G_READY && !r.existing; });
  }
  function needRows(rule) {
    var st = _status[rule.id];
    if (!st) return [];
    return Object.keys(st.rows).map(function (k) { return st.rows[k]; }).filter(function (r) { return r.status === G_LOOK; });
  }
  function editedRows(rule) {
    var st = _status[rule.id];
    if (!st) return [];
    return Object.keys(st.rows).map(function (k) { return st.rows[k]; }).filter(function (r) { return r.edited; });
  }

  function cardModel(spec, rule) {
    var total = (spec.qubit_pairs || []).length, n = countOn(spec, rule);
    var cls = shortName(rule.gate.macro_class);
    var info = window.GenAdditions && window.GenAdditions.classInfo ? window.GenAdditions.classInfo(rule.gate.macro_class) : null;
    var parts = [rule.gate.name, (info && info.label ? info.label + " (" + cls + ")" : cls)];
    if (rule.source) {
      var alive = (spec.qubit_pairs || []).some(function (p) { return sameKey(rule.source, pairIdOf(p)); });
      parts.push("from " + rule.source + (alive ? "" : " (pair deleted)"));
    } else parts.push("for all pairs (no source)");
    var cr = spec.pair_gate === "cr";
    if (cr) parts.push("not applicable to cross-resonance — " + plural(n, "gate") + " kept, nothing applies");
    else {
      parts.push("on " + n + " of " + total + " pairs");
      var ed = editedRows(rule).length;
      if (ed) parts.push(ed + " edited here");
    }
    var needs = cr ? [] : needRows(rule), fresh = cr ? [] : newReadyRows(rule);
    var second = [];
    if (needs.length) second.push("◐ " + needs.length + " need" + (needs.length === 1 ? "s" : "") + " a look (" + (needs[0].reason || "pair facts changed") + ")");
    if (fresh.length) second.push(plural(fresh.length, "new pair") + " " + fresh.map(function (r) { return r.pair; }).join(", "));
    return { line1: parts.join(" · "), line2: second.join("  ·  "), needs: needs, fresh: fresh, cr: cr };
  }

  function cardBtn(label, cls, onClick) {
    var b = el("button", { type: "button", className: "secondary " + cls }, label);
    b.addEventListener("click", onClick);
    return b;
  }

  function renderCards() {
    var ctx = _cardCtx;
    if (!ctx || !_cardTable || !_cardTable.isConnected) return;
    var spec = ctx.spec(), rules = rulesOf(spec);
    var host = document.getElementById("gen-rule-cards");
    if (!host) {
      host = el("div", { id: "gen-rule-cards", className: "gen-rule-cards" });
      var anchor = _cardTable.closest("#gen-pop-pairs") || _cardTable.parentNode;
      anchor.parentNode.insertBefore(host, anchor);
    }
    var focusedCls = null, focusedRule = null;
    var act = document.activeElement;
    if (act && host.contains(act)) {
      focusedCls = (act.className || "").split(" ").filter(function (c) { return /^gen-rule-card-/.test(c); })[0] || null;
      focusedRule = act.closest ? (act.closest(".gen-rule-card") || {}).getAttribute && act.closest(".gen-rule-card").getAttribute("data-rule") : null;
    }
    var keep = {};
    rules.forEach(function (rule) {
      var card = host.querySelector('.gen-rule-card[data-rule="' + rule.id + '"]');
      if (!card) {
        card = el("div", { className: "gen-rule-card", "data-rule": rule.id, tabindex: "-1", role: "group", "aria-live": "polite" });
        host.appendChild(card);
      }
      keep[rule.id] = true;
      card.setAttribute("aria-label", "Rule " + rule.gate.name);
      var m = cardModel(spec, rule);
      card.innerHTML = "";
      card.appendChild(el("div", { className: "gen-rule-card-line1" }, m.line1));
      if (m.line2) card.appendChild(el("div", { className: "gen-rule-card-line2" }, m.line2));
      var btns = el("div", { className: "gen-rule-card-btns" });
      if (m.needs.length) btns.appendChild(cardBtn("Review " + m.needs.length + "…", "gen-rule-card-review", function () {
        openReview({ ctx: ctx, rule: ruleById(ctx.spec(), rule.id) || rule, mode: "review", opener: btns.querySelector(".gen-rule-card-review") });
      }));
      if (m.fresh.length) btns.appendChild(cardBtn(m.fresh.length === 1 ? "Add to " + m.fresh[0].pair : "Add to " + m.fresh.length + " new pairs", "gen-rule-card-addnew", function () {
        openApply({ ctx: ctx, rule: clone(ruleById(ctx.spec(), rule.id) || rule), opener: btns.querySelector(".gen-rule-card-addnew") });
      }));
      if (!m.cr) btns.appendChild(cardBtn("Apply to more pairs…", "gen-rule-card-more", function () {
        openApply({ ctx: ctx, rule: clone(ruleById(ctx.spec(), rule.id) || rule), opener: btns.querySelector(".gen-rule-card-more") });
      }));
      btns.appendChild(cardBtn("Edit rule…", "gen-rule-card-edit", function () { editRule(ctx, rule.id, btns.querySelector(".gen-rule-card-edit")); }));
      var mb = el("button", { type: "button", className: "secondary gen-rule-card-menu", "aria-label": "Actions for rule " + rule.gate.name,
                              "aria-haspopup": "menu", "aria-expanded": "false" }, "⋯");
      mb.addEventListener("click", function () { menu(mb, ruleMenuItems(ctx, rule.id, mb), { label: "Rule " + rule.gate.name }); });
      btns.appendChild(mb);
      card.appendChild(btns);
    });
    Array.prototype.forEach.call(host.querySelectorAll(".gen-rule-card"), function (c) {
      if (!keep[c.getAttribute("data-rule")]) c.parentNode.removeChild(c);
    });
    if (focusedCls && focusedRule) {
      var again = host.querySelector('.gen-rule-card[data-rule="' + focusedRule + '"] .' + focusedCls);
      if (again) again.focus();
    }
  }

  function editRule(ctx, ruleId, opener) {
    var spec = ctx.spec(), rule = ruleById(spec, ruleId);
    var tr = _cardTable && _cardTable.querySelector("tr.gen-pop-setall");
    if (!rule || !tr || !window.GenAdditions || !window.GenAdditions.openRuleForm) return;
    var n = (spec.qubit_pairs || []).length;
    window.GenAdditions.openRuleForm(tr, ctx, opener, clone(rule), "Save → review " + plural(n, "pair"), function (built) {
      var sp = ctx.spec();
      rulesTxn(sp, "edit rule " + built.gate.name);
      sp.addition_rules = sp.addition_rules.map(function (r) { return r.id === built.id ? built : r; });
      delete _status[built.id];
      resetRuleRuntime();
      ctx.changed();
      if (window.GenAdditions.refresh) window.GenAdditions.refresh();
      renderCards();
      openReview({ ctx: ctx, rule: built, mode: "review", opener: opener });
    });
  }

  /* The Review step's lines: computed from the MODEL (+ the last answers), never from the DOM,
   * because the Additions list may not be mounted (mountAdditionsWhenNear). Pure. */
  function ruleReviewCounts(spec, status, err) {
    status = status || {};
    var out = [];
    rulesOf(spec).forEach(function (rule) {
      var gates = 0, numbers = 0, pairs = {};
      (spec.additions || []).forEach(function (a) {
        if (a.rule_id !== rule.id) return;
        numbers += Object.keys(a.copied_value || {}).filter(function (field) {
          return (a.fields || {})[field] === a.copied_value[field];
        }).length;
        if (a.kind === "macro") {
          gates++;
          pairs[a.target] = 1;
        }
      });
      var line = "rule " + rule.gate.name + ": " + plural(gates, "gate") + " on " + plural(Object.keys(pairs).length, "pair");
      if (numbers) line += " · " + numbers + " numbers are copied initial values from " + (rule.source || "the form") + " (recalibrate per pair)";
      var st = status[rule.id];
      var unreviewed = st ? Object.keys(st.rows).filter(function (k) { return st.rows[k].status === G_LOOK; }).length : 0;
      if (unreviewed) line += " · " + plural(unreviewed, "pair") + " " + (unreviewed === 1 ? "has" : "have") + " unreviewed changes (Populate › Rule › Review)";
      if (err) line += " · rules not checked: " + err;
      out.push(line);
    });
    return out;
  }

  function renderReviewLines(box, spec) {
    var lines = ruleReviewCounts(spec, _status, _settleErr);
    if (!lines.length || !box) return null;
    var ul = el("ul", { className: "gen-add-review-list gen-rule-review-lines" });
    lines.forEach(function (l) { ul.appendChild(el("li", { className: "gen-rule-review-line" }, l)); });
    box.appendChild(ul);
    return ul;
  }
  var _settleErr = null;

  function focusCard(id, fallback) {
    var card = id && document.querySelector('.gen-rule-card[data-rule="' + id + '"]');
    if (card) { card.focus(); return; }
    if (fallback && fallback.isConnected) fallback.focus();
  }
  /* A CZ orientation flip renames one pair id (no qubit renamed): a rule's `source`
   * follows the pair. `excluded` / `reviewed` keys are flip-stable already. */
  function flipPairRules(rules, oldPid, newPid) {
    var out = clone(rules || []);
    out.forEach(function (r) {
      if (r && typeof r === "object" && r.source === oldPid) r.source = newPid;
    });
    return out;
  }
  /* A qubit rename (generate.js applyQubitIdMap) re-expressed in the rules: `source`, the
   * keys of `excluded`/`reviewed` and the values of `source_names` follow by the rebuild's own
   * token rule ("_"-delimited tokens, longest first, so q1 -> qA leaves q10 alone). map = {old: new};
   * qubitIds / pairs = the spec's BEFORE the rename. Python twin: core/additions.py::rename_rules. */
  function renameRules(rules, map, qubitIds, pairs) {
    var tok = {};
    Object.keys(map || {}).forEach(function (o) { if (String(map[o]) !== o) tok[o] = String(map[o]); });
    var out = clone(rules || []);
    if (!Object.keys(tok).length) return out;
    var seen = {};
    var every = Object.keys(tok).concat((qubitIds || []).map(String)).filter(function (k) {
      if (!k || seen[k]) return false; seen[k] = true; return true;
    }).sort(function (a, b) { return b.length - a.length; });
    var pat = new RegExp("(?<![^_])(?:" + every.map(function (k) {
      return k.replace(/[.*+?^${}()|[\]\-]/g, "\$&");
    }).join("|") + ")(?![^_])", "g");
    var rename = function (t) { return String(t).replace(pat, function (m) { return tok.hasOwnProperty(m) ? tok[m] : m; }); };
    var pmap = {}, kmap = {};
    var pairId = window.GenAdditions && window.GenAdditions.pairId;
    (pairs || []).forEach(function (p) {
      if (!Array.isArray(p) || p.length !== 2) return;
      var c = String(p[0]), t = String(p[1]);
      var nc = tok.hasOwnProperty(c) ? tok[c] : c, nt = tok.hasOwnProperty(t) ? tok[t] : t;
      if (pairId) pmap[pairId(c, t)] = pairId(nc, nt);
      kmap[unorderedKey(c, t)] = unorderedKey(nc, nt);
    });
    function rekey(obj, keyMap) {
      if (!obj || typeof obj !== "object") return obj;
      var o = {};
      Object.keys(obj).forEach(function (k) { o[keyMap.hasOwnProperty(k) ? keyMap[k] : k] = obj[k]; });
      return o;
    }
    out.forEach(function (r) {
      if (!r || typeof r !== "object") return;
      if (typeof r.source === "string") r.source = pmap.hasOwnProperty(r.source) ? pmap[r.source] : r.source;
      if (Array.isArray(r.excluded)) r.excluded = r.excluded.map(function (k) { return kmap.hasOwnProperty(k) ? kmap[k] : k; });
      if (r.reviewed) r.reviewed = rekey(r.reviewed, kmap);
      if (r.source_names && typeof r.source_names === "object") {
        Object.keys(r.source_names).forEach(function (role) {
          if (typeof r.source_names[role] === "string") r.source_names[role] = rename(r.source_names[role]);
        });
      }
    });
    return out;
  }

  // Reasons that must never make the rules write anything on their own (C1).
  var BLOCKED = { restore: 1, hydrate: 1, draft: 1, csv_import: 1, board_undo: 1 };
  var _lastReason = null;
  var REFRESH_MS = 400;

  function hasRules(spec) { return rulesOf(spec).length > 0; }

  function scheduleRefresh() {
    if (!_cardCtx || !hasRules(_cardCtx.spec())) return;
    if (_bg.timer) clearTimeout(_bg.timer);
    _bg.timer = setTimeout(function () { _bg.timer = null; refreshStatuses(_cardCtx); }, REFRESH_MS);
  }

  /* The wizard's ONE funnel: every spec write path says why. It only records and re-asks;
   * writing happens at settle, never here. */
  function specChanged(reason, spec) {
    if (reason !== "save") _lastReason = reason || "edit";   // an autosave is not a decision
    var d = _open;
    if (d && !d.closed && !d.applying) {
      var sig = currentSig(d);
      if (sig !== d.reqSig) {
        if (d.status === "ready" || d.status === "stale") {
          d.changeText = describeChange(d.specSnap || {}, d.ctx.spec());
          markStale(d, d.changeText);
        } else if (d.status === "loading") {
          recheck(d, "moved", RECHECK_MS);
        }
      }
    }
    scheduleRefresh();
  }

  /* Settle (C1 / C15): ask once for every rule, show what could not be checked, and -- only for a
   * rule with auto_new and only when the last change was a person's -- add the gate to the pairs
   * created since, as its OWN undo entry. Never rejects; a failure is a sentence for Review. */
  function settleOne(ctx, id, blocked) {
    var rule = ruleById(ctx.spec(), id);
    if (!rule) return Promise.resolve();
    return refreshStatuses(ctx, true, [id]).then(function (answers) {
      if (!answers[0] || answers[0].pending) {
        _settleErr = _statusErr || (answers[0] && answers[0].pending
          ? "The environment manifest is still warming." : "The chip changed while checking rules.");
        return null;
      }
      if (_statusErr) { _settleErr = _statusErr; return null; }
      if (blocked || !rule.auto_new) return null;
      var rows = newReadyRows(rule), st = _status[id];
      if (!rows.length || !st) return null;
      var pids = rows.map(function (r) { return r.pair; });
      var gname = rule.gate.name;
      return commitApply({ ctx: ctx, rule: clone(ruleById(ctx.spec(), id)), answer: { rule_sig: st.rule_sig }, pids: pids,
        label: "auto-added " + gname + " to " + plural(pids.length, "new pair") }).then(function (res) {
        if (!res.ok) { _settleErr = res.reason; return null; }
        toast("Pairs " + pids.join(", ") + " added earlier · " + gname + " added to them too (rule). Ctrl+Z undoes it.");
        return null;
      });
    });
  }
  function settle(ctx, reason) {
    if (_bg.timer) { clearTimeout(_bg.timer); _bg.timer = null; }
    _settleErr = null; _statusErr = null;
    var spec = ctx.spec();
    if (!hasRules(spec)) return Promise.resolve({ ok: true });
    var blocked = !!(BLOCKED[reason] || BLOCKED[_lastReason]);
    var chain = Promise.resolve();
    rulesOf(spec).map(function (r) { return r.id; }).forEach(function (id) {
      chain = chain.then(function () { return settleOne(ctx, id, blocked); });
    });
    return chain.then(function () { return _settleErr ? { ok: false, error: _settleErr } : { ok: true }; },
                      function (e) { _settleErr = String(e && e.message || e); return { ok: false, error: _settleErr }; });
  }

  window.GenRules = {
    settle: settle, hasRules: hasRules, BLOCKED: BLOCKED,
    bindCards: bindCards, refreshStatuses: refreshStatuses, ruleReviewCounts: ruleReviewCounts,
    renderReviewLines: renderReviewLines, editRule: editRule, statusFresh: statusFresh,
    setSettleError: function (e) { _settleErr = e || null; },
    renameRules: renameRules, flipPair: flipPairRules, rulesOf: rulesOf, ruleById: ruleById, unorderedKey: unorderedKey, specSig: specSig,
    resetRuleRuntime: resetRuleRuntime, reset: resetRuleRuntime, specChanged: specChanged,
    openApply: openApply, closeApply: function () { if (_open) closeApply(_open, "cancel"); },
    menu: menu, closeMenu: closeMenu, renderCards: renderCards,
    gateInfo: gateInfo, countOn: countOn, pairParts: pairParts, applyToOthers: applyToOthers,
    resetToRule: resetToRule, confirmRemove: confirmRemove, ruleMenuItems: ruleMenuItems, openReview: openReview,
    ruleFromGate: ruleFromGate, noteExpand: noteExpand, statusRow: statusRow, commitApply: commitApply,
    _state: function () { return { seq: _seq, open: _open }; },
  };
})();
