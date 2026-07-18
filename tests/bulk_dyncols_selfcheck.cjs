// Behavioral check for r6 item 4 — Table View full coverage (the REAL
// bulk-edit.js under jsdom):
//  - openJsonCell modal: prefills from /field/peek's RAW value; bad JSON shows
//    an inline error (no POST); a server 400 renders the row error inline;
//    Ctrl+Enter posts the PARSED value (never a string) with expect_chip; on
//    success the preview cell updates + gets the committed (red) marker, the
//    tray swaps, _diagChanged fires, the modal closes; Esc cancels
//  - search hint: a query matching a NOT-enabled dynamic column shows the
//    "N hidden columns match — Show" chip; Show appends the matched keys to
//    localStorage quam_bulk_dyncols and reloads the pane via htmx.ajax
//  - configRequest: /bulk GETs gain dyncols= from localStorage; /bulk/all-values
//    and other paths are untouched; an existing dyncols= is replaced, not duped
//
// Run: node tests/bulk_dyncols_selfcheck.cjs   (needs jsdom)
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

const ROOT = path.join(__dirname, '..');
const BULK_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');

const MATRIX = 'qubits.qA1.resonator.confusion_matrix';
const PORT_ALIAS = 'qubits.qA1.z.opx_output.exponential_filter';    // raw path NOT navigable
const PORT_RESOLVED = 'ports.analog_outputs.con1.2.1.exponential_filter';

// Minimal Table View: toolbar (search + count + hint + colvis menu) and a
// 2-column grid — one curated editable cell, one dynamic listedit cell.
const DOM = `
<div id="bulk-panel">
<div id="table-pane">
  <div class="bulk-toolbar">
    <details class="bulk-colvis"><summary>Properties</summary>
      <div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>
    <span class="bulk-search-wrap">
      <input type="search" id="bulk-search">
      <span id="bulk-search-count"></span>
      <button type="button" id="bulk-dyncol-hint" hidden></button>
    </span>
    <span id="bulk-dirty-count"></span>
    <button id="bulk-apply-all" disabled></button>
    <button id="bulk-reset" disabled></button>
  </div>
  <div class="bulk-table-wrap">
  <table id="bulk-table">
    <thead>
      <tr class="bulk-group-row">
        <th class="bulk-corner" data-col-key="__id__">qubit</th>
        <th class="bulk-group-head" data-group="Frequencies" colspan="1">Frequencies</th>
        <th class="bulk-group-head" data-group="Resonator+" colspan="1">Resonator+</th>
      </tr>
      <tr class="bulk-head-row">
        <th class="bulk-col-head" data-col-key="f_01" data-section="Frequencies"><span class="bulk-col-label">Qubit f01</span></th>
        <th class="bulk-col-head" data-col-key="dyn__resonator_confusion_matrix" data-section="Resonator+"><span class="bulk-col-label">confusion_matrix</span></th>
      </tr>
    </thead>
    <tbody>
      <tr data-qubit="qA1">
        <th class="bulk-rowhead" data-col-key="__id__">qA1</th>
        <td class="bulk-td" data-col-key="f_01">
          <input class="bulk-cell" value="6,250,000,000" data-orig="6,250,000,000"
                 data-dot-path="qubits.qA1.f_01" data-resolved="qubits.qA1.f_01" data-linkable="1">
          <span class="bulk-ba"><span class="bulk-ba-old"></span> → <span class="bulk-ba-new"></span></span>
        </td>
        <td class="bulk-td bulk-td-ro" data-col-key="dyn__resonator_confusion_matrix">
          <span class="bulk-cell-list" data-path="${MATRIX}">[[0.98,0.02],[0.03,0.9…</span>
          <button type="button" class="bulk-list-edit">✎</button>
        </td>
        <td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>
          <span class="bulk-row-error" hidden></span></td>
      </tr>
    </tbody>
  </table>
  </div>
</div>
</div>`;

