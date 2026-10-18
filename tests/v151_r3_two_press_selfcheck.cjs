/* v1.5.1 R3 M6 / MINOR 3 / MINOR 7: two-press confirms.
 *
 * M6: a double click on "Keep mine -- overwrite live" sent two preflights;
 *     the second answer found the button the first had just armed and took
 *     that as the confirm -- live was overwritten before the armed label
 *     could be read. A held Enter did the same to any two-press button.
 *     Now one preflight at a time per button, and a confirm counts only
 *     SyncPanel.config.armMinMs after arming.
 * MINOR 3: a button a re-render detached kept counting down and toasted
 *     "nothing was changed" after the write on its replacement had landed.
 * MINOR 7: the seconds left were CSS generated content (read as part of the
 *     button's name), and the expiry was an info toast no screen reader hears.
 *
 * The driver (tests/test_v151_r3_two_press.py) renders the REAL sync panel
 * (/state/review) with one unapplied edit.
 *
 * Run: node tests/v151_r3_two_press_selfcheck.cjs <fixture.json>
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
const ROOT = path.join(__dirname, '..');
const APP = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<div id="state-review-overlay" class="sync-panel-overlay" style="display:block">'
    + '<div id="state-review-host">' + FX.panel_html + '</div></div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
const triggered = [];
w.htmx = { ajax: () => Promise.resolve(), trigger: (el, name) => { triggered.push(name); },
           process: () => {}, on: () => {}, config: {} };
let preflights = 0;
const pending = [];
w.fetch = (url) => {
    if (String(url).indexOf('/state/overwrite-live/preflight') === 0) {
        preflights++;
        return new Promise((res) => pending.push(() => res({
            json: () => Promise.resolve({ ok: true, live_changes: 3, live_paths: ['qubits.q1.T1'],
                                          live_hash: 'h' }) })));
    }
    return new Promise(() => {});
};
w.eval(APP);
w.eval('_keepMinePush = function (d) { window.__pushes = (window.__pushes || 0) + 1; };');
const toasts = [];
w.showToast = (m) => { toasts.push(String(m)); };
let now = 1.7e12;
w.Date.now = () => now;
const ivals = new Map();
let nid = 0;
w.setInterval = (cb) => { ivals.set(++nid, cb); return nid; };
w.clearInterval = (id) => { ivals.delete(id); };
w.setTimeout = () => ++nid;
w.clearTimeout = () => {};
const second = () => { now += 1000; [...ivals.values()].forEach((cb) => cb()); };
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

(async function () {
    const d = w.document;
    const minMs = w.SyncPanel.config && w.SyncPanel.config.armMinMs;
    ok(minMs >= 300 && minMs <= 600, 'a confirm needs a few hundred ms after arming: ' + minMs);

    // --- M6: a double click on the real Keep mine
    const keep = d.querySelector('.sp-keep');
    ok(!!keep, 'the real panel renders Keep mine');
    w.overwriteLiveWithWorking(keep);
    w.overwriteLiveWithWorking(keep);          // the double click's second press
    ok(preflights === 1, 'one preflight for a double click: ' + preflights);
    while (pending.length) { pending.shift()(); await settle(); }
    ok(keep.getAttribute('data-armed') === '1', 'the preflight arms the button');
    ok(/Press again to overwrite 3 live values/.test(keep.querySelector('.sp-choice-label').textContent),
       'with the label that names what goes');
    ok(!w.__pushes, 'and nothing was pushed');
    w.overwriteLiveWithWorking(keep);          // a held Enter repeats at once
    ok(!w.__pushes, 'a press right after arming is not the confirm');
    now += minMs;
    w.overwriteLiveWithWorking(keep);
    ok(w.__pushes === 1, 'a press after the label could be read pushes once');

    // --- M6: a held Enter on any two-press button
    const disc = d.querySelector('.sp-discard-all');
    ok(!!disc, 'the real panel renders the two-press Discard');
    w.SyncPanel.arm(disc, null);
    w.SyncPanel.arm(disc, null);
    w.SyncPanel.arm(disc, null);
    ok(triggered.length === 0 && disc.getAttribute('data-armed') === '1',
       'repeated presses inside the minimum keep it armed, nothing run');
    now += minMs;
    w.SyncPanel.arm(disc, null);
    ok(triggered.indexOf('armed') >= 0, 'a confirm after the minimum runs it');

    // --- MINOR 7: the seconds are not in the accessible name; expiry is said
    w.SyncPanel.arm(disc, null);
    const badge = disc.querySelector('.arm-left-badge');
    ok(!!badge && badge.getAttribute('aria-hidden') === 'true' && badge.textContent === String(w.SyncPanel.armSeconds),
       'the seconds left are an aria-hidden badge');
    ok(!/content:\s*attr\(data-arm-left\)/.test(CSS), 'and not CSS generated content (read as the name)');
    for (let i = 0; i < w.SyncPanel.armSeconds; i++) second();
    const live = d.getElementById('sr-live-polite');
    ok(!!live && live.getAttribute('aria-live') === 'polite' && /Not done/.test(live.textContent),
       'the expiry is said in a polite live region: ' + (live && live.textContent));

    // --- MINOR 3: a re-render detaches an armed button
    toasts.length = 0;
    const take = d.querySelector('.sp-discard-all');
    w.SyncPanel.arm(take, null);
    second();
    d.getElementById('state-review-host').innerHTML = FX.panel_html;     // the panel re-renders
    const fresh = d.querySelector('.sp-discard-all');
    w.SyncPanel.arm(fresh, null);
    now += minMs;
    w.SyncPanel.arm(fresh, null);                                     // the write on the new button
    for (let i = 0; i < 7; i++) second();
    ok(!toasts.some((t) => /nothing was changed/.test(t)),
       'the detached button\'s countdown says nothing after the write: ' + toasts.join(' | '));

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
