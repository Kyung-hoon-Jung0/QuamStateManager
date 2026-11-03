/* jsdom selfcheck: the live-undo position a window has SEEN (D2). It follows
 * every tray this window's own actions land (htmx or a fetch caller's swap),
 * never a tray the foreign-edit refresh rendered; /undo declares it.
 * Run: node tests/undo_cursor_seen_selfcheck.cjs (driven by tests/test_undo_live.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms || 0));

const dom = new JSDOM('<!doctype html><html><body><div id="tray-host"><div id="pending-tray" data-undo-cursor="2"></div></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;

window.fetch = global.fetch = async () => ({ headers: { get: () => null }, json: async () => ({}) });
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const d = window.document;
const swap = (c) => { const t = d.createElement('div'); t.id = 'pending-tray'; t.setAttribute('data-undo-cursor', String(c)); d.getElementById('pending-tray').replaceWith(t); };
(async function () {
  await tick(5);
  ok(window.__undoCursorSeen === 2, 'the page load records the cursor it shows');
  swap(3); await tick(5);
  ok(window.__undoCursorSeen === 3, 'a tray this window landed (any way) moves it');
  window.__trayForeignSwap = true; swap(1); await tick(5); window.__trayForeignSwap = false;
  ok(window.__undoCursorSeen === 3, 'a tray the foreign-edit refresh rendered does not');
  const src = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
  ok(/seen_undo_cursor: \(item\.path === "\/undo" && window\.__undoCursorSeen != null\)/.test(src), '/undo declares it');
  process.exit(fails ? 1 : 0);
})();
