'use strict';
// Compare the served table, shared blob, plot coordinates and marker attributes.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const dom = new JSDOM('<body>' + fixture.html + '</body>', {
    url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true
});
const w = dom.window, d = w.document;
const root = path.join(__dirname, '..', 'quam_state_manager/web/static');
const renders = new Map(), posts = [];
w.requirePlotly = () => Promise.resolve();
w._plotlyRender = (el, traces) => {
    el.data = traces;
    renders.set(el.id, traces);
    el.innerHTML = '<svg><g class="scatterlayer">' + traces.map(t => '<g class="trace">' +
        ((t.mode || '').includes('markers') ? (t.x || []).map(() => '<path class="point"/>').join('') : '') + '</g>').join('') + '</g></svg>';
    return Promise.resolve();
};
w.fetch = (url, options) => {
    let data;
    if (url === '/field/edit-batch') {
        posts.push(JSON.parse(options.body));
        data = {ok: true};
    } else if (url.startsWith('/cz-plan/solve')) data = fixture.solves[new URL(url, 'http://localhost').searchParams.get('op')];
    else if (url.startsWith('/cz-plan/data')) data = fixture.data[new URL(url, 'http://localhost').searchParams.get('op')];
    else if (url.startsWith('/cz-plan/map')) data = fixture.map;
    else if (url.startsWith('/cz-plan/loss')) data = {ok: true, qubits: {}, curves: {}, notes: []};
    else data = {ok: true, ops: {}};
    return Promise.resolve({json: () => Promise.resolve(JSON.parse(JSON.stringify(data)))});
};
w._keepMineSeen = () => ({n: '3', sig: 'seen'});
['topo-graph.js', 'cz-map.js', 'cz-plan.js'].forEach(f => w.eval(fs.readFileSync(path.join(root, f), 'utf8')));
const wait = () => new Promise(r => setTimeout(r, 45));
(async () => {
    w.CzPlan.mount();
    await wait();
    const toggle = d.querySelector('#czp-include-coupler');
    for (const enabled of [false, true, false]) {
        toggle.checked = enabled;
        toggle.dispatchEvent(new w.Event('change'));
        await wait();
        const blob = JSON.parse(d.querySelector('#cz-operating-points').textContent);
        const map = renders.get('czp-map') || [];
        let shifted = 0;
        for (const trace of map.filter(t => t._czOperating)) {
            const p = trace._czOperating;
            const point = blob.find(b => b.op === p.op && b.line === p.line);
            assert(point);
            const cell = d.querySelector(`span[data-op="${p.op}"][data-line="${p.line}"]`);
            for (const key of ['coupler_shift_hz','shifted_frequency_hz','coupler_method','coupler_frequency_hz']) {
                assert.strictEqual(p[key], point[key]);
                const attr = cell.getAttribute('data-' + key.replaceAll('_','-'));
                if (point.coupler_frequency_hz != null) assert.strictEqual(point[key] == null ? attr : String(point[key]), attr);
            }
            if ((trace.marker || {}).symbol === 'diamond-open') {
                shifted++;
                assert.strictEqual(trace.y[0], point.shifted_frequency_hz / 1e9);
                assert(cell.textContent.includes((point.shifted_frequency_hz / 1e9).toFixed(4)));
                const rs = enabled ? point.shifted_resonances : point.bare_resonances;
                const near = rs.filter(r=>r.distance_hz!=null).sort((a,b)=>Math.abs(a.distance_hz)-Math.abs(b.distance_hz))[0];
                const row = cell.closest('tr');
                assert(row.cells[6].textContent.includes((near.distance_hz/1e6).toFixed(1)));
                const segment = map.find(t => t.meta === 'seg' && t.text.some(text => text && text.includes(p.pair)));
                assert(segment && segment.text.some(text => text && text.includes((near.distance_hz/1e6).toFixed(1))), 'map and table select the same server distance');
            }
        }
        assert(shifted > 0);
    }
    dom.window.close();
    console.log('Coupler table/map/blob and toggle checks passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
