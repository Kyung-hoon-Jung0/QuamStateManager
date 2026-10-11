/* v1.3.0 A3: every chart of one Trends view opens on ONE time window.
 *
 * Before, each chart picked its own x range: autorange over its own points,
 * or the docs/301 F29 focus on its own change points. On one page T1 spanned
 * the whole history while a gate-fidelity chart with three change points
 * opened on one hour "held to" its last point. Driven by
 * tests/test_v130_trends_window.py. */
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('assert');

function grid(metrics) {
  return '<div class="topo-trends" id="topo-trends"><div class="topo-trends-grid">'
    + metrics.map((m, i) => '<div class="topo-trend-box" data-trend-metric="' + m + '" data-trend-kind="qubit"'
        + ' data-trend-sig="s' + m + '"><div class="topo-trend-chart" id="topo-trend-' + i + '"></div></div>').join('')
    + '</div></div>';
}
function boot(html) {
  const dom = new JSDOM(html, {url: 'http://localhost', runScripts: 'outside-only'});
  const w = dom.window;
  const s = {w, layouts: {}, renders: [], relayouts: []};
  w.htmx = {trigger: () => {}, ajax: () => Promise.resolve()};
  w.fetch = () => new Promise(() => {});
  for (const name of ['app.js', 'topo-graph.js', 'chip-status.js']) {
    w.eval(fs.readFileSync('quam_state_manager/web/static/' + name, 'utf8'));
  }
  w.SnapTime.setZone('UTC');
  w._plotlyRender = (host, data, layout) => {
    const m = host.closest('.topo-trend-box').getAttribute('data-trend-metric');
    s.renders.push(m); s.layouts[m] = layout;
    host._fullLayout = {xaxis: {range: layout.xaxis.range, autorange: layout.xaxis.autorange,
                                _rangeInitial0: (layout.xaxis.range || [])[0],
                                _rangeInitial1: (layout.xaxis.range || [])[1]}};
    host.data = data;
    return Promise.resolve();
  };
  return s;
}
let n = 0; const ok = (c, m) => { assert(c, m); n++; };
const ms = (x) => Date.parse(String(x).replace(' ', 'T') + 'Z');
const hasNote = (L) => (L.annotations || []).some(a => /^held to /.test(a.text));
const noteOf = (L) => ((L.annotations || []).find(a => /^held to /.test(a.text)) || {}).text;

// A: a chip with T1 measured for two months and a gate fidelity recorded three
// times in ten minutes, both held to the same newest record. The old code drew
// T1 over the whole span and the fidelity over one hour "held to" its last point.
{
  const s = boot(grid(['T1', 'gate_fidelity_avg']));
  const NEWEST = '20261008_040000';
  s.w.ChipTrends.render([
    {metric: 'T1', kind: 'qubit', unit: 's', series: [{entity: 'q1',
      points: [['20260801_030000', 3e-5], ['20260901_140000', 2e-5], ['20261001_110000', 2.5e-5], [NEWEST, 2.5e-5]],
      held: {[NEWEST]: '20261001_110000'}}]},
    {metric: 'gate_fidelity_avg', kind: 'qubit', unit: '', series: [{entity: 'q1',
      points: [['20261003_120000', 0.991], ['20261003_120500', 0.993], ['20261003_121000', 0.992], [NEWEST, 0.992]],
      held: {[NEWEST]: '20261003_121000'}}]},
  ]);
  const a = s.layouts.T1.xaxis, b = s.layouts.gate_fidelity_avg.xaxis;
  ok(Array.isArray(a.range) && a.autorange === false && Array.isArray(b.range) && b.autorange === false,
     'both charts open on a set range');
  ok(a.range.join() === b.range.join(), 'ONE window for the view: ' + a.range + ' vs ' + b.range);
  ok(ms(a.range[0]) < ms('2026-08-01T03:00:00') && ms(a.range[1]) > ms('2026-10-08T04:00:00'),
     'the window holds every point of every chart, the held one included: ' + a.range);
  ok(ms(b.range[1]) - ms(b.range[0]) > 60 * 86400e3,
     'the three-point chart is no longer an hour wide: ' + b.range);
  ok(!hasNote(s.layouts.T1) && !hasNote(s.layouts.gate_fidelity_avg),
     'nothing runs past the window, so no chart says "held to"');
  ok(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(a.range[0]), 'naive spelling, as the points: ' + a.range[0]);
}

