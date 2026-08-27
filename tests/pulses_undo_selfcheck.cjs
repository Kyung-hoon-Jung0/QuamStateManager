/* jsdom selfcheck: the Pulses committed waveform follows Ctrl+Z / Ctrl+Shift+Z
 * (docs/141 4e). An undo reverts the VALUES in place; the plot used to stay
 * at root._committedPlot -- the pre-undo waveform under the reverted
 * numbers. Pins:
 *   1. an undo touching the open pulse refreshes the committed plot with ONE
 *      synth request for the final state (params {} = the stored state)
 *   2. a state this page has already drawn comes from the RAM cache -- no
 *      request
 *   3. a burst of presses is one debounced refresh (one request)
 *   4. another pulse's undo is ignored
 *   5. while a refresh is pending the stale committed plot is never drawn
 *   6. a slow older refresh never lands over a newer one (generation token)
 * Run: node tests/pulses_undo_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PULSE = 'qubits.q1.xy.operations.x180';
const PLOT0 = { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.1, 0] }] };
const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray"></div>'
    + '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="' + PULSE + '">'
    + '<form class="inline-edit pulse-edit-form"><input type="hidden" name="dot_path" value="' + PULSE + '.amplitude">'
    + '<input type="text" name="value" data-param="amplitude" data-kind="float" data-synth="1" data-committed="0.1" value="0.1"></form>'
    + '<form class="inline-edit pulse-edit-form"><input type="hidden" name="dot_path" value="' + PULSE + '.length">'
    + '<input type="text" name="value" data-param="length" data-kind="int" data-synth="1" data-committed="40" value="40"></form>'
    + '<div id="pulse-detail-plot"></div><span class="pulse-synth-err" hidden></span>'
    + '</div><script id="pulse-detail-data" type="application/json">' + JSON.stringify({ plot: PLOT0, overlays: [] }) + '</script></div>'
    + '</body></html>', { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage;
// the app.js helpers pulses.js reads bare
const timers = {};
window._debounce = function (key, fn, delay) { if (timers[key]) clearTimeout(timers[key]); timers[key] = setTimeout(fn, delay); };
global._debounce = window._debounce;
const renders = [];
window._plotlyRender = function (divId, data) { renders.push({ divId: divId, y: data.length ? data[0].y.slice() : null }); return Promise.resolve(null); };
window.requirePlotly = () => Promise.resolve(); window.Plotly = { purge: () => {} };
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
// synth stub: records bodies; each response carries a y scaled from a counter; delay per call
const synthCalls = []; let delays = [];
let plotN = 0;
global.fetch = (url, opts) => {
    if (String(url).indexOf('/api/pulse/synth') !== 0) return new Promise(() => {});
    const body = JSON.parse(opts.body); synthCalls.push(body);
    const n = ++plotN; const d = delays.length ? delays.shift() : 30;
    return new Promise((res) => setTimeout(() => res({ json: () => Promise.resolve({ ok: true, plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, n, 0] }] } }) }), d));
};
window.fetch = global.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));

const d = window.document;
const root = d.getElementById('pulse-detail-root');
const amp = d.querySelector('input[data-param="amplitude"]');
function revert(dotPath, committed) {
    // what app.js's _revertCell does on cellsReverted, then the event itself
    if (committed !== undefined) { amp.value = committed; amp.setAttribute('data-committed', committed); amp.dispatchEvent(new Event('input', { bubbles: true })); }
    d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [{ dot_path: dotPath, old_value_str: committed || '', old_value_disp: committed || '', old_kind: 'num' }] } }));
}

(async function main() {
    d.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: d.getElementById('inspector-pane') } }));
    await sleep(80);
    ok(root._committedPlot && root._committedPlot.traces[0].y[1] === 0.1, 'fixture: the detail render stashed the committed plot');
    const r0 = renders.length;

    // 1. an undo touching this pulse -> one synth request for the stored state
    revert(PULSE + '.amplitude', '0.2');
    await sleep(400);
    ok(synthCalls.length === 1 && synthCalls[0].path === PULSE && JSON.stringify(synthCalls[0].params) === '{}',
       'an undo on the open pulse asks synth ONCE for the stored state (params {})');
    ok(root._committedPlot.traces[0].y[1] === 1, 'the committed plot is the new waveform');
    ok(renders.length > r0 && renders[renders.length - 1].y[1] === 1, 'and it was drawn');

    // 2. back to a state already drawn -> RAM cache, no request
    const c1 = synthCalls.length, r1 = renders.length;
    revert(PULSE + '.amplitude', '0.1');
    await sleep(400);
    ok(synthCalls.length === c1, 'undo back to an already-drawn state costs NO synth request (cache)');
    ok(root._committedPlot.traces[0].y[1] === 0.1 && renders[renders.length - 1].y[1] === 0.1 && renders.length > r1,
       'the cached waveform is drawn');

    // 3. a burst -> one refresh
    const c2 = synthCalls.length;
    for (let i = 0; i < 5; i++) { revert(PULSE + '.amplitude', '0.' + (3 + i)); await sleep(10); }
    await sleep(500);
    ok(synthCalls.length === c2 + 1, 'a burst of 5 presses is ONE synth request (' + (synthCalls.length - c2) + ')');
    ok(root._committedPlot.traces[0].y[1] === plotN, 'for the final state');

    // 4. another pulse's undo is ignored
    const c3 = synthCalls.length, r3 = renders.length;
    d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [{ dot_path: 'qubits.q2.xy.operations.x180.amplitude', old_value_str: '1' }] } }));
    await sleep(300);
    ok(synthCalls.length === c3 && renders.length === r3 && !root._cpPending, "another pulse's undo does nothing here");

    // 5. while the refresh is pending, the stale committed plot is never drawn
    const r4 = renders.length;
    delays = [200];                                   // slow synth
    revert(PULSE + '.amplitude', '0.9');              // fires 'input' -> schedulePreview (150 ms) too
    await sleep(200);                                 // preview debounce fired at 150; synth still pending
    ok(renders.length === r4, 'no render of the stale committed plot while the refresh is pending');
    await sleep(300);
    ok(renders.length > r4 && renders[renders.length - 1].y[1] === plotN, 'the refreshed plot is what gets drawn');

    // 6. an older, slower refresh never lands over a newer one
    delays = [400, 30];
    revert(PULSE + '.amplitude', '0.11');
    await sleep(130);                                 // past the 120 ms debounce: request A issued (slow)
    revert(PULSE + '.amplitude', '0.12');
    await sleep(700);
    const last = plotN;
    ok(root._committedPlot.traces[0].y[1] === last && renders[renders.length - 1].y[1] === last,
       'the newer state wins even though the older request answered later');
    ok(!root._cpPending, 'nothing left pending');

    // 7. the live PREVIEW is memoised: typing back to a value already
    //    previewed redraws from RAM, no synth request
    const c7 = synthCalls.length;
    amp.value = '0.5'; amp.dispatchEvent(new Event('input', { bubbles: true }));   // dirty -> preview
    await sleep(300);
    ok(synthCalls.length === c7 + 1 && JSON.stringify(synthCalls[c7].params) === '{"amplitude":"0.5"}', 'a new preview value asks synth once');
    amp.value = amp.getAttribute('data-committed'); amp.dispatchEvent(new Event('input', { bubbles: true }));   // back to clean
    await sleep(300);
    const r7 = renders.length;
    amp.value = '0.5'; amp.dispatchEvent(new Event('input', { bubbles: true }));   // the same preview again
    await sleep(300);
    ok(synthCalls.length === c7 + 1 && renders.length > r7, 'the same preview value again: drawn from RAM, no request');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
