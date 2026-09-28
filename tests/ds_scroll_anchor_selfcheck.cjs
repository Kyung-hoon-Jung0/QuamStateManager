/* Queue item 6 -- the Datasets run detail keeps the reader's place EXACTLY
 * across run switches (web/static/ds-scroll-anchor.js + its app.js wiring).
 *
 * jsdom has no layout, so this harness gives it one: every element is a block
 * whose height is its `data-h` (a leaf) or the sum of its children (a box),
 * hidden elements are 0, and getBoundingClientRect / scrollTop / scrollHeight
 * are derived from that for the one scroll container (#inspector-pane).
 * ResizeObserver is a fake the harness fires by hand when it "loads" content.
 *
 * Pinned (each mechanism was measured in real Chrome, docs record):
 *   A. capture -> apply on a DIFFERENT run puts the deepest holdable landmark
 *      exactly `within` above the pane top (integer layout here, so == 0).
 *   B. a landmark shorter than the offset walks up to the next level that can
 *      hold it, and says exact:false.
 *   C. the pin re-applies when lazy content above the anchor grows AFTER the
 *      restore (figure <img>s load late).
 *   D. a scroll the pin did not cause ends the pin and reports the reader.
 *   E. randomized: 400 run switches over random runs, the reader moving at
 *      random: a cached/remembered place never differs from what a cold
 *      capture of the reader's last own view says -- in particular one short
 *      run (clamped restore) never overwrites the place (the customer bug).
 *   F. app.js wiring: the intent is re-captured only when the reader moved,
 *      the restore runs in the swap (no setTimeout pixel restore left), and
 *      the tab the reader is on is restored.
 *   H. app.js's OWN listeners, executed (verifier P1/P3, 2026-09-27): a click
 *      with no scroll on a clamped run does not re-capture the intent; a
 *      pin's own write is not a reader move even when its scroll event fires
 *      after the pin stopped; a real scroll (pane or inner tree) and a tab
 *      the reader picked do re-capture.
 *   C2/B2. the pin re-applies on a capturing <img> load alone (no resize);
 *      a landmark exactly as tall as the offset walks up (>=, not >).
 *   I. final QA (2026-09-27): the READING LINE is the sticky header's bottom
 *      edge, not the pane top hidden under it -- the verifier's repro (pane
 *      top in the margin between q1 and q2, the visible line 53 px into q2,
 *      a neighbour whose q1 is 102 px taller) lands exact.
 *   J. a line IN the gap between two blocks is held by the block below at a
 *      negative offset (the gap is deterministic: a landmark ends above, its
 *      sibling starts below, nothing rendered between); non-landmark content
 *      at the line, or something rendered in between, stays with the parent.
 *   E2. E again with a sticky header of a different height on every run and
 *      margins between every block, judged by an INDEPENDENT reading of the
 *      reader's view (the probe's rule: every landmark under the line, plus
 *      the next block below when no content sits at the line).
 *   K. w8/dstab, app.js's real capture + afterSwap restore + switchDatasetTab
 *      executed: a run WITHOUT the reader's tab (no Interactive, State N/A)
 *      shows Full View at its top and leaves the intent alone, so the next
 *      run with the tab lands on it at the same place; a scroll or a tab
 *      press on that run (even Full View, the tab it already shows) replaces
 *      the intent, a click does not, and re-pressing the restored tab on a
 *      clamped run does not; a run opened into an EMPTY pane (after a close)
 *      is a fresh open -- Full View at the top, not the stale offset.
 *
 * Run: node tests/ds_scroll_anchor_selfcheck.cjs
 */
'use strict';
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

let fails = 0, asserts = 0;
function ok(cond, msg) { asserts++; if (!cond) { fails++; console.log('FAIL: ' + msg); } }

const dom = new JSDOM('<!doctype html><html><body><div id="inspector-pane"></div></body></html>',
                      { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

// ── fake layout ──────────────────────────────────────────────────────────
const PANE_TOP = 50, PANE_H = 700;
const pane = document.getElementById('inspector-pane');
let scrollTop = 0;
function hidden(el) {
    for (let e = el; e && e !== pane; e = e.parentElement) {
        if (e.classList && e.classList.contains('hidden')) return true;
        if (e.style && e.style.display === 'none') return true;
    }
    return false;
}
// An element with data-maxh is an INNER scroller (a .json-tree): its box is
// min(content, maxh) and its descendants move with its own scrollTop.
const innerTop = new Map();
function margin(el) {   // data-mb: a CSS margin-bottom (a gap no element covers)
    return hidden(el) ? 0 : (+el.getAttribute('data-mb') || 0);
}
function contentHeight(el) {
    let h = 0;
    for (const c of el.children) h += height(c) + margin(c);
    return h;
}
function height(el) {
    if (hidden(el)) return 0;
    if (el.hasAttribute('data-h')) return +el.getAttribute('data-h');
    const h = contentHeight(el);
    return el.hasAttribute('data-maxh') ? Math.min(h, +el.getAttribute('data-maxh')) : h;
}
function contentTop(el) {   // offset of el from the top of the pane's content
    let top = 0;
    for (let e = el; e && e !== pane; e = e.parentElement) {
        for (let s = e.previousElementSibling; s; s = s.previousElementSibling) top += height(s) + margin(s);
        const par = e.parentElement;
        if (par && par !== pane && par.hasAttribute('data-maxh')) top -= (innerTop.get(par) || 0);
    }
    return top;
}
function scrollHeight() { let h = 0; for (const c of pane.children) h += height(c) + margin(c); return h; }
function maxTop() { return Math.max(0, scrollHeight() - PANE_H); }
Object.defineProperty(pane, 'scrollTop', {
    get() { return scrollTop; },
    set(v) {
        const nv = Math.max(0, Math.min(Math.round(v), maxTop()));
        const changed = nv !== scrollTop;
        scrollTop = nv;
        if (changed) queueScrollEvent();
    },
    configurable: true,
});
Object.defineProperty(pane, 'clientHeight', { get() { return PANE_H; } });
Object.defineProperty(pane, 'scrollHeight', { get() { return scrollHeight(); } });
Object.defineProperty(pane, 'clientTop', { get() { return 0; } });
pane.scrollTo = undefined;   // exercise the scrollTop path
// every other element: scrollTop is only real on a data-maxh scroller
Object.defineProperty(window.Element.prototype, 'scrollTop', {
    get() { return this.hasAttribute('data-maxh') ? (innerTop.get(this) || 0) : 0; },
    set(v) {
        if (!this.hasAttribute('data-maxh')) return;
        const max = Math.max(0, contentHeight(this) - +this.getAttribute('data-maxh'));
        innerTop.set(this, Math.max(0, Math.min(Math.round(v), max)));
    },
    configurable: true,
});
let pendingScroll = false;
function queueScrollEvent() { pendingScroll = true; }
function flushScroll() {     // browsers dispatch scroll events a frame later
    if (!pendingScroll) return;
    pendingScroll = false;
    pane.dispatchEvent(new window.Event('scroll'));
    document.dispatchEvent(new window.Event('scroll'));   // not bubbling in reality; capture listeners see it
}
window.Element.prototype.getBoundingClientRect = function () {
    if (this === pane) return { top: PANE_TOP, bottom: PANE_TOP + PANE_H, height: PANE_H, left: 0, right: 800, width: 800 };
    const h = height(this);
    let top = PANE_TOP + contentTop(this) - scrollTop;
    // position:sticky; top:0 -- the dataset run header pins to the pane top
    if (this.classList.contains('inspector-header')) top = Math.max(top, PANE_TOP);
    return { top, bottom: top + h, height: h, left: 0, right: 800, width: 800 };
};

// fake ResizeObserver, fired by hand
const observers = [];
window.ResizeObserver = class {
    constructor(cb) { this.cb = cb; this.els = []; observers.push(this); }
    observe(el) { this.els.push(el); }
    disconnect() { this.els = []; }
};
function fireResize() { observers.forEach(o => { if (o.els.length) o.cb([]); }); }

// load the module under test
// The harness is a Node realm, not a jsdom script realm: hand the module every
// global it reads bare (CLAUDE.md harness rule), ResizeObserver included.
new Function('window', 'document', 'ResizeObserver',
    fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'ds-scroll-anchor.js'), 'utf8'))(
    window, document, function (cb) { return new window.ResizeObserver(cb); });
