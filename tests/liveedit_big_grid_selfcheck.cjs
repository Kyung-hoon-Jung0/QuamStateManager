// w7 liveedit -- what a WIDE Live-Edit grid costs per interaction, pinned
// by mechanism, and the tail collapse that takes its cold right end out of
// layout.
//
// Measured on the 30Q rig (2,389 pair columns x 69 pairs, 1,264 qubit columns
// x 30 qubits) in real Chrome, before these fixes one Enter cost:
//   * ~0.95 s of querySelector inside the pair grid's header-stats pass (two
//     whole-table attribute scans PER COLUMN),
//   * ~0.6-1.2 s of Style/Layout/PrePaint/Paint/HitTest over ~200k EMPTY cold
//     tds that were still laid out, painted and hit-tested,
//   * tens of ms in applySearch rebuilding every cell's search text for an
//     EMPTY query, an O(columns^2) column lookup, a whole-table class walk and
//     a whole-table selector per group head.
// Each is pinned below by the count of the work it must NOT do, so a
// revert shows up red here rather than as a slow Enter on a big chip.
//
// Drives the REAL grid-virt.js + bulk-edit.js + pair-edit.js under jsdom.
// Run: node tests/liveedit_big_grid_selfcheck.cjs   (driven by tests/test_liveedit_big_grid.py)
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
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const NCOL = 40, HOT = 10;
const ROWS = ['q1', 'q2', 'q3', 'q4', 'q5'];
const key = (i) => 'c' + i;
const sec = (i) => (i < 20 ? 'A' : 'B');
const val = (r, i) => String((ROWS.indexOf(r) + 1) * 100 + i);

