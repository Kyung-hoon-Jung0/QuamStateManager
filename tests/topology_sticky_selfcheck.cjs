/* The shipped renderer keeps all map controls in the map's sticky band. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="topo-hero" class="topo-hero"></div>', {
  runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/'
});
const win = dom.window;
win.htmx = {ajax() {}};
win.fetch = () => new win.Promise(() => {});
let scrolls = 0;
win.scrollTo = () => scrolls++;
win.HTMLElement.prototype.scrollIntoView = () => scrolls++;
const read = f => fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static', f), 'utf8');
new win.Function(['app.js', 'topo-graph.js', 'chip-status.js'].map(read).join('\n;\n')).call(win);
win.ChipStatus.mount({topo: {
  nodes: [{id: 'q1', grid_location: '0,0', T1: 2e-5},
          {id: 'q2', grid_location: '1,0', T1: 3e-5}], edges: []
}, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {}});
const host = win.document.getElementById('topo-hero');
function check() {
  const band = host.querySelector(':scope > .topo-hero-controls');
  assert.ok(band, 'map must own the sticky controls');
  assert.ok(band.querySelector('.topo-hero-bar [data-hero-metric]'));
  assert.ok(band.querySelector('.topo-hero-zoomctl input[type="range"]'));
  assert.equal(band.querySelectorAll('[data-hero-zoom]').length, 3);
  assert.ok(band.querySelector('[data-hero-compact]'));
  assert.ok(host.querySelector(':scope > .topo-hero-map'), 'map must be a sibling, not an overflow ancestor');
  assert.equal(host.querySelector('.topo-hero-scroll').contains(band), false);
}
check();
host.querySelector('[data-hero-metric="T1"]').click();
check();
assert.equal(host.querySelector('[data-hero-metric="T1"]').getAttribute('aria-selected'), 'true');
assert.match(host.querySelector('.topo-hero-val').textContent, /20/);
host.querySelector('[data-hero-compact]').click();
check();
assert.equal(scrolls, 0, 'switching values must not scroll the page');
dom.window.close();
console.log('all checks passed');
