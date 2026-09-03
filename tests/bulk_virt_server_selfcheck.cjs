// docs/141 §4n — server-side column virtualization, the client half, against
// the REAL bulk-edit.js under jsdom. The server rendered the columns past the
// look-ahead window as EMPTY tds (class bulk-td-cold) and shipped their values
// in #bulk-cold-map; the client:
//  - adopts them into _virt whatever its own gates say (the grid here is far
//    under _VIRT_MIN_CELLS — the server applied the gates), reading NO geometry
//  - freezes a server-cold column's width from the header's data-maxlen
//  - finds a value that lives only in a server-cold column (whole-chip search)
//  - hydrates from GET /bulk/cells: ONE request per pass with every due column,
//    a column already in flight is never asked for twice, the landed cells are
//    the server's markup verbatim, the column leaves the cold set, stats are
//    computed for it
//  - a failed batch stays cold, says so in one line, and is retried on the
//    next pass; a 409 (another chip) says "reload"
//  - revertPaths on a path in a server-cold column: missing, NO fetch (the
//    column arrives from the reverted working copy)
//  - a carried edit into a server-cold column is placed after the fetch
//  - sorting by a server-cold column fetches it, then sorts
//  - the htmx configRequest hook sends vw=screen.availWidth on /bulk only
//
// Run: node tests/bulk_virt_server_selfcheck.cjs   (needs jsdom)
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
const GRID_VIRT_JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');
// docs/141 4ae B-7: the honest line for "grid-virt.js never arrived" lives in
// app.js (a CORE script -- a helper inside the module that went missing would
// be missing too), and this harness cannot eval the whole of app.js. Slice it
// out between its sentinels; tests/test_bulk_virt_server.py pins that both
// sentinels exist, so a rename can never leave this slice silently empty.
const APP_JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const _mnA = APP_JS.indexOf('/* GRIDVIRT-MISSING-NOTE:BEGIN');
const _mnB = APP_JS.indexOf('/* GRIDVIRT-MISSING-NOTE:END */');
if (_mnA < 0 || _mnB <= _mnA) { console.error('FAIL: app.js has no GRIDVIRT-MISSING-NOTE sentinels'); process.exit(1); }
const MISSING_NOTE_JS = APP_JS.slice(_mnA, _mnB);
if (MISSING_NOTE_JS.indexOf('window.GridVirtMissingNote') < 0) { console.error('FAIL: the sliced block does not define GridVirtMissingNote'); process.exit(1); }

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }
// docs/141 4ae: wait for what HAPPENED, not for how fast. A fixed sleep after a
// scroll made this file fail ~1 run in 4 once 4ad's extraction moved the
// hydration settle from a 79 ms median to 106 ms -- the harness was measuring
// latency while claiming to measure behaviour. The deadline stays tight (600 ms
// against a 60 ms mock) so a genuine slowdown still fails; `settle` never
// asserts, it only stops waiting, and the assertion that follows is unchanged.
async function settle(pred, maxMs) {
  const deadline = (maxMs || 600) / 5;
  for (let i = 0; i < deadline; i++) {
    let done = false;
    try { done = !!pred(); } catch (e) { done = false; }
    if (done) return true;
    await tick(5);
  }
  return false;
}

const N_ROWS = 4, N_HOT = 3, N_COLD = 4;          // 28 cells: far under every client gate
const COLS = [];
for (let i = 0; i < N_HOT + N_COLD; i++) COLS.push({ key: 'c' + i, label: 'col ' + i, section: 'S', unit: '', default_on: true });

