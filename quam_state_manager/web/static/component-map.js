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
  // docs/166: a mount may claim its own memory. The Pulses map is a row
  // FILTER above a table, not the page's subject, so collapsing it must
  // not also collapse the component pages' drawing (one key, two roles).
  function openKey(root) {
    return (root && root.getAttribute("data-open-key")) || OPEN_KEY;
  }
  // A PICK mount is a control: its click belongs to the page that mounted it,
  // and the hover card must not promise an inspector it will not open.
  function _isPick() {
    return !!(_active && _active.root
              && _active.root.getAttribute("data-cm-pick") === "1");
  }
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

  // The explicit detail panel reads the current table row, including units.
  // Shared TopoGraph controls own its affordance, positioning and dismissal.
  var _pop = null;
  var _popEnt = null;          // "kind:id" of the card currently shown
  var _popAnchor = null;      // the anchor of the card shown
  var _popTitleEl = null;      // the SVG <title> we emptied while showing it
  var _popTitleText = "";
  function _popupEl() {
    if (_pop && _pop.isConnected) return _pop;
    _pop = document.createElement("div");
    _pop.className = "cm-popup";
    _pop.setAttribute("role", "dialog");
    _pop.hidden = true;
    document.body.appendChild(_pop);
    return _pop;
  }
  function _restoreNativeTitle() {
    if (_popTitleEl) { _popTitleEl.textContent = _popTitleText; }
    _popTitleEl = null; _popTitleText = "";
  }
  function _hidePopup() {
    if (window.TopoGraph) window.TopoGraph.closeDetails();
    if (_pop) _pop.hidden = true;
    _popEnt = null;
    _restoreNativeTitle();
  }
  // Audit: the map is swapped/scrolled under a motionless cursor — no
  // mouseleave fires on a removed container, so the fixed card would float
  // stale. One module-level guard each closes both cases for every mount.


  var _ABSENT = { "-": 1, "—": 1, "–": 1, "?": 1 };
  // Pairs page: rows served by the page (core/pair_map_panel), one per pair.
  function _pairPanelRows(id) {
    var el = document.getElementById("cm-pair-panel");
    if (!el) return null;
    if (el._parsed === undefined) {
      try { el._parsed = JSON.parse(el.textContent || "{}"); } catch (e) { el._parsed = null; }
    }
    var rows = el._parsed && el._parsed[id];
    return rows && rows.length ? rows : null;
  }

  // Hover panels (Qubits / Pairs pages only): a map whose click does something
  // else -- a pick filter, the add door's ghosts -- keeps the explicit Expand.
  function _hoverMode(root) {
    if (!root || root.getAttribute("data-cm-pick") === "1") return false;
    var hl = root.getAttribute("data-highlight") || "";
    return hl === "qubits" || hl === "pairs";
  }
  function _boxOf(el) {
    var s = el.querySelector(".cm-stone") || el.querySelector(".cm-edge-hit, .cm-hit") || el;
    return s.getBoundingClientRect();
  }
  // Place the panel beside its anchor on the side that covers the fewest
  // other map entities, so the pointer can still reach a neighbour.
  function _placeAvoiding(pop, anchor) {
    var b = pop.getBoundingClientRect();
    var gap = 10, pad = 8, W = window.innerWidth, H = window.innerHeight;
    var body = anchor.closest(".cmap-body") || document;
    // a pair's own end stones belong to it: the panel goes clear of all three
    var ends = (anchor.getAttribute("data-link-ends") || "").split(/\s+/).filter(Boolean);
    var mine = [anchor];
    ends.forEach(function (id) {
      var st = body.querySelector('[data-cm="q:' + _cssEsc(id) + '"]');
      if (st) mine.push(st);
    });
    var a = null;
    mine.forEach(function (e) {
      var r = _boxOf(e);
      if (!r.width && !r.height) return;
      a = a ? { left: Math.min(a.left, r.left), top: Math.min(a.top, r.top),
                right: Math.max(a.right, r.right), bottom: Math.max(a.bottom, r.bottom) } : r;
    });
    if (!a) a = _boxOf(anchor);
    var others = Array.prototype.filter.call(body.querySelectorAll("[data-cm]"), function (e) {
      return mine.indexOf(e) < 0 && !e.classList.contains("cm-dir-ghost");
    }).map(_boxOf).filter(function (r) { return r.width || r.height; });
    var cx = (a.left + a.right - b.width) / 2, cy = (a.top + a.bottom - b.height) / 2;
    var cands = [[a.right + gap, cy], [a.left - b.width - gap, cy], [cx, a.bottom + gap],
                 [cx, a.top - b.height - gap], [a.right + gap, a.top], [a.left - b.width - gap, a.top],
                 [a.right + gap, a.bottom - b.height], [a.left - b.width - gap, a.bottom - b.height]];
    var best = null, bestScore = Infinity;
    cands.forEach(function (c) {
      var x = Math.max(pad, Math.min(c[0], W - b.width - pad));
      var y = Math.max(pad, Math.min(c[1], H - b.height - pad));
      var score = 0;
      others.concat([a]).forEach(function (r, i) {
        var w = Math.min(x + b.width, r.right) - Math.max(x, r.left);
        var h = Math.min(y + b.height, r.bottom) - Math.max(y, r.top);
        // covering its own anchor is worst: the pointer would lose it
        if (w > 0 && h > 0) score += w * h * (i === others.length ? 4 : 1) + 1e5;
      });
      if (score < bestScore) { bestScore = score; best = [x, y]; }
    });
    pop.style.left = Math.round(best[0]) + "px";
    pop.style.top = Math.round(best[1]) + "px";
  }

  function _servedRowsEl(rows) {
    var sl = document.createElement("div");
    sl.className = "cm-popup-rows";
    sl.setAttribute("data-served", "");
    rows.forEach(function (rw) {
      var r = document.createElement("div");
      r.className = "cm-popup-row";
      var k = document.createElement("span");
      k.className = "cm-popup-k";
      k.textContent = rw[0];
      var v = document.createElement("span");
      v.className = "cm-popup-v";
      v.textContent = rw[1];
      if (rw[2]) {
        var n = document.createElement("small");
        n.className = "muted";
        n.textContent = rw[2];
        v.appendChild(document.createElement("br"));
        v.appendChild(n);
      }
      r.appendChild(k); r.appendChild(v);
      sl.appendChild(r);
    });
    return sl;
  }
  function _refreshPairRows(pop, id, key) {
    if (!window.fetch) return;
    fetch("/api/pair-map-panel/" + encodeURIComponent(id), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var rows = j && j.rows;
        if (!rows || !rows.length) return;
        var el = document.getElementById("cm-pair-panel");
        if (el && el._parsed) el._parsed[id] = rows;
        if (_popEnt !== key || !pop.isConnected || pop.hidden) return;
        var fresh = _servedRowsEl(rows), old = pop.querySelector(".cm-popup-rows");
        if (old) pop.replaceChild(fresh, old);
        else pop.insertBefore(fresh, pop.querySelector(".cm-popup-hint"));
        if (pop._place) pop._place(_popAnchor || pop);
      })
      .catch(function () {});
  }

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
    pop._closeDetail = function () { pop.hidden = true; _popEnt = null; _restoreNativeTitle(); };
    while (pop.firstChild) pop.removeChild(pop.firstChild);
    var title = document.createElement("div");
    title.className = "cm-popup-title";
    title.textContent = (ent.kind === "pair" ? "pair " : "") + ent.id;
    var close = document.createElement("button");
    close.type = "button"; close.className = "topo-popup-close";
    close.textContent = "X"; close.setAttribute("aria-label", "Close details");
    close.addEventListener("click", function () { window.TopoGraph.closeDetails(); });
    title.appendChild(close);
    pop.appendChild(title);
    var served = ent.kind === "pair" ? _pairPanelRows(ent.id) : null;
    // the page's copy shows at once; a fresh read replaces it (edits, undo, sync)
    if (ent.kind === "pair" && document.getElementById("cm-pair-panel")) _refreshPairRows(pop, ent.id, key);
    if (served) {
      pop.appendChild(_servedRowsEl(served));
    } else if (row) {
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
        // absent values have no row; an active entity needs no "Active" row
        if (_ABSENT[val] || (/^active$/i.test(label) && val === "✓")) continue;
        if (/^grid$/i.test(label)) continue;  // the map already shows where it sits
        // a value that already carries its unit does not repeat it in the label
        var um = /\s*\(([^()]+)\)\s*$/.exec(label);
        if (um && val.slice(-um[1].length - 1) === " " + um[1]) label = label.slice(0, um.index);
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
    var hint = document.createElement("button");
    hint.type = "button";
    hint.className = "cm-popup-hint";
    hint.setAttribute("data-detail-primary", "");
    // A pick mount may name its own action (data-cm-pick-hint); the Pulses page keeps its words.
    var root0 = _active && _active.root;
    var pickHint = _isPick() && root0.getAttribute("data-cm-pick-hint");
    hint.title = pickHint ? pickHint : _isPick() ? "click to show only its pulses"
                                 : "open in the inspector";
    hint.textContent = pickHint ? pickHint : _isPick() ? "Show pulses" : "Expand";
    hint.addEventListener("click", function (e) {
      e.stopPropagation();
      var pick = _isPick();
      _hidePopup();
      if (pick) {
        if (pickHint) {
          root0.dispatchEvent(new CustomEvent("cm:pick", { bubbles: true, detail: { kind: ent.kind, id: ent.id } }));
        } else if (window.pulsePickOwner) window.pulsePickOwner(ent.id);
      } else if (window.htmx) {
        htmx.ajax("GET", (ent.kind === "pair" ? "/pair/" : "/qubit/") + encodeURIComponent(ent.id),
                  { source: "#inspector-pane", target: "#inspector-pane", swap: "innerHTML" });
      }
    });
    if ((window.htmx || _isPick()) && (!pickHint || ent.kind === "pair")) pop.appendChild(hint);
    pop.hidden = false;
    _popEnt = key;
    _popAnchor = el;
    pop._place = _hoverMode(_active && _active.root) ? function (a) { _placeAvoiding(pop, a); } : null;
    window.TopoGraph.positionDetail(pop, el);
    return pop;
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
      if (!el) return;
      var e = _parseCm(el);
      if (!e) return;
      var row = _rowFor(e.kind, e.id);
      if (row) {
        row.classList.add("cm-row-hot");
        _hotRow = row;
      }
      // Audit: an entity with no row on THIS page (a qubit stone on the
      // couplers page) still gets the minimal card — title + the click
      // hint — so hover never feels dead on any component page.
      // Details are opened only by the shared Expand button.
    });
    body.addEventListener("mouseleave", function () {
      _clearHotRow();
    });
    if (window.TopoGraph.bindLinked) window.TopoGraph.bindLinked(body);
    // The add buttons are controls, not entities: no Expand on them (FB-10).
    window.TopoGraph.bindDetails(body, "[data-cm]:not(.cm-dir-ghost)", function (el) {
      return _parseCm(el).id;
    }, function (el) {
      var ent = _parseCm(el);
      return _showPopup(ent, _rowFor(ent.kind, ent.id), el);
    }, { hover: _hoverMode(_active && _active.root) });
    body.addEventListener("click", function (ev) {
      _hidePopup();
      if (_isPick()) return;    // the page owns this click (docs/166)
      var el = ev.target.closest && ev.target.closest("[data-cm]");
      if (!el) return;
      // the add door owns its ghost stones and, while it is open, the stones
      if (window.DiagramAdd && window.DiagramAdd.onMapClick(el)) return;
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

  // UD-3 (D-106): while the user has not chosen (no saved open/closed
  // state) the drawing is scaled down so the table head reaches the first
  // screen -- but never below an 11px map label. When that floor cannot bring
  // the head in, the map keeps its natural size. A theme repaint rebuilds the
  // SVG at natural size, so the observer puts the chosen size back.
  var MIN_LABEL_PX = 11;
  function _tableAfter(root) {
    var pane = (root.closest && root.closest("#table-pane")) || document.body;
    var ts = pane.querySelectorAll("table");
    for (var i = 0; i < ts.length; i++) {
      if (root.compareDocumentPosition(ts[i]) & Node.DOCUMENT_POSITION_FOLLOWING) return ts[i];
    }
    return null;
  }
  function fitUnsized(root, body) {
    root.removeAttribute("data-cmap-fit");
    body._cmapFitApply = null;
    if (root.getAttribute("data-cm-pick") === "1") return null;
    // "open" / "0" are a person's choice; "1" is what every page view wrote
    // before only a person's toggle was recorded, so it chose nothing
    try {
      var chosen = localStorage.getItem(openKey(root));
      if (chosen !== null && chosen !== "1") return null;
    } catch (e) { return null; }
    var svg = body.querySelector("svg.cm-svg");
    if (!svg) return null;
    var W = parseFloat(svg.getAttribute("width")), H = parseFloat(svg.getAttribute("height"));
    if (!(W > 0 && H > 0)) return null;
    var minFs = Infinity, ts = svg.querySelectorAll("text");
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      if (!t.getClientRects().length) continue;            // hidden by declutter / CSS
      var f = parseFloat(getComputedStyle(t).fontSize);
      if (f > 0 && f < minFs) minFs = f;
    }
    var table = _tableAfter(root);
    var pane = (root.closest && root.closest("#table-pane")) || null;
    if (!table || !isFinite(minFs)) return null;
    var head = table.tHead || table;
    var hr = head.getBoundingClientRect();
    var bottom = pane ? pane.getBoundingClientRect().bottom : (window.innerHeight || 800);
    // the head plus one more row's height must be on the first screen
    var need = hr.bottom + hr.height - bottom;
    var floor = MIN_LABEL_PX / minFs;
    var s = need > 0 ? (H - need) / H : 1;
    // UD-9 (b): when the head cannot come in, still shrink to the label floor.
    if (s < floor) s = Math.min(1, floor);
    root.setAttribute("data-cmap-fit", s >= 1 ? "1" : (s > floor ? s.toFixed(3) : "floor"));
    if (s >= 1) return null;
    function apply() {
      var g = body.querySelector("svg.cm-svg");
      if (!g || g.getAttribute("data-fit") === "1") return;
      g.setAttribute("width", String(Math.round(W * s)));
      g.setAttribute("height", String(Math.round(H * s)));
      g.setAttribute("data-fit", "1");
    }
    apply();
    // one observer per map body: a later load() replaces the callback only
    body._cmapFitApply = apply;
    if (window.MutationObserver && !body._cmapFitObs) {
      body._cmapFitObs = new MutationObserver(function () { if (body._cmapFitApply) body._cmapFitApply(); });
      body._cmapFitObs.observe(body, { childList: true });
    }
    return s;
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
      var saved = localStorage.getItem(openKey(root));
      if (saved === "0") root.removeAttribute("open");
      else if (saved === "1" || saved === "open") root.setAttribute("open", "open");
    } catch (e) {}
    // Only a person's toggle is a choice: a parsed <details open> fires its
    // own toggle event, which used to be saved as one on every page view.
    var sum = root.querySelector("summary");
    if (sum) sum.addEventListener("click", function () { root._cmapByUser = true; });
    root.addEventListener("toggle", function () {
      if (root._cmapByUser) {
        root._cmapByUser = false;
        try { localStorage.setItem(openKey(root), root.open ? "open" : "0"); } catch (e) {}
      }
      if (root.open && !root._cmapLoaded) load();
    });

    function load() {
      root._cmapLoaded = true;
      var mine = _active;
      // v1.3.0 (perf): the map draws structure only, so it asks for the
      // layout alone -- the per-gate RB derivation (a Datasets index lookup,
      // a cold scan of the whole archive on a first open) and the ledger's
      // calibration ages are Chip Status numbers this map never shows
      fetch("/api/topology?layout=1", { cache: "no-store" })
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
            ghost: root.getAttribute("data-cm-add") === "1",
          });
          _renderFeedLegend(body, highlight, mine.api);
          _bindMapEvents(body);
          fitUnsized(root, body);
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

  return { mount: mount, fitUnsized: fitUnsized };
})();
