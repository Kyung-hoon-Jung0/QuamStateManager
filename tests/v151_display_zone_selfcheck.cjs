/* v1.5.1 (T1-F3): the CZ gate plan's "Last set" times read in the app's
 * display zone, like every other surface.
 *
 * cz-plan.js printed the history's ISO stamp as it came ("2026-10-15 00:47:40
 * UTC") while the rest of the app shows the project zone with its offset
 * (SnapTime.display, docs/244). The driver (tests/test_v151_p2_display_zone.py)
 * renders the REAL /cz-plan fragment and the REAL /cz-plan/history answer
 * through the Flask test client and gives this harness the markup plus that
 * answer with one operation's last_set filled in; the SHIPPED app.js supplies
 * SnapTime, the page's <html data-sm-zone> its zone.
 *
 * Run: node tests/v151_display_zone_selfcheck.cjs <fixture.json>
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
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const CZ = fs.readFileSync(path.join(STATIC, 'cz-plan.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html data-sm-zone="' + FX.zone + '"><body><div id="table-pane">'
    + FX.page + '</div></body></html>',
    { url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = () => Promise.resolve();
w.fetch = (url) => {
    if (String(url).indexOf('/cz-plan/history') === 0) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(FX.history) });
    }
    return new Promise(() => {});
};
w.eval(APP);
w.eval(CZ);

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
    ok(!!w.SnapTime && w.SnapTime.zone() === FX.zone, 'the page zone is ' + FX.zone);
    w.CzPlan.mount();
    await wait(50);
    const td = d.querySelector('td.czp-last[data-last="' + FX.op + '"]');
    ok(!!td, 'the real table has the operation\'s Last set cell');
    const text = td ? td.textContent : '';
    ok(text.indexOf(FX.expect_long) >= 0, 'Last set in the display zone: ' + text);
    ok(!/UTC(?![+-])/.test(text), 'no bare UTC stamp left: ' + text);
    const other = d.querySelector('td.czp-last[data-last="' + FX.other_op + '"] span');
    const tip = other ? other.getAttribute('title') || '' : '';
    ok(tip.indexOf(FX.expect_long) >= 0 && tip.indexOf('T00:47') < 0,
       'an ISO time inside a server note reads the same: ' + tip);
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
