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
let queued = [], displayOnly = false, serverCap = null;
const authoritative = {request_hash: 'server-hash', classification: {type: 'erroramp', models: ['erroramp', 'extremum'], feature_axis: 'y'},
    caps_applied: {cap_x: 2.3, cap_y: 0, sigma_x: 2, sigma_y: 0, default_x: 2, default_y: 0},
    smoothed: {x: [0, 1, 2], y: [1, 2, 3], z: [[0, 1, 0], [1, 0, 1], [0, 1, 0]]},
    contours: [{level: .5, lines: [[[0, 1], [1, 2]]]}], xref: 'x', yref: 'y',
    compare: {node_name: 'opt_amp_prefactor', node_value: .98, sm_value: .981, delta: .001, tol: .003, tol_source: 'reference constant', verdict: 'agree', reason: 'within tolerance', raw_smoothed_delta: .0001},
    // v1.4.1: ambiguous verdict suffix -> explicitly name the fit independently of comparison.
    fit: {verdict_line: 'VERDICT: ok'}, provenance_text: 'derived from smoothed map; proposed, not applied',
    updates: [{dot_path: 'qubits.q0.amplitude', value: .1962, provenance: {src: 'map_fit', request_hash: 'server-hash', text: 'derived from smoothed map'}}]};
