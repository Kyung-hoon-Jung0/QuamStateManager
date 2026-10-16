'use strict';
// v1.5.0 (customer): a reproduced figure keeps its full width-driven size whatever the pane
// height -- v1.4.4 narrowed it to the measured pane height, so resizing the panel changed the
// figure being read. Text still grows by at most 1.2x and shrinks with the canvas.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="ds-detail-root" style="--saved-figure-height:520px">' +
    '<div class="ds-interactive-list" style="--ds-cols:1"><div class="ds-interactive-fig"><div id="tile"></div></div></div>' +
    '</div></body></html>', { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function _fetchInteractiveFig(');
const end = source.indexOf('function _fetchAndRenderPlot(', start);
Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 1237 });
let paneHeight = 520;
Object.defineProperty(w.HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => paneHeight });
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
    tile.removeAttribute('data-rendered');
    tile.setAttribute('data-endpoint', 'replot/plot');
    w._fetchInteractiveFig(tile, 'sample', 'figure', 0, { _gen: 0 });
    return new Promise(resolve => setTimeout(() => resolve(rendered), 20));
}

(async () => {
    let n = 0;
    const eq = (a, b, msg) => { assert.strictEqual(a, b, msg); n++; };
    eq(typeof w._replotFitToPane, 'undefined', 'no fit-to-pane helper is left');
    const big = w._replotFigureLayout({ font: { size: 10 }, annotations: [] }, [8, 6], 1237, 928);
    eq(Math.round(big.font.size * 100) / 100, 16, 'a small figure on a wide row grows its text by 1.2x at most (10pt -> 16px)');
    const small = w._replotFigureLayout({ font: { size: 10 } }, [15, 9], 600, 360);
    eq(Math.round(small.font.size * 100) / 100, 5.56, 'shrinking still follows the canvas');
    const body = { data: [], layout: { height: 576, font: { size: 10 } }, source_aspect: 0.75, source_size_inches: [8, 6] };
    for (const h of [520, 200, 1400]) {
        paneHeight = h;
        w.document.getElementById('ds-detail-root').style.setProperty('--saved-figure-height', h + 'px');
        const r = await render(body);
        eq(r.layout.height, 928, 'pane ' + h + ' px: the figure keeps the height its width and aspect give');
        eq(r.el.style.width, '100%', 'pane ' + h + ' px: the figure keeps the full row width');
        eq(Math.round(r.layout.font.size * 100) / 100, 16, 'pane ' + h + ' px: the text size does not follow the pane');
    }
    console.log(`replot_fit_pane_selfcheck: ${n} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
