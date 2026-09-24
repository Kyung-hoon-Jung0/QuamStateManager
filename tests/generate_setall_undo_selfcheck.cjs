// Behavioral check: Ctrl+Z after a Populate "Set all" restores each row's OWN
// previous value (QA regenerate-r2-23).
//
// The wizard undo restored the Set-all box's own previous text ("") and
// re-dispatched its change — which the Set-all handler reads as an explicit
// empty commit, so the whole column went BLANK (in regen the build then
// silently carried the old chip values while the table showed blanks).
//
// Pins:
//  S1. Set-all over differing per-row values, then undo: every row gets its
//      own pre-fill value back (spec + cell text); the toast names the column.
//  S2. two Set-alls undo LIFO.
//  S3. regenerate: the fill's populate-touched marks are withdrawn on undo
//      (a cell touched BEFORE the fill stays touched).
//  S4. absolute power mode: undoing an amp Set-all restores the amps AND the
//      port FSPs the re-allocation rewrote.
//  S5. an explicit empty Set-all commit still clears the column (docs/27),
//      and undoing it brings the column back.
//  S6. a per-cell edit still undoes through the generic path (no regression).
//
// Run: node tests/generate_setall_undo_selfcheck.cjs   (driven by test_generate_setall_undo.py)
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
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  win._toasts = [];
  win.showToast = function (m) { win._toasts.push(String(m)); };
  new win.Function(GEN_JS).call(win);
  return win;
}

// A real user commit: focus (the undo snapshots the old value on focusin),
// type, commit.
function commit(win, el, value) {
  el.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function cell(win, group, rid, field) {
  return win.document.querySelector(
    '.gen-pop-in[data-group="' + group + '"][data-rid="' + rid +
    '"][data-field="' + field + '"]');
}
function setAll(win, group, label) {
  return win.document.querySelector('#gen-pop-tbl-' + group +
    ' tr.gen-pop-setall input[aria-label="Set all ' + label + '"]');
}
const QS = ['q1', 'q2', 'q3'];

function regenWorld(populate) {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                   opx_plus: [], octaves: [] },
    qubits: QS.slice(), qubit_pairs: [], twpas: [], lines: [],
    pair_gate: 'cz_tunable', populate: populate
  }, { mode: 'regenerate' });
  G._test.state.allocation = {
    q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }] },
    q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }] },
    q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }] }
  };
  G.goToStep(6);
  return { win: win, G: G, st: G._test.state };
}
function lengths(st) {
  return QS.map(function (q) {
    return ((st.spec.populate.pulses || {})[q] || {}).x180_length;
  });
}
function lengthCells(win) {
  return QS.map(function (q) { return cell(win, 'pulses', q, 'x180_length').value; });
}
const PULSES = { pulses: {
  q1: { x180_length: 20 }, q2: { x180_length: 24 }, q3: { x180_length: 28 } } };

// ---- S1 / S2 ------------------------------------------------------------
(function () {
  const w = regenWorld(JSON.parse(JSON.stringify(PULSES)));
  const sa = setAll(w.win, 'pulses', 'x180 length');
  ok(!!sa, 'S1: the x180 length Set-all box renders (with its column label)');
  commit(w.win, sa, '40');
  ok(JSON.stringify(lengths(w.st)) === '[40,40,40]', 'S1: Set-all fills every row');
  commit(w.win, sa, '50');
  ok(JSON.stringify(lengths(w.st)) === '[50,50,50]', 'S2: second Set-all fills');
  w.win._wizUndo.tryUndo();
  ok(JSON.stringify(lengths(w.st)) === '[40,40,40]',
    'S2: first undo goes back to the first fill (got ' + JSON.stringify(lengths(w.st)) + ')');
  w.win._wizUndo.tryUndo();
  ok(JSON.stringify(lengths(w.st)) === '[20,24,28]',
    'S1: undo restores each row\'s OWN value, never blanks (got ' +
    JSON.stringify(lengths(w.st)) + ')');
  ok(JSON.stringify(lengthCells(w.win)) === '["20","24","28"]',
    'S1: the cells show them (got ' + JSON.stringify(lengthCells(w.win)) + ')');
  const t = w.win._toasts[w.win._toasts.length - 1] || '';
  ok(/Set all x180 length/.test(t), 'S1: the toast names the column (got ' + t + ')');
})();

