/* jsdom selfcheck for docs/141 4x: enhanceColumnResize moves ONE column.
 * Under table-layout:fixed the table kept width:100%, so a shrunk column's
 * space was handed to every other column (Pulses: drag WAVEFORM narrower,
 * OWNER / CHANNEL / OPERATION widen). Now the table is the sum of its
 * columns once any column is under manual control. jsdom has no layout, so
 * offsetWidth is stubbed per <th>.
 * Run: node tests/col_resize_selfcheck.cjs   (driven by tests/test_col_resize.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><table id="pulses-table"><thead><tr><th>OWNER</th><th>CHANNEL</th><th>WAVEFORM</th><th>LENGTH</th></tr></thead><tbody><tr><td>q1</td><td>xy</td><td>~</td><td>40</td></tr></tbody></table></body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
const table = d.getElementById('pulses-table');
const ths = Array.from(table.querySelectorAll('thead th'));
const NATURAL = [120, 200, 400, 80];
ths.forEach((th, i) => Object.defineProperty(th, 'offsetWidth', { get: () => (parseFloat(th.style.width) || NATURAL[i]), configurable: true }));
const widths = () => ths.map((th) => th.style.width);
const KEY = 'quam_test_col_widths';

window.enhanceColumnResize('pulses-table', KEY);
ok(table.style.tableLayout === 'fixed', 'the table is frozen to fixed layout');
ok(widths().join() === '120px,200px,400px,80px', 'every column pinned at its natural width: ' + widths().join());
ok(table.style.width === '', 'with nothing saved the table still fills the pane (no px width)');
ok(ths.every((th) => th.querySelector('.col-resize-handle')), 'each header carries a handle');

// drag WAVEFORM (col 2) 150 px narrower
function drag(i, dx) {
    const h = ths[i].querySelector('.col-resize-handle');
    h.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, clientX: 1000 }));
    d.dispatchEvent(new window.MouseEvent('mousemove', { bubbles: true, clientX: 1000 + dx }));
    d.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));
}
drag(2, -150);
ok(ths[2].style.width === '250px', 'the dragged column is 400 -> 250 px');
ok(ths[0].style.width === '120px' && ths[1].style.width === '200px' && ths[3].style.width === '80px', 'the other three columns did not move');
ok(table.style.width === '650px', 'the table is exactly the sum of its columns (650 px), not 100%: ' + table.style.width);
const saved = JSON.parse(store[KEY]);
ok(saved['2'] === 250 && Object.keys(saved).length === 1, 'only the dragged column is persisted');

// widen OWNER: again one column
drag(0, 30);
ok(widths().join() === '150px,200px,250px,80px' && table.style.width === '680px', 'a second drag moves only its own column, table follows the sum');

// double-click clears that column and releases the table width (auto-fit to the pane)
const h2 = ths[2].querySelector('.col-resize-handle');
h2.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true }));
ok(ths[2].style.width === '' && table.style.width === '', 'dblclick clears the column and the table width');
ok(!('2' in JSON.parse(store[KEY])) && JSON.parse(store[KEY])['0'] === 150, 'the cleared column leaves the store, the other stays');
// the next drag re-freezes the cleared column at its (stubbed) laid-out width and pins the table again
drag(3, 20);
ok(ths[2].style.width === '400px' && ths[3].style.width === '100px' && table.style.width === '850px', 'the next drag re-freezes the cleared column and re-derives the sum');

// a fresh render with saved widths comes back pinned AND summed (saved widths only stick when the table is their sum)
const t2 = d.createElement('table'); t2.id = 't2';
t2.innerHTML = '<thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead><tbody><tr><td></td><td></td><td></td><td></td></tr></tbody>';
d.body.appendChild(t2);
const ths2 = Array.from(t2.querySelectorAll('thead th'));
ths2.forEach((th, i) => Object.defineProperty(th, 'offsetWidth', { get: () => (parseFloat(th.style.width) || NATURAL[i]), configurable: true }));
window.enhanceColumnResize('t2', KEY);
ok(ths2.map((th) => th.style.width).join() === '150px,200px,400px,100px', 'a re-render restores the saved widths and freezes the rest');
ok(t2.style.width === '850px', 'and pins the table to their sum so the saved widths actually hold');
const t3 = d.createElement('table'); t3.id = 't3'; t3.innerHTML = t2.innerHTML; d.body.appendChild(t3);
Array.from(t3.querySelectorAll('thead th')).forEach((th, i) => Object.defineProperty(th, 'offsetWidth', { get: () => NATURAL[i], configurable: true }));
window.enhanceColumnResize('t3', 'quam_test_other_key');
ok(t3.style.width === '', 'a table with nothing saved is left at the pane width');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (16 assertions)');
process.exit(fails ? 1 : 0);