const COLS = [
  { key: 'f_01', label: 'Qubit f01', section: 'Frequencies', unit: 'Hz', default_on: true },
  { key: 'dyn__resonator_confusion_matrix', label: 'confusion_matrix', section: 'Resonator+',
    unit: '', default_on: true, dyn: true }
];
const DYN = [
  { key: 'dyn__resonator_confusion_matrix', label: 'confusion_matrix', section: 'Resonator+', unit: '', kind: 'listedit' },
  { key: 'dyn__z_opx_output_exponential_filter', label: 'out · exponential_filter', section: 'Z Port+', unit: '', kind: 'listedit' },
  { key: 'dyn__xy_operations_x180_DragCosine_alpha', label: 'op · x180_DragCosine · alpha', section: 'XY+', unit: '', kind: 'edit' }
];

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.__chipToken = 'tok';
  win._log = { peek: 0, editBatch: [], tray: [], diag: 0, ajax: [] };
  win._editBatchQueue = [];       // shift()ed per POST; empty → generic ok
  win._swapPendingTray = function (h) { win._log.tray.push(h); };
  win._diagChanged = function () { win._log.diag++; };
  win.htmx = { ajax: function (verb, url, opts) { win._log.ajax.push([verb, url, opts]); } };
  function jsonResp(data) {
    return Promise.resolve({ status: 200, json: function () { return Promise.resolve(data); } });
  }
  win.fetch = function (url, opts) {
    if (url.indexOf('/field/peek') === 0) {
      win._log.peek++;
      const p = decodeURIComponent(url.split('dot_path=')[1]);
      // the port ALIAS is not raw-navigable (io key is a pointer string):
      // values[alias]=null + a resolved block, like the real /field/peek
      if (p === PORT_ALIAS) {
        const values = {}; values[PORT_ALIAS] = null;
        const resolved = {};
        resolved[PORT_ALIAS] = { resolved_path: PORT_RESOLVED, resolvable: true };
        return jsonResp({ ok: true, values: values,
          errors: { [PORT_ALIAS]: 'Cannot traverse' }, resolved: resolved });
      }
      if (p === PORT_RESOLVED) {
        const values = {}; values[PORT_RESOLVED] = [[0.8, 120.0]];
        return jsonResp({ ok: true, values: values, errors: {}, resolved: {} });
      }
      const values = {}; values[MATRIX] = [[0.98, 0.02], [0.03, 0.97]];
      return jsonResp({ ok: true, values: values, errors: {}, resolved: {} });
    }
    if (url === '/field/edit-batch') {
      const body = JSON.parse(opts.body);
      win._log.editBatch.push(body);
      const r = win._editBatchQueue.length ? win._editBatchQueue.shift()
        : { ok: true, tray_html: '<div id="pending-tray">tray</div>',
            results: body.updates.map(function (u) {
              return { dot_path: u.dot_path, resolved_path: u.dot_path,
                applied: true, new_value: u.value, display: String(u.value) };
            }) };
      return jsonResp(r);
    }
    return Promise.reject(new Error('unexpected fetch ' + url));
  };
  new win.Function(BULK_JS).call(win);
  return win;
}

function keydown(win, el, key, ctrl) {
  el.dispatchEvent(new win.KeyboardEvent('keydown',
    { key: key, ctrlKey: !!ctrl, bubbles: true, cancelable: true }));
}

async function checkJsonModal() {
  const win = makeWorld();
  const doc = win.document;
  win.BulkEdit.mount(COLS, { bands: {} }, DYN);

  const btn = doc.querySelector('.bulk-list-edit');
  win.BulkEdit.openJsonCell(MATRIX, btn);
  await tick(10);
  let modal = doc.getElementById('bulk-json-modal');
  ok(!!modal, 'openJsonCell creates the modal');
  ok(win._log.peek === 1, 'prefill fetched /field/peek once');
  const ta = modal.querySelector('.bulk-json-ta');
  ok(ta.value === JSON.stringify([[0.98, 0.02], [0.03, 0.97]], null, 2),
    'textarea prefilled from the RAW peek value, got: ' + ta.value);
  ok(modal.textContent.indexOf(MATRIX) >= 0, 'modal shows the dot path');

  // bad JSON → inline error, NO POST
  ta.value = '[[0.98';
  keydown(win, ta, 'Enter', true);
  await tick(5);
  let err = modal.querySelector('.bulk-json-err');
  ok(!err.hidden && /Invalid JSON/.test(err.textContent), 'bad JSON shows an inline error');
  ok(win._log.editBatch.length === 0, 'bad JSON never POSTs');
  ok(!!doc.getElementById('bulk-json-modal'), 'modal stays open on bad JSON');

  // server 400 → inline error, modal stays open
  ta.value = '[[1,0],[0,1]]';
  win._editBatchQueue.push({ ok: false, results: [
    { dot_path: MATRIX, applied: false, error: 'matrix must be 2x2 of floats' }] });
  keydown(win, ta, 'Enter', true);
  await tick(10);
  err = modal.querySelector('.bulk-json-err');
  ok(!err.hidden && /matrix must be 2x2/.test(err.textContent), 'server 400 error shown inline');
  ok(!!doc.getElementById('bulk-json-modal'), 'modal stays open on a server error');

  // success → PARSED value posted, cell preview + marker updated, tray + diag, modal closed
  keydown(win, ta, 'Enter', true);   // same valid JSON, default ok response
  await tick(10);
  ok(win._log.editBatch.length === 2, 'two POSTs total (400 + ok)');
  const body = win._log.editBatch[1];
  ok(body.expect_chip === 'tok', 'expect_chip stamped');
  ok(Array.isArray(body.updates[0].value)
    && JSON.stringify(body.updates[0].value) === '[[1,0],[0,1]]',
    'Ctrl+Enter posts the PARSED array, never a string');
  ok(!doc.getElementById('bulk-json-modal'), 'modal closes on success');
  const prev = doc.querySelector('.bulk-cell-list');
  ok(prev.textContent === '[[1,0],[0,1]]', 'preview cell updated, got: ' + prev.textContent);
  ok(prev.classList.contains('bulk-cell-modified'), 'cell carries the committed marker');
  ok(win._log.tray.length === 1, 'tray swapped once');
  ok(win._log.diag === 1, '_diagChanged fired');

  // Esc cancels without posting
  win.BulkEdit.openJsonCell(MATRIX, btn);
  await tick(10);
  modal = doc.getElementById('bulk-json-modal');
  keydown(win, modal.querySelector('.bulk-json-ta'), 'Escape');
  ok(!doc.getElementById('bulk-json-modal'), 'Esc closes the modal');
  ok(win._log.editBatch.length === 2, 'Esc never POSTs');

  // port-ALIAS path (raw not navigable) → prefill falls back to the RESOLVED path
  win.BulkEdit.openJsonCell(PORT_ALIAS, btn);
  await tick(15);
  modal = doc.getElementById('bulk-json-modal');
  ok(modal.querySelector('.bulk-json-ta').value
      === JSON.stringify([[0.8, 120.0]], null, 2),
    'alias prefill re-peeks the resolved port path');
  keydown(win, modal.querySelector('.bulk-json-ta'), 'Escape');
}

