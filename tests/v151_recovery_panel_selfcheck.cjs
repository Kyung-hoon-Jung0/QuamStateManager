/* v1.5.1 R1: a refused recovery checkpoint is decided in the sync panel.
 *
 *  - the one-shot refusal text (an error notice) is a toast, not an inline
 *    alert the next tray refresh removes ~150 ms later;
 *  - Restore posts the file id; a collision answer is written into the row
 *    (role=status, stays) and the button becomes "Restore over them", whose
 *    press sends force=1;
 *  - Discard needs a second press and posts the file id.
 *
 * Run: node tests/v151_recovery_panel_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const ERR = 'Staged edits could not be recovered: it was written in an older format (version 1; this SM reads version 2). No rows were restored.';
const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' +
    '<div id="pending-tray"><div role="alert" class="tray-recovery-notice tray-recovery-error" data-toast="error" hidden>' +
    ERR + '</div></div>' +
    '<div id="state-review-overlay" style="display:block"><div id="state-review-host"><div class="state-review sync-panel">' +
    '<div class="sp-body"><div class="sp-note sp-unrecovered" data-recovery-id="ab12cd34">' +
    '<div class="sp-unrec-msg" role="status"></div>' +
    '<button type="button" class="sp-unrec-restore">Restore</button>' +
    '<button type="button" class="sp-unrec-discard" data-arm-label="Press again to discard">Discard</button>' +
    '</div></div></div></div></div>' +
    '</body></html>', { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'], [window, 'localStorage'], [window, 'sessionStorage']]
    .forEach(function (t) { Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true }); });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const posts = [];
let answer = null;
const fetchStub = (url, opts) => {
    if (opts && opts.method === 'POST') posts.push([url, JSON.parse(opts.body || '{}')]);
    const a = answer || { status: 200, body: {} };
    return Promise.resolve({ ok: a.status < 400, status: a.status, json: () => Promise.resolve(a.body),
                             text: () => Promise.resolve('') });
};
window.fetch = global.fetch = fetchStub;

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
window.fetch = global.fetch = fetchStub;

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));

(async function main() {
    await tick(30);
    const bar = window.document.getElementById('status-bar');
    const toasts = [...bar.querySelectorAll('.toast')].map((t) => [t.className, t.textContent]);
    ok(toasts.length === 1 && toasts[0][1] === ERR && /toast-error/.test(toasts[0][0]),
       'the refusal text became one error toast (got ' + JSON.stringify(toasts) + ')');
    ok(!window.document.querySelector('#pending-tray .tray-recovery-error'),
       'the inline alert left the one-row top bar');

    ok(window.SyncPanel && typeof window.SyncPanel.recovery === 'function', 'SyncPanel.recovery exists');
    const box = window.document.querySelector('.sp-unrecovered');
    const restore = box.querySelector('.sp-unrec-restore');
    const discard = box.querySelector('.sp-unrec-discard');
    const msg = box.querySelector('.sp-unrec-msg');

    answer = { status: 409, body: { ok: false, error_kind: 'recovery_collision',
        collisions: [{ path: 'qubits.q1.f_01' }], missing: [],
        error: 'Nothing staged yet. 1 field changed since: qubits.q1.f_01. Press again to stage the recovered values over them.' } };
    window.SyncPanel.recovery(restore, 'restore', null);
    await tick();
    ok(posts.length === 1 && posts[0][0] === '/state/recovery/restore' && posts[0][1].id === 'ab12cd34'
       && !posts[0][1].force, 'Restore posts the file id, no force (got ' + JSON.stringify(posts) + ')');
    ok(/qubits\.q1\.f_01/.test(msg.textContent) && msg.classList.contains('sp-unrec-msg-bad'),
       'the collision is written in the row: ' + msg.textContent);
    ok(restore.textContent === 'Restore over them', 'the button now says what it will do: ' + restore.textContent);
    await tick(200);
    ok(/qubits\.q1\.f_01/.test(msg.textContent), 'the answer stays (not a timed toast)');

    answer = { status: 200, body: { ok: true, message: 'Restored 2 edits to the tray (not on the live chip yet).' } };
    window.SyncPanel.recovery(restore, 'restore', null);
    await tick();
    ok(posts.length === 2 && posts[1][1].force === '1', 'the second press sends force=1');
    ok([...bar.querySelectorAll('.toast')].some((t) => /Restored 2 edits/.test(t.textContent)),
       'success is announced');

    answer = { status: 200, body: { ok: true, message: 'Discarded. The file is kept at X.' } };
    window.SyncPanel.recovery(discard, 'discard', null);
    await tick();
    ok(posts.length === 2, 'Discard does nothing on the first press');
    ok(discard.getAttribute('data-armed') === '1' && /Press again/.test(discard.textContent),
       'Discard is armed: ' + discard.textContent);
    window.SyncPanel.recovery(discard, 'discard', null);
    await tick();
    ok(posts.length === 3 && posts[2][0] === '/state/recovery/discard' && posts[2][1].id === 'ab12cd34',
       'the second press posts the discard (got ' + JSON.stringify(posts[2]) + ')');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_recovery_panel_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
