/* Interactive column controls scope to the current dataset container, including
 * pinned columns. Exercise the real app.js with multiple independent menus.
 * Run: node tests/replot_cols_selfcheck.cjs
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
    <div id="ds-interactive-container">
      <div class="ds-replot-head">${cols()}</div>
      <div class="ds-interactive-list" id="reproduced-list" style="--ds-cols:2"></div>
    </div>
    <div id="peer-ds-interactive-container">
      <div class="ds-interactive-toolbar">${cols()}</div>
      <div class="ds-interactive-list" id="peer-list" style="--ds-cols:2"></div>
    </div>
  </div>
  <div class="dataset-tab-content" id="pinned-ds-tab-interactive">
    <div id="pinned-ds-interactive-container">
      <div class="ds-replot-head">${cols()}</div>
      <div class="ds-interactive-list" id="pinned-reproduced-list" style="--ds-cols:2"></div>
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
    window.setInteractiveCols(3, btn('#ds-interactive-container', 3));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('reproduced-list') === '3', 'a replot button lays out the reproduced list: ' + cssCols('reproduced-list'));
    ok(cssCols('peer-list') === '2', 'and leaves the peer grid alone: ' + cssCols('peer-list'));
    ok(btn('#ds-interactive-container', 3).classList.contains('active'),
       'the replot group marks its own pressed button');
    ok(!btn('#peer-ds-interactive-container', 3).classList.contains('active'),
       'the peer group is not marked by a replot press');

    window.setInteractiveCols(1, btn('#peer-ds-interactive-container', 1));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('peer-list') === '1', 'a peer button still drives the peer grid');
    ok(cssCols('reproduced-list') === '3', 'and never the replot list');

    window.setInteractiveCols(1, btn('#pinned-ds-interactive-container', 1));
    await new Promise(r => setTimeout(r, 20));
    ok(cssCols('pinned-reproduced-list') === '1', 'a pinned replot container scopes to itself');
    ok(cssCols('reproduced-list') === '3' && cssCols('peer-list') === '1',
       'and touches neither unpinned list');

    process.exit(fails ? 1 : 0);
})();
