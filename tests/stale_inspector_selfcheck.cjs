/* P1-1 (UX round) -- a page's entity inspector does not cover the next page.
 *
 * Runs the REAL split script from base.html (the same extraction as
 * split_arrows_selfcheck) in jsdom. Sidebar navigation to a page that cannot
 * show the open inspector's entity closes it (the pane is emptied and hidden:
 * the page gets its full height); a page that can show it keeps it; a dataset
 * run keeps its own rule (collapsed, not closed); and the user's remembered
 * split (quam_split_user) is never written by the close -- the next inspector
 * opens where the user's last drag or arrow put it.
 *
 * Run: node tests/stale_inspector_selfcheck.cjs   (driven by tests/test_v151_ux_stale_inspector.py)
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const root = path.resolve(__dirname, '..');
const base = fs.readFileSync(path.join(root, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const bodyScripts = [...base.slice(base.indexOf('<body')).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    .filter(m => !/\bsrc\s*=|application\/json/i.test(m[1])).map(m => m[2]);
const registryScript = bodyScripts.find(s => s.includes('registry.replace = function'));
const splitScript = bodyScripts.find(s => s.includes("__smBase.replace('split')"));
assert.ok(registryScript && splitScript, 'the split scripts are found');

const SPLIT = { defaultSizes: [55, 45], expandedSizes: [15, 85], collapsedSizes: [85, 15],
                datasetExpandedSizes: [25, 75], minSizes: [0, 60], gutterSize: 26 };
const head = (t) => '<div class="inspector-header inspector-header-' + t + '"><strong>x</strong></div>';
const QUBIT = head('qubit') + '<article class="qubit-detail">q2</article>';
const PAIR = head('pair') + '<article class="pair-detail">q1-2</article>';
const PULSE = head('pulse') + '<div>pulse</div>';
const SEARCH = head('search') + '<div class="search-panel">results</div>';
const RUN = head('dataset') + '<div id="ds-detail-root" data-uid="run-a">run</div>';
const PAGES = ['/qubits', '/resonators', '/pairs', '/datasets', '/cz-plan', '/pulses', '/instrument', '/help', '/table'];

let checks = 0;
const eq = (a, b, m) => { assert.deepEqual(a, b, m); checks++; };
const ok = (c, m) => { assert.ok(c, m); checks++; };

async function boot(page) {
    const dom = new JSDOM(`<!doctype html><html><body>
        <nav class="sidebar-nav">${PAGES.map(p => `<a id="nav${p.replace('/', '-')}" href="${p}">${p}</a>`).join('')}</nav>
        <div id="table-pane">table</div><div id="inspector-pane"></div>
        <div id="status-bar"></div></body></html>`,
        { url: 'https://example.invalid' + page, runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    await new Promise(r => setImmediate(r));
    d.querySelector('.sidebar-nav').addEventListener('click', e => e.preventDefault());
    w.UI_CONFIG = { split: JSON.parse(JSON.stringify(SPLIT)) };
    const calls = [];
    w.Split = (selectors, options) => {
        const panes = selectors.map(s => d.querySelector(s));
        const gutter = d.createElement('div');
        gutter.className = 'gutter gutter-vertical';
        panes[0].after(gutter);
        const inst = { pairs: [{ gutter }], options, getSizes: () => options.sizes.slice(), destroy: () => gutter.remove() };
        calls.push(inst);
        return inst;
    };
    // the real closeInspector is app.js's; the split script listens for its event
    w.closeInspector = function () {
        d.getElementById('inspector-pane').innerHTML = '';
        d.body.dispatchEvent(new w.Event('inspector-closed'));
    };
    w.eval(registryScript);
    w.eval(splitScript);
    return {
        w, d, calls,
        sizes: () => Array.from(calls[calls.length - 1].options.sizes),
        user: () => w.localStorage.getItem('quam_split_user'),
        pane: () => d.getElementById('inspector-pane'),
        open(html) {
            const target = d.getElementById('inspector-pane');
            target.innerHTML = html;
            target.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target } }));
        },
        drag(to) { const i = calls[calls.length - 1]; i.options.onDragStart(i.getSizes()); i.options.onDragEnd(to.slice()); },
        nav(p) { d.getElementById('nav' + p.replace('/', '-')).click(); w.history.pushState({}, '', p); },
    };
}

(async () => {
    // the reported case: Qubits -> q2 -> sidebar Datasets
    let s = await boot('/qubits');
    s.open(QUBIT);
    s.drag([30, 70]);
    const mine = s.user();
    s.nav('/datasets');
    eq(s.pane().innerHTML, '', 'a qubit inspector is closed on the way to Datasets');
    ok(s.pane().classList.contains('split-hidden'), 'and its pane is hidden: the page gets its full height');
    eq(s.user(), mine, 'the close never writes the remembered split');
    s.open(QUBIT);
    eq(s.sizes(), [30, 70], 'the next inspector opens where the user last put it');

    // a page that shows the same entity keeps it
    s = await boot('/qubits');
    s.open(QUBIT);
    s.nav('/resonators');
    ok(s.pane().querySelector('.qubit-detail'), 'Qubits -> Resonators keeps the qubit inspector');
    // R3B MINOR 9: the summary table and Instrument Wiring open qubit
    // inspectors themselves -- going there keeps one open
    for (const to of ['/table', '/instrument']) {
        s = await boot('/qubits');
        s.open(QUBIT);
        s.nav(to);
        ok(s.pane().querySelector('.qubit-detail'), 'Qubits -> ' + to + ' keeps the qubit inspector');
    }

    // the other reported pairs
    for (const [from, html, to] of [['/pairs', PAIR, '/cz-plan'], ['/qubits', QUBIT, '/pulses'],
                                    ['/pulses', PULSE, '/instrument'], ['/qubits', SEARCH, '/help'],
                                    ['/resonators', QUBIT, '/help']]) {
        s = await boot(from);
        s.open(html);
        s.nav(to);
        eq(s.pane().innerHTML, '', 'closed on ' + from + ' -> ' + to);
    }
    s = await boot('/pairs');
    s.open(PAIR);
    s.nav('/qubits');
    eq(s.pane().innerHTML, '', 'a pair inspector does not follow to Qubits');

    // a dataset run keeps its own rule: collapsed on navigation, not closed
    s = await boot('/datasets');
    s.open(RUN);
    s.nav('/qubits');
    ok(s.pane().querySelector('#ds-detail-root'), 'a dataset run is kept');
    eq(s.sizes(), SPLIT.collapsedSizes, 'and collapsed, as before');

    console.log('stale_inspector_selfcheck: ' + checks + ' checks ok');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
