/* Node selfcheck for web/static/topo-graph.js conventions. Pins normalizeGrid to
 * BOTH chip-status inline paths (card = tolerant, heatmap = strict) by replicating
 * their exact math as golden, and exercises quamPairId + pairGridPositions. Also
 * prints the quamPairId map for the Python cross-check vs run_build._quam_pair_id.
 *
 * Run: node tests/topo_graph_selfcheck.cjs   (driven by tests/test_topo_graph.py).
 */
const fs = require('fs');
const path = require('path');

global.window = {};
const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'topo-graph.js'), 'utf8');
// eslint-disable-next-line no-eval
eval(src);
const TG = global.window.TopoGraph;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) { console.error('FAIL: ' + m + ' got ' + JSON.stringify(a) + ' want ' + JSON.stringify(b)); fails++; } }

if (!TG) { console.error('FAIL: window.TopoGraph not exposed'); process.exit(1); }

// ── Golden replications of the two chip-status inline paths ──────────────────
function goldenTolerant(nodes) {        // chip-status.js buildTopology 520-556
  const positions = nodes.map(function (n, i) {
    const parts = (n.grid_location || '').split(',');
    return {
      col: parts.length === 2 ? parseFloat(parts[0]) : (i % 4),
      row: parts.length === 2 ? parseFloat(parts[1]) : Math.floor(i / 4),
    };
  });
  let minCol = Infinity, maxRow = -Infinity;
  positions.forEach(function (p) { if (p.col < minCol) minCol = p.col; if (p.row > maxRow) maxRow = p.row; });
  const out = {};
  nodes.forEach(function (n, i) { out[n.id] = { col: positions[i].col - minCol, row: maxRow - positions[i].row }; });
  return out;
}
function goldenStrict(nodes) {          // chip-status.js gridPositions 1073-1099
  const gp = {}; let minGC = Infinity, minGR = Infinity;
  nodes.forEach(function (n) {
    const parts = (n.grid_location || '').split(',');
    if (parts.length === 2) {
      const c = parseInt(parts[0], 10), r = parseInt(parts[1], 10);
      if (!isNaN(c) && !isNaN(r)) { gp[n.id] = { col: c, row: r }; if (c < minGC) minGC = c; if (r < minGR) minGR = r; }
    }
  });
  if (Object.keys(gp).length !== nodes.length) return null;   // strict gate
  let maxGR = -Infinity;
  for (const q in gp) { gp[q].col -= minGC; gp[q].row -= minGR; if (gp[q].row > maxGR) maxGR = gp[q].row; }
  for (const q2 in gp) { gp[q2].row = maxGR - gp[q2].row; }
  return gp;
}

