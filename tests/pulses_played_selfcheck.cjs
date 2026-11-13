/* jsdom selfcheck for three Pulses-detail drawing rules :
 *   1. the amplitude axis always includes 0 V (rangemode 'tozero')
 *   2. the server's edge note on the committed plot is shown in the plot bar,
 *      and the preview's note replaces it while a preview is drawn
 *   3. Verify vs config re-runs after a config regenerate while it is ON:
 *      a 'configRegenerated' event fetches the ground truth again, and
 *      regenerateThenVerify re-runs it on the detail that is on screen NOW,
 *      even when the detail was re-rendered while the regenerate ran;
 *      toggled OFF, a regenerate leaves it off
 * Run: node tests/pulses_played_selfcheck.cjs   (driven by tests/test_pulse_played_copy.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const OWN = 'qubits.q1.z.operations.cz_gauss';
const EDGE = 'the pulse starts at +46.0 mV and ends at +46.0 mV (23% of the 200.0 mV peak)';
const PREVIEW_EDGE = 'preview edge note';
function detailHtml() {
    const detail = {
        path: OWN, actual_path: OWN, mode: 'single',
        plot: { ok: true, edge_note: EDGE, traces: [{ name: 'I', x: [0, 1, 2], y: [0.046, 0.2, 0.046] }] },
        pulses: [{ path: OWN, actual_path: OWN, label: 'q1 \u00b7 z \u00b7 cz_gauss', role: 'pulse', color: '#1095c1', index: 0,
                   plot: { ok: true, edge_note: EDGE, traces: [{ name: 'I', x: [0, 1, 2], y: [0.046, 0.2, 0.046] }] } }],
    };
    return '<div id="pulse-detail-root" data-pulse-path="' + OWN + '" data-actual-path="' + OWN + '">' +
        '<div class="pulse-plot-bar"><span class="pulse-dirty-pill" hidden></span>' +
        '<span class="pulse-verify-note" hidden></span>' +
        '<span class="pulse-synth-err" hidden></span>' +
        '<span class="pulse-synth-err" data-edge-note="1" hidden></span></div>' +
        '<details open class="pulse-sec" data-pulse-path="' + OWN + '"><summary>s</summary>' +
        '<form class="inline-edit pulse-edit-form"><input type="hidden" name="dot_path" value="' + OWN + '.amplitude">' +
        '<input type="text" name="value" data-param="amplitude" data-kind="float" data-synth="1" data-committed="0.2" value="0.2"></form></details>' +
        '<div id="pulse-detail-plot"></div>' +
        '<script id="pulse-detail-data" type="application/json">' + JSON.stringify(detail) + '</script></div>';
}
const dom = new JSDOM('<!doctype html><html><body><div id="inspector-pane">' + detailHtml() + '</div></body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.HTMLElement = window.HTMLElement; global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.requestAnimationFrame = (f) => setTimeout(f, 0);
global.localStorage = window.localStorage;

const layouts = [];
window._plotlyRender = function (id, data, layout) { layouts.push(layout); return null; };
global._plotlyRender = window._plotlyRender;
window._debounce = function (k, f) { f(); };
global._debounce = window._debounce;
window.htmx = { ajax: function () { return Promise.resolve(); } };
global.htmx = window.htmx;
let truthCalls = 0;
let regenResolve = null;
window.fetch = function (url) {
    if (String(url).indexOf('/api/pulse/ground-truth') === 0) {
        truthCalls++;
        return Promise.resolve({ json: () => Promise.resolve({ ok: true, meta: { at: 'now' },
            plot: { ok: true, traces: [{ name: 'I', x: [0, 1], y: [0, 0.2] }] }, comparison: { match: true } }) });
    }
    if (String(url) === '/config/regenerate') {
        return new Promise(function (res) { regenResolve = function () { res({ ok: true, text: () => Promise.resolve('<p>ok</p>') }); }; });
    }
    return Promise.resolve({ json: () => Promise.resolve({ ok: true,
        plot: { ok: true, edge_note: PREVIEW_EDGE, traces: [{ name: 'I', x: [0, 1], y: [0, 0.5] }] } }) });
};
global.fetch = window.fetch;

window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));
const d = window.document;
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
const edgeEl = () => d.querySelector('.pulse-plot-bar [data-edge-note]');

(async function () {
    window.PulsesPage.initDetail();
    await tick(40);
    const L = layouts[layouts.length - 1] || {};
    ok(L.yaxis && L.yaxis.rangemode === 'tozero', 'the amplitude axis includes 0 V (rangemode ' + (L.yaxis && L.yaxis.rangemode) + ')');
    ok(edgeEl() && !edgeEl().hidden && edgeEl().textContent === EDGE, 'the committed plot\'s edge note is shown (' + (edgeEl() && edgeEl().textContent) + ')');
    // the preview's own note replaces it while a preview is drawn
    const inp = d.querySelector('input[data-param="amplitude"]');
    inp.value = '0.3'; inp.dispatchEvent(new window.Event('input', { bubbles: true }));
    await tick(60);
    ok(edgeEl().textContent === PREVIEW_EDGE, 'a preview shows its own edge note (' + edgeEl().textContent + ')');
    inp.value = '0.2'; inp.dispatchEvent(new window.Event('input', { bubbles: true }));
    await tick(60);

    // 3a. Verify ON, then a config regenerate elsewhere: the comparison runs again
    window.PulsesPage.verifyPulse(null);
    await tick(30);
    ok(truthCalls === 1, 'Verify fetched the ground truth once (' + truthCalls + ')');
    d.body.dispatchEvent(new window.CustomEvent('configRegenerated', { bubbles: true }));
    await tick(30);
    ok(truthCalls === 2, 'a regenerated config re-runs Verify while it is on (' + truthCalls + ')');
    const note = d.querySelector('.pulse-verify-note');
    ok(note && !note.hidden && /matches/.test(note.textContent), 'the re-run comparison is on screen (' + (note && note.textContent) + ')');

    // 3b. regenerateThenVerify while the detail is re-rendered meanwhile
    const btn = d.createElement('button');
    d.getElementById('pulse-detail-root').appendChild(btn);
    window.PulsesPage.regenerateThenVerify(btn);
    await tick(10);
    d.getElementById('inspector-pane').innerHTML = detailHtml();     // a re-render during the regenerate
    window.PulsesPage.initDetail();
    await tick(30);
    const before = truthCalls;
    regenResolve();
    await tick(40);
    ok(truthCalls === before + 1, 'after Regenerate the comparison runs on the re-rendered detail (' + (truthCalls - before) + ')');
    const note2 = d.querySelector('#pulse-detail-root .pulse-verify-note');
    ok(note2 && !note2.hidden && /matches/.test(note2.textContent), 'the re-rendered detail shows it (' + (note2 && note2.textContent) + ')');

    // 3c. toggled OFF: a regenerate leaves it off
    window.PulsesPage.verifyPulse(null);          // second press turns the overlay off
    await tick(20);
    const off = truthCalls;
    d.body.dispatchEvent(new window.CustomEvent('configRegenerated', { bubbles: true }));
    await tick(30);
    ok(truthCalls === off, 'with Verify off a regenerate fetches nothing (' + (truthCalls - off) + ')');

    console.log(fails ? 'FAILED ' + fails : 'all ok');
    process.exit(fails ? 1 : 0);
})();
