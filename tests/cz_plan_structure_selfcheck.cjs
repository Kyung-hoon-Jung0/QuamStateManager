"use strict";
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const mode = process.argv[3] || 'picker';
const payload = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const wait = () => new Promise(resolve => setTimeout(resolve, 30));
(async () => {
    const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {
        url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true
    });
    const w = dom.window, d = w.document;
    w.fetch = url => Promise.resolve({json: () => Promise.resolve(url.startsWith('/cz-plan/data') ? payload : url.startsWith('/cz-plan/map') ?
        {ok: true, operations: ['cz', 'cz_two'], qubits: [], segments: [], pairs: {}, refused: [], missing: []} :
        {ok: false, error: 'stub'} )});
    w._plotlyRender = () => Promise.resolve();
    w._plotlyPurge = () => {};
    w.requirePlotly = () => Promise.resolve();
    ['cz-map.js', 'cz-plan.js'].forEach(file => w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static', file), 'utf8')));
    await wait();
    if (mode === 'card') {
        const card = d.getElementById('czp-card');
        assert(card.textContent.includes('Z amp (V)'), 'the card displays the server amplitude');
        payload.row.lines.forEach(line => {
            assert(card.textContent.includes(line.amp_text), 'the card prints the server text');
            assert(Array.from(card.querySelectorAll('dd')).some(dd => dd.title === line.amp_title));
        });
    } else if (mode === 'amp') {
        const rows = Array.from(d.querySelectorAll('.czp-row'));
        rows.forEach((row, i) => row.setAttribute('data-s-amp', ['.12', '', '.09', '.2'][i]));
        const button = d.querySelector('th[data-sort-key="amp"] button');
        assert(button, 'amplitude has a sortable header');
        button.click();
        const values = () => Array.from(d.querySelectorAll('.czp-row'), row => row.getAttribute('data-s-amp'));
        assert.deepStrictEqual(values(), ['.09', '.12', '.2', '']);
        button.click();
        assert.deepStrictEqual(values(), ['.2', '.12', '.09', '']);
    } else {
        const map = d.getElementById('czp-map-op'), table = d.getElementById('czp-table-op');
        assert(table, 'the local operation picker exists');
        assert.deepStrictEqual(Array.from(table.options, o => [o.value, o.text]), Array.from(map.options, o => [o.value, o.text]), 'options copied after asynchronous map load');
        assert.strictEqual(table.options.length, 3);
        let changes = 0;
        map.addEventListener('change', () => changes++);
        table.value = 'cz_two'; table.dispatchEvent(new w.Event('change'));
        await wait();
        assert.strictEqual(map.value, 'cz_two');
        assert.strictEqual(changes, 1, 'exactly one map change, no event loop');
        assert.strictEqual(d.querySelectorAll('.czp-row:not([hidden])').length, 1);
        map.value = 'cz'; map.dispatchEvent(new w.Event('change'));
        await wait();
        assert.strictEqual(table.value, 'cz');
        assert.strictEqual(changes, 2);
        assert.strictEqual(d.querySelectorAll('.czp-row:not([hidden])').length, 2);
        map.value = ''; map.dispatchEvent(new w.Event('change'));
        await wait();
        assert.strictEqual(table.value, '');
    }
    console.log('structure assertions passed');
    dom.window.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
