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
const BULK_JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

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
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + build() + '</body></html>',
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
  const sheet = (doc.getElementById('bulk-virt-width-style') || {}).textContent || '';
  const expectPx = Math.round(20 * (17 * 0.92 * 0.62) + 28);
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
  await tick(90);
  const c4q2 = doc.querySelector('tr[data-qubit="q2"] td[data-col-key="c4"] .bulk-cell');
  ok(!!c4q2 && c4q2.value === '9024' && c4q2.getAttribute('data-src') === 'server' && c4q2.getAttribute('data-dot-path') === 'qubits.q2.f4',
     'the landed cell is the server\'s markup (' + (c4q2 && c4q2.value) + ')');
  st = win.BulkEdit._virtState();
  ok(st && st.cold.join(',') === 'c5,c6' && st.inflight.length === 0 && doc.querySelectorAll('td.bulk-td-cold').length === 2 * N_ROWS,
     'the columns not yet on screen stay cold (' + (st && st.cold.join(',')) + ')');
  const stat = doc.querySelector('[data-col-stats="c4"]');
  ok(stat && stat.textContent.length > 0, 'the hydrated column got its header stats (' + (stat && stat.textContent) + ')');
  W.wrap.scrollLeft = 2200;                               // edge 2700: c5 + c6
  W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(90);
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
  await tick(90);
  st = win.BulkEdit._virtState();
  ok(st && st.cold.join(',') === 'c3,c4', 'the columns left of the window are still cold (' + (st && st.cold.join(',')) + ')');

  /* ── failure: stays cold, one honest line, retried ────────────────── */
  W = world({ fetchMode: 'fail' }); doc = W.doc; win = W.win; await tick(40);
  win._log.fetches.length = 0;
  W.wrap.scrollLeft = 1200; W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  st = win.BulkEdit._virtState();
  ok(st && st.cold.length === N_COLD && st.inflight.length === 0 && st.failed === 2, 'a failed batch leaves the columns cold and not in flight (failed ' + (st && st.failed) + ')');
  const note = doc.getElementById('bulk-virt-note');
  ok(note && !note.hidden && /2 columns could not be loaded/.test(note.textContent) && /retry/.test(note.textContent),
     'and says so in one line (' + (note && note.textContent) + ')');
  win._fetchMode = 'ok';
  W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(90);
  ok(win._log.fetches.length === 2 && doc.querySelectorAll('td.bulk-td-cold').length === 2 * N_ROWS, 'the next pass retries and lands (the off-screen two stay cold)');
  ok(note.hidden || note.textContent === '', 'the note clears once the columns are here');
  W = world({ fetchMode: '409' }); doc = W.doc; win = W.win; await tick(40);
  W.wrap.scrollLeft = 1200; W.wrap.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  const note409 = doc.getElementById('bulk-virt-note');
  ok(note409 && /different chip/.test(note409.textContent) && /reload/.test(note409.textContent),
     'a 409 (another chip open) names it and asks for a reload');

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
  await tick(90);
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
  await tick(90);
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

  console.log(fails ? ('FAILED ' + fails) : 'bulk_virt_server_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
}
main().catch(function (e) { console.error('ERR', e && e.stack || e); process.exit(1); });
