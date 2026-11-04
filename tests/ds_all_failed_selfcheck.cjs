'use strict';
// F9: a run whose every target failed must not read like a good run.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/dataset-virtual.js'), 'utf8');
const base = {f: 'root1', exp: 'spec', date: '2026-08-01', time: '01:00:00', tags: [], sm: {}, pm: {},
              status: 'finished', metric: '5.0450 GHz'};
const rows = [
    Object.assign({id: 3, q: ['q1', 'q2'], oc: {q1: 'failed', q2: 'failed'}}, base),
    Object.assign({id: 2, q: ['q1', 'q2'], oc: {q1: 'successful', q2: 'failed'}}, base),
    Object.assign({id: 1, q: ['q1']}, base),
];
const dom = new JSDOM('<body><input id="dataset-search" value="">' +
    '<script id="ds-rows-data" data-now="1">' + JSON.stringify(rows) + '</script>' +
    '<div id="datasets-scroll"><table><tbody id="datasets-tbody"></tbody></table></div></body>', {
    url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
w.fetch = () => new Promise(() => {});
w.eval(source);
w.DatasetVirtual.init();
setTimeout(() => {
    let checks = 0;
    const tr = id => Array.from(w.document.querySelectorAll('#datasets-tbody tr'))
        .find(t => (t.textContent || '').includes('#' + id));
    const r3 = tr(3), r2 = tr(2), r1 = tr(1);
    assert.ok(r3 && r2 && r1, 'three rows rendered'); checks++;
    assert.ok(r3.querySelector('.ds-all-failed'), 'all-failed run carries the failed mark'); checks++;
    assert.ok(r3.querySelector('.ds-metric-failed'), 'its key metric is marked as a failed fit'); checks++;
    assert.ok(!r2.querySelector('.ds-all-failed') && !r2.querySelector('.ds-metric-failed'), 'a partly successful run is not marked'); checks++;
    assert.ok(!r1.querySelector('.ds-all-failed'), 'a run without outcomes is not marked'); checks++;
    console.log(checks + ' checks passed');
    w.close();
}, 300);
