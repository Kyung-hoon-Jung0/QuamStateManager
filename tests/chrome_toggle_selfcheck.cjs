/* jsdom selfcheck for the ☰ chrome toggle (customer feedback 2026-08-27):
 * ONE press collapses the sidebar AND the top bar together (docs/126's
 * three-state cycle made users press twice); one press restores both. Any
 * mixed state reached through the individual toggles resolves the same way:
 * anything still visible ⇒ collapse everything; nothing visible ⇒ restore
 * everything. Both legs persist through their own localStorage keys.
 *
 * Run: node tests/chrome_toggle_selfcheck.cjs   (driven by tests/test_chrome_toggle.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body><div class="app-layout"><aside id="sidebar"></aside>' +
    '<div id="content-area"></div></div></body></html>',
    { url: 'http://localhost/qubits', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
const store = {};
global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
const layout = d.querySelector('.app-layout');
const sb = () => layout.classList.contains('sidebar-collapsed');
const tb = () => d.documentElement.classList.contains('topbar-hidden');

// 1. one press from everything-visible collapses BOTH
ok(!sb() && !tb(), 'starts with everything visible');
window.cycleChrome();
ok(sb() && tb(), 'ONE press collapses the sidebar AND the top bar');
ok(store.quam_sidebar_collapsed === '1' && store.quam_topbar_hidden === '1',
   'both legs persist ("1" / "1")');

// 2. the next press (the floating ☰) restores BOTH
window.cycleChrome();
ok(!sb() && !tb(), 'the next press restores both');
ok(store.quam_sidebar_collapsed === '0' && store.quam_topbar_hidden === '0',
   'both legs persist the restore ("0" / "0")');

// 3. mixed states reached through the individual toggles: anything still
//    visible ⇒ collapse everything (never a partial leg, never a flip-flop)
window.toggleSidebar();                       // sidebar only
ok(sb() && !tb(), 'mixed: sidebar collapsed, top bar visible');
window.cycleChrome();
ok(sb() && tb(), 'mixed (sidebar only) → one press collapses the rest');
window.cycleChrome();                         // back to all visible
window.toggleTopbar();                        // top bar only
ok(!sb() && tb(), 'mixed: top bar hidden, sidebar visible');
window.cycleChrome();
ok(sb() && tb(), 'mixed (top bar only) → one press collapses the rest');

// 4. the entry-point titles say what one press does now
const base = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
ok(/title="Collapse the sidebar and the top bar/.test(base),
   'the ☰ title promises one-press collapse of both');
ok(base.indexOf('then the top bar') === -1, 'the old two-step wording is gone');

process.exit(fails ? 1 : 0);
