/* v1.5.1 (T2-F3): a split dragged in an older SM is carried over once.
 *
 * SM 1.3.0 kept a dragged split only in quam_split_sizes; 1.5 reads the last
 * action from quam_split_user, so after an upgrade the dataset inspector
 * opened at the default instead of where the user had dragged it. The driver
 * (tests/test_v151_p2_split_migrate.py) renders a REAL full page through the
 * Flask test client; its split script runs here (with the page's own
 * __smBase registry) against a recording Split stub.
 *
 * Pinned: an old drag becomes the last action ONCE; one of SM's own fixed
 * positions is never taken as a choice; a recorded choice wins; after the
 * one migration, a later SM move is never migrated (last action decides).
 *
 * Run: node tests/v151_split_migrate_selfcheck.cjs <fixture.json>
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

async function boot(storage) {
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">t</div><div id="inspector-pane"></div>'
        + '<div id="status-bar"></div></body></html>',
        { url: 'https://example.invalid/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    await new Promise((r) => setImmediate(r));
    Object.keys(storage || {}).forEach((k) => w.localStorage.setItem(k, storage[k]));
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
    return env;
}

(async function () {
    ok(!!registry && !!split, 'the real page carries the split script and its registry');
    const OLD = [38.6664, 61.3336];
    // 1. an old drag (only quam_split_sizes) becomes the last action, once
    const a = await boot({ quam_split_sizes: JSON.stringify(OLD) });
    const opened = a.open();
    ok(same(opened, OLD), 'a run opens where the old version had it dragged: ' + JSON.stringify(opened));
    ok(same(a.user(), OLD), 'and that drag is now the remembered last action');
    // 5. an SM move after the migration is never migrated on the next load
    const after = a.storage();
    after.quam_split_sizes = JSON.stringify(SPLIT.collapsedSizes);
    after.quam_split_user = JSON.stringify('down');
    const a2 = await boot(after);
    ok(a2.user() === 'down', 'the migration ran once: a later choice is kept as it is');
    // 2. SM's own fixed position is not a choice
    const b = await boot({ quam_split_sizes: JSON.stringify(SPLIT.collapsedSizes) });
    const bOpen = b.open();
    ok(b.user() === null && same(bOpen, SPLIT.datasetExpandedSizes),
       "SM's own collapsed position is not taken as a choice: " + JSON.stringify(bOpen));
    // 3. a recorded choice wins over the old key
    const c = await boot({ quam_split_sizes: JSON.stringify(OLD), quam_split_user: JSON.stringify([40, 60]) });
    ok(same(c.open(), [40, 60]) && same(c.user(), [40, 60]), 'a recorded last action wins over the old key');
    // 4. never twice: once marked, an old-looking key stays where it is
    const e = await boot({ quam_split_sizes: JSON.stringify(OLD), quam_split_migrated: '1' });
    e.open();
    ok(e.user() === null, 'a browser already migrated is not migrated again');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((err) => { console.error('UNCAUGHT', err && err.stack || err); process.exit(1); });
