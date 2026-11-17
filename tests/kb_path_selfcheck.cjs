/* P1-7 (UX round) -- the keyboard path through Live State Edit, in the REAL
 * bulk-edit.js + app.js under jsdom:
 *   A. "/" search then Enter (or Down) lands on the first matching grid cell
 *      -- even when the search was typed faster than its debounce;
 *   B. the quick-filter chip strip is one tab stop: one chip has tabindex 0,
 *      Left / Right / Home / End move the stop and the focus;
 *   C. Ctrl+Enter is Apply: the grid's Apply all when it has un-applied
 *      cells, else the top bar's Apply (a click), never from another field;
 *   D. "Skip to content" focuses the page pane;
 *   E. the Json tree's chip strip is one tab stop too (no grid bundle there);
 *   F. Ctrl+Enter from a Json tree value: not staged yet -> stages it only;
 *      staged -> the top bar's Apply (a click); plain Enter never applies.
 *
 * Run: node tests/kb_path_selfcheck.cjs   (driven by tests/test_v151_ux_keyboard_path.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const COLS = [
  { key: 'f_01', label: 'f01', section: 'Qubit', unit: 'Hz', default_on: true },
  { key: 'T1', label: 'T1', section: 'Qubit', unit: 's', default_on: true },
];
function cellTd(colKey, qid, val) {
  return '<td class="bulk-td" data-col-key="' + colKey + '">' +
    '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"' +
    ' data-dot-path="qubits.' + qid + '.' + colKey + '" data-resolved="qubits.' + qid + '.' + colKey + '"></td>';
}
function row(qid, f01, t1) {
  return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>' +
    cellTd('f_01', qid, f01) + cellTd('T1', qid, t1) +
    '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>' +
    '<span class="bulk-row-error" hidden></span></td></tr>';
}
const HTML = '<!doctype html><html><body>' +
  '<a href="#table-pane" class="skip-link" id="skip-link">Skip to content</a>' +
  '<div id="pending-tray"><button type="button" class="sync-control-act btn-apply-live" id="top-apply">Apply 1</button></div>' +
  '<input type="search" id="other-field">' +
  '<div id="table-pane"><div id="bulk-panel">' +
  '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>' +
  '<button id="bulk-qubit-pill" hidden></button>' +
  '<input type="search" id="bulk-search"><span id="bulk-search-count"></span>' +
  '<button id="bulk-dyncol-hint" hidden></button>' +
  '<span id="bulk-dirty-count"></span>' +
  '<button id="bulk-apply-all" disabled></button><button id="bulk-reset"></button>' +
  '<div class="bulk-chipbar" id="bulk-chipbar">' +
  '<button type="button" class="bulk-chip-mode" id="bulk-chip-mode" data-mode="and">AND</button>' +
  '<span class="bulk-chip-scroll">' +
  '<button type="button" class="bulk-chip" id="c1" data-chip-term="t1" aria-pressed="false" tabindex="0">T1</button>' +
  '<button type="button" class="bulk-chip" id="c2" data-chip-term="f_01" aria-pressed="false" tabindex="-1">f01</button>' +
  '<button type="button" class="bulk-chip" id="c3" data-chip-term="amp" aria-pressed="false" tabindex="-1">amp</button>' +
  '</span>' +
  '<span class="bulk-chip-offer" id="bulk-chip-offer" hidden><span class="bulk-chip-offer-text"></span>' +
  '<button type="button" class="bulk-chip-offer-yes" id="bulk-chip-offer-yes">Yes</button>' +
  '<button type="button" class="bulk-chip-offer-no" id="bulk-chip-offer-no">No</button></span></div>' +
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
  '</tbody></table></div></div>' +
  '<input type="search" id="explorer-search">' +
  '<div class="bulk-chipbar explorer-chipbar" id="explorer-chipbar"></div>' +
  '<div id="xtree"></div></div></body></html>';

const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/bulk', pretendToBeVisual: true });
const w = dom.window;
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
const edits = [];
w.fetch = (url, opts) => {
  if (String(url) !== '/field/edit') return new Promise(() => {});
  const b = new w.URLSearchParams(opts && opts.body);
  let v = b.get('value');
  try { v = JSON.parse(v); } catch (e) {}
  edits.push(b.get('dot_path') + '=' + JSON.stringify(v));
  return Promise.resolve({ ok: true, json: async () => ({ ok: true, stored: v, stored_kind: typeof v }) });
};
w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const doc = w.document;
function key(k, target, opts) {
  const ev = new w.KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, opts || {}));
  (target || doc).dispatchEvent(ev);
  return ev;
}

(async function () {
  w.BulkEdit.mount(COLS, { bands: {} }, [], { chip: 'testchip', qubits: [
    { id: 'q1', grid: null }, { id: 'q2', grid: null }, { id: 'q10', grid: null }] });
  const search = doc.getElementById('bulk-search');

  // A. search -> first matching cell, even before the debounce lands
  search.focus();
  search.value = 'q2';
  search.dispatchEvent(new w.Event('input', { bubbles: true }));
  const ev = key('Enter', search);
  ok(ev.defaultPrevented, 'Enter in the grid search is taken');
  const a = doc.activeElement;
  ok(a && a.classList.contains('bulk-cell') && a.getAttribute('data-dot-path') === 'qubits.q2.f_01',
     'Enter lands on the first matching cell: ' + (a && a.getAttribute('data-dot-path')));
  search.focus();
  search.value = 'q10';
  search.dispatchEvent(new w.Event('input', { bubbles: true }));
  key('ArrowDown', search);
  ok(doc.activeElement.getAttribute('data-dot-path') === 'qubits.q10.f_01', 'Down does the same');
  search.focus();
  search.value = 'zzz-nothing';
  search.dispatchEvent(new w.Event('input', { bubbles: true }));
  const ev3 = key('Enter', search);
  ok(!ev3.defaultPrevented && doc.activeElement === search, 'no match: focus stays in the search');

  // B. the chip strip: one tab stop, arrows move it (ChipBar's own "+" chip is
  //    part of the strip, so the last stop is whatever chip ends it)
  w._bulkChipRoveInit();
  const strip = () => Array.from(doc.querySelectorAll('#bulk-chipbar .bulk-chip')).filter((c) => !c.hidden);
  const stops = () => strip().filter((c) => c.getAttribute('tabindex') === '0');
  ok(stops().length === 1 && stops()[0].id === 'c1', 'one chip is the tab stop of the strip');
  const c1 = doc.getElementById('c1'), c2 = doc.getElementById('c2');
  c1.focus();
  key('ArrowRight', c1);
  ok(doc.activeElement === c2 && stops().length === 1 && stops()[0] === c2, 'Right moves focus and the stop');
  key('End', c2);
  const last = strip()[strip().length - 1];
  ok(doc.activeElement === last && stops().length === 1 && stops()[0] === last, 'End goes to the last chip');
  key('Home', last);
  ok(doc.activeElement === c1 && stops().length === 1 && stops()[0] === c1, 'Home goes to the first chip');
  key('ArrowLeft', c1);
  ok(doc.activeElement === c1, 'Left at the first chip stays');

  // C. Ctrl+Enter is Apply
  let gridApplies = 0, topApplies = 0;
  const all = doc.getElementById('bulk-apply-all');
  all.addEventListener('click', () => gridApplies++);
  doc.getElementById('top-apply').addEventListener('click', () => topApplies++);
  w.BulkEdit.resetDirty && w.BulkEdit.resetDirty();
  all.disabled = true;
  const cell = doc.querySelector('tr[data-qubit="q1"] .bulk-cell');
  cell.focus();
  key('Enter', cell, { ctrlKey: true });
  ok(topApplies === 1 && gridApplies === 0, 'nothing un-applied in the grid: Ctrl+Enter presses the top-bar Apply');
  all.disabled = false;
  key('Enter', cell, { ctrlKey: true });
  ok(gridApplies === 1 && topApplies === 1, 'un-applied cells: Ctrl+Enter presses Apply all first');
  all.disabled = true;
  const other = doc.getElementById('other-field');
  other.focus();
  key('Enter', other, { ctrlKey: true });
  ok(topApplies === 1, 'never from another text field');

  // D. skip link
  doc.getElementById('skip-link').dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(doc.activeElement === doc.getElementById('table-pane'), 'Skip to content focuses the page pane');

  // E. the Json tree's chip strip (rendered by ExplorerChips, no grid bundle)
  w.ExplorerChips.mount('explorer-chipbar', [{ freq: 1, flux: 2, amp: 3 }]);
  const xstrip = () => Array.from(doc.querySelectorAll('#explorer-chipbar .bulk-chip')).filter((c) => !c.hidden);
  const xstops = () => xstrip().filter((c) => c.getAttribute('tabindex') !== '-1');
  ok(xstrip().length >= 4 && xstops().length === 1 && xstops()[0] === xstrip()[0],
     'the Json tree chip strip is one tab stop: ' + xstops().length + ' of ' + xstrip().length);
  xstrip()[0].focus();
  key('ArrowRight', xstrip()[0]);
  ok(doc.activeElement === xstrip()[1] && xstops().length === 1 && xstops()[0] === xstrip()[1],
     'Right moves focus and the stop in the Json tree strip');
  key('End', doc.activeElement);
  ok(doc.activeElement === xstrip()[xstrip().length - 1] && xstops().length === 1, 'End in the Json tree strip');

  // F. Ctrl+Enter from a Json tree value
  w.renderJsonTree('xtree', { a: 1, b: 2 }, { defaultDepth: 1, crud: true });
  const xrow = (p) => doc.querySelector('#xtree .tree-node[data-path="' + p + '"] > .tree-row');
  xrow('a').focus();
  const ed = doc.activeElement;
  ok(ed && ed.classList.contains('ui-tree-editor'), 'focusing a tree value mounts its editor');
  const top0 = topApplies;
  ed.value = '5';
  ed.dispatchEvent(new w.Event('input', { bubbles: true }));
  const evS = key('Enter', ed, { ctrlKey: true });
  await sleep(30);
  ok(evS.defaultPrevented && edits.indexOf('a=5') >= 0, 'Ctrl+Enter on typing not staged yet stages it: ' + edits);
  ok(topApplies === top0, 'and does not press Apply in the same stroke');
  ok(doc.activeElement === ed && ed.value === ed.defaultValue, 'the value is staged, the editor stays');
  key('Enter', ed);
  ok(topApplies === top0, 'plain Enter on a staged value never applies');
  const evA = key('Enter', ed, { ctrlKey: true });
  ok(topApplies === top0 + 1 && evA.defaultPrevented, 'Ctrl+Enter on a staged value presses the top-bar Apply');
  key('Enter', ed, { metaKey: true });
  ok(topApplies === top0 + 2, 'Cmd+Enter does the same');
  key('Enter', ed, { ctrlKey: true, altKey: true });
  ok(topApplies === top0 + 2, 'Ctrl+Alt+Enter is not the chord');

  console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
