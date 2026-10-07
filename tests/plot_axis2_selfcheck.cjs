/* docs/301 F28 -- a secondary (overlaying) axis is themed and draws no grid.
 *
 * The qubit-spectroscopy figure's "Detuning [MHz]" scale on top overlays the
 * RF-frequency axis. houseLayout themed xaxis/yaxis only, so Plotly drew the
 * overlay's grid in its own near-white default: bright vertical lines at
 * -10 / +10 MHz that read as markers on the data.
 * Run: node tests/plot_axis2_selfcheck.cjs (driven by tests/test_plot_axis2.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'), 'utf8');
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { runScripts: 'outside-only', url: 'http://localhost/' });
const win = dom.window;
win.eval(SRC);
const H = win.PlotTheme.houseLayout;

const L = H({ xaxis2: { overlaying: 'x', side: 'top', title: { text: 'Detuning [MHz]' } },
              yaxis2: { overlaying: 'y', side: 'right', showgrid: true } });
ok(L.xaxis2.showgrid === false, 'an overlaying axis draws no grid of its own');
ok(L.xaxis2.zeroline === false, '...and no second zero line');
ok(L.xaxis2.gridcolor === L.xaxis.gridcolor, 'it carries the house grid colour, not Plotly\'s default');
ok(L.xaxis2.title.text === 'Detuning [MHz]' && L.xaxis2.side === 'top', 'the caller\'s settings stay');
ok(L.yaxis2.showgrid === true, 'an explicit showgrid is kept');

const S = H({ xaxis2: { domain: [0.55, 1], anchor: 'y2' } });
ok(S.xaxis2.showgrid === undefined, 'a side-by-side subplot axis (no overlaying) is left alone');

if (fails) { console.error(fails + ' failed'); process.exit(1); }
console.log('all passed');
