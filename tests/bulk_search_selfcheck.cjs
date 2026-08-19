/* jsdom behavioral check for the Live State Edit search performance work
 * (audit: "typing keywords in Live Edit is slow" — un-debounced per-keystroke
 * full-table rescans, ×2 via the pair grid's second listener).
 *
 * Pins:
 *  A. DEBOUNCE — an input keystroke does NOT filter synchronously; the filter
 *     lands after the debounce window (one pass per typing pause).
 *  B. Correctness through the HAYSTACK CACHE — repeated searches reuse cached
 *     cell text and still filter rows/columns correctly.
 *  C. INVALIDATION — editing a cell (input event) drops the cache, so a
 *     search for the NEW value finds the row (the cache's stale-text risk).
 *  D. Sorting reorders rows without staling the cache (row-ELEMENT keyed).
 *
 * Run:  node tests/bulk_search_selfcheck.cjs   (driven by test_live_edit_search_perf.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'bulk-edit.js');

const COLS = [
  { key: 'f_01', label: 'f01', section: 'Qubit', unit: 'Hz', default_on: true },
  { key: 'T1', label: 'T1', section: 'Qubit', unit: 's', default_on: true },
  // A real chip's pair-gate column carries the PARTNER qubit's id in `search`
  // (folded header, docs/85). That made every qubit-id token hit both axes.
  { key: 'cz_amp', label: 'cz amp', section: 'Gate', unit: '', default_on: true,
    search: 'cz_SNZ_flux_pulse_q10 cz_SNZ_flux_pulse_q2' },
];
function cellTd(colKey, qid, val) {
  return '<td class="bulk-td" data-col-key="' + colKey + '">' +
    '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"' +
    ' data-dot-path="qubits.' + qid + '.' + colKey + '" data-resolved="qubits.' + qid + '.' + colKey + '"></td>';
}
function row(qid, f01, t1) {
  return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>' +
    cellTd('f_01', qid, f01) + cellTd('T1', qid, t1) + cellTd('cz_amp', qid, '0.1') +
    '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>' +
    '<span class="bulk-row-error" hidden></span></td></tr>';
}
const HTML = '<!doctype html><html><body><div id="bulk-panel">' +
  '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>' +
  '<button id="bulk-qubit-pill" hidden></button>' +
  '<input id="bulk-search"><span id="bulk-search-count"></span>' +
  '<button id="bulk-dyncol-hint" hidden></button>' +
  '<span id="bulk-dirty-count"></span>' +
  '<button id="bulk-apply-all"></button><button id="bulk-reset"></button>' +
  '<div class="bulk-chipbar" id="bulk-chipbar">' +
  '<button type="button" class="bulk-chip-mode" id="bulk-chip-mode" data-mode="and">AND</button>' +
  '<span class="bulk-chip-scroll">' +
  '<button type="button" class="bulk-chip" data-chip-term="t1" aria-pressed="false">T1</button>' +
  '</span>' +
  '<span class="bulk-chip-offer" id="bulk-chip-offer" hidden>' +
  '<span class="bulk-chip-offer-text">No matches — try OR?</span>' +
  '<button type="button" class="bulk-chip-offer-yes" id="bulk-chip-offer-yes">Yes</button>' +
  '<button type="button" class="bulk-chip-offer-no" id="bulk-chip-offer-no">No</button>' +
  '</span></div>' +
  '<div class="bulk-table-wrap"><table id="bulk-table"><thead>' +
  '<tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit<span class="bulk-sort-caret"></span></th></tr>' +
  '<tr class="bulk-head-row">' +
  COLS.map(function (c) {
    return '<th class="bulk-col-head" data-col-key="' + c.key + '"><span class="bulk-col-label">' +
      c.label + '</span><span class="bulk-sort-caret"></span><span class="bulk-col-stats" data-col-stats="' + c.key + '"></span></th>';
  }).join('') + '</tr></thead><tbody>' +
  row('q1', '5,100,000,000', '0.000021') +
  row('q2', '5,200,000,000', '0.000034') +
  row('q10', '6,000,000,000', '0.000055') +
  '</tbody></table></div></div></body></html>';

const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
w.eval(fs.readFileSync(SRC, 'utf8'));

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}
function visibleRows() {
  return Array.prototype.slice.call(w.document.querySelectorAll('#bulk-table tbody tr'))
    .filter(function (r) { return !r.classList.contains('bulk-row-hidden'); })
    .map(function (r) { return r.getAttribute('data-qubit'); });
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

(async function () {
  w.BulkEdit.mount(COLS, { bands: {} }, [], { chip: 'testchip', qubits: [
    { id: 'q1', grid: null }, { id: 'q2', grid: null }, { id: 'q10', grid: null }] });

  const search = w.document.getElementById('bulk-search');
  function type(q) {
    search.value = q;
    search.dispatchEvent(new w.Event('input', { bubbles: true }));
  }

  // A. debounce: no synchronous filtering on keystroke
  type('q10');
  check('A1 keystroke does not filter synchronously', visibleRows().length === 3,
        visibleRows().join(','));
  await sleep(300);
  check('A2 filter lands after the debounce', visibleRows().join(',') === 'q10',
        visibleRows().join(','));

  // B. cached haystacks still filter correctly on repeated searches
  type('0.000034');
  await sleep(300);
  check('B1 value search via cache', visibleRows().join(',') === 'q2', visibleRows().join(','));
  type('');
  await sleep(300);
  check('B2 clearing restores all rows', visibleRows().length === 3, visibleRows().join(','));

  // C. cache invalidation: edit a cell, then search for the NEW text
  const q1cell = w.document.querySelector('tr[data-qubit="q1"] .bulk-cell');
  q1cell.value = '7,777,000,000';
  q1cell.dispatchEvent(new w.Event('input', { bubbles: true }));   // → _refreshGlobal → cache drop
  type('7777000000');
  await sleep(300);
  check('C1 edited value is searchable (cache invalidated)',
        visibleRows().join(',') === 'q1', visibleRows().join(','));

  // D. sorting must not stale the cache (row-element-keyed WeakMap)
  type('');
  await sleep(300);
  w.BulkEdit.sort('__id__');   // natural id sort reorders rows
  type('0.000055');
  await sleep(300);
  check('D1 search correct after sort', visibleRows().join(',') === 'q10', visibleRows().join(','));

  // E. a qubit-id token must filter ROWS even though a column's search text
  //    names that qubit. Before the fix both axes read it as "neutral", so on
  //    the real 21-qubit chip typing a qubit id filtered nothing at all.
  type('q10');
  await sleep(300);
  check('E1 an id named by a column still narrows rows',
        visibleRows().join(',') === 'q10', visibleRows().join(','));
  const colsShown = Array.prototype.slice
    .call(w.document.querySelectorAll('#bulk-table th.bulk-col-head'))
    .filter(function (h) { return !h.classList.contains('bulk-search-hidden'); }).length;
  check('E2 an id token leaves every column visible', colsShown === 3, String(colsShown));
  // a token that only occurs INSIDE an id keeps the column reading
  type('cz_snz');
  await sleep(300);
  check('E3 a column-only token still filters columns, not rows',
        visibleRows().length === 3, visibleRows().join(','));
  type('');
  await sleep(300);

  // F. docs/126 ③ — custom patches + stylesheet-based td hiding.
  type('');
  await sleep(300);
  const barEl = w.document.getElementById('bulk-chipbar');
  check('F1 the + patch button is injected',
        !!barEl.querySelector('.bulk-chip-add'));

  // add a custom patch through the real add flow
  barEl.querySelector('.bulk-chip-add').dispatchEvent(
    new w.Event('click', { bubbles: true }));
  const addInp = barEl.querySelector('.bulk-chip-add-input');
  check('F2 + opens the inline input', !!addInp);
  addInp.value = 'cz';
  addInp.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await sleep(300);
  const czChip = barEl.querySelector('.bulk-chip-custom[data-chip-term="cz"]');
  check('F3 the custom patch chip exists and is active',
        !!czChip && czChip.classList.contains('active'));
  check('F4 it landed in the search box', /(^|[ ])cz([ ]|$)/.test(search.value),
        'value=' + JSON.stringify(search.value));
  check('F5 persisted to localStorage',
        /"cz"/.test(w.localStorage.getItem('quam_bulk_custom_chips') || ''));
  check('F6 and it filters like any chip (cz_amp column survives)',
        !w.document.querySelector('th[data-col-key="cz_amp"]').classList.contains('bulk-search-hidden')
        && w.document.querySelector('th[data-col-key="f_01"]').classList.contains('bulk-search-hidden'));

  // stylesheet-based td hiding (docs/126 ③ perf): tds carry NO class; the
  // generated sheet hides them, and cell nav keys off _searchHiddenKeys.
  const styleEl = w.document.getElementById('bulk-search-hide-style');
  check('F7 hidden columns ride ONE stylesheet, not per-td classes',
        !!styleEl && styleEl.textContent.indexOf('td[data-col-key="f_01"]') >= 0
        && !w.document.querySelector('td[data-col-key="f_01"]').classList.contains('bulk-search-hidden'));

  // × removes the custom patch and its store entry
  czChip.querySelector('.bulk-chip-x').dispatchEvent(
    new w.Event('click', { bubbles: true }));
  await sleep(300);
  check('F8 × removes the patch from the bar and the store',
        !barEl.querySelector('.bulk-chip-custom[data-chip-term="cz"]')
        && !/"cz"/.test(w.localStorage.getItem('quam_bulk_custom_chips') || ''));
  check('F9 removal also clears the filter (all columns back)',
        !w.document.querySelector('th[data-col-key="f_01"]').classList.contains('bulk-search-hidden'),
        'value=' + JSON.stringify(search.value)
        + ' style=' + JSON.stringify((w.document.getElementById('bulk-search-hide-style') || {}).textContent));

  if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
