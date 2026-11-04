'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web');
const read = f => fs.readFileSync(path.join(root, 'static', f), 'utf8');
const dom = new JSDOM('<div id="qualibrate-running-slot"></div><div id="topo-health-tiles"></div>',
  {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
w.fetch = () => new Promise(() => {});
w.htmx = {process() {}, ajax() {}};
w.eval(read('app.js'));
const loadError = {target: Object.assign(w.document.createElement('div'), {id:'table-pane'}),
  xhr: {status:400, responseText:'<div class="load-failed-panel">Cannot open this folder</div>'}, shouldSwap:false};
w.document.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {detail:loadError}));
assert(loadError.shouldSwap && !loadError.isError, 'failed current-chip loads show their reason');
const slot = w.document.getElementById('qualibrate-running-slot');
function sample(html) {
  const detail = {target: slot, xhr: {status: 200, responseText: html}, serverResponse: html, shouldSwap: true};
  slot.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {bubbles: true, detail}));
  if (detail.shouldSwap) slot.innerHTML = html;
}
sample('<div>running</div>');
sample('');
assert(slot.textContent.includes('running'), 'one miss must retain the banner');
sample('<div>running</div>');
sample(''); sample('');
assert(slot.textContent.includes('running'), 'a hit resets consecutive misses');
sample('');
assert.equal(slot.textContent, '', 'three misses hide the banner');
const base = fs.readFileSync(path.join(root, 'templates', 'base.html'), 'utf8');
assert(/id="qualibrate-running-slot"[^>]*class="ui-running-overlay"/.test(base));
const css = read('ui-kit.css');
assert(/\.ui-running-overlay\s*\{[^}]*position:\s*fixed/.test(css), 'banner cannot push content');
w.eval(read('topo-graph.js'));
w.eval(read('chip-status.js'));
w.ChipStatus.mount({topo: {nodes: [{id:'q1'}], edges: [], summary:{}}, rawWiring:{}, defaultThresholds:{}, diagFindings:[], metricMeta:{}});
const tiles = [...w.document.querySelectorAll('#topo-health-tiles .topo-health-tile')];
for (const label of ['qubits below spec', 'CZ below spec']) {
  const tile = tiles.find(t => t.textContent.includes(label));
  assert(tile && tile.textContent.includes('—') && tile.textContent.includes('no data yet'), label);
}
console.log('offline3 passed');
dom.window.close();