// B: the F29 case decided for the VIEW -- every change point within a day,
// held two months: every chart opens on the change points of ALL charts, and
// each one that holds a line past the window says until when, its own date.
{
  const s = boot(grid(['T1', 'gate_fidelity_avg', 'f_01', 'x180_amplitude']));
  s.w.ChipTrends.render([
    {metric: 'T1', kind: 'qubit', unit: 's', series: [{entity: 'q1',
      points: [['20260809_030000', 3e-5], ['20260809_140000', 2e-5], ['20261008_040000', 2e-5]],
      held: {'20261008_040000': '20260809_140000'}}]},
    {metric: 'gate_fidelity_avg', kind: 'qubit', unit: '', series: [{entity: 'q1',
      points: [['20260809_200000', 0.991], ['20260809_201000', 0.993], ['20261007_000000', 0.993]],
      held: {'20261007_000000': '20260809_201000'}}]},
    // no held point: the line ends inside the window
    {metric: 'f_01', kind: 'qubit', unit: 'Hz', series: [{entity: 'q1',
      points: [['20260809_050000', 4.9e9], ['20260809_060000', 4.91e9]]}]},
    // a held point INSIDE the window (it is drawn, hollow): nothing to announce
    {metric: 'x180_amplitude', kind: 'qubit', unit: '', series: [{entity: 'q1',
      points: [['20260809_100000', 0.1], ['20260809_201500', 0.1]],
      held: {'20260809_201500': '20260809_100000'}}]},
  ]);
  const L = s.layouts;
  const r = L.T1.xaxis.range;
  ok(r && L.gate_fidelity_avg.xaxis.range.join() === r.join() && L.f_01.xaxis.range.join() === r.join(),
     'the focused window is shared: ' + [r, L.gate_fidelity_avg.xaxis.range, L.f_01.xaxis.range].join(' | '));
  ok(ms(r[0]) < ms('2026-08-09T03:00:00') && ms(r[1]) > ms('2026-08-09T20:10:00') && ms(r[1]) < ms('2026-08-11T00:00:00'),
     'it spans the change points of every chart, not the held tail: ' + r);
  ok(noteOf(L.T1) === 'held to 2026-10-08 \u2192', 'T1 says its own date: ' + noteOf(L.T1));
  ok(noteOf(L.gate_fidelity_avg) === 'held to 2026-10-07 \u2192',
     'the fidelity chart says ITS own date, so the note stays true: ' + noteOf(L.gate_fidelity_avg));
  ok(!hasNote(L.f_01), 'a chart with nothing past the window says nothing');
  ok(L.x180_amplitude.xaxis.range.join() === r.join() && !hasNote(L.x180_amplitude),
     'a chart whose held point is inside the window says nothing either');
}

// C: a chart whose ids do not parse stays on its category axis and does not
// move the window of the date charts.
{
  const s = boot(grid(['T1', 'weird']));
  s.w.ChipTrends.render([
    {metric: 'T1', kind: 'qubit', unit: 's', series: [{entity: 'q1',
      points: [['20260801_000000', 1], ['20260802_000000', 2]]}]},
    {metric: 'weird', kind: 'qubit', unit: '', series: [{entity: 'q1', points: [['run-a', 1], ['run-b', 2]]}]},
  ]);
  ok(s.layouts.weird.xaxis.type === 'category' && !s.layouts.weird.xaxis.range, 'a category chart keeps its own axis');
  const r = s.layouts.T1.xaxis.range;
  ok(r && ms(r[1]) - ms(r[0]) < 2 * 86400e3, 'and leaves the date window alone: ' + r);
}

// D: a rename mark is on every date chart, and the whole-view window shows it
// (Plotly's autorange counted the shape before; a set range must too).
{
  const html = grid(['T1']).replace('</div></div>',
    '</div><script type="application/json" id="topo-trends-renames">[{"t":"20260720_000000","words":"q1 -> qA1"}]</script></div>');
  const s = boot(html);
  s.w.ChipTrends.render([{metric: 'T1', kind: 'qubit', unit: 's', series: [{entity: 'q1',
    points: [['20260801_000000', 1], ['20260810_000000', 2]]}]}]);
  const r = s.layouts.T1.xaxis.range;
  ok((s.layouts.T1.shapes || []).length === 1, 'the rename mark is drawn');
  ok(r && ms(r[0]) < ms('2026-07-20T00:00:00'), 'the window starts before the rename mark: ' + r);
}

