/* v1.5.1 (U P2-7): "Compare Selected" keeps a short label; runs that are
 * selected but not in view are said on a line of their own.
 *
 * The button read "Compare Selected (2 — 2 not in view)" and, in its third of
 * the sidebar, broke over five lines. The driver
 * (tests/test_v151_p2_compare_offview.py) renders a REAL full page with runs
 * in the sidebar and the REAL filtered tree (/workspace/tree?name=...); the
 * SHIPPED app.js runs the ticks and the filter swap here.
 *
 * Run: node tests/v151_compare_offview_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 40));

(async function () {
    const body = FX.page.slice(FX.page.indexOf('<body'), FX.page.lastIndexOf('</body>') + 7)
        .replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html>' + body + '</html>',
        { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    w.fetch = () => new Promise(() => {});
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {}, on: () => {}, config: {} };
    w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.eval(APP);
    d.dispatchEvent(new w.Event('DOMContentLoaded'));
    await tick();
    const swapTree = (html) => {
        const tree = d.getElementById('sidebar-tree');
        tree.innerHTML = html;
        tree.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: tree } }));
    };
    swapTree(FX.tree);              // the tree's own fetch, as the page does on load
    await tick();
    const boxes = () => Array.from(d.querySelectorAll('#sidebar-tree input[type="checkbox"][name="paths"]'));
    ok(boxes().length === FX.n_runs, 'the real sidebar lists the runs (' + boxes().length + ')');
    const off = boxes().filter((b) => FX.hidden_by_filter.some((h) => b.value.indexOf(h) >= 0));
    off.forEach((b) => b.click());
    await tick();
    const cmp = d.querySelector('#compare-form .btn-compare');
    ok(cmp.textContent === 'Compare Selected (' + off.length + ')', 'ticked in view: ' + cmp.textContent);
    // the filter swaps the tree for rows that do not include the ticked ones
    swapTree(FX.filtered_tree);
    await tick();
    ok(cmp.textContent === 'Compare Selected (' + off.length + ')',
       'the label stays short with them out of view: ' + cmp.textContent);
    const line = d.getElementById('compare-offview');
    ok(!!line && !line.hidden && line.textContent === off.length + ' of the ' + off.length + ' selected are not in view (filtered or folded away)',
       'their own line says it: ' + (line && line.textContent));
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
