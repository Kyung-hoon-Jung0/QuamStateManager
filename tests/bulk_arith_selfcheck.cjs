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
// calc.js is loaded on every page by base.html, and the operand parser hands
// it anything that is not a plain literal. Stubbing it would pin a fiction:
// the bug this file now guards was calc.js's REAL contract ({ok, value})
// being read as a bare number, and a stub would have hidden it.
const CALC_JS = fs.readFileSync(path.join(STATIC, 'calc.js'), 'utf8');
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
  new win.Function(CALC_JS).call(win);
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
// 1b. Past double precision, the row says it was rounded
//     (found in a real browser on the customer chip: 0.45919729451219904 * 1.1
//     is EXACTLY 0.505117023963418944, eighteen significant digits, and
//     state.json stores doubles)
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.45919729451219904', '0.2', '0.3', '0.4']);
  selectAll(win);
  const r = win.BulkEdit._ge.arithPlan('*1.1').rows[0];
  ok(r.text === '0.505117023963419',
     'the value is the shortest round-tripping form of that double (got ' + r.text + ')');
  ok(r.exact === false, 'and the row is marked as not exact');
  ok(Number(r.text) === Number('0.505117023963418944'),
     'it is the SAME double, so nothing was lost -- only digits that could '
     + 'never have been stored');
  // the plain case is untouched
  const plain = win.BulkEdit._ge.arithPlan('*2').rows[1];
  ok(plain.text === '0.4' && plain.exact === true,
     'a result inside double precision stays exact (got ' + plain.text + ')');
}

// ───────────────────────────────────────────────────────────────────────────
// 1c. The SAME guard, on the other two branches
//     Only the `*` branch was pinned when the guard was written, so `+`/`-` and
//     the terminating `/` could each have lost it in silence -- and did stay
//     green through a mutation that deleted them outright. Every input below
//     needs more significant digits than a double carries, so a branch missing
//     the guard writes the long spelling AND calls it exact.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.4', '0.2', '0.3', '0.45919729451219904']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  const plus = ge.arithPlan('+0.100000000000000001').rows[0];
  ok(plus.text === '0.5',
     '+ : the sum is written as the double that will store it (got ' + plus.text + ')');
  ok(plus.exact === false, '+ : and the row says so');

  const minus = ge.arithPlan('-0.100000000000000001').rows[0];
  ok(minus.text === '0.3',
     '- : the same on subtraction (got ' + minus.text + ')');
  ok(minus.exact === false, '- : and the row says so');

  // a division whose remainder DOES reach zero -- an exact decimal, still longer
  // than a double: the branch that returns early must be fitted too.
  const div = ge.arithPlan('/0.0625').rows[3];
  ok(div.text === '7.347156712195185',
     '/ : a terminating quotient is fitted too (got ' + div.text + ')');
  ok(div.exact === false, '/ : and the row says so');

  // The two asserts above cannot tell the terminating branch from the float
  // fallback: both spell this quotient identically and both report inexact, so
  // lowering _DIV_SCALE_CAP silently moves the fixture to the other branch and
  // they stay green. Only the terminating branch can return exact:true.
  const tiny = world(['0.0000000000000001', '0.2', '0.3', '0.4']);
  selectAll(tiny);
  const long = tiny.BulkEdit._ge.arithPlan('/8').rows[0];
  ok(long.text === '0.0000000000000000125' && long.exact === true,
     '/ : a long-scale terminating division stays exact, so the fixture really '
     + 'is on the terminating branch (got ' + long.text + ' exact=' + long.exact + ')');
}

