// QA liveedit-r2-02 -- an edit made in Flat View reaches the Table View.
//
// Flat View (all-values.js) lives in #table-pane beside the Table View, so its
// tray swap runs inside `window._bulkSelfEdit = true` to stop the Table's
// quam:state-changed listener from re-GETting /bulk (which would wipe Flat's
// own DOM). That re-GET was also the Table's ONLY way to learn of the edit:
// switching back showed the pre-edit number, its stale `data-orig`, no
// modified marker and the old header min/max until a page reload -- and the
// Table's selection arithmetic / fill-down then computed from the stale value.
//
// Drives the REAL all-values.js + bulk-edit.js (+ grid-virt.js) under jsdom:
// a Flat Enter commits through a mocked /field/edit-batch and the Table cell
// must carry the committed value as its clean baseline, the modified marker,
// and a recomputed header; an alias cell (data-resolved) too; nothing does a
// full /bulk re-GET; and a change the patch cannot repaint honestly (the
// docs/56 stored-as-text decoration) falls back to the honest resync when
// the Table is shown again.
//
// Run: node tests/flat_to_table_selfcheck.cjs   (needs jsdom)
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
const AV_JS = fs.readFileSync(path.join(STATIC, 'all-values.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const ANH = (q) => 'qubits.' + q + '.anharmonicity';
const X90A = (q) => 'qubits.' + q + '.xy.operations.x90.amplitude';            // the alias
const X90R = (q) => 'qubits.' + q + '.xy.operations.x90_DragCosine.amplitude'; // the leaf
const T1 = (q) => 'qubits.' + q + '.T1';
const CM = (q) => 'qubits.' + q + '.resonator.confusion_matrix';                // a LIST cell
const VALS = {
  q1: { anh: '210,000,000', x90: '0.15', t1: '0.0001' },
  q2: { anh: '216,286,684.1857654', x90: '0.1516', t1: '0.000135' },
  q3: { anh: '190,000,000', x90: '0.14', t1: '0.00009' },
};

function td(key, attrs, cls) {
  return '<td class="bulk-td" data-col-key="' + key + '"><input type="text" class="bulk-cell'
    + (cls ? ' ' + cls : '') + '" ' + attrs + ' size="14"></td>';
}
function tableHtml() {
  const head = ['anh', 'x90', 't1', 'cm'].map((k) =>
    '<th class="bulk-col-head" data-col-key="' + k + '" data-section="S"><span class="bulk-col-label">'
    + k + '</span><span class="bulk-col-stats" data-col-stats="' + k + '"></span></th>').join('');
  const rows = Object.keys(VALS).map(function (q) {
    const v = VALS[q];
    return '<tr data-qubit="' + q + '"><th class="bulk-rowhead" data-col-key="__id__">' + q + '</th>'
      + td('anh', 'value="' + v.anh + '" data-orig="' + v.anh + '" data-dot-path="' + ANH(q)
           + '" data-resolved="' + ANH(q) + '" data-linkable="1"')
      + td('x90', 'value="' + v.x90 + '" data-orig="' + v.x90 + '" data-dot-path="' + X90A(q)
           + '" data-resolved="' + X90R(q) + '" data-linkable="1"')
      // docs/56: a stored-as-text value renders decorated -- a plain value
      // write cannot keep that promise, so it must stay UNCOVERED
      + td('t1', 'value="' + v.t1 + '" data-orig="' + v.t1 + '" data-str-numeric="1" data-dot-path="'
           + T1(q) + '" data-resolved="' + T1(q) + '"', 'bulk-cell-str')
      // the docs/159 list preview span: its path is the CONTAINER
      + '<td class="bulk-td" data-col-key="cm"><span class="bulk-cell-list" data-path="' + CM(q)
      + '" data-resolved="' + CM(q) + '" tabindex="0">[[0.968,0.032],[0.123,0.…</span></td>'
      + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled></button>'
      + '<span class="bulk-row-error" hidden></span></td></tr>';
  }).join('');
  return '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>'
    + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>'
    + '<span class="bulk-search-wrap"><input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
    + '<span id="bulk-search-hint"></span></span><span id="bulk-dirty-count"></span>'
    + '<button id="bulk-apply-all" disabled></button><button id="bulk-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr>'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + head + '</tr></thead><tbody>'
    + rows + '</tbody></table></div>';
}
const FLAT = `
  <input type="search" id="av-search">
  <span id="av-coverage"></span><span id="av-showing"></span>
  <span id="av-dirty-count"></span>
  <button id="av-apply" disabled></button><button id="av-reset" disabled></button>
  <div id="av-chips"></div>
  <div class="av-scroll" id="av-scroll">
    <table class="av-table-virtual" id="av-table"><tbody id="av-tbody"></tbody></table>
  </div>`;
const DOM = '<div id="table-pane">'
  + '<button type="button" class="bulk-seg" data-pane="grid">Grid</button>'
  + '<button type="button" class="bulk-seg" data-pane="allvalues">All values</button>'
  + '<div data-bulk-pane="grid">' + tableHtml() + '</div>'
  + '<div data-bulk-pane="allvalues" hidden>' + FLAT + '</div></div>';

function flatRows() {
  const rows = [];
  Object.keys(VALS).forEach(function (q) {
    rows.push([ANH(q), VALS[q].anh, 'scalar', 0]);
    rows.push([X90R(q), VALS[q].x90, 'scalar', 0]);
    rows.push([T1(q), VALS[q].t1, 'scalar', 0]);
    rows.push([CM(q) + '.0.0', '0.968', 'list', 0]);   // Flat lists matrix ELEMENTS
  });
  return rows;
}

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;                       // docs/125: bridge, or it throws
  win.__chipToken = 'tok';
  Object.defineProperty(win.document.getElementById('av-scroll'), 'clientHeight', { value: 600 });
  win._log = { ajax: [], editBatch: [], resync: [] };
  win.htmx = { ajax: function (m, u) { win._log.ajax.push(m + ' ' + u); }, process() {}, trigger() {} };
  win.showToast = function () {};
  win._diagChanged = function () {};
  win._scheduleGridResync = function (ms) { win._log.resync.push(ms); };
  // the real tray swap announces quam:state-changed -- the Table's listener
  // must still see _bulkSelfEdit and NOT re-GET (Flat's DOM would die)
  win._swapPendingTray = function () {
    win.document.dispatchEvent(new win.CustomEvent('quam:state-changed'));
  };
  const modified = [];
  function jsonResp(data, headers) {
    headers = headers || {};
    return Promise.resolve({ status: 200, ok: true,
      headers: { get: function (k) { return headers[k] || null; } },
      json: function () { return Promise.resolve(data); } });
  }
  win.fetch = function (url, opts) {
    if (url.indexOf('/bulk/all-values') === 0) {
      return jsonResp({ rows: flatRows(), summary: { total: 9, editable: 9, readonly: 0, by_kind: {} } },
        { ETag: '"e1"' });
    }
    if (url.indexOf('/field/peek') === 0) return jsonResp({ ok: true, values: {}, errors: {}, resolved: {} });
    if (url === '/field/edit-batch') {
      const body = JSON.parse(opts.body);
      win._log.editBatch.push(body);
      const results = body.updates.map(function (u) {
        const disp = win._display[u.dot_path] || String(u.value);
        const old = flatRows().filter((r) => r[0] === u.dot_path)[0];
        if (!modified.some((m) => m.resolved_path === u.dot_path)) {
          modified.push({ resolved_path: u.dot_path, old_value: old && old[1], old_display: old && old[1] });
        }
        return { dot_path: u.dot_path, resolved_path: u.dot_path, applied: true,
                 new_value: u.value, display: disp };
      });
      return jsonResp({ ok: true, results: results, tray_html: '<div></div>', modified: modified.slice() });
    }
    return Promise.reject(new Error('unexpected fetch ' + url));
  };
  win._display = {};
  new win.Function(GRID_VIRT_JS).call(win);
  new win.Function(BULK_JS).call(win);
  new win.Function(AV_JS).call(win);
  win.BulkEdit.mount([
    { key: 'anh', label: 'anh', section: 'S', unit: '', default_on: true },
    { key: 'x90', label: 'x90', section: 'S', unit: '', default_on: true },
    { key: 't1', label: 't1', section: 'S', unit: '', default_on: true },
    { key: 'cm', label: 'cm', section: 'S', unit: '', default_on: true },
  ], { bands: {} }, [], { chip: 'chipA', qubits: [] });
  return win;
}

function flatInput(win, p) {
  return win.document.querySelector('#av-tbody .av-input[data-dot-path="' + p + '"]');
}
// open the collapsed group that holds `p` (a click TOGGLES and re-renders, so
// click exactly that one group row, by its label)
function expandFor(win, p) {
  const q = p.split('.')[1];
  const rows = Array.prototype.slice.call(win.document.querySelectorAll('#av-tbody .av-group-row'));
  const g = rows.filter((r) => / · /.test(r.textContent) && r.textContent.indexOf('· ' + q) >= 0)[0];
  if (g) g.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
}
async function flatCommit(win, p, value, display) {
  win._display[p] = display;
  let inp = flatInput(win, p);
  if (!inp) { expandFor(win, p); inp = flatInput(win, p); }
  if (!inp) throw new Error('no Flat input for ' + p);
  inp.value = value;
  inp.dispatchEvent(new win.Event('input', { bubbles: true }));
  inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await tick(40);
}
function tcell(win, q, key) {
  return win.document.querySelector('#bulk-table tr[data-qubit="' + q + '"] td[data-col-key="' + key + '"] .bulk-cell');
}
function stat(win, key) {
  return win.document.querySelector('[data-col-stats="' + key + '"]').textContent;
}

(async function main() {
  const win = world();
  // (no app.js here, so the header prints ungrouped digits -- compare without commas)
  const S = (k) => stat(win, k).replace(/,/g, '');
  ok(/max 216286684\.1857654/.test(S('anh')), 'fixture: the header max starts at q2 (' + stat(win, 'anh') + ')');
  ok(tcell(win, 'q2', 'anh').classList.contains('cell-best'), 'fixture: q2 is the column max');

  win.AllValues.switchPane('allvalues');
  await tick(30);
  await flatCommit(win, ANH('q2'), '200000001', '200,000,001');
  ok(win._log.editBatch.length === 1, 'fixture: the Flat Enter committed one edit');

  win.AllValues.switchPane('grid');
  await tick(10);
  const c = tcell(win, 'q2', 'anh');
  ok(c.value === '200,000,001', 'the Table cell shows the Flat-committed value (' + c.value + ')');
  ok(c.getAttribute('data-orig') === '200,000,001' && !c.classList.contains('dirty'),
     'and it is the clean baseline (data-orig moved, not an edit)');
  ok(c.classList.contains('bulk-cell-modified')
     && c.getAttribute('data-baseline') === '216,286,684.1857654',
     'with the modified marker and the before->after baseline');
  ok(!/216286684/.test(S('anh')) && /max 210000000/.test(S('anh')),
     'the header min/max are recomputed (' + stat(win, 'anh') + ')');
  ok(tcell(win, 'q1', 'anh').classList.contains('cell-best') && !c.classList.contains('cell-best'),
     'and the column-max colouring moved to q1');

  // an ALIAS cell (x90 amp over the DragCosine leaf) is found by data-resolved
  win.AllValues.switchPane('allvalues');
  await tick(30);
  await flatCommit(win, X90R('q2'), '0.152', '0.152');
  win.AllValues.switchPane('grid');
  await tick(10);
  const a = tcell(win, 'q2', 'x90');
  ok(a.value === '0.152' && a.getAttribute('data-orig') === '0.152' && a.classList.contains('bulk-cell-modified'),
     'an alias Table cell lands the leaf the Flat View edited (' + a.value + ')');

  // Apply all (the chunked path) lands in the Table too
  win.AllValues.switchPane('allvalues');
  await tick(30);
  win._display[ANH('q3')] = '185,000,000';
  let i3 = flatInput(win, ANH('q3'));
  if (!i3) { expandFor(win, ANH('q3')); i3 = flatInput(win, ANH('q3')); }
  i3.value = '185000000';
  i3.dispatchEvent(new win.Event('input', { bubbles: true }));
  win.document.getElementById('av-apply').click();
  await tick(40);
  win.AllValues.switchPane('grid');
  await tick(10);
  ok(tcell(win, 'q3', 'anh').value === '185,000,000' && tcell(win, 'q3', 'anh').getAttribute('data-orig') === '185,000,000',
     'Flat "Apply all" lands in the Table as well (' + tcell(win, 'q3', 'anh').value + ')');

  ok(win._log.ajax.length === 0, 'no full /bulk re-GET was needed (' + win._log.ajax.join(', ') + ')');
  ok(win._log.resync.length === 0, 'and no resync was scheduled for repaintable cells');

  // a change the patch cannot repaint honestly -> resync on the next show
  win.AllValues.switchPane('allvalues');
  await tick(30);
  await flatCommit(win, T1('q2'), '0.0002', '0.0002');
  ok(win._log.resync.length === 0, 'the fallback waits until the Table is shown');
  win.AllValues.switchPane('grid');
  await tick(10);
  ok(win._log.resync.length === 1, 'a stored-as-text cell the patch cannot promise resyncs the Table on show ('
     + win._log.resync.length + ')');
  win.AllValues.switchPane('allvalues');
  await tick(30);
  win.AllValues.switchPane('grid');
  ok(win._log.resync.length === 1, 'once: the stale flag is consumed');

  // review follow-up: a list/matrix ELEMENT (Flat's ▦ rows) has no Table cell
  // of its own, but the Table's list cell shows the whole matrix -- the patch
  // cannot repaint it, so it must count as uncovered (resync on show), never
  // as a path no grid holds (which left '[[0.968,…' on screen until F5)
  win.AllValues.switchPane('allvalues');
  await tick(30);
  await flatCommit(win, CM('q2') + '.0.0', '0.5', '0.5');
  win.AllValues.switchPane('grid');
  await tick(10);
  ok(win._log.resync.length === 2, 'a matrix-element Flat edit resyncs the Table on show ('
     + win._log.resync.length + ')');
  ok(win.BulkEdit.revertPaths([{ dot_path: CM('q1') + '.1.0', old_value_disp: '0.1' }]).uncovered.length === 1,
     'and the grid reports the element as uncovered (so an undo repaint resyncs too)');
  ok(win.BulkEdit.revertPaths([{ dot_path: 'qubits.q1.resonator.depletion_time', old_value_disp: '1' }]).missing === 1,
     'a path no cell or list holds stays missing (no needless 2.4 s rebuild)');

  if (fails === 0) console.log('all checks passed (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})().catch(function (e) {
  console.error('harness error: ' + (e && e.stack || e));
  process.exit(1);
});
