/* Source typography scales with the tile, including the height clamp. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/app.js'), 'utf8');
const start = source.indexOf('function _replotFigureLayout(');
const end = source.indexOf('window._replotFigureLayout = _replotFigureLayout;', start);
assert(start >= 0 && end > start);
const adapt = new Function(source.slice(start, end) + '\nreturn _replotFigureLayout;')();
const input = { height: 600, font: { size: 10 }, margin: { l: 0, r: 0, t: 0, b: 0 },
    title: { font: { size: 12 } }, legend: { font: { size: 10 } },
    xaxis: { domain: [.1, .9], tickfont: { size: 10 }, title: { font: { size: 10 } } },
    annotations: [{ font: { size: 9 }, xshift: 10, yshift: -60 },
                  { ax: 20, ay: -10 }, { ax: 20, ay: -10, axref: 'x', ayref: 'y' }] };
const small = adapt(input, [10, 10], 480, 480);
assert(Math.abs(small.font.size - 10 * 2 / 3) < 1e-8);
assert.strictEqual(small.title.font.size, 8);
assert(Math.abs(small.legend.font.size - 10 * 2 / 3) < 1e-8);
assert(Math.abs(small.xaxis.tickfont.size - 10 * 2 / 3) < 1e-8);
assert.strictEqual(small.annotations[0].font.size, 6);
assert.strictEqual(small.annotations[0].yshift, -30);
assert.strictEqual(small.annotations[1].ax, 10);
assert.strictEqual(small.annotations[2].ax, 20, 'data coordinates stay unchanged');
assert.deepStrictEqual(small.xaxis.domain, input.xaxis.domain);
assert.strictEqual(input.annotations[0].yshift, -60, 'source is immutable');
const tall = adapt(input, [10, 30], 1240, 2200);
assert(Math.abs(tall.annotations[0].yshift - -60 * 2200 / 2880) < 1e-8);
for (const size of [null, [0, 2], [NaN, 2], [10], ['10', 10]]) {
    assert.strictEqual(adapt(input, size, 600, 600), input, 'old caches keep their typography');
}
const mount = source.slice(source.indexOf('function _fetchInteractiveFig'), source.indexOf('function _replotHeight'));
// v1.4.4: a figure fitted to the pane passes its fitted width (replot_fit_pane_selfcheck covers the fit).
assert(mount.includes('_replotFigureLayout(layout, d.source_size_inches, fit ? fit.width : inner.clientWidth, layout.height)'));
console.log('source layout: all passed');
