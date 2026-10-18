/* v1.5.1 U P2-28: an idle or hidden tab stops asking what it cannot show.
 *
 * Two idle tabs made 164 requests a minute: /scheduler/status every 2.5 s
 * although the Experiment Runner UI is hidden without SM_EXPERIMENTAL, and
 * the QUAlibrate banner / data hint every 3 s from a hidden tab.
 *
 * The driver (tests/test_v151_idle_polls.py) renders the REAL full page with
 * and without SM_EXPERIMENTAL and the REAL /datasets fragment. This harness
 * runs the page's own registry and scheduler-poll inline scripts (and app.js
 * for the hidden-tab hook) on that markup with fake timers, and counts
 * requests.
 *
 * Run: node tests/v151_idle_polls_selfcheck.cjs <fixture.json>
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

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function inlineScripts(html) {
    return [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
}

/* the page's scheduler poll, run on its own rendered body, with `status`
 * answering every /scheduler/status; returns how many it asked in `ticks`
 * intervals */
async function schedulerPolls(pageHtml, status, ticks) {
    const body = pageHtml.slice(pageHtml.indexOf('<body'));
    const dom = new JSDOM('<!doctype html><html>' + body.replace(/<script\b[\s\S]*?<\/script>/gi, '') + '</html>',
        { url: 'http://localhost/help', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    const intervals = [];
    w.setInterval = (cb) => { intervals.push(cb); return intervals.length; };
    w.clearInterval = () => {};
    let asked = 0;
    w.fetch = (url) => {
        if (String(url).indexOf('/scheduler/status') === 0) asked++;
        return Promise.resolve({ json: () => Promise.resolve(status) });
    };
    const scripts = inlineScripts(body);
    const reg = scripts.find((s) => s.indexOf('registry.replace = function') >= 0);
    const sched = scripts.find((s) => s.indexOf('replace("scheduler")') >= 0);
    if (!reg || !sched) throw new Error('the rendered page lost its registry or scheduler script');
    w.eval(reg);
    w.eval(sched);
    await tick();
    for (let i = 0; i < ticks; i++) { intervals.forEach((cb) => cb()); await tick(); }
    return asked;
}

(async function main() {
    const IDLE = { run: { status: 'idle' }, queue: [] };
    const RUN = { run: { status: 'running', current_id: null }, queue: [] };
    const off = await schedulerPolls(FX.page_plain, IDLE, 10);
    ok(off === 1, 'no runner UI, nothing running: one look, then silence (asked ' + off + ')');
    const offRun = await schedulerPolls(FX.page_plain, RUN, 10);
    ok(offRun === 11, 'no runner UI but a queue left running: it is followed (asked ' + offRun + ')');
    const on = await schedulerPolls(FX.page_runner, IDLE, 10);
    ok(on === 11, 'with the runner UI the badge keeps polling (asked ' + on + ')');

    // the hidden-tab hook on the REAL poll elements
    const dom = new JSDOM('<!doctype html><html><body>' + FX.page_plain.slice(FX.page_plain.indexOf('<body') + 5)
        .replace(/<script\b[\s\S]*?<\/script>/gi, '') + FX.datasets_html.replace(/<script\b[\s\S]*?<\/script>/gi, '')
        + '</body></html>', { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    w.fetch = () => new Promise(() => {});
    w.eval(APP);
    let hidden = true;
    Object.defineProperty(w.document, 'hidden', { get: () => hidden, configurable: true });
    ['qualibrate-running-slot', 'qualibrate-running-data-slot'].forEach(function (id) {
        const el = w.document.getElementById(id);
        ok(!!el, 'the real markup renders #' + id);
        if (!el) return;
        ok(/\bsmVisible\b/.test(el.getAttribute('hx-trigger') || ''), '#' + id + ' asks once on return (smVisible)');
        hidden = true;
        const ev = new w.CustomEvent('htmx:beforeRequest', { bubbles: true, cancelable: true, detail: { elt: el } });
        el.dispatchEvent(ev);
        ok(ev.defaultPrevented, '#' + id + ': a hidden tab sends no poll');
        hidden = false;
        const ev2 = new w.CustomEvent('htmx:beforeRequest', { bubbles: true, cancelable: true, detail: { elt: el } });
        el.dispatchEvent(ev2);
        ok(!ev2.defaultPrevented, '#' + id + ': a visible tab polls as before');
        let back = 0;
        el.addEventListener('smVisible', () => { back++; });
        w.document.dispatchEvent(new w.Event('visibilitychange'));
        ok(back === 1, '#' + id + ': returning to the tab asks at once');
    });

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
