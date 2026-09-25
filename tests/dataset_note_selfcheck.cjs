/* datasets-r2-12: a run's note is never silently lost -- against the REAL
 * app.js under jsdom.
 *   - saveDatasetNote sends the editor's baseline (`expected`) with keepalive,
 *     and sends nothing when the text did not change;
 *   - a 409 conflict keeps the typed text, never flashes green, says so, and
 *     makes the next leave an informed overwrite (data-saved := current);
 *   - any other failure (read-only folder 400) never flashes green either;
 *   - pagehide (F5 / close fires no blur) flushes only notes that differ from
 *     what is stored and are not already on their way;
 *   - Escape in the note leaves the box, so it saves.
 *
 * Run: node tests/dataset_note_selfcheck.cjs  (driven by tests/test_dataset_note_cas.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/datasets', pretendToBeVisual: true, runScripts: 'dangerously',   // the inline onkeydown / onblur run
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

// the server: record every POST, answer with whatever the test queued
const posts = [];
let answer = () => ({ status: 200, body: {} });
global.fetch = (url, opts) => {
    if (String(url).indexOf('/note') === -1) {   // app.js's own start-up fetches
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    }
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    posts.push({ url: String(url), opts: opts || {}, body });
    const a = answer(body);
    return Promise.resolve({
        ok: a.status >= 200 && a.status < 300, status: a.status,
        json: () => Promise.resolve(a.body),
    });
};
window.fetch = global.fetch;
const toasts = [];

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
window.showToast = (m, lvl) => { toasts.push({ m: String(m), lvl }); };
window.TagVocab = { load: () => {} };
global.TagVocab = window.TagVocab;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = () => new Promise((r) => setTimeout(r, 5));
const doc = window.document;

// the editor exactly as _dataset_detail.html renders it (inline handlers and all)
function editor(uid, saved) {
    return '<div class="ds-note-block"><button type="button" class="ds-note-toggle">Note</button>'
        + '<textarea class="ds-note-input ds-note-textarea" rows="1" data-uid="' + uid + '"'
        + ' data-saved="' + saved + '"'
        + ' onkeydown="if (event.key === \'Escape\') this.blur();"'
        + ' onblur="saveDatasetNote(\'' + uid + '\', this.value, this)">' + saved + '</textarea></div>';
}

(async function main() {
    doc.body.innerHTML = '<div id="a">' + editor('k:37', '') + '</div>'
        + '<div id="b">' + editor('k:38', 'kept') + '</div>';
    const ta = doc.querySelector('#a textarea');
    const tb = doc.querySelector('#b textarea');

    // 1. nothing changed -> nothing sent
    window.saveDatasetNote('k:37', '', ta);
    await tick();
    ok(posts.length === 0, 'a leave with an unchanged note sends nothing');

    // 2. a real save carries the baseline and keepalive; success flashes green + moves the baseline
    ta.value = 'tabA';
    window.saveDatasetNote('k:37', ta.value, ta);
    await tick();
    ok(posts.length === 1 && posts[0].url === '/dataset/k:37/note', 'a changed note is posted');
    ok(posts[0].body.note === 'tabA' && posts[0].body.expected === '', 'the post names the note the editor started from (expected)');
    ok(posts[0].opts.keepalive === true, 'the save is keepalive, so a reload does not cancel it');
    ok(ta.dataset.saved === 'tabA', 'after a 200 the baseline is the saved text');
    ok(/39, 174, 96|27ae60/i.test(ta.style.borderColor), 'a 200 flashes green');

    // 3. a 409: the other window's text is named, ours is kept, no green
    ta.style.borderColor = '';
    answer = () => ({ status: 409, body: { note_conflict: true, current: 'from the other tab', error: 'x' } });
    ta.value = 'tabA plus mine';
    window.saveDatasetNote('k:37', ta.value, ta);
    await tick();
    ok(ta.value === 'tabA plus mine', 'a 409 keeps the typed text in the box');
    ok(!/39, 174, 96|27ae60/i.test(ta.style.borderColor), 'a 409 never flashes green');
    ok(toasts.length === 1 && toasts[0].lvl === 'error' && toasts[0].m.indexOf('from the other tab') !== -1
       && /NOT saved/.test(toasts[0].m), 'a 409 says the note changed elsewhere, names that text, and says ours was not saved');
    ok(ta.dataset.saved === 'from the other tab', 'after a 409 the baseline is the stored text (the next leave is an informed overwrite)');
    answer = () => ({ status: 200, body: {} });
    window.saveDatasetNote('k:37', ta.value, ta);
    await tick();
    ok(posts[posts.length - 1].body.expected === 'from the other tab', '...and that overwrite sends the text it now knows about');

    // 4. a read-only folder (400) never looks saved
    answer = () => ({ status: 400, body: { error: 'dataset folder is read-only (x)' } });
    ta.style.borderColor = '';
    ta.value = 'on a read-only folder';
    window.saveDatasetNote('k:37', ta.value, ta);
    await tick();
    ok(!/39, 174, 96|27ae60/i.test(ta.style.borderColor), 'a 400 never flashes green');
    ok(toasts[toasts.length - 1].m.indexOf('read-only') !== -1, 'a 400 says why the note was not saved');
    ok(ta.dataset.saved !== 'on a read-only folder', 'a failed save does not move the baseline');
    answer = () => ({ status: 200, body: {} });

    // 5. pagehide flushes only the dirty notes (F5 fires no blur)
    ta.value = ta.dataset.saved;          // clean
    tb.value = 'kept + typed before F5';  // dirty
    let n0 = posts.length;
    window.dispatchEvent(new window.Event('pagehide'));
    await tick();
    ok(posts.length === n0 + 1 && posts[posts.length - 1].url === '/dataset/k:38/note'
       && posts[posts.length - 1].body.note === 'kept + typed before F5'
       && posts[posts.length - 1].body.expected === 'kept'
       && posts[posts.length - 1].opts.keepalive === true,
       'pagehide saves the one dirty note, with its baseline, keepalive');
    // ...and never sends one that is already on its way
    let release;
    global.fetch = window.fetch = (url, opts) => {
        posts.push({ url: String(url), opts: opts || {}, body: JSON.parse(opts.body) });
        return new Promise((r) => { release = () => r({ ok: true, status: 200, json: () => Promise.resolve({}) }); });
    };
    tb.value = 'blurred then reloaded';
    window.saveDatasetNote('k:38', tb.value, tb);   // the blur's save, still in flight
    n0 = posts.length;
    window.dispatchEvent(new window.Event('pagehide'));
    await tick();
    ok(posts.length === n0, 'pagehide does not resend a note whose save is already in flight');
    release();
    await tick();

    // 5b. beforeunload flushes too (it runs before the reload's own request),
    // and a note that just met another window's text is never overwritten by
    // an unload -- that takes a leave
    global.fetch = window.fetch = (url, opts) => {
        posts.push({ url: String(url), opts: opts || {}, body: JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    tb.value = 'typed, then F5';
    n0 = posts.length;
    window.dispatchEvent(new window.Event('beforeunload'));
    await tick();
    ok(posts.length === n0 + 1 && posts[posts.length - 1].body.note === 'typed, then F5',
       'beforeunload saves a dirty note');
    window.dispatchEvent(new window.Event('pagehide'));
    await tick();
    ok(posts.length === n0 + 1, '...and the pagehide that follows does not send it twice');
    global.fetch = window.fetch = (url, opts) => {
        posts.push({ url: String(url), opts: opts || {}, body: JSON.parse(opts.body) });
        return Promise.resolve({ ok: false, status: 409,
            json: () => Promise.resolve({ note_conflict: true, current: 'theirs' }) });
    };
    tb.value = 'mine';
    window.saveDatasetNote('k:38', tb.value, tb);
    await tick();
    ok(tb.dataset.conflict === '1', '(fixture) the 409 marked the box');
    n0 = posts.length;
    window.dispatchEvent(new window.Event('pagehide'));
    window.dispatchEvent(new window.Event('beforeunload'));
    await tick();
    ok(posts.length === n0, 'an unload never overwrites a note that just met another window\'s text');
    global.fetch = window.fetch = (url, opts) => {
        posts.push({ url: String(url), opts: opts || {}, body: JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    window.saveDatasetNote('k:38', tb.value, tb);    // the leave: an informed overwrite
    await tick();
    ok(posts[posts.length - 1].body.expected === 'theirs' && !tb.dataset.conflict,
       'the leave after it overwrites knowingly and clears the mark');

    // 6. Escape in the note leaves the box -> the blur saves it
    global.fetch = window.fetch = (url, opts) => {
        posts.push({ url: String(url), opts: opts || {}, body: JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    ta.focus();
    ta.value = 'escaped';
    n0 = posts.length;
    ta.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await tick();
    ok(doc.activeElement !== ta, 'Escape leaves the note box');
    ok(posts.length === n0 + 1 && posts[posts.length - 1].body.note === 'escaped', 'Escape saves the note');

    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
