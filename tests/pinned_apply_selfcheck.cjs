/* QA r2-07 — Pin & Browse: a pinned run's "Apply →" carries ITS OWN chip.
 *
 * togglePinDataset prefixes every id of the pinned clone with "pinned-", so in
 * the split the global #ds-detail-root is always the OTHER (current) column.
 * applyFitValue / applyAllFitValues / goToFitState / the Interactive-tab click
 * read that global root, so a pinned KRISS fit was sent with the current KH
 * run's chip token: no cross-chip confirm, and expect_chip let the server gate
 * pass too. Driven through the REAL app.js (the real _openPlotApplyPopup and
 * _renderPlotApplyPopup), with /chip/active-token naming the loaded chip "B".
 *
 * Run: node tests/pinned_apply_selfcheck.cjs  (driven by tests/test_pinned_apply.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

function detail(prefix, uid, token, name, exp, qubits) {
    return '<div id="' + prefix + 'ds-detail-root" data-uid="' + uid + '" data-experiment="' + exp +
        '" data-chip-token="' + token + '" data-chip-name="' + name + '" data-qubits="' + qubits + '">' +
        '<section class="detail-section">' +
        '<button class="fit-apply-btn" data-fit-path="qubits.qA1.resonator.f_01" data-fit-value="7125966604.37"' +
        ' data-fit-qubit="qA1">Apply →</button>' +
        '<button class="fit-apply-all">Apply all</button>' +
        '<button class="fit-goto-btn" data-fit-path="qubits.qA1.resonator.f_01">Go to state</button>' +
        '</section><div class="interactive-tile"></div></div>';
}
const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"><div class="inspector-split">' +
    '<div class="inspector-pinned-col">' + detail('pinned-', 'kriss:9', 'A', 'KRISS_CZ_260906', '03_res_spec', 'qZ9') + '</div>' +
    '<div class="inspector-current-col">' + detail('', 'kh:4113', 'B', 'KH_CHIP', '03_res_spec', 'qA1') + '</div>' +
    '</div></div>' +
    '<div id="plot-apply-popup" style="display:none"><h3 id="plot-apply-title"></h3>' +
    '<div id="plot-apply-context"></div><div id="plot-apply-verdict" hidden tabindex="-1"></div>' +
    '<div id="plot-apply-extra" hidden></div><div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

const fetched = [];
window.fetch = function (url) {
    url = String(url); fetched.push(url);
    if (url.indexOf('/chip/active-token') === 0) {
        return Promise.resolve({ ok: true, status: 200, json: function () {
            return Promise.resolve({ loaded: true, token: 'B', name: 'KH_CHIP', path: 'D:/chips/kh' }); } });
    }
    return new Promise(function () {});   // peek / verdict: never answer
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {},
                trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let confirms = [];
window.confirm = function (msg) { confirms.push(msg); return true; };
const toasts = [];
window.showToast = function (msg, level) { toasts.push(msg); };
const pop = document.getElementById('plot-apply-popup');
function reset() {
    confirms = []; fetched.length = 0; toasts.length = 0;
    pop.style.display = 'none';
    delete pop.dataset.expectChip; delete pop.dataset.forceChip; delete pop.dataset.runUid;
    if (pop._releaseTrap) { try { pop._releaseTrap(); } catch (e) {} pop._releaseTrap = null; }
}
const P = document.getElementById('pinned-ds-detail-root');
const C = document.getElementById('ds-detail-root');

(async function main() {
    /* 1. pinned column's per-row Apply → the pinned run's chip, confirm fires */
    reset();
    window.applyFitValue(P.querySelector('.fit-apply-btn'));
    await sleep(20);
    ok(confirms.length === 1 && confirms[0].indexOf('KRISS_CZ_260906') !== -1,
       'pinned Apply → the cross-chip confirm names the PINNED run\'s chip');
    ok(pop.dataset.expectChip === 'A', 'pinned Apply → the write carries the pinned run\'s token (A), not the other column\'s');
    ok(pop.dataset.forceChip === '1', 'the accepted confirm is what forces it (force_chip), never a silent pass');
    ok(pop.dataset.runUid === 'kriss:9', 'the popup knows WHICH run it applies (verdict badge audits the pinned run)');

    /* 2. current column is unchanged: same chip as loaded → no confirm, token B */
    reset();
    window.applyFitValue(C.querySelector('.fit-apply-btn'));
    await sleep(20);
    ok(confirms.length === 0, 'current-column Apply (same chip as loaded) → no confirm');
    ok(pop.dataset.expectChip === 'B', 'current-column Apply → its own token (B)');
    ok(pop.dataset.runUid === 'kh:4113', 'current-column popup → its own run uid');

    /* 3. Apply all mapped (section button) in the pinned column */
    reset();
    window.applyAllFitValues(P.querySelector('.fit-apply-all'));
    await sleep(20);
    ok(confirms.length === 1 && pop.dataset.expectChip === 'A',
       'pinned "Apply all" → confirm + the pinned run\'s token');

    /* 4. Go to state in the pinned column warns about the pinned run's chip */
    reset();
    window.goToFitState(P.querySelector('.fit-goto-btn'));
    await sleep(20);
    ok(toasts.length === 1 && toasts[0].indexOf('KRISS_CZ_260906') !== -1,
       'pinned "Go to state" → the A18 cross-chip warning names the pinned run\'s chip');

    /* 5. Interactive-tab click on a tile INSIDE the pinned column */
    reset();
    const tile = P.querySelector('.interactive-tile');
    tile.on = function (n, cb) { this._h = this._h || {}; this._h[n] = cb; };
    window._attachInteractivePlotClickHandler(tile, { axis: 'x', targets: [{ path: 'qubits.{q}.resonator.f_01' }] }, '9');
    tile._h['plotly_click']({ points: [{ x: 7.1e9, y: 0 }] });
    await sleep(20);
    ok(confirms.length === 1 && pop.dataset.expectChip === 'A',
       'pinned Interactive click → confirm + the pinned run\'s token');
    ok(/qubits\.qZ9\.resonator/.test(document.getElementById('plot-apply-rows').innerHTML),
       'the qubit fallback reads the pinned run\'s own qubit list');

    /* 6. verdict badge fetches for the popup's run, not the global root's */
    reset();
    window._renderPlotApplyPopup([{ dot_path: 'qubits.qA1.resonator.f_01', value: 1 }], 'e', 'qA1', [],
                                 { token: 'A', name: 'x' }, 'kriss:9');
    ok(fetched.some(function (u) { return u.indexOf('/fit-audit/verdict?uid=' + encodeURIComponent('kriss:9')) === 0; }),
       'the verdict badge audits the popup\'s run (kriss:9)');

    /* 7. an element outside any detail (and a fake with no .closest) keeps the global root */
    reset();
    const fake = { on: function (n, cb) { this._h = this._h || {}; this._h[n] = cb; } };
    window._attachInteractivePlotClickHandler(fake, { axis: 'x', qubit: 'qA1', targets: [{ path: 'qubits.{q}.f_01' }] }, 'r');
    fake._h['plotly_click']({ points: [{ x: 5, y: 0 }] });
    await sleep(20);
    ok(pop.dataset.expectChip === 'B', 'no .closest → falls back to the global #ds-detail-root');

    if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
    console.log('all ok');
    process.exit(0);
})().catch(function (e) { console.error('CRASH ' + (e && e.stack || e)); process.exit(1); });