async function checkSearchHint() {
  const win = makeWorld();
  const doc = win.document;
  // one dyn column already enabled; the exponential_filter one is NOT
  win.localStorage.setItem('quam_bulk_dyncols',
    JSON.stringify(['dyn__resonator_confusion_matrix']));
  win.BulkEdit.mount(COLS, { bands: {} }, DYN);

  const search = doc.getElementById('bulk-search');
  const hint = doc.getElementById('bulk-dyncol-hint');
  search.value = 'exponential';
  search.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(!hint.hidden, 'hint chip appears for a matching disabled dynamic column');
  ok(/1 hidden column match/.test(hint.textContent),
    'hint counts the match, got: ' + hint.textContent);

  win.BulkEdit.showMatchedDynCols();
  const saved = JSON.parse(win.localStorage.getItem('quam_bulk_dyncols'));
  ok(saved.indexOf('dyn__z_opx_output_exponential_filter') >= 0,
    'Show appends the matched key to quam_bulk_dyncols');
  ok(saved.indexOf('dyn__resonator_confusion_matrix') >= 0,
    'already-enabled keys survive');
  ok(win._log.ajax.length === 1 && win._log.ajax[0][1] === '/bulk'
    && win._log.ajax[0][2].target === '#table-pane',
    'Show reloads the pane via htmx.ajax GET /bulk');

  // an ENABLED column never counts as hidden
  search.value = 'confusion';
  search.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(hint.hidden, 'no hint when every matching dyn column is already enabled');

  // sub-2-char queries never hint
  search.value = 'e';
  search.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(hint.hidden, 'a 1-char query never hints');

  // the colvis menu carries the collapsible dynamic groups with counts
  const menu = doc.getElementById('bulk-colvis-menu');
  ok(menu.querySelectorAll('details.bulk-colvis-dyn').length === 3,
    'one collapsible group per dynamic section');
  ok(/Z Port\+ /.test(menu.textContent) || menu.textContent.indexOf('Z Port+') >= 0,
    'group header names the section');
  ok(menu.querySelectorAll('[data-dyn-toggle]').length === 3,
    'every dynamic column has a toggle');
  const cb = menu.querySelector('[data-dyn-toggle="dyn__xy_operations_x180_DragCosine_alpha"]');
  ok(cb && !cb.checked, 'disabled dyn column starts unchecked');
  cb.checked = true;
  cb.dispatchEvent(new win.Event('change', { bubbles: true }));
  const saved2 = JSON.parse(win.localStorage.getItem('quam_bulk_dyncols'));
  ok(saved2.indexOf('dyn__xy_operations_x180_DragCosine_alpha') >= 0,
    'menu toggle adds the key');
  ok(win._log.ajax.length === 2, 'menu toggle reloads the pane');
}

async function checkConfigRequest() {
  const win = makeWorld();
  const doc = win.document;
  win.localStorage.setItem('quam_bulk_dyncols', JSON.stringify(['dyn__a', 'dyn__b']));

  function fire(path) {
    const evt = new win.CustomEvent('htmx:configRequest',
      { detail: { path: path, parameters: {}, elt: doc.body } });
    doc.dispatchEvent(evt);
    return evt.detail.path;
  }
  ok(fire('/bulk') === '/bulk?dyncols=' + encodeURIComponent('dyn__a,dyn__b'),
    '/bulk gains dyncols from localStorage');
  ok(fire('/bulk?foo=1').indexOf('foo=1') >= 0 && /dyncols=/.test(fire('/bulk?foo=1')),
    'existing params preserved');
  ok(fire('/bulk?dyncols=stale') === '/bulk?dyncols=' + encodeURIComponent('dyn__a,dyn__b'),
    'a baked dyncols is replaced, never duplicated');
  ok(fire('/bulk/all-values') === '/bulk/all-values', '/bulk/all-values untouched');
  ok(fire('/pulses?q=x') === '/pulses?q=x', 'other paths untouched');

  win.localStorage.setItem('quam_bulk_dyncols', '[]');
  ok(fire('/bulk?dyncols=stale') === '/bulk', 'empty set strips a stale dyncols');
}

(async function () {
  await checkJsonModal();
  await checkSearchHint();
  await checkConfigRequest();
  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('bulk dyncols selfcheck: all checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
