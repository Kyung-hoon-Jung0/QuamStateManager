'use strict';
// A reproduced lab figure that a column would shrink below ~3/4 of its own size takes the
// whole row (its text scales with the tile). Drives the real tile fetcher under jsdom.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div class="ds-interactive-list" style="--ds-cols:2">' +
    '<div class="ds-interactive-fig"><div id="tile"></div></div><div class="ds-interactive-fig"></div></div></body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function _fetchInteractiveFig(');
const end = source.indexOf('function _fetchAndRenderPlot(', start);
// A column is 450 px; the whole row is 900 px.
Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', { configurable: true, get() {
    const fig = this.closest && this.closest('.ds-interactive-fig');
    return fig && fig.classList.contains('ds-fig-span') ? 900 : 450;
} });
let payload, rendered;
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(JSON.parse(JSON.stringify(payload))) });
w._plotlyRender = (el, data, layout) => { rendered = { el, layout }; return Promise.resolve(); };
w.PlotHost = { observe: () => {} };
w._pruneInteractiveTiles = () => {};
w._purgeInteractiveTile = () => {};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'), 'utf8'));
w.eval(source.slice(start, end));

function render(body) {
    payload = body;
    rendered = null;
    const tile = w.document.getElementById('tile');
    tile.closest('.ds-interactive-fig').classList.remove('ds-fig-span');
    tile.removeAttribute('data-rendered');
    tile.setAttribute('data-endpoint', 'replot/plot');
    w._fetchInteractiveFig(tile, 'sample', 'figure', 0, { _gen: 0 });
    return new Promise(resolve => setTimeout(() => resolve(rendered), 20));
}

(async () => {
    let n = 0;
    const eq = (a, b, msg) => { assert.strictEqual(a, b, msg); n++; };
    const fig = w.document.querySelector('.ds-interactive-fig');
    const tall = { data: [], layout: { height: 2200 }, source_aspect: 2.18, source_size_inches: [9.13, 19.9] };
    let r = await render(tall);
    eq(fig.classList.contains('ds-fig-span'), true, 'a 9 in figure in a 450 px column takes the row');
    eq(r.layout.height, 1962, 'its height follows the 900 px row width');
    const small = { data: [], layout: { height: 480 }, source_aspect: 0.75, source_size_inches: [6, 4.5] };
    r = await render(small);
    eq(fig.classList.contains('ds-fig-span'), false, 'a 6 in figure keeps its column (450 px is 78% of its size)');
    eq(r.layout.height, 338, 'and its height follows the column');
    r = await render({ data: [], layout: { height: 720 }, source_aspect: 0.6 });
    eq(fig.classList.contains('ds-fig-span'), false, 'no recorded size (older cache): the column stands');
    const list = w.document.querySelector('.ds-interactive-list');
    list.style.setProperty('--ds-cols', '1');
    eq(w._replotNeedsRow([20, 10], 450, fig), false, 'one column: there is no row to take');
    list.style.setProperty('--ds-cols', '3');
    eq(w._replotNeedsRow([9.13, 19.9], 450, fig), true, 'three columns: the same rule');
    eq(w._replotNeedsRow([9.13, 19.9], 700, fig), false, '700 px shows a 9 in figure at 80%');
    eq(w._replotNeedsRow(null, 450, fig), false, 'no size: no decision');
    console.log(`replot_needs_row_selfcheck: ${n} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
