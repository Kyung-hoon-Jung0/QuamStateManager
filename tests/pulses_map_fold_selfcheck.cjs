// User-view sweep: opening a pulse inspector folds the chip map so pulse rows stay visible;
// a re-render of an already-open inspector leaves the map as the user set it.
const fs = require('fs'), path = require('path');
const { JSDOM } = require('jsdom');
const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'pulses.js'), 'utf8');
const dom = new JSDOM('<input type="hidden" id="pulses-owner-pick"><details id="component-map" open><summary>m</summary></details><div id="inspector-pane"></div>', { runScripts: 'outside-only' });
const w = dom.window;
w.fetch = () => new w.Promise(() => {});
try { w.eval(src); } catch (e) { /* page-only setup may throw; the fold listener is registered last */ }
let fails = 0; const check = (c, m) => { if (!c) { fails++; console.log('FAIL ' + m); } };
const pane = w.document.getElementById('inspector-pane'), map = w.document.getElementById('component-map');
const swap = html => {
    w.document.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', { detail: { target: pane } }));
    pane.innerHTML = html;
    w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: pane } }));
};
swap('<div id="pulse-detail-root"></div>');
check(!map.open, 'opening a pulse inspector folds the chip map');
map.open = true;
swap('<div id="pulse-detail-root"></div>');
check(map.open, 'a re-render of an open inspector leaves the map open');
swap('<p>x</p>'); map.open = true; swap('<p>y</p>');
check(map.open, 'a non-pulse inspector never folds the map');
console.log(fails ? fails + ' failed' : 'pulses map fold: 3 assertions ok');
process.exit(fails ? 1 : 0);
