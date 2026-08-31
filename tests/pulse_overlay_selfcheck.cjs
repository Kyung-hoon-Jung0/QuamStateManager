/* jsdom selfcheck for the Pulses-page VIEW bar (docs/141 4k, superseding the
 * 2026-08-27 overlay bar): every pulse is time × voltage, so any set can share
 * one plot. The server renders one section per pulse in view; the client draws
 * every section's committed traces in its own colour under its own label, and
 * the view bar drops/adds pulses by re-rendering the same route. Pins:
 *   1. every section in view is DRAWN with the main trace on first render,
 *      each under its own label (the legend suffix)
 *   2. a section that failed to synth is never drawn as a line
 *   3. × on a chip re-renders the view WITHOUT that pulse, keeping the main
 *   4. the picker lists the library table's OTHER pulses (never one already in
 *      view), labelled owner · channel.op; picking one re-renders the view
 *      WITH it appended
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
const BROKEN = 'qubit_pairs.q1-2.macros.cz_bipolar.broken';
const OTHER = 'qubits.q1.xy.operations.x180_DragCosine';
const LABEL_Q = 'q1-2 · cz_bipolar · qubit';
const LABEL_C = 'q1-2 · cz_bipolar · coupler';
const detail = {
    path: OWN, actual_path: OWN, qclass: 'CosineBipolarPulse', mode: 'group',
    plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.1, 0] }] },
    pulses: [
        { path: OWN, actual_path: OWN, label: LABEL_Q, role: 'qubit', color: '#1095c1', index: 0,
          plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.1, 0] }] } },
        { path: SIB, actual_path: SIB, label: LABEL_C, role: 'coupler', color: '#e67e22', index: 1,
          plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, -0.2, 0] }] } },
        { path: BROKEN, actual_path: BROKEN, label: 'q1-2 · cz_bipolar · broken', role: 'pulse', color: '#9b59b6', index: 2,
          plot: { ok: false, error: 'boom' } },
    ],
};
function chip(p, color) {
    return '<span class="pulse-overlay-chip on" style="--ov-hue: ' + color + '" title="' + p + '">' +
        '<button type="button" class="pulse-overlay-x" data-drop-path="' + p + '">×</button></span>';
}
const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<table>' + [OWN, SIB, BROKEN, OTHER].map((p) => '<tr><td><input type="checkbox" class="pulse-sel-chk" data-path="' + p + '"></td></tr>').join('') + '</table>' +
    '<div id="pulse-detail-root" data-pulse-path="' + OWN + '" data-actual-path="' + OWN + '">' +
    '<div class="pulse-plot-bar"><span class="pulse-dirty-pill" hidden></span><span class="pulse-synth-err" hidden></span></div>' +
    '<div class="pulse-overlay-bar pulse-view-bar" data-view-paths=\'' + JSON.stringify([OWN, SIB, BROKEN]) + '\' data-view-main="' + OWN + '">' +
    '<span class="pulse-overlay-chips">' + chip(OWN, '#1095c1') + chip(SIB, '#e67e22') + chip(BROKEN, '#9b59b6') + '</span>' +
    '<select class="pulse-overlay-pick" hidden></select></div>' +
    [OWN, SIB, BROKEN].map((p) => '<details open class="pulse-sec" data-pulse-path="' + p + '"><summary>s</summary>'
        + '<form class="inline-edit pulse-edit-form"><input type="hidden" name="dot_path" value="' + p + '.amplitude">'
        + '<input type="text" name="value" data-param="amplitude" data-kind="float" data-synth="1" data-committed="0.1" value="0.1"></form></details>').join('') +
    '<div id="pulse-detail-plot"></div>' +
    '<script id="pulse-detail-data" type="application/json">' + JSON.stringify(detail) + '</script>' +
    '</div></body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.HTMLElement = window.HTMLElement; global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.requestAnimationFrame = (f) => setTimeout(f, 0);
global.localStorage = window.localStorage;

const rendered = [];
window._plotlyRender = function (id, data) { rendered.push({ id: id, names: data.map((t) => t.name) }); return null; };
global._plotlyRender = window._plotlyRender;
window._debounce = function (k, f) { f(); };
global._debounce = window._debounce;
const ajax = [];
window.htmx = { ajax: function (method, url, opts) { ajax.push({ method: method, url: url, opts: opts }); return Promise.resolve(); } };
global.htmx = window.htmx;
const synthBodies = [];
window.fetch = function (url, opts) {
    const body = opts && opts.body ? JSON.parse(opts.body) : {};
    synthBodies.push(body);
    return Promise.resolve({ json: () => Promise.resolve({ ok: true, plot: { ok: true, traces: [{ name: 'I', x: [0, 1], y: [0, 0.5] }] } }) });
};
global.fetch = window.fetch;

window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));

const d = window.document;
const root = d.getElementById('pulse-detail-root');
const last = () => rendered[rendered.length - 1].names;
// docs/141 4l-review: paths= is REPEATED per pulse (a comma is legal inside a foreign op name)
const viewUrl = (main, paths) => '/pulse/detail?path=' + main + paths.map((p) => '&paths=' + p).join('');
const has = (names, needle) => names.some((n) => String(n).indexOf(needle) !== -1);

window.PulsesPage.initDetail();
setTimeout(function () {
    // 1. every section drawn, each under its own label
    ok(rendered.length > 0, 'first render happened');
    ok(has(last(), LABEL_Q) && has(last(), LABEL_C), 'both sections are drawn with their labels (' + last().join(' | ') + ')');
    // 2. a failed section is never a line
    ok(!has(last(), 'broken'), 'a section that failed to synth is not drawn');
    // 3. × drops a pulse, the main pulse survives
    const xs = root.querySelectorAll('.pulse-overlay-x[data-drop-path]');
    ok(xs.length === 3, 'one drop button per pulse in view (' + xs.length + ')');
    root.querySelector('.pulse-overlay-x[data-drop-path="' + SIB + '"]').click();
    ok(ajax.length === 1 && ajax[0].method === 'GET' && ajax[0].opts && ajax[0].opts.target === '#inspector-pane',
       'dropping re-renders the inspector from the server');
    const dropUrl = ajax.length ? decodeURIComponent(ajax[0].url) : '';
    ok(dropUrl === viewUrl(OWN, [OWN, BROKEN]),
       'the dropped pulse is gone from the view, the main pulse kept (' + dropUrl + ')');
    // dropping the MAIN pulse hands the view to the next one
    root.querySelector('.pulse-overlay-x[data-drop-path="' + OWN + '"]').click();
    const dropMain = ajax.length === 2 ? decodeURIComponent(ajax[1].url) : '';
    ok(dropMain === viewUrl(SIB, [SIB, BROKEN]),
       'dropping the main pulse promotes the next one (' + dropMain + ')');
    // 4. picker: other pulses only
    const pick = root.querySelector('.pulse-overlay-pick');
    const vals = Array.prototype.map.call(pick.options, (o) => o.value).filter(Boolean);
    ok(!pick.hidden, 'the picker shows when the table lists other pulses');
    ok(vals.length === 1 && vals[0] === OTHER,
       'the picker offers only pulses not already in view (' + vals.join(',') + ')');
    ok(/q1 · xy\.x180_DragCosine/.test(pick.options[1].textContent), 'picker labels read owner · channel.op');
    pick.value = OTHER; pick.dispatchEvent(new window.Event('change', { bubbles: true }));
    const addUrl = ajax.length === 3 ? decodeURIComponent(ajax[2].url) : '';
    ok(addUrl === viewUrl(OWN, [OWN, SIB, BROKEN, OTHER]),
       'picking appends the pulse to the view and re-renders (' + addUrl + ')');
    ok(pick.value === '', 'the picker resets after a pick');
    // 5. typing in section B previews B only (docs/141 4l-review pin): one
    //    synth for SIB, A's committed trace still drawn, B's preview dashed
    const nb = synthBodies.length;
    const inpB = root.querySelector('.pulse-sec[data-pulse-path="' + SIB + '"] input[data-param="amplitude"]');
    inpB.value = '0.5'; inpB.dispatchEvent(new window.Event('input', { bubbles: true }));
    setTimeout(function () {
        ok(synthBodies.length === nb + 1 && synthBodies[nb].path === SIB && synthBodies[nb].params && synthBodies[nb].params.amplitude === '0.5',
           'typing in a companion section synthesizes THAT pulse with its override (' + JSON.stringify(synthBodies[nb]) + ')');
        ok(has(last(), LABEL_Q) && !has(last(), LABEL_Q + ' (preview)'), "the main section's committed trace stays, with no preview of its own");
        ok(has(last(), LABEL_C + ' (preview)'), "the edited section carries a preview trace (" + last().join(' | ') + ')');
        console.log(fails ? 'FAILED ' + fails : 'all ok');
        process.exit(fails ? 1 : 0);
    }, 30);
}, 30);
