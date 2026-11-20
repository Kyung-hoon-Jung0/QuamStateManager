/* jsdom selfcheck: the "Simulate a node" page bundle (nodesim.js).
 *
 * Real: ui.js, plot-theme.js, gate-timeline.js and nodesim.js, run in the jsdom
 * realm. Stubbed: window._plotlyRender (records the figure, draws nothing),
 * window.PlotHost, and fetch (installed in the realm, answers from canned
 * JSON). Timers of 1 s or more are recorded, not run: the test fires them, so
 * the poll period is read, not waited for.
 *
 * Pins: pickers and their defaults, the start request, the phase list, every
 * state of spec 1.2 shown with its line verbatim, report -> lanes (one lane per
 * element and output port, I/Q de-duplicated, readout lanes, digital marks),
 * banners and provenance, the script fold and its save, and the fault rows
 * C-1..C-5. NODESIM_JS points at another copy of nodesim.js (mutation runs).
 * NODESIM_ANSWERS names a JSON list of server answers: each is rendered and one
 * "render {...}" line per answer is printed (the end-to-end test reads them).
 *
 * Exit 2 = jsdom not installed. */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('skip: jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const SRC_PATH = process.env.NODESIM_JS || path.join(STATIC, 'nodesim.js');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

const PAGE = '<!doctype html><html><body>'
    + '<div id="page" data-nodesim data-node="11_rabi.py" data-qubit="q2"></div></body></html>';
const dom = new JSDOM(PAGE, { url: 'http://localhost/nodesim', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;
const errors = [];
window.addEventListener('error', (e) => errors.push(String(e.message)));

const M = { calls: [], queue: {}, timers: [] };
window.__M = M;
window.eval(`
window.__smWindowId = 'win-1';
window.__renders = [];
window._plotlyRender = function (el, data, layout) {
    window.__renders.push({ el: el, data: data, layout: layout });
    el.on = function () {}; el.removeListener = function () {};
    return Promise.resolve(el);
};
window.Plotly = { relayout: function (el) { return Promise.resolve(el); } };
window.PlotHost = { observe: function () {}, unobserve: function () {}, purgeWithin: function () {} };
(function () {
    var real = window.setTimeout;
    window.setTimeout = function (fn, ms) {
        if ((ms || 0) >= 1000) {
            window.__M.timers.push({ fn: fn, ms: ms, done: false });
            return 1e6 + window.__M.timers.length;
        }
        return real.apply(window, arguments);
    };
    var realClear = window.clearTimeout;
    window.clearTimeout = function (id) {
        if (id > 1e6) { var t = window.__M.timers[id - 1e6 - 1]; if (t) t.done = true; return; }
        return realClear.apply(window, arguments);
    };
})();
window.fetch = function (url, opt) {
    var M = window.__M;
    url = String(url);
    var route = url.split('?')[0];
    M.calls.push({ route: route, url: url, body: opt && opt.body ? JSON.parse(opt.body) : null });
    var q = (M.queue[route] || []).shift();
    if (q === 'offline') return Promise.reject(new TypeError('Failed to fetch'));
    if (typeof q === 'string') {
        return Promise.resolve({ ok: true, status: 200,
            headers: { get: function () { return 'text/plain'; } },
            text: function () { return Promise.resolve(q); },
            json: function () { return Promise.reject(new Error('not json')); } });
    }
    return Promise.resolve({ ok: true, status: 200,
        headers: { get: function () { return 'application/json'; } },
        json: function () { return Promise.resolve(JSON.parse(JSON.stringify(q || {}))); } });
};
`);
window.eval(read('plot-theme.js'));
window.eval(read('ui-menu.js'));
window.eval(read('ui.js'));
window.eval(read('gate-timeline.js'));
const NODESIM_SRC = fs.readFileSync(SRC_PATH, 'utf8');

let n = 0, failed = 0;
function ok(cond, label) {
    n++;
    if (cond) console.log('ok - ' + label);
    else { failed++; console.log('FAIL ' + label); }
}
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(cond, cap) {
    for (let i = 0; i < (cap || 300); i++) { if (cond()) return true; await tick(5); }
    return false;
}
const $ = (s, r) => (r || doc).querySelector(s);
const $$ = (s, r) => [...(r || doc).querySelectorAll(s)];
const txt = (s) => { const e = $(s); return e ? e.textContent : null; };
const callsTo = (route) => M.calls.filter((c) => c.route === route);
const liveTimers = () => M.timers.filter((t) => !t.done);
async function fireTimer() {
    const t = liveTimers().pop();
    if (!t) return false;
    t.done = true;
    const c0 = M.calls.length;
    t.fn();
    await until(() => M.calls.length > c0);
    await tick(10);
    return true;
}
function change(node) { node.dispatchEvent(new window.Event('change')); }

/* ---- canned answers ---------------------------------------------------- */
function nodeItem(file, targets, extra) {
    return Object.assign({ file: file, name: file.replace(/\.py$/, ''), kind: 'node', targets_name: targets,
                           description: '', error: null, refused: false }, extra || {});
}
const NODES = {
    ok: true,
    items: [
        nodeItem('06_flux.py', 'qubits'),
        nodeItem('11_rabi.py', 'qubits', { estimate_lines: {
            '10': 'About 79 s from a cold instance.', '20': 'About 81 s from a cold instance.',
            '50': 'About 87 s from a cold instance.', '100': 'About 98 s from a cold instance.' } }),
        nodeItem('32a_cz.py', 'qubit_pairs'),
        nodeItem('90_sweep_all.py', 'qubits', { refused: true, refused_line: 'Server refusal verbatim.' }),
        nodeItem('graph.py', 'qubits', { kind: 'graph' })],
    qubits: ['q1', 'q2', 'q3'], pairs: ['q1-2', 'q2-3'], active_qubits: ['q1', 'q2', 'q3'], active_pairs: [],
    context: { chip: 'chipA', env: 'labenv', library: 'D:/lib', qop: 'v3_7_3' },
};
const SPEC_LINES = {
    blocked: 'The simulator library in env labenv is qm-saas 1.1.0; this needs 1.2.0 or newer. Upgrade it in '
        + 'that env with: C:/py/python.exe -m pip install "qm-saas>=1.2.0". SM does not install it.',
    no_env: 'Pick a Python env for this chip first; the node is built in that env.',
    no_library: 'No calibration library found for this chip. Set the calibrations folder in the project first.',
    not_configured: 'No cloud simulator login saved. Add it under Settings, Simulator login.',
    needs_targets: 'Pick at least one qubit or pair to simulate; this node has none selected.',
    refused: 'This node is not simulated here: it writes the chip between programs',
    busy: 'A simulation is already running in this window.',
    starting: 'Starting a cloud simulator instance (about 62 s).',
    running: 'Waiting for the cloud simulator.',
    done: 'Simulated 20 us of 11_rabi on q2. Report only, no samples.',
    node_error: 'The node stopped while building its program: KeyError: amplitude',
    serializer: 'qm could not turn this program into a script: Error in serializer\n'
        + 'This usually means no qubit or pair was selected; pick targets and simulate again.',
    guard: 'Stopped by the safety guard: GUARD FAIL socket.connect 192.0.2.1:80',
    chip_changed: 'The live chip files changed while the node was built; the result was dropped.',
    too_long: 'QMConnectionError: Internal Server Error, status: StatusCode.UNKNOWN\n'
        + 'The cloud simulator refused a window this long.',
    timeout: 'No answer from the cloud simulator after 120 s; the instance was closed.',
    stopped: 'Stopped. The cloud instance was closed.',
    failed: 'RuntimeError: login refused',
};
const V_DANGER = '.ui-verdict.ui-verdict--danger';
const PART = {
    blocked: '.ui-callout.ui-callout--danger', no_env: '.ui-notice.ui-notice--warn',
    no_library: '.ui-notice.ui-notice--warn', not_configured: '.ui-notice.ui-notice--warn',
    needs_targets: '.ui-notice.ui-notice--note', refused: '.ui-notice.ui-notice--warn',
    busy: '.ui-notice.ui-notice--note', starting: '[data-nodesim-phases]', running: '[data-nodesim-phases]',
    done: '.ui-verdict.ui-verdict--ok', node_error: V_DANGER, serializer: V_DANGER, guard: V_DANGER,
    chip_changed: V_DANGER, too_long: V_DANGER, timeout: V_DANGER,
    stopped: '.ui-verdict.ui-verdict--neutral', failed: V_DANGER,
};
/* a report shaped like WaveformReport.to_dict() (keys as the cloud simulator returns them) */
function wf(element, pulse, ts, len, port, iq, extra) {
    return Object.assign({ waveform_name: element + '.' + pulse + '.wf', pulse_name: pulse, length: len,
                           timestamp: ts, iq_info: iq, element: element, output_ports: [port],
                           controller: 'con1', fem: 3 }, extra || {});
}
const IQ_I = { isPartOfIq: true, iqGroupId: 1, isI: true, isQ: false };
const IQ_Q = { isPartOfIq: true, iqGroupId: 1, isI: false, isQ: true };
const NO_IQ = { isPartOfIq: false, iqGroupId: 0, isI: false, isQ: false };
const REPORT = {
    analog_waveforms: [
        wf('q2.xy', 'x180', 600, 40, 2, IQ_I), wf('q2.xy', 'x180', 600, 40, 2, IQ_Q),   // I and Q on one port
        wf('q2.resonator', 'readout', 700, 1000, 1, IQ_I), wf('q2.resonator', 'readout', 700, 1000, 1, IQ_Q),
        wf('q2.z', 'flux', 100, 400, 5, NO_IQ, { fem: 5 }),                              // one element, two ports
        wf('q2.z', 'flux', 100, 400, 6, NO_IQ, { fem: 5 }),
    ],
    digital_waveforms: [{ waveform_name: 'ON', pulse_name: 'q2.resonator.readout', length: 1000, timestamp: 690,
                          element: 'q2.resonator', output_ports: [], controller: 'con1', fem: 3 }],
    adc_acquisitions: [{ start_time: 900, end_time: 1900, process: 'DualDemodIntegration',
                         quantum_element: 'q2.resonator', adc_ports: [1, 2], controller: 'con1', fem: 3,
                         element: 'q2.resonator' }],
    events: [],
};
const PHASES_DONE = [['copy', 0.03], ['build', 7.1], ['login', 1.0], ['spawn', 62.4], ['connect', 1.1],
                     ['submit', 0.4], ['wait', 6.6], ['report', 0.6], ['close', 1.1]]
    .map((p) => ({ name: p[0], s: p[1] }));
const PROV = 'Simulated from chip copy seq 41, 11_rabi @ 1a2b3c4d, QOP v3_7_3, 10:01:02';
function doneAnswer(extra) {
    return Object.assign({ ok: true, state: 'done', id: 'run-1', line: SPEC_LINES.done, elapsed_s: 81.2,
        cached: false, phases: PHASES_DONE, banners: ['REDUCED SIMULATION', 'partial pass'],
        result: { report: REPORT, notes: ['num_shots 1 for simulation'], cannot_draw: null, script_ok: true,
                  provenance: { line: PROV, seq: 41 } } }, extra || {});
}
function doneWith(id, report) {
    return doneAnswer({ id: id, result: { report: report, provenance: { line: 'p', seq: 41 } } });
}
const EMPTY = '[data-nodesim-chart] .ui-state--empty';

/* ---- render mode: answers the server gave in the end-to-end test --------- */
function shownOf(box) {
    const verdict = box.querySelector('.ui-verdict');
    if (verdict) {
        return [verdict.querySelector('.ui-verdict-key').textContent]
            .concat([...verdict.querySelectorAll('.ui-verdict-why')].map((x) => x.textContent)).join('\n');
    }
    const live = box.querySelector('[data-nodesim-line]');
    if (live) return live.textContent;
    return (box.querySelector('.ui-notice-text, .ui-callout-text') || {}).textContent || '';
}
function renderMode(file) {
    window.eval(NODESIM_SRC);
    JSON.parse(fs.readFileSync(file, 'utf8')).forEach((a) => {
        const box = window.NodeSim.renderAnswer(a);
        const v = box.querySelector('.ui-verdict');
        const tone = v ? (v.className.match(/ui-verdict--(\w+)/) || [])[1] : null;
        const tl = a.result && a.result.report ? window.NodeSim.reportToTimeline(a.result.report) : null;
        console.log('render ' + JSON.stringify({
            state: box.getAttribute('data-state'), tone: tone, shown: shownOf(box),
            phases: [...box.querySelectorAll('[data-nodesim-phases] li')].map((l) => l.getAttribute('data-phase')),
            lanes: tl ? tl.lanes.length : null, cannot_draw: tl ? tl.cannot_draw : null }));
    });
    process.exit(errors.length ? 1 : 0);
}

(async () => {
    if (process.env.NODESIM_ANSWERS) return renderMode(process.env.NODESIM_ANSWERS);
    const page = $('#page');
    M.queue['/api/nodesim/nodes'] = [NODES];
    M.queue['/api/nodesim/status'] = [{ ok: true, state: 'idle', line: '' }];
    window.eval(NODESIM_SRC);
    if (!page.__nodesim) window.NodeSim.mount(page);
    const S = page.__nodesim;
    ok(!!S && window.NodeSim.mount(page) === S, 'the page mounts once; a second mount is a no-op');
    await S.ready;
    await tick(10);

    /* ---- page, pickers, defaults ---------------------------------------- */
    const sel = $('[data-nodesim-node]');
    ok(sel && sel.classList.contains('ui-select'), 'the node picker is the kit select');
    ok(sel.value === '11_rabi.py', 'data-node preselects the node');
    ok(!$$('option', sel).some((o) => o.value === 'graph.py'), 'graphs are not offered');
    ok($$('option', sel).filter((o) => o.value === '90_sweep_all.py')[0].disabled, 'a refused node is disabled');
    const targetNames = () => $$('[data-target]').map((b) => b.getAttribute('data-target')).join(',');
    const checked = () => $$('[data-target]').filter((b) => b.checked).map((b) => b.getAttribute('data-target'));
    ok(targetNames() === 'q1,q2,q3', 'a qubits node offers the qubits');
    ok(checked().join(',') === 'q2', 'the qubit selected in the sidebar is the default target');
    ok(txt('[data-nodesim-estimate]') === 'About 81 s from a cold instance.', 'the 20 us estimate shown verbatim');
    ok($$('[data-window-us]').map((b) => b.textContent).join('|') === '10 us|20 us|50 us|100 us',
       'window choices stop at 100 us');
    ok($('[data-window-us="20"]').getAttribute('aria-pressed') === 'true', '20 us is the default window');
    $('[data-window-us="50"]').click();
    ok(S.windowUs === 50 && $('[data-window-us="50"]').classList.contains('ui-segmented-on'),
       'a window click picks it');
    ok(txt('[data-nodesim-estimate]') === 'About 87 s from a cold instance.', 'the estimate follows the window');
    ok($$('[data-nodesim-context] .ui-badge').map((b) => b.textContent).join('|')
       === 'Chip chipA|Env labenv|Library D:/lib|QOP v3_7_3', 'the context strip names chip, env, library and QOP');
    ok(!$('[data-nodesim-start]').disabled, 'Simulate is enabled with a node and a target');
    ok($('[data-nodesim-start]').className === 'btn-sync', 'Simulate is the kit sync button');

    sel.value = '32a_cz.py'; change(sel);
    ok(targetNames() === 'q1-2,q2-3', 'a pair node offers the pairs');
    ok(S.targets.length === 0 && $('[data-nodesim-start]').disabled,
       'no active pair: no target picked and Simulate disabled');
    sel.value = '06_flux.py'; change(sel);
    ok($('[data-nodesim-estimate]').hidden, 'a node with no estimate line shows none');
    sel.value = '90_sweep_all.py'; change(sel);
    ok(txt('[data-nodesim-pick-note]') === 'Server refusal verbatim.',
       'a refused node says why under the picker');
    ok($('[data-nodesim-start]').disabled, 'a refused node cannot be started');
    sel.value = '11_rabi.py'; change(sel);
    const q1 = $('[data-target="q1"]'); q1.checked = true; change(q1);
    ok(S.targets.join(',') === 'q2,q1', 'a target toggle adds it');

    /* ---- start, running, polling ---------------------------------------- */
    M.queue['/api/nodesim/start'] = [{ ok: true, state: 'starting', id: 'run-1', line: SPEC_LINES.starting,
                                       elapsed_s: 0.2, phases: [{ name: 'copy', s: 0.03 }], phase: 'build' }];
    await S.start();
    const st = callsTo('/api/nodesim/start').pop();
    ok(st && st.body.node === '11_rabi.py' && st.body.targets.join(',') === 'q2,q1' && st.body.window_us === 50
       && st.body.window === 'win-1', 'start sends node, targets, window_us and the window id');
    ok(txt('[data-nodesim-line]') === SPEC_LINES.starting, 'the starting line is shown verbatim');
    ok($('[data-phase="copy"]').classList.contains('is-done') && txt('[data-phase="copy"]') === 'copy 0.03 s',
       'a finished phase shows its measured seconds');
    ok($('[data-phase="build"]').classList.contains('is-current') && txt('[data-phase="build"]') === 'build',
       'the current phase is marked and carries no invented time');
    ok($$('[data-nodesim-phases] li').map((l) => l.getAttribute('data-phase')).join(',')
       === 'copy,build,login,spawn,connect,submit,wait,report,close', 'the phase list is the spec order');
    ok(!$('[data-nodesim-stop]').hidden && $('[data-nodesim-start]').disabled, 'while live: Stop shown, Simulate off');
    ok(liveTimers().length === 1 && liveTimers()[0].ms <= 4000, 'a live run is polled every 4 s or faster');

    M.queue['/api/nodesim/status'] = [{ ok: true, state: 'running', id: 'run-1', phase: 'wait', elapsed_s: 75,
        line: 'Waiting for the cloud simulator.', phases: PHASES_DONE.slice(0, 6) }];
    await fireTimer();
    const pc = callsTo('/api/nodesim/status').pop();
    ok(/window=win-1/.test(pc.url) && /id=run-1/.test(pc.url), 'the poll names the window and the run');
    ok($('[data-phase="wait"]').classList.contains('is-current') && txt('[data-phase="spawn"]') === 'spawn 62.4 s',
       'the poll answer moves the phase list');
    ok(txt('[data-nodesim-elapsed]') === '75.0 s', 'elapsed seconds are the server\'s');

    /* ---- done: report -> lanes ------------------------------------------ */
    M.queue['/api/nodesim/status'] = [doneAnswer()];
    await fireTimer();
    ok(txt('[data-nodesim-answer] .ui-verdict--ok .ui-verdict-key') === SPEC_LINES.done,
       'done: the line verbatim in an ok verdict');
    ok($('[data-nodesim-stop]').hidden && !$('[data-nodesim-start]').disabled, 'done: Stop hidden, Simulate on');
    const tl = window.NodeSim.reportToTimeline(REPORT);
    ok(tl.lanes.map((l) => l.key).join(',')
       === 'q2.z con1 / 5 / 5,q2.z con1 / 5 / 6,q2.xy,q2.resonator,q2.resonator (readout)',
       'one lane per element and output port, in time order; a one-port element keeps its own name');
    const xy = tl.lanes.filter((l) => l.key === 'q2.xy')[0];
    ok(xy.segments.length === 1 && xy.segments[0].operation === 'x180 (I+Q)',
       'an I and a Q entry of one pulse are one segment');
    ok(xy.segments[0].start_ns === 600 && xy.segments[0].end_ns === 640 && xy.segments[0].length_ns === 40,
       'a segment spans timestamp .. timestamp + length');
    ok(xy.segments[0].t_ns.length === 0 && xy.segments[0].v.length === 0 && xy.segments[0].peak_v === null,
       'report only: no sample and no level is invented');
    ok(xy.port === 'con1 / 3 / 2', 'the lane names its controller / fem / port');
    const ro = tl.lanes.filter((l) => l.key === 'q2.resonator (readout)')[0];
    ok(ro && ro.segments[0].operation === 'readout' && ro.segments[0].start_ns === 900
       && ro.segments[0].end_ns === 1900, 'an ADC acquisition is a readout segment start_time .. end_time');
    ok(ro.port === 'con1 / 3 / in 1, 2', 'the readout lane names its inputs');
    ok(tl.marks.length === 1 && tl.marks[0].start_ns === 690 && tl.marks[0].end_ns === 1690
       && tl.marks[0].element === 'q2.resonator', 'a digital waveform is a mark');
    ok(tl.notes.length === 0 && tl.cannot_draw === null, 'a full report has no notes and draws');
    ok(S.chart && S.chart.laneCount().drawn === 5, 'the timeline draws the five lanes');
    ok(window.__renders.length === 1, 'Plotly got one figure');
    ok($$('[data-nodesim-chart] .ui-note').some((p) => p.textContent === 'num_shots 1 for simulation'),
       'the result notes are shown verbatim');
    ok($$('[data-nodesim-strip] .ui-badge').map((b) => b.textContent).join('|') === 'REDUCED SIMULATION|partial pass',
       'banners are kit badges, verbatim');
    ok(txt('[data-nodesim-provenance]') === PROV, 'the provenance strip text is the server\'s');
    ok(!$('[data-nodesim-stale]'), 'a fresh result is not marked stale');
    ok($$('[data-nodesim-answer] [data-nodesim-phases] li').length === 9, 'the measured phases stay in a fold');

    /* ---- script fold and save ------------------------------------------- */
    const fold = $('[data-nodesim-script-fold]');
    ok(fold && fold.tagName === 'DETAILS' && fold.classList.contains('ui-disclosure'), 'Show script is a disclosure');
    M.queue['/api/nodesim/script'] = ['from qm.qua import *\nwith program() as prog:\n    pass\n'];
    fold.open = true; fold.dispatchEvent(new window.Event('toggle'));
    await until(() => $('[data-nodesim-script-text]').textContent.length > 0);
    ok(/run=run-1/.test(callsTo('/api/nodesim/script').pop().url), 'the script is fetched for this run');
    ok(txt('[data-nodesim-script-text]').indexOf('with program() as prog:') >= 0, 'the script is shown as text');
    $('[data-nodesim-save]').click();
    ok(callsTo('/api/nodesim/save-script').length === 0
       && $('[data-nodesim-folder]').getAttribute('aria-invalid') === 'true',
       'Save script without a folder asks for one and sends nothing');
    $('[data-nodesim-folder]').value = 'D:/scratch/out';
    M.queue['/api/nodesim/save-script'] = [{ ok: true, state: 'saved', line: 'Saved 11_rabi_simulated.py.' }];
    $('[data-nodesim-save]').click();
    await until(() => $('[data-nodesim-save-line]').textContent.length > 0);
    const sv = callsTo('/api/nodesim/save-script').pop();
    ok(sv && sv.body.run === 'run-1' && sv.body.folder === 'D:/scratch/out', 'Save sends the run and the folder');
    ok(txt('[data-nodesim-save-line]') === 'Saved 11_rabi_simulated.py.', 'the save answer line is shown verbatim');

    /* ---- C-3: the chip moved on ------------------------------------------ */
    ok(liveTimers().length === 1 && liveTimers()[0].ms === 2000,
       'R2B P3-f: after a result a page on screen keeps asking every 2 s');
    Object.defineProperty(doc, 'hidden', { configurable: true, get: () => true });
    M.queue['/api/nodesim/status'] = [doneAnswer({ chip_seq: 44 })];
    await fireTimer();
    ok(liveTimers().length === 1 && liveTimers()[0].ms > 4000, 'R2B P3-f: a hidden page keeps asking, slowly');
    delete doc.hidden;
    ok(txt('[data-nodesim-stale]') === 'chip changed since - re-simulate'
       && $('[data-nodesim-stale]').classList.contains('ui-badge--warn'),
       'C-3: chip seq past the copy seq: the strip says chip changed since - re-simulate');
    ok(window.__renders.length === 1, 'C-3: the same result is not drawn again on a poll');
    M.queue['/api/nodesim/status'] = [doneAnswer({
        banners: ['REDUCED SIMULATION', 'chip changed since - re-simulate'] })];
    await fireTimer();
    ok($$('[data-nodesim-stale]').length === 1, 'C-3: the server banner marks it stale too, once');

    /* ---- C-4: SM stops answering ------------------------------------------ */
    M.queue['/api/nodesim/status'] = ['offline', 'offline'];
    await fireTimer(); await fireTimer();
    ok(!$('[data-nodesim-lost] .ui-notice'), 'C-4: two failed polls do not alarm yet');
    M.queue['/api/nodesim/status'] = ['offline'];
    await fireTimer();
    ok(txt('[data-nodesim-lost] .ui-notice-text') === 'No answer from SM; the run stops if this page stops asking.',
       'C-4: three failed polls show the no-answer line');
    ok(liveTimers().length === 1, 'C-4: the page keeps asking');
    M.queue['/api/nodesim/status'] = [doneAnswer()];
    await fireTimer();
    ok(!$('[data-nodesim-lost] .ui-notice'), 'C-4: an answer clears the no-answer line');

    /* ---- stop -------------------------------------------------------------- */
    M.queue['/api/nodesim/start'] = [{ ok: true, state: 'running', id: 'run-2', phase: 'wait', phases: [],
                                       line: 'Waiting for the cloud simulator.' }];
    await S.start();
    ok(!$('[data-nodesim-chart]').firstChild, 'a new run drops the old picture');
    M.queue['/api/nodesim/stop'] = [{ ok: true, state: 'stopped', id: 'run-2', line: SPEC_LINES.stopped }];
    $('[data-nodesim-stop]').click();
    await until(() => callsTo('/api/nodesim/stop').length === 1);
    await tick(10);
    ok(callsTo('/api/nodesim/stop')[0].body.id === 'run-2', 'Stop names the run');
    ok(txt('.ui-verdict--neutral .ui-verdict-key') === SPEC_LINES.stopped, 'stopped: the line in a neutral verdict');
    ok(liveTimers().length === 0, 'a stopped run is not polled');

    /* ---- every state of spec 1.2 ----------------------------------------- */
    Object.keys(SPEC_LINES).forEach((state) => {
        const box = window.NodeSim.renderAnswer({ state: state, line: SPEC_LINES[state], phase: 'build', phases: [] });
        const part = box.querySelector(PART[state]);
        let shown;
        if (state === 'starting' || state === 'running') shown = box.querySelector('[data-nodesim-line]').textContent;
        else if (PART[state].indexOf('verdict') >= 0) {
            shown = [box.querySelector('.ui-verdict-key').textContent]
                .concat([...box.querySelectorAll('.ui-verdict-why')].map((x) => x.textContent)).join('\n');
        } else shown = (box.querySelector('.ui-notice-text, .ui-callout-text') || {}).textContent;
        ok(!!part && shown === SPEC_LINES[state], state + ': shown with its part and its line verbatim');
    });
    ok(window.NodeSim.STATES_SHOWN.length === Object.keys(SPEC_LINES).length
       && Object.keys(SPEC_LINES).every((s) => window.NodeSim.STATES_SHOWN.indexOf(s) >= 0),
       'the client knows exactly the states of spec 1.2');
    const ser = window.NodeSim.renderAnswer({ state: 'serializer', line: SPEC_LINES.serializer });
    ok(ser.querySelector('.ui-verdict-key').textContent
       === 'qm could not turn this program into a script: Error in serializer'
       && ser.querySelector('.ui-verdict-why').textContent.indexOf('This usually means') === 0,
       'a two-line answer: conclusion, then reason');

    /* ---- C-5: unknown state ----------------------------------------------- */
    const unk = window.NodeSim.renderAnswer({ state: 'mystery', line: 'Some words the server chose.' });
    ok(unk.textContent === 'Some words the server chose.', 'C-5: an unknown state shows its line verbatim, only it');
    const blank = window.NodeSim.renderAnswer({ state: 'mystery' });
    ok(blank.textContent === '', 'C-5: no line, no invented text');

    /* ---- C-1: no analog waveform ------------------------------------------ */
    M.queue['/api/nodesim/start'] = [doneWith('run-3', { analog_waveforms: [], digital_waveforms: [],
                                                         adc_acquisitions: [], events: [] })];
    await S.start();
    ok(S.chart && S.chart.laneCount().drawn === 0 && S.chart.laneCount().gate === 0, 'C-1: laneCount 0');
    ok(txt(EMPTY) === 'cannot draw: the report has no analog waveform inside this window',
       'C-1: the cannot-draw line is shown');
    ok($$('[data-nodesim-chart] .ui-note').some((p) => p.textContent
       === 'No readout inside this window; try a longer window.'), 'C-1 / Q-7: no readout says so');

    /* ---- C-2: two lanes on one key, a reserved key -------------------------- */
    const dupReport = { analog_waveforms: [wf('q9.z (readout)', 'flux', 0, 100, 1, NO_IQ)], digital_waveforms: [],
        adc_acquisitions: [{ start_time: 10, end_time: 50, element: 'q9.z', adc_ports: [], controller: 'con1',
                             fem: 3 }], events: [] };
    M.queue['/api/nodesim/start'] = [doneWith('run-4', dupReport)];
    await S.start();
    ok(txt(EMPTY) === 'cannot draw: two lines share the key q9.z (readout)',
       'C-2: two lanes on one key: the cannot-draw line, not a merge');
    ok(S.chart.laneCount().drawn === 0 && S.chart.laneCount().gate === 2,
       'C-2: both lanes reach the timeline, none is drawn');
    const resReport = { analog_waveforms: [wf('\u0001spectators', 'a', 0, 100, 1, NO_IQ),
                                           wf('\u0001spectators', 'b', 200, 100, 1, NO_IQ)],
                        digital_waveforms: [], adc_acquisitions: [], events: [] };
    M.queue['/api/nodesim/start'] = [doneWith('run-5', resReport)];
    await S.start();
    ok(txt(EMPTY) === 'cannot draw: a line key is reserved', 'C-2: a reserved key: the cannot-draw line');
    const BAD = 'cannot draw: the report has a form SM does not read';
    const badReport = { analog_waveforms: [{ element: 'q1.xy', pulse_name: 'x', timestamp: 'soon', length: 4 }] };
    ok(window.NodeSim.reportToTimeline(badReport).cannot_draw === BAD
       && window.NodeSim.reportToTimeline(null).cannot_draw === BAD, 'a report SM cannot read is refused, not guessed');

    /* ---- start without an answer ------------------------------------------- */
    M.queue['/api/nodesim/start'] = ['offline'];
    await S.start();
    ok(txt('[data-nodesim-lost] .ui-notice-text') === 'No answer from SM; the run stops if this page stops asking.',
       'a start SM does not answer shows the no-answer line');

    /* ---- a cache hit shows only the phases that ran ------------------------ */
    const hit = window.NodeSim.renderAnswer(doneAnswer({ cached: true,
        phases: [{ name: 'copy', s: 0.03 }, { name: 'cache', s: 0.001 }] }));
    ok($$('[data-nodesim-phases] li', hit).map((l) => l.getAttribute('data-phase')).join(',') === 'copy,cache',
       'a cache hit lists exactly copy and cache, no build or spawn');
    ok($$('[data-nodesim-phases] li', hit).every((l) => l.classList.contains('is-done')),
       'a cache hit marks both phases done');

    /* ---- a node in a subfolder, preselected from the link ------------------- */
    const subItem = nodeItem('sub/12_ramsey.py', 'qubits');
    const sub = doc.createElement('div');
    sub.setAttribute('data-node', 'sub/12_ramsey.py');
    doc.body.appendChild(sub);
    M.queue['/api/nodesim/nodes'] = [Object.assign({}, NODES, { items: NODES.items.concat([subItem]) })];
    M.queue['/api/nodesim/status'] = [{ ok: true, state: 'idle', line: '' }];
    await window.NodeSim.mount(sub).ready;
    ok(callsTo('/api/nodesim/nodes').pop().url === '/api/nodesim/nodes?node=sub%2F12_ramsey.py',
       'the page names the wanted node to the server (a subfolder node is not in the folder scan)');
    ok($('[data-nodesim-node]', sub).value === 'sub/12_ramsey.py' && sub.__nodesim.node === 'sub/12_ramsey.py',
       'a node in a subfolder is preselected');
    ok($('[data-nodesim-pick-note]', sub).hidden, 'a found node shows no pick note');
    const away = doc.createElement('div');
    away.setAttribute('data-node', 'elsewhere/13_t1.py');
    doc.body.appendChild(away);
    M.queue['/api/nodesim/nodes'] = [NODES];
    M.queue['/api/nodesim/status'] = [{ ok: true, state: 'idle', line: '' }];
    await window.NodeSim.mount(away).ready;
    ok($('[data-nodesim-pick-note]', away).textContent
       === 'The node asked for is not in the calibration library of this chip.'
       && !$('[data-nodesim-pick-note]', away).hidden, 'a node not in the library says so, not a silent swap');

    /* ---- P2: explicit intent, keep-warm, renewal, instance strip --------------- */
    const warmPage = doc.createElement('div');
    warmPage.setAttribute('data-nodesim', '');
    warmPage.setAttribute('data-warm-pool', 'true');
    doc.body.appendChild(warmPage);
    M.queue['/api/nodesim/nodes'] = [NODES];
    M.queue['/api/nodesim/settings'] = [{ keep_warm: false, label: 'Keep the cloud simulator warm',
        note: 'Starts a cloud instance when this panel opens; it uses one of the account\'s 3 instances '
            + 'until 10 min pass without a click. Off by default.' }];
    M.queue['/api/nodesim/intent'] = [{ state: 'ready', line: 'Node simulator ready.' }];
    M.queue['/api/nodesim/status'] = [{ state: 'ready', line: 'Node simulator ready.' }];
    M.queue['/api/nodesim/instances'] = [{ rows: [{ line: 'Cloud instance open until 12:34.' }] }];
    const warm = window.NodeSim.mount(warmPage);
    await warm.ready;
    await tick();
    ok(callsTo('/api/nodesim/intent').pop().body.explicit === false, 'P2: panel open has no explicit intent');
    const keep = $('[data-nodesim-keep-warm]', warmPage);
    ok(keep && !keep.checked, 'P2: keep-warm is off by default');
    ok(txt('[data-nodesim-instances]') !== null, 'P2: instance strip exists');
    const wpick = $('[data-nodesim-node]', warmPage);
    wpick.value = '11_rabi.py'; change(wpick);
    await tick();
    ok(callsTo('/api/nodesim/intent').pop().body.explicit === true, 'P2: a deliberate node pick sends intent');
    warm.apply({ state: 'needs_instance', line: 'The server needs a new instance.' });
    ok(!$('[data-nodesim-new-instance]', warmPage).hidden, 'P2: hash change shows Start new instance');
    M.queue['/api/nodesim/new-instance'] = [{ state: 'ready', line: 'Node simulator ready.' }];
    $('[data-nodesim-new-instance]', warmPage).click();
    await tick();
    ok(callsTo('/api/nodesim/new-instance').length === 1, 'P2: Start new instance sends its POST');
    warm.apply({ state: 'renew_offer', line: 'The cloud instance closes at 12:34.' });
    ok(!$('[data-nodesim-renew]', warmPage).hidden, 'P2: renewal offer shows its action');
    M.queue['/api/nodesim/renew'] = [{ state: 'ready', line: 'Node simulator ready.' }];
    $('[data-nodesim-renew]', warmPage).click();
    await tick();
    ok(callsTo('/api/nodesim/renew').length === 1, 'P2: renewal sends its POST');
    const warmBox = window.NodeSim.renderAnswer({ state: 'running', line: 'Building.', warm: true, phases: [] });
    ok($$('[data-phase]', warmBox).map((x) => x.getAttribute('data-phase')).join(',')
       === 'copy,lock,build,lease,connect,submit,wait,report', 'P2: warm phases follow the IPC contract');
    ['warming', 'no_slot', 'spawn_failed', 'stopped_spawning', 'stopped_kept', 'idle_closed', 'broker_failed']
        .forEach((state) => {
            const b = window.NodeSim.renderAnswer({ state: state, line: 'Server verdict for ' + state });
            ok(b.textContent.indexOf('Server verdict for ' + state) >= 0, 'P2: verbatim ' + state);
        });
    /* R2B P3-b / P3-c: every failure line reaches the page as text; a new pick clears an old failure. */
    const multi = window.NodeSim.renderAnswer({ state: 'failed', line: 'Head line\nSecond line\nThird line' });
    ok(['Head line', 'Second line', 'Third line'].every((l) => $$('*', multi).some((e) => e.textContent === l)),
       'R2B P3-b: each failure line is its own text element');
    warm.apply({ state: 'failed', line: 'Old failure of the earlier node' });
    ok(warmPage.textContent.indexOf('Old failure of the earlier node') >= 0, 'R2B P3-c: failure line shown');
    wpick.value = '06_flux.py'; change(wpick);
    await tick();
    ok(warmPage.textContent.indexOf('Old failure of the earlier node') < 0, 'R2B P3-c: a new pick clears it');

    /* ---- static rules on the source ------------------------------------------ */
    const src = NODESIM_SRC;
    const lines = Object.values(SPEC_LINES).concat([window.NodeSim.NO_ANSWER]);
    ok(!/ -- |CAS|patch/.test(src), 'nodesim.js has no " -- ", "CAS" or "patch"');
    ok(lines.every((l) => !/ -- |CAS|patch/.test(l)), 'the state lines have no " -- ", "CAS" or "patch"');
    ok(!/hx-on|hx-trigger/.test(src), 'no hx-on and no hx-trigger (CSP)');
    ok(!/innerHTML|outerHTML|insertAdjacentHTML|\beval\(|new Function/.test(src), 'text only: no HTML, no eval');
    ok(!/\.style\b|setAttribute\(\s*["']style/.test(src), 'no inline style');
    ok(!/#[0-9a-fA-F]{3,6}\b|rgba?\(/.test(src), 'no colour literal');
    // A Windows checkout (core.autocrlf) puts CR before every LF; CR is a line ending, not content.
    ok(!/[^\x0d\x0a\x20-\x7e]/.test(src), 'nodesim.js is printable ASCII');
    ok(src.split('\n').every((l) => l.length <= 120), 'nodesim.js lines are at most 120 characters');
    ok(errors.length === 0, 'no uncaught error in the page' + (errors.length ? ': ' + errors.join('; ') : ''));

    const emptyPage = doc.createElement('div');
    emptyPage.setAttribute('data-nodesim', '');
    doc.body.appendChild(emptyPage);
    M.queue['/api/nodesim/nodes'] = [NODES];
    M.queue['/api/nodesim/intent'] = [{ state: 'warming', line: 'Preparing env demo.', id: null }];
    M.queue['/api/nodesim/status'] = [{ state: 'warming', line: 'Preparing env demo.', id: null }];
    const emptyState = window.NodeSim.mount(emptyPage);
    await emptyState.ready;
    const emptyPick = $('[data-nodesim-node]', emptyPage);
    ok(emptyPick.value === '' && emptyPick.options[0].textContent === 'Pick a node',
       'P3-2: no default utility or refused node');
    ok($('[data-nodesim-stop]', emptyPage).hidden, 'P3-6: warming without a run hides Stop');
    const refusedOption = [...emptyPick.options].find((o) => o.value === '90_sweep_all.py');
    ok(refusedOption.disabled && refusedOption.textContent.includes('Server refusal verbatim.'),
       'P3-3: disabled option includes the server refusal verbatim');

    console.log((failed ? 'FAIL' : 'ok') + ' nodesim_selfcheck (' + n + ' assertions)');
    process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL crash ' + e.stack); process.exit(1); });
