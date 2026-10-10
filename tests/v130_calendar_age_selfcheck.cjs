/* Calendar ages on real Chip Status tiles, with the shared display zone. */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const scripts = ['app.js', 'topo-graph.js', 'chip-status.js']
  .map(name => fs.readFileSync(path.join(root, name), 'utf8')).join('\n;\n');
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
for (const c of cases) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="topo-overview-tiles"></div></body></html>',
    { runScripts: 'outside-only', url: 'http://localhost/' });
  const win = dom.window;
  win.document.documentElement.setAttribute('data-sm-zone', c.zone);
  win.localStorage.setItem('quam_tz', 'Etc/GMT+12');
  win.Date.now = () => c.now_ms;
  win.fetch = () => new Promise(() => {});
  win.Function(scripts).call(win);
  win.ChipStatus.mount({ topo: {
    nodes: [{ id: 'q1', last_calibrated: c.run_ms, last_calibrated_from: 'run' }], edges: [],
  }, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {} });
  const tile = win.document.querySelector('.topo-card[data-tile-id="cal_age"]');
  assert.ok(tile, 'Calibration Age tile exists');
  assert.ok(tile.textContent.includes('oldest ' + c.label), c.zone + ': ' + tile.textContent);
  assert.ok(tile.querySelector('.topo-card-value').textContent.includes(c.label), c.label);
  dom.window.close();
}
console.log('Calendar age: ' + cases.length + ' fixed clocks passed');
