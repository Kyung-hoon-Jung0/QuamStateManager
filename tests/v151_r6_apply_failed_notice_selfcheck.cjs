/* v1.5.1 (r6 P2-6): the red "Apply failed" notice goes once a later live
 * write lands, and stays while none has.
 *
 * It stayed after the live folder was writable again and a later Apply had
 * landed, and followed every page after that. The REAL app.js under jsdom:
 * doStateSync, showToast, the tray swap and the htmx error path. Every
 * server answer replayed here is a REAL answer captured by
 * tests/test_v151_r6_apply_failed_notice.py; the fake htmx only replays an
 * htmx swap's events (an out-of-band #status-bar, then the tray).
 *
 * Run: node tests/v151_r6_apply_failed_notice_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<span id="topbar-tray-slot">' + FX.tray0 + '</span>'
    + '<div id="table-pane"><p>Qubits</p></div><div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/qubits', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const syncAnswers = [];
w.fetch = (url) => {
    const u = String(url);
    if (u.indexOf('/state/sync') === 0) {
        const a = syncAnswers.shift();
        if (!a) throw new Error('no /state/sync answer queued');
        return Promise.resolve({ status: a.status, ok: a.status < 400, json: () => Promise.resolve(a.body),
                                 text: () => Promise.resolve(JSON.stringify(a.body)) });
    }
    return new Promise(() => {});
};
w.htmx = { ajax: () => Promise.resolve(), process() {}, trigger() {}, on() {}, config: {} };
w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.confirm = () => false;
w.eval(APP);

const failNotes = () => Array.from(d.querySelectorAll('#status-bar .toast[data-live-write-failed]'));
async function until(cond, ms) {
    for (let t = 0; t < (ms || 2000); t += 20) { if (cond()) return true; await sleep(20); }
    return cond();
}
function htmxSwap(body) {
    // htmx's order: the out-of-band #status-bar, then the target (#pending-tray)
    const tmp = d.createElement('div');
    tmp.innerHTML = body;
    const oob = tmp.querySelector('#status-bar[hx-swap-oob]');
    const bar = d.getElementById('status-bar');
    if (oob) {
        bar.innerHTML = oob.innerHTML;
        bar.dispatchEvent(new w.CustomEvent('htmx:oobAfterSwap', { bubbles: true, detail: { target: bar } }));
    }
    const tray = tmp.querySelector('#pending-tray');
    if (tray) {
        d.getElementById('pending-tray').outerHTML = tray.outerHTML;
        const nt = d.getElementById('pending-tray');
        nt.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: nt } }));
    }
}

(async function () {
    const landed0 = String(FX.fail.live_landed);
    ok(d.getElementById('pending-tray').getAttribute('data-live-landed') === landed0,
       'the page shows a chip whose last live write landed at ' + landed0);

    // 1. Apply (the JSON door) is refused: the notice stays until closed
    syncAnswers.push({ status: 409, body: FX.fail });
    w.doStateSync('apply');
    await until(() => failNotes().length === 1);
    let notes = failNotes();
    ok(notes.length === 1 && /Apply failed/.test(notes[0].textContent), 'the refusal shows its red notice');
    ok(notes.length === 1 && notes[0].getAttribute('data-live-write-failed') === landed0,
       'the notice carries when a write last landed, as of the failure: '
       + (notes[0] && notes[0].getAttribute('data-live-write-failed')));
    await until(() => !w._applyInFlight);

    // 2. nothing lands: a tray re-read and a page change keep it
    w._swapPendingTray(FX.tray_nothing);
    await sleep(30);
    ok(failNotes().length === 1, 'a tray re-read with nothing landed keeps the notice');
    const pane = d.getElementById('table-pane');
    pane.innerHTML = '<p>Search</p>';
    pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
    await sleep(30);
    ok(failNotes().length === 1, 'a page change keeps it (nothing landed)');

    // 3. the htmx door refused too: its server-rendered notice, the same rule
    htmxSwap(FX.fail_htmx);
    await sleep(30);
    notes = failNotes();
    ok(notes.length === 1 && notes[0].getAttribute('data-live-write-failed') === landed0 && /refused the write/.test(notes[0].textContent),
       'the htmx door\'s notice carries the same stamp and stays');
    // ...and the generic htmx error path (an answer not swapped into the tray)
    const xhr = { status: 409, responseText: '<p>Apply failed: the live folder refused the write.</p>',
                  getResponseHeader: (h) => ({ 'x-sm-error-code': 'live_write_failed',
                                               'x-sm-live-landed': FX.fail_htmx_landed })[String(h).toLowerCase()] || null };
    d.body.dispatchEvent(new w.CustomEvent('htmx:responseError', { bubbles: true, detail: { target: pane, xhr } }));
    await sleep(30);
    ok(failNotes().length === 2 && failNotes()[1].getAttribute('data-live-write-failed') === landed0,
       'the htmx error path marks its notice from X-SM-Live-Landed');
    const other = w.showToast('Some other sticky warning', 'warning', -1);
    ok(!!other && other.isConnected, 'an unrelated sticky warning is up too');

    // 4. the folder is writable again and Apply lands: every failure notice goes
    syncAnswers.push({ status: 200, body: FX.ok1 });
    w.doStateSync('apply');
    await until(() => failNotes().length === 0);
    ok(failNotes().length === 0, 'the landed Apply takes the failure notices down');
    ok(other.isConnected, 'an unrelated notice stays');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