const A = window.DsScrollAnchor;
ok(A && typeof A.capture === 'function' && typeof A.pin === 'function', 'module loads');

// ── run fixtures ─────────────────────────────────────────────────────────
// A run: header (varies), tab strip, combined container with sections.
function runHTML(o) {
    const mb = o.mb ? ` data-mb="${o.mb}"` : '';
    const figs = (o.figs || []).map(f =>
        `<div class="figure-card"${mb}><div class="figure-label" data-h="20"><code>${f.name}</code></div><div class="img" data-h="${f.h}"></div></div>`).join('');
    const dets = (list) => list.map(d =>
        (d.between ? `<p data-h="${d.between}"${mb}></p>` : '') +
        `<details open class="detail-section"${mb}><summary data-h="24">${d.name}<button data-h="0">Copy</button></summary><div data-h="${d.h}"></div></details>`).join('');
    return `<div id="ds-detail-root" data-uid="${o.uid}">
      ${o.sticky ? `<div class="inspector-header" data-h="${o.sticky}"></div>` : ''}
      <div class="hdr" data-h="${o.header || 80}"></div>
      <nav class="dataset-tabs" data-h="40"><a class="active" data-ds-tab="full" data-h="0"></a></nav>
      <div class="dataset-tab-content" id="ds-tab-combined">
        <section data-fvsec="figures"${mb}><div class="figure-grid">${figs}</div>${(o.figs || []).length ? '' : '<p data-h="30"></p>'}</section>
        <section data-fvsec="overview"${mb}>${dets(o.overview || [])}</section>
        <section data-fvsec="results"${mb}>${o.lead ? `<div class="autofit-diag-bar" data-h="${o.lead}"></div>` : ''}${dets(o.results || [])}</section>
      </div></div>`;
}
function load(o) { pane.innerHTML = runHTML(o); if (scrollTop > maxTop()) pane.scrollTop = maxTop(); }
function container() { return pane.querySelector('#ds-tab-combined'); }
function el(key) {   // find a landmark by a simple descriptor for assertions
    const [kind, name] = key.split(':');
    if (kind === 'sec') return pane.querySelector(`[data-fvsec="${name}"]`);
    if (kind === 'fig') return [...pane.querySelectorAll('.figure-card')].find(c => c.querySelector('code').textContent === name);
    if (kind === 'det') return [...pane.querySelectorAll('details')].find(d => d.firstElementChild.firstChild.nodeValue === name);
    return null;
}
function offsetIn(key) { return PANE_TOP - el(key).getBoundingClientRect().top; }

const RUN1 = { uid: 'a', header: 80, figs: [{ name: 'raw', h: 400 }, { name: 'fit', h: 400 }],
               overview: [{ name: 'Experiment Info', h: 300 }, { name: 'Outcomes', h: 120 }, { name: 'Parameters', h: 600 }],
               results: [{ name: 'q1', h: 300 }, { name: 'q2', h: 300 }] };
const RUN2 = { uid: 'b', header: 104, figs: [{ name: 'raw', h: 250 }],
               overview: [{ name: 'Experiment Info', h: 380 }, { name: 'Outcomes', h: 90 }, { name: 'Parameters', h: 900 }],
               results: [{ name: 'q2', h: 280 }, { name: 'q3', h: 300 }] };
const SHORT = { uid: 'c', header: 80, figs: [], overview: [{ name: 'Experiment Info', h: 200 }], results: [] };

// ── A: exact landmark identity across runs ───────────────────────────────
load(RUN1);
pane.scrollTop = contentTop(el('det:Parameters')) + 137;   // reader is 137 px into Parameters
let cap = A.capture(pane, container());
ok(cap.chain && cap.chain.map(c => c.key).join('>') === '@container>sec:overview>det:Parameters',
   'A: chain is container > section > details: ' + JSON.stringify(cap.chain));
load(RUN2);
let res = A.apply(pane, container(), cap);
ok(res.exact === true, 'A: exact on a run that holds the landmark');
ok(offsetIn('det:Parameters') === 137, 'A: Parameters sits exactly 137 px above the top, got ' + offsetIn('det:Parameters'));

// summary buttons / run ids do not change a <details> key
load(RUN1);
pane.querySelector('summary').insertAdjacentHTML('beforeend', '<span class="inspector-runid" data-h="0">#999</span>');
ok(A._keyOf(pane.querySelector('details')) === 'det:Experiment Info', 'A: summary key is its own text only');

// ── B: a shorter landmark walks up, exact:false ──────────────────────────
load(RUN1);
pane.scrollTop = contentTop(el('fig:fit')) + 300;          // 300 px into the "fit" figure
cap = A.capture(pane, container());
ok(cap.chain[cap.chain.length - 1].key === 'fig:fit', 'B: anchored on the figure card');
load(RUN2);                                                // RUN2 has no "fit" figure
res = A.apply(pane, container(), cap);
ok(res.exact === false, 'B: not exact when the figure is absent');
ok(res.key === 'sec:figures' || res.key === '@container', 'B: fell back to a shallower level: ' + res.key);
if (res.key === 'sec:figures') {
    ok(offsetIn('sec:figures') === cap.chain[1].within, 'B: section-level offset kept');
}

