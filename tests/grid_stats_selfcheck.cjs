// QA F9 + F10 -- what Escape, an undo repaint and Reset put back, the column
// header and the row message follow.
//
// F9: the header min/max (and the cell-best / cell-worst colouring) were only
//     recomputed by the Apply paths. The in-place repaint every undo, Revert
//     last apply and sync pull goes through (revertPaths), the Escape revert
//     and Reset all put values back WITHOUT a recompute, so the header kept
//     describing values no cell held (customer chip: 'max 4,895,431,254.5'
//     over a column whose max was 4,895,431,254.262516).
// F10: a failed commit's row message ("expected a number, got 'abc'")
//     survived the Escape that threw 'abc' away -- nothing clears it but the
//     next Apply, and the row is clean afterwards, so no Apply ever comes.
//
// Drives the REAL bulk-edit.js + pair-edit.js (+ grid-virt.js) under jsdom,
// through the real applyRow (a mocked /field/edit-batch refusal), the real
// document-level Escape handler and the real revertPaths / resetDirty.
//
// Run: node tests/grid_stats_selfcheck.cjs   (driven by tests/test_grid_stats.py)
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
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

// qubit grid: f01 (q1 is the max) and T1 (q3 is the min)
const Q = {
  q1: { f01: '4,895,431,254.5', t1: '0.0000135' },
  q2: { f01: '4,700,000,000', t1: '0.000011627458545397817' },
  q3: { f01: '4,800,000,000', t1: '0.000011349' },
};
// pair grid: detuning (q1-2 is the max)
const P = { 'q1-2': '9,000,000', 'q2-3': '5,000,000', 'q3-4': '7,000,000' };

