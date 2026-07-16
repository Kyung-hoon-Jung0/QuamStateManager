/* Chip-topology BOARD for the Generate-Config wizard (window.WiringGrid).
 *
 * A baduk-style grid where the user PLACES qubits (writes grid_location) and draws
 * ARBITRARY edges between any two qubits (writes qubit_pairs) — ring / star /
 * heavy-hex, NOT just grid adjacency. The board NEVER allocates ports: it only
 * mutates state.spec.{qubits, qubit_pairs, populate.qubit[*].grid_location}; the
 * existing /generate/allocate consumes them, so band/LO safety + feature-D
 * persistence are preserved (see [[topology-board-wiring-redesign]]).
 *
 * Convention (no row-flip math here): the board renders row 0 at the BOTTOM (same
 * as Chip Status), so grid_location is stored RAW in QUAM convention (row 0 = chip
 * bottom). The shared TopoGraph applies the single read-side flip for the
 * read-only mounts. House convention: IIFE exposing window.WiringGrid.
 */
window.WiringGrid = (function () {
  "use strict";

  var CELL = 40;            // px per grid cell
  var STONE_R = 15;         // stone radius px
  var DRAG_THRESHOLD = 4;   // px before a press becomes a drag

  // UI-only state (the source of truth is state.spec).
  var _armed = null;        // qid armed as an edge endpoint (first click)
  var _sel = null;          // selected qid (highlight)
  var _drag = null;         // { qid, startX, startY, moved }
  var _onChange = null;     // host callback after a spec mutation (sync + persist)

  // Re-render the board AND notify the host (generate.js) so the pairs dropdown /
  // derived lines / draft stay in sync. `kind` tells the host WHAT changed
  // ("place" | "move" | "edge" | "delete" | "preset") so it can skip the heavy
  // pair-list/line re-derivation for placement/drag (which don't touch pairs) —
  // that gating is what keeps a 50-qubit hand-placement fast. Defaults to a full
  // sync when unknown, so callers that don't classify stay correct.
  function commit(kind) { if (_onChange) { try { _onChange(kind || "full"); } catch (e) {} } render(); }

  function S() { return (window.QuamGen && window.QuamGen.state) || null; }
  function spec() { var s = S(); return s && s.spec; }
  function root() { return document.getElementById("gen-topo-board"); }

  // -- grid_location <-> board cell (data coords; row 0 = chip bottom) ---------

  function popQubit(qid, create) {
    var sp = spec(); if (!sp) return null;
    var p = sp.populate || (create ? (sp.populate = {}) : null); if (!p) return null;
    var q = p.qubit || (create ? (p.qubit = {}) : null); if (!q) return null;
    return q[qid] || (create ? (q[qid] = {}) : null);
  }

  function cellOf(qid) {            // -> {col,row} data coords, or null
    var sp = spec(); if (!sp || !sp.populate || !sp.populate.qubit) return null;
    var pq = sp.populate.qubit[qid]; if (!pq || pq.grid_location == null) return null;
    var parts = String(pq.grid_location).split(",");
    if (parts.length !== 2) return null;
    var c = parseInt(parts[0], 10), r = parseInt(parts[1], 10);
    return (isNaN(c) || isNaN(r)) ? null : { col: c, row: r };
  }

  function setCell(qid, col, row) { popQubit(qid, true).grid_location = col + "," + row; }
  function clearCell(qid) {
    var pq = popQubit(qid, false);
    if (pq) delete pq.grid_location;
  }

  // -- zone (board extent). Stored on state so it persists in the draft. -------

  // Bounding-box extent of currently-placed qubits (0 if none) — used as a FLOOR
  // for the zone so a stone can never fall outside the rendered grid (shrinking the
  // zone below a placement would otherwise strand the stone off-canvas, unclickable).
  function placedExtent() {
    var qs = qubits(), maxc = -1, maxr = -1;
    for (var i = 0; i < qs.length; i++) {
      var c = cellOf(qs[i]);
      if (c) { if (c.col > maxc) maxc = c.col; if (c.row > maxr) maxr = c.row; }
    }
    return { cols: maxc + 1, rows: maxr + 1 };
  }
  function zone() {
    var s = S();
    var z = s && s.topoZone, cols, rows;
    if (z && z.cols && z.rows) { cols = z.cols; rows = z.rows; }   // explicit wins…
    else {
      // …otherwise a near-square default that comfortably holds the qubit count,
      // recomputed each call (NOT cached) so it grows with the qubit count.
      var n = ((spec() && spec().qubits) || []).length || 1;
      var side = Math.max(3, Math.ceil(Math.sqrt(n)) + 1);
      cols = side; rows = side;
    }
    // Never smaller than the placed bounding box — keeps every stone on-board.
    var ext = placedExtent();
    return { cols: Math.max(cols, ext.cols), rows: Math.max(rows, ext.rows) };
  }
  function setZone(cols, rows) {
    var s = S(); if (!s) return;
    s.topoZone = { cols: Math.max(1, cols | 0), rows: Math.max(1, rows | 0) };
  }

  // -- pairs (spec form [[control,target]]) -----------------------------------

  function pairIndex(a, b) {
    var pairs = (spec() && spec().qubit_pairs) || [];
    for (var i = 0; i < pairs.length; i++) {
      var p = pairs[i];
      if ((p[0] === a && p[1] === b) || (p[0] === b && p[1] === a)) return i;
    }
    return -1;
  }
  function toggleEdge(a, b) {
    if (a === b) return;
    var sp = spec(); if (!sp) return;
    sp.qubit_pairs = sp.qubit_pairs || [];
    var i = pairIndex(a, b);
    if (i >= 0) sp.qubit_pairs.splice(i, 1);
    else sp.qubit_pairs.push([a, b]);
    var s = S(); if (s) s.pairsTouched = true;
  }

  function qubits() { return (spec() && spec().qubits) || []; }
  function occupant(col, row) {
    var qs = qubits();
    for (var i = 0; i < qs.length; i++) {
      var c = cellOf(qs[i]);
      if (c && c.col === col && c.row === row) return qs[i];
    }
    return null;
  }
  function nextUnplaced() {
    var qs = qubits();
    for (var i = 0; i < qs.length; i++) if (!cellOf(qs[i])) return qs[i];
    return null;
  }

  // -- pixel geometry (row 0 at the BOTTOM) -----------------------------------

  function centerPx(col, row, z) {
    z = z || zone();          // accept a hoisted zone so render() computes it once
    return { x: (col + 0.5) * CELL, y: (z.rows - 1 - row + 0.5) * CELL };
  }

  // -- render (single innerHTML; per-cell/stone/edge nodes) -------------------

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function render() {
    var host = root(); if (!host) return;
    // Fault-proof empty state: with no qubits the grid is inert (clicks
    // place nothing) — say so instead of showing a dead 3×3 board.
    if (!qubits().length) {
      host.innerHTML = '<div class="gen-topo-empty">Set the qubit count to ' +
        'start placing.</div>';
      renderCaption();
      return;
    }
    var z = zone();
    var W = z.cols * CELL, H = z.rows * CELL;
    var html = '<div class="gen-topo-grid" style="width:' + W + 'px;height:' + H + 'px">';

    // cells (top visual row = data row rows-1; bottom = data row 0)
    for (var vr = 0; vr < z.rows; vr++) {
      var dataRow = z.rows - 1 - vr;
      for (var col = 0; col < z.cols; col++) {
        html += '<div class="gen-topo-cell" data-col="' + col + '" data-row="' + dataRow +
                '" style="left:' + (col * CELL) + 'px;top:' + (vr * CELL) + 'px;' +
                'width:' + CELL + 'px;height:' + CELL + 'px"></div>';
      }
    }

    // edges (SVG overlay) — styled by the chip type's 2-qubit gate so the board
    // shows the line bundle each pair will consume: CR = directed (control→target),
    // CZ fixed-coupler = dashed (qubit-flux only), CZ tunable = solid + coupler dot.
    var es = edgeStyle();
    var pairs = (spec() && spec().qubit_pairs) || [];
    var lines = "";
    for (var i = 0; i < pairs.length; i++) {
      var a = pairs[i][0], b = pairs[i][1];
      var ca = cellOf(a), cb = cellOf(b);
      if (!ca || !cb) continue;
      var pa = centerPx(ca.col, ca.row, z), pb = centerPx(cb.col, cb.row, z);
      var pid = a + "-" + b;
      var vcls = "gen-topo-edge" + (es === "dashed" ? " gen-topo-edge--dashed" : "");
      lines += '<line class="gen-topo-edge-hit" data-edge="' + esc(pid) +
               '" x1="' + pa.x + '" y1="' + pa.y + '" x2="' + pb.x + '" y2="' + pb.y + '"/>';
      lines += '<line class="' + vcls + '" x1="' + pa.x + '" y1="' + pa.y +
               '" x2="' + pb.x + '" y2="' + pb.y + '"/>';
      if (es === "directed") {
        // arrowhead at the target end (b), pointing pa->pb, seated at the stone edge.
        var dx = pb.x - pa.x, dy = pb.y - pa.y, L = Math.sqrt(dx * dx + dy * dy) || 1;
        var ux = dx / L, uy = dy / L;
        var tx = pb.x - ux * STONE_R, ty = pb.y - uy * STONE_R;   // tip at stone rim
        var bx = tx - ux * 9, by = ty - uy * 9;                   // base, 9px back
        var nx = -uy, ny = ux;                                    // perpendicular
        var pts = (bx + nx * 4) + "," + (by + ny * 4) + " " +
                  (bx - nx * 4) + "," + (by - ny * 4) + " " + tx + "," + ty;
        lines += '<polygon class="gen-topo-arrow" data-edge="' + esc(pid) + '" points="' + pts + '"/>';
      } else if (es === "coupler") {
        lines += '<circle class="gen-topo-coupler" data-edge="' + esc(pid) +
                 '" cx="' + ((pa.x + pb.x) / 2) + '" cy="' + ((pa.y + pb.y) / 2) + '" r="5"/>';
      }
    }
    html += '<svg class="gen-topo-edges" width="' + W + '" height="' + H + '">' + lines + '</svg>';

    // stones (placed qubits)
    var qs = qubits();
    for (var k = 0; k < qs.length; k++) {
      var c2 = cellOf(qs[k]); if (!c2) continue;
      var p = centerPx(c2.col, c2.row, z);
      var cls = "gen-topo-stone";
      if (qs[k] === _sel) cls += " selected";
      if (qs[k] === _armed) cls += " armed";
      html += '<div class="' + cls + '" data-qubit="' + esc(qs[k]) +
              '" style="left:' + (p.x - STONE_R) + 'px;top:' + (p.y - STONE_R) + 'px;' +
              'width:' + (STONE_R * 2) + 'px;height:' + (STONE_R * 2) + 'px">' + esc(qs[k]) + '</div>';
    }
    html += '</div>';
    host.innerHTML = html;
    renderCaption();
  }

  // Edge rendering style + legend derived from the chip type's 2-qubit gate. The
  // gate->style/legend mapping lives ONCE in TopoGraph so the editable board and
  // the read-only Populate view can't drift; we only supply state.pairGate. (Tiny
  // local fallback keeps the board working if TopoGraph somehow isn't loaded.)
  function curGate() { var s = S(); return s && s.pairGate; }
  function edgeStyle() {
    if (window.TopoGraph) return window.TopoGraph.edgeStyleForGate(curGate());
    var g = curGate();
    return g === "cr" ? "directed" : g === "cz_fixed" ? "dashed" :
           g === "cz_tunable" ? "coupler" : "plain";
  }

  function renderCaption() {
    var cap = document.getElementById("gen-topo-caption");
    var placed = 0, qs = qubits();
    for (var i = 0; i < qs.length; i++) if (cellOf(qs[i])) placed++;
    var npairs = ((spec() && spec().qubit_pairs) || []).length;
    if (cap) {
      cap.textContent = placed + "/" + qs.length + " qubits placed · " + npairs + " pairs";
      // Pre-announce the step-leave gate: partial placement (some but not all)
      // is exactly what the Next-check rejects — tint the progress chip now.
      cap.classList.toggle("gen-topo-caption-warn", placed > 0 && placed < qs.length);
    }
    var leg = document.getElementById("gen-topo-legend");
    if (leg) leg.textContent = window.TopoGraph ? window.TopoGraph.legendForGate(curGate()) : "";
    renderStatus();
  }

  // Strong, unmistakable feedback for the armed (mid-pair) vs selected (delete/move
  // candidate) states — the two faint CSS rings alone aren't enough to tell which
  // mode a click put you in. Empty when neither is active.
  function renderStatus() {
    var el = document.getElementById("gen-topo-status");
    if (!el) return;
    if (_armed) {
      el.textContent = _armed + " armed — click another qubit to pair, or Esc to cancel.";
      el.className = "gen-topo-status gen-topo-status--armed";
    } else if (_sel) {
      el.textContent = _sel + " selected — Delete to remove, or drag to move.";
      el.className = "gen-topo-status gen-topo-status--sel";
    } else {
      el.textContent = "";
      el.className = "gen-topo-status";
    }
  }

  // -- interaction (one delegated handler) ------------------------------------

  function cellFromEvent(e) {
    var el = e.target.closest && e.target.closest(".gen-topo-cell");
    if (el) return { col: +el.dataset.col, row: +el.dataset.row };
    // a stone overlaps its cell — derive from the stone's qubit
    var st = e.target.closest && e.target.closest(".gen-topo-stone");
    if (st) return cellOf(st.dataset.qubit);
    return null;
  }

  function onDown(e) {
    var stone = e.target.closest && e.target.closest(".gen-topo-stone");
    if (stone) {
      _drag = { qid: stone.dataset.qubit, startX: e.clientX, startY: e.clientY, moved: false };
      e.preventDefault();
    }
  }
  function onMove(e) {
    if (!_drag) return;
    if (!_drag.moved &&
        (Math.abs(e.clientX - _drag.startX) > DRAG_THRESHOLD ||
         Math.abs(e.clientY - _drag.startY) > DRAG_THRESHOLD)) {
      _drag.moved = true;
    }
  }
  function onUp(e) {
    var drag = _drag; _drag = null;
    if (drag && drag.moved) {
      var cell = cellFromEvent(e);
      if (cell && !occupant(cell.col, cell.row)) { setCell(drag.qid, cell.col, cell.row); commit("move"); }
      return;
    }
    // a click (no drag)
    var stone = e.target.closest && e.target.closest(".gen-topo-stone");
    var edge = e.target.closest && e.target.closest(".gen-topo-edge-hit");
    if (edge) {
      // qubit ids contain no "-", so the pair "a-b" rebuilds uniquely from the list.
      var pairs = (spec() && spec().qubit_pairs) || [];
      var idx = -1;
      for (var i = 0; i < pairs.length; i++) {
        if (pairs[i][0] + "-" + pairs[i][1] === edge.dataset.edge) { idx = i; break; }
      }
      if (idx >= 0) { pairs.splice(idx, 1); var s = S(); if (s) s.pairsTouched = true; commit("edge"); }
      return;
    }
    if (stone) {
      var qid = stone.dataset.qubit;
      if (_armed && _armed !== qid) { toggleEdge(_armed, qid); _armed = null; _sel = null; commit("edge"); }
      else if (_armed === qid) { _armed = null; _sel = null; render(); }
      else { _armed = qid; _sel = qid; render(); }
      return;
    }
    // empty cell
    var c = cellFromEvent(e);
    if (c && !occupant(c.col, c.row)) {
      if (_sel) { setCell(_sel, c.col, c.row); _sel = null; _armed = null; }
      else { var nx = nextUnplaced(); if (nx) setCell(nx, c.col, c.row); }
      commit("place");
    } else { _armed = null; _sel = null; render(); }
  }

  // Remove a qubit entirely (creates an id gap; the step-4 gate enforces a
  // renumber before leaving). Drops its placement, incident edges, and populate.
  function removeQubit(qid) {
    var sp = spec(); if (!sp) return;
    var i = (sp.qubits || []).indexOf(qid);
    if (i < 0) return;
    sp.qubits.splice(i, 1);
    sp.qubit_pairs = (sp.qubit_pairs || []).filter(function (p) { return p[0] !== qid && p[1] !== qid; });
    var pop = sp.populate || {};
    ["qubit", "resonator", "flux", "pulses"].forEach(function (g) { if (pop[g]) delete pop[g][qid]; });
    // Drop any per-pair populate whose key references this qubit (spec-form
    // "qC-qT") so a deleted qubit leaves no orphaned pair physics behind.
    if (pop.pairs) {
      Object.keys(pop.pairs).forEach(function (key) {
        var seg = key.split("-");
        if (seg.indexOf(qid) !== -1) delete pop.pairs[key];
      });
    }
    var s = S(); if (s) s.pairsTouched = true;
    _sel = null; _armed = null;
    commit("delete");
  }

  var _bound = false;
  function bind() {
    var host = root(); if (!host || _bound) return;
    _bound = true;
    host.addEventListener("mousedown", onDown);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("keydown", function (e) {
      // Only act when the board is the focus context (a stone is selected) and the
      // user isn't typing in a field — so Backspace in an input is never hijacked.
      var ae = document.activeElement;
      var typing = ae && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName);
      if (e.key === "Escape" && (_armed || _sel)) { _armed = null; _sel = null; render(); return; }
      if ((e.key === "Delete" || e.key === "Backspace") && _sel && !typing) {
        e.preventDefault(); removeQubit(_sel);
      }
    });
  }

  // -- architecture presets ---------------------------------------------------
  // Each lays out ALL qubits (grid_location) + sets the topology edges
  // (qubit_pairs). Placement is kept simple/valid (unique cells, in-zone); the
  // EDGE topology is the point (chain=line, ring=cycle, star=hub, grid=4-NN).

  function layoutChain(qs) {        // snake fill + consecutive edges
    var cols = Math.max(1, zone().cols), pos = {}, edges = [];
    qs.forEach(function (q, i) {
      var r = Math.floor(i / cols), inRow = i % cols;
      pos[q] = { col: (r % 2 === 0) ? inRow : (cols - 1 - inRow), row: r };
      if (i > 0) edges.push([qs[i - 1], q]);
    });
    return { pos: pos, edges: edges };
  }

  function layoutGrid(qs) {         // row-major fill + 4-adjacency edges
    var cols = Math.max(1, zone().cols), pos = {}, byCell = {};
    qs.forEach(function (q, i) { var c = i % cols, r = Math.floor(i / cols); pos[q] = { col: c, row: r }; byCell[c + ',' + r] = q; });
    var edges = [];
    qs.forEach(function (q) {
      var c = pos[q].col, r = pos[q].row;
      var right = byCell[(c + 1) + ',' + r], up = byCell[c + ',' + (r + 1)];
      if (right) edges.push([q, right]);
      if (up) edges.push([q, up]);
    });
    return { pos: pos, edges: edges };
  }

  function squarePerimeter(s) {     // cells around an s×s square, clockwise from (0,0)
    if (s < 2) return [{ col: 0, row: 0 }];
    var path = [], c, r;
    for (c = 0; c < s; c++) path.push({ col: c, row: s - 1 });          // top edge L→R
    for (r = s - 2; r >= 0; r--) path.push({ col: s - 1, row: r });     // right edge top→bottom
    for (c = s - 2; c >= 0; c--) path.push({ col: c, row: 0 });         // bottom edge R→L
    for (r = 1; r < s - 1; r++) path.push({ col: 0, row: r });          // left edge bottom→top
    return path;
  }

  function layoutRing(qs) {         // square-perimeter placement + cyclic edges
    var n = qs.length;
    var s = 2; while (4 * (s - 1) < n) s++;        // s×s perimeter holds 4(s-1)
    var perim = squarePerimeter(s), pos = {}, edges = [];
    qs.forEach(function (q, i) { pos[q] = perim[Math.round(i * perim.length / n) % perim.length]; });
    qs.forEach(function (q, i) { if (n > 1) edges.push([q, qs[(i + 1) % n]]); });
    if (n === 2) edges = [[qs[0], qs[1]]];
    return { pos: pos, edges: edges };
  }

  function layoutStar(qs) {         // centre + spokes
    var center = qs[0], arms = qs.slice(1);
    var z = zone(), cc = Math.floor((z.cols - 1) / 2), cr = Math.floor((z.rows - 1) / 2);
    var pos = {}; pos[center] = { col: cc, row: cr };
    var edges = [];
    // ring offsets at growing radius (8-neighbour first), excluding the centre.
    var offs = [], radius = 1;
    while (offs.length < arms.length) {
      for (var dc = -radius; dc <= radius; dc++) for (var dr = -radius; dr <= radius; dr++) {
        if (Math.max(Math.abs(dc), Math.abs(dr)) === radius) offs.push({ dc: dc, dr: dr });
      }
      radius++;
    }
    arms.forEach(function (q, i) { pos[q] = { col: cc + offs[i].dc, row: cr + offs[i].dr }; edges.push([center, q]); });
    return { pos: pos, edges: edges };
  }

  // Normalize a layout so the minimum cell is (0,0) (no negative cells from star
  // offsets), grow the zone to fit, write grid_location + qubit_pairs, commit.
  function applyLayout(pos, edges) {
    var minC = Infinity, minR = Infinity;
    var qs = qubits();
    qs.forEach(function (q) { if (pos[q]) { if (pos[q].col < minC) minC = pos[q].col; if (pos[q].row < minR) minR = pos[q].row; } });
    if (!isFinite(minC)) return;
    var maxC = 0, maxR = 0;
    qs.forEach(function (q) { if (pos[q]) { pos[q] = { col: pos[q].col - minC, row: pos[q].row - minR }; if (pos[q].col > maxC) maxC = pos[q].col; if (pos[q].row > maxR) maxR = pos[q].row; } });
    var z = zone();
    if (maxC + 1 > z.cols || maxR + 1 > z.rows) setZone(Math.max(z.cols, maxC + 1), Math.max(z.rows, maxR + 1));
    qs.forEach(function (q) { if (pos[q]) setCell(q, pos[q].col, pos[q].row); });
    var sp = spec(); if (sp) sp.qubit_pairs = edges.slice();
    var s = S(); if (s) s.pairsTouched = true;
    _armed = null; _sel = null;
    commit("preset");
  }

  function preset(name) {
    var qs = qubits().slice();
    if (!qs.length) return;
    var L = name === 'chain' ? layoutChain(qs)
          : name === 'grid' ? layoutGrid(qs)
          : name === 'ring' ? layoutRing(qs)
          : name === 'star' ? layoutStar(qs)
          : null;
    if (!L) return;
    // A preset REPLACES the whole layout + pair list. Confirm first if that would
    // discard hand-done work (any placed stone, or hand-edited pairs) so one stray
    // click can't wipe a carefully drawn chip. A pristine board (auto-chain pairs,
    // nothing placed) applies without a prompt — presets are the fast way to start.
    var s = S();
    var dirty = placedExtent().cols > 0 ||
                !!(s && s.pairsTouched && ((spec() && spec().qubit_pairs) || []).length);
    if (dirty && typeof window !== "undefined" && window.confirm &&
        !window.confirm("Replace the current layout and pairs with the " + name + " preset?")) {
      return;
    }
    applyLayout(L.pos, L.edges);
  }

  // Public: (re)render the board from state (called on step entry / panel open).
  function refresh() { bind(); render(); }
  function setOnChange(fn) { _onChange = fn; }

  return {
    refresh: refresh,
    setOnChange: setOnChange,
    setZone: setZone,
    zone: zone,
    preset: preset,
    render: render,
    // exposed for tests
    _cellOf: cellOf, _toggleEdge: toggleEdge, _nextUnplaced: nextUnplaced,
    _occupant: occupant, _removeQubit: removeQubit,
  };
})();
