'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const base = path.join(__dirname, '../quam_state_manager/web/static');
const app = fs.readFileSync(path.join(base, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(base, 'style.css'), 'utf8');
const start = app.indexOf('window.SavedFigureFit = (function () {');
assert.ok(start >= 0, 'saved figures measure their visible pane');
const dom = new JSDOM('<body><div id="inspector-pane"><div id="ds-detail-root">' +
    '<header class="inspector-header"></header><nav class="dataset-tabs"></nav>' +
    '<div class="figure-card"><div class="figure-label"></div><img onclick="toggleFigureZoom(this)"></div>' +
    '</div></div></body>', {runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
const pane = w.document.getElementById('inspector-pane');
let paneHeight = 400;
Object.defineProperty(pane, 'clientHeight', {get: () => paneHeight});
pane.getBoundingClientRect = () => ({top: 300});
for (const [selector, height] of [['.inspector-header', 45], ['.dataset-tabs', 40], ['.figure-label', 35]]) {
    w.document.querySelector(selector).getBoundingClientRect = () => ({height});
}
w.eval(app.slice(start, app.indexOf('})();', start) + 5));
w.SavedFigureFit.init();
const root = w.document.getElementById('ds-detail-root');
assert.equal(root.style.getPropertyValue('--saved-figure-height'), '248px', 'height excludes the header, tabs and figure label');
paneHeight = 250;
w.SavedFigureFit.init();
assert.equal(root.style.getPropertyValue('--saved-figure-height'), '98px', 'a shorter split pane limits the image further');
const rule = css.match(/#ds-detail-root \.figure-card img\s*\{([^}]+)\}/);
assert.ok(rule && /max-height:\s*var\(--saved-figure-height/.test(rule[1]), 'saved images use the measured height limit');
assert.ok(/object-fit:\s*contain/.test(rule[1]), 'the complete image keeps its aspect ratio');
assert.ok(w.document.querySelector('img').getAttribute('onclick').includes('toggleFigureZoom'), 'click still opens full-size zoom');
console.log('6 checks passed');
dom.window.close();
