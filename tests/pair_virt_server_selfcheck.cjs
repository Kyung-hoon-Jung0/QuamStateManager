// docs/141 §4ad — the PAIR grid's cold-column virtualization, against the REAL
// pair-edit.js + grid-virt.js under jsdom. §4n built this mechanism for the
// qubit grid and left the pair grid whole on purpose; §4ac then measured what
// that cost (the pair table was 53% of the /bulk document). The core is shared
// now, so what this file pins is the PAIR grid's binding to it and the call
// sites that had to learn a cell may not be here:
//  - the server-cold tds are adopted from #bulk-pair-cold-map (its OWN element,
//    never the qubit grid's) and read no geometry
//  - a value that lives only in a cold pair column is still found by the
//    whole-chip search (docs/85), and the count is honest
//  - hydration is GET /bulk/cells?grid=pair, ONE request per pass, a column in
//    flight is never asked for twice, the landed markup is the server's own
//  - sorting by a cold column fetches it FIRST, then sorts
//  - keyboard navigation into a cold column starts its fetch
//  - an undo naming a cold column repairs the map without a round trip
//  - the two grids' instances do not share a style element, a note, or a map
//
// Run: node tests/pair_virt_server_selfcheck.cjs   (needs jsdom)
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
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const GRID_VIRT_JS = fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8');
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const N_ROWS = 4, N_HOT = 3, N_COLD = 4;      // 28 cells: far under every client gate
const COLS = [];
for (let i = 0; i < N_HOT + N_COLD; i++) {
  COLS.push({ key: 'p' + i, label: 'pair col ' + i, section: 'S', unit: '', default_on: true, maxlen: i < N_HOT ? 12 : 20 });
}
const PAIRS = [];
for (let r = 0; r < N_ROWS; r++) PAIRS.push('q' + r + '-' + (r + 1));

function coldVal(r, i) { return String(7000 + r * 10 + i); }
function cellHtml(r, i, v, src) {
  const dp = 'qubit_pairs.' + PAIRS[r] + '.f' + i;
  return '<input type="text" class="bulk-cell" value="' + v + '" size="12" data-dot-path="' + dp
    + '" data-resolved="' + dp + '" data-orig="' + v + '"' + (src ? ' data-src="' + src + '"' : '')
    + ' title="' + dp + '">';
}

function build() {
  let head = '';
  for (let i = 0; i < COLS.length; i++) {
    head += '<th scope="col" class="bulk-col-head ck-' + i + '" data-col-key="p' + i + '" data-section="S" data-maxlen="'
      + COLS[i].maxlen + '"><span class="bulk-col-label">' + COLS[i].label + '</span>'
      + '<span class="bulk-col-stats" data-col-stats="p' + i + '"></span></th>';
  }
  let body = '';
  const map = { rows: [], cols: {} };
  for (let r = 0; r < N_ROWS; r++) {
    map.rows.push(PAIRS[r]);
    let tds = '';
    for (let i = 0; i < COLS.length; i++) {
      if (i < N_HOT) {
        tds += '<td class="bulk-td ck-' + i + '" data-col-key="p' + i + '">' + cellHtml(r, i, String((r + 1) * 100 + i)) + '</td>';
      } else {
        tds += '<td class="bulk-td ck-' + i + ' bulk-td-cold" data-col-key="p' + i + '"></td>';
        (map.cols['p' + i] = map.cols['p' + i] || []).push([coldVal(r, i), 'qubit_pairs.' + PAIRS[r] + '.f' + i, 0]);
      }
    }
    body += '<tr data-qubit="' + PAIRS[r] + '" data-pair="' + PAIRS[r] + '"><th class="bulk-rowhead" data-col-key="__id__">'
      + PAIRS[r] + '</th>' + tds
      + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button><span class="bulk-row-error" hidden></span></td></tr>';
  }
  return '<div id="table-pane"><div class="bulk-panel"><div class="bulk-toolbar">'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span></span>'
    + '<span id="bulk-pair-search-count"></span></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th></tr></thead><tbody></tbody></table></div>'
    + '<div class="bulk-table-wrap bulk-pair-table-wrap"><table id="bulk-pair-table" class="bulk-table bulk-pair-table">'
    + '<thead><tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>' + head
    + '</tr></thead><tbody>' + body + '</tbody></table></div>'
    + '<script type="application/json" id="bulk-pair-cold-map">' + JSON.stringify(map) + '</script>'
    + '</div></div>';
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
  win.document.querySelectorAll('#bulk-pair-table th.bulk-col-head').forEach(function (h, i) {
    Object.defineProperty(h, 'offsetLeft', { get: function () { win.__geomReads++; return 60 + i * 400; } });
    Object.defineProperty(h, 'offsetWidth', { get: function () { win.__geomReads++; return 130; } });
  });
  const pane = win.document.getElementById('table-pane');
  Object.defineProperty(pane, 'clientWidth', { value: 200 });
  pane.scrollLeft = 0;
  win.htmx = { ajax: function () {} };
  win._log = { fetches: [] };
  win._fetchMode = opts.fetchMode || 'ok';
  win.__bulkChipKey = 'chipA#deadbeef';
  win.fetch = function (url) {
    win._log.fetches.push(url);
    const m = /\/bulk\/cells\?cols=([^&]*)/.exec(url);
    if (!m) return Promise.reject(new Error('unexpected ' + url));
    const keys = decodeURIComponent(m[1]).split(',');
    if (win._fetchMode === 'fail') return Promise.reject(new Error('network down'));
    const cells = {};
    keys.forEach(function (k) {
      const i = parseInt(k.slice(1), 10);
      cells[k] = {};
      for (let r = 0; r < N_ROWS; r++) cells[k][PAIRS[r]] = cellHtml(r, i, coldVal(r, i), 'server');
    });
    return new Promise(function (res) {
      setTimeout(function () {
        res({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, grid: 'pair', cells: cells, seq: 1 }); } });
      }, opts.delay || 60);
    });
  };
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
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  new win.Function(PAIR_JS).call(win);
  win.BulkPairEdit.mount(COLS);
  win.__mountReads = win.__geomReads;
  return { win: win, doc: win.document, pane: pane };
}

