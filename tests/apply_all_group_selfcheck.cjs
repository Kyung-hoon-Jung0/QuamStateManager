// QA diagnostics-r2-15 -- one Apply all is ONE server change group.
//
// Apply all posts one atomic /field/edit-batch PER ROW. A fill-down leaves one
// dirty cell per row, so every row was its own ungrouped change: undoing one
// Apply all took one Ctrl+Z per qubit (banner 10 -> 8 -> 6 ...). docs/20:
// "Apply All = ... ONE gid = one Review bundle = one Ctrl+Z".
//
// Pins, against the REAL bulk-edit.js + pair-edit.js under jsdom with a mocked
// server that joins the way /field/edit-batch does:
//   1. the first row asks for a new group, every later row joins the gid the
//      server answered with (both grids -- pair-edit.js is a copy, kept in sync)
//   2. a row that failed (rolled back, no gid) does not break the chain
//   3. a single-row Apply (applyRow) sends no group -- unchanged behaviour
//
// Run: node tests/apply_all_group_selfcheck.cjs   (driven by test_apply_all_group.py)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const GRID_VIRT_JS = fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8');
const BULK_JS = fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8');
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const QUBITS = ['q1', 'q2', 'q3', 'q4', 'q5'];
const PAIRS = ['q1-2', 'q2-3', 'q3-4'];

