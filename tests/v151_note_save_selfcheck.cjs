/* v1.5.1 U P2-22: a run note has a visible Save and says whether it is saved.
 *
 * The note saved only on blur / Escape, with no Save button and only a 1.2 s
 * border flash. Now the open editor shows Save and a state line ("Not saved
 * yet", "Saving...", "Saved HH:MM", "Not saved"); Ctrl+Enter saves; leaving
 * the box still saves; a Save press is ONE request (its mousedown keeps the
 * focus, so no blur-save runs beside it).
 *
 * The driver (tests/test_v151_note_save.py) renders the REAL dataset detail
 * fragment; its .ds-note-block runs here with its inline handlers and app.js.
 *
 * Run: node tests/v151_note_save_selfcheck.cjs <fixture.json>
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
const posts = [];
let release = null;
w.fetch = (url, opts) => {
    if (String(url).indexOf('/note') < 0) return new Promise(() => {});
    posts.push(JSON.parse(opts.body));
    return new Promise((res) => { release = () => res({ ok: true, status: 200, json: () => Promise.resolve({}) }); });
};
w.eval(APP);
w.showToast = () => {};

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const d = w.document;
const ta = d.querySelector('.ds-note-textarea');
const acts = d.querySelector('.ds-note-actions');
const save = d.querySelector('.ds-note-save');
const state = () => (d.querySelector('.ds-note-state') || {}).textContent || '';

(async function () {
    ok(!!acts && !!save, 'the rendered note has a Save button and a state line');
    ok(acts.hidden, 'both stay hidden while the editor is closed');
    d.querySelector('.ds-note-toggle').click();
    ok(!ta.hidden && !acts.hidden, 'opening the note shows Save beside it');
    ok(state() === '', 'an untouched note claims nothing');
    ta.value = 'first line\nsecond line';
    ta.dispatchEvent(new w.Event('input', { bubbles: true }));
    ok(state() === 'Not saved yet', 'typing says it is not saved yet: ' + state());
    // the Save press: mousedown (focus kept) then click
    const md = new w.MouseEvent('mousedown', { bubbles: true, cancelable: true });
    save.dispatchEvent(md);
    ok(md.defaultPrevented, 'the Save mousedown keeps the focus in the box (no blur-save beside it)');
    save.click();
    ok(posts.length === 1 && posts[0].note === 'first line\nsecond line', 'Save sends the note once');
    ok(state() === 'Saving…', 'and says it is saving: ' + state());
    save.click();
    ok(posts.length === 1, 'a second press while it is on its way sends nothing more');
    release(); await tick();
    ok(/^Saved \d\d:\d\d$/.test(state()), 'then says it is saved: ' + state());
    ta.dispatchEvent(new w.FocusEvent('blur'));
    ok(posts.length === 1, 'leaving the box after the save sends nothing');
    // Ctrl+Enter saves; Enter alone adds a line
    ta.value = 'third';
    ta.dispatchEvent(new w.Event('input', { bubbles: true }));
    const enter = new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    ta.dispatchEvent(enter);
    ok(posts.length === 1 && !enter.defaultPrevented, 'Enter alone adds a line, saves nothing');
    const ce = new w.KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true });
    ta.dispatchEvent(ce);
    ok(posts.length === 2 && posts[1].note === 'third' && ce.defaultPrevented, 'Ctrl+Enter saves');
    release(); await tick();
    // leaving the box still saves
    ta.value = 'fourth';
    ta.dispatchEvent(new w.Event('input', { bubbles: true }));
    ta.dispatchEvent(new w.FocusEvent('blur'));
    ok(posts.length === 3 && posts[2].note === 'fourth', 'leaving the box still saves');
    release(); await tick();
    ok(/^Saved/.test(state()), 'saved: ' + state());

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
