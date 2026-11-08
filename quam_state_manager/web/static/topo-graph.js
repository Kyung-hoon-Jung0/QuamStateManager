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
  // D-101: one coupler-mark radius for the drawing and every clearance that assumes it.
  function couplerR(R) { return Math.max(7, Math.round(R * 0.42)); }
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

  // Coincident positions (real chips DECLARE them — a 10Q chip carries two
  // qubits at "4,0") -> fan the members of each shared cell around its centre
  // so every stone stays visible: a visual de-overlap at the same cell, never
  // a fabricated new cell. Returns {id: {dx, dy, shared:true}} in CELL units
  // (multiply by the renderer's cell px); ids not in a shared cell are absent.
  function spreadCoincident(positions, spread) {
    var byCell = {}, out = {};
    for (var id in positions) {
      if (!Object.prototype.hasOwnProperty.call(positions, id)) continue;
      var p = positions[id];
      var k = p.col + "|" + p.row;
      (byCell[k] = byCell[k] || []).push(id);
    }
    for (var k2 in byCell) {
      if (!Object.prototype.hasOwnProperty.call(byCell, k2)) continue;
      var ids = byCell[k2];
      if (ids.length < 2) continue;
      for (var i = 0; i < ids.length; i++) {
        var a = (2 * Math.PI * i) / ids.length;
        out[ids[i]] = { dx: Math.cos(a) * spread, dy: Math.sin(a) * spread, shared: true };
      }
    }
    return out;
  }

  // --- shared component-page layout renderer (docs/92 P2) --------------------
  // ONE no-numbers drawing of every component type — qubit stones, pair edges
  // (direction arrows, coupler dots), resonator marks, flux stubs, feedline
  // buses (nodes sharing one rr_port) — reused by every component page; the
  // page only chooses WHAT LIGHTS UP (opts.highlight; emphasis, never
  // content). Geometry + honesty mode come from layoutFor (§2.1): a logical
  // layout wears LOGICAL_LAYOUT_NOTE ON the map, a no-layout chip gets one
  // honest line. Numbers are deliberately absent (§2.4 — the table beside it
  // has every number).
  //   opts: { nodes, edges, highlight, cell, noneMessage }
  //     nodes: get_topology nodes ({id, grid_location, rr_port, z_port, ...})
  //     edges: get_topology edges ({pair_id, source, target, directed,
  //            has_coupler, active, ...})
  //     highlight: 'qubits'|'pairs'|'resonators'|'flux'|'couplers'|'' — the
  //            svg root gets class cm-hl-<highlight>; CSS owns the dimming.
  // Returns {mode, highlightEntity(kind, id, on)} — the map↔table hover hook
  // ('qubit'|'pair' + entity id -> .cm-hot on the matching [data-cm] group).
  // Shared pair-end pills and vocabulary for the component maps and hero.
  function pairLegendItems() {
    return [['C', 'control'], ['T', 'target'], ['M', 'moving qubit'],
            ['H', 'higher f01'], ['L', 'lower f01']].map(function (item) {
      return '<span class="cm-dir-legend-m"><b>' + item[0] + '</b> ' + item[1] + '</span>';
    }).join(' &middot; ');
  }

  // One pill per pair end, shared by every map. Moving roles come only from
  // the stored role; frequency order requires two finite, unequal numbers.
  function pairGlyphs(nodes, edges, opts) {
    opts = opts || {};
    var px = opts.px, CELL = opts.cell || 64, svg = '', fById = {};
    (nodes || []).forEach(function (n) {
      if (typeof n.f_01 === 'number' && isFinite(n.f_01)) fById[n.id] = n.f_01;
    });
    var done = {};
    (edges || []).forEach(function (e) {
      if (!opts.roles || !e || e.source == null || e.target == null || e.source === e.target) return;
      var key = e.pair_id != null ? String(e.pair_id)
        : [String(e.source), String(e.target)].sort().join('|');
      if (done[key]) return;
      done[key] = true;
      var a = px(e.source), b = px(e.target);
      if (!a || !b) return;
      var dx = b.x - a.x, dy = b.y - a.y, len = Math.sqrt(dx * dx + dy * dy) || 1;
      var ux = dx / len, uy = dy / len;
      // Component maps supply a readable scale; the hero keeps its compact cap.
      var fs = opts.roleFontSize || Math.min(6.5, Math.max(6, CELL * 0.105)), h = fs + 4;
      var radius = opts.stoneRadius != null ? opts.stoneRadius : Math.round(CELL * 0.30);
      var fS = fById[e.source], fT = fById[e.target];
      var ordered = fS != null && fT != null && fS !== fT;
      [['C', a, 1, e.source, 'control'], ['T', b, -1, e.target, 'target']].forEach(function (end) {
        var text = end[0] + (e.moving_qubit === end[4] ? ' M' : '');
        if (ordered) text += ' ' + ((end[0] === 'C' ? fS > fT : fT > fS) ? 'H' : 'L');
        var w = text.length * fs * 0.55 + 4;
        // Axis-aligned text stays readable on steep edges too. Its projection
        // along the pair clears the stone, leaving the middle line exposed.
        var reach = Math.abs(ux) * w / 2 + Math.abs(uy) * h / 2;
        var inset = radius + 2 + reach;
        var x = end[1].x + ux * end[2] * inset, y = end[1].y + uy * end[2] * inset;
        svg += '<g class="cm-role cm-role-' + end[0].toLowerCase() + '" data-cm-pair="' + esc(key)
          + '" data-cm-at="' + esc(end[3]) + '"><rect class="cm-role-dot" x="' + (x - w / 2)
          + '" y="' + (y - h / 2) + '" width="' + w + '" height="' + h + '" rx="' + h / 2 + '"/>'
          + '<text class="cm-role-txt" x="' + x + '" y="' + y + '" font-size="' + fs
          + '" text-anchor="middle" dominant-baseline="central">' + text + '</text></g>';
      });
    });
    return svg;
  }

  /* D-94: pair direction as an arrow, labels along the edge.
   *
   * Every pair edge carries ONE arrow head at the target end (control ->
   * target), so "qA2-qA1" and "qA1-qA2" read differently without a letter.
   * The moving qubit (a role string, never inferred from frequencies — see the
   * C/T/M note in pairGlyphs) is a small filled dot ON the line beside the
   * moving end. The pair id is written along the edge with a halo; where it
   * still meets a stone, a glyph or another label after the wider spacing,
   * declutterLabels() tries the other side of the line, then a smaller font
   * (never below DIR_MIN_FONT, D-6).
   */
  var DIR_SPACING = 1.5, DIR_MIN_FONT = 12;
  // D-4: the direction arrow is the map's subject: its head grows with the
  // 1.5x line stroke (ui-kit .cm-dir .cm-edge-line), the pair label shrinks.
  var DIR_HEAD_SCALE = 1.4, DIR_LABEL_SCALE = 0.87;
  function r1(v) { return Math.round(v * 10) / 10; }
  function dirEdgeMarks(e, x1, y1, x2, y2, R, font) {
    var dx = x2 - x1, dy = y2 - y1, out = "";
    // Map tidy: roles are the shared C / T / M badges (pairGlyphs) on every
    // map now; this draws only the pair label.
    var ang = Math.atan2(dy, dx) * 180 / Math.PI;
    var cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
    if (Math.abs(dy) > 0.01) {
      // Read every non-level edge top-first, using endpoints rather than the
      // display pair id (which may abbreviate or contain hyphenated names).
      var top = y1 <= y2 ? e.source : e.target, bot = y1 <= y2 ? e.target : e.source;
      out += '<g class="cm-pair-label cm-dir-label cm-dir-stack" font-size="' + font + '"' +
             ' data-cx="' + r1(cx) + '" data-cy="' + r1(cy) + '" data-stack="1" data-ang="' + r1(ang) + '"' +
             (e.has_coupler ? ' data-clear="' + r1(couplerR(R) + 3) + '"' : "") +
             ' data-pair-id="' + esc(e.pair_id) + '" text-anchor="middle" dominant-baseline="central">' +
             '<text>' + esc(top) + '</text><line class="cm-dir-connector"/><text>' + esc(bot) + "</text></g>";
      return out;
    }
    out += '<text class="cm-pair-label cm-dir-label" font-size="' + font + '"' +
           ' data-cx="' + r1(cx) + '" data-cy="' + r1(cy) + '" data-ang="' + r1(ang) + '"' +
           (e.has_coupler ? ' data-clear="' + r1(couplerR(R) + 3) + '"' : "") +
           ' text-anchor="middle" dominant-baseline="central" x="0" y="0">' +
           esc(e.pair_id) + "</text>";
    return out;
  }

  // ---- small 2-D geometry: convex polygons (SAT) and circles -------------
  function rectPoly(cx, cy, w, h, angDeg) {
    var a = angDeg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    var hw = w / 2, hh = h / 2, pts = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
    return pts.map(function (p) { return [cx + p[0] * c - p[1] * s, cy + p[0] * s + p[1] * c]; });
  }
  function bboxPoly(b, pad) {
    pad = pad || 0;
    return [[b.x - pad, b.y - pad], [b.x + b.width + pad, b.y - pad],
            [b.x + b.width + pad, b.y + b.height + pad], [b.x - pad, b.y + b.height + pad]];
  }
  function project(poly, ax) {
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < poly.length; i++) {
      var d = poly[i][0] * ax[0] + poly[i][1] * ax[1];
      if (d < lo) lo = d; if (d > hi) hi = d;
    }
    return [lo, hi];
  }
  function polysHit(A, B, tol) {
    var polys = [A, B];
    for (var k = 0; k < 2; k++) {
      var P = polys[k];
      for (var i = 0; i < P.length; i++) {
        var p = P[i], q = P[(i + 1) % P.length];
        var ax = [-(q[1] - p[1]), q[0] - p[0]], l = Math.sqrt(ax[0] * ax[0] + ax[1] * ax[1]) || 1;
        ax = [ax[0] / l, ax[1] / l];
        var a = project(A, ax), b = project(B, ax);
        if (a[1] - tol <= b[0] || b[1] - tol <= a[0]) return false;
      }
    }
    return true;
  }
  function circleHitsPoly(c, P, tol) {
    // inside, or closer than r to any edge
    var inside = false;
    for (var i = 0, j = P.length - 1; i < P.length; j = i++) {
      if (((P[i][1] > c.y) !== (P[j][1] > c.y)) &&
          (c.x < (P[j][0] - P[i][0]) * (c.y - P[i][1]) / (P[j][1] - P[i][1]) + P[i][0])) inside = !inside;
    }
    if (inside) return true;
    for (var k = 0; k < P.length; k++) {
      var a = P[k], b = P[(k + 1) % P.length];
      var vx = b[0] - a[0], vy = b[1] - a[1], wl = vx * vx + vy * vy || 1;
      var t = Math.max(0, Math.min(1, ((c.x - a[0]) * vx + (c.y - a[1]) * vy) / wl));
      var ex = a[0] + vx * t - c.x, ey = a[1] + vy * t - c.y;
      if (Math.sqrt(ex * ex + ey * ey) < c.r - tol) return true;
    }
    return false;
  }
  function itemsHit(a, b, tol) {
    if (a.circle && b.circle) {
      var dx = a.circle.x - b.circle.x, dy = a.circle.y - b.circle.y;
      return Math.sqrt(dx * dx + dy * dy) < a.circle.r + b.circle.r - tol;
    }
    if (a.circle) return circleHitsPoly(a.circle, b.poly, tol);
    if (b.circle) return circleHitsPoly(b.circle, a.poly, tol);
    return polysHit(a.poly, b.poly, tol);
  }
  function labelPoly(t) {
    var f = parseFloat(t.getAttribute("font-size")) || 12;
    var w = t.getComputedTextLength ? t.getComputedTextLength() : f * 4;
    var cx = parseFloat(t.getAttribute("data-px")), cy = parseFloat(t.getAttribute("data-py"));
    if (t.hasAttribute("data-stack")) {
      return bboxPoly(t.getBBox(), 2);
    }
    if (isNaN(cx)) {   // an unrotated label: its own box
      var b = t.getBBox();
      return bboxPoly(b, 0);
    }
    return rectPoly(cx, cy, w + 4, f * 1.05, parseFloat(t.getAttribute("data-ang")) || 0);
  }
  // The items a reader must be able to tell apart on a map: stones, pair
  // labels, role / direction glyphs. Lines are not items (an arrow on its
  // own line is the point).
  var OVERLAP_ITEMS = ".cm-stone, .cm-pair-label, .cm-role, .cm-arrow, .cm-freq, .cm-coupler";
  function collectItems(svg, sel) {
    var out = [];
    var els = svg.querySelectorAll(sel || OVERLAP_ITEMS);
    for (var i = 0; i < els.length; i++) {
      var el = els[i], it = { el: el };
      if (getComputedStyle(el).opacity === "0" || el.closest(".cm-off")) continue;
      if (el.tagName.toLowerCase() === "circle") {
        it.circle = { x: +el.getAttribute("cx"), y: +el.getAttribute("cy"), r: +el.getAttribute("r") };
      } else if (el.classList.contains("cm-pair-label")) {
        it.poly = labelPoly(el); it.label = true;
      } else {
        it.poly = bboxPoly(el.getBBox(), 0);
      }
      it.stone = el.classList.contains("cm-stone");
      it.role = el.classList.contains("cm-role");
      out.push(it);
    }
    return out;
  }
  /* Count overlapping item pairs (stone-stone excluded). Returns
   * {count, pairs:[[a,b],...]} with a short description per pair. */
  function labelOverlaps(svg, sel, tol) {
    tol = tol == null ? 0.5 : tol;
    var items = collectItems(svg, sel), pairs = [];
    function name(it) {
      var el = it.el;
      return (el.getAttribute("class") || el.tagName).split(" ")[0] + ":" +
        (el.textContent || el.getAttribute("data-cm-pair") || "").slice(0, 16);
    }
    for (var i = 0; i < items.length; i++) {
      for (var j = i + 1; j < items.length; j++) {
        if (items[i].stone && items[j].stone) continue;
        // compact C / T / M badges sit ON the stone rim by design (map tidy)
        if ((items[i].stone && items[j].role) || (items[j].stone && items[i].role)) continue;
        if (itemsHit(items[i], items[j], tol)) pairs.push([name(items[i]), name(items[j])]);
      }
    }
    return { count: pairs.length, items: items.length, pairs: pairs };
  }
  function placeStack(t, side, f) {
    var cx = +t.getAttribute("data-cx"), cy = +t.getAttribute("data-cy");
    var texts = t.querySelectorAll("text"), line = t.querySelector(".cm-dir-connector");
    t.setAttribute("font-size", f);
    // Font bounds vary by browser and font: leave a third-em connector plus
    // a small breathing gap between the two measured text boxes.
    var rowHeight = 0, i;
    for (i = 0; i < texts.length; i++) {
      texts[i].setAttribute("x", 0); texts[i].setAttribute("y", 0);
      rowHeight = Math.max(rowHeight, texts[i].getBBox().height);
    }
    var step = rowHeight + f * 0.45;
    for (i = 0; i < texts.length; i++) texts[i].setAttribute("y", (i - 0.5) * step);
    var upper = texts[0].getBBox(), lower = texts[1].getBBox();
    var gapY = (upper.y + upper.height + lower.y) / 2;
    line.setAttribute("x1", 0); line.setAttribute("x2", 0);
    line.setAttribute("y1", gapY - f / 6); line.setAttribute("y2", gapY + f / 6);
    // Re-measure the complete group at every trial font and restored candidate.
    var box = t.getBBox(), w = box.width, h = box.height;
    var ang = +t.getAttribute("data-ang") * Math.PI / 180;
    var nx = -Math.sin(ang), ny = Math.cos(ang);
    var off = Math.abs(nx) * w / 2 + Math.abs(ny) * h / 2 + 3 + Math.max(10, +t.getAttribute("data-clear") || 0);
    var px = cx + nx * off * side, py = cy + ny * off * side;
    var ox = px - (box.x + w / 2), oy = py - (box.y + h / 2);
    t.removeAttribute("transform");
    t.setAttribute("data-px", r1(px)); t.setAttribute("data-py", r1(py));
    t.setAttribute("data-w", r1(w)); t.setAttribute("data-h", r1(h));
    for (i = 0; i < texts.length; i++) {
      texts[i].setAttribute("x", r1(ox));
      texts[i].setAttribute("y", r1(oy + (i - 0.5) * step));
    }
    line.setAttribute("x1", r1(ox)); line.setAttribute("x2", r1(ox));
    line.setAttribute("y1", r1(oy + gapY - f / 6)); line.setAttribute("y2", r1(oy + gapY + f / 6));
  }

  function placeLabel(t, side, f) {
    if (t.hasAttribute("data-stack")) { placeStack(t, side, f); return; }
    var cx = +t.getAttribute("data-cx"), cy = +t.getAttribute("data-cy");
    var ang = +t.getAttribute("data-ang") * Math.PI / 180;
    var nx = -Math.sin(ang), ny = Math.cos(ang);    // the text frame's "down"
    // clears the arrow head (half-width ~7 px at the default cell), and the
    // coupler dot on the midpoint where the edge has one
    var off = f * 0.5 + Math.max(10, +t.getAttribute("data-clear") || 0);
    var px = cx + nx * off * side, py = cy + ny * off * side;
    t.setAttribute("font-size", f);
    t.setAttribute("data-px", r1(px)); t.setAttribute("data-py", r1(py));
    t.setAttribute("x", r1(px)); t.setAttribute("y", r1(py));
    // A level label needs no transform at all (FB-01: no rotated text).
    t.removeAttribute("transform");
  }
  /* D-1: every label sits on ONE screen side of its edge -- above a level
   * edge, left of a steep one -- whatever the edge's control -> target
   * direction. Choosing the side in the edge's own frame made labels
   * alternate with the arrow direction, which read as a tilted grid. */
  function screenSide(t) {
    var ang = +t.getAttribute("data-ang") * Math.PI / 180;
    var nx = -Math.sin(ang), ny = Math.cos(ang);
    if (Math.abs(ny) >= Math.abs(nx)) return ny > 0 ? -1 : 1;   // up
    return nx > 0 ? -1 : 1;                                      // left
  }
  /* Place each upright label on its screen side (screenSide), then one px
   * smaller at a time down to minFont; a label that still collides keeps the
   * placement with the fewest hits and is marked .cm-dir-crowded. */
  function declutterLabels(svg, minFont) {
    if (!svg || !svg.querySelectorAll) return;
    minFont = minFont || DIR_MIN_FONT;
    var fixed = collectItems(svg, ".cm-stone, .cm-arrow, .cm-role, .cm-freq, .cm-coupler, .cm-res");
    var labels = svg.querySelectorAll(".cm-dir-label");
    var placed = [];
    for (var i = 0; i < labels.length; i++) {
      var t = labels[i], f0 = parseFloat(t.getAttribute("font-size")) || 14;
      var best = null;
      var side = screenSide(t);
      for (var f = f0; f >= minFont - 0.01 && !(best && best.hits === 0); f = (f > minFont && f - 1 < minFont) ? minFont : f - 1) {
        for (var s = 0; s < 1; s++) {
          placeLabel(t, side, f);
          var me = { poly: labelPoly(t) }, hits = 0;
          for (var k = 0; k < fixed.length; k++) if (itemsHit(me, fixed[k], 0.5)) hits++;
          for (var m = 0; m < placed.length; m++) if (itemsHit(me, placed[m], 0.5)) hits++;
          if (!best || hits < best.hits) best = { side: side, f: f, hits: hits };
          if (hits === 0) break;
        }
        if (f <= minFont) break;
      }
      placeLabel(t, best.side, best.f);
      t.classList.toggle("cm-dir-crowded", best.hits > 0);
      placed.push({ poly: labelPoly(t) });
    }
  }
  // A map drawn inside a closed <details> (or before layout) has no text
  // metrics yet: place its labels the first time it gets a size.
  function dirDeclutter(svg) {
    if (!svg) return;
    function run() { try { declutterLabels(svg); } catch (err) { /* no layout (jsdom) */ } }
    var r = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
    if (r && r.width > 0 && r.height > 0) { run(); return; }
    if (typeof ResizeObserver === "undefined") { run(); return; }
    var ro = new ResizeObserver(function () {
      var b = svg.getBoundingClientRect();
      if (b.width > 0 && b.height > 0) { ro.disconnect(); run(); }
    });
    ro.observe(svg);
  }

  // Mounted maps share one theme notification listener. Detached hosts are pruned.
  var themeMaps = new Map();
  function onTheme(host, repaint) {
    themeMaps.forEach(function (_repaint, mounted) { if (!mounted.isConnected) themeMaps.delete(mounted); });
    themeMaps.set(host, repaint);
  }
  if (typeof document !== 'undefined') document.addEventListener('sm:themechange', function () {
    themeMaps.forEach(function (repaint, host) {
      if (!host.isConnected) themeMaps.delete(host);
      else repaint();
    });
  });
  function renderLayout(mount, opts) {
    var noop = { mode: "none", highlightEntity: function () {} };
    if (!mount) return noop;
    opts = opts || {};
    onTheme(mount, function () {
      var legend = mount.querySelector('.cm-feed-legend');
      renderLayout(mount, opts);
      if (legend) mount.appendChild(legend);
    });
    var nodes = opts.nodes || [], edges = opts.edges || [];
    var lay = layoutFor(nodes, edges);
    if (lay.mode === "none") {
      mount.innerHTML = '<p class="muted cm-none" style="margin:0">' +
        esc(opts.noneMessage || "No chip layout — this chip declares no positions and no pairs.") +
        "</p>";
      return noop;
    }

    var CELL = opts.cell || 64;
    var R = Math.round(CELL * 0.30);
    // D-94: only the SPACING grows (x DIR_SPACING); stones keep their size,
    // so the gap between two stones has room for a label and an arrow.
    var SP = Math.round(CELL * DIR_SPACING);
    var W = Math.max(SP, Math.round(lay.cols * SP));
    var H = Math.max(SP, Math.round(lay.rows * SP));
    // The add door (core/diagram_add.py): one extra column on the right holds
    // a "+" stone (add a qubit) and a "+ pair" stone (pick two qubits). It
    // claims no grid position; the server proposes the new qubit's cell.
    var ghostX = opts.ghost ? W + SP / 2 : null;
    if (opts.ghost) { W += SP; H = Math.max(H, 2 * SP); }
    var offC = spreadCoincident(lay.positions, 0.22);
    function px(id) {
      var p = lay.positions[id], o = offC[id];
      if (!p) return null;
      return { x: (p.col + 0.5) * SP + (o ? o.dx * SP : 0),
               y: (p.row + 0.5) * SP + (o ? o.dy * SP : 0),
               shared: !!(o && o.shared) };
    }
    function resAnchor(pt) { return { x: pt.x + R * 0.95, y: pt.y - R * 0.95 }; }

    var svg = "";

    // feedline buses (bottom layer): nodes sharing one rr_port label share the
    // physical readout line — a thin bus through their resonator marks.
    // docs/93 F4: each DRAWN bus takes a palette slot in SPATIAL order along
    // the dominant axis, so palette adjacency coincides with screen adjacency
    // (the gate the validated palette passes). Colour/dash live in CSS and
    // apply only under the resonators highlight; past 7 buses the rest wear
    // the neutral slot 0 with a unique inline dash — hues never cycle.
    var feeds = {}, feedOrder = [];
    for (var fi = 0; fi < nodes.length; fi++) {
      var fp = nodes[fi].rr_port;
      if (!fp || !lay.positions[nodes[fi].id]) continue;
      if (!Object.prototype.hasOwnProperty.call(feeds, fp)) { feeds[fp] = []; feedOrder.push(fp); }
      feeds[fp].push(nodes[fi].id);
    }
    var buses = [];
    for (var fo = 0; fo < feedOrder.length; fo++) {
      var members = feeds[feedOrder[fo]];
      if (members.length < 2) continue;
      var pts = [];
      for (var mi = 0; mi < members.length; mi++) {
        var mp = px(members[mi]);
        if (mp) pts.push(resAnchor(mp));
      }
      if (pts.length < 2) continue;
      // order along the dominant axis so the bus reads as one line
      pts.sort(W >= H ? function (a, b) { return a.x - b.x || a.y - b.y; }
                      : function (a, b) { return a.y - b.y || a.x - b.x; });
      var mean = 0;
      for (var pm = 0; pm < pts.length; pm++) mean += (W >= H ? pts[pm].y : pts[pm].x);
      buses.push({ label: feedOrder[fo], ids: members, pts: pts, mean: mean / pts.length });
    }
    // spatial slot order: buses sorted ACROSS the dominant axis (a landscape
    // chip's feedlines stack vertically -> sort by mean y), ties by label
    // ...ties by NATURAL label order (feedline_2 before feedline_10) — the
    // slot a bus lands in picks its colour, so this is a displayed order.
    buses.sort(function (a, b) {
      return a.mean - b.mean
          || String(a.label).localeCompare(String(b.label), undefined,
                                           { numeric: true, sensitivity: 'base' })
          || a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' });
    });
    var feedSlotById = {};
    for (var bi = 0; bi < buses.length; bi++) {
      var bus = buses[bi];
      bus.slot = bi < 7 ? bi + 1 : 0;
      for (var bm = 0; bm < bus.ids.length; bm++) feedSlotById[bus.ids[bm]] = bus.slot;
      var ptStr = "";
      for (var pi = 0; pi < bus.pts.length; pi++) ptStr += (pi ? " " : "") + bus.pts[pi].x + "," + bus.pts[pi].y;
      // slot-0 unique dash as inline STYLE, not an attribute — a stylesheet
      // dasharray rule beats any presentation attribute (same precedence trap
      // as the F2 font-size)
      svg += '<polyline class="cm-feed cm-feed-s' + bus.slot + '" data-cm-feed="' + esc(bus.label) + '"' +
             (bus.slot === 0 ? ' style="stroke-dasharray:2 ' + (2 + (bi - 7)) + '"' : "") +
             ' points="' + ptStr + '">' +
             "<title>readout feedline " + esc(bus.label) + " — " + bus.ids.length + " resonators</title></polyline>";
    }

    // pair edges (+ CR direction arrows, coupler dots)
    var pairFont = Math.round((CELL * 9 / 64) * 10) / 10;
    pairFont = Math.round(pairFont * 1.2 * 10) / 10;   // D-98: the pair is this map's subject
    pairFont = Math.max(Math.min(pairFont, DIR_MIN_FONT), Math.round(pairFont * DIR_LABEL_SCALE * 10) / 10);
    for (var ei = 0; ei < edges.length; ei++) {
      var e = edges[ei];
      var a = px(e.source), b = px(e.target);
      if (!a || !b) continue;
      var x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;
      if (e.directed) {
        var ddx = x2 - x1, ddy = y2 - y1, dl = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
        var doff = CELL * 0.06;
        x1 += -ddy / dl * doff; y1 += ddx / dl * doff;
        x2 += -ddy / dl * doff; y2 += ddx / dl * doff;
      }
      // solid = measured 2Q gate, dashed = defined but not measured,
      // dotted = no 2Q gate, faded = an old measurement (pairState)
      var pst = pairState(e);
      var cls = "cm-edge cm-edge-" + pst.state + (pst.stale ? " cm-edge-stale" : "") +
                (e.has_coupler ? " cm-edge-hascoupler" : "") +
                (e.active === false ? " cm-off" : "");
      // A wide transparent stroke makes pair hover and touch reachable.
      svg += '<g class="' + cls + '" data-cm="p:' + esc(e.pair_id) +
             '" data-link-ends="' + esc(e.source + ' ' + e.target) + '">' +
             '<line class="cm-edge-hit" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"/>' +
             '<line class="cm-edge-line" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"/>';
      if (e.has_coupler) {
        svg += '<circle class="cm-coupler" cx="' + ((x1 + x2) / 2) + '" cy="' + ((y1 + y2) / 2) +
               '" r="' + couplerR(R) + '"/>';
      }
      svg += dirEdgeMarks(e, x1, y1, x2, y2, R, pairFont);
      svg += '<line class="cm-hit" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"/>' +
             "<title>" + esc(e.pair_id) + " — control " + esc(e.source) + " → target " +
             esc(e.target) + (e.moving_qubit ? " · moving: " + esc(e.moving_qubit) : "") +
             "</title></g>";
    }

    // docs/120 item 11: the chevrons (and now the C/T/M role markers) are
    // drawn by the SHARED pairGlyphs, so the Chip Status hero and this map
    // cannot drift apart. The CHEVRON half is lifted verbatim and its geometry
    // is unchanged; the role markers are ADDITIVE here (the customer asked for
    // one convention across SM), so this map's output is deliberately NOT
    // byte-identical to what it replaced -- component_map_selfcheck was
    // updated in the same change, restating the docs/92 §2.4 pin at the
    // invariant it always meant: ids or role letters, and never a number.
    // v1.3.0: drawn AFTER the stones (below), as the Chip Status hero has
    // since docs/126 -- the flux stubs and resonator marks are part of each
    // stone's group and were painted over the C/T/M letters.
    var glyphSvg = pairGlyphs(nodes, edges, { px: px, cell: CELL, roles: true,
      roleFontSize: Math.max(9, CELL * 0.095) });

    // qubit stones + per-qubit component marks (resonator NE, flux stub S)
    // id text scales WITH the cell (docs/93 F2) — a CSS-fixed size left tiny
    // labels inside 1.9x stones. Anchored to the original 10.5px at the
    // original 64px cell, so a default-cell render keeps its exact font.
    var idFont = Math.round((CELL * 10.5 / 64) * 10) / 10;
    idFont = Math.max(DIR_MIN_FONT, Math.round(idFont * 0.88 * 10) / 10);   // D-98
    // Chain emphasis (docs/93 F3): with opts.emphasisChain set (the page's
    // active chain filter), matching stones read as selected and the rest
    // drop to context — composes with the page highlight, purely additive.
    var emphChain = opts.emphasisChain == null ? "" : String(opts.emphasisChain);
    for (var ni = 0; ni < nodes.length; ni++) {
      var n = nodes[ni];
      var pt = px(n.id);
      if (!pt) continue;
      var ra = resAnchor(pt);
      var nChain = n.chain == null ? "" : String(n.chain);
      var chainCls = !emphChain ? ""
        : (nChain === emphChain ? " cm-chain-emph" : " cm-chain-dim");
      svg += '<g class="cm-node' + chainCls + '" data-cm="q:' + esc(n.id) +
             '" data-link-id="' + esc(n.id) + '" data-cm-chain="' + esc(nChain) + '">' +
             '<circle class="cm-occluder" cx="' + pt.x + '" cy="' + pt.y + '" r="' + R + '"/>' +
             '<circle class="cm-stone" cx="' + pt.x + '" cy="' + pt.y + '" r="' + R + '"' +
             (pt.shared ? ' stroke-dasharray="3 2"' : "") + "/>" +
             '<text class="cm-id" font-size="' + idFont + '" x="' + pt.x + '" y="' + pt.y +
             '" text-anchor="middle" dominant-baseline="central">' + esc(n.id) + "</text>";
      if (n.rr_port) {
        var fSlot = feedSlotById[n.id];
        svg += '<circle class="cm-res' + (fSlot != null ? " cm-feed-s" + fSlot : "") +
               '" cx="' + ra.x + '" cy="' + ra.y + '" r="' + (R * 0.42) + '">' +
               "<title>" + esc(n.id) + " readout — " + esc(n.rr_port) + "</title></circle>";
      }
      if (n.z_port) {
        svg += '<line class="cm-flux" x1="' + pt.x + '" y1="' + (pt.y + R * 0.6) +
               '" x2="' + pt.x + '" y2="' + (pt.y + R * 1.5) + '">' +
               "<title>" + esc(n.id) + " flux — " + esc(n.z_port) + "</title></line>";
      }
      // docs/136 — a QDAC-biased qubit has NO z_port (no OPX analog output), so
      // the stub above drew nothing for 11 of this chip's 20 qubits and the map
      // showed them as if they had no flux bias at all. Its own mark, because
      // it is its own thing: a short bar across the stub position, dashed on a
      // bias tee where BOTH sources feed one line.
      if (n.bias_mode === "qdac" || n.bias_mode === "bias_tee") {
        var by = pt.y + R * (n.bias_mode === "bias_tee" ? 1.5 : 1.1);
        svg += '<line class="cm-qdac' +
               (n.bias_mode === "bias_tee" ? " cm-qdac-tee" : "") +
               '" x1="' + (pt.x - R * 0.42) + '" y1="' + by +
               '" x2="' + (pt.x + R * 0.42) + '" y2="' + by + '">' +
               "<title>" + esc(n.id) + " QDAC-II bias" +
               (n.qdac_channel != null ? " — channel " + esc(String(n.qdac_channel)) : "") +
               (n.bias_mode === "bias_tee" ? " (bias tee, with the LF-FEM above)" : "") +
               "</title></line>";
      }
      svg += "<title>" + esc(n.id) + "</title></g>";
    }
    svg += glyphSvg;
    if (opts.ghost) svg += ghostStones(ghostX, SP, R, idFont);

    var hl = opts.highlight ? " cm-hl-" + esc(String(opts.highlight)) : "";
    mount.innerHTML =
      (lay.mode === "logical" ? '<div class="cm-note">' + esc(LOGICAL_LAYOUT_NOTE) + "</div>" : "") +
      '<div class="cm-scroll"><svg class="cm-svg cm-dir' + hl + '" width="' + W + '" height="' + H +
      '" viewBox="0 0 ' + W + " " + H + '">' + svg + "</svg></div>" +
      ('<div class="cm-dir-legend">' + pairLegendItems(true) + '</div>');

    dirDeclutter(mount.querySelector("svg.cm-svg"));
    // Theme repaint replaces SVG children; keep delegated bindings and indices current.
    if (mount._detailBinding) {
      var binding = mount._detailBinding;
      bindDetails(mount, binding.selector, binding.label, binding.open);
    }
    if (mount._linkedBound) bindLinked(mount);
    function cssEsc(s) {
      return (typeof window !== "undefined" && window.CSS && window.CSS.escape)
        ? window.CSS.escape(String(s)) : String(s);
    }
    return {
      mode: lay.mode,
      // drawn feedline buses in slot order — the resonators-page legend reads
      // this (label = the shared rr_port, count = resonators on the line)
      feeds: buses.map(function (b) { return { label: b.label, slot: b.slot, count: b.ids.length }; }),
      highlightEntity: function (kind, id, on) {
        var sel = '[data-cm="' + (kind === "pair" ? "p" : "q") + ":" + cssEsc(id) + '"]';
        var els = mount.querySelectorAll(sel);
        for (var i = 0; i < els.length; i++) els[i].classList.toggle("cm-hot", !!on);
      },
    };
  }

  // The add door's two ghost stones: dashed, a "+" and a "+ pair" label.
  function ghostStones(x, SP, R, font) {
    var out = "";
    var rows = [["add:qubit", "qubit", "Add qubit", "Add a qubit"],
                ["add:pair", "pair", "Add pair", "Add a pair: then click two qubits"]];
    var lh = font * 1.15;
    for (var i = 0; i < rows.length; i++) {
      var y = (i + 0.5) * SP;
      // Two lines, one size for both buttons: "Add" over the noun.
      out += '<g class="cm-dir-ghost" data-cm="' + rows[i][0] + '" role="button" tabindex="0" aria-label="' +
             esc(rows[i][2]) + '"><circle class="cm-dir-ghost-stone" cx="' + x + '" cy="' + y + '" r="' + R + '"/>' +
             '<text class="cm-dir-ghost-text" font-size="' + font + '" text-anchor="middle" dominant-baseline="central">' +
             '<tspan x="' + x + '" y="' + r1(y - lh / 2) + '">Add</tspan>' +
             '<tspan x="' + x + '" y="' + r1(y + lh / 2) + '">' + esc(rows[i][1]) + "</tspan></text>" +
             "<title>" + esc(rows[i][3]) + "</title></g>";
    }
    return out;
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


  // One small hover affordance and one explicit detail panel across all maps.
  var detailButton = null, detailAnchor = null, detailPanel = null;
  var detailTimer = null, releaseDetailTrap = null, closingDetail = false;
  function positionDetail(surface, anchor) {
    if (!anchor || !anchor.isConnected) { closeDetails(); return; }
    var stone = anchor.querySelector('.cm-stone');
    var a = (stone || anchor).getBoundingClientRect(), b = surface.getBoundingClientRect();
    if (!a.width && !a.height) a = anchor.getBoundingClientRect();
    var pair = anchor.matches('[data-hero-pair], [data-cm^="p:"]');
    if (pair) {
      var line = anchor.querySelector('.cm-edge-line, .topo-hero-edge-hit');
      if (line) a = line.getBoundingClientRect();
      var midX = (a.left + a.right) / 2, midY = (a.top + a.bottom) / 2;
      a = {left: midX, right: midX, top: midY, bottom: midY, width: 0};
    }
    // A pair with a drawn badge: sit beside the badge, never over its name.
    var pid = pair && anchor.getAttribute('data-hero-pair');
    var svgRoot = pid && anchor.ownerSVGElement;
    var badgeEl = svgRoot && Array.prototype.filter.call(svgRoot.querySelectorAll('.topo-pair-badge'),
      function (el) { return el.getAttribute('data-hero-pair') === pid; })[0];
    if (badgeEl) {
      var br = badgeEl.getBoundingClientRect();
      if (br.width || br.height) a = br;
    }
    var pad = 8, gap = 5, x, y;
    if (surface === detailButton) {
      // Above, below, right or left of the stone: the first spot that covers
      // no pair badge or edge label of this map (above sat on the badge of a
      // vertical edge and hid its value).
      var svg = anchor.ownerSVGElement || anchor.closest('svg') || (anchor.parentNode && anchor.parentNode.querySelector && anchor.parentNode.querySelector('svg'));
      var obst = svg ? Array.prototype.map.call(svg.querySelectorAll('.topo-pair-badge, text'), function (el) {
        return anchor.contains(el) ? null : el.getBoundingClientRect();
      }).filter(function (r) { return r && (r.width || r.height); }) : [];
      // A stone: its diagonal corners first -- pair edges and their badges
      // run on the axes, so a corner never sits on one. A pair: off its badge.
      var cands = pair ? [
        [(a.left + a.right - b.width) / 2, a.top - b.height - gap],
        [(a.left + a.right - b.width) / 2, a.bottom + gap],
        [a.right + gap, (a.top + a.bottom - b.height) / 2],
        [a.left - b.width - gap, (a.top + a.bottom - b.height) / 2]] : [
        [a.right - b.width * 0.25, a.top - b.height * 0.75],
        [a.left - b.width * 0.75, a.top - b.height * 0.75],
        [a.right - b.width * 0.25, a.bottom - b.height * 0.25],
        [a.left - b.width * 0.75, a.bottom - b.height * 0.25],
        [(a.left + a.right - b.width) / 2, a.top - b.height - gap]];
      var bestHit = Infinity;
      cands.forEach(function (c) {
        if (c[1] < pad || c[0] < pad || c[0] + b.width > window.innerWidth - pad) return;
        var hit = 0;
        obst.forEach(function (r) {
          var w = Math.min(c[0] + b.width, r.right) - Math.max(c[0], r.left);
          var h = Math.min(c[1] + b.height, r.bottom) - Math.max(c[1], r.top);
          if (w > 0 && h > 0) hit += w * h;
        });
        if (hit < bestHit) { bestHit = hit; x = c[0]; y = c[1]; }
      });
      if (x == null) { x = cands[1][0]; y = cands[1][1]; }
    } else {
      x = a.right + gap; y = a.top;
      if (x + b.width > window.innerWidth - pad) x = a.left - b.width - gap;
    }
    surface.style.left = Math.round(Math.max(pad, Math.min(x, window.innerWidth - b.width - pad))) + 'px';
    surface.style.top = Math.round(Math.max(pad, Math.min(y, window.innerHeight - b.height - pad))) + 'px';
  }

  function hideDetailButton() {
    clearTimeout(detailTimer);
    if (detailButton) detailButton.remove();
    detailButton = null;
  }
  function closeDetails() {
    closingDetail = true;
    var anchor = detailAnchor;
    hideDetailButton();
    if (detailPanel) {
      var panel = detailPanel;
      detailPanel = null;
      if (panel._closeDetail) panel._closeDetail();
      panel.remove();
    }
    if (releaseDetailTrap) { releaseDetailTrap(); releaseDetailTrap = null; }
    if (anchor) anchor.setAttribute('aria-expanded', 'false');
    detailAnchor = null;
    closingDetail = false;
  }
  function scheduleDetailHide() {
    clearTimeout(detailTimer);
    detailTimer = setTimeout(function () {
      if (detailButton && detailButton !== document.activeElement &&
          detailAnchor !== document.activeElement) hideDetailButton();
    }, 240);
  }
  // Standalone reports load the map without app.js and its shared trapFocus.
  function trapStandaloneDetails(panel, anchor) {
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); closeDetails(); return; }
      if (e.key !== 'Tab') return;
      var items = Array.prototype.filter.call(panel.querySelectorAll(
        'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
        function (el) { return !el.disabled && (el.offsetWidth || el.offsetHeight); });
      var first = items[0] || panel, last = items[items.length - 1] || panel;
      if (!panel.contains(document.activeElement) || (!e.shiftKey && document.activeElement === last)) {
        e.preventDefault(); first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      }
    }
    document.addEventListener('keydown', onKey, true);
    return function () {
      document.removeEventListener('keydown', onKey, true);
      if (anchor.isConnected) anchor.focus({preventScroll: true});
    };
  }
  function offerDetail(anchor, label, open) {
    if (closingDetail || detailPanel) return;
    clearTimeout(detailTimer);
    if (detailButton && detailAnchor === anchor) return;
    hideDetailButton();
    detailAnchor = anchor;
    var button = document.createElement('button');
    button.type = 'button'; button.className = 'topo-expand';
    button.textContent = 'Expand';
    button.setAttribute('aria-label', 'Expand ' + label);
    button.setAttribute('aria-haspopup', 'dialog');
    anchor.setAttribute('aria-haspopup', 'dialog');
    anchor.setAttribute('aria-expanded', 'false');
    button.addEventListener('mouseenter', function () { clearTimeout(detailTimer); });
    button.addEventListener('mouseleave', scheduleDetailHide);
    button.addEventListener('focus', function () { clearTimeout(detailTimer); });
    button.addEventListener('blur', scheduleDetailHide);
    button.addEventListener('click', function (e) {
      e.stopPropagation();
      hideDetailButton();
      // The node survives closing, so trapFocus returns focus to that node.
      closingDetail = true;
      anchor.focus({preventScroll: true});
      closingDetail = false;
      var panel = open(anchor);
      if (!panel) return;
      detailPanel = panel; detailAnchor = anchor;
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-modal', 'true');
      panel.setAttribute('aria-label', label + ' details');
      panel.setAttribute('tabindex', '-1');
      anchor.setAttribute('aria-expanded', 'true');
      positionDetail(panel, anchor);
      if (window.trapFocus) releaseDetailTrap = window.trapFocus(panel, closeDetails);
      else releaseDetailTrap = trapStandaloneDetails(panel, anchor);
      var first = panel.querySelector('button');
      (first || panel).focus();
    });
    detailButton = button;
    document.body.appendChild(button);
    positionDetail(button, anchor);
  }
  function bindDetails(host, selector, label, open) {
    host._detailBinding = {selector: selector, label: label, open: open};
    host.querySelectorAll(selector).forEach(function (anchor) {
      anchor.setAttribute('aria-label', label(anchor));
      Array.prototype.forEach.call(anchor.children, function (child) {
        if (child.tagName.toLowerCase() === 'title' && !child.textContent.trim()) child.textContent = label(anchor);
      });
      if (!anchor.hasAttribute('tabindex')) anchor.setAttribute('tabindex', '0');
    });
    if (host._detailDelegated) return;
    host._detailDelegated = true;
    var touchAnchor = null;
    function handle(e) {
      var binding = host._detailBinding;
      var anchor = e.target.closest && e.target.closest(binding.selector);
      if (!anchor || !host.contains(anchor)) return;
      function offer() { offerDetail(anchor, binding.label(anchor), function () { return binding.open(anchor); }); }
      if (e.type === 'mouseenter' || e.type === 'focusin') {
        if (!e.relatedTarget || !anchor.contains(e.relatedTarget)) offer();
      } else if (e.type === 'mouseleave' || e.type === 'focusout') {
        if (!e.relatedTarget || !anchor.contains(e.relatedTarget)) scheduleDetailHide();
      } else if (e.type === 'pointerdown') {
        touchAnchor = e.pointerType === 'touch' ? anchor : null;
      } else if (e.type === 'click') {
        var noHover = window.matchMedia && window.matchMedia('(hover: none)').matches;
        if (touchAnchor !== anchor && !noHover) return;
        touchAnchor = null; e.preventDefault(); e.stopImmediatePropagation(); offer();
      } else if (e.type === 'keydown' && e.key === 'Tab' && !e.shiftKey && detailButton && detailAnchor === anchor) {
        e.preventDefault(); detailButton.focus();
      }
    }
    ['mouseenter', 'mouseleave', 'focusin', 'focusout', 'pointerdown', 'click', 'keydown'].forEach(function (type) {
      host.addEventListener(type, handle, true);
    });
  }
  // Page-wide closers; absent when the module is loaded without a DOM.
  if (typeof document !== 'undefined') {
  document.addEventListener('click', function (e) {
    if (detailPanel && !detailPanel.contains(e.target)) closeDetails();
    else if (detailButton && e.target !== detailButton &&
             !(detailAnchor && detailAnchor.contains(e.target))) closeDetails();
  });
  document.addEventListener('keydown', function (e) {
    // Page-level Escape handlers skip a press that belongs to the map details.
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (detailPanel) {
      e._topoDetail = true;
      if (!releaseDetailTrap) { e.preventDefault(); closeDetails(); }
    } else if (detailButton) {
      // A bare Expand offer is dismissed without consuming the key.
      hideDetailButton();
    }
  }, true);
  document.addEventListener('htmx:beforeSwap', function (e) {
    var target = e.detail && e.detail.target;
    if (detailAnchor && target && target.contains(detailAnchor)) closeDetails();
  });
  document.addEventListener('scroll', function (e) {
    if (detailPanel && detailPanel.contains(e.target)) return;
    var surface = detailPanel || detailButton;
    if (!surface || !detailAnchor) return;
    var a = detailAnchor.getBoundingClientRect();
    if (a.bottom < 0 || a.top > window.innerHeight || a.right < 0 || a.left > window.innerWidth) closeDetails();
    else positionDetail(surface, detailAnchor);
  }, true);
  window.addEventListener('resize', function () {
    var surface = detailPanel || detailButton;
    if (surface) positionDetail(surface, detailAnchor);
  });
  }

  // -- Pairs as first-class map objects (shared by every map that draws edges) --
  // What the chip holds for a pair's 2Q gate: 'measured' (a fidelity value),
  // 'pending' (a gate is defined but never measured) or 'none' (no 2Q gate).
  // `stale` follows the caller's age rule; the default is the Chip Status
  // calibration-age rule (older than 30 days fails).
  var PAIR_STALE_DAYS = 30;
  function pairState(e, isStale) {
    var v = e && typeof e.cz_fidelity === 'number' ? e.cz_fidelity : null;
    var state = v != null ? 'measured' : ((e && (e.has_cz || (e.gate_kind && e.gate_kind !== 'none'))) ? 'pending' : 'none');
    var at = e && typeof e.cz_fidelity_updated_at === 'number' ? e.cz_fidelity_updated_at : null;
    var stale = false;
    if (state === 'measured' && at != null) {
      stale = isStale ? !!isStale(at) : (Date.now() - at) / 86400000 > PAIR_STALE_DAYS;
    }
    return { state: state, value: v, stale: stale, age: at == null ? "unknown" : (stale ? "stale" : "fresh") };
  }
  // Midpoint badge: a pill with the pair name over at most one number, or a
  // small dot when the pill does not fit (dense chips). The pill is always in
  // the markup so a hover or a linked highlight can show it.
  function pairBadgeSvg(o) {
    var lines = [o.name];
    if (o.valueText) lines.push(o.valueText);
    var fs = o.fontSize || 9, lh = Math.round(fs * 1.15 * 10) / 10;
    var longest = 0;
    lines.forEach(function (t) { longest = Math.max(longest, String(t).length); });
    var w = Math.round(longest * fs * 0.62 + fs * 1.1);
    var h = Math.round(lines.length * lh + fs * 0.6);
    var dense = !!o.dense || (o.room != null && (w > o.room || h > o.roomAcross));
    var fill = o.fill ? ' style="fill:' + o.fill + '"' : '';
    var fg = o.fg ? ' style="fill:' + o.fg + '"' : '';
    var y0 = -((lines.length - 1) * lh) / 2;
    var text = '';
    lines.forEach(function (t, i) {
      text += '<text class="topo-pair-badge-text' + (i ? ' topo-pair-badge-val' : '') + '" y="' +
        Math.round((y0 + i * lh) * 10) / 10 + '" font-size="' + fs +
        '" text-anchor="middle" dominant-baseline="central"' + fg + '>' + esc(t) + '</text>';
    });
    return {
      dense: dense, width: w, height: h,
      svg: '<g class="topo-pair-badge' + (dense ? ' topo-pair-badge-dense' : '') +
        (o.state ? ' topo-pair-badge-' + o.state : '') + '"' +
        (o.attrs || '') + ' transform="translate(' + Math.round(o.x * 10) / 10 + ',' + Math.round(o.y * 10) / 10 + ')">' +
        '<circle class="topo-pair-badge-dot" r="' + Math.max(3, Math.round(fs * 0.45)) + '"' + fill + '></circle>' +
        '<g class="topo-pair-badge-pill">' +
        '<rect x="' + (-w / 2) + '" y="' + (-h / 2) + '" width="' + w + '" height="' + h +
        '" rx="' + Math.min(h / 2, fs) + '"' + fill + '></rect>' + text + '</g>' +
        (o.title ? '<title>' + esc(o.title) + '</title>' : '') + '</g>'
    };
  }
  // Linked highlight: a pair lights its two qubits, a qubit lights its pairs.
  // Markup contract: a pair element (edge or badge) carries
  // data-link-ends="a b"; a qubit element carries data-link-id="a".
  function bindLinked(host) {
    if (!host) return;
    // Rebuild once per render; hover only visits the selected adjacency list.
    var pairs = new Map(), qubits = new Map(), adjacent = new Map();
    function add(index, key, el) {
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(el);
    }
    host.querySelectorAll('[data-link-ends]').forEach(function (el) {
      var ends = el.getAttribute('data-link-ends');
      add(pairs, ends, el);
      ends.split(' ').forEach(function (id) { if (id) add(adjacent, id, el); });
    });
    host.querySelectorAll('[data-link-id]').forEach(function (el) { add(qubits, el.getAttribute('data-link-id'), el); });
    host._linkedIndex = {pairs: pairs, qubits: qubits, adjacent: adjacent};
    if (host._linkedBound) return;
    host._linkedBound = true;
    var lit = [], hidden = [];
    function clear() {
      lit.forEach(function (el) { el.classList.remove('topo-linked'); });
      lit = [];
      hidden.forEach(function (el) { el.style.visibility = ''; });
      hidden = [];
    }
    // A badge shown on hover sits on its edge's midpoint, where the edge's own
    // label (e.g. the frequency gap) is drawn: hide a label it would half-cover
    // instead of leaving its clipped remains around the pill.
    function unclutter() {
      var shown = lit.filter(function (el) { return el.classList.contains('topo-pair-badge'); });
      if (!shown.length) return;
      var rects = shown.map(function (el) { return el.getBoundingClientRect(); });
      host.querySelectorAll('svg text').forEach(function (t) {
        if (t.closest('.topo-pair-badge, [data-link-id]')) return;
        var r = t.getBoundingClientRect();
        if (!r.width) return;
        if (rects.some(function (b) { return r.right > b.left && r.left < b.right && r.bottom > b.top && r.top < b.bottom; })) {
          t.style.visibility = 'hidden'; hidden.push(t);
        }
      });
    }
    function light(el) { el.classList.add('topo-linked'); lit.push(el); }
    function apply(target) {
      clear();
      var index = host._linkedIndex;
      var pair = target.closest && target.closest('[data-link-ends]');
      var qubit = !pair && target.closest && target.closest('[data-link-id]');
      if (pair && host.contains(pair)) {
        var ends = pair.getAttribute('data-link-ends');
        (index.pairs.get(ends) || []).forEach(light);
        ends.split(' ').forEach(function (id) { (index.qubits.get(id) || []).forEach(light); });
      } else if (qubit && host.contains(qubit)) {
        var id = qubit.getAttribute('data-link-id');
        (index.qubits.get(id) || []).forEach(light);
        (index.adjacent.get(id) || []).forEach(light);
      }
      unclutter();
    }
    host.addEventListener('mouseover', function (e) { apply(e.target); });
    host.addEventListener('focusin', function (e) { apply(e.target); });
    host.addEventListener('mouseleave', clear);
    host.addEventListener('focusout', function (e) {
      if (!e.relatedTarget || !host.contains(e.relatedTarget)) clear();
    });
  }

  return {
    onTheme: onTheme,
    bindDetails: bindDetails,
    pairState: pairState,
    pairBadgeSvg: pairBadgeSvg,
    bindLinked: bindLinked,
    closeDetails: closeDetails,
    positionDetail: positionDetail,
    bareIndex: bareIndex,
    quamPairId: quamPairId,
    normalizeGrid: normalizeGrid,
    pairGridPositions: pairGridPositions,
    layoutFor: layoutFor,
    LOGICAL_LAYOUT_NOTE: LOGICAL_LAYOUT_NOTE,
    spreadCoincident: spreadCoincident,
    pairGlyphs: pairGlyphs,
    renderLayout: renderLayout,
    labelOverlaps: labelOverlaps,
    declutterLabels: declutterLabels,
    screenSide: screenSide,
    pairLegendItems: pairLegendItems,
    edgeStyleForGate: edgeStyleForGate,
    legendForGate: legendForGate,
    renderStatic: renderStatic,
  };
})();
