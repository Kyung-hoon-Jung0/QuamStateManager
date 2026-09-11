// docs/177 — an unapplied edit never vanishes, on the COLUMN axis too.
//
// `BulkEdit.applyAll` writes every dirty cell in the table. The ROW pickers
// have refused to hide a row carrying an unapplied edit since docs/141 §4s,
// precisely so "Apply all" stays "apply what you see" — the column picker and
// the column search layer never learned it, so a dirty cell in a hidden column
// was written by a press whose confirm named a count the presser could not
// account for.
//
// The case that matters is NOT someone hiding a column they just typed in. It
// is a MIRROR write — the coupled f_01 <-> xy.RF_frequency twin, an
// FSP+amplitudes bundle (docs/160 §5e) — making an OFF-SCREEN column dirty on
// its own, which no picker click precedes.
//
// Run: node tests/bulk_dirtycol_selfcheck.cjs   (needs jsdom)
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

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const QUBITS = ['q1', 'q2'];
// Two real columns, so hiding one means something. `amp` is the one we hide;
// `f_01` is the visible partner a mirror write starts from.
const COLS = [
  { key: 'f_01', label: 'f 01', section: 'XY', unit: 'Hz', default_on: true },
  { key: 'amp', label: 'amplitude', section: 'XY', unit: '', default_on: true }
];

function cell(idx, key, dp, v) {
  return '<td class="bulk-td ck-' + idx + '" data-col-key="' + key + '">'
    + '<input type="text" class="bulk-cell" value="' + v + '" data-orig="' + v + '"'
    + ' data-dot-path="' + dp + '" data-resolved="' + dp + '" size="8"></td>';
}

function build() {
  let rows = '';
  QUBITS.forEach(function (q, i) {
    rows += '<tr data-qubit="' + q + '">'
      + '<th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>'
      + cell(0, 'f_01', 'qubits.' + q + '.f_01', 5000000000 + i)
      + cell(1, 'amp', 'qubits.' + q + '.xy.operations.x180.amplitude', (i + 1) / 10)
      + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
      + '<span class="bulk-row-error" hidden></span></td></tr>';
  });
  return '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>Columns</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<details class="bulk-colvis bulk-qubitvis"><summary>Q</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-qubitvis-menu"></div></details>'
    + '<button id="bulk-qubit-pill" hidden></button>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search">'
    + '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button>'
    + '<button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-table"><thead>'
    + '<tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>'
    + '<th class="bulk-col-head ck-0" data-col-key="f_01"><span class="bulk-col-label">f 01</span></th>'
    + '<th class="bulk-col-head ck-1" data-col-key="amp"><span class="bulk-col-label">amplitude</span></th>'
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    + '</div></div>';
}

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + build() + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(COLS, { bands: {} }, [],
    { chip: 'chipA', qubits: QUBITS.map(function (q) { return { id: q, grid: null }; }) });
  return win;
}

const win = world();
const doc = win.document;

function th(key) {
  return doc.querySelector('th.bulk-col-head[data-col-key="' + key + '"]');
}
function colHidden(key) {
  const h = th(key);
  return h.classList.contains('bulk-col-hidden') || h.classList.contains('bulk-search-hidden');
}
function cellOf(q, key) {
  return doc.querySelector('tr[data-qubit="' + q + '"] td[data-col-key="' + key + '"] .bulk-cell');
}
function type(input, v) {
  input.value = v;
  input.dispatchEvent(new win.Event('input', { bubbles: true }));
}
function toggleCol(key, on) {
  const cb = doc.querySelector('#bulk-colvis-menu input[data-col-toggle="' + key + '"]');
  cb.checked = on;
  cb.dispatchEvent(new win.Event('change', { bubbles: true }));
  return cb;
}
function search(q) {
  const inp = doc.getElementById('bulk-search');
  inp.value = q;
  inp.dispatchEvent(new win.Event('input', { bubbles: true }));
}
// The property the whole round is about: nothing `applyAll` would write may sit
// in a column the presser cannot see.
function hiddenDirty() {
  return Array.prototype.filter.call(doc.querySelectorAll('#bulk-table .bulk-cell'),
    function (c) {
      if (c.value === c.getAttribute('data-orig')) return false;
      const td = c.closest('td[data-col-key]');
      return colHidden(td.getAttribute('data-col-key'));
    }).length;
}

// ── D1: the picker still works ───────────────────────────────────────────────
ok(!colHidden('amp'), 'D1: both columns start visible');
toggleCol('amp', false);
ok(colHidden('amp') && !colHidden('f_01'), 'D1: unchecking a column hides that one');
ok(JSON.parse(win.localStorage.getItem('quam_bulk_hidden_cols_v2')).join(',') === 'amp',
  'D1: and the choice persists');

// ── D2: a MIRROR write into the hidden column force-shows it ────────────────
// No picker click precedes this — the value simply changes, the way a coupled
// write changes its twin. This is the path the round exists for.
type(cellOf('q1', 'amp'), '0.75');
ok(!colHidden('amp'),
  'D2: a hidden column holding an unapplied edit comes back on screen');
ok(hiddenDirty() === 0, 'D2: nothing Apply-all would write is off screen');
ok(JSON.parse(win.localStorage.getItem('quam_bulk_hidden_cols_v2')).join(',') === 'amp',
  'D2: the user’s CHOICE is untouched — it is being overridden, not forgotten');
const cb = doc.querySelector('#bulk-colvis-menu input[data-col-toggle="amp"]');
ok(cb && cb.checked === false,
  'D2: so its checkbox still reads hidden rather than silently re-ticking');

// ── D3: the choice takes effect again once nothing is unapplied ─────────────
win.BulkEdit.resetDirty();
ok(colHidden('amp'),
  'D3: with the edit gone the column hides again — the choice was kept, not dropped');

// ── D4: a SEARCH cannot hide an unapplied edit either ───────────────────────
toggleCol('amp', true);
search('');
type(cellOf('q2', 'amp'), '0.42');
search('f 01');                       // a query that excludes the amp column
ok(!colHidden('f_01'), 'D4: the searched-for column shows');
ok(!colHidden('amp'),
  'D4: and the column with an unapplied edit is not filtered away');
ok(hiddenDirty() === 0, 'D4: nothing Apply-all would write is off screen');

// …and a column with nothing unapplied IS filtered away, so the search still
// does its job.
win.BulkEdit.resetDirty();
search('f 01');
ok(colHidden('amp'), 'D4: with nothing unapplied the query filters normally');

// ── D5: both layers at once ────────────────────────────────────────────────
// checkbox-hidden AND search-hidden, then made dirty by a mirror write: the
// stale verdict of whichever layer is not re-run must not keep it off screen.
search('');
toggleCol('amp', false);
search('f 01');
ok(colHidden('amp'), 'D5: hidden by both layers');
type(cellOf('q1', 'amp'), '0.11');
ok(!colHidden('amp'), 'D5: an unapplied edit still brings it back through both');
ok(hiddenDirty() === 0, 'D5: nothing Apply-all would write is off screen');

// ── D6: the count the confirm quotes is the count on screen ────────────────
type(cellOf('q2', 'f_01'), '6000000000');
const shown = Array.prototype.filter.call(doc.querySelectorAll('#bulk-table .bulk-cell'),
  function (c) { return c.value !== c.getAttribute('data-orig'); }).length;
ok(shown === 2 && hiddenDirty() === 0,
  'D6: every edit Apply-all would write is one the presser can see (' + shown + ')');

console.log(fails ? 'FAILED (' + fails + ')'
  : 'bulk_dirtycol_selfcheck ok (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
