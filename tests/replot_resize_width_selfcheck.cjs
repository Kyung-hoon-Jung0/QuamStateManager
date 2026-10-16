'use strict';
// v1.5.1 (D3 class R + H). An open reproduced figure resized in WIDTH (window 1600 -> 1280)
// kept its 1600 height, stretching the lab aspect until the run was reopened. A width change
// now re-derives the size a fresh open at the new width gives; a pane-HEIGHT change alone still
// changes nothing (the v1.5.0 customer rule, also pinned by replot_fit_pane_selfcheck.cjs).
// H: a figure seated by the lab's own panel positions keeps its aspect below 320 px (a very
// wide strip was stretched by the floor). Drives the real tile fetcher + PlotHost.resizeWithin.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html data-theme="dark"><body>' +
    '<div id="pane"><div class="ds-interactive-list" id="list" style="--ds-cols:1">' +
    '<div class="ds-interactive-fig"><div class="ds-interactive-plot" id="a" style="min-height:340px"></div></div>' +
    '<div class="ds-interactive-fig"><div class="ds-interactive-plot" id="b" style="min-height:340px"></div></div>' +
    '</div></div></body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const hostStart = source.indexOf('window.PlotHost = (function () {');
const hostEnd = source.indexOf('/* Enforce the purge rule', hostStart);
const start = source.indexOf('function _fetchInteractiveFig(');
const end = source.indexOf('function _fetchAndRenderPlot(', start);
assert(hostStart > 0 && hostEnd > hostStart && start > 0 && end > start);

let paneWidth = 1237, paneHeight = 520;
Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => paneWidth });
Object.defineProperty(w.HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => paneHeight });
Object.defineProperty(w.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return w.document.body; } });

// Plotly 2.35.2's width relayout implies autosize=null and pins the other dimension
// (the same faithful fake as plothost_selfcheck.cjs); dotted / indexed keys are set in place.
const relayouts = [];
function setPath(obj, key, value) {
    const parts = key.replace(/\[(\d+)\]/g, '.$1').split('.');
    let at = obj;
    parts.slice(0, -1).forEach(p => { if (at[p] === undefined || at[p] === null) at[p] = {}; at = at[p]; });
    at[parts[parts.length - 1]] = value;
}
w.Plotly = {
    relayout(el, upd) {
        relayouts.push({ el, upd });
        el.layout = el.layout || {};
        el._fullLayout = el._fullLayout || {};
        Object.keys(upd).forEach(k => setPath(el.layout, k, upd[k]));
        if (typeof upd.width === 'number') {
            delete el.layout.autosize;
            el._fullLayout.width = upd.width;
            if (typeof upd.height !== 'number') el.layout.height = el._fullLayout.height;
        }
        if (typeof upd.height === 'number') el._fullLayout.height = upd.height;
        return Promise.resolve();
    },
    purge() {},
};
w._plotlyRender = (el, data, layout) => {
    el.classList.add('js-plotly-plot');
    el.layout = JSON.parse(JSON.stringify(layout));
    el.data = data;
    el._fullLayout = { width: el.clientWidth, height: layout.height };
    return Promise.resolve();
};
let payload;
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(JSON.parse(JSON.stringify(payload))) });
w._pruneInteractiveTiles = () => {};
w._purgeInteractiveTile = () => {};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'), 'utf8'));
w.eval(source.slice(hostStart, hostEnd));
w.eval(source.slice(start, end));

const flush = () => new Promise(resolve => setTimeout(resolve, 30));
async function mount(id, body) {
    payload = body;
    const tile = w.document.getElementById(id);
    tile.innerHTML = '';
    tile.removeAttribute('data-rendered');
    tile.setAttribute('data-endpoint', 'replot/plot');
    w._fetchInteractiveFig(tile, 'sample', 'figure', 0, { _gen: 0 });
    await flush();
    return tile.querySelector('.js-plotly-plot');
}
// a 15 x 9 in lab figure seated by its own panel positions, with every scaled text kind
const lab = { data: [{ type: 'scatter', x: [0, 1], y: [0, 1] }], source_aspect: 0.6, source_size_inches: [15, 9],
    layout: { height: 720, font: { size: 10 }, title: { text: 'T', font: { size: 12 } }, legend: { font: { size: 10 } },
        xaxis: { tickfont: { size: 10 }, title: { text: 'x', font: { size: 10 } } },
        xaxis2: { tickfont: { size: 10 }, title: { text: 'x2', font: { size: 10 } } },
        annotations: [{ text: 'fit', font: { size: 9 }, xshift: 8, yshift: -12, xref: 'paper', yref: 'paper' },
                      { text: 'arrow', ax: 20, ay: -10 }] } };