function head(i, withStats) {
  return '<th scope="col" class="bulk-col-head ck-' + i + '" data-col-key="' + key(i) + '" data-section="' + sec(i)
    + '" data-maxlen="10"><span class="bulk-col-label">' + key(i) + '</span>'
    + (withStats ? '<span class="bulk-col-stats muted" data-col-stats="' + key(i) + '"></span>' : '')
    + '<span class="bulk-resize-handle" data-col-key="' + key(i) + '"></span></th>';
}
function cell(r, i, cold, ent) {
  const dp = ent + '.' + r + '.' + key(i);
  if (cold) return '<td class="bulk-td ck-' + i + ' bulk-td-cold" data-col-key="' + key(i) + '"></td>';
  return '<td class="bulk-td ck-' + i + '" data-col-key="' + key(i) + '"><input type="text" class="bulk-cell" value="'
    + val(r, i) + '" data-orig="' + val(r, i) + '" data-dot-path="' + dp + '" data-resolved="' + dp + '" size="10"></td>';
}
function applyCol() {
  return '<td class="bulk-apply-col"><button type="button" class="btn-xs bulk-row-apply" disabled>Apply</button>'
    + '<span class="bulk-row-error" hidden></span></td>';
}
function coldMap(ent, from) {
  const cols = {};
  for (let i = from; i < NCOL; i++) {
    cols[key(i)] = ROWS.map(function (r) { const dp = ent + '.' + r + '.' + key(i); return [val(r, i), dp, dp]; });
  }
  return JSON.stringify({ rows: ROWS, cols: cols });
}
// the qubit grid: c0..c9 hot, c10..c39 server-cold (the shape bulk_virt.plan
// renders: a cold SUFFIX), two sections with a group band
function qubitGrid(coldFrom) {
  let h = '', rows = '';
  for (let i = 0; i < NCOL; i++) h += head(i, true);
  ROWS.forEach(function (r) {
    rows += '<tr data-qubit="' + r + '"><th class="bulk-rowhead" data-col-key="__id__">' + r + '</th>';
    for (let i = 0; i < NCOL; i++) rows += cell(r, i, i >= coldFrom, 'qubits');
    rows += applyCol() + '</tr>';
  });
  return '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
    + '<span id="bulk-search-hint"></span></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-table"><thead>'
    + '<tr class="bulk-group-row"><th class="bulk-corner" rowspan="2" data-col-key="__id__"></th>'
    + '<th class="bulk-group-head" data-group="A" colspan="20">A</th>'
    + '<th class="bulk-group-head" data-group="B" colspan="20">B</th><th class="bulk-apply-col" rowspan="2"></th></tr>'
    + '<tr class="bulk-head-row">' + h + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    + (coldFrom < NCOL ? '<script type="application/json" id="bulk-cold-map">' + coldMap('qubits', coldFrom) + '</script>' : '');
}
// the pair grid: every column hot (the stats pass is what it pins)
// (section 7: `coldFrom` gives it a server-cold suffix + a group band, the
// shape the 30Q rig's 2,389-column pair grid has)
function pairGrid(coldFrom) {
  if (coldFrom == null) coldFrom = NCOL;
  let h = '', rows = '';
  for (let i = 0; i < NCOL; i++) h += head(i, true);
  ROWS.forEach(function (r) {
    const id = r + '-x';
    rows += '<tr data-pair="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">' + id + '</th>';
    for (let i = 0; i < NCOL; i++) rows += cell(id, i, i >= coldFrom, 'qubit_pairs').replace(/value="\d+"/, 'value="' + val(r, i) + '"');
    rows += applyCol() + '</tr>';
  });
  const band = coldFrom < NCOL
    ? '<tr class="bulk-group-row"><th class="bulk-corner" rowspan="2" data-col-key="__id__"></th>'
      + '<th class="bulk-group-head" data-group="A" colspan="20">A</th>'
      + '<th class="bulk-group-head" data-group="B" colspan="20">B</th><th class="bulk-apply-col" rowspan="2"></th></tr>'
    : '';
  const pmap = {};
  for (let i = coldFrom; i < NCOL; i++) pmap[key(i)] = ROWS.map(function (r) { const dp = 'qubit_pairs.' + r + '-x.' + key(i); return [val(r, i), dp, dp]; });
  const map = coldFrom < NCOL
    ? '<script type="application/json" id="bulk-pair-cold-map">' + JSON.stringify({ rows: ROWS.map(function (r) { return r + '-x'; }), cols: pmap }) + '</script>'
    : '';
  if (band) {
    return '<div class="bulk-pair-divider" id="bulk-pair-divider">'
      + '<div class="bulk-colvis-menu" id="bulk-pair-colvis-menu"></div>'
      + '<span id="bulk-pair-search-count"></span><span id="bulk-pair-dirty-count"></span>'
      + '<button id="bulk-pair-apply-all" disabled></button><button id="bulk-pair-apply-sync" disabled></button>'
      + '<button id="bulk-pair-reset" disabled></button></div>'
      + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-pair-table"><thead>' + band
      + '<tr class="bulk-head-row">' + h + '</tr></thead><tbody>' + rows + '</tbody></table></div>' + map;
  }
  return '<div class="bulk-pair-divider" id="bulk-pair-divider">'
    + '<div class="bulk-colvis-menu" id="bulk-pair-colvis-menu"></div>'
    + '<span id="bulk-pair-search-count"></span><span id="bulk-pair-dirty-count"></span>'
    + '<button id="bulk-pair-apply-all" disabled></button><button id="bulk-pair-apply-sync" disabled></button>'
    + '<button id="bulk-pair-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-pair-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + h
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

const cols = () => Array.from({ length: NCOL }, function (_, i) {
  return { key: key(i), label: key(i), section: sec(i), unit: '', default_on: true, editable: true, kind: 'scalar', maxlen: 10 };
});

function world(opts) {
  opts = opts || {};
  const dom = new JSDOM('<!DOCTYPE html><html><head></head><body><div id="table-pane"><div class="bulk-panel">'
    + qubitGrid(opts.coldFrom == null ? HOT : opts.coldFrom) + pairGrid(opts.pairColdFrom) + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;                       // docs/125: bridge, or it throws
  win.__chipToken = 'tok';
  win.htmx = { ajax() {}, trigger() {}, process() {} };
  win.showToast = function () {};
  win._diagChanged = function () {};
  win.LiveEditUndo = { record() {}, clear() {} };
  win.__bulkSearchDebounce = 1;
  try { win.localStorage.clear(); } catch (e) {}
  if (opts.hidden) win.localStorage.setItem('quam_bulk_hidden_cols_v2', JSON.stringify(opts.hidden));
  // jsdom lays nothing out (every offsetWidth is 0), which to the scroll pass
  // means "the laid-out table ends before the look-ahead edge" -- it would
  // reveal the whole tail one rAF after mount. A table as wide as a real one
  // keeps the plan observable; section 5 takes the width away on purpose.
  win.__tableW = opts.tableW == null ? 100000 : opts.tableW;
  Object.defineProperty(win.HTMLElement.prototype, 'offsetWidth', { configurable: true,
    get: function () { return this.tagName === 'TABLE' ? win.__tableW : 0; } });
  win.__fetched = [];
  win.fetch = function (url) {
    win.__fetched.push(String(url));
    // a cold fetch that never lands (nothing in sections 1-6 needs it)...
    if (!opts.land) return new Promise(function () {});
    // ...or, for section 7, one that lands with the pair grid's cells
    const keys = decodeURIComponent(/cols=([^&]*)/.exec(url)[1]).split(',');
    const cells = {};
    keys.forEach(function (k) {
      const i = parseInt(k.slice(1), 10);
      cells[k] = {};
      ROWS.forEach(function (r) {
        const dp = 'qubit_pairs.' + r + '-x.' + k;
        cells[k][r + '-x'] = '<input type="text" class="bulk-cell" value="' + val(r, i) + '" data-orig="' + val(r, i)
          + '" data-dot-path="' + dp + '" data-resolved="' + dp + '" size="10">';
        const dq = 'qubits.' + r + '.' + k;       // ...and the qubit grid's (section 8)
        cells[k][r] = '<input type="text" class="bulk-cell" value="' + val(r, i) + '" data-orig="' + val(r, i)
          + '" data-dot-path="' + dq + '" data-resolved="' + dq + '" size="10">';
      });
    });
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, cells: cells, seq: 1 }); } });
  };
  new win.Function(GRID_VIRT_JS).call(win);
  const create = win.GridVirt.create;
  win.__gvs = {};
  win.GridVirt.create = function (o) {
    if (opts.tailMin) o.tailMinCells = opts.tailMin;
    if (opts.tailBlock) o.tailBlock = opts.tailBlock;
    return (win.__gvs[o.tableSel] = create(o));
  };
  new win.Function(BULK_JS).call(win);
  new win.Function(PAIR_JS).call(win);
  win.BulkEdit.mount(cols(), { bands: {} }, [], { chip: 'chipA', qubits: ROWS.map(function (q) { return { id: q, grid: null }; }) });
  win.BulkPairEdit.mount(cols());
  return win;
}

