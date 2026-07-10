/* jsdom behavioral check for the Generate-Config chip-topology board
 * (wiring-grid.js): place qubits (grid_location), draw + remove arbitrary edges
 * (qubit_pairs), drag to move, zone resize, and dropdown sync via onChange.
 *
 * Run: node tests/generate_topoboard_selfcheck.cjs   (driven by test_generate_topoboard.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) { console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1); });

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
function js(n) { return fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/static/', n), 'utf8'); }
const GEN = js('generate.js'), TOPO = js('topo-graph.js'), WIRE = js('wiring-grid.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="table-pane">' + HTML + '</div></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN).call(win);
  new win.Function(TOPO).call(win);
  new win.Function(WIRE).call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function clickEl(win, el) {
  el.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }));
  el.dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true, clientX: 0, clientY: 0 }));
}
function cell(win, col, row) {
  return win.document.querySelector('.gen-topo-cell[data-col="' + col + '"][data-row="' + row + '"]');
}
function stone(win, qid) {
  return win.document.querySelector('.gen-topo-stone[data-qubit="' + qid + '"]');
}

(function main() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '6');
  G.state.spec.qubit_pairs = [];   // start from a clean slate (count auto-chains)
  G.state.pairsTouched = true;

  // Open the topology board.
  const details = win.document.getElementById('gen-topo');
  details.open = true;
  details.dispatchEvent(new win.Event('toggle'));

  // The board rendered a grid; default zone is near-square for 6 qubits.
  const grid = win.document.querySelector('.gen-topo-grid');
  ok(!!grid, 'board grid rendered');
  const z = win.WiringGrid.zone();
  ok(z.cols >= 3 && z.rows >= 3, 'default zone holds 6 qubits (' + z.cols + 'x' + z.rows + ')');

  // Place q1 at (1,1) and q2 at (2,1) by clicking empty cells (next-unplaced).
  ok(win.WiringGrid._nextUnplaced() === 'q1', 'first unplaced is q1');
  clickEl(win, cell(win, 1, 1));
  ok(JSON.stringify(win.WiringGrid._cellOf('q1')) === JSON.stringify({ col: 1, row: 1 }), 'q1 placed at (1,1)');
  clickEl(win, cell(win, 2, 1));
  ok(JSON.stringify(win.WiringGrid._cellOf('q2')) === JSON.stringify({ col: 2, row: 1 }), 'q2 placed at (2,1)');
  // grid_location stored on populate.qubit (QUAM convention, raw).
  ok(G.state.spec.populate.qubit.q1.grid_location === '1,1', 'q1 grid_location written to populate.qubit');

  // Draw an edge q1-q2 (click stone q1, then stone q2).
  clickEl(win, stone(win, 'q1'));
  clickEl(win, stone(win, 'q2'));
  ok(win.WiringGrid._cellOf('q1') && G.state.spec.qubit_pairs.some(function (p) {
    return (p[0] === 'q1' && p[1] === 'q2') || (p[0] === 'q2' && p[1] === 'q1');
  }), 'edge q1-q2 added to qubit_pairs');

  // onChange synced the dropdown pair list (renderPairs ran).
  const pairList = win.document.getElementById('gen-pair-list');
  ok(pairList && pairList.querySelectorAll('.gen-pair-row').length >= 1, 'dropdown pair list synced from board');

  // Remove the edge by clicking its hit-line.
  const edge = win.document.querySelector('.gen-topo-edge-hit');
  ok(!!edge, 'edge line rendered');
  clickEl(win, edge);
  ok(!G.state.spec.qubit_pairs.some(function (p) {
    return (p[0] === 'q1' && p[1] === 'q2') || (p[0] === 'q2' && p[1] === 'q1');
  }), 'edge q1-q2 removed on edge click');

  // Drag q1 from (1,1) to an empty cell (3,2).
  const s1 = stone(win, 'q1');
  s1.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }));
  win.document.dispatchEvent(new win.MouseEvent('mousemove', { bubbles: true, clientX: 50, clientY: 50 }));
  cell(win, 3, 2).dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true, clientX: 50, clientY: 50 }));
  ok(JSON.stringify(win.WiringGrid._cellOf('q1')) === JSON.stringify({ col: 3, row: 2 }), 'q1 moved to (3,2) by drag');

  // Zone resize.
  setInput(win, 'gen-topo-cols', '7');
  setInput(win, 'gen-topo-rows', '6');
  const z2 = win.WiringGrid.zone();
  ok(z2.cols === 7 && z2.rows === 6, 'zone resized to 7x6');
  ok(win.document.querySelectorAll('.gen-topo-cell').length === 42, '7x6 = 42 cells rendered');

  // topoZone persists into the draft.
  const before = new win.Event('htmx:beforeSwap');
  before.detail = { target: win.document.getElementById('table-pane') };
  win.document.dispatchEvent(before);
  const draft = JSON.parse(win.sessionStorage.getItem('quam_generate_draft') || '{}');
  ok(draft.topoZone && draft.topoZone.cols === 7 && draft.topoZone.rows === 6, 'topoZone persisted in draft');

  // ---- presets ----
  function allPlaced(W, qs) { return qs.every(function (q) { return W._cellOf(q); }); }
  function hasEdge(pairs, a, b) { return pairs.some(function (p) { return (p[0] === a && p[1] === b) || (p[0] === b && p[1] === a); }); }
  function freshChip() {
    const w = makeWorld(); const g = w.QuamGen; g.init(); g.goToStep(4);
    setInput(w, 'gen-qubit-count', '6');
    const d = w.document.getElementById('gen-topo'); d.open = true; d.dispatchEvent(new w.Event('toggle'));
    return w;
  }
  const QS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6'];

  { // Chain: all placed, consecutive edges, n-1 of them
    const w = freshChip(); w.WiringGrid.preset('chain');
    const pairs = w.QuamGen.state.spec.qubit_pairs;
    ok(allPlaced(w.WiringGrid, QS), 'chain: all 6 placed');
    ok(pairs.length === 5, 'chain: 5 edges (got ' + pairs.length + ')');
    for (let i = 0; i < 5; i++) ok(hasEdge(pairs, QS[i], QS[i + 1]), 'chain: edge ' + QS[i] + '-' + QS[i + 1]);
  }
  { // Ring: cyclic — includes the closing edge q6-q1
    const w = freshChip(); w.WiringGrid.preset('ring');
    const pairs = w.QuamGen.state.spec.qubit_pairs;
    ok(allPlaced(w.WiringGrid, QS), 'ring: all 6 placed');
    ok(pairs.length === 6, 'ring: 6 cyclic edges (got ' + pairs.length + ')');
    ok(hasEdge(pairs, 'q6', 'q1'), 'ring: closing edge q6-q1');
  }
  { // Star: hub q1 connected to all others, n-1 edges all touching q1
    const w = freshChip(); w.WiringGrid.preset('star');
    const pairs = w.QuamGen.state.spec.qubit_pairs;
    ok(allPlaced(w.WiringGrid, QS), 'star: all 6 placed');
    ok(pairs.length === 5, 'star: 5 spokes (got ' + pairs.length + ')');
    ok(pairs.every(function (p) { return p[0] === 'q1' || p[1] === 'q1'; }), 'star: every edge touches the hub q1');
    for (let i = 1; i < 6; i++) ok(hasEdge(pairs, 'q1', QS[i]), 'star: spoke q1-' + QS[i]);
  }
  { // Grid-NN: 4-adjacency. unique cells; edges only between grid neighbours.
    const w = freshChip(); w.WiringGrid.preset('grid');
    const pairs = w.QuamGen.state.spec.qubit_pairs;
    ok(allPlaced(w.WiringGrid, QS), 'grid: all 6 placed');
    ok(pairs.length >= 1, 'grid: has NN edges');
    ok(pairs.every(function (p) {
      const a = w.WiringGrid._cellOf(p[0]), b = w.WiringGrid._cellOf(p[1]);
      return a && b && (Math.abs(a.col - b.col) + Math.abs(a.row - b.row)) === 1;
    }), 'grid: every edge is a Manhattan-distance-1 neighbour');
    // unique cells
    const cells = {}; let dup = false;
    QS.forEach(function (q) { const c = w.WiringGrid._cellOf(q); const k = c.col + ',' + c.row; if (cells[k]) dup = true; cells[k] = 1; });
    ok(!dup, 'grid: no two qubits share a cell');
  }

  // ---- ID-gate: delete -> hole -> renumber to contiguous ----
  {
    const w = freshChip();
    // Give the chip an MW-FEM + cross-resonance gate so the step-4 line/pair guards
    // pass and execution reaches the ID-gate (the part under test).
    w.QuamGen.state.spec.instruments.controllers = [{ con: 'con1', fems: [{ slot: 1, fem: 'mw' }] }];
    w.QuamGen.state.pairGate = 'cr';
    w.WiringGrid.preset('chain');                 // 6 qubits, edges q1..q6
    // populate a per-qubit + a per-pair value to prove they survive the remap
    w.QuamGen.state.spec.populate.qubit = w.QuamGen.state.spec.populate.qubit || {};
    w.QuamGen.state.spec.populate.qubit.q4 = { RF_freq: 5.4e9, grid_location: w.QuamGen.state.spec.populate.qubit.q4.grid_location };
    w.QuamGen.state.spec.populate.pairs = { 'q4-q5': { cz_amplitude: 0.22 } };

    w.WiringGrid._removeQubit('q3');              // create a hole
    const qs = w.QuamGen.state.spec.qubits;
    ok(JSON.stringify(qs) === JSON.stringify(['q1', 'q2', 'q4', 'q5', 'q6']), 'delete q3 -> hole (' + qs.join(',') + ')');
    ok(!w.QuamGen.state.spec.qubit_pairs.some(function (p) { return p[0] === 'q3' || p[1] === 'q3'; }), 'incident edges of q3 removed');

    // The gate blocks proceeding while holes exist; with confirm() stubbed true it
    // renumbers and advances. (makeWorld's confirm returns true.)
    w.QuamGen.goToStep(4);
    w.QuamGen.tryNext();
    const qs2 = w.QuamGen.state.spec.qubits;
    ok(JSON.stringify(qs2) === JSON.stringify(['q1', 'q2', 'q3', 'q4', 'q5']), 'gate renumbered to contiguous (' + qs2.join(',') + ')');
    ok(w.QuamGen.state.step === 5, 'gate advanced to step 5 after renumber');

    // Data followed the remap: old q4 -> q3 (per-qubit value), old q4-q5 -> q3-q4.
    ok(w.QuamGen.state.spec.populate.qubit.q3 && w.QuamGen.state.spec.populate.qubit.q3.RF_freq === 5.4e9,
       'per-qubit populate followed the renumber (q4->q3)');
    ok(w.QuamGen.state.spec.populate.pairs['q3-q4'] && w.QuamGen.state.spec.populate.pairs['q3-q4'].cz_amplitude === 0.22,
       'per-pair populate key followed the renumber (q4-q5 -> q3-q4)');
    ok(!w.QuamGen.state.spec.qubits.some(function (q, i) { return q !== 'q' + (i + 1); }), 'qubit ids now contiguous');
  }

  // ---- ID-gate: declining the renumber BLOCKS (step does not advance) ----
  {
    const w = freshChip();
    w.QuamGen.state.spec.instruments.controllers = [{ con: 'con1', fems: [{ slot: 1, fem: 'mw' }] }];
    w.QuamGen.state.pairGate = 'cr';
    w.WiringGrid.preset('chain');
    w.WiringGrid._removeQubit('q3');              // hole at q3
    w.confirm = function () { return false; };    // user declines the renumber
    w.QuamGen.goToStep(4);
    w.QuamGen.tryNext();
    ok(w.QuamGen.state.step === 4, 'declined renumber keeps the user on step 4');
    ok(JSON.stringify(w.QuamGen.state.spec.qubits) === JSON.stringify(['q1', 'q2', 'q4', 'q5', 'q6']),
       'declined renumber leaves the hole intact (no silent mutation)');
    const msg = w.document.getElementById('gen-message');
    ok(msg && /contiguous/i.test(msg.textContent || ''), 'declined renumber shows the contiguity warning');
  }

  // ---- edge styling + legend reflect the chip type's 2-qubit gate ----
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');                 // edges present
    const legend = function () { return (w.document.getElementById('gen-topo-legend').textContent || ''); };

    w.QuamGen.state.pairGate = 'cr'; w.WiringGrid.refresh();
    ok(w.document.querySelector('.gen-topo-arrow'), 'CR: directional arrowheads drawn');
    ok(!w.document.querySelector('.gen-topo-edge--dashed'), 'CR: edges not dashed');
    ok(/cross-resonance/i.test(legend()), 'CR: legend names cross-resonance (' + legend().slice(0, 30) + '…)');

    w.QuamGen.state.pairGate = 'cz_fixed'; w.WiringGrid.refresh();
    ok(w.document.querySelector('.gen-topo-edge--dashed'), 'CZ fixed: edges dashed');
    ok(!w.document.querySelector('.gen-topo-arrow'), 'CZ fixed: no arrowheads');
    ok(/fixed coupler/i.test(legend()), 'CZ fixed: legend names fixed coupler');

    w.QuamGen.state.pairGate = 'cz_tunable'; w.WiringGrid.refresh();
    ok(w.document.querySelector('.gen-topo-coupler'), 'CZ tunable: coupler dot drawn');
    ok(!w.document.querySelector('.gen-topo-edge--dashed'), 'CZ tunable: edges solid');
    ok(/tunable coupler/i.test(legend()), 'CZ tunable: legend names tunable coupler');
  }

  // ---- read-only topology mirror on the Populate step (step 6) ----
  {
    const w = freshChip();
    w.QuamGen.state.spec.instruments.controllers = [{ con: 'con1', fems: [{ slot: 1, fem: 'mw' }] }];
    w.QuamGen.state.pairGate = 'cr';
    w.WiringGrid.preset('chain');                 // 6 placed, 5 pairs
    w.localStorage.setItem('quam_pop_topo_open', '1');   // want it open
    w.QuamGen.goToStep(6);

    const details = w.document.getElementById('gen-pop-topo');
    ok(details && !details.hidden, 'populate topology panel visible when qubits are placed');
    const cap = w.document.getElementById('gen-pop-topo-caption').textContent || '';
    ok(/6\/6 placed/.test(cap) && /5 pairs/.test(cap), 'populate topo caption (' + cap + ')');
    const leg = w.document.getElementById('gen-pop-topo-legend').textContent || '';
    ok(/cross-resonance/i.test(leg), 'populate topo legend reflects the gate');
    const board = w.document.getElementById('gen-pop-topo-board');
    ok((board.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 6, 'populate topo board renders 6 stones');
    ok(/gen-topo-arrow/.test(board.innerHTML), 'populate topo board shows CR arrowheads');
  }

  // ---- populate topology hidden when NOTHING is placed ----
  {
    const w = makeWorld(); const g = w.QuamGen; g.init(); g.goToStep(4);
    setInput(w, 'gen-qubit-count', '3');           // qubits exist but never placed on the board
    w.QuamGen.state.spec.instruments.controllers = [{ con: 'con1', fems: [{ slot: 1, fem: 'mw' }] }];
    w.QuamGen.goToStep(6);
    const details = w.document.getElementById('gen-pop-topo');
    ok(details && details.hidden, 'populate topology panel hidden when nothing is placed');
  }

  // ===================== audit-fix regression guards =====================
  function buildable(w) {   // give a freshChip MW-FEM + CR so step-4 line/pair guards pass
    w.QuamGen.state.spec.instruments.controllers = [{ con: 'con1', fems: [{ slot: 1, fem: 'mw' }] }];
    w.QuamGen.state.pairGate = 'cr';
  }

  // FIX: step-rail forward jump must NOT bypass the step-4 ID-gate (was a P0 — a
  // hole-y/partial spec could reach the build via a step-number click).
  {
    const w = freshChip(); buildable(w);
    w.WiringGrid.preset('chain');
    w.WiringGrid._removeQubit('q3');             // hole
    w.confirm = function () { return false; };    // decline the renumber
    w.QuamGen.goToStep(4);
    // simulate a step-rail click to step 8 via the guarded path
    const steps = w.document.querySelectorAll('#gen-steps li');
    let liEight = null; steps.forEach(function (li) { if (Number(li.dataset.step) === 8) liEight = li; });
    liEight.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    ok(w.QuamGen.state.step === 4, 'step-rail forward jump blocked by the ID-gate (stayed on 4)');
    ok(JSON.stringify(w.QuamGen.state.spec.qubits) === JSON.stringify(['q1', 'q2', 'q4', 'q5', 'q6']),
       'declined renumber via step-rail left the hole intact');
  }
  { // backward step-rail jump is always free
    const w = freshChip(); buildable(w); w.WiringGrid.preset('chain');
    w.QuamGen.goToStep(4);
    const steps = w.document.querySelectorAll('#gen-steps li');
    let liOne = null; steps.forEach(function (li) { if (Number(li.dataset.step) === 1) liOne = li; });
    liOne.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    ok(w.QuamGen.state.step === 1, 'backward step-rail jump is free');
  }

  // FIX: lowering qubit count prunes populate; lowering then raising does NOT
  // resurrect stale physics/placement (was a P0/P1 data issue).
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');                 // q1..q6 placed
    w.QuamGen.state.spec.populate.qubit.q5 = { RF_freq: 9.9e9, grid_location: '2,1' };
    setInput(w, 'gen-qubit-count', '4');          // drop to 4
    ok(!('q5' in w.QuamGen.state.spec.populate.qubit), 'count↓ prunes orphaned populate.qubit.q5');
    ok(!('q6' in w.QuamGen.state.spec.populate.qubit), 'count↓ prunes orphaned populate.qubit.q6');
    setInput(w, 'gen-qubit-count', '6');          // back up to 6
    const q5 = w.QuamGen.state.spec.populate.qubit.q5;
    ok(!q5 || q5.RF_freq !== 9.9e9, 'count↑ does NOT resurrect stale q5 physics');
    ok(!q5 || q5.grid_location !== '2,1', 'count↑ does NOT resurrect stale q5 placement');
  }

  // FIX: partial placement is blocked at step 4 (some but not all placed) — was a
  // consistency bug (unplaced qubits get snake-fill defaults that can collide).
  {
    const w = freshChip(); buildable(w);
    // place only q1, q2 (click two cells); leave q3..q6 unplaced
    const c = function (col, row) { return w.document.querySelector('.gen-topo-cell[data-col="' + col + '"][data-row="' + row + '"]'); };
    clickEl(w, c(0, 0)); clickEl(w, c(1, 0));
    w.QuamGen.state.spec.qubit_pairs = [];        // no pairs, isolate the placement gate
    w.QuamGen.goToStep(4);
    w.QuamGen.tryNext();
    ok(w.QuamGen.state.step === 4, 'partial placement blocks leaving step 4');
    const msg = w.document.getElementById('gen-message').textContent || '';
    ok(/aren't placed/i.test(msg), 'partial-placement message names the gap (' + msg.slice(0, 40) + '…)');
  }
  { // zero placed (pure form flow) is allowed
    const w = makeWorld(); const g = w.QuamGen; g.init(); g.goToStep(4);
    setInput(w, 'gen-qubit-count', '3'); buildable(w);
    w.QuamGen.state.spec.qubit_pairs = [];
    w.QuamGen.tryNext();
    ok(w.QuamGen.state.step === 5, 'zero-placed (form flow) is allowed past step 4');
  }

  // FIX: a preset on a non-empty board confirms first; declining is a no-op.
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');                 // now the board is non-empty
    const before = JSON.stringify(w.QuamGen.state.spec.qubit_pairs);
    w.confirm = function () { return false; };     // decline the overwrite
    w.WiringGrid.preset('ring');
    ok(JSON.stringify(w.QuamGen.state.spec.qubit_pairs) === before, 'declined preset leaves layout untouched');
    w.confirm = function () { return true; };
    w.WiringGrid.preset('ring');
    ok(w.QuamGen.state.spec.qubit_pairs.length === 6, 'accepted preset overwrites to ring (6 edges)');
  }

  // FIX (perf): placement/drag must NOT rebuild the pairs dropdown (was O(N²)).
  {
    const w = freshChip();
    let pairListBuilds = 0;
    const list = w.document.getElementById('gen-pair-list');
    // observe innerHTML writes to the pair list as a proxy for renderPairs running
    const origDesc = Object.getOwnPropertyDescriptor(w.HTMLElement.prototype, 'innerHTML');
    Object.defineProperty(list, 'innerHTML', {
      configurable: true,
      get() { return origDesc.get.call(this); },
      set(v) { pairListBuilds++; return origDesc.set.call(this, v); },
    });
    const c = function (col, row) { return w.document.querySelector('.gen-topo-cell[data-col="' + col + '"][data-row="' + row + '"]'); };
    clickEl(w, c(0, 0)); clickEl(w, c(1, 0)); clickEl(w, c(2, 0));   // 3 placements
    ok(pairListBuilds === 0, 'placement does not rebuild the pairs dropdown (' + pairListBuilds + ' builds)');
    clickEl(w, stone(w, 'q1')); clickEl(w, stone(w, 'q2'));          // draw an edge
    ok(pairListBuilds > 0, 'edge toggle DOES rebuild the pairs dropdown');
  }

  // FIX: zone never shrinks below placed qubits (stones can't fall off-board).
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');                 // places qubits across several cells
    w.WiringGrid.setZone(2, 2);                    // try to shrink absurdly small
    const z = w.WiringGrid.zone();
    // every placed qubit must still be inside the (clamped) zone
    let allInside = true;
    ['q1', 'q2', 'q3', 'q4', 'q5', 'q6'].forEach(function (q) {
      const cell = w.WiringGrid._cellOf(q);
      if (cell && (cell.col >= z.cols || cell.row >= z.rows)) allInside = false;
    });
    ok(allInside, 'zone clamps to keep every placed qubit on-board (zone ' + z.cols + 'x' + z.rows + ')');
  }

  // FIX: deleting a qubit prunes its populate.pairs entry (no orphaned pair physics).
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');
    w.QuamGen.state.spec.populate.pairs = { 'q2-q3': { cz_amplitude: 0.3 }, 'q4-q5': { cz_amplitude: 0.4 } };
    w.WiringGrid._removeQubit('q3');
    ok(!('q2-q3' in w.QuamGen.state.spec.populate.pairs), 'delete prunes incident populate.pairs key q2-q3');
    ok('q4-q5' in w.QuamGen.state.spec.populate.pairs, 'delete keeps unrelated populate.pairs key q4-q5');
  }

  // FIX: the Renumber button shows only while ids have gaps.
  {
    const w = freshChip();
    w.WiringGrid.preset('chain');
    const btn = w.document.getElementById('gen-topo-renumber');
    ok(btn.hidden, 'Renumber button hidden when ids are contiguous');
    w.WiringGrid._removeQubit('q3');              // create a gap -> onChange syncs controls
    ok(!btn.hidden, 'Renumber button shown when a gap exists');
    btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    ok(JSON.stringify(w.QuamGen.state.spec.qubits) === JSON.stringify(['q1', 'q2', 'q3', 'q4', 'q5']),
       'Renumber button renumbers to contiguous');
    ok(btn.hidden, 'Renumber button hidden again after renumber');
  }

  // FIX: populate mirror has no stale-closure — collapse/reopen reflects deletes.
  {
    const w = freshChip(); buildable(w);
    w.WiringGrid.preset('chain');
    w.localStorage.setItem('quam_pop_topo_open', '1');
    w.QuamGen.goToStep(6);
    let board = w.document.getElementById('gen-pop-topo-board');
    ok((board.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 6, 'populate mirror shows 6 before delete');
    // go back, delete a qubit, return
    w.QuamGen.goToStep(4);
    w.WiringGrid._removeQubit('q6');
    w.confirm = function () { return true; };
    w.QuamGen.goToStep(6);
    board = w.document.getElementById('gen-pop-topo-board');
    ok((board.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 5, 'populate mirror reflects the delete (5)');
    // collapse + reopen must NOT redraw a stale 6-stone topology
    const details = w.document.getElementById('gen-pop-topo');
    details.open = false; details.dispatchEvent(new w.Event('toggle'));
    details.open = true; details.dispatchEvent(new w.Event('toggle'));
    board = w.document.getElementById('gen-pop-topo-board');
    ok((board.innerHTML.match(/gen-topo-stone-ro/g) || []).length === 5, 'reopen redraws CURRENT topology, not a stale closure');
  }

  if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
  console.log('generate_topoboard_selfcheck: all checks passed');
})();