// ───────────────────────────────────────────────────────────────────────────
// 1c2. Integral results, in both directions
//     An integral text was carved out of the round trip for one round, on the
//     grounds that `type_policy.parse_value` tries int() before float(). That
//     is true of parse_value and FALSE of the path this grid takes: arithmetic
//     always scales a cell that ALREADY holds a number, so
//     `modifier._type_coerce` has a non-None old value and casts through
//     float() for an int-typed leaf as well as a float-typed one. So an
//     integer past 2^53 is shortened and marked like anything else --
//     and 1e18, which IS exactly representable, is left alone even though it
//     is nineteen digits long, which the digit-counting gate marked as rounded.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['9007199254740993', '1000000000', '0.3', '0.4']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  const big = ge.arithPlan('+2').rows[0];
  ok(big.text === '9,007,199,254,740,996',
     'an integer past 2^53 is written as the double that will store it (got '
     + big.text + ')');
  ok(big.exact === false, 'and the row says it was shortened');

  const e18 = ge.arithPlan('*1000000000').rows[1];
  ok(e18.text === '1,000,000,000,000,000,000',
     'an exactly-representable 19-digit product is left alone (got ' + e18.text + ')');
  ok(e18.exact === true, 'and carries no approx mark, because nothing was rounded');
}

// ───────────────────────────────────────────────────────────────────────────
// 1c3. The comparison is made on the same spelling at both ends
//     `_decStr` comma-groups the integer part. Comparing its output against an
//     UNGROUPED string marks every result over 999 as shortened when nothing
//     was shortened -- and this is not a corner case: a frequency is the most
//     ordinary value on the grid.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['6000000000', '1234.5', '0.215', '999']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  const freq = ge.arithPlan('*2').rows[0];
  ok(freq.text === '12,000,000,000' && freq.exact === true,
     'a doubled 6 GHz frequency is exact and grouped (got ' + freq.text
     + ' exact=' + freq.exact + ')');

  const mixed = ge.arithPlan('*2').rows[1];
  ok(mixed.text === '2,469' && mixed.exact === true,
     'so is a grouped result that came from a fractional cell (got '
     + mixed.text + ' exact=' + mixed.exact + ')');

  const small = ge.arithPlan('*2').rows[3];
  ok(small.text === '1,998' && small.exact === true,
     'and one that only just crosses the grouping threshold (got '
     + small.text + ')');
}

// ───────────────────────────────────────────────────────────────────────────
// 1f. The expressions the bar's own tooltip advertises
//     calc.js returns {ok, value}. This read it as if it returned a number, so
//     every expression the tooltip promises was refused with an error toast
//     while the tooltip went on promising them. A UI that advertises a syntax
//     it rejects is worse than one that never offered it.
//     calc.js is evaluated in the world for this reason -- a stub would have
//     hidden the very contract mismatch that was the bug.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.5', '0.25', '0.125', '0.0625']);
  selectAll(win);
  const ge = win.BulkEdit._ge;

  // A refused operand comes back as a plan with NO rows array at all, so every
  // read below -- the assert AND its failure message -- goes through nRows.
  // Reading db.rows.length inside a message crashes the file instead of failing
  // it, which costs every assert after this block.
  const nRows = (p) => (p && p.rows ? p.rows.length : -1);
  const row0 = (p, k) => (p && p.rows && p.rows[0] ? p.rows[0][k] : null);

  const db = ge.arithPlan('*10^(-1/20)');
  ok(nRows(db) === 4,
     'a dB-style expression is accepted (got ' + nRows(db) + ' rows)');
  ok(row0(db, 'text') !== null
     && Number(row0(db, 'text')) === 0.5 * Math.pow(10, -1 / 20),
     'and lands on the value calc.js computes (got ' + row0(db, 'text') + ')');
  ok(row0(db, 'exact') === false,
     'and says so: an expression result is a float64, never an exact decimal');

  ok(nRows(ge.arithPlan('/sqrt(2)')) === 4, 'so is /sqrt(2)');
  ok(nRows(ge.arithPlan('*(1+0.05)')) === 4, 'so is *(1+0.05)');

  // and the refusal path still refuses
  const bad = ge.arithPlan('*banana');
  ok(!bad || !bad.rows || bad.rows.length === 0,
     'while a non-expression is still refused');
}

