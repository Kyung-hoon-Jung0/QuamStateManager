/* v1.5.1 U P2-32: a two-press confirm shows its seconds and says when it ran out.
 *
 * Apply -> Confirm and Discard -> "Press again to discard" expired after 5 s
 * with no sign, and a second press that came later just armed it again. Now
 * the seconds left show on the button (data-arm-left, a corner badge), the
 * expiry is marked on the button and said in a toast (nothing was done), and
 * a press inside the window still runs the action.
 *
 * The driver (tests/test_v151_arm_countdown.py) renders the REAL sync panel
 * (/state/review) with one unapplied edit; this harness runs app.js on it with
 * captured timers.
 *
 * Run: node tests/v151_arm_countdown_selfcheck.cjs <fixture.json>
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
    + '<div id="state-review-host">' + FX.panel_html + '</div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
const triggered = [];
w.htmx = { ajax: () => Promise.resolve(), trigger: (el, name) => { triggered.push(name); },
           process: () => {}, on: () => {}, config: {} };
w.fetch = () => new Promise(() => {});
w.eval(APP);
const toasts = [];
w.showToast = (m, l) => { toasts.push(String(m)); };
// captured timers: the test decides when a second passes
const ivals = new Map(), touts = new Map();
let nid = 0;
w.setInterval = (cb) => { ivals.set(++nid, cb); return nid; };
w.clearInterval = (id) => { ivals.delete(id); };
w.setTimeout = (cb) => { touts.set(++nid, cb); return nid; };
w.clearTimeout = (id) => { touts.delete(id); };
const second = () => { [...ivals.values()].forEach((cb) => cb()); };

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const btn = w.document.querySelector('.sp-discard-all');
ok(!!btn, 'the real panel renders the two-press Discard');
const label = () => btn.querySelector('.sp-choice-label').textContent;
const orig = label();
w.SyncPanel.arm(btn, null);
ok(btn.getAttribute('data-armed') === '1' && /Press again/.test(label()), 'the first press arms: ' + label());
ok(btn.getAttribute('data-arm-left') === String(w.SyncPanel.armSeconds), 'the seconds left show: ' + btn.getAttribute('data-arm-left'));
second();
ok(btn.getAttribute('data-arm-left') === String(w.SyncPanel.armSeconds - 1), 'and count down: ' + btn.getAttribute('data-arm-left'));
for (let i = 1; i < w.SyncPanel.armSeconds; i++) second();
ok(btn.getAttribute('data-armed') !== '1' && label() === orig, 'after the window it is disarmed, its label back');
ok(btn.classList.contains('sync-arm-expired') && btn.getAttribute('data-arm-left') === 'expired',
   'the button shows it expired');
ok(toasts.some((t) => /Not done/.test(t) && /nothing was changed/.test(t)), 'and says nothing was done: ' + toasts.join(' | '));
ok(triggered.length === 0, 'nothing ran');
// a later press arms again (it was told), and a second press in the window runs it
w.SyncPanel.arm(btn, null);
ok(btn.getAttribute('data-armed') === '1' && !btn.classList.contains('sync-arm-expired'),
   'a press after the expiry arms afresh, the expiry mark cleared');
w.SyncPanel.arm(btn, null);
ok(triggered.indexOf('armed') >= 0, 'a second press in the window runs the action');
ok(!btn.hasAttribute('data-arm-left') && btn.getAttribute('data-armed') !== '1', 'and clears the countdown');

console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
process.exit(fails ? 1 : 0);
