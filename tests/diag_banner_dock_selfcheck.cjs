/* jsdom selfcheck: the late crash banner never covers the controls a user is
 * working with, and never moves anything under the pointer (docs/251, C-13).
 *
 * It used to float at the bottom-left -- over the Agent composer's select, the
 * plan-mode selects and Agent Setup's Test buttons -- and joined the flow only
 * on a main-pane swap, a pointer over the head block or a hidden tab, none of
 * which a user working inside one page produces. Now it floats over exactly
 * the box it will take in the flow and joins the flow at the first moment
 * that is MEASURED to move nothing under the pointer.
 *
 * jsdom has no layout, so this harness models one: a head block, the banner
 * (the same box floating or docked -- the CSS contract pinned below), content
 * that moves down by the banner's height when it docks (a strip outside any
 * scroller, rows inside a scroller with room, rows inside a scroller without
 * room) and a bottom-anchored composer that does not move. getBoundingClientRect,
 * elementFromPoint, scrollTop/scrollHeight/clientHeight are stubs over it.
 *
 * Run: node tests/diag_banner_dock_selfcheck.cjs (driven by tests/test_diag_banner_dock.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/agent', pretendToBeVisual: true,
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
global.history = window.history;
global.getComputedStyle = window.getComputedStyle.bind(window);
const store = {};
const ss = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = ss;
window.localStorage = global.localStorage;
Object.defineProperty(window, 'sessionStorage', { value: ss, configurable: true });
global.fetch = () => new Promise(() => {});
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

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const doc = window.document;

// ---- the stylesheet contract the geometry model below relies on ----------
const css = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
    'static', 'style.css'), 'utf8');
const floatRule = (css.match(/#diagnostics-banner-slot\.diag-banner-overlay > \.diag-error-banner\s*\{([^}]*)\}/) || [])[1] || '';
ok(/position:\s*absolute/.test(floatRule) && /top:\s*0/.test(floatRule)
    && /left:\s*0/.test(floatRule) && /right:\s*0/.test(floatRule),
    'the floating banner sits on the box it takes in the flow (absolute, top/left/right 0 inside its slot)');
ok(!/bottom\s*:/.test(floatRule) && !/position:\s*fixed/.test(floatRule) && !/margin\s*:/.test(floatRule),
    'no bottom-left anchoring, no fixed position, no margin override (the in-flow margins hold)');
ok(/#diagnostics-banner-slot\s*\{[^}]*display:\s*flow-root[^}]*position:\s*relative/.test(css),
    'the slot keeps the banner margins inside it in both modes (flow-root) and anchors the float (relative)');
ok(/#diagnostics-banner-slot\[data-held\] > \.diag-error-banner\s*\{[^}]*visibility:\s*hidden/.test(css),
    'a held banner is invisible and not hit-testable');

// ---- a layout model ----------------------------------------------------
const H = 80;              // the banner's in-flow height (+ margins)
const BANNER = { top: 56, bottom: 136, left: 16, right: 1350 };
function build() {
    doc.body.innerHTML =
        '<div class="shell-head"><header class="topbar"><a id="tb">x</a></header>' +
        '<div id="diagnostics-banner-slot"></div></div>' +
        '<div class="app-layout">' +
        ' <aside id="sidebar"></aside>' +
        ' <main id="main"><div id="table-pane">' +
        '  <div id="strip"><a id="link">Connect</a></div>' +
        '  <div id="feed"><div id="card0">first card</div><div id="card">card</div></div>' +
        '  <div id="short"><div id="shortrow">r</div></div>' +
        '  <div id="composer"><select id="engine"></select></div>' +
        ' </div></main></div>';
    const slot = doc.getElementById('diagnostics-banner-slot');
    // the banner takes H of layout only while it is in the flow
    const shift = () => (!slot.classList.contains('diag-banner-overlay')
        && slot.querySelector('.diag-error-banner') ? H : 0);
    const writes = {};
    function scroller(id, sh, chFloat, shrinks, st) {
        const el = doc.getElementById(id);
        el.style.overflowY = 'auto';
        let top = st;
        writes[id] = 0;
        Object.defineProperty(el, 'scrollTop', { get: () => top, set: (v) => { writes[id]++; top = Math.max(0, Math.min(v, sh - el.clientHeight)); }, configurable: true });
        Object.defineProperty(el, 'scrollHeight', { get: () => sh, configurable: true });
        Object.defineProperty(el, 'clientHeight', { get: () => chFloat - (shrinks ? shift() : 0), configurable: true });
        return el;
    }
    // feed: a scroller whose top moves down and bottom stays (it shrinks) -- room
    const feed = scroller('feed', 2000, 400, true, 0);
    // short: a fixed-height scroller that moves down whole, already at its end -- no room
    const short = scroller('short', 300, 200, false, 100);
    const R = (t, b, l, r) => ({ top: t, bottom: b, left: l, right: r, width: r - l, height: b - t, x: l, y: t });
    const s = shift;
    const geo = {
        tb: () => R(0, 48, 0, 1366),
        'table-pane': () => R(48 + s(), 768, 300, 1350),
        strip: () => R(60 + s(), 100 + s(), 300, 1350),
        link: () => R(70 + s(), 90 + s(), 300, 400),
        feed: () => R(150 + s(), 550, 300, 800),
        card: () => R(150 + s() + 100 - feed.scrollTop, 190 + s() + 100 - feed.scrollTop, 300, 800),
        card0: () => R(150 + s() - feed.scrollTop, 190 + s() - feed.scrollTop, 300, 800),
        short: () => R(150 + s(), 350 + s(), 850, 1350),
        shortrow: () => R(150 + s() + 250 - short.scrollTop, 180 + s() + 250 - short.scrollTop, 850, 1350),
        composer: () => R(690, 740, 300, 1350),
        engine: () => R(700, 730, 300, 400),
    };
    for (const id of Object.keys(geo)) doc.getElementById(id).getBoundingClientRect = geo[id];
    const bannerRect = () => R(BANNER.top, BANNER.bottom, BANNER.left, BANNER.right);
    // hit-test order: the banner (unless held), then leaves, then containers
    const order = ['tb', 'link', 'strip', 'card0', 'card', 'feed', 'shortrow', 'short', 'engine', 'composer', 'table-pane'];
    document.elementFromPoint = (x, y) => {
        const b = slot.querySelector('.diag-error-banner');
        if (b && !b.hidden && !slot.hasAttribute('data-held')) {
            const r = bannerRect();
            if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return b;
        }
        for (const id of order) {
            const el = doc.getElementById(id), r = el.getBoundingClientRect();
            // a scroller clips its content to its own box
            const clipEl = el.parentElement && el.parentElement.closest('#feed, #short');
            if (clipEl) {
                const c = clipEl.getBoundingClientRect();
                if (y < c.top || y > c.bottom) continue;
            }
            if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return el;
        }
        return doc.body;
    };
    return { slot, feed, short, bannerRect, writes };
}
function arrive(m) {
    const slot = m.slot;
    slot.innerHTML = '<div class="topo-change-banner diag-error-banner" id="diagnostics-banner" data-diag-sig="1:c:z">' +
        '<span class="t">1 error</span><button id="rev">Review</button></div>';
    const b = slot.querySelector('.diag-error-banner');
    b.getBoundingClientRect = m.bannerRect;
    Object.defineProperty(b, 'offsetHeight', { get: () => H - 20, configurable: true });
    slot.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: slot } }));
    return slot;
}
function pointer(type, x, y, target) {
    const e = new window.Event(type, { bubbles: true });
    e.clientX = x; e.clientY = y;
    (target || doc.elementFromPoint(x, y) || doc.body).dispatchEvent(e);
}
const mode = (s) => s.getAttribute('data-mode');

(async () => {
    // 1. the pointer rests on the Agent composer (bottom-anchored): it docks at once
    let m = build();
    pointer('pointermove', 350, 715);
    let slot = arrive(m);
    ok(mode(slot) === 'flow' && !slot.classList.contains('diag-banner-overlay'),
        'pointer on a bottom-anchored control: the banner joins the flow at once (it covers nothing)');
    ok(doc.getElementById('engine').getBoundingClientRect().top === 700, 'and the control under the pointer did not move');

    // 2. the pointer rests on a feed card (a scroller with room): docks, and the
    //    scroller absorbs the shift so the card stays exactly where it was
    m = build();
    pointer('pointermove', 600, 260);
    const cardTop = doc.getElementById('card').getBoundingClientRect().top;
    slot = arrive(m);
    ok(mode(slot) === 'flow', 'pointer on a row in a scroller with room: it joins the flow');
    ok(m.feed.scrollTop === H, 'the scroller scrolled by exactly the banner height (got ' + m.feed.scrollTop + ')');
    ok(doc.getElementById('card').getBoundingClientRect().top === cardTop, 'the row under the pointer did not move');

    // 3. the pointer rests on a top strip outside any scroller: docking would move
    //    it -- the banner keeps floating, nothing is scrolled
    m = build();
    pointer('pointermove', 600, 95);   // on the strip, below... inside the banner box (56..136)
    slot = arrive(m);
    ok(mode(slot) === 'overlay' && slot.classList.contains('diag-banner-overlay'),
        'pointer on content docking would move: the banner keeps floating');
    ok(doc.getElementById('strip').getBoundingClientRect().top === 60, 'the strip under the pointer did not move');
    ok(slot.hasAttribute('data-held'), 'and it does not appear under the resting pointer (held)');
    // the pointer moves off the banner box onto other undockable content: shown
    pointer('pointermove', 600, 145, doc.getElementById('table-pane'));
    await sleep(200);
    ok(mode(slot) === 'overlay' && !slot.hasAttribute('data-held'),
        'once the pointer is outside its box the held banner is shown (still floating)');

    // 4. ...the user then moves onto the floating banner: the banner does not
    //    move when it docks, so this is a safe moment
    pointer('pointermove', 900, 100);
    await sleep(200);
    ok(mode(slot) === 'flow', 'pointer on the floating banner itself: it joins the flow');
    ok(doc.getElementById('strip').getBoundingClientRect().top === 60 + H,
        'the strip it covered is pushed below it (it was not under the pointer)');

    // 5. a scroller WITHOUT room: the row would move -> keep floating, nothing scrolled
    m = build();
    pointer('pointermove', 1000, 315);   // on shortrow (300..330)
    slot = arrive(m);
    ok(mode(slot) === 'overlay', 'pointer on a row whose scroller has no room: it keeps floating');
    ok(m.short.scrollTop === 100 && m.writes.short === 0, 'that scroller was not touched');
    ok(doc.getElementById('shortrow').getBoundingClientRect().top === 300, 'the row under the pointer did not move');
    ok(!slot.hasAttribute('data-held'), 'the pointer is not inside its box: shown');
    // ...the user moves on to the composer
    pointer('pointermove', 350, 715);
    await sleep(200);
    ok(mode(slot) === 'flow', 'the pointer reaches the composer: it joins the flow');

    // 5b. a scroller WITH room, but the row is so close to its top that after
    //     docking the pointer would be above the scroller: never scroll for that
    m = build();
    pointer('pointermove', 600, 160);    // on card0 (150..190), just under the banner box
    slot = arrive(m);
    ok(mode(slot) === 'overlay', 'pointer at the very top of a scroller: it keeps floating');
    ok(m.writes.feed === 0, 'and the scroller was never scrolled to try');

    // 6. the pointer leaves the window: nothing can move under it
    m = build();
    pointer('pointermove', 600, 95);
    slot = arrive(m);
    ok(mode(slot) === 'overlay', 'premise: floating while the pointer rests on the strip');
    const out = new window.Event('pointerout', { bubbles: true });
    out.clientX = 600; out.clientY = -1;
    doc.getElementById('strip').dispatchEvent(out);
    ok(mode(slot) === 'flow', 'the pointer left the window: it joins the flow');

    // 7. no pointer seen since the page loaded: it floats, visible, not docked
    m = build();
    window.eval("_diagPtr = null;");
    slot = arrive(m);
    ok(mode(slot) === 'overlay' && !slot.hasAttribute('data-held'),
        'unknown pointer: it floats (visible) instead of shifting anything');
    // a key press with the pointer still unknown does not force it in either
    doc.body.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    await sleep(200);
    ok(mode(slot) === 'overlay', 'a key press with no known pointer does not shift the page');

    // 8. a main-pane swap docks unconditionally, without scrolling the NEW pane's
    //    content out of view (the pointer is in the replaced pane)
    m = build();
    pointer('pointermove', 600, 95);
    slot = arrive(m);
    pointer('pointermove', 600, 260);   // on a feed card in the pane being replaced
    const pane = doc.getElementById('table-pane');
    pane.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
    ok(mode(slot) === 'flow', 'a main-pane swap docks it');
    ok(m.feed.scrollTop === 0 && m.writes.feed === 0, 'and never scrolls a pane whose content was just replaced');
    await sleep(200);

    // 9. dismissed while floating: the next trial clears the floating state
    m = build();
    pointer('pointermove', 600, 95);
    slot = arrive(m);
    slot.querySelector('.diag-error-banner').hidden = true;
    pointer('pointermove', 1000, 315);
    await sleep(200);
    ok(!slot.classList.contains('diag-banner-overlay') && mode(slot) === null,
        'dismissed while floating: no overlay state is left behind');

    // 10. the head block above the slot still docks it directly
    m = build();
    pointer('pointermove', 600, 95);
    slot = arrive(m);
    pointer('pointerover', 20, 20, doc.getElementById('tb'));
    ok(mode(slot) === 'flow', 'pointer over the head block docks it');

    // 11. a reserved slot renders in the flow and is never held
    m = build();
    slot = m.slot;
    slot.setAttribute('data-reserved', '1');
    pointer('pointermove', 600, 95);
    arrive(m);
    ok(mode(slot) === 'flow' && !slot.hasAttribute('data-held'), 'a reserved banner stays in the flow, never held');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('all ok');
    process.exit(0);
})().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
