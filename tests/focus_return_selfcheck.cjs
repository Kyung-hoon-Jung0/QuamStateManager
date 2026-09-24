// QA liveedit-r2-21 -- focus comes back after a grid dialog or an Apply.
//
// Two ways the keyboard walk used to restart from the top of the page:
//  (b) the ✎ JSON cell editor removed its overlay (with the focused textarea
//      inside it) and never refocused the ✎ that opened it -> <body>;
//  (c) Apply all / Apply to live disables the pressed button; Chrome's focus
//      fixup then moves focus to <body>, and after a clean apply the button
//      stays disabled, so nothing ever took focus back.
// jsdom has no focus-fixup rule, so (c) blurs the button by hand right after
// the press -- exactly what Chrome does -- and asserts where focus lands once
// the apply resolves.
//
// Drives the REAL bulk-edit.js + pair-edit.js (+ grid-virt.js) under jsdom.
// Run: node tests/focus_return_selfcheck.cjs   (driven by test_focus_return.py)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const GRID_VIRT_JS = fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8');
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

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
// a list cell exactly as _bulk_cell_macros.html renders it (preview span + ✎)
function listTd(dp) {
  return '<td class="bulk-td" data-col-key="fl"><span class="bulk-cell-list" data-path="' + dp
    + '" data-resolved="' + dp + '" tabindex="0">[1, 2]</span> <button type="button" class="bulk-list-edit"'
    + ' onclick="BulkEdit.openJsonCell(\'' + dp + '\', this)">&#9998;</button></td>';
}
function qubitGrid() {
  const rows = ['q1', 'q2'].map(function (q, i) {
    return '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>'
      + qtd('f01', 'qubits.' + q + '.f_01', String(4800000000 + i))
      + listTd('qubits.' + q + '.z.filter') + applyCol() + '</tr>';
  }).join('');
  return '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
    + '<span id="bulk-search-hint"></span></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-apply-sync" disabled></button>'
    + '<button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head('f01') + head('fl')
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function pairGrid() {
  const rows = ['q1-2', 'q2-3'].map(function (id, i) {
    return '<tr data-qubit="' + id + '" data-pair="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">'
      + id + '</th>' + qtd('det', 'qubit_pairs.' + id + '.detuning', String(5000000 + i)) + applyCol() + '</tr>';
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
  win.confirm = function () { return true; };
  win.fetches = [];
  // /field/edit-batch accepting every update, in the real response shape
  win.fetch = function (url, opts) {
    win.fetches.push(url);
    const body = JSON.parse(opts.body);
    const results = body.updates.map(function (u) {
      return { dot_path: u.dot_path, applied: true, display: String(u.value) };
    });
    return Promise.resolve({ status: 200, ok: true,
      json: function () { return Promise.resolve({ ok: true, results: results }); } });
  };
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  new win.Function(PAIR_JS).call(win);
  win.BulkEdit.mount(
    [{ key: 'f01', label: 'f01', section: 'S', unit: '', default_on: true },
     { key: 'fl', label: 'fl', section: 'S', unit: '', default_on: true }],
    { bands: {} }, [], { chip: 'chipA', qubits: [{ id: 'q1', grid: null }, { id: 'q2', grid: null }] });
  win.BulkPairEdit.mount([{ key: 'det', label: 'det', section: 'S', unit: '', default_on: true,
                             editable: true, kind: 'scalar', maxlen: 12 }]);
  return win;
}

const inp = (win, dp) => win.document.querySelector('.bulk-cell[data-dot-path="' + dp + '"]');
function type(win, cell, v) {
  cell.focus();
  cell.value = v;
  cell.dispatchEvent(new win.Event('input', { bubbles: true }));
}
// A real click on a toolbar button: the cell loses focus TO the button.
function press(win, btnId, call) {
  const b = win.document.getElementById(btnId);
  b.focus();
  call();
  // Chrome's focus fixup: a focused control that becomes disabled loses focus.
  // (jsdom's blur() refuses a disabled element, so lift it for the blur.)
  if (b.disabled && win.document.activeElement === b) {
    b.disabled = false; b.blur(); b.disabled = true;
  }
  return b;
}

(async function main() {
  // ── (b) the ✎ JSON cell editor ─────────────────────────────────────────
  {
    const win = world();
    const doc = win.document;
    const pen = doc.querySelector('tr[data-qubit="q1"] .bulk-list-edit');
    pen.focus();
    win.BulkEdit.openJsonCell('qubits.q1.z.filter', pen, '[1, 2]');
    const ta = doc.querySelector('#bulk-json-modal .bulk-json-ta');
    ok(!!ta && doc.activeElement === ta, 'precondition: the editor opens with its textarea focused');
    ta.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(!doc.getElementById('bulk-json-modal'), 'Esc closes the JSON editor');
    ok(doc.activeElement === pen,
       'Esc returns focus to the ✎ that opened it (got ' + doc.activeElement.tagName + ')');

    win.BulkEdit.openJsonCell('qubits.q1.z.filter', pen, '[1, 2]');
    doc.querySelector('#bulk-json-modal [data-bulk-json-cancel]').click();
    ok(doc.activeElement === pen, 'Cancel returns focus to the ✎ as well');

    win.BulkEdit.openJsonCell('qubits.q1.z.filter', pen, '[3, 4]');
    doc.querySelector('#bulk-json-modal [data-bulk-json-save]').click();
    await tick(30);
    ok(!doc.getElementById('bulk-json-modal') && win.fetches.length === 1,
       'precondition: Save posted once and closed the editor');
    ok(doc.activeElement === pen, 'a successful Save returns focus to the ✎ too');
  }

  // ── (c) Apply to live now / Apply all -- qubit grid ───────────────────
  {
    const win = world();
    const doc = win.document;
    win.applyEditsToLive = function () { win._pushed = (win._pushed || 0) + 1; };
    const cell = inp(win, 'qubits.q2.f_01');
    type(win, cell, '4900000000');
    ok(!doc.getElementById('bulk-apply-sync').disabled, 'precondition: Apply to live is armed');
    const b = press(win, 'bulk-apply-sync', function () { win.BulkEdit.applyAll(true); });
    ok(b.disabled && doc.activeElement === doc.body,
       'precondition: the pressed button went disabled and dropped focus to <body>');
    await tick(40);
    ok(win._pushed === 1, 'precondition: the apply landed and pushed to live');
    ok(doc.activeElement === cell,
       'Apply to live hands focus back to the edited cell (got ' + doc.activeElement.tagName
       + (doc.activeElement.getAttribute ? ' ' + (doc.activeElement.getAttribute('data-dot-path') || doc.activeElement.id || '') : '') + ')');

    // ...but never pulls focus away from where the user went meanwhile
    const cell2 = inp(win, 'qubits.q1.f_01');
    type(win, cell2, '4700000000');
    press(win, 'bulk-apply-all', function () { win.BulkEdit.applyAll(false); });
    const search = doc.getElementById('bulk-search');
    search.focus();
    await tick(40);
    ok(doc.activeElement === search, 'focus the user moved elsewhere mid-apply stays there');
  }

  // ── (c) the pair grid's Apply to live (twin in pair-edit.js) ──────────
  {
    const win = world();
    const doc = win.document;
    win.applyEditsToLive = function () {};
    const cell = inp(win, 'qubit_pairs.q2-3.detuning');
    type(win, cell, '6000000');
    ok(!doc.getElementById('bulk-pair-apply-sync').disabled, 'precondition: pair Apply to live is armed');
    press(win, 'bulk-pair-apply-sync', function () { win.BulkPairEdit.applyAll(true); });
    ok(doc.activeElement === doc.body, 'precondition: pair button dropped focus to <body>');
    await tick(40);
    ok(cell.value === '6000000' && cell.getAttribute('data-orig') === '6000000',
       'precondition: the pair apply landed');
    ok(doc.activeElement === cell,
       'pair Apply to live hands focus back to the edited cell (got ' + doc.activeElement.tagName + ')');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ' assertions)');
  process.exit(0);
})().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
