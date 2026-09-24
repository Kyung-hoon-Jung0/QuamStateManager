/* jsdom behavioral check for the wizard's port-label CSV import (docs/54)
 * around its two QA findings:
 *
 *  F20 (confirm before validation): the "Importing the CSV replaces the
 *    current qubits…" confirm ran BEFORE the parse, so a CSV that could not
 *    be read asked to replace the chip and then failed. Now: a bad CSV is
 *    refused with zero confirms; a good one confirms, then applies; a
 *    declined confirm applies nothing.
 *  generate-r2-30 (Ctrl+Z after an import): the import never touched the
 *    wizard undo stack, so Ctrl+Z popped an older, unrelated field edit (the
 *    naming select), replayed the gen-chip-arch change the import itself
 *    dispatched, or restored a pre-import board-deleted qubit into the
 *    imported chip. Now the import is a barrier: Ctrl+Z says it can't undo
 *    it and changes nothing; the board's own delete-undo stack is emptied;
 *    edits made after the import still undo one at a time.
 *
 * Run: node tests/generate_csv_import_selfcheck.cjs
 *      (driven by tests/test_generate_csv_import.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) {
  console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1);
});

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
function js(n) {
  return fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', n), 'utf8');
}
const GEN = js('generate.js'), TOPO = js('topo-graph.js'), WIRE = js('wiring-grid.js');

let fails = 0, checks = 0;
function ok(c, m) { checks++; if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const GOOD = {
  ok: true,
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                 opx_plus: [], octaves: [] },
  qubits: ['q0', 'q1', 'q2'],
  grid: { q0: '0,0', q1: '1,0', q2: '2,0' },
  qubit_pairs: [['q0', 'q1'], ['q1', 'q0'], ['q1', 'q2'], ['q2', 'q1']],
  pins: {},
  feedlines: {},
  warnings: []
};
const BAD = { ok: false, errors: ['missing CSV columns: qubit, port'] };

function makeWorld(csvResponse) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.__confirms = []; win.__alerts = []; win.__toasts = []; win.__posts = 0;
  win.__confirmAnswer = true;
  win.confirm = function (m) { win.__confirms.push(String(m)); return win.__confirmAnswer; };
  win.alert = function (m) { win.__alerts.push(String(m)); };
  win.showToast = function (m) { win.__toasts.push(String(m)); };
  win.fetch = function (url) {
    if (String(url).indexOf('/generate/import-port-csv') === 0) {
      win.__posts++;
      const body = JSON.parse(JSON.stringify(csvResponse));
      return win.Promise.resolve({ json: function () { return win.Promise.resolve(body); } });
    }
    return new win.Promise(function () {});
  };
  new win.Function(GEN).call(win);
  new win.Function(TOPO).call(win);
  new win.Function(WIRE).call(win);
  return win;
}

function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
// A real user edit: focus (the undo stack's snapshot), then commit.
function userEdit(win, el, value) {
  el.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
  setInput(win, el, value);
}

// A 3-qubit flux-tunable chip on step 4 (q1..q3).
function buildChip(win) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems =
    [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  return G;
}

// The file picker's change, with a real File read by jsdom's FileReader.
function pickCsv(win, text) {
  const inp = win.document.getElementById('gen-csv-file');
  const f = new win.File([text], 'ports.csv', { type: 'text/csv' });
  Object.defineProperty(inp, 'files', { configurable: true, get: function () { return [f]; } });
  inp.dispatchEvent(new win.Event('change', { bubbles: true }));
}

(async function main() {
  // ---- C1 (F20): a CSV that fails to parse never asks to replace the chip --
  {
    const win = makeWorld(BAD);
    const G = buildChip(win);
    pickCsv(win, 'a,b,c\n1,2,3\n');
    for (let i = 0; i < 10 && !win.__alerts.length; i++) await tick();
    ok(win.__posts === 1, 'C1: the CSV was sent to the parser');
    ok(win.__confirms.length === 0,
       'C1: a bad CSV asks NO replace-the-chip confirm (got ' +
       JSON.stringify(win.__confirms) + ')');
    ok(win.__alerts.length === 1 && win.__alerts[0].indexOf('CSV import failed') === 0,
       'C1: one "CSV import failed" alert');
    ok(G.state.spec.qubits.join(',') === 'q1,q2,q3', 'C1: the chip is untouched');
  }

  // ---- C2 (F20): a good CSV confirms (chip non-empty), then applies --------
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    pickCsv(win, 'qubit,port\nq0,1\n');
    for (let i = 0; i < 10 && G.state.spec.qubits[0] !== 'q0'; i++) await tick();
    ok(win.__confirms.length === 1 &&
       win.__confirms[0].indexOf('replaces the current qubits') >= 0,
       'C2: a good CSV asks the replace confirm once');
    ok(G.state.spec.qubits.join(',') === 'q0,q1,q2', 'C2: the import applied');
    ok(win.__alerts.length === 0, 'C2: no alert');
  }

  // ---- C3 (F20): declining the confirm applies nothing ---------------------
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    win.__confirmAnswer = false;
    pickCsv(win, 'qubit,port\nq0,1\n');
    for (let i = 0; i < 10 && !win.__confirms.length; i++) await tick();
    await tick();
    ok(win.__confirms.length === 1, 'C3: the confirm was asked');
    ok(G.state.spec.qubits.join(',') === 'q1,q2,q3', 'C3: declined — chip untouched');
  }

  // ---- U1 (r2-30): Ctrl+Z after an import never undoes an older field ------
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    const nm = win.document.getElementById('gen-naming-preset');
    userEdit(win, nm, 'zero_based');
    ok(G.state.naming.preset === 'zero_based', 'U1: naming edit landed');
    G._test.applyPortCsv(JSON.parse(JSON.stringify(GOOD)));
    const toasts0 = win.__toasts.length;
    ok(win._wizUndo.tryUndo() === true, 'U1: Ctrl+Z consumed by the wizard');
    ok(nm.value === 'zero_based' && G.state.naming.preset === 'zero_based',
       'U1: the pre-import naming edit is NOT undone (got ' + nm.value + ')');
    ok(G.state.spec.qubits.join(',') === 'q0,q1,q2' && G.state.pairGate === 'cr',
       'U1: the imported chip stays');
    const t = win.__toasts.slice(toasts0).join(' | ');
    ok(t.indexOf("can't undo the port-CSV import") >= 0,
       'U1: the toast says the import cannot be undone (got "' + t + '")');
    // Pressing again stays at the barrier.
    win._wizUndo.tryUndo();
    ok(G.state.naming.preset === 'zero_based', 'U1: a second Ctrl+Z still stops');
  }

  // ---- U2 (r2-30): the arch change the import dispatched is not undoable ---
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    const arch = win.document.getElementById('gen-chip-arch');
    // The user once focused the arch select (flux arch) — __wizPrev is set.
    arch.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    ok(arch.value !== 'fixed_frequency', 'U2: chip starts on a flux arch');
    G._test.applyPortCsv(JSON.parse(JSON.stringify(GOOD)));
    win._wizUndo.tryUndo();
    ok(G.state.chipArch === 'fixed_frequency' && arch.value === 'fixed_frequency',
       'U2: Ctrl+Z never re-applies the pre-import arch onto the CR chip (got ' +
       G.state.chipArch + ')');
  }

  // ---- U3 (r2-30): a pre-import board delete is never restored into it -----
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    win.WiringGrid._removeQubit('q3');
    ok(win.WiringGrid.hasUndo() === true, 'U3: board delete recorded');
    G._test.applyPortCsv(JSON.parse(JSON.stringify(GOOD)));
    ok(win.WiringGrid.hasUndo() === false, 'U3: the board delete-undo stack is emptied');
    win._wizUndo.tryUndo();
    ok(G.state.spec.qubits.join(',') === 'q0,q1,q2',
       'U3: Ctrl+Z restores no pre-import qubit (got ' + G.state.spec.qubits.join(',') + ')');
  }

  // ---- U4 (r2-30): edits AFTER the import still undo, then the barrier -----
  {
    const win = makeWorld(GOOD);
    const G = buildChip(win);
    G._test.applyPortCsv(JSON.parse(JSON.stringify(GOOD)));
    const nm = win.document.getElementById('gen-naming-preset');
    const before = nm.value;
    userEdit(win, nm, 'grid');
    win._wizUndo.tryUndo();
    ok(nm.value === before && G.state.naming.preset === before,
       'U4: a post-import edit undoes (got ' + nm.value + ')');
    const toasts0 = win.__toasts.length;
    win._wizUndo.tryUndo();
    ok(win.__toasts.slice(toasts0).join(' ').indexOf('port-CSV import') >= 0,
       'U4: the next Ctrl+Z reaches the barrier');
  }

  if (fails) { console.error(fails + ' of ' + checks + ' check(s) failed'); process.exit(1); }
  console.log('generate_csv_import_selfcheck: all ' + checks + ' checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
