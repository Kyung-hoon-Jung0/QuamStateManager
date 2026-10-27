/* v1.5.5 bug 5 -- with no remembered split choice, the first run after SM
 * starts opens at the up arrow's default height, never collapsed at the bottom.
 * A stale collapsed current split (SM's own move, or an old page's) is neither
 * migrated into the user's choice nor applied to a panel open at page load.
 * Driven by tests/test_v155_bugs.py on a REAL rendered page.
 * Run: node tests/v155_split_default_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const page = FX.page;
const scripts = [...page.slice(page.indexOf('<body')).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
const registry = scripts.find((s) => s.includes('registry.replace = function'));
const split = scripts.find((s) => s.includes("__smBase.replace('split')"));
const SPLIT = FX.split;
const RUN = '<div id="ds-detail-root" data-uid="run-a">run</div>';

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const same = (a, b) => !!(a && b) && Math.abs(a[0] - b[0]) < 0.01 && Math.abs(a[1] - b[1]) < 0.01;

async function boot(storage, preload) {
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">t</div><div id="inspector-pane"></div>'
        + '<div id="status-bar"></div></body></html>',
        { url: 'https://example.invalid/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    await new Promise((r) => setImmediate(r));
    Object.keys(storage || {}).forEach((k) => w.localStorage.setItem(k, storage[k]));
    if (preload) d.getElementById('inspector-pane').innerHTML = RUN;
    w.UI_CONFIG = { split: JSON.parse(JSON.stringify(SPLIT)) };
    const calls = [];
    w.Split = (selectors, options) => {
        const panes = selectors.map((s) => d.querySelector(s));
        const gutter = d.createElement('div');
        gutter.className = 'gutter gutter-vertical';
        panes[0].after(gutter);
        const inst = { pairs: [{ gutter }], options, getSizes: () => options.sizes.slice(), destroy: () => {} };
        calls.push(inst);
        return inst;
    };
    w.eval(registry);
    w.eval(split);
    const env = {
        w, d,
        open() {
            const t = d.getElementById('inspector-pane');
            t.innerHTML = RUN;
            t.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: t } }));
            return Array.from(calls[calls.length - 1].options.sizes);
        },
        user: () => JSON.parse(w.localStorage.getItem('quam_split_user') || 'null'),
        storage() {
            const out = {};
            for (let i = 0; i < w.localStorage.length; i++) out[w.localStorage.key(i)] = w.localStorage.getItem(w.localStorage.key(i));
            return out;
        },
    };
    env.calls = calls;
    return env;
}

(async function () {
    ok(!!registry && !!split, 'the real page carries the split script and its registry');
    const EXP = SPLIT.datasetExpandedSizes;
    // 1. a collapsed old split is not taken as the user's choice
    for (const old of [[100, 0], [96.5, 3.5], SPLIT.collapsedSizes.map((x, i) => i ? x - 2 : x + 2)]) {
        const a = await boot({ quam_split_sizes: JSON.stringify(old) });
        const o = a.open();
        ok(a.user() === null && same(o, EXP),
           'a collapsed current split ' + JSON.stringify(old) + ' is no choice: the first run opens at ' + JSON.stringify(o));
    }
    // 2. a run already in the panel at page load, no choice, a stale collapse as the current split
    await new Promise((r) => setTimeout(r, 0));
    const b = await boot({ quam_split_sizes: JSON.stringify(SPLIT.collapsedSizes), quam_split_migrated: '1' }, true);
    await new Promise((r) => setTimeout(r, 30));
    const first = b.calls.length ? Array.from(b.calls[0].options.sizes) : null;
    ok(first && same(first, EXP), 'a run open at page load with no choice opens at the default height: ' + JSON.stringify(first));
    ok(b.user() === null, 'and that is not recorded as a choice');
    // 2b. a browser that ran the 1.5.1 migration and kept a collapsed split as its "choice" is healed once
    const h = await boot({ quam_split_migrated: '1', quam_split_user: JSON.stringify([96.5, 3.5]) });
    const ho = h.open();
    ok(same(ho, EXP) && h.user() === null, 'a collapsed remembered split from an earlier migration is dropped once: ' + JSON.stringify(ho));
    const h2 = await boot({ quam_split_migrated: '1', quam_split_healed_v155: '1', quam_split_user: JSON.stringify([96.5, 3.5]) });
    ok(same(h2.open(), [96.5, 3.5]), 'only once: a low drag made after the heal is kept');
    const h3 = await boot({ quam_split_migrated: '1', quam_split_user: JSON.stringify([40, 60]) });
    ok(same(h3.open(), [40, 60]), 'the heal leaves a normal remembered drag alone');
    // 3. a real choice still wins
    const c = await boot({ quam_split_user: JSON.stringify('down') }, true);
    await new Promise((r) => setTimeout(r, 30));
    ok(c.calls.length && same(c.calls[0].options.sizes, SPLIT.collapsedSizes), 'a remembered down-arrow choice still decides');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((err) => { console.error('UNCAUGHT', err && err.stack || err); process.exit(1); });
