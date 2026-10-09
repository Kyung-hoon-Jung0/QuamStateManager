/* docs/208: the Trends client half of the IRB fix, under jsdom. */
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('assert');
const HTML = `<div id="topo-trends"><div class="topo-trends-controls"><input id="topo-trend-path"><div id="topo-trend-suggest"><button data-path="qubit_pairs.*.macros.*.fidelity.InterleavedRB"></button></div></div><p>Pick a metric above.</p><div class="topo-trends-grid"><div id="topo-trend-0"></div></div><script id="topo-trends-snaps" type="application/json">{"20260102_000000":{"run":99,"uid":"run:99"}}</script></div>`;
function boot(html) {
  const dom = new JSDOM(html, {url: 'http://localhost', runScripts: 'outside-only'});
  const w = dom.window;
  const s = {events: [], traces: null, timers: [], dom, w};
  w.htmx = {trigger: (t, e) => s.events.push(e), ajax: (m, url) => { s.events.push(url); return Promise.resolve(); }};
  w.fetch = () => new Promise(() => {});
  for (const name of ['app.js', 'topo-graph.js', 'chip-status.js']) w.eval(fs.readFileSync('quam_state_manager/web/static/' + name, 'utf8'));
  w._plotlyRender = (host, data, layout) => { s.traces = data; s.layout = layout; return Promise.resolve(); };
  return s;
}
let n = 0; const ok = (c, m) => { assert(c, m); n++; };

// F3 + F5: Enter picks the first suggestion; a press aborts, then asks, and says loading.
let s = boot(HTML), w = s.w;
w.ChipTrends.enter('interleaved');
ok(w.document.getElementById('topo-trend-path').value === 'qubit_pairs.*.macros.*.fidelity.InterleavedRB', 'Enter picks the first suggestion');
ok(s.events[0] === 'htmx:abort' && s.events[1].startsWith('/topology/trends?'), 'abort, then the new request');
const loading = w.document.querySelector('.topo-trends-loading');
ok(loading && loading.textContent.includes('loading'), 'loading... shown');
ok(loading.previousElementSibling && loading.previousElementSibling.classList.contains('topo-trends-controls'), 'loading sits under the controls, not below every chart');
ok(!w.document.getElementById('topo-trends').textContent.includes('Pick a metric above.'), 'never "Pick a metric" for a pressed badge');

// F2: a held point is hollow, bigger, carries no snapshot id and no run.
w.ChipTrends.render([{metric: 'macros.*.fidelity.InterleavedRB', label: '2Q gate fid. (IRB)', kind: 'pair', series: [{entity: 'q1-2 · cz_SNZ', points: [['20260101_000000', .99], ['20260102_000000', .99]], held: {'20260102_000000': '20260101_000000'}}]}]);
ok(s.traces[0].marker.symbol[1] === 'circle-open' && s.traces[0].marker.symbol[0] === 'circle', 'held point hollow, real point filled');
ok(s.traces[0].marker.size[1] === 7, 'held marker size');
ok(s.traces[0].line && s.traces[0].line.shape === 'hv', 'a stored value holds until its next change: a step line, as the value drawer draws it');
ok(s.traces[0].customdata[1][0] === '', 'held point has no snapshot id (a click does nothing)');
ok(/^unchanged since 20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:00$/.test(s.traces[0].customdata[1][1]), 'hover says unchanged since <time>');
ok(!JSON.stringify(s.traces[0].customdata[1]).includes('99'), 'held point not attributed to the newest run');
// D4: a wildcard family's y axis is its label, not the raw path.
ok(s.layout && s.layout.yaxis.title.text.indexOf('2Q gate fid. (IRB)') === 0, 'wildcard y-axis uses the label: ' + (s.layout && s.layout.yaxis.title.text));

// D2: before the first fragment there is no selection to read -- nothing sent, nothing stored.
s = boot('<div id="topo-trends"></div>'); w = s.w;
w.localStorage.clear();
w.ChipTrends.reload();
ok(s.events.length === 0, 'no abort / request before the first fragment: ' + JSON.stringify(s.events));
ok(w.localStorage.length === 0, 'no empty selection stored');

