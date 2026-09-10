// Two grids, one search box — and one flag on it silenced all but the first.
//
// `pair-edit.js` became a factory so every collection a chip carries gets a
// grid (customer, 2026-09-10: their `twpas` had none). The factory works; what
// it quietly broke is a thing this file's own comment already warns about for a
// DIFFERENT element — `#bulk-search` is ONE element shared by every grid, and
// the mount guarded its listener with `search._pairBound`. Whichever instance
// mounted first claimed the flag; every other grid stopped filtering. Measured
// in real Chrome the moment a discovered collection mounted before the pair
// grid: the pair table stayed at 34 of 34 columns for every query typed.
//
// Same shape as docs/141 §4ad (the two grids sharing a scroll flag on
// #table-pane) and the same fix grid-virt.js already uses:
// `'_virtScrollBound_' + styleId`. A source grep would not have caught it —
// the flag was there, it was just the wrong scope — so this MOUNTS two
// instances and types in the shared box.
//
// Run: node tests/entity_grid_selfcheck.cjs   (needs jsdom)
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
const PAIR_JS = fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

/* Two grids whose columns do NOT overlap, so a query can only be answered by
   the grid that owns it — which is what makes "the other one is filtering too"
   observable rather than assumed. */
const GRIDS = [
  { gid: 'bulk-pair', rowattr: 'data-pair', rows: ['q1-2', 'q2-3'],
    cols: [{ key: 'general__detuning', label: 'detuning' },
           { key: 'general__moving_qubit', label: 'moving_qubit' }] },
  { gid: 'bulk-e_twpas', rowattr: 'data-entity', rows: ['twpa1'],
    cols: [{ key: 'general__pump_amplitude', label: 'pump_amplitude' },
           { key: 'general__pump_frequency', label: 'pump_frequency' }] },
];

