/* v1.5.1 R4 a1: the sync panel's merge and Apply presses declare the live
 * values the panel read at the fields they write (expect_live_values), next
 * to the live chip's hash. The server refuses only when one of THOSE moved.
 *
 * The REAL panel (/state/review) under jsdom: "Apply to live" (an unapplied
 * edit, live unchanged) through its own inline onclick, and the merge (live
 * changed another field) through SyncPanel.merge.
 *
 * Run: node tests/v151_r4_merge_live_values_selfcheck.cjs <fixture.json>
 *      (fixture written by tests/test_v151_r4_merge_live_values.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function world(panelHtml) {
    const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
        + '<div id="state-review-overlay" class="sync-panel-overlay" style="display:block">'
        + '<div id="state-review-host">' + panelHtml + '</div></div></body></html>',
        { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    const bodies = [];
    w.fetch = (url, init) => {
        if (String(url).indexOf('/state/sync') === 0) bodies.push(String((init && init.body) || ''));
        return new Promise(() => {});
    };
    w.eval(APP);
    w.showToast = () => {};
    return { w, d: w.document, bodies };
}
function field(body, name) {
    const m = new RegExp('(?:^|&)' + name + '=([^&]*)').exec(body);
    return m ? decodeURIComponent(m[1]) : null;
}

(function () {
    // Apply to live: the inline onclick, as the browser runs it
    {
        const t = world(FX.mine);
        const btn = [...t.d.querySelectorAll('button.sp-choice-primary')]
            .find((b) => /Apply to live/.test(b.textContent) && /doStateSync\('apply'/.test(b.getAttribute('onclick') || ''));
        ok(!!btn, 'the panel renders Apply to live');
        const declared = btn && btn.getAttribute('data-live-values');
        ok(!!declared && JSON.parse(declared)['qubits.q1.T1'], 'it carries the live value at the edited field');
        new t.w.Function('event', btn.getAttribute('onclick')).call(btn, null);
        const b = t.bodies[0] || '';
        ok(field(b, 'expect_live_values') === declared, 'Apply to live declares those values: ' + b.slice(0, 300));
        ok(!!field(b, 'expect_live_hash'), 'next to the live hash');
    }
    // the merge (no same-field collision: one press)
    {
        const t = world(FX.both);
        const btn = t.d.querySelector('#sp-merge');
        ok(!!btn, 'the panel renders the merge');
        const declared = btn && btn.getAttribute('data-live-values');
        ok(!!declared && JSON.parse(declared)['qubits.q1.T1'], 'it carries the live value at the edited field');
        t.w.SyncPanel.merge(btn);
        const b = t.bodies[0] || '';
        ok(field(b, 'expect_live_values') === declared, 'the merge declares those values: ' + b.slice(0, 300));
        ok(field(b, 'check_collisions') === '1' && !!field(b, 'expect_live_hash'),
           'with the same-field check and the live hash (R3 M4)');
    }
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})();
