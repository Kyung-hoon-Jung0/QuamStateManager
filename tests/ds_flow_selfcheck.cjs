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

function boot(preSort, extra) {
  const x = extra || {};
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-digest-band">${SERVER_BAND}</div>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      ${x.html || ''}
      <script id="ds-rows-data" data-now="1000" data-view="${x.view || 'datasets'}">${JSON.stringify(x.rows || ROWS)}</script>
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
  w.__ajax = [];
  w.htmx = { ajax: (m, url, o) => { w.__ajax.push({ m: m, url: url, o: o }); return Promise.resolve(); } };
  global.htmx = w.htmx;
  if (x.tags) w._selectedTags = new Set(x.tags);
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

  // ── QA F7: j scrolls the active row into view in the ONE-scroller pane ────
  // On a fresh page the filter section is taller than #table-pane, so the list
  // starts below the fold. listMetrics() clamps that offset for the virtual
  // window, and _kbMove's old arithmetic on the clamped values scrolled 32 px
  // per press: the active row stayed off-screen. Geometry is stubbed (jsdom
  // has no layout): pane 900 px high, list 1100 px down it, a 30 px header.
  {
    const w = boot(null, { html: '<div id="table-pane"></div>' });
    await tick();
    const doc = w.document;
    const pane = doc.getElementById('table-pane');
    const tb = doc.getElementById('datasets-tbody');
    const thead = doc.createElement('thead');
    tb.parentNode.insertBefore(thead, tb);
    const g = { S: 0 };
    Object.defineProperty(pane, 'scrollTop', { configurable: true,
      get: () => g.S, set: (v) => { g.S = v; } });
    Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => 900 });
    pane.getBoundingClientRect = () => ({ top: 0, bottom: 900, left: 0, right: 900, width: 900, height: 900 });
    tb.getBoundingClientRect = () => ({ top: 1100 - g.S, bottom: 1100 - g.S + 128, left: 0, right: 900, width: 900, height: 128 });
    thead.getBoundingClientRect = () => ({ top: 0, bottom: 30, left: 0, right: 900, width: 900, height: 30 });
    key(w, 'j');
    // row 0: 1100 - S .. 1100 - S + 32 must end inside the 900 px viewport
    ok(1100 - g.S + 32 <= 900 && 1100 - g.S >= 30,
       `F7: the first j brings row 0 on screen (scrollTop ${g.S}, want 232 -- 32 was the bug)`);
    key(w, 'j');
    ok(1100 - g.S + 32 + 32 <= 900 && g.S === 264,
       `F7: the second j keeps row 1 on screen (scrollTop ${g.S}, want 264)`);
    // scrolled well past the list top: k must land row 0 BELOW the sticky
    // header, not under it (the old arithmetic put it at the pane's top edge)
    g.S = 1300;                       // list top now 200 px above the pane
    key(w, 'k');
    const rowTop = 1100 - g.S;
    ok(rowTop >= 30 && rowTop + 32 <= 900,
       `F7: k lands row 0 under the sticky header, not behind it (row top ${rowTop}, header 30)`);
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

  // ── QA datasets-r2-18: a digest chip shows exactly the runs it counted ────
  // The band said "all OK" (run STATUS) beside a red "q3 ×2" (per-qubit
  // OUTCOME), and the chip pasted `qubit:q3 outcome:fail` -- every date, and
  // any row holding q3 where ANY target failed. Pair chips found nothing.
  {
    const mk = (id, date, q, p, oc, status) => ({
      id: id, exp: 'rabi', date: date, time: '0' + (id % 10) + ':00:00', q: q, p: p,
      oc: oc, metric: '', bm: false, tags: [], status: status || 'successful',
      dur: 1, note: '', parent: null, hs: false, sm: {}, pm: {}, f: 'f1' });
    const R18 = [
      mk(10, '2026-09-24', ['q3', 'q4'], [], { q3: 'failed', q4: 'successful' }),
      mk(9, '2026-09-24', ['q3'], [], { q3: 'failed' }),
      // q3 is on this run and SOME target failed -- but not q3
      mk(8, '2026-09-24', ['q3', 'q4'], [], { q3: 'successful', q4: 'failed' }),
      // a 2Q run keys its outcome by the PAIR name
      mk(7, '2026-09-24', ['q3', 'q4'], ['q3-4'], { 'q3-4': 'failed' }),
      // an older day's q3 failure is not in the band's count
      mk(6, '2026-09-23', ['q3'], [], { q3: 'failed' }),
      mk(5, '2026-09-23', ['q4'], [], {}, 'error'),
    ];
    const w = boot(null, { rows: R18 });
    await tick();
    const doc = w.document;
    const band = doc.querySelector('.ds-digest-band');
    const s = doc.getElementById('dataset-search');
    s.value = 'rabi';                       // every row: forces the client digest
    s.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);
    ok(band.textContent.indexOf('all OK') < 0,
       'r2-18: failed outcomes on the day => the band never says "all OK"');
    ok(band.textContent.indexOf('no run errors') >= 0,
       'r2-18: no failed run STATUS on the day => "no run errors", beside the chips');
    const chips = Array.prototype.map.call(
      doc.querySelectorAll('.ds-digest-band .ds-digest-qchip'),
      b => ({ ex: b.getAttribute('data-example'),
              n: Number((b.textContent.match(/×(\d+)/) || [])[1]) }));
    ok(chips.length === 3, 'r2-18: three chips (q3, q4, q3-4), got ' + chips.length);
    ok(chips.every(c => c.ex.indexOf('date:2026-09-24 ') === 0),
       'r2-18: every chip is scoped to the band\'s day: ' + chips.map(c => c.ex).join(' | '));
    ok(chips.some(c => c.ex === 'date:2026-09-24 outcome:q3-4=fail'),
       'r2-18: the pair chip filters by the pair key');
    for (const c of chips) {
      s.value = c.ex;                       // what the chip click pastes
      s.dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(300);
      const shown = doc.querySelectorAll('#datasets-tbody tr[data-id]').length;
      ok(shown === c.n && c.n > 0,
         `r2-18: "${c.ex}" shows exactly the ${c.n} run(s) its chip counted (shows ${shown})`);
    }
    // the failed-run button carries the same day scope
    const w2 = boot(null, { rows: R18.map(r => r.id === 9
      ? Object.assign({}, r, { status: 'error' }) : r) });
    await tick();
    const s2 = w2.document.getElementById('dataset-search');
    s2.value = 'rabi';
    s2.dispatchEvent(new w2.Event('input', { bubbles: true }));
    await tick(300);
    const bad = w2.document.querySelector('.ds-digest-band .ds-digest-bad');
    ok(bad && bad.getAttribute('data-example') === 'date:2026-09-24 is:failed',
       'r2-18: the "N failed" button is scoped to the band\'s day: '
       + (bad && bad.getAttribute('data-example')));
    s2.value = bad ? bad.getAttribute('data-example') : '';
    s2.dispatchEvent(new w2.Event('input', { bubbles: true }));
    await tick(300);
    ok(w2.document.querySelectorAll('#datasets-tbody tr[data-id]').length === 1,
       'r2-18: ... and shows the 1 failed run of that day, not the older one');
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

  // ── QA datasets-r2-09: "Clear all filters" clears EVERY filter ───────────
  // A folder chip + a date tab only another folder has = an empty table, and
  // the empty state's own button left both (and any tag) in place.
  for (const view of ['datasets', 'collections']) {
    const html = `<div id="folder-filter-grid">
        <span class="folder-chip active" data-folder-key="">All</span>
        <span class="folder-chip" data-folder-key="f1">f1</span>
        <span class="folder-chip" data-folder-key="kh">kh</span></div>
      <div id="tag-filter-grid"><span class="tag-chip active" data-tag="">All</span>
        <span class="tag-chip" data-tag="flagged">flagged</span></div>
      <input type="hidden" id="ds-active-date" name="date" value="2026-08-10">
      <div id="datasets-empty" style="display:none">No runs <button>Clear all filters</button></div>`;
    // Collections only ever holds TAGGED runs (the server's rule, applied
    // live by the client since QA datasets-r2-14), so its fixture rows carry one.
    const rows = view === 'collections'
      ? ROWS.map(r => Object.assign({}, r, { tags: ['flagged'] })) : undefined;
    const w = boot(null, { html: html, view: view, rows: rows });
    await tick();
    const doc = w.document;
    w.DatasetVirtual.toggleFolder('kh');                       // a folder with no runs on this date
    doc.querySelectorAll('#folder-filter-grid .folder-chip').forEach(c =>
      c.classList.toggle('active', c.getAttribute('data-folder-key') === 'kh'));
    ok(doc.getElementById('datasets-empty').style.display === '',
       `[${view}] fixture: the folder chip empties the table`);
    w._selectedTags = new Set(['flagged']);
    const tagSet = w._selectedTags;                           // app.js holds THIS Set
    doc.querySelectorAll('#tag-filter-grid .tag-chip').forEach(c =>
      c.classList.toggle('active', c.getAttribute('data-tag') === 'flagged'));
    w.clearDatasetFilters();
    ok(w.DatasetVirtual.folderFilterKeys().length === 0,
       `[${view}] Clear all clears the folder filter (${w.DatasetVirtual.folderFilterKeys()})`);
    const activeFolders = Array.from(doc.querySelectorAll('#folder-filter-grid .folder-chip.active'))
      .map(c => c.getAttribute('data-folder-key'));
    ok(activeFolders.join(',') === '', `[${view}] and only the "All" folder chip is lit (${activeFolders})`);
    ok(tagSet.size === 0 && w._selectedTags === tagSet,
       `[${view}] the tag selection is cleared IN PLACE (size ${tagSet.size})`);
    const activeTags = Array.from(doc.querySelectorAll('#tag-filter-grid .tag-chip.active'))
      .map(c => c.getAttribute('data-tag'));
    ok(activeTags.join(',') === '', `[${view}] and only the "All" tag chip is lit (${activeTags})`);
    ok(doc.getElementById('ds-active-date').value === '', `[${view}] the date tab is cleared`);
    const want = view === 'collections' ? '/collections' : '/datasets';
    const a = w.__ajax[w.__ajax.length - 1];
    ok(w.__ajax.length === 1 && a.m === 'GET' && a.url === want
       && a.o.target === '#table-pane' && a.o.source === '#table-pane',
       `[${view}] the page is re-read without the date, no search, on its own view (${JSON.stringify(w.__ajax)})`);
    ok(doc.getElementById('datasets-empty').style.display === 'none',
       `[${view}] the rows it holds come back at once`);
  }
  {
    // no date tab active: nothing to re-read, the client-side clear is enough
    const w = boot(null, { html: '<input type="hidden" id="ds-active-date" value="">' });
    await tick();
    w.clearDatasetFilters();
    ok(w.__ajax.length === 0, 'with no date tab active Clear all issues no request');
  }

  process.exit(fails ? 1 : 0);
})();
