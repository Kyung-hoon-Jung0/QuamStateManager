/* QA diagnostics-r2-11 -- an open page learns that the live chip moved:
 * the drift poll re-renders the #live-diverged-slot banner (Review & sync /
 * Take live / Keep mine) when its verdict and the slot disagree. Driven
 * through the REAL app.js (window._onDriftDivergedBanner, the poll's hook).
 *
 * Real Chrome before the fix: an outside write to the rig chip, /diagnostics
 * open -- the pill turned "Live chip moved" within 8 s, the banner never came
 * (38 s watched) although /state/drift said live_diverged:true throughout.
 *
 * Run: node tests/drift_banner_follow_selfcheck.cjs   (needs jsdom)
 */

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error("Cannot find module 'jsdom'"); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/diagnostics', pretendToBeVisual: true,
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
const calls = [];
window.htmx = { ajax: (m, url, o) => { calls.push([m, url, o && o.target]); return Promise.resolve(); },
                process: () => {}, trigger: () => {} };
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
const doc = window.document;
const slot = doc.createElement('div');
slot.id = 'live-diverged-slot';
doc.body.appendChild(slot);
const hook = window._onDriftDivergedBanner;
const banner = () => calls.filter((c) => c[1] === '/state/diverged-banner');

(async function main() {
    ok(typeof hook === 'function', 'setup: the drift poll exposes its banner hook');
    ok(hook({ live_diverged: true }) === true, 'r2-11: live diverged + an empty slot -> the banner is fetched');
    ok(banner().length === 1 && banner()[0][2] === '#live-diverged-slot',
       'r2-11: ...into #live-diverged-slot (' + JSON.stringify(calls) + ')');
    await sleep(10);
    slot.innerHTML = '<div id="live-diverged-banner">moved</div>';
    ok(hook({ live_diverged: true }) === false && banner().length === 1,
       'r2-11: a banner already up is not re-fetched on every poll');
    ok(hook({ live_diverged: false }) === true && banner().length === 2,
       'r2-11: a verdict back to clean takes the shown banner down the same way');
    await sleep(10);
    slot.innerHTML = '';
    ok(hook({ live_diverged: true, auto_pull: true }) === false,
       'r2-11: not while Auto-Sync is pulling (it repaints the slot itself)');
    window._applyInFlight = true;
    ok(hook({ live_diverged: true }) === false, 'r2-11: not while an apply is in flight');
    window._applyInFlight = false;
    ok(hook({ tracked: false }) === false, 'r2-11: no verdict, no request');

    // the wiring: the drift poll itself calls the hook with its payload
    calls.length = 0;
    slot.innerHTML = '';
    const payload = { ok: true, tracked: true, count: 3, live_diverged: true, edit_seq: 's1' };
    const fake = (url) => Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    window.fetch = fake; global.fetch = fake;
    doc.dispatchEvent(new window.CustomEvent('liveDriftChanged'));
    await sleep(50);
    ok(banner().length === 1, 'r2-11: a drift poll that reports live_diverged fetches the banner (' + JSON.stringify(calls) + ')');
    console.log(fails ? 'FAILED ' + fails : 'drift_banner_follow_selfcheck: all checks passed');
    process.exit(fails ? 1 : 0);
})();