// ───────────────────────────────────────────────────────────────────────────
// 1d. What the approx glyph is allowed to claim
//     The legend is the only thing that gives `exact: false` a meaning, and TWO
//     different things set it now: a division that never terminates, and an
//     exact decimal shortened to what a double holds. A legend naming only the
//     first is a false statement about every row of the second kind -- and the
//     rows below are the second kind: each is computed in exact BigInt decimal,
//     not in floating point, and each is then shortened.
//     The four values are real x180 amplitudes from a 20-qubit chip. Round
//     fixtures like 0.2 scale exactly and would leave one marked row, which
//     cannot pin the plural clause.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.45919729451219904', '0.5921391107323938',
                     '0.4167411533476113', '0.3491514335162928']);
  selectAll(win);
  win.BulkEdit._ge.arithOpen('*1.1');
  const ov = win.document.querySelector('.ch-overlay');
  ok(!!ov, 'the preview opened');
  // Read the LEGEND from its own element. Taking ov.textContent lets the
  // per-row glyph satisfy a legend assert and the legend satisfy a row assert,
  // so neither is pinned -- deleting the row glyph outright stayed green.
  const legend = ov ? (ov.querySelector('p.muted') || {}).textContent || '' : '';
  const bodyRow = ov ? (ov.querySelector('.ch-table tbody tr') || {}).textContent || '' : '';
  ok(legend.indexOf('≈') >= 0, 'the legend names the glyph');
  ok(legend.indexOf('shortened to what the chip can store') >= 0,
     'and covers the shortened case');
  ok(legend.indexOf('computed in floating point') >= 0,
     'while still covering the float-division case');
  ok(bodyRow.indexOf('≈') >= 0,
     'and the inexact row actually carries the glyph the legend explains');
  ok(legend.indexOf('rows marked') >= 0 && legend.indexOf('were computed') >= 0,
     'plural, because this plan has several marked rows');
}

