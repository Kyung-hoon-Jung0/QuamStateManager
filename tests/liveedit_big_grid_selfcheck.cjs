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
require('./_sm_root_boot.cjs').install();

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
    ok(sty.textContent.indexOf('#bulk-table th.ck-10:not(.bulk-virt-on),#bulk-table td.ck-10:not(.bulk-virt-on){display:none!important}') >= 0
       && sty.textContent.indexOf('.ck-9:') < 0, 'the run starts at the first cold column and spares the hot ones');
    // w8: the rule holds only while the column lacks the on-class -- the
    // CSS itself, evaluated (jsdom cascades the sheets for `display`)
    const td10 = qt.querySelector('tr[data-qubit="q2"] td.ck-10'), td9 = qt.querySelector('tr[data-qubit="q2"] td.ck-9');
    ok(win.getComputedStyle(td10).display === 'none' && win.getComputedStyle(td9).display !== 'none',
       'a collapsed column computes display:none, a hot one does not');
    // (the margin is an inline style on the table: moving it must not change
    // a sheet, which walks every element of the document -- see TAIL_ON)
    const mr = /^(\d+)px$/.exec(qt.style.marginRight || '');
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
    const on = (i) => Array.from(qt.querySelectorAll('th.ck-' + i + ', td.ck-' + i));
    ok(sty2 === sty.textContent && on(14).every((e) => e.classList.contains('bulk-virt-on'))
       && on(15).every((e) => !e.classList.contains('bulk-virt-on'))
       && win.getComputedStyle(qt.querySelector('tr[data-qubit="q2"] td.ck-14')).display !== 'none'
       && win.getComputedStyle(qt.querySelector('tr[data-qubit="q2"] td.ck-15')).display === 'none',
       'the reveal is the on-class on the own th + tds (the rules are not rewritten), and it lifts the rule');
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
    // at 5400px nothing is far enough out to leave, and only the
    // window around it (c18..c36) is fetched
    const gv8 = win.__gvs['#' + G.id];
    win.__tableW = 0;
    gv8.revealAll();
    pane.scrollLeft = 5400;
    for (let i = 0; i < 2; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    ok(pt.querySelectorAll('th.bulk-virt-collapsed').length === 0, 'the whole run shows, and nothing near the window leaves layout');
    // back at scrollLeft 0: everything past 1200 * (1.5 + TAIL_KEEP) px may leave again
    pane.scrollLeft = 0;
    win.__tableW = 1e6;
    const c30 = pt.querySelector('tr[' + G.attr + '="q2' + G.sfx + '"] td.ck-30 .bulk-cell');
    ok(!!c30, 'c30 landed (a hydrated column: its cells are inputs)');
    c30.value = '999';                         // an unapplied edit in c30
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    ok(pt.querySelector('th.ck-31').classList.contains('bulk-virt-collapsed')
       && !pt.querySelector('th.ck-30').classList.contains('bulk-virt-collapsed')
       && pt.querySelector('th.ck-28').classList.contains('bulk-virt-collapsed')
       && pt.querySelector('th.ck-29').classList.contains('bulk-virt-spacer'),
       G.pre + ': ' + 'the far end leaves layout again -- all but the column holding an edit, which no longer pins everything left of it (w8: c22..c29 go too, c29 the spacer holding them)');
    const cs = (i) => win.getComputedStyle(pt.querySelector('tr[' + G.attr + '="q2' + G.sfx + '"] td.ck-' + i)).display;
    ok(cs(31) === 'none' && cs(30) !== 'none' && cs(28) === 'none' && cs(29) !== 'none',
       'the rule applies again where the on-class went (' + cs(31) + '/' + cs(30) + '/' + cs(28) + ', spacer ' + cs(29) + ')');
    c30.value = c30.getAttribute('data-orig');  // the edit is undone
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    // the first column whose box starts past the kept window: 1200 * (1.5 + TAIL_KEEP)
    const K = Math.floor(1200 * (win.GridVirt.BUFFER + win.GridVirt.TAIL_KEEP) / 200) + 1;
    ok(K > HOT + 1 && K < 20 && pt.querySelector('th.ck-' + K).classList.contains('bulk-virt-collapsed')
       && !pt.querySelector('th.ck-' + (K - 1)).classList.contains('bulk-virt-collapsed')
       && pt.querySelector('th.ck-30').classList.contains('bulk-virt-collapsed') && !pt.querySelector('.bulk-virt-spacer'),
       G.pre + ': ' + 'clean again, everything past the kept window goes -- c30 too, one suffix, no spacer (from c' + K + '), no further');
    const gA = pt.querySelector('.bulk-group-head[data-group="A"]'), gB = pt.querySelector('.bulk-group-head[data-group="B"]');
    ok(gA.colSpan === K && gB.classList.contains('bulk-col-hidden'),
       G.pre + ': ' + 'the group band follows (A spans c0..c' + (K - 1) + ': ' + gA.colSpan + '; B spans nothing)');
    // the focus pins its column the same way
    win.__tableW = 0; gv8.revealAll(); pane.scrollLeft = 5400;
    for (let i = 0; i < 2; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    win.__tableW = 1e6; pane.scrollLeft = 0;
    pt.querySelector('tr[' + G.attr + '="q1' + G.sfx + '"] td.ck-35 .bulk-cell').focus();
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    ok(!pt.querySelector('th.ck-35').classList.contains('bulk-virt-collapsed')
       && pt.querySelector('th.ck-36').classList.contains('bulk-virt-collapsed'),
       G.pre + ': ' + 'a focused column is never taken out of layout');
    // w8: a PINNED column (it sticks to the pane's edge, so it must keep its
    // box) and a column holding a live multi-cell SELECTION (Ctrl+D / paste
    // act on it) are held the same way
    win.document.activeElement.blur();
    win.__tableW = 0; gv8.revealAll(); pane.scrollLeft = 5400;
    for (let i = 0; i < 2; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    win.__tableW = 1e6; pane.scrollLeft = 0;
    pt.querySelectorAll('th.ck-33, td.ck-33').forEach(function (e) { e.classList.add('bulk-col-pinned'); });
    pt.querySelector('tr[' + G.attr + '="q2' + G.sfx + '"] td.ck-37').classList.add('bulk-sel');
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    const coll = (i) => pt.querySelector('th.ck-' + i).classList.contains('bulk-virt-collapsed');
    ok(!coll(33) && !coll(37) && coll(34) && coll(38) && coll(35),
       G.pre + ': ' + 'a pinned column and a selected one stay in layout, their neighbours go (33/37 held: ' + !coll(33) + '/' + !coll(37) + ')');
  }

  // ── 9. a far JUMP reveals only where it lands (w7 final QA) ────────────────
  // The 30Q rig's jump to the far right of its 2,389-column pair grid was one
  // 3.0-3.2 s task: the scroll pass revealed the WHOLE cold tail up to the
  // landing (a full style + layout of ~200k cells). Revealing it in rAF slices
  // does not help -- each slice re-lays out the whole grown table (measured:
  // 73 -> 294 ms per 50-column slice, 13 s in all). Now the pass reveals the
  // columns around the landing, the run left of them stays out of layout with
  // one of its columns kept as a blank spacer holding its width, and a landing
  // at the END stays at the end when the real widths beat the estimates.
  // w8: the rule blocks are written once at plan time and never again -- a
  // reveal, a collapse or a spacer is a class (+ an inline min-width) on the
  // column's own th + tds, because ANY stylesheet change makes Blink walk the
  // whole document (19-50 ms on the rig, per change).
  // A coherent layout model: a shown column is its estimate + 10 px wide while
  // cold and + 30 once its cells are in (so both the reveal AND the landing
  // cells beat the estimates), a tail column without the on-class is out of
  // layout (the CSS rule itself is evaluated in section 3), a column's box is
  // at least its inline min-width, the pane scrolls (clamped) over the table +
  // its inline margin. Run twice: with the cells landing, and with a fetch
  // that never lands (then only the pass itself can keep the end).
  function layoutModel(win, tables, pane, paneW, vrect) {
    const px = win.GridVirt.pxPerChar();
    const est = (i) => Math.max(10 * px + win.GridVirt.EST_PAD, ('c' + i).length * 7.5 + 30);
    const colOf = (el) => { const m = /\bck-(\d+)\b/.exec(el.className || ''); return m ? +m[1] : -1; };
    const models = (Array.isArray(tables) ? tables : [tables]).map(function (pt) {
      const ths = [], tds = [];
      for (let i = 0; i < NCOL; i++) { ths.push(pt.querySelector('th.ck-' + i)); tds.push(pt.querySelector('tbody td.ck-' + i)); }
      let memoKey = null, memo = null;
      function geo() {
        let key = '';
        for (let i = 0; i < NCOL; i++) key += ths[i].className + '|' + ths[i].style.cssText + '|' + tds[i].className + ';';
        if (key === memoKey) return memo;
        const out = []; let x = 0;
        for (let i = 0; i < NCOL; i++) {
          // a column is laid out when it is not in the tail's rules, or carries the on-class
          const tail = /\bbulk-virt-(on|collapsed|spacer)\b/.test(ths[i].className) || i >= HOT;
          const none = tail && !ths[i].classList.contains('bulk-virt-on') && i >= HOT;
          const mw = parseFloat(ths[i].style.getPropertyValue('min-width')) || 0;
          const nat = est(i) + (tds[i].classList.contains('bulk-td-cold') ? 10 : 30);
          const w = none ? 0 : Math.max(mw, nat);
          out.push({ x: none ? 0 : x, w: w });
          x += w;
        }
        out.total = x;
        memoKey = key; memo = out;
        return out;
      }
      return { t: pt, ths: ths, tds: tds, geo: geo, margin: () => parseFloat(pt.style.marginRight) || 0 };
    });
    const modelOf = (el) => { for (const m of models) if (m.t === el || m.t.contains(el)) return m; return null; };
    Object.defineProperty(win.HTMLElement.prototype, 'offsetLeft', { configurable: true, get: function () {
      const m = modelOf(this), i = colOf(this); if (!m || i < 0 || this === m.t) return 0; return m.geo()[i].x; } });
    Object.defineProperty(win.HTMLElement.prototype, 'offsetWidth', { configurable: true, get: function () {
      const m = modelOf(this); if (!m) return 0;
      if (this === m.t) return m.geo().total;
      const i = colOf(this); if (i < 0) return 0; return m.geo()[i].w; } });
    const span = () => Math.max.apply(null, models.map((m) => m.geo().total + m.margin()));
    let SL = 0;
    Object.defineProperty(pane, 'clientWidth', { configurable: true, get: () => paneW });
    Object.defineProperty(pane, 'scrollWidth', { configurable: true, get: () => span() });
    Object.defineProperty(pane, 'scrollLeft', { configurable: true, get: () => SL,
      set: (v) => { SL = Math.max(0, Math.min(+v || 0, span() - paneW)); } });
    win.Element.prototype.getBoundingClientRect = function () {
      if (this === pane) return { left: 0, right: paneW, width: paneW, top: 0, bottom: 800, height: 800, x: 0, y: 0 };
      if (this.tagName === 'TR') {
        // a row is as tall as its tallest laid-out cell (c12 is a two-line
        // column: 50 px, every other 30), and at least its inline height
        const mr = modelOf(this); let hgt = 0;
        if (mr) { const g = mr.geo(); for (let i = 0; i < NCOL; i++) if (g[i].w > 0) hgt = Math.max(hgt, i === 12 ? 50 : 30); }
        hgt = Math.max(hgt, parseFloat(this.style.height) || 0);
        return { left: 0, right: 0, width: 0, top: 0, bottom: hgt, height: hgt, x: 0, y: 0 };
      }
      const m = modelOf(this), i = colOf(this);
      if (m && this === m.t) {
        // where the table sits vertically in the pane (default: filling it)
        const vr = (vrect && vrect[m.t.id]) || { top: 0, bottom: 800 };
        return { left: -SL, right: m.geo().total - SL, width: m.geo().total, top: vr.top, bottom: vr.bottom, height: vr.bottom - vr.top, x: -SL, y: vr.top };
      }
      if (!m || i < 0) return { left: 0, right: 0, width: 0, top: 0, bottom: 0, height: 0, x: 0, y: 0 };
      const g = m.geo()[i], l = g.x - SL;
      return { left: l, right: l + g.w, width: g.w, top: 0, bottom: 10, height: 10, x: l, y: 0 };
    };
    const m0 = models[0];
    return { geo: m0.geo, est: est, ths: m0.ths, tds: m0.tds, margin: m0.margin, colOf: colOf, models: models };
  }
  const tailSheetsOf = (d, pre) => Array.from(d.querySelectorAll('style')).filter((s) => new RegExp('^' + pre + '-virt-width-style-tail(-\\d+|-s)?$').test(s.id));
  for (const LAND of [true, false]) {
    const ok9 = (c, m) => ok(c, (LAND ? '' : '[no cells land] ') + m);
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: NCOL, pairColdFrom: HOT, land: LAND });
    await tick(10);
    const d = win.document, pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const gv = win.__gvs['#bulk-pair-table'];
    const M = layoutModel(win, pt, pane, 300);
    const collapsed = () => { const o = []; for (let i = HOT; i < NCOL; i++) if (gv.isCollapsed('c' + i)) o.push(i); return o; };
    ok9(collapsed().length === NCOL - HOT, 'model: the whole cold tail starts out of layout');
    // count what each step writes: rule sheets (must be none) and on-class toggles
    const TC = Object.getOwnPropertyDescriptor(win.Node.prototype, 'textContent');
    let sheetWrites = 0, onToggles = 0;
    Object.defineProperty(win.Node.prototype, 'textContent', { configurable: true, get: TC.get, set: function (v) {
      if (this.tagName === 'STYLE' && /^bulk-pair-virt-width-style-tail/.test(this.id)) sheetWrites++;
      return TC.set.call(this, v); } });
    const TL = win.DOMTokenList.prototype, tog0 = TL.toggle;
    TL.toggle = function (c) { if (c === 'bulk-virt-on') onToggles++; return tog0.apply(this, arguments); };
    const sheetText0 = tailSheetsOf(d, 'bulk-pair').map((s) => s.textContent).join('\n');
    win.__fetched = [];
    // the jump: the scrollbar dragged to the end
    pane.scrollLeft = pane.scrollWidth - pane.clientWidth;
    for (let i = 0; i < 4; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    const col1 = collapsed();
    const shown1 = []; for (let i = HOT; i < NCOL; i++) if (col1.indexOf(i) < 0) shown1.push(i);
    ok9(shown1.length > 0 && shown1[shown1.length - 1] === NCOL - 1 && shown1.every((c, k) => k === 0 || c === shown1[k - 1] + 1),
       'the jump reveals one run of columns ending at the last one (c' + shown1[0] + '..c' + shown1[shown1.length - 1] + ')');
    // the window is (1 + 2 * 1.5) viewports + one of slack at most, in estimated px
    ok9(shown1.length <= Math.ceil((300 * 5) / M.est(20)) + 1 && col1.length >= 10,
       'and ONLY around the landing: ' + shown1.length + ' shown, ' + col1.length + ' of ' + (NCOL - HOT) + ' still out of layout');
    const sp = shown1[0] - 1;                       // the middle run's last column
    const spTh = pt.querySelector('th.ck-' + sp);
    const mw1 = parseFloat(spTh.style.getPropertyValue('min-width'));
    let wantMid = 0; for (let i = HOT; i <= sp; i++) wantMid += M.est(i);
    ok9(Math.abs(mw1 - wantMid) <= 0.01 && spTh.style.getPropertyPriority('min-width') === 'important'
       && win.getComputedStyle(pt.querySelector('tbody td.ck-' + sp)).display !== 'none',
       'the column left of it is a blank spacer, on screen, holding the middle run\'s estimated width (' + mw1 + ' vs ' + wantMid.toFixed(3) + ')');
    ok9(Array.from(pt.querySelectorAll('.ck-' + sp)).filter((e) => /^T[HD]$/.test(e.tagName)).every((e) => e.classList.contains('bulk-virt-spacer'))
       && /th\.bulk-virt-spacer>\*,#bulk-pair-table td\.bulk-virt-spacer>\*\{visibility:hidden!important\}/.test((d.getElementById('bulk-pair-virt-width-style-tail-s') || {}).textContent || '')
       && !spTh.classList.contains('bulk-virt-collapsed'),
       'the spacer shows no content (its th + tds carry the spacer class the static rule hides), and the group band spans it (no collapsed marker)');
    ok9(M.margin() === 0, 'nothing is left at the right end, so the margin is 0');
    const g1 = M.geo();
    ok9(Math.abs(pane.scrollLeft - (pane.scrollWidth - pane.clientWidth)) <= 1
       && g1[NCOL - 1].x + g1[NCOL - 1].w <= pane.scrollLeft + 300 + 1 && g1[NCOL - 1].x >= pane.scrollLeft,
       'it LANDS at the end: the real widths beat the estimates and the last column is still in view (' + Math.round(pane.scrollLeft) + ' of ' + (pane.scrollWidth - 300) + ')');
    const changed = NCOL - HOT - col1.length + 1;   // the window + the spacer
    ok9(sheetWrites === 0 && tailSheetsOf(d, 'bulk-pair').map((s) => s.textContent).join('\n') === sheetText0
       && onToggles > 0 && onToggles <= changed * (ROWS.length + 1),
       'the jump wrote NO rule sheet, only the on-class on the changed columns\' own cells (' + onToggles + ' toggles for ' + changed + ' columns x ' + (ROWS.length + 1) + ')');
    const asked = win.__fetched.map(decodeURIComponent).join(',');
    const askedMid = col1.filter((i) => new RegExp('[=,]c' + i + '(,|&|$)').test(asked));
    const askedWin = shown1.filter((i) => new RegExp('[=,]c' + i + '(,|&|$)').test(asked));
    ok9(askedWin.length > 0 && askedMid.length === 0,
        'the columns of the landing were fetched (c' + askedWin.join(',c') + '), nothing in the middle run was (' + askedMid.join(',') + ')');
    // three viewports back left: the middle run gives up its RIGHT end, no more
    pane.scrollLeft -= 900;
    for (let i = 0; i < 3; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    const col2 = collapsed();
    // the middle run (HOT..sp): what came back of it is a block at its RIGHT end
    const midBack = []; for (let i = HOT; i <= sp; i++) if (col2.indexOf(i) < 0) midBack.push(i);
    const back = midBack.length;
    ok9(back > 0 && back <= Math.ceil(300 * 4 / M.est(20)) + 1 && midBack[back - 1] === sp && midBack[0] === sp - back + 1,
       'scrolling back left brings the middle run back from its right end only (' + back + ' columns: c' + midBack.join(',c') + ')');
    // back to 0: the far end folds away again, spacer and all
    pane.scrollLeft = 0;
    for (let i = 0; i < 3; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(40); }
    ok9(collapsed().length === NCOL - HOT && !pt.querySelector('.bulk-virt-spacer') && !pt.querySelector('.bulk-virt-on')
       && M.ths.every((h) => !h.style.getPropertyValue('min-width')) && M.margin() > 0,
       'back at 0 the whole tail is out of layout again, one suffix, no spacer, no on-class left behind (margin ' + M.margin() + ')');
    ok9(sheetWrites === 0, 'and still no rule sheet was written (' + sheetWrites + ')');
    // asking for one column deep inside a run reveals that column, not the run
    gv.ensureTd(pt.querySelector('tr[data-pair="q2-x"] td.ck-30'));
    ok9(!gv.isCollapsed('c30') && gv.isCollapsed('c29') && gv.isCollapsed('c31')
       && pt.querySelector('th.ck-29').classList.contains('bulk-virt-spacer') && parseFloat(pt.querySelector('th.ck-29').style.minWidth) > 0,
       'a caret asked deep into the tail brings back that column alone, a spacer holding the run left of it');
    Object.defineProperty(win.Node.prototype, 'textContent', TC);
    TL.toggle = tog0;
  }

  // ── 10. a SLOW scroll keeps a bounded window on BOTH sides (w8) ─────────────
  // On the 30Q rig a slow scroll to the far right revealed column after column
  // and only ever took columns back on the RIGHT, so it ended up laying out
  // the whole 2,389-column table (every later step 0.5-0.77 s). Now a column
  // more than two viewports behind the window leaves layout too, behind a
  // spacer holding its MEASURED width, so nothing on screen moves; a reveal
  // from ESTIMATED widths (after a jump) is scrolled back by however far it
  // moved what is on screen; the tail is never released once everything has
  // landed; and no step queries the whole table.
  {
    const PW = 100;                                  // a narrow pane: the window is ~10 columns of 30
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: NCOL, pairColdFrom: HOT, land: true });
    await tick(10);
    const d = win.document, pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const gv = win.__gvs['#bulk-pair-table'];
    const M = layoutModel(win, pt, pane, PW);
    // PhysAmp as the page has it: a landing in tail mode repaints its own cells, never the table
    let physTable = 0, physCells = 0;
    win.PhysAmp = { applyAll: function (r) { if (r && r.tagName === 'TABLE') physTable++; }, paintWithin: function (els) { physCells += els.length; } };
    const E = win.Element.prototype, qsa0 = E.querySelectorAll;
    let tableScans = 0;
    E.querySelectorAll = function (sel) { if (this === pt) tableScans++; return qsa0.apply(this, arguments); };
    const TC = Object.getOwnPropertyDescriptor(win.Node.prototype, 'textContent');
    let sheetWrites = 0;
    Object.defineProperty(win.Node.prototype, 'textContent', { configurable: true, get: TC.get, set: function (v) {
      if (this.tagName === 'STYLE' && /^bulk-pair-virt-width-style-tail/.test(this.id)) sheetWrites++;
      return TC.set.call(this, v); } });
    const laid = () => { let n = 0; for (let i = HOT; i < NCOL; i++) if (M.ths[i].classList.contains('bulk-virt-on') && !M.ths[i].classList.contains('bulk-virt-spacer')) n++; return n; };
    // the first laid-out, non-spacer column in view and its screen x
    const anchor = () => { const g = M.geo(); for (let i = 0; i < NCOL; i++) {
      if (i >= HOT && (!M.ths[i].classList.contains('bulk-virt-on') || M.ths[i].classList.contains('bulk-virt-spacer'))) continue;
      if (g[i].w > 0 && g[i].x + g[i].w > pane.scrollLeft && g[i].x < pane.scrollLeft + PW) return { i: i, sx: g[i].x - pane.scrollLeft }; } return null; };
    const coldInView = () => { const g = M.geo(), o = []; for (let i = HOT; i < NCOL; i++) {
      if (g[i].w > 0 && g[i].x + g[i].w > pane.scrollLeft && g[i].x < pane.scrollLeft + PW
          && !M.ths[i].classList.contains('bulk-virt-spacer') && M.tds[i].classList.contains('bulk-td-cold')) o.push(i); } return o; };
    const settle = async () => { for (let i = 0; i < 4; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(15); } };
    let maxLaid = 0, moved = [], cold = [], steps = 0, unstable = [];
    const bound = Math.ceil((PW + 2 * (PW * 3.5 + PW)) / M.est(20)) + 2;
    const walk = async (dir, stepPx, onStep) => {
      for (let k = 0; k < 400; k++) {
        const a = anchor();
        const before = pane.scrollLeft;
        pane.scrollLeft = before + dir * stepPx;
        const did = pane.scrollLeft - before;
        if (!did) break;
        steps++;
        await settle();
        // what the user scrolled by is `did`; anything else is the screen moving under them
        if (a) {
          const g = M.geo(), on = M.ths[a.i].classList.contains('bulk-virt-on') || a.i < HOT;
          if (on && g[a.i].w > 0) { const sx = g[a.i].x - pane.scrollLeft, want = a.sx - did; if (Math.abs(sx - want) > 1) moved.push('c' + a.i + '@' + Math.round(before) + ':' + Math.round(sx - want)); }
        }
        maxLaid = Math.max(maxLaid, laid());
        cold = cold.concat(coldInView().map((i) => 'c' + i + '@' + Math.round(pane.scrollLeft)));
        if (onStep) onStep();
        // at rest the window is at rest: more passes with no scroll change nothing
        const snap = M.ths.map((h) => h.className).join('|');
        await settle();
        if (M.ths.map((h) => h.className).join('|') !== snap) unstable.push(Math.round(pane.scrollLeft));
      }
    };
    await settle();
    tableScans = 0;
    // walk until c12 has landed, and keep one of its inputs
    let inp12 = null;
    for (let k = 0; k < 40 && !inp12; k++) { pane.scrollLeft += 60; await settle(); inp12 = pt.querySelector('tr[data-pair="q2-x"] td.ck-12 .bulk-cell'); }
    const td12 = pt.querySelector('tr[data-pair="q2-x"] td.ck-12');
    await walk(+1, 60);
    const atEnd = { sl: pane.scrollLeft, max: pane.scrollWidth - PW, laid: laid(), c10: gv.isCollapsed('c10'), sp: !!pt.querySelector('th.bulk-virt-spacer') };
    const row2 = pt.querySelector('tr[data-pair="q2-x"]');
    ok(gv.isCollapsed('c12') && row2.getBoundingClientRect().height === 50 && parseFloat(row2.style.height) === 50,
       'a row does not get shorter when its tallest column leaves: c12 (two lines) is out of layout, the row keeps 50 px (' + row2.getBoundingClientRect().height + ', inline ' + row2.style.height + ')');
    ok(physTable === 0 && physCells > 0, 'landings repainted the units of their own cells (' + physCells + ' cells), never the whole table (' + physTable + ')');
    const inputsIn = qsa0.call(pt, '.bulk-cell').length;
    ok(!!inp12 && !inp12.isConnected && gv.isCold('c12') && gv.valOf(td12) === String(inp12.value).toLowerCase()
       && inputsIn <= (HOT + bound + 1) * ROWS.length,
       'a column left far behind gives its cells back to a fragment (out of the document: ' + inputsIn + ' inputs left in the table), its value kept for the search (' + gv.valOf(td12) + ')');
    ok(atEnd.sl >= atEnd.max - 1, 'the slow scroll reaches the far right (' + Math.round(atEnd.sl) + ' of ' + Math.round(atEnd.max) + ', ' + steps + ' steps)');
    ok(atEnd.c10 && atEnd.sp && gv.state() && gv.state().tail,
       'there the columns it left behind are out of layout behind a spacer, and the tail was kept although every visited column landed');
    ok(maxLaid <= bound && maxLaid < NCOL - HOT,
       'at no step were more than a bounded window of tail columns laid out (max ' + maxLaid + ', bound ' + bound + ', tail ' + (NCOL - HOT) + ')');
    ok(moved.length === 0, 'nothing on screen moved under the user on the way right (' + moved.slice(0, 4).join(' ') + ')');
    ok(cold.length === 0, 'no cold cell was in view after any step (' + cold.slice(0, 4).join(' ') + ')');
    const fetched0 = win.__fetched.length;
    let back12 = null;
    await walk(-1, 60, function () {
      const g = M.geo();
      if (back12 === null && g[12].w > 0 && g[12].x < pane.scrollLeft + PW && g[12].x + g[12].w > pane.scrollLeft)
        back12 = { same: td12.contains(inp12) && inp12.isConnected, cold: gv.isCold('c12') };
    });
    ok(pane.scrollLeft === 0 && gv.isCollapsed('c' + (NCOL - 1)) && laid() <= bound,
       'and back at 0 the far end is out of layout again (laid ' + laid() + ')');
    ok(back12 && back12.same && !back12.cold && win.__fetched.length === fetched0,
       'on the way back the parked column was in view with the SAME input nodes, and nothing was fetched again (' + JSON.stringify(back12) + ', ' + (win.__fetched.length - fetched0) + ' fetches)');
    ok(moved.length === 0 && cold.length === 0, 'nothing moved and nothing cold showed on the way back left (' + moved.slice(0, 4).join(' ') + ' | ' + cold.slice(0, 4).join(' ') + ')');
    ok(unstable.length === 0, 'the window never thrashes: at every step, more passes without scrolling change nothing (' + unstable.slice(0, 6).join(',') + ')');
    ok(sheetWrites === 0, 'a whole slow scroll wrote no rule sheet (' + sheetWrites + ')');
    ok(tableScans === 0, 'and no step queried the whole table (' + tableScans + ' table-wide querySelectorAll)');
    E.querySelectorAll = qsa0;
    Object.defineProperty(win.Node.prototype, 'textContent', TC);
  }
  // the same after a JUMP: the run it skipped comes back from ESTIMATES as
  // the user scrolls left into it -- the screen must not move by the error
  {
    const PW = 100;
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: NCOL, pairColdFrom: HOT, land: true });
    await tick(10);
    const d = win.document, pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const M = layoutModel(win, pt, pane, PW);
    const settle = async () => { for (let i = 0; i < 4; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(15); } };
    pane.scrollLeft = pane.scrollWidth; await settle();
    const moved = []; let n = 0, revealed = 0;
    for (let k = 0; k < 400; k++) {
      const g0 = M.geo(); let a = null;
      for (let i = 0; i < NCOL && !a; i++) {
        if (i >= HOT && (!M.ths[i].classList.contains('bulk-virt-on') || M.ths[i].classList.contains('bulk-virt-spacer'))) continue;
        if (g0[i].w > 0 && g0[i].x + g0[i].w > pane.scrollLeft && g0[i].x < pane.scrollLeft + PW) a = { i: i, sx: g0[i].x - pane.scrollLeft };
      }
      // shown as a real column (on, not a spacer) before this step
      const onBefore = M.ths.map((h) => h.classList.contains('bulk-virt-on') && !h.classList.contains('bulk-virt-spacer'));
      const before = pane.scrollLeft;
      pane.scrollLeft = before - 60;
      const did = pane.scrollLeft - before;
      if (!did) break;
      n++;
      await settle();
      if (M.ths.some((h, i) => h.classList.contains('bulk-virt-on') && !onBefore[i] && !h.classList.contains('bulk-virt-spacer'))) revealed++;
      if (a) { const g = M.geo(); if (g[a.i].w > 0) { const sx = g[a.i].x - pane.scrollLeft; if (Math.abs(sx - (a.sx - did)) > 1) moved.push('c' + a.i + '@' + Math.round(before) + ':' + Math.round(sx - (a.sx - did))); } }
    }
    ok(revealed > 0 && moved.length === 0,
       'scrolling left into a run a jump skipped: ' + revealed + ' reveals from estimates, and nothing on screen moved (' + moved.slice(0, 4).join(' ') + ', ' + n + ' steps)');
  }

  // ── 11. two grids, one scroller: one layout change per FRAME, and no starving ─
  // #table-pane scrolls the qubit and the pair grid together. When both
  // changed their windows in the same rAF, each forced the other's layout and
  // the frame paid both (250-340 ms on the 30Q rig); now the first grid to
  // change stamps the frame and the other waits one frame -- and is owed it,
  // so a scroll that keeps the first grid changing every frame cannot starve
  // the second (its cells would stay cold on screen).
  {
    const PW = 100;
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: HOT, pairColdFrom: HOT, land: true });
    await tick(10);
    const d = win.document, qt = d.getElementById('bulk-table'), pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const M = layoutModel(win, [qt, pt], pane, PW);
    const raf0 = win.requestAnimationFrame.bind(win);
    // which grid wrote in a frame: compare each grid's on-set before/after every frame
    const onSet = (t) => Array.from(t.querySelectorAll('th.bulk-col-head')).map((h) => h.classList.contains('bulk-virt-on') ? 1 : 0).join('');
    const perFrame = [];
    let prev = { q: onSet(qt), p: onSet(pt) };
    const frameTick = () => new Promise((r) => raf0(function () { setTimeout(function () {
      const now = { q: onSet(qt), p: onSet(pt) };
      perFrame.push({ q: now.q !== prev.q, p: now.p !== prev.p });
      prev = now; r();
    }, 0); }));
    // a scroll that moves both windows every frame: three viewports per frame
    for (let k = 0; k < 16; k++) {
      pane.scrollLeft = pane.scrollLeft + 3 * PW;
      pane.dispatchEvent(new win.Event('scroll'));
      await frameTick();
    }
    const both = perFrame.filter((f) => f.q && f.p).length;
    const qFrames = perFrame.filter((f) => f.q).length, pFrames = perFrame.filter((f) => f.p).length;
    ok(both === 0, 'no frame changed both grids\' layout (' + both + ' of ' + perFrame.length + ' frames did)');
    ok(qFrames > 0 && pFrames > 0 && Math.min(qFrames, pFrames) >= Math.floor(perFrame.length / 4),
       'and neither grid starved: the qubit grid changed in ' + qFrames + ' frames, the pair grid in ' + pFrames + ' of ' + perFrame.length);
    // once the scroll stops, both catch up: nothing cold on screen in either grid
    for (let k = 0; k < 12; k++) { pane.dispatchEvent(new win.Event('scroll')); await tick(20); }
    const coldIn = (m) => { const g = m.geo(), o = []; for (let i = HOT; i < NCOL; i++) {
      if (g[i].w > 0 && g[i].x + g[i].w > pane.scrollLeft && g[i].x < pane.scrollLeft + PW
          && !m.ths[i].classList.contains('bulk-virt-spacer') && m.tds[i].classList.contains('bulk-td-cold')) o.push(i); } return o; };
    const cq = coldIn(M.models[0]), cp = coldIn(M.models[1]);
    ok(cq.length === 0 && cp.length === 0, 'and once it stops both grids are hydrated on screen (cold: qubit ' + cq.join(',') + ' / pair ' + cp.join(',') + ')');
  }

  // ── 11b. one scroller, two grids: only the grid being looked at keeps the screen ─
  // A scrollLeft that keeps one grid still moves the other by the same amount
  // (their columns are unrelated). On the 30Q rig the qubit grid, scrolled out
  // of view above, "kept" its own cells after a jump and shoved the pair grid
  // the user was reading 26-77 px sideways. The qubit table sits above the
  // pane here; the pair table fills it.
  {
    const PW = 100;
    const win = world({ tailMin: 100, tailBlock: 4, coldFrom: HOT, pairColdFrom: HOT, land: true });
    await tick(10);
    const d = win.document, qt = d.getElementById('bulk-table'), pt = d.getElementById('bulk-pair-table'), pane = d.getElementById('table-pane');
    const M = layoutModel(win, [qt, pt], pane, PW, { 'bulk-table': { top: -2000, bottom: -1000 }, 'bulk-pair-table': { top: 0, bottom: 800 } });
    const P = M.models[1];
    const settle = async () => { for (let i = 0; i < 6; i++) { pane.dispatchEvent(new win.Event('scroll')); await tick(15); } };
    pane.scrollLeft = Math.round(pane.scrollWidth * 0.6); await settle();
    const moved = []; let n = 0;
    for (let k = 0; k < 60; k++) {
      const g0 = P.geo(); let a = null;
      for (let i = 0; i < NCOL && !a; i++) {
        if (i >= HOT && (!P.ths[i].classList.contains('bulk-virt-on') || P.ths[i].classList.contains('bulk-virt-spacer'))) continue;
        if (g0[i].w > 0 && g0[i].x + g0[i].w > pane.scrollLeft && g0[i].x < pane.scrollLeft + PW) a = { i: i, sx: g0[i].x - pane.scrollLeft };
      }
      const before = pane.scrollLeft;
      pane.scrollLeft = before - 60;
      const did = pane.scrollLeft - before;
      if (!did) break;
      n++;
      await settle();
      if (a) { const g = P.geo(); if (g[a.i].w > 0) { const sx = g[a.i].x - pane.scrollLeft; if (Math.abs(sx - (a.sx - did)) > 1) moved.push('c' + a.i + '@' + Math.round(before) + ':' + Math.round(sx - (a.sx - did))); } }
    }
    ok(n > 10 && moved.length === 0, 'with the qubit grid out of view, the pair grid being read never moves sideways (' + moved.slice(0, 4).join(' ') + ', ' + n + ' steps)');
  }

  // ── 12. the toolbars follow a sideways scroll without walking the grids ───
  // #table-pane's toolbar rows follow a sideways scroll by transform, one rAF
  // per scroll event. Finding them was a querySelectorAll over the pane -- its
  // every grid cell and both column menus, 22-50 ms of EVERY scroll frame on
  // the 30Q rig (w8). Now a walk that enters neither a table nor a bar.
  {
    const win = world({ tailMin: 100, coldFrom: HOT, pairColdFrom: HOT });
    await tick(10);
    const d = win.document, pane = d.getElementById('table-pane');
    const SEL = '.bulk-toolbar, .bulk-chipbar, .bulk-pair-divider, .bulk-dyn-truncated, .bulk-virt-note';
    const E = win.Element.prototype, qsa0 = E.querySelectorAll, m0 = E.matches;
    let paneScans = 0, inTable = 0, tested = 0;
    E.querySelectorAll = function (sel) { if (this === pane && sel === SEL) paneScans++; return qsa0.apply(this, arguments); };
    E.matches = function (sel) { if (sel === SEL) { tested++; if (this.closest && this.closest('table')) inTable++; } return m0.apply(this, arguments); };
    pane.scrollLeft = 500;
    pane.dispatchEvent(new win.Event('scroll')); await tick(40);
    E.querySelectorAll = qsa0; E.matches = m0;
    const bars = Array.from(qsa0.call(pane, SEL));
    ok(bars.length >= 3 && bars.every((b) => b.style.transform === 'translateX(500px)'),
       'every toolbar row follows the scroll (' + bars.length + ' bars: ' + bars.map((b) => b.style.transform || '-').join(' ') + ')');
    ok(paneScans === 0 && inTable === 0 && tested > 0 && tested < 40,
       'found without a pane-wide query and without looking inside a table (' + paneScans + ' scans, ' + tested + ' elements tested, ' + inTable + ' in a table)');
  }

  console.log(fails ? ('FAILED ' + fails + ' of ' + asserts) : ('all checks passed (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
