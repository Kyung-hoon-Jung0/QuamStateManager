/* docs/296 -- a click on a run saved before a qubit rename stages into the
 * LOADED chip's names, or refuses; it never writes the qubit that holds the
 * run's old name today.
 *
 * Driven through the REAL app.js (the Interactive tab's click handler) and
 * the REAL ndview.js (the Data tab's value chip): the server hands both a
 * `names` map ({run name: today's name or null}) only when the run predates
 * a rename; without it everything behaves as before.
 *
 * Run: node tests/rename_click_selfcheck.cjs  (driven by tests/test_rename_datasets.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="status-bar"></div>' +
    '<div id="ndv-root" data-uid="k:1" data-which="ds_raw.h5">' +
    '<div id="ndv-controls" hidden></div><div id="ndv-plot"></div>' +
    '<div id="ndv-fallback" hidden></div></div>' +
    '<div id="plot-apply-popup" style="display:none">' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

const fetched = [];
let fetchImpl = function () { return new Promise(function () {}); };
window.fetch = function (url, opts) { fetched.push(String(url)); return fetchImpl(String(url), opts); };
function jsonResponse(obj) {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(obj); } });
}
window.Plotly = {
    newPlot: function (el) { el.on = function (n, cb) { el._h = el._h || {}; el._h[n] = cb; };
                             return Promise.resolve(el); },
    react: function (el) { return window.Plotly.newPlot(el); },
    purge: function () {},
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {},
                trigger: function () {}, process: function () {} };
const staticDir = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
for (const f of ['app.js', 'plot-theme.js', 'ndview.js']) {
    window.eval(fs.readFileSync(path.join(staticDir, f), 'utf8'));
}

let popups = [];
window._openPlotApplyPopup = function (updates, expName, q) { popups.push({ updates: updates, q: q }); };
function toasts() {
    return Array.prototype.map.call(document.querySelectorAll('#status-bar .toast'),
                                    function (t) { return t.textContent; });
}
function clickOn(clickable, x) {
    const div = document.createElement('div');
    div.on = function (name, cb) { div._cb = cb; };
    document.body.appendChild(div);
    window._attachInteractivePlotClickHandler(div, clickable, 'k:1');
    if (div._cb) div._cb({ points: [{ x: x, y: 0 }] });
    return !!div._cb;
}

(async function main() {
    /* 1. Interactive: a {q} path is filled with TODAY's name */
    popups = [];
    clickOn({ axis: 'x', qubit: 'q1', targets: [{ path: 'qubits.{q}.f_01', scale: 1e9 }],
              names: { q1: 'q0', q2: 'q1' }, refusals: {}, refused: null }, 5.1);
    ok(popups.length === 1 && popups[0].updates[0].dot_path === 'qubits.q0.f_01',
       'an old run\'s {q} target is staged on today\'s name: ' +
       (popups[0] && popups[0].updates[0].dot_path));
    ok(popups.length === 1 && popups[0].q === 'q1',
       'the popup still names the RUN\'s qubit (its fit audit is keyed by it)');

    /* 2. a name with none today is refused, nothing staged */
    popups = [];
    clickOn({ axis: 'x', qubit: 'q3', targets: [{ path: 'qubits.{q}.f_01', scale: 1e9 }],
              names: { q3: null }, refusals: { q3: 'Not applied: q3 of this run has no qubit' },
              refused: null }, 5.1);
    ok(popups.length === 0, 'no popup for a qubit with no name today');
    ok(toasts().some(function (t) { return t.indexOf('Not applied: q3 of this run') !== -1; }),
       'the refusal is said: ' + JSON.stringify(toasts()));

    /* 3. a refused contract (no targets) still answers the click */
    popups = [];
    const bound = clickOn({ axis: 'x', qubit: 'q3', targets: [], names: { q3: null },
                            refusals: {}, refused: 'Not applied: whole contract refused' }, 1);
    ok(bound, 'a refused contract keeps its click handler');
    ok(popups.length === 0 && toasts().some(function (t) {
        return t.indexOf('whole contract refused') !== -1; }), 'and says the refusal');

    /* 4. no names map (a chip never renamed / today's era): as before */
    popups = [];
    clickOn({ axis: 'x', qubit: 'q1', targets: [{ path: 'qubits.{q}.f_01', scale: 1e9 }] }, 5.1);
    ok(popups.length === 1 && popups[0].updates[0].dot_path === 'qubits.q1.f_01',
       'without a names map the run\'s name is used as is');

    /* 5. Data tab value chip */
    const root = document.getElementById('ndv-root');
    const card = document.createElement('button');
    card.className = 'ndv-var-card'; card.setAttribute('data-var', 'S');
    root.appendChild(card);
    const cube = {
        ok: true, var: 'S', dtype: 'float64', units: null, long_name: null,
        dims: [
            { name: 'qubit', size: 2, kind: 'entity', coord: ['q1', 'q2'], units: null, decimated: false },
            { name: 'full_freq', size: 3, kind: 'sweep', coord: [5e9, 5.1e9, 5.2e9], units: 'Hz', decimated: false },
        ],
        data: [[1, 2, 3], [4, 5, 6]], kept: null, aux_axes: [], iq_partner: null,
        default_view: { x: 'full_freq', y: null, entity: 'qubit', overlay: [], sliders: {} },
        click: {
            candidates: [{ axis: 'x', dim: 'full_freq', path: 'qubits.{q}.f_01', label: 'Qubit f_01', tier: 'node' }],
            experiment: '08_qubit_spectroscopy',
            names: { q1: 'q0', q2: 'q1' }, refusals: {}, refused: 'x',
        },
    };
    fetchImpl = function () { return jsonResponse(cube); };
    window.NdView.mount();
    await sleep(30);
    let peekUrl = null;
    fetchImpl = function (url) {
        if (url.indexOf('/field/peek') !== -1) {
            peekUrl = url;
            return jsonResponse({ ok: true, values: { 'qubits.q0.f_01': 5.0e9 }, errors: {} });
        }
        return jsonResponse({});
    };
    window.NdViewChip.show({ clientX: 5, clientY: 5 }, [['f', 5.1e9, 'Hz']], cube, { x: 5.1e9, y: null });
    await sleep(30);
    ok(peekUrl && peekUrl.indexOf(encodeURIComponent('qubits.q0.f_01')) !== -1,
       'the value chip peeks today\'s name for the run\'s q1: ' + peekUrl);
    const chip = document.getElementById('ndv-value-chip');
    const stage = chip.querySelector('.ndv-cand-stage');
    ok(stage && stage.getAttribute('data-path') === 'qubits.q0.f_01', 'and stages it');
    ok(chip.textContent.indexOf('now q0') !== -1, 'the chip says the run\'s q1 is q0 now');

    /* 6. an entity with no name today: refused, nothing peeked or staged */
    peekUrl = null;
    const refusedCube = JSON.parse(JSON.stringify(cube));
    refusedCube.click.names = { q1: null, q2: 'q1' };
    refusedCube.click.refusals = { q1: 'Not applied: q1 of this run has no qubit on the loaded chip' };
    window.NdViewChip.show({ clientX: 5, clientY: 5 }, [['f', 5.1e9, 'Hz']], refusedCube, { x: 5.1e9, y: null });
    await sleep(30);
    const chip2 = document.getElementById('ndv-value-chip');
    ok(peekUrl === null, 'no /field/peek for a refused entity');
    ok(!chip2.querySelector('.ndv-cand-stage'), 'no Stage button');
    ok(!chip2.querySelector('.ndv-chip-explorer'), 'no Explorer jump to a wrong qubit');
    ok(chip2.textContent.indexOf('Not applied: q1 of this run') !== -1, 'the refusal is said');

    /* 7. no names map: today's behaviour */
    peekUrl = null;
    const plainCube = JSON.parse(JSON.stringify(cube));
    delete plainCube.click.names; delete plainCube.click.refusals; delete plainCube.click.refused;
    window.NdViewChip.show({ clientX: 5, clientY: 5 }, [['f', 5.1e9, 'Hz']], plainCube, { x: 5.1e9, y: null });
    await sleep(30);
    ok(peekUrl && peekUrl.indexOf(encodeURIComponent('qubits.q1.f_01')) !== -1,
       'without a names map the run\'s name is peeked as is: ' + peekUrl);

    if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
    console.log('all ok');
    process.exit(0);
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