// count every selector query issued on or below one element while fn runs
function countQueries(win, el, fn) {
  const E = win.Element.prototype, D = win.Document.prototype;
  const saved = [E.querySelector, E.querySelectorAll, D.querySelector, D.querySelectorAll];
  let n = 0;
  const wrap = (f) => function () { if (this === el || (el.contains && el.contains(this))) n++; return f.apply(this, arguments); };
  E.querySelector = wrap(saved[0]); E.querySelectorAll = wrap(saved[1]);
  try { fn(); } finally { E.querySelector = saved[0]; E.querySelectorAll = saved[1]; }
  return n;
}
const statOf = (win, t, k) => win.document.querySelector('#' + t + ' [data-col-stats="' + k + '"]').textContent;

(async function main() {
  // ── 1. the header-stats pass is ONE index, not two scans per column ────────
  {
    const win = world({ coldFrom: NCOL });     // qubit grid all hot
    const qt = win.document.getElementById('bulk-table');
    const pt = win.document.getElementById('bulk-pair-table');
    const nq = countQueries(win, qt, function () { win.BulkEdit.recomputeStats(); });
    ok(nq <= 4, 'qubit stats over ' + NCOL + ' columns issue a constant number of table queries (got ' + nq + ', a per-column scan is ' + 2 * NCOL + ')');
    ok(/^min 100 · max 500$/.test(statOf(win, 'bulk-table', 'c0')), 'and the numbers are right (c0: ' + statOf(win, 'bulk-table', 'c0') + ')');
    ok(/^min 139 · max 539$/.test(statOf(win, 'bulk-table', 'c39')), 'for the last column too (c39: ' + statOf(win, 'bulk-table', 'c39') + ')');
    ok(win.document.querySelector('.bulk-cell[data-dot-path="qubits.q5.c3"]').classList.contains('cell-best')
       && win.document.querySelector('.bulk-cell[data-dot-path="qubits.q1.c3"]').classList.contains('cell-worst'),
       'the extreme marks land on the max / min cells');
    const np = countQueries(win, pt, function () { win.BulkPairEdit.recomputeStats(); });
    ok(np <= 4, 'pair stats: constant table queries too (got ' + np + ', per-column is ' + 2 * NCOL + ')');
    ok(/^min 107 · max 507$/.test(statOf(win, 'bulk-pair-table', 'c7')), 'pair numbers right (c7: ' + statOf(win, 'bulk-pair-table', 'c7') + ')');
    // a keyed pass still touches only its column's header
    win.document.querySelector('#bulk-pair-table [data-col-stats="c2"]').textContent = 'stale';
    win.document.querySelector('#bulk-pair-table [data-col-stats="c3"]').textContent = 'keep';
    win.BulkPairEdit.recomputeStats({ c2: 1 });
    ok(statOf(win, 'bulk-pair-table', 'c2') === 'min 102 · max 502' && statOf(win, 'bulk-pair-table', 'c3') === 'keep',
       'a keyed pass recomputes its keys and leaves the rest alone');
  }

  // ── 2. an EMPTY query builds no search text ────────────────────────────────
  {
    const win = world({ tailMin: 100000 });    // server-cold c10.. , no collapse
    const qt = win.document.getElementById('bulk-table');
    await tick(10);
    const sb = win.document.getElementById('bulk-search');
    const E = win.Element.prototype, qsa = E.querySelectorAll;
    let coldScans = 0;
    E.querySelectorAll = function (s) { if (s === 'td.bulk-td-cold' && this.tagName === 'TR') coldScans++; return qsa.apply(this, arguments); };
    sb.value = ''; sb.dispatchEvent(new win.Event('input', { bubbles: true }));
    await tick(30);
    const emptyScans = coldScans;
    sb.value = '339'; sb.dispatchEvent(new win.Event('input', { bubbles: true }));   // q3's c39 (a COLD cell's value)
    await tick(30);
    E.querySelectorAll = qsa;
    ok(emptyScans === 0, 'an empty query reads no cell text (cold-row scans: ' + emptyScans + ')');
    ok(coldScans >= ROWS.length, 'a value query still reads every row, cold cells included (' + coldScans + ')');
    const shown = Array.from(qt.querySelectorAll('tbody tr')).filter(function (tr) { return !tr.hidden && tr.style.display !== 'none' && !tr.classList.contains('bulk-row-search-hidden'); });
    const q3 = qt.querySelector('tr[data-qubit="q3"]');
    const q1 = qt.querySelector('tr[data-qubit="q1"]');
    ok(win.getComputedStyle(q1).display === 'none' || q1.hidden || q1.classList.length > 0,
       'the value query filters rows (q1 does not hold 339)');
    ok(!(q3.hidden) && win.getComputedStyle(q3).display !== 'none', 'q3, which holds it in a COLD cell, stays');
    void shown;
  }

  // ── 3. the tail collapse: plan, rules, margin, reveal ──────────────────────
  {
    const win = world({ tailMin: 100 });       // 30 cold columns x 5 rows = 150 >= 100
    await tick(10);
    const d = win.document, qt = d.getElementById('bulk-table');
    const gv = win.BulkEdit._virtState ? null : null;
    void gv;
    const sty = d.getElementById('bulk-virt-width-style-tail');
    ok(!!sty && sty.textContent.length > 0, 'a wide cold tail gets its collapse sheet');
    const rules = (sty.textContent.match(/\{display:none!important\}/g) || []).length;
    ok(rules === NCOL - HOT, 'one rule per collapsed column (' + rules + ' for ' + (NCOL - HOT) + ') -- a single giant selector list stopped applying in real Chrome');
    ok(sty.textContent.indexOf('#bulk-table th.ck-10,#bulk-table td.ck-10{display:none!important}') >= 0
       && sty.textContent.indexOf('.ck-9{') < 0, 'the run starts at the first cold column and spares the hot ones');
    // (the margin has a sheet of its own: moving it must not invalidate a
    // block of cells -- see TAIL_BLOCK in grid-virt.js)
    const msty = d.getElementById('bulk-virt-width-style-tail-m');
    const mr = /#bulk-table\{margin-right:(\d+)px\}/.exec(msty ? msty.textContent : '');
    const px = win.GridVirt.pxPerChar();
    let want = 0;
    for (let i = HOT; i < NCOL; i++) want += Math.max(10 * px + win.GridVirt.EST_PAD, ('c' + i).length * 7.5 + 30);
    ok(mr && Math.abs(+mr[1] - Math.round(want)) <= 1, 'the margin keeps the scroll range: the estimated width of what it took out (' + (mr && mr[1]) + ' vs ' + Math.round(want) + ')');
    ok(qt.querySelectorAll('th.bulk-virt-collapsed').length === NCOL - HOT, 'the collapsed heads carry the marker');
    const gB = qt.querySelector('.bulk-group-head[data-group="B"]');
    ok(gB.classList.contains('bulk-col-hidden'), 'a group band whose columns are all collapsed spans nothing');
    ok(qt.querySelector('.bulk-group-head[data-group="A"]').colSpan === HOT, 'group A spans only its shown columns (' + qt.querySelector('.bulk-group-head[data-group="A"]').colSpan + ')');
    ok(!win.__fetched.some(function (u) { return /c(1[0-9]|[23][0-9])\b/.test(decodeURIComponent(u)); }),
       'nothing collapsed was fetched at mount (' + win.__fetched.map(decodeURIComponent).join(' | ') + ')');

    // a values-only hydrate (an Enter's twin repaint) never brings columns back
    win.BulkEdit._virtHydrateCols(['c30'], { reveal: false });
    ok(qt.querySelector('th.ck-30').classList.contains('bulk-virt-collapsed'), 'reveal:false leaves the tail as it was');
    // an ordinary ask brings the run back THROUGH that column, from its left end
    win.BulkEdit._virtHydrateCols(['c14']);
    ok(!qt.querySelector('th.ck-10').classList.contains('bulk-virt-collapsed')
       && !qt.querySelector('th.ck-14').classList.contains('bulk-virt-collapsed')
       && qt.querySelector('th.ck-15').classList.contains('bulk-virt-collapsed'),
       'hydrateCols reveals ck-10..ck-14 and nothing past it');
    const sty2 = d.getElementById('bulk-virt-width-style-tail').textContent;
    ok(sty2.indexOf('.ck-14{') < 0 && sty2.indexOf('td.ck-15{') >= 0, 'and its rules go with them');
    ok(qt.querySelector('.bulk-group-head[data-group="A"]').colSpan === 15, 'the group band follows the reveal (A spans 15)');
    // focusing into a collapsed column must give it a box first
    const td25 = qt.querySelector('tr[data-qubit="q2"] td.ck-25');
    win.BulkEdit._virtHydrateCols.call(null, []);
    // keyboard navigation reaches ensureTd through the public hydrateColumn too
    // (not awaited: the stub fetch never lands, and an await on it would
    // end the process with nothing pending -- a silent exit 0)
    win.BulkEdit.hydrateColumn('c25');
    ok(!qt.querySelector('th.ck-25').classList.contains('bulk-virt-collapsed')
       && qt.querySelector('th.ck-26').classList.contains('bulk-virt-collapsed'),
       'asking for one column (Column History, keyboard nav) reveals through it');
    ok(qt.querySelectorAll('.bulk-group-head:not(.bulk-col-hidden)').length === 2
       && qt.querySelector('.bulk-group-head[data-group="B"]').colSpan === 6, 'group B now spans c20..c25 (' + qt.querySelector('.bulk-group-head[data-group="B"]').colSpan + ')');
    void td25;
  }

  // ── 4. under the gate nothing changes ──────────────────────────────────────
  {
    const win = world({ tailMin: 1000 });
    await tick(10);
    const sty = win.document.getElementById('bulk-virt-width-style-tail');
    ok(!sty || sty.textContent === '', 'a tail under the cell gate is not collapsed');
    ok(win.document.querySelectorAll('th.bulk-virt-collapsed').length === 0, 'and no head is marked');
    ok(win.GridVirt.TAIL_MIN_CELLS === 20000, 'the default gate is 20,000 cells (every real chip measured is under it)');
  }

  // ── 5. the scroll pass grows the tail back and never fetches collapsed ─────
  {
    const win = world({ tailMin: 100 });
    await tick(10);
    const qt = win.document.getElementById('bulk-table');
    // jsdom lays nothing out: offsetWidth 0 and clientWidth 0, so the look-ahead
    // edge (1200 * 1.5) is past the table and the pass must bring columns back
    win.__fetched = [];
    win.__tableW = 0;                          // the laid-out part ends here
    const st = win.BulkEdit._virtState && win.BulkEdit._virtState();
    void st;
    const pane = win.document.getElementById('table-pane');
    pane.dispatchEvent(new win.Event('scroll'));
    await tick(40);
    const still = qt.querySelectorAll('th.bulk-virt-collapsed').length;
    ok(still < NCOL - HOT, 'a scroll near the end of the laid-out table reveals more of the tail (' + (NCOL - HOT - still) + ' back)');
    const revealed = [];
    for (let i = HOT; i < NCOL; i++) if (!qt.querySelector('th.ck-' + i).classList.contains('bulk-virt-collapsed')) revealed.push('c' + i);
    const asked = win.__fetched.map(decodeURIComponent).join(',');
    const collapsedAsked = [];
    for (let i = HOT; i < NCOL; i++) {
      if (qt.querySelector('th.ck-' + i).classList.contains('bulk-virt-collapsed') && new RegExp('[=,]c' + i + '(,|&|$)').test(asked)) collapsedAsked.push('c' + i);
    }
    ok(collapsedAsked.length === 0, 'the pass never fetches a column still collapsed (' + collapsedAsked.join(',') + ')');
  }

  // ── 6. column visibility walks only what changed ───────────────────────────
  {
    const win = world({ coldFrom: NCOL, hidden: ['c5'] });
    const qt = win.document.getElementById('bulk-table');
    ok(Array.from(qt.querySelectorAll('[data-col-key="c5"]')).every(function (e) { return e.classList.contains('bulk-col-hidden'); }),
       'a column hidden at mount: its head, handle and every cell carry the class');
    const TL = win.DOMTokenList.prototype, tog = TL.toggle;
    let n = 0;
    TL.toggle = function (c) { if (c === 'bulk-col-hidden') n++; return tog.apply(this, arguments); };
    // the Properties menu's own checkbox (showAllColumns re-GETs the pane)
    const cb = win.document.querySelector('#bulk-colvis-menu [data-col-toggle="c5"]');
    cb.checked = true;
    cb.dispatchEvent(new win.Event('change', { bubbles: true }));
    TL.toggle = tog;
    ok(Array.from(qt.querySelectorAll('[data-col-key="c5"]')).every(function (e) { return !e.classList.contains('bulk-col-hidden'); }),
       'showing it again clears every keyed element of that column');
    const perCol = 2 + ROWS.length;               // head + handle + one td per row
    ok(n <= perCol + 2, 'and only that column is walked (' + n + ' bulk-col-hidden writes; the whole table is ' + (NCOL * perCol) + ')');
  }

  // ── 7. the pair grid: its band, and a filled-but-collapsed column ──────────
  {
    const win = world({ tailMin: 100, coldFrom: NCOL, pairColdFrom: HOT, land: true });
    await tick(10);
    const pt = win.document.getElementById('bulk-pair-table');
    ok(pt.querySelectorAll('th.bulk-virt-collapsed').length === NCOL - HOT, 'the pair grid collapses its cold tail too');
    ok(pt.querySelector('.bulk-group-head[data-group="B"]').classList.contains('bulk-col-hidden')
       && pt.querySelector('.bulk-group-head[data-group="A"]').colSpan === HOT,
       'the pair band counts only what shows (A spans ' + pt.querySelector('.bulk-group-head[data-group="A"]').colSpan + ')');
    const gv = win.__gvs['#bulk-pair-table'];
    // an Enter's twin repaint fills c30 without bringing it back...
    gv.hydrateCols(['c30'], { reveal: false });
    await tick(20);
    ok(!gv.isCold('c30') && gv.isCollapsed('c30'), 'a values-only fill leaves c30 filled but collapsed');
    // ...so a caret asked into it (keyboard nav, a pinned column) must put it
    // back itself: it is no longer cold, so no fetch path would
    gv.ensureTd(pt.querySelector('tr[data-pair="q2-x"] td.ck-30'));
    ok(!gv.isCollapsed('c30') && gv.isCollapsed('c31'), 'ensureTd on a filled-but-collapsed column reveals through it, and no further');
  }

  // ── 8. scrolling back left takes the far end out of layout again ───────────
  for (const G of [{ id: 'bulk-pair-table', pre: 'bulk-pair', attr: 'data-pair', sfx: '-x', o: { coldFrom: NCOL, pairColdFrom: HOT } },
                   { id: 'bulk-table', pre: 'bulk', attr: 'data-qubit', sfx: '', o: { coldFrom: HOT } }]) {
    const win = world(Object.assign({ tailMin: 100, land: true }, G.o));
    await tick(10);
    const d = win.document, pt = d.getElementById(G.id), pane = d.getElementById('table-pane');
    // geometry: column N starts at N*200px
    Object.defineProperty(win.HTMLElement.prototype, 'offsetLeft', { configurable: true,
      get: function () { const m = /\bck-(\d+)\b/.exec(this.className || ''); return m ? +m[1] * 200 : 0; } });
    // the whole run on screen (a far JUMP now reveals only where it lands --
    // section 9 -- so the state this section starts from is set directly);
    // at 6000px nothing is within two viewports of leaving, and only the
    // window around it (c22..c38) is fetched
    const gv8 = win.__gvs['#' + G.id];
    win.__tableW = 0;
    gv8.revealAll();
    pane.scrollLeft = 6000;
    for (let i = 0; i < 2; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    ok(pt.querySelectorAll('th.bulk-virt-collapsed').length === 0, 'the whole run shows, and nothing near the window leaves layout');
    // back at scrollLeft 0: everything past 1200 * (1.5 + 2) = 4200px may leave again
    pane.scrollLeft = 0;
    win.__tableW = 1e6;
    const c30 = pt.querySelector('tr[' + G.attr + '="q2' + G.sfx + '"] td.ck-30 .bulk-cell');
    ok(!!c30, 'c30 landed (a hydrated column: its cells are inputs)');
    c30.value = '999';                         // an unapplied edit in c30
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    ok(pt.querySelector('th.ck-31').classList.contains('bulk-virt-collapsed')
       && !pt.querySelector('th.ck-30').classList.contains('bulk-virt-collapsed'),
       G.pre + ': ' + 'the far end leaves layout again, and stops at the column holding an edit');
    const sty = d.getElementById(G.pre + '-virt-width-style-tail').textContent;
    ok(sty.indexOf('td.ck-31{display:none') >= 0 && sty.indexOf('td.ck-30{') < 0, 'its rules are back');
    c30.value = c30.getAttribute('data-orig');  // the edit is undone
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    ok(pt.querySelector('th.ck-22').classList.contains('bulk-virt-collapsed')
       && !pt.querySelector('th.ck-21').classList.contains('bulk-virt-collapsed'),
       G.pre + ': ' + 'clean again, it goes back to two viewports past the window (c22), no further');
    ok(pt.querySelector('.bulk-group-head[data-group="B"]').colSpan === 2,
       G.pre + ': ' + 'the group band follows (B spans c20..c21: ' + pt.querySelector('.bulk-group-head[data-group="B"]').colSpan + ')');
    // the focus pins its column the same way
    win.__tableW = 0; gv8.revealAll(); pane.scrollLeft = 6000;
    for (let i = 0; i < 2; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    win.__tableW = 1e6; pane.scrollLeft = 0;
    pt.querySelector('tr[' + G.attr + '="q1' + G.sfx + '"] td.ck-35 .bulk-cell').focus();
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    ok(!pt.querySelector('th.ck-35').classList.contains('bulk-virt-collapsed')
       && pt.querySelector('th.ck-36').classList.contains('bulk-virt-collapsed'),
       G.pre + ': ' + 'a focused column is never taken out of layout');
  }

  // ── 9. a far JUMP reveals only where it lands (w7 final QA) ────────────────
  // The 30Q rig's jump to the far right of its 2,389-column pair grid was one
  // 3.0-3.2 s task: the scroll pass revealed the WHOLE cold tail up to the
  // landing (a full style + layout of ~200k cells). Revealing it in rAF slices
  // does not help -- each slice re-lays out the whole grown table (measured:
  // 73 -> 294 ms per 50-column slice, 13 s in all). Now the pass reveals the
  // columns around the landing, the run left of them stays out of layout with
  // one of its columns kept as a blank spacer holding its width, the rules are
  // rewritten one small block at a time, and a landing at the END stays at the
  // end when the real widths beat the estimates.
  // A coherent layout model: a shown column is its estimate + 10 px wide while
  // cold and + 30 once its cells are in (so both the reveal AND the landing
  // cells beat the estimates), a display:none rule takes a column out, a
  // spacer is its min-width, the pane is 300 px wide and scrolls over the
  // table + its margin. Run twice: with the cells landing, and with a fetch
  // that never lands (then only the pass itself can keep the end).
  for (const LAND of [true, false]) {
    const ok9 = (c, m) => ok(c, (LAND ? '' : '[no cells land] ') + m);
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: NCOL, pairColdFrom: HOT, land: LAND });
    await tick(10);
    const d = win.document, pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const gv = win.__gvs['#bulk-pair-table'];
    const px = win.GridVirt.pxPerChar();
    const est = (i) => Math.max(10 * px + win.GridVirt.EST_PAD, ('c' + i).length * 7.5 + 30);
    const tailSheets = () => Array.from(d.querySelectorAll('style')).filter((s) => /^bulk-pair-virt-width-style-tail(-\d+)?$/.test(s.id));
    const tailText = () => tailSheets().map((s) => s.textContent).join('\n');
    const marginOf = () => { const m = d.getElementById('bulk-pair-virt-width-style-tail-m'); const r = m && /margin-right:(\d+)px/.exec(m.textContent); return r ? +r[1] : 0; };
    function geo() {                                // column i -> {x, w} in the pair table
      const txt = tailText(), out = [];
      let x = 0;
      for (let i = 0; i < NCOL; i++) {
        const none = txt.indexOf('th.ck-' + i + ',') >= 0 && new RegExp('td\\.ck-' + i + '\\{display:none').test(txt);
        const mw = new RegExp('th\\.ck-' + i + '\\{min-width:(\\d+)px').exec(txt);
        const td = pt.querySelector('tbody td.ck-' + i);
        const w = none ? 0 : (mw ? +mw[1] : est(i) + (td && !td.classList.contains('bulk-td-cold') ? 30 : 10));
        out.push({ x: none ? 0 : x, w: w });
        x += w;
      }
      out.total = x;
      return out;
    }
    const colOf = (el) => { const m = /\bck-(\d+)\b/.exec(el.className || ''); return m ? +m[1] : -1; };
    Object.defineProperty(win.HTMLElement.prototype, 'offsetLeft', { configurable: true, get: function () {
      const i = colOf(this); if (i < 0 || !pt.contains(this)) return 0; return geo()[i].x; } });
    Object.defineProperty(win.HTMLElement.prototype, 'offsetWidth', { configurable: true, get: function () {
      if (this === pt) return geo().total;
      if (this.tagName === 'TABLE') return 0;
      const i = colOf(this); if (i < 0 || !pt.contains(this)) return 0; return geo()[i].w; } });
    Object.defineProperty(pane, 'clientWidth', { configurable: true, get: () => 300 });
    Object.defineProperty(pane, 'scrollWidth', { configurable: true, get: () => geo().total + marginOf() });
    const collapsed = () => { const o = []; for (let i = HOT; i < NCOL; i++) if (gv.isCollapsed('c' + i)) o.push(i); return o; };
    ok9(collapsed().length === NCOL - HOT, 'model: the whole cold tail starts out of layout');
    // count what each write reassigns
    const TC = Object.getOwnPropertyDescriptor(win.Node.prototype, 'textContent');
    let rewritten = [];
    Object.defineProperty(win.Node.prototype, 'textContent', { configurable: true, get: TC.get, set: function (v) {
      if (this.tagName === 'STYLE' && /^bulk-pair-virt-width-style-tail(-\d+)?$/.test(this.id)) rewritten.push((String(v).match(/\{display:none!important\}/g) || []).length);
      return TC.set.call(this, v); } });
    win.__fetched = [];
    // the jump: the scrollbar dragged to the end
    pane.scrollLeft = pane.scrollWidth - pane.clientWidth;
    for (let i = 0; i < 4; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    const col1 = collapsed();
    const shown1 = []; for (let i = HOT; i < NCOL; i++) if (col1.indexOf(i) < 0) shown1.push(i);
    ok9(shown1.length > 0 && shown1[shown1.length - 1] === NCOL - 1 && shown1.every((c, k) => k === 0 || c === shown1[k - 1] + 1),
       'the jump reveals one run of columns ending at the last one (c' + shown1[0] + '..c' + shown1[shown1.length - 1] + ')');
    // the window is (1 + 2 * 1.5) viewports + one of slack at most, in estimated px
    ok9(shown1.length <= Math.ceil((300 * 5) / est(20)) + 1 && col1.length >= 10,
       'and ONLY around the landing: ' + shown1.length + ' shown, ' + col1.length + ' of ' + (NCOL - HOT) + ' still out of layout');
    const sp = shown1[0] - 1;                       // the middle run's last column
    const txt1 = tailText();
    const mw1 = new RegExp('th\\.ck-' + sp + '\\{min-width:(\\d+)px').exec(txt1);
    let wantMid = 0; for (let i = HOT; i <= sp; i++) wantMid += est(i);
    ok9(!!mw1 && Math.abs(+mw1[1] - Math.round(wantMid)) <= 1 && !new RegExp('td\\.ck-' + sp + '\\{display:none').test(txt1),
       'the column left of it is a blank spacer holding the middle run\'s estimated width (' + (mw1 && mw1[1]) + ' vs ' + Math.round(wantMid) + ')');
    ok9(new RegExp('td\\.ck-' + sp + '>\\*\\{visibility:hidden').test(txt1) && !pt.querySelector('th.ck-' + sp).classList.contains('bulk-virt-collapsed'),
       'the spacer shows no content, and the group band spans it (no collapsed marker)');
    ok9(marginOf() === 0, 'nothing is left at the right end, so the margin is 0');
    const g1 = geo();
    ok9(Math.abs(pane.scrollLeft - (pane.scrollWidth - pane.clientWidth)) <= 1
       && g1[NCOL - 1].x + g1[NCOL - 1].w <= pane.scrollLeft + 300 + 1 && g1[NCOL - 1].x >= pane.scrollLeft,
       'it LANDS at the end: the real widths beat the estimates and the last column is still in view (' + Math.round(pane.scrollLeft) + ' of ' + (pane.scrollWidth - 300) + ')');
    const total = NCOL - HOT;
    ok9(rewritten.length > 0 && rewritten.length < Math.ceil(total / 4) && Math.max.apply(null, rewritten) <= 4,
       'every write reassigned only the small blocks it changed (' + rewritten.length + ' sheet writes of ' + Math.ceil(total / 4) + ' blocks, at most ' + Math.max.apply(null, rewritten) + ' rules each; one sheet would carry all the collapsed columns)');
    const asked = win.__fetched.map(decodeURIComponent).join(',');
    const askedMid = col1.filter((i) => new RegExp('[=,]c' + i + '(,|&|$)').test(asked));
    const askedWin = shown1.filter((i) => new RegExp('[=,]c' + i + '(,|&|$)').test(asked));
    ok9(askedWin.length > 0 && askedMid.length === 0,
        'the columns of the landing were fetched (c' + askedWin.join(',c') + '), nothing in the middle run was (' + askedMid.join(',') + ')');
    // three viewports back left: the middle run gives up its RIGHT end, no more
    rewritten = [];
    pane.scrollLeft -= 900;
    for (let i = 0; i < 3; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    const col2 = collapsed();
    const back = col1.length - col2.length;
    ok9(back > 0 && back <= Math.ceil(300 * 4 / est(20)) + 1 && col2[col2.length - 1] === sp - back,
       'scrolling back left brings the middle run back from its right end only (' + back + ' columns, it now ends at c' + col2[col2.length - 1] + ')');
    // back to 0: the far end folds away again, spacer and all
    pane.scrollLeft = 0;
    for (let i = 0; i < 3; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    const txt3 = tailText();
    ok9(collapsed().length === total && txt3.indexOf('min-width') < 0 && marginOf() > 0,
       'back at 0 the whole tail is out of layout again, one suffix, no spacer (margin ' + marginOf() + ')');
    // asking for one column deep inside a run reveals that column, not the run
    gv.ensureTd(pt.querySelector('tr[data-pair="q2-x"] td.ck-30'));
    const txt4 = tailText();
    ok9(!gv.isCollapsed('c30') && gv.isCollapsed('c29') && gv.isCollapsed('c31')
       && new RegExp('th\\.ck-29\\{min-width:').test(txt4),
       'a caret asked deep into the tail brings back that column alone, a spacer holding the run left of it');
    Object.defineProperty(win.Node.prototype, 'textContent', TC);
  }

  console.log(fails ? ('FAILED ' + fails + ' of ' + asserts) : ('all checks passed (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