// E: a toggle PATCH (RAM P2) keeps a drawn chart, but the new chart widens the
// window: the kept chart moves its axis (relayout, no redraw) and records the
// new window as Plotly's initial one (a double-click must not reset to the
// old window); the next patch on the same window touches nothing.
{
  const frag = (boxes) => {
    const charts = boxes.map(b => ({metric: b.m, kind: 'qubit', unit: '', sig: b.sig,
      series: [{entity: 'q1', points: b.pts}]}));
    return '<div class="topo-trends" id="topo-trends"><div class="topo-trends-controls"><input id="topo-trend-path"></div>'
      + '<div class="topo-trends-grid">'
      + boxes.map((b, i) => '<div class="topo-trend-box" data-trend-metric="' + b.m + '" data-trend-kind="qubit" data-trend-sig="'
          + b.sig + '"><div class="topo-trend-chart" id="topo-trend-' + i + '"></div></div>').join('')
      + '</div><script type="application/json" id="topo-trends-data">' + JSON.stringify(charts) + '</script></div>';
  };
  const s = boot('<div id="topo-trends"></div>');
  const w = s.w;
  let next = null;
  w.htmx.ajax = (m, url, spec) => { const r = next; next = null;
    if (r && spec && spec.handler) spec.handler(null, {xhr: {status: 200, responseText: r}}); return Promise.resolve(); };
  w.Plotly = {relayout: (host, upd) => {
    const m = host.closest('.topo-trend-box').getAttribute('data-trend-metric');
    s.relayouts.push([m, upd]);
    host._fullLayout.xaxis.range = upd['xaxis.range'];
    host._fullLayout.xaxis.autorange = upd['xaxis.autorange'];
    return Promise.resolve();
  }};
  const T1 = {m: 'T1', sig: 'a', pts: [['20260901_000000', 1], ['20260910_000000', 2]]};
  const host0 = w.document.getElementById('topo-trends');
  const t = w.document.createElement('template'); t.innerHTML = frag([T1]);
  host0.replaceWith(t.content.querySelector('#topo-trends'));
  w.ChipTrends.render(JSON.parse(w.document.getElementById('topo-trends-data').textContent));
  const first = s.layouts.T1.xaxis.range.join();
  s.renders = [];
  next = frag([{m: 'f_01', sig: 'b', pts: [['20260801_000000', 5], ['20260805_000000', 6]]}, T1]);
  w.ChipTrends.toggle('f_01');
  ok(s.renders.join() === 'f_01', 'the kept chart is not redrawn: ' + s.renders);
  const rl = s.relayouts.filter(x => x[0] === 'T1');
  ok(rl.length === 1, 'the kept chart moves its axis once: ' + JSON.stringify(s.relayouts));
  const r = rl[0][1]['xaxis.range'];
  ok(r.join() === s.layouts.f_01.xaxis.range.join() && r.join() !== first,
     'onto the window the new chart opens on: ' + r + ' vs ' + s.layouts.f_01.xaxis.range);
  ok(rl[0][1]['xaxis.autorange'] === false && Array.isArray(rl[0][1].annotations), 'range set, notes rewritten');
  Promise.resolve().then(() => new Promise(res => setTimeout(res, 0))).then(() => {
    const ax = w.document.querySelector('.topo-trend-box[data-trend-metric="T1"] .topo-trend-chart')._fullLayout.xaxis;
    ok(ax._rangeInitial0 === r[0] && ax._rangeInitial1 === r[1],
       'the new window is the chart\'s initial range (double-click resets to it): ' + [ax._rangeInitial0, ax._rangeInitial1]);
    s.renders = []; s.relayouts = [];
    next = frag([{m: 'f_01', sig: 'b', pts: [['20260801_000000', 5], ['20260805_000000', 6]]}, T1]);
    w.ChipTrends.reload();
    ok(s.renders.length === 0 && s.relayouts.length === 0,
       'the same window again: nothing drawn, nothing moved: ' + s.renders + ' ' + JSON.stringify(s.relayouts));
    // and a toggle OFF of the chart that widened it brings the window back
    next = frag([T1]);
    w.ChipTrends.toggle('f_01');
    const back = s.relayouts.filter(x => x[0] === 'T1');
    ok(s.renders.length === 0 && back.length === 1 && back[0][1]['xaxis.range'].join() === first,
       'turning it off narrows the kept chart back to its own window: ' + JSON.stringify(back));
    console.log('ok (' + n + ' assertions)');
    setTimeout(() => process.exit(0), 0);
  }).catch(e => { console.error(e); process.exit(1); });
}