// hot values 100..302, cold values 9000 + 10r + i (unique, numeric so the
// header stats can be computed; the server returns the SAME value the map
// carried, marked data-src so the test can tell the two renders apart)
function coldVal(r, i) { return String(9000 + r * 10 + i); }
function cellHtml(r, i, v, src) {
  return '<input type="text" class="bulk-cell" value="' + v + '" size="12" data-dot-path="qubits.q' + r + '.f' + i
    + '" data-resolved="qubits.q' + r + '.f' + i + '" data-linkable="1" data-orig="' + v + '"' + (src ? ' data-src="' + src + '"' : '')
    + ' title="qubits.q' + r + '.f' + i + '">';
}
function build() {
  let head = '';
  for (let i = 0; i < COLS.length; i++) {
    head += '<th class="bulk-col-head ck-' + i + '" data-col-key="c' + i + '" data-section="S" data-maxlen="' + (i < N_HOT ? 12 : 20) + '">'
      + '<span class="bulk-col-label">col ' + i + '</span><span class="bulk-col-stats" data-col-stats="c' + i + '"></span></th>';
  }
  let body = '';
  const map = { rows: [], cols: {} };
  for (let r = 0; r < N_ROWS; r++) {
    map.rows.push('q' + r);
    let tds = '';
    for (let i = 0; i < COLS.length; i++) {
      if (i < N_HOT) {
        tds += '<td class="bulk-td ck-' + i + '" data-col-key="c' + i + '">' + cellHtml(r, i, String((r + 1) * 100 + i)) + '</td>';
      } else {
        tds += '<td class="bulk-td ck-' + i + ' bulk-td-cold" data-col-key="c' + i + '"></td>';
        (map.cols['c' + i] = map.cols['c' + i] || []).push([coldVal(r, i), 'qubits.q' + r + '.f' + i, 0]);
      }
    }
    body += '<tr data-qubit="q' + r + '"><th class="bulk-rowhead" data-col-key="__id__">q' + r + '</th>' + tds
      + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr>';
  }
  return '<div id="table-pane"><div class="bulk-toolbar">'
    + '<details class="bulk-colvis"><summary>P</summary><div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span>'
    + '<span id="bulk-dirty-count"></span><button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr><th class="bulk-corner" data-col-key="__id__"></th>' + head
    + '</tr></thead><tbody>' + body + '</tbody></table></div>'
    + '<script type="application/json" id="bulk-cold-map">' + JSON.stringify(map) + '</script></div>';
}

function world(opts) {
  opts = opts || {};
  // docs/141 4ae B-8: the ROOT font is a stylesheet fact, and on the real chip
  // it is Pico's top breakpoint step -- 21 px, not the 16 px jsdom defaults to
  // and not the 17 px the old code hard-coded. Model it, because the width
  // freeze is computed from it and this file is the only place that pins the
  // frozen number. Note there is deliberately NO inline documentElement.style
  // .fontSize and no data-font-size attribute here: neither exists in the app
  // either, which is exactly why the old inline-only read could never see 21.
  const dom = new JSDOM('<!DOCTYPE html><html><head><style>html{font-size:21px}</style></head><body>'
    + build() + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win.__geomReads = 0;
  Object.defineProperty(win.screen, 'availWidth', { value: 1200, configurable: true });
  Object.defineProperty(win, 'innerWidth', { get: function () { win.__geomReads++; return 1200; }, configurable: true });
  win.document.querySelectorAll('th.bulk-col-head').forEach(function (h, i) {
    Object.defineProperty(h, 'offsetLeft', { get: function () { win.__geomReads++; return 60 + i * 400; } });
    Object.defineProperty(h, 'offsetWidth', { get: function () { win.__geomReads++; return 130; } });
  });
  const wrap = win.document.getElementById('table-pane');    // docs/141 4q: the ONE scroller
  Object.defineProperty(wrap, 'clientWidth', { value: 200 });
  wrap.scrollLeft = 0;
  win.htmx = { ajax: function () {} };
  win._log = { fetches: [] };
  win._fetchMode = opts.fetchMode || 'ok';
  win.fetch = function (url) {
    win._log.fetches.push(url);
    const m = /\/bulk\/cells\?cols=([^&]*)/.exec(url);
    if (!m) return Promise.reject(new Error('unexpected ' + url));
    const keys = decodeURIComponent(m[1]).split(',');
    if (win._fetchMode === 'fail') return Promise.reject(new Error('network down'));
    if (win._fetchMode === '400') {
      // docs/141 4ae: the answer that CANNOT change without a new page --
      // the server does not know these columns. 4af B-1 reads the header it
      // leaves behind.
      return Promise.resolve({ ok: false, status: 400, json: function () { return Promise.resolve({ ok: false, error: 'unknown columns' }); } });
    }
    if (win._fetchMode === '409') {
      return Promise.resolve({ ok: false, status: 409, json: function () { return Promise.resolve({ ok: false, error: 'a different chip is open' }); } });
    }
    const cells = {};
    keys.forEach(function (k) {
      const i = parseInt(k.slice(1), 10);
      cells[k] = {};
      for (let r = 0; r < N_ROWS; r++) cells[k]['q' + r] = cellHtml(r, i, coldVal(win._sortDesc ? (N_ROWS - 1 - r) : r, i), 'server');
    });
    return new Promise(function (res) {
      setTimeout(function () {
        res({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, cells: cells, seq: 1 }); } });
      }, opts.delay || 60);
    });
  };
  // docs/141 4ac: a REMEMBERED search. mount() fills the box FROM
  // localStorage['quam_bulk_search'] itself, so that is what has to be seeded
  // -- writing the box directly is overwritten a line later, exactly as it
  // would be in the app.
  if (opts.search) {
    const store = { quam_bulk_search: opts.search };
    Object.defineProperty(win, 'localStorage', {
      configurable: true,
      value: {
        getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
        setItem: function (k, v) { store[k] = String(v); },
        removeItem: function (k) { delete store[k]; },
      },
    });
  }
  // docs/141 4ae B-7: `noGridVirt` is the transport failure -- bulk-edit.js
  // arrives, grid-virt.js does not.
  if (!opts.noGridVirt) new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(MISSING_NOTE_JS).call(win);
  // docs/141 4ae B-8: grid-virt.js primes its root-font memo at SCRIPT
  // EVALUATION -- one getComputedStyle(root), which jsdom answers by
  // evaluating media queries and so charges one innerWidth read (real Chrome
  // forces 0 layouts and 0 style recalcs for it, measured with
  // Performance.getMetrics). That is not the mount, and this counter exists
  // to pin what the MOUNT reads, so bank it and start the mount from zero.
  win.__evalReads = win.__geomReads; win.__geomReads = 0;
  new win.Function(BULK_JS).call(win);
  win.BulkEdit.mount(COLS, { bands: {} }, [], { chip: 'chipA', qubits: [] });
  win.__mountReads = win.__geomReads;                    // what the MOUNT read (the later scroll pass may read geometry)
  return { win: win, doc: win.document, wrap: wrap };
}

