/* jsdom selfcheck for the Pulses-page overlay bar (customer ask 2026-08-27):
 * every pulse is time × voltage, so any set can share one plot. Pins:
 *   1. a CZ macro's companion pulse (server `overlays`, default_on) is DRAWN
 *      with the committed trace on first render, under its own label
 *   2. its chip is checked; unchecking removes the trace, re-checking restores
 *   3. the picker lists the library table's OTHER pulses (never its own path,
 *      never one already overlaid); picking one fetches /api/pulse/synth for
 *      that path and draws it; its × removes it
 *   4. a companion that failed to synth is labelled, never drawn as a line
 * Run: node tests/pulse_overlay_selfcheck.cjs   (driven by tests/test_pulse_overlay.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const OWN = 'qubit_pairs.q1-2.macros.cz_bipolar.flux_pulse_qubit';
const SIB = 'qubit_pairs.q1-2.macros.cz_bipolar.coupler_flux_pulse';
const OTHER = 'qubits.q1.xy.operations.x180_DragCosine';
const detail = {
    path: OWN, actual_path: OWN, qclass: 'CosineBipolarPulse',
    plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.1, 0] }] },
    overlays: [
        { path: SIB, label: 'coupler_flux_pulse', default_on: true,
          plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, -0.2, 0] }] } },
        { path: 'qubit_pairs.q1-2.macros.cz_bipolar.broken', label: 'broken', default_on: true,
          plot: { ok: false, error: 'boom' } },
    ],
};
const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<table><tr><td><input type="checkbox" class="pulse-sel-chk" data-path="' + OWN + '"></td></tr>' +
    '<tr><td><input type="checkbox" class="pulse-sel-chk" data-path="' + SIB + '"></td></tr>' +
    '<tr><td><input type="checkbox" class="pulse-sel-chk" data-path="' + OTHER + '"></td></tr></table>' +
    '<div id="pulse-detail-root" data-pulse-path="' + OWN + '">' +
    '<div class="pulse-plot-bar"><span class="pulse-synth-err" hidden></span><span class="pulse-dirty-pill" hidden></span></div>' +
    '<div class="pulse-overlay-bar"><span class="pulse-overlay-chips"></span>' +
    '<select class="pulse-overlay-pick" hidden><option value="">+ add pulse…</option></select></div>' +
    '<div id="pulse-detail-plot"></div>' +
    '<script id="pulse-detail-data" type="application/json">' + JSON.stringify(detail) + '</script>' +
    '</div></body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage;
// the bits of app.js pulses.js leans on: an immediate debounce + a recording plot sink
const rendered = [];
window._plotlyRender = function (id, data) { rendered.push({ id: id, names: data.map((t) => t.name) }); return null; };
global._plotlyRender = window._plotlyRender;
window._debounce = function (k, f) { f(); };
global._debounce = window._debounce;
const fetched = [];
window.fetch = function (url, opts) {
    fetched.push(JSON.parse(opts.body));
    return Promise.resolve({ json: () => Promise.resolve({ ok: true, plot: { ok: true, traces: [{ name: 'I', x: [0, 1], y: [0, 0.3] }, { name: 'Q', x: [0, 1], y: [0, 0.1] }] } }) });
};
global.fetch = window.fetch;

window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));

const d = window.document;
const root = d.getElementById('pulse-detail-root');
const last = () => rendered[rendered.length - 1].names;

window.PulsesPage.initDetail();
setTimeout(function () {
    // 1. companion drawn by default, under its label
    ok(rendered.length > 0, 'first render happened');
    ok(last().indexOf('coupler_flux_pulse') !== -1 && last().indexOf('I') !== -1,
       'the companion coupler pulse is drawn WITH the committed trace (' + last().join(', ') + ')');
    // 4. a failed companion is never a line
    ok(last().indexOf('broken') === -1, 'a companion that failed to synth is not drawn');
    const chips = root.querySelectorAll('.pulse-overlay-chip');
    ok(chips.length === 2, 'one chip per companion (' + chips.length + ')');
    ok(/no waveform/.test(chips[1].textContent), 'the failed companion says so on its chip');
    // 2. toggle off / on
    const cb = root.querySelector('input[data-overlay="' + SIB + '"]');
    ok(cb && cb.checked, 'the companion chip starts checked');
    cb.checked = false; cb.dispatchEvent(new window.Event('change', { bubbles: true }));
    ok(last().indexOf('coupler_flux_pulse') === -1, 'unchecking removes the companion trace');
    cb.checked = true; cb.dispatchEvent(new window.Event('change', { bubbles: true }));
    ok(last().indexOf('coupler_flux_pulse') !== -1, 're-checking restores it');
    // 3. picker: other pulses only
    const pick = root.querySelector('.pulse-overlay-pick');
    const vals = Array.prototype.map.call(pick.options, (o) => o.value).filter(Boolean);
    ok(!pick.hidden, 'the picker shows when the table lists other pulses');
    ok(vals.indexOf(OWN) === -1 && vals.indexOf(SIB) === -1 && vals.indexOf(OTHER) !== -1,
       'the picker offers only pulses not already on the plot (' + vals.join(',') + ')');
    ok(/q1 · xy\.x180_DragCosine/.test(pick.options[1].textContent), 'picker labels read owner · channel.op');
    pick.value = OTHER; pick.dispatchEvent(new window.Event('change', { bubbles: true }));
    setTimeout(function () {
        ok(fetched.length === 1 && fetched[0].path === OTHER, 'picking fetches /api/pulse/synth for THAT path');
        ok(last().indexOf('q1 · xy.x180_DragCosine I') !== -1 && last().indexOf('q1 · xy.x180_DragCosine Q') !== -1,
           'a picked two-quadrature pulse draws both traces under its label');
        const x = root.querySelector('.pulse-overlay-x');
        ok(!!x, 'a picked overlay carries a × (companions do not)');
        x.click();
        ok(last().indexOf('q1 · xy.x180_DragCosine I') === -1, '× removes the picked overlay');
        ok(root.querySelectorAll('.pulse-overlay-x').length === 0, 'and its chip');
        process.exit(fails ? 1 : 0);
    }, 20);
}, 30);
