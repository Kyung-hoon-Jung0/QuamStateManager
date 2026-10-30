/* Generate Config -- pulse additions (lab additions, phase 2).
 *
 * window.GenAdditions:
 *   channels(spec)            -> [{target, label, qubit}] the channels a pulse can go on
 *   mount(host, ctx)          -> renders the "+ Add pulse" section; ctx = {spec(), changed()}
 *   renderResult(report)      -> a DOM node listing added / refused additions
 *
 * The class list comes from the selected env's role manifest (/generate/roles):
 * official and lab classes are labelled, every field form is generated from the
 * class's own fields (defaults prefilled, docstring as hover). Nothing here
 * decides what is valid -- the build checks each addition and refuses one alone.
 */
(function () {
  "use strict";

  var QUBIT_CH = { drive: "xy", flux: "z", resonator: "resonator" };
  var roles = null;          // last /generate/roles answer
  var rolesLoading = false;

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

  function shortPair(a, b) {
    var m = /^([A-Za-z_]+)(\d+)$/.exec(b);
    return (m && a.indexOf(m[1]) === 0) ? a + "-" + m[2] : a + "-" + b;
  }

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
      if (has) out.push({ target: "qubit_pairs." + shortPair(String(p[0]), String(p[1])) + ".coupler.operations",
                          label: shortPair(String(p[0]), String(p[1])) + " · coupler", qubit: null });
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

  function pointerCandidates(ch, fieldName, cls, spec) {
    var out = [];
    if (ch.qubit) ((roles && roles.qubit_fields) || []).forEach(function (n) {
      out.push("#/qubits/" + ch.qubit + "/" + n);
    });
    Object.keys((cls && cls.fields) || {}).forEach(function (n) {
      if (n !== fieldName) out.push("#./" + n);
    });
    list(spec).forEach(function (a) {
      if (a.target === ch.target) out.push("#../" + a.name);
    });
    return out;
  }

  function renderForm(box, ch, ctx, rerender) {
    box.innerHTML = "";
    var pulses = (roles && roles.pulses) || {};
    var paths = Object.keys(pulses).sort();
    var form = el("div", { className: "gen-add-form", "data-target": ch.target });
    var row = el("div", { className: "gen-add-row" });
    var sel = el("select", { className: "gen-add-class", "aria-label": "Pulse class" });
    sel.appendChild(el("option", { value: "" }, "Choose a pulse class…"));
    ["lab", "official"].forEach(function (origin) {
      var grp = el("optgroup", { label: origin === "lab" ? "Lab classes" : "Official classes" });
      paths.forEach(function (p) {
        if ((pulses[p].origin || "official") !== origin) return;
        var o = el("option", { value: p }, p.split(".").pop() + "  —  " + p);
        if (pulses[p].doc) o.title = pulses[p].doc;
        grp.appendChild(o);
      });
      if (grp.children.length) sel.appendChild(grp);
    });
    var name = el("input", { className: "gen-add-name", type: "text", placeholder: "operation name", "aria-label": "Operation name" });
    row.appendChild(sel); row.appendChild(name);
    form.appendChild(row);
    var fieldsBox = el("div", { className: "gen-add-fields" });
    form.appendChild(fieldsBox);
    var msg = el("p", { className: "gen-add-msg muted" });
    var actions = el("div", { className: "gen-add-actions" });
    var ok = el("button", { type: "button", className: "btn-sm gen-add-ok" }, "Add");
    var cancel = el("button", { type: "button", className: "btn-sm secondary gen-add-cancel" }, "Cancel");
    actions.appendChild(ok); actions.appendChild(cancel);
    form.appendChild(actions); form.appendChild(msg);
    box.appendChild(form);

    sel.addEventListener("change", function () {
      fieldsBox.innerHTML = "";
      var cls = pulses[sel.value];
      if (!cls) return;
      var origin = el("p", { className: "gen-add-origin muted" },
        (cls.origin === "lab" ? "Lab class" : "Official class") + (cls.doc ? " — " + cls.doc : ""));
      fieldsBox.appendChild(origin);
      Object.keys(cls.fields).forEach(function (fn) {
        var f = cls.fields[fn];
        if (fn === "id") return;
        var lab = el("label", { className: "gen-add-field" + (f.has_default ? "" : " gen-add-required") });
        lab.appendChild(el("span", { className: "gen-add-fname", title: f.doc || "" },
          fn + (f.has_default ? "" : " *")));
        var inp = el("input", { type: "text", "data-field": fn, "data-raw": f.raw || "",
                                "data-default": defaultText(f),
                                title: (f.doc ? f.doc + "\n" : "") + "type: " + (f.raw || "?") + " · kind: " + f.kind });
        inp.value = defaultText(f);
        var cands = pointerCandidates(ch, fn, cls, ctx.spec());
        if (cands.length) {
          var dlId = "gen-add-dl-" + ch.target.replace(/\W/g, "_") + "-" + fn;
          var dl = el("datalist", { id: dlId });
          cands.forEach(function (c) { dl.appendChild(el("option", { value: c })); });
          inp.setAttribute("list", dlId);
          lab.appendChild(dl);
          inp.classList.add("gen-add-pointable");
        }
        lab.appendChild(inp);
        fieldsBox.appendChild(lab);
      });
    });
    cancel.addEventListener("click", function () { box.innerHTML = ""; });
    ok.addEventListener("click", function () {
      var cls = pulses[sel.value];
      var nm = (name.value || "").trim();
      if (!cls) { msg.textContent = "Choose a pulse class."; return; }
      if (!/^[A-Za-z0-9_\-]+$/.test(nm)) { msg.textContent = "Name: letters, digits, '_' or '-'."; return; }
      var spec = ctx.spec();
      if (list(spec).some(function (a) { return a.target === ch.target && a.name === nm; })) {
        msg.textContent = nm + " is already added on this channel."; return;
      }
      var fields = {}, missing = [];
      Array.prototype.forEach.call(fieldsBox.querySelectorAll("input[data-field]"), function (inp) {
        var fn = inp.getAttribute("data-field"), v = inp.value;
        var f = cls.fields[fn];
        if (v === "") { if (!f.has_default) missing.push(fn); return; }
        if (f.has_default && v === inp.getAttribute("data-default")) return;   // the class default applies
        fields[fn] = parseValue(v, inp.getAttribute("data-raw"));
      });
      if (missing.length) { msg.textContent = "Required: " + missing.join(", "); return; }
      if (!Array.isArray(spec.additions)) spec.additions = [];
      spec.additions.push({ kind: "pulse", target: ch.target, name: nm, "class": sel.value, fields: fields });
      ctx.changed();
      rerender();
    });
  }

  function mount(host, ctx) {
    if (!host) return;
    var spec = ctx.spec();
    host.innerHTML = "";
    var chs = channels(spec);
    if (!roles) {
      host.appendChild(el("p", { className: "muted" }, "Loading pulse classes from the environment…"));
      loadRoles(false, null, function () { mount(host, ctx); });
      return;
    }
    var bar = el("div", { className: "gen-add-bar" });
    if (!roles.ok) {
      bar.appendChild(el("span", { className: "muted gen-add-cold" },
        roles.cold ? "The environment's pulse classes have not been read yet."
                   : "Could not read the environment's classes: " + (roles.error || "unknown error")));
    } else {
      var nLab = 0, nOff = 0;
      Object.keys(roles.pulses || {}).forEach(function (p) { if (roles.pulses[p].origin === "lab") nLab++; else nOff++; });
      bar.appendChild(el("span", { className: "muted gen-add-count" },
        nOff + " official and " + nLab + " lab pulse classes" +
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
    if (!chs.length) {
      host.appendChild(el("p", { className: "muted" }, "No channels yet — add lines in the Qubits step."));
      return;
    }
    var table = el("div", { className: "gen-add-list" });
    chs.forEach(function (ch) {
      var item = el("div", { className: "gen-add-ch", "data-target": ch.target });
      var head = el("div", { className: "gen-add-ch-head" });
      head.appendChild(el("span", { className: "gen-add-ch-label" }, ch.label));
      var add = el("button", { type: "button", className: "btn-sm secondary gen-add-btn" }, "+ Add pulse");
      if (!(roles && roles.ok)) add.disabled = true;
      head.appendChild(add);
      item.appendChild(head);
      list(spec).forEach(function (a, i) {
        if (a.target !== ch.target) return;
        var r = el("div", { className: "gen-add-item" });
        var origin = (roles && roles.pulses && roles.pulses[a["class"]] && roles.pulses[a["class"]].origin) || "";
        r.appendChild(el("code", null, a.name));
        r.appendChild(el("span", { className: "muted" }, " " + String(a["class"]).split(".").pop() +
          (origin ? " · " + origin : "") + " · " + Object.keys(a.fields || {}).length + " fields set"));
        var rm = el("button", { type: "button", className: "btn-sm secondary gen-add-rm", title: "Remove this addition" }, "✕");
        rm.addEventListener("click", function () {
          spec.additions.splice(i, 1);
          if (!spec.additions.length) delete spec.additions;
          ctx.changed(); mount(host, ctx);
        });
        r.appendChild(rm);
        item.appendChild(r);
      });
      var box = el("div", { className: "gen-add-formbox" });
      item.appendChild(box);
      add.addEventListener("click", function () { renderForm(box, ch, ctx, function () { mount(host, ctx); }); });
      table.appendChild(item);
    });
    host.appendChild(table);
  }

  function renderResult(rep) {
    var box = el("div", { className: "gen-add-result" });
    rep = rep || {};
    var added = rep.added || [], refused = rep.refused || [];
    box.appendChild(el("p", { className: "gen-add-result-head" },
      "Additions: " + added.length + " added" + (refused.length ? ", " + refused.length + " refused" : "")));
    var ul = el("ul", { className: "gen-add-result-list" });
    added.forEach(function (e) {
      var li = el("li", { className: "gen-add-ok-item" });
      li.textContent = "✓ " + e.path + " (" + String(e["class"]).split(".").pop() + ", " + e.origin + ")";
      ul.appendChild(li);
    });
    refused.forEach(function (e) {
      var li = el("li", { className: "gen-add-refused" });
      li.style.color = "var(--sm-danger, #d33)";
      li.textContent = "✗ " + e.target + "." + e.name + " (" + String(e["class"]).split(".").pop() + ") — " + e.reason;
      ul.appendChild(li);
    });
    box.appendChild(ul);
    (rep.notes || []).forEach(function (n) { box.appendChild(el("p", { className: "muted" }, n)); });
    return box;
  }

  window.GenAdditions = { channels: channels, mount: mount, renderResult: renderResult,
                          _reset: function () { roles = null; rolesLoading = false; } };
})();
