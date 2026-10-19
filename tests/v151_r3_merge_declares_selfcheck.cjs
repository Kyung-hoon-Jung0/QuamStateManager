/* v1.5.1 R3 M4: the sync panel's merge with NO pick sends the same-field
 * check and the live chip it read.
 *
 * The driver (tests/test_v151_r3_merge_declares.py) renders the REAL
 * /state/review panel with a lab change on another field (no collision).
 *
 * Run: node tests/v151_r3_merge_declares_selfcheck.cjs <fixture.json>
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

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<div id="state-review-overlay"><div id="state-review-host">' + FX.panel_html + '</div></div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
const posts = [];
w.fetch = (url, opts) => {
    url = String(url);
    if (url.indexOf('/state/sync') === 0) {
        posts.push(String(opts && opts.body || ''));
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'ok' }) });
    }
    return new Promise(() => {});
};
w.eval(APP);
w.showToast = () => {};

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

(async function () {
    const d = w.document;
    const btn = d.getElementById('sp-merge');
    ok(!!btn && !btn.disabled && !btn.getAttribute('data-needs-picks'),
       'the rendered merge needs no pick (no collision shown)');
    w.SyncPanel.merge(btn);
    await tick();
    const sync = posts.find((p) => p.indexOf('mode=apply') >= 0);
    ok(!!sync, 'the merge posts /state/sync');
    ok(sync && /(^|&)check_collisions=1(&|$)/.test(sync), 'with the same-field check: ' + sync);
    ok(sync && sync.indexOf('expect_live_hash=' + encodeURIComponent(FX.live_hash)) >= 0,
       'and the live chip its panel read: ' + sync);
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
