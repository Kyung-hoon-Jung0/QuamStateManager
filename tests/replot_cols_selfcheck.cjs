/* datasets-r2-22 — the reproduced-figure list's own "Columns 1 2 3" buttons
 * lay out THAT list. setInteractiveCols scoped with
 * `closest('[id$="interactive-container"]')`, which a button inside
 * #ds-replot-container never matches, so it fell back to
 * #ds-interactive-container: a click re-laid the RECIPE grid beside it and
 * left the replot list untouched. The REAL app.js under jsdom: a replot button
 * sets --ds-cols + the active mark on the replot list only; a recipe button
 * still drives the recipe grid only; a pinned ("pinned-"-prefixed) replot
 * container scopes to itself too.
 *
 * Run: node tests/replot_cols_selfcheck.cjs  (driven by tests/test_interactive_replot.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

function cols(n) {
    return '<div class="ds-interactive-colbtns">'
        + [1, 2, 3].map(function (k) {
            return '<button type="button" class="ds-col-btn" data-cols="' + k + '">' + k + '</button>';
        }).join('') + '</div>';
}
const dom = new JSDOM(`<!doctype html><html><body>
  <div class="dataset-tab-content" id="ds-tab-interactive">
    <div id="ds-replot-container">
      <div class="ds-replot-head">${cols()}</div>
      <div class="ds-interactive-list" id="replot-list" style="--ds-cols:2"></div>
    </div>
    <div id="ds-interactive-container">
      <div class="ds-interactive-toolbar">${cols()}</div>
      <div class="ds-interactive-list" id="recipe-list" style="--ds-cols:2"></div>
    </div>
  </div>
  <div class="dataset-tab-content" id="pinned-ds-tab-interactive">
    <div id="pinned-ds-replot-container">
      <div class="ds-replot-head">${cols()}</div>
      <div class="ds-interactive-list" id="pinned-replot-list" style="--ds-cols:2"></div>
    </div>
  </div>
</body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
// Node 24 makes these getter-only on globalThis; under 'use strict' a plain
// write throws, so define rather than assign.
function bridge(name, value) {
    try { global[name] = value; }
    catch (e) {
        try { Object.defineProperty(global, name, { value: value, configurable: true }); }
        catch (e2) { /* the realm already provides it */ }
    }
}
bridge('navigator', window.navigator);
bridge('location', window.location);
bridge('localStorage', window.localStorage);   // jsdom's own (the URL gives it an origin)
bridge('sessionStorage', window.sessionStorage);
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const doc = window.document;
const cssCols = (id) => doc.getElementById(id).style.getPropertyValue('--ds-cols').trim();
const btn = (sel, n) => doc.querySelector(sel + ' .ds-col-btn[data-cols="' + n + '"]');

(async () => {
    window.setInteractiveCols(3, btn('#ds-replot-container', 3));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('replot-list') === '3', 'a replot button lays out the REPLOT list: ' + cssCols('replot-list'));
    ok(cssCols('recipe-list') === '2', 'and leaves the recipe grid alone: ' + cssCols('recipe-list'));
    ok(btn('#ds-replot-container', 3).classList.contains('active'),
       'the replot group marks its own pressed button');
    ok(!btn('#ds-interactive-container', 3).classList.contains('active'),
       'the recipe group is not marked by a replot press');

    window.setInteractiveCols(1, btn('#ds-interactive-container', 1));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('recipe-list') === '1', 'a recipe button still drives the recipe grid');
    ok(cssCols('replot-list') === '3', 'and never the replot list');

    window.setInteractiveCols(1, btn('#pinned-ds-replot-container', 1));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('pinned-replot-list') === '1', 'a pinned replot container scopes to itself');
    ok(cssCols('replot-list') === '3' && cssCols('recipe-list') === '1',
       'and touches neither unpinned list');

    process.exit(fails ? 1 : 0);
})();