async function main() {
  /* ── adoption ─────────────────────────────────────────────────────── */
  let W = world(); await tick(40);                       // the mount's own rAF pass settles first
  let doc = W.doc, win = W.win;
  let st = win.BulkEdit._virtState();
  ok(!!st, 'server-cold columns are adopted although the grid is far under the client gates (28 cells)');
  ok(st && st.remote.length === N_COLD && st.cold.length === N_COLD, 'the cold set is exactly the server-cold columns (' + (st && st.cold.join(',')) + ')');
  ok(win.__mountReads === 0, 'adoption read no geometry (reads: ' + win.__mountReads + ')');
  ok(doc.querySelectorAll('td.bulk-td-cold').length === N_COLD * N_ROWS && doc.querySelector('[data-col-key="c5"] .bulk-cell') === null,
     'server-cold tds stay empty at mount (no fetch before a pass)');
  ok(win._log.fetches.length === 0, 'the mount itself fetched nothing');

  /* ── docs/141 4af B-1: what assistive technology is told ──────────── */
  // The live region must be in the accessibility tree BEFORE it has anything
  // to say: content already present when a region enters the tree is not
  // announced, and `hidden` takes it back out (so un-hiding + filling in one
  // task is the same anti-pattern). Real chip, Chrome
  // Accessibility.getPartialAXTree: unpatched the element did not exist at
  // mount at all; patched it is role=status, name "", ignored=false.
  const noteAtMount = doc.getElementById('bulk-virt-note');
  ok(!!noteAtMount && noteAtMount.textContent === '' && noteAtMount.hidden !== true
     && noteAtMount.getAttribute('aria-live') === 'polite' && noteAtMount.getAttribute('role') === 'status',
     'the live region exists EMPTY and un-hidden at mount, so a later message is an addition ('
     + (noteAtMount ? JSON.stringify({ t: noteAtMount.textContent, h: noteAtMount.hidden }) : 'absent') + ')');
  // A cold cell is role=cell name="" -- indistinguishable from a parameter the
  // chip does not carry (7,200 of 7,810 data cells on the real 20Q chip). The
  // header says it once per column instead of 7,200 times per page.
  const a11yOf = (k) => { const m = doc.querySelector('th[data-col-key="' + k + '"] .bulk-col-a11y'); return m ? m.textContent : null; };
  ok(a11yOf('c3') === 'not loaded' && a11yOf('c6') === 'not loaded'
     && a11yOf('c0') === null && a11yOf('c1') === null,
     'every cold column header carries a "not loaded" mark and no hot one does ('
     + [a11yOf('c0'), a11yOf('c3')].join(' / ') + ')');
  const mark3 = doc.querySelector('th[data-col-key="c3"] .bulk-col-a11y');
  ok(mark3 && mark3.className.indexOf('visually-hidden') >= 0
     && mark3.nextElementSibling && mark3.nextElementSibling.classList.contains('bulk-col-stats'),
     'the mark is visually-hidden and sits before the stats, so the header name reads "label not loaded min..max"');
  const sheet = (doc.getElementById('bulk-virt-width-style') || {}).textContent || '';
  // docs/141 4ae B-8. The glyph estimate must follow the COMPUTED root font
  // (21 px here), not an inline style nobody writes: at 17 it froze this
  // column 46 px too narrow, and on the real 20Q chip 143 of 224 columns then
  // GREW on hydration (pane 53,411 -> 57,899 px) -- the churn the freeze
  // exists to remove. Reading it from the stylesheet cuts that to 129 columns
  // and 57,043 -> 57,899 px, worst single column +86 -> +39 px.
  ok(win.document.documentElement.style.fontSize === ''
     && !win.document.documentElement.hasAttribute('data-font-size'),
     'the root carries NO inline font-size and no data-font-size -- as in the app');
  const rootPx = parseFloat(win.getComputedStyle(win.document.documentElement).fontSize);
  ok(rootPx === 21, 'the fixture root font is the stylesheet ladder value (' + rootPx + 'px)');
  ok(Math.abs(win.GridVirt.pxPerChar() - rootPx * 0.92 * 0.62) < 1e-6,
     'pxPerChar is derived from the COMPUTED root font (' + win.GridVirt.pxPerChar().toFixed(4)
     + ', not the 9.6968 a 17 px assumption gives)');
  const expectPx = Math.round(20 * (rootPx * 0.92 * 0.62) + 28);
  ok(sheet.indexOf('#bulk-table th.ck-5{min-width:' + expectPx + 'px}') >= 0,
     'a server-cold column is frozen at the width its header data-maxlen implies (' + expectPx + 'px)');
  ok(sheet.indexOf('th.ck-1{') < 0, 'a hot column is not frozen');

  /* ── whole-chip search over a server-cold value ───────────────────── */
  const sb = doc.getElementById('bulk-search');
  sb.value = '9026'; sb.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(250);
  const rowHidden = (id) => doc.querySelector('tr[data-qubit="' + id + '"]').classList.contains('bulk-row-hidden');
  ok(!rowHidden('q2') && rowHidden('q0'), 'a value that lives only in a server-cold column still finds its row');
  sb.value = ''; sb.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(250);
  // the search's pass may have asked for on-screen cold columns; reset the log
  win._log.fetches.length = 0;

  /* ── docs/141 4ac (CRITICAL): a REMEMBERED search at MOUNT ────────── */
  {
    const W2 = world({ search: '9026' });                 // the value lives only in a server-cold column
    await tick(250);
    const d2 = W2.doc;
    const hid = (id) => d2.querySelector('tr[data-qubit="' + id + '"]').classList.contains('bulk-row-hidden');
    const visible2 = Array.prototype.filter.call(
      d2.querySelectorAll('tr[data-qubit]'), (r) => !r.classList.contains('bulk-row-hidden')
    ).map((r) => r.getAttribute('data-qubit'));
    ok(visible2.length === 1 && visible2[0] === 'q2',
       'a search restored BEFORE the mount finds its server-cold value and hides the rest ('
       + JSON.stringify(visible2) + ')');
    ok((d2.getElementById('bulk-search-count').textContent || '').indexOf('1 of ') === 0,
       'and the count says 1, not 0 (' + d2.getElementById('bulk-search-count').textContent + ')');
    // restore the shared world for the rest of the run
    global.window = win; global.document = doc;
  }

  /* ── docs/141 4ac: an undo in a server-cold column repairs its SEARCH ─ */
  {
    const W3 = world();
    await tick(40);
    const w3 = W3.win, d3 = W3.doc;
    const st3 = w3.BulkEdit._virtState();
    ok(st3 && st3.remote.length > 0, 'fixture: the third world has server-cold columns');
    // the path of a value that lives only in a cold column, from the cold map
    const map = JSON.parse(d3.getElementById('bulk-cold-map').textContent);
    const colKey = Object.keys(map.cols)[0];
    const rowId = map.rows[0];
    const ent = map.cols[colKey][0];
    const dotPath = ent[1];
    const before = String(ent[0]);
    const sb3 = d3.getElementById('bulk-search');
    const shown = () => Array.prototype.filter.call(
      d3.querySelectorAll('tr[data-qubit]'), (r) => !r.classList.contains('bulk-row-hidden')
    ).map((r) => r.getAttribute('data-qubit'));

    sb3.value = before; sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(250);
    ok(shown().indexOf(rowId) >= 0, 'fixture: the pre-undo value is found in the cold column');

    // an undo names the path and its new display value; the cell is remote, so
    // nothing repaints -- only the cold map can carry the new search text
    w3.BulkEdit.revertPaths([{ dot_path: dotPath, old_value_disp: 'ZZZ9' }]);
    await tick(60);

    sb3.value = 'ZZZ9'; sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(250);
    ok(shown().indexOf(rowId) >= 0,
       'after the undo the search finds the value the chip now holds');
    sb3.value = before; sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(250);
    ok(shown().indexOf(rowId) < 0,
       'and no longer matches the value it replaced (the map is not a stale snapshot)');
    ok(w3._log.fetches.length === 0 || !w3._log.fetches.some((u) => u.indexOf('cols=' + colKey) >= 0),
       'repairing the map costs no /bulk/cells round trip');
    global.window = win; global.document = doc;
  }

  /* ── hydration on a scroll pass ───────────────────────────────────── */
  W.wrap.scrollLeft = 1200;                               // edge 1700: c3 (1260) + c4 (1660) due, c5/c6 not
  W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  ok(win._log.fetches.length === 1 && /\/bulk\/cells\?cols=c3%2Cc4(&|$)/.test(win._log.fetches[0]),
     'one request carries every due column and only those (' + win._log.fetches.join(' | ') + ')');
  ok(/chip=chipA/.test(win._log.fetches[0] || ''), 'the request names the chip the page was rendered for');
  // docs/141 4q: the pane is the ONE scroller, so the toolbar row is moved
  // along with it (it would otherwise scroll out to the left)
  ok(doc.querySelector('.bulk-toolbar').style.transform === 'translateX(1200px)',
     'the toolbar follows the pane\'s sideways scroll (' + doc.querySelector('.bulk-toolbar').style.transform + ')');
  st = win.BulkEdit._virtState();
  ok(st && st.inflight.length === 2, 'the two columns are in flight');
  W.wrap.dispatchEvent(new win.Event('scroll'));       // a second pass while in flight
  await tick(5);
  ok(win._log.fetches.length === 1, 'a column in flight is not asked for twice');
  await settle(() => doc.querySelector('tr[data-qubit="q2"] td[data-col-key="c4"] .bulk-cell'));
  const c4q2 = doc.querySelector('tr[data-qubit="q2"] td[data-col-key="c4"] .bulk-cell');
  ok(!!c4q2 && c4q2.value === '9024' && c4q2.getAttribute('data-src') === 'server' && c4q2.getAttribute('data-dot-path') === 'qubits.q2.f4',
     'the landed cell is the server\'s markup (' + (c4q2 && c4q2.value) + ')');
  st = win.BulkEdit._virtState();
  ok(st && st.cold.join(',') === 'c5,c6' && st.inflight.length === 0 && doc.querySelectorAll('td.bulk-td-cold').length === 2 * N_ROWS,
     'the columns not yet on screen stay cold (' + (st && st.cold.join(',')) + ')');
  const stat = doc.querySelector('[data-col-stats="c4"]');
  ok(stat && stat.textContent.length > 0, 'the hydrated column got its header stats (' + (stat && stat.textContent) + ')');
  // docs/141 4af B-1: the mark is a statement about NOW, so it goes when the
  // column lands (real chip: 311 marks -> 310 on one hydrateColumn)
  ok(doc.querySelector('th[data-col-key="c4"] .bulk-col-a11y') === null
     && doc.querySelector('th[data-col-key="c5"] .bulk-col-a11y') !== null,
     'a landed column drops its "not loaded" mark; one still cold keeps it');
  W.wrap.scrollLeft = 2200;                               // edge 2700: c5 + c6
  W.wrap.dispatchEvent(new win.Event('scroll'));
  await settle(() => win.BulkEdit._virtState() === null);
  ok(win._log.fetches.length === 2 && doc.querySelectorAll('td.bulk-td-cold').length === 0 && win.BulkEdit._virtState() === null,
     'every column is here: the cold set is empty and _virt is released');

  /* ── the pass is a window: a jump to the far right skips the middle ── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  win._log.fetches.length = 0;
  W.wrap.scrollLeft = 10000; W.wrap.dispatchEvent(new win.Event('scroll'));   // every column is LEFT of the window
  await tick(20);
  ok(win._log.fetches.length === 0, 'a jump past every column fetches nothing (the skipped columns stay cold)');
  W.wrap.scrollLeft = 2200; W.wrap.dispatchEvent(new win.Event('scroll'));    // window 1900..2700: c5 (2060) + c6 (2460)
  await tick(20);
  ok(win._log.fetches.length === 1 && /cols=c5%2Cc6(&|$)/.test(win._log.fetches[0]),
     'scrolling back fetches the columns inside the window only (' + win._log.fetches.join(' | ') + ')');
  await settle(() => { const v = win.BulkEdit._virtState(); return v && !v.inflight.length; });
  st = win.BulkEdit._virtState();
  ok(st && st.cold.join(',') === 'c3,c4', 'the columns left of the window are still cold (' + (st && st.cold.join(',')) + ')');

  /* ── failure: stays cold, one honest line, retried ────────────────── */
  W = world({ fetchMode: 'fail' }); doc = W.doc; win = W.win; await tick(40);
  const noteBornEmpty = doc.getElementById('bulk-virt-note');   // docs/141 4af B-1
  win._log.fetches.length = 0;
  W.wrap.scrollLeft = 1200; W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  st = win.BulkEdit._virtState();
  ok(st && st.cold.length === N_COLD && st.inflight.length === 0 && st.failed === 2, 'a failed batch leaves the columns cold and not in flight (failed ' + (st && st.failed) + ')');
  const note = doc.getElementById('bulk-virt-note');
  // docs/141 4af B-1: the message is a TEXT change to a region that was
  // already in the accessibility tree, never a region born carrying it
  ok(note === noteBornEmpty && noteBornEmpty !== null,
     'the message went into the live region that already existed (an addition, not a newly-inserted node)');
  ok(note && !note.hidden && /2 columns could not be loaded/.test(note.textContent) && /retry/.test(note.textContent),
     'and says so in one line (' + (note && note.textContent) + ')');
  win._fetchMode = 'ok';
  W.wrap.dispatchEvent(new win.Event('scroll'));
  await settle(() => win._log.fetches.length === 2
                    && doc.querySelectorAll('td.bulk-td-cold').length === 2 * N_ROWS);
  ok(win._log.fetches.length === 2 && doc.querySelectorAll('td.bulk-td-cold').length === 2 * N_ROWS, 'the next pass retries and lands (the off-screen two stay cold)');
  ok(note.hidden || note.textContent === '', 'the note clears once the columns are here');
  // docs/141 4af B-1: and it stays IN the accessibility tree while empty. A
  // `hidden` region is out of the tree, so the NEXT message would again be
  // present at the moment the region re-enters it -- which is not announced.
  ok(note.hidden !== true && note.textContent === '',
     'the emptied region is still in the accessibility tree, so the next message is an addition (hidden='
     + note.hidden + ')');
  W = world({ fetchMode: '409' }); doc = W.doc; win = W.win; await tick(40);
  W.wrap.scrollLeft = 1200; W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  const note409 = doc.getElementById('bulk-virt-note');
  ok(note409 && /different chip/.test(note409.textContent) && /reload/.test(note409.textContent),
     'a 409 (another chip open) names it and asks for a reload');
  /* ── docs/141 4ae B-10: a RETIRED cell says so, per cell ───────────── */
  {
    const deadTds = doc.querySelectorAll('#bulk-table tbody td.bulk-td-dead');
    ok(deadTds.length === 2 * N_ROWS,
       'the retired columns\' cells are marked bulk-td-dead, so LOADING and NEVER-COMING '
       + 'are not one appearance (' + deadTds.length + ' of ' + (2 * N_ROWS) + ')');
    ok(deadTds.length > 0 && /reload the page/.test(deadTds[0].getAttribute('title') || ''),
       'and each carries the only per-cell explanation a user can reach ('
       + (deadTds.length ? deadTds[0].getAttribute('title') : 'none') + ')');
    const stillCold = doc.querySelectorAll('#bulk-table tbody td.bulk-td-cold:not(.bulk-td-dead)');
    ok(stillCold.length === 2 * N_ROWS,
       'a merely-cold cell is NOT marked dead — it is still on its way ('
       + stillCold.length + ')');
  }

  /* ── docs/141 4ae B-7: grid-virt.js never arrived ──────────────────── */
  {
    const W7 = world({ noGridVirt: true });
    await tick(40);
    const d7 = W7.doc, w7 = W7.win;
    ok(!w7.GridVirt && w7.BulkEdit._virtState() === null,
       'fixture: with grid-virt.js blocked there is no GridVirt and no virt state');
    const n7 = d7.getElementById('bulk-virt-note');
    ok(n7 && !n7.hidden && /4 columns could not be loaded/.test(n7.textContent)
        && /reload the page/.test(n7.textContent),
       'the blank half explains itself instead of staying silent (' + (n7 && n7.textContent) + ')');
    ok(d7.querySelectorAll('#bulk-table tbody td.bulk-td-dead').length === N_COLD * N_ROWS,
       'and every unfillable cell is marked never-coming, not loading ('
       + d7.querySelectorAll('#bulk-table tbody td.bulk-td-dead').length + ')');
    ok(w7._log.fetches.length === 0,
       'nothing is fetched — there is no hydrator to fetch with');
    global.window = win; global.document = doc;   // restore the shared world
  }

  /* ── docs/141 4af B-1: a RETIRED column says the OTHER thing ──────── */
  // "still coming" and "never coming" are not one state, and the header is
  // the only place a screen reader hears the difference for a blank cell.
  W = world({ fetchMode: '400' }); doc = W.doc; win = W.win; await tick(40);
  W.wrap.scrollLeft = 1200; W.wrap.dispatchEvent(new win.Event('scroll'));
  await settle(() => { const m = doc.querySelector('th[data-col-key="c3"] .bulk-col-a11y'); return m && m.textContent === 'could not be loaded'; });
  const dead3 = doc.querySelector('th[data-col-key="c3"] .bulk-col-a11y');
  const alive5 = doc.querySelector('th[data-col-key="c5"] .bulk-col-a11y');
  ok(dead3 && dead3.textContent === 'could not be loaded' && alive5 && alive5.textContent === 'not loaded',
     'a column the server refused says "could not be loaded"; one still on its way says "not loaded" ('
     + (dead3 && dead3.textContent) + ' / ' + (alive5 && alive5.textContent) + ')');

  /* ── undo repaint: a server-cold path is missing, no fetch ────────── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  win._log.fetches.length = 0;
  const rp = win.BulkEdit.revertPaths([{ dot_path: 'qubits.q1.f5', old_value_str: '9' }]);
  ok(rp && rp.missing === 1 && (rp.uncovered || []).length === 0 && win._log.fetches.length === 0,
     'an undo naming a server-cold path is "missing" (no cell, no fetch, no rebuild) — the column arrives reverted');
  const rp2 = win.BulkEdit.revertPaths([{ dot_path: 'qubits.q1.f1', old_value_str: '9' }]);
  ok(rp2 && rp2.patched === 1, 'a hot path is repainted as before');

  /* ── an apply's cross-table sync never fetches a server-cold column ── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  win._log.fetches.length = 0;
  win.BulkEdit._syncApplied([{ dot_path: 'qubits.q0.f1', resolved_path: 'qubits.q0.f1', applied: true, display: '777' }]);
  await tick(20);
  ok(win._log.fetches.length === 0, 'the apply-echo sync touches only what is here (a server-cold column arrives from the working copy when fetched)');
  ok(doc.querySelector('tr[data-qubit="q0"] td[data-col-key="c1"] .bulk-cell').value === '777', 'and repaints the hot linked cell');

  /* ── the edit carry lands after the fetch ─────────────────────────── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  win._log.fetches.length = 0;
  // an unsaved edit captured before a dyn-column reload whose target column
  // the fresh render made server-cold (the shape _captureEditCarry stores)
  win.BulkEdit._setCarry({ at: Date.now(), list: [
    { dp: 'qubits.q1.f5', value: 'CARRIED' },          // server-cold column
    { dp: 'qubits.q1.f1', value: 'HOTEDIT' } ] });      // hot column
  win.BulkEdit._ge.consumeCarry();
  ok(win._log.fetches.length === 1, 'a carried edit into a server-cold column fetches (' + win._log.fetches.length + ')');
  ok(doc.querySelector('tr[data-qubit="q1"] td[data-col-key="c5"] .bulk-cell') === null, 'and is not placed before the column is here');
  await settle(() => doc.querySelector('tr[data-qubit="q1"] td[data-col-key="c5"] .bulk-cell'));
  const c5q1 = doc.querySelector('tr[data-qubit="q1"] td[data-col-key="c5"] .bulk-cell');
  const c1q1 = doc.querySelector('tr[data-qubit="q1"] td[data-col-key="c1"] .bulk-cell');
  ok(!!c5q1 && c5q1.value === 'CARRIED' && c5q1.classList.contains('dirty'), 'the carried edit lands in the fetched cell, dirty (' + (c5q1 && c5q1.value) + ')');
  ok(!!c1q1 && c1q1.value === 'HOTEDIT' && c1q1.classList.contains('dirty'), 'the hot one too');

  /* ── sort by a server-cold column fetches first ───────────────────── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  win._sortDesc = true;                                  // server values descend by row: a real sort reorders
  win._log.fetches.length = 0;
  win.BulkEdit.sort('c6');
  ok(win._log.fetches.length === 1 && /cols=c6(&|$)/.test(win._log.fetches[0]), 'sorting a server-cold column fetches that column first');
  await settle(() => doc.querySelector('td[data-col-key="c6"] .bulk-cell')
                    && Array.from(doc.querySelectorAll('tbody tr'))
                         .map((tr) => tr.getAttribute('data-qubit')).join(',') === 'q3,q2,q1,q0');
  const order = Array.from(doc.querySelectorAll('tbody tr')).map((tr) => tr.getAttribute('data-qubit'));
  ok(doc.querySelector('td[data-col-key="c6"] .bulk-cell') !== null && order.join(',') === 'q3,q2,q1,q0', 'then the column is here and the rows were sorted by it (' + order.join(',') + ')');

  /* ── the request hook sends the viewport hint ─────────────────────── */
  W = world(); doc = W.doc; win = W.win; await tick(40);
  const evt = new win.CustomEvent('htmx:configRequest', { detail: { path: '/bulk', parameters: {} } });
  doc.dispatchEvent(evt);
  ok(/vw=1200/.test(evt.detail.path), '/bulk carries vw=screen.availWidth (' + evt.detail.path + ')');
  const evt2 = new win.CustomEvent('htmx:configRequest', { detail: { path: '/bulk/all-values', parameters: {} } });
  doc.dispatchEvent(evt2);
  ok(evt2.detail.path === '/bulk/all-values', 'other paths are untouched');

  // ── docs/163: the mount reads geometry ONCE, and last ───────────────────
  // `_pinBarsToScroll` reads scrollLeft. Every phase it used to sit in front
  // of WRITES, and a read after a write lays the whole grid out again -- so
  // the mount paid two full layouts of the same 53,000 px table (measured on
  // the 20Q chip: 137 ms for this read, 124 ms for grid-virt's deferred pass
  // whose own read the following writes invalidated). Moving the read behind
  // every write cut the cold mount's blocking time 510 -> 422 ms and the
  // keep-route return's 158 -> 131 ms, both over three runs.
  //
  // Pinned behaviourally: the real mount pushes one entry per phase, so the
  // LAST entry naming the pin proves the ordering. A source-text check would
  // pass with the call moved back (docs/148's lesson about vacuous pins).
  {
    const w = world({}).win;
    const phases = (w.__bulkMountTimings || []).map((e) => e[0]);
    ok(phases.length > 3, 'S9: the mount recorded its phases (got ' + phases.length + ')');
    ok(phases[phases.length - 1] === 'pin bars',
       'S9: the geometry read is the mount\'s LAST phase (got "'
       + phases[phases.length - 1] + '", order: ' + phases.join(' > ') + ')');
    const iPin = phases.indexOf('pin bars');
    ['virtualization', 'editing + pins', 'linked cells'].forEach((ph) => {
      const j = phases.indexOf(ph);
      if (j < 0) return;
      ok(j < iPin, 'S9: "' + ph + '" (a writing phase) runs BEFORE the read');
    });
  }

  console.log(fails ? ('FAILED ' + fails) : 'bulk_virt_server_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
}
main().catch(function (e) { console.error('ERR', e && e.stack || e); process.exit(1); });
