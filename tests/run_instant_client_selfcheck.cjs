/* docs/262 -- the browser reads a run's INSTANT, never its folder digits.
 *
 *   A. Datasets "When" column (dataset-virtual.js, SnapTime from app.js): the
 *      relative text and the title come from the row's `t` (UTC ms) in the
 *      viewer's zone, with the acquisition PC's own clock named beside it;
 *      sorting by When orders two archives from different zones by instant.
 *   B. Datasets > Trends: x is the instant drawn in the viewer's zone
 *      (SnapTime.axisValue), not the raw ms Plotly would draw as UTC.
 *
 * Run with TZ=Asia/Seoul: node tests/run_instant_client_selfcheck.cjs
 * (driven by tests/test_one_run_instant.py::test_the_clients_read_the_instant)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const k = APP.indexOf("    var KEY = 'quam_tz';");
const SNAP = APP.slice(APP.lastIndexOf('(function () {', k), APP.indexOf('})();', k) + 5);
const DSV = fs.readFileSync(path.join(STATIC, 'dataset-virtual.js'), 'utf8');

// A -04:00 run saved at 2026-04-03 00:07:48 (04:07:48Z) and a +09:00 run whose
// folder says 10:00:00 (01:00:00Z): by folder digits B is newer, by instant A.
const T_A = Date.UTC(2026, 3, 3, 4, 7, 48);
const T_B = Date.UTC(2026, 3, 3, 1, 0, 0);
const NOW = T_A + 3 * 3600 * 1000;
function row(id, date, time, t, f) {
  return { id: id, exp: 'zz_off', date: date, time: time, t: t, tq: 'offset', q: ['q1'], p: [],
           oc: {}, metric: '', bm: false, tags: [], status: 'finished', dur: 1, note: '',
           parent: null, hs: true, sm: {}, pm: {}, f: f };
}
const ROWS = [row(1182, '2026-04-03', '00:07:48', T_A, 'fa'), row(5, '2026-04-03', '10:00:00', T_B, 'fk')];

function bootDatasets(rows, now, zone) {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <span class="sort-banner-summary" id="sort-banner-summary"></span>
      <div id="sort-filter-grid"><div id="sort-col-badges"></div>
        <div class="sort-filter-section"><div id="sort-fit-badges"></div></div>
        <div class="sort-filter-section"><div id="sort-param-badges"></div></div>
        <div id="sort-qubit-menu"></div><span id="sort-qubit-summary"></span>
        <div id="sort-pair-menu"></div><span id="sort-pair-summary"></span></div>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(rows || ROWS)}</script>
      <div id="datasets-scroll" style="height:900px"><table>
        <colgroup id="datasets-colgroup"></colgroup><thead id="datasets-thead"></thead>
        <tbody id="datasets-tbody"></tbody></table></div>
    </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true, runScripts: 'outside-only' });
  const w = dom.window;
  w.requestAnimationFrame = w.requestAnimationFrame || ((cb) => setTimeout(cb, 0));
  w.cancelAnimationFrame = w.cancelAnimationFrame || ((id) => clearTimeout(id));
  Object.assign(global, { window: w, document: w.document, CSS: w.CSS, Event: w.Event,
    CustomEvent: w.CustomEvent, KeyboardEvent: w.KeyboardEvent, MouseEvent: w.MouseEvent,
    requestAnimationFrame: w.requestAnimationFrame, cancelAnimationFrame: w.cancelAnimationFrame,
    localStorage: w.localStorage });
  const stub = () => new Promise(() => {});
  w.fetch = stub; global.fetch = stub;
  w.htmx = { ajax: () => Promise.resolve() }; global.htmx = w.htmx;
  w.eval('Date.now = function () { return ' + (now || NOW) + '; };');
  if (zone) w.localStorage.setItem('quam_tz', zone);
  w.eval(SNAP);
  w.eval(DSV);
  w.DatasetVirtual.init();
  return w;
}

function whenCell(w, id) {
  const trs = Array.prototype.filter.call(w.document.querySelectorAll('#datasets-tbody tr[data-id]'),
    (tr) => Number(String(tr.getAttribute('data-id')).split(':').pop()) === id);
  return trs.length ? trs[0].querySelector('td[data-col-key="when"]') : null;
}
function ids(w) {
  return Array.prototype.map.call(w.document.querySelectorAll('#datasets-tbody tr[data-id]'),
    (tr) => Number(String(tr.getAttribute('data-id')).split(':').pop()));
}

(async () => {
  // ---------------- A. the When column ----------------
  {
    const w = bootDatasets();
    await tick(80);
    ok(!!w.SnapTime, 'fixture: SnapTime is loaded');
    const a = whenCell(w, 1182), b = whenCell(w, 5);
    ok(!!a && !!b, 'fixture: both rows render a When cell');
    if (a && b) {
      const sa = a.querySelector('span'), sb = b.querySelector('span');
      ok(sa.textContent === '3h ago', 'A: the -04:00 run is 3h old by its instant (got "' + sa.textContent + '")');
      ok(sb.textContent === '6h ago', 'A: the +09:00 run is 6h old by its instant (got "' + sb.textContent + '")');
      ok(/^2026-04-03 13:07:48 \(UTC\+9\)/.test(sa.title),
         'A: the title is the instant in the viewer zone with its offset (got "' + sa.title + '")');
      ok(/acquisition PC clock 2026-04-03 00:07:48/.test(sa.title),
         'A: ... and names the acquisition PC clock the folder carries');
      ok(/^2026-04-03 10:00:00 \(UTC\+9\)/.test(sb.title), 'A: the +09:00 run reads its own clock (got "' + sb.title + '")');
    }
    const th = w.document.querySelector('#datasets-thead th[data-sort="when"]');
    ok(!!th, 'fixture: the When header sorts');
    if (th) {
      th.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
      await tick(80);
      ok(JSON.stringify(ids(w)) === '[1182,5]',
         'A: sorted by When (newest first), the -04:00 run is newer by instant (got ' + JSON.stringify(ids(w)) + ')');
    }
  }

  // ---------------- A2. an old run's day is the VIEWER's day ----------------
  // 2026-04-03T02:00Z is 4/3 11:00 in Seoul (this process) but 4/2 22:00 in
  // New York (the zone chosen in Settings); 90 days later the column shows M/D.
  {
    const t = Date.UTC(2026, 3, 3, 2, 0, 0);
    const w = bootDatasets([row(7, '2026-04-02', '22:00:00', t, 'fa')], t + 90 * 86400 * 1000,
                           'America/New_York');
    await tick(80);
    const c = whenCell(w, 7);
    const sp = c && c.querySelector('span');
    ok(sp && sp.textContent === '4/2', 'A2: an old run shows its day in the chosen zone (New York 4/2, not Seoul 4/3): "' + (sp && sp.textContent) + '"');
    ok(sp && /^2026-04-02 22:00:00 \(UTC-4\)/.test(sp.title), 'A2: ... and its title is that zone with its offset: "' + (sp && sp.title) + '"');
  }

  // ---------------- A3. a delta's arrival is "new" by its instant ----------------
  // The table holds the +09:00 run (folder 10:00:00 = 01:00Z). A delta brings a
  // -04:00 run whose folder says 00:07:48 (04:07:48Z): by digits it is older and
  // would land silently; by instant it is the newest run -- announced.
  {
    const delta = { now: 2000, vanished: [], updated: [row(1182, '2026-04-03', '00:07:48', T_A, 'fa')] };
    let served = 0;
    const w = bootDatasets([row(5, '2026-04-03', '10:00:00', T_B, 'fk')]);
    w.fetch = (url) => {
      if (String(url).indexOf('/datasets/changes-since') >= 0 && !served++)
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(delta) });
      return new Promise(() => {});
    };
    global.fetch = w.fetch;
    await tick(60);
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await tick(120);
    const pill = w.document.getElementById('ds-new-pill');
    ok(served === 1, 'fixture: the delta was served (' + served + ')');
    ok(pill && !pill.hidden && /^1 new run/.test(pill.textContent),
       'A3: the -04:00 arrival is announced as new (instant 04:07Z > 01:00Z): "' + (pill && pill.textContent) + '"');
  }

  // ---------------- B. the Trends axis ----------------
  {
    const H = require('./trends_view_harness.cjs');
    const W = H.world();
    const p = H.payload(3, [{ q: 'q1', m: 'T1', v: [1, 2, 3] }]);
    W.answers.push({ body: p });
    W.mount();
    await H.until(() => W.draws.length >= 1);
    const d = W.draws[0];
    const tr = d && d.data.find((t) => / \/ /.test(t.name) && !/isolated/.test(t.name));
    ok(!!tr, 'fixture: the trend drew');
    if (tr) {
      ok(JSON.stringify(tr.x) === JSON.stringify(['2026-09-01T09:00:00', '2026-09-01T09:01:00', '2026-09-01T09:02:00']),
         'B: x is each run\'s instant in the viewer zone (00:00Z -> 09:00 at UTC+9): ' + JSON.stringify(tr.x));
      ok(d.layout.xaxis.type === 'date', 'B: still a date axis');
    }
    const notes = W.w.document.querySelector('[data-role="notes"]');
    ok(notes && /x = when each run saved its state, in /.test(notes.textContent) && !/folder names it/.test(notes.textContent),
       'B: the caption says what x is now -- the run\'s saved instant in the viewer zone (got "' + (notes && notes.textContent) + '")');
  }

  console.log(fails ? fails + ' FAILED' : 'all passed');
  process.exit(fails ? 1 : 0);
})();
