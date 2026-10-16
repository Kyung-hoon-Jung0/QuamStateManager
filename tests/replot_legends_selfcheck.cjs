/* v1.5.1: a reproduced figure whose panels each drew a legend keeps legend, legend2, ... --
 * every one scaled with the canvas like the text, filled by the theme (framed like the lab's),
 * re-filled on a theme toggle and re-sized on a width change.
 * Run: node tests/replot_legends_selfcheck.cjs <converted.json> (driven by test_v151_replot_text_legends.py)
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const dom = new JSDOM('<html data-theme="dark"><body><div id="plot" class="js-plotly-plot"></div></body></html>',
    { runScripts: 'outside-only' });
const w = dom.window;
w.document.documentElement.style.setProperty('--plot-hover-bg', '#101820');
w.eval(fs.readFileSync(path.join(STATIC, 'plot-theme.js'), 'utf8'));
const app = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
w.eval(app.slice(app.indexOf('function _fetchInteractiveFig('), app.indexOf('function _fetchAndRenderPlot(')));
const view = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };

const d = { data: view.data, layout: view.layout, source_aspect: view.source_aspect, source_size_inches: view.source_size_inches };
const at1237 = w._replotLayoutAt(d, 1237);
const at919 = w._replotLayoutAt(d, 919);
for (const k of ['legend', 'legend2']) {
    ok(at1237[k].font.size > 0 && at1237[k].font.size !== view.layout[k].font.size, k + ': its text scales with the canvas');
    ok(Math.abs(at919[k].font.size / at1237[k].font.size - at919.font.size / at1237.font.size) < 1e-9,
        k + ': by the same factor as the figure text');
    ok(at1237[k].bgcolor === '#101820', k + ': a framed lab legend is filled with the theme card colour');
}
const upd = w._replotSizeUpdate(at919);
ok(upd['legend2.font.size'] === at919.legend2.font.size, 'a width resize re-sizes legend2 too');
const el = w.document.getElementById('plot');
el.layout = {}; el._fullLayout = {};
el._houseSourceLayout = view.layout; el._houseSourceData = view.data;
const relayouts = [];
w.Plotly = { relayout: (t, u) => relayouts.push(u), restyle: () => {} };
w.document.documentElement.style.setProperty('--plot-hover-bg', '#fafafa');
w.PlotTheme.retheme();
ok(relayouts[1] && relayouts[1]['legend2.bgcolor'] === '#fafafa', 'a theme toggle re-fills the legend frames');
console.log(`replot_legends_selfcheck: ${n} assertions passed`);
