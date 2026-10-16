/* v1.5.1 (D3 class K): dark-theme artist contrast of reproduced lab figures.
 *
 *  1. Text written on a heatmap cell keeps the lab colour: the lab chose black for the bright
 *     cells, and mapping it to the light foreground put light text on yellow cells (a
 *     confusion matrix's diagonal). Text off the map still follows the theme.
 *  2. Marker sizes are points, like the text: they scale with the canvas by the text's factor
 *     (an IQ cloud's 4 pt dots drew as 8 px discs and blended into one mass).
 *  3. Map tools redraw from the traces as drawn, not the raw server traces.
 * Run: node tests/dark_artist_contrast_selfcheck.cjs [converted-lab-confusion.json]
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const FG = '#e6e9ef';
const dom = new JSDOM('<html data-theme="dark"><body><div id="plot" class="js-plotly-plot"></div></body></html>',
    { runScripts: 'outside-only' });
const w = dom.window;
w.document.documentElement.style.setProperty('--plot-axis-text', FG);
w.eval(fs.readFileSync(path.join(STATIC, 'plot-theme.js'), 'utf8'));
const H = w.PlotTheme.houseLayout;
let n = 0;
const eq = (a, b, m) => { assert.strictEqual(a, b, m); n++; };
const colour = (layout, i) => layout.annotations[i].font.color;

// -- 1. text on a heatmap ------------------------------------------------------------------
const recipe = [{ type: 'heatmap', x: ['g', 'e'], y: ['g', 'e'], z: [[0.9, 0.1], [0.2, 0.8]] }];
const recipeLayout = { annotations: [
    { x: 'g', y: 'g', text: '0.900', showarrow: false, font: { color: '#000' } },
    { x: 'e', y: 'g', text: '0.100', showarrow: false, font: { color: '#fff' } },
    { x: 'f', y: 'g', text: 'off the map', showarrow: false, font: { color: '#000' } }] };
let L = H(recipeLayout, recipe);
eq(colour(L, 0), '#000', 'a recipe confusion cell keeps its black text on the bright diagonal');
eq(colour(L, 1), '#fff', '...and its white text on a dark cell');
eq(colour(L, 2), FG, 'a category outside the matrix follows the theme');
eq(colour(H(recipeLayout), 0), FG, 'without the traces the old rule stands (near-black -> foreground)');

const lab = [{ type: 'heatmap', x: [0, 1], y: [0, 1], z: [[0.9, 0.1], [0.1, 0.9]], xaxis: 'x2', yaxis: 'y2' },
             { type: 'heatmap', x: [-0.5, 0.5, 1.5], y: [-0.5, 0.5, 1.5], z: [[1, 0], [0, 1]], xaxis: 'x3', yaxis: 'y3' },
             { type: 'heatmap', x: [10, 100], y: [0, 1], z: [[1, 2], [3, 4]], xaxis: 'x4', yaxis: 'y4' }];
const labLayout = { xaxis4: { type: 'log' }, annotations: [
    { x: 0, y: 0, xref: 'x2', yref: 'y2', showarrow: false, text: '90%', font: { color: '#000000' } },  // centre
    { x: 1.45, y: 1.45, xref: 'x2', yref: 'y2', showarrow: false, text: 'edge', font: { color: '#000000' } },
    { x: 1.6, y: 0, xref: 'x2', yref: 'y2', showarrow: false, text: 'beside', font: { color: '#000000' } },
    { x: 1.4, y: 0, xref: 'x3', yref: 'y3', showarrow: false, text: 'edges', font: { color: 'black' } },
    { x: 1.6, y: 0, xref: 'x3', yref: 'y3', showarrow: false, text: 'out', font: { color: 'black' } },
    { x: 0, y: 0, xref: 'x2 domain', yref: 'y2 domain', showarrow: false, text: 'domain', font: { color: '#000000' } },
    { x: 0, y: 0, xref: 'x2', yref: 'y2', text: 'arrow', font: { color: '#000000' } },
    { x: 0, y: 0, xref: 'x', yref: 'y', showarrow: false, text: 'other axes', font: { color: '#000000' } },
    { x: 1.9, y: 0, xref: 'x4', yref: 'y4', showarrow: false, text: 'log', font: { color: '#000000' } },
    { x: 2.6, y: 0, xref: 'x4', yref: 'y4', showarrow: false, text: 'log out', font: { color: '#000000' } }] };
L = H(labLayout, lab);
eq(colour(L, 0), '#000000', 'lab text on a cell centre keeps its colour');
eq(colour(L, 1), '#000000', 'half a cell beyond the last centre is still on the map');
eq(colour(L, 2), FG, 'beyond that it is off the map');
eq(colour(L, 3), 'black', 'cell EDGES bound the map too');
eq(colour(L, 4), FG, '...exactly');
eq(colour(L, 5), FG, 'a domain-placed note is not on a cell');
eq(colour(L, 6), FG, 'an arrow note (its text is elsewhere) follows the theme');
eq(colour(L, 7), FG, 'a note on axes without a heatmap follows the theme');
eq(colour(L, 8), '#000000', 'a log axis compares in log10 (1.9 sits in the 10..100 centres)');
eq(colour(L, 9), FG, '...and 2.6 is beyond them');

// the theme toggle re-derives the same colours
const el = w.document.getElementById('plot');
el.layout = { annotations: [{}, {}, {}] };
el._fullLayout = {};
el._houseSourceLayout = recipeLayout;
el._houseSourceData = recipe;
const relayouts = [];
w.Plotly = { relayout: (target, u) => relayouts.push(u), restyle: () => {} };
w.PlotTheme.retheme();
eq(relayouts[1]['annotations[0].font.color'], '#000', 'toggle: the cell text keeps the lab colour');
eq(relayouts[1]['annotations[2].font.color'], FG, 'toggle: off-map text follows the theme');

// a real lab confusion matrix, converted by the server (the Python driver passes it in)
if (process.argv[2]) {
    const view = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const themed = H(view.layout, view.data);
    const cells = themed.annotations.filter(a => /%$/.test(a.text));
    eq(cells.length, 4, 'the converted lab matrix carries its four cell texts');
    cells.forEach(a => {
        const src = view.layout.annotations.find(s => s.text === a.text && s.x === a.x && s.y === a.y);
        eq(a.font.color, src.font.color, 'converted lab cell ' + a.text + ' keeps ' + src.font.color);
    });
    assert.ok(cells.some(a => a.font.color === '#000000') && cells.some(a => a.font.color === '#ffffff'),
        'the lab matrix has both black and white cell text');
}

// -- 2. marker sizes follow the canvas like the text ------------------------------------------
const app = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const s = app.indexOf('function _fetchInteractiveFig('), e = app.indexOf('function _fetchAndRenderPlot(', s);
w.eval(app.slice(s, e));
const traces = [{ type: 'scattergl', marker: { size: 4, opacity: 0.2, line: { width: 1, color: '#1f77b4' } } },
                { type: 'scatter', marker: { size: [2, 4, null] } },
                { type: 'heatmap', z: [[1]] },
                { type: 'scatter', mode: 'lines', line: { width: 1.5 } }];
const before = JSON.stringify(traces);
const scaled = w._replotFigureData(traces, [15, 9], 1237, 742);
const k = w._replotFigureLayout({ font: { size: 1 } }, [15, 9], 1237, 742).font.size;   // the text's factor
const near = (a, b, msg) => { assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} vs ${b}`); n++; };
near(scaled[0].marker.size, 4 * k, 'a 4 pt dot is drawn at the text\'s scale');
near(scaled[0].marker.line.width, k, 'its 1 pt edge too');
eq(scaled[0].marker.opacity, 0.2, 'its alpha stays the lab\'s');
near(scaled[1].marker.size[1], 4 * k, 'per-point sizes scale');
eq(scaled[1].marker.size[2], null, 'a missing size stays missing');
eq(scaled[2], traces[2], 'a heatmap is untouched');
eq(scaled[3], traces[3], 'a line without markers is untouched (line widths are not scaled)');
eq(JSON.stringify(traces), before, 'the source traces are never changed');
eq(w._replotFigureData(traces, null, 1237, 742), traces, 'no source size (old cache): sizes as served');
const restyle = w._replotMarkerRestyle(traces, [15, 9], 919, 551);
const k919 = w._replotFigureLayout({ font: { size: 1 } }, [15, 9], 919, 551).font.size;
eq(JSON.stringify(restyle.map(r => r.indices)), '[[0,1],[0]]', 'a resize restyles marker sizes and edges by trace');
near(restyle[0].update['marker.size'][0], 4 * k919, 'a resize gives the size a fresh open at that width gives');
const app_mount = app.slice(s, app.indexOf('function _replotHeight', s));
assert.ok(app_mount.includes('themedData = _replotFigureData(themedData, d.source_size_inches, inner.clientWidth, layout.height)'),
    'the tile mount scales the drawn markers'); n++;

// -- 3. map tools redraw from the traces as drawn -------------------------------------------------
(async () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', '_map_tools.html'), 'utf8');
    const d2 = new JSDOM('<!doctype html><body>' + tpl + '<div id="tile"><div class="ds-interactive-fig-title">Map</div><div id="p"></div></div></body>',
        { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const m = d2.window;
    Object.assign(m, { console, AbortController, structuredClone });
    const draws = [];
    const result = { request_hash: 'h', classification: { type: 'ridge', models: ['ridge'], feature_axis: 'x' },
        caps_applied: { cap_x: 2, cap_y: 2, sigma_x: 0, sigma_y: 0, default_x: 1, default_y: 1 },
        smoothed: { x: [0, 1], y: [0, 1], z: [[0, 1], [1, 0]] }, contours: [], xref: 'x', yref: 'y',
        compare: { verdict: 'n/a' }, fit: { verdict_line: '' }, provenance_text: '', updates: [] };
    m.fetch = (url, opts) => Promise.resolve({ ok: true, json: () => Promise.resolve(opts ? { results: [result] } : result) });
    m.Plotly = { react: (plot, data) => { draws.push(data); return Promise.resolve(); }, purge: () => {} };
    m.PlotTheme = { houseLayout: l => l };
    m._dsRootFor = () => m.document.getElementById('tile');
    m.eval(fs.readFileSync(path.join(STATIC, 'map-tools.js'), 'utf8'));
    const payload = { data: [{ type: 'heatmap', x: [0, 1], y: [0, 1], z: [[0, 1], [1, 0]] },
                             { type: 'scatter', x: [0.5], y: [0.5], marker: { size: 6, color: '#000000' } }], layout: {} };
    const plot = m.document.getElementById('p');
    plot.data = [payload.data[0], { type: 'scatter', x: [0.5], y: [0.5], marker: { size: 6.87, color: FG } }];
    plot.layout = {};
    m.MapTools.mount(m.document.getElementById('tile'), plot, payload, { uid: 'run:1', fig: 'map' });
    m.document.querySelector('.map-tools-button').click();
    await new Promise(r => setTimeout(r, 60));
    const view = m.document.querySelector('[data-map="view"]');
    view.value = 'raw';
    view.dispatchEvent(new m.Event('input', { bubbles: true }));
    view.dispatchEvent(new m.Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 60));
    assert.ok(draws.length >= 1, 'the map tools drew: ' + m.document.querySelector('[data-map="status"]').textContent); n++;
    const marker = draws[draws.length - 1][1].marker;
    eq(marker.size, 6.87, 'a map-tools redraw keeps the drawn marker size');
    eq(marker.color, FG, '...and the drawn (themed) colour');
    console.log(`dark_artist_contrast_selfcheck: ${n} assertions passed`);
})().catch(err => { console.error(err); process.exitCode = 1; });
