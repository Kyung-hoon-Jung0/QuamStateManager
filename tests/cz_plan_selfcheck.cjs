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
<section><h3 id="czp-title"></h3><button id="czp-stale-chip" hidden></button><select id="czp-op"></select><p id="czp-status" hidden></p>
<div class="czp-plots"><p id="czp-flux-caveat" hidden></p><div id="czp-flux"></div><p id="czp-time-caveat" hidden></p><div id="czp-time"></div><details id="czp-frequency-box"><summary>Frequency</summary><div id="czp-frequency"></div></details></div>
<div id="czp-card"></div><details id="czp-notes-box"><summary id="czp-notes-summary"></summary><ul id="czp-notes"></ul></details></section></div>`;
const dom = new JSDOM('<!doctype html><body>' + page + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
Object.assign(w, {console});
const renders = [];
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces, layout) => { renders.push({id: el.id, traces, layout}); return Promise.resolve(); };
w.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444']};

function point(f, kind, extra) { return Object.assign({frequency_hz: f, kind: kind, quad_hz: f, model_hz: null, error_hz: null, validity: 'within the quadratic range', reason: null}, extra || {}); }
function payload(op, stale) {
    const result = {ok: true, notes: [], amax_v: 0.2,
        row: {path: op, pair: 'p', operation: op.split('.').pop(), class: 'CZGate', kind: 'single mover', alias_of: null,
            lines: [{role: 'control', name: 'qa', moves: true, pulse_path: op + '.flux_pulse_qubit', pulse_class: 'SquarePulse', amplitude_v: 0.1,
                     point: point(4.92e9, 'quad-term estimate')},
                    {role: 'target', name: 'qb', moves: false, pulse_path: null, amplitude_v: 0, point: point(4.7e9, 'idle f_01', {validity: 'idle'})}],
            detuning_hz: 220e6, idle_detuning_hz: 300e6, nearest: {name: '|11>-|20>', distance_hz: 20e6},
            duration_ns: 40, duration_note: '10 clock cycles x 4 ns = 40 ns (play-time override)',
            phases: {control: 0.1, target: null}, fidelity: {InterleavedRB: {value: 0.991, load_id: 7}},
            plan: null, interaction_frequency_hz: null, coupler_amplitude_v: null, notes: []},
        qubits: [{role: 'control', name: 'qa', tls: [], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.68e9, 5e9, 4.68e9], model_hz: null},
                  summary: {f01_hz: 5e9, anharmonicity_hz: -200e6, model_kind: null, notes: ['a qubit note'], frame: {reason: 'upper sweet spot: flux moves it down'},
                            range: {limit_hz: 190e6, source: 'measured on fitted arcs at upper sweet spots'}}},
                 {role: 'target', name: 'qb', tls: [{lo_hz: 4.8e9, hi_hz: 4.81e9}], curve: {amplitude_v: [-0.2, 0, 0.2], quad_hz: [4.7e9, 4.7e9, 4.7e9], model_hz: null},
                  summary: {f01_hz: 4.7e9, anharmonicity_hz: -210e6, model_kind: null, notes: [], frame: {reason: 'lower sweet spot: flux moves it up'}, range: {}}}],
        trajectory: [{amplitude_v: [0, 0.1, 0.1, 0], frequency_hz: [5e9, 4.92e9, 4.92e9, 5e9], quad_hz: [5e9, 4.92e9, 4.92e9, 5e9], model_hz: null}, null],
        calibration: stale ? {last_set: {t: '2026-01-02T03:04:05Z', run_id: 41, proven: true, provenance: 'run_proven'}, proven: true, unknown: false, mode: 'ledger', note: null,
                              warnings: [{path: 'qubits.qa.f_01', t: '2026-01-03T00:00:00Z', run_id: 50, text: 'calibrated against an older qa.f_01'}]}
                           : {last_set: null, proven: false, unknown: true, mode: 'unavailable', note: 'No change history has been built for this chip yet.', warnings: []},
        // v1.4.1: empty comparison points -> one point, so secondary marker contrast is checked.
        pair_ops: [{path: 'qubit_pairs.p.macros.a', operation: 'a', points: []}, {path: 'qubit_pairs.p.macros.b', operation: 'b', points: [{frequency_hz: 4.8e9, amplitude_v: .05}]}]};
    if (op.endsWith('.b')) {
        result.row.lines[0].point = point(4.81e9, 'quad-term estimate');
        result.row.lines[1].point = point(4.6e9, 'idle f_01');
        result.row.detuning_hz = 210e6;
        result.qubits[0].summary.f01_hz = 4.9e9;
        result.qubits[1].summary.f01_hz = 4.6e9;
        result.qubits[0].curve.quad_hz = [4.58e9, 4.9e9, 4.58e9];
        result.qubits[1].curve.quad_hz = [4.6e9, 4.6e9, 4.6e9];
        result.trajectory[0].frequency_hz = result.trajectory[0].quad_hz = [4.9e9, 4.81e9, 4.81e9, 4.9e9];
    }
    return result;
}
// Server-contract fixture adapter; production computes this in cz_gate_plan.time_panel.
function withPanel(body) {
    if (!body.ok || body.time_panel) return body;
    const n = Math.max(0, ...body.trajectory.filter(Boolean).map(t => t.frequency_hz.length));
    const series = body.row.lines.map((ln,k) => Array.from({length:n}, (_,i) => {
        const t=body.trajectory[k], idle=body.qubits[k].summary?.f01_hz;
        return t ? (i<t.frequency_hz.length ? t.frequency_hz[i] : t.playback_known === false ? null : idle) : ln.moves ? null : idle;
    }));
    const delta=series[0].map((v,i)=>v==null||series[1][i]==null?null:(v-series[1][i])/1e6);
    const levels=[['|11>-|20>',Math.abs(body.qubits[0].summary?.anharmonicity_hz)/1e6],['iSWAP',0],['|11>-|02>',-Math.abs(body.qubits[1].summary?.anharmonicity_hz)/1e6]];
    const dwell=levels.map(([name,v])=>{const n=delta.filter(x=>x!=null&&Math.abs(x-v)<=5).length;return n?name+' '+n+' ns':null}).filter(Boolean).join('; ');
    body.time_panel={time_ns:Array.from({length:n},(_,i)=>i),series_hz:series,detuning_mhz:delta,dwell,
        levels:levels.filter(([,v])=>Number.isFinite(v)).map(([name,v])=>({name,detuning_mhz:v,lo_mhz:v-5,hi_mhz:v+5,
            frequency_hz:body.row.lines[1].point.frequency_hz==null?null:body.row.lines[1].point.frequency_hz+v*1e6}))};
    return body;
}
function hints(el) { return Array.from(el.querySelectorAll('[title]')).map(e=>e.title).join(' '); }
const pending = [];
let historyAnswer = {ok: true, mode: 'ledger', note: null, ops: {
    'qubit_pairs.p.macros.a': {last_set: {t: '2026-01-02T03:04:05Z', run_id: 41}, proven: true, unknown: false,
                               warnings: [{text: 'calibrated against an older qa.f_01'}]},
    'qubit_pairs.p.macros.b': {last_set: null, proven: false, unknown: true, warnings: [], note: 'first recorded in run #8'}}};
w.fetch = (url) => {
    if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve(historyAnswer)});
    const op = decodeURIComponent(url.split('op=')[1]);
    return new Promise(resolve => pending.push({op, resolve}));
};
const answer = (i, body) => pending[i].resolve({json: () => Promise.resolve(withPanel(body))});
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
(async () => {
    const r = d.getElementById('czp-root');
    w.CzPlan.mount(); await wait(10);
    if (process.env.CZ_VALIDITY_PIN) {
        const body = payload('qubit_pairs.p.macros.a', false);
        body.row.lines[0].point.kind = 'lab flux model';
        body.row.lines[0].point.validity = "within the fitted model's measured range";
        answer(0, body); await wait(30);
        const text = d.getElementById('czp-card').textContent;
        check(text.includes("within the fitted model's measured range") && !text.includes('(limit '),
              'model validity never borrows a quadratic shift limit');
        dom.window.close(); console.log(checks + ' validity assertions passed'); return;
    }
    if (process.env.CZ_NOQUAD_PIN) {
        // a qubit with no quad term and no model: the curve holds the idle point only, so the figure
        // never names a quad-term estimate that does not exist
        const body = payload('qubit_pairs.p.macros.a', false);
        body.qubits[0].curve.quad_hz = [null, 5e9, null];
        answer(0, body); await wait(30);
        const names = renders.filter(x => x.id === 'czp-flux').at(-1).traces.map(t => t.name || '');
        check(!names.some(n => n.startsWith('qa') && n.includes('quad-term estimate (extrapolated')), 'no quad-term legend entry from the idle point alone: ' + names.join(' | '));
        check(names.some(n => n.startsWith('qb') && /quad-term estimate/.test(n)), 'a real quad curve keeps its entry');
        dom.window.close(); console.log(checks + ' no-quad assertions passed'); return;
    }
    if (process.env.CZ_LOBES_PIN) {
        const body = payload('qubit_pairs.p.macros.a', false);
        body.row.lines[0].lobes = [{name: 'first lobe', amplitude_v: .1, point: body.row.lines[0].point},
            {name: 'mirror lobe', amplitude_v: -.12, point: point(4.88e9, 'quad-term extrapolated estimate')}];
        body.row.nearest.lobe = 'mirror lobe';
        answer(0, body); await wait(30);
        const text = d.getElementById('czp-card').textContent;
        check(text.includes('mirror lobe') && text.includes('4.8800 GHz') && text.includes('extrapolated estimate'),
              'the detail labels the other lobe and its estimate');
        check(text.includes('closest across played lobes'), 'the nearest resonance names its lobe and selection rule');
        check(renders.filter(x => x.id === 'czp-flux').at(-1).traces.some(t =>
              /mirror lobe/.test(t.name || '') && t.x[0] === -.12 && t.y[0] === 4.88),
              'the figure includes the mirror operating point');
        dom.window.close(); console.log(checks + ' lobe assertions passed'); return;
    }
    check(pending.length === 1 && pending[0].op === 'qubit_pairs.p.macros.a', 'the selected operation loads on mount');
    // a click on b before a answers: a's late answer must not paint under b
    r.querySelector('tr[data-op="qubit_pairs.p.macros.b"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    await wait(10);
    answer(1, payload('qubit_pairs.p.macros.b', false)); await wait(20);
    answer(0, payload('qubit_pairs.p.macros.a', true)); await wait(20);
    check(r.getAttribute('data-rendered') === 'qubit_pairs.p.macros.b' && d.getElementById('czp-title').textContent.includes('b'), 'a late answer is dropped');
    const card = d.getElementById('czp-card').textContent;
    // v1.4.1: identical operation fixtures -> distinct frequencies, selection identity must be visible.
    check(card.includes('4.8100 GHz') && card.includes('quad-term estimate') && card.includes('does not move'), 'the card names the selected frequency and its tier');
    check(hints(d.getElementById('czp-card')).includes('clock cycles x 4 ns') && card.includes('40 ns'), 'the card explains the duration unit conversion');
    check(card.includes('InterleavedRB 0.9910') && card.includes('load id 7'), 'IRB with its load id');
    check(card.includes('unknown') && hints(d.getElementById('czp-card')).includes('No change history'), 'no history: the time is unknown, with the reason');
    check(d.getElementById('czp-op').options.length === 2 && d.getElementById('czp-op').value === 'qubit_pairs.p.macros.b', 'the picker lists the pair operations');
    const flux = renders.filter(x => x.id === 'czp-flux').at(-1);
    // v1.4.1: assume panel exists -> assert panel identity before inspecting its curve.
    check(flux && flux.traces.some(t => /quad-term estimate/.test(t.name || '')) && flux.layout.annotations.some(a => a.showarrow), 'curve in the flux panel labelled with an arrow to the operating point');
    check(flux.layout.shapes.some(s => s.type === 'rect') && flux.layout.annotations.some(a => /control on \|11>-\|20>/.test(a.text)), 'defect band and resonance levels drawn');
    const time = renders.filter(x => x.id === 'czp-time').at(-1);
    const delta = time.traces.find(t => t.yaxis === 'y2');
    check(time.layout.xaxis.anchor === 'y2' && time.layout.annotations.map(a => a.x).join() === '1,0.8,0.6' &&
          time.layout.annotations.map(a => a.y).join() === '200,0,-210' &&
          time.layout.shapes.filter(s => s.type === 'rect').every(s => s.fillcolor === 'rgba(173,50,107,0.08)' && (s.opacity || 1) === 1),
          'time label sits below both panels; resonance names stay at their levels with separate horizontal positions');
    // v1.4.1: identical operation detuning -> distinct detuning, a wrong-operation plot must fail.
    check(delta && delta.y[1] === 210 && delta.y[0] === 300, 'the selected detuning in time comes from both trajectories');
    check(flux.traces.find(t => /qa.*quad-term/.test(t.name || '')).y[1] === 4.9, 'the flux panel uses the selected curve');
    check(time.layout.shapes.filter(s => s.yref === 'y2' && s.type === 'rect').length === 3, 'three resonance bands');
    // the stale operation: its warning reaches the notes
    r.querySelector('tr[data-op="qubit_pairs.p.macros.a"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    await wait(10); answer(2, payload('qubit_pairs.p.macros.a', true)); await wait(20);
    const notes = d.getElementById('czp-notes').textContent;
    // v1.4.1 (A14 F1): one flat warning line per path -> grouped per qubit and run, in the collapsed box.
    check(notes.includes('qa') && notes.includes('run #50 (2026-01-03 00:00 UTC): f_01') && !d.getElementById('czp-notes-box').open,
          'a later dependency change is listed with its run, grouped per qubit, in the collapsed notes box');
    // v1.4.1 (A14 F1): qubit notes in the notes list -> under the flux figure they explain, always visible.
    check(d.getElementById('czp-flux-caveat').textContent.includes('qa: a qubit note') && !d.getElementById('czp-flux-caveat').hidden,
          'qubit notes are shown under the flux figure');
    check(d.getElementById('czp-card').textContent.includes('run #41'), 'last set names the run');
    // the table's Last set column
    const cells = Array.from(r.querySelectorAll('td[data-last]')).map(td => td.textContent);
    // v1.4.1: bare unknown -> unknown with the observation, never presented as a setting.
    // v1.5.0: the observation's long text made the table unreadable -> compact cell, details on hover.
    const hint = r.querySelectorAll('td[data-last]')[1].querySelector('span').getAttribute('title') || '';
    check(cells[0] === '2026-01-02' && hints(r.querySelectorAll('td[data-last]')[0]).includes('run #41') && cells[1] === '-' &&
          hint.includes('first recorded in run #8'), 'Last set filled, observations retain unknown setting time');

    async function select(op, data) {
        const index = pending.length;
        r.querySelector('tr[data-op="qubit_pairs.p.macros.' + op + '"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
        await wait(5); answer(index, data); await wait(15);
    }
    const aPath = 'qubit_pairs.p.macros.a', bPath = 'qubit_pairs.p.macros.b';
    const observation = payload(aPath, false);
    observation.calibration.note = 'first recorded in run #8; setting time unknown';
    await select('a', observation);
    check(hints(d.getElementById('czp-card')).includes('first recorded in run #8') &&
          !d.getElementById('czp-card').textContent.includes('written by that run'), 'the card describes observations without inventing a setting');
    const uncertain = payload(aPath, true);
    uncertain.calibration.last_set.provenance = 'run_uncertain_chip';
    await select('a', uncertain);
    check(!d.getElementById('czp-card').textContent.includes('written by that run'), 'writer attribution requires run_proven provenance');
    const unknown = payload(aPath, false);
    unknown.row.lines[1].moves = true;
    unknown.row.duration_ns = null;
    unknown.row.duration_note = 'unknown: stored duration is a runtime property';
    await select('a', unknown);
    let currentTime = renders.filter(x => x.id === 'czp-time').at(-1);
    check(d.getElementById('czp-frequency-box').open === false && currentTime.traces.every(t => t.yaxis === 'y2'), 'unsupported moving waveform stays unknown');
    check(currentTime.traces.find(t => t.yaxis === 'y2').y.every(v => v === null), 'unknown motion makes detuning unknown');
    check(d.getElementById('czp-time').getAttribute('data-dwell') === '', 'unknown samples contribute no resonance dwell');
    check(d.getElementById('czp-card').textContent.includes('Durationunknown'), 'duration with no proven unit is unknown');

    const nominal = payload(aPath, false);
    nominal.trajectory[0].frequency_hz = [5e9, 4.92e9];
    nominal.trajectory[0].playback_known = false;
    nominal.row.lines[1].moves = true;
    nominal.trajectory[1] = {frequency_hz: [4.7e9, 4.7e9, 4.7e9, 4.7e9]};
    await select('a', nominal);
    currentTime = renders.filter(x => x.id === 'czp-time').at(-1);
    check(currentTime.traces.find(t => t.yaxis === 'y2').y.slice(2).every(v => v === null), 'nominal waveform does not invent a known idle tail');

    // One shared library promise: a queued plot must not replace b's refusal.
    let ready;
    w.requirePlotly = () => new Promise(resolve => { ready = resolve; });
    const library = w.requirePlotly();
    w.requirePlotly = () => library;
    renders.length = 0;
    await select('a', payload(aPath, false));
    const refused = payload(bPath, false);
    refused.trajectory = [null, null];
    await select('b', refused);
    check(d.getElementById('czp-time').textContent.includes('Not drawn'), 'the newer refusal appears before Plotly is ready');
    check(!d.getElementById('czp-time').hasAttribute('data-dwell'), 'emptying removes earlier dwell metadata');
    ready(); await wait(15);
    check(renders.filter(x => x.id === 'czp-time').length === 0, 'an older deferred time render cannot replace a refusal');
    const deferredFlux = renders.filter(x => x.id === 'czp-flux');
    check(deferredFlux.length === 1 && deferredFlux[0].traces.find(t => /qa.*quad-term/.test(t.name || '')).y[1] === 4.9,
          'only the newer operation renders in the flux panel');

    // Resolve the newer plot before the older promise to expose final overwrite in both panels.
    let oldReady;
    const olderLibrary = new Promise(resolve => { oldReady = resolve; });
    w.requirePlotly = () => olderLibrary;
    renders.length = 0;
    await select('a', payload(aPath, false));
    w.requirePlotly = () => Promise.resolve();
    await select('b', payload(bPath, false));
    oldReady(); await wait(15);
    check(renders.length === 2, 'older deferred renders never draw over newer plots');
    check(renders.find(x => x.id === 'czp-time').traces.find(t => t.yaxis === 'y2').y[1] === 210, 'the time panel retains b');
    check(renders.find(x => x.id === 'czp-flux').traces.find(t => /qa.*quad-term/.test(t.name || '')).y[1] === 4.9, 'the flux panel retains b');

    let failedReady;
    const failedLibrary = new Promise(resolve => { failedReady = resolve; });
    w.requirePlotly = () => failedLibrary;
    renders.length = 0;
    await select('a', payload(aPath, false));
    await select('b', {ok: false, error: 'Unavailable operation'});
    failedReady(); await wait(15);
    check(renders.length === 0 && d.getElementById('czp-flux').textContent.includes('Not drawn') &&
          d.getElementById('czp-time').textContent.includes('Not drawn'), 'a failed newer selection invalidates both pending panels');
    // The real helper waits again behind an existing per-element render chain.
    const probe = require('./cz_plotly_probe.cjs');
    probe.queue(w, renders);
    w.requirePlotly = () => Promise.resolve();
    w.PlotHost = {purgeWithin: el => { probe.purge(el); delete el.data; }};
    let releaseTime, releaseFlux;
    const timeEl = d.getElementById('czp-time'), fluxEl = d.getElementById('czp-flux');
    timeEl.__plotlyRenderChain = new Promise(resolve => { releaseTime = resolve; });
    fluxEl.__plotlyRenderChain = new Promise(resolve => { releaseFlux = resolve; });
    renders.length = 0;
    await select('a', payload(aPath, false));
    await select('b', refused);
    releaseTime(); releaseFlux(); await wait(25);
    check(renders.filter(x => x.id === 'czp-time').length === 0 && timeEl.textContent.includes('Not drawn'),
          'production queue: old time work cannot replace the newer refusal');
    const queuedFlux = renders.filter(x => x.id === 'czp-flux');
    check(queuedFlux.length === 1 && queuedFlux[0].traces.find(t => /qa.*quad-term/.test(t.name || '')).y[1] === 4.9,
          'production queue: only the current selection draws its flux curve');
    timeEl.__plotlyRenderChain = new Promise(resolve => { releaseTime = resolve; });
    fluxEl.__plotlyRenderChain = new Promise(resolve => { releaseFlux = resolve; });
    renders.length = 0;
    await select('a', payload(aPath, false));
    const epoch = timeEl.__plotlyRenderEpoch || 0;
    await select('b', {ok: false, error: 'refused'});
    releaseTime(); releaseFlux(); await wait(25);
    check(renders.length === 0 && timeEl.__plotlyRenderEpoch > epoch && fluxEl.textContent.includes('Not drawn'),
          'production queue: emptying invalidates pending work in both panels');
    timeEl.__plotlyRenderChain = new Promise(resolve => { releaseTime = resolve; });
    fluxEl.__plotlyRenderChain = new Promise(resolve => { releaseFlux = resolve; });
    await select('a', payload(aPath, false));
    probe.purgeWithin(w, r);
    releaseTime(); releaseFlux(); await wait(25);
    check(renders.length === 0, 'production purge invalidates queued detail work without a selection change');
    dom.window.close();

    // ===== A14 D1-D3: the detail on the page's bundle (topo-graph.js, cz-map.js, cz-plan.js) =====
    const FX = f => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/cz_plan_contract', f), 'utf8'));
    async function detail(opts) {
        const page2 = `<div id="czp-root" class="czp-root" data-selected="${opts.sel}">
