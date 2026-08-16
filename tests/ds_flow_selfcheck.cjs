/* docs/112 (#12) — datasets daily-flow in the REAL dataset-virtual.js under
 * jsdom: j/k/Enter keyboard navigation, the "↻ Newest" sort-reset chip, and
 * the digest band following the FILTER (recomputed over the filtered set,
 * byte-identical server band restored when filters clear).
 *
 * Run: node tests/ds_flow_selfcheck.cjs  (driven by tests/test_ds_flow.py)
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

const ROWS = [
  { id: 4, exp: 'rabi', date: '2026-08-10', time: '04:00:00', q: ['q1'], p: [],
    oc: { q1: 'failed' }, metric: '', bm: false, tags: [], status: 'error',
    dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' },
  { id: 3, exp: 'rabi', date: '2026-08-10', time: '03:00:00', q: ['q1'], p: [],
    oc: {}, metric: '', bm: false, tags: [], status: 'successful',
    dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' },
  { id: 2, exp: 'spec', date: '2026-08-09', time: '02:00:00', q: ['q2'], p: [],
    oc: {}, metric: '', bm: false, tags: [], status: 'successful',
    dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' },
  { id: 1, exp: 'spec', date: '2026-08-09', time: '01:00:00', q: ['q2'], p: [],
    oc: {}, metric: '', bm: false, tags: [], status: 'successful',
    dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' },
];

const SERVER_BAND = '<span class="ds-digest-date">2026-08-10</span>'
  + '<span class="ds-digest-item">2 runs</span>'
  + '<button class="ds-help-example ds-digest-bad" data-example="is:failed">1 failed</button>';

function boot(preSort) {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-digest-band">${SERVER_BAND}</div>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(ROWS)}</script>
      <div id="datasets-scroll" style="height:400px">
        <table><tbody id="datasets-tbody"></tbody></table>
      </div>
    </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
  const w = dom.window;
  w.requestAnimationFrame = w.requestAnimationFrame || (cb => setTimeout(cb, 0));
  w.cancelAnimationFrame = w.cancelAnimationFrame || (id => clearTimeout(id));
  global.window = w;
  // jsdom bridges only what we hand it, and `CSS` was never on the list: the
  // window HAS a CSS object, but bare `CSS` is undefined here, so
  // dataset-virtual.js's `window.CSS && CSS.escape ? CSS.escape(uid) : uid`
  // THREW ReferenceError instead of taking either branch — _kbHighlight blew
  // up on the first j/k press. A browser has CSS as a global; the harness must
  // too.
  global.CSS = w.CSS;
  global.document = w.document;
  global.Event = w.Event;
  global.CustomEvent = w.CustomEvent;
  global.KeyboardEvent = w.KeyboardEvent;
  global.requestAnimationFrame = w.requestAnimationFrame;
  global.cancelAnimationFrame = w.cancelAnimationFrame;
  global.localStorage = w.localStorage;
  if (preSort) {
    w.localStorage.setItem('quam_ds_sort_key', preSort.key);
    w.localStorage.setItem('quam_ds_sort_desc', preSort.desc ? '1' : '0');
  }
  const rec = function (fn, ms) { return NODE_SET_INTERVAL(fn, ms); };
  w.setInterval = rec; global.setInterval = rec;
  const stub = () => new Promise(() => {});
  w.fetch = stub; global.fetch = stub;
  w.htmx = { ajax: () => Promise.resolve() }; global.htmx = w.htmx;
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.DatasetVirtual.init();
  return w;
}
function key(w, k) {
  w.document.dispatchEvent(new w.KeyboardEvent('keydown',
    { key: k, bubbles: true, cancelable: true }));
}
function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

(async () => {
  // ── j/k/Enter keyboard navigation ─────────────────────────────────────────
  {
    const w = boot();
    await tick();
    const doc = w.document;
    key(w, 'j');
    let active = doc.querySelector('#datasets-tbody tr.ds-row-active');
    ok(!!active, 'j activates the first row');
    const firstId = active && active.getAttribute('data-id');
    key(w, 'j');
    active = doc.querySelector('#datasets-tbody tr.ds-row-active');
    ok(active && active.getAttribute('data-id') !== firstId, 'j moves down');
    key(w, 'k');
    active = doc.querySelector('#datasets-tbody tr.ds-row-active');
    ok(active && active.getAttribute('data-id') === firstId, 'k moves back up');
    let clicked = 0;
    active.addEventListener('click', () => clicked++);
    key(w, 'Enter');
    ok(clicked === 1, 'Enter opens the active row (row click path)');
    // typing in an input is never hijacked
    const s = doc.getElementById('dataset-search');
    s.focus();
    const before = doc.querySelector('#datasets-tbody tr.ds-row-active');
    key(w, 'j');
    ok(doc.querySelector('#datasets-tbody tr.ds-row-active') === before,
       'j inside a text input is not hijacked');
    s.blur();
    key(w, 'Escape');
    ok(!doc.querySelector('#datasets-tbody tr.ds-row-active'),
       'Escape clears the active row');
  }

  // ── ↻ Newest chip ────────────────────────────────────────────────────────
  {
    const w = boot({ key: 'status', desc: false });   // restored non-default sort
    await tick();
    const doc = w.document;
    const chip = doc.getElementById('ds-sort-newest');
    ok(!!chip && !chip.hidden, 'a restored non-default sort shows the ↻ Newest chip');
    chip.click();
    await tick();
    ok(chip.hidden, 'clicking it hides the chip');
    ok(w.localStorage.getItem('quam_ds_sort_key') === 'id',
       'and restores + persists the newest-first default');
    const w2 = boot();
    await tick();
    ok(!w2.document.getElementById('ds-sort-newest')
       || w2.document.getElementById('ds-sort-newest').hidden,
       'the default sort never shows the chip');
  }

  // ── digest follows the filter ────────────────────────────────────────────
  {
    const w = boot();
    await tick();
    const doc = w.document;
    const band = doc.querySelector('.ds-digest-band');
    const orig = band.innerHTML;
    const s = doc.getElementById('dataset-search');
    s.value = 'spec';
    s.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);   // the search handler may debounce
    ok(band.getAttribute('data-filtered') === '1',
       'an active filter switches the band to the filtered digest');
    ok(band.textContent.indexOf('2026-08-09') >= 0,
       'the digest date follows the FILTERED set (spec runs are 08-09)');
    ok(band.textContent.indexOf('all OK') >= 0,
       'failed count follows the filtered set (no failed spec runs)');
    ok(band.textContent.indexOf('(filtered set)') >= 0,
       'the band says it describes the filtered set');
    s.value = '';
    s.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);
    ok(band.innerHTML === orig,
       'clearing the filter restores the server band byte-identically');
  }

  // ── integration-audit fixes ───────────────────────────────────────────────
  {
    const w = boot({ key: 'status', desc: false });
    await tick();
    const doc = w.document;
    // the chip must land in the DATASETS toolbar, never the sidebar wrap
    const chip = doc.getElementById('ds-sort-newest');
    ok(!!chip && chip.closest('.ds-search-wrap')
       && chip.closest('.ds-search-wrap').querySelector('#dataset-search'),
       'audit: the ↻ Newest chip lands in the datasets search wrap');
    // Enter/Space must stay with a FOCUSED control after a j press
    key(w, 'j');
    const btn = doc.createElement('button');
    let pressed = 0;
    btn.addEventListener('keydown', e => { if (e.key === 'Enter') pressed++; });
    doc.body.appendChild(btn); btn.focus();
    const ev = new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    btn.dispatchEvent(ev);
    ok(!ev.defaultPrevented, 'audit: Enter belongs to the focused control, not the row nav');
    btn.blur();
  }

  process.exit(fails ? 1 : 0);
})();