// D1: the "index updating" note re-fetches the CURRENT selection -- and never
// when the user asked for something newer meanwhile.
const NOTE = HTML.replace('<p>Pick a metric above.</p>', '<p class="muted topo-trends-updating" data-trends-updating="1">History index updating (9 snapshots)</p>');
s = boot(NOTE); w = s.w;
w.setTimeout = (fn, ms) => { s.timers.push({fn, ms}); return s.timers.length; };
w.clearTimeout = () => {};
w.ChipTrends.render([]);
const tick = s.timers.filter(t => t.ms === 3000).pop();
ok(tick, 'a 3 s follow-up is scheduled while the note shows');
w.ChipTrends.reload();                       // the user presses a badge first
const before = s.events.length;
tick.fn();
ok(s.events.length === before, 'the follow-up yields to the newer user request');
w.ChipTrends.render([]);
const tick2 = s.timers.filter(t => t.ms === 3000).pop();
const before2 = s.events.length;
tick2.fn();
ok(s.events.length === before2 + 2 && s.events[before2] === 'htmx:abort', 'with no newer request it re-fetches');

// S10 C5 (C3 review): a ledger WAIT (no controls) answering a badge press is merged by
// _apply; the client asks again with the selection the wait carries -- the note itself
// never fetches its own URL (docs/208 D1), and a newer press still wins.
{
  const WAIT = '<div class="topo-trends" id="topo-trends"><p class="vh-wait topo-trends-updating" data-vh-mode="preparing" data-trends-updating="1" data-trends-query="metrics=T1&amp;paths=x">Preparing the change history…</p><script>if (window.ChipTrends) ChipTrends.render(null);</script></div>';
  const sw = boot(HTML); const ww = sw.w;
  ww.setTimeout = (fn, ms) => { sw.timers.push({fn, ms}); return sw.timers.length; };
  ww.clearTimeout = () => {};
  ww.htmx.ajax = (m, url, opts) => {
    sw.events.push(url);
    if (opts && opts.handler) opts.handler(null, {xhr: {status: 200, responseText: WAIT}});
    return Promise.resolve();
  };
  ww.localStorage.clear();
  ww.ChipTrends.reload();                    // a press answered by a wait
  const host = ww.document.getElementById('topo-trends');
  ok(host.querySelector('[data-trends-updating]') && !host.querySelector('[hx-get]'),
     'the wait is merged in and fetches nothing by itself');
  const stored = ww.localStorage.getItem('quam_trends_sel_v1');
  const t = sw.timers.filter(x => x.ms === 3000).pop();
  ok(t, 'a follow-up is scheduled for the wait');
  ww.htmx.ajax = (m, url) => { sw.events.push(url); return Promise.resolve(); };
  const b = sw.events.length;
  t.fn();
  ok(sw.events.length === b + 2 && sw.events[b] === 'htmx:abort'
     && sw.events[b + 1] === '/topology/trends?metrics=T1&paths=x',
     'it asks again with the selection the wait answers: ' + JSON.stringify(sw.events.slice(b)));
  ok(ww.localStorage.getItem('quam_trends_sel_v1') === stored,
     'asking again from a wait stores no selection of its own');
  // a wait with no query of its own (an empty section) asks nothing
  const se = boot('<div id="topo-trends"><p class="vh-wait" data-trends-updating="1">Preparing</p></div>');
  se.w.ChipTrends.reload();
  ok(se.events.length === 0, 'a wait without its query asks nothing: ' + JSON.stringify(se.events));
}

