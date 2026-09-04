// docs/167 — selection arithmetic on the Live State Edit grid, against the
// REAL bulk-edit.js under jsdom.
//
// What is pinned, in the order it matters:
//  - the SAFETY claim: a keypress opens a preview and writes NOTHING. This is
//    driven through the door (`arithOpen`), not the planner, because a wiring
//    that skipped the preview would leave a planner-only pin green.
//  - exactness: 0.215 * 1.1 is 0.2365, not 0.23650000000000002 — float noise
//    would land in state.json, in every Δ chip and in the leaf index.
//  - the output SHAPE: plain grouped decimal, never exponential. That is what
//    makes the round trip through the unchanged server safe by construction.
//  - `%` means a fraction of the cell's OWN value, both directions.
//  - every selected cell lands in exactly one bucket, with a reason.
//  - an unchanged cell is never written (dirtiness is a string compare).
//  - one Ctrl+Z undoes the whole fill, with every `prev` snapshotted first.
//
// ValueDelta is taken from the REAL app.js rather than stubbed: the arithmetic
// is built on its exact parse semantics, so a stub would pin a fiction.
//
// Run: node tests/bulk_arith_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const GRID_VIRT_JS = fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8');
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

// Just the ValueDelta IIFE — the whole of app.js is not needed and its mount
// side effects would only add noise.
const VD_START = APP_JS.indexOf('window.ValueDelta = (function () {');
if (VD_START < 0) { console.error('FAIL: ValueDelta not found in app.js'); process.exit(1); }
const VD_END = APP_JS.indexOf('\n})();', VD_START);
const VALUE_DELTA_JS = APP_JS.slice(VD_START, VD_END + 6);

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const QUBITS = ['q1', 'q2', 'q3', 'q4'];

function cell(dp, v, extra) {
  return '<td class="bulk-td ck-0" data-col-key="amp" data-dot-path="' + dp + '"'
    + (extra || '') + '><input type="text" class="bulk-cell" value="' + v
    + '" data-orig="' + v + '" data-dot-path="' + dp + '" data-resolved="' + dp
    + '" size="10"></td>';
}

function build(values, extras) {
  let rows = '';
  QUBITS.forEach(function (q, i) {
    rows += '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">'
      + q + '</th>'
      + cell('qubits.' + q + '.amp', values[i], (extras || [])[i] || '')
      + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
      + '<span class="bulk-row-error" hidden></span></td></tr>';
  });
  return '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>P</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search">'
    + '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button>'
    + '</div><div class="bulk-table-wrap"><table id="bulk-table"><thead>'
    + '<tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>'
    + '<th class="bulk-col-head ck-0" data-col-key="amp"><span class="bulk-col-label">amp</span></th></tr>'
    + '</thead><tbody>' + rows + '</tbody></table></div></div></div>';
}

function world(values, extras) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + build(values, extras) + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;          // the docs/125 standing rule: bridge, or it throws
  win.htmx = { ajax: function () {}, trigger: function () {}, process: function () {} };
  win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
  win.toasts = [];
  win.showToast = function (t) { win.toasts.push(String(t)); };
  win.undoRecords = [];
  win.LiveEditUndo = { record: function (label, entries) { win.undoRecords.push({ label: label, entries: entries }); } };
  win.trapFocus = function () { return function () {}; };
  win.smModalOpen = function () { return !!win.document.querySelector('.ch-overlay'); };
  new win.Function(VALUE_DELTA_JS).call(win);
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(
    [{ key: 'amp', label: 'amp', section: 'S', unit: '', default_on: true }],
    { bands: {} }, [],
    { chip: 'chipA', qubits: QUBITS.map(function (q) { return { id: q, grid: null }; }) });
  return win;
}

function selectAll(win) {
  const tds = Array.prototype.slice.call(
    win.document.querySelectorAll('#bulk-table tbody td[data-col-key="amp"]'));
  tds.forEach(function (td) { td.classList.add('bulk-sel'); });
  return tds;
}
const vals = (win) => Array.prototype.slice.call(
  win.document.querySelectorAll('#bulk-table tbody input.bulk-cell')).map(function (i) { return i.value; });

