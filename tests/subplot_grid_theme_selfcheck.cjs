/* v1.5.1 (D3 class G): every subplot axis carries the house grid, on render and on a toggle.
 *
 * houseLayout themed xaxis/yaxis (and overlaying axes) only, so a lab figure's panels
 * 2..N drew Plotly's near-white default grid next to a dim first panel; the theme
 * toggle also re-coloured only xaxis/yaxis. A grid colour the source layout set itself
 * stays in both paths.
 * Run: node tests/subplot_grid_theme_selfcheck.cjs [converted-layout.json]
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const dom = new JSDOM('<html data-theme="dark"><body><div id="plot" class="js-plotly-plot"></div></body></html>',
    { runScripts: 'outside-only' });
const w = dom.window;
w.document.documentElement.style.setProperty('--plot-grid', 'rgba(1, 2, 3, 0.2)');
w.eval(fs.readFileSync(path.join(root, 'plot-theme.js'), 'utf8'));
const H = w.PlotTheme.houseLayout;
let n = 0;
const eq = (a, b, m) => { assert.strictEqual(a, b, m); n++; };

const L = H({ xaxis: { domain: [0, 0.3] }, yaxis: { domain: [0, 1] },
              xaxis2: { domain: [0.35, 0.65], anchor: 'y2' }, yaxis2: { anchor: 'x2' },
              xaxis3: { domain: [0.7, 1], anchor: 'y3', gridcolor: '#123456' }, yaxis3: { anchor: 'x3' },
              yaxis102: { overlaying: 'y2', side: 'right' } });
eq(L.xaxis.gridcolor, 'rgba(1, 2, 3, 0.2)', 'the first panel keeps the house grid');
for (const k of ['xaxis2', 'yaxis2', 'yaxis3']) {
    eq(L[k].gridcolor, L.xaxis.gridcolor, k + ': a later panel draws the same grid as the first');
    eq(L[k].zerolinecolor, L.xaxis.gridcolor, k + ': and the same zero line');
}
eq(L.xaxis3.gridcolor, '#123456', 'a grid colour the lab set itself stays');
eq(L.xaxis3.zerolinecolor, L.xaxis.gridcolor, '...and only that one key is kept');
eq(L.xaxis2.anchor, 'y2', 'the caller\'s axis settings stay');
eq(L.xaxis2.automargin, undefined, 'no automargin is added to a source-positioned subplot');
eq(L.yaxis102.showgrid, false, 'an overlaying axis still draws no grid of its own');

// a converted lab figure handed in by the Python driver: every non-overlaying axis themed
const file = process.argv[2];
if (file) {
    const converted = JSON.parse(fs.readFileSync(file, 'utf8'));
    const themed = H(converted.layout);
    const axes = Object.keys(themed).filter(k => /^[xy]axis\d*$/.test(k) && !themed[k].overlaying);
    assert.ok(axes.length >= 6, 'the converted figure has several panels: ' + axes.join());
    for (const k of axes) eq(themed[k].gridcolor, 'rgba(1, 2, 3, 0.2)', 'converted ' + k + ' carries the house grid');
}

// the toggle: every axis the plot has follows, a source-owned grid colour stays
const el = w.document.getElementById('plot');
el.layout = { xaxis: {}, yaxis: {}, xaxis2: {}, yaxis2: {}, yaxis102: { overlaying: 'y2' } };
el._fullLayout = { xaxis: {}, yaxis: {}, xaxis2: {}, yaxis2: {}, xaxis3: {}, yaxis3: {}, yaxis102: {} };
el._houseSourceLayout = { xaxis3: { gridcolor: '#abcdef' } };
el._houseSourceData = [];
const relayouts = [];
w.Plotly = { relayout: (target, update) => relayouts.push(update), restyle: () => {} };
w.document.documentElement.style.setProperty('--plot-grid', 'rgba(9, 9, 9, 0.3)');
w.PlotTheme.retheme();
const first = relayouts[0];
for (const k of ['xaxis', 'yaxis', 'xaxis2', 'yaxis2', 'yaxis3']) {
    eq(first[k + '.gridcolor'], 'rgba(9, 9, 9, 0.3)', 'toggle: ' + k + ' grid follows the theme');
    eq(first[k + '.zerolinecolor'], 'rgba(9, 9, 9, 0.3)', 'toggle: ' + k + ' zero line follows the theme');
}
eq(first['xaxis3.gridcolor'], undefined, 'toggle: a source-owned grid colour is not overwritten');
eq(first['xaxis3.zerolinecolor'], 'rgba(9, 9, 9, 0.3)', 'toggle: its zero line still follows');
console.log(`subplot_grid_theme_selfcheck: ${n} assertions passed`);