const CASES = {
  full_grid: [
    { id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' }, { id: 'q3', grid_location: '2,0' },
    { id: 'q4', grid_location: '0,1' }, { id: 'q5', grid_location: '1,1' }, { id: 'q6', grid_location: '2,1' },
  ],
  shifted: [   // non-zero origin -> 0-base normalization must kick in
    { id: 'q1', grid_location: '3,5' }, { id: 'q2', grid_location: '4,5' }, { id: 'q3', grid_location: '3,6' },
  ],
  ring8: [     // ring on a 3x3 perimeter (sparse-ish)
    { id: 'q1', grid_location: '0,2' }, { id: 'q2', grid_location: '1,2' }, { id: 'q3', grid_location: '2,2' },
    { id: 'q4', grid_location: '2,1' }, { id: 'q5', grid_location: '2,0' }, { id: 'q6', grid_location: '1,0' },
    { id: 'q7', grid_location: '0,0' }, { id: 'q8', grid_location: '0,1' },
  ],
  missing_some: [   // tolerant falls back; strict gate fails
    { id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '' }, { id: 'q3', grid_location: '1,0' },
  ],
};

Object.keys(CASES).forEach(function (name) {
  const nodes = CASES[name];
  eq(TG.normalizeGrid(nodes, { mode: 'tolerant' }).positions, goldenTolerant(nodes), 'tolerant matches chip-status card path [' + name + ']');
  const gs = goldenStrict(nodes);
  const strict = TG.normalizeGrid(nodes, { mode: 'strict' });
  if (gs === null) ok(strict.placed === false, 'strict gate fails when a node lacks grid [' + name + ']');
  else { ok(strict.placed === true, 'strict placed [' + name + ']'); eq(strict.positions, gs, 'strict matches chip-status heatmap path [' + name + ']'); }
});

// ── pairGridPositions (doubled-coord) ────────────────────────────────────────
{
  const nodes = CASES.full_grid;
  const pos = TG.normalizeGrid(nodes, { mode: 'strict' }).positions;
  const edges = [{ source: 'q1', target: 'q2', pair_id: 'q1-2' }, { source: 'q1', target: 'q4', pair_id: 'q1-4' }];
  const pg = TG.pairGridPositions(edges, pos);
  eq(pg.positions['q1-2'], { col: pos.q1.col + pos.q2.col, row: pos.q1.row + pos.q2.row }, 'pairGridPositions doubled-coord q1-2');
  ok(pg.has === true, 'pairGridPositions has');
}

// ── quamPairId — spec "q1-q2" -> QUAM "q1-2" (NOT a hyphen-join) ──────────────
const PAIRS = ['q1-q2', 'q2-q3', 'q10-q11', 'qA1-qB2', 'q1-q10'];
ok(TG.quamPairId('q1-q2') === 'q1-2', 'quamPairId q1-q2 -> q1-2');
ok(TG.quamPairId('q10-q11') === 'q10-11', 'quamPairId q10-q11 -> q10-11');
ok(TG.quamPairId('qA1-qB2') === 'qA1-B2', 'quamPairId qA1-qB2 -> qA1-B2');
ok(TG.quamPairId(['q1', 'q2']) === 'q1-2', 'quamPairId array form');

// ── gate -> edge style + legend (shared convention) ──────────────────────────
eq(TG.edgeStyleForGate('cr'), 'directed', 'edgeStyleForGate cr -> directed');
eq(TG.edgeStyleForGate('cz_fixed'), 'dashed', 'edgeStyleForGate cz_fixed -> dashed');
eq(TG.edgeStyleForGate('cz_tunable'), 'coupler', 'edgeStyleForGate cz_tunable -> coupler');
eq(TG.edgeStyleForGate('nope'), 'plain', 'edgeStyleForGate unknown -> plain');
ok(/cross-resonance/i.test(TG.legendForGate('cr')), 'legendForGate cr names cross-resonance');
ok(/fixed coupler/i.test(TG.legendForGate('cz_fixed')), 'legendForGate cz_fixed names fixed coupler');
ok(/tunable coupler/i.test(TG.legendForGate('cz_tunable')), 'legendForGate cz_tunable names tunable coupler');
ok(TG.legendForGate('nope') === '', 'legendForGate unknown is empty');

// ── renderStatic (read-only Populate mirror) — pure string render ─────────────
{
  const nodes = [
    { id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' },
    { id: 'q3', grid_location: '2,0' },
  ];
  const pairs = [['q1', 'q2'], ['q2', 'q3']];

  const m1 = { innerHTML: '' };
  TG.renderStatic(m1, { qubits: nodes, pairs: pairs, gate: 'cr' });
  ok((m1.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 3, 'renderStatic draws 3 stones');
  ok((m1.innerHTML.match(/gen-topo-stone-label/g) || []).length === 3, 'renderStatic labels 3 stones');
  ok((m1.innerHTML.match(/class="gen-topo-edge"/g) || []).length === 2, 'renderStatic draws 2 edges');
  ok(/gen-topo-arrow/.test(m1.innerHTML), 'renderStatic CR -> arrowheads');
  ok(!/gen-topo-edge--dashed/.test(m1.innerHTML), 'renderStatic CR -> not dashed');

  const m2 = { innerHTML: '' };
  TG.renderStatic(m2, { qubits: nodes, pairs: pairs, gate: 'cz_fixed' });
  ok(/gen-topo-edge--dashed/.test(m2.innerHTML), 'renderStatic cz_fixed -> dashed');
  ok(!/gen-topo-arrow/.test(m2.innerHTML), 'renderStatic cz_fixed -> no arrows');

  const m3 = { innerHTML: '' };
  TG.renderStatic(m3, { qubits: nodes, pairs: pairs, gate: 'cz_tunable' });
  ok(/gen-topo-coupler/.test(m3.innerHTML), 'renderStatic cz_tunable -> coupler dots');

  // unplaced qubits are omitted (no i%4 fallback); empty -> friendly message
  const m4 = { innerHTML: '' };
  TG.renderStatic(m4, { qubits: [{ id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: null }], pairs: [], gate: 'cr' });
  ok((m4.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 1, 'renderStatic omits unplaced qubits');
  const m5 = { innerHTML: '' };
  TG.renderStatic(m5, { qubits: [{ id: 'q1', grid_location: null }], pairs: [], gate: 'cr' });
  ok(/No qubits placed/.test(m5.innerHTML), 'renderStatic nothing-placed message');
  // an edge to a non-existent/unplaced node is silently skipped (no crash)
  const m6 = { innerHTML: '' };
  TG.renderStatic(m6, { qubits: nodes, pairs: [['q1', 'qZ']], gate: 'cr' });
  ok((m6.innerHTML.match(/class="gen-topo-edge"/g) || []).length === 0, 'renderStatic skips edge to missing node');

  // ── P2 extensions (compare-hub structure strip) ──────────────────────
  // layout:"auto" — grid-less qubits get a tolerant (i%4, i/4) fallback row
  // BELOW the placed grid instead of being omitted; default stays faithful.
  const auto1 = { innerHTML: '' };
  TG.renderStatic(auto1, {
    qubits: [{ id: 'q1', grid_location: '0,1' }, { id: 'q2' }, { id: 'q3', grid_location: 'junk' }],
    pairs: [['q1', 'q2']], gate: 'cr', layout: 'auto',
  });
  ok((auto1.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 3, 'auto layout places grid-less qubits');
  ok((auto1.innerHTML.match(/class="gen-topo-edge"/g) || []).length === 1, 'auto layout edges reach fallback stones');
  const noAuto = { innerHTML: '' };
  TG.renderStatic(noAuto, {
    qubits: [{ id: 'q1', grid_location: '0,1' }, { id: 'q2' }],
    pairs: [], gate: 'cr',
  });
  ok((noAuto.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 1, 'default still omits unplaced (faithful mirror)');
  const allAuto = { innerHTML: '' };
  TG.renderStatic(allAuto, { qubits: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }, { id: 'e' }], pairs: [], layout: 'auto' });
  ok((allAuto.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 5, 'auto layout works with NO grid at all');
  ok(!/No qubits placed/.test(allAuto.innerHTML), 'auto layout never shows the empty-board message for named qubits');

  // per-node class hook — cls appended (escaped) to the stone circle
  const tinted = { innerHTML: '' };
  TG.renderStatic(tinted, {
    qubits: [{ id: 'q1', grid_location: '0,0', cls: 'cmp-stone-diff' },
             { id: 'q2', grid_location: '1,0' },
             { id: 'q3', grid_location: '2,0', cls: '"><svg onload=x>' }],
    pairs: [], gate: 'cr',
  });
  ok((tinted.innerHTML.match(/gen-topo-stone-ro cmp-stone-diff/g) || []).length === 1, 'cls hook tints exactly the flagged stone');
  ok(!/onload=x>/.test(tinted.innerHTML), 'cls hook escapes hostile class strings');
}

// ── P0 (docs/91 §2.1): layoutFor — the honest layout selector ────────────────
// Three states, never two: 'physical' ONLY when every node passes the strict
// grid gate; 'logical' = positions derived from pair connectivity alone (never
// the tolerant (i%4, i/4) raster promoted to a map); 'none' when neither
// positions nor pairs exist. Pure + deterministic.
{
  ok(typeof TG.layoutFor === 'function', 'layoutFor exposed');

  const dist = function (p, q) { return Math.hypot(p.col - q.col, p.row - q.row); };

  // 2x3 lattice connectivity, given two ways (object edges + array pairs)
  const LAT_EDGES = [
    { source: 'q1', target: 'q2' }, { source: 'q2', target: 'q3' },
    { source: 'q4', target: 'q5' }, { source: 'q5', target: 'q6' },
    { source: 'q1', target: 'q4' }, { source: 'q2', target: 'q5' }, { source: 'q3', target: 'q6' },
  ];
  const LAT_PAIRS = LAT_EDGES.map(function (e) { return [e.source, e.target]; });

  // 1) full valid grid -> physical, positions IDENTICAL to strict normalizeGrid
  const ph = TG.layoutFor(CASES.full_grid, LAT_EDGES);
  ok(ph.mode === 'physical', 'layoutFor full grid -> physical');
  eq(ph.positions, TG.normalizeGrid(CASES.full_grid, { mode: 'strict' }).positions,
     'physical positions == strict normalizeGrid (same math, same flip)');
  eq([ph.cols, ph.rows], [3, 2], 'physical cols/rows from the strict gate');

  // 1b) faithful to strict: duplicate declared positions still gate as physical
  // (the chip's own claim — layoutFor adds NO new gates over normalizeGrid)
  const dup = TG.layoutFor(
    [{ id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '0,0' }], []);
  ok(dup.mode === 'physical', 'duplicate declared positions stay physical (faithful to strict)');

  // 2) grid-less chip + pairs -> logical, and NOT the (i%4, i/4) raster
  const NOGRID = [{ id: 'q1' }, { id: 'q2' }, { id: 'q3' }, { id: 'q4' }, { id: 'q5' }, { id: 'q6' }];
  const lg = TG.layoutFor(NOGRID, LAT_EDGES);
  ok(lg.mode === 'logical', 'no grid + pairs -> logical');
  const raster = {};
  NOGRID.forEach(function (n, i) { raster[n.id] = { col: i % 4, row: Math.floor(i / 4) }; });
  ok(JSON.stringify(lg.positions) !== JSON.stringify(raster),
     'logical positions are NOT the tolerant i%4 raster (docs/91 §2.1 — the fabrication this exists to prevent)');
  ok(Object.keys(lg.positions).length === 6, 'logical places every node');

  // layout quality properties (loose bounds — this is a layout, not a golden):
  // every edge lands near one cell; nothing overlaps; connectivity governs
  // (mean edge length < mean non-edge distance).
  let eSum = 0, eN = 0, neSum = 0, neN = 0, minPair = Infinity;
  const ids = NOGRID.map(function (n) { return n.id; });
  const isEdge = {};
  LAT_EDGES.forEach(function (e) { isEdge[e.source + '|' + e.target] = isEdge[e.target + '|' + e.source] = true; });
  for (let a = 0; a < ids.length; a++) {
    for (let b = a + 1; b < ids.length; b++) {
      const d = dist(lg.positions[ids[a]], lg.positions[ids[b]]);
      if (d < minPair) minPair = d;
      if (isEdge[ids[a] + '|' + ids[b]]) { eSum += d; eN++; } else { neSum += d; neN++; }
    }
  }
  LAT_EDGES.forEach(function (e) {
    const d = dist(lg.positions[e.source], lg.positions[e.target]);
    ok(d > 0.4 && d < 2.5, 'logical edge length sane [' + e.source + '-' + e.target + '] got ' + d.toFixed(2));
  });
  ok(minPair > 0.3, 'logical layout never overlaps nodes (min pair dist ' + minPair.toFixed(2) + ')');
  ok(eSum / eN < neSum / neN, 'connected nodes sit closer than unconnected ones');

  // positions normalized to a 0-based box the renderer can size from
  let minC = Infinity, minR = Infinity, maxC = -Infinity, maxR = -Infinity;
  ids.forEach(function (id) {
    const p = lg.positions[id];
    minC = Math.min(minC, p.col); maxC = Math.max(maxC, p.col);
    minR = Math.min(minR, p.row); maxR = Math.max(maxR, p.row);
  });
  ok(Math.abs(minC) < 1e-6 && Math.abs(minR) < 1e-6, 'logical layout is 0-based');
  ok(lg.cols >= maxC + 1 - 1e-6 && lg.rows >= maxR + 1 - 1e-6, 'cols/rows cover the extent');

  // 3) PARTIAL grid (the §2.1 first-class case) -> logical, never physical
  const PARTIAL = [
    { id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '' },
    { id: 'q3', grid_location: '1,0' }, { id: 'q4', grid_location: '1,1' },
  ];
  const lp = TG.layoutFor(PARTIAL, [['q1', 'q2'], ['q2', 'q3'], ['q3', 'q4']]);
  ok(lp.mode === 'logical', 'PARTIAL grid is NEVER physical (strict gate honored)');
  ok(Object.keys(lp.positions).length === 4, 'partial-grid chip still places every node (from pairs alone)');

  // 4) no grid AND no pairs -> none (no map; the honest line is the caller's job)
  eq(TG.layoutFor(NOGRID, []).mode, 'none', 'no grid + no pairs -> none');
  eq(TG.layoutFor(NOGRID, null).mode, 'none', 'no grid + null edges -> none');
  // partial grid + no pairs is STILL none — half-fabricated maps do not exist
  eq(TG.layoutFor(PARTIAL, []).mode, 'none', 'partial grid + no pairs -> none');
  // edges that reference only unknown ids give no usable connectivity
  eq(TG.layoutFor(NOGRID, [['qX', 'qY']]).mode, 'none', 'edges to unknown ids only -> none');
  // self-loops are not connectivity
  eq(TG.layoutFor(NOGRID, [['q1', 'q1']]).mode, 'none', 'self-loops only -> none');

  // 5) empty / single-node chips
  eq(TG.layoutFor([], []).mode, 'none', 'empty nodes -> none');
  eq(TG.layoutFor([{ id: 'q1', grid_location: '0,0' }], []).mode, 'physical', 'single placed node -> physical');
  eq(TG.layoutFor([{ id: 'q1' }], []).mode, 'none', 'single unplaced node -> none');

  // 6) isolated nodes ride a visually separate strip BELOW the connected part
  const ISO = [{ id: 'q1' }, { id: 'q2' }, { id: 'q3' }, { id: 'q4' }, { id: 'qz' }];
  const li = TG.layoutFor(ISO, [['q1', 'q2'], ['q2', 'q3'], ['q3', 'q4'], ['q4', 'q1']]);
  ok(li.mode === 'logical', 'isolated node does not break logical mode');
  let connMaxRow = -Infinity;
  ['q1', 'q2', 'q3', 'q4'].forEach(function (id) { connMaxRow = Math.max(connMaxRow, li.positions[id].row); });
  ok(li.positions.qz.row >= connMaxRow + 0.9,
     'isolated node sits on a separate strip below (row ' + li.positions.qz.row.toFixed(2) +
     ' vs connected max ' + connMaxRow.toFixed(2) + ')');

  // 7) deterministic: same input -> byte-identical output
  eq(TG.layoutFor(NOGRID, LAT_EDGES), TG.layoutFor(NOGRID, LAT_EDGES), 'layoutFor is deterministic');

  // 8) edge-shape agnostic: object edges and array pairs give the SAME layout
  eq(TG.layoutFor(NOGRID, LAT_PAIRS), lg, 'array-pair edges == object edges');

  // 9) CR chips carry BOTH directions as separate pairs — anti-parallel
  // duplicates must not change the layout
  const both = LAT_EDGES.concat(LAT_EDGES.map(function (e) { return { source: e.target, target: e.source }; }));
  eq(TG.layoutFor(NOGRID, both), lg, 'anti-parallel duplicate edges are deduped');

  // 10) the label contract: the note every logical render must show
  ok(typeof TG.LOGICAL_LAYOUT_NOTE === 'string' && /logical layout/i.test(TG.LOGICAL_LAYOUT_NOTE),
     'LOGICAL_LAYOUT_NOTE exposed and names the logical layout');
  ok(/physical position/i.test(TG.LOGICAL_LAYOUT_NOTE), 'LOGICAL_LAYOUT_NOTE says positions are not physical');
}

// Emit the map for the Python cross-check vs run_build._quam_pair_id.
const map = {};
PAIRS.forEach(function (p) { map[p] = TG.quamPairId(p); });
console.log('__QUAMPAIRID__ ' + JSON.stringify(map));

if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
console.log('topo_graph_selfcheck: all checks passed');
