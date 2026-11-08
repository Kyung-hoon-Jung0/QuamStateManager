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
 *     nav_sidebar / nav_boost / nav_push / nav_history -- navigation collapses runs
 *                     at any height, across swaps/reruns, without changing memory
 *     nav_same_page -- same-page navigation and run switches retain user height
 *     nav_collapse_low / nav_collapse_high -- navigation uses min(remembered
 *                     inspector share, collapsed share), including on restore
 *     nav_reopen_low / nav_reopen_high -- first run after navigation opens at
 *                     max(remembered inspector share, dataset expanded share)
 *     nav_run_only -- qubit opens do not consume the next-run navigation rule
 *     table_first  -- R4-TP-1: on Pulses with no choice the table keeps 35 %, a
 *                     remembered choice wins, and the clicked row is scrolled
 *                     back into the shrunken pane
 *     trend_chart  -- fix/trends-stress T7: a run opened from a trend chart's
 *                     point leaves the table pane room for the whole chart and
 *                     scrolls it into view (SM's own move: the user's choice
 *                     is untouched); a run opened any other way is unchanged
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
    datasetExpandedSizes: [25, 75], minSizes: [0, 60], gutterSize: 12, tableFirstSizes: [35, 65],
};
const RUN = '<div id="ds-detail-root" data-uid="run-a">run</div>';
const QUBIT = '<div class="qubit-detail">qubit</div>';