// docs/301 F10: the hover's value carries its unit, the way the tiles and the
// axis title say it -- Plotly's %{y} borrowed the axis' SI exponent and read
// "33.883" + micro sign with no unit. PlotTheme.siFormat is the text rule.
const THEME = fs.readFileSync('quam_state_manager/web/static/plot-theme.js', 'utf8');
function hoverOf(html, chart) {
  const b = boot(html);
  b.w.eval(THEME);
  b.w.ChipTrends.render([chart]);
  return b.traces[0];
}
const PTS = (vals) => vals.map((v, i) => ['2026010' + (i + 1) + '_000000', v]);
let tr = hoverOf(HTML, {metric: 'T1', unit: 's', series: [{entity: 'qA1', points: PTS([3.3883e-5, 3.2e-5])}]});
ok(/%\{text\}/.test(tr.hovertemplate) && !/%\{y\}/.test(tr.hovertemplate), 'the hover shows the formatted value, not the axis-formatted %{y}: ' + tr.hovertemplate);
ok(tr.text[0] === '33.883 \u00b5s' && tr.text[1] === '32 \u00b5s', 'a T1 reads in microseconds, with its unit: ' + JSON.stringify(tr.text));
tr = hoverOf(HTML, {metric: 'gate_fidelity_avg', unit: '', series: [{entity: 'qA1', points: PTS([0.99123])}]});
ok(tr.text[0] === '0.99123', 'a bare ratio takes no prefix: ' + tr.text[0]);
tr = hoverOf(HTML, {metric: 'f_01', unit: 'Hz', series: [{entity: 'qA1', points: PTS([4.9876e9])}]});
ok(tr.text[0] === '4.9876 GHz', 'a frequency reads in GHz: ' + tr.text[0]);
tr = hoverOf(HTML, {metric: 'xy_power', unit: 'dBm', series: [{entity: 'qA1', points: PTS([-0.5])}]});
ok(tr.text[0] === '-0.5 dBm', 'a unit that carries its own scale takes no prefix: ' + tr.text[0]);
tr = hoverOf('<div id="topo-trends"><div class="topo-trends-grid"><div id="topo-trend-0"></div></div></div>',
             {metric: 'T1', unit: 's', series: [{entity: 'qA1', points: PTS([3.3883e-5])}]});
ok(/%\{text\}/.test(tr.hovertemplate) && tr.text[0] === '33.883 \u00b5s', 'with no provenance map too: ' + tr.hovertemplate);

// docs/301 F29: change points over a day and a held tail two months long --
// the chart opens on the change points and says until when the values hold.
{
  const one = '<div id="topo-trends"><div class="topo-trends-grid"><div id="topo-trend-0"></div></div></div>';
  let s9 = boot(one);
  s9.w.ChipTrends.render([{metric: 'T1', unit: 's', series: [{entity: 'qA1',
    points: [['20260809_030000', 3e-5], ['20260809_140000', 2e-5], ['20260810_110000', 2.5e-5], ['20261008_040000', 2.5e-5]],
    held: {'20261008_040000': '20260810_110000'}}]}]);
  const L = s9.layout, r = L.xaxis.range;
  ok(Array.isArray(r) && L.xaxis.autorange === false, 'a long held tail: the chart opens on a set range');
  const lo = Date.parse(r[0] + 'Z'), hi = Date.parse(r[1] + 'Z');
  const first = Date.parse(s9.traces[0].x[0] + 'Z'), lastChange = Date.parse(s9.traces[0].x[2] + 'Z'),
        heldX = Date.parse(s9.traces[0].x[3] + 'Z');
  ok(lo < first && hi > lastChange && hi < heldX, 'the range spans the change points, not the held tail: ' + r);
  ok(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(r[0]), 'the range is in the points’ own naive spelling: ' + r[0]);
  ok((L.annotations || []).some(a => /^held to \d{4}-\d{2}-\d{2}/.test(a.text) && /Double-click/.test(a.hovertext)),
     'it says until when the values hold, and how to see it all');
  // a held tail no longer than the changes: the whole span, untouched
  s9 = boot(one);
  s9.w.ChipTrends.render([{metric: 'T1', unit: 's', series: [{entity: 'qA1',
    points: [['20260809_030000', 3e-5], ['20260810_030000', 2e-5], ['20260810_200000', 2e-5]],
    held: {'20260810_200000': '20260810_030000'}}]}]);
  ok(!s9.layout.xaxis.range && !(s9.layout.annotations || []).some(a => /held to/.test(a.text)),
     'a short held tail leaves the axis alone');
}

console.log(n + ' client assertions passed');
setTimeout(() => process.exit(0), 0);
