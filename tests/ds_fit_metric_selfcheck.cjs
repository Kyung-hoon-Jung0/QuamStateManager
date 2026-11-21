'use strict';
// Final round P2: at a 1280 px window with two data folders (Folder column shown) the fitted Datasets table keeps
// Key Metric wide enough for "fidelity 0.xxxx" and "epg X.XX% / NN%", Status and When whole, and fits the pane.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/dataset-virtual.js'), 'utf8');
const base = {exp: 'spec', date: '2026-08-01', time: '01:00:00', tags: [], sm: {}, pm: {}, status: 'finished',
              q: ['q1'], oc: {q1: 'successful'}};
const rows = [Object.assign({}, base, {id: 2, f: 'a', metric: 'fidelity 0.6008'}),
              Object.assign({}, base, {id: 1, f: 'b', metric: 'epg 18.59% / 61%'})];
const folders = [{key: 'a', label: 'a', full_path: 'a'}, {key: 'b', label: 'b', full_path: 'b'}];
let checks = 0;
// 905 / 909 / 917: the 1280 px pane with and without a vertical scrollbar
for (const pane of [905, 909, 917]) {
    const dom = new JSDOM('<body><input id="dataset-search" value="">' +
        '<script id="ds-folders-data">' + JSON.stringify(folders) + '</script>' +
        '<script id="ds-rows-data" data-now="1">' + JSON.stringify(rows) + '</script>' +
        '<div id="datasets-scroll"><table><colgroup id="datasets-colgroup"></colgroup>' +
        '<tbody id="datasets-tbody"></tbody></table></div></body>', {
        url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window;
    w.fetch = () => new Promise(() => {});
    Object.defineProperty(w.document.getElementById('datasets-scroll'), 'clientWidth', {get: () => pane});
    w.eval(source);
    w.DatasetVirtual.init();
    const cols = Array.from(w.document.querySelectorAll('#datasets-colgroup col'));
    const colW = key => parseFloat((cols.find(c => c.dataset.colKey === key) || {style: {}}).style.width);
    assert.ok(cols.some(c => c.dataset.colKey === 'folder'), pane + ': the Folder column is shown'); checks++;
    const sum = cols.reduce((a, c) => a + parseFloat(c.style.width), 0);
    assert.ok(sum <= pane - 4, pane + ': the table fits its pane: ' + sum); checks++;
    // 16 glyphs of "epg 18.59% / 61%" at ~0.6 em of 14.08 px plus 2 x 0.35rem padding: 136 px; 140 keeps margin
    assert.ok(colW('metric') >= 140, pane + ': Key Metric shows its text whole: ' + colW('metric')); checks++;
    assert.ok(colW('status') >= 204, pane + ': Status keeps 204: ' + colW('status')); checks++;
    assert.ok(colW('when') >= 88, pane + ': When keeps 88 ("11/21 12:34"): ' + colW('when')); checks++;
    w.close();
}
console.log(checks + ' checks passed');