// ───────────────────────────────────────────────────────────────────────────
// 1. The planner is exact, and its output is shaped for the server
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.215', '0.4', '1000000000', '0.001']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  const p = ge.arithPlan('*1.1');
  ok(p.rows.length === 4, 'every selected numeric cell is planned (got ' + p.rows.length + ')');
  ok(p.rows[0].text === '0.2365',
     'exact decimal: 0.215 * 1.1 is 0.2365, not float noise (got ' + p.rows[0].text + ')');
  ok(p.rows[0].exact === true, 'and the row is marked exact');
  ok(p.rows[2].text === '1,100,000,000',
     'the integer part is comma-grouped like group_digits (got ' + p.rows[2].text + ')');
  ok(!/[eE]/.test(p.rows.map(function (r) { return r.text; }).join(' ')),
     'no result is written in exponential form — parse_value would not strip it');

  const PLAIN = /^[+-]?\d[\d,]*(\.\d+)?$/;
  ok(p.rows.every(function (r) { return PLAIN.test(r.text); }),
     'every result matches type_policy._PLAIN_GROUPED_NUMBER');

  // a small value must not be rounded away by an exponential switch
  const tiny = ge.arithPlan('/1000');
  ok(tiny.rows[3].text === '0.000001',
     'a small quotient stays plain decimal (got ' + tiny.rows[3].text + ')');
}

// ───────────────────────────────────────────────────────────────────────────
// 2. The operators, including what "%" is defined to mean
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['100', '0.5', '2', '8']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  ok(ge.arithPlan('+10%').rows[0].text === '110',
     '+10% is ten percent OF THE CELL, not ten percentage points');
  ok(ge.arithPlan('-2%').rows[0].text === '98', '-2% is x0.98');
  ok(ge.arithPlan('-2%').rows[1].text === '0.49', 'and it is exact on a fraction');
  ok(ge.arithPlan('+5e6').rows[0].text === '5,000,100', '+5e6 adds (the exponent is read)');
  ok(ge.arithPlan('/2').rows[3].text === '4', '/2 divides');
  ok(ge.arithPlan('-0.5').rows[1].text === '0', '- subtracts to zero without a sign');

  // a bare number is refused: it would be a second, worse fill-down and would
  // collide with absolute entry
  ok(!!ge.arithPlan('1.1').error, 'a bare number is refused with a message');
  ok(!!ge.arithPlan('').error, 'an empty expression is refused');
  ok(!!ge.arithPlan('*banana').error, 'an unreadable operand is refused');
  ok(ge.arithPlan('*0').rows.length === 4, 'multiplying by zero is allowed (it is a number)');
  ok(!ge.arithPlan('/0').rows || ge.arithPlan('/0').rows.length === 0 || !!ge.arithPlan('/0').error,
     'dividing by zero produces no write');
}

// ───────────────────────────────────────────────────────────────────────────
// 3. Every selected cell lands in exactly one bucket, with a reason
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.2', '#/qubits/q1/amp', 'hello', '0.4'],
                    ['', ' data-is-pointer="1"', '', ' data-missing="1"']);
  selectAll(win);
  const p = win.BulkEdit._ge.arithPlan('*2');
  const total = p.rows.length + p.skipped.length + p.unchanged.length;
  ok(total === 4, 'every selected cell is accounted for (' + total + ' of 4)');
  ok(p.rows.length === 1, 'only the numeric cell is planned');
  ok(p.skipped.length === 3, 'the other three are skipped, not silently dropped');
  const reasons = p.skipped.map(function (k) { return k.reason; }).join(' | ');
  ok(/reference/.test(reasons), 'a pointer cell says it holds a reference');
  ok(/not set/.test(reasons), 'a missing cell says there is no value to scale');
  ok(/not a number/.test(reasons), 'a text cell says it is not a number');
  ok(p.skipped.every(function (k) { return !!k.label; }), 'every skipped row names its cell');
}

// ───────────────────────────────────────────────────────────────────────────
// 4. An unchanged cell is never written
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.2', '0.4', '0.6', '0.8']);
  selectAll(win);
  const p = win.BulkEdit._ge.arithPlan('*1');
  ok(p.rows.length === 0, 'x1 plans no writes at all');
  ok(p.unchanged.length === 4, 'all four are reported as already holding the result');
  win.BulkEdit._ge.arithApply(p);
  ok(win.undoRecords.length === 0, 'and nothing is recorded as an undoable action');
  ok(vals(win).join(',') === '0.2,0.4,0.6,0.8', 'the cells are byte-unchanged');
}

