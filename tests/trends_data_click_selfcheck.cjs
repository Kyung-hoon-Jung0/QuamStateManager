/* QA F19 -- a point on a Datasets > Trends chart opens its run.
 *
 * The Chip Status and Param History trends open the run behind a point in the
 * inspector (docs/204); the Datasets > Trends charts bound no plotly_click and
 * their traces carried no run uid, so a click did nothing. Since P3 the charts
 * are drawn by window.DatasetTrends (app.js) from /trends/series; this drives
 * the SHIPPED shell + app.js under jsdom (tests/trends_view_harness.cjs), with
 * a Plotly-like _plotlyRender that gives the element an .on() registry, as
 * Plotly.newPlot does. Pinned:
 *   1. the data trace's customdata is the runs' uids, in order;
 *   2. exactly one plotly_click handler per drawn chart;
 *   3. a click whose FIRST point is a statistics trace (no uid -- 'x unified'
 *      puts the moving average there) still opens the run the data point
 *      names, in #inspector-pane (never #table-pane, docs/204);
 *   4. a click with no uid-carrying point does nothing.
 * Run: node tests/trends_data_click_selfcheck.cjs (driven by
 * tests/test_trends_data_click.py). Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
try { require('jsdom'); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const H = require('./trends_view_harness.cjs');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

(async () => {
  const W = H.world();
  const p = H.payload(6, [{ q: 'q1', m: 'opt_amp', v: [0.1, 0.2, 0.3, 0.25, 0.15, 0.2] },
                          { q: 'q2', m: 'opt_amp', v: [0.4, null, 0.5, 0.45, 0.4, 0.41] }]);
  p.runs = p.runs.map((r, i) => [3268 + i, r[1], 'kh:' + (3268 + i)]);
  W.answers.push({ body: p });
  W.mount();
  await H.until(() => W.draws.length >= 2 && W.draws.every((d) => d.el.__handlers.plotly_click));

  const el = W.w.document.getElementById('trend-chart-0');
  const data = el.data || [];
  const dataTrace = data.find((t) => /^q1 \/ opt_amp$/.test(t.name));
  const want = ['kh:3268', 'kh:3269', 'kh:3270', 'kh:3271', 'kh:3272', 'kh:3273'];
  ok(dataTrace && JSON.stringify(dataTrace.customdata) === JSON.stringify(want),
     'the data trace carries each run uid as customdata (' + JSON.stringify(dataTrace && dataTrace.customdata) + ')');
  ok(data[0] && !data[0].customdata, 'fixture: a statistics trace (no uid) is drawn before the data trace');
  ok(W.draws.length === 2 && W.draws.every((d) => (d.el.__handlers.plotly_click || []).length === 1),
     'one plotly_click handler per drawn chart (' + W.draws.map((d) => (d.el.__handlers.plotly_click || []).length) + ')');

  const clicks = () => W.ajax.filter((a) => /^\/dataset\//.test(a.url));
  const click = (evt) => el.__handlers.plotly_click.forEach((fn) => fn(evt));
  click({ points: [{ curveNumber: 0, customdata: undefined }, { curveNumber: 3, customdata: 'kh:3270' }] });
  ok(clicks().length === 1 && clicks()[0].verb === 'GET' && clicks()[0].url === '/dataset/kh:3270',
     'a click opens the run the data point names, past the statistics point (' + JSON.stringify(clicks()) + ')');
  ok(clicks().length === 1 && clicks()[0].opts.target === '#inspector-pane' && clicks()[0].opts.source === '#inspector-pane',
     'in the inspector pane, never #table-pane (docs/204) (' + JSON.stringify(clicks()[0] && clicks()[0].opts) + ')');

  const before = clicks().length;
  click({});
  click({ points: [] });
  click({ points: [{ customdata: null }, { curveNumber: 0 }] });
  ok(clicks().length === before, 'a click with no run uid does nothing (' + (clicks().length - before) + ' calls)');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
