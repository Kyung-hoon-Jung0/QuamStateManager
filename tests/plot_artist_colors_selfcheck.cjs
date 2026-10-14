/* Synthetic artist palettes stay visible and reversible under the house theme. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const dom = new JSDOM('<html data-theme="dark"><body><div class="js-plotly-plot"></div></body></html>',
    { runScripts: 'outside-only' });
const w = dom.window;
w.document.documentElement.style.setProperty('--plot-axis-text', '#e6e9ef');
w.eval(fs.readFileSync(path.join(root, 'plot-theme.js'), 'utf8'));
const theme = w.PlotTheme, foreground = '#e6e9ef';
const dark = ['k', 'black', '#000', '#122', '#000000', '#2f2f2f', 'rgb(10, 20, 30)', 'rgba(0,0,0,1)'];
const preserved = ['#303030', '#30302f', '#123', '#333', '#808080', '#ffffff', '#000080', '#ff0000',
    '#008000', '#1f77b4', '#9467bd', 'transparent', 'rgba(0,0,0,0)', '#0000', '#00000000'];
const source = [{ type: 'scatter', x: [0, 1], y: [1, 2],
    line: { color: '#000000' }, marker: { color: dark.concat(preserved), line: { color: '#222' } },
    textfont: { color: '#121212' } },
    { type: 'heatmap', z: [[0, 1]], colorscale: [[0, '#000000'], [1, '#ffffff']],
      marker: { color: [0, 1] } }];
const original = JSON.stringify(source);
const data = theme.houseData(source);
assert.strictEqual(data[0].line.color, foreground);
assert.strictEqual(data[0].textfont.color, foreground);
assert.strictEqual(data[0].marker.line.color, foreground);
assert.deepStrictEqual(Array.from(data[0].marker.color.slice(0, dark.length)), dark.map(() => foreground));
assert.deepStrictEqual(Array.from(data[0].marker.color.slice(dark.length)), preserved);
assert.strictEqual(JSON.stringify(source), original, 'source artists must be immutable');
assert.strictEqual(data[0].x, source[0].x, 'do not copy large numeric arrays');
assert.strictEqual(data[1].colorscale, source[1].colorscale);
assert.deepStrictEqual(Array.from(data[1].marker.color), [0, 1]);
const layout = { annotations: [{ text: 'Summary', font: { color: '#222222', size: 9 } },
    { text: 'Limit', font: { color: '#ff0000' } }],
    shapes: [{ type: 'line', line: { color: '#000000' }, fillcolor: '#000000' }] };
assert.strictEqual(theme.houseLayout(layout).annotations[0].font.color, foreground);
assert.strictEqual(theme.houseLayout(layout).annotations[1].font.color, '#ff0000');
assert.strictEqual(layout.annotations[0].font.color, '#222222');
assert.strictEqual(theme.houseLayout(layout).shapes[0].line.color, foreground);
assert.strictEqual(theme.houseLayout(layout).shapes[0].fillcolor, '#000000');
const el = w.document.querySelector('div');
el._houseSourceData = source; el._houseSourceLayout = layout;
let restyles = [], relayouts = [];
w.Plotly = { restyle: (el, update, indices) => restyles.push({ update, indices }),
    relayout: (el, update) => relayouts.push(update) };
theme.retheme();
assert.strictEqual(restyles[0].update['line.color'][0], foreground);
w.document.documentElement.setAttribute('data-theme', 'light');
assert.strictEqual(theme.houseData(source), source);
assert.strictEqual(theme.houseLayout(layout).annotations[0].font.color, '#222222');
restyles = []; relayouts = [];
theme.retheme();
assert.strictEqual(restyles[0].update['line.color'][0], '#000000');
assert.strictEqual(relayouts[1]['annotations[0].font.color'], '#222222');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const mount = app.slice(app.indexOf('function _fetchInteractiveFig'), app.indexOf('function _replotHeight'));
assert(mount.includes('window.PlotTheme.houseData(d.data)'), 'mount must adapt data');
assert(mount.includes('_plotlyRender(inner, themedData,'), 'mount must render adapted data');
assert(mount.includes('inner._houseSourceData = d.data'), 'mount must preserve source palette for toggles');
assert(mount.includes('inner._houseSourceLayout = d.layout'), 'mount must preserve annotation colors');
console.log('artist colors: all passed');
