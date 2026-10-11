/* jsdom selfcheck for the inspector split bar (v1.3.0, customer feedback).
 *
 * Runs the REAL split script from base.html (and the __smBase registry it
 * needs) against a recording Split stub, so every layout the script asks for
 * is visible as the sizes of the last Split() call.
 *
 *   node tests/split_arrows_selfcheck.cjs <mode>
 *     buttons      -- two arrows only, titled and labelled; no set buttons
 *     arrows       -- each arrow lands on its FIXED position, again when pressed twice
 *     drag         -- a drag -> the next inspector opens at the dragged height
 *     up_next      -- an up press after a drag -> the next one opens at the fixed max
 *                     (for ITS kind: a run's or a qubit's)
 *     down_next    -- a down press after a drag -> the next one opens at the fixed min
 *     drag_after_arrow -- a drag after an arrow -> the drag wins
 *     reload       -- the last choice survives a reload / a new session, over a
 *                     stale current split from an SM move
 *     programmatic -- menu auto-collapse, Compare, _applySplitPreset and the
 *                     table-pane initSplit leave the last choice alone
 *     retired      -- the old set-high / set-low keys are removed on load
 *     defaults     -- with no choice yet, the defaults as before
 *     still_click  -- a press on the bar that does not move it is not a choice
 *     focus        -- a keyboard user stays on the arrow after a press; a mouse
 *                     press drops focus as before
 * Driven by tests/test_split_arrows.py.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const base = fs.readFileSync(path.join(root, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const bodyScripts = [...base.slice(base.indexOf('<body')).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    .filter(m => !/\bsrc\s*=|application\/json/i.test(m[1])).map(m => m[2]);
const registryScript = bodyScripts.find(s => s.includes('registry.replace = function'));
const splitScript = bodyScripts.find(s => s.includes("__smBase.replace('split')"));
assert.ok(registryScript, 'the __smBase registry script is found');
assert.ok(splitScript, 'the split script is found');

const SPLIT = {
    defaultSizes: [55, 45], expandedSizes: [15, 85], collapsedSizes: [85, 15],
    datasetExpandedSizes: [25, 75], minSizes: [0, 60], gutterSize: 26,
};
const RUN = '<div id="ds-detail-root" data-uid="run-a">run</div>';
const QUBIT = '<div class="qubit-detail">qubit</div>';

async function boot({ storage = {}, inspector = '' } = {}) {
    const dom = new JSDOM(`<!doctype html><html><body>
        <nav class="sidebar-nav"><a id="nav-qubits" href="/qubits">Qubits</a>
            <a id="nav-datasets" href="/datasets">Datasets</a></nav>
        <div id="table-pane">table</div><div id="inspector-pane">${inspector}</div>
        <form id="compare-form"><button id="compare-go">Compare</button></form>
        <div id="status-bar"></div></body></html>`,
        { url: 'https://example.invalid/', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    // let jsdom finish parsing, as a loaded page has (the script's ready() runs at once)
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(d.readyState, 'complete');
    for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
    // jsdom does not navigate; keep a menu click to its SM handlers
    d.querySelector('.sidebar-nav').addEventListener('click', e => e.preventDefault());
    w.UI_CONFIG = { split: JSON.parse(JSON.stringify(SPLIT)) };
    const calls = [];
    w.Split = (selectors, options) => {
        const panes = selectors.map(s => d.querySelector(s));
        const gutter = d.createElement('div');
        gutter.className = 'gutter gutter-vertical';
        panes[0].after(gutter);
        const inst = { pairs: [{ gutter }], options, getSizes: () => options.sizes.slice(),
                       destroy: () => {} };
        calls.push(inst);
        return inst;
    };
    w.eval(registryScript);
    w.eval(splitScript);
    const last = () => calls[calls.length - 1];
    const env = {
        w, d, calls,
        sizes: () => Array.from(last().options.sizes),
        user: () => w.localStorage.getItem('quam_split_user'),
        current: () => w.localStorage.getItem('quam_split_sizes'),
        swap(id, html) {
            const target = d.getElementById(id);
            if (html !== undefined) target.innerHTML = html;
            target.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target } }));
        },
        // Split.js calls onDragStart on the gutter mousedown, onDragEnd on mouseup.
        drag(to) {
            const inst = last();
            inst.options.onDragStart(inst.getSizes());
            inst.options.onDragEnd(to.slice());
        },
        press(which) {
            const btn = d.querySelector('.gutter-controls .split-' + which + '-btn');
            assert.ok(btn, 'the ' + which + ' arrow is on the gutter');
            btn.click();
        },
        storage() {
            const out = {};
            for (let i = 0; i < w.localStorage.length; i++) {
                const k = w.localStorage.key(i);
                out[k] = w.localStorage.getItem(k);
            }
            return out;
        },
    };
    return env;
}

const mode = process.argv[2];
(async () => {
const arr = v => (v == null ? v : Array.from(v));
const eq = (a, b, msg) => assert.deepEqual(arr(a), arr(b), msg);
const RUN_B = '<div id="ds-detail-root" data-uid="run-b">run b</div>';

if (mode === 'buttons') {
    const s = await boot();
    s.swap('inspector-pane', QUBIT);
    const buttons = s.d.querySelectorAll('.gutter button');
    assert.equal(buttons.length, 2, 'the gutter carries exactly two buttons');
    const [up, down] = buttons;
    assert.ok(up.classList.contains('split-up-btn') && down.classList.contains('split-down-btn'),
        'up arrow first, down arrow second');
    assert.equal(up.textContent, String.fromCharCode(0x25B2));
    assert.equal(down.textContent, String.fromCharCode(0x25BC));
    assert.equal(up.title, 'Expand the panel');
    assert.equal(down.title, 'Collapse the panel');
    assert.equal(up.getAttribute('aria-label'), 'Expand the panel');
    assert.equal(down.getAttribute('aria-label'), 'Collapse the panel');
    assert.equal(up.type, 'button');
    assert.equal(s.d.querySelectorAll('.split-set-btn, .inspector-toggle-btn').length, 0,
        'no set-high / set-low / toggle button is left');
    // a rebuild of the split never doubles them
    s.press('down'); s.press('up');
    assert.equal(s.d.querySelectorAll('.gutter button').length, 2, 'still two after presses');
} else if (mode === 'arrows') {
    const s = await boot();
    s.swap('inspector-pane', QUBIT);
    s.drag([40, 60]);
    s.press('down');
    eq(s.sizes(), SPLIT.collapsedSizes, 'down lands on the fixed collapsed position, not the dragged one');
    let n = s.calls.length;
    s.press('down');
    assert.equal(s.calls.length, n + 1, 'a second down press is applied again');
    eq(s.sizes(), SPLIT.collapsedSizes, 'and lands on the same collapsed position');
    s.press('up');
    eq(s.sizes(), SPLIT.expandedSizes, 'up lands on the fixed expanded position');
    n = s.calls.length;
    s.press('up');
    assert.equal(s.calls.length, n + 1, 'a second up press is applied again');
    eq(s.sizes(), SPLIT.expandedSizes, 'and lands on the same expanded position');
    // a dataset RUN in the inspector: up goes to the dataset expanded position
    s.swap('inspector-pane', RUN);
    s.press('down');
    s.press('up');
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'up on a dataset run lands on datasetExpandedSizes');
    s.press('up');
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'twice too');
    eq(JSON.parse(s.current()), SPLIT.datasetExpandedSizes, 'the press is the current split');
} else if (mode === 'drag') {
    // a drag -> the next run opens at the dragged height
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    eq(JSON.parse(s.user()), [40, 60], 'the drag end is stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), [40, 60], 'the next run opens at the dragged height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [40, 60], 'a qubit inspector too');
    s.swap('table-pane', 'other');
    eq(s.sizes(), [40, 60], 'a concurrent table-pane swap keeps it');
    s.drag([62, 38]);
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), [62, 38], 'a later drag replaces the earlier one');
} else if (mode === 'up_next') {
    // an up press (after a drag) -> the next inspector opens at the fixed max
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    s.press('down');
    s.press('up');
    assert.equal(s.user(), '"up"', 'the press is stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'the next run opens at the fixed max, not the dragged height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.expandedSizes, 'a qubit opens at ITS fixed max (the arrow, not a run-sized number)');
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'and a run again at the run max');
} else if (mode === 'down_next') {
    // a down press (after a drag) -> the next inspector opens at the fixed min
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    s.press('down');
    assert.equal(s.user(), '"down"', 'the press is stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), SPLIT.collapsedSizes, 'the next run opens at the fixed min, not the dragged height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.collapsedSizes, 'a qubit too');
} else if (mode === 'drag_after_arrow') {
    // a drag after an arrow -> the drag wins
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.press('down');
    s.drag([45, 55]);
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), [45, 55], 'after down then a drag, the next run opens at the dragged height');
    s.press('up');
    s.drag([70, 30]);
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [70, 30], 'after up then a drag, the next qubit opens at the dragged height');
} else if (mode === 'reload') {
    // the last choice survives a reload, over a stale current split from an SM move
    const a = await boot();
    a.swap('inspector-pane', RUN);
    a.drag([40, 60]);
    a.d.getElementById('nav-qubits').click();    // SM's auto-collapse: current = collapsed
    eq(JSON.parse(a.current()), SPLIT.collapsedSizes, 'precondition: the current split is collapsed');
    const saved = a.storage();
    // a reload of a page that renders the inspector server-side (a qubit page)
    const b = await boot({ storage: saved, inspector: QUBIT });
    eq(b.sizes(), [40, 60], 'a reload opens the inspector at the dragged height, not at an SM move');
    b.swap('table-pane', 'other');
    eq(b.sizes(), [40, 60], 'and a table-pane swap after the reload keeps it');
    // a reload with an empty inspector, then a run click
    const c = await boot({ storage: saved });
    assert.equal(c.calls.length, 0, 'no split while the inspector is empty');
    c.swap('inspector-pane', RUN);
    eq(c.sizes(), [40, 60], 'the first run after a reload opens at the dragged height');
    // an arrow choice survives a reload as well
    c.press('down');
    const d2 = await boot({ storage: c.storage(), inspector: QUBIT });
    eq(d2.sizes(), SPLIT.collapsedSizes, 'after down, a reload opens at the fixed min');
    d2.press('up');
    const e2 = await boot({ storage: d2.storage() });
    e2.swap('inspector-pane', RUN);
    eq(e2.sizes(), SPLIT.datasetExpandedSizes, 'after up, a new session opens a run at the fixed max');
} else if (mode === 'programmatic') {
    for (const first of ['drag', 'up']) {
        const s = await boot();
        s.swap('inspector-pane', RUN);
        if (first === 'drag') s.drag([30, 70]); else s.press('up');
        const before = s.user();
        const open = first === 'drag' ? [30, 70] : SPLIT.datasetExpandedSizes;
        // the sidebar-menu auto-collapse with a dataset detail open
        s.d.getElementById('nav-qubits').click();
        eq(s.sizes(), SPLIT.collapsedSizes, first + ': menu navigation collapses the run');
        assert.equal(s.user(), before, first + ': the menu auto-collapse leaves the last choice alone');
        s.swap('table-pane', 'qubits page');
        eq(s.sizes(), SPLIT.collapsedSizes, first + ': the concurrent table-pane initSplit keeps the collapse');
        assert.equal(s.user(), before, first + ': the table-pane initSplit leaves it alone');
        s.d.getElementById('nav-datasets').click();
        s.swap('table-pane', 'datasets page');
        s.swap('inspector-pane', RUN);
        eq(s.sizes(), open, first + ': back on a run: the last choice');
        // the Compare-form collapse
        const go = s.d.getElementById('compare-go');
        s.d.dispatchEvent(new s.w.CustomEvent('htmx:beforeRequest', { detail: { elt: go } }));
        eq(s.sizes(), SPLIT.collapsedSizes, first + ': the compare form collapses the inspector');
        assert.equal(s.user(), before, first + ': the Compare collapse leaves it alone');
        // "Go to state" and the other _applySplitPreset callers
        s.swap('inspector-pane', RUN);
        s.w._applySplitPreset('collapsed');
        eq(s.sizes(), SPLIT.collapsedSizes);
        s.w._applySplitPreset('expanded');
        eq(s.sizes(), SPLIT.datasetExpandedSizes);
        s.w._applySplitPreset('collapsed');
        assert.equal(s.user(), before, first + ': _applySplitPreset leaves it alone');
        s.swap('inspector-pane', RUN);
        eq(s.sizes(), open, first + ': and the next open is still the last choice');
    }
} else if (mode === 'retired') {
    const s = await boot({ storage: { quam_split_expanded: '[0,100]', quam_split_collapsed: '[50,50]' } });
    assert.equal(s.w.localStorage.getItem('quam_split_expanded'), null, 'the set-high key is removed');
    assert.equal(s.w.localStorage.getItem('quam_split_collapsed'), null, 'the set-low key is removed');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.expandedSizes, 'and it no longer decides where an inspector opens');
    s.press('down');
    eq(s.sizes(), SPLIT.collapsedSizes, 'nor where the down arrow goes');
} else if (mode === 'defaults') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'no choice yet: a run opens at the dataset default');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.expandedSizes, 'no choice yet: a qubit opens at the expanded default');
    assert.equal(s.user(), null, 'opening never writes the last choice');
    const r = await boot({ storage: { quam_split_sizes: '[50,50]' }, inspector: QUBIT });
    eq(r.sizes(), [50, 50], 'no choice yet: a reload keeps the current split, as before');
    const bad = await boot({ storage: { quam_split_user: '"sideways"' } });
    bad.swap('inspector-pane', QUBIT);
    eq(bad.sizes(), SPLIT.expandedSizes, 'an unreadable stored choice falls back to the default');
} else if (mode === 'still_click') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    s.press('up');
    // a press on the bar (mousedown + mouseup) that did not move it
    const inst = s.calls[s.calls.length - 1];
    inst.options.onDragStart(inst.getSizes());
    inst.options.onDragEnd(inst.getSizes());
    assert.equal(s.user(), '"up"', 'a press on the bar that did not move it is not a choice');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.expandedSizes, 'the qubit opens at the up arrow position, not the run-sized bar');
} else if (mode === 'focus') {
    const s = await boot();
    s.swap('inspector-pane', QUBIT);
    s.d.querySelector('.split-down-btn').focus();
    s.press('down');
    const now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('split-down-btn'),
        'focus is on the rebuilt down arrow, not dropped to the body');
    s.press('down');
    eq(s.sizes(), SPLIT.collapsedSizes, 'a second keyboard press works');
    // a MOUSE press (detail 1) drops focus as before -- no lingering focus ring
    const up = s.d.querySelector('.split-up-btn');
    up.focus();
    up.dispatchEvent(new s.w.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    eq(s.sizes(), SPLIT.expandedSizes, 'the mouse press moved the bar');
    const after = s.d.activeElement;
    assert.ok(!(after && after.classList && after.classList.contains('split-up-btn')),
        'a mouse press does not put focus back on the rebuilt arrow');
} else {
    assert.fail('unknown mode: ' + mode);
}
console.log('split_arrows_selfcheck: ' + mode + ' ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
