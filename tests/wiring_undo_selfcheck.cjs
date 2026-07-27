/* jsdom behavioral check for the Generate-Config wizard board's DELETE-UNDO
 * (supercritical feedback: an accidental Del on an armed stone irrecoverably
 * destroyed the qubit + its pairs + its physics — users restarted the wizard).
 *
 * Pins:
 *  A. removeQubit snapshots everything it destroys; undoDelete restores the
 *     qubit at its original position with placement, physics, incident pairs
 *     (at their original list positions) and the pair-physics buckets.
 *  B. Stacked deletes undo in LIFO order, and a pair whose OTHER member is
 *     still deleted is NOT resurrected until that member returns.
 *  C. undoDelete on an empty stack is a null no-op.
 *
 * Run:  node tests/wiring_undo_selfcheck.cjs   (driven by test_generate_feedback_r4.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'wiring-grid.js');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
const w = dom.window;

function mkState() {
  return {
    pairsTouched: false,
    topoZone: { cols: 4, rows: 4 },
    spec: {
      qubits: ['q1', 'q2', 'q3', 'q4'],
      qubit_pairs: [['q1', 'q2'], ['q2', 'q3'], ['q3', 'q4']],
      populate: {
        qubit: {
          q1: { grid_location: '0,0', f_01: 5.0e9 },
          q2: { grid_location: '1,0', f_01: 5.1e9 },
          q3: { grid_location: '2,0', f_01: 5.2e9 },
          q4: { grid_location: '3,0', f_01: 5.3e9 },
        },
        resonator: { q4: { readout_amplitude: 0.05 } },
        pairs: { 'q3-q4': { cz_amplitude: 0.2, cz_variant: 'SNZ' } },
      },
    },
  };
}

w.QuamGen = { state: mkState() };
w.eval(fs.readFileSync(SRC, 'utf8'));
const WG = w.WiringGrid;

let failures = 0;
function check(name, cond, detail) {
  if (cond) { console.log('  ok  ' + name); }
  else { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}
function spec() { return w.QuamGen.state.spec; }
function j(v) { return JSON.stringify(v); }

// ── A. single delete → undo restores everything bit-for-bit ────────────────
const before = JSON.parse(JSON.stringify(spec()));
WG._removeQubit('q4');
check('A1 q4 removed', spec().qubits.indexOf('q4') === -1, j(spec().qubits));
check('A2 incident pair dropped', j(spec().qubit_pairs) === j([['q1', 'q2'], ['q2', 'q3']]));
check('A3 physics dropped', !spec().populate.qubit.q4 && !spec().populate.resonator.q4);
check('A4 pair bucket dropped', !spec().populate.pairs['q3-q4']);
check('A5 hasUndo', WG.hasUndo() === true);

const restored = WG.undoDelete();
check('A6 undo returns qid', restored === 'q4');
check('A7 spec fully restored', j(spec()) === j(before),
      'diff: ' + j(spec()).slice(0, 200));
check('A8 stack empty again', WG.hasUndo() === false);

// ── B. stacked deletes: LIFO, partner-gated pair restore ───────────────────
WG._removeQubit('q4');
WG._removeQubit('q3');
check('B1 both gone', spec().qubits.indexOf('q3') === -1 && spec().qubits.indexOf('q4') === -1);

let r = WG.undoDelete();   // q3 first (LIFO)
check('B2 q3 back first', r === 'q3' && spec().qubits.indexOf('q3') >= 0);
check('B3 q2-q3 pair back', j(spec().qubit_pairs).indexOf(j(['q2', 'q3'])) >= 0);
check('B4 q3-q4 NOT resurrected (q4 still deleted)',
      j(spec().qubit_pairs).indexOf('q4') === -1, j(spec().qubit_pairs));
check('B5 q3-q4 bucket still absent', !spec().populate.pairs['q3-q4']);

r = WG.undoDelete();       // then q4
check('B6 q4 back', r === 'q4' && spec().qubits.indexOf('q4') >= 0);
check('B7 spec equals original after both undos', j(spec()) === j(before),
      'diff: ' + j(spec()).slice(0, 200));

// ── C. empty stack no-op ───────────────────────────────────────────────────
check('C1 empty-stack undo returns null', WG.undoDelete() === null);
check('C2 spec untouched by empty undo', j(spec()) === j(before));

if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
