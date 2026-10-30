/* Generate Config -- lab additions (phase 2: pulses; phase 3: macros + the CZ group).
 *
 * window.GenAdditions:
 *   channels(spec)            -> [{target, label, qubit}] the channels a pulse can go on
 *   owners(spec)              -> [{target, label, kind: "qubit"|"pair", qubit|pair, control, target}]
 *   pairId(a, b)              -> the builder's own pair id (q1-2), never re-derived differently
 *   mount(host, ctx)          -> renders the additions section; ctx = {spec(), changed()}
 *   renderResult(report)      -> a DOM node listing added / refused additions
 *   outcome(report)           -> "ok" | "mixed" | "refused" | "none"
 *   renderReview(box, info)   -> the Review step's list of additions (before Generate)
 *
 * Presets (phase 4): "+ Readout…" on a resonator (a readout pulse, plus a
 * measure macro pointing at it where the env's quam_builder has one) and
 * "+ Pulsed mode…" on a TWPA (field additions: extras.twpa_pulse_mode, the
 * pump_ pulse the builder writes, active_twpa_names where the root declares it).
 *
 * The class lists come from the selected env's role manifest (/generate/roles):
 * official and lab classes are labelled, every field form is generated from the
 * class's own fields (defaults prefilled, docstring as hover). Nothing here
 * decides what is valid -- the build checks each addition against the built
 * chip and the environment, and refuses one alone.
 */
