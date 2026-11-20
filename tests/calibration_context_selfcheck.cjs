/* Last-run context and shared acknowledgement, with real client modules. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const dom = new JSDOM(`<div id="pending-tray"><button class="state-status-badge">Synced</button></div>
    <small data-last-run hidden></small><p data-last-run hidden></p>
    <div id="sidebar-tree" style="overflow:auto"><details open><summary>day <small>(631)</small></summary>
    <ul id="group"><span data-tree-group-count="632" hidden></span>
    <li class="tree-entry-click" data-uid="f:2">run</li></ul></details></div>`,
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const w = dom.window, d = w.document;
w.eval(read('static/sync-badge.js'));
const chip = () => d.querySelector('.state-status-notice');
const pin = process.env.CONTEXT_PIN || 'all';
if (pin === 'all' || pin === 'context') {
    assert.equal(typeof w.SyncBadge.lastRun, 'function', 'last-run renderer exists');
    w.SyncBadge.lastRun({ uid: 'f:2', run_id: 2, experiment_name: 'power_rabi_full_node_name',
        qubits: ['q1', 'q2'], time: '18:49:20' });
    for (const e of d.querySelectorAll('[data-last-run]')) {
        assert.equal(e.textContent, 'last: #2 power_rabi_full_node_name q1,q2 18:49');
        assert.equal(e.hidden, false);
        assert.equal(e.title, e.textContent, 'full name is available on hover');
    }
    d.querySelector('p').textContent = '';
    d.dispatchEvent(new w.CustomEvent('htmx:afterSwap'));
    assert.ok(d.querySelector('p').textContent.includes('power_rabi'), 'overview swap restores last context');
    assert.ok(read('templates/base.html').includes('data-last-run'), 'top bar ships its context host');
    assert.ok(read('templates/_wiring.html').includes('data-last-run'), 'overview ships its context host');
}
if (pin === 'all' || pin === 'indexing') {
    const run = { uid: 'f:163', run_id: 163, experiment_name: 'ramsey', indexing: true };
    w.SyncBadge.lastRun(run);
    assert.match(d.querySelector('[data-last-run]').textContent, /#163 ramsey .*indexing/);
    d.dispatchEvent(new w.CustomEvent('htmx:afterSwap'));
    assert.match(d.querySelector('p').textContent, /indexing/);
    w.SyncBadge.lastRun({ ...run, run_id: 280, indexing: false });
    assert.match(d.querySelector('[data-last-run]').textContent, /#280/);
    assert.doesNotMatch(d.querySelector('[data-last-run]').textContent, /indexing/);
}
if (pin === 'all' || pin === 'seen') {
    const row = d.querySelector('[data-uid]'), tree = d.getElementById('sidebar-tree');
    const box = (top, bottom) => ({ top, bottom, left: 0, right: 150, width: 150, height: bottom - top });
    tree.getBoundingClientRect = () => box(0, 100);
    row.getBoundingClientRect = () => box(200, 220);
    w.SyncBadge.note('new', { count: 2, uids: ['f:2', 'f:3'] });
    assert.equal(chip().textContent, '2 new', 'an open group offscreen has not been seen');
    assert.equal(chip().title, '2 runs landed since you last looked');
    row.getBoundingClientRect = () => box(30, 50);
    tree.dispatchEvent(new w.Event('scroll'));
    assert.equal(chip().title, '1 run landed since you last looked');
    assert.equal(chip().textContent, '1 new', 'scrolling the row into view acknowledges it');
    w.SyncBadge.acknowledge(['f:3']);
    assert.equal(chip(), null, 'a table dismissal acknowledges its exact identities');
    w.SyncBadge.note('new', { count: 3, uids: ['f:2', 'f:3', 'f:4'] });
    assert.equal(chip().textContent, '1 new', 'a repeated poll cannot resurrect dismissed identities');
}
if (pin === 'all' || pin === 'summary') {
    const app = read('static/app.js');
    const start = app.lastIndexOf("document.addEventListener('htmx:afterSwap'", app.indexOf("data-tree-group-count"));
    const end = app.indexOf('\n});', start) + 4;
    w._reapplyShownTreeHighlight = () => {};
    w.eval(app.slice(start, end));
    d.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: d.getElementById('group') } }));
    assert.equal(d.querySelector('summary small').textContent, '(632)', 'summary uses the landed group snapshot');
    const fragment = read('templates/_sidebar_tree_entries.html');
    assert.ok(fragment.includes('data-tree-group-count="{{ group_total | default(entries | length) }}"'));
}
if (pin === 'all' || pin === 'token') {
    const app = read('static/app.js');
    const block = app.slice(app.indexOf('    roleColors: {'), app.indexOf('    roleColors: {') + 400);
    assert.ok(block.includes("rr_in:     'var(--ui-role-rr-in)'"), 'readout input has one token source');
    assert.ok(!app.includes('#c026d3'), 'dead fuchsia literal is gone');
}
w.close();
console.log('calibration context checks passed');
