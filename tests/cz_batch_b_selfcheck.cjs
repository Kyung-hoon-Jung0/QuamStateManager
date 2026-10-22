'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const Module = require('module');
const mode = process.argv[2];
function harness(file, names) {
    const filename = path.join(__dirname, file);
    const source = fs.readFileSync(filename, 'utf8').split('(async () => {')[0];
    const mod = new Module(filename, module);
    mod.filename = filename; mod.paths = module.paths;
    mod._compile(source + '\nmodule.exports = {' + names + '};', filename);
    return mod.exports;
}
(async () => {
    if (mode === 'history' || mode === 'card') {
        const h = harness('cz_plan_selfcheck.cjs', 'w,d,payload,answer,historyAnswer,wait');
        const {w,d} = h;
        let processed = 0;
        w.htmx = {process: el => { assert.strictEqual(el.id, 'czp-history-note'); processed++; }};
        h.historyAnswer.note = 'No data folder is linked to this chip.';
        h.historyAnswer.link = {url: '/hub/link-folder', offer: true};
        h.historyAnswer.ops['qubit_pairs.p.macros.b'].note = null;
        w.CzPlan.mount(); await h.wait(20);
        if (mode === 'history') {
            const note = d.getElementById('czp-history-note');
            assert(note.textContent.includes(h.historyAnswer.note), 'ledger mode preserves its explanation');
            const button = note.querySelector('button');
            assert(button && button.textContent === 'Link the data folder');
            assert.strictEqual(button.getAttribute('hx-get'), '/hub/link-folder');
            assert.strictEqual(button.getAttribute('hx-target'), '#hub-folder-body');
            assert.strictEqual(button.getAttribute('onclick'), 'HubFolders.open(this)');
            assert.strictEqual(button.getAttribute('hx-swap'), 'innerHTML');
            assert(processed > 0, 'inserted drawer button is processed by htmx');
            assert.strictEqual(d.querySelectorAll('td[data-last]')[1].firstChild.title, h.historyAnswer.note);
        } else {
            const data = h.payload('qubit_pairs.p.macros.a', false);
            const unknown = process.argv[3] === 'unknown';
            data.row.coupler = unknown
                ? {name: 'c12', amp_text: 'unknown', amp_title: 'x: not playable: flux missing on c12', amp_reason: 'not playable: flux missing on c12'}
                : {name: 'c12', amp_text: '+100 mV', amp_title: 'couplers.c12.operations.flux'};
            data.row.coupler_amplitude_v = unknown ? null : .3;
            h.answer(0, data); await h.wait(30);
            const line = d.querySelector('.czp-card-coupler');
            if (unknown) assert(line && line.textContent === 'unknown (not playable: flux missing on c12)', 'an unresolved coupler says unknown and why');
            else assert(line && line.textContent === '+100 mV', 'card prints the played coupler amplitude');
            assert.strictEqual(line.title, data.row.coupler.amp_title);
        }
        w.close();
    } else if (mode === 'fit') {
        const h = harness('cz_table_fit_selfcheck.cjs', 'w,d,observers,wait');
        const {w,d} = h, table = d.getElementById('czp-table'), wrap = table.parentElement;
        let tableWidth = 600, wrapWidth = 700;
        table.getBoundingClientRect = () => ({width: tableWidth});
        table.tHead.getBoundingClientRect = () => ({width: tableWidth, height: 31});
        Object.defineProperty(wrap, 'clientWidth', {get: () => wrapWidth});
        // Overflow changes scrollWidth; the measurement must never depend on that value.
        Object.defineProperty(wrap, 'scrollWidth', {get: () => wrap.classList.contains('czp-wrap-fits') ? 900 : 600});
        w.CzPlan.mount(); await h.wait(20);
        const sync = () => h.observers.forEach(o => o.cb([]));
        assert(wrap.classList.contains('czp-wrap-fits'), 'a table within the content width enables sticky headers');
        sync(); sync(); assert(wrap.classList.contains('czp-wrap-fits'), 'two measurements never flip-flop');
        tableWidth = 800; sync(); sync(); assert(!wrap.classList.contains('czp-wrap-fits'), 'a wide table retains its horizontal scroller');
        tableWidth = 680; wrap.style.padding = '0 15px'; sync(); assert(!wrap.classList.contains('czp-wrap-fits'), 'padding is excluded from available width');
        wrapWidth = 720; sync(); sync(); assert(wrap.classList.contains('czp-wrap-fits'));
        assert(h.observers.some(o => o.el === table), 'the table is observed');
        assert.strictEqual(table.style.getPropertyValue('--czp-head-h'), '31px', 'the sticky header height is measured for the rows scroll margin');
        const pane = d.createElement('div'); pane.id = 'table-pane';
        d.body.appendChild(pane); pane.appendChild(d.getElementById('czp-root'));
        const scrolled = [];
        table.querySelectorAll('tbody tr').forEach((tr,i) => {
            tr.classList.add('clickable-row'); tr.scrollIntoView = options => scrolled.push([i, options.block]);
        });
        const app = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8');
        const handler = app.slice(app.indexOf('(function() {', app.indexOf('/* Keyboard navigation for data tables')));
        w.eval(handler.slice(0, handler.indexOf('})();') + 5));
        for (const key of ['ArrowDown', 'ArrowDown', 'ArrowUp']) d.dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles: true}));
        assert.deepStrictEqual(scrolled, [[0,'nearest'], [1,'nearest'], [0,'nearest']], 'row navigation scrolls within the page pane');
        w.close();
    } else if (['map', 'map_segment', 'map_refused', 'map_unknown'].includes(mode)) {
        const h = harness('cz_map_selfcheck.cjs', 'page,NO_LOSS');
        const qubits = [1,2,3].map((i) => ({name: 'q'+i+'x', path: 'qubits.q'+i, f01_hz: 5e9,
            grid_location: i+',0', direction: 'unknown', defects: [], safe: []}));
        const seg = (q, f) => ({pair: 'q1-2', operation: 'cz', path: 'qubit_pairs.q1-2.macros.cz',
            qubit: 'q'+q, qubit_path: 'qubits.q'+q, role: q === 1 ? 'control' : 'target', moves: true,
            frequency_hz: f, amp_text: '+120 mV', kind: 'estimate'});
        const map = {ok: true, qubits, segments: [seg(1,5e9), seg(2,5e9)], refused: [], planned: [],
            missing: [], operations: ['cz'], pairs: {'q1-2': {control: 'q1x', target: 'q2x',
                control_path: 'qubits.q1', target_path: 'qubits.q2', coupler_amp_text: '+100 mV'}}};
        if (mode === 'map_unknown') {
            Object.assign(map.pairs['q1-2'], {coupler_amp_text: 'unknown', coupler_amp_reason: 'not playable: flux missing on c12'});
            const U = await h.page({map, loss: h.NO_LOSS, width: 320});
            const seg = U.last().traces.filter(t => t.meta === 'seg').flatMap(t => t.text).filter(Boolean);
            assert(seg.length && seg.every(t => t.includes('coupler unknown (not playable: flux missing on c12)') && !t.includes('coupler \u2014')), 'hover names an unresolved coupler and why');
            U.close(); console.log('batch b map_unknown: passed'); return;
        }
        const S = await h.page({map, loss: h.NO_LOSS, width: 320});
        function boxes() {
            const r = S.last(), p = r.traces.find(t => t.meta === 'partners');
            const H = (r.layout.height - r.layout.margin.t - r.layout.margin.b) * (1 - r.layout.yaxis.domain[0]);
            const scale = H / (r.layout.yaxis.range[1] - r.layout.yaxis.range[0]);
            const col = (S.width - r.layout.margin.l - r.layout.margin.r) / 3;
            return {r,p,H, scale, col, values: p ? p.text.map((text,i) => {
                const width = Math.max(...text.split('<br>').map(t => S.w.CzMap.partnerWidth(t)));
                const x = p.x[i]*col, y = (p.y[i] - r.layout.yaxis.range[0])*scale;
                return Object.assign(S.w.CzMap.partnerBox(x, y, p.textposition[i], width, text.split('<br>').length), {y});
            }) : []};
        }
        // Tune a high row to put its top box across the header's padded lower edge.
        // A distant idle dot fixes the frequency range while the segment moves.
        S.root._czMap.qubits.forEach(q => { q.f01_hz = 4e9; });
        await S.w.CzMap.paint(S.root);
        let b = boxes();
        if (mode === 'map') assert(b.p && b.p.textposition.every(p => p.startsWith('bottom')), 'blocked top labels use a bottom spot');
        if (mode === 'map') assert(b.values.every(a => a.y0 >= 0 && a.y1 <= b.H), 'every partner box is within the plot');
        if (mode === 'map') {
            // the box follows Plotly's own text offset: r = marker radius / 0.8 + 1 (Drawing.textPointPosition)
            assert(b.p.mode.includes('markers') && b.p.marker.opacity === 0, 'the partner trace sets its own text offset');
            const r = b.p.marker.size / 2 / 0.8 + 1;
            assert.strictEqual(S.w.CzMap.partnerGap(), r, 'the placement gap is the offset Plotly applies');
            // a label under its own segment (3.2 px line) keeps a real margin, not a 1 px tie
            assert(b.values.every(a => a.y > a.y1 && a.y - 1.6 - a.y1 >= S.w.CzMap.PARTNER_CLEAR), 'a bottom label clears its own segment line');
        }
        const overlaps = (a,c) => a.x0 < c.x1+1 && c.x0 < a.x1+1 && a.y0 < c.y1+1 && c.y0 < a.y1+1;
        if (mode === 'map') assert(b.values.every(a => qubits.every((q,i) => !overlaps(a, {x0: (i+.5)*b.col-14, x1:(i+.5)*b.col+14, y0:b.H-3, y1:b.H+15}))), 'every partner box clears every header');
        if (mode !== 'map_refused') assert(b.r.traces.filter(t => t.meta === 'seg').some(t => t.text.some(t => t && t.includes('q1 Z amp +120 mV') && t.includes('coupler +100 mV'))), 'segment hover prints both amplitudes');
        // Another row just below blocks the bottom fallback as well.
        S.root._czMap.segments.push(seg(1, 5e9 - 12 / b.scale * 1e9), seg(2, 5e9 - 12 / b.scale * 1e9));
        await S.w.CzMap.paint(S.root);
        b = boxes();
        if (mode === 'map') assert(!b.p || !b.p.y.includes(5), 'when both spots are blocked the high row is hover only');
        const refusedMap = JSON.parse(JSON.stringify(map));
        refusedMap.refused = refusedMap.segments.map(s => Object.assign(s, {frequency_hz:null, reason:'no flux-frequency model'}));
        refusedMap.segments = [];
        S.state.map = refusedMap; await S.w.CzPlan.drawMap(S.root);
        const bars = S.last().traces.find(t => t.meta === 'bars');
        assert(bars && bars.text.some(t => t.includes('q1 Z amp +120 mV') && t.includes('coupler +100 mV') && t.includes('no flux-frequency model')), 'refused pair hover prints amplitudes and the refusal');
        assert(S.d.getElementById('czp-map-notes').textContent.includes('coupler +100 mV'), 'refused list prints amplitudes');
        S.close();
    } else throw new Error('unknown selfcheck mode');
    console.log('batch b ' + mode + ': passed');
})().catch(e => { console.error(e); process.exit(1); });