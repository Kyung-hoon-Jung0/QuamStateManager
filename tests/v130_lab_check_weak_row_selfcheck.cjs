/* v1.3.0 B1 P2 #9: lab-check.js must not keep a swapped-out pane alive.
 *
 * LabCheck remembers the row the user last focused (the badge's fallback
 * anchor). It held that element STRONGLY, so after an htmx swap the whole old
 * pane stayed in memory until the next focus: a heap snapshot of the Datasets
 * run detail showed one stale pane (~1,600 detached DOM nodes) retained through
 * LabCheck -> _lastRow -> the focused Older-run button. The row is now held by
 * a WeakRef; the badge still anchors on it while it is connected.
 *
 * Drives the SHIPPED web/static/lab-check.js in a jsdom window, with the V8
 * garbage collector exposed (v8.setFlagsFromString), and checks with a WeakRef
 * of its own whether a focused-then-removed row can be collected.
 * Run: node tests/v130_lab_check_weak_row_selfcheck.cjs
 * Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('SKIP: jsdom not installed'); process.exit(2); }
const fs = require('fs');
const path = require('path');
const v8 = require('v8');
const vm = require('vm');
v8.setFlagsFromString('--expose_gc');
const gc = vm.runInNewContext('gc');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'lab-check.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

const dom = new JSDOM('<!doctype html><body><div id="host"></div></body>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/' });
const w = dom.window;
w.fetch = function (url) {
  if (url === '/field/lab-watch') return Promise.resolve({ status: 200, json: () => Promise.resolve({ lab: true }) });
  return new Promise(function () {});   // the edit stays unanswered: the badge stays
};
w.Element.prototype.getClientRects = function () { return this.isConnected ? [{ width: 10, height: 10 }] : []; };
w.eval(SRC);

// jsdom's selector engine (nwsapi) keeps the last element it matched from;
// matching the same selector from an element that stays flushes that cache, so
// only LabCheck's own reference can keep a removed row alive
const ROW_SEL = (SRC.match(/function _rowOf[\s\S]*?closest\('([^']+)'\)/) || [])[1];
const stay = w.document.createElement('span');
w.document.body.appendChild(stay);

// a pane with one row (built without innerHTML: jsdom's parser keeps its own
// references); returns a WeakRef to the row after the pane is removed
function paneRow(focus, keep) {
  const d = w.document;
  const pane = d.createElement('div');
  const table = d.createElement('table');
  const tr = d.createElement('tr');
  const td = d.createElement('td');
  const input = d.createElement('input');
  td.appendChild(input); tr.appendChild(td); table.appendChild(tr); pane.appendChild(table);
  d.getElementById('host').appendChild(pane);
  if (focus) input.dispatchEvent(new w.FocusEvent('focusin', { bubbles: true }));
  pane.remove();
  stay.closest(ROW_SEL);
  if (keep) keep.push(td);
  return new WeakRef(td);
}

async function collected(ref) {
  for (let i = 0; i < 4; i++) { await tick(); gc(); }
  return ref.deref() === undefined;
}

(async () => {
  ok((typeof w.LabCheck === 'object' || typeof w.LabCheck === 'function') && !!ROW_SEL,
     'lab-check.js loaded (window.LabCheck) and its row selector read: ' + ROW_SEL);
  // the pin first (jsdom's own caches see fewer elements before it): a row
  // LabCheck saw focused, then swapped out
  const focused = paneRow(true, null);
  ok(await collected(focused), 'a focused row in a swapped-out pane is collected (LabCheck holds it weakly)');
  // controls: the harness can tell retained from collected
  const plain = paneRow(false, null);
  ok(await collected(plain), 'control: a removed row nobody holds is collected (jsdom keeps no reference)');
  const held = [];
  const strong = paneRow(false, held);
  ok(!(await collected(strong)), 'control: a row the test itself holds survives gc');
  // the weak hold still anchors: an edit whose cell is not on the page puts
  // its badge at the row the user focused, while that row is connected
  {
    const d = w.document;
    const td = d.createElement('td');
    const input = d.createElement('input');
    td.appendChild(input);
    d.getElementById('host').appendChild(td);
    input.dispatchEvent(new w.FocusEvent('focusin', { bubbles: true }));
    w.fetch('/field/edit', { method: 'POST', body: 'dot_path=qubits.q9.not_on_page&value=1' });
    for (let i = 0; i < 50 && !d.querySelector('.lab-check-badge'); i++) await tick(5);
    const b = d.querySelector('.lab-check-badge');
    ok(!!b && b._anchor === td, 'the badge anchors at the focused row (weak hold dereferenced)');
  }
  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('v130_lab_check_weak_row_selfcheck: all checks passed (5 assertions)');
  process.exit(0);
})();
