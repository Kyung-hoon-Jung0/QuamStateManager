/* v1.5.3 -- a click outside the sync panel closes it AND reaches its target.
 * EXECUTED against the real shipped app.js under jsdom.
 *
 * Before the fix the panel (a popover with no backdrop) stayed open when the
 * topbar Reload was pressed; Reload's own popup then opened UNDER the panel and
 * the press looked like nothing. One press must now close the panel and run
 * Reload; presses inside the panel, the control's own toggle and Esc keep
 * working as before.
 *
 * Run: node tests/sync_panel_outside_click_selfcheck.cjs (driven by tests/test_sync_panel_outside_click.py)
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
function resp(payload) {
    return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(payload),
                             text: () => Promise.resolve(typeof payload === 'string' ? payload : '') });
}

const BODY = '<header class="topbar"><nav><ul>'
    + '<li><button type="button" id="topbar-reload" aria-expanded="false">Reload</button></li>'
    + '<li id="topbar-tray-slot"><div id="pending-tray" data-change-count="0">'
    + '<span class="sync-control"><button type="button" class="sync-control-main state-status-badge">In sync</button></span>'
    + '</div></li><li><button type="button" id="other-btn">Other</button></li></ul></nav></header>'
    + '<main id="table-pane"><p id="pane-text">pane</p></main>'
    + '<div id="state-review-overlay" class="state-review-overlay" style="display:none">'
    + '<div class="state-review-backdrop"></div><div id="state-review-host" class="state-review-host"></div></div>';

(async function () {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + BODY + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window, d = w.document;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger() {}, process() {}, on() {}, config: {} };
    w.fetch = function (url) {
        if (String(url).indexOf('/state/review') === 0)
            return resp('<div class="sync-panel"><button type="button" id="in-panel">inside</button></div>');
        return resp({});
    };
    w.LiveEditUndo = { record() {}, clear() {}, _updateTrayBtn() {} };
    w.eval(APP);
    // the control's inline onclick="openReview()" (inline handlers do not run under outside-only)
    d.querySelector('.sync-control-main').addEventListener('click', () => w.openReview());
    const click = (el) => { const ev = new w.MouseEvent('click', { bubbles: true, cancelable: true }); el.dispatchEvent(ev); return ev; };
    const isOpen = () => w.SyncPanel.isOpen();
    const reloadPop = () => d.getElementById('sm-reload-pop');

    await w.openReview(); await sleep(20);
    ok(isOpen(), 'the panel opens from the control');

    // the press that used to do nothing
    click(d.getElementById('topbar-reload'));
    await sleep(10);
    ok(!isOpen(), 'a Reload press outside the panel closes the panel');
    ok(reloadPop() && !reloadPop().hidden, 'and the same press still opens Reload (it reached its target)');
    ok(d.querySelector('.sync-control-main').getAttribute('aria-expanded') === 'false', 'the control says collapsed');
    click(d.getElementById('topbar-reload')); await sleep(10);   // close Reload again

    // a press inside the panel keeps it open
    await w.openReview(); await sleep(20);
    const inside = d.getElementById('in-panel');
    ok(inside && isOpen(), 'the panel is open with its content');
    click(inside); await sleep(10);
    ok(isOpen(), 'a press inside the panel leaves it open');

    // the control's own press still toggles (closes) it, not a close-then-reopen
    click(d.querySelector('.sync-control-main')); await sleep(20);
    ok(!isOpen(), 'a second press on the control closes the panel (toggle intact)');
    click(d.querySelector('.sync-control-main')); await sleep(20);
    ok(isOpen(), 'a press on the control opens it again');

    // a press on plain page content closes it and is not swallowed
    const ev2 = click(d.getElementById('pane-text'));
    ok(!isOpen() && !ev2.defaultPrevented, 'a press on the page closes the panel and is not prevented');

    // Esc still closes it
    await w.openReview(); await sleep(20);
    ok(isOpen(), 'reopened');
    (d.getElementById('in-panel') || d.getElementById('state-review-host')).dispatchEvent(
        new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await sleep(10);
    ok(!isOpen(), 'Esc closes the panel');

    console.log((fails ? 'FAILED ' : 'PASSED ') + (asserts - fails) + '/' + asserts);
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