async function main() {
  /* ── adoption ─────────────────────────────────────────────────────── */
  let W = world(); await tick(40);
  let doc = W.doc, win = W.win;
  const coldTds = () => doc.querySelectorAll('#bulk-pair-table td.bulk-td-cold');
  ok(coldTds().length === N_COLD * N_ROWS,
     'the server-cold pair tds stay empty at mount (' + coldTds().length + ')');
  ok(win._log.fetches.length === 0, 'the mount itself fetched nothing');
  ok(win.__mountReads === 0, 'adoption read no geometry (reads: ' + win.__mountReads + ')');
  const sheet = (doc.getElementById('bulk-pair-virt-width-style') || {}).textContent || '';
  ok(sheet.indexOf('#bulk-pair-table th.ck-' + N_HOT + '{min-width:') >= 0,
     'a cold pair column is frozen from its header data-maxlen (' + sheet.split('\n')[0] + ')');
  ok(sheet.indexOf('#bulk-table ') < 0, 'and the rule names the PAIR table, never the qubit one');
  ok(!doc.getElementById('bulk-virt-width-style')
     || (doc.getElementById('bulk-virt-width-style').textContent || '') === '',
     'the qubit grid\'s own style element is untouched');

  /* ── the whole-chip search still finds a cold pair value ──────────── */
  const sb = doc.getElementById('bulk-search');
  sb.value = '7013'; sb.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(260);
  const rowHidden = (id) => doc.querySelector('#bulk-pair-table tr[data-pair="' + id + '"]').classList.contains('bulk-row-hidden');
  ok(!rowHidden(PAIRS[1]) && rowHidden(PAIRS[0]),
     'a value that lives only in a cold pair column still finds its row');
  const cnt = doc.getElementById('bulk-pair-search-count');
  ok(/^1 of 4 pairs/.test(cnt.textContent || ''), 'and the count is honest (' + cnt.textContent + ')');
  sb.value = ''; sb.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(260);
  win._log.fetches.length = 0;

  /* ── hydration on a scroll pass ───────────────────────────────────── */
  W.pane.scrollLeft = 1200;                     // edge 1700: p3 (1260), p4 (1660)
  W.pane.dispatchEvent(new win.Event('scroll'));
  await tick(30);
  ok(win._log.fetches.length === 1, 'one request for the whole pass (' + win._log.fetches.length + ')');
  ok(/grid=pair/.test(win._log.fetches[0] || ''), 'and it names the PAIR grid (' + win._log.fetches[0] + ')');
  ok(/chip=chipA%23deadbeef/.test(win._log.fetches[0] || ''), 'and carries the chip token');
  W.pane.dispatchEvent(new win.Event('scroll'));   // a second pass while in flight
  await tick(5);
  ok(win._log.fetches.length === 1, 'a column in flight is not asked for twice');
  await tick(120);
  const landed = doc.querySelector('#bulk-pair-table td[data-col-key="p3"] .bulk-cell');
  ok(!!landed && landed.getAttribute('data-src') === 'server',
     'the landed cell is the SERVER\'s markup, verbatim');
  ok(landed && landed.value === coldVal(0, 3), 'with the right value (' + (landed && landed.value) + ')');
  const stat = doc.querySelector('[data-col-stats="p3"]');
  ok(stat && stat.textContent.length > 0, 'and the column got its header stats (' + (stat && stat.textContent) + ')');

  /* ── sorting a cold column fetches it first ───────────────────────── */
  {
    const W2 = world(); await tick(40);
    const w2 = W2.win, d2 = W2.doc;
    w2._log.fetches.length = 0;
    const th = d2.querySelector('#bulk-pair-table th[data-col-key="p6"]');
    th.dispatchEvent(new w2.MouseEvent('click', { bubbles: true }));
    await tick(10);
    ok(w2._log.fetches.length === 1 && /cols=p6/.test(w2._log.fetches[0]),
       'sorting by a cold pair column fetches that column first');
    await tick(140);
    const ids = Array.prototype.map.call(d2.querySelectorAll('#bulk-pair-table tbody tr'),
      (r) => r.getAttribute('data-pair'));
    ok(d2.querySelectorAll('#bulk-pair-table td[data-col-key="p6"] .bulk-cell').length === N_ROWS,
       'then the column is here');
    ok(ids.length === N_ROWS, 'and the rows are still all there (' + ids.join(',') + ')');
    global.window = win; global.document = doc;
  }

  /* ── an undo of a cold pair value repairs the search map ──────────── */
  {
    const W3 = world(); await tick(40);
    const w3 = W3.win, d3 = W3.doc;
    const dp = 'qubit_pairs.' + PAIRS[0] + '.f' + N_HOT;
    const sb3 = d3.getElementById('bulk-search');
    const shown = () => Array.prototype.filter.call(
      d3.querySelectorAll('#bulk-pair-table tbody tr'), (r) => !r.classList.contains('bulk-row-hidden')
    ).map((r) => r.getAttribute('data-pair'));

    sb3.value = coldVal(0, N_HOT); sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(260);
    ok(shown().indexOf(PAIRS[0]) >= 0, 'fixture: the pre-undo value is found');
    w3._log.fetches.length = 0;
    w3.BulkPairEdit.revertPaths([{ dot_path: dp, old_value_disp: 'ZZZ7' }]);
    await tick(40);
    sb3.value = 'ZZZ7'; sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(260);
    ok(shown().indexOf(PAIRS[0]) >= 0, 'after the undo the search finds the value the chip now holds');
    sb3.value = coldVal(0, N_HOT); sb3.dispatchEvent(new w3.Event('input', { bubbles: true }));
    await tick(260);
    ok(shown().indexOf(PAIRS[0]) < 0, 'and no longer matches the value it replaced');
    ok(w3._log.fetches.length === 0, 'repairing the map costs no /bulk/cells round trip');
    global.window = win; global.document = doc;
  }

  /* ── a failed batch is honest, and retried ────────────────────────── */
  {
    const W4 = world({ fetchMode: 'fail' }); await tick(40);
    const w4 = W4.win, d4 = W4.doc;
    W4.pane.scrollLeft = 1200;
    W4.pane.dispatchEvent(new w4.Event('scroll'));
    await tick(60);
    const note = d4.getElementById('bulk-pair-virt-note');
    ok(note && /could not be loaded/.test(note.textContent || ''),
       'a failed pair batch says so in one line (' + (note && note.textContent) + ')');
    ok(d4.querySelectorAll('#bulk-pair-table td.bulk-td-cold').length === N_COLD * N_ROWS,
       'and the columns stay cold, to be retried');
    ok(!d4.getElementById('bulk-virt-note'), 'the note is the PAIR grid\'s own element');
    global.window = win; global.document = doc;
  }

  /* ── keyboard navigation into a cold column starts its fetch ──────── */
  {
    const W5 = world(); await tick(40);
    const w5 = W5.win, d5 = W5.doc;
    w5._log.fetches.length = 0;
    // the REAL gesture: Tab out of the last hot cell of a row. _tabMove walks
    // the row's tds through _editableIn, which is where a cold td must start
    // its fetch (a cold cell has no input at all, so navigation would
    // otherwise step straight over the whole column).
    const lastHot = d5.querySelector(
      '#bulk-pair-table tr[data-pair="' + PAIRS[0] + '"] td[data-col-key="p' + (N_HOT - 1) + '"] .bulk-cell');
    ok(!!lastHot, 'fixture: the last hot cell of the first row');
    lastHot.focus();
    lastHot.dispatchEvent(new w5.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    await tick(10);
    ok(w5._log.fetches.length >= 1,
       'Tab into a cold pair column starts its fetch (' + w5._log.fetches.join(' | ') + ')');
    ok(/grid=pair/.test(w5._log.fetches[0] || ''), 'and it asks for the PAIR grid');
    await tick(140);
    ok(d5.querySelectorAll('#bulk-pair-table td[data-col-key="p' + N_HOT + '"] .bulk-cell').length === N_ROWS,
       'and the column is here a moment later');
    global.window = win; global.document = doc;
  }

  console.log(fails ? ('FAILED ' + fails) : 'pair_virt_server_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
}
main().catch(function (e) { console.error('ERR', e && e.stack || e); process.exit(1); });
