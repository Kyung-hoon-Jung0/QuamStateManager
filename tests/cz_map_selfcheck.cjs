'use strict';
// Chip Status > CZ gate plan > Chip frequency map: the shipped cz-plan.js under jsdom. One column per
// qubit, each pair's operating point as segments (solid: fitted model, dashed: quad-term estimate,
// thin: a member that stays at idle), defects as bars, safe intervals as bands, refused frequencies
// listed and never drawn, the operation picker and column order, and a click opening that operation.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const page = `<div id="czp-root" class="czp-root" data-selected="">
<section class="czp-map-box"><select id="czp-map-op"><option value="">default</option></select>
<select id="czp-map-order"><option value="name">name</option><option value="f01">f01</option></select>
<div id="czp-map"></div><p id="czp-map-notes"></p></section>
<table><tbody></tbody></table><p id="czp-history-note"></p>
<section><h3 id="czp-title"></h3><select id="czp-op"></select><ul id="czp-notes"></ul>
<div class="czp-plots"><div id="czp-flux"></div><div id="czp-time"></div></div><div id="czp-card"></div></section></div>`;
const dom = new JSDOM('<!doctype html><body>' + page + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
Object.assign(w, {console});
const renders = [], fetched = [];
const handlers = {};
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces, layout) => {
    renders.push({id: el.id, traces, layout});
    el.on = (evt, fn) => { handlers[evt] = fn; };
    return Promise.resolve();
};
w.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444', '#555', '#666']};
const MAP = {ok: true, unavailable: [], op: null, missing: [], operations: ['cz', 'cz_b'],
    qubits: [{name: 'q2', path: 'qubits.q2', f01_hz: 4.0e9, defects: [], safe: [], loss_data: false, tier: 'quad-term estimate', direction: 'lower sweet spot'},
             {name: 'q10', path: 'qubits.q10', f01_hz: 5.0e9, defects: [{lo_hz: 4.618e9, hi_hz: 4.622e9, peak_loss: .3}],
              safe: [{lo_hz: 4.7e9, hi_hz: 4.95e9}], loss_data: true, tier: 'lab flux model', direction: 'upper sweet spot'},
             {name: 'q3', path: 'qubits.q3', f01_hz: 4.5e9, defects: [], safe: [], loss_data: false, tier: 'quad-term estimate', direction: 'unclear'}],
    segments: [{pair: 'q10-2', operation: 'cz', path: 'qubit_pairs.q10-2.macros.cz', role: 'control', qubit: 'q10', qubit_path: 'qubits.q10',
                moves: true, frequency_hz: 4.98e9, kind: 'lab flux model', validity: 'within the quadratic range', detuning_hz: 980e6, nearest: null},
               {pair: 'q10-2', operation: 'cz', path: 'qubit_pairs.q10-2.macros.cz', role: 'target', qubit: 'q2', qubit_path: 'qubits.q2',
                moves: false, frequency_hz: 4.0e9, kind: 'idle f_01', validity: 'idle', detuning_hz: 980e6, nearest: null},
               {pair: 'q3-2', operation: 'cz', path: 'qubit_pairs.q3-2.macros.cz', role: 'target', qubit: 'q2', qubit_path: 'qubits.q2',
                moves: true, frequency_hz: 4.2e9, kind: 'quad-term estimate', validity: 'within the quadratic range', detuning_hz: null, nearest: null}],
    refused: [{pair: 'q3-2', operation: 'cz', qubit: 'q3', reason: 'sign convention unclear'}],
    planned: [{pair: 'q10-2', qubit: 'q10', qubit_path: 'qubits.q10', frequency_hz: 4.6e9, what: 'planned interaction frequency'}]};
w.fetch = (url) => {
    fetched.push(url);
    if (url.startsWith('/cz-plan/map')) {
        const body = JSON.parse(JSON.stringify(MAP));
        if (url.includes('op=cz_b')) { body.op = 'cz_b'; body.segments = body.segments.slice(0, 2); body.missing = ['q3-2']; body.refused = []; }
        return Promise.resolve({json: () => Promise.resolve(body)});
    }
    if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve({ok: true, mode: 'ledger', ops: {}})});
    return new Promise(() => {});      // /cz-plan/data: never answers here (only that it was asked)
};
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
(async () => {
    w.CzPlan.mount(); await wait(30);
    const r = renders.filter(x => x.id === 'czp-map').at(-1);
    check(r, 'the map is drawn on mount');
    check(r.layout.xaxis.ticktext.join() === 'q2,q10,q3', 'one column per qubit, in the order given');
    const segs = r.traces.filter(t => t.legendgroup && t.mode === 'lines' && t.customdata);
    check(segs.length === 3, 'one segment per drawn pair member');
    const q10 = segs.find(t => t.x[0] === 1.12), q2still = segs.find(t => t.x[0] === 0.12 && t.line.width < 2);
    check(q10 && q10.line.dash === 'solid' && q10.line.width > 2, 'a fitted-model member is a solid thick segment');
    check(q2still && q2still.y[0] === 4.0, 'a member that stays at idle is a thin segment at its idle frequency');
    const quad = segs.find(t => t.legendgroup === 'q3-2');
    check(quad && quad.line.dash === 'dash', 'a quad-term estimate is dashed');
    check(!segs.some(t => t.x[0] === 2.12), 'the refused member (q3) is not drawn');
    const notes = d.getElementById('czp-map-notes').textContent;
    check(notes.includes('q3 in q3-2 cz (sign convention unclear)'), 'the refusal is listed with its reason');
    check(r.traces.some(t => t.mode === 'lines' && !t.legendgroup && t.y[0] === 4.62), 'a defect is a bar at its frequency');
    check(r.layout.shapes.some(s => s.type === 'rect' && s.y0 === 4.7 && s.y1 === 4.95 && s.x0 >= 1.9 && s.x1 <= 2.0),
          'a safe interval is a strip at its column edge');
    check(r.traces.some(t => t.marker && t.marker.symbol === 'diamond-open' && t.y[0] === 4.6 && t.x[0] === 1.5), 'the planned point sits in the carrier column');
    check(r.traces.some(t => t.mode === 'lines' && t.line.dash === 'dot' && t.legendgroup === 'q10-2'), 'the pair members are joined');
    // the picker: every operation name; choosing one re-asks the route with it
    const opSel = d.getElementById('czp-map-op');
    check(Array.from(opSel.options).map(o => o.value).join() === ',cz,cz_b', 'the picker lists every operation name');
    opSel.value = 'cz_b'; opSel.dispatchEvent(new w.Event('change', {bubbles: true})); await wait(30);
    check(fetched.at(-1) === '/cz-plan/map?op=cz_b' && d.getElementById('czp-map-notes').textContent.includes('No cz_b operation on q3-2'),
          'an operation name is drawn where it exists and its absence is named');
    // column order by idle frequency
    const ord = d.getElementById('czp-map-order');
    ord.value = 'f01'; ord.dispatchEvent(new w.Event('change', {bubbles: true})); await wait(30);
    check(renders.filter(x => x.id === 'czp-map').at(-1).layout.xaxis.ticktext.join() === 'q2,q3,q10', 'columns by idle frequency');
    // eleven pairs: no connectors across the map (the legend isolates a pair instead)
    const many = JSON.parse(JSON.stringify(MAP));
    many.segments = [];
    for (let k = 0; k < 11; k++) many.segments.push(
        {...MAP.segments[0], pair: 'p' + k, path: 'qubit_pairs.p' + k + '.macros.cz'},
        {...MAP.segments[1], pair: 'p' + k, path: 'qubit_pairs.p' + k + '.macros.cz'});
    const fetchBefore = w.fetch;
    w.fetch = (url) => url.startsWith('/cz-plan/map') ? Promise.resolve({json: () => Promise.resolve(many)}) : fetchBefore(url);
    w.CzPlan.drawMap(d.getElementById('czp-root')); await wait(30);
    const crowded = renders.filter(x => x.id === 'czp-map').at(-1);
    check(!crowded.traces.some(t => t.line && t.line.dash === 'dot') && d.getElementById('czp-map-notes').textContent.includes('legend'),
          'more than ten pairs: no connectors, the legend isolates a pair');
    w.fetch = fetchBefore;
    // a click on a segment opens that operation
    handlers.plotly_click({points: [{customdata: 'qubit_pairs.q10-2.macros.cz'}]}); await wait(10);
    check(fetched.some(u => u === '/cz-plan/data?op=' + encodeURIComponent('qubit_pairs.q10-2.macros.cz')), 'a click opens that operation');
    console.log(checks + ' assertions passed');
    dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
