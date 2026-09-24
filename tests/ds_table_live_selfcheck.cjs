/* QA datasets-r2-14 + datasets-r2-16 -- the Datasets/Collections table keeps
 * its own rules live, in the REAL dataset-virtual.js under jsdom.
 *
 *   A. (r2-14) Collections holds only runs with >=1 tag. The server filters
 *      once at render; removing a run's last tag / unstarring it (patchTags /
 *      patchRow {bm, tags}) left the row drawn until F5, and a delta arrival
 *      with no tag was added. Now the client applies the same rule. On
 *      /datasets an untagged row stays; a note edit never hides a row.
 *   B. (r2-16) the header select-all box follows the VISIBLE rows: after a
 *      filter change it is not left ticked over unselected rows, a sort click
 *      (header rebuild) does not forget the ticks, and a partial selection is
 *      indeterminate. The "over" compare bar (6+ runs) names the count --
 *      app.js updateCompareButton + the template's bar markup, both shipped.
 *
 * Run: node tests/ds_table_live_selfcheck.cjs (driven by
 * tests/test_ds_table_live.py). Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..', 'quam_state_manager', 'web');
const SRC = path.join(ROOT, 'static', 'dataset-virtual.js');
const APP = fs.readFileSync(path.join(ROOT, 'static', 'app.js'), 'utf8');
const TPL = fs.readFileSync(path.join(ROOT, 'templates', '_datasets.html'), 'utf8');
const NODE_SET_INTERVAL = global.setInterval;
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function row(id, exp, tags, extra) {
  return Object.assign({ id: id, exp: exp, date: '2026-09-01', time: '0' + (id % 10) + ':00:00', q: ['q1'], p: [],
    oc: {}, metric: '', bm: false, tags: tags, status: 'successful', dur: 1, note: '', parent: null,
    hs: false, sm: {}, pm: {}, f: 'f1' }, extra || {});
}
// The shipped compare bar: the template's markup up to its (Jinja) comment,
// and app.js's _selectedRunIds + updateCompareButton, verbatim.
function compareBarHtml() {
  const i = TPL.indexOf('<div id="ds-compare-bar"');
  const j = TPL.indexOf('{#', i);
  if (i < 0 || j < 0) throw new Error('compare bar markup not found');
  return TPL.slice(i, j) + '</div>';
}
function compareBarJs() {
  const i = APP.indexOf('function _selectedRunIds() {');
  const k = APP.indexOf('window.updateCompareButton = function() {');
  const j = APP.indexOf('\n};', k);
  if (i < 0 || k < 0 || j < 0) throw new Error('updateCompareButton not found in app.js');
  return APP.slice(i, j + 3);
}

function boot(view, rows, fetchImpl) {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <script id="ds-rows-data" data-now="1000" data-view="${view}">${JSON.stringify(rows)}</script>
      <div id="datasets-scroll" style="height:400px">
        <table><thead id="datasets-thead"></thead><tbody id="datasets-tbody"></tbody></table>
      </div>
      ${compareBarHtml()}
    </body></html>`, { url: 'http://localhost/' + view, pretendToBeVisual: true, runScripts: 'outside-only' });
  const w = dom.window;
  w.requestAnimationFrame = w.requestAnimationFrame || (cb => setTimeout(cb, 0));
  w.cancelAnimationFrame = w.cancelAnimationFrame || (id => clearTimeout(id));
  global.window = w;
  global.CSS = w.CSS;
  global.document = w.document;
  global.Event = w.Event;
  global.CustomEvent = w.CustomEvent;
  global.KeyboardEvent = w.KeyboardEvent;
  global.requestAnimationFrame = w.requestAnimationFrame;
  global.cancelAnimationFrame = w.cancelAnimationFrame;
  global.localStorage = w.localStorage;
  const rec = function (fn, ms) { return NODE_SET_INTERVAL(fn, ms); };
  w.setInterval = rec; global.setInterval = rec;
  const stub = fetchImpl || (() => new Promise(() => {}));
  w.fetch = stub; global.fetch = stub;
  w.eval(compareBarJs());
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.DatasetVirtual.init();
  return w;
}
const shownIds = (w) => Array.from(w.document.querySelectorAll('#datasets-tbody tr[data-id]'))
  .map((tr) => tr.getAttribute('data-id'));
async function search(w, q) {
  const inp = w.document.getElementById('dataset-search');
  inp.value = q;
  inp.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(60);
}

(async () => {
  // ── A. Collections keeps only tagged runs, live ──────────────────────────
  {
    const w = boot('collections', [row(701, 'rabi', ['qa-coll']), row(40, 'rabi', ['favorite'], { bm: true }),
                                   row(39, 'ramsey', ['qa-coll'])]);
    await tick();
    ok(shownIds(w).length === 3, 'collections: three tagged runs drawn (' + shownIds(w) + ')');
    w.DatasetVirtual.patchTags('f1:39', []);                          // its only tag removed
    w.DatasetVirtual.patchRow('f1:40', { bm: false, tags: [] });      // unstarred (favorite was its tag)
    await tick();
    const ids = shownIds(w);
    ok(ids.length === 1 && ids[0] === 'f1:701',
       'collections: a run with no tag left leaves the list at once (' + ids + ')');
    w.DatasetVirtual.patchNote('f1:701', 'looked at');
    await tick();
    ok(shownIds(w).length === 1, 'collections: a note edit hides nothing');
  }
  {
    const w = boot('datasets', [row(2, 'rabi', ['x']), row(1, 'ramsey', ['y'])]);
    await tick();
    w.DatasetVirtual.patchTags('f1:2', []);
    await tick();
    ok(shownIds(w).length === 2, 'datasets: an untagged run stays on /datasets (' + shownIds(w) + ')');
  }
  {
    // a delta poll brings one untagged and one tagged new run to Collections
    const delta = { now: 2000, updated: [row(800, 'rabi', [], { date: '2026-09-02' }),
                                         row(801, 'rabi', ['qa-coll'], { date: '2026-09-02' })], vanished: [] };
    let served = 0;
    const fetchImpl = (url) => {
      if (String(url).indexOf('/datasets/changes-since') >= 0 && !served++) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(delta) });
      }
      return new Promise(() => {});
    };
    const w = boot('collections', [row(701, 'rabi', ['qa-coll'])], fetchImpl);
    await tick();
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await tick(80);
    const ids = shownIds(w);
    ok(served === 1 && ids.indexOf('f1:801') >= 0 && ids.indexOf('f1:800') < 0,
       'collections: a delta adds the tagged arrival, not the untagged one (' + ids + ')');
    const pill = w.document.getElementById('ds-new-pill');
    ok(!pill || pill.hidden || /^1 new run /.test(pill.textContent),
       'collections: the new-runs pill counts only what the view shows (' + (pill && pill.textContent) + ')');
  }
  {
    // the same delta HELD while the user is busy (docs/104 #3): the pill must
    // not announce the untagged run the view will never show
    const delta = { now: 2000, updated: [row(800, 'rabi', [], { date: '2026-09-02' }),
                                         row(801, 'rabi', ['qa-coll'], { date: '2026-09-02' })], vanished: [] };
    let served = 0;
    const fetchImpl = (url) => {
      if (String(url).indexOf('/datasets/changes-since') >= 0 && !served++) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(delta) });
      }
      return new Promise(() => {});
    };
    const w = boot('collections', [row(701, 'rabi', ['qa-coll'])], fetchImpl);
    await tick();
    await search(w, 'rabi');                    // an interaction: the delta is held
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await tick(80);
    const pill = w.document.getElementById('ds-new-pill');
    ok(served === 1 && pill && !pill.hidden && /^1 new run /.test(pill.textContent),
       'collections: a HELD delta announces only the tagged arrival (' + (pill && pill.textContent) + ')');
  }

  // ── B. select-all follows the visible set; the over bar counts ───────────
  {
    const rows = [];
    for (let i = 1; i <= 4; i++) rows.push(row(i, 'power_rabi', []));
    for (let i = 5; i <= 7; i++) rows.push(row(i, 'ramsey', []));
    const w = boot('datasets', rows);
    await tick();
    const doc = w.document;
    const master = () => doc.getElementById('ds-select-all');
    await search(w, 'power_rabi');
    master().checked = true;
    master().dispatchEvent(new w.Event('change', { bubbles: true }));
    await tick();
    ok(w.DatasetVirtual.getSelectedIds().length === 4, 'select-all ticks the four visible rabi runs');
    ok(master().checked, 'the master box is checked while every visible row is');
    await search(w, 'ramsey');
    ok(!master().checked && !master().indeterminate,
       'after the filter changes to ramsey the master box is NOT left checked (none of these rows is)');
    ok(w.DatasetVirtual.getSelectedIds().length === 4, 'hidden ticks survive the filter change (docs/25 §4)');
    const cb = doc.querySelector('#datasets-tbody tr[data-id="f1:5"] input.ds-check');
    cb.checked = true;
    cb.dispatchEvent(new w.Event('change', { bubbles: true }));
    await tick();
    ok(master().indeterminate && !master().checked, 'one of three visible ticked: the master box is indeterminate');
    await search(w, '');
    master().checked = true;
    master().dispatchEvent(new w.Event('change', { bubbles: true }));
    await tick();
    const th = doc.querySelector('#datasets-thead th.sortable[data-sort="id"]');
    th.click();                                                       // a sort click rebuilds the header
    await tick();
    ok(master().checked, 'a header rebuild (sort click) keeps the master box checked over all-ticked rows');
    const bar = doc.getElementById('ds-compare-bar');
    ok(bar.getAttribute('data-state') === 'over', 'seven selected: the bar is "over"');
    const over = bar.querySelector('.ds-compare-msg-over');
    ok(over && /\b7\b/.test(over.textContent) && /up to 5/i.test(over.textContent),
       'the over message names the count: "' + (over && over.textContent.trim()) + '"');
    w.DatasetVirtual.clearSelection();
    ok(!master().checked && !master().indeterminate, 'Clear unticks the master box');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