(function () {
  "use strict";

  var QUBIT_CH = { drive: "xy", flux: "z", resonator: "resonator" };
  var roles = null;          // last /generate/roles answer
  var rolesLoading = false;
  var INFERRED_HINT = "Inferred from the shape: the class computes this from its other fields. " +
    "Leave it as is unless you want to fix it to a number.";

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "className") e.className = attrs[k];
      else if (k === "hidden") e.hidden = !!attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    if (text != null) e.textContent = text;
    return e;
  }

  function bareIndex(q) {
    var s = String(q);
    return (s.charAt(0) === "q" || s.charAt(0) === "Q") ? s.slice(1) : s;
  }

  // The builder's own pair key (run_build._quam_pair_id): TopoGraph.quamPairId
  // when loaded, else the same rule.
  function pairId(a, b) {
    if (window.TopoGraph && window.TopoGraph.quamPairId) return window.TopoGraph.quamPairId([a, b]);
    return "q" + bareIndex(a) + "-" + bareIndex(b);
  }

  function shortName(path) { return String(path).split(".").pop(); }

  function channels(spec) {
    var out = [];
    var lines = (spec && spec.lines) || [];
    (spec.qubits || []).forEach(function (q) {
      ["drive", "flux", "resonator"].forEach(function (lt) {
        var has = lines.some(function (ln) { return ln && ln.line === lt && String(ln.element) === String(q); });
        if (has) out.push({ target: "qubits." + q + "." + QUBIT_CH[lt] + ".operations",
                            label: q + " · " + QUBIT_CH[lt], qubit: String(q) });
      });
    });
    (spec.qubit_pairs || []).forEach(function (p) {
      if (!Array.isArray(p) || p.length !== 2) return;
      var long = p[0] + "-" + p[1];
      var has = lines.some(function (ln) { return ln && ln.line === "coupler" && String(ln.element) === long; });
      var id = pairId(String(p[0]), String(p[1]));
      if (has) out.push({ target: "qubit_pairs." + id + ".coupler.operations",
                          label: id + " · coupler", qubit: null, pair: id });
    });
    return out;
  }

  function hasLine(spec, line, element) {
    return ((spec && spec.lines) || []).some(function (ln) {
      return ln && ln.line === line && String(ln.element) === String(element);
    });
  }

  function owners(spec) {
    var out = [];
    (spec.qubits || []).forEach(function (q) {
      out.push({ target: "qubits." + q + ".macros", label: String(q), kind: "qubit", qubit: String(q) });
    });
    Object.keys((displayFacts || {}).pairs || {}).forEach(function (key) {
      out.push(displayFacts.pairs[key]);
    });
    return out;
  }

  function list(spec) { return Array.isArray(spec.additions) ? spec.additions : []; }

  var displayFacts = null, displaySig = null, displayLoading = false;
  function ensureDisplay(spec, done) {
    var inputs = { qubits: spec.qubits, qubit_pairs: spec.qubit_pairs, lines: spec.lines,
                   populate: spec.populate, pair_gate: spec.pair_gate };
    var sig = JSON.stringify(inputs);
    if (displaySig === sig) return true;
    if (!displayLoading) {
      displayLoading = true;
      fetch("/generate/roles?spec=" + encodeURIComponent(sig)).then(function (r) { return r.json(); })
        .then(function (res) { displayLoading = false; displayFacts = res.display; displaySig = sig; done(); })
        .catch(function () { displayLoading = false; });
    }
    return false;
  }

  function loadRoles(probe, modules, done) {
    if (rolesLoading) return;
    rolesLoading = true;
    var req = probe
      ? fetch("/generate/roles", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ modules: modules || [] }) })
      : fetch("/generate/roles" + (modules && modules.length ? "?modules=" + encodeURIComponent(modules.join(",")) : ""));
    req.then(function (r) { return r.json(); }).then(function (res) {
      rolesLoading = false; roles = res; done();
    }).catch(function (e) {
      rolesLoading = false; roles = { ok: false, error: String(e) }; done();
    });
  }

  function parseValue(text, raw) {
    var t = String(text);
    if (/^\s*(<class 'str'>|str)\s*$/.test(raw || "")) return t;
    if (t.charAt(0) === "#") return t;
    try { return JSON.parse(t); } catch (e) { return t; }
  }

  function defaultText(f) {
    var d = f.default_repr;
    if (d == null || d === "<factory>") return "";
    if (d === "None") return "";
    if (d === "True") return "true";
    if (d === "False") return "false";
    if (/^'.*'$/.test(d)) return d.slice(1, -1);
    return d;
  }

  function isInferred(f) { return /^'?#\.\/inferred_/.test(String(f.default_repr || "")); }

  function valueText(v) {
    if (v == null) return "";
    if (typeof v === "string") return v;
    return JSON.stringify(v);
  }

  // A class <select>: each option reads the class name and its origin, the module path on hover (P2-UX-4).
  function classSelect(classes, label, filter) {
    var sel = el("select", { className: "gen-add-class", "aria-label": label });
    sel.appendChild(el("option", { value: "" }, "Choose a class…"));
    var paths = Object.keys(classes || {}).sort(function (a, b) {
      return shortName(a).localeCompare(shortName(b)) || a.localeCompare(b);
    });
    ["lab", "official"].forEach(function (origin) {
      var grp = el("optgroup", { label: origin === "lab" ? "Lab classes" : "Official classes" });
      paths.forEach(function (p) {
        if ((classes[p].origin || "official") !== origin) return;
        if (filter && !filter(classes[p])) return;
        var o = el("option", { value: p }, shortName(p) + " · " + origin);
        o.title = p + (classes[p].doc ? "\n" + classes[p].doc : "");
        grp.appendChild(o);
      });
      if (grp.children.length) sel.appendChild(grp);
    });
    sel.addEventListener("change", function () {
      sel.title = sel.value || "";
    });
    return sel;
  }

  /* The field inputs of one class. opts: {skip: [names], values: {name: v},
   * candidates(fieldName) -> [pointer strings], idPrefix}. Returns {collect()}
   * where collect() -> {fields, missing}. */
  function fieldsForm(box, cls, opts) {
    opts = opts || {};
    var skip = opts.skip || [];
    var values = opts.values || {};
    Object.keys(cls.fields || {}).forEach(function (fn) {
      var f = cls.fields[fn];
      if (fn === "id" || skip.indexOf(fn) >= 0) return;
      var lab = el("label", { className: "gen-add-field" + (f.has_default ? "" : " gen-add-required") });
      lab.appendChild(el("span", { className: "gen-add-fname", title: f.doc || "" },
        fn + (f.has_default ? "" : " *")));
      var inferred = isInferred(f);
      var inp = el("input", { type: "text", "data-field": fn, "data-raw": f.raw || "",
                              "data-default": defaultText(f),
                              title: (inferred ? INFERRED_HINT + "\n" : "") + (f.doc ? f.doc + "\n" : "") +
                                     "type: " + (f.raw || "?") + " · kind: " + f.kind });
      if (inferred) { inp.classList.add("gen-add-inferred"); lab.classList.add("gen-add-inferred-field"); }
      inp.value = (fn in values) ? valueText(values[fn]) : defaultText(f);
      var cands = opts.candidates ? opts.candidates(fn, f) : [];
      if (cands.length) {
        var dlId = "gen-add-dl-" + (opts.idPrefix || "x").replace(/\W/g, "_") + "-" + fn;
        var dl = el("datalist", { id: dlId });
        cands.forEach(function (c) { dl.appendChild(el("option", { value: c })); });
        inp.setAttribute("list", dlId);
        lab.appendChild(dl);
        inp.classList.add("gen-add-pointable");
      }
      lab.appendChild(inp);
      if (inferred) lab.appendChild(el("span", { className: "gen-add-hint muted", title: INFERRED_HINT }, "inferred"));
      box.appendChild(lab);
    });
    return {
      collect: function () {
        var fields = {}, missing = [];
        Array.prototype.forEach.call(box.querySelectorAll("input[data-field]"), function (inp) {
          var fn = inp.getAttribute("data-field"), v = inp.value;
          var f = cls.fields[fn];
          if (!f) return;
          if (v === "") { if (!f.has_default) missing.push(fn); return; }
          if (f.has_default && v === inp.getAttribute("data-default")) return;   // the class default applies
          fields[fn] = parseValue(v, inp.getAttribute("data-raw"));
        });
        return { fields: fields, missing: missing };
      },
    };
  }

  function qubitPointers(q) {
    return ((roles && roles.qubit_fields) || []).map(function (n) { return "#/qubits/" + q + "/" + n; });
  }

  // Pulses a macro field can point at: pulse ADDITIONS of this spec on the
  // qubits / coupler the field plays on (the built chip's own pulses are
  // checked at build time; a name may be typed).
  function pulsePointers(spec, owner, fieldName) {
    var out = [];
    var on = null;
    if (owner.kind === "pair") {
      if (fieldName === "flux_pulse_target") on = [owner.target_q];
      else if (fieldName === "coupler_flux_pulse") on = ["coupler"];
      else on = [owner.control, owner.target_q, "coupler"];
    } else on = [owner.qubit];
    list(spec).forEach(function (a) {
      if (a.kind === "macro") return;
      var m = /^qubits\.([^.]+)\.([^.]+)\.operations$/.exec(a.target || "");
      if (m && on.indexOf(m[1]) >= 0) out.push("#/qubits/" + m[1] + "/" + m[2] + "/operations/" + a.name);
      var c = /^qubit_pairs\.([^.]+)\.coupler\.operations$/.exec(a.target || "");
      if (c && owner.kind === "pair" && c[1] === owner.pair && on.indexOf("coupler") >= 0)
        out.push("#/qubit_pairs/" + c[1] + "/coupler/operations/" + a.name);
    });
    return out;
  }

  /* One addition form (a pulse on a channel, or a macro on a qubit / pair).
   * cfg: {kind, target, classes, classLabel, owner|ch, existing:{index, add}} */
  /* The knowledge card a class follows (its own or its base's), or "no card":
   * then only the field form and the environment's check apply. */
  function cardLine(cls) {
    var c = cls && cls.card;
    if (!c) {
      return el("p", { className: "gen-add-card gen-add-nocard muted",
                       title: "No knowledge card covers this class: no placement or naming convention is applied." },
        "No card — field form and the environment's check only.");
    }
    var ptr = Object.keys(c.pointers || {}).map(function (f) { return f + " → " + c.pointers[f]; });
    return el("p", { className: "gen-add-card muted", "data-card": c.id,
                     title: (c.notes || "") + (c.origin === "overlay" ? "\n(lab overlay card)" : "") },
      "Card: " + (c.title || c.id) + (c.placement && c.placement.target ? " · goes on " + c.placement.target : "") +
      (ptr.length ? " · " + ptr.join(", ") : ""));
  }

  function renderForm(box, cfg, ctx, rerender) {
    box.innerHTML = "";
    var classes = cfg.classes || {};
    var ex = cfg.existing ? cfg.existing.add : null;
    var form = el("div", { className: "gen-add-form", "data-target": cfg.target, "data-kind": cfg.kind });
    var row = el("div", { className: "gen-add-row" });
    var sel = classSelect(classes, cfg.classLabel || "Class");
    var name = el("input", { className: "gen-add-name", type: "text",
                             placeholder: cfg.kind === "macro" ? "macro name" : "operation name",
                             "aria-label": cfg.kind === "macro" ? "Macro name" : "Operation name" });
    row.appendChild(sel); row.appendChild(name);
    form.appendChild(row);
    var fieldsBox = el("div", { className: "gen-add-fields" });
    form.appendChild(fieldsBox);
    var msg = el("p", { className: "gen-add-msg muted" });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-ok" }, ex ? "Save" : "Add");
    var cancel = el("button", { type: "button", className: "btn-sm secondary gen-add-cancel" }, "Cancel");
    actions.appendChild(ok); actions.appendChild(cancel);
    form.appendChild(actions); form.appendChild(msg);
    box.appendChild(form);
    var ff = null;

    function draw(values) {
      fieldsBox.innerHTML = "";
      ff = null;
      var cls = classes[sel.value];
      if (!cls) return;
      fieldsBox.appendChild(el("p", { className: "gen-add-origin muted", title: sel.value },
        (cls.origin === "lab" ? "Lab class" : "Official class") + (cls.doc ? " — " + cls.doc : "")));
      fieldsBox.appendChild(cardLine(cls));
      if (!ex && !name.value && cls.default_name) name.value = cls.default_name;
      ff = fieldsForm(fieldsBox, cls, {
        values: values, idPrefix: cfg.target,
        candidates: function (fn, f) {
          var out = [];
          if (cfg.kind === "macro") {
            if (f.kind === "pointer_pulse" || f.kind === "dict_of_pointers")
              out = pulsePointers(ctx.spec(), cfg.owner, fn);
            return out;
          }
          var ch = cfg.ch;
          if (ch.qubit) out = out.concat(qubitPointers(ch.qubit));
          Object.keys(cls.fields || {}).forEach(function (n) { if (n !== fn) out.push("#./" + n); });
          list(ctx.spec()).forEach(function (a) {
            if (a.target === ch.target && a.kind !== "macro") out.push("#../" + a.name);
          });
          return out;
        },
      });
    }
    sel.addEventListener("change", function () { draw({}); });
    if (ex) {
      sel.value = ex["class"]; sel.title = ex["class"]; name.value = ex.name;
      draw(ex.fields || {});
    }
    cancel.addEventListener("click", function () { box.innerHTML = ""; });
    ok.addEventListener("click", async function () {
      var cls = classes[sel.value];
      var nm = (name.value || "").trim();
      if (!cls) { msg.textContent = "Choose a class."; return; }
      if (!/^[A-Za-z0-9_\-]+$/.test(nm)) { msg.textContent = "Name: letters, digits, '_' or '-'."; return; }
      var spec = ctx.spec();
      var skipIndex = cfg.existing ? cfg.existing.index : -1;
      if (list(spec).some(function (a, i) { return i !== skipIndex && a.target === cfg.target && a.name === nm; })) {
        msg.textContent = nm + " is already added here."; return;
      }
      var got = ff ? ff.collect() : { fields: {}, missing: [] };
      if (got.missing.length) { msg.textContent = "Required: " + got.missing.join(", "); return; }
      var add = { kind: cfg.kind, target: cfg.target, name: nm, "class": sel.value, fields: got.fields };
      if (cfg.kind === "macro" && cfg.target.indexOf("qubit_pairs.") === 0) {
        var judged = await postJSON("/generate/additions/lint", {
          spec: Object.assign({}, spec, { additions: list(spec).filter(function (a, i) { return i !== skipIndex; }) }),
          addition: add
        });
        if (!judged.body.ok) { msg.textContent = judged.body.errors.join("; "); return; }
      }
      additionsTxn(spec, (cfg.existing ? "edit " : "add ") + cfg.kind + " " + nm);
      if (!Array.isArray(spec.additions)) spec.additions = [];
      if (cfg.existing) spec.additions[cfg.existing.index] = add;
      else spec.additions.push(add);
      ctx.changed();
      rerender();
    });
  }

  function recipeOn(item, owner) {
    var facts = ((displayFacts || {}).pairs || {})[owner.long];
    return facts && facts.roles[item.on] || null;
  }

  /* The one-click CZ group on a pair: a CZ macro class, the flux pulse(s) it
   * plays as pulse additions, and the macro pointing at them. No number is
   * filled in that the class does not default. */
  function renderCzGroup(box, owner, ctx, rerender) {
    box.innerHTML = "";
    var macros = (roles && roles.macros_pair) || {};
    var czClasses = {};
    Object.keys(macros).forEach(function (p) { if (macros[p].cz) czClasses[p] = macros[p]; });
    var pulses = (roles && roles.pulses) || {};
    var form = el("div", { className: "gen-add-form gen-add-cz", "data-target": owner.target });
    form.appendChild(el("p", { className: "gen-add-cz-head" },
      "CZ on " + owner.pair + " — a gate class, the flux pulse(s) it plays, and the gate pointing at them. " +
      "Everything stays editable in the list before you generate."));
    var row = el("div", { className: "gen-add-row" });
    var sel = classSelect(czClasses, "CZ gate class");
    sel.classList.add("gen-add-cz-class");
    var name = el("input", { className: "gen-add-name", type: "text", placeholder: "macro name", "aria-label": "Macro name" });
    row.appendChild(sel); row.appendChild(name);
    form.appendChild(row);
    var body = el("div", { className: "gen-add-cz-body" });
    form.appendChild(body);
    var msg = el("p", { className: "gen-add-msg muted" });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-ok" }, "Add CZ");
    var cancel = el("button", { type: "button", className: "btn-sm secondary gen-add-cancel" }, "Cancel");
    actions.appendChild(ok); actions.appendChild(cancel);
    form.appendChild(actions); form.appendChild(msg);
    box.appendChild(form);
    if (!Object.keys(czClasses).length) {
      body.appendChild(el("p", { className: "muted" }, "This environment offers no flux CZ gate class."));
      ok.disabled = true;
    }
    var parts = [];   // [{item, nameInp, clsSel, ff, onSel, enabled}]
    var macroFF = null;

    function draw() {
      body.innerHTML = ""; parts = []; macroFF = null;
      var cls = czClasses[sel.value];
      if (!cls) return;
      var slug = shortName(sel.value).replace(/[^A-Za-z0-9]+/g, "_").toLowerCase().replace(/^cz_?(gate_?)?/, "") || "gate";
      if (!name.value || name.getAttribute("data-auto") === "1") {
        name.value = cls.default_name || ("cz_" + slug); name.setAttribute("data-auto", "1");
      }
      body.appendChild(el("p", { className: "gen-add-origin muted", title: sel.value },
        (cls.origin === "lab" ? "Lab class" : "Official class") + (cls.doc ? " — " + cls.doc.split("\n")[0] : "")));
      cls.cz.forEach(function (item) {
        if (item.on === "coupler" && !owner.coupler) return;
        var part = { item: item, enabled: true };
        var sec = el("div", { className: "gen-add-cz-pulse", "data-field": item.field });
        var head = el("div", { className: "gen-add-row" });
        var title = el("span", { className: "gen-add-cz-label" }, item.field + " →");
        head.appendChild(title);
        if (item.on === "coupler" && !item.required) {
          var cb = el("input", { type: "checkbox", className: "gen-add-cz-use", "aria-label": "Add a coupler pulse" });
          cb.checked = false; part.enabled = false;
          var cbl = el("label", { className: "gen-add-cz-usel" });
          cbl.appendChild(cb);
          cbl.appendChild(document.createTextNode("also a coupler pulse (optional)"));
          head.appendChild(cbl);
          cb.addEventListener("change", function () { part.enabled = cb.checked; inner.hidden = !cb.checked; });
        }
        var where = recipeOn(item, owner, ctx.spec());
        if (item.on === "moving" || ((item.on === "high" || item.on === "low") && !where)) {
          var on = el("select", { className: "gen-add-cz-on", "aria-label": "Plays on" });
          [["control", owner.control], ["target", owner.target_q]].forEach(function (o) {
            on.appendChild(el("option", { value: o[1] }, o[1] + " (" + o[0] + ")"));
          });
          var mv = ((((ctx.spec().populate || {}).pairs) || {})[owner.long] || {}).moving_qubit;
          if (mv === "target") on.value = owner.target_q;
          head.appendChild(on);
          part.onSel = on;
          head.appendChild(el("span", { className: "muted gen-add-cz-note" }, item.on === "moving"
            ? "sets this pair's moving qubit for the whole build" : "idle frequencies not known yet: choose"));
        } else {
          head.appendChild(el("span", { className: "gen-add-cz-where" },
            where === "coupler" ? owner.pair + " coupler"
              : where + " z" + (item.on === "high" ? " (higher f_01)" : item.on === "low" ? " (lower f_01)" : "")));
        }
        sec.appendChild(head);
        var inner = el("div", { className: "gen-add-cz-inner" });
        if (!part.enabled) inner.hidden = true;
        var r2 = el("div", { className: "gen-add-row" });
        var pc = classSelect(pulses, "Pulse class");
        pc.classList.add("gen-add-cz-pclass");
        var pn = el("input", { className: "gen-add-name gen-add-cz-pname", type: "text",
                               readOnly: true, placeholder: "Named for this pair when added",
                               "aria-label": "Pulse name" });
        r2.appendChild(pc); r2.appendChild(pn);
        inner.appendChild(r2);
        var pf = el("div", { className: "gen-add-fields" });
        inner.appendChild(pf);
        pc.addEventListener("change", function () {
          pf.innerHTML = ""; part.ff = null;
          var c = pulses[pc.value];
          if (c) part.ff = fieldsForm(pf, c, { idPrefix: owner.target + "-" + item.field });
        });
        sec.appendChild(inner);
        part.nameInp = pn; part.clsSel = pc; part.where = where;
        parts.push(part);
        body.appendChild(sec);
      });
      var rest = el("div", { className: "gen-add-fields gen-add-cz-macro" });
      body.appendChild(el("p", { className: "muted gen-add-cz-sub" }, "Gate fields"));
      body.appendChild(rest);
      macroFF = fieldsForm(rest, cls, { skip: cls.cz.map(function (i) { return i.field; }),
                                         idPrefix: owner.target + "-macro" });
    }
    sel.addEventListener("change", draw);
    name.addEventListener("input", function () { name.setAttribute("data-auto", "0"); });
    cancel.addEventListener("click", function () { box.innerHTML = ""; });
    ok.addEventListener("click", async function () {
      if (ok.disabled) return;
      var cls = czClasses[sel.value], nm = (name.value || "").trim();
      if (!cls) { msg.textContent = "Choose a CZ gate class."; return; }
      var spec = ctx.spec(), snapshot = JSON.stringify(spec), problems = [];
      var items = parts.filter(function (part) { return part.enabled; }).map(function (part) {
        var got = part.ff ? part.ff.collect() : { fields: {}, missing: [] };
        if (got.missing.length) problems.push(part.item.field + " pulse requires " + got.missing.join(", "));
        return { role: part.item.on, field: part.item.field, enabled: true,
          on_choice: part.onSel ? (part.onSel.value === owner.control ? "control" : "target") : "auto",
          pulse_class: part.clsSel.value, fields: got.fields };
      });
      var mg = macroFF ? macroFF.collect() : { fields: {}, missing: [] };
      if (mg.missing.length) problems.push("gate requires " + mg.missing.join(", "));
      if (problems.length) { msg.textContent = problems.join("; "); return; }
      ok.disabled = true;
      try {
        var response = await postJSON("/generate/additions/gate-action", { spec: spec, pair: owner.long,
          action: { gate: { name: nm, macro_class: sel.value }, items: items, fields: mg.fields } });
        var result = response.body;
        if (!box.contains(form) || JSON.stringify(ctx.spec()) !== snapshot) { msg.textContent = "The spec changed; try again."; return; }
        if (!result.ok) { msg.textContent = result.errors.join("; "); return; }
        additionsTxn(spec, "add gate " + nm + " on " + result.owner.pair);
        if (!Array.isArray(spec.additions)) spec.additions = [];
        result.additions.forEach(function (a) { spec.additions.push(a); });
        if (result.moving_qubit) {
          spec.populate = spec.populate || {};
          spec.populate.pairs = spec.populate.pairs || {};
          spec.populate.pairs[result.owner.long] = spec.populate.pairs[result.owner.long] || {};
          spec.populate.pairs[result.owner.long].moving_qubit = result.moving_qubit;
        }
        ctx.changed(); rerender();
      } catch (e) { msg.textContent = "Could not check the gate: " + String(e); }
      finally { ok.disabled = false; }
    });
  }

  function slug(path) { return shortName(path).replace(/[^A-Za-z0-9]+/g, "_").toLowerCase(); }

  function fieldLabel(target, value) {
    return target + " = " + (typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value));
  }

  /* The readout preset on a resonator channel: a readout pulse class (lab or
   * official), and -- only where this env's quam_builder ships a transmon
   * MeasureMacro -- a measure macro on the qubit pointing at that pulse. */
  function renderReadoutGroup(box, ch, ctx, rerender) {
    box.innerHTML = "";
    var pulses = (roles && roles.pulses) || {};
    var ro = {};
    Object.keys(pulses).forEach(function (p) { if (pulses[p].readout) ro[p] = pulses[p]; });
    var cap = (roles && roles.readout) || { mode: "pulse_only", reason: "unknown" };
    var mpath = roles && roles.measure_macro;
    var mcls = mpath && ((roles.macros_qubit || {})[mpath]);
    var withMacro = cap.mode === "pulse_and_macro" && !!mcls;
    var form = el("div", { className: "gen-add-form gen-add-ro", "data-target": ch.target });
    form.appendChild(el("p", { className: "gen-add-cz-head" },
      "Readout on " + ch.qubit + " — a readout pulse on the resonator" +
      (withMacro ? " and a measure macro on the qubit that plays it." : ".")));
    var row = el("div", { className: "gen-add-row" });
    var sel = classSelect(ro, "Readout pulse class");
    sel.classList.add("gen-add-ro-class");
    var name = el("input", { className: "gen-add-name gen-add-ro-name", type: "text",
                             placeholder: "operation name", "aria-label": "Readout pulse name" });
    name.setAttribute("data-auto", "1");
    row.appendChild(sel); row.appendChild(name);
    form.appendChild(row);
    var fbox = el("div", { className: "gen-add-fields" });
    form.appendChild(fbox);
    var mrow = el("div", { className: "gen-add-row gen-add-ro-macro" });
    var cb = null, mname = null;
    if (withMacro) {
      cb = el("input", { type: "checkbox", className: "gen-add-ro-use", "aria-label": "Also a measure macro" });
      cb.checked = true;
      var cbl = el("label", { className: "gen-add-cz-usel" });
      cbl.appendChild(cb);
      cbl.appendChild(document.createTextNode("also a measure macro (" + shortName(mpath) + ") on " + ch.qubit + " named"));
      mrow.appendChild(cbl);
      mname = el("input", { className: "gen-add-name gen-add-ro-mname", type: "text", value: "measure",
                            "aria-label": "Measure macro name" });
      mrow.appendChild(mname);
      cb.addEventListener("change", function () { mname.disabled = !cb.checked; });
    } else {
      mrow.appendChild(el("span", { className: "muted gen-add-ro-only" },
        "Pulse only: " + (cap.reason || "no measure macro class in this environment") +
        ", so measurement goes through the pulse itself."));
    }
    form.appendChild(mrow);
    var msg = el("p", { className: "gen-add-msg muted" });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-ok" }, "Add readout");
    var cancel = el("button", { type: "button", className: "btn-sm secondary gen-add-cancel" }, "Cancel");
    actions.appendChild(ok); actions.appendChild(cancel);
    form.appendChild(actions); form.appendChild(msg);
    box.appendChild(form);
    if (!Object.keys(ro).length) {
      fbox.appendChild(el("p", { className: "muted" }, "This environment offers no readout pulse class."));
      ok.disabled = true;
    }
    var ff = null;
    sel.addEventListener("change", function () {
      fbox.innerHTML = ""; ff = null;
      var c = ro[sel.value];
      if (!c) return;
      if (name.getAttribute("data-auto") === "1") {
        var stem = slug(sel.value).replace(/(readout)?pulse$/, "");
        name.value = c.default_name || ("readout" + (stem ? "_" + stem : ""));
      }
      fbox.appendChild(el("p", { className: "gen-add-origin muted", title: sel.value },
        (c.origin === "lab" ? "Lab class" : "Official class") + (c.doc ? " — " + c.doc.split("\n")[0] : "") +
        " · array fields (weights) take a JSON list, e.g. [0.1, 0.2]"));
      ff = fieldsForm(fbox, c, { idPrefix: ch.target + "-ro" });
    });
    name.addEventListener("input", function () { name.setAttribute("data-auto", "0"); });
    cancel.addEventListener("click", function () { box.innerHTML = ""; });
    ok.addEventListener("click", function () {
      var c = ro[sel.value];
      var nm = (name.value || "").trim();
      if (!c) { msg.textContent = "Choose a readout pulse class."; return; }
      if (!/^[A-Za-z0-9_\-]+$/.test(nm)) { msg.textContent = "Name: letters, digits, '_' or '-'."; return; }
      var spec = ctx.spec();
      var got = ff ? ff.collect() : { fields: {}, missing: [] };
      if (got.missing.length) { msg.textContent = "Required: " + got.missing.join(", "); return; }
      var adds = list(spec);
      if (adds.some(function (a) { return a.target === ch.target && a.name === nm; })) {
        msg.textContent = nm + " is already added here."; return;
      }
      var macro = null;
      if (cb && cb.checked) {
        var mn = (mname.value || "").trim();
        var mt = "qubits." + ch.qubit + ".macros";
        if (!/^[A-Za-z0-9_\-]+$/.test(mn)) { msg.textContent = "Macro name: letters, digits, '_' or '-'."; return; }
        if (adds.some(function (a) { return a.target === mt && a.name === mn; })) {
          msg.textContent = mn + " is already added on " + ch.qubit + "."; return;
        }
        macro = { kind: "macro", target: mt, name: mn, "class": mpath,
                  fields: { pulse: "#/qubits/" + ch.qubit + "/resonator/operations/" + nm } };
      }
      additionsTxn(spec, "add readout " + nm + " on " + ch.qubit);
      if (!Array.isArray(spec.additions)) spec.additions = [];
      spec.additions.push({ kind: "pulse", target: ch.target, name: nm, "class": sel.value, fields: got.fields });
      if (macro) spec.additions.push(macro);
      ctx.changed();
      rerender();
    });
  }

  // Does the chip's root declare active_twpa_names? true / false / null (unknown)
  function rootDeclares(spec, field) {
    var roots = (roles && roles.roots) || {};
    if (spec.quam_class) return roots[spec.quam_class] ? !!roots[spec.quam_class][field] : null;
    var builders = Object.keys(roots).filter(function (r) { return roots[r].extras && roots[r].origin !== "lab"; });
    if (!builders.length) return null;
    var yes = builders.filter(function (r) { return roots[r][field]; }).length;
    return yes === builders.length ? true : (yes === 0 ? false : null);
  }

  /* Pulsed TWPA, honestly: not a class. extras.twpa_pulse_mode, the pump
   * pulse on the non-sticky pump_ channel (the builder writes one named
   * "pump"; its length / amplitude are set here only if given), and
   * active_twpa_names when the chip's root declares it. */
  function renderTwpaGroup(box, t, ctx, rerender) {
    box.innerHTML = "";
    var spec = ctx.spec();
    var form = el("div", { className: "gen-add-form gen-add-twpa", "data-twpa": t });
    form.appendChild(el("p", { className: "gen-add-cz-head" },
      "Pulsed mode on " + t + " — not a class: a flag in extras, the pump pulse on the non-sticky pump_ " +
      "channel, and the active TWPA list where the chip's root has one. The lab code that reads the flag " +
      "is yours; this only writes the state it reads."));
    var frow = el("div", { className: "gen-add-row" });
    var flag = el("label", { className: "gen-add-cz-usel" });
    var fcb = el("input", { type: "checkbox", className: "gen-add-tw-flag", "aria-label": "extras.twpa_pulse_mode" });
    fcb.checked = true;
    flag.appendChild(fcb);
    flag.appendChild(document.createTextNode(" extras.twpa_pulse_mode = true"));
    frow.appendChild(flag);
    form.appendChild(frow);
    form.appendChild(el("p", { className: "muted gen-add-cz-sub" },
      "pump_ pulse \"pump\" (the builder writes it): nothing is prefilled; a blank value keeps what the builder writes."));
    var pbox = el("div", { className: "gen-add-fields" });
    var len = el("input", { type: "text", className: "gen-add-tw-len", placeholder: "blank = the builder's length (ns)", "aria-label": "Pump length" });
    var amp = el("input", { type: "text", className: "gen-add-tw-amp", placeholder: "blank = the builder's amplitude", "aria-label": "Pump amplitude" });
    [["length", len], ["amplitude", amp]].forEach(function (x) {
      var lab = el("label", { className: "gen-add-field" });
      lab.appendChild(el("span", { className: "gen-add-fname" }, x[0]));
      lab.appendChild(x[1]);
      pbox.appendChild(lab);
    });
    form.appendChild(pbox);
    var decl = rootDeclares(spec, "active_twpa_names");
    var acb = null, aval = null;
    var arow = el("div", { className: "gen-add-row gen-add-tw-active" });
    if (decl === false) {
      arow.appendChild(el("span", { className: "muted gen-add-tw-noactive" },
        "active_twpa_names: not set — the chip's root does not declare it in this environment" +
        (roles && roles.versions && roles.versions.quam_builder ? " (quam_builder " + roles.versions.quam_builder + ")" : "") + "."));
    } else {
      acb = el("input", { type: "checkbox", className: "gen-add-tw-use-active", "aria-label": "Set active_twpa_names" });
      acb.checked = decl === true;
      var al = el("label", { className: "gen-add-cz-usel" });
      al.appendChild(acb);
      al.appendChild(document.createTextNode(" active_twpa_names ="));
      arow.appendChild(al);
      aval = el("input", { type: "text", className: "gen-add-tw-active-val", value: JSON.stringify([t]),
                           "aria-label": "active_twpa_names value" });
      arow.appendChild(aval);
      if (decl === null) arow.appendChild(el("span", { className: "muted" },
        "the root is chosen at build time; refused there if it does not declare the field"));
    }
    form.appendChild(arow);
    var msg = el("p", { className: "gen-add-msg muted" });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-ok" }, "Add pulsed mode");
    var cancel = el("button", { type: "button", className: "btn-sm secondary gen-add-cancel" }, "Cancel");
    actions.appendChild(ok); actions.appendChild(cancel);
    form.appendChild(actions); form.appendChild(msg);
    box.appendChild(form);
    cancel.addEventListener("click", function () { box.innerHTML = ""; });
    ok.addEventListener("click", function () {
      var out = [];
      var problems = [];
      if (fcb.checked) out.push({ kind: "field", target: "extras.twpa_pulse_mode", value: true });
      var base = "twpas." + t + ".pump_.operations.pump.";
      [["length", len, true], ["amplitude", amp, false]].forEach(function (x) {
        var v = (x[1].value || "").trim();
        if (v === "") return;
        var n = Number(v);
        if (!isFinite(n) || (x[2] && Math.round(n) !== n)) { problems.push(x[0] + " must be a " + (x[2] ? "whole number" : "number")); return; }
        out.push({ kind: "field", target: base + x[0], value: n });
      });
      if (acb && acb.checked) {
        var parsed = null;
        try { parsed = JSON.parse(aval.value); } catch (e) { parsed = null; }
        if (!Array.isArray(parsed)) problems.push("active_twpa_names must be a JSON list, e.g. [\"" + t + "\"]");
        else out.push({ kind: "field", target: "active_twpa_names", value: parsed });
      }
      if (problems.length) { msg.textContent = problems.join("; "); return; }
      if (!out.length) { msg.textContent = "Nothing to add."; return; }
      var spec2 = ctx.spec();
      additionsTxn(spec2, "add pulsed mode on " + t);
      if (!Array.isArray(spec2.additions)) spec2.additions = [];
      out.forEach(function (f) {
        var i = -1;
        spec2.additions.forEach(function (a, j) { if (a.kind === "field" && a.target === f.target) i = j; });
        if (i >= 0) spec2.additions[i] = f; else spec2.additions.push(f);
      });
      ctx.changed();
      rerender();
    });
  }

  function fieldRow(spec, a, i, host, ctx) {
    var r = el("div", { className: "gen-add-item gen-add-field-item", "data-index": String(i) });
    r.appendChild(el("span", { className: "gen-add-ch-label" }, fieldLabel(a.target, a.value)));
    r.appendChild(el("span", { className: "muted" }, " · field"));
    var rm = el("button", { type: "button", className: "btn-sm secondary gen-add-rm", title: "Remove this field" }, "✕");
    rm.addEventListener("click", function () {
      additionsTxn(spec, "remove field " + a.target);
      spec.additions.splice(i, 1);
      if (!spec.additions.length) delete spec.additions;
      ctx.changed(); mount(host, ctx);
    });
    r.appendChild(rm);
    return r;
  }

  function itemRow(spec, a, i, host, ctx, box, cfgFor) {
    var r = el("div", { className: "gen-add-item", "data-index": String(i) });
    var pool = a.kind === "macro"
      ? (roles && (roles.macros_pair || {})[a["class"]]) || (roles && (roles.macros_qubit || {})[a["class"]])
      : roles && roles.pulses && roles.pulses[a["class"]];
    var origin = (pool && pool.origin) || "";
    var edit = el("button", { type: "button", className: "btn-sm secondary gen-add-edit", title: "Edit this addition" }, a.name);
    r.appendChild(edit);
    r.appendChild(el("span", { className: "muted", title: String(a["class"]) }, " " + shortName(a["class"]) +
      (origin ? " · " + origin : "") + " · " + Object.keys(a.fields || {}).length + " fields set" +
      (a.proposed_by ? " · proposed by " + String(a.proposed_by).replace(/^by_/, "") + ", accepted" : "")));
    var rm = el("button", { type: "button", className: "btn-sm secondary gen-add-rm", title: "Remove this addition" }, "✕");
    rm.addEventListener("click", function () {
      additionsTxn(spec, "remove " + a.kind + " " + a.name);
      spec.additions.splice(i, 1);
      // a default that named the removed gate goes with it
      if (a.kind === "macro") spec.additions = spec.additions.filter(function (d) {
        return !(d.kind === "default_gate" && d.target === a.target && d.to === a.name);
      });
      if (!spec.additions.length) delete spec.additions;
      ctx.changed(); mount(host, ctx);
    });
    edit.addEventListener("click", function () {
      var cfg = cfgFor();
      cfg.existing = { index: i, add: a };
      renderForm(box, cfg, ctx, function () { mount(host, ctx); });
    });
    r.appendChild(rm);
    r.appendChild(el("span", { className: "gen-add-lint", "data-lint-index": String(i) }));
    return r;
  }

  /* ---- live lint: every listed addition is judged by the server's one lint
   * (/generate/additions/lint -> additions.lint_proposal) before Build ---- */
  var LINT_DELAY_MS = 400;
  var lintTimer = null, lintGen = 0;
  function scheduleLint(host, spec) {
    if (lintTimer) clearTimeout(lintTimer);
    var gen = ++lintGen;
    lintTimer = setTimeout(function () { lintTimer = null; runLint(host, spec, gen); }, LINT_DELAY_MS);
  }
  function runLint(host, spec, gen) {
    var adds = list(spec);
    var slots = Array.prototype.slice.call(host.querySelectorAll(".gen-add-lint[data-lint-index]"))
      .filter(function (slot) { return adds[+slot.getAttribute("data-lint-index")]; });
    if (!slots.length) return;
    // ONE request for every listed addition (a 60Q chip lists ~200)
    var idx = slots.map(function (slot) { return +slot.getAttribute("data-lint-index"); });
    postJSON("/generate/additions/lint", { spec: spec, indices: idx }).then(function (res) {
      if (gen !== lintGen) return;          // a newer edit owns the screen
      var out = (res.body && res.body.results) || [];
      slots.forEach(function (slot, k) {
        var j = out[k] || {};
        if (!slot.isConnected) return;
        slot.innerHTML = "";
        (j.errors || []).forEach(function (e) {
          var x = el("span", { className: "gen-add-lint-err" }, "✗ " + e);
          x.style.color = "var(--sm-danger, #d33)";
          slot.appendChild(x);
        });
        (j.warnings || []).forEach(function (w) {
          slot.appendChild(el("span", { className: "muted gen-add-lint-warn" }, "⚠ " + w));
        });
      });
    }).catch(function () { /* offline: the build still checks it */ });
  }

  /* ---- one convention for a pair's 2Q gates (P5) ---------------------------
   * The Populate "Qubit pairs" table is the one place a pair's gates are
   * listed: the ones the build makes (mirror of run_build._finalize_pair_gates,
   * core/additions.builder_gate_names), the ones added here, and the default.
   * The default is written as the alias macros.cz -> <gate> (the shape the
   * builder itself writes on a tunable-coupler chip); SM's CZ plan reads `cz`
   * first, official CZ nodes play macros[operation] (default cz_unipolar). */
  var DEFAULT_GATE = "cz";
  var NODE_DEFAULT = "cz_unipolar";
  var CZ_VARIANTS = ["unipolar", "flattop", "bipolar", "SNZ", "flattop_erf"];
  var GATE_RULE = "One list of gates per pair: what the build makes, and what you add. " +
    "Default writes macros.cz → that gate, which SM's CZ plan reads first and a CZ node plays with " +
    "operation=cz (left alone, the official CZ nodes play " + NODE_DEFAULT + "). Two gates never share a name.";

  function builderGates(spec, long) {
    var facts = ((displayFacts || {}).pairs || {})[long];
    return facts ? facts.builder : [];
  }

  function pairGates(spec, pid, long) {
    var tgt = "qubit_pairs." + pid + ".macros";
    var b = builderGates(spec, long);
    var added = [], chosen = null, chosenIdx = -1;
    list(spec).forEach(function (a, i) {
      if (a.target !== tgt) return;
      if (a.kind === "macro") added.push({ name: a.name, index: i, cls: a["class"] });
      if (a.kind === "default_gate") { chosen = a; chosenIdx = i; }
    });
    var effective = chosen ? chosen.to
      : (b.indexOf(DEFAULT_GATE) >= 0 ? b[0] : null);
    return { target: tgt, builder: b, added: added, chosen: chosen, chosenIndex: chosenIdx,
             effective: effective, by: chosen ? "chosen" : (effective ? "builder" : null) };
  }

  function setDefault(spec, g, to) {
    if (!Array.isArray(spec.additions)) spec.additions = [];
    if (g.chosenIndex >= 0) spec.additions.splice(g.chosenIndex, 1);
    if (to) spec.additions.push({ kind: "default_gate", target: g.target, name: DEFAULT_GATE, to: to });
    if (!spec.additions.length) delete spec.additions;
  }

  var pairCtx = null, pairTable = null, addHost = null, addCtx = null;

  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  /* One additions edit = ONE wizard Ctrl+Z step. Called right before the edit
   * writes: it snapshots everything an additions edit may change -- the
   * additions list and populate.pairs (the CZ form sets a pair's
   * moving_qubit) -- under one label (the RuleTxn shape: {label, additions,
   * populate_pairs}). Undo puts both back and repaints. Without the wizard's
   * undo stack (another page, a test) it does nothing. */
  function additionsTxn(spec, label) {
    var u = window._wizUndo;
    if (!u || typeof u.pushRestore !== "function") return null;
    var txn = {
      label: label,
      additions: Array.isArray(spec.additions) ? clone(spec.additions) : null,
      populate_pairs: spec.populate && spec.populate.pairs ? clone(spec.populate.pairs) : null,
    };
    u.pushRestore(label, function () {
      if (txn.additions) spec.additions = clone(txn.additions); else delete spec.additions;
      if (txn.populate_pairs) {
        spec.populate = spec.populate || {};
        spec.populate.pairs = clone(txn.populate_pairs);
      } else if (spec.populate) delete spec.populate.pairs;
      refreshAll();
    });
    return txn;
  }

  function refreshAll() {
    if (pairTable && pairCtx) decoratePairTable(pairTable, pairCtx);
    if (addHost && addCtx) mount(addHost, addCtx);
  }

  /* The pulse additions an added gate plays (its fields point at them by
   * absolute pointer) and that no other addition still points at: what the
   * gate created, and what goes when the gate is removed. Returns indices. */
  function gatePulses(spec, gateIndex) {
    var adds = list(spec), gate = adds[gateIndex];
    if (!gate || gate.kind !== "macro") return [];
    function refs(a) {
      var out = [];
      (function walk(v) {
        if (Array.isArray(v)) v.forEach(walk);
        else if (v && typeof v === "object") Object.keys(v).forEach(function (k) { walk(v[k]); });
        else if (typeof v === "string" && v.indexOf("#/") === 0) out.push(v);
      })(a.fields || {});
      return out;
    }
    var ptrOf = function (a) { return "#/" + String(a.target).split(".").join("/") + "/" + a.name; };
    var mine = {};
    refs(gate).forEach(function (r) { mine[r] = true; });
    var others = {};
    adds.forEach(function (a, j) {
      if (j !== gateIndex) refs(a).forEach(function (r) { others[r] = true; });
    });
    var out = [];
    adds.forEach(function (a, j) {
      if (a.kind !== "pulse") return;
      var ptr = ptrOf(a);
      if (mine[ptr] && !others[ptr]) out.push(j);
    });
    return out;
  }

  function gateRemoveText(spec, a, pid, pulses) {
    var names = pulses.map(function (j) { return list(spec)[j].name; });
    return "Remove the added gate " + a.name + " from " + pid + "? " + (names.length
      ? "This also removes the " + names.length + " pulse" + (names.length > 1 ? "s" : "") +
        " it plays: " + names.join(", ") + "."
      : "It plays no pulse added here (0 pulses removed).");
  }

  function gatesCell(td, tr, pid, long, ctx) {
    td.innerHTML = "";
    var spec = ctx.spec();
    var g = pairGates(spec, pid, long);
    var chips = el("div", { className: "gen-gates" });
    function chip(name, kind, extra) {
      var c = el(kind === "added" ? "button" : "span",
                 { className: "gen-gate gen-gate-" + kind + (name === g.effective ? " gen-gate-default" : ""),
                   "data-gate": name, type: kind === "added" ? "button" : null }, name);
      if (kind === "added") c.setAttribute("type", "button"); else c.removeAttribute("type");
      c.title = (kind === "builder" ? "made by the build" : "added here — click to edit") +
        (name === g.effective ? "; the pair's default gate (macros.cz)" : "") + (extra || "");
      if (name === g.effective) c.insertBefore(el("span", { className: "gen-gate-star", "aria-label": "default" }, "★ "), c.firstChild);
      chips.appendChild(c);
      return c;
    }
    g.builder.forEach(function (n) {
      if (n === DEFAULT_GATE && g.builder.length > 1) return;   // the coupler chip's alias, shown as ★
      chip(n, "builder", n === NODE_DEFAULT ? "; what the official CZ nodes play by default" : "");
    });
    g.added.forEach(function (a) {
      var c = chip(a.name, "added", " (" + shortName(a.cls) + ")");
      c.addEventListener("click", function () { openForm(tr, pid, long, ctx, { index: a.index, add: list(ctx.spec())[a.index] }); });
      var rm = el("button", { type: "button", className: "btn-sm secondary gen-gate-rm", "data-gate": a.name,
                              title: "Remove the added gate " + a.name + " and the pulses it plays" }, "✕");
      rm.addEventListener("click", function () {
        var sp = ctx.spec();
        var pulses = gatePulses(sp, a.index);
        if (typeof window.confirm === "function" && !window.confirm(gateRemoveText(sp, a, pid, pulses))) return;
        additionsTxn(sp, "remove gate " + a.name + " on " + pid);
        sp.additions = list(sp).filter(function (d, j) {
          // the gate, the pulses it plays that nothing else plays, and a default that named it
          return j !== a.index && pulses.indexOf(j) < 0 &&
            !(d.kind === "default_gate" && d.target === g.target && d.to === a.name);
        });
        if (!sp.additions.length) delete sp.additions;
        ctx.changed(); refreshAll();
      });
      chips.appendChild(rm);
    });
    if (!g.builder.length && !g.added.length)
      chips.appendChild(el("span", { className: "muted gen-gate-none" }, "no gate yet"));
    td.appendChild(chips);
    var row = el("div", { className: "gen-gates-actions" });
    var dl = el("label", { className: "gen-gate-deflabel" }, "Default ");
    var sel = el("select", { className: "gen-gate-default-sel", "aria-label": "Default gate of " + pid });
    var names = g.builder.filter(function (n) { return n !== DEFAULT_GATE; })
      .concat(g.added.map(function (a) { return a.name; }));
    sel.appendChild(el("option", { value: "" }, g.by === "builder"
      ? "the build's (" + g.effective + ")" : "none (nodes: " + NODE_DEFAULT + ")"));
    names.forEach(function (n) { sel.appendChild(el("option", { value: n }, n)); });
    sel.value = g.chosen ? g.chosen.to : "";
    sel.addEventListener("change", function () {
      additionsTxn(ctx.spec(), "default gate of " + pid);
      setDefault(ctx.spec(), pairGates(ctx.spec(), pid, long), sel.value);
      ctx.changed(); refreshAll();
    });
    dl.appendChild(sel);
    row.appendChild(dl);
    var cz = el("button", { type: "button", className: "btn-sm secondary gen-add-czbtn",
                            title: "A CZ gate class, the flux pulse(s) it plays, and the gate pointing at them" }, "+ CZ…");
    if (!(roles && roles.ok)) cz.disabled = true;
    cz.addEventListener("click", function () { openForm(tr, pid, long, ctx, null); });
    row.appendChild(cz);
    td.appendChild(row);
  }

  function openForm(tr, pid, long, ctx, existing) {
    var next = tr.nextElementSibling;
    if (!(next && next.classList.contains("gen-gates-formrow"))) {
      next = el("tr", { className: "gen-gates-formrow", "data-rid": tr.getAttribute("data-rid") });
      var cell = el("td", { colspan: String(tr.children.length) });
      // the form stays in view however wide the table scrolls
      cell.appendChild(el("div", { className: "gen-gates-formbox" }));
      next.appendChild(cell);
      tr.parentNode.insertBefore(next, tr.nextSibling);
    }
    var box = next.firstChild.firstChild;
    var scroller = tr.closest(".gen-pop-host") || tr.closest("table").parentNode;
    if (scroller && scroller.clientWidth) box.style.width = Math.max(320, scroller.clientWidth - 24) + "px";
    var spec = ctx.spec();
    var ow = owners(spec).filter(function (o) { return o.kind === "pair" && o.pair === pid; })[0];
    if (!ow) return;
    if (existing) {
      renderForm(box, { kind: "macro", target: ow.target, classes: (roles && roles.macros_pair) || {},
                        classLabel: "Macro class", owner: ow, existing: existing }, ctx, refreshAll);
    } else {
      renderCzGroup(box, ow, ctx, refreshAll);
    }
  }

  /* Decorate the Populate pair table (built by generate.js) with a Gates column. */
  function decoratePairTable(table, ctx) {
    if (!table) return;
    pairCtx = ctx; pairTable = table;
    if (!roles) { loadRoles(false, null, function () { refreshAll(); }); }
    var hr = table.querySelector("thead tr");
    if (hr && !hr.querySelector(".gen-gates-th")) {
      var th = el("th", { className: "gen-gates-th", title: GATE_RULE }, "Gates");
      hr.insertBefore(th, hr.children[1] || null);
      var sa = table.querySelector("tr.gen-pop-setall");
      if (sa) sa.insertBefore(el("td", { className: "gen-gates-setall" }), sa.children[1] || null);
    }
    var spec = ctx.spec();
    if (!ensureDisplay(spec, function () { refreshAll(); })) return;
    var byLong = {};
    (spec.qubit_pairs || []).forEach(function (p) {
      if (Array.isArray(p) && p.length === 2) byLong[p[0] + "-" + p[1]] = pairId(String(p[0]), String(p[1]));
    });
    Array.prototype.forEach.call(table.querySelectorAll("tbody tr[data-rid]"), function (tr) {
      if (tr.classList.contains("gen-gates-formrow")) return;
      var long = tr.getAttribute("data-rid");
      var pid = byLong[long];
      if (!pid) return;
      var td = tr.querySelector("td.gen-gates");
      if (!td) {
        td = el("td", { className: "gen-gates" });
        tr.insertBefore(td, tr.children[1] || null);
        // the variant cell changes which gates the build makes
        tr.addEventListener("change", function (ev) {
          if (ev.target && ev.target.closest && ev.target.closest("td.gen-gates")) return;
          setTimeout(function () { decoratePairTable(table, ctx); }, 0);
        });
      }
      gatesCell(td, tr, pid, long, ctx);
    });
    var note = table.parentNode && table.parentNode.querySelector(".gen-gates-rule");
    if (!note && table.parentNode) {
      note = el("p", { className: "muted gen-gates-rule" }, GATE_RULE);
      table.parentNode.insertBefore(note, table);
    }
  }

  /* ---- agent proposals (optional; nothing here without a connected agent) --
   * An agent's proposal is linted on the server like any addition and waits
   * here as PENDING; only a person's Accept puts it into spec.additions. */
  var ctxTimer = null, pendTimer = null, pendUntil = 0;
  var ASK_TEXT = "In the Generate Config wizard I have open, call generate_roles, then propose additions " +
    "with propose_additions that follow the knowledge cards (placement, pointers, naming). Do not set any " +
    "calibration value; give a reason for each item.";

  function postJSON(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  function recordContext(spec) {
    clearTimeout(ctxTimer);
    ctxTimer = setTimeout(function () {
      try { postJSON("/generate/proposals/context", { spec: spec }).catch(function () {}); } catch (e) { /* offline */ }
    }, 300);
  }

  function describe(a) {
    if (!a || typeof a !== "object") return "?";
    if (a.kind === "field") return "field " + fieldLabel(a.target, a.value);
    if (a.kind === "default_gate") return a.target + "." + a.name + " → " + a.to + " (default gate)";
    return a.kind + " " + a.target + "." + a.name + " (" + shortName(a["class"] || "?") + ")";
  }

  function pendingRow(it, host, ctx) {
    var r = el("div", { className: "gen-add-prop", "data-proposal": it.id });
    var who = String(it.author || "agent").replace(/^by_/, "");
    var head = el("p", { className: "gen-add-prop-head" });
    head.appendChild(el("span", { className: "gen-add-prop-by" }, "proposed by " + who));
    head.appendChild(document.createTextNode(" " + (it.kind === "card"
      ? "a knowledge card " + ((it.card || {}).id || "?") + " (lab overlay)" : describe(it.addition))));
    r.appendChild(head);
    if (it.reason) r.appendChild(el("p", { className: "muted gen-add-prop-why" }, "Why: " + it.reason));
    var lint = it.lint || {};
    if (it.kind !== "card") {
      r.appendChild(el("p", { className: "muted gen-add-prop-card" },
        lint.card ? "Card: " + lint.card : "No card — field form and the environment's check only."));
    }
    (lint.warnings || []).forEach(function (w) {
      r.appendChild(el("p", { className: "gen-add-prop-warn" }, "⚠ " + w));
    });
    var inputs = {};
    (lint.needs_person || []).forEach(function (f) {
      var lab = el("label", { className: "gen-add-field gen-add-required" });
      lab.appendChild(el("span", null, f + " (yours to type)"));
      var inp = el("input", { type: "text", "data-needs": f, "aria-label": f });
      lab.appendChild(inp); r.appendChild(lab); inputs[f] = inp;
    });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-prop-accept" }, "Accept");
    var no = el("button", { type: "button", className: "btn-sm secondary gen-add-prop-dismiss" }, "Dismiss");
    var msg = el("p", { className: "gen-add-msg muted" });
    actions.appendChild(ok); actions.appendChild(no);
    r.appendChild(actions); r.appendChild(msg);
    ok.addEventListener("click", function () {
      var typed = {}, missing = [];
      Object.keys(inputs).forEach(function (f) {
        var v = (inputs[f].value || "").trim();
        if (!v) missing.push(f); else typed[f] = parseValue(v, "");
      });
      if (missing.length) { msg.textContent = "Type: " + missing.join(", "); return; }
      ok.disabled = true;
      postJSON("/generate/proposals/" + encodeURIComponent(it.id) + "/accept", { spec: ctx.spec(), fields: typed })
        .then(function (res) {
          ok.disabled = false;
          if (!res.body.ok) { msg.textContent = (res.body.errors || ["refused"]).join("; "); return; }
          if (res.body.addition) {
            var spec = ctx.spec();
            additionsTxn(spec, "accept proposal " + (res.body.addition.name || res.body.addition.target || ""));
            if (!Array.isArray(spec.additions)) spec.additions = [];
            spec.additions.push(res.body.addition);
            ctx.changed();
          }
          mount(host, ctx);
        }).catch(function (e) { ok.disabled = false; msg.textContent = String(e); });
    });
    no.addEventListener("click", function () {
      postJSON("/generate/proposals/" + encodeURIComponent(it.id) + "/dismiss", {})
        .then(function () { mount(host, ctx); });
    });
    return r;
  }

  function loadPending(box, ctx) {
    var host = addHost;
    fetch("/generate/proposals").then(function (r) { return r.json(); }).then(function (res) {
      box.innerHTML = "";
      var items = (res && res.items) || [];
      if (items.length) {
        box.appendChild(el("p", { className: "gen-add-sub" }, "Proposed — waiting for you (" + items.length + ")"));
        box.appendChild(el("p", { className: "muted" },
          "An agent proposed these. Nothing is added until you press Accept; each was checked by the same rules as your own additions."));
        items.forEach(function (it) { box.appendChild(pendingRow(it, host, ctx)); });
      }
      clearTimeout(pendTimer);
      if (Date.now() < pendUntil) pendTimer = setTimeout(function () { if (box.isConnected) loadPending(box, ctx); }, 3000);
    }).catch(function () { /* the wizard works without it */ });
  }

  function askAgent(btn) {
    btn.disabled = true;
    var note = btn.parentNode.querySelector(".gen-add-ask-note") ||
      btn.parentNode.appendChild(el("span", { className: "muted gen-add-ask-note", role: "status" }));
    var f = new FormData();
    f.append("text", ASK_TEXT); f.append("feed", "1");
    fetch("/api/agent/chat/ask", { method: "POST", body: f })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        btn.disabled = false;
        if (res.body && res.body.ok) note.textContent = "Asked — proposals appear below as they arrive.";
        else note.textContent = "Ask your agent: “" + ASK_TEXT + "” (" + ((res.body && res.body.error) || "the in-app agent needs an open chip") + ")";
        pendUntil = Date.now() + 5 * 60 * 1000;
        var box = addHost && addHost.querySelector(".gen-add-pending");
        if (box) loadPending(box, addCtx);
      }).catch(function (e) { btn.disabled = false; note.textContent = String(e); });
  }

  function mount(host, ctx) {
    if (!host) return;
    addHost = host; addCtx = ctx;
    var spec = ctx.spec();
    host.innerHTML = "";
    if (!ensureDisplay(spec, function () { refreshAll(); })) return;
    var chs = channels(spec);
    if (!roles) {
      host.appendChild(el("p", { className: "muted" }, "Loading classes from the environment…"));
      loadRoles(false, null, function () { mount(host, ctx); });
      return;
    }
    var bar = el("div", { className: "gen-add-bar" });
    if (!roles.ok) {
      bar.appendChild(el("span", { className: "muted gen-add-cold" },
        roles.cold ? "The environment's classes have not been read yet."
                   : "Could not read the environment's classes: " + (roles.error || "unknown error")));
    } else {
      var nLab = 0, nOff = 0;
      Object.keys(roles.pulses || {}).forEach(function (p) { if (roles.pulses[p].origin === "lab") nLab++; else nOff++; });
      var nMac = Object.keys(roles.macros_pair || {}).length + Object.keys(roles.macros_qubit || {}).length;
      bar.appendChild(el("span", { className: "muted gen-add-count" },
        nOff + " official and " + nLab + " lab pulse classes, " + nMac + " macro classes" +
        (roles.versions && roles.versions.quam_builder ? " (quam_builder " + roles.versions.quam_builder + ")" : "")));
    }
    var mods = el("input", { type: "text", className: "gen-add-modules", placeholder: "lab modules, e.g. my_lab.pulses",
                             title: "Modules to look in for lab classes (comma separated). Lab packages are never walked." });
    var read = el("button", { type: "button", className: "btn-sm secondary gen-add-read" }, "Read classes");
    read.addEventListener("click", function () {
      var m = (mods.value || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      read.disabled = true; read.textContent = "Reading…";
      loadRoles(true, m, function () { mount(host, ctx); });
    });
    bar.appendChild(mods); bar.appendChild(read);
    if (roles.ok && roles.agent_connected) {
      var ask = el("button", { type: "button", className: "btn-sm secondary gen-add-ask",
                               title: "The connected agent reads this draft and the knowledge cards and proposes " +
                                      "additions; each one waits here until you accept it" }, "Ask agent to propose");
      ask.addEventListener("click", function () { askAgent(ask); });
      bar.appendChild(ask);
    }
    host.appendChild(bar);
    var pend = el("div", { className: "gen-add-pending", "aria-live": "polite" });
    host.appendChild(pend);
    recordContext(spec);
    loadPending(pend, ctx);
    // a re-generate from a chip built with additions: they came from its build record
    var carried = list(spec).filter(function (a) { return a && a.env && typeof a.env === "object"; });
    if (carried.length) {
      var vb = (carried[0].env.versions || {}).quam_builder;
      host.appendChild(el("p", { className: "muted gen-add-carried", title: String(carried[0].env.python || "") },
        carried.length + " addition" + (carried.length > 1 ? "s" : "") + " carried from this chip's own build record" +
        (vb ? " (recorded with quam_builder " + vb + ")" : "") +
        "; the rebuild places them again and checks them in the selected environment."));
    }
    if (!chs.length && !(spec.qubits || []).length) {
      host.appendChild(el("p", { className: "muted" }, "No channels yet — add lines in the Qubits step."));
      return;
    }
    var ready = !!(roles && roles.ok);
    host.appendChild(el("p", { className: "gen-add-sub" }, "Pulses"));
    var table = el("div", { className: "gen-add-list" });
    chs.forEach(function (ch) {
      var item = el("div", { className: "gen-add-ch", "data-target": ch.target });
      var head = el("div", { className: "gen-add-ch-head" });
      head.appendChild(el("span", { className: "gen-add-ch-label" }, ch.label));
      var add = el("button", { type: "button", className: "btn-sm secondary gen-add-btn" }, "+ Add pulse");
      if (!ready) add.disabled = true;
      head.appendChild(add);
      var box = el("div", { className: "gen-add-formbox" });
      if (/\.resonator\.operations$/.test(ch.target)) {
        var rb = el("button", { type: "button", className: "btn-sm secondary gen-add-robtn",
                                title: "A readout pulse class and, where this environment has one, a measure macro" }, "+ Readout…");
        if (!ready) rb.disabled = true;
        head.appendChild(rb);
        rb.addEventListener("click", function () { renderReadoutGroup(box, ch, ctx, function () { mount(host, ctx); }); });
      }
      item.appendChild(head);
      var cfgFor = function () {
        return { kind: "pulse", target: ch.target, classes: (roles && roles.pulses) || {}, classLabel: "Pulse class", ch: ch };
      };
      list(spec).forEach(function (a, i) {
        if (a.target !== ch.target || a.kind === "macro" || a.kind === "field") return;
        item.appendChild(itemRow(spec, a, i, host, ctx, box, cfgFor));
      });
      item.appendChild(box);
      add.addEventListener("click", function () { renderForm(box, cfgFor(), ctx, function () { mount(host, ctx); }); });
      table.appendChild(item);
    });
    host.appendChild(table);

    host.appendChild(el("p", { className: "gen-add-sub" }, "Macros"));
    var mtable = el("div", { className: "gen-add-list gen-add-macros" });
    owners(spec).forEach(function (ow) {
      var item = el("div", { className: "gen-add-owner", "data-target": ow.target });
      var head = el("div", { className: "gen-add-ch-head" });
      head.appendChild(el("span", { className: "gen-add-ch-label" }, ow.label + " · macros"));
      var pool = ow.kind === "pair" ? (roles && roles.macros_pair) || {} : (roles && roles.macros_qubit) || {};
      var add = el("button", { type: "button", className: "btn-sm secondary gen-add-mbtn" }, "+ Add macro");
      if (!ready || !Object.keys(pool).length) add.disabled = true;
      head.appendChild(add);
      var box = el("div", { className: "gen-add-formbox" });
      if (ow.kind === "pair") {
        head.appendChild(el("span", { className: "muted gen-add-czhere" },
          "CZ gates and the default: the Qubit pairs table below"));
      }
      item.appendChild(head);
      var cfgFor = function () {
        return { kind: "macro", target: ow.target, classes: pool, classLabel: "Macro class", owner: ow };
      };
      var nGates = 0;
      list(spec).forEach(function (a, i) {
        if (a.target !== ow.target || a.kind !== "macro") return;
        // a pair's macros are its gates: listed once, in the Qubit pairs table
        if (ow.kind === "pair") { nGates++; return; }
        item.appendChild(itemRow(spec, a, i, host, ctx, box, cfgFor));
      });
      if (nGates) item.appendChild(el("p", { className: "muted gen-add-gates-there" },
        nGates + " added gate" + (nGates > 1 ? "s" : "") + " — listed in the Qubit pairs table"));
      item.appendChild(box);
      add.addEventListener("click", function () { renderForm(box, cfgFor(), ctx, function () { mount(host, ctx); }); });
      mtable.appendChild(item);
    });
    host.appendChild(mtable);

    var twpas = (spec.twpas || []).map(function (t) { return t && typeof t === "object" ? String(t.id) : String(t); });
    var fieldIdx = [];
    list(spec).forEach(function (a, i) { if (a.kind === "field") fieldIdx.push(i); });
    if (twpas.length || fieldIdx.length) {
      host.appendChild(el("p", { className: "gen-add-sub" }, "Fields and presets"));
      var ftable = el("div", { className: "gen-add-list gen-add-fieldlist" });
      twpas.forEach(function (t) {
        var item = el("div", { className: "gen-add-owner", "data-twpa": t });
        var head = el("div", { className: "gen-add-ch-head" });
        head.appendChild(el("span", { className: "gen-add-ch-label" }, t + " · TWPA"));
        var b = el("button", { type: "button", className: "btn-sm secondary gen-add-twbtn",
                               title: "Pulsed pump: a flag, the pump_ pulse, and the active TWPA list" }, "+ Pulsed mode…");
        if (!ready) b.disabled = true;
        head.appendChild(b);
        item.appendChild(head);
        var box = el("div", { className: "gen-add-formbox" });
        item.appendChild(box);
        b.addEventListener("click", function () { renderTwpaGroup(box, t, ctx, function () { mount(host, ctx); }); });
        ftable.appendChild(item);
      });
      fieldIdx.forEach(function (i) { ftable.appendChild(fieldRow(spec, spec.additions[i], i, host, ctx)); });
      host.appendChild(ftable);
    }
    if (pairTable && pairCtx && pairTable.isConnected) decoratePairTable(pairTable, pairCtx);
    if (list(spec).length) scheduleLint(host, spec);
  }

  function aliasLabel(e) { return e.target + "." + e.name + " → " + e.to + " (default gate)"; }

  function outcome(rep) {
    rep = rep || {};
    var a = (rep.added || []).length, r = (rep.refused || []).length;
    if (!a && !r) return "none";
    if (!r) return "ok";
    return a ? "mixed" : "refused";
  }

  function renderResult(rep) {
    rep = rep || {};
    var oc = outcome(rep);
    var box = el("div", { className: "gen-add-result gen-add-result-" + oc });
    var added = rep.added || [], refused = rep.refused || [];
    box.appendChild(el("p", { className: "gen-add-result-head" },
      (oc === "refused" ? "✗ " : oc === "mixed" ? "⚠ " : "") +
      "Additions: " + added.length + " added" + (refused.length ? ", " + refused.length + " refused" : "")));
    var ul = el("ul", { className: "gen-add-result-list" });
    added.forEach(function (e) {
      var li = el("li", { className: "gen-add-ok-item" });
      li.textContent = e.kind === "default_gate" ? "✓ " + aliasLabel(e)
        : e.kind === "field" ? "✓ " + fieldLabel(e.path, e.value) + " (field)"
        : "✓ " + e.path + " (" + shortName(e["class"]) + ", " + e.origin + ")";
      ul.appendChild(li);
    });
    refused.forEach(function (e) {
      var li = el("li", { className: "gen-add-refused" });
      li.style.color = "var(--sm-danger, #d33)";
      li.textContent = e.kind === "default_gate" ? "✗ " + aliasLabel(e) + " — " + e.reason
        : e.kind === "field"
        ? "✗ " + fieldLabel((e.target ? e.target + "." : "") + e.name, e.value) + " (field) — " + e.reason
        : "✗ " + e.target + "." + e.name + " (" + shortName(e["class"]) + ") — " + e.reason;
      ul.appendChild(li);
    });
    box.appendChild(ul);
    (rep.notes || []).forEach(function (n) { box.appendChild(el("p", { className: "muted" }, n)); });
    return box;
  }

  /* The Review step: every addition, in the order the build places them,
   * with what can already be said (info = /generate/capabilities `additions`). */
  function renderReview(box, info, spec) {
    if (!box) return null;
    var items = (info && info.items) || list(spec || {}).map(function (a, i) {
      if (a.kind === "field") {
        var k = String(a.target || "").lastIndexOf(".");
        return { index: i, kind: "field", target: k >= 0 ? a.target.slice(0, k) : "",
                 name: k >= 0 ? a.target.slice(k + 1) : a.target, value: a.value };
      }
      return { index: i, kind: a.kind, target: a.target, name: a.name, "class": a["class"], to: a.to };
    });
    if (!items.length) return null;
    var bad = items.filter(function (e) { return e.reason; }).length;
    var wrap = el("div", { className: "gen-add-review" });
    wrap.appendChild(el("p", { className: "gen-cap-subhead" },
      "Additions (" + items.length + ", pulses, then macros, then fields)" +
      (bad ? " — " + bad + " will be refused:" : " — checked against the built chip and in the environment during the build:")));
    var ul = el("ul", { className: "gen-add-review-list" });
    items.forEach(function (e) {
      var li = el("li", { className: e.reason ? "gen-add-review-bad" : "gen-add-review-item" });
      li.textContent = e.kind === "default_gate"
        ? (e.reason ? "✗ " : "") + aliasLabel(e) + (e.reason ? " — " + e.reason : "")
        : e.kind === "field"
        ? (e.reason ? "✗ " : "") + "field " + fieldLabel((e.target ? e.target + "." : "") + e.name, e.value) +
          (e.reason ? " — " + e.reason : "")
        : (e.reason ? "✗ " : "") + e.kind + " " + e.target + "." + e.name + " (" + shortName(e["class"]) +
          (e.origin ? ", " + e.origin : "") + ")" + (e.reason ? " — " + e.reason : "");
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
    if (info && info.manifest === false) {
      wrap.appendChild(el("p", { className: "muted" },
        "The environment's classes have not been read yet, so classes and fields are checked at build time."));
    }
    box.appendChild(wrap);
    return { count: items.length, refused: bad };
  }

  /* A qubit rename (the wizard's applyQubitIdMap) re-expressed in the
   * additions, by the rebuild's own rules (regen_merge.token_pattern /
   * _renamed_pointer; the Python twin is additions.rename_qubits, pinned for
   * parity): a pair id after qubit_pairs is swapped whole; every other target
   * segment, every pointer in fields / value, the name and a default gate's
   * `to` get their "_"-delimited qubit-id tokens renamed (below extras kept
   * verbatim). So a pointer that names an addition keeps naming it.
   * map = {old: new}; qubitIds / pairs = the spec's BEFORE the rename. */
  function renameQubits(adds, map, qubitIds, pairs) {
    var tok = {};
    Object.keys(map || {}).forEach(function (o) { if (String(map[o]) !== o) tok[o] = String(map[o]); });
    var out = clone(adds || []);
    if (!Object.keys(tok).length) return out;
    var seen = {};
    var every = Object.keys(tok).concat((qubitIds || []).map(String)).filter(function (k) {
      if (!k || seen[k]) return false; seen[k] = true; return true;
    }).sort(function (a, b) { return b.length - a.length; });
    var pat = new RegExp("(?<![^_])(?:" + every.map(function (k) {
      return k.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&");
    }).join("|") + ")(?![^_])", "g");
    var rename = function (t) { return t.replace(pat, function (m) { return tok.hasOwnProperty(m) ? tok[m] : m; }); };
    var pmap = {};
    (pairs || []).forEach(function (p) {
      if (!Array.isArray(p) || p.length !== 2) return;
      var c = String(p[0]), t = String(p[1]);
      pmap[pairId(c, t)] = pairId(tok.hasOwnProperty(c) ? tok[c] : c, tok.hasOwnProperty(t) ? tok[t] : t);
    });
    function pointer(s) {
      var i = s.indexOf("/");
      if (i < 0 || i === s.length - 1) return s;
      var segs = s.slice(i + 1).split("/"), free = false;
      var res = segs.map(function (seg, k) {
        if (free) return seg;
        var prev = k ? segs[k - 1] : null;
        var v = prev === "qubit_pairs" ? (pmap.hasOwnProperty(seg) ? pmap[seg] : seg) : rename(seg);
        free = seg === "extras";
        return v;
      });
      return s.slice(0, i) + "/" + res.join("/");
    }
    function ptrs(node) {
      if (Array.isArray(node)) return node.map(ptrs);
      if (node && typeof node === "object") {
        var o = {};
        Object.keys(node).forEach(function (k) { o[k] = ptrs(node[k]); });
        return o;
      }
      if (typeof node === "string" && node.charAt(0) === "#") return pointer(node);
      return node;
    }
    out.forEach(function (a) {
      if (!a || typeof a !== "object") return;
      if (typeof a.target === "string") a.target = pointer("#/" + a.target.split(".").join("/")).slice(2).split("/").join(".");
      ["name", "to"].forEach(function (k) { if (typeof a[k] === "string") a[k] = rename(a[k]); });
      if ("fields" in a) a.fields = ptrs(a.fields);
      if ("value" in a) a.value = ptrs(a.value);
    });
    return out;
  }

  // The class label/origin the loaded role manifest knows (for the rule dialog's header).
  function classInfo(cls) {
    var all = [(roles && roles.macros_pair) || {}, (roles && roles.pulses) || {}];
    for (var i = 0; i < all.length; i++) if (all[i][cls]) return { label: all[i][cls].label || null, origin: all[i][cls].origin || null };
    return null;
  }

  window.GenAdditions = { classInfo: classInfo, refresh: function () { refreshAll(); }, channels: channels, owners: owners, pairId: pairId, mount: mount,
                          renameQubits: renameQubits, gatePulses: gatePulses,
                          renderResult: renderResult, outcome: outcome, renderReview: renderReview,
                          decoratePairTable: decoratePairTable, builderGates: builderGates,
                          pairGates: pairGates, GATE_RULE: GATE_RULE,
                          LINT_DELAY_MS: LINT_DELAY_MS,
                          _reset: function () { roles = null; rolesLoading = false; displaySig = null; displayFacts = null; } };
})();
