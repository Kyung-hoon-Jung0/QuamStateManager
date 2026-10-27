/* v1.5.5 bug 4 -- a notice (toast) can be selected and copied, and does not
 * fade while its text is selected. EXECUTED against the real shipped app.js.
 * Run: node tests/v155_toast_copy_selfcheck.cjs (driven by tests/test_v155_bugs.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
    const dom = new JSDOM('<!doctype html><html><body><main id="table-pane"></main><div id="status-bar"></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window, d = w.document;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger() {}, process() {}, on() {}, config: {} };
    w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
    w.LiveEditUndo = { record() {}, clear() {}, _updateTrayBtn() {} };
    let copied = null;
    Object.defineProperty(w.navigator, 'clipboard', { value: { writeText: (t) => { copied = t; return Promise.resolve(); } }, configurable: true });
    w.eval(APP);

    const t = w.showToast('Apply failed: the folder is read-only', 'warning', 120, 'Errno 13 permission denied');
    await sleep(5);
    const btn = t.querySelector('.toast-copy');
    ok(!!btn, 'every notice carries a Copy button');
    btn && btn.click();
    await sleep(5);
    ok(copied === 'Apply failed: the folder is read-only\nDetails: Errno 13 permission denied',
       'Copy copies the whole text, the details line included: ' + JSON.stringify(copied));

    // an out-of-band notice the server rendered gets the button too
    const oob = d.createElement('div'); oob.className = 'toast toast-success'; oob.innerHTML = '<p>Applied to the live chip.</p>';
    d.getElementById('status-bar').appendChild(oob);
    await sleep(5);
    ok(!!oob.querySelector('.toast-copy'), 'a server-rendered notice gets a Copy button');

    // selected text holds the notice past its time
    const r = d.createRange(); r.selectNodeContents(t.querySelector('p'));
    w.getSelection().removeAllRanges(); w.getSelection().addRange(r);
    await sleep(900);
    ok(t.isConnected && t.style.opacity !== '0', 'a notice whose text is selected does not fade');
    // hovering holds it too
    const t2 = w.showToast('hover me', 'info', 120);
    t2.querySelector('p').dispatchEvent(new w.MouseEvent('mouseover', { bubbles: true }));
    await sleep(900);
    ok(t2.isConnected, 'a hovered notice does not fade');
    t2.querySelector('p').dispatchEvent(new w.MouseEvent('mouseout', { bubbles: true, relatedTarget: d.body }));
    w.getSelection().removeAllRanges();
    await sleep(1500);
    ok(!t.isConnected && !t2.isConnected, 'once the selection and the pointer are gone they fade and go');

    // v1.5.6: a plain click on the notice text reaches the control under it
    const cell = d.createElement('input'); cell.className = 'bulk-cell'; d.getElementById('table-pane').appendChild(cell);
    let cellClicks = 0; cell.addEventListener('click', () => { cellClicks++; });
    const t3 = w.showToast('a notice over a cell', 'error', 5000);
    await sleep(5);
    const tp = t3.querySelector('p');
    d.elementsFromPoint = () => [tp, t3, d.getElementById('status-bar'), cell, d.getElementById('table-pane'), d.body];
    tp.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 50, clientY: 50 }));
    tp.dispatchEvent(new w.MouseEvent('mouseup', { bubbles: true, button: 0, clientX: 50, clientY: 50 }));
    tp.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0, detail: 1, clientX: 50, clientY: 50 }));
    ok(cellClicks === 1 && d.activeElement === cell, 'a click on the notice text reaches the cell under it and focuses it');
    // a drag (the pointer moved) selects instead: nothing is handed on
    cell.blur();
    tp.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 50, clientY: 50 }));
    tp.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0, detail: 1, clientX: 140, clientY: 50 }));
    ok(cellClicks === 1 && d.activeElement !== cell, 'a drag across the text is not handed on');
    // the Copy button is never handed on
    const cb = t3.querySelector('.toast-copy');
    d.elementsFromPoint = () => [cb, t3, cell];
    cb.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 60, clientY: 60 }));
    cb.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0, detail: 1, clientX: 60, clientY: 60 }));
    ok(cellClicks === 1, 'a press on Copy stays on Copy');
    t3.remove();

    ok(/#status-bar \.toast p \{[^}]*pointer-events: auto;[^}]*user-select: text/.test(CSS),
       'the notice text takes the pointer and is selectable');
    console.log(fails ? fails + ' FAILED' : 'all checks passed (' + asserts + ' assertions)');
    process.exit(fails ? 1 : 0);
})();
