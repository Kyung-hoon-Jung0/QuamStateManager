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
    var seen = {}, parts = [];
    Object.keys(numbers || {}).forEach(function (k) {
      var f = k.slice(k.indexOf(".") + 1), v = numbers[k], key = f + "=" + v;
      if (seen[key]) return;
      seen[key] = 1;
      parts.push(f + " " + v + (/(^|_)length$/.test(f) ? " ns" : ""));
    });
    var shown = parts.slice(0, 3).join(", ") + (parts.length > 3 ? ", …" : "");
    if (!parts.length) return "";
    return sourceLabel
      ? "Numbers copied from " + sourceLabel + " (current values) as initial values: " + shown
      : "Initial values from the form: " + shown;
  }

  function classLine(rule) {
    var cls = (rule.gate && rule.gate.macro_class) || "";
    var info = window.GenAdditions && window.GenAdditions.classInfo ? window.GenAdditions.classInfo(cls) : null;
    var label = info && info.label ? info.label : shortName(cls);
    var line = label + " (" + shortName(cls) + (info && info.origin === "lab" ? ", lab class" : "") + ") · gate " +
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
    d.applyBtn.disabled = !(d.status === "ready" && !d.pending && !d.applying && n > 0);
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

  function doApply(d) {
    if (d.applyBtn.disabled) return;
    var pids = tickedPids(d);
    if (!pids.length) return;
    d.applying = true; renderFoot(d);
    var spec = d.ctx.spec(), seq = _seq, sig = currentSig(d);
    var choices = {};
    pids.forEach(function (p) { if (d.choices[p]) choices[p] = d.choices[p]; });
    var body = { spec: spec, rule: d.rule, rule_sig: d.answer.rule_sig, pairs: pids };
    if (Object.keys(choices).length) body.choices = choices;
    post(APPLY_URL, body).then(function (res) {
      d.applying = false;
      if (d.closed) return;
      if (seq !== _seq || sig !== currentSig(d)) { renderFoot(d); return; }   // content moved: the stale path owns it
      if (res.status === 409 || !res.body || res.body.ok === false) {
        d.status = "stale";
        d.staleReason = (res.body && res.body.error) || "The chip changed while this was open";
        d.specSnap = watchedOf(spec);
        render(d);
        d.cancelBtn.focus();
        recheck(d, "conflict", 0);   // ask again for the current answer; never re-send this apply
        return;
      }
      var out = res.body, next = out.spec;
      var label = "add " + ((d.rule.gate && d.rule.gate.name) || "gate") + " to " + plural((out.added || []).length, "pair");
      var before = clone(spec);
      replaceInPlace(spec, next);
      var rid = (out.rule && out.rule.id) || d.rule.id;
      var stored = ruleById(spec, rid);
      if (stored && stored.auto_new !== d.laterEl.checked) stored.auto_new = d.laterEl.checked;
      pushUndo(spec, label, before, function () { d.ctx.changed(); refreshViews(); });
      closeApply(d, "applied");
      resetRuleRuntime();
      d.ctx.changed();
      if (d.cache) d.cache(null, null);
      refreshViews();
      var msg = addedText(d.rule, out.added || []);
      if ((out.skipped || []).length) {
        msg += " " + plural(out.skipped.length, "pair") + " skipped: " + out.skipped.map(function (s) { return s.pair + " — " + s.reason; }).join("; ") + ".";
      }
      toast(msg, "info");
      focusCard(rid);
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
              feedline: null, cache: opts.cache || null, landed: false };
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

  /* ---- placeholders the later steps fill ------------------------------- */

  function renderCards() { /* 6e */ }
  function focusCard(id) {
    var card = document.querySelector('.gen-rule-card[data-rule="' + id + '"]');
    if (card) card.focus();
  }
  function specChanged(reason, spec) {
    var d = _open;
    if (!d || d.closed || d.applying) return;
    var sig = currentSig(d);
    if (sig === d.reqSig) return;
    if (d.status === "ready" || d.status === "stale") {
      d.changeText = describeChange(d.specSnap || {}, d.ctx.spec());
      markStale(d, d.changeText);
    } else if (d.status === "loading") {
      recheck(d, "moved", RECHECK_MS);
    }
  }

  window.GenRules = {
    rulesOf: rulesOf, ruleById: ruleById, unorderedKey: unorderedKey, specSig: specSig,
    resetRuleRuntime: resetRuleRuntime, reset: resetRuleRuntime, specChanged: specChanged,
    openApply: openApply, closeApply: function () { if (_open) closeApply(_open, "cancel"); },
    menu: menu, closeMenu: closeMenu, renderCards: renderCards,
    _state: function () { return { seq: _seq, open: _open }; },
  };
})();
