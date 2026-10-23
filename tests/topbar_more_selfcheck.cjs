/* v1.5.3 (design A) -- the topbar keeps the chip name, the Sync status control
 * and Review; every other control is the same node moved into one overflow menu.
 * EXECUTED against the real shipped app.js under jsdom, on the topbar the server
 * actually renders (argv[2] = that header's HTML).
 *
 * Run: node tests/topbar_more_selfcheck.cjs <header.html> (driven by tests/test_topbar_more.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const HEADER = fs.readFileSync(process.argv[2], 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + HEADER
        + '<main id="table-pane"><p id="pane-text">pane</p></main></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window, d = w.document;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger() {}, process() {}, on() {}, config: {} };
    w.fetch = () => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
    w.LiveEditUndo = { record() {}, clear() {}, _updateTrayBtn() {} };
    w.eval(APP);
    const btn = d.getElementById('topbar-more-btn'), menu = d.getElementById('topbar-more-menu');
    ok(btn && menu && menu.hidden, 'the overflow button exists and its menu starts closed');
    if (!btn || !menu) { console.log('FAILED'); process.exit(1); }
    ok(btn.getAttribute('aria-expanded') === 'false' && btn.getAttribute('aria-controls') === 'topbar-more-menu',
       'the button names its menu and says collapsed');
    const first = d.querySelector('.topbar > nav > ul:first-child');
    ok(first.contains(d.getElementById('pending-tray')), 'Sync + Review (the tray) stay on the bar');
    ['topbar-reload', 'global-search', 'state-version-slot', 'diag-tray-slot', 'agent-pill'].forEach(function (id) {
        const el = d.getElementById(id);
        ok(el && menu.contains(el) && d.querySelectorAll('#' + id).length === 1, id + ' lives in the menu, once');
    });
    ok(!first.querySelector('.wb-link') && menu.querySelector('.wb-link'), 'Sync-Qualibrate moved into the menu');

    const key = (el, k) => { const ev = new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }); el.dispatchEvent(ev); return ev; };
    const click = (el, detail) => { const ev = new w.MouseEvent('click', { bubbles: true, cancelable: true, detail: detail == null ? 1 : detail }); el.dispatchEvent(ev); return ev; };

    // keyboard: Enter/Space on a native button fire a click with detail 0
    btn.focus();
    click(btn, 0);
    ok(!menu.hidden && btn.getAttribute('aria-expanded') === 'true', 'Enter/Space (a keyboard click) opens the menu');
    const items = w.TopbarMore.items();
    ok(items.length >= 3, 'the menu has focusable items (' + items.length + ')');
    ok(d.activeElement === items[0], 'opening from the keyboard focuses the first item');
    key(d.activeElement, 'ArrowDown');
    ok(d.activeElement === items[1], 'ArrowDown moves to the next item');
    key(d.activeElement, 'ArrowUp');
    ok(d.activeElement === items[0], 'ArrowUp moves back');
    key(d.activeElement, 'ArrowUp');
    ok(d.activeElement === items[items.length - 1], 'ArrowUp from the first wraps to the last');
    key(d.activeElement, 'Escape');
    ok(menu.hidden && btn.getAttribute('aria-expanded') === 'false', 'Esc closes the menu');
    ok(d.activeElement === btn, 'and focus returns to the button');

    // mouse: toggle, outside click closes and still reaches its target
    click(btn);
    ok(!menu.hidden, 'a mouse click opens the menu');
    click(btn);
    ok(menu.hidden, 'a second click closes it');
    click(btn);
    let reached = false;
    d.getElementById('pane-text').addEventListener('click', () => { reached = true; });
    const ev = click(d.getElementById('pane-text'));
    ok(menu.hidden && reached && !ev.defaultPrevented, 'an outside click closes the menu and still reaches its target');

    // the moved Reload keeps its own handler (same node, not a copy)
    click(btn);
    click(d.getElementById('topbar-reload'));
    await sleep(5);
    const pop = d.getElementById('sm-reload-pop');
    ok(pop && !pop.hidden, 'Reload, moved into the menu, still opens its own popup');

    // v1.5.4: the attention mark on the closed menu's button
    const tick = () => sleep(5);
    ok(!btn.hasAttribute('data-attention') && btn.querySelector('.topbar-more-dot').hidden, 'nothing needs attention: no mark');
    d.getElementById('diag-tray-slot').innerHTML = '<a class="diag-header-badge diag-error" href="/diagnostics"><span class="tbm-label">Diagnostics</span><span class="tbm-badge">3 issues</span></a>';
    await tick();
    ok(btn.getAttribute('data-attention') === 'error' && !btn.querySelector('.topbar-more-dot').hidden, 'a health error marks the button in the error colour');
    ok(/3 health issues/.test(btn.getAttribute('aria-label')), 'the aria-label names it: ' + btn.getAttribute('aria-label'));
    d.getElementById('diag-tray-slot').innerHTML = '<a class="diag-header-badge diag-ok-badge" href="/diagnostics">healthy</a>';
    const ag = d.getElementById('agent-pill'); ag.hidden = false; ag.setAttribute('data-state', 'waiting');
    await tick();
    ok(btn.getAttribute('data-attention') === 'warning' && /asking to write/.test(btn.getAttribute('aria-label')), 'the agent asking to write marks it as a warning');
    ag.setAttribute('data-state', 'idle');
    const imp = d.getElementById('topbar-import-pill'); imp.hidden = false;
    await tick();
    ok(btn.getAttribute('data-attention') === 'info' && /import/.test(btn.getAttribute('aria-label')), 'a running import marks it as info');
    imp.hidden = true;
    await tick();
    ok(!btn.hasAttribute('data-attention') && btn.querySelector('.topbar-more-dot').hidden && btn.getAttribute('aria-label') === 'More controls', 'all clear: the mark goes');

    console.log((fails ? 'FAILED ' : 'PASSED ') + (asserts - fails) + '/' + asserts);
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
