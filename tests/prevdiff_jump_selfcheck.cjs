/* datasets-r2-15: the Prev State run box refuses what is not a run number,
 * and says so -- against the REAL app.js under jsdom. '12.5' used to become
 * #125 silently (every non-digit stripped) and 'abc' did nothing at all,
 * leaving the typed text beside a table that still named the old run.
 *
 * Run: node tests/prevdiff_jump_selfcheck.cjs  (driven by tests/test_misc_ui.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/datasets', pretendToBeVisual: true,
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
global.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
const loads = [];
window.loadPrevDiff = (inp, uid, n, compact) => { loads.push({ uid, n, compact }); };

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;

// the bar as _dataset_prev_diff.html renders it (4113 vs 4112), optionally with a note
function bar(note) {
    doc.body.innerHTML = '<div class="prevdiff"><div class="prevdiff-bar"><div class="prevdiff-stepper">'
        + '<span class="prevdiff-vs">#4113 vs <span class="prevdiff-vs-pick">#<input class="prevdiff-vs-input"'
        + ' value="4112"></span></span></div>'
        + (note ? '<p class="muted prevdiff-note">' + note + '</p>' : '')
        + '<div class="prevdiff-badges"><span class="diff-badge">+3</span></div></div></div>';
    return doc.querySelector('.prevdiff-vs-input');
}
const noteText = () => { const n = doc.querySelector('.prevdiff-note'); return n ? n.textContent : null; };

for (const bad of ['12.5', 'abc', '-5', '4,112', '']) {
    const inp = bar();
    inp.value = bad;
    const n0 = loads.length;
    window.prevDiffJump(inp, 'k:4113', 0);
    ok(loads.length === n0, JSON.stringify(bad) + ' loads nothing (never rewritten into another run)');
    ok(noteText() && /not a run number/.test(noteText()) && noteText().indexOf(bad) !== -1,
       JSON.stringify(bad) + ' is refused with a message naming it');
    ok(inp.value === '4112', JSON.stringify(bad) + ': the box goes back to the run the table shows');
    const notes = doc.querySelectorAll('.prevdiff-note');
    ok(notes.length === 1 && notes[0].nextElementSibling === doc.querySelector('.prevdiff-badges'),
       JSON.stringify(bad) + ': one note, in the note slot before the badges');
}

// an old note ('#99999 has no saved state') is REPLACED, not left beside the refusal
let inp = bar('Run #99999 has no saved state; showing #4112.');
inp.value = 'abc';
window.prevDiffJump(inp, 'k:4113', 1);
ok(doc.querySelectorAll('.prevdiff-note').length === 1 && !/99999/.test(noteText()),
   'the stale #99999 note is replaced by the refusal, not kept beside it');

// what IS a run number still goes straight through
for (const [good, n] of [['125', 125], ['#125', 125], [' 125 ', 125], ['# 4100', 4100]]) {
    inp = bar();
    inp.value = good;
    window.prevDiffJump(inp, 'k:4113', 1);
    const last = loads[loads.length - 1];
    ok(last && last.n === n && last.uid === 'k:4113' && last.compact === 1,
       JSON.stringify(good) + ' compares against #' + n);
}

process.exit(fails ? 1 : 0);
