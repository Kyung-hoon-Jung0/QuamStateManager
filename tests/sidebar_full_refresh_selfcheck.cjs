'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const app = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8');
const start = app.indexOf('    // Capture before the refresh replaces full groups');
const end = app.indexOf('    function _showNewRunPopup', start);
// Keep the old-source mutation executable: it has the refresh function but no capture listener.
const code = start < 0 ? app.slice(app.indexOf('    window.refreshRunLists ='),
    app.indexOf('    function _showNewRunPopup')) : app.slice(start, end);
const dom = new JSDOM(`<button class="btn-workspace-refresh">Refresh</button><div id="sidebar-tree">
    <details open class="tree-dir tree-date" data-tpath="2026-11-20"><ul class="tree-entries">
    <li hidden data-tree-group-count="285"></li>${'<li class="tree-entry">run</li>'.repeat(285)}
    </ul></details></div>`, { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(code);
let requests = 0;
d.querySelector('button').addEventListener('click', () => {
    const detail = { path: '/workspace/refresh', parameters: {} };
    d.dispatchEvent(new w.CustomEvent('htmx:configRequest', { detail }));
    assert.deepEqual(JSON.parse(detail.parameters.open_groups || '[]'), ['2026-11-20']);
    assert.deepEqual(JSON.parse(detail.parameters.full_groups || '[]'), ['2026-11-20']);
    requests++;
});
w.refreshRunLists(); // the chip's handler calls the same public refresh function
d.querySelector('button').click();
assert.equal(requests, 2, 'chip and explicit refresh both preserve Show all');
for (const route of ['/workspace/tree', '/workspace/tree?name=']) {
    const detail = { path: route, parameters: {} };
    d.dispatchEvent(new w.CustomEvent('htmx:configRequest', { detail }));
    assert.deepEqual(JSON.parse(detail.parameters.full_groups || '[]'), ['2026-11-20'],
        'direct tree reload also keeps Show all');
}
w.close();
console.log('full group refresh checks passed');
