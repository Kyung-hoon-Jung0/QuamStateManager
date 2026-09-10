// A band conflict must be re-judged when the thing it accuses is fixed.
//
// Customer, 2026-09-10, on the live 5050 instance: moving every XY band from 2
// to 1, one cell at a time. Each edit raised "Band 1 conflicts with LO peer q2
// (band 2)" -- correct while q2 really was on 2. When every cell had been
// changed the warnings were all still there and the toolbar still said
// "6 band issues".
//
// The verdict rested on `data-peer-band` and `data-band`: SERVER-rendered
// snapshots of the values at page render, which nothing writes to. So the
// second edit was judged against the first cell's OLD value, and the first
// cell was never re-judged at all, because only the edited cell was checked.
//
// What is pinned here:
//  - the LIVE read: a band cell is judged against what its peer's cell says NOW
//  - the GROUP re-judge: one edit repaints both ends of the pair, and the
//    frequency cells judged against them
//  - the fallback: with the peer cell absent from the page (a hidden column, an
//    unhydrated one) the server's attribute is still used -- silence is not an
//    answer, and inventing "no conflict" would be worse than a stale one
//  - the counter follows, since that is what the report was reading
//  - and the scope: this is FOUR cells, on the page. No fetch, no diagnostics.
//
// Run: node tests/bulk_band_live_selfcheck.cjs   (needs jsdom)
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

// The real MW-FEM ranges (core/mw_fem.py BANDS), so an "outside band" verdict
// here means what it means on the page.
const BANDS = { '1': [50e6, 5.5e9], '2': [4.5e9, 7.5e9], '3': [6.5e9, 10.5e9] };

// Four qubits on two LO PAIRS: q1+q2 share con1/1 out2<->out3, q3+q4 share
// out4<->out5. Every one starts on band 2, which is the shape the report
// describes -- and the pair is what makes the stale read observable.
const PAIRS = [
  { a: 'q1', b: 'q2', pa: 'mw_outputs:2', pb: 'mw_outputs:3', fa: 5.05e9, fb: 5.2e9 },
  { a: 'q3', b: 'q4', pa: 'mw_outputs:4', pb: 'mw_outputs:5', fa: 5.3e9, fb: 5.4e9 },
];

function loAttrs(port, peerPort, band, freq, peerQubit, peerBand) {
  const group = [port, peerPort].sort().join('|');
  return ' data-lo-field="band" data-band="' + band + '" data-freq="' + freq + '"'
    + ' data-peer-band="' + peerBand + '" data-peer="' + peerQubit + '"'
    + ' data-lo-port="' + port + '" data-lo-peer-port="' + peerPort + '"'
    + ' data-lo-group="' + group + '"';
}
function freqAttrs(port, peerPort, band, freq) {
  const group = [port, peerPort].sort().join('|');
  return ' data-lo-field="freq" data-band="' + band + '" data-freq="' + freq + '"'
    + ' data-lo-port="' + port + '" data-lo-peer-port="' + peerPort + '"'
    + ' data-lo-group="' + group + '"';
}

/* `withFreqCols` off = only the band column exists, which is the report's own
   view (they were searching "band"). On = the frequency cells are there too,
   which is what proves the group re-judge reaches them. */
function build(withFreqCols) {
  let rows = '';
  PAIRS.forEach(function (p) {
    [[p.a, 'con1/1/' + p.pa, 'con1/1/' + p.pb, p.fa, p.b, 2],
     [p.b, 'con1/1/' + p.pb, 'con1/1/' + p.pa, p.fb, p.a, 2]].forEach(function (r) {
      const q = r[0], port = r[1], peer = r[2], freq = r[3];
      const dpB = 'qubits.' + q + '.xy.opx_output.band';
      const dpF = 'qubits.' + q + '.xy.opx_output.upconverter_frequency';
      rows += '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">'
        + q + '</th>'
        + '<td class="bulk-td ck-0" data-col-key="xyband" data-dot-path="' + dpB + '">'
        + '<input type="text" class="bulk-cell" value="2" data-orig="2" data-dot-path="'
        + dpB + '" data-resolved="ports.' + port + '.band" size="6"'
        + loAttrs(port, peer, 2, freq, r[4], r[5]) + '></td>'
        + (withFreqCols
           ? '<td class="bulk-td ck-1" data-col-key="xyfreq" data-dot-path="' + dpF + '">'
             + '<input type="text" class="bulk-cell" value="' + freq + '" data-orig="' + freq
             + '" data-dot-path="' + dpF + '" data-resolved="ports.' + port
             + '.upconverter_frequency" size="14"' + freqAttrs(port, peer, 2, freq) + '></td>'
           : '')
        + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
        + '<span class="bulk-row-error" hidden></span></td></tr>';
    });
  });
  const heads = '<th class="bulk-col-head ck-0" data-col-key="xyband"><span class="bulk-col-label">XY band</span></th>'
    + (withFreqCols
       ? '<th class="bulk-col-head ck-1" data-col-key="xyfreq"><span class="bulk-col-label">XY LO</span></th>'
       : '');
  return '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>P</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search">'
    + '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-band-warn" hidden></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button>'
    + '</div><div class="bulk-table-wrap"><table id="bulk-table"><thead>'
    + '<tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>'
    + heads + '</tr></thead><tbody>' + rows + '</tbody></table></div></div></div>';
}

