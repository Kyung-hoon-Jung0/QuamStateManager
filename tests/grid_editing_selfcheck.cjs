// docs/111 (#11) — the 21-qubit-retune toolkit in the REAL bulk-edit.js
// under jsdom: same-column multi-select + fill-down, paste-a-column,
// row/column pinning (persisted; survives a sort), and the dyn-reload
// unsaved-edit carry. Entirely client-side — /bulk HTML is byte-identical.
//
// Run: node tests/grid_editing_selfcheck.cjs   (driven by tests/test_grid_editing.py)
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const BULK_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('not ok - ' + msg); fails++; }
}

function world(nRows) {
  let body = '';
  for (let r = 0; r < nRows; r++) {
    body += '<tr data-qubit="q' + r + '"><th class="bulk-rowhead">q' + r + '</th>'
      + '<td class="bulk-td" data-col-key="amp"><input type="text" class="bulk-cell" value="0.' + r
      + '" data-orig="0.' + r + '" data-dot-path="qubits.q' + r + '.amp"></td>'
      + '<td class="bulk-td" data-col-key="len"><input type="text" class="bulk-cell" value="' + (100 + r)
      + '" data-orig="' + (100 + r) + '" data-dot-path="qubits.q' + r + '.len"></td></tr>';
  }
  const html = '<!doctype html><html><body>'
    + '<div id="table-pane"><table class="bulk-table" id="bulk-table">'
    + '<thead><tr><th></th>'
    + '<th class="bulk-col-head sortable" data-col-key="amp" data-section="s">amp</th>'
    + '<th class="bulk-col-head sortable" data-col-key="len" data-section="s">len</th>'
    + '</tr></thead><tbody>' + body + '</tbody></table></div>';
  const dom = new JSDOM(html, { url: 'http://localhost/bulk', pretendToBeVisual: true,
                              runScripts: 'outside-only' });
  const win = dom.window;
  const store = {};
  // window.localStorage is getter-only under jsdom — shadow it instead
  Object.defineProperty(win, 'localStorage', { configurable: true, value: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  } });
  Object.defineProperty(win, 'sessionStorage', { configurable: true, value: win.localStorage });
  win.__chipToken = 'chipT';
  win.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
  const toasts = [];
  win.showToast = (m) => toasts.push(m);
  win._toasts = toasts;
  const recs = [];
  win.LiveEditUndo = { record: (label, cells) => recs.push({ label, cells }), clear: () => {} };
  win._leuRecs = recs;
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount([{ key: 'amp', label: 'amp', section: 's', default_on: true },
                      { key: 'len', label: 'len', section: 's', default_on: true }]);
  return win;
}

// ── multi-select + fill-down ────────────────────────────────────────────────
{
  const win = world(6);
  const doc = win.document;
  const cells = doc.querySelectorAll('td[data-col-key="amp"] .bulk-cell');
  // click q0, shift-click q3 → 4 selected in the amp column
  cells[0].dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  cells[3].dispatchEvent(new win.MouseEvent('click', { bubbles: true, shiftKey: true }));
  ok(win.BulkEdit._ge.selCells().length === 4, 'shift-click selects the column range');
  // anchor value fills the selection
  cells[0].value = '0.5';
  const n = win.BulkEdit._ge.fill();
  ok(n === 3, 'fill-down fills the other 3 selected cells');
  ok(cells[1].value === '0.5' && cells[3].value === '0.5', 'values landed');
  ok(cells[4].value === '0.4', 'cells outside the selection untouched');
  ok(win._leuRecs.length === 1 && win._leuRecs[0].cells.length === 3,
     'fill-down records ONE LiveEditUndo action');
  // cross-column shift-click never selects
  const lenCell = doc.querySelectorAll('td[data-col-key="len"] .bulk-cell')[2];
  cells[0].dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  lenCell.dispatchEvent(new win.MouseEvent('click', { bubbles: true, shiftKey: true }));
  ok(win.BulkEdit._ge.selCells().length === 0, 'cross-column ranges are refused');
}

// ── paste a column ──────────────────────────────────────────────────────────
{
  const win = world(4);
  const doc = win.document;
  const cells = doc.querySelectorAll('td[data-col-key="len"] .bulk-cell');
  const handled = win.BulkEdit._ge.paste(cells[1], '200\n201\n202\n203\n204');
  ok(handled === true, 'multi-line paste is handled');
  ok(cells[1].value === '200' && cells[2].value === '201' && cells[3].value === '202',
     'values fill downward from the focused cell');
  ok(doc.querySelectorAll('td[data-col-key="amp"] .bulk-cell')[1].value === '0.1',
     'the other column is untouched');
  ok(win._toasts.some(m => /2 beyond the last row ignored/.test(m)),
     'overflow beyond the last row is REPORTED, not silent');
  ok(win.BulkEdit._ge.paste(cells[0], '42') === false, 'single-value paste stays native');
}

// ── pinning ────────────────────────────────────────────────────────────────
{
  const win = world(5);
  const doc = win.document;
  ok(doc.querySelectorAll('.bulk-pin-col').length === 2
     && doc.querySelectorAll('.bulk-pin-row').length === 5,
     'pin glyphs are JS-injected (server HTML untouched)');
  win.BulkEdit._ge.pinRow('q3');
  const firstRow = doc.querySelector('#bulk-table tbody tr');
  ok(firstRow.getAttribute('data-qubit') === 'q3', 'a pinned row floats to the top');
  ok(firstRow.classList.contains('bulk-row-pinned'), 'pinned row is marked');
  ok(win.localStorage.getItem('quam_bulk_pinned_rows::chipT') === '["q3"]',
     'row pins persist per chip');
  win.BulkEdit._ge.pinCol('len');
  const th = doc.querySelector('th.bulk-col-head[data-col-key="len"]');
  ok(th.classList.contains('bulk-col-pinned') && th.style.position === 'sticky',
     'a pinned column goes sticky');
  win.BulkEdit._ge.pinRow('q3');   // unpin
  ok(!doc.querySelector('tr.bulk-row-pinned'), 'unpinning clears the mark');
}

// ── dyn-reload edit carry ───────────────────────────────────────────────────
{
  const win = world(3);
  const doc = win.document;
  const c = doc.querySelectorAll('td[data-col-key="amp"] .bulk-cell')[1];
  c.value = '0.99';                       // dirty, unsaved
  win.BulkEdit._ge.captureCarry();
  ok(typeof win._dynReloadAt === 'number', 'the carry arms the leave-confirm carve-out');
  // simulate the reload: fresh server DOM (values back to data-orig)
  c.value = c.getAttribute('data-orig');
  win.BulkEdit._ge.consumeCarry();
  ok(c.value === '0.99', 'the unsaved edit survives the dyn-column reload');
  ok(win._toasts.some(m => /1 unsaved edit preserved/.test(m)),
     'the carry is announced');
}

process.exit(fails ? 1 : 0);
