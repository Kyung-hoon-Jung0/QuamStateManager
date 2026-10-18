/* v1.5.1 (U P2-18): the Health tile and the "Needs attention" chip agree on
 * the oldest calibration.
 *
 * The tile ("oldest calibration 2 days ago") reads the topology summary,
 * which spans qubits AND pairs; the chip read qubits only and named
 * "q1 today -- oldest calibration" beside it. The driver
 * (tests/test_v151_p2_oldest_calibration.py) renders the REAL /topology
 * health fragment (its inline mount script carries the topology); the
 * SHIPPED app.js + topo-graph.js + chip-status.js run it here.
 *
 * Run: node tests/v151_oldest_calibration_selfcheck.cjs <fixture.json>
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
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 30));

(async function () {
    const scripts = [...FX.page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
    const markup = FX.page.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/topology?view=health', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    w.htmx = { ajax: () => {}, process: () => {} };
    w.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ changed: false }) });
    w.IntersectionObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
    w.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
    w.Element.prototype.scrollIntoView = function () {};
    w.Plotly = {};
    w._plotlyRender = () => new Promise(() => {});
    new w.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('chip-status.js')).call(w);
    scripts.forEach((s) => { try { w.eval(s); } catch (e) { console.log('# page script: ' + e.message); } });
    await wait(60);
    const tile = Array.from(d.querySelectorAll('#topo-health-tiles *'))
        .find((el) => el.children.length === 0 && /oldest calibration/.test(el.textContent || ''));
    const tileBox = tile && tile.parentElement;
    const tileVal = tileBox ? tileBox.textContent.replace(/\s+/g, ' ').trim() : '';
    ok(!!tileBox, 'the Health tile says the oldest calibration: ' + tileVal);
    const chip = Array.from(d.querySelectorAll('#topo-health-worst .worst-chip'))
        .find((b) => /oldest calibration/.test(b.textContent));
    ok(!!chip, 'Needs attention names the oldest calibration');
    const who = chip ? chip.getAttribute('data-inspect-id') : '';
    const age = chip ? chip.querySelector('b').nextSibling.textContent.trim() : '';
    ok(who === FX.expect_id && chip.getAttribute('data-inspect-kind') === FX.expect_kind,
       'it is the entity the tile counts (' + who + ', kind ' + (chip && chip.getAttribute('data-inspect-kind')) + ')');
    ok(age && tileVal.indexOf(age) === 0, 'and the same age as the tile: chip "' + age + '", tile "' + tileVal + '"');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
