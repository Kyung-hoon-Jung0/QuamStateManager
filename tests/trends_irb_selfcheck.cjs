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

console.log(n + ' client assertions passed');
setTimeout(() => process.exit(0), 0);
