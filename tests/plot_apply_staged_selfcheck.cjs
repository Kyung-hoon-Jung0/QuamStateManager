/* P1-3 (UX round) -- the plot-apply popup says what a press did to the chip.
 *
 * A press STAGES the value in SM; the chip file changes only on Apply (top
 * bar) or, at once, inside an armed Auto-Sync push session. The popup used to
 * say "applied" for a value that was only staged (the top bar read
 * "1 unapplied", state.json unchanged). The REAL app.js under jsdom:
 *   - not armed: the row reads "staged - press Apply to write the chip", never
 *     "applied", and the closing toast says "Staged ... press Apply";
 *   - armed: the row reads "applied" and the toast says Auto-Sync writes it.
 *
 * Run: node tests/plot_apply_staged_selfcheck.cjs
 * (driven by tests/test_v151_ux_plot_apply_staged.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="pending-tray" data-change-count="0"></div>' +
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

const P1 = 'qubits.q1.xy.operations.x180.amplitude';
const P2 = 'qubits.q1.f_01';
let peek = null;
window.fetch = function (url, opts) {
    url = String(url);
    if (opts && opts.method === 'POST') {
        return Promise.resolve({ ok: true, status: 200, json: function () {
            return Promise.resolve({ ok: true, stored: 0.2, stored_kind: 'float' }); } });
    }
    let body = {};
    if (url.indexOf('/chip/active-token') === 0) body = { loaded: true, token: 'tokA', name: 'chip', path: '/c/chip' };
    else if (url.indexOf('/field/peek') === 0) body = peek;
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const toasts = [];
window.showToast = function (msg) { toasts.push(String(msg)); };
let armed = false;
window.AutoApply = { armed: function () { return armed; } };

function row(p) { return document.querySelector('#plot-apply-rows .plot-apply-row[data-input-path="' + p + '"]'); }

async function openTwo() {
    peek = { ok: true, values: { [P1]: 0.1, [P2]: 5e9 }, errors: {},
             resolved: { [P1]: { is_pointer: false, resolvable: true, candidates: [] },
                         [P2]: { is_pointer: false, resolvable: true, candidates: [] } } };
    window._openPlotApplyPopup([{ dot_path: P1, value: 0.2 }, { dot_path: P2, value: 5.1e9 }],
                               'power_rabi', 'q1', [], null);
    await sleep(60);
}

(async function main() {
    // 1. Auto-Sync push not armed: the press only staged the value
    armed = false;
    await openTwo();
    const r1 = row(P1);
    ok(!!r1, 'popup rendered the row');
    window.applyPlotRow(r1);
    await sleep(40);
    const t1 = (r1.querySelector('.plot-apply-row-action') || {}).textContent || '';
    ok(/staged/.test(t1) && /press Apply to write the chip/.test(t1), 'a staged row says so: ' + t1);
    ok(!/applied/.test(t1), 'a staged row never says "applied": ' + t1);
    window.applyPlotRow(row(P2));
    await sleep(40);
    const last = toasts[toasts.length - 1] || '';
    ok(/^Staged 2 values/.test(last) && /press Apply/.test(last), 'the closing toast says staged: ' + last);
    ok(!/^Applied/.test(last), 'the closing toast never says "Applied" for staged values');

    // 2. Auto-Sync push armed: the session writes the chip at once
    armed = true;
    toasts.length = 0;
    await openTwo();
    const r2 = row(P1);
    window.applyPlotRow(r2);
    await sleep(40);
    const t2 = (r2.querySelector('.plot-apply-row-action') || {}).textContent || '';
    ok(/✓ applied/.test(t2) && !/staged/.test(t2), 'inside an armed session the row says applied: ' + t2);
    window.applyPlotRow(row(P2));
    await sleep(40);
    const last2 = toasts[toasts.length - 1] || '';
    ok(/^Applied 2 values/.test(last2) && /Auto-Sync writes them to the chip/.test(last2),
       'the armed toast names Auto-Sync: ' + last2);

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
