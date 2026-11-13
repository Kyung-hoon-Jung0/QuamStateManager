/* Add / remove a qubit or a pair -- window.DiagramAdd.
 *
 * The map draws two ghost stones ("+" and "+ pair", TopoGraph ghostStones).
 * "+" opens the add panel (GET /diagram-add/panel) under the map; while it is
 * open a click on a qubit stone moves the new qubit to that stone's feedline.
 * "+ pair" arms a two-stone pick (the button stays pressed and the pick says
 * so, brought into view); the second stone opens the pair panel. An entity's
 * inspector carries "Remove..." (GET /diagram-remove/panel).
 *
 * Every panel shows the instrument wiring rack (the /instrument renderer,
 * renderInstrumentWiring, fed by GET /api/instrument/data). A port field is a
 * listbox: the chosen port is lit on the rack, and the option under the
 * pointer or the keyboard focus lights too; leaving returns to the chosen one.
 * A removal lights the ports it frees.
 *
 * Review (dry=1) loads the RESULT in the chip's env (POST /diagram-add/check,
 * triggered by the review itself); the stage button arrives with a passing
 * answer. Staging writes the working copy only: the pending tray, Review /
 * Apply and one Ctrl+Z hold it; nothing here writes the live files.
 * House convention: framework-free IIFE.
 */
