// S10 C7: old -> new, remove snapshot-only payloads and preserve ledger behavior.
/* RAM P2: a Trends toggle PATCHES the section (ChipTrends._apply) instead of
 * re-rendering every chart, and SnapTime builds one Intl formatter per zone.
 * Driven by tests/test_chip_trends_ram.py::test_client_patch_selfcheck. */
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('assert');

function frag(boxes, extra) {
  // boxes: [{metric, kind, sig, pts}] -> a /topology/trends fragment
  const charts = boxes.map(b => ({metric: b.metric, kind: b.kind, label: '', unit: '', n_entities: 1, sig: b.sig,
    series: [{entity: 'q1', points: b.pts || [['20260101_000000', 1], ['20260102_000000', 2]]}]}));
  return '<div class="topo-trends" id="topo-trends">'
    + '<div class="topo-trends-controls"><input id="topo-trend-path"><div id="topo-trend-suggest" hidden></div>'
    + '<span id="history-count" hx-swap-oob="true">' + ((extra && extra.count) || 7) + '</span></div>'
    + '<div class="topo-trends-grid">'
    + boxes.map((b, i) => '<div class="topo-trend-box" data-trend-metric="' + b.metric + '" data-trend-kind="' + b.kind
        + '" data-trend-sig="' + b.sig + '"><div class="topo-trend-title">' + b.metric + '</div>'
        + '<div class="topo-trend-chart" id="topo-trend-' + i + '"></div></div>').join('')
    + '</div><script type="application/json" id="topo-trends-data">' + JSON.stringify(charts) + '</script>'
    + ''
    + '<script>ChipTrends.render(JSON.parse(document.getElementById("topo-trends-data").textContent));</script></div>';
}

function boot() {
  const dom = new JSDOM('<span id="history-count">1</span><div id="topo-trends"></div>',
                        {url: 'http://localhost', runScripts: 'outside-only'});
  const w = dom.window;
  const s = {w, renders: [], ajax: [], next: null};
  w.htmx = {
    trigger: () => {},
    ajax: (m, url, spec) => {
      s.ajax.push(url);
      const r = s.next; s.next = null;
      if (r && spec && spec.handler) spec.handler(w.document.getElementById('topo-trends'),
                                                  {xhr: {status: r.status || 200, responseText: r.html}});
      return Promise.resolve();
    },
  };
  w.fetch = () => new Promise(() => {});
  for (const name of ['app.js', 'topo-graph.js', 'chip-status.js']) w.eval(fs.readFileSync('quam_state_manager/web/static/' + name, 'utf8'));
  w._plotlyRender = (host) => { s.renders.push(host.closest('.topo-trend-box').getAttribute('data-trend-metric'));
                                host._fullLayout = {}; host.data = [{type: 'scatter'}]; return Promise.resolve(); };
  // the first build: what the section's lazy htmx swap + inline script do
  s.first = (html) => {
    const host = w.document.getElementById('topo-trends');
    const t = w.document.createElement('template'); t.innerHTML = html;
    host.replaceWith(t.content.querySelector('#topo-trends'));
    w.ChipTrends.render(JSON.parse(w.document.getElementById('topo-trends-data').textContent));
  };
  return s;
}
let n = 0; const ok = (c, m) => { assert(c, m); n++; };
const box = (w, m) => w.document.querySelector('.topo-trend-box[data-trend-metric="' + m + '"]');

