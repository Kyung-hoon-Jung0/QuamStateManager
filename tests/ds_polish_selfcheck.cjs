/* Datasets readability polish, under jsdom against the REAL app.js:
 *   - the plot-click popup's context line has no dangling text separator
 *     (its flex gap separates the items);
 *   - a plot click that opens the apply popup shows no "Copied" toast over it
 *     (both click paths), while a click that opens no popup still does;
 *   - the same notice shown again while it is up collapses into one notice
 *     with a repeat count instead of stacking copies.
 *
 * Run: node tests/ds_polish_selfcheck.cjs (driven by tests/test_ds_popup_units.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="status-bar"></div>' +
    '<div id="ds-detail-root" data-experiment="time_of_flight"></div>' +
    '<div id="plot-apply-popup" style="display:none">' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<div id="plot-apply-point"></div><div id="plot-apply-verdict" hidden></div>' +
    '<div id="plot-apply-rows"></div><button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

window.fetch = function (url) {
    if (String(url).indexOf('/chip/active-token') === 0) return Promise.resolve({ ok: true, status: 200,
        json: function () { return Promise.resolve({ loaded: true, token: 't', name: 'c', path: '/c' }); } });
    return Promise.resolve({ ok: true, status: 200, headers: { get: function () { return null; } },
        json: function () { return Promise.resolve({ ok: true, values: {}, resolved: {}, units: {} }); },
        text: function () { return Promise.resolve(''); } });
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
const bar = document.getElementById('status-bar');
const toasts = function () { return Array.prototype.filter.call(bar.children, function (t) { return t.classList.contains('toast'); }); };

(async function main() {
    // 1. the context line
    window._openPlotApplyPopup([{ dot_path: 'qubits.q2.xy.operations.x180.amplitude', value: '0.2' }], 'power_rabi', 'q2', [], null);
    await sleep(120);
    const ctx = document.getElementById('plot-apply-context');
    ok(/Experiment:/.test(ctx.textContent) && /Qubit:/.test(ctx.textContent), 'the context names the experiment and qubit');
    ok(ctx.textContent.indexOf('·') === -1, 'no text separator is left to dangle: ' + JSON.stringify(ctx.textContent));
    window._closePlotApplyPopup && window._closePlotApplyPopup();

    // 2a. the interactive (contract) click path opens the popup: no toast
    bar.innerHTML = '';
    let opened = 0;
    const realOpen = window._openPlotApplyPopup;
    window._openPlotApplyPopup = function () { opened++; };
    window._dsRootFor = function () { return document.getElementById('ds-detail-root'); };
    const handlers = {};
    const plot = { on: function (n, f) { handlers[n] = f; }, removeAllListeners: function () {} };
    window._attachInteractivePlotClickHandler(plot, { axis: 'x', qubit: 'q2',
        targets: [{ path: 'qubits.{q}.xy.operations.x180.amplitude', axis: 'x' }] }, 1);
    handlers.plotly_click({ points: [{ x: 0.25, y: 1 }] });
    await sleep(20);
    ok(opened === 1, 'the contract click opens the popup');
    ok(toasts().length === 0, 'and shows no toast over it (' + toasts().map(function (t) { return t.textContent; }).join('|') + ')');

    // 2b. the experiment-map click path: a popup means no toast; no popup keeps the toast
    window._showPlotApplyPopup = function () { opened++; };
    window._navigateToExplorerPath = function () {};
    const h2 = {};
    window._attachPlotClickHandler({ on: function (n, f) { h2[n] = f; }, removeAllListeners: function () {} });
    bar.innerHTML = ''; opened = 0;
    h2.plotly_click({ points: [{ x: 0.3, y: 1, customdata: 'q2' }] });
    await sleep(20);
    ok(opened === 1 && toasts().length === 0, 'a mapped click opens the popup with no toast (opened ' + opened + ', toasts ' + toasts().length + ')');
    document.getElementById('ds-detail-root').setAttribute('data-experiment', 'zz_nothing_maps_here_zz');
    bar.innerHTML = ''; opened = 0;
    h2.plotly_click({ points: [{ x: 0.3, y: 1, customdata: 'q2' }] });
    await sleep(20);
    ok(opened === 0 && toasts().length === 1 && /Copied: x=0\.3/.test(toasts()[0].textContent),
       'a click that opens no popup still says what was copied');
    window._openPlotApplyPopup = realOpen;

    // 3. repeated identical notices collapse
    bar.innerHTML = '';
    const M = "Couldn't reach the app — is it still running? Please retry.";
    const a = window.showToast(M, 'error');
    const b = window.showToast(M, 'error');
    window.showToast(M, 'error'); window.showToast(M, 'error');
    ok(toasts().length === 1 && a === b, 'four identical notices are one notice (' + toasts().length + ')');
    ok(/\(×4\)/.test(toasts()[0].textContent), 'which counts the repeats: ' + toasts()[0].textContent);
    window.showToast('Something else', 'error');
    window.showToast(M, 'warning');
    ok(toasts().length === 3, 'a different message or level is its own notice (' + toasts().length + ')');

    if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
    console.log('ALL OK');
    process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