function gridHtml(g) {
  const heads = g.cols.map((c, i) =>
    '<th scope="col" class="bulk-col-head ck-' + i + '" data-col-key="' + c.key
    + '" data-section="General" data-maxlen="12">'
    + '<span class="bulk-col-label">' + c.label + '</span></th>').join('');
  const rows = g.rows.map(function (id) {
    const tds = g.cols.map((c, i) =>
      '<td class="bulk-td ck-' + i + '" data-col-key="' + c.key + '">'
      + '<input type="text" class="bulk-cell" value="1" data-orig="1"'
      + ' data-dot-path="x.' + id + '.' + c.key + '" data-resolved="x.' + id + '.' + c.key
      + '" size="10"></td>').join('');
    return '<tr data-qubit="' + id + '" ' + g.rowattr + '="' + id + '">'
      + '<th class="bulk-rowhead" scope="row" data-col-key="__id__">' + id + '</th>'
      + tds + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>'
      + '<span class="bulk-row-error" hidden></span></td></tr>';
  }).join('');
  return '<div class="bulk-pair-divider" id="' + g.gid + '-divider">'
    + '<div class="bulk-colvis-menu" id="' + g.gid + '-colvis-menu"></div>'
    + '<span id="' + g.gid + '-search-count"></span>'
    + '<span id="' + g.gid + '-dirty-count"></span>'
    + '<button id="' + g.gid + '-apply-all" disabled></button>'
    + '<button id="' + g.gid + '-apply-sync" disabled></button>'
    + '<button id="' + g.gid + '-reset" disabled></button></div>'
    + '<div class="bulk-table-wrap"><table class="bulk-table" id="' + g.gid + '-table">'
    + '<thead><tr class="bulk-head-row">'
    + '<th class="bulk-corner" data-col-key="__id__"></th>' + heads
    + '<th class="bulk-apply-col"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="bulk-panel">'
  + '<div class="bulk-toolbar"><input type="search" id="bulk-search"></div>'
  + GRIDS.map(gridHtml).join('')
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
global.window = win; global.document = win.document;
global.CSS = win.CSS;                       // docs/125: bridge, or it throws
win.htmx = { ajax() {}, trigger() {}, process() {} };
win.fetch = function () { return Promise.reject(new Error('no fetch expected')); };
win.showToast = function () {};
win.LiveEditUndo = { record: function () {} };
win.__bulkSearchDebounce = 1;               // the mount reads this
new win.Function(GRID_VIRT_JS).call(win);
new win.Function(PAIR_JS).call(win);

ok(typeof win.makeEntityGrid === 'function', '0a the factory is exported');
ok(!!win.BulkPairEdit && typeof win.BulkPairEdit.mount === 'function',
   '0b the pair grid is still its own instance');

/* The order that broke it: a DISCOVERED grid mounts first, as the template
   renders them. Before the fix this claimed `#bulk-search._pairBound` and the
   pair grid below never bound a listener at all. */
const twpa = win.makeEntityGrid('e_twpas', 'TWPAs');
twpa.mount(GRIDS[1].cols.map(function (c) {
  return { key: c.key, label: c.label, section: 'General', unit: '', default_on: true,
           editable: true, kind: 'scalar', maxlen: 12 };
}));
win.BulkPairEdit.mount(GRIDS[0].cols.map(function (c) {
  return { key: c.key, label: c.label, section: 'General', unit: '', default_on: true,
           editable: true, kind: 'scalar', maxlen: 12 };
}));
ok(win.EntityGrids && win.EntityGrids.e_twpas === twpa,
   '0c the instance is registered under its own key');

function shownCols(gid) {
  const t = win.document.getElementById(gid + '-table');
  const heads = Array.prototype.slice.call(t.querySelectorAll('thead .bulk-col-head'));
  return heads.filter(function (h) {
    return !h.classList.contains('bulk-col-hidden')
        && !h.classList.contains('bulk-search-hidden');
  }).length;
}
function type(q) {
  const s = win.document.getElementById('bulk-search');
  s.value = q;
  s.dispatchEvent(new win.Event('input', { bubbles: true }));
}
function settle() {
  return new Promise(function (res) { setTimeout(res, 60); });
}

(async function () {
  ok(shownCols('bulk-pair') === 2 && shownCols('bulk-e_twpas') === 2,
     'A1: both grids start whole');

  type('pump');
  await settle();
  ok(shownCols('bulk-e_twpas') === 2,
     'A2: the grid that owns the word keeps its columns ('
     + shownCols('bulk-e_twpas') + ')');
  ok(shownCols('bulk-pair') === 0,
     'A3: the OTHER grid narrows too — one box filters every grid. Before the '
     + 'fix the first instance to mount claimed `#bulk-search._pairBound` and '
     + 'this stayed at 2 for every query typed. Got '
     + shownCols('bulk-pair'));

  type('detuning');
  await settle();
  ok(shownCols('bulk-pair') === 1, 'A4: ...and it answers its own word ('
     + shownCols('bulk-pair') + ')');
  ok(shownCols('bulk-e_twpas') === 0, 'A5: while the first grid narrows in turn');

  type('');
  await settle();
  ok(shownCols('bulk-pair') === 2 && shownCols('bulk-e_twpas') === 2,
     'A6: clearing the box restores both');

  /* Each grid writes its OWN count element, with its OWN noun. A shared
     element here would have one grid reporting the other's numbers. */
  type('pump');
  await settle();
  const pc = win.document.getElementById('bulk-pair-search-count').textContent;
  const tc = win.document.getElementById('bulk-e_twpas-search-count').textContent;
  ok(/pairs/.test(pc), 'B1: the pair grid counts in pairs: ' + JSON.stringify(pc));
  ok(/TWPAs/.test(tc), 'B2: the TWPA grid counts in TWPAs: ' + JSON.stringify(tc));
  ok(pc !== tc, 'B3: and the two counts are not the same element');

  /* The per-instance flags themselves, since they are the mechanism. */
  const box = win.document.getElementById('bulk-search');
  ok(box['_searchBound_bulk-pair'] === true && box['_searchBound_bulk-e_twpas'] === true,
     'C1: the shared box carries ONE flag per instance, not one flag');
  ok(win['_bulkNavGuard_bulk-pair'] === true
     && win['_bulkNavGuard_bulk-e_twpas'] === true,
     'C2: ...and so does the unapplied-edits nav guard');

  if (fails === 0) console.log('all checks passed (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})().catch(function (e) {
  console.error('harness error: ' + (e && e.stack || e));
  process.exit(1);
});