// ── B2: a landmark EXACTLY as tall as the offset cannot hold it (>=) ─────
// Its top edge would sit on the landmark's bottom -- in whatever follows.
load(RUN1);
pane.scrollTop = contentTop(el('det:Outcomes')) + 40;
cap = A.capture(pane, container());
ok(cap.chain[cap.chain.length - 1].key === 'det:Outcomes' && cap.chain[cap.chain.length - 1].within === 40, 'B2: anchored 40 px into Outcomes');
load(Object.assign({}, RUN2, { overview: [{ name: 'Experiment Info', h: 380 }, { name: 'Outcomes', h: 16 }, { name: 'Parameters', h: 900 }] }));   // 24 + 16 = 40 tall
res = A.apply(pane, container(), cap);
ok(res.key === 'sec:overview' && res.exact === false, 'B2: walked up to the section: ' + res.key);
ok(offsetIn('sec:overview') === cap.chain[1].within, 'B2: the section-level offset is used, got ' + offsetIn('sec:overview'));

// ── C: the pin re-applies when content above grows late ─────────────────
load(RUN1);
pane.scrollTop = contentTop(el('det:Outcomes')) + 40;
cap = A.capture(pane, container());
load(Object.assign({}, RUN2, { figs: [{ name: 'raw', h: 0 }] }));   // image not loaded yet: 0 tall
let userCalls = 0;
let ctl = A.pin(pane, container, cap, () => { userCalls++; });
flushScroll();
ok(offsetIn('det:Outcomes') === 40, 'C: exact at pin time');
pane.querySelector('.figure-card .img').setAttribute('data-h', '520');   // the <img> loads
ok(offsetIn('det:Outcomes') !== 40, 'C: (fixture) the load really moved the content');
fireResize();
flushScroll();
ok(offsetIn('det:Outcomes') === 40, 'C: pin restored the place after the late load, got ' + offsetIn('det:Outcomes'));
ok(userCalls === 0 && ctl.active, 'C: its own re-apply is not mistaken for the reader');

// ── C2: an <img> load alone re-applies (capturing listener, no resize) ──
// Chrome delivers the ResizeObserver callback a frame late; the capturing
// load listener is what re-applies in the load task itself.
{
    const img = pane.querySelector('.figure-card .img');
    img.setAttribute('data-h', '700');
    ok(offsetIn('det:Outcomes') !== 40, 'C2: (fixture) the load really moved the content');
    img.dispatchEvent(new window.Event('load'));   // does not bubble; the pin listens in capture
    ok(offsetIn('det:Outcomes') === 40, 'C2: the img load event alone restored the place, got ' + offsetIn('det:Outcomes'));
    flushScroll();
    ok(userCalls === 0 && ctl.active, 'C2: that re-apply is not mistaken for the reader');
}

// ── D: a scroll the pin did not cause = the reader ───────────────────────
pane.scrollTop = pane.scrollTop + 200;
flushScroll();
ok(userCalls === 1 && !ctl.active, 'D: foreign scroll ends the pin and reports the reader');
const before = pane.scrollTop;
pane.querySelector('.figure-card .img').setAttribute('data-h', '100');
fireResize();
ok(pane.scrollTop === before, 'D: a stopped pin no longer moves the pane');

