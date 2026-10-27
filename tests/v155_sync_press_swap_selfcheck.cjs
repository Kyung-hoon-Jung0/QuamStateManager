/* v1.5.5 bug 3 -- a press on the Sync status control opens the sync panel even
 * when the tray re-renders under the press (a drift poll or a flush changes
 * the control's words mid-press). Before the fix the press was dropped with a
 * "the tray changed while you were pressing it" toast, so the control looked
 * dead. EXECUTED against the real shipped app.js under jsdom.
 *
 * Run: node tests/v155_sync_press_swap_selfcheck.cjs (driven by tests/test_v155_bugs.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PILL = (txt) => '<span class="sync-control"><button type="button" class="sync-control-main state-status-badge"'
    + ' onclick="openReview()">' + txt + '</button></span>';
const BODY = '<header class="topbar"><nav><ul><li id="topbar-tray-slot"><div id="pending-tray" data-change-count="0">'
    + PILL('In sync') + '</div></li></ul></nav></header><main id="table-pane"></main>'
    + '<div id="state-review-overlay" class="state-review-overlay" style="display:none">'
    + '<div id="state-review-host" class="state-review-host"></div></div><div id="toast-container"></div>';

(async function () {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + BODY + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window, d = w.document;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger() {}, process() {}, on() {}, config: {} };
    w.fetch = () => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}),
        text: () => Promise.resolve('<div class="sync-panel">panel</div>') });
    w.LiveEditUndo = { record() {}, clear() {}, _updateTrayBtn() {} };
    w.eval(APP);
    const toasts = [];
    w.showToast = (m) => toasts.push(m);
    // stand-in for the inline onclick (inline handlers do not run under outside-only)
    d.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('.sync-control-main')) w.openReview(); });
    const pe = (type, el) => el.dispatchEvent(new w.MouseEvent(type, { bubbles: true, cancelable: true, button: 0 }));

    pe('pointerdown', d.querySelector('.sync-control-main'));
    d.getElementById('pending-tray').innerHTML = PILL('Live chip changed');   // the tray re-renders mid-press
    pe('pointerup', d.querySelector('.sync-control-main'));
    await sleep(30);
    ok(w.SyncPanel.isOpen(), 'a press whose control was re-rendered mid-press still opens the sync panel');
    ok(!toasts.some((t) => /tray changed/.test(t)), 'and no "the tray changed" toast swallows it: ' + toasts.join(' | '));

    console.log(fails ? fails + ' FAILED' : 'all checks passed (' + asserts + ' assertions)');
    process.exit(fails ? 1 : 0);
})();