// ───────────────────────────────────────────────────────────────────────────
// 5. THE SAFETY CLAIM — the door opens a preview and writes nothing
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.2', '0.4', '0.6', '0.8']);
  selectAll(win);
  const before = vals(win).join(',');
  win.BulkEdit._ge.arithOpen('*2');
  const ov = win.document.querySelector('.ch-overlay');
  ok(!!ov, 'the door opens an overlay');
  ok(vals(win).join(',') === before, 'and writes NOTHING before the user confirms');
  ok(win.undoRecords.length === 0, 'no undo action is recorded either');
  ok(/0\.4/.test(ov.textContent) && /1\.6/.test(ov.textContent),
     'the overlay shows the new numbers, so the offer is real');
  ok(/Ctrl\+Z/.test(ov.textContent) && /Apply/.test(ov.textContent),
     'and states that nothing reaches the chip yet');

  // the confirm is what writes
  ov.querySelector('.bulk-arith-fill').dispatchEvent(new win.Event('click', { bubbles: true }));
  ok(vals(win).join(',') === '0.4,0.8,1.2,1.6', 'confirming writes every planned cell');
  ok(!win.document.querySelector('.ch-overlay'), 'and closes the overlay');
}

// ───────────────────────────────────────────────────────────────────────────
// 6. One press of Ctrl+Z undoes the whole fill, with honest `prev` values
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.2', '0.4', '0.6', '0.8']);
  selectAll(win);
  const p = win.BulkEdit._ge.arithPlan('*2');
  const n = win.BulkEdit._ge.arithApply(p);
  ok(n === 4, 'four cells written');
  ok(win.undoRecords.length === 1, 'ONE undo action, not four');
  const rec = win.undoRecords[0];
  ok(rec.entries.length === 4, 'carrying all four cells');
  ok(rec.entries.map(function (e) { return e.prev; }).join(',') === '0.2,0.4,0.6,0.8',
     'every prev is the ORIGINAL, snapshotted before any write (audit F13)');
  ok(rec.entries.map(function (e) { return e.next; }).join(',') === '0.4,0.8,1.2,1.6',
     'and every next is the computed value');
  ok(/\*2/.test(rec.label), 'the action is labelled with the expression the user typed');
  ok(win.toasts.length === 0, 'the planner/applier is silent — the door does the talking');
}

// ───────────────────────────────────────────────────────────────────────────
// 7. The bar follows the selection and never touches the server-rendered HTML
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['1', '2', '3', '4']);
  const bar = win.BulkEdit._ge.arithBar();
  ok(!!bar && bar.id === 'bulk-arith-bar', 'the bar is injected, not templated');
  ok(bar.hidden === true, 'and is hidden with no selection');
  selectAll(win);
  // _syncSelHint is what every selection change calls; the bar rides it rather
  // than binding its own listener, so one thing decides when both are shown.
  win.BulkEdit._ge.syncSel();
  ok(win.document.getElementById('bulk-arith-bar').hidden === false,
     'and appears once cells are selected');
  const hint = win.document.getElementById('bulk-sel-hint');
  ok(/scale them/.test(hint.textContent),
     'the selection hint names it, or nobody discovers it');
}

// ───────────────────────────────────────────────────────────────────────────
// 8. Escape belongs to the preview while one is open
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.2', '0.4', '0.6', '0.8']);
  selectAll(win);
  win.BulkEdit._ge.arithOpen('*2');
  ok(win.document.querySelectorAll('td.bulk-sel').length === 4, 'precondition: 4 selected');
  const ev = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
  win.document.dispatchEvent(ev);
  ok(win.document.querySelectorAll('td.bulk-sel').length === 4,
     'Escape with the preview open does not clear the selection behind it');
}

