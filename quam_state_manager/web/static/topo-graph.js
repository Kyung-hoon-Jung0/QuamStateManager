/* Shared chip-topology convention + render helpers (window.TopoGraph).
 *
 * The SINGLE source of the grid/topology conventions so every surface — the
 * Generate-Config topology board (editable), the Populate step (read-only), and
 * the Chip Status / Topology menu (read-only) — agrees on qubit placement and
 * pair identity. The board NEVER produces port allocation; it only writes
 * grid_location + qubit_pairs, which the existing /generate/allocate consumes.
 *
 * This module owns the pure conventions (no DOM): the grid normalize/row-flip,
 * the doubled-coordinate pair midpoint, and the spec->QUAM pair-id transform.
 * House convention: framework-free IIFE exposing window.TopoGraph (like app.js /
 * generate.js / pulses.js).
 */
window.TopoGraph = (function () {
  "use strict";

  // --- pair id ---------------------------------------------------------------

  // Strip a leading q/Q and return the bare index part (mirrors run_build
  // _norm_index for the string form): "q1" -> "1", "qA1" -> "A1", "1" -> "1".
  function bareIndex(qid) {
    var s = String(qid);
    if (s.charAt(0) === "q" || s.charAt(0) === "Q") s = s.slice(1);
    return s;
  }

  // JS port of run_build._quam_pair_id: a spec pair (the "q1-q2" string OR a
  // [control, target] array) -> the QUAM qubit_pairs KEY "q1-2" (control keeps
  // its q + index; the target keeps ONLY its bare index). Splits on the FIRST
  // "-" so multi-character qubit labels survive. This is the transform a preview
  // MUST apply to match get_topology()'s post-build pair_id — a naive hyphen-join
  // ("q1-q2") is WRONG.
  function quamPairId(specPair) {
    var control, target;
    if (Object.prototype.toString.call(specPair) === "[object Array]") {
      control = specPair[0]; target = specPair[1];
    } else {
      var s = String(specPair);
      var i = s.indexOf("-");
      if (i < 0) return s;                 // not a pair id — return as-is
      control = s.slice(0, i);
      target = s.slice(i + 1);
    }
    return "q" + bareIndex(control) + "-" + bareIndex(target);
  }

  // --- grid normalize --------------------------------------------------------

  // Parse each node's grid_location "col,row" and normalize to 0-based col + a
  // ROW-FLIPPED row (QUAM convention: row 0 = chip bottom; screen y grows down,
  // so flip once here — the single place the flip lives). Faithfully reproduces
  // BOTH chip-status paths:
  //   mode 'tolerant' (the property-card path, chip-status.js:520-527): parseFloat,
  //     and a node with no/invalid grid_location falls back to (i%4, floor(i/4));
  //     always returns placed:true (real chips return "" grid_location freely).
  //   mode 'strict' (the heatmap path, chip-status.js:1073-1099): parseInt, and
  //     placed:false (no positions) UNLESS every node has a valid grid_location.
  // Both yield positions[id] = {col: col-minCol, row: maxRow-row} — identical math,
  // verified against the two inline blocks.
  function normalizeGrid(nodes, opts) {
    opts = opts || {};
    var strict = opts.mode === "strict";
    var gridKey = opts.gridKey || "grid_location";
    var idKey = opts.idKey || "id";
    var raw = [], validCount = 0;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var parts = String(n[gridKey] == null ? "" : n[gridKey]).split(",");
      var c, r;
      if (strict) {
        // heatmap path: parseInt + isNaN guard; an invalid node fails the gate.
        var valid = false;
        if (parts.length === 2) {
          var pc = parseInt(parts[0], 10), pr = parseInt(parts[1], 10);
          if (!isNaN(pc) && !isNaN(pr)) { c = pc; r = pr; valid = true; validCount++; }
        }
        if (!valid) { c = NaN; r = NaN; }
      } else {
        // card path: parseFloat (may be NaN), fall back to (i%4, floor(i/4)) ONLY
        // when grid_location isn't exactly two comma-separated parts.
        if (parts.length === 2) { c = parseFloat(parts[0]); r = parseFloat(parts[1]); }
        else { c = i % 4; r = Math.floor(i / 4); }
      }
      raw.push({ id: n[idKey], col: c, row: r });
    }

    if (strict && validCount !== nodes.length) {
      return { positions: {}, cols: 0, rows: 0, placed: false };
    }

    var minCol = Infinity, maxCol = -Infinity, minRow = Infinity, maxRow = -Infinity;
    for (var j = 0; j < raw.length; j++) {
      var p = raw[j];
      if (p.col < minCol) minCol = p.col;
      if (p.col > maxCol) maxCol = p.col;
      if (p.row < minRow) minRow = p.row;
      if (p.row > maxRow) maxRow = p.row;
    }

    var positions = {};
    for (var k = 0; k < raw.length; k++) {
      positions[raw[k].id] = { col: raw[k].col - minCol, row: maxRow - raw[k].row };
    }
    return {
      positions: positions,
      cols: (maxCol - minCol) + 1,
      rows: (maxRow - minRow) + 1,
      placed: true,
    };
  }

  // Doubled-coordinate pair midpoint (chip-status.js:1165-1176): with 0-based,
  // row-flipped integer node positions, source.col + target.col is the doubled
  // coordinate directly. `edges` = [{source, target, pair_id}], `gridPositions`
  // = the normalizeGrid(...).positions map.
  function pairGridPositions(edges, gridPositions) {
    var out = {}, cols = 0, rows = 0;
    for (var i = 0; i < edges.length; i++) {
      var e = edges[i];
      var sp = gridPositions[e.source], tp = gridPositions[e.target];
      if (!sp || !tp) continue;
      var mc = sp.col + tp.col, mr = sp.row + tp.row;
      out[e.pair_id] = { col: mc, row: mr };
      if (mc + 1 > cols) cols = mc + 1;
      if (mr + 1 > rows) rows = mr + 1;
    }
    return { positions: out, cols: cols, rows: rows, has: Object.keys(out).length > 0 };
  }

  // --- gate -> edge style + legend (the SHARED convention) -------------------
  // One source for how a chip type's 2-qubit gate is drawn, used by BOTH the
  // editable board (wiring-grid.js) and the read-only Populate view (renderStatic)
  // so they can never drift apart.
  function edgeStyleForGate(gate) {
    if (gate === "cr") return "directed";
    if (gate === "cz_fixed") return "dashed";
    if (gate === "cz_tunable") return "coupler";
    return "plain";
  }

  function legendForGate(gate) {
    var es = edgeStyleForGate(gate);
    if (es === "directed") {
      return "Cross-resonance — each qubit: readout + xy drive; each pair adds a " +
             "cross_resonance tone on the control (arrow → target).";
    }
    if (es === "dashed") {
      return "CZ, fixed coupler — each qubit: readout + xy + z flux; pairs (dashed) " +
             "play on qubit flux, no dedicated coupler line.";
    }
    if (es === "coupler") {
      return "CZ, tunable coupler — each qubit: readout + xy + z flux; each pair " +
             "adds a coupler flux line (●).";
    }
    return "";
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // Read-only, all-SVG render of the placed topology — same bottom-up convention
  // and gate edge-styling as the editable board, so the Populate view mirrors what
  // was drawn in step 4. Draws ONLY placed qubits (a grid_location parseable as
  // "col,row"); unplaced ones are omitted (no i%4 fallback — this is a faithful
  // mirror, not a heuristic layout).
  //   opts: { qubits:[{id, grid_location, cls?}], pairs:[[control,target],...],
  //           gate, cell, stoneR, layout }
  //   layout:"auto" — qubits whose grid_location is absent/unparseable get a
  //   tolerant (i%4, floor(i/4)) fallback instead of being omitted (compare-hub
  //   structure strip: real chips may carry no grid at all — docs/49). The
  //   default stays the faithful mirror (omit unplaced).
  //   Per-node class hook: qubits[i].cls is appended to the stone's class
  //   (escaped) — the hub tints bucket-② stones whose mapped counterpart
  //   differs.
  function renderStatic(mount, opts) {
    if (!mount) return;
    opts = opts || {};
    var cell = opts.cell || 36, R = opts.stoneR || 13;
    var qubits = opts.qubits || [], pairs = opts.pairs || [];
    var es = edgeStyleForGate(opts.gate);
    var auto = opts.layout === "auto";

    var pos = {}, maxc = 0, maxr = 0, any = false;
    var fallback = [];
    for (var i = 0; i < qubits.length; i++) {
      var gl = qubits[i].grid_location;
      var parts = gl == null ? [] : String(gl).split(",");
      var c = parts.length === 2 ? parseInt(parts[0], 10) : NaN;
      var r = parts.length === 2 ? parseInt(parts[1], 10) : NaN;
      if (isNaN(c) || isNaN(r)) {
        if (auto) fallback.push(qubits[i].id);
        continue;
      }
      pos[qubits[i].id] = { col: c, row: r }; any = true;
      if (c > maxc) maxc = c;
      if (r > maxr) maxr = r;
    }
    if (auto && fallback.length) {
      // Tolerant fallback rows on grid rows ABOVE maxr (the bottom-up row
      // flip in cy() renders them at the TOP of the SVG, visually separate
      // from the placed grid) — mirrors normalizeGrid's tolerant (i%4, i/4).
      var baseRow = any ? maxr + 1 : 0;
      for (var f = 0; f < fallback.length; f++) {
        var fc = f % 4, fr = baseRow + Math.floor(f / 4);
        pos[fallback[f]] = { col: fc, row: fr }; any = true;
        if (fc > maxc) maxc = fc;
        if (fr > maxr) maxr = fr;
      }
    }
    if (!any) {
      mount.innerHTML = '<p class="muted" style="margin:0">No qubits placed on the board yet.</p>';
      return;
    }
    var cols = maxc + 1, rows = maxr + 1;
    var W = cols * cell, H = rows * cell;
    function cx(col) { return (col + 0.5) * cell; }
    function cy(row) { return (rows - 1 - row + 0.5) * cell; }

    var svg = "";
    for (var p = 0; p < pairs.length; p++) {
      var a = pos[pairs[p][0]], b = pos[pairs[p][1]];
      if (!a || !b) continue;
      var ax = cx(a.col), ay = cy(a.row), bx = cx(b.col), by = cy(b.row);
      svg += '<line class="gen-topo-edge' + (es === "dashed" ? " gen-topo-edge--dashed" : "") +
             '" x1="' + ax + '" y1="' + ay + '" x2="' + bx + '" y2="' + by + '"/>';
      if (es === "directed") {
        var dx = bx - ax, dy = by - ay, L = Math.sqrt(dx * dx + dy * dy) || 1;
        var ux = dx / L, uy = dy / L;
        var tx = bx - ux * R, ty = by - uy * R;
        var sx = tx - ux * 9, sy = ty - uy * 9, nx = -uy, ny = ux;
        svg += '<polygon class="gen-topo-arrow" points="' +
               (sx + nx * 4) + "," + (sy + ny * 4) + " " +
               (sx - nx * 4) + "," + (sy - ny * 4) + " " + tx + "," + ty + '"/>';
      } else if (es === "coupler") {
        svg += '<circle class="gen-topo-coupler" cx="' + ((ax + bx) / 2) +
               '" cy="' + ((ay + by) / 2) + '" r="5"/>';
      }
    }
    for (var q = 0; q < qubits.length; q++) {
      var pp = pos[qubits[q].id]; if (!pp) continue;
      var x = cx(pp.col), y = cy(pp.row);
      var extraCls = qubits[q].cls ? " " + esc(String(qubits[q].cls)) : "";
      svg += '<circle class="gen-topo-stone-ro' + extraCls + '" cx="' + x + '" cy="' + y + '" r="' + R + '"/>';
      svg += '<text class="gen-topo-stone-label" x="' + x + '" y="' + y +
             '" text-anchor="middle" dominant-baseline="central">' + esc(qubits[q].id) + '</text>';
    }
    mount.innerHTML = '<svg class="gen-topo-edges gen-topo-static" width="' + W +
      '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">' + svg + '</svg>';
  }

  return {
    bareIndex: bareIndex,
    quamPairId: quamPairId,
    normalizeGrid: normalizeGrid,
    pairGridPositions: pairGridPositions,
    edgeStyleForGate: edgeStyleForGate,
    legendForGate: legendForGate,
    renderStatic: renderStatic,
  };
})();
