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

// Emit the map for the Python cross-check vs run_build._quam_pair_id.
const map = {};
PAIRS.forEach(function (p) { map[p] = TG.quamPairId(p); });
console.log('__QUAMPAIRID__ ' + JSON.stringify(map));

if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
console.log('topo_graph_selfcheck: all checks passed');
