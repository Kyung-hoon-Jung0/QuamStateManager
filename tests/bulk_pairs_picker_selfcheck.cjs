// docs/141 §4s — the ⚯ Pairs picker beside ⚏ Qubits on Live State Edit,
// against the REAL bulk-edit.js under jsdom:
//  - the menu lists every pair row with All / None / Invert / only
//  - unchecking a pair hides ITS row (bulk-qubit-off), persists per chip
//    (quam_bulk_qhidden:pairs:<chip>), and the pill says "N of M pairs"
//  - a pair with an unsaved edit can never be hidden (None / Invert skip it,
//    its checkbox is disabled)
//  - a pair whose qubit is hidden by the Qubits picker follows it and says so
//  - Show all (the pill) clears the set; a fresh page applies the remembered set
//
// Run: node tests/bulk_pairs_picker_selfcheck.cjs   (needs jsdom)
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
const BULK_JS = fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const QUBITS = ['q1', 'q2', 'q3'];
const PAIRS = ['q1-2', 'q2-3', 'q1-3'];
function cell(dp, v) {
  return '<td class="bulk-td ck-0" data-col-key="f"><input type="text" class="bulk-cell" value="' + v + '" data-orig="' + v
    + '" data-dot-path="' + dp + '" data-resolved="' + dp + '" data-linkable="1" size="8"></td>';
}
function build() {
  let qrows = '', prows = '';
  QUBITS.forEach(function (q, i) { qrows += '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>' + cell('qubits.' + q + '.f_01', 100 + i) + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr>'; });
  PAIRS.forEach(function (p, i) { prows += '<tr data-qubit="' + p + '" data-pair="' + p + '"><th class="bulk-rowhead" data-col-key="__id__">' + p + '</th>' + cell('qubit_pairs.' + p + '.gates.CZ.amplitude', (i + 1) / 10) + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr>'; });
  return '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>P</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<details class="bulk-colvis bulk-qubitvis"><summary>Q</summary><div class="bulk-colvis-menu" id="bulk-qubitvis-menu"></div></details>'
    + '<button id="bulk-qubit-pill" hidden></button>'
    + '<details class="bulk-colvis bulk-pairvis"><summary>Pairs</summary><div class="bulk-colvis-menu" id="bulk-pairvis-menu"></div></details>'
    + '<button id="bulk-pair-pill" hidden></button>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-dirty-count"></span><button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th><th class="bulk-col-head ck-0" data-col-key="f"><span class="bulk-col-label">f</span></th></tr></thead><tbody>' + qrows + '</tbody></table></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-pair-table"><thead><tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th><th class="bulk-col-head ck-0" data-col-key="f"><span class="bulk-col-label">f</span></th></tr></thead><tbody>' + prows + '</tbody></table></div>'
    + '</div></div>';
}
function world(storage) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + build() + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  if (storage) Object.keys(storage).forEach(function (k) { win.localStorage.setItem(k, storage[k]); });
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount([{ key: 'f', label: 'f', section: 'S', unit: '', default_on: true }], { bands: {} }, [],
    { chip: 'chipA', qubits: QUBITS.map(function (q) { return { id: q, grid: null }; }) });
  return win;
}
const off = (doc, pid) => doc.querySelector('tr[data-pair="' + pid + '"]').classList.contains('bulk-qubit-off');
const cb = (doc, pid) => doc.querySelector('#bulk-pairvis-menu input[data-pcb="' + pid + '"]');

let win = world();
let doc = win.document;
const menu = doc.getElementById('bulk-pairvis-menu');
ok(menu.querySelectorAll('input[data-pcb]').length === 3 && menu.querySelector('[data-psel="all"]') && menu.querySelector('[data-psel="none"]') && menu.querySelector('[data-psel="invert"]'),
   'the Pairs menu lists every pair with All / None / Invert');
ok(Array.from(menu.querySelectorAll('input[data-pcb]')).every((c) => c.checked) && doc.getElementById('bulk-pair-pill').hidden,
   'everything shown at first, no pill');
cb(doc, 'q2-3').checked = false; cb(doc, 'q2-3').dispatchEvent(new win.Event('change', { bubbles: true }));
ok(off(doc, 'q2-3') && !off(doc, 'q1-2') && !off(doc, 'q1-3'), 'unchecking a pair hides its row only');
ok(JSON.parse(win.localStorage.getItem('quam_bulk_qhidden:pairs:chipA')).join(',') === 'q2-3', 'persisted per chip under its own key');
const pill = doc.getElementById('bulk-pair-pill');
ok(!pill.hidden && pill.textContent === '2 of 3 pairs — Show all', 'the pill says how many are shown (' + pill.textContent + ')');
menu.querySelector('[data-ponly="q1-3"]').click();
ok(off(doc, 'q1-2') && off(doc, 'q2-3') && !off(doc, 'q1-3'), '"only" keeps one pair');
win.BulkEdit.showAllPairs();
ok(!off(doc, 'q1-2') && !off(doc, 'q2-3') && !off(doc, 'q1-3') && pill.hidden && win.BulkEdit._pairsHidden().length === 0, 'Show all clears the set');

// a dirty pair never hides
const dirtyCell = doc.querySelector('tr[data-pair="q1-2"] .bulk-cell');
dirtyCell.value = '0.99'; dirtyCell.dispatchEvent(new win.Event('input', { bubbles: true }));
menu.querySelector('[data-psel="none"]').click();
ok(!off(doc, 'q1-2') && off(doc, 'q2-3') && off(doc, 'q1-3'), 'None hides every pair except the one with an unsaved edit');
ok(cb(doc, 'q1-2').disabled && cb(doc, 'q1-2').checked && /unsaved edit/.test(menu.textContent), 'its checkbox is disabled and says why');
menu.querySelector('[data-psel="invert"]').click();
ok(!off(doc, 'q1-2') && !off(doc, 'q2-3') && !off(doc, 'q1-3'), 'Invert flips the others and leaves the dirty pair shown');
dirtyCell.value = '0.1'; dirtyCell.dispatchEvent(new win.Event('input', { bubbles: true }));

// a dirty pair is not taken away by the Qubits picker either (the follow
// rule's own guard -- the only path that could hide a row someone is editing)
dirtyCell.value = '0.55'; dirtyCell.dispatchEvent(new win.Event('input', { bubbles: true }));
win.localStorage.setItem('quam_bulk_qhidden:chipA', JSON.stringify(['q1']));
win.BulkEdit.showAllQubits();
win.localStorage.setItem('quam_bulk_qhidden:chipA', JSON.stringify(['q1']));
doc.getElementById('bulk-qubitvis-menu').querySelector('[data-qsel="invert"]').click();
doc.getElementById('bulk-qubitvis-menu').querySelector('[data-qsel="invert"]').click();   // q1 hidden again
ok(off(doc, 'q1-3') && !off(doc, 'q1-2'), 'hiding q1 takes q1-3 but NOT the dirty q1-2 (unsaved edits never vanish)');
dirtyCell.value = '0.1'; dirtyCell.dispatchEvent(new win.Event('input', { bubbles: true }));
win.BulkEdit.showAllQubits();

// the Qubits picker still takes a pair with it
win.localStorage.setItem('quam_bulk_qhidden:chipA', JSON.stringify(['q3']));
win.BulkEdit.showAllQubits();                       // clears q3 again -- use the menu path instead
win.localStorage.setItem('quam_bulk_qhidden:chipA', JSON.stringify(['q3']));
const qmenu = doc.getElementById('bulk-qubitvis-menu');
qmenu.querySelector('[data-qsel="invert"]').click();  // q3 hidden -> invert -> q1,q2 hidden, q3 shown
qmenu.querySelector('[data-qsel="invert"]').click();  // back: q3 hidden
ok(off(doc, 'q2-3') && off(doc, 'q1-3') && !off(doc, 'q1-2'), 'a pair whose qubit is hidden follows it');
ok(/qubit hidden/.test(menu.textContent), 'and the Pairs menu says so');
win.BulkEdit.showAllQubits();
ok(!off(doc, 'q2-3') && !off(doc, 'q1-3'), 'showing the qubit again shows its pairs');

// a fresh page applies the remembered pair set
win = world({ 'quam_bulk_qhidden:pairs:chipA': JSON.stringify(['q1-3']) });
doc = win.document;
ok(off(doc, 'q1-3') && !off(doc, 'q1-2') && !cb(doc, 'q1-3').checked && doc.getElementById('bulk-pair-pill').textContent === '2 of 3 pairs — Show all',
   'a fresh page applies the remembered set and the pill');

console.log(fails ? ('FAILED ' + fails) : 'bulk_pairs_picker_selfcheck: all ok');
process.exit(fails ? 1 : 0);