const near = (a, b, tol, msg) => { assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`); n++; };
let n = 0;
const eq = (a, b, msg) => { assert.strictEqual(a, b, msg); n++; };
function texts(l) {
    return [l.font.size, l.title.font.size, l.legend.font.size, l.xaxis.tickfont.size, l.xaxis.title.font.size,
        l.xaxis2.tickfont.size, l.annotations[0].font.size, l.annotations[0].xshift, l.annotations[0].yshift,
        l.annotations[1].ax, l.annotations[1].ay];
}

(async () => {
    const list = w.document.getElementById('list');
    paneWidth = 1237;
    const a = await mount('a', lab);
    eq(a.layout.height, 742, 'opened at 1600: the lab aspect at the 1237 px row');
    // the window narrows to 1280: the open figure follows its width
    paneWidth = 919;
    w.PlotHost.resizeWithin(list);
    await flush();
    const fresh = await mount('b', lab);
    eq(fresh.layout.height, 551, 'a fresh open at 1280 draws the lab aspect at 919 px');
    near(a.layout.height, fresh.layout.height, 2, 'resized 1600 -> 1280 is as tall as a fresh 1280 open');
    near(a._fullLayout.height, fresh.layout.height, 2, '...and drawn that tall');
    eq(a.style.height, fresh.style.height, 'the plot element follows the new height');
    eq(a._fullLayout.width, 919, 'the width follows the row');
    const ta = texts(a.layout), tf = texts(fresh.layout);
    ta.forEach((v, i) => near(v, tf[i], 1e-6, 'text size / offset #' + i + ' equals a fresh open'));
    eq(a.layout.width, undefined, 'layout.width is handed back absent (the window-resize path stays alive)');
    eq(a.layout.height, 551, 'the new height is the declared one from now on');
    eq(a.parentElement.style.minHeight, '340px', 'a figure taller than the skeleton keeps its 340 px reserve');
    // a pane-HEIGHT change alone (the split bar dragged) changes nothing
    const before = relayouts.length, h = a.layout.height, sizes = JSON.stringify(texts(a.layout));
    for (const ph of [176, 466, 816]) {
        paneHeight = ph;
        w.PlotHost.resizeWithin(list);
        await flush();
    }
    eq(relayouts.length, before, 'a pane-height change alone relayouts nothing');
    eq(a.layout.height, h, 'and the figure keeps its height');
    eq(JSON.stringify(texts(a.layout)), sizes, 'and its text sizes');
    // and back to 1600: the 1600 figure again
    paneWidth = 1237;
    w.PlotHost.resizeWithin(list);
    await flush();
    eq(a.layout.height, 742, 'widened back to 1600: the 1600 height again');
    near(a.layout.font.size, 10 * Math.min(1237 / 1440, 742 / 864) * 96 / 72, 1e-6, '...and the 1600 text size');

    // H: a very wide strip of panels keeps its aspect below the old 320 px floor
    const strip = { data: [], source_aspect: 0.1761, source_size_inches: [15.48, 2.73], layout: { height: 320, font: { size: 10 } } };
    paneWidth = 1237;
    const s = await mount('b', strip);
    eq(s.layout.height, 218, 'H: a 15.5 x 2.7 in strip is 218 px tall at 1237 px, not stretched to 320');
    eq(s.parentElement.style.minHeight, '218px', 'H: its tile is not padded to the 340 px skeleton reserve');
    paneWidth = 919;
    w.PlotHost.resizeWithin(list);
    await flush();
    eq(s.layout.height, 162, 'H: and 162 px at 919 px (resized, same aspect)');
    eq(s.parentElement.style.minHeight, '162px', 'H: the tile reserve follows a resize');
    eq(w._replotHeight(0.1761, 1237, 320), 320, 'H: a figure laid out by Plotly margins (no source size) keeps the 320 px floor');
    eq(w._replotHeight(0.01, 1237, 320, true), 100, 'H: a degenerate strip stops at 100 px');
    console.log(`replot_resize_width_selfcheck: ${n} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
