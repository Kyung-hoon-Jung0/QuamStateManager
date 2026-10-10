/* v1.3.0 B1 P2: a Column History value equal to the cell's value never marks
 * the cell edited.
 *
 * The grid shows a number grouped ("5,075,187,484.52453"); Column History fills
 * it plain ("5075187484.52453"). The old string compare saw two values, typed
 * the plain one in, and the cell turned dirty (the navigation guard then asked
 * about an edit nobody made). Runs the shipped app.js under jsdom.
 *
 * Run: node tests/v130_colhist_same_value_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const GROUPED = '5,075,187,484.52453';
const HTML = '<!doctype html><html><head></head><body><table class="bulk-table"><thead><tr>' +
    '<th data-col-key="f_01"><button class="bulk-col-hist" data-grid="qubit" data-label="f_01">h</button></th>' +
    '</tr></thead><tbody>' +
    '<tr data-qubit="q1"><td data-col-key="f_01"><input class="bulk-cell" data-dot-path="qubits.q1.f_01" value="' + GROUPED + '" data-orig="' + GROUPED + '"></td></tr>' +
    '<tr data-qubit="q2"><td data-col-key="f_01"><input class="bulk-cell" data-dot-path="qubits.q2.f_01" value="4,900,000,000" data-orig="4,900,000,000"></td></tr>' +
    '</tbody></table></body></html>';
const dom = new JSDOM(HTML, { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'], [window, 'localStorage'], [window, 'sessionStorage']]
    .forEach(function (t) { Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true }); });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
global.URLSearchParams = window.URLSearchParams;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

// the panel the server answers: run 0 holds q1's current value and a new q2 value
const PANEL = '<div class="ch-view ch-view-byrun"><table><tr>' +
    '<th class="ch-run"><button class="ch-use-all" data-run-index="0">Use all</button></th></tr>' +
    '<tr data-row="q1"><td class="ch-val" data-row="q1" data-run-index="0" data-fill="5075187484.52453">a</td></tr>' +
    '<tr data-row="q2"><td class="ch-val" data-row="q2" data-run-index="0" data-fill="4950000000">b</td></tr>' +
    '</table></div>';
function fetchStub() {
    return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(PANEL),
                             json: () => Promise.resolve({}) });
}
global.fetch = fetchStub;
Object.defineProperty(window, 'fetch', { value: fetchStub, configurable: true, writable: true });

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

// a page's script realm sees window properties as bare globals; this harness must too
global.LiveEditUndo = window.LiveEditUndo;
global.ValueDelta = window.ValueDelta;
const toasts = [];
window.showToast = function (m) { toasts.push(String(m)); };
global.showToast = window.showToast;

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const cell = (q) => window.document.querySelector('.bulk-cell[data-dot-path="qubits.' + q + '.f_01"]');

(async function main() {
    const btn = window.document.querySelector('.bulk-col-hist');
    window.ColumnHistory.open(btn);
    await wait(30);
    const td1 = window.document.querySelector('.ch-card td.ch-val[data-row="q1"]');
    const td2 = window.document.querySelector('.ch-card td.ch-val[data-row="q2"]');
    ok(td1 && td2, 'the panel rendered its cells');

    window.ColumnHistory.useValue(td1);
    ok(cell('q1').value === GROUPED, 'the same value in another spelling leaves the cell as it was (got ' + cell('q1').value + ')');
    ok(cell('q1').value === cell('q1').getAttribute('data-orig'), 'the cell is not edited');
    ok(toasts.some((t) => /already this value/.test(t)), 'it says the cell already holds it (got ' + JSON.stringify(toasts) + ')');

    // control: a different value still fills
    window.ColumnHistory.useValue(td2);
    ok(cell('q2').value === '4950000000', 'a different value fills the cell (got ' + cell('q2').value + ')');

    // Use all: only the row whose value differs is filled
    cell('q2').value = '4,900,000,000';
    toasts.length = 0;
    window.ColumnHistory.useAll(window.document.querySelector('.ch-card .ch-use-all'));
    ok(cell('q1').value === GROUPED && cell('q2').value === '4950000000',
       'Use all fills only the cell whose value differs (q1 ' + cell('q1').value + ', q2 ' + cell('q2').value + ')');
    ok(toasts.some((t) => /Filled 1 cell/.test(t)), 'and counts one fill (got ' + JSON.stringify(toasts) + ')');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v130_colhist_same_value_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
