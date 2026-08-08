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

  // --- honest layout selector (docs/91 §2.1) ---------------------------------

  // The label every logical-mode render MUST show on the map itself (not a
  // tooltip, not a legend): a connectivity layout that reads as physical
  // asserts neighbours that do not exist — worse than showing no map.
  var LOGICAL_LAYOUT_NOTE =
    "Logical layout — this chip declares no usable physical positions";

  // Decide HOW a chip may be drawn as a map. Three states, never two:
  //   'physical' — every node passes the strict grid gate (normalizeGrid
  //                strict); positions are the chip's own, row-flipped, 0-based.
  //   'logical'  — positions are DERIVED from pair connectivity ALONE (partial
  //                grid_locations are ignored entirely — a half-fabricated map
  //                is still a fabricated map). Caller shows LOGICAL_LAYOUT_NOTE.
  //   'none'     — no complete positions and no usable pairs: draw no map; the
  //                honest one-line message is the caller's job.
  // The tolerant (i%4, i/4) raster stays display-only decoration and is NEVER
  // promoted to a map mode here. Pure, no DOM, deterministic (no Math.random,
  // fixed iteration counts): same input -> byte-identical output.
  // positions[id] = {col, row} in cell units (floats in logical mode); screen
  // convention (row grows downward) in BOTH modes, so renderers just scale.
  function layoutFor(nodes, edges, opts) {
    opts = opts || {};
    nodes = nodes || [];
    var idKey = opts.idKey || "id";
    var NONE = { mode: "none", positions: {}, cols: 0, rows: 0 };
    if (!nodes.length) return NONE;

    var strict = normalizeGrid(nodes, { mode: "strict", idKey: idKey, gridKey: opts.gridKey });
    if (strict.placed) {
      return { mode: "physical", positions: strict.positions, cols: strict.cols, rows: strict.rows };
    }

    function has(obj, k) { return Object.prototype.hasOwnProperty.call(obj, k); }

    // Known node ids, input order, deduped.
    var ids = [], idx = {};
    for (var i = 0; i < nodes.length; i++) {
      var id = nodes[i][idKey];
      if (id == null) continue;
      id = String(id);
      if (has(idx, id)) continue;
      idx[id] = ids.length; ids.push(id);
    }
    if (!ids.length) return NONE;
    var n = ids.length;

    // Undirected deduped adjacency from edges among KNOWN ids. Accepts BOTH
    // get_topology objects ({source, target}) and spec pair arrays ([a, b]);
    // drops self-loops and the CR anti-parallel duplicates.
    var adj = [], seen = {};
    var list = edges || [];
    for (var e = 0; e < list.length; e++) {
      var ed = list[e]; if (ed == null) continue;
      var a, b;
      if (Object.prototype.toString.call(ed) === "[object Array]") { a = ed[0]; b = ed[1]; }
      else { a = ed.source; b = ed.target; }
      if (a == null || b == null) continue;
      a = String(a); b = String(b);
      if (!has(idx, a) || !has(idx, b)) continue;
      var ai = idx[a], bi = idx[b];
      if (ai === bi) continue;
      var ek = ai < bi ? ai + ":" + bi : bi + ":" + ai;
      if (seen[ek]) continue;
      seen[ek] = true;
      adj.push([ai, bi]);
    }
    if (!adj.length) return NONE;

    // Connected components, input-order stable; degree-0 nodes -> the strip.
    var nbr = new Array(n);
    for (var n0 = 0; n0 < n; n0++) nbr[n0] = [];
    for (var e0 = 0; e0 < adj.length; e0++) {
      nbr[adj[e0][0]].push(adj[e0][1]);
      nbr[adj[e0][1]].push(adj[e0][0]);
    }
    var comp = new Array(n);
    for (var c0 = 0; c0 < n; c0++) comp[c0] = -1;
    var comps = [];
    for (var s = 0; s < n; s++) {
      if (comp[s] !== -1 || !nbr[s].length) continue;
      var queue = [s], members = [];
      comp[s] = comps.length;
      while (queue.length) {
        var u = queue.shift();
        members.push(u);
        for (var w = 0; w < nbr[u].length; w++) {
          var v = nbr[u][w];
          if (comp[v] === -1) { comp[v] = comps.length; queue.push(v); }
        }
      }
      comps.push(members);
    }
    var isolated = [];
    for (var s2 = 0; s2 < n; s2++) if (comp[s2] === -1) isolated.push(s2);

    // Fruchterman-Reingold on one component, k = 1 cell. Deterministic circle
    // init by input order; linear cooling; then principal-axis align, mirror
    // normalize, median-edge rescale to 1 cell and a short overlap relax.
    function frLayout(mem) {
      var m = mem.length;
      var local = {}, le = [], li;
      for (li = 0; li < m; li++) local[mem[li]] = li;
      for (li = 0; li < adj.length; li++) {
        var la = adj[li][0], lb = adj[li][1];
        if (local[la] !== undefined && local[lb] !== undefined) le.push([local[la], local[lb]]);
      }
      var px = new Array(m), py = new Array(m);
      var R0 = Math.max(1, Math.sqrt(m));
      for (li = 0; li < m; li++) {
        var th = (2 * Math.PI * li) / m;
        px[li] = R0 * Math.cos(th);
        py[li] = R0 * Math.sin(th);
      }
      var ITER = 300, t0 = Math.max(0.5, Math.sqrt(m) * 0.4);
      for (var it = 0; it < ITER; it++) {
        var t = t0 * (1 - it / ITER) + 0.02;
        var dx = new Array(m), dy = new Array(m);
        for (li = 0; li < m; li++) { dx[li] = 0; dy[li] = 0; }
        for (var p = 0; p < m; p++) {
          for (var q = p + 1; q < m; q++) {
            var rx = px[p] - px[q], ry = py[p] - py[q];
            var d2 = rx * rx + ry * ry;
            var d = Math.sqrt(d2) || 1e-6;
            var fr = 1 / (d2 || 1e-9);              // k^2/d along the unit vector
            dx[p] += rx * fr; dy[p] += ry * fr;
            dx[q] -= rx * fr; dy[q] -= ry * fr;
          }
        }
        for (var ei = 0; ei < le.length; ei++) {
          var aa = le[ei][0], bb = le[ei][1];
          var ex = px[aa] - px[bb], ey = py[aa] - py[bb];
          var el = Math.sqrt(ex * ex + ey * ey) || 1e-6;
          // attraction d^2/k along the unit vector == delta * (d/k), k = 1
          dx[aa] -= ex * el; dy[aa] -= ey * el;
          dx[bb] += ex * el; dy[bb] += ey * el;
        }
        for (li = 0; li < m; li++) {
          var dl = Math.sqrt(dx[li] * dx[li] + dy[li] * dy[li]);
          if (dl > 1e-9) {
            var cap = Math.min(dl, t) / dl;
            px[li] += dx[li] * cap;
            py[li] += dy[li] * cap;
          }
        }
      }
      // principal-axis align (long axis horizontal)
      var cx = 0, cy = 0;
      for (li = 0; li < m; li++) { cx += px[li]; cy += py[li]; }
      cx /= m; cy /= m;
      var sxx = 0, sxy = 0, syy = 0;
      for (li = 0; li < m; li++) {
        var vx = px[li] - cx, vy = py[li] - cy;
        sxx += vx * vx; sxy += vx * vy; syy += vy * vy;
      }
      var ang = 0.5 * Math.atan2(2 * sxy, sxx - syy);
      var ca = Math.cos(-ang), sa = Math.sin(-ang);
      for (li = 0; li < m; li++) {
        var ox = px[li] - cx, oy = py[li] - cy;
        px[li] = ox * ca - oy * sa;
        py[li] = ox * sa + oy * ca;
      }
      // mirror normalize: first member (lowest input index) upper-left-ish
      if (m > 1) {
        if (px[0] > 1e-9) for (li = 0; li < m; li++) px[li] = -px[li];
        if (py[0] > 1e-9) for (li = 0; li < m; li++) py[li] = -py[li];
      }
      // rescale so the MEDIAN edge is exactly 1 cell
      var lens = [];
      for (li = 0; li < le.length; li++) {
        var mx = px[le[li][0]] - px[le[li][1]], my = py[le[li][0]] - py[le[li][1]];
        lens.push(Math.sqrt(mx * mx + my * my));
      }
      lens.sort(function (x, y) { return x - y; });
      var med = lens.length ? lens[(lens.length - 1) >> 1] : 1;
      if (med > 1e-9) {
        for (li = 0; li < m; li++) { px[li] /= med; py[li] /= med; }
      }
      // short overlap relax: push apart pairs closer than 0.55 cells
      for (var rp = 0; rp < 40; rp++) {
        var movedAny = false;
        for (var p2 = 0; p2 < m; p2++) {
          for (var q2 = p2 + 1; q2 < m; q2++) {
            var ox2 = px[p2] - px[q2], oy2 = py[p2] - py[q2];
            var od = Math.sqrt(ox2 * ox2 + oy2 * oy2);
            if (od >= 0.55) continue;
            movedAny = true;
            var puX, puY;
            if (od < 1e-9) {                      // coincident: deterministic direction by index
              var pa = (2 * Math.PI * p2) / m;
              puX = Math.cos(pa); puY = Math.sin(pa);
            } else { puX = ox2 / od; puY = oy2 / od; }
            var push = (0.55 - od) / 2;
            px[p2] += puX * push; py[p2] += puY * push;
            px[q2] -= puX * push; py[q2] -= puY * push;
          }
        }
        if (!movedAny) break;
      }
      return { px: px, py: py };
    }

    // Lay out each component, pack left-to-right with a 1-cell gap.
    var positions = {}, xOff = 0, globalMaxRow = 0;
    for (var ci = 0; ci < comps.length; ci++) {
      var laid = frLayout(comps[ci]);
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (var mi = 0; mi < comps[ci].length; mi++) {
        if (laid.px[mi] < minX) minX = laid.px[mi];
        if (laid.px[mi] > maxX) maxX = laid.px[mi];
        if (laid.py[mi] < minY) minY = laid.py[mi];
        if (laid.py[mi] > maxY) maxY = laid.py[mi];
      }
      for (var mi2 = 0; mi2 < comps[ci].length; mi2++) {
        var col = laid.px[mi2] - minX + xOff, row = laid.py[mi2] - minY;
        positions[ids[comps[ci][mi2]]] = {
          col: Math.round(col * 1e4) / 1e4,
          row: Math.round(row * 1e4) / 1e4,
        };
        if (row > globalMaxRow) globalMaxRow = row;
      }
      xOff += (maxX - minX) + 2;
    }
    var placedWidth = Math.max(1, xOff - 2);

    // Isolated nodes: a visually separate strip BELOW the connected part —
    // they have no connectivity information, so they get no implied neighbours.
    if (isolated.length) {
      var wrapN = Math.max(4, Math.ceil(placedWidth) + 1);
      var stripTop = globalMaxRow + 1.5;
      for (var ii = 0; ii < isolated.length; ii++) {
        positions[ids[isolated[ii]]] = {
          col: ii % wrapN,
          row: Math.round((stripTop + Math.floor(ii / wrapN)) * 1e4) / 1e4,
        };
      }
    }

    // Final 0-base + extents.
    var fMinC = Infinity, fMinR = Infinity, fMaxC = -Infinity, fMaxR = -Infinity;
    for (var fk in positions) {
      if (!has(positions, fk)) continue;
      var fp = positions[fk];
      if (fp.col < fMinC) fMinC = fp.col;
      if (fp.col > fMaxC) fMaxC = fp.col;
      if (fp.row < fMinR) fMinR = fp.row;
      if (fp.row > fMaxR) fMaxR = fp.row;
    }
    for (var fk2 in positions) {
      if (!has(positions, fk2)) continue;
      positions[fk2].col = Math.round((positions[fk2].col - fMinC) * 1e4) / 1e4;
      positions[fk2].row = Math.round((positions[fk2].row - fMinR) * 1e4) / 1e4;
    }
    return {
      mode: "logical",
      positions: positions,
      cols: Math.round((fMaxC - fMinC + 1) * 1e4) / 1e4,
      rows: Math.round((fMaxR - fMinR + 1) * 1e4) / 1e4,
    };
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
    var cell = opts.cell || 46, R = opts.stoneR || 17;   // r16 ⓪-5: scaled with the editable board
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
        var ah = R * 0.6, aw = R * 0.27;              // scale with the stones (r16 ⓪-5)
        var sx = tx - ux * ah, sy = ty - uy * ah, nx = -uy, ny = ux;
        svg += '<polygon class="gen-topo-arrow" points="' +
               (sx + nx * aw) + "," + (sy + ny * aw) + " " +
               (sx - nx * aw) + "," + (sy - ny * aw) + " " + tx + "," + ty + '"/>';
      } else if (es === "coupler") {
        svg += '<circle class="gen-topo-coupler" cx="' + ((ax + bx) / 2) +
               '" cy="' + ((ay + by) / 2) + '" r="' + Math.round(R / 3) + '"/>';
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
    layoutFor: layoutFor,
    LOGICAL_LAYOUT_NOTE: LOGICAL_LAYOUT_NOTE,
    edgeStyleForGate: edgeStyleForGate,
    legendForGate: legendForGate,
    renderStatic: renderStatic,
  };
})();
