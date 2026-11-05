/* Add a qubit or a pair from the chip diagram -- window.DiagramAdd.
 *
 * The map draws two ghost stones ("+" and "+ pair", TopoGraph ghostStones).
 * "+" opens the add panel (GET /diagram-add/panel) under the map; while it is
 * open a click on a qubit stone moves the new qubit to that stone's feedline.
 * "+ pair" arms a two-stone pick; the second stone opens the pair panel.
 * The panel previews the change (POST /diagram-add/stage dry=1) and stages it
 * (dry=0) into the working copy: the pending tray, Review / Apply and one
 * Ctrl+Z hold it; nothing here writes the live files.
 * House convention: framework-free IIFE.
 */
window.DiagramAdd = (function () {
  "use strict";

  var mode = null;          // null | "qubit" | "pair"
  var picked = [];          // pair mode: stone ids, in click order
  var portTimer = null;
  var portSeq = 0;

  function panel() { return document.getElementById("cm-add-panel"); }

  function load(url) {
    var p = panel();
    if (!p || !window.htmx) return;
    window.htmx.ajax("GET", url, { target: "#cm-add-panel", swap: "innerHTML" });
  }

  function markPicked() {
    var map = document.getElementById("component-map");
    if (!map) return;
    var els = map.querySelectorAll(".cm-dir-addsel");
    for (var i = 0; i < els.length; i++) els[i].classList.remove("cm-dir-addsel");
    for (var j = 0; j < picked.length; j++) {
      var g = map.querySelector('[data-cm="q:' + cssEsc(picked[j]) + '"]');
      if (g) g.classList.add("cm-dir-addsel");
    }
  }

  function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s);
  }

  function hint(text) {
    var p = panel();
    if (!p) return;
    p.textContent = "";
    var n = document.createElement("p");
    n.className = "ui-note";
    n.textContent = text;
    p.appendChild(n);
  }

  function cancel() {
    mode = null; picked = [];
    markPicked();
    var p = panel();
    if (p) p.textContent = "";
  }

  // The map hands every click on a [data-cm] element here first; true = taken.
  function onMapClick(el) {
    var v = el.getAttribute("data-cm") || "";
    if (v === "add:qubit") {
      mode = "qubit"; picked = []; markPicked();
      load("/diagram-add/panel?kind=qubit");
      return true;
    }
    if (v === "add:pair") {
      mode = "pair"; picked = []; markPicked();
      hint("Click the control qubit, then the target qubit.");
      return true;
    }
    if (v.indexOf("q:") !== 0 || !mode) return false;
    var id = v.slice(2);
    if (mode === "qubit") {
      load("/diagram-add/panel?kind=qubit&feedline_of=" + encodeURIComponent(id));
      return true;
    }
    var at = picked.indexOf(id);
    if (at >= 0) picked.splice(at, 1); else picked.push(id);
    markPicked();
    if (picked.length === 2) {
      load("/diagram-add/panel?kind=pair&a=" + encodeURIComponent(picked[0]) +
           "&b=" + encodeURIComponent(picked[1]));
    } else {
      hint(picked.length ? "Control " + picked[0] + ": now click the target qubit."
                         : "Click the control qubit, then the target qubit.");
    }
    return true;
  }

  // Re-propose the drive outputs for a typed frequency: one request per pause
  // in typing, and only the newest answer lands.
  function reproposePorts(form) {
    var rf = form.querySelector("[data-cm-add-rf]");
    var sel = form.querySelector("[data-cm-add-drive]");
    var fl = form.querySelector('input[name="feedline"]');
    if (!rf || !sel || !fl) return;
    var mine = ++portSeq;
    fetch("/api/diagram-add/ports?feedline=" + encodeURIComponent(fl.value) +
          "&drive_rf=" + encodeURIComponent(rf.value), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || mine !== portSeq || !sel.isConnected) return;
        var keep = sel.value;
        sel.textContent = "";
        var list = j.drive || [];
        for (var i = 0; i < list.length; i++) {
          var c = list[i], o = document.createElement("option");
          o.value = c.con + "/" + c.fem + "/" + c.port;
          o.textContent = c.label + " (" + c.why + ")";
          sel.appendChild(o);
        }
        if (!list.length) {
          var none = document.createElement("option");
          none.value = ""; none.textContent = "no free MW-FEM output fits";
          sel.appendChild(none);
        }
        for (var k = 0; k < sel.options.length; k++) if (sel.options[k].value === keep) sel.value = keep;
      })
      .catch(function () {});
  }

  function stage(form) {
    var dry = form.querySelector('input[name="dry"]');
    if (!dry || !window.htmx) return;
    dry.value = "0";
    window.htmx.ajax("POST", "/diagram-add/stage", {
      source: form, target: "#cm-add-review", swap: "innerHTML",
      values: window.htmx.values ? window.htmx.values(form) : undefined,
    }).then(function () { dry.value = "1"; });
  }

  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    if (t.closest("[data-cm-add-cancel]")) { cancel(); return; }
    var sw = t.closest("[data-cm-add-swap]");
    if (sw) {
      picked = [sw.getAttribute("data-a"), sw.getAttribute("data-b")];
      markPicked();
      load("/diagram-add/panel?kind=pair&a=" + encodeURIComponent(picked[0]) +
           "&b=" + encodeURIComponent(picked[1]));
      return;
    }
    if (t.closest("[data-cm-add-stage]")) {
      var form = document.getElementById("cm-add-form");
      if (form) stage(form);
    }
  });

  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (t && t.closest && t.closest("#cm-add-form")) {
      var review = document.getElementById("cm-add-review");
      if (review) review.textContent = "";
    }
    if (t && t.matches && t.matches("[data-cm-add-feedline]")) {
      load("/diagram-add/panel?kind=qubit&feedline=" + encodeURIComponent(t.value));
    }
  });

  document.addEventListener("input", function (ev) {
    var t = ev.target;
    if (!t || !t.matches || !t.matches("[data-cm-add-rf]")) return;
    var form = t.form;
    clearTimeout(portTimer);
    portTimer = setTimeout(function () { reproposePorts(form); }, 250);
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    var g = ev.target && ev.target.closest && ev.target.closest(".cm-dir-ghost");
    if (!g) return;
    ev.preventDefault();
    onMapClick(g);
  });

  // A staged add: the page's table and map re-render with the new entity,
  // and the result line is carried over into the fresh panel.
  document.addEventListener("diagram-added", function () {
    mode = null; picked = [];
    var rev = document.getElementById("cm-add-review");
    var note = rev ? rev.innerHTML : "";
    var page = document.querySelector('.cmap[data-highlight="pairs"]') ? "/pairs" : "/qubits";
    if (!document.querySelector("#table-pane") || !window.htmx) return;
    window.htmx.ajax("GET", page, { target: "#table-pane", swap: "innerHTML" }).then(function () {
      var p = panel();
      if (p && note) p.innerHTML = note;
    });
  });

  return { onMapClick: onMapClick, cancel: cancel, _state: function () { return { mode: mode, picked: picked.slice() }; } };
})();
