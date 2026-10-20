/* v1.5.1 (r6, D4a P2-1): a colour-bar title the server fitted to its bar carries its size in
 * points; a fresh open and a width change draw it at the canvas scale of every other text. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/app.js'), 'utf8');
function slice(name, end) {
    const s = source.indexOf('function ' + name + '(');
    const e = source.indexOf(end, s);
    assert(s >= 0 && e > s, name);
    return source.slice(s, e);
}
const fns = new Function(slice('_replotFigureLayout', 'window._replotFigureLayout') +
    slice('_replotFigureData', 'window._replotFigureData') +
    slice('_replotMarkerRestyle', 'window._replotMarkerRestyle') +
    '\nreturn {layout: _replotFigureLayout, data: _replotFigureData, restyle: _replotMarkerRestyle};')();
const size = [13, 5.4];
const traces = [
    { type: 'heatmap', z: [[1]], colorbar: { len: .6, title: { text: 'long title', side: 'right', font: { size: 8.25 } } } },
    { type: 'heatmap', z: [[1]], colorbar: { len: .6, title: { text: 'fits', side: 'right' } } },
    { type: 'scatter', marker: { size: 4, colorbar: { title: { text: 'marker scale', font: { size: 9 } } } } },
];
const before = JSON.stringify(traces);
const near = (a, b, msg) => assert(Math.abs(a - b) < 1e-9, `${msg}: ${a} vs ${b}`);
for (const [w, h] of [[1237, 514], [919, 382]]) {
    const k = fns.layout({ font: { size: 1 } }, size, w, h).font.size;   // the text's factor
    const drawn = fns.data(traces, size, w, h);
    near(drawn[0].colorbar.title.font.size, 8.25 * k, `a fitted title scales with the canvas at ${w} px`);
    assert.strictEqual(drawn[0].colorbar.title.text, 'long title');
    assert.strictEqual(drawn[0].colorbar.len, .6, 'the bar itself is untouched');
    assert.strictEqual(drawn[1], traces[1], 'a title without a fitted size is left to Plotly');
    near(drawn[2].marker.colorbar.title.font.size, 9 * k, 'a marker colour bar title scales too');
    near(drawn[2].marker.size, 4 * k, 'its markers still scale');
    const restyle = fns.restyle(traces, size, w, h);
    const title = restyle.find(r => 'colorbar.title.font.size' in r.update);
    assert(title, `a width change restyles the fitted title at ${w} px`);
    assert.deepStrictEqual(title.indices, [0]);
    near(title.update['colorbar.title.font.size'][0], 8.25 * k, 'to the size a fresh open at that width gives');
    const marker = restyle.find(r => 'marker.colorbar.title.font.size' in r.update);
    assert(marker && marker.indices[0] === 2, 'and the marker colour bar title');
}
assert.strictEqual(JSON.stringify(traces), before, 'the source traces are never changed');
assert.strictEqual(fns.data(traces, null, 1237, 514), traces, 'no source size (old cache): sizes as served');
console.log('colour-bar title: all passed');
