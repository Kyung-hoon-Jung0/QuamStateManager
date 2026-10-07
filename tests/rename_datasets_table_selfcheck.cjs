/* docs/296 -- the Datasets table after a Re-generate rename, in the REAL
 * dataset-virtual.js under jsdom: a run saved before the rename carries `qn`
 * (its qubits by today's names) beside `q` (as recorded). Searching a qubit by
 * today's name finds every run of that qubit -- and never a run that recorded
 * that name for another qubit -- free text still finds the recorded spelling,
 * and the cell shows today's name with the recorded one beside it.
 *
 * Run: node tests/rename_datasets_table_selfcheck.cjs  (driven by tests/test_rename_datasets_table.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                      'dataset-virtual.js');
const NODE_SET_INTERVAL = global.setInterval;

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('FAIL: ' + msg); fails++; }
}

// run 1-2 saved before a rename (q1 then was today's q0), 3-4 after it
function row(id, q, qn) {
  const r = { id: id, exp: 'rabi', date: '2026-09-2' + id, time: '0' + id + ':00:00',
              q: q, p: [], oc: {}, metric: '', bm: false, tags: [], status: 'successful',
              dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' };
  if (qn) r.qn = qn;
  return r;
}
const ROWS = [row(1, ['q1'], ['q0']), row(2, ['q2'], ['q1']), row(3, ['q0'], null), row(4, ['q1'], null)];

function boot() {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <span class="sort-banner-summary" id="sort-banner-summary"></span>
      <div id="sort-filter-grid">
        <div id="sort-col-badges"></div>
        <div class="sort-filter-section"><div id="sort-fit-badges"></div></div>
        <div class="sort-filter-section"><div id="sort-param-badges"></div></div>
        <div id="sort-qubit-menu"></div><span id="sort-qubit-summary"></span>
        <div id="sort-pair-menu"></div><span id="sort-pair-summary"></span>
      </div>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(ROWS)}</script>
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
  w.htmx = { ajax: () => Promise.resolve() };
  global.htmx = w.htmx;
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.DatasetVirtual.init();
  return w;
}
function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }
function ids(w) {
  return Array.prototype.map.call(
    w.document.querySelectorAll('#datasets-tbody tr[data-id]'),
    tr => Number(String(tr.getAttribute('data-id')).split(':').pop()));
}
function click(w, el) {
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
}

(async () => {
  const w = boot();
  await tick();
  const doc = w.document;
  const s = doc.getElementById('dataset-search');
  async function search(text) {
    s.value = text;
    s.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);
    return JSON.stringify(ids(w).sort());
  }
  ok(await search('qubit:q0') === '[1,3]', 'a scoped qubit search finds the old run of q0 (named today): ' + JSON.stringify(ids(w)));
  ok(await search('qubit:q1') === '[2,4]', '...and q1 today is the run recorded as q2, never the old q1: ' + JSON.stringify(ids(w)));
  ok(await search('qubit:q2') === '[]', 'a qubit filter names a qubit today: no run measured q2 as named today: ' + JSON.stringify(ids(w)));
  ok(await search('q2') === '[2]', 'free text still finds the recorded spelling: ' + JSON.stringify(ids(w)));
  ok(await search('q1') === '[2,4]', 'a bare id that names a qubit today means that qubit, never the run that recorded it for another: ' + JSON.stringify(ids(w)));
  await search('');
  const cell = Array.prototype.map.call(doc.querySelectorAll('#datasets-tbody tr[data-id]'), tr => tr.textContent).join('|');
  ok(/q0 \(as q1\)/.test(cell), 'the old run shows the name today with the recorded one: ' + cell.slice(0, 300));
  process.exit(fails ? 1 : 0);
})();
