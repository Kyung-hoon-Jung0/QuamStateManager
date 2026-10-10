// Display pins for the value drawers and kind chips.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const dom = new JSDOM('<!doctype html><html><body><div id="param-history-drawer">'
  + '<div id="phd-chart"></div></div><button id="anchor"></button></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.htmx = { ajax() {}, trigger() {}, process() {} };
win.eval(fs.readFileSync(path.join(root, 'app.js'), 'utf8'));
const plots = [];
win.Plotly = {
  newPlot(el, traces, layout) {
    plots.push({ el, layout });
    return Promise.resolve();
  }, purge() {}, relayout() { return Promise.resolve(); }
};
win.requirePlotly = () => Promise.resolve();
win.document.getElementById('phd-chart').on = () => {};
const points = [
  { t: '2026-09-15T08:22:35Z', v: 0.000005509247852422442, trigger: 'save' },
  { t: '2026-09-16T08:22:35Z', v: 0.000006509247852422442, trigger: 'save' }
];
win.fetch = () => Promise.resolve({ ok: true, text: () => Promise.resolve(
  '<div class="fh-body"><div id="fh-chart"></div>'
  + '<script id="fh-chart-data" type="application/json">'
  + JSON.stringify(points) + '</script></div>') });
const wait = () => new Promise(resolve => setTimeout(resolve, 30));

(async function () {
  if (process.argv[2]) {
    const note = win.document.createElement('div');
    note.innerHTML = fs.readFileSync(process.argv[2], 'utf8');
    win.document.body.appendChild(note);
    win.SnapTime.setZone('Asia/Seoul');
    win.applyLocalTimes(note);
    assert.strictEqual(note.querySelector('[data-note="idle"]').textContent,
      'The ledger is not being kept current in this window; runs after 2026-09-15 17:22:35 (UTC+9) may be missing.',
      'A13: the rendered note uses the display zone and offset');
  }
  win.FieldHistory.open(win.document.getElementById('anchor'), 'qubits.q1.T1', null);
  await wait();
  const field = plots.find(p => p.el && p.el.id === 'fh-chart');
  assert(field, 'the value chart rendered');
  assert.strictEqual(field.layout.yaxis.automargin, true, 'A15: value chart reserves axis label room');
  win.paramHistoryRenderDrawerChart({ property: 'T1', ledger: true,
    values: points.map(p => ({ timestamp: p.t, value: p.v, trigger: p.trigger })) }, null);
  await wait();
  const param = plots.find(p => p.el === 'phd-chart');
  assert(param, 'the parameter chart rendered');
  assert.strictEqual(param.layout.yaxis.automargin, true, 'A15: parameter chart reserves title room');
  assert.strictEqual(param.layout.yaxis.title, 'T1');

  // Inspect the shipped stylesheet through CSSOM, without running a browser.
  const style = win.document.createElement('style');
  style.textContent = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
  win.document.head.appendChild(style);
  const rules = Array.from(style.sheet.cssRules);
  const writer = rules.find(r => r.selectorText === '.vh-table .fh-src').style;
  assert.strictEqual(writer.getPropertyValue('overflow-wrap'), 'normal', 'A6: writer words stay whole');
  assert.strictEqual(writer.getPropertyValue('word-break'), 'normal', 'A6: no forced word breaks');
  const backup = rules.find(r => r.selectorText === '.sv-kind-backup').style;
  assert.strictEqual(backup.getPropertyValue('border'), '1px solid var(--pico-muted-color)', 'A15: backup chip has a visible border');
  dom.window.close();
  console.log('all checks passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