function world(withFreqCols) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + build(withFreqCols) + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;          // the docs/125 standing rule: bridge, or it throws
  win.htmx = { ajax: function () {}, trigger: function () {}, process: function () {} };
  win.fetches = 0;
  win.fetch = function () { win.fetches++; return Promise.reject(new Error('no fetch expected')); };
  win.showToast = function () {};
  win.LiveEditUndo = { record: function () {} };
  win.trapFocus = function () { return function () {}; };
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  const cols = [{ key: 'xyband', label: 'XY band', section: 'XY Port', unit: '', default_on: true }];
  if (withFreqCols) cols.push({ key: 'xyfreq', label: 'XY LO', section: 'XY Port', unit: '', default_on: true });
  win.BulkEdit.mount(cols, { bands: BANDS }, [],
    { chip: 'chipA', qubits: ['q1', 'q2', 'q3', 'q4'].map(function (q) { return { id: q, grid: null }; }) });
  return win;
}

const bandCell = (win, q) => win.document.querySelector(
  'tr[data-qubit="' + q + '"] .bulk-cell[data-lo-field="band"]');
const freqCell = (win, q) => win.document.querySelector(
  'tr[data-qubit="' + q + '"] .bulk-cell[data-lo-field="freq"]');
const msgOf = (win, q) => {
  const el = win.document.querySelector(
    'tr[data-qubit="' + q + '"] td[data-col-key="xyband"] .bulk-band-msg');
  return el && !el.hidden ? el.textContent : '';
};
const warns = (win, q) => bandCell(win, q).classList.contains('bulk-band-warn');
const counter = (win) => {
  const el = win.document.getElementById('bulk-band-warn');
  return el.hidden ? '' : el.textContent;
};
function type(win, cell, v) {
  cell.value = v;
  cell.dispatchEvent(new win.Event('input', { bubbles: true }));
}

// ── A. the report, replayed ────────────────────────────────────────────────
{
  const win = world(false);
  ok(!warns(win, 'q1') && !warns(win, 'q2') && counter(win) === '',
     'A1: four qubits all on band 2 -- nothing warns to begin with');

  type(win, bandCell(win, 'q1'), '1');
  ok(warns(win, 'q1'), 'A2: q1 moved to band 1 while q2 is on 2 -- that IS a conflict');
  ok(/conflicts with LO peer q2 \(band 2\)/.test(msgOf(win, 'q1')),
     'A3: and it names the peer and its band: ' + msgOf(win, 'q1'));
  ok(warns(win, 'q2'),
     'A4: the peer is re-judged too -- the conflict is a property of the PAIR, '
     + 'and before this it stayed silent while being half of the problem');
  ok(counter(win) === '⚠ 2 band issues', 'A5: the counter says 2, got: ' + counter(win));

  /* The report's second step, and the whole defect: fixing the peer. */
  type(win, bandCell(win, 'q2'), '1');
  ok(!warns(win, 'q2'),
     'A6: q2 on band 1 beside q1 on band 1 is NOT a conflict '
     + '(it was judged against q1’s rendered 2): ' + msgOf(win, 'q2'));
  ok(!warns(win, 'q1'),
     'A7: ...and q1’s own warning is withdrawn, which nothing used to do: '
     + msgOf(win, 'q1'));
  ok(msgOf(win, 'q1') === '' && msgOf(win, 'q2') === '',
     'A8: both inline messages are cleared');
  ok(counter(win) === '', 'A9: and the toolbar count is gone, got: ' + counter(win));

  /* The other pair is untouched -- a group is a pair, not the grid. */
  ok(!warns(win, 'q3') && !warns(win, 'q4'), 'A10: the other LO pair never moved');
  type(win, bandCell(win, 'q3'), '1');
  ok(warns(win, 'q3') && warns(win, 'q4'), 'A11: ...and warns on its own terms when it does');
  ok(!warns(win, 'q1') && !warns(win, 'q2'), 'A12: without disturbing the fixed pair');
  ok(win.fetches === 0, 'A13: nothing was fetched -- this is four cells on the page');
}

