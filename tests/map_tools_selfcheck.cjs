'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_map_tools.html'), 'utf8');
const dom = new JSDOM('<!doctype html><body>' + template + '<div id="tile"><div class="ds-interactive-fig-title">Map</div><div id="plot"></div></div></body>',
    {url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
// All browser globals read by the shipped code live in this one realm.
Object.assign(w, {console, AbortController, structuredClone});
const draws = [], posts = [], popups = [];
let queued = [], displayOnly = false;
const authoritative = {request_hash: 'server-hash', classification: {type: 'erroramp', models: ['erroramp', 'extremum'], feature_axis: 'y'},
    caps_applied: {cap_x: 2.3, cap_y: 0, sigma_x: 2, sigma_y: 0},
    smoothed: {x: [0, 1, 2], y: [1, 2, 3], z: [[0, 1, 0], [1, 0, 1], [0, 1, 0]]},
    contours: [{level: .5, lines: [[[0, 1], [1, 2]]]}], xref: 'x', yref: 'y',
    compare: {node_name: 'opt_amp_prefactor', node_value: .98, sm_value: .981, delta: .001, tol: .003, tol_source: 'reference constant', verdict: 'agree', reason: 'within tolerance', raw_smoothed_delta: .0001},
    fit: {verdict_line: 'ok'}, provenance_text: 'derived from smoothed map; proposed, not applied',
    updates: [{dot_path: 'qubits.q0.amplitude', value: .1962, provenance: {src: 'map_fit', request_hash: 'server-hash', text: 'derived from smoothed map'}}]};
w.fetch = (url, opts) => {
    if (opts) posts.push(JSON.parse(opts.body));
    const value = JSON.parse(JSON.stringify(authoritative));
    if (displayOnly) { value.updates = []; value.display_only_reason = 'not verified on this run'; }
    if (queued.length && opts) return new Promise(resolve => queued.shift()(resolve, value));
    return Promise.resolve({ok: true, json: () => Promise.resolve(value)});
};
w.Plotly = {react: (plot, data, layout) => { draws.push({plot, data, layout}); return Promise.resolve(); }, purge: () => {}};
w.PlotTheme = {houseLayout: layout => layout};
w._dsRootFor = () => d.getElementById('tile');
w._runChipExpect = () => ({token: 'scratch-token'});
w._openPlotApplyPopup = (...args) => popups.push(args);
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/map-tools.js'), 'utf8'));
const tile = d.getElementById('tile'), plot = d.getElementById('plot');
const payload = {data: [{type: 'heatmap', x: [0, 1, 2], y: [1, 2, 3], z: [[0, 1, 0], [1, 0, 1], [0, 1, 0]], colorscale: 'Viridis'}],
    layout: {}, status_chip: 'current code', clickable: {evidence_tier: 'proposed'}};
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const c = name => tile.querySelector('[data-map="' + name + '"]');
function input(name, value) { const el = c(name); if (el.type === 'checkbox') el.checked = value; else el.value = value; el.dispatchEvent(new w.Event('input', {bubbles: true})); }
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }
(async () => {
    check(w.MapTools.localPreview([[null, null], [null, null]], 1, 1, 'gaussian').every(row => row.every(v => v === null)), 'NaNs stay missing');
    const circle = w.MapTools.localPreview([[359, 1, 359]], 1, 0, 'gaussian', 360)[0][1];
    check(circle < 3 || circle > 357, 'phase smoothing follows shortest arc');
    w.MapTools.mount(tile, plot, payload, {uid: 'run:1', fig: 'map'});
    tile.querySelector('.map-tools-button').click(); await wait(20);
    check(posts.length === 2 && posts[1].smooth.sigma_x === 0, 'Smooth starts off with raw server analysis');
    check(c('model').options.length === 2 && c('model').value === 'erroramp', 'only type models, auto selected');
    check(c('sigma_x').min === '0' && c('sigma_x').max === '8' && c('sigma_x').step === '0.1', 'slider range');
    check(c('sigma_y').disabled && c('sigma_y').value === '0', 'count axis locked');
    const chipTexts = Array.from(tile.querySelectorAll('.map-tools-chip')).map(el => el.textContent);
    check(chipTexts.includes('current code') && chipTexts.includes('proposed, not applied'), 'status and proposal chips');
    input('smooth', true); input('sigma_x', 4);
    check(Number(c('sigma_x').value) <= 2.3, 'sigma clamped to cap');
    check(c('apply').disabled, 'old result cannot apply while controls change');
    check(draws.at(-1).data[0].z[0][0] > 0, 'local live preview draws before server');
    input('override', true); input('sigma_x', 4);
    check(c('sigma_x').value === '4' && c('warning').textContent.includes('bias'), 'override unclamps with warning');
    c('sigma_x').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowRight', shiftKey: true, bubbles: true, cancelable: true}));
    check(c('sigma_x').value === '5', 'Shift arrows move ten steps');
    input('contour', true); await wait(340);
    check(draws.at(-1).data.some(t => t.name === 'Contour 0.5'), 'server contours draw');
    check(c('status').textContent.includes('agree') && c('status').textContent.includes('raw-to-smoothed delta'), 'comparison and raw gap visible');
    c('apply').click(); await wait(20);
    check(popups.length === 1 && popups[0][0][0].value === .1962, 'popup uses replayed authoritative transformed value');
    check(popups[0][0][0].provenance.request_hash === 'server-hash' && popups[0][4].token === 'scratch-token', 'provenance and chip gate carried');
    input('mode', 'custom'); input('level', 3);
    // v1.4.1: popup outlived slider changes -> its validator invalidates that request.
    check(typeof popups[0][0][0].mapValid === 'function' && !popups[0][0][0].mapValid(), 'changing controls revokes the open popup');
    const before = posts.length;
    c('level').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true})); await wait(20);
    check(posts.length === before + 1 && posts.at(-1).contour.level === 3, 'Enter analyzes custom level immediately');
    c('level').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    check(tile.querySelector('.map-tools-strip').hidden && d.activeElement === tile.querySelector('.map-tools-button'), 'Escape closes and focuses header');
    check(JSON.parse(w.localStorage.getItem('quam_map_tools_v1')).erroramp.override, 'settings persisted by type');
    tile.querySelector('.map-tools-button').click();
    // v1.4.1: a server cap changed the slider after fitting -> re-fit the displayed strength.
    queued.push((resolve, value) => {
        value.caps_applied.cap_x = .25; value.caps_applied.sigma_x = .25;
        resolve({ok: true, json: () => Promise.resolve(value)});
    });
    input('override', false); input('sigma_x', 1); await wait(340);
    check(posts.at(-1).smooth.sigma_x === .2 && c('sigma_x').value === '0.2', 'server cap clamp is analyzed at the displayed slider setting');
    input('override', true);
    let resolveOld;
    queued.push(resolve => { resolveOld = resolve; });
    input('sigma_x', 6); await wait(320);
    input('sigma_x', 7);
    resolveOld({ok: true, json: () => Promise.resolve({...authoritative, provenance_text: 'STALE'})}); await wait(20);
    check(!c('provenance').textContent.includes('STALE') && c('apply').disabled, 'stale response ignored');
    displayOnly = true; await wait(330);
    check(c('apply').disabled && c('status').textContent.includes('not verified on this run'), 'display-only contract has no Apply');
    // v1.4.1: a disabled Apply had no reason -> the button identifies the missing verified target.
    check(c('apply').textContent.includes('no verified target') && c('apply').title.includes('not verified'), 'disabled Apply explains why');
    const nonmap = d.createElement('div'); d.body.appendChild(nonmap);
    w.MapTools.mount(nonmap, plot, {data: [{type: 'scatter'}]}, {uid: 'run'});
    check(!nonmap.querySelector('.map-tools-button'), 'non heatmaps hide tools');
    const instance = tile._mapTools; instance.destroy(); await wait(20);
    check(!tile.querySelector('.map-tools-strip') && !tile.querySelector('.map-tools-button'), 'prune removes tools');
    console.log(checks + ' assertions passed'); dom.window.close();
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
