/* v1.5.1 (3b leftovers): two page readers of the tray read the REAL markup.
 *
 * The tray's rows render only in the sync panel now. Two readers still looked
 * for rows inside #pending-tray that no template renders there:
 *  - the Json Tree pending tint (JT-07) read `.tray-change-path[title]`, so a
 *    re-render, a lazily built row or a redo showed an unapplied value
 *    untinted;
 *  - the tray undo button's tooltip read `.tray-change-item`, so it never
 *    named the edit the next press removes.
 * Both now read what the tray does carry (data-change-paths, and the undo
 * button's data-top-*).
 *
 * This harness builds no tray and no tree: the driver
 * (tests/test_v151_tray_readers.py) renders /state/tray and the /explorer
 * fragment with the Flask test client; the fragment's own inline script
 * builds the tree with the REAL app.js.
 *
 * Run: node tests/v151_tray_readers_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<ul><li id="topbar-tray-slot">' + FX.tray_html + '</li></ul>'
    + '<div id="table-pane">' + FX.explorer_html + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {},
           process: function () {}, on: function () {}, config: {} };
w.fetch = function () { return new Promise(function () {}); };
w.eval(APP);
// the fragment's inline scripts, as the browser would run them after the swap
Array.prototype.forEach.call(w.document.querySelectorAll('#table-pane script'), function (s) {
    if (!s.src) w.eval(s.textContent);
});

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const d = w.document;
function node(p) { return d.querySelector('#explorer-tree-state .tree-node[data-path="' + p + '"]'); }
function openTo(p) {
    const segs = p.split('.');
    for (let i = 1; i <= segs.length; i++) {
        const nd = node(segs.slice(0, i).join('.'));
        const t = nd && nd.querySelector(':scope > .tree-row > .tree-toggle.collapsed');
        if (t) t.click();
    }
    return node(p);
}
function tinted(p) {
    const nd = openTo(p);
    const r = nd && nd.querySelector(':scope > .tree-row');
    return !!(r && r.classList.contains('tree-row-pending'));
}

ok(!!node('qubits'), 'the real /explorer fragment built its tree');
FX.pending.forEach(function (p) { ok(tinted(p), 'a pending path is tinted after a lazy expand: ' + p); });
FX.clean.forEach(function (p) { ok(!tinted(p), 'a clean path is not tinted: ' + p); });
// a re-render (the soft refresh) keeps the tint: no commit site involved
d.dispatchEvent(new w.CustomEvent('sm:tray-swapped'));
ok(tinted(FX.pending[0]), 'a tray swap re-applies the tint from the tray');

const btn = d.getElementById('tray-undo-btn');
ok(!!btn, 'the real tray renders the undo button');
w.LiveEditUndo.refreshTip(btn);
console.log('TIP ' + JSON.stringify(btn.title));
ok(btn.title.indexOf(FX.top_path) >= 0, 'the tooltip names the edit the next press removes: ' + btn.title);
if (FX.top_n > 1) {
    ok(btn.title.indexOf('+' + (FX.top_n - 1) + ' more') >= 0, 'and how many more share that action');
}

console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
process.exit(fails ? 1 : 0);