function qtd(key, dp, v) {
  return '<td class="bulk-td" data-col-key="' + key + '"><input type="text" class="bulk-cell" value="' + v
    + '" data-orig="' + v + '" data-dot-path="' + dp + '" data-resolved="' + dp + '" size="14"></td>';
}
function applyCol() {
  return '<td class="bulk-apply-col"><button type="button" class="btn-xs bulk-row-apply" disabled>Apply</button>'
    + '<span class="bulk-row-error" hidden></span></td>';
}
function head(key) {
  return '<th class="bulk-col-head" data-col-key="' + key + '" data-section="S">'
    + '<span class="bulk-col-label">' + key + '</span>'
    + '<span class="bulk-col-stats muted" data-col-stats="' + key + '"></span></th>';
}
function qubitGrid() {
  const rows = Object.keys(Q).map(function (q) {
    return '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>'
      + qtd('f01', 'qubits.' + q + '.f_01', Q[q].f01)
      + qtd('t1', 'qubits.' + q + '.T1', Q[q].t1) + applyCol() + '</tr>';
  }).join('');
  return '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
    + '<span id="bulk-search-hint"></span></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head('f01') + head('t1')
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function pairGrid() {
  const rows = Object.keys(P).map(function (id) {
    return '<tr data-qubit="' + id + '" data-pair="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">'
      + id + '</th>' + qtd('det', 'qubit_pairs.' + id + '.detuning', P[id]) + applyCol() + '</tr>';
  }).join('');
  return '<div class="bulk-pair-divider" id="bulk-pair-divider">'
    + '<div class="bulk-colvis-menu" id="bulk-pair-colvis-menu"></div>'
    + '<span id="bulk-pair-search-count"></span><span id="bulk-pair-dirty-count"></span>'
    + '<button id="bulk-pair-apply-all" disabled></button><button id="bulk-pair-apply-sync" disabled></button>'
    + '<button id="bulk-pair-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-pair-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head('det')
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane"><div class="bulk-panel">'
    + qubitGrid() + pairGrid() + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;                       // docs/125: bridge, or it throws
  win.__chipToken = 'tok';
  win.htmx = { ajax() {}, trigger() {}, process() {} };
  win.showToast = function () {};
  win._diagChanged = function () {};
  win.LiveEditUndo = { record() {}, clear() {} };
  win.__bulkSearchDebounce = 1;
  // the server's refusal of a non-number, in the /field/edit-batch shape
  win.fetch = function (url, opts) {
    const body = JSON.parse(opts.body);
    const results = body.updates.map(function (u) {
      return { dot_path: u.dot_path, applied: false, error: "expected a number, got '" + u.value + "'" };
    });
    return Promise.resolve({ status: 400, ok: false,
      json: function () { return Promise.resolve({ ok: false, results: results }); } });
  };
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  new win.Function(PAIR_JS).call(win);
  win.BulkEdit.mount(
    [{ key: 'f01', label: 'f01', section: 'S', unit: '', default_on: true },
     { key: 't1', label: 't1', section: 'S', unit: '', default_on: true }],
    { bands: {} }, [], { chip: 'chipA', qubits: Object.keys(Q).map(function (q) { return { id: q, grid: null }; }) });
  win.BulkPairEdit.mount([{ key: 'det', label: 'det', section: 'S', unit: '', default_on: true,
                             editable: true, kind: 'scalar', maxlen: 12 }]);
  return win;
}
// (the page's digit grouping is app.js's _groupDigits, not loaded here --
// the header renders String(n), which is all these checks need)
const stat = (win, table, key) =>
  win.document.querySelector('#' + table + ' [data-col-stats="' + key + '"]').textContent;
const inp = (win, dp) => win.document.querySelector('.bulk-cell[data-dot-path="' + dp + '"]');
function escapeOn(win, cell) {
  cell.focus();
  win.document.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
}

(async function main() {
  // ── F9 (a): the undo / Revert-last-apply repaint moves the header ──────────
  {
    const win = world();
    win.BulkEdit.recomputeStats();          // the page's own mount-time numbers
    ok(/max 4895431254\.5$/.test(stat(win, 'bulk-table', 'f01')),
       'precondition: the header max is q1 (' + stat(win, 'bulk-table', 'f01') + ')');
    const r = win.BulkEdit.revertPaths([{ dot_path: 'qubits.q1.f_01',
      old_value_str: '4895431254.262516', old_value_disp: '4,895,431,254.262516', old_kind: 'num' }]);
    ok(r.patched === 1, 'the repaint landed in the q1 cell');
    ok(/max 4895431254\.262516$/.test(stat(win, 'bulk-table', 'f01')),
       'F9: after the repaint the header max is the value the cell now shows (got '
       + stat(win, 'bulk-table', 'f01') + ')');
    // a repaint that makes q1 the SMALLEST moves the extreme colouring too
    win.BulkEdit.revertPaths([{ dot_path: 'qubits.q1.f_01',
      old_value_str: '4600000000', old_value_disp: '4,600,000,000', old_kind: 'num' }]);
    ok(inp(win, 'qubits.q1.f_01').classList.contains('cell-worst')
       && inp(win, 'qubits.q3.f_01').classList.contains('cell-best'),
       'F9: the cell-best / cell-worst marks follow the repaint');
    ok(/^min 4600000000 · max 4800000000$/.test(stat(win, 'bulk-table', 'f01')),
       'F9: min and max both re-derived (got ' + stat(win, 'bulk-table', 'f01') + ')');
    ok(stat(win, 'bulk-table', 't1') !== '', 'an untouched column keeps its numbers');
  }

  // ── F9 (a) on the pair grid ───────────────────────────────────────────────
  {
    const win = world();
    win.BulkPairEdit.recomputeStats();
    ok(/max 9000000$/.test(stat(win, 'bulk-pair-table', 'det')), 'precondition: pair header max');
    win.BulkPairEdit.revertPaths([{ dot_path: 'qubit_pairs.q1-2.detuning',
      old_value_str: '6000000', old_value_disp: '6,000,000', old_kind: 'num' }]);
    ok(/^min 5000000 · max 7000000$/.test(stat(win, 'bulk-pair-table', 'det')),
       'F9: the pair grid header follows its repaint (got ' + stat(win, 'bulk-pair-table', 'det') + ')');
  }

  // ── F9 (b) + F10: a refused commit, then Escape ───────────────────────────
  {
    const win = world();
    win.BulkEdit.recomputeStats();
    const before = stat(win, 'bulk-table', 't1');
    ok(/^min 0\.000011349/.test(before), 'precondition: q3 is the T1 min (' + before + ')');
    const c = inp(win, 'qubits.q3.T1');
    const tr = c.closest('tr');
    const slot = tr.querySelector('.bulk-row-error');
    c.focus();
    c.value = 'abc';
    c.dispatchEvent(new win.Event('input', { bubbles: true }));
    win.BulkEdit.applyRow(tr.querySelector('.bulk-row-apply'));
    await tick(20);
    ok(!slot.hidden && /expected a number/.test(slot.textContent),
       'the refusal is shown on the row (' + slot.textContent + ')');
    ok(!/^min 0\.000011349/.test(stat(win, 'bulk-table', 't1')),
       'precondition: the failed commit recomputed the header WITHOUT q3 (the bug\'s setup)');
    escapeOn(win, c);
    ok(c.value === '0.000011349', 'Escape restores the committed value');
    ok(slot.hidden === true && slot.textContent === '',
       'F10: and the row message about the thrown-away value goes with it');
    ok(stat(win, 'bulk-table', 't1') === before,
       'F9: and the header is back to the numbers the cells show (got '
       + stat(win, 'bulk-table', 't1') + ')');
    ok(c.classList.contains('cell-worst'), 'F9: q3 is marked the minimum again');
  }

  // ── F10: a row with ANOTHER pending edit keeps its message ─────────────────
  {
    const win = world();
    const c = inp(win, 'qubits.q3.T1'), other = inp(win, 'qubits.q3.f_01');
    const tr = c.closest('tr'), slot = tr.querySelector('.bulk-row-error');
    c.focus(); c.value = 'abc'; c.dispatchEvent(new win.Event('input', { bubbles: true }));
    win.BulkEdit.applyRow(tr.querySelector('.bulk-row-apply'));
    await tick(20);
    other.value = '4,810,000,000';                 // a second typed edit, still pending
    other.dispatchEvent(new win.Event('input', { bubbles: true }));
    escapeOn(win, c);
    ok(!slot.hidden && /expected a number/.test(slot.textContent),
       'F10: a row that still holds a pending edit keeps its message');
  }

  // ── F9 (b) + F10 on the pair grid (the Escape handler serves every grid) ───
  {
    const win = world();
    win.BulkPairEdit.recomputeStats();
    const before = stat(win, 'bulk-pair-table', 'det');
    const c = inp(win, 'qubit_pairs.q1-2.detuning');
    const tr = c.closest('tr'), slot = tr.querySelector('.bulk-row-error');
    c.focus(); c.value = 'abc'; c.dispatchEvent(new win.Event('input', { bubbles: true }));
    win.BulkPairEdit.applyRow(tr.querySelector('.bulk-row-apply'));
    await tick(20);
    ok(!slot.hidden, 'the pair row shows its refusal');
    ok(stat(win, 'bulk-pair-table', 'det') !== before, 'precondition: the pair header lost q1-2');
    escapeOn(win, c);
    ok(c.value === '9,000,000' && slot.hidden === true,
       'F10: Escape on a pair cell restores it and clears the row message');
    ok(stat(win, 'bulk-pair-table', 'det') === before,
       'F9: and the pair header is back (got ' + stat(win, 'bulk-pair-table', 'det') + ')');
  }

  // ── F9: Reset puts values back, so the header follows ──────────────────────
  {
    const win = world();
    win.BulkEdit.recomputeStats();
    const before = stat(win, 'bulk-table', 't1');
    const c = inp(win, 'qubits.q3.T1'), tr = c.closest('tr');
    c.focus(); c.value = 'abc'; c.dispatchEvent(new win.Event('input', { bubbles: true }));
    win.BulkEdit.applyRow(tr.querySelector('.bulk-row-apply'));
    await tick(20);
    win.BulkEdit.resetDirty();
    ok(stat(win, 'bulk-table', 't1') === before,
       'F9: Reset re-derives the header of the column it restored (got '
       + stat(win, 'bulk-table', 't1') + ')');
    const pw = world();
    pw.BulkPairEdit.recomputeStats();
    const pb = stat(pw, 'bulk-pair-table', 'det');
    const pc = inp(pw, 'qubit_pairs.q1-2.detuning'), ptr = pc.closest('tr');
    pc.focus(); pc.value = 'abc'; pc.dispatchEvent(new pw.Event('input', { bubbles: true }));
    pw.BulkPairEdit.applyRow(ptr.querySelector('.bulk-row-apply'));
    await tick(20);
    pw.BulkPairEdit.resetDirty();
    ok(stat(pw, 'bulk-pair-table', 'det') === pb, 'F9: the pair grid Reset too');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ' assertions)');
})().catch(function (e) { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