// ── E: randomized switch sequence against a cold recompute ───────────────
// Model of the app contract: intent is captured only after the reader moved;
// a switch applies the intent. Oracle: after every switch to a run that can
// hold the intent's deepest landmark (and has the scroll range), that
// landmark is exactly where the reader last left it.
function rnd(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const R = rnd(20260926);
const FIGN = ['raw', 'fit', 'flux', 'iq'];
const DETN = ['Experiment Info', 'Description', 'Outcomes', 'Parameters'];
function randomRun(i) {
    const nf = Math.floor(R() * 4);
    return { uid: 'r' + i, header: 60 + Math.floor(R() * 60),
             figs: FIGN.slice(0, nf).map(n => ({ name: n, h: 100 + Math.floor(R() * 500) })),
             overview: DETN.filter(() => R() < 0.8).map(n => ({ name: n, h: 30 + Math.floor(R() * 700) })),
             results: ['q1', 'q2', 'q3'].filter(() => R() < 0.7).map(n => ({ name: n, h: 50 + Math.floor(R() * 400) })) };
}
let intent = null, moved = true, pinCtl = null, exactN = 0, holdN = 0, misses = 0, overwrites = 0;
let readerView = null;   // the chain captured right after the reader's own last move (the cold truth)
load(randomRun(0));
pane.scrollTop = Math.floor(R() * maxTop());
for (let i = 1; i <= 400; i++) {
    // beforeSwap
    if (pinCtl) { pinCtl.stop(); pinCtl = null; }
    if (moved || !intent) { intent = A.capture(pane, container()); readerView = JSON.stringify(intent.chain); moved = false; }
    else if (JSON.stringify(intent.chain) !== readerView) overwrites++;
    // swap
    const run = randomRun(i);
    const lazy = run.figs.length && R() < 0.5;
    const realH = lazy ? run.figs.map(f => f.h) : null;
    if (lazy) run.figs.forEach(f => { f.h = 0; });        // images not loaded yet
    load(run);
    pinCtl = A.pin(pane, container, intent, () => { moved = true; pinCtl = null; });
    flushScroll();
    if (lazy) {                                            // images arrive, one by one
        pane.querySelectorAll('.figure-card .img').forEach((im, k) => { im.setAttribute('data-h', String(realH[k])); fireResize(); });
        flushScroll();
    }
    // oracle: deepest holdable level, recomputed cold from the DOM
    const chain = intent.chain;
    if (chain) {
        let e = container(), d = 0, els = [e];
        for (let k = 1; k < chain.length; k++) {
            const kids = A._children(e); let seen = 0, f = null;
            for (const c of kids) { if (A._keyOf(c) !== chain[k].key) continue; if (seen === chain[k].n) { f = c; break; } seen++; }
            if (!f || height(f) <= 0) break; els.push(f); e = f; d = k;
        }
        while (d > 0 && chain[d].within >= height(els[d])) d--;
        const want = contentTop(els[d]) + chain[d].within;
        if (chain[d].within < height(els[d]) && want <= maxTop()) {
            holdN++;
            const got = PANE_TOP - els[d].getBoundingClientRect().top;
            if (got === chain[d].within) exactN++; else { misses++; if (misses < 5) console.log('  E miss at switch', i, chain[d].key, got, chain[d].within); }
        }
    }
    // the reader sometimes moves (a real scroll: the pin sees a foreign scroll)
    if (R() < 0.25) {
        pane.scrollTop = Math.floor(R() * (maxTop() + 1));
        flushScroll();
        if (pinCtl) ok(false, 'E: reader scroll must end the pin (switch ' + i + ')');
        moved = true;   // app.js marks a move on a real scroll change (section H)
    }
}
console.log(`  E: ${exactN}/${holdN} holdable switches exact over 400 random switches`);
ok(misses === 0, `E: ${exactN}/${holdN} holdable switches exact, ${misses} misses`);
ok(holdN > 150, 'E: (fixture) enough holdable switches to mean something: ' + holdN);
ok(overwrites === 0, 'E: an untouched intent never drifted from the reader\'s own capture: ' + overwrites);

// ── I: the reading line is the sticky header's bottom edge ──────────────
// The verifier's KH repro, 1600x950, Results tab: the pane top (hidden under
// the 61 px header) sat in the margin between q1 and q2 while the visible
// line was already inside q2; the neighbour run's q1 was 102 px taller.
function lineY() {
    const h = pane.querySelector('.inspector-header');
    return h ? Math.max(PANE_TOP, h.getBoundingClientRect().bottom) : PANE_TOP;
}
function below(key) { return lineY() - el(key).getBoundingClientRect().top; }   // + = line inside it
const QRUN = (uid, q1, extra) => Object.assign({ uid, sticky: 61, header: 80, mb: 16,
    figs: [{ name: 'raw', h: 400 }], overview: [{ name: 'Experiment Info', h: 300 }],
    results: [{ name: 'q1', h: q1 }, { name: 'q2', h: 300 }, { name: 'q3', h: 300 }] }, extra || {});
{
    load(QRUN('i1', 300));
    pane.scrollTop = contentTop(el('det:q2')) - 8;                 // pane top 8 px into the 16 px gap
    ok(PANE_TOP > el('det:q1').getBoundingClientRect().bottom && PANE_TOP < el('det:q2').getBoundingClientRect().top,
       'I: (fixture) the pane top is in the gap between q1 and q2');
    ok(below('det:q2') === 53, 'I: (fixture) the visible line is 53 px into q2, got ' + below('det:q2'));
    ok(A._lineOf(pane) === lineY(), 'I: the module reads the line at the header bottom: ' + A._lineOf(pane));
    cap = A.capture(pane, container());
    const last = cap.chain[cap.chain.length - 1];
    ok(last.key === 'det:q2' && last.within === 53 && !last.gap,
       'I: the chain reaches the block under the VISIBLE line: ' + JSON.stringify(cap.chain));
    load(QRUN('i2', 402, { sticky: 83 }));                          // q1 102 px taller; the header wrapped
    res = A.apply(pane, container(), cap);
    ok(res.exact === true && below('det:q2') === 53,
       'I: on the neighbour run q2 is exactly where it was under the line, got ' + below('det:q2') + ' (' + JSON.stringify(res) + ')');
    load(QRUN('i3', 300));
    res = A.apply(pane, container(), cap);
    ok(res.exact === true && pane.scrollTop === contentTop(el('det:q2')) - 8, 'I: and home again is the same scrollTop');
}

// ── J: a reading line IN the gap between two blocks ──────────────────────
{
    load(QRUN('j1', 300));
    pane.scrollTop = contentTop(el('det:q2')) - 61 - 6;            // the line 6 px above q2, in the gap
    ok(below('det:q2') === -6 && below('det:q1') >= el('det:q1').getBoundingClientRect().height,
       'J: (fixture) the line is in the gap, 6 px above q2');
    cap = A.capture(pane, container());
    let last = cap.chain[cap.chain.length - 1];
    ok(last.key === 'det:q2' && last.within === -6 && last.gap === true && cap.chain[cap.chain.length - 2].key === 'sec:results',
       'J: held by the block BELOW, at a negative offset: ' + JSON.stringify(cap.chain));
    load(QRUN('j2', 402));
    res = A.apply(pane, container(), cap);
    ok(res.exact === true && below('det:q2') === -6, 'J: a taller q1 on the next run does not move q2 (got ' + below('det:q2') + ')');
    load(QRUN('j3', 150, { sticky: 83 }));
    res = A.apply(pane, container(), cap);
    ok(res.exact === true && below('det:q2') === -6, 'J: nor a shorter one under a taller header (got ' + below('det:q2') + ')');
    const again = A.capture(pane, container()).chain, a1 = again[again.length - 1];
    ok(a1.key === 'det:q2' && a1.within === -6 && a1.gap === true, 'J: deterministic: a capture of the landing is the same place: ' + JSON.stringify(a1));
    load(QRUN('j4', 300, { results: [{ name: 'q1', h: 300 }, { name: 'q3', h: 300 }] }));   // no q2 here
    res = A.apply(pane, container(), cap);
    ok(res.exact === false && res.key === 'sec:results', 'J: a run without that block falls back to the section, not exact: ' + res.key);
    // between two SECTIONS: held by the next section
    load(QRUN('j5', 300));
    pane.scrollTop = contentTop(el('sec:overview')) - 61 - 10;
    cap = A.capture(pane, container());
    last = cap.chain[cap.chain.length - 1];
    ok(cap.chain.length === 2 && last.key === 'sec:overview' && last.within === -10 && last.gap,
       'J: the gap between two sections is held by the next section: ' + JSON.stringify(cap.chain));
    load(QRUN('j6', 300, { figs: [{ name: 'raw', h: 777 }] }));
    res = A.apply(pane, container(), cap);
    ok(res.exact === true && below('sec:overview') === -10, 'J: and stays there when the figures above change height');
    // not a gap: non-landmark content at the line stays with the parent
    load(QRUN('j7', 300, { lead: 40 }));
    pane.scrollTop = contentTop(el('sec:results')) - 61 + 15;      // the line 15 px into a lead bar
    cap = A.capture(pane, container());
    last = cap.chain[cap.chain.length - 1];
    ok(last.key === 'sec:results' && last.within === 15, 'J: content at the line (a lead bar) is not a gap: ' + JSON.stringify(last));
    // not a gap: something rendered between the two blocks
    load(QRUN('j8', 300, { results: [{ name: 'q1', h: 300 }, { name: 'q2', h: 300, between: 20 }, { name: 'q3', h: 900 }] }));
    pane.scrollTop = contentTop(el('det:q1')) + 324 + 4 - 61;       // 4 px into the margin after q1, a <p> follows
    ok(below('det:q1') === 328, 'J: (fixture) the line is in q1\'s trailing margin');
    cap = A.capture(pane, container());
    last = cap.chain[cap.chain.length - 1];
    ok(last.key === 'sec:results' && !last.gap, 'J: a rendered element between the blocks is not a gap: ' + JSON.stringify(last));
}

// ── E2: randomized, sticky header + margins, an INDEPENDENT oracle ───────
{
    const R2 = rnd(20260927);
    const run2 = (i) => {
        const r = randomRun(i);
        r.sticky = 50 + Math.floor(R2() * 40);
        r.mb = [0, 8, 16][Math.floor(R2() * 3)];
        return r;
    };
    const LM = '[data-fvsec], .figure-card, details';
    const lkey = (e) => e.hasAttribute('data-fvsec') ? 'sec:' + e.getAttribute('data-fvsec')
        : e.classList.contains('figure-card') ? 'fig:' + e.querySelector('code').textContent
        : 'det:' + e.firstElementChild.firstChild.nodeValue;
    // the reader's view, read the way the real-Chrome probe reads it
    function view() {
        const L = lineY(), c = container();
        const all = [...c.querySelectorAll(LM)].map(e => ({ e, k: lkey(e), r: e.getBoundingClientRect() }))
            .filter(x => x.r.height > 0);
        const v = all.filter(x => x.r.top <= L && x.r.bottom > L).map(x => ({ k: x.k, off: L - x.r.top }));
        const contentAt = [...c.querySelectorAll('[data-h]')].some(e => {
            const r = e.getBoundingClientRect(); return r.height > 0 && r.top <= L && r.bottom > L; });
        const cr = c.getBoundingClientRect();
        if (!contentAt && cr.top <= L && cr.bottom > L) {
            const inside = v.length ? all.find(x => x.k === v[v.length - 1].k).e : c;
            const nxt = all.find(x => inside.contains(x.e) && x.e !== inside && x.r.top > L);
            if (nxt) v.push({ k: nxt.k, off: L - nxt.r.top, gap: true });
        }
        return { v, st: pane.scrollTop, top: cr.top > L };
    }
    function judge(ref) {
        const L = lineY(), c = container();
        const map = {};
        [...c.querySelectorAll(LM)].forEach(e => { if (e.getBoundingClientRect().height > 0) map[lkey(e)] = e; });
        for (let d = ref.v.length - 1; d >= 0; d--) {
            const lv = ref.v[d], e = map[lv.k];
            if (!e) continue;
            const r = e.getBoundingClientRect();
            if (lv.off >= r.height) continue;
            const need = pane.scrollTop + (r.top - L) + lv.off;
            if (need < 0 || need > maxTop()) return 'unreach';
            return (L - r.top) === lv.off ? 'exact' : 'miss ' + lv.k + ' ' + (L - r.top) + ' vs ' + lv.off;
        }
        return 'none';
    }
    let intent2 = null, moved2 = true, pin2 = null, ref = null, gaps = 0, n2 = 0, ex2 = 0, miss2 = [];
    load(run2(0));
    pane.scrollTop = Math.floor(R2() * maxTop());
    for (let i = 1; i <= 400; i++) {
        if (pin2) { pin2.stop(); pin2 = null; }
        if (moved2 || !intent2) {
            intent2 = A.capture(pane, container()); ref = view(); moved2 = false;
            if (ref.v.length && ref.v[ref.v.length - 1].gap) gaps++;
        }
        const run = run2(i);
        const lazy = run.figs.length && R2() < 0.5;
        const realH = lazy ? run.figs.map(f => f.h) : null;
        if (lazy) run.figs.forEach(f => { f.h = 0; });
        load(run);
        pin2 = A.pin(pane, container, intent2, () => { moved2 = true; pin2 = null; });
        flushScroll();
        if (lazy) {
            pane.querySelectorAll('.figure-card .img').forEach((im, k) => { im.setAttribute('data-h', String(realH[k])); fireResize(); });
            flushScroll();
        }
        if (!ref.top) {
            const j = judge(ref);
            if (j === 'exact') { n2++; ex2++; } else if (j.indexOf('miss') === 0) { n2++; miss2.push(i + ': ' + j); }
        }
        if (R2() < 0.3) {   // the reader moves; a third of the moves land the line in a gap on purpose
            const gapsNow = [...container().querySelectorAll(LM)].filter(e => e.hasAttribute('data-mb') && +e.getAttribute('data-mb') > 2);
            if (gapsNow.length && R2() < 0.35) {
                const g = gapsNow[Math.floor(R2() * gapsNow.length)];
                const r = g.getBoundingClientRect();
                pane.scrollTop = Math.max(0, Math.min(maxTop(), pane.scrollTop + (r.bottom + 1 + Math.floor(R2() * (+g.getAttribute('data-mb') - 1))) - lineY()));
            } else {
                pane.scrollTop = Math.floor(R2() * (maxTop() + 1));
            }
            flushScroll();
            moved2 = true;
        }
    }
    console.log(`  E2: ${ex2}/${n2} holdable switches exact over 400 random switches (${gaps} reader places in a gap)`);
    ok(miss2.length === 0, 'E2: every holdable switch exact by the independent reading, misses: ' + miss2.slice(0, 5).join(' | '));
    ok(n2 > 150 && gaps >= 10, 'E2: (fixture) enough holdable switches and gap places to mean something: ' + n2 + ', ' + gaps);
}

// ── G: a scroller inside the tab (the State tab's .json-tree) ──────────
function treeHTML(uid, nodes, lead) {
    // lead: a per-run block above the tree inside the scroller (differs by run)
    const n = nodes.map(p => `<div class="tree-node" data-path="${p}"><div class="tree-row" data-h="22"></div></div>`).join('');
    return `<div id="ds-detail-root" data-uid="${uid}"><div class="hdr" data-h="90"></div>
      <div class="dataset-tab-content" id="ds-tab-state"><div class="bar" data-h="60"></div>
        <div id="ds-state-tree-state" class="json-tree" data-maxh="400"><div data-h="${lead}"></div>${n}</div>
        <div data-h="1200"></div></div></div>`;
}
const P1 = []; for (let k = 0; k < 80; k++) P1.push('qubits.q' + k);
pane.innerHTML = treeHTML('t1', P1, 0);
scrollTop = 0;
const tree = () => pane.querySelector('#ds-state-tree-state');
tree().scrollTop = 22 * 30 + 7;                           // reader 7 px into qubits.q30, inside the tree
pane.scrollTop = 120;
cap = A.capture(pane, pane.querySelector('#ds-tab-state'));
ok(cap.inner.length === 1 && cap.inner[0].key === '#ds-state-tree-state', 'G: the tree scroller is captured: ' + JSON.stringify(cap.inner.map(i => i.key)));
ok(cap.chain.every(c => c.key.indexOf('path:') !== 0), 'G: the pane chain does not descend into the inner scroller');
const leaf = cap.inner[0].chain[cap.inner[0].chain.length - 1];
ok(leaf.key === 'path:qubits.q30' && leaf.within === 7, 'G: inner anchor is the tree path: ' + JSON.stringify(leaf));
pane.innerHTML = treeHTML('t2', ['extra.a', 'extra.b'].concat(P1), 55);   // another run: 2 more nodes + a 55 px lead
scrollTop = 0;
res = A.apply(pane, pane.querySelector('#ds-tab-state'), cap);
const q30 = [...pane.querySelectorAll('.tree-node')].find(n => n.getAttribute('data-path') === 'qubits.q30');
const tr = tree().getBoundingClientRect();
ok(tr.top - q30.getBoundingClientRect().top === 7, 'G: qubits.q30 sits 7 px above the tree top again, got ' + (tr.top - q30.getBoundingClientRect().top));
ok(res.inner[0].exact === true && pane.scrollTop === 120, 'G: inner exact and the pane offset kept');
pane.innerHTML = treeHTML('t3', ['other.x', 'other.y'], 0);        // a run without that path
res = A.apply(pane, pane.querySelector('#ds-tab-state'), cap);
ok(res.inner[0].key === '@px' || res.inner[0].depth === 0, 'G: no path -> the raw offset fallback');

// ── F: app.js wiring ─────────────────────────────────────────────────────
const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
// The place is read in the CAPTURE phase: a bubble-phase listener registered
// earlier (_plotSwapTeardown) purges Plotly graphs first, the pane shrinks and
// Chrome clamps scrollTop before a bubble listener can read it (measured).
const capStart = app.indexOf("Queue item 6: the reader's PLACE is captured in the CAPTURE phase");
// the listener ends at the FIRST top-level close after it -- whichever form
// it takes; slicing to the next '}, true);' would borrow another listener's
const _endOf = (s, from) => { const a = s.indexOf('\n});', from), b = s.indexOf('\n}, true);', from);
    return (b >= 0 && (a < 0 || b < a)) ? b + 10 : a + 4; };
const bs = app.slice(capStart, _endOf(app, capStart));
ok(capStart > 0 && /^document\.addEventListener\('htmx:beforeSwap', function\(evt\) \{/m.test(bs) && /\}, true\);$/.test(bs),
   'F: the place is captured by a CAPTURE-phase beforeSwap listener');
ok(/if \(dsRoot && \(_dsScroll\.userMoved \|\| tabChanged \|\| !_dsScroll\.intent\)\)/.test(bs),
   'F: beforeSwap re-captures the intent only when the reader moved (scroll or tab)');
ok(bs.indexOf('DsScrollAnchor.capture(pane, shown.container)') > 0, 'F: the capture is the landmark chain');
const bsTrees = app.slice(app.indexOf('// Capture inspector state just before swap'), app.indexOf('// Restore qubit/pair inspector state after HTMX swap'));
ok(/if \(dsRoot && _dsScroll\.recaptured\)/.test(bsTrees) && bsTrees.indexOf('DsScrollAnchor.capture') < 0,
   'F: the trees follow the same re-capture decision; the place is not re-read after the teardown');
const as = app.slice(app.indexOf('// Restore state after HTMX loads new dataset detail'), app.indexOf('Global new-run detection poller'));
ok(as.indexOf('window.DsScrollAnchor.pin(pane') > 0 && as.indexOf('window.DsScrollAnchor.pin(pane') < as.indexOf('setTimeout(function() {'),
   'F: the restore is pinned IN the swap, before any timer');
ok(!/p\.scrollTop = _dsSticky\.scrollTop/.test(as) && !/_restoreSectionScroll/.test(as),
   'F: no delayed pixel restore is left to fight the pin');
ok(/switchDatasetTab\(it\.tab, link\)/.test(as), 'F: the tab the reader is on is restored');
ok(/'wheel', 'touchstart', 'mousedown', 'keydown'/.test(app), 'F: reader input ends the pin');
ok(as.indexOf('_dsScroll.landedTab = _dsShownTab(pane).tab;') > as.indexOf('window.DsScrollAnchor.pin(pane'),
   'F: the restore records the tab it landed on (after the pin, so a run without the tab records Full View)');
const base = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
ok(base.indexOf("asset_url('ds-scroll-anchor.js')") > 0 &&
   base.indexOf("asset_url('ds-scroll-anchor.js')") < base.indexOf("asset_url('app.js')"),
   'F: the module is a core script loaded before app.js');
const tpl = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', '_dataset_detail.html'), 'utf8');
['full', 'overview', 'results', 'figures', 'prev', 'interactive', 'data', 'state'].forEach(t =>
    ok(tpl.indexOf(`data-ds-tab="${t}" onclick="switchDatasetTab('${t}', this)"`) > 0, 'F: tab link carries data-ds-tab=' + t));

// ── H: app.js's own listeners, executed (verifier P1 + P3) ───────────────
// The slices are the shipped code, run in this realm with the fake layout.
{
    const app2 = app.replace(/\r/g, '');
    const sl = (a, b) => { const s = app2.indexOf(a); const e = app2.indexOf(b, s);
        if (s < 0 || e < 0) throw new Error('H: slice not found: ' + a); return app2.slice(s, e + b.length); };
    const src = [
        "var _DS_COMBINED_TABS = ['full', 'overview', 'results', 'figures'];",
        sl('var _dsScroll = {', '};'),
        sl('function _dsShownTab(pane) {', '\n}'),
        sl('(function() {\n    function _dsPaneOf', '\n})();'),
        bs.slice(bs.indexOf('document.addEventListener')).replace(/\r/g, ''),
        'return { get s() { return _dsScroll; } };',
    ].join('\n');
    let W;
    try {
        W = new Function('window', 'document', src)(window, document);
    } catch (e) { ok(false, 'H: the app.js slices run: ' + e.message); }
    if (W) {
        const beforeSwap = () => pane.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: pane } }));
        const S = () => W.s;
        const restore = () => {   // what afterSwap does: pin the intent, record the landing
            const s = S();
            s.pin = A.pin(pane, container, s.intent.anchor, () => { s.userMoved = true; s.pin = null; });
            s.userMoved = false;
            s.landedTab = 'full';
        };
        const click = () => pane.querySelector('.hdr').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
        // the reader, deep inside RUN1 (Parameters), by a real scroll
        load(RUN1);
        pane.scrollTop = 0;
        pendingScroll = false;
        pane.scrollTop = contentTop(el('det:Parameters')) + 333;
        flushScroll();
        ok(S().userMoved === true, 'H: a real scroll marks a move');
        beforeSwap();
        ok(S().recaptured === true, 'H: that move re-captures the intent');
        const readerIntent = JSON.stringify(S().intent);
        // P1: a short run clamps the restore; one click on a blank spot, no scroll
        load(SHORT);
        restore();
        ok(S().pin && S().pin.last && S().pin.last.exact === false, 'H: (fixture) the short run clamps the restore');
        flushScroll();                            // the clamp + the pin's own write
        ok(S().userMoved === false, "H/P3: the pin's own (clamped) write is not a reader move");
        click();
        ok(S().pin === null, 'H: reader input ends the pin');
        ok(S().userMoved === false, 'H/P1: a click with no scroll is not a move');
        beforeSwap();
        ok(S().recaptured === false && JSON.stringify(S().intent) === readerIntent,
           "H/P1: the next switch keeps the reader's intent (not the clamped landing)");
        load(RUN2);
        restore();
        flushScroll();
        ok(offsetIn('det:Parameters') === 333, 'H/P1: back on a tall run the place is exact, got ' + offsetIn('det:Parameters'));
        // P3: the pin writes, the reader's click stops it BEFORE that write's
        // scroll event fires (Chrome dispatches it a frame later)
        pane.querySelector('.figure-card .img').setAttribute('data-h', '900');
        fireResize();                             // the pin re-applies -> a pending scroll event
        ok(pendingScroll === true, 'H: (fixture) the re-apply really scrolled');
        click();
        flushScroll();                            // fires after the pin has stopped
        ok(S().userMoved === false, "H/P3: a stopped pin's last write is still not a reader move");
        beforeSwap();
        ok(S().recaptured === false && JSON.stringify(S().intent) === readerIntent, 'H/P3: intent kept');
        // a real scroll after a restore DOES re-capture
        load(RUN1);
        restore();
        flushScroll();
        pane.scrollTop = pane.scrollTop - 150;
        flushScroll();
        ok(S().userMoved === true, 'H: a reader scroll after a restore marks a move');
        beforeSwap();
        ok(S().recaptured === true && JSON.stringify(S().intent) !== readerIntent, 'H: and re-captures');
        // an inner scroller (a JSON tree) scrolled by the reader is a move too
        load(RUN1);
        restore();
        flushScroll();
        ok(S().userMoved === false, 'H: (fixture) clean after the restore');
        const tr = document.createElement('div');
        tr.className = 'json-tree'; tr.setAttribute('data-maxh', '100');
        tr.innerHTML = '<div data-h="500"></div>';
        container().appendChild(tr);
        tr.scrollTop = 60;
        tr.dispatchEvent(new window.Event('scroll'));
        ok(S().userMoved === true, 'H: an inner scroller moved by the reader is a move');
        // a tab the reader picked, with no scroll change, is a move
        beforeSwap();
        load(RUN1);
        restore();
        flushScroll();
        S().landedTab = 'figures';                 // landed on Figures; the reader is now on Full View
        beforeSwap();
        ok(S().recaptured === true, 'H: a tab the reader picked re-captures the intent');
        load(RUN1);
        restore();
        flushScroll();
        beforeSwap();
        ok(S().recaptured === false, 'H: same tab, no scroll: the intent is kept');
    }
}

