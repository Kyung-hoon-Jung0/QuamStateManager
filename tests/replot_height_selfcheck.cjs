'use strict';
// A reproduced lab figure keeps its own aspect at the width the pane really has
// (the server height assumes a 1200 px pane). Drives the real tile fetcher.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="tile"></div></body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function _fetchInteractiveFig(');
const end = source.indexOf('function _fetchAndRenderPlot(', start);
let paneWidth = 0;
Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => paneWidth });
let payload, rendered;
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(JSON.parse(JSON.stringify(payload))) });
w._plotlyRender = (el, data, layout) => { rendered = { el, layout }; return Promise.resolve(); };
w.PlotHost = { observe: () => {} };
w._pruneInteractiveTiles = () => {};
w._purgeInteractiveTile = () => {};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'), 'utf8'));
w.eval(source.slice(start, end));

function render(width, body) {
    paneWidth = width;
    payload = body;
    rendered = null;
    const tile = w.document.getElementById('tile');
    tile.removeAttribute('data-rendered');
    tile.setAttribute('data-endpoint', 'replot/plot');
    w._fetchInteractiveFig(tile, 'sample', 'figure', 0, { _gen: 0 });
    return new Promise(resolve => setTimeout(() => resolve(rendered), 20));
}

const lab = { data: [], layout: { height: 720 }, source_aspect: 0.6 };  // a 15 x 9 in lab figure
(async () => {
    let n = 0;
    const eq = (a, b, msg) => { assert.strictEqual(a, b, msg); n++; };
    let r = await render(1000, lab);
    eq(r.layout.height, 600, 'a 1000 px pane draws the 15x9 figure 600 px tall');
    eq(r.el.style.height, '600px', 'the plot element follows the derived height');
    r = await render(1237, lab);
    eq(r.layout.height, 742, 'a wider pane draws it taller, same aspect');
    r = await render(491, lab);
    eq(r.layout.height, 320, 'a narrow pane stops at the 320 px floor');
    r = await render(0, lab);
    eq(r.layout.height, 720, 'no measured width: the server height stands');
    r = await render(1000, { data: [], layout: { height: 420 } });
    eq(r.layout.height, 420, 'a figure without a source aspect keeps the server height');
    r = await render(1000, { data: [], layout: {} });
    eq(r.layout.height, 360, 'nothing at all: the old 360 px default');
    eq(w._replotHeight(3, 1000, 720), 2200, 'a very tall figure stops at 2200 px');
    eq(w._replotHeight(NaN, 1000, 720), 720, 'NaN aspect is no aspect');
    eq(w._replotHeight(-0.5, 1000, 720), 720, 'a negative aspect is no aspect');
    eq(w._replotHeight('0.6', 1000, 720), 720, 'a string aspect is no aspect');
    console.log(`replot_height_selfcheck: ${n} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
