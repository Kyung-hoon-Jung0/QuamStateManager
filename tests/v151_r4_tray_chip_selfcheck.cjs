/* v1.5.1 R4 a2: a tab takes the drift poll's tray_durable for its own chip only.
 *
 * The REAL page of chip A, drawn while A's checkpoint writes fail (no
 * data-tray-durable: leaving asks), then the REAL /state/drift answers:
 * chip B's (another window opened it; its rows are checkpointed) must not
 * mark A's tray durable; A's own answers still move it both ways.
 *
 * Run: node tests/v151_r4_tray_chip_selfcheck.cjs <fixture.json>
 *      (fixture written by tests/test_v151_r4_tray_chip.py)
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
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const SQ = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

const body = FX.page.slice(FX.page.indexOf('<body'));
const html = body.replace(/<script\b[\s\S]*?<\/script>/gi, '');
const dom = new JSDOM('<!doctype html><html>' + html + '</html>',
    { url: 'http://localhost/help', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
let answer = FX.drift_b;
w.fetch = (url) => {
    url = String(url);
    if (url.indexOf('/state/drift') === 0 && url.indexOf('/view') < 0) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(answer) });
    }
    return new Promise(() => {});
};
// the page's own script sets the tab's chip key (stripped above): as the browser does
const ck = /window\.__chipKey = ("(?:[^"\\]|\\.)*")/.exec(FX.page);
w.__chipKey = ck ? JSON.parse(ck[1]) : '';
w.eval(SQ);
w.eval(APP);

function asks() {
    const ev = new w.Event('beforeunload', { cancelable: true });
    w.dispatchEvent(ev);
    return ev.defaultPrevented;
}
async function poll(d) {
    answer = d;
    w.document.dispatchEvent(new w.Event('liveDriftChanged'));     // the drift poll
    await tick();
}

(async function () {
    const tray = w.document.getElementById('pending-tray');
    ok(w.__chipKey && w.__chipKey === FX.drift_a.tray_chip, 'the tab shows chip A: ' + w.__chipKey);
    ok(tray && !tray.hasAttribute('data-tray-durable'), 'fixture: A\'s checkpoint is failing, its tray is not durable');
    ok(asks(), 'leaving asks');
    await poll(FX.drift_b);
    ok(!tray.hasAttribute('data-tray-durable'), 'chip B\'s durable answer does not mark A\'s tray');
    ok(asks(), 'leaving still asks for A\'s rows');
    await poll(Object.assign({}, FX.drift_b, { tray_chip: FX.drift_a.tray_chip }));
    ok(tray.getAttribute('data-tray-durable') === '1', 'an answer for A itself still marks it durable');
    await poll(FX.drift_a);
    ok(!tray.hasAttribute('data-tray-durable'), 'and A\'s failing answer unmarks it');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
