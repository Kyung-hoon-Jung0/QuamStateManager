'use strict';
// CZ gate plan, flux figure: the frequency span follows what is read there -- the main curves, the
// idle and operating points, the resonance levels. A quad-term estimate drawn beside a fitted model
// is the secondary curve and leaves the span (on a real chip it ran to ~10 GHz and flattened the
// operating region); without a model the quad term IS the main curve and sets the span.
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
<div class="czp-plots"><p id="czp-flux-caveat" hidden></p><div id="czp-flux"></div><p id="czp-time-caveat" hidden></p><div id="czp-time"></div></div>
<div id="czp-card"></div><details id="czp-notes-box"><summary id="czp-notes-summary"></summary><ul id="czp-notes"></ul></details></section></div>`;
const dom = new JSDOM('<!doctype html><body>' + page + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
Object.assign(w, {console});
const renders = [];
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces, layout) => { renders.push({id: el.id, traces, layout}); return Promise.resolve(); };
w.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444']};

function point(f, kind) { return {frequency_hz: f, kind: kind, quad_hz: f, model_hz: null, error_hz: null, validity: 'within the quadratic range', reason: null}; }
let withModel = true;
function payload(op) {
    return {ok: true, notes: [], amax_v: 0.4,
        row: {path: op, pair: 'p', operation: 'a', class: 'CZGate', kind: 'single mover', alias_of: null,
            lines: [{role: 'control', name: 'qa', moves: true, pulse_path: op + '.flux_pulse_qubit', pulse_class: 'SquarePulse', amplitude_v: 0.2,
                     point: point(4.6e9, withModel ? 'lab flux model' : 'quad-term estimate')},
                    {role: 'target', name: 'qb', moves: false, pulse_path: null, amplitude_v: 0, point: Object.assign(point(4.4e9, 'idle f_01'), {validity: 'idle'})}],
            detuning_hz: 200e6, idle_detuning_hz: 600e6, nearest: null, duration_ns: 40, duration_note: null,
            phases: {control: null, target: null}, fidelity: {}, plan: null, interaction_frequency_hz: null, coupler_amplitude_v: null, notes: []},
        qubits: [{role: 'control', name: 'qa', tls: [],
                  curve: {amplitude_v: [-0.4, 0, 0.4], quad_hz: [3.0e9, 5e9, 10.0e9], model_hz: withModel ? [4.5e9, 5e9, 4.5e9] : null},
                  summary: {f01_hz: 5e9, anharmonicity_hz: -200e6, model_kind: withModel ? 'lab flux model' : null, notes: [], frame: {reason: 'upper'}, range: {}}},
                 {role: 'target', name: 'qb', tls: [], curve: {amplitude_v: [-0.4, 0, 0.4], quad_hz: [4.4e9, 4.4e9, 4.4e9], model_hz: null},
                  summary: {f01_hz: 4.4e9, anharmonicity_hz: -210e6, model_kind: null, notes: [], frame: {reason: 'lower'}, range: {}}}],
        trajectory: [null, null],
        calibration: {last_set: null, proven: false, unknown: true, mode: 'unavailable', note: 'none', warnings: []},
        pair_ops: [{path: op, operation: 'a', points: []}]};
}
w.fetch = (url) => {
    if (url.startsWith('/cz-plan/data')) return Promise.resolve({json: () => Promise.resolve(payload('qubit_pairs.p.macros.a'))});
    return Promise.resolve({json: () => Promise.resolve({ok: true, mode: 'ledger', ops: {}, curves: {}, qubits: [], segments: [], refused: [], planned: [], missing: [], operations: [], pairs: {}})});
};
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
(async () => {
    w.CzPlan.mount(); await wait(40);
    let flux = renders.filter(x => x.id === 'czp-flux').at(-1);
    check(flux, 'the flux figure renders');
    let [lo, hi] = flux.layout.yaxis.range;
    // lowest drawn level: |11>-|02> at f_t - |alpha_t| = 4.19 GHz; highest: the idle 5.0 GHz
    check(lo > 4.1 && lo < 4.19 && hi > 5.0 && hi < 5.2,
          'with a fitted model the span is the model, idle and operating points (' + lo + '..' + hi + '), not the quad term 3..10 GHz');
    check(flux.traces.some(t => /qa · quad-term estimate/.test(t.name || '') && t.line.dash === 'dash'), 'the quad-term estimate is still drawn, dashed');
    withModel = false;
    renders.length = 0;
    w.CzPlan.refresh = null;
    d.getElementById('czp-root')._czMounted = false;
    w.CzPlan.mount(); await wait(40);
    flux = renders.filter(x => x.id === 'czp-flux').at(-1);
    [lo, hi] = flux.layout.yaxis.range;
    check(lo < 3.0 && hi > 10.0, 'without a model the quad term is the main curve and sets the span (' + lo + '..' + hi + ')');
    console.log(`cz_flux_span_selfcheck: ${checks} assertions passed`);
    w.close();
})().catch(e => { console.error(e); process.exitCode = 1; w.close(); });
