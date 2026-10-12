'use strict';
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
let rendered, observed;
const payload = {
    data: [{ type: 'scatter', x: [1, 2], y: [3, 4], line: { shape: 'hv' } }],
    layout: { xaxis: { type: 'log' }, height: 420 },
    notices: ['Not shown: Custom x2 (<script>literal</script>)', 'Map preview reduced; full array retained.']
};
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(payload) });
w._plotlyRender = (el, data, layout) => { rendered = { el, data, layout }; return Promise.resolve(); };
w.PlotHost = { observe: el => { observed = el; } };
w._pruneInteractiveTiles = () => {};
w._purgeInteractiveTile = () => {};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'), 'utf8'));
w.eval(source.slice(start, end));
const tile = w.document.getElementById('tile');
tile.setAttribute('data-endpoint', 'replot/plot');
w._fetchInteractiveFig(tile, 'sample', 'figure', 0, { _gen: 0 });
setTimeout(() => {
    try {
        assert(rendered, 'Plotly render is called');
        assert.strictEqual(rendered.data, payload.data, 'all converted artist data reaches Plotly');
        assert.strictEqual(rendered.layout.xaxis.type, 'log');
        assert.strictEqual(rendered.layout.height, 420);
        assert.strictEqual(rendered.el.style.height, '420px', 'footer follows the full plot height');
        assert(rendered.layout.font.color, 'house theme remains active');
        assert.strictEqual(observed, rendered.el, 'PlotHost observes the rendered element');
        const notes = tile.querySelectorAll('.ds-replot-note');
        assert.strictEqual(notes.length, 2, 'skips and reduced previews are visible');
        assert.strictEqual(notes[0].textContent, payload.notices[0]);
        assert.strictEqual(tile.querySelectorAll('script').length, 0, 'notes use literal text');
        console.log('replot_artists_selfcheck: 10 assertions passed');
        w.close();
    } catch (e) { console.error(e); process.exitCode = 1; w.close(); }
}, 20);