window.DiagramAdd = (function () {
  "use strict";

  var mode = null;          // null | "qubit" | "pair"
  var picked = [];          // pair mode: stone ids, in click order
  var portTimer = null;
  var portSeq = 0;
  var rackData = null;      // one /api/instrument/data answer per panel open

  function panel() { return document.getElementById("cm-add-panel"); }

  function reveal(el) {
    if (!el || !el.scrollIntoView) return;
    try { el.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (e) { /* jsdom */ }
  }

  function load(url) {
    var p = panel();
    if (!p || !window.htmx) return;
    window.htmx.ajax("GET", url, { target: "#cm-add-panel", swap: "innerHTML" });
  }

  function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s).replace(/"/g, '\\"');
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
    var pairBtn = map.querySelector('[data-cm="add:pair"]');
    if (pairBtn) {
      var on = mode === "pair";
      pairBtn.classList.toggle("cm-dir-ghost--armed", on);
      pairBtn.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function hint(text) {
    var p = panel();
    if (!p) return;
    p.textContent = "";
    var n = document.createElement("p");
    n.className = "ui-callout ui-callout--note cm-add-hint";
    n.setAttribute("data-cm-add-hint", "");
    n.textContent = text + " ";
    var c = document.createElement("button");
    c.type = "button"; c.className = "btn-sm outline secondary";
    c.setAttribute("data-cm-add-cancel", ""); c.textContent = "Cancel";
    n.appendChild(c);
    p.appendChild(n);
    reveal(n);
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
      if (mode === "pair") { cancel(); return true; }   // a second press un-arms it
      mode = "pair"; picked = []; markPicked();
      hint("Adding a pair: click the control qubit on the map, then the target qubit.");
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
      hint(picked.length ? "Adding a pair: control " + picked[0] + ". Now click the target qubit."
                         : "Adding a pair: click the control qubit on the map, then the target qubit.");
    }
    return true;
  }

  // ------------------------------------------------------------ the rack

  // "con1/2/7" + io ("out" | "in") -> the rack's <g class="iw-port"> or null.
  function portCell(rack, value, io) {
    if (!rack || !value) return null;
    var p = String(value).split("/");
    if (p.length !== 3) return null;
    var cells = rack.querySelectorAll('.iw-port[data-con="' + cssEsc(p[0]) + '"][data-slot="' +
                                      cssEsc(p[1]) + '"][data-port="' + cssEsc(p[2]) + '"]');
    for (var i = 0; i < cells.length; i++) {
      var cio = cells[i].getAttribute("data-io") || "";
      if (cio.indexOf(io || "out") === 0) return cells[i];
    }
    return null;
  }

  function clearLit(rack, cls) {
    var els = rack.querySelectorAll("." + cls);
    for (var i = 0; i < els.length; i++) {
      els[i].classList.remove(cls);
      els[i].removeAttribute("data-cm-lit-role");
    }
    var rings = rack.querySelectorAll(".cm-lit-ring--" + cls.slice(7));
    for (var j = 0; j < rings.length; j++) rings[j].parentNode.removeChild(rings[j]);
  }

  // A ring larger than the port, so a rack scaled to fit still shows it.
  function ring(cell, kind, role) {
    var cx, cy, r;
    try {
      var b = cell.getBBox();
      cx = b.x + b.width / 2; cy = b.y + b.height / 2; r = Math.max(b.width, b.height) / 2;
    } catch (e) { b = null; }
    if (r === undefined || !isFinite(r) || r <= 0) {
      var c = cell.querySelector("circle");
      if (!c) return;
      cx = +c.getAttribute("cx"); cy = +c.getAttribute("cy"); r = +c.getAttribute("r") || 6;
    }
    var o = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    o.setAttribute("class", "cm-lit-ring cm-lit-ring--" + kind);
    o.setAttribute("cx", cx); o.setAttribute("cy", cy); o.setAttribute("r", r * (kind === "sel" || kind === "hover" ? 2.2 : 1.6) + 4);
    if (role) o.setAttribute("data-role", role);
    cell.appendChild(o);
  }

  function light(rack, value, io, cls, role) {
    var cell = portCell(rack, value, io);
    if (cell) {
      cell.classList.add(cls);
      if (role) cell.setAttribute("data-cm-lit-role", role);
      ring(cell, cls.slice(7), role);
    }
    return cell;
  }

  function rackOf(el) {
    var box = el.closest("[data-cm-box]");
    return box ? box.querySelector("[data-cm-rack]") : null;
  }

  function note(rack, text) {
    var box = rack && rack.closest("[data-cm-box]");
    var n = box && box.querySelector("[data-cm-rack-note]");
    if (n) n.textContent = text || "";
  }

  // Light every list's chosen port; ``hoverOpt`` (an option) lights too.
  function relight(rack, hoverOpt) {
    if (!rack || !rack.querySelector(".iw-port")) return;
    clearLit(rack, "cm-lit-sel"); clearLit(rack, "cm-lit-hover");
    var box = rack.closest("[data-cm-box]");
    var lists = box ? box.querySelectorAll("[data-cm-port-list]") : [];
    var missing = [];
    for (var i = 0; i < lists.length; i++) {
      var l = lists[i];
      var hid = box.querySelector('input[data-cm-port-value="' + l.getAttribute("data-cm-port-list") + '"]');
      if (hid && hid.value && !light(rack, hid.value, l.getAttribute("data-io"), "cm-lit-sel", l.getAttribute("data-role")))
        missing.push(hid.value);
    }
    if (hoverOpt) {
      var hl = hoverOpt.closest("[data-cm-port-list]");
      var v = hoverOpt.getAttribute("data-value");
      var hc = v && light(rack, v, hl.getAttribute("data-io"), "cm-lit-hover", hl.getAttribute("data-role"));
      if (v && !hc) missing.push(v);
      else if (hc) inView(rack, hc);
    }
    note(rack, missing.length ? "Not drawn on the rack (that FEM declares no such port yet): " + missing.join(", ") : "");
  }

  // A 1:1 rack wider than the pane scrolls sideways: bring the hovered port
  // into view horizontally (never the page vertically).
  function inView(rack, cell) {
    var host = cell.parentNode;
    var scrolls = function (el) {
      if (!(el.scrollWidth > el.clientWidth + 1) || !window.getComputedStyle) return false;
      var o = window.getComputedStyle(el).overflowX;
      return o === "auto" || o === "scroll";
    };
    while (host && host !== rack.parentNode && !scrolls(host)) host = host.parentNode;
    if (!host || host === rack.parentNode || !cell.getBoundingClientRect) return;
    var c = cell.getBoundingClientRect(), h = host.getBoundingClientRect();
    if (c.left < h.left || c.right > h.right) host.scrollLeft += (c.left + c.right) / 2 - (h.left + h.right) / 2;
  }

  function staticLights(rack) {
    var list = [];
    try { list = JSON.parse(rack.getAttribute("data-light") || "[]"); } catch (e) { list = []; }
    var cls = rack.id === "cm-rm-rack" ? "cm-lit-freed" : "cm-lit-shared";
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      light(rack, e.con + "/" + e.fem + "/" + e.port, e.io || "out", cls, null);
    }
  }

  function drawRack(rack) {
    if (!rack || typeof window.renderInstrumentWiring !== "function") return;
    var go = function (d) {
      if (!rack.isConnected) return;
      if (!d || d.error || !d.instrument) {
        rack.textContent = "The wiring rack could not be drawn" + (d && d.error ? ": " + d.error : ".");
        return;
      }
      window.renderInstrumentWiring(rack.id, d.instrument, d.wiring || {}, {});
      staticLights(rack);
      relight(rack, null);
    };
    if (rackData) { go(rackData); return; }
    fetch("/api/instrument/data", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { rackData = d; go(d); })
      .catch(function () { go(null); });
  }

  // ------------------------------------------------------- the port lists

  function options(list) {
    return list.querySelectorAll('[role="option"][data-value]');
  }

  function hiddenOf(list) {
    var box = list.closest("[data-cm-box]") || document;
    return box.querySelector('input[data-cm-port-value="' + list.getAttribute("data-cm-port-list") + '"]');
  }

  function choose(opt) {
    var list = opt.closest("[data-cm-port-list]");
    var hid = hiddenOf(list);
    var opts = options(list);
    for (var i = 0; i < opts.length; i++) opts[i].setAttribute("aria-selected", opts[i] === opt ? "true" : "false");
    list.setAttribute("aria-activedescendant", opt.id);
    if (hid && hid.value !== opt.getAttribute("data-value")) {
      hid.value = opt.getAttribute("data-value");
      var box = list.closest("[data-cm-box]");
      var review = box && box.querySelector("[data-cm-review]");
      if (review) review.textContent = "";
    }
    var rack = rackOf(list);
    if (rack) relight(rack, null);
  }

  function focusOpt(list, opt) {
    var opts = options(list);
    for (var i = 0; i < opts.length; i++) opts[i].classList.toggle("ui-option--active", opts[i] === opt);
    if (opt) {
      list.setAttribute("aria-activedescendant", opt.id);
      if (opt.scrollIntoView) { try { opt.scrollIntoView({ block: "nearest" }); } catch (e) { /* jsdom */ } }
    }
    var rack = rackOf(list);
    if (rack) relight(rack, opt);
  }

  function fillList(list, cands, empty) {
    var name = list.getAttribute("data-cm-port-list");
    var hid = hiddenOf(list);
    var keep = hid ? hid.value : "";
    list.textContent = "";
    var kept = null;
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i], o = document.createElement("div");
      o.className = "ui-option"; o.setAttribute("role", "option");
      o.id = "cmopt-" + name + "-" + (i + 1);
      o.setAttribute("data-value", c.con + "/" + c.fem + "/" + c.port);
      o.setAttribute("data-new", c["new"] ? "1" : "0");
      o.textContent = c.label + " ";
      var why = document.createElement("span");
      why.className = "ui-table-muted"; why.textContent = "(" + c.why + ")";
      o.appendChild(why);
      list.appendChild(o);
      if (o.getAttribute("data-value") === keep) kept = o;
    }
    if (!cands.length) {
      var none = document.createElement("div");
      none.className = "ui-option ui-option--empty"; none.setAttribute("aria-disabled", "true");
      none.textContent = empty;
      list.appendChild(none);
      if (hid) hid.value = "";
      list.removeAttribute("aria-activedescendant");
      var rack = rackOf(list);
      if (rack) relight(rack, null);
      return;
    }
    choose(kept || options(list)[0]);
  }

  // Re-propose the drive outputs for a typed frequency: one request per pause
  // in typing, and only the newest answer lands.
  function reproposePorts(form) {
    var rf = form.querySelector("[data-cm-add-rf]");
    var list = form.querySelector("[data-cm-add-drive]");
    var fl = form.querySelector('input[name="feedline"]');
    if (!rf || !list || !fl) return;
    var mine = ++portSeq;
    fetch("/api/diagram-add/ports?feedline=" + encodeURIComponent(fl.value) +
          "&drive_rf=" + encodeURIComponent(rf.value), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || mine !== portSeq || !list.isConnected) return;
        fillList(list, j.drive || [], "no free MW-FEM output fits");
      })
      .catch(function () {});
  }

  function stage(btn) {
    var box = btn.closest("[data-cm-box]");
    var form = box && box.querySelector("form");
    var review = box && box.querySelector("[data-cm-review]");
    if (!form || !review || !window.htmx) return;
    var vals = window.htmx.values ? window.htmx.values(form) : {};
    vals.dry = "0";
    if (btn.getAttribute("data-override") === "1") vals.override = "1";
    btn.disabled = true;
    window.htmx.ajax("POST", "/diagram-add/stage", {
      source: form, target: "#" + review.id, swap: "innerHTML", values: vals,
    });
  }

  // "Delete together" on a removal the lab's code refused: the named paths
  // join the removal form, which is reviewed (and env-checked) again whole
  function together(btn) {
    var form = document.getElementById("cm-rm-form");
    if (!form) return;
    var also = [];
    try { also = JSON.parse(btn.getAttribute("data-also") || "[]"); } catch (e) { also = []; }
    Array.prototype.forEach.call(form.querySelectorAll('input[name="also"]'),
      function (i) { i.remove(); });
    also.forEach(function (a) {
      var i = document.createElement("input");
      i.type = "hidden"; i.name = "also"; i.value = a;
      form.appendChild(i);
    });
    btn.disabled = true;
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }

  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    if (t.closest("[data-cm-add-cancel]")) { cancel(); return; }
    if (t.closest("[data-cm-rm-cancel]")) {
      var slot = document.getElementById("cm-rm-slot");
      if (slot) slot.textContent = "";
      return;
    }
    var sw = t.closest("[data-cm-add-swap]");
    if (sw) {
      picked = [sw.getAttribute("data-a"), sw.getAttribute("data-b")];
      markPicked();
      load("/diagram-add/panel?kind=pair&a=" + encodeURIComponent(picked[0]) +
           "&b=" + encodeURIComponent(picked[1]));
      return;
    }
    var opt = t.closest('[data-cm-port-list] [role="option"][data-value]');
    if (opt) { choose(opt); return; }
    var tg = t.closest("[data-cm-rm-together]");
    if (tg) { together(tg); return; }
    var st = t.closest("[data-cm-add-stage]");
    if (st) stage(st);
  });

  document.addEventListener("mouseover", function (ev) {
    var t = ev.target;
    var opt = t && t.closest && t.closest('[data-cm-port-list] [role="option"][data-value]');
    if (!opt) return;
    var rack = rackOf(opt);
    if (rack) relight(rack, opt);
  });

  document.addEventListener("mouseout", function (ev) {
    var t = ev.target;
    var list = t && t.closest && t.closest("[data-cm-port-list]");
    if (!list) return;
    var to = ev.relatedTarget;
    if (to && list.contains(to)) return;
    var rack = rackOf(list);
    if (rack) relight(rack, null);
  });

  document.addEventListener("focusout", function (ev) {
    var list = ev.target && ev.target.closest && ev.target.closest("[data-cm-port-list]");
    if (list) focusOpt(list, null);
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
    var list = ev.target && ev.target.closest && ev.target.closest("[data-cm-port-list]");
    if (list && ev.target === list) {
      var opts = Array.prototype.slice.call(options(list));
      if (!opts.length) return;
      var cur = list.querySelector(".ui-option--active") ||
                list.querySelector('[aria-selected="true"]') || opts[0];
      var i = opts.indexOf(cur);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        i = ev.key === "ArrowDown" ? Math.min(opts.length - 1, i + 1) : Math.max(0, i - 1);
        focusOpt(list, opts[i]);
      } else if (ev.key === "Home" || ev.key === "End") {
        ev.preventDefault();
        focusOpt(list, ev.key === "Home" ? opts[0] : opts[opts.length - 1]);
      } else if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        choose(cur);
        focusOpt(list, cur);
      } else if (ev.key === "Escape") {
        focusOpt(list, null);
      }
      return;
    }
    if (ev.key !== "Enter" && ev.key !== " ") return;
    var g = ev.target && ev.target.closest && ev.target.closest(".cm-dir-ghost");
    if (!g || g.tagName === "BUTTON") return;   // a native button clicks itself
    ev.preventDefault();
    onMapClick(g);
  });

  // A panel landed: draw its rack, and bring it into view (it opens under
  // the map, below the fold on a short screen -- FB-10 D).
  document.addEventListener("htmx:afterSwap", function (ev) {
    var tgt = ev.detail && ev.detail.target;
    if (!tgt || (tgt.id !== "cm-add-panel" && tgt.id !== "cm-rm-slot")) return;
    rackData = null;
    var rack = tgt.querySelector("[data-cm-rack]");
    if (rack) drawRack(rack);
    reveal(tgt.firstElementChild);
  });

  // A staged add: the page's table and map re-render with the new entity,
  // and the result line is carried over into the fresh panel.
  function _refreshMapSurface(msg) {
    var map = document.querySelector("#table-pane .cmap[data-cm-add='1']");
    if (!map || !window.htmx) return;
    var page = map.getAttribute("data-highlight") === "pairs" ? "/pairs" : "/qubits";
    window.htmx.ajax("GET", page, { target: "#table-pane", swap: "innerHTML" }).then(function () {
      var p = panel();
      if (p && msg) p.innerHTML = msg;
    });
  }

  document.addEventListener("diagram-added", function () {
    mode = null; picked = [];
    var rev = document.getElementById("cm-add-review");
    var msg = rev ? rev.innerHTML : "";
    _refreshMapSurface(msg);
  });

  // A staged removal: the entity is gone, so its inspector closes and the
  // Qubits / Pairs page (when it is the one open) re-renders without it,
  // carrying the result line into its add panel.
  document.addEventListener("diagram-removed", function () {
    var rev = document.getElementById("cm-rm-review");
    var msg = rev ? rev.innerHTML : "";
    if (window.closeInspector) window.closeInspector();
    _refreshMapSurface(msg);
  });

  // Structural undo/redo (entity added/removed) lands through cellsReverted.
  // Refresh the map pages so the stone set and add/remove panels match store/live.
  document.addEventListener("cellsReverted", function (evt) {
    var d = (evt && evt.detail) || {};
    var entries = d.entries || [];
    var structural = !!d.structural || entries.some(function (e) { return e && (e.created || e.deleted); });
    if (!structural) return;
    _refreshMapSurface("");
  });

  return {
    onMapClick: onMapClick, cancel: cancel, portCell: portCell, relight: relight,
    _state: function () { return { mode: mode, picked: picked.slice() }; },
  };
})();
