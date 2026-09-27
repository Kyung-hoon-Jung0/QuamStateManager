// w7 fq-sync P2: a live write that answered before the chip's lint was ready
// carries a content token; the page fetches the crash-value advisory for THAT
// content and shows it -- and shows nothing when the server says it is stale.
//
// Pinned against the REAL window.CrashAdvisory + the `crashPending` listener
// in app.js (an htmx apply-to-live answer), under jsdom:
//   C1 the follow-up asks /state/crash-values for exactly the token it got
//   C2 an advisory for that content is shown, as a warning, with its sentence
//   C3 a "stale" answer shows nothing (never another content's values)
//   C4 "no crash values" shows nothing
//   C5 no token, no request
//
// Run: node tests/crash_advisory_follow_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function world(answer) {
    const dom = new JSDOM('<!doctype html><html><head></head><body><div id="status-bar"></div>'
        + '<div id="pending-tray" data-change-count="0"></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {}, process: function () {}, on: function () {}, config: {} };
    const asked = [];
    w.fetch = function (url) {
        asked.push(String(url));
        const body = String(url).indexOf('/state/crash-values') >= 0 ? answer : {};
        return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); },
                                 text: function () { return Promise.resolve(''); } });
    };
    w.eval(APP);
    const toasts = [];
    w.showToast = function (m, l) { toasts.push({ m: String(m), l: l }); };
    return { w: w, toasts: toasts, asked: asked };
}
function pending(w, detail) {
    w.document.body.dispatchEvent(new w.CustomEvent('crashPending', { bubbles: true, detail: detail }));
}

(async function () {
{
    const S = '1 error on the live chip would crash a node run: q1.readout.amplitude.';
    const { w, toasts, asked } = world({ stale: false, crash_values: { sentence: S, sig: 'q1' } });
    pending(w, { seq: '7:42' });
    await tick();
    const q = asked.filter((u) => u.indexOf('/state/crash-values') >= 0);
    ok(q.length === 1 && /[?&]seq=7%3A42$/.test(q[0]), 'C1 the follow-up asks for exactly its token (got ' + JSON.stringify(q) + ')');
    ok(toasts.length === 1 && toasts[0].l === 'warning' && toasts[0].m.indexOf(S) >= 0,
       'C2 the advisory is named, as a warning (got ' + JSON.stringify(toasts) + ')');
}
{
    const { w, toasts } = world({ stale: true });
    pending(w, { seq: '7:43' });
    await tick();
    ok(toasts.length === 0, 'C3 a stale answer shows nothing (got ' + JSON.stringify(toasts) + ')');
}
{
    const { w, toasts } = world({ stale: false, crash_values: null });
    pending(w, { seq: '7:44' });
    await tick();
    ok(toasts.length === 0, 'C4 a clean chip shows nothing (got ' + JSON.stringify(toasts) + ')');
}
{
    const { w, asked } = world({ stale: false, crash_values: { sentence: 'x' } });
    pending(w, {});
    w.CrashAdvisory.follow(null, function () {});
    await tick();
    ok(asked.filter((u) => u.indexOf('/state/crash-values') >= 0).length === 0, 'C5 no token, no request');
}
if (fails) { console.error(fails + ' of ' + asserts + ' failed'); process.exit(1); }
console.log('all checks passed (' + asserts + ' assertions)');
process.exit(0);          // app.js keeps timers alive
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