// ---- S3: regen touched marks --------------------------------------------
(function () {
  const w = regenWorld(JSON.parse(JSON.stringify(PULSES)));
  commit(w.win, cell(w.win, 'pulses', 'q2', 'x180_length'), '30');   // touched before
  const sa = setAll(w.win, 'pulses', 'x180 length');
  commit(w.win, sa, '44');
  ok(w.st.regenTouched['pulses|q1|x180_length'] === 1, 'S3: the fill marks q1 touched');
  w.win._wizUndo.tryUndo();
  ok(!w.st.regenTouched['pulses|q1|x180_length'] &&
     !w.st.regenTouched['pulses|q3|x180_length'],
    'S3: undo withdraws the fill\'s touched marks (got ' +
    JSON.stringify(Object.keys(w.st.regenTouched)) + ')');
  ok(w.st.regenTouched['pulses|q2|x180_length'] === 1,
    'S3: a cell the user touched BEFORE the fill stays touched');
  ok(JSON.stringify(lengths(w.st)) === '[20,30,28]',
    'S3: values back to before the fill (got ' + JSON.stringify(lengths(w.st)) + ')');
})();

// ---- S4: absolute power mode --------------------------------------------
(function () {
  const w = regenWorld({
    qubit: { q1: { full_scale_power_dbm: 10 }, q2: { full_scale_power_dbm: 4 },
             q3: { full_scale_power_dbm: 7 } },
    pulses: { q1: { x180_amplitude: 0.3, saturation_amplitude: 0.02 },
              q2: { x180_amplitude: 0.2, saturation_amplitude: 0.05 },
              q3: { x180_amplitude: 0.1, saturation_amplitude: 0.01 } } });
  const pm = w.win.document.querySelector('#gen-pop-units .gen-pop-powermode select');
  pm.value = 'absolute';
  pm.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  const before = JSON.stringify([w.st.spec.populate.qubit, w.st.spec.populate.pulses]);
  commit(w.win, setAll(w.win, 'pulses', 'x180 amplitude'), '-3');
  const mid = JSON.stringify([w.st.spec.populate.qubit, w.st.spec.populate.pulses]);
  ok(mid !== before, 'S4: the dBm fill re-allocated amps / FSPs');
  w.win._wizUndo.tryUndo();
  const after = JSON.stringify([w.st.spec.populate.qubit, w.st.spec.populate.pulses]);
  ok(after === before, 'S4: undo restores every amp and port FSP (got ' + after +
    ' want ' + before + ')');
})();

// ---- S5: an explicit empty commit still clears; undo brings it back -----
(function () {
  const w = regenWorld(JSON.parse(JSON.stringify(PULSES)));
  const sa = setAll(w.win, 'pulses', 'x180 length');
  commit(w.win, sa, '40');
  commit(w.win, sa, '');
  ok(lengths(w.st).every(function (v) { return v === undefined; }),
    'S5: an explicit empty Set-all commit clears the column (docs/27)');
  w.win._wizUndo.tryUndo();
  ok(JSON.stringify(lengths(w.st)) === '[40,40,40]',
    'S5: undoing the clear brings the column back (got ' + JSON.stringify(lengths(w.st)) + ')');
})();

// ---- S6: the generic per-cell path is unchanged --------------------------
(function () {
  const w = regenWorld(JSON.parse(JSON.stringify(PULSES)));
  commit(w.win, cell(w.win, 'pulses', 'q3', 'x180_length'), '33');
  w.win._wizUndo.tryUndo();
  ok(JSON.stringify(lengths(w.st)) === '[20,24,28]',
    'S6: a per-cell undo still restores that cell (got ' + JSON.stringify(lengths(w.st)) + ')');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_setall_undo_selfcheck: all checks passed');
