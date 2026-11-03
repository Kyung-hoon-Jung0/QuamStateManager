/* jsdom selfcheck: Enter in the State Load folder box submits the form once,
 * exactly like the State Load button; a highlighted suggestion is taken first.
 * Run: node tests/stateload_enter_selfcheck.cjs (driven by tests/test_stateload_enter.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms || 0));

const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray"></div><form id="load-form"><div class="path-input-group"><input type="text" name="folder" id="load-path-input"></div><button type="submit">State Load</button></form></body></html>',
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
const form = d.getElementById('load-form'), input = d.getElementById('load-path-input');
let submits = 0; form.addEventListener('submit', (e) => { e.preventDefault(); submits++; });
const enter = (mods) => { const ev = new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...(mods || {}) }); input.dispatchEvent(ev); return ev; };
input.value = 'D:/some/quam_state'; input.focus();
const ev = enter();
ok(submits === 1, 'Enter in the folder box submits the State Load form once (' + submits + ')');
ok(ev.defaultPrevented, 'the browser default (a second submit) is suppressed');
// a highlighted suggestion is taken, still one submit
const box = d.createElement('div'); box.className = 'path-suggestions';
box.innerHTML = '<div class="path-suggestion active" data-path="D:/picked/quam_state">picked</div>';
input.parentNode.appendChild(box);
submits = 0; enter();
ok(input.value === 'D:/picked/quam_state' && submits === 1, 'a highlighted suggestion is loaded with one submit');
submits = 0; enter({ shiftKey: true });
ok(submits === 0, 'a modified Enter is not ours');
process.exit(fails ? 1 : 0);
