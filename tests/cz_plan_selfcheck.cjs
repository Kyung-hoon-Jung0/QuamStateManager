'use strict';
// Chip Status > CZ gate plan: the shipped cz-plan.js under jsdom. What a reader sees from the
// data the route hands it: the card, the notes (staleness included), the operation picker, the
// table's "Last set" column (unknown stays "unknown"), and a late answer never painting over a
// newer selection.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const page = `<div id="czp-root" class="czp-root" data-selected="qubit_pairs.p.macros.a">
<table id="czp-table"><tbody>
<tr class="czp-row" data-op="qubit_pairs.p.macros.a"><td class="czp-last" data-last="qubit_pairs.p.macros.a">...</td></tr>
<tr class="czp-row" data-op="qubit_pairs.p.macros.b"><td class="czp-last" data-last="qubit_pairs.p.macros.b">...</td></tr>
</tbody></table><p id="czp-history-note"></p>
<section><h3 id="czp-title"></h3><select id="czp-op"></select><ul id="czp-notes"></ul>
<div class="czp-plots"><div id="czp-flux"></div><div id="czp-time"></div></div><div id="czp-card"></div></section></div>`;
const dom = new JSDOM('<!doctype html><body>' + page + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
Object.assign(w, {console});
const renders = [];
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces, layout) => { renders.push({id: el.id, traces, layout}); return Promise.resolve(); };
w.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444']};

function point(f, kind, extra) { return Object.assign({frequency_hz: f, kind: kind, quad_hz: f, model_hz: null, error_hz: null, validity: 'within the quadratic range', reason: null}, extra || {}); }
function payload(op, stale) {
    return {ok: true, notes: [], amax_v: 0.2,
        row: {path: op, pair: 'p', operation: op.split('.').pop(), class: 'CZGate', kind: 'single mover', alias_of: null,
            lines: [{role: 'control', name: 'qa', moves: true, pulse_path: op + '.flux_pulse_qubit', pulse_class: 'SquarePulse', amplitude_v: 0.1,
                     point: point(4.92e9, 'quad-term estimate')},
                    {role: 'target', name: 'qb', moves: false, pulse_path: null, amplitude_v: 0, point: point(4.7e9, 'idle f_01', {validity: 'idle'})}],
            detuning_hz: 220e6, idle_detuning_hz: 300e6, nearest: {name: '|11>-|20>', distance_hz: 20e6},
            duration_ns: 40, phases: {control: 0.1, target: null}, fidelity: {InterleavedRB: {value: 0.991, load_id: 7}},
            plan: null, interaction_frequency_hz: null, coupler_amplitude_v: null, notes: []},
        qubits: [{role: 'control', name: 'qa', tls: [], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.68e9, 5e9, 4.68e9], model_hz: null},
                  summary: {f01_hz: 5e9, anharmonicity_hz: -200e6, model_kind: null, notes: ['a qubit note'], frame: {reason: 'upper sweet spot: flux moves it down'},
                            range: {limit_hz: 190e6, source: 'measured on fitted arcs at upper sweet spots'}}},
                 {role: 'target', name: 'qb', tls: [{lo_hz: 4.8e9, hi_hz: 4.81e9}], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.7e9, 4.7e9, 4.7e9], model_hz: null},
                  summary: {f01_hz: 4.7e9, anharmonicity_hz: -210e6, model_kind: null, notes: [], frame: {reason: 'lower sweet spot: flux moves it up'}, range: {}}}],
        trajectory: [{amplitude_v: [0, 0.1, 0.1, 0], frequency_hz: [5e9, 4.92e9, 4.92e9, 5e9], quad_hz: [5e9, 4.92e9, 4.92e9, 5e9], model_hz: null}, null],
        calibration: stale ? {last_set: {t: '2026-01-02T03:04:05Z', run_id: 41, proven: true}, proven: true, unknown: false, mode: 'ledger', note: null,
                              warnings: [{path: 'qubits.qa.f_01', t: '2026-01-03T00:00:00Z', run_id: 50, text: 'calibrated against an older qa.f_01'}]}
                           : {last_set: null, proven: false, unknown: true, mode: 'unavailable', note: 'No change history has been built for this chip yet.', warnings: []},
        pair_ops: [{path: 'qubit_pairs.p.macros.a', operation: 'a', points: []}, {path: 'qubit_pairs.p.macros.b', operation: 'b', points: []}]};
}
const pending = [];
let historyAnswer = {ok: true, mode: 'ledger', note: null, ops: {
    'qubit_pairs.p.macros.a': {last_set: {t: '2026-01-02T03:04:05Z', run_id: 41}, proven: true, unknown: false,
                               warnings: [{text: 'calibrated against an older qa.f_01'}]},
    'qubit_pairs.p.macros.b': {last_set: null, proven: false, unknown: true, warnings: []}}};
w.fetch = (url) => {
    if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve(historyAnswer)});
    const op = decodeURIComponent(url.split('op=')[1]);
    return new Promise(resolve => pending.push({op, resolve}));
};
const answer = (i, body) => pending[i].resolve({json: () => Promise.resolve(body)});
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
(async () => {
    const r = d.getElementById('czp-root');
    w.CzPlan.mount(); await wait(10);
    check(pending.length === 1 && pending[0].op === 'qubit_pairs.p.macros.a', 'the selected operation loads on mount');
    // a click on b before a answers: a's late answer must not paint under b
    r.querySelector('tr[data-op="qubit_pairs.p.macros.b"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    await wait(10);
    answer(1, payload('qubit_pairs.p.macros.b', false)); await wait(20);
    answer(0, payload('qubit_pairs.p.macros.a', true)); await wait(20);
    check(r.getAttribute('data-rendered') === 'qubit_pairs.p.macros.b' && d.getElementById('czp-title').textContent.includes('b'), 'a late answer is dropped');
    const card = d.getElementById('czp-card').textContent;
    check(card.includes('4.9200 GHz') && card.includes('quad-term estimate') && card.includes('does not move'), 'the card names the frequency and its tier');
    check(card.includes('InterleavedRB 0.9910') && card.includes('load id 7'), 'IRB with its load id');
    check(card.includes('unknown') && card.includes('No change history'), 'no history: the time is unknown, with the reason');
    check(d.getElementById('czp-op').options.length === 2 && d.getElementById('czp-op').value === 'qubit_pairs.p.macros.b', 'the picker lists the pair operations');
    const flux = renders.filter(x => x.id === 'czp-flux').at(-1);
    check(flux.traces.some(t => /quad-term estimate/.test(t.name || '')) && flux.layout.annotations.some(a => a.showarrow), 'curve labelled and an arrow to the operating point');
    check(flux.layout.shapes.some(s => s.type === 'rect') && flux.layout.annotations.some(a => /control on \|11>-\|20>/.test(a.text)), 'defect band and resonance levels drawn');
    const time = renders.filter(x => x.id === 'czp-time').at(-1);
    const delta = time.traces.find(t => t.yaxis === 'y2');
    check(delta && delta.y[1] === 220 && delta.y[0] === 300, 'the detuning in time comes from both trajectories (a still qubit idles)');
    check(time.layout.shapes.filter(s => s.yref === 'y2' && s.type === 'rect').length === 3, 'three resonance bands');
    // the stale operation: its warning reaches the notes
    r.querySelector('tr[data-op="qubit_pairs.p.macros.a"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    await wait(10); answer(2, payload('qubit_pairs.p.macros.a', true)); await wait(20);
    const notes = d.getElementById('czp-notes').textContent;
    check(notes.includes('calibrated against an older qa.f_01') && notes.includes('run #50'), 'a later dependency change is a warning with its run');
    check(notes.includes('qa: a qubit note'), 'qubit notes are shown');
    check(d.getElementById('czp-card').textContent.includes('run #41'), 'last set names the run');
    // the table's Last set column
    const cells = Array.from(r.querySelectorAll('td[data-last]')).map(td => td.textContent);
    check(cells[0].includes('run #41') && cells[0].includes('1 newer') && cells[1] === 'unknown', 'Last set filled, unknown stays unknown');
    console.log(checks + ' assertions passed');
    dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
