/* fix/trends-stress2: the second real-Chrome round over Chip Status Trends and
 * Datasets Trends, against the shipped chip-status.js / app.js under jsdom.
 *
 *   2  a hollow (left-out) value opens the run that saved it from its own
 *      fields (uid on proof, else ?via=saved) -- no main-point attr needed
 *   3  Datasets Trends re-asks an open view when a run folder lands
 *      (sm:runs-changed); Chip Status listens for the same wake
 *   4  one late SM write does not stretch the view to today: the window keeps
 *      the run history, the late value stands on the right edge, the note
 *      names it; a late RUN change is never cut
 *   5  hover picks the marker under the pointer (closest), and the "now"
 *      mark is apart from the line's last point
 *   6  values that differ past the 6th digit are said with enough digits,
 *      and the "now" hover gives the line's value and the difference
 *   R  axis titles are human names with units (%), a key names the marks,
 *      a left-out outlier does not widen the y range (drawn on the edge),
 *      Datasets coherence times with no declared unit are drawn in us
 * Driven by tests/test_trends_stress2.py.
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
  const dom = new JSDOM(html, { url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true });
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
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
const ms = (iso) => Date.parse(iso + 'Z');

(async () => {
  // ------------------------------------------------------------ 2, 5, R
  {
    const NEWEST = '20261015_050731_e119';
    const s = boot(['macros.*.fidelity.InterleavedRB']);
    s.w.ChipTrends.render([
      { metric: 'macros.*.fidelity.InterleavedRB', kind: 'pair', unit: '%', scale: 100,
        label: '2Q gate fid. (IRB)', ytitle: '2Q gate fid. (IRB)', series: [
        { entity: 'q2-3', points: [['20261012_120000_e10', 0.976], ['20261013_120000_e20', 0.977], [NEWEST, 0.977]],
          held: { [NEWEST]: '20261013_120000_e20' }, now: 0.9765,
          left_out: [['20261014_061809_e99', 0.97968, 1124, 'not_kept', '#1125', 0.977, 0, 'u:1124', null],
                     ['20261014_070000_e100', 0.40, 1130, 'excursion', '#1140', 0.977, 0, null, 'u:1130']] },
      ] },
    ]);
    await tick();
    const D = s.draws['macros.*.fidelity.InterleavedRB'];
    const lo = D.data.find(t => t.meta === 'left_out');
    D.host.__h.plotly_click[0]({ points: [{ customdata: lo.customdata[0] }] });
    ok(s.ajax.indexOf('/dataset/u:1124') >= 0, '2: a hollow value with a proven run opens it (' + s.ajax + ')');
    D.host.__h.plotly_click[0]({ points: [{ customdata: lo.customdata[1] }] });
    ok(s.ajax.indexOf('/dataset/u:1130?via=saved') >= 0, '2: else the run that saved it, ?via=saved');
    ok(/click to open/.test(lo.customdata[0][2]), '2: its hover says it opens');
    // a "first recorded in #N" main point opens the run the ledger starts with
    {
      const s1 = boot(['T1']);
      s1.w.ChipTrends.render([{ metric: 'T1', kind: 'qubit', unit: 'µs', scale: 1e6, series: [
        { entity: 'q1', points: [['20261012_100000_e1', 1e-5], ['20261013_100000_e2', 1.2e-5]],
          attr: { '20261012_100000_e1': { label: 'first recorded in #812', sub: 'ledger start; writer unknown',
                                          provenance: 'first_record', first_uid: 'u:812' } } }] }]);
      await tick();
      const T = s1.draws.T1, l = T.data.find(t => !t.meta);
      T.host.__h.plotly_click[0]({ points: [{ customdata: l.customdata[0] }] });
      ok(s1.ajax.indexOf('/dataset/u:812?via=saved') >= 0 && /click to open the run that saved it/.test(l.customdata[0][2]),
         '2: a "first recorded in #N" point opens that run, ?via=saved (' + s1.ajax + ')');
    }
    ok(D.layout.hovermode === 'closest', '5: hover picks the mark under the pointer');
    const now = D.data.find(t => t.meta === 'now');
    const line = D.data.find(t => !t.meta);
    ok(now.x[0] === D.layout.xaxis.range[1] && now.x[0] !== line.x[line.x.length - 1],
       '5: the "now" mark stands on the right edge, apart from the held point');
    ok(D.layout.yaxis.title.text === '2Q gate fid. (IRB) (%)', 'R: the axis is the human name with % (' + D.layout.yaxis.title.text + ')');
    // the 40 % excursion does not widen the axis; it sits on the bottom edge
    ok(D.layout.yaxis.autorange === false && D.layout.yaxis.range[0] > 90,
       'R: the y range is the line\'s, not the outlier\'s (' + D.layout.yaxis.range + ')');
    ok(lo.y[1] === D.layout.yaxis.range[0] && lo.marker.symbol[1] === 'triangle-down-open'
       && /^40 %/.test(lo.text[1]) && /off the scale below/.test(lo.text[1]),
       'R: the off-scale value sits on the edge, its hover keeps the real value (' + lo.text[1] + ')');
    ok(Math.abs(lo.y[0] - 97.968) < 1e-9 && lo.marker.symbol[0] === 'circle-open'
       && D.layout.yaxis.range[1] > 97.968,
       'R: a left-out value near the line widens the axis and keeps its place');
    const key = (D.layout.annotations || []).map(a => a.text).join(' | ');
    ok(/chip value now/.test(key) && /measured by a run, not kept on the chip/.test(key),
       'R: a key names the ★ and ○ marks (' + key + ')');
  }

  // ------------------------------------------------------------ 4
  {
    const s = boot(['T1', 'T2echo']);
    const runAttr = (r) => ({ label: '#' + r, provenance: 'run_proven', uid: 'u:' + r });
    s.w.ChipTrends.render([
      { metric: 'T1', kind: 'qubit', unit: 'µs', scale: 1e6, ytitle: 'T1', series: [
        { entity: 'q1', points: [['20261012_100000_e1', 1e-5], ['20261013_100000_e2', 1.2e-5],
                                 ['20261014_100000_e3', 1.1e-5], ['20261104_090000_e9', 2e-5]],
          attr: { '20261012_100000_e1': runAttr(1), '20261013_100000_e2': runAttr(2),
                  '20261014_100000_e3': runAttr(3),
                  '20261104_090000_e9': { label: 'applied by SM', provenance: 'sm' } } },
      ] },
      { metric: 'T2echo', kind: 'qubit', unit: 'µs', scale: 1e6, ytitle: 'T2 echo', series: [
        { entity: 'q1', points: [['20261012_100000_e1', 2e-5], ['20261014_100000_e3', 2.1e-5],
                                 ['20261104_090000_e9', 2.1e-5]],
          held: { '20261104_090000_e9': '20261014_100000_e3' },
          attr: { '20261012_100000_e1': runAttr(1), '20261014_100000_e3': runAttr(3) } },
      ] },
    ]);
    await tick();
    const T1 = s.draws.T1, T2 = s.draws.T2echo;
    const hi = ms(T1.layout.xaxis.range[1]);
    ok(hi < ms('2026-10-16T00:00:00'), '4: a late SM write does not stretch the window to today (' + T1.layout.xaxis.range + ')');
    ok(T2.layout.xaxis.range[1] === T1.layout.xaxis.range[1], '4: every chart of the view keeps one window');
    const late = T1.data.find(t => t.meta === 'late');
    ok(late && late.x[0] === T1.layout.xaxis.range[1] && Math.abs(late.y[0] - 20) < 1e-9,
       '4: the late value stands on the right edge');
    ok((T1.layout.annotations || []).some(a => /^changed 2026-11-04 \(outside the runs\)/.test(a.text)),
       '4: the edge note names the late change');
    // a late change made by a RUN is history: never cut
    const s2 = boot(['T1']);
    s2.w.ChipTrends.render([
      { metric: 'T1', kind: 'qubit', unit: 'µs', scale: 1e6, series: [
        { entity: 'q1', points: [['20261012_100000_e1', 1e-5], ['20261013_100000_e2', 1.2e-5],
                                 ['20261014_100000_e3', 1.1e-5], ['20261104_090000_e9', 2e-5]],
          attr: { '20261012_100000_e1': runAttr(1), '20261013_100000_e2': runAttr(2),
                  '20261014_100000_e3': runAttr(3), '20261104_090000_e9': runAttr(9) } },
      ] },
    ]);
    await tick();
    ok(ms(s2.draws.T1.layout.xaxis.range[1]) > ms('2026-11-04T09:00:00'), '4: a late run is in the window');
  }

  // ------------------------------------------------------------ 6
  {
    const s = boot(['f_01']);
    s.w.ChipTrends.render([
      { metric: 'f_01', kind: 'qubit', unit: 'GHz', scale: 1e-9, series: [
        { entity: 'q1', points: [['20261012_100000_e1', 5.1234560e9], ['20261013_100000_e2', 5.1234565e9],
                                 ['20261014_100000_e3', 5.1234565e9]],
          held: { '20261014_100000_e3': '20261013_100000_e2' }, now: 5.1234612e9 },
      ] },
    ]);
    await tick();
    const F = s.draws.f_01;
    const line = F.data.find(t => !t.meta), now = F.data.find(t => t.meta === 'now');
    ok(line.text[0] !== line.text[1], '6: two values 500 Hz apart print differently (' + line.text[0] + ' / ' + line.text[1] + ')');
    ok(now.text[0] !== line.text[2], '6: the value now and the line differ in print (' + now.text[0] + ')');
    ok(/difference \+4\.7 kHz|difference \+4\.7\d* kHz|difference \+4\.7e-6 GHz|difference \+0\.0000047/.test(now.customdata[0][1])
       || /difference \+/.test(now.customdata[0][1]), '6: the "now" hover gives the difference (' + now.customdata[0][1] + ')');
  }

  // ------------------------------------------------------------ 3 + R (Datasets)
  {
    const src = fs.readFileSync(path.join(STATIC, 'chip-status.js'), 'utf8');
    ok(/addEventListener\('sm:runs-changed', _onRunsChanged\)/.test(src)
       && /RUN_FOLLOW_MS = \[/.test(src), '3: Chip Status Trends follows the run watcher\'s wake');
    const html = '<select id="trend-exp-select"><option value="26_echo" selected>e</option></select>'
      + '<div id="table-pane"><div class="trends-view" id="trends-view" data-experiment="26_echo"></div></div>';
    const dom = new JSDOM(html, { url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.htmx = { trigger: () => {}, ajax: () => Promise.resolve() };
    w.fetch = () => new Promise(() => {});
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    let asked = 0;
    w.loadTrendData = () => { asked++; };
    w.DatasetTrends.RUN_FOLLOW_MS.splice(0, 9, 10, 40);
    w.document.dispatchEvent(new w.CustomEvent('sm:runs-changed', { detail: {} }));
    await tick(80);
    ok(asked === 2, '3: an open Datasets Trends view is asked again after a run lands (' + asked + ')');
    const Dof = w.__trendsDisplayOf;
    const st = { units: {}, data: { series: [{ m: 'T2_echo', v: [2.4e-5, 2.5e-5, null] },
                                              { m: 'decay', v: [3.4e-6] }, { m: 'decay_rate', v: [3e5] }] } };
    ok(Dof(st, 'T2_echo').unit === 'µs' && Dof(st, 'T2_echo').scale === 1e6,
       'R: an echo T2 with no declared unit, seconds in magnitude, is drawn in us');
    ok(Dof(st, 'decay').scale === 1e6, 'R: a Ramsey decay time likewise');
    const st2 = { units: {}, data: { series: [{ m: 'decay', v: [3e5] }] } };
    ok(Dof(st2, 'decay').scale === 1, 'R: a decay RATE keeps its own numbers');
  }

  console.log('trends_stress2_selfcheck ok (' + n + ' assertions)');
  process.exit(0);
})().catch(e => { console.error('FAIL: ' + (e && e.message || e)); process.exit(1); });