// A POST names its panels and is answered with one result per panel (the batch route).
const batch = (value, body) => ({results: (body.panels || [0]).map(() => JSON.parse(JSON.stringify(value)))});
w.fetch = (url, opts) => {
    const body = opts ? JSON.parse(opts.body) : null;
    if (opts) posts.push(body);
    let value = JSON.parse(JSON.stringify(authoritative));
    if (displayOnly) { value.updates = []; value.display_only_reason = 'not verified on this run'; }
    if (serverCap !== null) { value.caps_applied.cap_x = serverCap; value.caps_applied.sigma_x = Math.min(value.caps_applied.sigma_x, serverCap); }
    if (body) value = batch(value, body);
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
    // bug report 2026-10-15: opening asked twice -> one request, raw (smooth off), every panel, defaults learned
    check(posts.length === 1 && posts[0].smooth.sigma_x === 0 && posts[0].panels.join() === '0', 'Smooth starts off with one raw server analysis');
    check(c('sigma_x').value === '2', 'the default strength comes back with the answer');
    check(c('model').options.length === 2 && c('model').value === 'erroramp', 'only type models, auto selected');
    check(c('sigma_x').min === '0' && c('sigma_x').max === '8' && c('sigma_x').step === '0.1', 'slider range');
    check(c('sigma_y').disabled && c('sigma_y').value === '0', 'count axis locked');
    const chipTexts = Array.from(tile.querySelectorAll('.map-tools-chip')).map(el => el.textContent);
    check(chipTexts.includes('current code') && chipTexts.includes('proposed, not applied'), 'status and proposal chips');
    // bug report 2026-10-15: the tools sat under a tall figure -> they sit under the title, above the plot
    const kids = Array.from(tile.children);
    check(kids.indexOf(tile.querySelector('.map-tools-strip')) === 1 && kids.indexOf(plot) === 2, 'strip sits between the title and the plot');
    check(!c('override'), 'no separate override switch');
    input('smooth', true); input('sigma_x', 4);
    // a cap is a bias limit, not a wall: the slider keeps the value and the request asks for it explicitly
    check(c('sigma_x').value === '4' && c('warning').textContent.includes('bias limit'), 'above the cap keeps the value with a warning');
    check(c('x_cap').textContent.includes('bias limit 2.3'), 'the cap is named a bias limit');
    check(c('apply').disabled, 'old result cannot apply while controls change');
    check(draws.at(-1).data[0].z[0][0] > 0, 'local live preview draws before server');
    await wait(340);
    check(posts.at(-1).smooth.sigma_x === 4 && posts.at(-1).smooth.override_cap === true, 'a value above the cap is requested as an override');
    c('sigma_x').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowRight', shiftKey: true, bubbles: true, cancelable: true}));
    check(c('sigma_x').value === '5', 'Shift arrows move ten steps');
    input('contour', true); await wait(340);
    check(draws.at(-1).data.some(t => t.name === 'Contour 0.5'), 'server contours draw');
    check(c('status').textContent.includes('agree') && c('status').textContent.includes('raw-to-smoothed delta'), 'comparison and raw gap visible');
    check(c('status').textContent.includes('fit: ok') && !c('status').textContent.includes('VERDICT:'), 'fit verdict has its own label');
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
    check(JSON.parse(w.localStorage.getItem('quam_map_tools_v1')).erroramp.sigma_x === '5', 'settings persisted by type');
    tile.querySelector('.map-tools-button').click();
    // v1.4.1: a server cap changed the slider after fitting -> re-fit the displayed strength.
    serverCap = .25;      // the server knows a tighter cap than the client has seen
    input('sigma_x', 1); await wait(340);
    // the server's tighter cap arrives with the answer: the displayed strength is re-asked as an override
    check(posts.at(-1).smooth.sigma_x === 1 && posts.at(-1).smooth.override_cap === true && c('sigma_x').value === '1'
        && c('warning').textContent.includes('bias limit'), 'a tighter cap from the answer keeps the shown strength and warns');
    serverCap = null;
    let resolveOld;
    queued.push(resolve => { resolveOld = resolve; });
    input('sigma_x', 6); await wait(320);
    input('sigma_x', 7);
    resolveOld({ok: true, json: () => Promise.resolve({results: [{...authoritative, provenance_text: 'STALE'}]})}); await wait(20);
    check(!c('provenance').textContent.includes('STALE') && c('apply').disabled, 'stale response ignored');
    displayOnly = true; await wait(330);
    check(c('apply').disabled && c('status').textContent.includes('not verified on this run'), 'display-only contract has no Apply');
    // v1.4.1: a disabled Apply had no reason -> the button identifies the missing verified target.
    check(c('apply').textContent.includes('no verified target') && c('apply').title.includes('not verified'), 'disabled Apply explains why');
    // bug report 2026-10-15: smoothing reached only Map 1 -> every map panel is analyzed and redrawn
    tile._mapTools.destroy(); displayOnly = false;
    const two = {data: [payload.data[0], {...payload.data[0], xaxis: 'x2', yaxis: 'y2'}], layout: {}, status_chip: 'current code'};
    w.MapTools.mount(tile, plot, two, {uid: 'run:2', fig: 'map'});
    const first = posts.length;
    tile.querySelector('.map-tools-button').click(); await wait(20);
    // (settings saved earlier for this map type are restored, so a second request may follow)
    check(posts.length > first && posts.slice(first).every(p => p.panels.join() === '0,1'), 'both panels analyzed in one request');
    input('smooth', true); input('sigma_x', 1); await wait(340);
    check(posts.at(-1).panels.join() === '0,1' && posts.at(-1).smooth.sigma_x === 1, 'a change re-analyzes every panel');
    check(draws.at(-1).data[0].z === undefined || JSON.stringify(draws.at(-1).data[0].z) === JSON.stringify(authoritative.smoothed.z), 'panel 1 redrawn from its result');
    check(JSON.stringify(draws.at(-1).data[1].z) === JSON.stringify(authoritative.smoothed.z), 'panel 2 redrawn from its result');
    const beforeReadout = posts.length;
    c('panel').value = '1'; c('panel').dispatchEvent(new w.Event('input', {bubbles: true})); c('panel').dispatchEvent(new w.Event('change', {bubbles: true}));
    await wait(340);
    check(posts.length === beforeReadout && c('status').textContent.startsWith('Map 2: '), 'switching the readout re-reads without re-analyzing');
    // review: a Readout switch while a request is out must not revive (or Apply) the older answer
    input('sigma_x', 1.5);
    let release;
    queued.push((resolve, value) => { release = () => resolve({ok: true, json: () => Promise.resolve(value)}); });
    await wait(330);
    c('panel').value = '0'; c('panel').dispatchEvent(new w.Event('change', {bubbles: true}));
    check(c('apply').disabled && !c('status').textContent.startsWith('Map 1: node'), 'a pending request keeps Apply off across a Readout switch');
    release(); await wait(20);
    check(!c('apply').disabled, 'the answer for the shown settings enables Apply');
    tile._mapTools.destroy();
    // review: more than 16 maps -> requests of at most 16 panels (the route's bound)
    const many = {data: Array.from({length: 18}, (_, i) => ({...payload.data[0], xaxis: 'x' + (i + 1), yaxis: 'y' + (i + 1)})), layout: {}};
    const before16 = posts.length;
    w.MapTools.mount(tile, plot, many, {uid: 'run:18', fig: 'map'});
    tile.querySelector('.map-tools-button').click(); await wait(30);
    const sent = posts.slice(before16).map(p => p.panels.length);
    check(sent.length >= 2 && sent.every(n => n <= 16) && posts.slice(before16, before16 + 2).reduce((n, p) => n + p.panels.length, 0) === 18,
          'eighteen maps go out as requests of at most sixteen');
    tile._mapTools.destroy();
    const nonmap = d.createElement('div'); d.body.appendChild(nonmap);
    w.MapTools.mount(nonmap, plot, {data: [{type: 'scatter'}]}, {uid: 'run'});
    check(!nonmap.querySelector('.map-tools-button'), 'non heatmaps hide tools');
    const instance = tile._mapTools; instance.destroy(); await wait(20);
    check(!tile.querySelector('.map-tools-strip') && !tile.querySelector('.map-tools-button'), 'prune removes tools');
    // v1.4.1: unresolved fetch stranded the strip -> a bounded timeout ends analysis and revokes Apply.
    const nativeTimer = w.setTimeout.bind(w);
    w.setTimeout = (fn, ms) => nativeTimer(fn, ms === 45000 ? 10 : ms);
    let resolveLate;
    w.fetch = () => new Promise(resolve => { resolveLate = resolve; });
    w.MapTools.mount(tile, plot, payload, {uid: 'run:slow', fig: 'map'});
    tile.querySelector('.map-tools-button').click(); await wait(30);
    check(c('status').textContent.includes('timed out after 45 seconds') && c('apply').disabled, 'bounded timeout clears busy state and Apply');
    resolveLate({ok: true, json: () => Promise.resolve(authoritative)}); await wait(20);
    check(c('status').textContent.includes('timed out') && c('apply').disabled, 'a late response cannot revive an expired result');
    tile._mapTools.destroy();
    // v1.4.1: unknown maps offered an extremum -> the UI has no model selector or numeric result.
    authoritative.classification = {type: 'unknown', models: [], reason: 'No supported model; smoothing and contours only.'};
    authoritative.fit.verdict_line = authoritative.classification.reason;
    authoritative.compare.sm_value = null; authoritative.compare.delta = null; authoritative.updates = [];
    w.fetch = (url, opts) => Promise.resolve({ok: true, json: () => Promise.resolve(batch(authoritative, JSON.parse(opts.body)))});
    w.localStorage.setItem('quam_map_tools_v1', JSON.stringify({unknown: {model: 'extremum'}}));
    w.MapTools.mount(tile, plot, payload, {uid: 'run:unsupported', fig: 'map'});
    tile.querySelector('.map-tools-button').click(); await wait(20);
    check(c('model').disabled && c('model').value === 'none', 'unsupported model menu stays disabled despite old stored settings');
    check(c('apply').disabled && c('status').textContent.includes('smoothing and contours only'), 'unsupported family explains why no model result can apply');
    // v1.4.1: unsupported maps printed null comparisons -> show only the honest reason.
    check(c('status').textContent === authoritative.classification.reason, 'no comparison noise for an unsupported model');
    tile._mapTools.destroy();
    console.log(checks + ' assertions passed'); dom.window.close();
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
