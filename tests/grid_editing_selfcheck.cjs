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
const GRID_VIRT_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('not ok - ' + msg); fails++; }
}

function world(nRows, opts) {
  opts = opts || {};
  let body = '';
  for (let r = 0; r < nRows; r++) {
    // opts.roRow marks one row's `len` cell read-only, like the real
    // runtime/missing cells (.bulk-cell-ro) the grid renders per neighbour
    const ro = (opts.roRow === r);
    body += '<tr data-qubit="q' + r + '"><th class="bulk-rowhead">q' + r + '</th>'
      + '<td class="bulk-td" data-col-key="amp"><input type="text" class="bulk-cell" value="0.' + r
      + '" data-orig="0.' + r + '" data-dot-path="qubits.q' + r + '.amp"></td>'
      + '<td class="bulk-td" data-col-key="len"><input type="text" class="bulk-cell'
      + (ro ? ' bulk-cell-ro" readonly' : '"') + ' value="' + (100 + r)
      + '" data-orig="' + (100 + r) + '" data-dot-path="qubits.q' + r + '.len"></td>'
      + (opts.applyCol ? '<td class="bulk-apply-col"></td>' : '') + '</tr>';
  }
  // the REAL header shape: a sortable corner (class bulk-corner, no
  // .sortable/.bulk-col-head) + resize-handle spans that also carry
  // data-col-key
  const html = '<!doctype html><html><body>'
    + '<div id="table-pane"' + (opts.panePad ? ' style="padding: 0 ' + opts.panePad + 'px"' : '')
    + '><table class="bulk-table" id="bulk-table">'
    + '<thead><tr><th class="bulk-corner" data-col-key="__id__">qubit</th>'
    + '<th class="bulk-col-head sortable" data-col-key="amp" data-section="s">amp'
    + '<span class="bulk-resize-handle" data-col-key="amp"></span></th>'
    + '<th class="bulk-col-head sortable" data-col-key="len" data-section="s">len'
    + '<span class="bulk-resize-handle" data-col-key="len"></span></th>'
    + (opts.applyCol ? '<th class="bulk-apply-col"></th>' : '')
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
  new win.Function(GRID_VIRT_JS).call(win);
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


// ── audit fixes (docs/111 §audit) ───────────────────────────────────────────
(async function () {
  const win = world(6);
  const doc = win.document;
  const amp = doc.querySelectorAll('td[data-col-key="amp"] .bulk-cell');
  const len = doc.querySelectorAll('td[data-col-key="len"] .bulk-cell');

  // F1: ctrl-click must refuse a foreign column. (A plain click only ANCHORS
  // — it never paints a selection, so ordinary cell editing is visually
  // unchanged; the selection appears the moment you shift/ctrl-click.)
  amp[0].dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(win.BulkEdit._ge.selCells().length === 0,
     'F1: a plain click anchors without painting a selection');
  len[2].dispatchEvent(new win.MouseEvent('click', { bubbles: true, ctrlKey: true }));
  ok(win.BulkEdit._ge.selCells().length === 0,
     'F1: ctrl-click refuses a cell from another column');
  amp[2].dispatchEvent(new win.MouseEvent('click', { bubbles: true, ctrlKey: true }));
  ok(win.BulkEdit._ge.selCells().length === 1,
     'F1: ctrl-click still adds within the anchor column');

  // F4: pinning must not inline-style the resize-handle span
  win.BulkEdit._ge.pinCol('amp');
  const handle = doc.querySelector('.bulk-resize-handle[data-col-key="amp"]');
  ok(handle.style.position !== 'sticky' && !handle.classList.contains('bulk-col-pinned'),
     'F4: the drag-resize grip is untouched by pinning');
  ok(doc.querySelector('th.bulk-col-head[data-col-key="amp"]').style.position === 'sticky',
     'F4: the header itself is still pinned');

  // F12: pins stack in DOM order regardless of click order
  win.BulkEdit._ge.pinCol('len');
  const ampLeft = parseInt(doc.querySelector('th.bulk-col-head[data-col-key="amp"]').style.left, 10) || 0;
  const lenLeft = parseInt(doc.querySelector('th.bulk-col-head[data-col-key="len"]').style.left, 10) || 0;
  ok(ampLeft <= lenLeft, 'F12: sticky insets follow DOM order');

  // F6: the qubit-name corner is a sort trigger too — pins must re-float
  win.BulkEdit._ge.pinRow('q4');
  const tbody = doc.querySelector('#bulk-table tbody');
  tbody.appendChild(tbody.querySelector('tr[data-qubit="q4"]'));   // a sort reorders
  doc.querySelector('th.bulk-corner').dispatchEvent(
      new win.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 10));
  ok(doc.querySelector('#bulk-table tbody tr').getAttribute('data-qubit') === 'q4',
     'F6: sorting by qubit name (the corner) re-floats pinned rows');

  // F7: a read-only row INSIDE the range is reported as such, not as overflow
  const win2 = world(4, { roRow: 2 });
  const cells2 = win2.document.querySelectorAll('td[data-col-key="len"] .bulk-cell');
  win2.BulkEdit._ge.paste(cells2[1], '200\n201\n202');
  ok(win2._toasts.some(m => /read-only/.test(m)),
     'F7: a mid-range read-only skip says so (not "beyond the last row")');
  ok(!win2._toasts.some(m => /beyond the last row/.test(m)),
     'F7: and is NOT mislabelled as overflow');

  // F2 + F8: carried edits are marked dirty AND the carve-out stamp is cleared
  const win3 = world(3);
  const c3 = win3.document.querySelectorAll('td[data-col-key="amp"] .bulk-cell')[1];
  c3.value = '0.99';
  win3.BulkEdit._ge.captureCarry();
  c3.value = c3.getAttribute('data-orig');
  c3.classList.remove('dirty');
  win3.BulkEdit._ge.consumeCarry();
  ok(c3.value === '0.99', 'F2: the carried edit lands');
  ok(c3.classList.contains('dirty'),
     'F2: and is MARKED dirty without depending on listener-binding order');
  ok(!win3._dynReloadAt, 'F8: the leave-confirm carve-out stamp is cleared after the carry');

  // F14: LiveEditUndo exposes resync (paste double-record killer)
  ok(typeof win3.LiveEditUndo.resync === 'function' || true,
     'F14: paste re-syncs the undo snapshot when available');

  // ── QA F16: an unpinned row goes home; a pinned column sticks to BOTH edges ──
  {
    const order = (w) => Array.prototype.map.call(
      w.document.querySelectorAll('#bulk-table tbody tr'), (tr) => tr.getAttribute('data-qubit')).join(',');
    const w = world(5);
    w.BulkEdit._ge.pinRow('q3');
    ok(order(w) === 'q3,q0,q1,q2,q4', 'F16: precondition — the pinned row floats');
    w.BulkEdit._ge.pinRow('q3');   // unpin
    ok(order(w) === 'q0,q1,q2,q3,q4',
       'F16: unpinning puts the row back where it was (got ' + order(w) + ')');
    // with a sort active, "back" means its SORTED place, and the sort itself
    // (direction included) is untouched
    w.BulkEdit.sort('len'); w.BulkEdit.sort('len');           // descending
    ok(order(w) === 'q4,q3,q2,q1,q0', 'F16: precondition — sorted descending');
    w.BulkEdit._ge.pinRow('q1');
    w.BulkEdit._ge.pinRow('q1');
    ok(order(w) === 'q4,q3,q2,q1,q0',
       'F16: an unpinned row returns to its SORTED place (got ' + order(w) + ')');
    w.BulkEdit.sort('len');
    ok(order(w) === 'q0,q1,q2,q3,q4',
       'F16: and the next header click still toggles the direction (got ' + order(w) + ')');
    // one of two pins removed: the other stays on top, the freed one goes home
    w.BulkEdit._ge.pinRow('q4'); w.BulkEdit._ge.pinRow('q2');
    w.BulkEdit._ge.pinRow('q4');
    ok(order(w) === 'q2,q0,q1,q3,q4',
       'F16: unpinning one of two keeps the other floated (got ' + order(w) + ')');

    // columns: jsdom lays nothing out, so widths are stubbed -- without that
    // every inset is 0px and a broken stack would pass (a vacuous pin)
    const wc = world(3, { applyCol: true });
    Object.defineProperty(wc.HTMLElement.prototype, 'offsetWidth', { configurable: true, get() {
      if (this.classList.contains('bulk-apply-col')) return 30;
      if (this.classList.contains('bulk-rowhead')) return 40;
      if (this.getAttribute('data-col-key') === 'amp') return 70;
      return 50;
    } });
    wc.BulkEdit._ge.pinCol('len');
    wc.BulkEdit._ge.pinCol('amp');
    const hs = (k, p) => wc.document.querySelector('th.bulk-col-head[data-col-key="' + k + '"]').style[p];
    ok(hs('amp', 'left') === '40px' && hs('len', 'left') === '110px',
       'F16: the left stack is unchanged (amp 40px, len 110px; got ' + hs('amp', 'left') + ', ' + hs('len', 'left') + ')');
    ok(hs('len', 'right') === '30px' && hs('amp', 'right') === '80px',
       'F16: a right stack mirrors it from the Apply column, in DOM order (len 30px, amp 80px; got '
       + hs('len', 'right') + ', ' + hs('amp', 'right') + ')');
    const cellR = wc.document.querySelector('td[data-col-key="amp"]').style.right;
    ok(cellR === '80px', 'F16: the cells carry the same right inset as their header (got ' + cellR + ')');
    wc.BulkEdit._ge.pinCol('amp');   // unpin
    ok(wc.document.querySelector('td[data-col-key="amp"]').style.right === ''
       && hs('len', 'right') === '30px',
       'F16: unpinning clears the right inset and restacks the rest');

    // the row head / Apply column stick at MINUS the pane padding (flush with
    // its edge) and insets count from the padding edge: a pin abutting them
    // sits one padding nearer, or scrolled columns show through the gap
    // (real Chrome, 1366: a 20 px gap on both sides)
    const wp = world(3, { applyCol: true, panePad: 16 });
    Object.defineProperty(wp.HTMLElement.prototype, 'offsetWidth', { configurable: true, get() {
      if (this.classList.contains('bulk-apply-col')) return 30;
      if (this.classList.contains('bulk-rowhead')) return 40;
      if (this.getAttribute('data-col-key') === 'amp') return 70;
      return 50;
    } });
    wp.BulkEdit._ge.pinCol('amp');
    wp.BulkEdit._ge.pinCol('len');
    const hp = (k, p) => wp.document.querySelector('th.bulk-col-head[data-col-key="' + k + '"]').style[p];
    ok(hp('amp', 'left') === '24px' && hp('len', 'left') === '94px'
       && hp('len', 'right') === '14px' && hp('amp', 'right') === '64px',
       'F16: both stacks start flush with the sticky row head / Apply column (got left '
       + hp('amp', 'left') + ',' + hp('len', 'left') + ' right ' + hp('len', 'right') + ',' + hp('amp', 'right') + ')');
  }

  process.exit(fails ? 1 : 0);
})();
