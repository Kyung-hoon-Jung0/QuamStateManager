'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const web = path.join(__dirname, '..', 'quam_state_manager/web');
const dom = new JSDOM('<body>' + fs.readFileSync(path.join(web, 'templates/_map_tools.html'), 'utf8') +
  '<div id="tile"><div class="ds-interactive-fig-title"></div><div id="plot"></div></div>',
  {url: 'http://localhost/', runScripts: 'outside-only'});
const w = dom.window;
Object.assign(w, {AbortController, structuredClone});
w.fetch = () => new Promise(() => {});
w.Plotly = {react: () => Promise.resolve(), purge() {}};
w.PlotTheme = {houseLayout: l => l};
w.eval(fs.readFileSync(path.join(web, 'static/map-tools.js'), 'utf8'));
const tile = w.document.getElementById('tile');
let n = 0;
for (const clickable of [null, {}, {verification_note: 'No saved values'},
  {n_verified: 1, targets: [{path: 'a'}, {path: 'b'}]}]) {
  w.MapTools.mount(tile, w.document.getElementById('plot'), {
    data: [{type: 'heatmap', x: [0, 1], y: [0, 1], z: [[0, 1], [1, 0]]}], clickable
  });
  const chip = tile.querySelector('.map-tools-chip');
  assert.equal(chip.textContent, 'not verified on this run'); n++;
  assert(chip.title.length > 0, 'title is not empty'); n++;
  assert.equal(chip.getAttribute('aria-label'), chip.textContent + ': ' + chip.title, 'same words without a mouse'); n++;
  tile._mapTools.destroy();
}
dom.window.close();
console.log('verification title passed (' + n + ' assertions)');
