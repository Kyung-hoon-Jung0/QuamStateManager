/* v1.5.1 SB-P2-1: Pull & apply with a "Keep mine" pick declares the live chip
 * its panel read.
 *
 * The panel named "Lost: the live chip's q1.T1 = A" from its read; the lab
 * wrote B a second later and the merge replaced B, unnamed. The real panel's
 * merge button now carries data-live-hash; the merge press sends it
 * (expect_live_hash) and a "live_moved" answer says so and re-reads the panel.
 *
 * The driver (tests/test_v151_merge_live_hash.py) renders the REAL /state/review
 * panel with a colliding field.
 *
 * Run: node tests/v151_merge_live_hash_selfcheck.cjs <fixture.json>
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
let answer = { status: 'live_moved', message: 'Nothing was written: the live chip changed after the panel showed its values' };
w.fetch = (url, opts) => {
    url = String(url);
    if (url.indexOf('/state/sync') === 0) {
        posts.push(String(opts && opts.body || ''));
        return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve(answer) });
    }
    if (url.indexOf('/state/review') === 0) {
        posts.push('GET ' + url);
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(FX.panel_html) });
    }
    return new Promise(() => {});
};
w.eval(APP);
const toasts = [];
w.showToast = (m, l) => { toasts.push(String(m)); };

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

(async function () {
    const d = w.document;
    const btn = d.getElementById('sp-merge');
    ok(!!btn && btn.getAttribute('data-live-hash') === FX.live_hash,
       'the rendered merge button carries the live chip the panel read');
    const mine = d.querySelector('input[data-pick-path][value="mine"]');
    ok(!!mine, 'the real panel offers Keep mine for the colliding field');
    mine.checked = true;
    w.SyncPanel.picked();
    ok(!btn.disabled, 'with every field picked the merge is pressable');
    w.SyncPanel.merge(btn);                 // arms
    w.SyncPanel.merge(btn);                 // confirms
    await tick();
    const sync = posts.find((p) => p.indexOf('mode=apply') >= 0);
    ok(!!sync, 'the merge posts /state/sync');
    ok(sync && sync.indexOf('expect_live_hash=' + encodeURIComponent(FX.live_hash)) >= 0,
       'and declares the live chip its panel read: ' + sync);
    ok(sync && /picks=/.test(sync), 'with its picks');
    await tick();
    ok(toasts.some((t) => /live chip changed after the panel/.test(t)), 'a live_moved answer is said');
    ok(posts.some((p) => p.indexOf('GET /state/review') === 0), 'and the panel re-reads');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
