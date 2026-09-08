/* Customer feedback 2026-09-08 — explore the data by keyboard alone: click a
 * run in the LEFT list, then ↑ / ↓ open the previous / next run (PgUp / PgDn
 * step 10), the detail following each press. In the REAL app.js under jsdom:
 * the clicked entry keeps focus, the arrows walk the VISIBLE entries relative
 * to the OPEN run, focus follows the run that opened (so the next press still
 * finds a focused entry), hidden entries are skipped, the end falls back to
 * the server neighbor, and nothing fires from a text field, from outside the
 * list, with a modifier, or while no detail is open.
 *
 * Run: node tests/ds_arrow_nav_selfcheck.cjs  (driven by tests/test_ds_flow.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/datasets', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;                 // app.js reads bare `CSS` (docs/113 harness rule)
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
const fetches = [];
global.fetch = (url) => { fetches.push(String(url)); return Promise.resolve({ json: () => Promise.resolve({}) }); };
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
// jsdom lays nothing out: offsetParent is always null and scrollIntoView is
// absent. dsNavRun keeps only entries whose offsetParent is non-null (the
// visible ones), so give the harness the browser's answer: hidden subtree -> null.
Object.defineProperty(window.HTMLElement.prototype, 'offsetParent', {
    get() { return (this.hidden || (this.closest && this.closest('[hidden]'))) ? null : this.parentElement; },
    configurable: true,
});
window.Element.prototype.scrollIntoView = function () {};
// the run loads: what the click handler / dsNavRun ask htmx for, and the detail
// root following it (the server would render a new #ds-detail-root)
const loads = [];
window.htmx = {
    ajax: (m, url) => {
        loads.push(url);
        const root = window.document.getElementById('ds-detail-root');
        if (root) root.setAttribute('data-uid', url.split('/').pop());
        return Promise.resolve();
    },
    trigger: () => {}, process: () => {},
};
global.htmx = window.htmx;

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function key(k, opts) {
    const ev = new window.KeyboardEvent('keydown', Object.assign(
        { key: k, bubbles: true, cancelable: true }, opts || {}));
    (opts && opts.target ? opts.target : (window.document.activeElement || window.document)).dispatchEvent(ev);
    return ev;
}
function entry(uid, runId) {
    return '<li class="tree-entry"><div class="tree-entry-label">'
        + '<span class="tree-entry-click" data-folder-path="/d" data-run-id="' + runId + '" data-uid="' + uid + '" tabindex="0" role="button">'
        + '<span class="run-id">#' + runId + '</span> <span class="entry-name">rabi</span></span></div></li>';
}

const doc = window.document;
doc.body.innerHTML =
    '<div id="sidebar"><input type="search" id="sidebar-search">'
    + '<details open><summary>2026-09-08</summary><ul class="tree-entries">'
    + entry('u3', 3) + entry('u2', 2) + entry('u1', 1)
    + '</ul></details></div>'
    + '<div id="table-pane"></div>'
    + '<div id="inspector-pane"><div id="ds-detail-root" data-uid=""></div></div>';
const E = (uid) => doc.querySelector('.tree-entry-click[data-uid="' + uid + '"]');
const active = () => (doc.querySelector('.tree-entry-click.tree-entry-active') || {}).getAttribute
    ? doc.querySelector('.tree-entry-click.tree-entry-active').getAttribute('data-uid') : null;

// 1. click a run in the left list: it opens, is marked, and KEEPS the keyboard
E('u2').click();
ok(loads[loads.length - 1] === '/dataset/u2', 'clicking a run entry loads its detail');
ok(active() === 'u2', 'the clicked run is marked active');
ok(doc.activeElement === E('u2'), 'the clicked entry has the keyboard (focus)');

// 2. ↓ opens the NEXT visible run and moves the keyboard onto it; ↑ the previous
let ev = key('ArrowDown');
ok(ev.defaultPrevented, '↓ on a focused run entry is taken (no page scroll)');
ok(loads[loads.length - 1] === '/dataset/u1' && active() === 'u1', '↓ opens the next run in the list (u2 -> u1)');
ok(doc.activeElement === E('u1'), 'focus followed the run that opened');
key('ArrowUp'); key('ArrowUp');
ok(loads[loads.length - 1] === '/dataset/u3' && active() === 'u3', '↑ ↑ walks back up (u1 -> u2 -> u3)');
ok(doc.activeElement === E('u3'), 'focus is on the top entry now');

// 3. the top: ↑ falls back to the server neighbor (a date group above may hold more)
fetches.length = 0;
ev = key('ArrowUp');
ok(ev.defaultPrevented && fetches.some((u) => u === '/dataset/u3/neighbor?dir=-1'),
   'at the top ↑ asks the server for the neighbor instead of doing nothing');

// 4. PgDn steps 10 and clamps to the last entry
key('PageDown');
ok(loads[loads.length - 1] === '/dataset/u1' && doc.activeElement === E('u1'), 'PgDn = 10 steps, clamped to the last run');
key('PageUp');
ok(loads[loads.length - 1] === '/dataset/u3' && doc.activeElement === E('u3'), 'PgUp = 10 steps, clamped to the first run');

// 5. a hidden entry (collapsed group / filtered out) is skipped
E('u2').closest('li').hidden = true;
key('ArrowDown');
ok(loads[loads.length - 1] === '/dataset/u1' && active() === 'u1', '↓ skips a hidden entry (u3 -> u1)');
E('u2').closest('li').hidden = false;

// 5b. the next run lives in a CLOSED date group below: ↓ opens that group and lands on the row
doc.querySelector('ul.tree-entries').closest('details').insertAdjacentHTML('afterend',
    '<details><summary>2026-09-07</summary><ul class="tree-entries">' + entry('u0', 0) + '</ul></details>');
const grp = E('u0').closest('details');
ok(grp.open === false, '(fixture) the older date group starts closed');
E('u1').focus(); key('ArrowDown');
ok(loads[loads.length - 1] === '/dataset/u0' && active() === 'u0', '↓ from the last row of a group moves into the next group (u1 -> u0)');
ok(grp.open === true, 'the closed date group was opened so the row is on screen');
ok(doc.activeElement === E('u0'), 'focus is on the row in the opened group');
key('ArrowUp');
ok(active() === 'u1' && doc.activeElement === E('u1'), '↑ walks back out of it (u0 -> u1)');
grp.remove();

// 6. the gates: never from a text field, never from outside the list, never with a modifier, never without a detail
const before = loads.length;
doc.getElementById('sidebar-search').focus();
ev = key('ArrowDown');
ok(!ev.defaultPrevented && loads.length === before, '↓ inside the search box is not hijacked');
doc.getElementById('sidebar-search').blur();
doc.getElementById('table-pane').setAttribute('tabindex', '-1');
doc.getElementById('table-pane').focus();
ev = key('ArrowDown');
ok(!ev.defaultPrevented && loads.length === before, '↓ with the keyboard in the main pane still scrolls (nothing loads)');
E('u1').focus();
ev = key('ArrowDown', { shiftKey: true });
ok(!ev.defaultPrevented && loads.length === before, 'Shift+↓ is left alone');
ev = key('ArrowUp', { ctrlKey: true });
ok(!ev.defaultPrevented && loads.length === before, 'Ctrl+↑ is left alone');
doc.getElementById('ds-detail-root').remove();
ev = key('ArrowUp');
ok(!ev.defaultPrevented && loads.length === before, 'with no detail open the arrows do nothing');

// 7. the anywhere-shortcut [ ] is unchanged (needs a detail; no focus requirement)
doc.getElementById('inspector-pane').innerHTML = '<div id="ds-detail-root" data-uid="u1"></div>';
doc.getElementById('table-pane').focus();
ev = key('[');
ok(ev.defaultPrevented && loads[loads.length - 1] === '/dataset/u2', '[ still steps to the previous run from anywhere');

process.exit(fails ? 1 : 0);
