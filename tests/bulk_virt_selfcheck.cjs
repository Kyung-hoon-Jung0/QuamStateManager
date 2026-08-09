// Behavioral check for docs/105 #1 — cold-column hydration in the REAL
// bulk-edit.js under jsdom:
//  - over the threshold, columns beyond the initial horizontal window are
//    pruned (empty td + .bulk-td-cold) with their widths frozen; below it,
//    NOTHING changes (the safety gate every small chip rides)
//  - the search stays whole-chip (docs/85): a value living only in a cold
//    column still matches its row + keeps its column search-visible
//  - Tab into a cold column hydrates it (the _editableIn choke point)
//  - horizontal scroll hydrates columns entering the look-ahead window
//
// jsdom has no layout, so the harness defines offsetLeft/offsetWidth on the
// header cells and clientWidth on the scroll wrap explicitly.
//
// Run: node tests/bulk_virt_selfcheck.cjs   (needs jsdom)
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

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('not ok - ' + msg); fails++; }
}
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const N_COLS = 45, N_ROWS = 90;      // 4050 cells — over the 4000 gate
const COL_W = 100, WRAP_W = 800;     // cold boundary: offsetLeft > 800*2.5

function bigWorld(nCols, nRows) {
  let head = '';
  const colsModel = [];
  for (let i = 0; i < nCols; i++) {
    head += '<th class="bulk-col-head" data-col-key="c' + i +
            '" data-section="s">c' + i + '</th>';
    colsModel.push({ key: 'c' + i, label: 'c' + i, section: 's',
                     default_on: true });
  }
  let body = '';
  for (let r = 0; r < nRows; r++) {
    let tds = '';
    for (let i = 0; i < nCols; i++) {
      tds += '<td class="bulk-td" data-col-key="c' + i + '">' +
        '<input type="text" class="bulk-cell" value="v' + r + 'c' + i +
        '" data-orig="v' + r + 'c' + i + '" data-dot-path="qubits.q' + r +
        '.f' + i + '"></td>';
    }
    body += '<tr data-qubit="q' + r + '"><th class="bulk-rowhead" ' +
            'data-col-key="__id__">q' + r + '</th>' + tds + '</tr>';
  }
  const DOM = '<div id="table-pane"><div class="bulk-toolbar">' +
    '<details class="bulk-colvis"><summary>P</summary>' +
    '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>' +
    '<span class="bulk-search-wrap"><input type="search" id="bulk-search">' +
    '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span>' +
    '</span></div>' +
    '<div class="bulk-scroll"><table id="bulk-table"><thead><tr>' +
    '<th class="bulk-corner" data-col-key="__id__"></th>' + head +
    '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true,
      url: 'http://localhost/' });
  const win = dom.window;
  // geometry (jsdom computes none): header positions + wrap width
  win.document.querySelectorAll('th.bulk-col-head').forEach(function (h, i) {
    Object.defineProperty(h, 'offsetLeft', { value: 60 + i * COL_W });
    Object.defineProperty(h, 'offsetWidth', { value: COL_W });
  });
  const wrap = win.document.querySelector('.bulk-scroll');
  Object.defineProperty(wrap, 'clientWidth', { value: WRAP_W });
  wrap.scrollLeft = 0;
  win.htmx = { ajax: function () {} };
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(colsModel, { bands: {} }, []);
  return { win: win, doc: win.document, wrap: wrap, cols: colsModel };
}

async function main() {
  // ── over-threshold world ──────────────────────────────────────────────
  const W = bigWorld(N_COLS, N_ROWS);
  const doc = W.doc;

  const coldTds = doc.querySelectorAll('td.bulk-td-cold');
  ok(coldTds.length > 0, 'over the gate: cold tds exist (' + coldTds.length + ')');
  const c30td = doc.querySelector('tr[data-qubit="q0"] td[data-col-key="c30"]');
  ok(c30td && c30td.classList.contains('bulk-td-cold')
     && c30td.innerHTML === '', 'a far column is pruned empty');
  const c3td = doc.querySelector('tr[data-qubit="q0"] td[data-col-key="c3"]');
  ok(c3td && !c3td.classList.contains('bulk-td-cold')
     && !!c3td.querySelector('.bulk-cell'), 'a near column keeps its input');
  const st = doc.getElementById('bulk-virt-width-style');
  ok(!!st && st.textContent.indexOf('data-col-key="c30"') >= 0
     && st.textContent.indexOf('min-width:' + COL_W + 'px') >= 0,
     'cold column widths are frozen');

  // ── whole-chip search over a cold value (docs/85) ─────────────────────
  const sb = doc.getElementById('bulk-search');
  sb.value = 'v7c30';                       // exists ONLY in cold column c30
  sb.dispatchEvent(new W.win.Event('input', { bubbles: true }));
  await tick(250);                          // past the 120ms debounce
  const row7 = doc.querySelector('tr[data-qubit="q7"]');
  const row8 = doc.querySelector('tr[data-qubit="q8"]');
  ok(row7 && !row7.classList.contains('bulk-row-hidden'),
     'a value in a COLD column still finds its row');
  ok(row8 && row8.classList.contains('bulk-row-hidden'),
     'other rows are filtered as usual');
  sb.value = '';
  sb.dispatchEvent(new W.win.Event('input', { bubbles: true }));
  await tick(250);

  // ── Tab into a cold column hydrates it ────────────────────────────────
  // boundary: last hot column ~ c19 (offsetLeft 60+19*100=1960 < 2000)
  const hotCell = doc.querySelector(
    'tr[data-qubit="q0"] td[data-col-key="c19"] .bulk-cell');
  ok(!!hotCell, 'boundary hot cell exists');
  hotCell.focus();
  hotCell.dispatchEvent(new W.win.KeyboardEvent('keydown',
    { key: 'Tab', bubbles: true, cancelable: true }));
  const c20td = doc.querySelector('tr[data-qubit="q0"] td[data-col-key="c20"]');
  ok(!!c20td.querySelector('.bulk-cell'),
     'Tab into the cold boundary hydrates the column');
  ok(!c20td.classList.contains('bulk-td-cold'), 'hydrated td loses the cold class');

  // ── horizontal scroll hydrates the look-ahead window ──────────────────
  W.wrap.scrollLeft = 2600;                 // edge = 2600+800+1200 = 4600 → all
  W.wrap.dispatchEvent(new W.win.Event('scroll'));
  await tick(80);                           // rAF + settle
  const stillCold = doc.querySelectorAll('td.bulk-td-cold').length;
  ok(stillCold === 0, 'scrolling to the end hydrates everything (cold left: '
     + stillCold + ')');
  ok(!doc.querySelector('td.bulk-td-cold'), 'no cold tds remain');
  const c44 = doc.querySelector('tr[data-qubit="q1"] td[data-col-key="c44"]');
  ok(!!c44.querySelector('.bulk-cell')
     && c44.querySelector('.bulk-cell').value === 'v1c44',
     'hydrated far cell restored with its exact value');

  // ── below-threshold world is byte-identical ───────────────────────────
  const S = bigWorld(10, 20);               // 200 cells — under the gate
  ok(S.doc.querySelectorAll('td.bulk-td-cold').length === 0,
     'below the gate: nothing is pruned');
  const sst = S.doc.getElementById('bulk-virt-width-style');
  ok(!sst || sst.textContent === '', 'below the gate: no width freeze');

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('bulk_virt_selfcheck: ALL OK');
}

main().catch(function (e) { console.error(e); process.exit(1); });
