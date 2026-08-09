/* Component-page chip layout (docs/92 P2) — window.ComponentMap.
 *
 * ONE shared drawing (TopoGraph.renderLayout — every component type's symbols,
 * no numbers) above every component table; the page only chooses WHAT LIGHTS
 * UP via the mount's data-highlight, and this module owns the map↔table hover
 * binding both ways plus the collapse persistence (docs/91 §6.5: default open,
 * the user's choice remembered — ONE key across all component pages, because
 * it is one picture).
 *
 * The tables are untouched: rows already carry data-qubit-id / data-pair-id,
 * and #table-pane (the HTMX swap TARGET, which persists across swaps) gets one
 * delegated hover listener that reads the CURRENT mount through a module-level
 * handle — re-mounting on every swap can therefore never stack listeners.
 * House convention: framework-free IIFE like app.js / topo-graph.js.
 */
window.ComponentMap = (function () {
  "use strict";

  var OPEN_KEY = "quam_component_map_open";
  var _active = null;          // {root, body, api} of the CURRENT mount
  var _hotRow = null;          // table row lit from the map
  var _hotEnt = null;          // "kind:id" lit from the table

  function _cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s);
  }

  function _rowFor(kind, id) {
    var sel = kind === "pair" ? 'tr[data-pair-id="' + _cssEsc(id) + '"]'
                              : 'tr[data-qubit-id="' + _cssEsc(id) + '"]';
    return document.querySelector(sel);
  }

  function _parseCm(el) {
    var v = el.getAttribute("data-cm") || "";
    var i = v.indexOf(":");
    if (i < 0) return null;
    return { kind: v.slice(0, i) === "p" ? "pair" : "qubit", id: v.slice(i + 1) };
  }

  function _clearHotRow() {
    if (_hotRow) { _hotRow.classList.remove("cm-row-hot"); _hotRow = null; }
  }
  function _clearHotEnt() {
    if (_hotEnt && _active && _active.api) {
      _active.api.highlightEntity(_hotEnt.kind, _hotEnt.id, false);
    }
    _hotEnt = null;
  }

  // map -> table (and map -> inspector on click)
  function _bindMapEvents(body) {
    body.addEventListener("mouseover", function (ev) {
      var el = ev.target.closest && ev.target.closest("[data-cm]");
      _clearHotRow();
      if (!el) return;
      var e = _parseCm(el);
      if (!e) return;
      var row = _rowFor(e.kind, e.id);
      if (row) { row.classList.add("cm-row-hot"); _hotRow = row; }
    });
    body.addEventListener("mouseleave", _clearHotRow);
    body.addEventListener("click", function (ev) {
      var el = ev.target.closest && ev.target.closest("[data-cm]");
      if (!el) return;
      var e = _parseCm(el);
      if (!e || !window.htmx) return;
      htmx.ajax("GET", (e.kind === "pair" ? "/pair/" : "/qubit/") + encodeURIComponent(e.id),
                { source: "#inspector-pane", target: "#inspector-pane", swap: "innerHTML" });
    });
  }

  // Feedline legend (docs/93 F4) — resonators page only: one swatch + port
  // label + count per drawn bus. Built with DOM APIs (port labels come from
  // wiring — textContent, never innerHTML) and doubles as the relief the
  // light-mode contrast WARN obligates (identity is never colour-alone).
  function _renderFeedLegend(body, highlight, api) {
    if (highlight !== "resonators" || !api || !api.feeds || !api.feeds.length) return;
    var box = document.createElement("div");
    box.className = "cm-feed-legend";
    for (var i = 0; i < api.feeds.length; i++) {
      var f = api.feeds[i];
      var item = document.createElement("span");
      item.className = "cm-feed-lg";
      var sw = document.createElement("i");
      sw.className = "cm-feed-swatch cm-feed-s" + f.slot;
      item.appendChild(sw);
      item.appendChild(document.createTextNode(
        f.label + " · " + f.count + " resonator" + (f.count === 1 ? "" : "s")));
      box.appendChild(item);
    }
    body.appendChild(box);
  }

  // table -> map: ONE delegated listener on the persistent pane; reads the
  // current mount at event time so swaps can't stack handlers.
  function _paneOver(ev) {
    if (!_active || !_active.api) return;
    var row = ev.target.closest && ev.target.closest("tr[data-qubit-id], tr[data-pair-id]");
    var ent = null;
    if (row) {
      var qid = row.getAttribute("data-qubit-id");
      ent = qid != null ? { kind: "qubit", id: qid }
                        : { kind: "pair", id: row.getAttribute("data-pair-id") };
    }
    if (_hotEnt && ent && _hotEnt.kind === ent.kind && _hotEnt.id === ent.id) return;
    _clearHotEnt();
    if (ent) {
      _active.api.highlightEntity(ent.kind, ent.id, true);
      _hotEnt = ent;
    }
  }
  function _paneLeave() { _clearHotEnt(); }

  function _bindPane(root) {
    var pane = (root.closest && root.closest("#table-pane")) || document.body;
    if (pane._cmapBound) return;
    pane._cmapBound = true;
    pane.addEventListener("mouseover", _paneOver);
    pane.addEventListener("mouseleave", _paneLeave);
  }

  function mount(root) {
    if (!root || !window.TopoGraph) return;
    var body = root.querySelector(".cmap-body");
    if (!body) return;
    _clearHotRow(); _hotEnt = null;
    _active = { root: root, body: body, api: null };

    // collapse persistence — default open (docs/91 §6.5), remembered choice wins
    try {
      var saved = localStorage.getItem(OPEN_KEY);
      if (saved === "0") root.removeAttribute("open");
      else if (saved === "1") root.setAttribute("open", "open");
    } catch (e) {}
    root.addEventListener("toggle", function () {
      try { localStorage.setItem(OPEN_KEY, root.open ? "1" : "0"); } catch (e) {}
      if (root.open && !root._cmapLoaded) load();
    });

    function load() {
      root._cmapLoaded = true;
      var mine = _active;
      fetch("/api/topology", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (topo) {
          if (_active !== mine || !body.isConnected) return;   // superseded by a newer swap
          if (!topo || !topo.nodes || !topo.nodes.length) {
            body.innerHTML = '<p class="muted" style="margin:0">Chip layout unavailable.</p>';
            return;
          }
          var highlight = root.getAttribute("data-highlight") || "";
          mine.api = window.TopoGraph.renderLayout(body, {
            nodes: topo.nodes,
            edges: topo.edges || [],
            highlight: highlight,
            // docs/93 F2: users asked for ~1.9x — the mount declares the cell
            // so the drawing AND its text scale together (renderLayout derives
            // the id font from the cell).
            cell: parseInt(root.getAttribute("data-cell"), 10) || undefined,
            // docs/93 F3: the page's active chain filter lights its qubits
            emphasisChain: root.getAttribute("data-chain") || "",
          });
          _renderFeedLegend(body, highlight, mine.api);
          _bindMapEvents(body);
        })
        .catch(function () {
          if (_active === mine && body.isConnected) {
            body.innerHTML = '<p class="muted" style="margin:0">Chip layout unavailable.</p>';
          }
        });
    }

    _bindPane(root);
    if (root.open) load();
  }

  return { mount: mount };
})();
