/* v1.5.1 T1-F2: Ctrl+Z into an earlier session's journal; the page side.
 *
 * Pins, against the real app.js + undo-trail.js under jsdom:
 *  A  the server's undoAskPrevSession question is asked (in-page smConfirm with
 *     the server's sentence); OK sends ONE /undo carrying that unit's yes
 *     (ack_prev_session), Cancel sends nothing and says so;
 *  B  a later /undo does not carry that unit's yes again, but carries the
 *     WINDOW's yes to old edits (R2 MINOR 4: kept per window in
 *     sessionStorage, keyed by the chip folder; not per chip context on the
 *     server), and never a yes to another window's step;
 *  F  a Yes to another window's step is sent with that step and then stands
 *     for this window (undo and redo, per tab and chip folder, bound to the
 *     document's window id); it is never a yes to old edits;
 *  G  a question naming both reasons: its Yes covers the age for that step
 *     only, the window part stands;
 *  H  one Yes, then redo, then an older undo: every later press carries the
 *     window's standing yes, so the server asks once; a Cancel sets none and
 *     a new document (reload) has none;
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
const WIN_KEY = 'sm.undoOtherWindowOk.chip_k-0123abcd';
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'undo-trail.js'), 'utf8'));
const d = window.document;

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));
function confirmOverlay() { return d.getElementById('sm-confirm-overlay'); }
function confirmMsg() {
    const o = confirmOverlay();
    const pre = o && o.querySelector('.sm-confirm-message');
    return pre ? pre.textContent : '';
}
async function clickConfirm(yes) {
    await tick(40);
    const o = confirmOverlay();
    if (!o) throw new Error('no sm-confirm overlay');
    const btn = o.querySelector(yes ? '.sm-confirm-ok' : '.sm-confirm-cancel');
    btn.click();
    await tick(40);
}
const toasts = [];
const realToast = window.showToast;
window.showToast = function (m, l, ms) { toasts.push(String(m)); return realToast && realToast(m, l, ms); };

function ask(unit) {
    d.dispatchEvent(new window.CustomEvent('undoAskPrevSession', { detail: {
        unit_id: unit, age: 'from 2 days ago', reasons: ['age'],
        message: 'This Ctrl+Z writes the live chip: ports.x.full_scale_power_dbm → 10. '
               + 'It undoes an edit from 2 days ago, made in an earlier SM session.' } }));
}

(async function main() {
    await tick();
    /* A: OK */
    ask('u-old-1');
    await tick(80);
    ok(!!confirmOverlay() && /from 2 days ago/.test(confirmMsg()) && /earlier SM session/.test(confirmMsg()),
       'A1: the question is asked with the edit and its age; ' + JSON.stringify(confirmMsg() || ''));
    await clickConfirm(true);
    const undo = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo.length === 1 && undo[0].o && undo[0].o.values
       && undo[0].o.values.ack_prev_session === 'u-old-1',
       'A2: OK sends one /undo with that unit\'s yes; ' + JSON.stringify(undo.map((c) => c.o && c.o.values)));
    ok(undo[0].o.values.ack_prev_window === '' && undo[0].o.values.ack_other_windows === '',
       'A2b: a Yes to the age question is not a yes to another window\'s step; ' + JSON.stringify(undo[0].o.values));
    /* B */
    window.UndoQueue.push('/undo');
    await tick(80);
    const undo2 = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo2.length === 2 && !undo2[1].o.values.ack_prev_session,
       'B1: the next press does not carry the yes again; ' + JSON.stringify(undo2[1] && undo2[1].o.values));
    ok(undo2[1].o.values.ack_old_edits === '1' && SESSION.getItem('sm.undoOldOk.chip_k-0123abcd') === '1',
       'B2: ...it carries the yes of THIS window to old edits, kept per window; ' + JSON.stringify(undo2[1].o.values));
    ok(undo2[1].o.values.ack_prev_window === '' && undo2[1].o.values.ack_other_windows === '',
       'B2b: ...and never a yes to another window\'s step; ' + JSON.stringify(undo2[1].o.values));
    SESSION._m.clear();
    window.UndoQueue.push('/undo');
    await tick(80);
    const undo3 = ajaxCalls.filter((c) => c.u === '/undo');
    ok(undo3.length === 3 && undo3[2].o.values.ack_old_edits === '',
       'B3: a window that never said yes sends none; ' + JSON.stringify(undo3[2].o.values));

    /* F: the window question; its Yes covers that one step and no age */
    function askWindow(unit, direction, reasons) {
        d.dispatchEvent(new window.CustomEvent('undoAskPrevSession', { detail: {
            unit_id: unit, direction: direction, reasons: reasons,
            message: (direction === '/redo' ? 'Redo' : 'Undo')
                   + ' another window\'s change: ports.x.full_scale_power_dbm to 10; Ctrl+Z writes the chip.' } }));
    }
    const last = () => ajaxCalls[ajaxCalls.length - 1];
    askWindow('w-1', '/undo', ['other_window']);
    await clickConfirm(true);
    ok(last().u === '/undo' && last().o.values.ack_prev_window === 'w-1'
       && last().o.values.ack_prev_session === '' && last().o.values.ack_old_edits === '',
       'F1: a Yes to the window question sends only the window yes, for that step; ' + JSON.stringify(last().o.values));
    ok(SESSION.getItem('sm.undoOldOk.chip_k-0123abcd') === null,
       'F2: ...and never stands for a yes to old edits');
    ok(!!window.__smWindowId && SESSION.getItem(WIN_KEY) === window.__smWindowId,
       'F2b: ...it stands for this window, kept per tab and chip folder with the window id');
    window.UndoQueue.push('/undo');
    await tick(80);
    ok(last().o.values.ack_prev_window === '' && last().o.values.ack_old_edits === ''
       && last().o.values.ack_other_windows === '1',
       'F3: the next step carries the standing yes of this window only; ' + JSON.stringify(last().o.values));
    askWindow('r-1', '/redo', ['other_window']);
    await clickConfirm(true);
    ok(last().u === '/redo' && last().o.values.ack_prev_window === 'r-1',
       'F4: a redo question Yes repeats the redo with that step\'s yes; ' + JSON.stringify(last().o.values));
    window.UndoQueue.push('/redo');
    await tick(80);
    ok(last().u === '/redo' && last().o.values.ack_prev_window === '' && last().o.values.ack_other_windows === '1',
       'F5: the next redo carries the standing yes, not a step yes; ' + JSON.stringify(last().o.values));
    SESSION._m.clear();

    /* G: one question naming both reasons; its Yes covers both, for that step only */
    askWindow('c-1', '/undo', ['other_window', 'age']);
    await clickConfirm(true);
    ok(last().u === '/undo' && last().o.values.ack_prev_window === 'c-1'
       && last().o.values.ack_prev_session === 'c-1',
       'G1: a combined Yes sends both yeses for that step; ' + JSON.stringify(last().o.values));
    window.UndoQueue.push('/undo');
    await tick(80);
    ok(last().o.values.ack_prev_window === '' && last().o.values.ack_prev_session === ''
       && last().o.values.ack_old_edits === '' && SESSION.getItem('sm.undoOldOk.chip_k-0123abcd') === null
       && last().o.values.ack_other_windows === '1',
       'G2: ...and for the next step only the window part stands; ' + JSON.stringify(last().o.values));

    /* H: one Yes per window, then redo and an older undo ride on it */
    SESSION._m.clear();
    askWindow('h-1', '/undo', ['other_window']);
    await clickConfirm(false);
    ok(SESSION.getItem(WIN_KEY) === null, 'H1: a Cancel sets no standing yes');
    const toastsWas = toasts.length;
    askWindow('h-0', '/redo', ['other_window']);
    await clickConfirm(false);
    ok(toasts.slice(toastsWas).some((t) => t === 'Not redone; nothing was written.')
       && !toasts.slice(toastsWas).some((t) => /Not undone/.test(t)),
       'H1b: a Cancel on a redo question says "Not redone"');
    askWindow('h-1', '/undo', ['other_window']);
    await clickConfirm(true);
    window.UndoQueue.push('/redo');
    await tick(80);
    const hRedo = last();
    window.UndoQueue.push('/undo');
    await tick(80);
    const hUndo = last();
    ok(hRedo.u === '/redo' && hRedo.o.values.ack_other_windows === '1'
       && hUndo.u === '/undo' && hUndo.o.values.ack_other_windows === '1',
       'H2: after one Yes the redo and the older undo both carry the yes of this window; '
       + JSON.stringify([hRedo.o.values, hUndo.o.values]));
    const realId = window.__smWindowId;
    window.__smWindowId = 'another-document';
    window.UndoQueue.push('/undo');
    await tick(80);
    ok(last().o.values.ack_other_windows === '', 'H3: a new document (reload) has no standing yes');
    window.__smWindowId = realId;
    SESSION._m.clear();

    d.body.focus();
    d.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'y', ctrlKey: true, bubbles: true }));
    await tick(80);
    ok(last().u === '/redo', 'Ctrl+Y routes to redo');
    const undoBeforeE = ajaxCalls.filter((c) => c.u === '/undo' || /^\/undo\?/.test(c.u)).length;
    /* E: presses queued behind the asked one */
    let release;
    hold = { p: new Promise((r) => { release = r; }) };
    window.UndoQueue.push('/undo');            // in flight
    await tick(20);
    window.UndoQueue.push('/undo');            // queued behind it
    window.UndoQueue.push('/undo');
    ok(window.UndoQueue.depth() === 2, 'E setup: two presses wait behind the asked one');
    ask('u-old-3');                            // the in-flight press was asked about
    await clickConfirm(false);
    hold = null;
    release();
    await tick(120);
    const undo4 = ajaxCalls.filter((c) => c.u === '/undo' || /^\/undo\?/.test(c.u));
    ok(undo4.length === undoBeforeE + 1 && window.UndoQueue.depth() === 0,
       'E1: Cancel sends nothing more; the queued presses were dropped (' + undo4.length + ')');
    /* A: Cancel */
    const before = ajaxCalls.length;
    ask('u-old-2');
    await tick(80);
    await clickConfirm(false);
    ok(ajaxCalls.length === before, 'A3: Cancel sends nothing');
    ok(toasts.some((t) => /Not undone/.test(t) && /nothing was written/.test(t)), 'A4: ...and says so');

    /* C + D */
    d.dispatchEvent(new window.CustomEvent('cellsReverted', { detail: {
        message: 'Undone → live: ports.x.full_scale_power_dbm → 10 (edit from 2 days ago)', live: true,
        age: 'from 2 days ago',
        entries: [{ dot_path: 'ports.x.full_scale_power_dbm', old_value_str: '10', old_value_disp: '10' }] } }));
    const p = d.getElementById('undo-trail');
    ok(p && !p.hidden && /edit from 2 days ago/.test(p.textContent),
       'C1: the Undo / Redo panel names the edit\'s age; ' + (p ? p.textContent.slice(0, 120) : 'no panel'));
    ok(d.body.classList.contains('undo-trail-on'), 'D1: the open panel lifts the toast sink');
    ok(/px$/.test(d.body.style.getPropertyValue('--undo-trail-h')),
       'D2: ...by its measured height; ' + d.body.style.getPropertyValue('--undo-trail-h'));
    p.querySelector('.undo-trail-close').click();
    ok(!d.body.classList.contains('undo-trail-on'), 'D3: closing the panel drops the lift');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_undo_prev_session_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