// P1: a toggle that ADDS a chart draws exactly that chart; the drawn ones stay the same nodes
let s = boot(), w = s.w;
s.first(frag([{metric: 'T1', kind: 'qubit', sig: 'a1'}, {metric: 'T2echo', kind: 'qubit', sig: 'b1'}]));
ok(s.renders.join() === 'T1,T2echo', 'first build draws every chart: ' + s.renders);
const t1 = box(w, 'T1'), t1chart = t1.querySelector('.topo-trend-chart');
s.renders = [];
// A kept (drawn) box is never DETACHED from the document during a patch: a
// detach+reattach re-styles/re-lays-out its whole Plotly SVG (measured
// ~250-300 ms per toggle on a 30-qubit chip). Every removal is recorded.
const detached = [];
const mo = new w.MutationObserver(recs => recs.forEach(r => r.removedNodes.forEach(n => detached.push(n))));
mo.observe(w.document.getElementById('topo-trends'), {childList: true, subtree: true});
s.next = {html: frag([{metric: 'f_01', kind: 'qubit', sig: 'c1'}, {metric: 'T1', kind: 'qubit', sig: 'a1'},
                      {metric: 'T2echo', kind: 'qubit', sig: 'b1'}], {count: 9})};
w.ChipTrends.toggle('f_01');
ok(s.renders.join() === 'f_01', 'a toggle draws ONLY the new chart: ' + s.renders);
ok(box(w, 'T1') === t1 && t1.querySelector('.topo-trend-chart') === t1chart, 'an unchanged chart keeps its drawn node');
ok(w.document.querySelectorAll('.topo-trend-box').length === 3, 'three boxes after the patch');
const ids = [].map.call(w.document.querySelectorAll('.topo-trend-chart'), c => c.id);
ok(ids.join() === 'topo-trend-0,topo-trend-1,topo-trend-2', 'positional host ids renumbered, no duplicates: ' + ids);
ok(w.document.getElementById('history-count').textContent === '9', 'the oob History (N) count is applied');
ok(w.document.querySelectorAll('#history-count').length === 1, 'exactly one History (N) count');
ok(!w.document.querySelector('#topo-trends script:not([type])'), 'the inline render script is not carried in');
ok(!w.document.querySelector('.topo-trends-loading'), 'loading... is gone after the patch');

// P2: a toggle that REMOVES a chart draws nothing
s.renders = [];
s.next = {html: frag([{metric: 'T1', kind: 'qubit', sig: 'a1'}, {metric: 'T2echo', kind: 'qubit', sig: 'b1'}])};
w.ChipTrends.toggle('f_01');
ok(s.renders.length === 0, 'turning a chart off draws nothing: ' + s.renders);
ok(!box(w, 'f_01') && box(w, 'T1') === t1, 'the removed box is gone, the kept one is the same node');
mo.takeRecords().forEach(r => r.removedNodes.forEach(n => detached.push(n)));
mo.disconnect();
ok(detached.length > 0, 'the observer saw the patches (the removed f_01 box at least)');
ok(!detached.includes(t1) && !detached.some(n => n.contains && n.contains(t1)),
   'a kept box was never detached across an ON and an OFF patch');

// (P3) a CHANGED signature is redrawn (new data), an unchanged one is not
s.renders = [];
s.next = {html: frag([{metric: 'T1', kind: 'qubit', sig: 'a2', pts: [['20260101_000000', 1], ['20260103_000000', 3]]},
                      {metric: 'T2echo', kind: 'qubit', sig: 'b1'}])};
w.ChipTrends.reload();
ok(s.renders.join() === 'T1', 'only the chart whose signature changed is redrawn: ' + s.renders);

// P5: the same (metric) with a different KIND is a different chart
s.renders = [];
s.next = {html: frag([{metric: 'T1', kind: 'pair', sig: 'a2'}, {metric: 'T2echo', kind: 'qubit', sig: 'b1'}])};
w.ChipTrends.reload();
ok(s.renders.join() === 'T1', 'same metric, other kind, same signature: still a NEW chart: ' + s.renders);

// P4: a chart drawn in another display zone is never kept
s.renders = [];
w.SnapTime.setZone('UTC');
s.next = {html: frag([{metric: 'T1', kind: 'qubit', sig: 'a2'}, {metric: 'T2echo', kind: 'qubit', sig: 'b1'}])};
w.ChipTrends.reload();
ok(s.renders.join() === 'T1,T2echo', 'a zone change redraws every chart: ' + s.renders);
w.SnapTime.setZone('');

