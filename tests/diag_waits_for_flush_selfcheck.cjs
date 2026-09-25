/* QA liveedit F14 -- the diagnostics refresh an edit announces waits for the
 * Auto-Sync flush that the same edit started, driven through the REAL app.js.
 *
 * Measured on the 5Q rig in real Chrome: the edit's `diagnostics-changed`
 * fan-out (findings, banners, type alarm) landed on the server while the
 * flush to the live chip was still running and competed with it, so the tray
 * kept "Working state · 1 unsaved" well after state.json was written. The
 * refresh is advisory: it now waits (bounded) while window._applyInFlight.
 *
 * Run: node tests/diag_waits_for_flush_selfcheck.cjs   (needs jsdom)
 */

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error("Cannot find module 'jsdom'"); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/bulk', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.history = window.history;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
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
const fired = [];
window.htmx = { ajax: () => Promise.resolve(), process: () => {},
                trigger: (el, name) => { fired.push([name, Date.now()]); } };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const diag = () => fired.filter((f) => f[0] === 'diagnostics-changed').length;

(async function main() {
    ok(typeof window._diagChanged === 'function', 'setup: app.js defines _diagChanged');

    // control: no flush in flight -> fires after its 350 ms debounce
    window._applyInFlight = false;
    window._diagChanged();
    await sleep(500);
    ok(diag() === 1, 'control: with nothing in flight the refresh fires after the debounce (' + diag() + ')');

    // a flush in flight: the refresh waits for it...
    fired.length = 0;
    window._applyInFlight = true;
    window._diagChanged();
    await sleep(700);
    ok(diag() === 0, 'F14: while the Auto-Sync flush is in flight the diagnostics refresh waits (' + diag() + ')');
    // ...and fires once the flush has answered
    window._applyInFlight = false;
    await sleep(250);
    ok(diag() === 1, 'F14: it fires once the flush is done (' + diag() + ')');

    // bounded: a latch that is never released cannot swallow the refresh
    fired.length = 0;
    window._applyInFlight = true;
    window._diagChanged();
    await sleep(7500);
    ok(diag() === 1, 'F14: a latch that never clears still lets the refresh through within ~5 s (' + diag() + ')');
    window._applyInFlight = false;

    console.log(fails ? 'FAILED ' + fails : 'diag_waits_for_flush_selfcheck: all checks passed');
    process.exit(fails ? 1 : 0);
})();
