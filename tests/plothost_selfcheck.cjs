/* docs/124 M-1 — PlotHost.resizeWithin must not kill the window-resize path.
 *
 * Plotly 2.35.2's width relayout implies autosize=null AND pins the OTHER
 * dimension, and Plots.resize (the responsive:true window handler) permanently
 * rejects once layout.width && layout.height are both set — so the shipped
 * bare `relayout({width})` froze every chart it touched against window
 * resizes forever (executed on the real chip: 168px-clipped /topology bar
 * charts, docs/124 M-1; the pre-campaign call was a no-op and therefore
 * accidentally protective — c4df8c7 introduced the freeze by making it real).
 *
 * The fix is snapshot-restore (winner of an executed 6-candidate × 3-shape
 * design probe, docs/125 fix 5): apply the width, then hand gd.layout back
 * exactly as the caller wrote it — fullLayout keeps the correction, and the
 * layout the window path judges is byte-identical to an untouched chart's.
 *
 * This file pins the CONTRACT against a 2.35.2-faithful fake (the implied
 * edits are simulated exactly), plus the §8 pin-gap the claim audit flagged:
 * _graphDivs must include its own root node (an outerHTML swap can replace
 * exactly the graph div).
 *
 * Run: node tests/plothost_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator',
    { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
global.fetch = () => new Promise(() => {});
Object.defineProperty(window, 'fetch',
    { value: global.fetch, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

// A 2.35.2-faithful fake: a width relayout performs the implied edits exactly
// — deletes layout.autosize and PINS layout.height from fullLayout — so a
// resizeWithin that failed to restore would leave the frozen state this fake
// makes visible. (Verified against the bundled minified source by the design
// probe: `E("autosize",null); E(J, fullLayout[J])`.)
const relayouts = [];
const fake = {
    relayout: function (el, upd) {
        relayouts.push({ el: el, upd: upd });
        el.layout = el.layout || {};
        el._fullLayout = el._fullLayout || {};
        if (upd && typeof upd.width === 'number') {
            el.layout.width = upd.width;
            delete el.layout.autosize;
            el.layout.height = (el._fullLayout.height !== undefined)
                ? el._fullLayout.height : 450;
            el._fullLayout.width = upd.width;
        }
        return Promise.resolve();
    },
    newPlot: function () { return Promise.resolve(); },
    react: function () { return Promise.resolve(); },
    purge: function () {},
    Plots: { resize: function () { return Promise.resolve(); } },
};
window.Plotly = fake;
global.Plotly = fake;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const flush = () => new Promise((r) => setTimeout(r, 20));

function mkChart(layout, fullLayout, w) {
    const holder = window.document.createElement('div');
    const el = window.document.createElement('div');
    el.className = 'js-plotly-plot';
    el.layout = layout;
    el._fullLayout = fullLayout;
    el.data = [{}];
    holder.appendChild(el);
    window.document.body.appendChild(holder);
    let wv = w;
    Object.defineProperty(el, 'offsetParent', { get: () => holder });
    Object.defineProperty(el, 'clientWidth', { get: () => wv });
    el.__setW = (x) => { wv = x; };
    return el;
}

(async function main() {

// ── the snapshot-restore contract, on the three real chart shapes ─────────
// S1: declared height (Trends 300 / the /topology bar charts) — the shape the
// freeze clipped on the real chip.
const s1 = mkChart({ height: 300 }, { width: 600, height: 300 }, 800);
window.PlotHost.resizeWithin(s1.parentElement);
await flush();
ok(relayouts.length === 1 && relayouts[0].upd.width === 800,
   'S1: the width is applied through relayout (' + JSON.stringify(relayouts[0] && relayouts[0].upd) + ')');
ok(s1._fullLayout.width === 800, 'S1: fullLayout keeps the correction (rendered width moved)');
ok(!('width' in s1.layout),
   'S1: layout.width is ABSENT again after the touch (the Plots.resize gate cannot arm)');
ok(s1.layout.height === 300,
   'S1: the caller\'s declared layout.height survives (got ' + s1.layout.height + ')');
ok(!('autosize' in s1.layout), 'S1: no autosize key invented');

// S2: fully-auto chart.
const s2 = mkChart({ autosize: true }, { width: 600, height: 450 }, 900);
window.PlotHost.resizeWithin(s2.parentElement);
await flush();
ok(s2.layout.autosize === true && !('width' in s2.layout) && !('height' in s2.layout),
   'S2: an autosize chart\'s layout returns to exactly {autosize:true} (got ' +
   JSON.stringify(s2.layout) + ')');
ok(s2._fullLayout.width === 900, 'S2: and its rendered width moved');

// S3: no sizing keys at all (CSS-height holder).
const s3 = mkChart({}, { width: 600, height: 340 }, 700);
window.PlotHost.resizeWithin(s3.parentElement);
await flush();
ok(!('width' in s3.layout) && !('height' in s3.layout) && !('autosize' in s3.layout),
   'S3: a keyless layout stays keyless (got ' + JSON.stringify(s3.layout) + ')');

// ── repeated touches keep seeing the restored layout (probe gotcha: the
//    snapshot is taken INSIDE the chain) ──────────────────────────────────
const before = relayouts.length;
window.PlotHost.resizeWithin(s1.parentElement);   // fullLayout 800 == clientWidth 800
await flush();
ok(relayouts.length === before,
   'idempotence: a touch at the already-matched width is skipped (<2px gate)');
s1.__setW(640);
window.PlotHost.resizeWithin(s1.parentElement);
await flush();
ok(s1._fullLayout.width === 640 && s1.layout.height === 300 && !('width' in s1.layout),
   'repeat: a second real touch still restores (height=' + s1.layout.height +
   ', layout.width absent=' + !('width' in s1.layout) + ')');

// ── §8 pin gap: _graphDivs includes its own root ──────────────────────────
const rootSelf = mkChart({}, { width: 500, height: 200 }, 555);
const n = window.PlotHost.resizeWithin(rootSelf);   // the ROOT IS the graph div
await flush();
ok(n === 1 && rootSelf._fullLayout.width === 555,
   '_graphDivs includes its own root node (an outerHTML swap can replace exactly the graph div)');

// ── retheme selects STRUCTURALLY (docs/124 M-6 / docs/125 round 2) ────────
// plot-theme.js used to iterate '.js-plotly-plot' only, so a chart whose
// class was stripped silently never rethemed. It now rides PlotHost.graphDivs.
{
    const themeSrc = fs.readFileSync(
        path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'plot-theme.js'),
        'utf8');
    window.eval(themeSrc);
    const stripped = mkChart({}, { width: 400, height: 200 }, 400);
    stripped.className = 'i-lost-my-class';           // the shipped corpse shape
    const inner = window.document.createElement('div');
    inner.className = 'plot-container plotly';        // Plotly's own child
    stripped.appendChild(inner);
    const before = relayouts.length;
    window.PlotTheme.retheme();
    ok(relayouts.slice(before).some((r) => r.el === stripped),
       'retheme reaches a class-stripped chart (structural graphDivs, not the bare class)');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})().catch(function (e) {
    console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
    process.exit(1);
});
