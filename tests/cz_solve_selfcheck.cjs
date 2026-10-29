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
    // UX-5: the solve preview belongs to the optional proposal controls.
    const solveBox = d.querySelector('.czp-solve-box');
    solveBox.open = true;
    solveBox.dispatchEvent(new w.Event('toggle'));
    await wait();
    for (const op of Object.keys(fixture.data)) {
        d.querySelector(`[data-op="${op}"].czp-row`).dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
        await wait();
        const blob = JSON.parse(d.querySelector('#cz-operating-points').textContent);
        const traces = renders.get('czp-flux') || [];
        for (const p of blob.filter(p => p.op === op)) {
            const cell = d.querySelector(`span[data-op="${op}"][data-line="${p.line}"]`);
            if (p.frequency_hz == null) continue;
            assert.strictEqual(Number(cell.dataset.freqHz), p.frequency_hz);
            const amp = d.querySelector(`span[data-value="amplitude"][data-op="${op}"][data-line="${p.line}"]`);
            assert.strictEqual(Number(amp.dataset.amplitudeV), p.amplitude_v);
            assert.strictEqual(amp.textContent, p.amp_text);
            assert.strictEqual(Number(cell.textContent), Number(p.y.toFixed(4)));
            const marker = traces.find(t => t._czOperating && !t._czOperating.preview && t._czOperating.op === op && t._czOperating.line === p.line);
            assert(marker, 'every known line has a marker');
            assert.strictEqual(marker.x[0], p.x);
            assert.strictEqual(marker.y[0], p.y);
            const node = d.querySelector(`#czp-flux .point[data-op="${op}"][data-line="${p.line}"]:not([data-preview])`);
            assert(node, 'actual marker has comparison attributes');
            assert.strictEqual(Number(node.dataset.freqHz), p.frequency_hz);
            assert.strictEqual(node.dataset.method, p.method);
            assert.strictEqual(node.dataset.path, p.path || '');
        }
        const proposal = fixture.solves[op];
        if (proposal.ok) {
            const preview = traces.find(t => t._czOperating && t._czOperating.preview);
            assert(preview, 'proposal is drawn from server preview coordinates');
            const cell = d.querySelector('td[data-preview="1"]');
            assert.strictEqual(Number(cell.dataset.freqHz), proposal.frequency_hz);
            assert.strictEqual(Number(cell.textContent), preview.y[0]);
            assert.strictEqual(preview.x[0], proposal.amplitude_v);
            d.querySelector('.czp-solve-stage').click();
            await wait();
            const post = posts.at(-1);
            assert.strictEqual(post.updates.length, 1);
            assert.strictEqual(post.updates[0].dot_path, proposal.path);
            assert.strictEqual(post.updates[0].value, proposal.amplitude_v);
            assert.strictEqual(post.expect_chip, proposal.chip);
            assert.strictEqual(post.group, 'new');
            assert.strictEqual(post.seen_changes, '3');
            assert.strictEqual(post.seen_sig, 'seen');
            assert(blob.some(p => p.op === op && p.solve && p.solve.amplitude_v === proposal.amplitude_v));
        }
    }
    const map = renders.get('czp-map') || [];
    for (const trace of map.filter(t => t._czOperating)) {
        const p = trace._czOperating;
        assert.strictEqual(trace.y[0], p.y);
        assert.strictEqual(trace.x[0], p.map_x);
    }
    assert(map.some(t => t._czOperating), 'map markers consume shared operating points');
    dom.window.close();
    console.log('Operating point and Stage checks passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
