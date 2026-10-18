/* v1.5.1 U P1-6: a refused Apply's notice starts "Apply failed", with the OS
 * error on a details line.
 *
 * It read "Sync failed: Apply to live failed: the live folder refused the
 * write ... -- [Errno 13] Permission denied: '...state.json.tmp'". The driver
 * (tests/test_v151_refused_write_rows.py) makes a REAL failing /state/sync
 * apply and hands its JSON and the real tray markup over; this harness runs
 * app.js's doStateSync on that answer.
 *
 * Run: node tests/v151_refused_write_rows_selfcheck.cjs <fixture.json>
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
    + '<ul><li id="topbar-tray-slot">' + FX.tray_html + '</li></ul><div id="table-pane"></div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
w.fetch = (url) => {
    if (String(url).indexOf('/state/sync') === 0) {
        return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve(FX.answer) });
    }
    return new Promise(() => {});
};
w.eval(APP);

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

(async function () {
    w.doStateSync('apply');
    await tick();
    const toast = w.document.querySelector('#status-bar .toast');
    ok(!!toast, 'the refusal is on screen');
    const head = toast && toast.querySelector('p');
    const text = head ? head.textContent : '';
    ok(/^Apply failed:/.test(text), 'it starts "Apply failed": ' + text);
    ok(text.indexOf('Sync failed') < 0 && text.indexOf('Errno') < 0, 'no "Sync failed", no OS error in the head');
    const det = toast && toast.querySelector('.toast-detail');
    ok(!!det && /^Details: /.test(det.textContent) && det.textContent.length > 10,
       'the OS error is on its own details line: ' + (det && det.textContent));
    ok(toast && toast.classList.contains('toast-sticky'), 'and it stays until closed');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
