/* QA F5 — the plot-apply popup must never offer Apply for a field the LOADED
 * chip does not have. Another chip's run (qA1 on a chip with no qA1) used to
 * list its rows with PREVIOUS "(not set)" and a live Apply / Apply All, and a
 * press answered with the raw KeyError text "Key 'qA1' not found while
 * navigating ...". The REAL app.js under jsdom: /field/peek reports the path
 * missing -> the row says so plainly, its Apply is disabled, pressing it (or
 * Enter in its input) posts nothing, and Apply All is disabled. A path that
 * only resolves THROUGH a pointer (resolvable) keeps its Apply, and a fresh
 * popup never inherits the previous one's disabled Apply All.
 *
 * Run: node tests/plot_apply_missing_selfcheck.cjs
 * (driven by tests/test_plot_apply_missing_field.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="plot-apply-popup" style="display:none">' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<div id="plot-apply-verdict" hidden></div>' +
    '<div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

const posts = [];
let peek = null;
window.fetch = function (url, opts) {
    url = String(url);
    if (opts && opts.method === 'POST') {
        posts.push(url);
        return Promise.resolve({ ok: false, status: 400, json: function () {
            return Promise.resolve({ ok: false, error: "Key 'qA1' not found while navigating 'x'" }); } });
    }
    let body = {};
    if (url.indexOf('/chip/active-token') === 0) body = { loaded: true, token: 'tokA', name: 'LabA', path: '/c/LabA' };
    else if (url.indexOf('/field/peek') === 0) body = peek;
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const MISS_F = 'qubits.qA1.resonator.f_01';
const MISS_RF = 'qubits.qA1.resonator.RF_frequency';
const VIA_PTR = 'qubits.q1.xy.operations.x90.amplitude';
function row(p) { return document.querySelector('#plot-apply-rows .plot-apply-row[data-input-path="' + p + '"]'); }
const allBtn = document.getElementById('plot-apply-all');

(async function main() {
    // 1. both rows missing on the loaded chip (the reported case)
    peek = { ok: true, values: { [MISS_F]: null, [MISS_RF]: null },
             errors: { [MISS_F]: "Key 'qA1' not found", [MISS_RF]: "Key 'qA1' not found" },
             resolved: { [MISS_F]: { is_pointer: false, resolvable: false, candidates: [] },
                         [MISS_RF]: { is_pointer: false, resolvable: false, candidates: [] } } };
    window._openPlotApplyPopup([{ dot_path: MISS_F, value: 7.1e9 }, { dot_path: MISS_RF, value: 7.2e9 }],
                               '03_resonator_spectroscopy_single', 'qA1', [], null);
    await sleep(60);
    const r1 = row(MISS_F);
    ok(!!r1, 'popup rendered the qA1 row');
    const b1 = r1 && r1.querySelector('.plot-apply-row-btn');
    ok(b1 && b1.disabled, 'a field missing on the loaded chip has its Apply DISABLED');
    const err1 = r1 && r1.querySelector('.plot-apply-row-error');
    ok(err1 && !err1.hidden && /Not on the loaded chip/.test(err1.textContent) && err1.textContent.indexOf(MISS_F) !== -1,
       'the row says plainly the field is not on the loaded chip: ' + (err1 && err1.textContent));
    ok(allBtn.disabled, 'Apply All is disabled while any row is missing');
    ok(/not on the loaded chip/.test(allBtn.title || ''), 'Apply All says why: ' + allBtn.title);
    window.applyPlotRow(r1);
    const inp = r1.querySelector('.plot-apply-new-input');
    inp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    window.applyAllPlotRows();
    await sleep(30);
    ok(posts.length === 0, 'pressing Apply / Enter / Apply All on a missing field posts NOTHING: ' + JSON.stringify(posts));
    ok(!/Key 'qA1'/.test(err1.textContent), 'no raw KeyError text is ever shown');
    window.closePlotApplyPopup();

    // 2. a path that only resolves THROUGH a pointer keeps Apply (get_value
    //    fails, the resolver reaches a real literal) -- no over-gating
    peek = { ok: true, values: { [VIA_PTR]: null }, errors: { [VIA_PTR]: 'cannot traverse str' },
             resolved: { [VIA_PTR]: { is_pointer: false, resolvable: true, resolved_value: 0.1,
                                      resolved_path: 'qubits.q1.xy.operations.x180.amplitude', candidates: [] } } };
    window._openPlotApplyPopup([{ dot_path: VIA_PTR, value: 0.2 }, { dot_path: 'qubits.q1.f_01', value: 5e9 }],
                               'exp', 'q1', [], null);
    await sleep(60);
    const r2 = row(VIA_PTR);
    ok(r2 && !r2.querySelector('.plot-apply-row-btn').disabled && !r2.classList.contains('plot-apply-missing'),
       'a pointer-resolvable path keeps its Apply');
    ok(!allBtn.disabled, 'a fresh popup does not inherit the previous popup\'s disabled Apply All');
    window.closePlotApplyPopup();

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
