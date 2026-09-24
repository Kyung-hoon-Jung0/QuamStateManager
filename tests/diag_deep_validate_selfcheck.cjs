/* jsdom selfcheck, QA diagnostics-r2-03: a failed "Validate deeply (Quam.load)"
 * must SHOW its reason. /config/regenerate answers 502 (400 with no env) with
 * an explanatory _config_status.html body; htmx 2.x drops 4xx/5xx bodies unless
 * app.js's htmx:beforeSwap allowance opts the target in, and it only does so
 * for `.config-status-host` targets. The Diagnostics slot was not one, so the
 * body -- naming the failing path -- was dropped and a generic "please try
 * again" toast appeared instead.
 *
 * Pins, against the SHIPPED template markup + the REAL app.js handler:
 *  1. the button's hx-target is the slot, and the slot carries the host class
 *  2. a 502 (and a 400) into that slot is swapped, not treated as an error
 *
 * Run: node tests/diag_deep_validate_selfcheck.cjs
 *      (driven by tests/test_diag_deep_validate.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const TPL = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates',
                                      '_diagnostics_env.html'), 'utf8');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const btn = TPL.match(/<button[^>]*hx-post="\/config\/regenerate"[^>]*>/);
ok(!!btn, 'the Validate deeply button posts /config/regenerate');
const tgt = btn && btn[0].match(/hx-target="#([\w-]+)"/);
ok(!!tgt, 'the button names an hx-target');
const slotRe = new RegExp('<div id="' + (tgt ? tgt[1] : 'diag-env-deep') + '"[^>]*></div>');
const slot = TPL.match(slotRe);
ok(!!slot, 'the target slot is in the same template (' + (tgt && tgt[1]) + ')');

const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' +
    (slot ? slot[0] : '') + '</div><div id="status-bar"></div></body></html>',
    { url: 'http://localhost/diagnostics', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.URLSearchParams = window.URLSearchParams;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.fetch = global.fetch = () => new Promise(() => {});
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
window.eval(fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const el = window.document.getElementById(tgt ? tgt[1] : 'diag-env-deep');
ok(!!el, 'the slot rendered');
[502, 400].forEach(function (status) {
    const detail = { target: el, xhr: { status: status }, shouldSwap: false, isError: true };
    el.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: detail }));
    ok(detail.shouldSwap === true, 'a ' + status + ' explanation is SWAPPED into the slot');
    ok(detail.isError === false, 'a ' + status + ' is not reported as a failed action (no generic toast)');
});

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK');
process.exit(0);
