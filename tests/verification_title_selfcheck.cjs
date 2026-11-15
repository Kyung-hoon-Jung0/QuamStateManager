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
// the sentence beside the chip: the same words as the title, a kit gap class, why and what would verify
const hints = [
  [{clickable: null}, 'No click contract is recorded for this kind of map, so nothing can verify a click on it: a click here sets no parameter whose saved value could be checked.'],
  [{clickable: null, display_only_reason: 'this run wrote no state: click-to-apply off'},
    'This run wrote no state: click-to-apply off. No click on this map is verified on this run.'],
  // reasons no later run can clear get the same honest sentence, never a promise of a verifying run
  [{clickable: null, display_only_reason: 'defaulted fields feed a marker: f'},
    'Defaulted fields feed a marker: f. No click on this map is verified on this run.'],
  [{clickable: null, display_only_reason: 'no node fit to compare; raw coordinates have no verified map write contract'},
    'No node fit to compare; raw coordinates have no verified map write contract. No click on this map is verified on this run.'],
  [{clickable: {}}, 'A click is verified once the node of this run saved a value for each parameter the click sets and the reading SM takes from the figure matches that value.'],
  [{clickable: {n_verified: 1, targets: [{path: 'a'}, {path: 'b'}]}},
    'The node of this run saved a value for only some of the parameters the click sets, so the click is not verified. A click is verified once the node of this run saved a value for each parameter the click sets and the reading SM takes from the figure matches that value.'],
];
for (const [extra, sentence] of hints) {
  w.MapTools.mount(tile, w.document.getElementById('plot'), Object.assign({
    data: [{type: 'heatmap', x: [0, 1], y: [0, 1], z: [[0, 1], [1, 0]]}]}, extra));
  const chip = tile.querySelector('.map-tools-chip'), hint = tile.querySelector('[data-verify-hint]');
  assert.equal(hint.textContent, sentence); n++;
  assert.equal(chip.title, sentence, 'title and visible sentence are one source'); n++;
  assert(hint.classList.contains('ui-inline-hint'), 'the sentence carries the gap class'); n++;
  tile._mapTools.destroy();
}
const kit = fs.readFileSync(path.join(web, 'static/ui-kit.css'), 'utf8');
assert(/\.ui-inline-hint\s*\{[^}]*margin-left:\s*var\(--ui-gap-\d\)/.test(kit), 'the gap is a kit token'); n++;
dom.window.close();
console.log('verification title passed (' + n + ' assertions)');
