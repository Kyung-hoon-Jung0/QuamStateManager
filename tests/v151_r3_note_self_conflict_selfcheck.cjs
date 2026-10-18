/* v1.5.1 R3 MINOR 7: two saves of one run note in flight are not a conflict
 * with "another window".
 *
 * Save (or Ctrl+Enter), a few more letters, Ctrl+Enter again before the first
 * answer: both requests carried `expected` = the text stored BEFORE the first.
 * The server stored the first and refused the second; the user was told the
 * note "was changed in another window" (their own save), the newer text was
 * not saved, and the unload flush skipped a conflicted note. The second save
 * now waits for the first and goes after it.
 *
 * The driver (tests/test_v151_r3_note_self_conflict.py) renders the REAL
 * dataset detail fragment; the fake server applies the same expected-vs-
 * stored rule as POST /dataset/<uid>/note, requests in arrival order.
 *
 * Run: node tests/v151_r3_note_self_conflict_selfcheck.cjs <fixture.json>
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

const block = new JSDOM('<!doctype html><html><body>' + FX.detail_html + '</body></html>')
    .window.document.querySelector('.ds-note-block');
if (!block) { console.error('the rendered detail has no .ds-note-block'); process.exit(1); }
const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' + block.outerHTML + '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'dangerously', pretendToBeVisual: true });
const w = dom.window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
let stored = FX.stored || '';
const queue = [];
const sent = [];
w.fetch = (url, opts) => {
    if (String(url).indexOf('/note') < 0) return new Promise(() => {});
    const body = JSON.parse(opts.body);
    sent.push(body);
    return new Promise((res) => queue.push(() => {
        if (body.expected !== undefined && body.expected !== stored) {
            res({ ok: false, status: 409, json: () => Promise.resolve({ note_conflict: true, current: stored }) });
            return;
        }
        stored = body.note;
        res({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    }));
};
w.eval(APP);
const toasts = [];
w.showToast = (m, lvl) => { toasts.push(lvl + ': ' + m); };

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const d = w.document;
const ta = d.querySelector('.ds-note-textarea');

(async function () {
    d.querySelector('.ds-note-toggle').click();
    const ctrlEnter = () => ta.dispatchEvent(new w.KeyboardEvent('keydown',
        { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }));
    ta.value = 'abc';
    ctrlEnter();                                     // the first save
    ta.value = 'abcd';
    ctrlEnter();                                     // a second, before the first answers
    ok(sent.length === 1, 'the second save waits for the first: ' + sent.length + ' sent');
    queue.shift()(); await tick();                   // the first lands
    ok(sent.length === 2 && sent[1].note === 'abcd' && sent[1].expected === 'abc',
       'then goes, after the text the first stored: ' + JSON.stringify(sent[1] || null));
    while (queue.length) { queue.shift()(); await tick(); }
    ok(stored === 'abcd', 'the newest text is stored: ' + JSON.stringify(stored));
    ok(!toasts.some((t) => /another window/.test(t)), 'nobody is told of another window: ' + toasts.join(' | '));
    ok(!ta.dataset.conflict, 'no conflict is recorded');
    ok(/^Saved/.test((d.querySelector('.ds-note-state') || {}).textContent || ''), 'and it says Saved');

    // the page goes while a save is on its way: the newer text is sent at once
    ta.value = 'abcde';
    ctrlEnter();
    ta.value = 'abcdef';
    w.dispatchEvent(new w.Event('beforeunload', { cancelable: true }));
    ok(sent.length === 4 && sent[3].note === 'abcdef' && sent[3].expected === 'abcde',
       'an unload during a save sends the newest text after the one on its way: '
       + JSON.stringify(sent.slice(2)));
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
