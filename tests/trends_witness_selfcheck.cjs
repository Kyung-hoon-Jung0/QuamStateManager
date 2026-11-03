/* fix/trends-stress: what a Chip Status Trends chart shows BESIDE its line,
 * against the shipped chip-status.js under jsdom.
 *
 *   T1  the chip's value now differs from the line's last (held) value: a
 *       "now" marker at the right end, in the chart's unit, and a note in
 *       words ("current value differs from the newest recorded run value");
 *       a series whose value now IS its last value gets neither.
 *   T2  a run value the witness rule keeps off the line (not in the next
 *       recorded state / not confirmed before the value came back) is a
 *       HOLLOW marker off the line, its hover naming the run that saved it
 *       and the one that still had the earlier value; the line itself is
 *       unchanged; a click opens the run that saved it.
 *   T4  a Calibration Age dated by the change ledger names the run whose
 *       saved state the age comes from.
 *   T5  a chart is drawn in the unit the server names (us for T1), every
 *       number scaled once: line, markers, hover text.
 *   T7  Plotly's "Double-click to zoom back out" toast is off.
 * Driven by tests/test_trends_stress_fixes.py.
 */
'use strict';
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
function boot(metrics) {
  const html = '<div class="topo-trends" id="topo-trends"><div class="topo-trends-grid">'
    + metrics.map((m, i) => '<div class="topo-trend-box" data-trend-metric="' + m + '" data-trend-kind="qubit"'
        + ' data-trend-sig="s' + m + '"><div class="topo-trend-chart" id="topo-trend-' + i + '"></div></div>').join('')
    + '</div></div><div id="inspector-pane"></div>';
  const dom = new JSDOM(html, { url: 'http://localhost', runScripts: 'outside-only' });
  const w = dom.window;
  const s = { w, draws: {}, ajax: [] };
  w.htmx = { trigger: () => {}, ajax: (v, u, o) => { s.ajax.push(u); return Promise.resolve(); } };
  w.fetch = () => new Promise(() => {});
  for (const name of ['app.js', 'plot-theme.js', 'topo-graph.js', 'chip-status.js']) {
    w.eval(fs.readFileSync(path.join(STATIC, name), 'utf8'));
  }
  w.SnapTime.setZone('UTC');
  w._plotlyRender = (host, data, layout, config) => {
    const m = host.closest('.topo-trend-box').getAttribute('data-trend-metric');
    s.draws[m] = { data, layout, config, host };
    host.data = data;
    host._fullLayout = { xaxis: { range: layout.xaxis.range } };
    host.__h = {};
    host.on = (ev, fn) => { (host.__h[ev] = host.__h[ev] || []).push(fn); };
    host.removeAllListeners = (ev) => { delete host.__h[ev]; };
    return Promise.resolve();
  };
  return s;
}
let n = 0;
const ok = (c, m) => { assert(c, m); n++; console.log('ok - ' + m); };
const tick = () => new Promise(r => setTimeout(r, 20));

