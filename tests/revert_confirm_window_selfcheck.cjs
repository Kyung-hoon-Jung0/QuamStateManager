/* gl-17y: the sync panel's "Revert last apply" two-press confirm keeps its
 * window for the full 5 s the button and the toast state, and survives a panel
 * refresh in between.
 *
 * The panel re-reads itself (SyncPanel.refresh) on a poll, another window's
 * move or a node's save; that replaced the armed button with a fresh, unarmed
 * twin, so a second press inside the window armed it again instead of running
 * the revert, and the "Not done ... within 5 s" toast followed. The window is
 * also a deadline now, not a count of timer ticks.
 *
 * The driver (tests/test_revert_confirm_window.py) renders the REAL panel
 * (/state/review) after one apply; this harness runs app.js on it with a fake
 * clock and captured timers.
 *
 * Run: node tests/revert_confirm_window_selfcheck.cjs <fixture.json>
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

function world() {
    const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
        + '<div id="state-review-overlay" class="sync-panel-overlay" style="display:block">'
        + '<div id="state-review-host">' + FX.panel_html + '</div></div></body></html>',
        { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    const triggered = [];
    w.htmx = { ajax: () => Promise.resolve(), trigger: (el, name) => { triggered.push([el, name]); },
               process: () => {}, on: () => {}, config: {} };
    let nextHtml = FX.panel_html;
    w.fetch = () => Promise.resolve({ text: () => Promise.resolve(nextHtml) });
    w.eval(APP);
    const toasts = [];
    w.showToast = (m) => { toasts.push(String(m)); };
    const ivals = new Map(), touts = new Map();
    let nid = 0;
    w.setInterval = (cb) => { ivals.set(++nid, cb); return nid; };
    w.clearInterval = (id) => { ivals.delete(id); };
    w.setTimeout = (cb) => { touts.set(++nid, cb); return nid; };
    w.clearTimeout = (id) => { touts.delete(id); };
    const clock = { now: 1.7e12 };
    w.Date.now = () => clock.now;
    return {
        w, triggered, toasts, clock,
        btn: () => w.document.querySelector('#state-review-host .tray-revert-apply'),
        // advance the clock; each pending interval fires once (a throttled
        // timer fires late and rarely, never more often)
        wait: (ms) => { clock.now += ms; [...ivals.values()].forEach((cb) => cb()); },
        setNext: (h) => { nextHtml = h; },
    };
}

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const flush = () => new Promise((r) => setImmediate(r));

(async () => {
    const S = 5;
    // A: a refresh between the two presses keeps the confirm armed
    {
        const t = world();
        const b0 = t.btn();
        ok(!!b0, 'the real panel renders Revert last apply');
        ok(/SyncPanel\.arm/.test(b0.getAttribute('onclick') || ''), 'it is the in-place two-press confirm');
        t.w.SyncPanel.arm(b0, null);
        ok(b0.getAttribute('data-armed') === '1', 'the first press arms it');
        t.wait(800);
        await t.w.SyncPanel.refresh();
        await flush();
        const b1 = t.btn();
        ok(b1 && b1 !== b0, 'the refresh replaced the button');
        ok(b1.getAttribute('data-armed') === '1', 'the fresh twin is still armed after the refresh');
        ok(b1.getAttribute('data-arm-left') === '5', 'its badge counts from the ORIGINAL arming: ' + b1.getAttribute('data-arm-left'));
        t.wait(400);
        t.w.SyncPanel.arm(b1, null);
        ok(t.triggered.length === 1 && t.triggered[0][1] === 'armed' && t.triggered[0][0] === b1,
           'a second press 1.2 s after the first, across the refresh, runs the revert');
        ok(!t.toasts.some((m) => /Not done/.test(m)), 'and no expiry toast: ' + t.toasts.join(' | '));
    }
    // B: the carried window still closes at the ORIGINAL deadline
    {
        const t = world();
        t.w.SyncPanel.arm(t.btn(), null);
        t.wait(3000);
        await t.w.SyncPanel.refresh();
        await flush();
        const b1 = t.btn();
        ok(b1.getAttribute('data-armed') === '1', 'carried at 3 s');
        t.wait(1900);
        ok(b1.getAttribute('data-armed') === '1', 'still armed at 4.9 s');
        t.wait(200);
        ok(b1.getAttribute('data-armed') !== '1' && b1.getAttribute('data-arm-left') === 'expired',
           'expired at 5.1 s: the refresh did not extend the window');
        ok(t.toasts.some((m) => new RegExp('within ' + S + ' s').test(m)), 'the toast names the same 5 s');
        t.w.SyncPanel.arm(b1, null);
        ok(t.triggered.length === 0 && b1.getAttribute('data-armed') === '1',
           'a press after the window arms afresh and runs nothing');
    }
    // C: no list of ticks decides the window -- one late tick past the deadline expires it
    {
        const t = world();
        const b = t.btn();
        t.w.SyncPanel.arm(b, null);
        t.wait(4900);
        ok(b.getAttribute('data-armed') === '1' && b.getAttribute('data-arm-left') === '1',
           'one late tick at 4.9 s: still armed, 1 s left');
        t.wait(400);
        t.w.SyncPanel.arm(b, null);
        ok(t.triggered.length === 0, 'past 5 s the second press is refused (it re-arms)');
    }
    // D: a press inside the window with no refresh still runs
    {
        const t = world();
        const b = t.btn();
        t.w.SyncPanel.arm(b, null);
        t.wait(1000);
        t.w.SyncPanel.arm(b, null);
        ok(t.triggered.length === 1, 'a plain second press at 1 s runs the revert');
    }
    // F: the refresh does not restart the double-click floor either: a press
    // just after the refresh, long after the first press, is the confirm
    {
        const t = world();
        t.w.SyncPanel.arm(t.btn(), null);
        t.wait(800);
        await t.w.SyncPanel.refresh();
        await flush();
        t.wait(50);
        t.w.SyncPanel.arm(t.btn(), null);
        ok(t.triggered.length === 1, 'a press 50 ms after the refresh (850 ms after arming) runs the revert');
    }
    // E: a twin for another action (another snapshot) is not armed by the carry
    {
        const t = world();
        t.w.SyncPanel.arm(t.btn(), null);
        t.setNext(FX.panel_html.replace(/\/state-history\/[^/"]+\/stage/, '/state-history/20000101_000000_0001/stage'));
        t.wait(500);
        await t.w.SyncPanel.refresh();
        await flush();
        ok(t.btn().getAttribute('data-armed') !== '1', 'a re-rendered button for another snapshot stays unarmed');
    }
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})();
