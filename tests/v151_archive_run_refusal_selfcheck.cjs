/* v1.5.1 SA-F5/F6: a run refused because its target is a dataset run archive
 * is SAID on the page -- the server's sentence, not a silent no-op or a code.
 *
 *  - Experiment Runner: Start answered {reason: "archive"} shows the message
 *    in a sticky toast and offers no "Start ANYWAY" (force cannot pass it);
 *  - Auto Calibrate: Run shows the server's message, not "archive_target".
 *
 * Run: node tests/v151_archive_run_refusal_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const MSG = 'The open chip is a dataset run\'s frozen quam_state (X), opened read-only. A run would rewrite that record.';

// ---- Experiment Runner ---------------------------------------------------------
(function runner() {
    const dom = new JSDOM('<!doctype html><html><body><div id="sched-root">' +
        '<input type="checkbox" id="sched-simulate" checked><input type="checkbox" id="sched-continue-ui" checked>' +
        '<button id="sched-start">Start</button><div id="sched-queue"></div></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:5050/scheduler' });
    const w = dom.window;
    const calls = [];
    const toasts = [];
    let confirms = 0;
    w.confirm = () => { confirms++; return true; };
    w.alert = (t) => toasts.push(['alert', t]);
    w.showToast = (t, level, ms) => toasts.push([t, level, ms]);
    w.fetch = function (url, o) {
        calls.push({ url: String(url), method: (o && o.method) || 'GET', body: (o && o.body) || '' });
        let body = {};
        if (String(url) === '/scheduler/status') {
            body = { queue: [{ id: 'a', name: 'n', enabled: true, status: 'queued' }], run: {} };
        } else if (String(url) === '/scheduler/start') {
            body = { ok: false, reason: 'archive', error: 'archive_target', message: MSG };
        }
        return w.Promise.resolve({ ok: true, status: 200, json: () => w.Promise.resolve(body) });
    };
    w.eval(fs.readFileSync(path.join(STATIC, 'scheduler.js'), 'utf8'));
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    return new Promise((res) => setTimeout(res, 60)).then(() => {
        calls.length = 0;
        w.document.getElementById('sched-start').click();
        return new Promise((res) => setTimeout(res, 60));
    }).then(() => {
        const starts = calls.filter((c) => c.url === '/scheduler/start');
        ok(starts.length === 1, 'Start posts once (got ' + JSON.stringify(calls.map((c) => c.url)) + ')');
        ok(toasts.some((t) => t[0] === MSG && t[1] === 'error' && t[2] === -1),
           'the archive refusal is a sticky error naming the reason: ' + JSON.stringify(toasts));
        ok(confirms === 0, 'no "Start ANYWAY" offer: force cannot pass it');
    });
})().then(function autofit() {
    // ---- Auto Calibrate --------------------------------------------------------
    const dom = new JSDOM('<!doctype html><html><body><div id="autofit-root">' +
        '<select id="autofit-preset"><option value="1q_bringup" selected>1q</option></select>' +
        '<select id="autofit-autonomy"><option value="full" selected>full</option></select>' +
        '<select id="autofit-backend"><option value="real" selected>real</option></select>' +
        '<span id="autofit-planbar-msg"></span></div></body></html>',
        { url: 'http://localhost/autofit', pretendToBeVisual: true });
    const { window } = dom;
    global.window = window; global.document = window.document;
    global.Event = window.Event; global.CustomEvent = window.CustomEvent;
    Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true, writable: true });
    global.location = window.location;
    global.fetch = function (url, opts) {
        const m = (opts && opts.method) || 'GET';
        const body = (m === 'POST' && url === '/autofit/start')
            ? { ok: false, error: 'archive_target', reason: 'archive', message: MSG } : {};
        return Promise.resolve({ status: m === 'POST' ? 409 : 200, json: () => Promise.resolve(body) });
    };
    window.fetch = global.fetch;
    vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'autofit.js'), 'utf8'), { filename: 'autofit.js' });
    window.autofitRun();
    return new Promise((res) => setTimeout(res, 40)).then(() => {
        const t = document.getElementById('autofit-planbar-msg').textContent;
        ok(t.indexOf('frozen quam_state') >= 0, 'Auto Calibrate shows the server\'s sentence: ' + t);
        ok(t.indexOf('archive_target') < 0, 'not the bare code');
    });
}).then(() => {
    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_archive_run_refusal_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
});
