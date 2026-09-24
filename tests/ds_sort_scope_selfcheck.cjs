/* QA datasets-r2-23 -- sorting by a fit metric that NONE of the filtered runs
 * carry, in the REAL dataset-virtual.js under jsdom. The summary said nothing
 * ("no values" was judged over the whole workspace, where a listed key always
 * has values) and the list flipped from newest-first to id-ascending (the
 * both-missing tie-break). Now: the note is judged over the rows in view and
 * follows the filter, and rows that all lack the key keep the default order.
 *
 * Run: node tests/ds_sort_scope_selfcheck.cjs  (driven by tests/test_ds_sort_scope.py)
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

const ROWS = [];
for (let id = 1; id <= 10; id++) {
  const withKey = id <= 5;                  // 1..5 carry f_x; 6..10 do not
  ROWS.push({
    id: id, exp: withKey ? 'rabi' : 'snz_landscape', date: '2026-09-2' + (id % 5),
    time: '0' + (id % 10) + ':00:00', q: ['q1'], p: [], oc: {}, metric: '',
    bm: false, tags: [], status: 'successful', dur: 1, note: '', parent: null,
    hs: false, sm: withKey ? { f_x: id * 1.5 } : {}, pm: {}, f: 'f1',
  });
}

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
  const sum = doc.getElementById('sort-banner-summary');
  s.value = 'snz_landscape';
  s.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(300);
  const before = ids(w);
  ok(JSON.stringify(before) === '[10,9,8,7,6]',
     'fixture: the search shows the 5 runs without f_x, newest first: ' + JSON.stringify(before));
  const badge = doc.querySelector('#sort-fit-badges [data-sort-key="f_x"]');
  ok(!!badge, 'fixture: the f_x fit badge is offered');
  click(w, badge);
  await tick(60);
  ok(/no values in these runs/.test(sum.textContent),
     'r2-23: sorting by a key none of the filtered runs carry says so: "' + sum.textContent + '"');
  ok(JSON.stringify(ids(w)) === JSON.stringify(before),
     'r2-23: ... and the list keeps its newest-first order (was flipped to id-ascending): '
     + JSON.stringify(ids(w)));
  for (let i = 0; i < 3; i++) {
    const agg = doc.querySelector('#sort-fit-badges [data-sort-agg]');
    if (agg) click(w, agg);
    await tick(60);
  }
  ok(JSON.stringify(ids(w)) === JSON.stringify(before),
     'r2-23: cycling first/max/min leaves that order alone: ' + JSON.stringify(ids(w)));
  ok(/no values in these runs/.test(sum.textContent), 'r2-23: the note survives the agg cycle');
  // clear the search: the key has values again -> the note goes, values sort first
  s.value = '';
  s.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(300);
  ok(!/no values/.test(sum.textContent),
     'r2-23: the note follows the filter (gone once runs with f_x are in view): "' + sum.textContent + '"');
  ok(JSON.stringify(ids(w)) === '[5,4,3,2,1,10,9,8,7,6]',
     'the runs with f_x sort by it (desc), the rest sink below, newest first: ' + JSON.stringify(ids(w)));

  process.exit(fails ? 1 : 0);
})();
