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
    (spec.qubit_pairs || []).forEach(function (p) {
      if (!Array.isArray(p) || p.length !== 2) return;
      var id = pairId(String(p[0]), String(p[1]));
      out.push({ target: "qubit_pairs." + id + ".macros", label: id, kind: "pair", pair: id,
                 long: p[0] + "-" + p[1], control: String(p[0]), target_q: String(p[1]),
                 coupler: hasLine(spec, "coupler", p[0] + "-" + p[1]) });
    });
    return out;
  }

  function list(spec) { return Array.isArray(spec.additions) ? spec.additions : []; }

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
    ok.addEventListener("click", function () {
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
      if (!Array.isArray(spec.additions)) spec.additions = [];
      if (cfg.existing) spec.additions[cfg.existing.index] = add;
      else spec.additions.push(add);
      ctx.changed();
      rerender();
    });
  }

  function recipeOn(item, owner, spec) {
    // which qubit a recipe pulse goes on, or null while unknown
    if (item.on === "control") return owner.control;
    if (item.on === "target") return owner.target_q;
    if (item.on === "coupler") return "coupler";
    var pop = ((spec.populate || {}).qubit) || {};
    var fc = Number((pop[owner.control] || {}).RF_freq), ft = Number((pop[owner.target_q] || {}).RF_freq);
    if (item.on === "high" || item.on === "low") {
      if (!(isFinite(fc) && isFinite(ft) && fc && ft)) return null;
      var high = fc >= ft ? owner.control : owner.target_q;   // ties go to the control
      var low = high === owner.control ? owner.target_q : owner.control;
      return item.on === "high" ? high : low;
    }
    return null;   // "moving": chosen in the form
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
        name.value = "cz_" + slug; name.setAttribute("data-auto", "1");
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
            where === "coupler" ? owner.pair + " coupler" : where + " z"));
        }
        sec.appendChild(head);
        var inner = el("div", { className: "gen-add-cz-inner" });
        if (!part.enabled) inner.hidden = true;
        var r2 = el("div", { className: "gen-add-row" });
        var pc = classSelect(pulses, "Pulse class");
        pc.classList.add("gen-add-cz-pclass");
        var pn = el("input", { className: "gen-add-name gen-add-cz-pname", type: "text",
                               value: name.value + "_" + (item.on === "moving" ? "pulse" : item.on),
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
    ok.addEventListener("click", function () {
      var cls = czClasses[sel.value];
      var nm = (name.value || "").trim();
      if (!cls) { msg.textContent = "Choose a CZ gate class."; return; }
      if (!/^[A-Za-z0-9_\-]+$/.test(nm)) { msg.textContent = "Name: letters, digits, '_' or '-'."; return; }
      var spec = ctx.spec();
      var adds = list(spec);
      var fields = {}, newPulses = [], problems = [], moving = null;
      parts.forEach(function (part) {
        if (!part.enabled) return;
        var q = part.onSel ? part.onSel.value : part.where;
        var pnm = (part.nameInp.value || "").trim();
        var pcls = pulses[part.clsSel.value];
        if (!pcls) { problems.push(part.item.field + ": choose a pulse class"); return; }
        if (!/^[A-Za-z0-9_\-]+$/.test(pnm)) { problems.push(part.item.field + ": pulse name"); return; }
        var got = part.ff ? part.ff.collect() : { fields: {}, missing: [] };
        if (got.missing.length) { problems.push(part.item.field + " pulse requires " + got.missing.join(", ")); return; }
        var tgt = q === "coupler" ? "qubit_pairs." + owner.pair + ".coupler.operations" : "qubits." + q + ".z.operations";
        if (adds.some(function (a) { return a.target === tgt && a.name === pnm; }) ||
            newPulses.some(function (a) { return a.target === tgt && a.name === pnm; })) {
          problems.push(pnm + " is already added on " + tgt); return;
        }
        newPulses.push({ kind: "pulse", target: tgt, name: pnm, "class": part.clsSel.value, fields: got.fields });
        fields[part.item.field] = q === "coupler"
          ? "#/qubit_pairs/" + owner.pair + "/coupler/operations/" + pnm
          : "#/qubits/" + q + "/z/operations/" + pnm;
        if (part.item.on === "moving") moving = q === owner.control ? "control" : "target";
      });
      var mg = macroFF ? macroFF.collect() : { fields: {}, missing: [] };
      if (mg.missing.length) problems.push("gate requires " + mg.missing.join(", "));
      if (adds.some(function (a) { return a.target === owner.target && a.name === nm; }))
        problems.push(nm + " is already added on " + owner.pair);
      if (problems.length) { msg.textContent = problems.join("; "); return; }
      Object.keys(mg.fields).forEach(function (k) { fields[k] = mg.fields[k]; });
      if (!Array.isArray(spec.additions)) spec.additions = [];
      newPulses.forEach(function (a) { spec.additions.push(a); });
      spec.additions.push({ kind: "macro", target: owner.target, name: nm, "class": sel.value, fields: fields });
      if (moving) {
        spec.populate = spec.populate || {};
        spec.populate.pairs = spec.populate.pairs || {};
        spec.populate.pairs[owner.long] = spec.populate.pairs[owner.long] || {};
        spec.populate.pairs[owner.long].moving_qubit = moving;
      }
      ctx.changed();
      rerender();
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
        name.value = "readout" + (stem ? "_" + stem : "");
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
      "pump_ pulse \"pump\" (the builder writes it): leave a value blank to keep the builder's."));
    var pbox = el("div", { className: "gen-add-fields" });
    var len = el("input", { type: "text", className: "gen-add-tw-len", placeholder: "length (ns)", "aria-label": "Pump length" });
    var amp = el("input", { type: "text", className: "gen-add-tw-amp", placeholder: "amplitude", "aria-label": "Pump amplitude" });
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
      (origin ? " · " + origin : "") + " · " + Object.keys(a.fields || {}).length + " fields set"));
    var rm = el("button", { type: "button", className: "btn-sm secondary gen-add-rm", title: "Remove this addition" }, "✕");
    rm.addEventListener("click", function () {
      spec.additions.splice(i, 1);
      if (!spec.additions.length) delete spec.additions;
      ctx.changed(); mount(host, ctx);
    });
    edit.addEventListener("click", function () {
      var cfg = cfgFor();
      cfg.existing = { index: i, add: a };
      renderForm(box, cfg, ctx, function () { mount(host, ctx); });
    });
    r.appendChild(rm);
    return r;
  }

  function mount(host, ctx) {
    if (!host) return;
    var spec = ctx.spec();
    host.innerHTML = "";
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
    host.appendChild(bar);
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
        var cz = el("button", { type: "button", className: "btn-sm secondary gen-add-czbtn",
                                title: "A CZ gate class, the flux pulse(s) it plays, and the gate pointing at them" }, "+ CZ…");
        if (!ready) cz.disabled = true;
        head.appendChild(cz);
        cz.addEventListener("click", function () { renderCzGroup(box, ow, ctx, function () { mount(host, ctx); }); });
      }
      item.appendChild(head);
      var cfgFor = function () {
        return { kind: "macro", target: ow.target, classes: pool, classLabel: "Macro class", owner: ow };
      };
      list(spec).forEach(function (a, i) {
        if (a.target !== ow.target || a.kind !== "macro") return;
        item.appendChild(itemRow(spec, a, i, host, ctx, box, cfgFor));
      });
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
  }

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
      li.textContent = e.kind === "field" ? "✓ " + fieldLabel(e.path, e.value) + " (field)"
        : "✓ " + e.path + " (" + shortName(e["class"]) + ", " + e.origin + ")";
      ul.appendChild(li);
    });
    refused.forEach(function (e) {
      var li = el("li", { className: "gen-add-refused" });
      li.style.color = "var(--sm-danger, #d33)";
      li.textContent = e.kind === "field"
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
      return { index: i, kind: a.kind, target: a.target, name: a.name, "class": a["class"] };
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
      li.textContent = e.kind === "field"
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

  window.GenAdditions = { channels: channels, owners: owners, pairId: pairId, mount: mount,
                          renderResult: renderResult, outcome: outcome, renderReview: renderReview,
                          _reset: function () { roles = null; rolesLoading = false; } };
})();
