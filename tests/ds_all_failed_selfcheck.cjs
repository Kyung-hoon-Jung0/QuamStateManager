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
    // a partial failure whose key metric came from the failed target
    Object.assign({id: 4, q: ['q1', 'q2'], oc: {q1: 'failed', q2: 'successful'}, mq: 'q1'}, base),
    // a partial failure whose key metric came from a successful target
    Object.assign({id: 5, q: ['q1', 'q2'], oc: {q1: 'successful', q2: 'failed'}, mq: 'q1'}, base),
    // every target successful: the plain row
    Object.assign({id: 6, q: ['q1', 'q2'], oc: {q1: 'successful', q2: 'successful'}}, base),
    // review r1 P3-6: a skipped target is neutral, an aborted one is a failure
    Object.assign({id: 7, q: ['q1'], oc: {q1: 'skipped'}}, base),
    Object.assign({id: 8, q: ['q1'], oc: {q1: 'aborted'}}, base),
    // final round P3-3: a failed run status stays on the Status cell beside the target count
    Object.assign({}, base, {id: 9, q: ['q1', 'q2'], status: 'error', oc: {q1: 'successful', q2: 'failed'}}),
    Object.assign({}, base, {id: 10, q: ['q1', 'q2'], status: 'Aborted', oc: {q1: 'failed', q2: 'failed'}}),
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
        .find(t => new RegExp('#' + id + '(?!\\d)').test(t.textContent || ''));
    const r3 = tr(3), r2 = tr(2), r1 = tr(1);
    assert.ok(r3 && r2 && r1, 'three rows rendered'); checks++;
    assert.ok(r3.querySelector('.ds-all-failed'), 'all-failed run carries the failed mark'); checks++;
    assert.ok(r3.querySelector('.ds-metric-failed'), 'its key metric is marked as a failed fit'); checks++;
    assert.ok(!r2.querySelector('.ds-all-failed') && !r2.querySelector('.ds-metric-failed'), 'a partly successful run is not marked'); checks++;
    assert.ok(!r1.querySelector('.ds-all-failed'), 'a run without outcomes is not marked'); checks++;
    const r4 = tr(4), r5 = tr(5), r6 = tr(6);
    assert.ok(r4 && r5 && r6, 'partial rows rendered'); checks++;
    const st2 = r2.querySelector('[data-failed]');
    assert.ok(st2 && st2.getAttribute('data-failed') === '1/2' && /1 of 2 failed/.test(st2.textContent),
              'a partial failure says how many targets failed'); checks++;
    assert.ok(r4.querySelector('.ds-metric-failed'), 'a metric read off the failed target is marked'); checks++;
    assert.ok(!r5.querySelector('.ds-metric-failed') && r5.querySelector('[data-failed]'),
              'a metric from a successful target stays plain, the status still counts the failure'); checks++;
    assert.ok(!r6.querySelector('[data-failed]') && !r6.querySelector('.ds-metric-failed')
              && !r6.querySelector('.ds-all-failed'), 'an all-successful run is unmarked'); checks++;
    assert.ok(!r1.querySelector('[data-failed]'), 'a run without outcomes has no failure count'); checks++;
    const r7 = tr(7), r8 = tr(8);
    assert.ok(r7 && r8, 'skipped / aborted rows rendered'); checks++;
    assert.ok(!r7.querySelector('.ds-all-failed') && !r7.querySelector('[data-failed]')
              && !r7.querySelector('.ds-metric-failed'),
              'a skipped target is not "all failed" nor "k of n failed"'); checks++;
    assert.ok(r8.querySelector('.ds-all-failed') && r8.querySelector('.ds-metric-failed'),
              'an aborted-only run is "all failed" and its metric is from a failed fit'); checks++;
    const cell = r => r.querySelector('.ds-status').textContent;
    assert.equal(cell(tr(9)), 'error, 1 of 2 failed', 'an error run says "error" beside its count'); checks++;
    assert.equal(cell(tr(10)), 'aborted, all failed', 'an aborted all-failed run keeps "all failed"'); checks++;
    assert.equal(cell(r2), '1 of 2 failed', 'a finished run with a failed target keeps its count alone'); checks++;
    assert.equal(cell(r3), 'all failed', 'a finished all-failed run keeps "all failed"'); checks++;
    // P3-4: the Status column fits its longest label, so the chip never ends in an ellipsis
    const col = w.document.querySelector('#datasets-colgroup col[data-col-key="status"]');
    const width = col && parseFloat(col.style.width);
    // 20 characters of the .72rem bold chip at 16 px root: at least 20 * 0.62 * 11.52 px plus paddings
    assert.ok(width >= Math.ceil(20 * 0.62 * 11.52) + 30, 'the Status column fits "error, 1 of 2 failed": ' + width);
    checks++;
    console.log(checks + ' checks passed');
    w.close();
}, 300);
