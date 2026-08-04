/* Emits window.ValueDelta's output for a case table as JSON, so
 * tests/test_value_delta.py can diff it against core/value_delta.py.
 *
 * The two implementations must agree CHARACTER FOR CHARACTER: the same delta
 * is rendered server-side (Review tray, sync screen, diff tables) and
 * client-side (plot-apply popup, bulk grid, FSP popup), often on the same
 * screen, and a formatting drift between them would read as a data
 * discrepancy.
 *
 * Usage:  node tests/value_delta_parity.cjs <cases.json>   -> JSON on stdout
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const casesPath = process.argv[2];
if (!casesPath) { console.error('usage: value_delta_parity.cjs <cases.json>'); process.exit(2); }
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));

const dom = new JSDOM('<!doctype html><html><body></body></html>',
                      { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.navigator = window.navigator;
global.location = window.location;
function mkStorage() {
    const m = new Map();
    return { getItem: (k) => (m.has(k) ? m.get(k) : null),
             setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}
global.localStorage = window.localStorage = mkStorage();
global.sessionStorage = window.sessionStorage = mkStorage();
global.requestAnimationFrame = window.requestAnimationFrame = (f) => setTimeout(f, 0);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = window.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
global.ResizeObserver = window.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.Element.prototype.getClientRects = function () { return [{}]; };
window.htmx = { ajax() {}, trigger() {}, process() {} };
window.fetch = global.fetch = function () {
    return Promise.resolve({ status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
};

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const out = cases.map(function (pair) {
    const d = window.ValueDelta.compute(pair[0], pair[1]);
    if (!d) return null;
    return { text: d.text, pct_text: d.pct_text, dir: d.dir,
             coerced: d.coerced, title: d.title };
});
process.stdout.write(JSON.stringify(out));
process.exit(0);