// ── B. bands 1 and 3 are compatible; 2 only with 2 ─────────────────────────
{
  const win = world(false);
  type(win, bandCell(win, 'q1'), '1');
  type(win, bandCell(win, 'q2'), '3');
  // Asserted on the CONFLICT half alone: q2's LO at 5.2 GHz is genuinely
  // outside band 3, and that is a different, correct complaint.
  const conflict = (q) => /conflicts with LO peer/.test(msgOf(win, q));
  ok(!conflict('q1') && !conflict('q2'),
     'B1: band 1 beside band 3 is allowed (they overlap on the same LO): '
     + msgOf(win, 'q1') + ' | ' + msgOf(win, 'q2'));
  ok(/outside Band 3/.test(msgOf(win, 'q2')),
     'B2: ...while the out-of-band frequency IS still reported: ' + msgOf(win, 'q2'));
  type(win, bandCell(win, 'q2'), '2');
  ok(conflict('q1') && conflict('q2'), 'B3: band 1 beside band 2 is not');
}

// ── C. the frequency cells are judged against the LIVE band ────────────────
{
  const win = world(true);
  // 5.05 GHz sits inside band 2 (4.5-7.5) and inside band 1 (0.05-5.5).
  ok(!freqCell(win, 'q1').classList.contains('bulk-band-warn'),
     'C1: the LO frequency starts inside its band');
  type(win, bandCell(win, 'q1'), '3');       // band 3 is 6.5-10.5 GHz
  ok(freqCell(win, 'q1').classList.contains('bulk-band-warn'),
     'C2: moving the band leaves the frequency outside it, and the frequency '
     + 'cell is re-judged -- it reads the band cell, not `data-band`');
  type(win, bandCell(win, 'q1'), '2');
  ok(!freqCell(win, 'q1').classList.contains('bulk-band-warn'),
     'C3: ...and the frequency verdict is withdrawn when the band comes back');

  // The band cell's own "freq outside band" half reads the live frequency too.
  type(win, freqCell(win, 'q1'), '9000000000');
  ok(/outside Band 2/.test(msgOf(win, 'q1')),
     'C4: an edited frequency reaches the BAND cell’s verdict: ' + msgOf(win, 'q1'));
  type(win, freqCell(win, 'q1'), '5050000000');
  ok(msgOf(win, 'q1') === '', 'C5: and is withdrawn when the frequency comes back');
}

// ── D. with the peer off the page, the server's value still answers ────────
{
  const win = world(false);
  // Exactly what a hidden or unhydrated column looks like to this code: the
  // input is gone. The verdict must fall back, not fall silent -- claiming "no
  // conflict" because we cannot see the peer would be worse than a stale one.
  const q2td = bandCell(win, 'q2').closest('td');
  q2td.removeChild(bandCell(win, 'q2'));
  type(win, bandCell(win, 'q1'), '1');
  ok(warns(win, 'q1'),
     'D1: with the peer cell absent the server’s data-peer-band still answers');
  ok(/\(band 2\)/.test(msgOf(win, 'q1')),
     'D2: and it is the value the server rendered: ' + msgOf(win, 'q1'));
}

// ── E. a cell with no LO pair is judged alone ──────────────────────────────
{
  const win = world(false);
  const c = bandCell(win, 'q1');
  ['data-lo-group', 'data-lo-peer-port', 'data-peer-band', 'data-peer']
    .forEach(function (a) { c.removeAttribute(a); });
  type(win, c, '1');
  ok(!warns(win, 'q1'),
     'E1: an unpaired port (an LF FEM, an odd port id) has no peer to conflict with');
  ok(!warns(win, 'q2'), 'E2: and the re-judge does not reach outside a group that is not there');
}

if (fails === 0) console.log('all checks passed (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
