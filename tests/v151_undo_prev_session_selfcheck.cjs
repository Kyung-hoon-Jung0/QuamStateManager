/* v1.5.1 T1-F2: Ctrl+Z into an earlier session's journal -- the page side.
 *
 * Pins, against the real app.js + undo-trail.js under jsdom:
 *  A  the server's undoAskPrevSession question is asked (window.confirm with
 *     the server's sentence); OK sends ONE /undo carrying that unit's yes
 *     (ack_prev_session), Cancel sends nothing and says so;
 *  B  a later /undo does not carry that unit's yes again, but carries the
 *     WINDOW's yes (R2 MINOR 4: kept per window in sessionStorage, keyed by
 *     the chip folder -- not per chip context on the server);
 *  E  presses queued behind the asked one are dropped when it asks (R2
 *     MINOR 4: a Cancel used to be asked again for each of them);
 *  C  the Undo / Redo panel names the edit's age;
 *  D  while the panel is open the toast sink is lifted above it
 *     (body.undo-trail-on + --undo-trail-h), and closing it drops the lift.
 *
 * Run: node tests/v151_undo_prev_session_selfcheck.cjs
 *      (driven by tests/test_v151_undo_prev_session.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<div id="topbar-tray-slot"><div id="pending-tray" data-change-sig="s0"></div></div>'
    + '<div id="table-pane"></div></body></html>',
    { url: 'http://localhost/qubits', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const SESSION = (function () {
    const m = new Map();
    return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
             removeItem: (k) => m.delete(k), _m: m };
})();
[[global, 'localStorage'], [window, 'localStorage']]
    .forEach(function (t) { Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true }); });
[[global, 'sessionStorage'], [window, 'sessionStorage']]
    .forEach(function (t) { Object.defineProperty(t[0], t[1], { value: SESSION, configurable: true, writable: true }); });
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
const ajaxCalls = [];
let hold = null;                  // a pending /undo (the server has not answered yet)
window.htmx = { ajax: (m, u, o) => { ajaxCalls.push({ m: m, u: u, o: o });
                                     return hold ? hold.p : Promise.resolve(); },
                trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.__chipKey = 'chip_k-0123abcd';
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'undo-trail.js'), 'utf8'));
const d = window.document;

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));
const asked = [];
let answer = true;
window.confirm = function (msg) { asked.push(String(msg)); return answer; };
const toasts = [];
const realToast = window.showToast;
window.showToast = function (m, l, ms) { toasts.push(String(m)); return realToast && realToast(m, l, ms); };

function ask(unit) {
    d.dispatchEvent(new window.CustomEvent('undoAskPrevSession', { detail: {
        unit_id: unit, age: 'from 2 days ago',
        message: 'This Ctrl+Z writes the live chip: ports.x.full_scale_power_dbm → 10. '
               + 'It undoes an edit from 2 days ago, made in an earlier SM session.' } }));
}

(async function main() {
    await tick();
    /* A: OK */
    ask('u-old-1');
    await tick(80);
    ok(asked.length === 1 && /from 2 days ago/.test(asked[0]) && /earlier SM session/.test(asked[0]),
       'A1: the question is asked with the edit and its age -- ' + JSON.stringify(asked[0] || ''));
    const undo = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo.length === 1 && undo[0].o && undo[0].o.values
       && undo[0].o.values.ack_prev_session === 'u-old-1',
       'A2: OK sends one /undo with that unit\'s yes -- ' + JSON.stringify(undo.map((c) => c.o && c.o.values)));
    /* B */
    window.UndoQueue.push('/undo');
    await tick(80);
    const undo2 = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo2.length === 2 && !undo2[1].o.values.ack_prev_session,
       'B1: the next press does not carry the yes again -- ' + JSON.stringify(undo2[1] && undo2[1].o.values));
    ok(undo2[1].o.values.ack_prev_window === '1' && SESSION.getItem('sm.undoOldOk.chip_k-0123abcd') === '1',
       'B2: ...it carries the yes of THIS window, kept per window -- ' + JSON.stringify(undo2[1].o.values));
    SESSION._m.clear();
    window.UndoQueue.push('/undo');
    await tick(80);
    const undo3 = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo3.length === 3 && undo3[2].o.values.ack_prev_window === '',
       'B3: a window that never said yes sends none -- ' + JSON.stringify(undo3[2].o.values));

    /* E: presses queued behind the asked one */
    let release;
    hold = { p: new Promise((r) => { release = r; }) };
    window.UndoQueue.push('/undo');            // in flight
    await tick(20);
    window.UndoQueue.push('/undo');            // queued behind it
    window.UndoQueue.push('/undo');
    ok(window.UndoQueue.depth() === 2, 'E setup: two presses wait behind the asked one');
    answer = false;
    ask('u-old-3');                            // the in-flight press was asked about
    hold = null;
    release();
    await tick(120);
    const undo4 = ajaxCalls.filter((c) => c.u === '/undo' || /^\/undo\?/.test(c.u));
    ok(undo4.length === 4 && window.UndoQueue.depth() === 0,
       'E1: Cancel sends nothing more -- the queued presses were dropped (' + undo4.length + ')');
    answer = true;
    asked.length = 0;
    /* A: Cancel */
    answer = false;
    const before = ajaxCalls.length;
    ask('u-old-2');
    await tick(80);
    ok(ajaxCalls.length === before, 'A3: Cancel sends nothing');
    ok(toasts.some((t) => /Not undone/.test(t) && /nothing was written/.test(t)), 'A4: ...and says so');

    /* C + D */
    d.dispatchEvent(new window.CustomEvent('cellsReverted', { detail: {
        message: 'Undone → live: ports.x.full_scale_power_dbm → 10 (edit from 2 days ago)', live: true,
        age: 'from 2 days ago',
        entries: [{ dot_path: 'ports.x.full_scale_power_dbm', old_value_str: '10', old_value_disp: '10' }] } }));
    const p = d.getElementById('undo-trail');
    ok(p && !p.hidden && /edit from 2 days ago/.test(p.textContent),
       'C1: the Undo / Redo panel names the edit\'s age -- ' + (p ? p.textContent.slice(0, 120) : 'no panel'));
    ok(d.body.classList.contains('undo-trail-on'), 'D1: the open panel lifts the toast sink');
    ok(/px$/.test(d.body.style.getPropertyValue('--undo-trail-h')),
       'D2: ...by its measured height -- ' + d.body.style.getPropertyValue('--undo-trail-h'));
    p.querySelector('.undo-trail-close').click();
    ok(!d.body.classList.contains('undo-trail-on'), 'D3: closing the panel drops the lift');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_undo_prev_session_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