// ── K: the tab INTENT survives a run that lacks the tab (w8/dstab) ───────
// The real capture listener, the real afterSwap restore (its synchronous
// part, up to the settle timer) and the real switchDatasetTab, executed.
// The reader is on tab T deep in run A; run C has no T (no HDF5 -> no
// Interactive; State N/A): C shows Full View at its top WITHOUT rewriting
// the intent, and the next run that has T lands on T at the same place --
// unless the reader acted on C (a scroll, a tab press: even Full View, the
// tab C already shows). A run opened into a pane that showed no run (after
// a close) is a fresh open: Full View at the top (KH rig: 194 px down).
{
    const app3 = app.replace(/\r/g, '');
    const sl = (a, b, from) => { const s = app3.indexOf(a, from || 0); const e = app3.indexOf(b, s);
        if (s < 0 || e < 0) throw new Error('K: slice not found: ' + a); return app3.slice(s, e + b.length); };
    const asStart = app3.indexOf("document.addEventListener('htmx:afterSwap', function(evt) {",
        app3.indexOf('// Restore state after HTMX loads new dataset detail'));
    const asEnd = app3.indexOf('\n    setTimeout(function() {\n        // 1. The tab + place', asStart);
    const src = [
        "var _DS_COMBINED_TABS = ['full', 'overview', 'results', 'figures'];",
        'var _pendingTreeHighlight = null;',
        'function syncSidebarTreeHighlight() {}',
        'function _h5Panel() { return document.getElementById("inspector-pane"); }',
        'function loadDatasetInteractive() {} function _observeInteractiveResize() {} function loadPrevDiffInto() {}',
        'var htmx = undefined;',
        sl('var _dsSticky = {', '\n};'),
        sl('var _dsScroll = {', '};'),
        sl('window.switchDatasetTab = function(tabName, linkEl) {', '\n};'),
        'var switchDatasetTab = window.switchDatasetTab;',   // app.js calls it bare
        sl('function _dsShownTab(pane) {', '\n}'),
        sl('(function() {\n    function _dsPaneOf', '\n})();'),
        bs.slice(bs.indexOf('document.addEventListener')).replace(/\r/g, ''),
        (asStart > 0 && asEnd > asStart) ? app3.slice(asStart, asEnd) + '\n});' : 'throw new Error("K: afterSwap slice")',
        'return { get s() { return _dsScroll; } };',
    ].join('\n');
    let K;
    try { K = new Function('window', 'document', 'setTimeout', src)(window, document, function () {}); }
    catch (e) { ok(false, 'K: the app.js slices run: ' + e.message); }
    if (K) {
        const IF = (hs) => hs.map((h, i) => ({ name: 'f' + (i + 1), h }));
        const run = (uid, o) => Object.assign({ uid, ifigs: null, state: true, figH: 600, infoH: 1600 }, o);
        const KA = run('ka', { ifigs: IF([400, 400, 500, 400]) });
        const KB = run('kb', { ifigs: IF([300, 450, 500, 400]), infoH: 1900 });   // other heights above f3
        const KC = run('kc', { ifigs: null, infoH: 2600 });                         // no Interactive
        const KN = run('kn', { ifigs: IF([400, 400, 500, 400]), state: false });     // State N/A
        const KS = run('ks', { ifigs: IF([300, 200]) });                             // Interactive, but short
        const html = (o) => {
            const link = (t, has) => has ? `<a data-ds-tab="${t}"${t === 'full' ? ' class="active"' : ''} data-h="0"></a>`
                                         : (t === 'state' ? '<a class="disabled" data-h="0"></a>' : '');
            return `<div id="ds-detail-root" data-uid="${o.uid}">
              <div class="hdr" data-h="80"></div>
              <nav class="dataset-tabs" data-h="40">${link('full', 1)}${link('figures', 1)}${link('interactive', !!o.ifigs)}${link('state', o.state)}</nav>
              <div class="dataset-tab-content" id="ds-tab-combined" data-view="full">
                <section data-fvsec="figures"><div data-h="${o.figH}"></div></section>
                <section data-fvsec="overview"><details open class="detail-section"><summary data-h="24">Experiment Info</summary><div data-h="${o.infoH}"></div></details></section>
              </div>
              <div class="dataset-tab-content hidden" id="ds-tab-interactive">${(o.ifigs || []).map(f =>
                  `<div class="ds-interactive-fig" data-fig="${f.name}" data-h="${f.h}"></div>`).join('')}</div>
              <div class="dataset-tab-content hidden" id="ds-tab-state">${o.state ? '<details open><summary data-h="24">state.json</summary><div data-h="2400"></div></details>' : ''}</div>
            </div>`;
        };
        const ev = (name) => pane.dispatchEvent(new window.CustomEvent(name, { bubbles: true, detail: { target: pane } }));
        const swap = (o) => {   // one htmx swap into the pane: beforeSwap -> new content -> afterSwap
            ev('htmx:beforeSwap');
            pane.innerHTML = html(o);
            if (scrollTop > maxTop()) pane.scrollTop = maxTop();
            ev('htmx:afterSwap');
            flushScroll();
        };
        const shown = () => { const a = pane.querySelector('.dataset-tabs a.active[data-ds-tab]'); return a ? a.getAttribute('data-ds-tab') : null; };
        const press = (t) => window.switchDatasetTab(t, pane.querySelector(`.dataset-tabs a[data-ds-tab="${t}"]`));   // the link's onclick
        const ifig = (n) => pane.querySelector(`.ds-interactive-fig[data-fig="${n}"]`);
        const offIfig = (n) => PANE_TOP - ifig(n).getBoundingClientRect().top;
        const readerAt = (tab, n, within) => {   // the reader picks `tab` and scrolls to `within` px into landmark n
            press(tab); flushScroll();
            const tgt = tab === 'interactive' ? ifig(n) : pane.querySelector('#ds-tab-state details');
            pane.scrollTop = scrollTop + (tgt.getBoundingClientRect().top - PANE_TOP) + within;
            flushScroll();
        };
        const S = () => K.s;

        // a first open into the empty pane: Full View, top
        pane.innerHTML = ''; scrollTop = 0;
        swap(KA);
        ok(shown() === 'full' && pane.scrollTop === 0, 'K: first open lands on Full View at the top');
        readerAt('interactive', 'f3', 55);
        ok(offIfig('f3') === 55 && S().userMoved === true, 'K: (fixture) the reader is 55 px into f3 on Interactive');
        const homeTop = pane.scrollTop;
        // C lacks Interactive: Full View at its top, the intent untouched
        swap(KC);
        ok(shown() === 'full' && pane.scrollTop === 0, 'K1: a run without the tab shows Full View at its top, got ' + shown() + '@' + pane.scrollTop);
        ok(S().intent && S().intent.tab === 'interactive', 'K1: the intent still names Interactive');
        swap(KB);
        ok(shown() === 'interactive', 'K1: the next run WITH the tab lands on it, got ' + shown());
        ok(offIfig('f3') === 55, 'K1: ...at the same place (55 px into f3), got ' + offIfig('f3'));
        swap(KC); swap(KA);
        ok(shown() === 'interactive' && pane.scrollTop === homeTop && offIfig('f3') === 55,
           'K1: home again through C: identical scrollTop ' + pane.scrollTop + ' vs ' + homeTop);

        // a click on a blank spot of C is not a choice
        swap(KC);
        pane.querySelector('.hdr').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
        swap(KB);
        ok(shown() === 'interactive' && offIfig('f3') === 55, 'K3: a click (no scroll) on C keeps the intent, got ' + shown());

        // Full View PRESSED on C (the tab it already shows) is the reader's choice
        swap(KC);
        press('full');
        swap(KB);
        ok(shown() === 'full', 'K2: Full View pressed on the run without the tab replaces the intent, got ' + shown());
        ok(S().intent && S().intent.tab === 'full', 'K2: the intent now names Full View');
        // ...and pressing the tab a run ALREADY landed on (the intent's own tab)
        // is not a new place: on a run too short to hold it, a re-press must
        // not re-capture the clamped landing
        readerAt('interactive', 'f3', 55);
        swap(KS);
        ok(shown() === 'interactive' && S().pin && S().pin.last && S().pin.last.exact === false,
           'K2: (fixture) the short run lands on Interactive, clamped');
        press('interactive');                      // re-press the tab it restored
        swap(KB);
        ok(offIfig('f3') === 55, 'K2: re-pressing the restored tab on a clamped run keeps the intent, got ' + offIfig('f3'));

        // a reader scroll on C replaces it (Full View at C's place)
        swap(KC);
        pane.scrollTop = 700; flushScroll();
        swap(KB);
        ok(shown() === 'full' && S().intent.tab === 'full', 'K4: a scroll on the run without the tab is a new place, got ' + shown());

        // State N/A: the same for the State tab
        swap(KA);
        readerAt('state', null, 333);
        const stTop = pane.scrollTop;
        swap(KN);
        ok(shown() === 'full' && pane.scrollTop === 0, 'K5: a run with State N/A shows Full View at its top, got ' + shown());
        swap(KA);
        ok(shown() === 'state' && pane.scrollTop === stTop, 'K5: back on a run with State: same tab, same place ' + pane.scrollTop + ' vs ' + stTop);

        // a close, then an open: fresh -- not the intent, not the emptied pane's stale offset
        swap(KB);
        readerAt('interactive', 'f3', 55);
        pane.innerHTML = '';                       // closeInspector(); Chrome kept the old offset (KH: 194)
        ok(scrollTop > 0, '(fixture) the emptied pane still holds the old offset');
        swap(KA);
        ok(shown() === 'full' && pane.scrollTop === 0, 'K6: open after a close lands on Full View at the top, got ' + shown() + '@' + pane.scrollTop);
        swap(KB);
        ok(shown() === 'full', 'K6: ...and the next switch keeps THAT view (Full View), got ' + shown());
        readerAt('interactive', 'f3', 55);
        pane.innerHTML = '';
        swap(KB);                                  // the SAME run re-opened after the close
        ok(shown() === 'full' && pane.scrollTop === 0, 'K6: the same run re-opened after a close: Full View at the top, got ' + shown() + '@' + pane.scrollTop);
    }
}

console.log(`ds_scroll_anchor_selfcheck: ${asserts - fails}/${asserts} ok (${asserts} assertions)`);
process.exit(fails ? 1 : 0);
