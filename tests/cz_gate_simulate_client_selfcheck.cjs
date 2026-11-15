/* jsdom selfcheck: the Simulate action of the CZ gate dialog (cz-gate.js).
 *
 * The fetch mock and the app's window-id wrapper are installed in the realm
 * (window.eval), then the real dialog script runs. Pins: the action's enabled
 * state and request (the check body plus the pair on screen, the window id
 * header), the server's line shown verbatim, polling with elapsed seconds and
 * Stop, the done answer matched onto lanes (time step unknown never drawn,
 * unmatched ports and lanes named, notes verbatim), stale output dropped on a
 * form edit / pair change / new check / close, and the two error paths. */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..', 'quam_state_manager', 'web');
const DATA = {
    pairs: [{ id: 'q1-2', control: 'q1', target: 'q2', moving_qubit: 'control', gates: [] },
            { id: 'q2-3', control: 'q2', target: 'q3', moving_qubit: 'target', gates: [] }],
    selected: 'q1-2', env: 'lab',
    classes: [{ 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [],
                slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: "the moving qubit's flux line", required: true,
                          pulse_classes: [{ 'class': 'P.SquarePulse', label: 'Square', length: 'explicit',
                                            fields: [{ name: 'length', label: 'Length', kind: 'int', unit: 'ns', 'default': 100 },
                                                     { name: 'amplitude', label: 'Amplitude', kind: 'float', unit: 'V', 'default': 0.1 }] }] }] }],
};
function dialogHtml() {
    if (process.env.CZG_DIALOG) return fs.readFileSync(process.env.CZG_DIALOG, 'utf8');
    let t = fs.readFileSync(path.join(ROOT, 'templates', '_cz_gate_dialog.html'), 'utf8');
    t = t.replace(/\{#[\s\S]*?#\}/g, '');
    t = t.replace('{{ data | tojson }}', JSON.stringify(DATA));
    t = t.replace(/\{%-? for p in data\.pairs[\s\S]*?endfor -?%\}/,
        DATA.pairs.map((p) => '<option value="' + p.id + '"' + (p.id === DATA.selected ? ' selected' : '') + '>' + p.id + '</option>').join(''));
    t = t.replace(/\{%-?[\s\S]*?-?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '');
    return t;
}

const PAGE = '<!doctype html><html><body><div id="inspector-pane"></div>'
    + '<button type="button" id="gate" data-czg-open data-czg-pair="q1-2">+ Gate</button></body></html>';
const dom = new JSDOM(PAGE, { url: 'http://localhost/pairs', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;

/* what the mock answers; the test sets these */
const M = { sent: [], sim: [], twoPairs: false };
window.__dialogHtml = dialogHtml;
window.__M = M;
window.__lanes = () => [
    { key: 'q1.z', group: 'gate', port: 'con1 / 5 / 1', segments: [], sim: null },
    { key: 'q2.z', group: 'gate', port: 'con1 / 5 / 2', segments: [], sim: null },
    { key: 'c12', group: 'gate', port: 'con1 / 5 / 3', segments: [], sim: null },
    { key: 'q3.z', group: 'gate', port: 'con1 / 5 / 4', segments: [], sim: null }];
window.eval(`
window.fetch = function (url, opt) {
    var M = window.__M;
    url = String(url);
    var hdr = opt && opt.headers;
    var win = hdr && (typeof hdr.get === 'function' ? hdr.get('X-SM-Window-ID') : hdr['X-SM-Window-ID']);
    function respond(status, j) {
        return Promise.resolve({ ok: status < 400, status: status,
            json: function () { return j === '__bad__' ? Promise.reject(new Error('not json')) : Promise.resolve(j); },
            text: function () { return Promise.resolve(''); } });
    }
    if (url.indexOf('/api/pulse/cz-gate/simulate/') >= 0) {
        var kind = url.split('/simulate/')[1].split('?')[0];
        M.sim.push({ kind: kind, url: url, body: opt && opt.body ? JSON.parse(opt.body) : null, win: win });
        var q = M.simQueue && M.simQueue[kind] && M.simQueue[kind].shift();
        if (q === 'offline') return Promise.reject(new TypeError('Failed to fetch'));
        if (q) return respond(q[0], q[1]);
        return respond(200, { ok: true, state: 'running', line: 'Simulating on the QM-SaaS cloud simulator: building the gate program.', id: 'r1' });
    }
    var http = url.replace(/^https?:\\/\\/[^/]+/, '');
    if (!opt || !opt.method) return Promise.resolve({ ok: true, status: 200, text: function () { return Promise.resolve(window.__dialogHtml()); } });
    var body = JSON.parse(opt.body);
    var k = http.split('/').pop();
    M.sent.push({ kind: k, body: body });
    if (k === 'plan') return respond(200, { ok: true, plan_sig: 's' + JSON.stringify(body), pairs: body.pairs.map(function (p) {
        return { pair: p, ok: true, refusals: [], creates: 1, paths: ['a'], layout: 'known',
                 operations: { flux_pulse_qubit: { key: 'op', channel: 'q1.z' } }, fields: { flux_pulse_qubit: {} },
                 pulses: { flux_pulse_qubit: { __class__: 'P.SquarePulse', length: 100, amplitude: 0.1 } } }; }) });
    if (k === 'check') {
        var pairs = M.twoPairs ? ['q1-2', 'q2-3'] : body.pairs;
        return respond(200, { ok: true, status: 'ok', plan_sig: 'x', check_token: 't', pairs: pairs.map(function (p) {
            return { pair: p, gate: body.gate_name, ok: true, refusals: [], checks: [{ name: 'range', status: 'pass', message: 'ok' }],
                     timeline: { lanes: window.__lanes(), notes: [], cannot_draw: null }, tiles: [], intent: [], paths: ['a'],
                     verdict: { allowed: true, state: 'verified', reason: '' } }; }) });
    }
    return respond(200, { ok: true });
};
`);
// the app's window-id wrapper (the first block of app.js) wraps the mock as it wraps the real fetch
{
    const app = fs.readFileSync(path.join(ROOT, 'static', 'app.js'), 'utf8');
    const end = app.indexOf('}());');
    window.eval(app.slice(0, end + 5));
}
const charts = [];
window.GateTimeline = { render: (host, data) => { charts.push(data); return { destroy() {} }; } };
window.htmx = { ajax: () => {}, trigger: () => {} };
window.showToast = () => {};
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'ui.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'cz-gate.js'), 'utf8'));

let n = 0, failed = 0;
function ok(cond, label) { n++; if (cond) console.log('ok - ' + label); else { failed++; console.log('FAIL ' + label); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(cond, cap) { for (let i = 0; i < (cap || 400); i++) { if (cond()) return true; await tick(10); } return false; }
const $ = (s) => doc.querySelector(s);
const simBtn = () => $('[data-czg-sim]');
const line = () => $('[data-czg-sim-line]').textContent;
const elapsed = () => $('[data-czg-sim-elapsed]').textContent;
const notes = () => [...doc.querySelectorAll('[data-czg-sim-notes] p')].map((p) => p.textContent);
const simOf = (kind) => M.sim.filter((x) => x.kind === kind);
const lastChart = () => charts[charts.length - 1];
const laneOf = (k) => (lastChart().lanes || []).filter((l) => l.key === k)[0];
function type(input, v) { input.value = v; input.dispatchEvent(new window.Event('input', { bubbles: true })); }
const STARTING = 'Starting the simulation.';
const RUNNING = 'Simulating on the QOP simulator at 10.0.0.1: building the gate program.';
const DONE = { ok: true, state: 'done', id: 'r1', line: 'Simulated on the QOP simulator at 10.0.0.1: 1200 ns.', sim_ns: 1200,
    note: 'Simulated output after the port\'s filters; the time axis starts at the program start.',
    cut_note: 'The simulation window ended while these ports were still playing: con1 / 5 / 1',
    time_step_note: 'The time step of these ports is not known: con1 / 5 / 4.',
    cannot_draw: 'cannot draw: a loop',
    ports: [
        { key: 'con1/5/1', port_text: 'con1 / 5 / 1', controller: 'con1', fem: 5, port: 1, elements: ['q1.z'], t0_ns: 300, dt_ns: 0.5, volts: [0, 0.1, 0.2], time_step_unknown: false },
        { key: 'con1/5/2', port_text: 'con1 / 5 / 2', controller: 'con1', fem: 5, port: 2, elements: ['q7.z'], t0_ns: 0, dt_ns: 1, volts: [0.3], time_step_unknown: false },
        { key: 'con1/5/4', port_text: 'con1 / 5 / 4', controller: 'con1', fem: 5, port: 4, elements: ['q3.z'], t0_ns: 0, dt_ns: null, volts: [0.4, 0.4], time_step_unknown: true },
        { key: 'con2/1/1', port_text: 'con2 / 1 / 1', controller: 'con2', fem: 1, port: 1, elements: ['q1.z'], t0_ns: 0, dt_ns: 1, volts: [0.5], time_step_unknown: false }] };

async function openAndCheck(two) {
    M.twoPairs = !!two;
    $('#gate').click();
    await until(() => $('#czg-dialog') && M.sent.some((s) => s.kind === 'plan'));
    await tick(400);
    type($('#czg-name'), 'cz_sim');
    await tick(400);
    const nChecks = M.sent.filter((s) => s.kind === 'check').length;
    $('#czg-primary').click();
    await until(() => M.sent.filter((s) => s.kind === 'check').length > nChecks && /Create/.test($('#czg-primary').textContent));
    await tick(20);
}

(async () => {
    $('#gate').click();
    await until(() => $('#czg-dialog') && M.sent.some((s) => s.kind === 'plan'));
    await tick(400);
    ok(simBtn() && simBtn().classList.contains('btn-sync') && simBtn().textContent === 'Simulate' && simBtn().disabled,
       'Simulate is a kit button, off before a check');
    type($('#czg-name'), 'cz_sim');
    await tick(400);
    $('#czg-primary').click();
    await until(() => /Create/.test($('#czg-primary').textContent));
    await tick(20);
    ok(!simBtn().disabled, 'Simulate is on once the check has a plan to draw');
    const checkBody = M.sent.filter((s) => s.kind === 'check').pop().body;

    // start, poll, elapsed, Stop
    M.simQueue = { start: [[200, { ok: true, state: 'starting', id: 'r1', line: STARTING }]],
                   status: [[200, { ok: true, state: 'running', id: 'r1', line: RUNNING, elapsed_s: 3.2 }]] };
    simBtn().click();
    await until(() => simOf('start').length === 1 && line() === STARTING);
    const st = simOf('start')[0];
    ok(st && JSON.stringify(st.body) === JSON.stringify(Object.assign({}, checkBody, { pair: 'q1-2' })),
       'start posts the check body plus the pair on screen');
    ok(st && typeof st.win === 'string' && st.win.length > 0, 'start carries the window id header');
    ok(line() === STARTING && simBtn().textContent === 'Stop' && !simBtn().disabled, 'starting: the server line verbatim, the action is Stop');
    ok(elapsed() === '0 s', 'starting: elapsed seconds shown');
    await until(() => simOf('status').length >= 1 && line() === RUNNING, 300);
    ok(simOf('status').length >= 1 && /status\?id=r1$/.test(simOf('status')[0].url), 'status is polled with the run id');
    ok(line() === RUNNING && elapsed() === '3 s', 'running: the server line verbatim and the elapsed seconds');
    M.simQueue.stop = [[200, { ok: true, state: 'running', phase: 'stopping', id: 'r1', line: 'Simulating on the QOP simulator at 10.0.0.1: stopping.' }]];
    simBtn().click();
    await until(() => simOf('stop').length === 1 && /stopping/.test(line()));
    ok(simOf('stop').length === 1 && simOf('stop')[0].body.id === 'r1', 'Stop posts stop with the id');
    ok(/stopping\.$/.test(line()), 'the stop answer line is shown');

    // done: the next status is the result
    M.simQueue.status = [[200, DONE]];
    await until(() => line() === DONE.line, 300);
    ok(line() === DONE.line && simBtn().textContent === 'Simulate' && elapsed() === '', 'done: the server line, the action is Simulate again');
    const q1 = laneOf('q1.z');
    ok(q1 && q1.sim && q1.sim.v.join() === '0,0.1,0.2' && q1.sim.t_ns.join() === '300,300.5,301', 'a matched lane gets sim with t0 + i*dt');
    ok(laneOf('q2.z') && !laneOf('q2.z').sim, 'a port on the lane\'s port but naming another element is not drawn there');
    ok(laneOf('q3.z') && !laneOf('q3.z').sim, 'a port whose time step is unknown is never drawn');
    const ns = notes();
    [DONE.time_step_note, DONE.cut_note, DONE.note, DONE.cannot_draw].forEach((t) => ok(ns.indexOf(t) >= 0, 'note verbatim: ' + t.slice(0, 30)));
    ok(ns.some((t) => /con1 \/ 5 \/ 2/.test(t) && /q7\.z/.test(t) && /not drawn/.test(t)), 'an unmatched port is named in one line');
    ok(ns.some((t) => /con2 \/ 1 \/ 1/.test(t)), 'a port on another controller is named, not drawn');
    ok(ns.some((t) => /^c12 has no simulated output/.test(t)) && ns.some((t) => /^q2\.z has no simulated output/.test(t)),
       'a lane the simulator gave no port for is named');
    ok(!ns.some((t) => /^q3\.z has no/.test(t)), 'a time-step-unknown lane is covered by the server note, not a second line');
    const statusN = simOf('status').length;
    await tick(1300);
    ok(simOf('status').length === statusN, 'polling stops at done');

    // a form edit drops the traces and the line
    const chartsBefore = charts.length;
    type($('input[data-czg-name="amplitude"]'), '0.2');
    await tick(20);
    ok(charts.length > chartsBefore && !laneOf('q1.z').sim, 'a form edit draws the chart again without the traces');
    ok(line() === '' && notes().length === 0 && simBtn().disabled, 'a form edit clears the status line and the notes; Simulate waits for a check');

    // a new check after a simulation drops it too
    await tick(400);
    $('#czg-primary').click();
    await until(() => /Create/.test($('#czg-primary').textContent));
    M.simQueue = { start: [[200, DONE]] };
    simBtn().click();
    await until(() => line() === DONE.line);
    ok(laneOf('q1.z').sim, 'done straight from start draws the traces');
    await window.CzGate._check();
    await tick(20);
    ok(line() === '' && !laneOf('q1.z').sim && notes().length === 0, 'a new check removes the simulated traces and the line');

    // running, then the form changes: polling stops and the run is stopped
    M.simQueue = { start: [[200, { ok: true, state: 'running', id: 'r2', line: RUNNING }]], status: [] };
    await tick(400);
    await window.CzGate._check();
    await until(() => !simBtn().disabled, 100);
    simBtn().click();
    await until(() => line() === RUNNING);
    const stops0 = simOf('stop').length;
    type($('input[data-czg-name="amplitude"]'), '0.25');
    await tick(20);
    const polls0 = simOf('status').length;
    await tick(1300);
    ok(simOf('status').length === polls0, 'a form edit stops the polling');
    ok(simOf('stop').length === stops0 + 1 && simOf('stop').pop().body.id === 'r2', 'a form edit stops the running simulation');
    ok(line() === '', 'a form edit clears a running status line');

    // errors: network failure, non-JSON, 409 busy
    await tick(400);
    await window.CzGate._check();
    await tick(20);
    M.simQueue = { start: ['offline'] };
    simBtn().click();
    await until(() => line() !== STARTING && line() !== '');
    ok(line() === 'Not simulated: the app did not answer.' && simBtn().textContent === 'Simulate', 'network failure: the one client line');
    M.simQueue = { start: [[500, '__bad__']] };
    simBtn().click();
    await tick(30);
    ok(line() === 'Not simulated: the app did not answer.', 'a non-JSON answer: the same line');
    const BUSY = 'A simulation is already running in this window. Stop it first.';
    M.simQueue = { start: [[409, { ok: true, state: 'busy', line: BUSY }]],
                   stop: [[200, { ok: true, state: 'stopped', line: 'Not simulated: stopped.' }]] };
    simBtn().click();
    await until(() => line() === BUSY);
    ok(line() === BUSY && simBtn().textContent === 'Stop' && !simBtn().disabled, '409 busy: the server line and Stop offered');
    const stops1 = simOf('stop').length;
    simBtn().click();
    await until(() => simOf('stop').length > stops1);
    await tick(20);
    ok(line() === 'Not simulated: stopped.' && simBtn().textContent === 'Simulate', 'Stop after busy shows the server line');

    // a pair change drops the output
    window.CzGate.close(true);
    await tick(20);
    await openAndCheck(true);
    M.simQueue = { start: [[200, DONE]] };
    simBtn().click();
    await until(() => line() === DONE.line);
    ok(simOf('start').pop().body.pair === 'q1-2' && laneOf('q1.z').sim, 'the shown pair is simulated');
    const seg = doc.querySelectorAll('[data-czg-pair-idx]');
    const other = [...seg].filter((b) => b.getAttribute('data-czg-pair-idx') === '1')[0];
    if (other) other.click(); else { window.CzGate.state().pairIdx = 1; }
    await tick(20);
    ok(other && line() === '' && !laneOf('q1.z').sim && notes().length === 0, 'a pair change removes the traces and the line');
    M.simQueue = { start: [[200, { ok: true, state: 'starting', id: 'r3', line: STARTING }]], status: [] };
    simBtn().click();
    await until(() => line() === STARTING);
    ok(simOf('start').pop().body.pair === 'q2-3', 'Simulate sends the pair on screen');

    // closing the dialog stops the polling
    const stops2 = simOf('stop').length;
    window.CzGate.close(true);
    const polls1 = simOf('status').length;
    await tick(1300);
    ok(simOf('status').length === polls1, 'closing the dialog stops the polling');
    ok(simOf('stop').length === stops2 + 1 && simOf('stop').pop().body.id === 'r3', 'closing the dialog stops the running simulation');

    console.log(failed ? `FAIL cz_gate_simulate_client_selfcheck (${n} assertions, ${failed} failed)` : `ok cz_gate_simulate_client_selfcheck (${n} assertions)`);
    process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL crashed: ' + (e && e.stack || e)); process.exit(1); });
