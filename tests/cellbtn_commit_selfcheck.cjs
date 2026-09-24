/* jsdom selfcheck: the value-history 🕘 (#fh-cellbtn) follows a COMMIT echo.
 *
 * QA liveedit-r2-22. The icon's `left` sits at the value's tail (docs/20
 * r11) and was repositioned on focus and on every `input` event only. Enter
 * keeps focus in the cell and the server's echo rewrites the value
 * programmatically -- '1700' comes back grouped as '1,700' -- which fires no
 * `input`, so the icon stayed at the TYPED text's tail and sat on the last
 * digit ("1,70🕘"); a click on the cell's middle opened Value History.
 *
 * Pins, for BOTH grids (bulk-edit.js and pair-edit.js share the idiom):
 *   1. an Enter commit whose echo changes the focused cell's text moves the
 *      icon to the NEW tail (style.left grows)
 *   2. an echo that leaves the text alone costs no re-measure (the docs/120
 *      item-24 rule: a forced layout only where it buys something)
 *
 * Run: node tests/cellbtn_commit_selfcheck.cjs   (driven by tests/test_tab_focus.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function row(attr, id, dot, val) {
  return '<tr ' + attr + '="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">' + id + '</th>'
    + '<td class="bulk-td" data-col-key="len">'
    + '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"'
    + ' data-dot-path="' + dot + '" data-resolved="' + dot + '"></td>'
    + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>'
    + '<span class="bulk-row-error" hidden></span></td></tr>';
}
const HEAD = '<thead><tr class="bulk-head-row"><th class="bulk-corner" data-col-key="__id__"></th>'
  + '<th class="bulk-col-head" data-col-key="len"><span class="bulk-col-label">len</span></th></tr></thead>';
const HTML = '<div id="table-pane"><div class="bulk-panel">'
  + '<input type="search" id="bulk-search"><span id="bulk-search-count"></span>'
  + '<span id="bulk-dirty-count"></span>'
  + '<div class="bulk-table-wrap"><table id="bulk-table">' + HEAD + '<tbody>'
  + row('data-qubit', 'q3', 'qubits.q3.resonator.operations.readout.length', '1500')
  + '</tbody></table></div>'
  + '<div class="bulk-table-wrap bulk-pair-table-wrap"><table id="bulk-pair-table">' + HEAD + '<tbody>'
  + row('data-pair', 'q1-2', 'qubit_pairs.q1-2.macros.cz.flux_pulse_control.length', '1200')
  + '</tbody></table></div>'
  + '</div></div><input id="outside">';

const dom = new JSDOM('<!doctype html><html><body>' + HTML + '</body></html>',
  { url: 'http://localhost/bulk', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
// bridge every global the shipped code reads BARE (a Node realm does not
// expose window properties as globals -- docs/125 harness rule)
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = global.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };

// jsdom has no layout: give the cells a real width so the icon is not
// clamped to the same edge before and after (which would pass vacuously).
Object.defineProperty(window.HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return 160; } });
Object.defineProperty(window.HTMLElement.prototype, 'offsetLeft', { configurable: true, get() { return 0; } });

// the server's echo: what /field/edit-batch returns as the committed display
let echo = {};
window.fetch = global.fetch = function (url, opts) {
  if (url === '/field/edit-batch') {
    const body = JSON.parse(opts.body);
    const results = body.updates.map(function (u) {
      return { dot_path: u.dot_path, resolved_path: u.dot_path, applied: true,
               display: echo[u.value] != null ? echo[u.value] : String(u.value) };
    });
    return Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, results: results, tray_html: null }) });
  }
  return new Promise(() => {});
};

window.eval(read('app.js'));
window.eval(read('search-query.js'));
global.SearchQuery = window.SearchQuery;
window.eval(read('grid-virt.js'));
window.eval(read('bulk-edit.js'));
window.eval(read('pair-edit.js'));
const COLS = [{ key: 'len', label: 'len', section: 'S', unit: 'ns', default_on: true }];
window.BulkEdit.mount(COLS, { bands: {} }, [], { chip: 'c', chipKey: 'k', qubits: [] });
window.BulkPairEdit.mount(COLS);

const d = window.document;
let invalidates = 0;
const realInv = window.__cellBtnInvalidate;
ok(typeof realInv === 'function', 'precondition: app.js publishes __cellBtnInvalidate');
window.__cellBtnInvalidate = function () { invalidates++; return realInv.apply(this, arguments); };

async function commitCase(label, sel) {
  const c = d.querySelector(sel);
  c.focus();
  const btn = d.getElementById('fh-cellbtn');
  ok(!!btn && btn.parentElement === c.closest('td'), label + ': precondition: the icon docks in the focused cell');
  echo = { '1700': '1,700', '42': '42' };

  // 1. an echo that REWRITES the text moves the icon to the new tail
  c.value = '1700';
  c.dispatchEvent(new window.Event('input', { bubbles: true }));
  const typedLeft = parseFloat(btn.style.left);
  const n0 = invalidates;
  c.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await tick(30);
  ok(c.value === '1,700', label + ': precondition: the commit landed the server echo (got ' + c.value + ')');
  ok(d.activeElement === c, label + ': precondition: Enter keeps focus in the cell');
  const echoLeft = parseFloat(btn.style.left);
  ok(echoLeft > typedLeft,
     label + ': r2-22: the icon follows the echo to the new tail (typed ' + typedLeft + 'px, after commit ' + echoLeft + 'px)');
  ok(invalidates === n0 + 1, label + ': r2-22: re-measured exactly once for the echo (got ' + (invalidates - n0) + ')');

  // 2. an echo identical to the typed text costs no re-measure
  c.value = '42';
  c.dispatchEvent(new window.Event('input', { bubbles: true }));
  const n1 = invalidates;
  c.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await tick(30);
  ok(c.value === '42' && c.getAttribute('data-orig') === '42', label + ': precondition: the second commit landed');
  ok(invalidates === n1, label + ': an unchanged echo forces no layout read (docs/120 item 24)');
}

(async function () {
  await commitCase('qubit grid', '#bulk-table input.bulk-cell');
  await commitCase('pair grid', '#bulk-pair-table input.bulk-cell');
  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('cellbtn commit selfcheck: all checks passed (' + asserts + ' assertions)');
  process.exit(0);   // app.js leaves pollers armed
})().catch(function (e) { console.error(e); process.exit(1); });
