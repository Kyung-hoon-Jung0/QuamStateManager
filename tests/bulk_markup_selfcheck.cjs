// docs/141 §4m — the Live-Edit cell markup no longer ships a before→after chip
// (.bulk-ba, four spans) and a band message (.bulk-band-msg) in EVERY cell; the
// REAL bulk-edit.js / pair-edit.js create them on first use. Pins, under jsdom:
//  - a fresh grid has neither element
//  - hovering a MODIFIED cell creates exactly one chip in its td, old text =
//    data-baseline, new text = the value, the Δ span is painted, .bulk-ba-show
//    toggles with mouseover/mouseout, and a second hover never duplicates it
//  - an unmodified cell never gets a chip; a modified cell with no baseline
//    falls back to data-orig
//  - the band message is created only when a cell actually warns, before the
//    physical-output line (where the render used to put it), and is re-used
//    (hidden, not duplicated) once the value is back in band
//  - the pair grid: the same chip contract
//
// Run: node tests/bulk_markup_selfcheck.cjs   (needs jsdom)
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
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const GRID_VIRT_JS = read('grid-virt.js');
const BULK_JS = read('bulk-edit.js');
const PAIR_JS = read('pair-edit.js');
const SQ_JS = read('search-query.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const BULK_DOM = `
<div id="bulk-panel"><div id="table-pane">
  <div class="bulk-toolbar">
    <details class="bulk-colvis"><summary>Properties</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>
    <span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span><button type="button" id="bulk-dyncol-hint" hidden></button></span>
    <span id="bulk-dirty-count"></span><button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button><span id="bulk-band-warn"></span>
  </div>
  <div class="bulk-table-wrap"><table id="bulk-table">
    <thead>
      <tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit</th><th class="bulk-group-head" data-group="Frequencies" colspan="3">Frequencies</th></tr>
      <tr class="bulk-head-row">
        <th class="bulk-col-head" data-col-key="f_01" data-section="Frequencies"><span class="bulk-col-label">Qubit f01</span></th>
        <th class="bulk-col-head" data-col-key="lo" data-section="Frequencies"><span class="bulk-col-label">LO</span></th>
        <th class="bulk-col-head" data-col-key="amp" data-section="Frequencies"><span class="bulk-col-label">amp</span></th>
      </tr>
    </thead>
    <tbody>
      <tr data-qubit="qA1"><th class="bulk-rowhead" data-col-key="__id__">qA1</th><td class="bulk-td ck-0" data-col-key="f_01"><input class="bulk-cell bulk-cell-modified" value="6,300,000,000" data-orig="6,300,000,000" data-baseline="6,250,000,000" data-dot-path="qubits.qA1.f_01" data-resolved="qubits.qA1.f_01" data-linkable="1" title="qubits.qA1.f_01"></td><td class="bulk-td ck-1" data-col-key="lo"><input class="bulk-cell" value="5,000,000,000" data-orig="5,000,000,000" data-dot-path="qubits.qA1.xy.LO" data-resolved="ports.x.LO" data-lo-field="freq" data-band="1" data-phys-kind="mw" data-phys-fsp="10"> <span class="bulk-phys" aria-hidden="true">—</span></td><td class="bulk-td ck-2" data-col-key="amp"><input class="bulk-cell" value="0.1" data-orig="0.1" data-dot-path="qubits.qA1.xy.operations.x180.amplitude" data-resolved="qubits.qA1.xy.operations.x180_DragCosine.amplitude"></td><td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr>
    </tbody>
  </table></div>
</div></div>`;

const COLS = [
  { key: 'f_01', label: 'Qubit f01', section: 'Frequencies', unit: 'Hz', default_on: true },
  { key: 'lo', label: 'LO', section: 'Frequencies', unit: 'Hz', default_on: true },
  { key: 'amp', label: 'amp', section: 'Frequencies', unit: '', default_on: true },
];

function world(html) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + html + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win.__chipToken = 'tok';
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
  // the Δ painter the chip's delta span is handed to (the real one lives in app.js)
  win.ValueDelta = { paint: function (el, a, b) { el.textContent = 'Δ(' + a + '→' + b + ')'; el.hidden = false; } };
  return win;
}
function mouse(win, el, type) { el.dispatchEvent(new win.MouseEvent(type, { bubbles: true })); }
function input(win, el) { el.dispatchEvent(new win.Event('input', { bubbles: true })); }

function qubitGrid() {
  const win = world(BULK_DOM);
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(COLS, { bands: { '1': [50e6, 5.5e9] } }, []);
  const doc = win.document;
  ok(doc.querySelectorAll('.bulk-ba').length === 0 && doc.querySelectorAll('.bulk-band-msg').length === 0,
     'qubit grid: a fresh grid carries no before→after chip and no band message');

  const mod = doc.querySelector('[data-col-key="f_01"] .bulk-cell');
  const tdMod = mod.closest('.bulk-td');
  mouse(win, mod, 'mouseover');
  const chips = tdMod.querySelectorAll('.bulk-ba');
  ok(chips.length === 1, 'hovering a modified cell creates exactly one chip in its td (' + chips.length + ')');
  ok(chips[0] && chips[0].querySelector('.bulk-ba-old').textContent === '6,250,000,000',
     'the chip\'s old text is data-baseline');
  ok(chips[0] && chips[0].querySelector('.bulk-ba-new').textContent === '6,300,000,000',
     'the chip\'s new text is the cell value');
  const d = chips[0] && chips[0].querySelector('.bulk-ba-delta');
  ok(d && !d.hidden && d.textContent === 'Δ(6,250,000,000→6,300,000,000)', 'the Δ span is created and painted');
  ok(chips[0] && chips[0].getAttribute('aria-hidden') === 'true' && /→/.test(chips[0].textContent),
     'the chip is aria-hidden and reads old → new');
  ok(tdMod.classList.contains('bulk-ba-show'), 'mouseover shows the chip');
  mouse(win, mod, 'mouseout');
  ok(!tdMod.classList.contains('bulk-ba-show'), 'mouseout hides it');
  mouse(win, mod, 'mouseover');
  ok(tdMod.querySelectorAll('.bulk-ba').length === 1, 'a second hover re-uses the chip (never a duplicate)');
  mouse(win, mod, 'mouseout');

  const plain = doc.querySelector('[data-col-key="amp"] .bulk-cell');
  mouse(win, plain, 'mouseover');
  ok(plain.closest('.bulk-td').querySelectorAll('.bulk-ba').length === 0
     && !plain.closest('.bulk-td').classList.contains('bulk-ba-show'),
     'an unmodified cell never gets a chip');
  mouse(win, plain, 'mouseout');
  // modified with no baseline (defensive): old text falls back to data-orig
  plain.classList.add('bulk-cell-modified');
  mouse(win, plain, 'mouseover');
  const c2 = plain.closest('.bulk-td').querySelector('.bulk-ba-old');
  ok(c2 && c2.textContent === '0.1', 'a modified cell with no baseline shows data-orig as old');
  mouse(win, plain, 'mouseout');

  // band message: created only when a cell warns, placed before .bulk-phys
  const lo = doc.querySelector('[data-col-key="lo"] .bulk-cell');
  const tdLo = lo.closest('.bulk-td');
  lo.value = '9,000,000,000'; input(win, lo);
  const msgs = tdLo.querySelectorAll('.bulk-band-msg');
  ok(msgs.length === 1 && !msgs[0].hidden && /Outside Band 1/.test(msgs[0].textContent),
     'an out-of-band value creates the message and shows it (' + (msgs[0] && msgs[0].textContent) + ')');
  ok(msgs[0] && msgs[0].nextElementSibling && msgs[0].nextElementSibling.classList.contains('bulk-phys'),
     'the message sits before the physical-output line, where the render used to put it');
  ok(lo.classList.contains('bulk-band-warn'), 'the cell carries the warn class');
  lo.value = '5,000,000,000'; input(win, lo);
  ok(tdLo.querySelectorAll('.bulk-band-msg').length === 1 && tdLo.querySelector('.bulk-band-msg').hidden
     && !lo.classList.contains('bulk-band-warn'),
     'back in band: the same element is hidden, not duplicated, and the warn class is gone');
  input(win, plain);
  ok(plain.closest('.bulk-td').querySelectorAll('.bulk-band-msg').length === 0,
     'a cell that never warns never gets a message element');
}

function pairGrid() {
  const win = world(
    '<table id="bulk-pair-table"><thead><tr class="bulk-head-row"><th class="bulk-col-head" data-col-key="cz_amp"><span class="bulk-col-label">cz · amplitude</span></th><th class="bulk-col-head" data-col-key="det"><span class="bulk-col-label">detuning</span></th></tr></thead><tbody>'
    + '<tr data-qubit="qA1-qA2" data-pair="qA1-qA2"><th class="bulk-rowhead" data-col-key="__id__">qA1-qA2</th>'
    + '<td class="bulk-td ck-0" data-col-key="cz_amp"><input class="bulk-cell bulk-cell-modified" value="0.15" data-orig="0.15" data-baseline="0.12" data-dot-path="qubit_pairs.qA1-qA2.gates.CZ.amplitude" data-resolved="qubit_pairs.qA1-qA2.gates.CZ.amplitude"></td>'
    + '<td class="bulk-td ck-1" data-col-key="det"><input class="bulk-cell" value="1e6" data-orig="1e6" data-dot-path="qubit_pairs.qA1-qA2.detuning" data-resolved="qubit_pairs.qA1-qA2.detuning"></td>'
    + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr></tbody></table>'
    + '<div id="bulk-pair-colvis-menu"></div>');
  win.eval(SQ_JS);
  win.eval(PAIR_JS);
  win.BulkPairEdit.mount([
    { key: 'cz_amp', label: 'cz · amplitude', section: 'Gate', default_on: true },
    { key: 'det', label: 'detuning', section: 'Pair', default_on: true },
  ]);
  const doc = win.document;
  ok(doc.querySelectorAll('.bulk-ba').length === 0, 'pair grid: a fresh grid carries no chip');
  const mod = doc.querySelector('[data-col-key="cz_amp"] .bulk-cell');
  const td = mod.closest('.bulk-td');
  mouse(win, mod, 'mouseout');
  ok(td.querySelectorAll('.bulk-ba').length === 0, 'pair grid: a mouseout before any hover creates nothing');
  mouse(win, mod, 'mouseover');
  ok(td.querySelectorAll('.bulk-ba').length === 1 && td.querySelector('.bulk-ba-old').textContent === '0.12'
     && td.querySelector('.bulk-ba-new').textContent === '0.15' && td.classList.contains('bulk-ba-show'),
     'pair grid: hover creates one chip, old = baseline, new = value, shown');
  const d = td.querySelector('.bulk-ba-delta');
  ok(d && !d.hidden && d.textContent === 'Δ(0.12→0.15)', 'pair grid: the Δ span is painted');
  mouse(win, mod, 'mouseout');
  mouse(win, mod, 'mouseover');
  ok(td.querySelectorAll('.bulk-ba').length === 1, 'pair grid: no duplicate on a second hover');
  const plain = doc.querySelector('[data-col-key="det"] .bulk-cell');
  mouse(win, plain, 'mouseover');
  ok(plain.closest('.bulk-td').querySelectorAll('.bulk-ba').length === 0, 'pair grid: an unmodified cell gets no chip');
}

qubitGrid();
pairGrid();
console.log(fails ? ('FAILED ' + fails) : 'bulk_markup_selfcheck: all ok');
process.exit(fails ? 1 : 0);
