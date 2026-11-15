'use strict';
/* The channel tabs are a segmented control: whenever page code moves the active
 * tab (a press, Back, a pane re-entry), the selected look and aria-current must
 * move with it. Drives the real app.js and ui-menu.js under jsdom.
 * Input on stdin: JSON {"all": <_pulses.html with no channel>, "xy": <same with channel xy>}.
 * Run through tests/test_reading_order_small.py. */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const pages = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/explorer', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
try { global.navigator = window.navigator; } catch (e) {}
global.location = window.location;
global.history = window.history;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
try { window.localStorage = global.localStorage; } catch (e) {}
try { window.sessionStorage = global.sessionStorage; } catch (e) {}
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

const stat = f => fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', f), 'utf8');
try { window.eval(stat('app.js')); window.eval(stat('ui-menu.js')); } catch (e) {
    console.error('FAIL: scripts did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;
const tab = ch => [...doc.querySelectorAll('#pulse-channel-tabs a')]
    .find(a => ((a.getAttribute('hx-get').match(/channel=([^&]*)/) || [])[1] || '') === ch);
// exactly one tab carries the selected look, aria-current and active, and it is `ch`
function oneSelected(ch, what) {
    const on = [...doc.querySelectorAll('#pulse-channel-tabs .ui-segmented-on')];
    const cur = [...doc.querySelectorAll('#pulse-channel-tabs [aria-current]')];
    const act = [...doc.querySelectorAll('#pulse-channel-tabs a.active')];
    ok(on.length === 1 && on[0] === tab(ch), what + ': the selected look sits on ' + (ch || 'All') + ' (got ' + on.map(x => x.textContent) + ')');
    ok(cur.length === 1 && cur[0] === tab(ch) && cur[0].getAttribute('aria-current') === 'true', what + ': aria-current sits on the same tab');
    ok(act.length === 1 && act[0] === tab(ch), what + ': active sits on the same tab');
}

// 1. a press through the page function (not the capture click handler)
doc.body.innerHTML = pages.all;
window.history.replaceState({}, '', '/pulses');
oneSelected('', 'initial render');
window.pulseTabActive(tab('xy'));
oneSelected('xy', 'pulseTabActive');
window.pulseTabActive(tab('z'));
oneSelected('z', 'pulseTabActive again');

// 2. Back (popstate restore) moves the look to the tab the URL names
window.htmx.ajax = () => Promise.resolve();
window.history.replaceState({}, '', '/pulses?channel=xy');
window._pulsesRestoreFromUrl();
oneSelected('xy', 'Back to xy');
window.history.replaceState({}, '', '/pulses');
window._pulsesRestoreFromUrl();
oneSelected('', 'Back to All');
window.history.replaceState({}, '', '/pulses?channel=resonator');
window._pulsesRestoreFromUrl();
oneSelected('resonator', 'Back to resonator');

// 3. pane re-entry: the parked filter is re-applied onto a fresh default render
doc.body.innerHTML = '<div id="pending-tray" data-seq="7"></div><div id="table-pane"><div id="stub">x</div></div>';
window.__chipToken = 'chipA';
const pane = () => doc.getElementById('table-pane');
function swapTo(pathTo, html) {
    const before = new window.CustomEvent('htmx:beforeSwap', { cancelable: true, detail: { shouldSwap: true, pathInfo: { finalRequestPath: pathTo } } });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    pane().innerHTML = html;
    const after = new window.CustomEvent('htmx:afterSwap', { detail: { pathInfo: { finalRequestPath: pathTo } } });
    Object.defineProperty(after, 'target', { value: pane() });
    doc.dispatchEvent(after);
}
const seq = n => doc.getElementById('pending-tray').setAttribute('data-seq', String(n));
window.history.replaceState({}, '', '/explorer');
swapTo('/pulses', pages.xy);
seq(8);
swapTo('/bulk', '<div>bulk</div>');
seq(9);   // an edit makes the parked copy stale: the soft tier re-applies the filter
swapTo('/pulses', pages.all);
oneSelected('xy', 're-entry');
if (fails) { console.error(fails + ' failed'); process.exitCode = 1; }
window.close();
