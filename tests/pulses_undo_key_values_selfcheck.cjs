/* jsdom selfcheck: the Pulses inspector's key-value tiles and the physical
 * output note follow an in-place undo / redo. app.js _revertCell repaints the
 * field; the headline tile and the note beside the field read the same stored
 * value and used to keep the old one. Pins:
 *   1. the tile of the reverted field shows the field's new committed text,
 *      unit kept; the other tile is untouched
 *   2. the note asks /bulk/phys once with the reverted amplitude path and
 *      paints the answer (LF volts text)
 *   3. a blank answer paints the blank mark and its reason
 *   4. a slow older answer never lands over a newer one
 *   5. another pulse's undo touches neither tile nor note
 * Run: node tests/pulses_undo_key_values_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PULSE = 'qubits.q1.z.operations.const';
const AMP = PULSE + '.amplitude';
const LEN = PULSE + '.length';
function tile(label, dp, value, unit) {
    return '<div class="ui-stat" data-stat-path="' + dp + '"><div class="ui-stat-label">' + label + '</div>'
        + '<p class="ui-stat-value">' + value + '<span class="ui-stat-unit">' + unit + '</span></p></div>';
}
function row(dp, key, value, note) {
    return '<tr><td class="col-val"><form class="inline-edit pulse-edit-form">'
        + '<input type="hidden" name="dot_path" value="' + dp + '">'
        + '<input type="text" name="value" data-param="' + key + '" data-kind="float" data-synth="1" data-committed="'
        + value + '" value="' + value + '"></form>' + note + '</td></tr>';
}
const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray"></div>'
    + '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="' + PULSE + '">'
    + '<div class="ui-stats" role="group">' + tile('Length', LEN, '40', 'ns') + tile('Amplitude', AMP, '0.25', 'V')
    + '</div><table>' + row(LEN, 'length', '40', '')
    + row(AMP, 'amplitude', '0.25', '<span class="phys-note muted" title="LF amplitude is the output voltage">'
        + '\u2248 250 mV</span>')
    + '</table><div id="pulse-detail-plot"></div><span class="pulse-synth-err" hidden></span>'
    + '</div><script id="pulse-detail-data" type="application/json">'
    + JSON.stringify({ plot: { ok: true, traces: [] }, overlays: [] }) + '</script></div>'
    + '</body></html>', { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage;
const timers = {};
window._debounce = function (key, fn, delay) {
    if (timers[key]) clearTimeout(timers[key]); timers[key] = setTimeout(fn, delay);
};
global._debounce = window._debounce;
window._plotlyRender = function () { return Promise.resolve(null); };
window.requirePlotly = () => Promise.resolve(); window.Plotly = { purge: () => {} };
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
// /bulk/phys stub: answers queue (value, delay); synth never answers (not under test)
const physCalls = []; let answers = [];
global.fetch = (url, opts) => {
    if (String(url) !== '/bulk/phys') return new Promise(() => {});
    physCalls.push(JSON.parse(opts.body));
    const a = answers.length ? answers.shift() : { body: { ok: true, phys: {} }, delay: 10 };
    return new Promise((res) => setTimeout(() => res({ ok: true, json: () => Promise.resolve(a.body) }), a.delay));
};
window.fetch = global.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));

const d = window.document;
const ampInput = d.querySelector('input[data-param="amplitude"]');
const ampTile = d.querySelector('.ui-stat[data-stat-path="' + AMP + '"] .ui-stat-value');
const lenTile = d.querySelector('.ui-stat[data-stat-path="' + LEN + '"] .ui-stat-value');
const note = () => d.querySelector('.phys-note');
function lf(v) { return { ok: true, phys: { [AMP]: { kind: 'lf', volts: v, text: (v * 1000) + ' mV', peak: 1 } } }; }
function revert(dp, committed) {
    // what app.js _revertCell does on cellsReverted, then the event itself
    // The page listener can precede app.js: the answer must win over stale DOM.
    d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
        { dot_path: dp, old_value_str: committed, old_value_disp: committed, old_kind: 'num' }] } }));
}

(async function main() {
    d.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: d.getElementById('inspector-pane') } }));
    await sleep(50);

    // 1 + 2
    revert(LEN, '100');
    ok(lenTile.firstChild.nodeValue === '100', 'undo length uses the answer before the input repaint');
    revert(LEN, '120');
    ok(lenTile.firstChild.nodeValue === '120', 'redo length uses the answer before the input repaint');
    revert(LEN, '40');
    answers.push({ body: lf(0.1), delay: 10 });
    revert(AMP, '0.1');
    await sleep(60);
    ok(ampTile.firstChild.nodeValue === '0.1', 'the amplitude tile shows the reverted value');
    ok(ampTile.querySelector('.ui-stat-unit') && ampTile.querySelector('.ui-stat-unit').textContent === 'V',
       'the tile keeps its unit');
    ok(lenTile.firstChild.nodeValue === '40', 'the length tile is untouched');
    ok(physCalls.length === 1 && JSON.stringify(physCalls[0].paths) === JSON.stringify([AMP]),
       'the note asks /bulk/phys once for the reverted amplitude path');
    ok(note().textContent === '\u2248 100 mV', 'the note shows the annotation of the value stored now');

    // 3. a blank answer
    answers.push({ body: { ok: true, phys: { [AMP]: null }, blank: { [AMP]: { mark: '?', text: 'no port' } } },
                   delay: 10 });
    revert(AMP, '0');
    await sleep(60);
    ok(note().textContent === '?' && note().title === 'no port',
       'a blank answer paints the blank mark and its reason');

    // 4. an older slow answer loses to a newer fast one
    answers.push({ body: lf(0.3), delay: 120 });
    answers.push({ body: lf(0.2), delay: 10 });
    revert(AMP, '0.3');
    revert(AMP, '0.2');
    await sleep(220);
    ok(note().textContent === '\u2248 200 mV' && ampTile.firstChild.nodeValue === '0.2',
       'a slow older answer never lands over a newer one');

    // 5. another pulse's undo
    const before = physCalls.length;
    revert('qubits.q2.z.operations.const.amplitude', '0.9');
    await sleep(60);
    ok(physCalls.length === before && ampTile.firstChild.nodeValue === '0.2',
       "another pulse's undo touches neither tile nor note");

    // 6. a list value: the tile gets the full JSON, as the input repaint does, never the 24-char preview
    d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
        { dot_path: LEN, old_kind: 'list', old_value_disp: '[1, 2, 3, ...', old_value_json: '[1,2,3,4,5,6,7,8,9]' }] } }));
    ok(lenTile.firstChild.nodeValue === '[1,2,3,4,5,6,7,8,9]', 'a list value repaints the tile from its JSON, not its preview');

    // 7. a missing pulse's answer refreshes the list once: through its header, never also by the swap handler
    const fired = [];
    window.htmx.trigger = (el, name) => fired.push(name);
    const missing = new window.Event('htmx:beforeSwap', { bubbles: true });
    missing.detail = { target: d.getElementById('inspector-pane'), shouldSwap: false, isError: true,
                       xhr: { getResponseHeader: (h) => (h === 'X-Pulse-Missing' ? '1' : null) } };
    d.body.dispatchEvent(missing);
    ok(missing.detail.shouldSwap === true && missing.detail.isError === false && fired.length === 0,
       'a missing pulse swaps its note in and leaves the list refresh to the response header');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('pulses undo key values selfcheck: all ok');
    process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