<section class="czp-map-box"><select id="czp-map-op"><option value="">d</option></select>
<select id="czp-map-order"><option value="chip" selected>c</option></select>
<select id="czp-map-scale"><option value="0.25" selected>25</option><option value="0.5">50</option></select>
<div id="czp-map"></div><p id="czp-map-key"></p><p id="czp-map-notes"></p><details id="czp-map-detail"><summary id="czp-map-detail-summary"></summary><div id="czp-map-detail-body"></div></details></section>
<table id="czp-table"><tbody>${opts.sel ? `<tr class="czp-row" data-op="${opts.sel}"><td class="czp-last" data-last="${opts.sel}">x</td></tr>` : ''}</tbody></table><p id="czp-history-note"></p>
<section><h3 id="czp-title"></h3><button id="czp-stale-chip" hidden></button><select id="czp-op"></select><p id="czp-status" hidden></p>
<div class="czp-plots"><p id="czp-flux-caveat" hidden></p><div id="czp-flux"></div><p id="czp-time-caveat" hidden></p><div id="czp-time"></div><details id="czp-frequency-box"><summary>Frequency</summary><div id="czp-frequency"></div></details></div>
<div id="czp-card"></div><details id="czp-notes-box"><summary id="czp-notes-summary"></summary><ul id="czp-notes"></ul></details></section></div>`;
        const dm = new JSDOM('<!doctype html><body>' + page2 + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
        const W = dm.window, env = {dm, W, D: W.document, renders: [], data: [], loss: opts.loss, holdLoss: !!opts.holdLoss, held: null};
        Object.assign(W, {console});
        if (opts.timers) {
            W._timers = [];
            const st = W.setTimeout.bind(W);
            W.setTimeout = (fn, ms) => { if (ms === 3000) { W._timers.push(fn); return 0; } return st(fn, ms); };
        }
        W.requirePlotly = () => Promise.resolve();
        W._plotlyRender = (el, traces, layout) => { env.renders.push({id: el.id, traces, layout}); el.data = traces; return Promise.resolve(); };
        W.Plotly = {restyle: () => Promise.resolve(), relayout: () => Promise.resolve()};
        W.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444']};
        W.fetch = url => {
            if (url.startsWith('/cz-plan/map')) return Promise.resolve({json: () => Promise.resolve(JSON.parse(JSON.stringify(opts.map)))});
            if (url.startsWith('/cz-plan/loss')) {
                const ans = {json: () => Promise.resolve(JSON.parse(JSON.stringify(env.loss)))};
                return env.holdLoss ? new Promise(res => { env.held = () => res(ans); }) : Promise.resolve(ans);
            }
            if (url.startsWith('/cz-plan/history')) {
                env.histories = (env.histories || 0) + 1;
                return Promise.resolve({json: () => Promise.resolve(JSON.parse(JSON.stringify(opts.history || {ok: true, mode: 'ledger', ops: {}})))});
            }
            return new Promise(res => env.data.push({op: decodeURIComponent(url.split('op=')[1]), res}));
        };
        ['topo-graph.js', 'cz-map.js', 'cz-plan.js'].forEach(f => W.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static', f), 'utf8')));
        env.root = W.document.getElementById('czp-root');
        env.answer = async body => { env.data.at(-1).res({json: () => Promise.resolve(withPanel(body))}); await wait(30); };
        env.flux = () => env.renders.filter(x => x.id === 'czp-flux').at(-1);
        env.text = id => W.document.getElementById(id).textContent;
        W.CzPlan.mount(); await wait(30);
        return env;
    }
    const SEL = 'qubit_pairs.p.macros.a';
    const curveMap = {ok: true, unavailable: [], op: null, missing: [], operations: [], refused: [], planned: [], segments: [], pairs: {},
                      qubits: [{name: 'qa', path: 'qubits.qa', f01_hz: 5e9, defects: [], safe: [], loss_data: true, grid_location: '0,0'},
                               {name: 'qb', path: 'qubits.qb', f01_hz: 4.7e9, defects: [], safe: [], loss_data: true, grid_location: '1,0'}]};
    const qaScan = {run_id: 7, interaction_time_ns: 100, flux_conversion: 'model', n_samples: 3, defects: [], notes: [], idle_note: null,
                    pieces: [{n_raw: 3, frequency_hz: [5.0e9, 4.9e9, 4.8e9], loss: [0.01, 0.4, -0.02], detuning_hz: null, stderr: null}]};
    const lossAns = {ok: true, reason: null, roots: [], runs_listed: 1, runs_read: 1, cap: 60, skipped: [], staleness: {mode: 'none', note: null, qubits: {}},
                     curves: {qa: {scan: qaScan, marker: {status: 'none', label: 'no stored defects'}, marker_curve: null, reason: null},
                              qb: {scan: null, marker: {status: 'none', label: 'no stored defects'}, marker_curve: null, reason: 'no population-loss run of qb'}}};
    function rich(extra) {
        const body = payload(SEL, true);
        body.calibration.warning_count = 3;
        // v1.4.1 (A14 fix3 P1-2): the groups carry the route's words for each run (proven writer or saved state)
        body.calibration.warning_groups = [{qubit: 'qa', runs: [{run_id: 60, t: '2026-01-05T08:09:10Z', fields: ['f_01', 'anharmonicity'],
                                                                provenance: 'run_proven', words: 'changed by run #60'},
                                                               {run_id: 50, t: '2026-01-03T00:00:00Z', fields: ['z.joint_offset'],
                                                                provenance: 'run_saved', words: 'first seen in run #50\'s saved state'}]}];
        body.notes = ['route note', 'wave note', 'entry unreadable'];
        body.notes_figure = ['both', 'time', 'status'];
        body.info = ['waveform from the transcription of the lab pulse class'];
        body.info_figure = ['time'];
        body.gate_loss = [{role: 'control', qubit: 'qa', moves: true, operating_hz: 4.92e9, reason: null, marker: {label: 'stored defects match run #7'},
                           scan: {run_id: 7, interaction_time_ns: 100, loss: 0.0123, pieces: [{piece: 0, loss: 0.0123, reason: null}], reason: null},
                           sweep: {f_min_hz: 4.92e9, f_max_hz: 5e9, source: 'trajectory', label: "from the gate's own waveform", note: 'the axis note'},
                           defects_touched: [{frequency_hz: 4.95e9, fwhm_hz: 4e6, peak_loss: 0.3, strong: true, inside: true, distance_hz: 0}]},
                          {role: 'target', qubit: 'qb', moves: false, operating_hz: 4.7e9, reason: 'stays at idle', marker: null, scan: null, sweep: null, defects_touched: []}];
        return Object.assign(body, extra || {});
    }
    const E = await detail({sel: SEL, map: curveMap, loss: lossAns});
    await E.answer(rich());
    const chip = E.D.getElementById('czp-stale-chip'), box = E.D.getElementById('czp-notes-box');
    check(!chip.hidden && chip.textContent === '3 inputs changed after this operation was set' && !box.open &&
          E.text('czp-notes-summary') === 'Notes — 3 inputs changed after this operation was set · 1 note on how the figures were drawn',
          'D1: a status chip in the title row counts the staleness; the notes box is collapsed and its summary counts both kinds');
    const items = Array.from(E.D.querySelectorAll('#czp-notes li')).map(li => li.textContent);
    // v1.4.1 (A14 fix3 P1-2): "qa run #60 (...)" -> the route's words: a proven writer "changed by run #60", a run
    // whose saved state only carried the value first "first seen in run #50's saved state".
    check(items[0] === "qa changed by run #60 (2026-01-05 08:09 UTC): f_01, anharmonicity; first seen in run #50's saved state (2026-01-03 00:00 UTC): z.joint_offset" &&
          (items[1] || '').includes('waveform from the transcription') && E.D.querySelectorAll('#czp-notes li.czp-note-muted').length === 1,
          'D1: staleness grouped per qubit and run, newest run first, then the drawing notes, muted');
    check(E.text('czp-flux-caveat') === 'route noteqa: a qubit note' && !E.D.getElementById('czp-flux-caveat').hidden &&
          E.text('czp-time-caveat') === 'route notewave note' && E.text('czp-status') === 'entry unreadable' && !E.D.getElementById('czp-status').hidden &&
          !E.text('czp-notes').includes('route note') && !E.text('czp-time-caveat').includes('transcription'),
          'D1: every caveat is visible under the figure it explains; the chip-wide status on the status line');
    const card2 = E.text('czp-card');
    check(card2.includes('3 inputs changed after this operation was set') && E.D.querySelector('#czp-card a.czp-notes-link') &&
          !card2.includes('calibrated against'), 'D1: the card states the staleness as a count with a link, not the list');
    E.D.querySelector('#czp-card a.czp-notes-link').dispatchEvent(new E.W.MouseEvent('click', {bubbles: true, cancelable: true}));
    check(box.open && E.W.sessionStorage.getItem('czp-notes-open') === '1', 'D1: the link opens the notes box and remembers it for this tab');
    E.root.querySelector('.czp-row').dispatchEvent(new E.W.MouseEvent('click', {bubbles: true})); await wait(10);
    await E.answer(rich());
    check(box.open, 'D1: an opened notes box stays open across operation switches');
    box.open = false; box.dispatchEvent(new E.W.Event('toggle'));
    chip.dispatchEvent(new E.W.MouseEvent('click', {bubbles: true}));
    check(box.open, 'D1: the status chip opens the notes box');
    check(card2.includes('Scan loss at the operating frequency: 1.2 % (run #7, 100 ns flux excursion from idle: dwell there plus every defect crossed on the way, with the scan\'s ramp)'),
          'D3: the scan loss at the operating frequency, with its run and duration and what it measures');
    check(card2.includes('Defects touched during the gate: 4.9500 GHz (FWHM 4.0 MHz, peak 30.0 %) strong') && hints(E.D.getElementById('czp-card')).includes('the axis note') &&
          hints(E.D.getElementById('czp-card')).includes('excursion 4.9200 GHz – 5.0000 GHz from the gate\'s own waveform'), 'D3: the defects the gate touches, strong flagged, with the axis caveat');
    check(card2.split('Scan loss at the operating frequency').length === 2, 'D3: a member that stays at idle gets no scan-loss line');
    const fx = E.flux();
    // v1.4.1 (A14 fix3 P2-2): every x2 trace -> the filled ones (the panel now also carries its clip ticks)
    const side = fx.traces.filter(t => t.xaxis === 'x2' && t.fill);
    // v1.4.1: mock palette -> contrast-checked CZ colours with an opaque boundary and pale fill.
    check(fx.layout.xaxis.domain.join() === '0,0.76' && fx.layout.xaxis2.domain.join() === '0.8,1' && fx.layout.xaxis2.range.join() === '0,25' &&
          side.length === 1 && side[0].fill === 'toself' && side[0].x.slice(0, 5).join() === '0,1,25,0,0' && side[0].line.color === '#245ec4' &&
          (side[0].opacity || 1) === 1 && side[0].fillcolor === 'rgba(36,94,196,0.16)',
          'D2: the flux figure carries the qubits\' scan loss in a right panel, in their colours, clipped to the map\'s scale');
    check(fx.layout.shapes.some(sh => sh.name === 'operating:qa' && sh.y0 === 4.92 && sh.x0 === 0.8), 'D2: the operating frequency is a tick across the loss panel');
    const clip = fx.traces.find(t => t.xaxis === 'x2' && t.mode === 'markers');
    check(clip && clip.x.join() === '25' && clip.y.join() === '4.9' && clip.marker.symbol === 'triangle-right' &&
          clip.text[0].includes('qa run #7: loss 40.0 % at 4.9000 GHz') && clip.text[0].includes('past the panel scale (0–25 %): clipped') &&
          fx.layout.xaxis2.title.text === 'scan loss (0–25 %)',
          'fix3 P2-2: a scan past the panel scale gets an edge tick whose hover gives the true loss; the axis title names the scale');
    const fluxRenders = E.renders.filter(x => x.id === 'czp-flux').length;
    const scaleSel = E.D.getElementById('czp-map-scale');
    scaleSel.value = '0.5'; scaleSel.dispatchEvent(new E.W.Event('change', {bubbles: true})); await wait(30);
    check(E.renders.filter(x => x.id === 'czp-flux').length === fluxRenders + 1 && E.flux().layout.xaxis2.range.join() === '0,50' &&
          E.flux().traces.find(t => t.xaxis === 'x2').x[2] === 40 && E.data.length === 2, 'D2: the loss scale select redraws the panel at its scale, without a request');
    check(!E.flux().traces.some(t => t.xaxis === 'x2' && t.mode === 'markers') && E.flux().layout.xaxis2.title.text === 'scan loss (0–50 %)',
          'fix3 P2-2: inside a wider scale nothing is clipped and the title follows the scale');
    const fetchN = E.data.length, renderN = E.renders.length;
    E.D.documentElement.setAttribute('data-theme', 'dark'); await wait(40);
    check(E.renders.length >= renderN + 2 && E.data.length === fetchN &&
          E.flux().traces.find(t => t.xaxis === 'x2' && t.fill).line.color === '#79b4fa' &&
          E.flux().layout.shapes.some(s => s.type === 'rect' && s.line.color === '#f49c67' && s.fillcolor === 'rgba(244,156,103,0.08)'),
          'theme toggle redraws both detail figures and defect boundaries from the cached answer');
    function contrast(c, bg) {
        const lum = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16) / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4).reduce((s, x, i) => s + x * [.2126, .7152, .0722][i], 0);
        const a = lum(c), b = lum(bg); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    }
    for (const theme of ['light', 'dark']) {
        E.D.documentElement.setAttribute('data-theme', theme); await wait(40);
        const f = E.flux(), bg = theme === 'dark' ? '#181c25' : '#ffffff';
        check(f.traces.filter(t => t.line && /^#/.test(t.line.color)).every(t => contrast(t.line.color, bg) >= 3 && (t.opacity || 1) === 1) &&
              f.traces.filter(t => t.marker && t.marker.size === 7).every(t => (t.marker.opacity || 1) === 1 && t.marker.symbol === 'circle-open' && contrast(t.marker.line.color, bg) >= 3) &&
              f.layout.annotations.filter(a => a.text && a.font).every(a => contrast(a.font.color, bg) >= 4.5),
              'detail marks and coloured text clear WCAG contrast in ' + theme);
    }
    // a failed load: the reason is a visible line, the caveats and the chip go
    E.root.querySelector('.czp-row').dispatchEvent(new E.W.MouseEvent('click', {bubbles: true})); await wait(10);
    await E.answer({ok: false, error: 'Unavailable operation'});
    check(!E.D.getElementById('czp-status').hidden && E.text('czp-status').includes('Not drawn: Unavailable operation') &&
          E.D.getElementById('czp-flux-caveat').hidden && chip.hidden, 'D1: a failed load is a visible status line, never hidden in the box');
    check(E.D.querySelectorAll('#czp-notes li').length === 0 && E.text('czp-notes-summary') === 'Notes — none' && E.text('czp-card') === '',
          'fix3 P2-4: a failed load leaves none of the previous operation\'s notes or card under its title');
    E.root.querySelector('.czp-row').dispatchEvent(new E.W.MouseEvent('click', {bubbles: true})); await wait(10);
    const nowave = rich({trajectory: [null, null]});
    nowave.gate_loss[0].scan = {run_id: 7, interaction_time_ns: 100, loss: null, reason: 'not measured at this frequency (a 40.0 MHz gap)',
                                pieces: [{piece: 0, loss: null, reason: 'gap'}, {piece: 1, loss: 0.02, reason: null}]};
    await E.answer(nowave);
    check(E.text('czp-time').includes('Not drawn') && E.text('czp-time').includes('the reason is above the figure') && !E.text('czp-time').includes('see the notes above') &&
          !E.text('czp-status').includes('Unavailable operation'),
          'D1: an empty time figure points at its own caveat line, not at notes above');
    // v1.4.1 (A14 fix3): the reason repeated the headline ("not measured ... (run #7; not measured ... (a gap))") -> only what it adds
    check(E.text('czp-card').includes('Scan loss at the operating frequency: not measured at this frequency (run #7; a 40.0 MHz gap)') &&
          E.text('czp-card').includes('piece 0: gap; piece 1: 2.0 %'), 'D3: not measured at this frequency, every bracketing piece listed');
    E.dm.window.close();
    // the loss answer arrives after the operation: the flux figure gains its panel without a request
    const L2 = await detail({sel: SEL, map: curveMap, loss: lossAns, holdLoss: true});
    await L2.answer(rich());
    const noPanel = !L2.flux().layout.xaxis2 && !L2.flux().traces.some(t => t.xaxis === 'x2');
    L2.held(); await wait(30);
    check(noPanel && L2.flux().layout.xaxis2 && L2.data.length === 1, 'D2: the loss panel joins the flux figure when the page\'s loss answer arrives');
    L2.dm.window.close();
    // the frozen route answers: the customer layout (5 qubits) and the 21-qubit grid
    const R5 = await detail({sel: 'qubit_pairs.q3-4.macros.cz_SNZ', map: FX('map_rig5q.json'), loss: FX('loss_rig5q.json')});
    await R5.answer(FX('data_rig5q.json'));
    check(R5.text('czp-notes').includes("waveform from SM's transcription") && R5.D.getElementById('czp-time-caveat').hidden &&
          R5.D.getElementById('czp-status').hidden && R5.D.getElementById('czp-stale-chip').hidden && !R5.D.getElementById('czp-notes-box').open,
          'F1 on the real answer: the transcription notes sit in the collapsed box, nothing between the title and the figures');
    check(R5.text('czp-card').includes('Scan loss at the operating frequency: 0.5 % (run #1909') &&
          // v1.4.1 (A14 fix3): fixtures regenerated after the scan-range reason (33e874a1) -> the line names the range the scan covers
          R5.text('czp-card').includes('Scan loss at the operating frequency: not measured at this frequency (run #1905; the scan covers 4.241-4.761 GHz)'),
          'D3 on the real answer: the mover with a reading and the one outside its scan');
    check(R5.flux().traces.filter(t => t.xaxis === 'x2' && t.fill).length === 2, 'D2 on the real answer: both qubits\' scans beside the flux figure');
    R5.dm.window.close();
    const R21 = await detail({sel: 'qubit_pairs.qA2-qA1.macros.cz_SNZ', map: FX('map_rig21.json'), loss: FX('loss_rig21.json')});
    await R21.answer(FX('data_rig21.json'));
    check(R21.D.getElementById('czp-stale-chip').textContent === '2 inputs changed after this operation was set' &&
          // v1.4.1 (A14 fix3 P1-2): "qA2 run #3450 (...)" twice -> one merged line in the route's words (#3450 is a saved state)
          R21.D.querySelector('#czp-notes li.zline-note-warn').textContent === "qA2, qA1 first seen in run #3450's saved state (2026-10-09 09:36 UTC): f_01" &&
          R21.D.querySelectorAll('#czp-notes li.zline-note-warn').length === 1, 'D1 on the real answer: the count chip and the grouped list');
    check(R21.text('czp-card').includes("Last setrun #3450 · 2026-10-09 09:36:28 UTC") &&
          hints(R21.D.getElementById('czp-card')).includes("first seen in that run's saved state; writer not proven") &&
          !R21.text('czp-card').includes('run_saved') && !/changed by run #3450|\(run #3450\)/.test(R21.text('czp-notes')),
          'fix3 P1-2 on the real answer: a run that only carried the values first is never named as their writer (box and card)');
    check(/Defects touched during the gate: 5\.5506 GHz \(FWHM 9\.6 MHz, peak 65\.6 %\) strong/.test(R21.text('czp-card')),
          'D3 on the real answer: the strong defect the gate crosses');
    check(R21.text('czp-card').includes('Scan loss at the operating frequency: -1.1 % (below zero: no loss resolved)') &&
          R21.D.querySelector('#czp-notes li strong.czp-note-q').textContent === 'qA2, qA1',
          'D3: a reading below zero says no loss was resolved; D1: the qubit name in the notes keeps its spelling');
    R21.dm.window.close();
    // the change history was still being built: the chip, the box and the card fill in when it is ready
    const B = await detail({sel: SEL, map: curveMap, loss: lossAns, timers: true});
    const building = rich();
    Object.assign(building.calibration, {mode: 'building', warning_count: 0, warning_groups: [], warnings: [], last_set: null});
    await B.answer(building);
    const fluxN = B.renders.filter(x => x.id === 'czp-flux').length, bchip = B.D.getElementById('czp-stale-chip');
    check(bchip.hidden && B.W._timers.length === 1, 'D1: while the history is being built there is no count yet, and the operation is asked again');
    B.W._timers.shift()(); await wait(10);
    check(B.data.length === 2 && B.data[1].op === SEL, 'D1: the same operation is asked again after a pause');
    await B.answer(rich());
    check(!bchip.hidden && bchip.textContent === '3 inputs changed after this operation was set' &&
          B.text('czp-card').includes('3 inputs changed after this operation was set') &&
          B.renders.filter(x => x.id === 'czp-flux').length === fluxN, 'D1: the ready history fills the chip and the card without redrawing the figures');
    B.dm.window.close();
    // fix3 P2-5: the history is still being built when the re-asks run out -- a final line, never "fills in" forever
    const bld = rich();
    Object.assign(bld.calibration, {mode: 'building', warning_count: 0, warning_groups: [], warnings: [], last_set: null,
                                    note: 'The change history is being built. It shows here when it is complete.'});
    const G2 = await detail({sel: SEL, map: curveMap, loss: lossAns, timers: true, history: {ok: true, mode: 'building', ops: {}}});
    await G2.answer(bld);
    const building0 = hints(G2.D.getElementById('czp-card')).includes('It shows here when it is complete') &&
        G2.text('czp-history-note').includes('fills in when it is ready');
    let answered = G2.data.length;
    for (let k = 0; k < 60 && G2.W._timers.length; k++) {
        G2.W._timers.shift()(); await wait(5);
        if (G2.data.length > answered) { await G2.answer(bld); answered = G2.data.length; }
    }
    const GAVE = 'still being prepared after a minute; reload the page to see when each operation was set';
    check(building0 && G2.W._timers.length === 0 && G2.data.length === 21 && G2.histories === 21 &&
          hints(G2.D.getElementById('czp-card')).includes(GAVE) && !hints(G2.D.getElementById('czp-card')).includes('It shows here when it is complete') &&
          G2.text('czp-history-note').includes(GAVE) && !G2.text('czp-history-note').includes('fills in'),
          'fix3 P2-5: after 20 re-asks the card and the table note end on a final line');
    G2.dm.window.close();
    // fix3 P1-2: the table's Last set names a saved state as one, a proven writer plainly
    const T = await detail({sel: SEL, map: curveMap, loss: lossAns, history: {ok: true, mode: 'ledger', ops: {
        [SEL]: {last_set: {run_id: 9, t: '2026-01-02T03:04:05Z', provenance: 'run_saved'}, warnings: []}}}});
    await wait(20);
    const td = T.root.querySelector('td[data-last]');
    check(td.textContent === '2026-01-02' &&
          td.querySelector('span').getAttribute('title').includes("first seen in that run's saved state; writer not proven"),
          'fix3 P1-2: the Last set column says "saved in" for a run that only carried the amplitude first');
    T.dm.window.close();
    console.log(checks + ' assertions passed');
    dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
