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

  /* docs/109 ③ — hover summary card. The card IS the entity's table row,
   * re-read as label→value pairs at hover time (headers from the row's own
   * <thead>), so it can never disagree with the page and needs no endpoint —
   * and the P(·) physical columns ride along already unit-formatted. Numbers
   * stay OFF the drawing (docs/92 §2.4 pin — this is transient
   * detail-on-demand, and the same data lives permanently in the table
   * below). Body-level singleton (the docs/89 overflow-clip lesson),
   * pointer-events:none so it can never trap the hover, DOM-API text only
   * (wiring-borne labels are text, never innerHTML). */
  var _pop = null;
  var _popEnt = null;          // "kind:id" of the card currently shown
  var _popTitleEl = null;      // the SVG <title> we emptied while showing it
  var _popTitleText = "";
  function _popupEl() {
    if (_pop && _pop.isConnected) return _pop;
    _pop = document.createElement("div");
    _pop.className = "cm-popup";
    _pop.setAttribute("role", "tooltip");
    _pop.hidden = true;
    document.body.appendChild(_pop);
    return _pop;
  }
  function _restoreNativeTitle() {
    if (_popTitleEl) { _popTitleEl.textContent = _popTitleText; }
    _popTitleEl = null; _popTitleText = "";
  }
  function _hidePopup() {
    if (_pop) _pop.hidden = true;
    _popEnt = null;
    _restoreNativeTitle();
  }
  // Audit: the map is swapped/scrolled under a motionless cursor — no
  // mouseleave fires on a removed container, so the fixed card would float
  // stale. One module-level guard each closes both cases for every mount.
  document.addEventListener("htmx:beforeSwap", _hidePopup);
  document.addEventListener("scroll", _hidePopup, true);
  function _showPopup(ent, row, el, ev) {
    var key = ent.kind + ":" + ent.id;
    if (_popEnt === key) return;   // same entity — no rebuild/reposition churn
    _restoreNativeTitle();
    // Audit: the stone/edge carries a native SVG <title> (the id) — dwelling
    // would render the OS tooltip ON TOP of the card repeating its title
    // line. Park exactly the hovered group's title while the card shows;
    // child marks (freq chevron Δ, feedline ports) keep theirs — the card
    // does not carry those numbers (docs/93 tooltip doctrine).
    var nt = el ? el.querySelector("title") : null;
    if (nt && nt.parentNode === el && nt.textContent) {
      _popTitleEl = nt; _popTitleText = nt.textContent; nt.textContent = "";
    }
    var pop = _popupEl();
    while (pop.firstChild) pop.removeChild(pop.firstChild);
    var title = document.createElement("div");
    title.className = "cm-popup-title";
    title.textContent = (ent.kind === "pair" ? "pair " : "") + ent.id;
    pop.appendChild(title);
    if (row) {
      var table = row.closest("table");
      var ths = table ? table.querySelectorAll("thead th") : [];
      var dl = document.createElement("div");
      dl.className = "cm-popup-rows";
      // Audit: header alignment must advance by colSpan — the pairs page's
      // poisoned-run rows are `<td>id</td><td colspan="7">⚠ …</td>` and a raw
      // index pairing would label the error text "Control".
      var hcur = 0;
      for (var i = 0; i < row.cells.length; i++) {
        var span = row.cells[i].colSpan || 1;
        var label = (span === 1 && hcur < ths.length)
          ? (ths[hcur].textContent || "").trim() : "";
        var val = (row.cells[i].textContent || "").trim();
        hcur += span;
        if (i === 0 || !val) continue;   // col 0 repeats the title
        var r = document.createElement("div");
        r.className = "cm-popup-row";
        if (label) {
          var k = document.createElement("span");
          k.className = "cm-popup-k";
          k.textContent = label;
          r.appendChild(k);
        }
        var v = document.createElement("span");
        v.className = "cm-popup-v";
        v.textContent = val;
        r.appendChild(v);
        dl.appendChild(r);
      }
      pop.appendChild(dl);
    }
    var hint = document.createElement("div");
    hint.className = "cm-popup-hint";
    hint.textContent = "click to open in the inspector";
    pop.appendChild(hint);
    pop.hidden = false;
    _popEnt = key;
    // place near the cursor, clamped to the viewport (flip left/up at edges)
    var pad = 14;
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
    pop.style.left = Math.max(4, x) + "px";
    pop.style.top = Math.max(4, y) + "px";
  }
  function _clearHotEnt() {
    if (_hotEnt && _active && _active.api) {
      _active.api.highlightEntity(_hotEnt.kind, _hotEnt.id, false);
    }
    _hotEnt = null;
  }

  // map -> table (and map -> inspector on click; docs/109 ③ hover card)
  function _bindMapEvents(body) {
    body.addEventListener("mouseover", function (ev) {
      var el = ev.target.closest && ev.target.closest("[data-cm]");
      _clearHotRow();
      if (!el) { _hidePopup(); return; }
      var e = _parseCm(el);
      if (!e) { _hidePopup(); return; }
      var row = _rowFor(e.kind, e.id);
      if (row) {
        row.classList.add("cm-row-hot");
        _hotRow = row;
      }
      // Audit: an entity with no row on THIS page (a qubit stone on the
      // couplers page) still gets the minimal card — title + the click
      // hint — so hover never feels dead on any component page.
      _showPopup(e, row, el, ev);
    });
    body.addEventListener("mouseleave", function () {
      _clearHotRow();
      _hidePopup();
    });
    body.addEventListener("click", function (ev) {
      _hidePopup();
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
    _hidePopup();   // audit: a re-mount under a motionless cursor must not
                    // leave the previous map's card floating
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
