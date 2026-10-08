// Show all uses the clicked drawer's path and purges its previous chart.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}
const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/app.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_field_history_ledger.html'), 'utf8');
const source = app.slice(app.indexOf('window.FieldHistory = (function () {'),
  app.indexOf('})();', app.indexOf('window.FieldHistory = (function () {')) + 5);
const button = template.match(/<button\b[^>]*class="fh-show-all"[\s\S]*?<\/button>/)[0];
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }
function html(field, all) {
  return '<div class="fh-result" data-path="' + field + '">' + (all ? 'All points' : 'Recent points') + '</div>'
    + '<div id="fh-chart" class="js-plotly-plot"></div>'
    + '<script id="fh-chart-data" type="application/json">'
    + JSON.stringify([{ t: '2026-01-01', v: 1 }, { t: '2026-01-02', v: 2 }]) + '</script>'
    + (all ? '' : button.replace('{{ dot_path }}', field).replace('{{ total }}', '3'));
}
function world() {
  const dom = new JSDOM('<!doctype html><body><button id="anchor">Open</button><div id="inspector-pane"></div></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'https://example.invalid/' });
  const w = dom.window;
  const requests = [], purges = [], plots = [], times = [], processed = [];
  w.fetch = function (url) {
    requests.push(url);
    const u = new URL(url, 'https://example.invalid/');
    return w.Promise.resolve({ text: () => w.Promise.resolve(html(u.searchParams.get('path'), u.searchParams.get('all') === '1')),
      json: () => w.Promise.resolve({ ok: false }) });
  };
  w.PlotHost = { purgeWithin(container) {
    container.querySelectorAll('.js-plotly-plot').forEach(chart => {
      purges.push({ container, chart, attached: chart.isConnected && container.contains(chart) });
    });
  } };
  w.Plotly = { newPlot(mount) { plots.push(mount); return w.Promise.resolve(); } };
  w.applyLocalTimes = container => times.push(container);
  w.htmx = { process: container => processed.push(container) };
  new w.Function(source).call(w);
  const inspector = w.document.getElementById('inspector-pane');
  const anchor = w.document.getElementById('anchor');
  function click(container) {
    const btn = container.querySelector('.fh-show-all');
    assert.ok(btn, 'precondition: Show all exists');
    new w.Function(btn.getAttribute('onclick')).call(btn);
  }
  return { dom, w, inspector, anchor, requests, purges, plots, times, processed, click };
}
async function routing() {
  const x = world();
  const { w, inspector, requests, click } = x;
  const field = 'qubits.q1.f_01';
  inspector.innerHTML = html(field, false);
  click(inspector);
  await settle();
  check(requests[0] === '/field/history?path=' + encodeURIComponent(field) + '&all=1',
    'H1: an inspector with no popover requests its own path and all=1');
  check(inspector.textContent.includes('All points') && !inspector.querySelector('.fh-show-all'),
    'H1: the inspector receives the full history');
  check(!w.document.getElementById('field-history-panel'), 'H1: no hidden popover is created');
  check(x.times.includes(inspector) && x.processed.includes(inspector) &&
    x.plots.some(chart => inspector.contains(chart)), 'H1: times, links and chart refresh in the inspector');

  w.FieldHistory.open(x.anchor, 'qubits.q2.T1', null);
  await settle();
  const popover = w.document.getElementById('field-history-panel');
  const before = popover.innerHTML;
  inspector.innerHTML = html(field, false);
  click(inspector);
  await settle();
  check(requests.at(-1).includes('path=' + encodeURIComponent(field)),
    'H2: an inspector ignores another open popover path');
  check(popover.innerHTML === before && inspector.textContent.includes('All points'),
    'H2: expanding the inspector leaves the popover content intact');
  click(popover);
  await settle();
  check(requests.at(-1) === '/field/history?path=qubits.q2.T1&all=1', 'H3: a popover uses its own button path');
  check(popover.textContent.includes('All points') && popover.style.display === 'block',
    'H3: the full history replaces the visible popover');

  w.FieldHistory.close();
  const count = requests.length;
  const orphan = w.document.createElement('button');
  orphan.setAttribute('data-value', '1');
  w.FieldHistory.revertTo(orphan);
  check(requests.length === count, 'H4: close clears the fallback path for a button without data-path');
  inspector.innerHTML = html(field, false);
  click(inspector);
  await settle();
  check(inspector.textContent.includes('All points') && popover.style.display === 'none',
    'H4: an inspector works after a different popover closes');

  let release;
  w.fetch = () => new w.Promise(resolve => { release = resolve; });
  inspector.innerHTML = html(field, false);
  click(inspector);
  inspector.innerHTML = '<p>Another view</p>';
  release({ text: () => w.Promise.resolve(html(field, true)) });
  await settle();
  check(inspector.textContent === 'Another view', 'H5: a late expansion cannot overwrite a new inspector view');
  x.dom.window.close();
}
async function purge() {
  for (const surface of ['popover', 'inspector']) {
    const x = world();
    const field = 'qubits.q1.f_01';
    let container;
    if (surface === 'popover') {
      x.w.FieldHistory.open(x.anchor, field, null);
      await settle();
      container = x.w.document.getElementById('field-history-panel');
    } else {
      container = x.inspector;
      container.innerHTML = html(field, false);
    }
    const previous = container.querySelector('#fh-chart');
    x.purges.length = 0;
    x.click(container);
    await settle();
    check(x.purges.some(p => p.container === container && p.chart === previous && p.attached),
      'P1: Show all purges the attached previous chart in the ' + surface);
    check(!previous.isConnected && x.plots.some(chart => chart !== previous && container.contains(chart)),
      'P2: the ' + surface + ' draws the replacement chart after purging');
    x.dom.window.close();
  }
}
(async function () {
  const mode = process.argv[2];
  if (!mode || mode === 'routing') await routing();
  if (!mode || mode === 'purge') await purge();
  console.log('field_history_show_all_selfcheck: all ' + checks + ' checks passed');
})().catch(e => { console.error(e.stack || e); process.exit(1); });
