/* docs/164 — a lazy date group restored to OPEN must ask for its runs.
 *
 * Customer-reported, twice: "loading…" sitting under a date with nobody
 * running an experiment. The sidebar tree refetches itself (a version bump,
 * and unconditionally every 10th poll), the swap rebuilds every <details>
 * CLOSED with the placeholder inside, and the sticky restore re-opens the ones
 * that were open. Their runs arrive on one path only —
 * `hx-trigger="toggle[this.open] once"` — which is true for a person opening
 * the group and, measured, not true for the restore.
 *
 * Three toggle-shaped fixes were tried against the real browser and NONE
 * reached the trigger: htmx.trigger's CustomEvent, a native Event dispatched
 * inside afterSwap, and the same deferred by a task. Each time `__lazyAsked`
 * came back true on a group that was still stuck — which is how they were
 * caught instead of shipped. The request is issued directly now, read off the
 * element's own hx-get/hx-vals so it cannot drift from the markup.
 *
 * Run: node tests/sidebar_lazy_group_selfcheck.cjs
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 5));

const HX_GET = '/workspace/tree/group?capped=1';
const HX_VALS = '{"root": "D:\\\\archive", "tpath": "2026-08-19"}';

function groupHtml(open, filled) {
    return '<details class="tree-root" open><span class="tree-root-label"><span title="D:\\archive">archive</span></span>'
        + '<details class="tree-dir" data-tpath="2026-08-19" data-lazy-group="1"'
        + ' hx-get="' + HX_GET + '" hx-vals=\'' + HX_VALS + '\''
        + ' hx-trigger="toggle[this.open] once" hx-target="find ul.tree-entries" hx-swap="innerHTML"'
        + (open ? ' open' : '') + '>'
        + '<summary>2026-08-19</summary>'
        + '<ul class="tree-entries" data-lazy="1">'
        + (filled
            ? '<li class="tree-entry"><label class="tree-entry-label">run 1</label></li>'
            : '<li class="tree-entry muted tree-lazy-hint"><small>loading…</small></li>')
        + '</ul></details></details>';
}

function makeWorld() {
    const dom = new JSDOM('<!doctype html><html><body>'
        + '<div id="sidebar"><div id="sidebar-tree"></div>'
        + '<textarea id="sidebar-filter-input"></textarea></div>'
        + '<div id="table-pane"></div></body></html>',
        { url: 'http://localhost/', pretendToBeVisual: true });
    const { window } = dom;
    global.window = window; global.document = window.document;
    global.CSS = window.CSS;
    global.getComputedStyle = window.getComputedStyle.bind(window);
    global.Event = window.Event; global.CustomEvent = window.CustomEvent;
    global.MouseEvent = window.MouseEvent; global.KeyboardEvent = window.KeyboardEvent;
    global.location = window.location;
    const memStore = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    global.localStorage = memStore; global.sessionStorage = memStore;
    global.requestAnimationFrame = (f) => setTimeout(f, 5);
    window.requestAnimationFrame = global.requestAnimationFrame;
    global.MutationObserver = window.MutationObserver;
    global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    window.IntersectionObserver = global.IntersectionObserver;
    global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    window.ResizeObserver = global.ResizeObserver;
    window.__ajax = [];
    window.htmx = {
        ajax: function (verb, url, opts) { window.__ajax.push({ verb, url, opts }); return Promise.resolve(); },
        trigger: function () {}, process: function () {},
    };
    global.htmx = window.htmx;
    window.eval("fetch = window.fetch = function(){ return new Promise(function(){}); };");
    window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    window.eval(APP_JS);
    return window;
}

// Drive one refetch cycle: the group is OPEN with runs, the swap brings it back
// CLOSED with the placeholder, and the restore re-opens it.
async function refetchCycle(win, { openBefore = true, filledAfter = false,
                                   openAfter = false, closeDuring = false,
                                   fillDuring = false } = {}) {
    const d = win.document;
    const tree = d.getElementById('sidebar-tree');
    tree.innerHTML = groupHtml(openBefore, true);
    d.dispatchEvent(new win.CustomEvent('htmx:beforeSwap', { detail: { target: tree } }));
    // `openAfter` renders the swapped markup ALREADY open, which is the state
    // the guards below are about -- reaching it through the restore alone left
    // two of them unobservable, and an unobservable guard is an unpinned one.
    tree.innerHTML = groupHtml(openAfter, filledAfter);
    win.__ajax.length = 0;
    d.dispatchEvent(new win.CustomEvent('htmx:afterSwap', { detail: { target: tree } }));
    if (closeDuring) {              // the user collapses it in the same task
        const g = d.querySelector('details[data-lazy-group]');
        if (g) g.open = false;
    }
    if (fillDuring) {               // its runs land between the sweep and the ask
        const box = d.querySelector('details[data-lazy-group] ul.tree-entries');
        if (box) box.innerHTML = '<li class="tree-entry"><label class="tree-entry-label">run 1</label></li>';
    }
    await tick(30);
    return win.__ajax.filter((a) => String(a.url).indexOf('/workspace/tree/group') === 0);
}

(async () => {
    // 1. the bug: a group that was open comes back open, placeholder inside
    {
        const win = makeWorld();
        const asked = await refetchCycle(win);
        const g = win.document.querySelector('details[data-lazy-group]');
        ok(g.open, 'the sticky restore re-opens the group that was open');
        ok(asked.length === 1,
           'an open group still showing the placeholder ASKS for its runs (got '
           + asked.length + ' request(s))');
        if (asked.length === 1) {
            ok(asked[0].url === HX_GET, 'it asks the URL the markup declares');
            ok(asked[0].opts && asked[0].opts.values
               && asked[0].opts.values.tpath === '2026-08-19'
               && asked[0].opts.values.root === 'D:\\archive',
               'with the markup\'s own hx-vals (read off the element, never rebuilt)');
            ok(asked[0].opts.target && asked[0].opts.target.classList.contains('tree-entries'),
               'into the group\'s own entries list');
        }
    }

    // 2. a group whose runs ARE there is left alone -- no needless refetch
    {
        const win = makeWorld();
        const asked = await refetchCycle(win, { filledAfter: true, openAfter: true });
        const g = win.document.querySelector('details[data-lazy-group]');
        ok(g.open && !g.querySelector('.tree-lazy-hint'),
           'the fixture really reaches "open, and already filled"');
        ok(asked.length === 0, 'a group that came back WITH its runs is not re-asked');
    }

    // 3. a group that was CLOSED stays closed and silent
    {
        const win = makeWorld();
        const asked = await refetchCycle(win, { openBefore: false });
        const g = win.document.querySelector('details[data-lazy-group]');
        ok(!g.open, 'a group that was closed stays closed');
        ok(asked.length === 0, 'and is not asked for anything');
    }

    // 2b. FILLED between the sweep and the deferred ask -- the group was empty
    // when the sweep saw it, and its runs arrived before the ask fired (a
    // toggle the user made, a slower earlier request landing). Asking again
    // would refetch what is already on screen. The outer sweep cannot cover
    // this one: it already returned.
    {
        const win = makeWorld();
        const asked = await refetchCycle(win, { openAfter: true, fillDuring: true });
        ok(asked.length === 0, 'a group filled between the sweep and the ask is not re-asked');
    }

    // 3b. collapsed between the sweep and the deferred ask -- the ask is one
    // task later, so a person can close the group in between. Do not fetch
    // runs for a group nobody is looking at.
    {
        const win = makeWorld();
        const asked = await refetchCycle(win, { openAfter: true, closeDuring: true });
        ok(asked.length === 0, 'a group closed before the ask fires is not asked');
    }

    // 4. one ask per element, however many swaps land
    {
        const win = makeWorld();
        await refetchCycle(win);
        const d = win.document;
        const tree = d.getElementById('sidebar-tree');
        win.__ajax.length = 0;
        d.dispatchEvent(new win.CustomEvent('htmx:afterSwap', { detail: { target: tree } }));
        await tick(30);
        ok(win.__ajax.filter((a) => String(a.url).indexOf('/workspace/tree/group') === 0).length === 0,
           'the same element is asked once, not once per afterSwap');
    }

    console.log(fails ? ('FAILED: ' + fails) : ('ALL OK (' + passes + ' assertions)'));
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.stack || e); process.exit(1); });