(async () => {
  const NEWEST = '20261015_050731_e119';
  const s = boot(['T1', 'IRB']);
  s.w.ChipTrends.render([
    { metric: 'T1', kind: 'qubit', unit: 'µs', scale: 1e6, series: [
      { entity: 'q4', points: [['20261013_101224_e53', 3.45e-6], ['20261013_165102_e73', 3e-5], [NEWEST, 3e-5]],
        held: { [NEWEST]: '20261013_165102_e73' }, now: 3.45e-6,
        attr: { '20261013_165102_e73': { label: 'saved in #810', sub: 'writer not proven', saved_uid: 'u:810' } } },
      { entity: 'q1', points: [['20261013_101224_e53', 1.2e-5], [NEWEST, 1.2e-5]],
        held: { [NEWEST]: '20261013_101224_e53' } },
    ] },
    { metric: 'IRB', kind: 'pair', unit: '', scale: 1, series: [
      { entity: 'q2-3', points: [['20261012_120000_e10', 0.97622], [NEWEST, 0.97622]],
        held: { [NEWEST]: '20261012_120000_e10' },
        left_out: [['20261014_061809_e99', 0.97968, 1124, 'not_kept', '#1125', 0.97622, 0],
                   ['20261014_070000_e100', 0.981, 1130, 'excursion', '#1140', 0.97622, 0]],
        attr: { '20261014_061809_e99': { label: 'saved in #1124', sub: 'writer not proven', saved_uid: 'u:1124' } } },
    ] },
  ]);
  await tick();
  const T1 = s.draws.T1, IRB = s.draws.IRB;
  assert(T1 && IRB, 'both charts drawn');

  // T5: one unit, every number scaled once
  const q4 = T1.data.find(t => t.name === 'q4' && !t.meta);
  ok(Math.abs(q4.y[0] - 3.45) < 1e-9 && Math.abs(q4.y[1] - 30) < 1e-9, 'T5: the line is in us (' + q4.y + ')');
  ok(/\(µs\)$/.test(T1.layout.yaxis.title.text), 'T5: the axis names the unit (' + T1.layout.yaxis.title.text + ')');
  ok(q4.text[1] === '30 µs', 'T5: the hover value is "30 us", no second SI prefix (' + q4.text[1] + ')');

  // T1: the "now" marker
  const now = T1.data.filter(t => t.meta === 'now');
  ok(now.length === 1 && now[0].name === 'q4', 'T1: one "now" mark, for the series whose value now differs');
  ok(Math.abs(now[0].y[0] - 3.45) < 1e-9 && now[0].x[0] === T1.layout.xaxis.range[1],
     'T1: at the right edge of the window, at the value now (' + now[0].y + ' @ ' + now[0].x + ')');
  ok(now[0].legendgroup === q4.legendgroup && now[0].showlegend === false, 'T1: it toggles with its line');
  ok((T1.layout.annotations || []).some(a => /now \(q4\): current value differs from the newest recorded run value/.test(a.text)),
     'T1: the chart says it in words');
  ok(!(IRB.layout.annotations || []).some(a => /current value differs/.test(a.text)), 'T1: a chart with no differing value says nothing');
  ok(/current value differs from the newest recorded run value/.test(now[0].customdata[0][1]), 'T1: and so does its hover');

  // T2: hollow markers off the line
  const line = IRB.data.find(t => !t.meta);
  ok(line.y.length === 2 && line.y.every(v => v === 0.97622), 'T2: the step line is unchanged');
  const lo = IRB.data.find(t => t.meta === 'left_out');
  ok(lo && lo.mode === 'markers' && [].concat(lo.marker.symbol)[0] === 'circle-open' && lo.y[0] === 0.97968,
     'T2: the left-out run value is a hollow marker off the line');
  ok(lo.customdata[0][1] === 'saved by #1124 · not in the next recorded state (#1125 still had 0.97622)',
     'T2: its hover names the run that saved it and the one that still had the earlier value (' + lo.customdata[0][1] + ')');
  ok(/^saved by #1130 · not confirmed before the value came back \(#1140 came back to 0\.97622\)$/.test(lo.customdata[1][1]),
     'T2: an excursion says so (' + lo.customdata[1][1] + ')');
  ok(lo.legendgroup === line.legendgroup && lo.showlegend === false, 'T2: it toggles with its line');
  // a click on it opens the run that saved it, in the inspector
  IRB.host.__h.plotly_click[0]({ points: [{ customdata: lo.customdata[0] }] });
  ok(s.ajax.indexOf('/dataset/u:1124?via=saved') >= 0, 'T2: a click opens the run that saved it (' + s.ajax + ')');

  // T7: no zoom toast
  ok(T1.config && T1.config.showTips === false && IRB.config.showTips === false, 'T7: Plotly’s zoom-out tip is off');

  // T4
  const t = s.w.ChipStatus.calAgeRunTitle({ last_run_change_run: { run: 989, experiment: '25_T1' } }, '19 days ago');
  ok(/run #989 25_T1’s saved state 19 days ago/.test(t) && /not named as its writer/.test(t),
     'T4: the age names the run its value comes from (' + t + ')');
  ok(/a run’s saved state/.test(s.w.ChipStatus.calAgeRunTitle({}, '1 day ago')), 'T4: no run known -> no run named');

  console.log('trends_witness_selfcheck ok (' + n + ' assertions)');
  process.exit(0);
})().catch(e => { console.error('FAIL: ' + (e && e.message || e)); process.exit(1); });
