'use strict';
// A reproduced figure taller than the pane is fitted to it when it still shows at >= 3/4 of
// its own size; text grows by at most 1.2x. Pure helpers plus the real tile fetcher.
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
    const fit = w._replotFitToPane;
    eq(JSON.stringify(fit([8, 6], 0.75, 928, 520)), '{"width":693,"height":520}', 'an 8x6 in figure is fitted to a 520 px pane');
    eq(fit([8.6, 20.1], 2.34, 2200, 520), null, 'a tall multi-panel figure would drop below 3/4: it keeps its width');
    eq(fit([13, 10], 0.77, 952, 520), null, '13x10 in at 520 px is 54%: no fit');
    eq(fit([8, 6], 0.75, 480, 520), null, 'a figure that already fits is left alone');
    eq(fit([8, 6], 0.75, 928, NaN), null, 'no measured pane: no fit');
    eq(JSON.stringify(fit([5, 4], 0.8, 990, 200)), '{"width":400,"height":320}', 'a pane below 320 px fits to the 320 px floor');
    const big = w._replotFigureLayout({ font: { size: 10 }, annotations: [] }, [8, 6], 1237, 928);
    eq(Math.round(big.font.size * 100) / 100, 16, 'a small figure on a wide row grows its text by 1.2x at most (10pt -> 16px)');
    const small = w._replotFigureLayout({ font: { size: 10 } }, [15, 9], 600, 360);
    eq(Math.round(small.font.size * 100) / 100, 5.56, 'shrinking still follows the canvas');
    const r = await render({ data: [], layout: { height: 576, font: { size: 10 } }, source_aspect: 0.75,
                             source_size_inches: [8, 6] });
    eq(r.layout.height, 520, 'the mount fits a one-figure run to the pane');
    eq(r.el.style.width, '693px', 'and narrows the plot to keep the lab aspect');
    eq(Math.round(r.layout.font.size * 100) / 100, 12.03, 'text then follows the fitted canvas (10pt at 0.90)');
    console.log(`replot_fit_pane_selfcheck: ${n} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