// P6: a failed request says so instead of loading... forever; a stale answer is ignored
s = boot(); w = s.w;
s.first(frag([{metric: 'T1', kind: 'qubit', sig: 'a1'}]));
s.next = {status: 500, html: 'boom'};
w.ChipTrends.toggle('f_01');
const l = w.document.querySelector('.topo-trends-loading');
ok(l && /could not be loaded/.test(l.textContent) && /500/.test(l.textContent), 'a 5xx says it failed: ' + (l && l.textContent));
ok(box(w, 'T1'), 'the drawn chart survives a failed patch');
// a response for an OLDER press arrives after a newer one was sent: ignored
let held = null;
w.htmx.ajax = (m, url, spec) => { s.ajax.push(url); if (!held) held = spec; return Promise.resolve(); };
w.ChipTrends.toggle('T2echo');
const old = held; held = 'x';
w.ChipTrends.toggle('f_01');
s.renders = [];
old.handler(w.document.getElementById('topo-trends'), {xhr: {status: 200, responseText: frag([{metric: 'ZZ', kind: 'qubit', sig: 'z'}])}});
ok(!box(w, 'ZZ') && s.renders.length === 0, 'an answer to an older press never lands');

// T1: SnapTime builds ONE formatter per zone, whatever the point count
s = boot(); w = s.w;
let built = 0;
const D = w.Intl.DateTimeFormat;
w.Intl.DateTimeFormat = function () { built++; return new D(...arguments); };
w.Intl.DateTimeFormat.prototype = D.prototype;
w.SnapTime.setZone('Asia/Seoul');
const a = [];
for (let i = 0; i < 500; i++) a.push(w.SnapTime.axisValue('20260101_00' + String(10 + (i % 50)) + '00'));
ok(built === 1, '500 stamps, one formatter: ' + built);
ok(a[0] === '2026-01-01T09:10:00', 'Seoul is UTC+9: ' + a[0]);
w.SnapTime.setZone('UTC');
ok(w.SnapTime.axisValue('20260101_001000') === '2026-01-01T00:10:00' && built === 2, 'a new zone, one more formatter');
w.SnapTime.setZone('Not/AZone');
const v = w.SnapTime.axisValue('20260101_001000');
ok(/^2026-01-01T\d\d:10:00$/.test(v), 'an invalid zone falls back to the browser zone: ' + v);
w.SnapTime.axisValue('20260101_001100');
ok(built <= 4, 'the fallback formatter is memoized too: ' + built);

// T2: a burst of htmx swaps measures the layout ONCE, after the burst -- the
// TopbarHeight measure forces a synchronous layout, and Chip Status swaps
// many fragments while its charts draw.
(async () => {
  const s2 = boot(), w2 = s2.w;
  const lay = w2.document.createElement('div'); lay.className = 'app-layout';
  w2.document.body.appendChild(lay);
  let measured = 0;
  lay.getBoundingClientRect = () => { measured++; return {top: 40, width: 100, height: 10}; };
  await new Promise(r => setTimeout(r, 40));            // the body observer's own publish
  measured = 0;
  for (let i = 0; i < 12; i++) w2.document.dispatchEvent(new w2.Event('htmx:afterSwap'));
  ok(measured === 0, 'a swap forces no synchronous layout: ' + measured);
  await new Promise(r => setTimeout(r, 60));
  ok(measured === 1, 'twelve swaps, one measure: ' + measured);
  ok(w2.document.documentElement.style.getPropertyValue('--topbar-height') === '40px', 'the height is still published');
  console.log('trends_patch_selfcheck ok (' + n + ' assertions)');
  setTimeout(() => process.exit(0), 0);
})().catch(e => { console.error(e); process.exit(1); });
