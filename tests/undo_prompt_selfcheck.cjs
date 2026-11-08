/* In-page smConfirm sheet (replaces native confirm on undo-prev-session and
 * torn-live paths). Proceed/cancel/Escape/Enter/TrapFocus/message text.
 *
 * Run: node tests/undo_prompt_selfcheck.cjs (tests/test_undo_prompt.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><body></body></html>',
    { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
window.eval(src);

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function overlay() { return document.getElementById('sm-confirm-overlay'); }
function msgText() {
    const o = overlay();
    const pre = o && o.querySelector('.sm-confirm-message');
    return pre ? pre.textContent : '';
}

async function testSmConfirmDirect() {
    const TEXT = "Line one\nLine two\nOK = proceed.\nCancel = stop.";
    const p = window.smConfirm(TEXT);
    await tick(30);
    ok(!!overlay(), 'direct: overlay opens');
    ok(msgText() === TEXT, 'direct: message text unchanged');
    ok(!!overlay().querySelector('.sm-confirm-ok'), 'direct: OK button present');
    const trap = overlay().querySelector('.kb-sheet');
    ok(!!trap && document.activeElement === trap.querySelector('.sm-confirm-ok'),
       'direct: focus starts on OK');
    overlay().querySelector('.sm-confirm-ok').click();
    ok((await p) === true, 'direct: OK click proceeds');
    ok(!overlay(), 'direct: overlay closes after OK');

    const p2 = window.smConfirm(TEXT);
    await tick(30);
    overlay().querySelector('.sm-confirm-cancel').click();
    ok((await p2) === false, 'direct: Cancel click aborts');

    const p3 = window.smConfirm(TEXT);
    await tick(30);
    document.dispatchEvent(new window.KeyboardEvent('keydown', {
        key: 'Escape', bubbles: true, cancelable: true }));
    await tick(30);
    ok((await p3) === false, 'direct: Escape cancels');

    const p4 = window.smConfirm(TEXT);
    await tick(30);
    const okBtn = overlay().querySelector('.sm-confirm-ok');
    okBtn.focus();
    okBtn.dispatchEvent(new window.KeyboardEvent('keydown', {
        key: 'Enter', bubbles: true, cancelable: true }));
    await tick(30);
    ok((await p4) === true, 'direct: Enter proceeds');
}

async function testUndoAskPrevSession() {
    const tray = document.createElement('div');
    tray.id = 'pending-tray';
    document.body.appendChild(tray);
    const calls = [];
    window.htmx = { ajax: (m, u, o) => { calls.push({ u, o }); return Promise.resolve(); },
                    trigger: () => {}, process: () => {} };
    global.htmx = window.htmx;
    const toasts = [];
    window.showToast = (m) => { toasts.push(String(m)); };

    const DETAIL = {
        unit_id: 'u-test',
        message: 'This Ctrl+Z writes the live chip: qubits.q1.T1 → 2.',
    };
    document.dispatchEvent(new window.CustomEvent('undoAskPrevSession', { detail: DETAIL }));
    await tick(50);
    ok(/T1 → 2/.test(msgText()) && /undo it on the live chip/.test(msgText()),
       'undoAskPrevSession: shows server message plus choices');
    const before = calls.length;
    overlay().querySelector('.sm-confirm-cancel').click();
    await tick(80);
    ok(calls.length === before, 'undoAskPrevSession: cancel issues no /undo');
    ok(toasts.some((t) => /Not undone/.test(t)), 'undoAskPrevSession: cancel toasts');

    document.dispatchEvent(new window.CustomEvent('undoAskPrevSession', { detail: DETAIL }));
    await tick(50);
    overlay().querySelector('.sm-confirm-ok').click();
    await tick(80);
    const undo = calls.filter((c) => c.u === '/undo');
    ok(undo.length === 1 && undo[0].o.values.ack_prev_session === 'u-test',
       'undoAskPrevSession: OK sends /undo with ack');
}

(async function main() {
    await testSmConfirmDirect();
    await testUndoAskPrevSession();
    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('undo_prompt_selfcheck: all checks passed');
    process.exit(0);
})().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
