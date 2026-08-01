/* jsdom selfcheck for the figure lightbox (r13 feedback ⑥).
 *
 * The old toggleFigureZoom only class-toggled the <img> into a fixed overlay —
 * users reported "the popup opens but there is no way to actually zoom in/out".
 * The new lightbox: wheel = cursor-anchored zoom (1–12×), drag = pan,
 * double-click = fit↔250%, +/−/⟲/× buttons with a live % readout,
 * Esc / backdrop-click close, trapFocus containment. Pins:
 *   1. open builds #figure-lightbox on <body> with a CLONED img (grid intact)
 *   2. wheel-up zooms in (transform scale grows; % readout updates)
 *   3. drag pans (translate changes); a drag is NOT treated as a close-click
 *   4. + / − / ⟲ buttons work; ⟲ resets to 100%
 *   5. double-click zooms from fit, resets when zoomed
 *   6. backdrop click closes; × closes; Esc (via trapFocus) closes
 *   7. re-invoking toggleFigureZoom while open closes (toggle semantics)
 *   8. CSS ships the .fig-lightbox block and the legacy .figure-zoomed rule
 *      is gone; the four templates keep calling toggleFigureZoom(this)
 *
 * Run: node tests/figure_lightbox_selfcheck.cjs  (driven by tests/test_web.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const TPL = path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div class="figure-card"><img id="fig1" src="/dataset/x/fig/amp.png" alt="amp"></div>' +
    '</body></html>',
    { url: 'http://localhost/datasets', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
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
const fig = d.getElementById('fig1');

function scaleOf(img) {
    const m = /scale\(([\d.]+)\)/.exec(img.style.transform || '');
    return m ? parseFloat(m[1]) : 1;
}
function translateOf(img) {
    const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(img.style.transform || '');
    return m ? [parseFloat(m[1]), parseFloat(m[2])] : [0, 0];
}

// 1. open
window.toggleFigureZoom(fig);
let box = d.getElementById('figure-lightbox');
ok(!!box, 'lightbox overlay exists after toggleFigureZoom');
ok(box && box.parentElement === d.body, 'overlay mounts on <body>');
const img = box && box.querySelector('.fig-lightbox-img');
ok(!!img && img !== fig, 'the lightbox shows a CLONE, the grid img stays put');
ok(!!img && img.getAttribute('src') === fig.getAttribute('src'), 'clone carries the same src');
ok(!!box.querySelector('.fig-lightbox-hint'), 'interaction hint is visible');
const zoomLabel = box.querySelector('.fig-lightbox-zoom');
ok(zoomLabel && zoomLabel.textContent === '100%', 'readout starts at 100%');

// 2. wheel-up zooms in
img.dispatchEvent(new window.WheelEvent('wheel',
    { deltaY: -240, clientX: 40, clientY: 30, bubbles: true, cancelable: true }));
const s1 = scaleOf(img);
ok(s1 > 1, 'wheel-up zooms in (scale ' + s1.toFixed(2) + ')');
ok(zoomLabel.textContent === Math.round(s1 * 100) + '%', 'readout follows the scale');

// 3. drag pans (delta-based — the wheel zoom above already moved the translate)
const [tx0, ty0] = translateOf(img);
img.dispatchEvent(new window.MouseEvent('pointerdown',
    { clientX: 50, clientY: 50, button: 0, bubbles: true }));
img.dispatchEvent(new window.MouseEvent('pointermove',
    { clientX: 90, clientY: 75, bubbles: true }));
img.dispatchEvent(new window.MouseEvent('pointerup',
    { clientX: 90, clientY: 75, bubbles: true }));
const [tx, ty] = translateOf(img);
ok(Math.round(tx - tx0) === 40 && Math.round(ty - ty0) === 25,
   'drag pans by the pointer delta (' + (tx - tx0) + ',' + (ty - ty0) + ')');
ok(!!d.getElementById('figure-lightbox'), 'a drag is NOT a close-click');

// 4. buttons
const btn = (act) => box.querySelector('.fig-lightbox-btn[data-act="' + act + '"]');
const before = scaleOf(img);
btn('in').click();
ok(scaleOf(img) > before, '+ button zooms in');
btn('out').click();
btn('reset').click();
ok(scaleOf(img) === 1 && translateOf(img).join(',') === '0,0', 'reset returns to fit');
ok(zoomLabel.textContent === '100%', 'readout back to 100%');

// 5. double-click zooms, second double-click resets
img.dispatchEvent(new window.MouseEvent('dblclick',
    { clientX: 10, clientY: 10, bubbles: true }));
ok(scaleOf(img) > 2, 'double-click zooms from fit (' + scaleOf(img) + 'x)');
img.dispatchEvent(new window.MouseEvent('dblclick',
    { clientX: 10, clientY: 10, bubbles: true }));
ok(scaleOf(img) === 1, 'double-click while zoomed resets to fit');

// 6a. backdrop click closes
box.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(!d.getElementById('figure-lightbox'), 'backdrop click closes the lightbox');

// 6b. x button closes
window.toggleFigureZoom(fig);
box = d.getElementById('figure-lightbox');
box.querySelector('.fig-lightbox-btn[data-act="close"]').click();
ok(!d.getElementById('figure-lightbox'), 'x button closes');

// 6c. Escape closes (trapFocus onEscape)
window.toggleFigureZoom(fig);
d.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
ok(!d.getElementById('figure-lightbox'), 'Escape closes');

// 7. toggle semantics
window.toggleFigureZoom(fig);
ok(!!d.getElementById('figure-lightbox'), 're-open works');
window.toggleFigureZoom(fig);
ok(!d.getElementById('figure-lightbox'), 'second toggle call closes');

// 8. source pins
const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
ok(css.indexOf('.fig-lightbox') !== -1, 'lightbox CSS shipped');
ok(css.indexOf('img.figure-zoomed') === -1, 'legacy .figure-zoomed CSS removed');
const appjs = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
ok(appjs.indexOf('img.figure-zoomed') === -1, 'legacy zoom teardown removed');
for (const t of ['_dataset_detail.html', '_dataset_compare.html',
                 '_dataset_interactive.html', '_trends_data.html']) {
    const html = fs.readFileSync(path.join(TPL, t), 'utf8');
    ok(html.indexOf('toggleFigureZoom(this)') !== -1, t + ' keeps the entry point');
}

if (fails) { console.error(fails + ' FAILURES'); process.exit(1); }
console.log('ALL OK');
// Explicit exit (matches ndview_selfcheck): jsdom's pretendToBeVisual RAF
// scheduler keeps the node event loop alive on some platforms (hung 60 s on
// WSL while exiting cleanly on Windows) — never rely on a drained loop.
process.exit(0);