// ───────────────────────────────────────────────────────────────────────────
// 1d2. The sentence agrees with the plan, and with the session it runs in
//     "1 cell will change -- ROWS marked ... WERE computed" was the plural half
//     of a sentence whose first half is carefully singularised. And the
//     unconditional "Nothing is written to the chip" is FALSE while an
//     auto-apply session is armed (docs/117) -- which is exactly the moment a
//     user most needs it to be true.
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world(['0.45919729451219904', '0.2', '0.3', '0.4']);
  const tds = Array.prototype.slice.call(
    win.document.querySelectorAll('#bulk-table tbody td[data-col-key="amp"]'));
  tds[0].classList.add('bulk-sel');          // exactly ONE marked row
  win.BulkEdit._ge.arithOpen('*1.1');
  const solo = (win.document.querySelector('.ch-overlay')
    .querySelector('p.muted') || {}).textContent || '';
  ok(/1 cell will change/.test(solo), 'the count is singular');
  ok(solo.indexOf('the row marked') >= 0 && solo.indexOf('was computed') >= 0,
     'and so is the clause about it (got: ' + solo.slice(0, 100) + ')');
  ok(solo.indexOf('Nothing is written to the chip') >= 0,
     'with auto-apply disarmed, staging really does write nothing');
}
{
  // And the clause is gated on the rows the table SHOWS. `anyFloat` is raised
  // before the unchanged-cell check, so a selection whose only inexact result
  // lands back on its own value used to print a sentence explaining a glyph
  // that appears nowhere: "0 cells will change - rows marked ~ were computed".
  const win = world(['1', '0.2', '0.3', '0.4']);
  const tds = Array.prototype.slice.call(
    win.document.querySelectorAll('#bulk-table tbody td[data-col-key="amp"]'));
  tds[0].classList.add('bulk-sel');
  const plan = win.BulkEdit._ge.arithPlan('*1.0000000000000001');
  ok(plan.rows.length === 0 && plan.unchanged.length === 1 && plan.anyFloat === true,
     'the fixture really is the inexact-but-unchanged case (rows='
     + plan.rows.length + ' unchanged=' + plan.unchanged.length
     + ' anyFloat=' + plan.anyFloat + ')');
  win.BulkEdit._ge.arithOpen('*1.0000000000000001');
  const none = (win.document.querySelector('.ch-overlay')
    .querySelector('p.muted') || {}).textContent || '';
  ok(none.indexOf('≈') < 0,
     'with no row to mark, the glyph is not explained (got: ' + none.slice(0, 90) + ')');
  ok(none.indexOf('marked') < 0, 'and nothing claims a row was marked');
}
{
  // The harder half of the same defect, found by a verifier rather than the
  // reporter: a POPULATED table. One cell changes and is exact; another is a
  // below-half-ULP no-op that raises anyFloat on its way to `unchanged`. The
  // table has a row, that row has no glyph, and the sentence used to send the
  // reader looking for one.
  const win = world(['0.5', '9007199254740992', '9007199254740992', '9007199254740992']);
  selectAll(win);
  const plan = win.BulkEdit._ge.arithPlan('+1');
  ok(plan.rows.length === 1 && plan.unchanged.length === 3 && plan.anyFloat === true,
     'the fixture is the mixed case (rows=' + plan.rows.length
     + ' unchanged=' + plan.unchanged.length + ' anyFloat=' + plan.anyFloat + ')');
  win.BulkEdit._ge.arithOpen('+1');
  const ov = win.document.querySelector('.ch-overlay');
  const legend = (ov.querySelector('p.muted') || {}).textContent || '';
  const body = Array.prototype.slice.call(ov.querySelectorAll('.ch-table tbody tr'))
    .map(function (t) { return t.textContent; }).join(' ');
  ok(body.indexOf('≈') < 0, 'the one row really carries no glyph');
  ok(legend.indexOf('marked') < 0,
     'so the sentence does not claim one (got: ' + legend.slice(0, 90) + ')');
  ok(/1 cell will change/.test(legend), 'while still reporting the count honestly');
}
{
  const win = world(['0.45919729451219904', '0.2', '0.3', '0.4']);
  win.AutoApply = { armed: function () { return true; } };
  selectAll(win);
  win.BulkEdit._ge.arithOpen('*1.1');
  const armed = (win.document.querySelector('.ch-overlay')
    .querySelector('p.muted') || {}).textContent || '';
  ok(armed.indexOf('Nothing is written to the chip') < 0,
     'an armed session is NOT promised that nothing is written');
  ok(armed.indexOf('pushed to the live chip') >= 0,
     'it is told what will really happen (got: ' + armed.slice(-100) + ')');
  // The module is auto-apply.js; the CONTROL is labelled Auto-Sync (docs/120
  // item 8), and that is the only name a user can go and find. A sentence that
  // names the module sends them looking for a switch that does not exist.
  ok(/Auto-Sync/.test(armed) && !/Auto-apply/i.test(armed),
     'and it calls the control what the pill calls it (got: ' + armed.slice(-100) + ')');
}
{
  // The fallback exists for the window before auto-apply.js has loaded, which
  // is exactly when a wrong answer is most likely -- so it is read from the
  // tray's own marker, and pinned separately from the module path.
  const win = world(['0.45919729451219904', '0.2', '0.3', '0.4']);
  const tray = win.document.createElement('div');
  tray.id = 'pending-tray';
  tray.setAttribute('data-auto-apply', '1');
  win.document.body.appendChild(tray);
  ok(!win.AutoApply, 'the module really is absent in this world');
  selectAll(win);
  win.BulkEdit._ge.arithOpen('*1.1');
  const viaTray = (win.document.querySelector('.ch-overlay')
    .querySelector('p.muted') || {}).textContent || '';
  ok(viaTray.indexOf('Nothing is written to the chip') < 0,
     'with only the tray marker, the reassuring sentence is still withheld');
  ok(viaTray.indexOf('pushed to the live chip') >= 0,
     'and the honest one is given (got: ' + viaTray.slice(-90) + ')');

  tray.setAttribute('data-auto-apply', '0');
  win.document.querySelector('.ch-overlay').remove();
  win.BulkEdit._ge.arithOpen('*1.1');
  const off = (win.document.querySelector('.ch-overlay')
    .querySelector('p.muted') || {}).textContent || '';
  ok(off.indexOf('Nothing is written to the chip') >= 0,
     'and a tray that says the session is OFF gets the ordinary sentence');
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
  new win.Function(CALC_JS).call(win);
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
