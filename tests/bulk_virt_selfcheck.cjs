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
const GRID_VIRT_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'grid-virt.js'), 'utf8');
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

function bigWorld(nCols, nRows, presearch) {
  let head = '';
  const colsModel = [];
  for (let i = 0; i < nCols; i++) {
    // ck-N on th AND td, as the template stamps them (docs/141 4d) -- without
    // it the width freeze can never fire and its pin was vacuous (4l-review)
    head += '<th class="bulk-col-head ck-' + i + '" data-col-key="c' + i +
            '" data-section="s">c' + i + '</th>';
    colsModel.push({ key: 'c' + i, label: 'c' + i, section: 's',
                     default_on: true });
  }
  let body = '';
  for (let r = 0; r < nRows; r++) {
    let tds = '';
    for (let i = 0; i < nCols; i++) {
      tds += '<td class="bulk-td ck-' + i + '" data-col-key="c' + i + '">' +
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
    '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr>' +
    '<th class="bulk-corner" data-col-key="__id__"></th>' + head +
    '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true,
      url: 'http://localhost/' });
  const win = dom.window;
  // geometry (jsdom computes none): header positions + wrap width
  // docs/141 4i: _virtInit must not read geometry (the first paint would be
  // the full table's layout); the getters COUNT reads, and the estimate from
  // the inputs' size attr (none here => 8 chars => 92 px) against a 720 px
  // window puts the cold boundary at c20 (x = 20 * 92 = 1840 > 720 * 2.5)
  win.__geomReads = 0;
  Object.defineProperty(win.screen, 'availWidth', { value: 720, configurable: true });   // what _virtInit reads (no layout)
  Object.defineProperty(win, 'innerWidth', { get: function () { win.__geomReads++; return 720; }, configurable: true });   // reading it forces layout in Blink
  win.document.querySelectorAll('th.bulk-col-head').forEach(function (h, i) {
    Object.defineProperty(h, 'offsetLeft', { get: function () { win.__geomReads++; return 60 + i * COL_W; } });
    Object.defineProperty(h, 'offsetWidth', { get: function () { win.__geomReads++; return COL_W; } });
  });
  const wrap = win.document.getElementById('table-pane');    // docs/141 4q: the ONE scroller
  Object.defineProperty(wrap, 'clientWidth', { value: WRAP_W });
  wrap.scrollLeft = 0;
  win.htmx = { ajax: function () {} };
  if (presearch) win.localStorage.setItem('quam_bulk_search', presearch);   // a remembered search, applied at mount
  new win.Function(GRID_VIRT_JS).call(win);
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
  ok(W.win.__geomReads === 0, 'the mount read NO geometry (offsetLeft/offsetWidth) -- coldness is estimated, the first layout is of the pruned table (reads: ' + W.win.__geomReads + ')');
  // the width freeze: exactly the COLD columns carry a min-width rule by class (4l-review)
  const st = doc.getElementById('bulk-virt-width-style');
  const sheet = (st && st.textContent) || '';
  const coldKeys = new Set(Array.from(doc.querySelectorAll('td.bulk-td-cold')).map((td) => td.getAttribute('data-col-key')));
  let freezeOk = coldKeys.size > 0, freezeBad = '';
  for (let i = 0; i < N_COLS; i++) {
    const has = sheet.indexOf('#bulk-table th.ck-' + i + '{min-width:') >= 0;
    if (has !== coldKeys.has('c' + i)) { freezeOk = false; freezeBad += ' c' + i + (has ? ':frozen-but-hot' : ':cold-but-unfrozen'); }
  }
  ok(freezeOk, 'every cold column is frozen at its estimated width by class, no hot column is (' + coldKeys.size + ' cold' + freezeBad + ')');
  // an undo naming a path in a COLD column hydrates that column only (4l-review)
  const coldBefore = doc.querySelectorAll('td.bulk-td-cold').length;
  const rp = W.win.BulkEdit.revertPaths([{ dot_path: 'qubits.q2.f40', old_value_str: '999' }]);
  const c40q2 = doc.querySelector('tr[data-qubit="q2"] td[data-col-key="c40"] .bulk-cell');
  ok(!!c40q2 && c40q2.value === '999', 'the undone cell in a cold column is hydrated and patched (' + (c40q2 && c40q2.value) + ')');
  ok(doc.querySelectorAll('td.bulk-td-cold').length === coldBefore - N_ROWS,
     'and ONLY that column was hydrated, not the whole grid (cold ' + coldBefore + ' -> ' + doc.querySelectorAll('td.bulk-td-cold').length + ')');
  ok(rp && rp.missing === 0, 'the path was covered');
  const coldBefore2 = doc.querySelectorAll('td.bulk-td-cold').length;
  W.win.BulkEdit.revertPaths([{ dot_path: 'qubit_pairs.p1.macros.cz.amp', old_value_str: '1' }]);
  ok(doc.querySelectorAll('td.bulk-td-cold').length === coldBefore2, 'a path in no column hydrates nothing (it is missing by definition)');

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

  // ── a REMEMBERED search at mount (docs/141 4d) ────────────────────────
  // The search is applied before _virtInit, so hidden columns have no
  // geometry; deciding coldness by offsetLeft alone left virtualization OFF
  // for every /bulk opened with a remembered query (8/8 loads measured on
  // the 20Q chip). A hidden column is cold by definition.
  const P = bigWorld(40, 30, 'c3');
  const psb = P.doc.getElementById('bulk-search');
  ok(psb.value === 'c3', 'remembered search restored at mount');
  ok(P.doc.querySelector('th[data-col-key="c5"]').classList.contains('bulk-search-hidden'), 'fixture: c5 is search-hidden');
  const pc5 = P.doc.querySelector('td[data-col-key="c5"]');
  ok(pc5 && pc5.classList.contains('bulk-td-cold') && pc5.querySelector('.bulk-cell') === null,
     'a search-hidden column is COLD at mount (virtualization engaged)');
  const pc3 = P.doc.querySelector('td[data-col-key="c3"]');
  ok(pc3 && !pc3.classList.contains('bulk-td-cold') && pc3.querySelector('.bulk-cell') !== null,
     'the surviving, on-screen column is hot');
  // a scroll with the search active must NOT hydrate the search-hidden columns (4l-review)
  P.wrap.scrollLeft = 2600;
  P.wrap.dispatchEvent(new P.win.Event('scroll'));
  await new Promise(function (r) { setTimeout(r, 80); });
  ok(pc5.classList.contains('bulk-td-cold') && pc5.querySelector('.bulk-cell') === null,
     'a search-hidden column stays cold through a scroll (it is not on screen)');
  const pst = P.doc.getElementById('bulk-virt-width-style');
  ok(pst && pst.textContent.indexOf('#bulk-table th.ck-5{min-width:') >= 0, 'a hidden-at-mount column is frozen too (inert until shown)');
  P.wrap.scrollLeft = 0;
  psb.value = ''; psb.dispatchEvent(new P.win.Event('input', { bubbles: true }));
  await new Promise(function (r) { setTimeout(r, 400); });
  ok(!pc5.classList.contains('bulk-td-cold') && pc5.querySelector('.bulk-cell') !== null,
     'clearing the search hydrates the columns now on screen (c5)');
  // c25 was search-hidden at mount (cold by definition) and sits at 2560px
  // after the clear -- past the 2000px scroll edge, so it stays cold; c30
  // was VISIBLE at mount ('c3' matches it) and near the left edge then, so
  // it was hydrated and stays so -- the estimate is conservative by design
  const pc25 = P.doc.querySelector('td[data-col-key="c25"]');
  ok(pc25.classList.contains('bulk-td-cold'), 'and leaves the off-screen ones cold (c25 at 2560px > 2000)');

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