// ───────────────────────────────────────────────────────────────────────────
// 9. The two states the plain fixture cannot reach
//    (a mutation sweep found both guards untested — docs/141 §4af)
// ───────────────────────────────────────────────────────────────────────────
function specialWorld() {
  // q1 and q2 are LINKED: they are two views of ONE physical leaf, so writing
  // either mirrors the other. q3's input is read-only.
  const SHARED = 'ports.mw_outputs.con1.1.1.amp';
  const rows =
      '<tr data-qubit="q1"><th class="bulk-rowhead" data-col-key="__id__">q1</th>'
    + '<td class="bulk-td ck-0" data-col-key="amp" data-dot-path="qubits.q1.amp">'
    + '<input type="text" class="bulk-cell bulk-cell-linked" value="0.2" data-orig="0.2"'
    + ' data-dot-path="qubits.q1.amp" data-resolved="' + SHARED + '" data-linkable="1" size="10">'
    + '</td><td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
    + '<span class="bulk-row-error" hidden></span></td></tr>'
    + '<tr data-qubit="q2"><th class="bulk-rowhead" data-col-key="__id__">q2</th>'
    + '<td class="bulk-td ck-0" data-col-key="amp" data-dot-path="qubits.q2.amp">'
    + '<input type="text" class="bulk-cell bulk-cell-linked" value="0.2" data-orig="0.2"'
    + ' data-dot-path="qubits.q2.amp" data-resolved="' + SHARED + '" data-linkable="1" size="10">'
    + '</td><td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
    + '<span class="bulk-row-error" hidden></span></td></tr>'
    + '<tr data-qubit="q3"><th class="bulk-rowhead" data-col-key="__id__">q3</th>'
    + '<td class="bulk-td bulk-cell-ro ck-0" data-col-key="amp" data-dot-path="qubits.q3.amp">'
    + '<input type="text" class="bulk-cell" value="0.9" data-orig="0.9" readonly'
    + ' data-dot-path="qubits.q3.amp" data-resolved="qubits.q3.amp" size="10">'
    + '</td><td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
    + '<span class="bulk-row-error" hidden></span></td></tr>';
  const html = '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>P</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search">'
    + '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button>'
    + '</div><div class="bulk-table-wrap"><table id="bulk-table"><thead>'
    + '<tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>'
    + '<th class="bulk-col-head ck-0" data-col-key="amp"><span class="bulk-col-label">amp</span></th></tr>'
    + '</thead><tbody>' + rows + '</tbody></table></div></div></div>';
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + html + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;
  win.htmx = { ajax: function () {}, trigger: function () {}, process: function () {} };
  win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
  win.toasts = []; win.showToast = function (t) { win.toasts.push(String(t)); };
  win.undoRecords = [];
  win.LiveEditUndo = { record: function (l, e) { win.undoRecords.push({ label: l, entries: e }); } };
  win.trapFocus = function () { return function () {}; };
  win.smModalOpen = function () { return !!win.document.querySelector('.ch-overlay'); };
  new win.Function(VALUE_DELTA_JS).call(win);
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(
    [{ key: 'amp', label: 'amp', section: 'S', unit: '', default_on: true }],
    { bands: {} }, [],
    { chip: 'chipA', qubits: ['q1', 'q2', 'q3'].map(function (q) { return { id: q, grid: null }; }) });
  return win;
}
{
  const win = specialWorld();
  selectAll(win);
  const p = win.BulkEdit._ge.arithPlan('*2');

  ok(p.rows.length === 2, 'the read-only cell is not planned (planned ' + p.rows.length + ')');
  ok(p.skipped.length === 1 && /read-only/.test(p.skipped[0].reason),
     'and it is reported as read-only rather than silently dropped');

  win.BulkEdit._ge.arithApply(p);
  const rec = win.undoRecords[0];
  ok(rec.entries.map(function (e) { return e.prev; }).join(',') === '0.2,0.2',
     'every prev is the ORIGINAL even though writing q1 MIRRORS into q2 — a '
     + 'read-as-you-go prev records 0.4 here and Ctrl+Z converges on an '
     + 'intermediate (audit F13; got ' + rec.entries.map(function (e) { return e.prev; }).join(',') + ')');
  ok(win.document.querySelector('tr[data-qubit="q3"] input.bulk-cell').value === '0.9',
     'the read-only cell is byte-unchanged');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed (' + asserts + ' assertions)');
