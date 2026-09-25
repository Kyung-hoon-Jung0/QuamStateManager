/* QA datasets-r2-30: the Datasets Qubits / Pairs filter pickers show each
 * qubit the way the runs and the chip SPELL it ('qA1', 'qA1-A2'), not the
 * lower-cased match key ('qa1', 'qa1-a2').  The lower-case key stays the
 * filter identity (data-qubit / data-pair, qubitFilter / pairFilter), so
 * ticking a box still filters case-insensitively.
 *
 * Drives the REAL shipped dataset-virtual.js under jsdom.
 * Run: node tests/ds_picker_labels_selfcheck.cjs  (driven by
 *      tests/test_ds_picker_labels.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const NODE_SET_INTERVAL = global.setInterval;

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('FAIL: ' + msg); fails++; }
}
function eqList(got, want, msg) {
  ok(JSON.stringify(got) === JSON.stringify(want),
     msg + '  [got ' + JSON.stringify(got) + ']');
}

function mkRow(id, q, p) {
  return { id: id, exp: 'rabi', date: '2026-09-01', time: '01:00:00',
           q: q, p: p, oc: {}, metric: '', bm: false, tags: [],
           status: 'successful', dur: 1, note: '', parent: null, hs: false,
           sm: {}, pm: {}, f: 'f1' };
}

function boot(rows) {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-digest-band"></div>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <div id="sort-filter-grid">
        <div id="sort-col-badges"></div>
        <div class="sort-filter-section"><div id="sort-fit-badges"></div></div>
        <div class="sort-filter-section"><div id="sort-param-badges"></div></div>
        <div id="sort-qubit-menu"></div><span id="sort-qubit-summary"></span>
        <div id="sort-pair-menu"></div><span id="sort-pair-summary"></span>
      </div>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(rows)}</script>
      <div id="datasets-scroll" style="height:900px">
        <table>
          <colgroup id="datasets-colgroup"></colgroup>
          <thead id="datasets-thead"></thead>
          <tbody id="datasets-tbody"></tbody>
        </table>
      </div>
    </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
  const w = dom.window;
  w.requestAnimationFrame = w.requestAnimationFrame || (cb => setTimeout(cb, 0));
  w.cancelAnimationFrame = w.cancelAnimationFrame || (id => clearTimeout(id));
  global.window = w;
  global.CSS = w.CSS;
  global.document = w.document;
  global.Event = w.Event;
  global.CustomEvent = w.CustomEvent;
  global.KeyboardEvent = w.KeyboardEvent;
  global.MouseEvent = w.MouseEvent;
  global.requestAnimationFrame = w.requestAnimationFrame;
  global.cancelAnimationFrame = w.cancelAnimationFrame;
  global.localStorage = w.localStorage;
  const rec = function (fn, ms) { return NODE_SET_INTERVAL(fn, ms); };
  w.setInterval = rec; global.setInterval = rec;
  const stub = () => new Promise(() => {});
  w.fetch = stub; global.fetch = stub;
  w.htmx = { ajax: () => Promise.resolve() }; global.htmx = w.htmx;
  w.eval(fs.readFileSync(path.join(STATIC, 'dataset-virtual.js'), 'utf8'));
  w.DatasetVirtual.init();
  return w;
}

function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

function labels(w, menuId, attr) {
  return Array.prototype.map.call(
    w.document.querySelectorAll('#' + menuId + ' input[' + attr + ']'),
    cb => ({ key: cb.getAttribute(attr), label: cb.parentNode.textContent.trim() }));
}
function renderedIds(w) {
  return Array.prototype.map.call(
    w.document.querySelectorAll('#datasets-tbody tr[data-id]'),
    tr => tr.getAttribute('data-id'));
}

(async () => {
  const w = boot([
    mkRow(101, ['qA1'], ['qA1-A2']),
    mkRow(102, ['qA2'], ['qA2-A3']),
    mkRow(103, ['q1'], ['q1-q2']),
  ]);
  await tick();

  const qs = labels(w, 'sort-qubit-menu', 'data-qubit');
  eqList(qs.map(x => x.label), ['q1', 'qA1', 'qA2'],
         'Qubits picker labels read as the runs spell them (qA1, not qa1)');
  eqList(qs.map(x => x.key), ['q1', 'qa1', 'qa2'],
         'the picker keys (data-qubit) stay the lower-case match identity');

  const ps = labels(w, 'sort-pair-menu', 'data-pair');
  eqList(ps.map(x => x.label), ['q1-q2', 'qA1-A2', 'qA2-A3'],
         'Pairs picker labels read as the runs spell them (qA1-A2, not qa1-a2)');
  eqList(ps.map(x => x.key), ['q1-q2', 'qa1-a2', 'qa2-a3'],
         'the picker keys (data-pair) stay the lower-case match identity');

  // Ticking 'qA1' still filters to the qA1 run (the key did not change case).
  const cb = w.document.querySelector('#sort-qubit-menu input[data-qubit="qa1"]');
  ok(!!cb, 'the qA1 checkbox exists');
  if (cb) {
    cb.checked = true;
    cb.dispatchEvent(new w.Event('change', { bubbles: true }));
    await tick(300);
    eqList(renderedIds(w), ['f1:101'], 'ticking qA1 filters the table to the qA1 run');
    // and a rebuilt picker keeps both the tick and the spelling
    const after = labels(w, 'sort-qubit-menu', 'data-qubit');
    ok(after.some(x => x.label === 'qA1'), 'the rebuilt picker still spells qA1');
  }

  const pcb = w.document.querySelector('#sort-pair-menu input[data-pair="qa2-a3"]');
  ok(!!pcb, 'the qA2-A3 checkbox exists');

  process.exit(fails ? 1 : 0);
})();
