'use strict';
// Fix X: the Datasets Key Metric cell carries its full text as a title (X-3) and a finished run whose
// outcomes map is empty says "finished, no outcome" (X-4); a run with outcomes is unchanged.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/dataset-virtual.js'), 'utf8');
const base = {f: 'root1', exp: 'spec', date: '2026-08-01', time: '01:00:00', tags: [], sm: {}, pm: {},
              status: 'finished', q: ['q1']};
const rows = [
    Object.assign({}, base, {id: 3, oc: {}, metric: '5.0450 GHz'}),
    Object.assign({}, base, {id: 2, oc: {q1: 'successful'}, metric: 'fidelity 0.9876'}),
    Object.assign({}, base, {id: 1, metric: ''}),
];
const dom = new JSDOM('<body><input id="dataset-search" value="">' +
    '<script id="ds-rows-data" data-now="1">' + JSON.stringify(rows) + '</script>' +
    '<div id="datasets-scroll"><table><colgroup id="datasets-colgroup"></colgroup>' +
    '<tbody id="datasets-tbody"></tbody></table></div></body>', {
    url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
w.fetch = () => new Promise(() => {});
w.eval(source);
w.DatasetVirtual.init();
setTimeout(() => {
    let checks = 0;
    const tr = id => Array.from(w.document.querySelectorAll('#datasets-tbody tr'))
        .find(t => new RegExp('#' + id + '(?!\d)').test(t.textContent || ''));
    const cell = r => r.querySelector('.ds-status').textContent;
    const r3 = tr(3), r2 = tr(2), r1 = tr(1);
    assert.ok(r3 && r2 && r1, 'three rows rendered'); checks++;
    assert.equal(cell(r3), 'finished, no outcome', 'outcomes {} says "finished, no outcome"'); checks++;
    assert.equal(cell(r2), 'finished', 'a run with outcomes is unchanged'); checks++;
    assert.equal(cell(r1), 'finished', 'a run with no outcomes key is unchanged'); checks++;
    const metricTd = (r, text) => Array.from(r.querySelectorAll('td')).find(td => td.title === text);
    const m2 = metricTd(r2, 'fidelity 0.9876');
    assert.ok(m2 && m2.textContent.trim() === 'fidelity 0.9876', 'the metric title equals its full text'); checks++;
    assert.ok(metricTd(r3, '5.0450 GHz'), 'every metric cell carries its title'); checks++;
    assert.ok(Array.from(r1.querySelectorAll('td')).some(td => /^No key metric/.test(td.title)),
              'a blank metric cell names why it is blank'); checks++;
    const col = w.document.querySelector('#datasets-colgroup col[data-col-key="metric"]');
    const width = col && parseFloat(col.style.width);
    // style.css: cells are --font-mono at --data-table-font 0.88em (14.08 px at 16 px), ~0.6 em per glyph,
    // plus 2 x --data-table-td-pad-h 0.35rem: 15 glyphs of "fidelity 0.9876" need 127 + 12 px
    assert.ok(width >= Math.ceil(15 * 0.6 * 14.08) + 12, 'Key Metric fits "fidelity 0.xxxx": ' + width); checks++;
    console.log(checks + ' checks passed');
    w.close();
}, 300);
