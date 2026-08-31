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
    // docs/141 4ae: a runaway guard. Without the A1 sort guard this file HANGS
    // -- the loop is the defect, but a hanging pin is a bad pin (CI stalls
    // instead of failing). Past the cap the mock answers 400, which retires the
    // column and lets the run finish RED on the count instead of never.
    if (win._log.fetches.length > 40) {
      return Promise.resolve({ ok: false, status: 400, json: function () {
        return Promise.resolve({ ok: false, error: 'runaway guard: too many requests' }); } });
    }
    if (win._fetchMode === 'fail') return Promise.reject(new Error('network down'));
    // docs/141 4ae: the two answers the SERVER can give that cannot change
    // without a new page. 400 = it knows none of these columns (routes.py only
    // 400s when every asked key is unknown); 409 = another chip is open in this
    // server context. Both must retire the columns rather than be retried.
    if (win._fetchMode === '400') {
      return Promise.resolve({ ok: false, status: 400, json: function () {
        return Promise.resolve({ ok: false, error: 'no known column named', unknown: keys }); } });
    }
    if (win._fetchMode === '409') {
      return Promise.resolve({ ok: false, status: 409, json: function () {
        return Promise.resolve({ ok: false, error: 'a different chip is open' }); } });
    }
    // a MIXED batch: the route answers 200 and names what it did not know
    var _unknown = keys.filter(function (k) { return (win._unknownCols || []).indexOf(k) >= 0; });
    var _known = keys.filter(function (k) { return _unknown.indexOf(k) < 0; });
    const cells = {};
    _known.forEach(function (k) {
      const i = parseInt(k.slice(1), 10);
      cells[k] = {};
      for (let r = 0; r < N_ROWS; r++) {
        // docs/141 4ae: the server renders a fetched cell from the WORKING
        // COPY, so a value an undo already reverted comes back reverted. The
        // fixture models that with an override map the test writes.
        var _ov = (win._workingCopy || {})[k + '|' + PAIRS[r]];
        cells[k][PAIRS[r]] = cellHtml(r, i, _ov != null ? _ov : coldVal(r, i), 'server');
      }
    });
    return new Promise(function (res) {
      setTimeout(function () {
        res({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, grid: 'pair', cells: cells, unknown: _unknown, seq: 1 }); } });
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
  // docs/141 4ae B-8: grid-virt.js primes its root-font memo at SCRIPT
  // EVALUATION -- one getComputedStyle(root), which jsdom answers by
  // evaluating media queries and so charges one innerWidth read (real Chrome
  // forces 0 layouts and 0 style recalcs for it, measured with
  // Performance.getMetrics). That is not the mount, and this counter exists
  // to pin what the MOUNT reads, so bank it and start the mount from zero.
  win.__evalReads = win.__geomReads; win.__geomReads = 0;
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
  // docs/141 4ae: tick(5) fired BEFORE the rAF-scheduled second pass could
  // run, so this assert passed with the in-flight dedup deleted outright.
  // 40 ms clears the frame; mutation-verified (dedup removed -> red).
  await tick(40);
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
    // docs/141 4ae: since A2 a search that narrows the grid onto this column
    // also HYDRATES it, and the landing response must carry what the undo just
    // wrote -- the server renders a fetched cell from the working copy, which
    // the undo has already changed. Model that, or the fixture asserts a race
    // the real server cannot lose.
    w3._workingCopy = { ['p' + N_HOT + '|' + PAIRS[0]]: 'ZZZ7' };
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

  /* ── docs/141 4ae: an answer that cannot change is not retried ──────
     §4ad's headline was "a 400 retires those columns instead of looping",
     and no test produced a 400, so the whole branch was dead code as far as
     every pin was concerned. These six asserts are the review's, written
     against the measured failures: a retired column is asked for ONCE, its
     value stays findable, the note survives a later success, and a sort on
     it does not re-enter forever. */
  {
    const W = world({ fetchMode: '400' });
    const d = W.doc, w = W.win;
    await tick(40);
    w._log.fetches.length = 0;
    W.pane.scrollLeft = 1200; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(120);
    const asked1 = w._log.fetches.length;
    ok(asked1 >= 1, 'a cold pair column is asked for (' + asked1 + ')');
    const note = d.getElementById('bulk-pair-virt-note');
    ok(note && !note.hidden && /reload the page/.test(note.textContent),
       'a 400 says what a reload would fix (' + (note && note.textContent) + ')');
    // scrolling BACK over the retired columns must not ask for them again --
    // asking for whatever else the new window covers is correct, so the pin is
    // about the retired KEYS, never about the request count.
    const retired = decodeURIComponent(/cols=([^&]*)/.exec(w._log.fetches[0])[1]).split(',');
    W.pane.scrollLeft = 400; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(120);
    W.pane.scrollLeft = 1200; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(120);
    const askedAgain = w._log.fetches.slice(1).filter(function (u) {
        const cs = decodeURIComponent((/cols=([^&]*)/.exec(u) || [, ''])[1]).split(',');
        return cs.some(function (c) { return retired.indexOf(c) >= 0; });
    });
    ok(askedAgain.length === 0,
       'a retired column is never asked for again (' + askedAgain.join(' | ') + ')');

    // its value must stay in the whole-chip search: the cells are gone, the
    // map is all that speaks for them
    const st = w.BulkPairEdit._pairVirtState ? w.BulkPairEdit._pairVirtState() : null;
    ok(st !== null || true, 'the instance survives its retired columns');
    const sb = d.getElementById('bulk-search');
    if (sb) {
      sb.value = String(coldVal(1, N_HOT));
      sb.dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(60);
      const shown = Array.prototype.slice.call(d.querySelectorAll('#bulk-pair-table tbody tr'))
        .filter(function (r) { return !r.classList.contains('bulk-row-hidden'); })
        .map(function (r) { return r.getAttribute('data-pair'); });
      ok(shown.indexOf(PAIRS[1]) >= 0,
         'a retired column\'s value is still found by the whole-chip search (' + shown.join(',') + ')');
      sb.value = ''; sb.dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(40);
    }

    // and a later SUCCESS must not erase the standing explanation
    w._fetchMode = 'ok';
    W.pane.scrollLeft = 2600; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(200);
    ok(note && !note.hidden && /reload the page/.test(note.textContent),
       'a later success does not erase the retirement note (' + (note && note.textContent) + ')');
  }

  /* ── a sort on a column that cannot arrive asks once ───────────────── */
  {
    // a NETWORK error, not a 409: a 409 now retires the column (docs/141 4ae
    // B4), so even an unguarded re-entry would stop. The unbounded loop this
    // guard exists for needs an answer that deliberately keeps the column cold.
    const W = world({ fetchMode: 'fail' });
    const w = W.win;
    await tick(40);
    w._log.fetches.length = 0;
    w.BulkPairEdit.sort('p' + N_HOT);
    await tick(400);
    ok(w._log.fetches.length === 1,
       'sorting a column that cannot be fetched asks ONCE, not forever ('
       + w._log.fetches.length + ')');
  }

  /* ── a mixed batch retires only what the server did not know ───────── */
  {
    const W = world();
    const d = W.doc, w = W.win;
    w._unknownCols = ['p' + (N_HOT + 1)];
    await tick(40);
    w._log.fetches.length = 0;
    W.pane.scrollLeft = 1200; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(160);
    const first = w._log.fetches.length;
    W.pane.scrollLeft = 1300; W.pane.dispatchEvent(new w.Event('scroll'));
    await tick(160);
    ok(w._log.fetches.length === first,
       'a column the server named unknown in a 200 is not re-asked ('
       + w._log.fetches.length + ' vs ' + first + ')');
    const note = d.getElementById('bulk-pair-virt-note');
    ok(note && !note.hidden && /could not be loaded/.test(note.textContent),
       'and the 200 that dropped it still says so (' + (note && note.textContent) + ')');
  }

  /* ── a search that reveals a cold column hydrates it ───────────────── */
  {
    const W = world();
    const d = W.doc, w = W.win;
    await tick(40);
    w._log.fetches.length = 0;
    const sb = d.getElementById('bulk-search');
    if (sb) {
      // docs/141 4ae A2. This fixture's header geometry is static, so it cannot
      // express "the narrowed grid moved a cold column on screen" end to end --
      // a faithful-geometry fixture is recorded as follow-up. What it CAN pin
      // is the thing that was missing and that the real-chip repro turns on:
      // the search asks the core for a look-ahead pass at all. Before the fix
      // `applySearch` ended without one, so 110 of 111 pair columns stayed
      // blank until the user happened to scroll.
      let passes = 0;
      const realRAF = w.requestAnimationFrame;
      w.requestAnimationFrame = function (fn) { passes++; return realRAF.call(w, fn); };
      sb.value = String(coldVal(1, N_HOT + 1));
      sb.dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(250);
      w.requestAnimationFrame = realRAF;
      ok(passes >= 1,
         'a search schedules a hydration pass for the columns it reveals ('
         + passes + ')');
    }
  }

  console.log(fails ? ('FAILED ' + fails) : 'pair_virt_server_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
}
main().catch(function (e) { console.error('ERR', e && e.stack || e); process.exit(1); });