async function boot({ storage = {}, inspector = '' } = {}) {
    // v1.4.0: root URL -> Datasets URL, navigation pins start on the run's page.
    const dom = new JSDOM(`<!doctype html><html><body>
        <nav class="sidebar-nav"><a id="nav-qubits" href="/qubits">Qubits</a>
            <a id="nav-datasets" href="/datasets">Datasets</a></nav>
        <div id="table-pane">table</div><div id="inspector-pane">${inspector}</div>
        <form id="compare-form"><button id="compare-go">Compare</button></form>
        <div id="status-bar"></div></body></html>`,
        { url: 'https://example.invalid/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
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
        // compact D-1: SM's own collapse docks the panel to a one-line bar
        docked: () => d.getElementById('inspector-pane').classList.contains('ui-inspector-docked')
            && !!d.querySelector('#inspector-pane > .ui-dock-bar') && !d.querySelector('.gutter'),
        restore() {
            const b = d.querySelector('#inspector-pane > .ui-dock-bar .ui-dock-restore');
            assert.ok(b, 'the dock bar has a restore button');
            b.click();
        },
        rerun() { w.eval(splitScript); },
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

if (mode === 'nav_reopen_low' || mode === 'nav_reopen_high') {
    const choices = mode === 'nav_reopen_low' ? [[90, 10], [80, 20], [40, 60], 'down', 'up', null] : [[10, 90], [0, 100]];
    for (const choice of choices) {
        const s = await boot();
        s.swap('inspector-pane', RUN);
        if (Array.isArray(choice)) s.drag(choice);
        else if (choice) s.press(choice);
        const before = s.user();
        s.d.getElementById('nav-qubits').click();
        s.w.history.pushState({}, '', '/qubits');
        s.swap('table-pane', 'other page');
        s.d.getElementById('nav-datasets').click();
        s.w.history.pushState({}, '', '/datasets');
        s.swap('table-pane', 'datasets page');
        const expected = mode === 'nav_reopen_high' ? choice : SPLIT.datasetExpandedSizes;
        s.swap('inspector-pane', RUN);
        eq(s.sizes(), expected, 'after navigation the run opens at max(user share, expanded share)');
        assert.equal(s.user(), before, 'reopening preserves the remembered choice');
        s.swap('table-pane', 'concurrent workspace selection');
        eq(s.sizes(), expected, 'a concurrent table swap keeps the reopened height');
        s.swap('inspector-pane', RUN_B);
        // compact D-1: the down arrow docks and is not a remembered choice
        const plain = Array.isArray(choice) ? choice : SPLIT.datasetExpandedSizes;
        eq(s.sizes(), plain, 'the following run within Datasets uses the plain remembered height');
        assert.equal(s.user(), before, 'within-page switching also preserves memory');
    }
} else if (mode === 'nav_collapse_low' || mode === 'nav_collapse_high') {
    const choices = mode === 'nav_collapse_low' ? [[95, 5], [90, 10]] : [[80, 20], [10, 90]];
    for (const choice of choices) {
        for (const event of ['sidebar', 'boost', 'push', 'popstate', 'htmx:historyRestore']) {
            const s = await boot();
            s.swap('inspector-pane', RUN);
            s.drag(choice);
            const before = s.user();
            // Use remembered sizes even when a programmatic move changed the current split.
            s.w._applySplitPreset('expanded');
            const expected = mode === 'nav_collapse_low' ? choice : SPLIT.collapsedSizes;
            if (event === 'sidebar') {
                s.d.getElementById('nav-qubits').click();
            } else if (event === 'boost') {
                const link = s.d.createElement('a');
                link.href = '/qubits';
                s.d.body.appendChild(link);
                s.d.dispatchEvent(new s.w.CustomEvent('htmx:beforeRequest', {
                    detail: { elt: link, requestConfig: { boosted: true } }
                }));
            } else {
                s.w.history.replaceState({}, '', '/qubits');
                if (event === 'push') {
                    s.d.dispatchEvent(new s.w.CustomEvent('htmx:pushedIntoHistory', {
                        detail: { path: '/qubits' }
                    }));
                } else if (event === 'popstate') {
                    s.w.dispatchEvent(new s.w.PopStateEvent(event));
                } else {
                    s.d.dispatchEvent(new s.w.CustomEvent(event));
                }
            }
            void expected;
            assert.ok(s.docked(), event + ': navigation docks the run at any height');
            assert.equal(s.user(), before, event + ': navigation preserves the remembered choice');
            s.w.history.replaceState({}, '', '/qubits');
            s.swap('table-pane', 'other page');
            assert.ok(s.docked(), event + ': table swaps keep the dock');
            s.rerun();
            assert.ok(s.docked(), event + ': script reruns keep the dock');
            s.d.dispatchEvent(new s.w.CustomEvent('htmx:historyRestore'));
            assert.ok(s.docked(), event + ': history restores keep the dock');
            assert.equal(s.d.querySelectorAll('#inspector-pane > .ui-dock-bar').length, 1, event + ': one bar');
            assert.equal(s.user(), before, event + ': swaps and restores preserve user memory');
        }
    }
} else if (mode === 'nav_run_only') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([80, 20]);
    const before = s.user();
    s.d.getElementById('nav-qubits').click();
    s.w.history.pushState({}, '', '/qubits');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [80, 20], 'a qubit still opens at the remembered height');
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'only a run consumes the post-navigation expanded minimum');
    assert.equal(s.user(), before, 'the qubit and run opens preserve memory');
} else if (mode === 'nav_sidebar') {
    for (const sizes of [[80, 20], [40, 60], [10, 90]]) {
        const s = await boot();
        s.swap('inspector-pane', RUN);
        s.drag(sizes);
        const before = s.user();
        s.d.getElementById('nav-qubits').click();
        assert.ok(s.docked(), 'sidebar navigation collapses a run at any height');
        assert.equal(s.user(), before, 'sidebar navigation preserves the remembered choice');
        s.swap('table-pane', 'other page');
        assert.ok(s.docked(), 'table-pane initSplit preserves the navigation collapse');
        assert.equal(s.user(), before, 'table-pane initSplit preserves the remembered choice');
    }
} else if (mode === 'nav_boost') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([80, 20]);
    const before = s.user();
    const link = s.d.createElement('a');
    link.href = '/qubits';
    s.d.body.appendChild(link);
    s.d.dispatchEvent(new s.w.CustomEvent('htmx:beforeRequest', {
        detail: { elt: link, requestConfig: { boosted: true } }
    }));
    assert.ok(s.docked(), 'boosted page navigation collapses without a sidebar click');
    s.w.history.pushState({}, '', '/qubits');
    s.rerun();
    assert.ok(s.docked(), 'a full-body script rerun keeps the navigation collapse');
    assert.equal(s.user(), before, 'boost and rerun preserve the remembered choice');
} else if (mode === 'nav_push') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([80, 20]);
    const before = s.user();
    s.swap('table-pane', 'other page');
    s.w.history.pushState({}, '', '/qubits');
    s.d.dispatchEvent(new s.w.CustomEvent('htmx:pushedIntoHistory', { detail: { path: '/qubits' } }));
    assert.ok(s.docked(), 'a pushed page navigation collapses without a sidebar click');
    assert.equal(s.user(), before, 'pushed navigation preserves the remembered choice');
    s.swap('table-pane', 'concurrent swap');
    assert.ok(s.docked(), 'later table swaps retain the pushed navigation collapse');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'the next run after pushed navigation gets the expanded minimum');
} else if (mode === 'nav_history') {
    for (const event of ['popstate', 'htmx:historyRestore']) {
        const s = await boot();
        s.swap('inspector-pane', RUN);
        s.drag([80, 20]);
        const before = s.user();
        for (const page of ['/qubits', '/datasets']) {
            s.w.history.replaceState({}, '', page);
            if (event === 'popstate') s.w.dispatchEvent(new s.w.PopStateEvent(event));
            else s.d.dispatchEvent(new s.w.CustomEvent(event));
            assert.ok(s.docked(), event + ': another page collapses the dataset panel');
            s.rerun();
            s.d.dispatchEvent(new s.w.CustomEvent('htmx:historyRestore'));
            assert.ok(s.docked(), event + ': restored content stays collapsed');
            assert.equal(s.user(), before, event + ': history preserves the remembered choice');
        }
    }
} else if (mode === 'nav_same_page') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([80, 20]);
    s.d.getElementById('nav-datasets').click();
    eq(JSON.parse(s.current()), [80, 20], 'the Datasets menu on Datasets preserves the height');
    s.w.history.replaceState({}, '', '/datasets?name=run');
    s.w.dispatchEvent(new s.w.PopStateEvent('popstate'));
    eq(JSON.parse(s.current()), [80, 20], 'same-page history preserves the height');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), [80, 20], 'within Datasets a low remembered height still applies');
    s.swap('inspector-pane', QUBIT);
    s.d.getElementById('nav-qubits').click();
    eq(s.sizes(), [80, 20], 'page navigation retains a qubit inspector');
} else if (mode === 'buttons') {
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
    assert.equal(down.title, 'Dock the panel to the bottom');
    assert.equal(up.getAttribute('aria-label'), 'Expand the panel');
    assert.equal(down.getAttribute('aria-label'), 'Dock the panel to the bottom');
    assert.equal(up.type, 'button');
    assert.equal(s.d.querySelectorAll('.split-set-btn, .inspector-toggle-btn').length, 0,
        'no set-high / set-low / toggle button is left');
    // a rebuild of the split never doubles them
    s.press('down'); s.restore(); s.press('up');
    assert.equal(s.d.querySelectorAll('.gutter button').length, 2, 'still two after presses');
} else if (mode === 'arrows') {
    const s = await boot();
    s.swap('inspector-pane', QUBIT);
    s.drag([40, 60]);
    s.press('down');
    assert.ok(s.docked(), 'down docks the panel to its one-line bar');
    eq(JSON.parse(s.user()), [40, 60], 'the dock is not a remembered choice');
    s.restore();
    assert.ok(!s.docked() && !s.d.querySelector('.ui-dock-bar'), 'restore removes the bar');
    eq(s.sizes(), [40, 60], 'restore returns to the last remembered split');
    s.press('up');
    eq(s.sizes(), SPLIT.expandedSizes, 'up lands on the fixed expanded position');
    const n = s.calls.length;
    s.press('up');
    assert.equal(s.calls.length, n + 1, 'a second up press is applied again');
    eq(s.sizes(), SPLIT.expandedSizes, 'and lands on the same expanded position');
    // a dataset RUN in the inspector: up goes to the dataset expanded position
    s.swap('inspector-pane', RUN);
    s.press('down');
    s.restore();
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'restore on a run after up: datasetExpandedSizes');
    s.press('up');
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'up too');
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
    s.restore();
    s.press('up');
    assert.equal(s.user(), '"up"', 'the press is stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'the next run opens at the fixed max, not the dragged height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.expandedSizes, 'a qubit opens at ITS fixed max (the arrow, not a run-sized number)');
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), SPLIT.datasetExpandedSizes, 'and a run again at the run max');
} else if (mode === 'down_next') {
    // compact D-1: a down press docks; the next inspector opens at the remembered height
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    s.press('down');
    eq(JSON.parse(s.user()), [40, 60], 'the down press is not stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    assert.ok(!s.docked(), 'a new inspector undocks');
    eq(s.sizes(), [40, 60], 'the next run opens at the remembered height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [40, 60], 'a qubit too');
} else if (mode === 'drag_after_arrow') {
    // a drag after an arrow -> the drag wins
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.press('down');
    s.restore();
    s.drag([45, 55]);
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), [45, 55], 'after down, restore and a drag, the next run opens at the dragged height');
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
    assert.ok(a.d.getElementById('inspector-pane').classList.contains('ui-inspector-docked'), 'precondition: the run is docked');
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
    eq(d2.sizes(), [40, 60], 'a dock is not a choice: a reload opens at the dragged height');
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
        assert.ok(s.docked(), first + ': menu navigation collapses the run');
        assert.equal(s.user(), before, first + ': the menu auto-collapse leaves the last choice alone');
        s.swap('table-pane', 'qubits page');
        assert.ok(s.docked(), first + ': the concurrent table-pane initSplit keeps the collapse');
        assert.equal(s.user(), before, first + ': the table-pane initSplit leaves it alone');
        s.d.getElementById('nav-datasets').click();
        s.swap('table-pane', 'datasets page');
        s.swap('inspector-pane', RUN);
        // v1.4.0: plain last choice -> expanded minimum, so a reopened run has room after navigation.
        eq(s.sizes(), SPLIT.datasetExpandedSizes, first + ': back on a run: at least the expanded height');
        // the Compare-form collapse
        const go = s.d.getElementById('compare-go');
        s.d.dispatchEvent(new s.w.CustomEvent('htmx:beforeRequest', { detail: { elt: go } }));
        assert.ok(s.docked(), first + ': the compare form collapses the inspector');
        assert.equal(s.user(), before, first + ': the Compare collapse leaves it alone');
        // "Go to state" and the other _applySplitPreset callers
        s.swap('inspector-pane', RUN);
        s.w._applySplitPreset('collapsed');
        assert.ok(s.docked());
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
    assert.ok(s.docked(), 'nor where the down arrow goes (it docks)');
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
} else if (mode === 'trend_chart') {
    const TRENDS = '<div class="topo-trends-grid"><div class="topo-trend-box" id="box-a">'
        + '<div class="topo-trend-chart" id="chart-a"></div></div></div>';
    const s = await boot();
    s.d.getElementById('table-pane').innerHTML = TRENDS;
    const pane = s.d.getElementById('table-pane'), ins = s.d.getElementById('inspector-pane');
    const box = s.d.getElementById('box-a');
    pane.getBoundingClientRect = () => ({ top: 60, bottom: 260, height: 200 });
    ins.getBoundingClientRect = () => ({ top: 286, bottom: 886, height: 600 });
    box.getBoundingClientRect = () => ({ top: 300, bottom: 624, height: 324 });
    let scrolled = [];
    box.scrollIntoView = (o) => { scrolled.push(o && o.block); };
    assert.equal(typeof s.w.__smKeepChartInView, 'function', 'the chart hook is published');
    s.w.__smKeepChartInView(s.d.getElementById('chart-a'));
    s.swap('inspector-pane', RUN);
    const got = s.sizes();
    assert.ok(got[0] >= Math.ceil((324 + 24) / 800 * 100) && got[0] <= 70,
              'the table pane keeps room for the whole chart: ' + JSON.stringify(got));
    assert.equal(s.user(), null, 'SM\'s move is not the user\'s choice');
    await new Promise(r => setTimeout(r, 50));
    assert.deepEqual(scrolled, ['start'], 'the clicked chart is scrolled into view');
    // a run opened any other way: the run default, nothing scrolled
    const t = await boot();
    t.d.getElementById('table-pane').innerHTML = TRENDS;
    t.swap('inspector-pane', RUN);
    eq(t.sizes(), SPLIT.datasetExpandedSizes, 'a run opened elsewhere keeps the run position');
    // a remembered choice that already leaves room wins
    const u = await boot({ storage: { quam_split_user: '[60,40]' } });
    u.d.getElementById('table-pane').innerHTML = TRENDS;
    const b2 = u.d.getElementById('box-a');
    u.d.getElementById('table-pane').getBoundingClientRect = () => ({ height: 480 });
    u.d.getElementById('inspector-pane').getBoundingClientRect = () => ({ height: 320 });
    b2.getBoundingClientRect = () => ({ top: 100, bottom: 424, height: 324 });
    b2.scrollIntoView = () => {};
    u.w.__smKeepChartInView(b2);
    u.swap('inspector-pane', RUN);
    eq(u.sizes(), [60, 40], 'a remembered choice with room enough is kept');
} else if (mode === 'table_first') {
    const PULSES = '<span id="pulses-total"></span><table><tr id="row-a"><td>x90</td></tr></table>';
    const s = await boot();
    s.d.getElementById('table-pane').innerHTML = PULSES;
    const pane = s.d.getElementById('table-pane'), row = s.d.getElementById('row-a');
    pane.getBoundingClientRect = () => ({ top: 100, bottom: 200 });
    row.getBoundingClientRect = () => ({ top: 400, bottom: 420 });
    let scrolled = 0;
    row.scrollIntoView = () => { scrolled++; };
    row.click();
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), SPLIT.tableFirstSizes, 'no choice yet: on Pulses the table keeps its share');
    await new Promise(r => setTimeout(r, 50));
    assert.equal(scrolled, 1, 'the clicked row is scrolled back into the shrunken pane');
    s.swap('inspector-pane', QUBIT);
    await new Promise(r => setTimeout(r, 50));
    assert.equal(scrolled, 1, 'a swap with no click scrolls nothing');
    row.getBoundingClientRect = () => ({ top: 120, bottom: 140 });
    row.click();
    s.swap('inspector-pane', QUBIT);
    await new Promise(r => setTimeout(r, 50));
    assert.equal(scrolled, 1, 'a row still in view is left alone');
    const q = await boot();
    q.swap('inspector-pane', QUBIT);
    eq(q.sizes(), SPLIT.expandedSizes, 'another page keeps the expanded default');
    const u = await boot({ storage: { quam_split_user: '"up"' } });
    u.d.getElementById('table-pane').innerHTML = PULSES;
    u.swap('inspector-pane', QUBIT);
    eq(u.sizes(), SPLIT.expandedSizes, 'a remembered choice wins on Pulses too');
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
    s.d.querySelector('.split-up-btn').focus();
    s.press('up');
    let now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('split-up-btn'),
        'focus is on the rebuilt up arrow, not dropped to the body');
    s.d.querySelector('.split-down-btn').focus();
    s.press('down');
    now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('ui-dock-restore'),
        'a keyboard dock moves focus to the bar\'s restore button');
    s.restore();
    eq(s.sizes(), SPLIT.expandedSizes, 'restore after up returns to the expanded position');
    // a MOUSE press (detail 1) drops focus as before -- no lingering focus ring
    const up = s.d.querySelector('.split-up-btn');
    up.focus();
    up.dispatchEvent(new s.w.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    eq(s.sizes(), SPLIT.expandedSizes, 'the mouse press moved the bar');
    const after = s.d.activeElement;
    assert.ok(!(after && after.classList && after.classList.contains('split-up-btn')),
        'a mouse press does not put focus back on the rebuilt arrow');
} else if (mode === 'dock_bar') {
    // compact D-1: the docked bar names the panel and carries restore + close
    const s = await boot();
    s.swap('inspector-pane', '<div class="inspector-header"><div class="inspector-title">'
        + '<span class="inspector-runid">#7</span> <strong>ramsey</strong></div></div>' + RUN);
    s.drag([30, 70]);
    s.d.getElementById('nav-qubits').click();
    assert.ok(s.docked(), 'navigation docks the run');
    const bar = s.d.querySelector('#inspector-pane > .ui-dock-bar');
    assert.equal(bar.querySelector('.ui-dock-title').textContent, '#7 ramsey', 'the bar names the panel');
    assert.equal(s.d.getElementById('table-pane').style.height, '', 'the page gets everything else');
    assert.equal(s.d.getElementById('inspector-pane').style.height, '', 'the panel is only its bar');
    s.restore();
    eq(s.sizes(), [30, 70], 'restore returns to the remembered split');
    s.w._applySplitPreset('collapsed');
    assert.ok(s.docked(), 'Go to state docks too');
    let closed = 0;
    s.w.closeInspector = () => {
        closed++;
        const p = s.d.getElementById('inspector-pane');
        p.innerHTML = '';
        p.dispatchEvent(new s.w.CustomEvent('inspector-closed', { bubbles: true }));
    };
    s.d.querySelector('#inspector-pane > .ui-dock-bar .ui-dock-close').click();
    assert.equal(closed, 1, 'the bar\'s x closes the panel');
    assert.ok(s.d.getElementById('inspector-pane').classList.contains('split-hidden'), 'and hides the pane');
    assert.ok(!s.d.getElementById('inspector-pane').classList.contains('ui-inspector-docked'), 'no dock left behind');
    s.swap('inspector-pane', QUBIT);
    assert.ok(!s.docked(), 'the next inspector opens undocked');
    eq(s.sizes(), [30, 70], 'at the remembered split');
} else if (mode === 'heading') {
    // compact D-2: with no choice, the page keeps only the top half of its title
    const s = await boot();
    const pane = s.d.getElementById('table-pane');
    pane.innerHTML = '<h1>Live edit - Json Tree view</h1><p>text</p>';
    Object.defineProperty(pane.parentElement, 'clientHeight', { value: 812, configurable: true });
    pane.getBoundingClientRect = () => ({ top: 100, bottom: 912, height: 812 });
    pane.querySelector('h1').getBoundingClientRect = () => ({ top: 110, bottom: 150, height: 40 });
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [3.75, 96.25], 'the top pane ends halfway down the title (30 of 800 px)');
    assert.equal(s.user(), null, 'SM\'s move is not a choice');
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), [3.75, 96.25], 'a run opened over another page too');
    s.drag([50, 50]);
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [50, 50], 'a remembered drag still wins');
    // the Datasets run list keeps its rows
    const t = await boot();
    t.d.getElementById('table-pane').innerHTML = '<h2>Datasets</h2><input id="dataset-search">';
    t.swap('inspector-pane', RUN);
    eq(t.sizes(), SPLIT.datasetExpandedSizes, 'the Datasets list keeps the run position');
} else {
    assert.fail('unknown mode: ' + mode);
}
console.log('split_arrows_selfcheck: ' + mode + ' ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
