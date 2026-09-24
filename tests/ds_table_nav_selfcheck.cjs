/* QA r2-01 — the inspector's ↑/↓ and [ ] walk the list the run was opened
 * FROM. A run opened from the Datasets TABLE with a search active ("rabi")
 * used to step #3808 -> #3807 -> #3806 -> #3805 18b_reset_digital_filters:
 * dsNavRun only knew the sidebar tree and the server's raw run-id neighbor,
 * so the table's filter was ignored. The REAL dataset-virtual.js + app.js
 * together under jsdom: open a filtered row, press ] / [ -> the next/previous
 * FILTERED row opens (never an unrelated run, never a /neighbor fetch); at the
 * end of the filtered list nothing opens; a run opened from the TREE keeps the
 * tree walk (docs/68); a run outside the filter falls back as before.
 *
 * Run: node tests/ds_table_nav_selfcheck.cjs  (driven by tests/test_ds_table_nav.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

function R(id, exp) {
  return { id: id, exp: exp, date: '2026-09-15', time: '01:00:00', q: ['q1'], p: [], oc: {},
           metric: '', bm: false, tags: [], status: 'successful', dur: 1, note: '',
           parent: null, hs: false, sm: {}, pm: {}, f: 'k' };
}
// newest first, the table's default sort: rabi runs are 3812, 3808, 3807, 3806, 3270
const ROWS = [R(3812, '11_power_rabi'), R(3810, '18b_reset'), R(3808, '11_power_rabi'),
              R(3807, '11_power_rabi'), R(3806, '11_power_rabi'), R(3805, '18b_reset'),
              R(3300, '03_res_spec'), R(3270, '11_power_rabi'), R(3100, '03_res_spec')];

const dom = new JSDOM(`<!doctype html><html><body>
    <div id="sidebar"><ul class="tree-entries">
      <li><span class="tree-entry-click" data-uid="k:3806" data-run-id="3806" tabindex="0">#3806</span></li>
      <li><span class="tree-entry-click" data-uid="k:3805" data-run-id="3805" tabindex="0">#3805</span></li>
    </ul></div>
    <div id="table-pane">
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(ROWS)}</script>
      <div id="datasets-scroll" style="height:400px"><table><tbody id="datasets-tbody"></tbody></table></div>
    </div>
    <div id="inspector-pane"></div>
  </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
const w = dom.window;
global.window = w;
global.CSS = w.CSS;
global.document = w.document;
global.Event = w.Event;
global.CustomEvent = w.CustomEvent;
global.KeyboardEvent = w.KeyboardEvent;
global.location = w.location;
global.localStorage = w.localStorage;
global.sessionStorage = w.sessionStorage;
global.requestAnimationFrame = w.requestAnimationFrame = (cb => setTimeout(cb, 0));
global.cancelAnimationFrame = w.cancelAnimationFrame = (id => clearTimeout(id));
global.MutationObserver = w.MutationObserver;
global.IntersectionObserver = w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
global.ResizeObserver = w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
Object.defineProperty(w.HTMLElement.prototype, 'offsetParent', {
  get() { return (this.hidden || (this.closest && this.closest('[hidden]'))) ? null : this.parentElement; },
  configurable: true,
});
w.Element.prototype.scrollIntoView = function () {};
const fetches = [];
global.fetch = w.fetch = (url) => { fetches.push(String(url)); return new Promise(() => {}); };
const NODE_SET_INTERVAL = setInterval;
w.setInterval = (fn, ms) => NODE_SET_INTERVAL(fn, ms);
// every run load, and the detail that load renders (the server's #ds-detail-root)
const loads = [];
w.htmx = {
  ajax: (m, url, opts) => {
    loads.push(url);
    const uid = url.split('/').pop();
    w.document.getElementById('inspector-pane').innerHTML =
      '<div id="ds-detail-root" data-uid="' + uid + '" data-run-id="' + uid.split(':')[1] + '"></div>';
    return Promise.resolve();
  },
  trigger: () => {}, process: () => {}, on: () => {},
};
global.htmx = w.htmx;
w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(STATIC, 'dataset-virtual.js'), 'utf8'));
w.DatasetVirtual.init();

const doc = w.document;
function key(k) {
  const ev = new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
  doc.body.dispatchEvent(ev);
  return ev;
}
function rowEl(uid) { return doc.querySelector('#datasets-tbody tr[data-id="' + uid + '"]'); }
function last() { return loads[loads.length - 1]; }
function neighborFetches() { return fetches.filter(u => u.indexOf('/neighbor') !== -1); }

(async () => {
  await tick();
  const s = doc.getElementById('dataset-search');
  s.value = 'power_rabi';
  s.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(300);
  s.blur();
  ok(!!rowEl('k:3808') && !rowEl('k:3805'), '(fixture) the search shows only the power_rabi rows');

  // open #3808 from the TABLE, then step "older" three times
  rowEl('k:3808').click();
  ok(last() === '/dataset/k:3808', 'clicking the table row opens #3808');
  key(']');
  ok(last() === '/dataset/k:3807', '] opens the next FILTERED row (#3807)');
  key(']');
  ok(last() === '/dataset/k:3806', '] again -> #3806');
  key(']');
  ok(last() === '/dataset/k:3270', '] again -> #3270, the next power_rabi run (was #3805 18b_reset)');
  ok(neighborFetches().length === 0, 'no raw run-id /neighbor fetch escaped the filter');
  ok(!!doc.querySelector('#datasets-tbody tr.ds-row-active[data-id="k:3270"]'),
     'the table highlight follows the inspector');
  const n = loads.length;
  key(']');
  ok(loads.length === n && neighborFetches().length === 0,
     'at the end of the filtered list ] opens nothing (never an unrelated run)');
  key('[');
  ok(last() === '/dataset/k:3806', '[ walks back up the filtered list');

  // the inspector's ↓ BUTTON path is the same function
  w.dsNavRun(1);
  ok(last() === '/dataset/k:3270', 'the ↓ button (dsNavRun(1)) follows the filtered list too');

  // a run opened from the TREE keeps the tree walk (docs/68)
  doc.querySelector('.tree-entry-click[data-uid="k:3806"]').click();
  ok(last() === '/dataset/k:3806' && w._dsNavFromTable === null,
     'a tree click clears the table marker');
  key(']');
  ok(last() === '/dataset/k:3805', 'from a tree-opened run ] walks the tree as before');

  // a table-opened run that the user then filters OUT falls back as before
  rowEl('k:3807').click();
  s.value = '18b_reset';
  s.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(300);
  s.blur();
  fetches.length = 0;
  key(']');
  ok(neighborFetches().some(u => u === '/dataset/k:3807/neighbor?dir=1'),
     'a run no longer in the filtered list falls back to the server neighbor');

  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
