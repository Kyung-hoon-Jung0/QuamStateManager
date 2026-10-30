'use strict';
// CZ gate plan, theme: the shipped plot-theme.js + cz-plan.js under jsdom. The flux figure's
// axis text, operating-point ring and loss-area fill are read from the theme tokens at render
// time, and a theme switch redraws the figure with the new theme's tokens (no refetch).
// Tokens are set on the root element's inline style, standing in for the theme stylesheet.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const page = `<div id="czp-root" class="czp-root" data-selected="qubit_pairs.p.macros.a">
<table id="czp-table"><tbody>
<tr class="czp-row" data-op="qubit_pairs.p.macros.a"><td class="czp-last" data-last="qubit_pairs.p.macros.a">...</td></tr>
</tbody></table><p id="czp-history-note"></p>
<section><h3 id="czp-title"></h3><button id="czp-stale-chip" hidden></button><select id="czp-op"></select><p id="czp-status" hidden></p>
<div class="czp-plots"><p id="czp-flux-caveat" hidden></p><div id="czp-flux"></div><p id="czp-time-caveat" hidden></p><div id="czp-time"></div><details id="czp-frequency-box"><summary>Frequency</summary><div id="czp-frequency"></div></details></div>
<div id="czp-card"></div><details id="czp-notes-box"><summary id="czp-notes-summary"></summary><ul id="czp-notes"></ul></details></section></div>`;
const dom = new JSDOM('<!doctype html><html data-theme="light"><body>' + page + '</body></html>',
    {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document, html = d.documentElement;
Object.assign(w, {console});

// Two token sets, one per theme (values differ in every token the figure must follow).
const THEMES = {
    light: {'--pico-color': '#373c44', '--pico-muted-color': '#646b79', '--pico-background-color': '#ffffff',
            '--plot-axis-text': '#373c44', '--plot-grid': 'rgba(60,70,90,0.16)', '--plot-mark-edge': '#ffffff', '--plot-area-alpha': '0.3', '--plot-loss': '#a16207'},
    dark: {'--pico-color': '#c2c7d0', '--pico-muted-color': '#7b8495', '--pico-background-color': '#13171f',
           '--plot-axis-text': '#c2c7d0', '--plot-grid': 'rgba(140,150,165,0.18)', '--plot-mark-edge': '#13171f', '--plot-area-alpha': '0.4', '--plot-loss': '#eab308'},
};
function setTokens(name) { Object.entries(THEMES[name]).forEach(([k, v]) => html.style.setProperty(k, v)); }
setTokens('light');

const renders = [];
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces, layout) => { renders.push({id: el.id, traces, layout}); return Promise.resolve(); };
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/plot-theme.js'), 'utf8'));
// the measured loss the map holds: one qubit, one piece
w.CzMap = {loss: () => ({curves: {qa: {scan: {run_id: 7, pieces: [{frequency_hz: [4.9e9, 4.95e9, 5.0e9], loss: [0.02, 0.1, 0.03]}]}}}}),
           scale: () => 0.25, paint: () => {}, draw: () => Promise.resolve(), highlight: () => {}, mount: () => {}, select: () => {}};

function point(f, kind, extra) { return Object.assign({frequency_hz: f, kind: kind, quad_hz: f, model_hz: null, error_hz: null, validity: 'within the quadratic range', reason: null}, extra || {}); }
function payload(op) {
    const lines = [{role: 'control', name: 'qa', moves: true, pulse_path: op + '.flux_pulse_qubit', pulse_class: 'SquarePulse', amplitude_v: 0.1,
                    point: point(4.92e9, 'quad-term estimate')},
                   {role: 'target', name: 'qb', moves: false, pulse_path: null, amplitude_v: 0, point: point(4.7e9, 'idle f_01', {validity: 'idle'})}];
    return {ok: true, notes: [], amax_v: 0.2,
        row: {path: op, pair: 'p', operation: 'a', class: 'CZGate', kind: 'single mover', alias_of: null, lines,
              detuning_hz: 220e6, idle_detuning_hz: 300e6, nearest: {name: '|11>-|20>', distance_hz: 20e6}, duration_ns: 4, duration_note: '',
              phases: {control: 0, target: null}, fidelity: {}, plan: null, interaction_frequency_hz: null, coupler_amplitude_v: null, notes: []},
        qubits: [{role: 'control', name: 'qa', tls: [], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.68e9, 5e9, 4.68e9], model_hz: null},
                  summary: {f01_hz: 5e9, anharmonicity_hz: -200e6, model_kind: null, notes: [], frame: {reason: ''}, range: {}}},
                 {role: 'target', name: 'qb', tls: [], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.7e9, 4.7e9, 4.7e9], model_hz: null},
                  summary: {f01_hz: 4.7e9, anharmonicity_hz: -210e6, model_kind: null, notes: [], frame: {reason: ''}, range: {}}}],
        trajectory: [{amplitude_v: [0, 0.1, 0.1, 0], frequency_hz: [5e9, 4.92e9, 4.92e9, 5e9], quad_hz: [5e9, 4.92e9, 4.92e9, 5e9], model_hz: null}, null],
        time_panel: {time_ns: [0, 1, 2, 3], series_hz: [[5e9, 4.92e9, 4.92e9, 5e9], [4.7e9, 4.7e9, 4.7e9, 4.7e9]], detuning_mhz: [300, 220, 220, 300], dwell: '',
                     levels: [{name: 'iSWAP', detuning_mhz: 0, lo_mhz: -5, hi_mhz: 5, frequency_hz: 4.7e9}]},
        calibration: {last_set: null, proven: false, unknown: true, mode: 'unavailable', note: '', warnings: []},
        pair_ops: [{path: op, operation: 'a', points: []}]};
}
let fetches = 0;
w.fetch = (url) => {
    if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve({ok: true, mode: 'ledger', ops: {}})});
    fetches++;
    return Promise.resolve({json: () => Promise.resolve(payload('qubit_pairs.p.macros.a'))});
};
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));

function lastFlux() { return renders.filter(r => r.id === 'czp-flux').at(-1); }
function operating(r) { return r.traces.find(t => t.marker && t.marker.symbol === 'diamond' && /at a/.test(t.name || '')); }
function lossFill(r) { return r.traces.find(t => t.fill === 'toself' && t.xaxis === 'x2'); }

function themeChecks(name) {
    const T = THEMES[name], r = lastFlux();
    check(r, name + ': the flux figure rendered');
    check(r.layout.font && r.layout.font.color === T['--plot-axis-text'], name + ': axis text reads --plot-axis-text, got ' + (r.layout.font || {}).color);
    check(r.layout.xaxis.gridcolor === T['--plot-grid'], name + ': gridlines read --plot-grid');
    const op = operating(r);
    check(op, name + ': the operating point is drawn');
    check(op.marker.line && op.marker.line.color === T['--plot-mark-edge'] && op.marker.line.width >= 2,
          name + ': the operating point is ringed in --plot-mark-edge: ' + JSON.stringify(op.marker.line));
    check(op.marker.line.color !== op.marker.color, name + ': the ring differs from the fill (not lost in its own curve)');
    const others = r.traces.filter(t => t !== op && t.marker && t.marker.size).map(t => t.marker.size);
    check(op.marker.size > Math.max.apply(null, others), name + ': the operating point is the largest marker (' + op.marker.size + ' vs ' + others + ')');
    const fill = lossFill(r);
    check(fill && new RegExp(',' + T['--plot-area-alpha'].replace('.', '\\.') + '\\)$').test(fill.fillcolor),
          name + ': the loss area takes --plot-area-alpha: ' + (fill && fill.fillcolor));
    const hex = T['--plot-loss'], rgb = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(',');
    check(fill.fillcolor.startsWith('rgba(' + rgb + ',') && fill.line.color === hex,
          name + ': the loss area and its outline are the --plot-loss hue, not a qubit colour or grey');
    check(r.layout.xaxis.tickfont.size >= 13 && r.layout.yaxis.tickfont.size >= 13 && r.layout.xaxis2.tickfont.size >= 13,
          name + ': tick text at least 13 px');
    check(r.layout.annotations.every(a => !a.text || a.font.size >= 12), name + ': every label at least 12 px');
    const label = r.layout.annotations.find(a => /^<b>op 4\.9200 GHz<\/b>$/.test(a.text || ''));
    check(label && label.bgcolor === T['--plot-mark-edge'] && label.font.color === T['--pico-color'],
          name + ': the operating point carries its own frequency label on a background box');
}

(async () => {
    w.CzPlan.mount(); await wait(40);
    themeChecks('light');
    const before = renders.length, fetched = fetches;
    // the theme switch: tokens change with the attribute, the figure follows without a refetch
    setTokens('dark'); html.setAttribute('data-theme', 'dark'); await wait(60);
    check(renders.length > before && fetches === fetched, 'a theme switch redraws from what the page holds (renders ' + before + ' -> ' + renders.length + ', fetches ' + fetched + ' -> ' + fetches + ')');
    themeChecks('dark');
    // and back: nothing is cached from the first theme
    setTokens('light'); html.setAttribute('data-theme', 'light'); await wait(60);
    themeChecks('light');
    // the shared helpers themselves
    check(w.PlotTheme.withAlpha('#79b4fa', 0.4) === 'rgba(121,180,250,0.4)' && w.PlotTheme.withAlpha('#abc', 0.5) === 'rgba(170,187,204,0.5)' &&
          w.PlotTheme.withAlpha('rgb(1, 2, 3)', 0.2) === 'rgba(1,2,3,0.2)', 'withAlpha handles #rrggbb, #rgb and rgb()');
    html.style.setProperty('--plot-area-alpha', 'nonsense');
    check(w.PlotTheme.areaAlpha() === 0.16, 'an unreadable area token falls back to the old opacity');
    dom.window.close();
    console.log(checks + ' theme assertions passed');
})().catch(e => { console.error(e); process.exit(1); });
