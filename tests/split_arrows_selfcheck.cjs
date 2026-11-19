/* jsdom selfcheck for the inspector split bar (v1.3.0, customer feedback).
 *
 * Runs the REAL split script from base.html (and the __smBase registry it
 * needs) against a recording Split stub, so every layout the script asks for
 * is visible as the sizes of the last Split() call.
 *
 *   node tests/split_arrows_selfcheck.cjs <mode>
 *     buttons      -- up, circle, down, titled and labelled; no set buttons
 *     arrows       -- up lands on the full height, down docks, the circle on the
 *                     last dragged height, again when pressed twice
 *     back         -- the circle: last drag after an up and after a down, from the
 *                     bar and the docked bar; no drag -> the default; seeded from an
 *                     old stored drag; an arrow press never overwrites the drag
 *     opens_full   -- no remembered choice -> a panel opens at the full height,
 *                     except over the Datasets run list
 *     drag         -- a drag -> the next inspector opens at the dragged height
 *     up_next      -- an up press after a drag -> the next one opens at the full height
 *     down_next    -- a down press after a drag -> the next one opens at the fixed min
 *     drag_after_arrow -- a drag after an arrow -> the drag wins
 *     reload       -- the last choice survives a reload / a new session, over a
 *                     stale current split from an SM move
 *     programmatic -- menu auto-collapse, Compare, _applySplitPreset and the
 *                     table-pane initSplit leave the last choice alone
 *     retired      -- the old set-high / set-low keys are removed on load
 *     defaults     -- with no choice yet, the defaults as before
 *     still_click  -- a press on the bar that does not move it is not a choice
 *     focus        -- a keyboard user stays on the arrow (or circle) after a press; a mouse
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
 *     keeps_scroll: a table-pane refresh (an undo answer re-lists the page) re-creates
 *                     the split; the inspector's reader stays at their scroll (the real
 *                     Split destroy clears the pane height, which clamps it to the top)
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
const FULL = [0, 100];   // the up arrow: the panel takes the whole height
const RUN = '<div id="ds-detail-root" data-uid="run-a">run</div>';
const QUBIT = '<div class="qubit-detail">qubit</div>';
const DATASETS_PAGE = '<h2>Datasets</h2><input id="dataset-search">';

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
        // the docked bar's up arrow: the panel to the full height
        restore() {
            const b = d.querySelector('#inspector-pane > .ui-dock-bar .ui-dock-restore');
            assert.ok(b, 'the dock bar has a restore button');
            b.click();
        },
        // the circle: back to the last dragged height (on the bar, or on the docked bar)
        back() {
            const b = d.querySelector('.gutter-controls .split-back-btn')
                || d.querySelector('#inspector-pane > .ui-dock-bar .ui-dock-back');
            assert.ok(b, 'a back-to-drag circle is shown');
            b.click();
        },
        drag_key: () => w.localStorage.getItem('quam_split_drag'),
        rerun() { w.eval(splitScript); },
        swap(id, html) {
            const target = d.getElementById(id);
            if (html !== undefined) target.innerHTML = html;
            target.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target } }));
        },
        // Split.js calls onDragStart on the gutter mousedown, onDragEnd on mouseup.
        drag(to) {
            const inst = last();
            // the bar starts where the previous drag left it
            inst.options.onDragStart(inst.dragged || inst.getSizes());
            inst.options.onDragEnd(to.slice());
            inst.dragged = to.slice();
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
        // from mid-page: the panel opens at the full height, and a drag must move the bar
        if (Array.isArray(choice)) { s.drag([50, 50]); s.drag(choice); }
        else if (choice) s.press(choice);
        const before = s.user();
        s.d.getElementById('nav-qubits').click();
        s.w.history.pushState({}, '', '/qubits');
        s.swap('table-pane', 'other page');
        s.d.getElementById('nav-datasets').click();
        s.w.history.pushState({}, '', '/datasets');
        s.swap('table-pane', DATASETS_PAGE);
        // the up arrow is the full height, above every expanded share
        const expected = mode === 'nav_reopen_high' ? choice : choice === 'up' ? FULL : SPLIT.datasetExpandedSizes;
        s.swap('inspector-pane', RUN);
        eq(s.sizes(), expected, 'after navigation the run opens at max(user share, expanded share)');
        assert.equal(s.user(), before, 'reopening preserves the remembered choice');
        s.swap('table-pane', DATASETS_PAGE);
        eq(s.sizes(), expected, 'a concurrent table swap keeps the reopened height');
        s.swap('inspector-pane', RUN_B);
        // compact D-1: the down arrow docks and is not a remembered choice
        const plain = Array.isArray(choice) ? choice : choice === 'up' ? FULL : SPLIT.datasetExpandedSizes;
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
    eq(s.sizes(), FULL, 'only a run consumes the post-navigation minimum');
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
    eq(s.sizes(), FULL, 'the next run after pushed navigation gets the opening height');
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
    assert.equal(buttons.length, 3, 'the gutter carries up, the circle and down');
    const [up, back, down] = buttons;
    assert.ok(up.classList.contains('split-up-btn') && back.classList.contains('split-back-btn')
        && down.classList.contains('split-down-btn'), 'up arrow first, the circle between, down arrow last');
    assert.equal(up.textContent, String.fromCharCode(0x25B2));
    assert.equal(back.textContent, String.fromCharCode(0x25CF));
    assert.equal(down.textContent, String.fromCharCode(0x25BC));
    assert.equal(up.title, 'Expand the panel to the full height');
    assert.equal(back.title, 'Back to the default height (nothing dragged yet)', 'no drag yet: the title says so');
    assert.equal(down.title, 'Dock the panel to the bottom');
    assert.equal(up.getAttribute('aria-label'), 'Expand the panel to the full height');
    assert.equal(back.getAttribute('aria-label'), back.title);
    assert.equal(down.getAttribute('aria-label'), 'Dock the panel to the bottom');
    assert.equal(up.type, 'button');
    assert.equal(back.type, 'button');
    assert.ok(back.classList.contains('ui-split-arrow'), 'the circle is the same kit part as the arrows');
    assert.equal(s.d.querySelectorAll('.split-set-btn, .inspector-toggle-btn').length, 0,
        'no set-high / set-low / toggle button is left');
    s.drag([40, 60]);
    assert.equal(s.d.querySelector('.split-back-btn').title, 'Back to the height you dragged it to',
        'after a drag the circle names the dragged height');
    // a rebuild of the split never doubles them
    s.press('down'); s.restore(); s.press('up'); s.back();
    assert.equal(s.d.querySelectorAll('.gutter button').length, 3, 'still three after presses');
} else if (mode === 'arrows') {
    const s = await boot();
    s.swap('inspector-pane', QUBIT);
    s.drag([40, 60]);
    s.press('down');
    assert.ok(s.docked(), 'down docks the panel to its one-line bar');
    eq(JSON.parse(s.user()), [40, 60], 'the dock is not a remembered choice');
    s.restore();
    assert.ok(!s.docked() && !s.d.querySelector('.ui-dock-bar'), 'restore removes the bar');
    eq(s.sizes(), FULL, 'the docked bar\'s up arrow takes the full height, not the dragged one');
    s.press('down');
    s.back();
    assert.ok(!s.docked(), 'the docked bar\'s circle undocks too');
    eq(s.sizes(), [40, 60], 'the circle returns to the last dragged split');
    s.press('up');
    eq(s.sizes(), FULL, 'up lands on the full height');
    const n = s.calls.length;
    s.press('up');
    assert.equal(s.calls.length, n + 1, 'a second up press is applied again');
    eq(s.sizes(), FULL, 'and lands on the full height again');
    s.back();
    eq(s.sizes(), [40, 60], 'the circle after up: the dragged split, not the up arrow');
    // a dataset RUN in the inspector: up is the full height there too
    s.swap('inspector-pane', RUN);
    s.press('down');
    s.back();
    eq(s.sizes(), [40, 60], 'the circle on a run after down: the dragged split');
    s.press('up');
    eq(s.sizes(), FULL, 'up on a run: the full height');
    eq(JSON.parse(s.current()), FULL, 'the press is the current split');
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
    // an up press (after a drag) -> the next inspector opens at the full height
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    s.press('down');
    s.back();
    s.press('up');
    assert.equal(s.user(), '"up"', 'the press is stored as the last choice');
    s.swap('inspector-pane', RUN_B);
    eq(s.sizes(), FULL, 'the next run opens at the full height, not the dragged height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), FULL, 'a qubit too');
    eq(JSON.parse(s.drag_key()), [40, 60], 'the up press leaves the dragged height for the circle');
    s.back();
    eq(s.sizes(), [40, 60], 'and the circle returns to it');
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
    eq(e2.sizes(), FULL, 'after up, a new session opens a run at the full height');
    e2.back();
    eq(e2.sizes(), [40, 60], 'and the circle still knows the drag from the earlier session');
} else if (mode === 'programmatic') {
    for (const first of ['drag', 'up']) {
        const s = await boot();
        s.swap('inspector-pane', RUN);
        if (first === 'drag') s.drag([30, 70]); else s.press('up');
        const before = s.user();
        const open = first === 'drag' ? [30, 70] : FULL;
        // the sidebar-menu auto-collapse with a dataset detail open
        s.d.getElementById('nav-qubits').click();
        assert.ok(s.docked(), first + ': menu navigation collapses the run');
        assert.equal(s.user(), before, first + ': the menu auto-collapse leaves the last choice alone');
        s.swap('table-pane', 'qubits page');
        assert.ok(s.docked(), first + ': the concurrent table-pane initSplit keeps the collapse');
        assert.equal(s.user(), before, first + ': the table-pane initSplit leaves it alone');
        s.d.getElementById('nav-datasets').click();
        s.swap('table-pane', DATASETS_PAGE);
        s.swap('inspector-pane', RUN);
        // v1.4.0: plain last choice -> expanded minimum, so a reopened run has room after navigation.
        eq(s.sizes(), first === 'drag' ? SPLIT.datasetExpandedSizes : FULL,
            first + ': back on a run: at least the expanded height');
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
    eq(s.sizes(), FULL, 'and it no longer decides where an inspector opens');
    s.press('down');
    assert.ok(s.docked(), 'nor where the down arrow goes (it docks)');
} else if (mode === 'defaults') {
    const s = await boot();
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), FULL, 'no choice yet: a run opens at the full height');
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), FULL, 'no choice yet: a qubit opens at the full height');
    assert.equal(s.user(), null, 'opening never writes the last choice');
    const r = await boot({ storage: { quam_split_sizes: '[50,50]' }, inspector: QUBIT });
    eq(r.sizes(), [50, 50], 'no choice yet: a reload keeps the current split, as before');
    const bad = await boot({ storage: { quam_split_user: '"sideways"' } });
    bad.swap('inspector-pane', QUBIT);
    eq(bad.sizes(), FULL, 'an unreadable stored choice falls back to the default');
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
    eq(t.sizes(), FULL, 'a run opened elsewhere opens like any other panel');
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
    eq(q.sizes(), FULL, 'another page opens at the full height');
    const u = await boot({ storage: { quam_split_user: '"up"' } });
    u.d.getElementById('table-pane').innerHTML = PULSES;
    u.swap('inspector-pane', QUBIT);
    eq(u.sizes(), FULL, 'a remembered choice (up: the full height) wins on Pulses too');
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
    eq(s.sizes(), FULL, 'the qubit opens at the up arrow position (full height), not the bar\'s');
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
    eq(s.sizes(), FULL, 'the docked bar\'s up arrow: the full height');
    now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('split-up-btn'),
        'a keyboard press on the docked bar\'s up arrow lands on the split bar\'s');
    s.press('down');
    s.back();
    now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('split-back-btn'),
        'a keyboard press on the docked bar\'s circle lands on the split bar\'s');
    // the circle is keyboard-reachable and keeps a keyboard user on it
    s.d.querySelector('.split-back-btn').focus();
    s.press('back');
    now = s.d.activeElement;
    assert.ok(now && now.classList && now.classList.contains('split-back-btn'),
        'focus is on the rebuilt circle after a keyboard press');
    // a MOUSE press (detail 1) drops focus as before -- no lingering focus ring
    const up = s.d.querySelector('.split-up-btn');
    up.focus();
    up.dispatchEvent(new s.w.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    eq(s.sizes(), FULL, 'the mouse press moved the bar');
    const after = s.d.activeElement;
    assert.ok(!(after && after.classList && after.classList.contains('split-up-btn')),
        'a mouse press does not put focus back on the rebuilt arrow');
} else if (mode === 'back') {
    // the circle returns to the last DRAGGED height, whatever arrow was pressed since
    const s = await boot();
    s.swap('inspector-pane', RUN);
    s.drag([40, 60]);
    eq(JSON.parse(s.drag_key()), [40, 60], 'a drag is kept for the circle');
    s.press('up');
    eq(s.sizes(), FULL, 'up: the full height');
    s.back();
    eq(s.sizes(), [40, 60], 'circle after up: the dragged height');
    eq(JSON.parse(s.user()), [40, 60], 'the circle makes the drag the last choice again');
    s.press('down');
    assert.ok(s.docked(), 'down docks');
    s.restore();
    eq(s.sizes(), FULL, 'up from the docked bar: the full height');
    s.press('down');
    s.back();
    eq(s.sizes(), [40, 60], 'circle from the docked bar (after a down): the dragged height');
    eq(JSON.parse(s.drag_key()), [40, 60], 'no arrow press overwrote the drag');
    // no drag yet: the default for the panel's kind, and the choice is cleared
    const n = await boot();
    n.swap('inspector-pane', QUBIT);
    assert.ok(/default height/.test(n.d.querySelector('.split-back-btn').title), 'the title says so');
    n.press('up');
    n.back();
    eq(n.sizes(), SPLIT.defaultSizes, 'no drag: the default split, so the circle still moves the panel');
    assert.equal(n.user(), null, 'and no choice is left behind');
    assert.equal(n.drag_key(), null, 'nor a drag invented');
    n.swap('inspector-pane', RUN);
    n.press('up');
    n.back();
    eq(n.sizes(), SPLIT.defaultSizes, 'no drag: a run at the default split too');
    // an older session's stored drag (quam_split_user sizes) seeds the circle
    const o = await boot({ storage: { quam_split_user: '[25,75]' } });
    o.swap('inspector-pane', RUN);
    o.press('up');
    o.back();
    eq(o.sizes(), [25, 75], 'the circle starts from an older stored drag');
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
    assert.ok(bar.querySelector('.ui-dock-back'), 'the docked bar carries the circle too');
    assert.equal(bar.querySelectorAll(':scope > .ui-dock-actions > button').length, 3,
        'up, circle and close sit together in the group the bar centres');
    s.restore();
    eq(s.sizes(), FULL, 'the docked bar\'s up arrow: the full height');
    assert.equal(s.user(), '"up"', 'stored as the up choice');
    s.w._applySplitPreset('collapsed');
    assert.ok(s.docked(), 'Go to state docks too');
    s.d.querySelector('#inspector-pane > .ui-dock-bar .ui-dock-back').click();
    eq(s.sizes(), [30, 70], 'the docked bar\'s circle: back to the dragged height');
    s.w._applySplitPreset('collapsed');
    assert.ok(s.docked(), 'docked again');
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
} else if (mode === 'keeps_scroll') {
    const s = await boot();
    s.swap('inspector-pane', '<div class="pair-detail">pair page</div>');
    const ins = s.d.getElementById('inspector-pane');
    let top = 0;
    Object.defineProperty(ins, 'scrollTop', { configurable: true, get: () => top, set: v => { top = v; } });
    // the real destroy clears the pane height, and the browser clamps the scroll to 0
    const realSplit = s.w.Split;
    s.w.Split = (selectors, options) => {
        const inst = realSplit(selectors, options);
        inst.destroy = () => { top = 0; };
        return inst;
    };
    s.swap('inspector-pane');
    top = 700;
    s.swap('table-pane', 'the page re-listed by an undo');
    assert.equal(top, 700, 'a table-pane refresh keeps the inspector scroll');
    s.swap('table-pane', 'and again');
    assert.equal(top, 700, 'every time');
    top = 0;
    s.swap('table-pane', 'at the top');
    assert.equal(top, 0, 'a reader at the top stays at the top');
} else if (mode === 'opens_full') {
    // With no remembered choice no strip of the page stays above a new panel
    const s = await boot();
    const pane = s.d.getElementById('table-pane');
    pane.innerHTML = '<h1>Live edit - Json Tree view</h1><p>text</p>';
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), FULL, 'a qubit opens at the full height');
    assert.equal(s.user(), null, 'SM\'s move is not a choice');
    assert.ok(pane.parentElement.classList.contains('ui-split-top'), 'and it is marked as the top state');
    s.swap('inspector-pane', RUN);
    eq(s.sizes(), FULL, 'a run opened over another page too');
    s.drag([50, 50]);
    s.swap('inspector-pane', QUBIT);
    eq(s.sizes(), [50, 50], 'a remembered drag still wins');
    assert.ok(!pane.parentElement.classList.contains('ui-split-top'), 'a real split is not the top state');
    // the Datasets run list keeps its rows
    const t = await boot();
    t.d.getElementById('table-pane').innerHTML = DATASETS_PAGE;
    t.swap('inspector-pane', RUN);
    eq(t.sizes(), SPLIT.datasetExpandedSizes, 'the Datasets list keeps the run position');
} else if (mode === 'top_edge') {
    // A panel expanded to the very top sits right under the top bar: its page marks the split
    // ui-split-top, and ui-kit.css then gives the panel the page's padding and the bar's row
    // (test_split_arrows.py::test_the_top_panel_leaves_no_strip measures that CSS). Every panel
    // kind that opens in the inspector shares this one split, so each is checked.
    const PANELS = {
        qubit: QUBIT,
        pair: '<div class="inspector-header inspector-header-pair"></div><div class="pair-detail">pair</div>',
        pulse: '<div class="inspector-header inspector-header-pulse"></div>pulse',
        dataset_run: RUN,
        dataset_compare: '<div class="inspector-header inspector-header-dataset inspector-header-compare"></div>cmp',
        search: '<div class="inspector-header inspector-header-search"></div>results',
        field_history: '<div class="field-history">ledger</div>',
    };
    for (const [name, html] of Object.entries(PANELS)) {
        const s = await boot();
        const host = s.d.getElementById('table-pane').parentElement;
        Object.defineProperty(host, 'clientHeight', { value: 845, configurable: true });
        const top = () => host.classList.contains('ui-split-top');
        s.swap('inspector-pane', html);
        assert.ok(top(), name + ': a first panel opens at the top');
        s.drag([40, 60]);
        assert.ok(!top(), name + ': a panel dragged to mid-page is not at the top');
        s.calls[s.calls.length - 1].options.onDrag([0, 100]);
        assert.ok(top(), name + ': while the bar is still held at the top, the strip is already gone');
        s.drag([0, 100]);
        assert.ok(top(), name + ': dragged all the way up, the panel is at the top');
        s.drag([40, 60]);
        assert.ok(!top(), name + ': dragged back down, the page shows again');
        // Split.js with minSize 0 stops a hair above 0 %: under one bar of page left is the top
        s.drag([0.71, 99.29]);
        assert.ok(top(), name + ': a page sliver thinner than the bar is the top');
        s.drag([3, 97]);
        assert.ok(!top(), name + ': a page row taller than the bar is not');
        s.drag([0, 100]);
        s.d.getElementById('inspector-pane').innerHTML = '';
        s.swap('inspector-pane');
        assert.ok(!top(), name + ': closing the panel leaves the page whole');
        s.swap('inspector-pane', html);
        eq(s.sizes(), [0, 100], name + ': the next panel reopens at the top');
        assert.ok(top(), name + ': and is marked at the top on open');
        s.press('down');
        assert.ok(!top(), name + ': the down arrow leaves the top');
        s.drag([0, 100]);
        s.d.getElementById('nav-qubits').click();
        assert.ok(s.docked() && !top(), name + ': a docked panel is not at the top');
        const p = s.d.getElementById('inspector-pane');
        p.innerHTML = '';
        s.swap('inspector-pane');
        assert.ok(!top(), name + ': a closed panel leaves the page whole');
    }
} else {
    assert.fail('unknown mode: ' + mode);
}
console.log('split_arrows_selfcheck: ' + mode + ' ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
