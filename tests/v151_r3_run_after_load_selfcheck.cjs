/* v1.5.1 R3 MINOR 6: a run that starts after a tab's first /scheduler/status
 * poll reaches the tab (plain build, no runner UI).
 *
 * The driver (tests/test_v151_r3_run_after_load.py) renders the REAL page and
 * the real /scheduler/status and /state/drift answers before and after a run
 * is written to the chip's queue. Here the page's own inline scheduler script
 * and app.js's drift poll run under jsdom with those answers.
 *
 * Run: node tests/v151_r3_run_after_load_selfcheck.cjs <fixture.json>
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
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

function inlineScripts(html) {
    return [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
}

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

(async function main() {
    const html = FX.page;
    const body = html.slice(html.indexOf('<body'));
    const dom = new JSDOM('<!doctype html><html>' + body.replace(/<script\b[\s\S]*?<\/script>/gi, '') + '</html>',
        { url: 'http://localhost/help', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    const intervals = [];
    let asked = 0;
    let status = FX.idle, drift = FX.drift0;
    w.fetch = (url) => {
        url = String(url);
        if (url.indexOf('/scheduler/status') === 0) {
            asked++;
            return Promise.resolve({ ok: true, json: () => Promise.resolve(status) });
        }
        if (url.indexOf('/state/drift') === 0 && url.indexOf('/view') < 0) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve(drift) });
        }
        return new Promise(() => {});
    };
    const scripts = inlineScripts(body);
    const reg = scripts.find((s) => s.indexOf('registry.replace = function') >= 0);
    const sched = scripts.find((s) => s.indexOf('replace("scheduler")') >= 0);
    ok(!!reg && !!sched, 'the real page carries its scheduler poll script');
    w.setInterval = (cb) => { intervals.push(cb); return intervals.length; };
    w.clearInterval = () => {};
    w.eval(reg);
    w.eval(sched);
    w.eval(APP);
    await tick();
    for (let i = 0; i < 3; i++) { intervals.forEach((cb) => cb()); await tick(); }
    w.document.dispatchEvent(new w.Event('liveDriftChanged'));   // a drift poll: the token as it is
    await tick();
    const before = asked;
    ok(before >= 1 && !w.document.body.classList.contains('scheduler-active'),
       'fixture: the first poll found nothing running');
    for (let i = 0; i < 4; i++) { intervals.forEach((cb) => cb()); await tick(); }
    ok(asked === before, 'with nothing running this tab stops asking (U P2-28 kept)');

    // a run starts on the chip from outside any page
    status = FX.running;
    drift = FX.drift1;
    w.document.dispatchEvent(new w.Event('liveDriftChanged'));   // the next drift poll
    await tick();
    await tick();
    ok(asked > before, 'the moved runner token makes the tab ask again');
    ok(w.document.body.classList.contains('scheduler-active'),
       'and it learns the run is going (the editing lock is on)');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(3); });
