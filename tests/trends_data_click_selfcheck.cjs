/* QA F19 -- a point on a Datasets > Trends chart opens its run.
 *
 * The Chip Status and Param History trends open the run behind a point in the
 * inspector (docs/204); the Datasets > Trends charts (_trends_data.html) bound
 * no plotly_click at all, and their traces carried no run uid, so a click did
 * nothing. Drives the SHIPPED chart script of the template (its one Jinja
 * placeholder filled with a trend payload) under jsdom, with a Plotly-like
 * _plotlyRender that gives the element an .on() registry, as Plotly.newPlot
 * does. Pinned:
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
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const TPL = path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', '_trends_data.html');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(fn, ms) { const t = Date.now(); while (!fn() && Date.now() - t < (ms || 2000)) await tick(5); await tick(30); }

function chartScript(trend) {
  const src = fs.readFileSync(TPL, 'utf8');
  const i = src.lastIndexOf('<script>');
  const j = src.indexOf('</script>', i);
  const body = src.slice(i + '<script>'.length, j);
  if (body.indexOf('{{ trend_data }}') < 0) throw new Error('the chart script no longer reads {{ trend_data }}');
  return body.replace('{{ trend_data }}', JSON.stringify(trend));
}

(async () => {
  const trend = {
    runs: [{ run_id: 3268, uid: 'kh:3268' }, { run_id: 3270, uid: 'kh:3270' }, { run_id: 3271, uid: 'kh:3271' }],
    series: [{ qubit: 'q1', metric: 'opt_amp', values: [0.1, 0.2, 0.3] },
             { qubit: 'q2', metric: 'opt_amp', values: [0.4, null, 0.5] }],
  };
  const dom = new JSDOM('<!doctype html><html><body><div id="table-pane"><div id="trends-content">' +
    trend.series.map((s, i) => '<div id="trend-chart-' + i + '" class="trend-mini-chart"></div>').join('') +
    '</div></div><div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/trends', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.UI_CONFIG = { plotly: { colorway: ['#111', '#222'],
    trendsMini: { margin: {}, xTickAngle: 0, xTickFont: {}, yTickFont: {}, height: 100 } } };
  // a moving-average trace BEFORE the data trace, as trendStatTraces returns
  w.trendStatTraces = (x) => [{ x: x, y: [0, 0, 0], name: 'moving avg' }];
  const drawn = [];
  w._plotlyRender = function (el, data) {
    el.data = data;
    el.__handlers = {};
    el.on = function (ev, fn) { (el.__handlers[ev] = el.__handlers[ev] || []).push(fn); };
    drawn.push(el);
    return Promise.resolve(el);
  };
  const ajax = [];
  w.htmx = { ajax: function (verb, url, opts) { ajax.push({ verb, url, opts }); } };
  w.eval(chartScript(trend));
  await until(() => drawn.length >= 2 && drawn.every((el) => el.__handlers.plotly_click));

  const el = w.document.getElementById('trend-chart-0');
  const dataTrace = el.data && el.data[el.data.length - 1];
  ok(dataTrace && JSON.stringify(dataTrace.customdata) === JSON.stringify(['kh:3268', 'kh:3270', 'kh:3271']),
     'the data trace carries each run uid as customdata (' + JSON.stringify(dataTrace && dataTrace.customdata) + ')');
  ok(drawn.length === 2 && drawn.every((d) => (d.__handlers.plotly_click || []).length === 1),
     'one plotly_click handler per drawn chart (' + drawn.map((d) => (d.__handlers.plotly_click || []).length) + ')');

  const click = (evt) => el.__handlers.plotly_click.forEach((fn) => fn(evt));
  click({ points: [{ curveNumber: 0, customdata: undefined }, { curveNumber: 1, customdata: 'kh:3270' }] });
  ok(ajax.length === 1 && ajax[0].verb === 'GET' && ajax[0].url === '/dataset/kh:3270',
     'a click opens the run the data point names, past the statistics point (' + JSON.stringify(ajax) + ')');
  ok(ajax.length === 1 && ajax[0].opts.target === '#inspector-pane' && ajax[0].opts.source === '#inspector-pane',
     'in the inspector pane, never #table-pane (docs/204) (' + JSON.stringify(ajax[0] && ajax[0].opts) + ')');

  ajax.length = 0;
  click({});
  click({ points: [] });
  click({ points: [{ customdata: null }, { curveNumber: 0 }] });
  ok(ajax.length === 0, 'a click with no run uid does nothing (' + ajax.length + ' calls)');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