function qtd(key, dp, v) {
  return '<td class="bulk-td" data-col-key="' + key + '"><input type="text" class="bulk-cell" value="' + v
    + '" data-orig="' + v + '" data-dot-path="' + dp + '" data-resolved="' + dp + '" size="14"></td>';
}
function applyCol() {
  return '<td class="bulk-apply-col"><button type="button" class="btn-xs bulk-row-apply" disabled>Apply</button>'
    + '<span class="bulk-row-error" hidden></span></td>';
}
function head(key) {
  return '<th class="bulk-col-head" data-col-key="' + key + '" data-section="S">'
    + '<span class="bulk-col-label">' + key + '</span>'
    + '<span class="bulk-col-stats muted" data-col-stats="' + key + '"></span></th>';
}
function qubitGrid() {
  const rows = QUBITS.map(function (q) {
    return '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>'
      + qtd('amp', 'qubits.' + q + '.xy.operations.x180.amplitude', '0.1') + applyCol() + '</tr>';
  }).join('');
  return '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
    + '<span id="bulk-search-hint"></span></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-apply-sync" disabled></button>'
    + '<button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head('amp')
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function pairGrid() {
  const rows = PAIRS.map(function (id, i) {
    return '<tr data-qubit="' + id + '" data-pair="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">'
      + id + '</th>' + qtd('det', 'qubit_pairs.' + id + '.detuning', String(5000000 + i)) + applyCol() + '</tr>';
  }).join('');
  return '<div class="bulk-pair-divider" id="bulk-pair-divider">'
    + '<div class="bulk-colvis-menu" id="bulk-pair-colvis-menu"></div>'
    + '<span id="bulk-pair-search-count"></span><span id="bulk-pair-dirty-count"></span>'
    + '<button id="bulk-pair-apply-all" disabled></button><button id="bulk-pair-apply-sync" disabled></button>'
    + '<button id="bulk-pair-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="bulk-pair-table"><thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head('det')
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

// failPath: a row the server rolls back (no gid, nothing in the log)
function world(failPath) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane"><div class="bulk-panel">'
    + qubitGrid() + pairGrid() + '</div></div></body></html>',
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
  win.confirm = function () { return true; };
  win.posts = [];
  let seq = 7, top = null;
  // /field/edit-batch with the server's join rule: "new" mints, a gid joins
  // only while it is the top of the log, a failed row commits nothing.
  win.fetch = function (url, opts) {
    const body = JSON.parse(opts.body);
    win.posts.push(body);
    const failed = body.updates.some(function (u) { return u.dot_path === failPath; });
    if (failed) {
      return Promise.resolve({ status: 400, ok: false, json: function () {
        return Promise.resolve({ ok: false, results: body.updates.map(function (u) {
          return { dot_path: u.dot_path, applied: false, error: 'bad value' }; }) }); } });
    }
    let gid = null;
    if (typeof body.group === 'string') gid = (body.group !== 'new' && body.group === top) ? top : ('grp' + (seq++));
    else if (body.updates.length > 1) gid = 'grp' + (seq++);
    top = gid;
    const results = body.updates.map(function (u) {
      return { dot_path: u.dot_path, applied: true, display: String(u.value) };
    });
    return Promise.resolve({ status: 200, ok: true,
      json: function () { return Promise.resolve({ ok: true, results: results, group_id: gid }); } });
  };
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  new win.Function(PAIR_JS).call(win);
  win.BulkEdit.mount(
    [{ key: 'amp', label: 'amp', section: 'S', unit: '', default_on: true }],
    { bands: {} }, [], { chip: 'chipA', qubits: QUBITS.map(function (q) { return { id: q, grid: null }; }) });
  win.BulkPairEdit.mount([{ key: 'det', label: 'det', section: 'S', unit: '', default_on: true,
                             editable: true, kind: 'scalar', maxlen: 12 }]);
  return win;
}

const inp = (win, dp) => win.document.querySelector('.bulk-cell[data-dot-path="' + dp + '"]');
function type(win, cell, v) {
  cell.value = v;
  cell.dispatchEvent(new win.Event('input', { bubbles: true }));
}
async function settle(win, n) {
  for (let k = 0; k < 60 && win.posts.length < n; k++) await tick(10);
  await tick(20);
}

(async function main() {
  // ── 1. a fill-down's Apply all: 5 single-cell rows -> one group ─────────
  {
    const win = world(null);
    QUBITS.forEach(function (q) { type(win, inp(win, 'qubits.' + q + '.xy.operations.x180.amplitude'), '1.5'); });
    win.BulkEdit.applyAll(false);
    await settle(win, 5);
    const g = win.posts.map(function (p) { return p.group; });
    ok(win.posts.length === 5, 'precondition: one request per dirty row (got ' + win.posts.length + ')');
    ok(g[0] === 'new', 'the first row asks for a NEW group (got ' + JSON.stringify(g[0]) + ')');
    ok(g.slice(1).every(function (x) { return x === 'grp7'; }),
       'every later row joins the gid the server answered (got ' + JSON.stringify(g) + ')');
  }

  // ── 2. a rolled-back row in the middle does not break the chain ─────────
  {
    const win = world('qubits.q2.xy.operations.x180.amplitude');
    QUBITS.forEach(function (q) { type(win, inp(win, 'qubits.' + q + '.xy.operations.x180.amplitude'), '1.5'); });
    win.BulkEdit.applyAll(false);
    await settle(win, 5);
    const g = win.posts.map(function (p) { return p.group; });
    ok(g[0] === 'new' && g[1] === 'grp7' && g[2] === 'grp7' && g[4] === 'grp7',
       'a failed row leaves the group intact for the rows after it (got ' + JSON.stringify(g) + ')');
  }

  // ── 3. the pair grid (a copy of the same code) ─────────────────────────
  {
    const win = world(null);
    PAIRS.forEach(function (id) { type(win, inp(win, 'qubit_pairs.' + id + '.detuning'), '6000000'); });
    win.BulkPairEdit.applyAll(false);
    await settle(win, 3);
    const g = win.posts.map(function (p) { return p.group; });
    ok(win.posts.length === 3 && g[0] === 'new' && g[1] === 'grp7' && g[2] === 'grp7',
       'the pair grid\'s Apply all is one group too (got ' + JSON.stringify(g) + ')');
  }

  // ── 4. a single-row Apply is unchanged: no group asked for ──────────────
  {
    const win = world(null);
    const cell = inp(win, 'qubits.q3.xy.operations.x180.amplitude');
    type(win, cell, '0.2');
    win.BulkEdit.applyRow(cell.closest('tr').querySelector('.bulk-row-apply'));
    await settle(win, 1);
    ok(win.posts.length === 1 && !('group' in win.posts[0]),
       'applyRow sends no group (got ' + JSON.stringify(win.posts[0] && win.posts[0].group) + ')');
  }

  if (fails) { console.error(fails + ' of ' + asserts + ' checks failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ')');
  process.exit(0);
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
