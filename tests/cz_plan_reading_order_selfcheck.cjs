'use strict';
// CZ gate plan reading order: the shipped cz-plan.js over a real /cz-plan page.
// Optional argv[2] = fixture JSON {html, data, solve, history}. Prints "(N assertions)".
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const f = JSON.parse(process.argv[2] ? fs.readFileSync(process.argv[2], 'utf8') :
    require('child_process').execFileSync(process.env.PYTHON || 'python',
        ['tests/test_cz_plan_reading_order.py'], {cwd: root, encoding: 'utf8', timeout: 60000,
            env: {...process.env, PYTHONUTF8: '1', PYTHONPATH: root, SM_DISABLE_ENV_WARMUP: '1'}}));
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let n = 0;
const failures = [];
const ok = (cond, msg) => { n++; if (!cond) failures.push(msg); };

(async () => {
    const dom = new JSDOM(f.html, {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window, d = w.document;
    w.HTMLElement.prototype.scrollIntoView = function () {};
    w.requirePlotly = () => Promise.resolve();
    w.PlotTheme = {houseLayout: l => l, houseConfig: c => c};
    w._plotlyRender = () => Promise.resolve();
    w.CzMap = {mount() {}, select() {}, highlight() {}, loss() { return null; }, scale() { return .25; }};
    w.fetch = url => Promise.resolve({json: () => Promise.resolve(structuredClone(
        url.startsWith('/cz-plan/solve') ? f.solve : url.startsWith('/cz-plan/history') ? f.history : f.data))});
    // the fetch mock serves one operation's answer, so the page opens on that operation
    d.getElementById('czp-root').setAttribute('data-selected', f.data.row.path);
    w.eval(source('cz-plan.js'));
    w.CzPlan.mount();
    await wait(60);

    // 1. the banner and the four tiles count the rows the table shows, by the row's own state
    const rows = [...d.querySelectorAll('.czp-row')];
    const tally = sel => sel.reduce((c, tr) => (c[tr.getAttribute('data-plan-state')]++, c),
        {'in-range': 0, outside: 0, unknown: 0, 'not-playable': 0});
    const all = tally(rows);
    ok(all['in-range'] > 0 && all['not-playable'] > 0 && all['in-range'] < rows.length, 'the fixture mixes states');
    const tile = st => d.querySelector(`[data-plan-tile="${st}"] [data-plan-value]`).textContent;
    const key = () => d.querySelector('[data-plan-key]').textContent;
    const check = (expect, shown) => {
        for (const st of Object.keys(expect)) ok(tile(st) === String(expect[st]), `tile ${st}: ${tile(st)} != ${expect[st]}`);
        ok(key() === `${expect['in-range']} of ${shown} operation${shown === 1 ? '' : 's'} in range`, 'banner key: ' + key());
        ok(d.querySelector('[data-plan-why]').textContent === `${expect.outside} outside range, ${expect.unknown} validity unknown, ${expect['not-playable']} not playable`,
            'banner reason');
    };
    check(all, rows.length);
    const verdict = d.querySelector('[data-plan-verdict]');
    ok(verdict.classList.contains('ui-verdict--danger') && verdict.getAttribute('role') === 'alert', 'a not-playable row makes the banner danger');
    ok(d.querySelector('[data-plan-tile="not-playable"]').classList.contains('ui-stat--danger'), 'not playable tile is danger');
    ok(all.outside === 1 && rows.some(tr => tr.getAttribute('data-plan-state') === 'outside' &&
        tr.querySelector('[data-validity-class="warn"]')), 'outside template classification');
    ok(d.querySelector('[data-plan-tile="outside"]').className === 'ui-stat ui-stat--warn' && tile('outside') === '1', 'outside tile text and warn tone');
    ok(d.querySelector('[data-plan-tile="unknown"]').className === 'ui-stat ui-stat--quiet' && tile('unknown') === String(all.unknown), 'unknown tile text and quiet tone');

    // the strip follows the table's filter
    const op = d.getElementById('czp-map-op');
    const name = 'cz';
    if (![...op.options].some(o => o.value === name)) op.appendChild(Object.assign(d.createElement('option'), {value: name, textContent: name}));
    op.value = name;
    d.getElementById('czp-root').dispatchEvent(new w.Event('czp:mapop'));
    await wait(20);
    const shown = rows.filter(tr => !tr.hidden);
    ok(shown.length > 0 && shown.length < rows.length, 'the filter hides some rows');
    check(tally(shown), shown.length);

    // A filter with no matching operations has one neutral sentence.
    op.appendChild(Object.assign(d.createElement('option'), {value: 'absent', textContent: 'absent'}));
    op.value = 'absent';
    d.getElementById('czp-root').dispatchEvent(new w.Event('czp:mapop'));
    await wait(20);
    ok(rows.every(tr => tr.hidden), 'the filter hides every row');
    ok(key() === 'No operation is shown.', 'empty filter sentence');
    ok(verdict.classList.contains('ui-verdict--neutral') && !verdict.classList.contains('ui-verdict--ok') &&
        verdict.getAttribute('role') === 'status', 'empty filter is neutral');
    ok(d.querySelector('[data-plan-why]').textContent === '', 'empty filter has no count sentence');
    for (const st of Object.keys(all)) ok(tile(st) === '0', 'empty filter tile ' + st);
    op.value = '';
    d.getElementById('czp-root').dispatchEvent(new w.Event('czp:mapop'));
    await wait(20);
    check(all, rows.length);

    // Filter actual rows to isolate each tone and the singular/plural sentences.
    const showRows = async (selected, tone, sentence) => {
        const names = rows.map(tr => tr.getAttribute('data-opname'));
        rows.forEach(tr => tr.setAttribute('data-opname', selected.includes(tr) ? 'selection' : 'other'));
        if (![...op.options].some(o => o.value === 'selection')) op.appendChild(Object.assign(d.createElement('option'), {value: 'selection'}));
        op.value = 'selection';
        d.getElementById('czp-root').dispatchEvent(new w.Event('czp:mapop'));
        await wait(20);
        check(tally(selected), selected.length);
        ok(key() === sentence, 'filtered sentence: ' + sentence);
        ok(verdict.className === `ui-verdict ui-verdict--${tone} ui-verdict--strong` &&
            verdict.getAttribute('role') === 'status', 'filtered banner tone: ' + tone);
        for (const st of Object.keys(all)) {
            const tones = {outside: 'warn', unknown: 'quiet', 'not-playable': 'danger'};
            const count = tally(selected)[st];
            ok(d.querySelector(`[data-plan-tile="${st}"]`).className === 'ui-stat' + (count && tones[st] ? ' ui-stat--' + tones[st] : '') &&
                tile(st) === String(count), 'filtered tile text and tone: ' + st);
        }
        rows.forEach((tr, i) => tr.setAttribute('data-opname', names[i]));
    };
    const inside = rows.filter(tr => tr.getAttribute('data-plan-state') === 'in-range');
    const outside = rows.filter(tr => tr.getAttribute('data-plan-state') === 'outside');
    const unknown = rows.filter(tr => tr.getAttribute('data-plan-state') === 'unknown');
    await showRows(inside.slice(0, 1), 'ok', '1 of 1 operation in range');
    await showRows([inside[0], outside[0]], 'warn', '1 of 2 operations in range');
    await showRows(outside, 'warn', '0 of 1 operation in range');
    await showRows(unknown, 'neutral', `0 of ${unknown.length} operation${unknown.length === 1 ? '' : 's'} in range`);
    await showRows([outside[0], unknown[0]], 'warn', '0 of 2 operations in range');
    op.value = '';
    d.getElementById('czp-root').dispatchEvent(new w.Event('czp:mapop'));
    await wait(20);

    // planCounts: precedence-free per-row state, hidden rows ignored, unknown words count as unknown
    const mk = (st, hidden) => { const tr = d.createElement('tr'); if (st) tr.setAttribute('data-plan-state', st); tr.hidden = !!hidden; return tr; };
    const c = w.CzPlan._planCounts([mk('in-range'), mk('outside'), mk('outside'), mk('unknown'), mk('not-playable'), mk('in-range', true), mk('odd'), mk(null)]);
    ok(c.shown === 7 && c['in-range'] === 1 && c.outside === 2 && c.unknown === 3 && c['not-playable'] === 1, 'planCounts ' + JSON.stringify(c));

    // 2. the table's state cells are pills
    ok(d.querySelector('td.czp-validity .ui-pill[data-validity-class]'), 'validity is a pill');
    ok(d.querySelector('.ui-pill--danger[data-not-playable="1"]').textContent === 'not playable', 'not playable is a danger pill');
    ok(d.querySelector('.czp-tier.ui-pill'), 'the estimate tier is a pill');

    // 7. the per-pair sentence carries three amplitude tiles from the same answer
    const row = d.querySelector('.czp-row[data-plan-state="in-range"]');
    row.click();
    await wait(60);
    const stats = d.getElementById('czp-match-stats');
    const vals = [...stats.querySelectorAll('.ui-stat')].map(t => [t.querySelector('.ui-stat-label').textContent, t.querySelector('.ui-stat-value').firstChild.textContent]);
    const mv = (v, s) => (s && v > 0 ? '+' : '') + (v * 1e3).toFixed(1);
    ok(!stats.hidden && vals.length === 3, 'three amplitude tiles: ' + JSON.stringify(vals));
    ok(vals.length === 3 && vals[0][1] === mv(f.solve.amplitude_v) && vals[1][1] === mv(f.solve.stored_amplitude_v) && vals[2][1] === mv(f.solve.difference_v, true),
        'tile values come from the solve answer');
    ok(d.getElementById('czp-match').textContent.length > 0, 'the sentence stays');

    // 8. the cards are kit panels; the operation card leads with its tiles
    const cards = [...d.querySelectorAll('#czp-card .czp-card-q')];
    ok(cards.length >= 2 && cards.every(c => c.classList.contains('ui-panel')), 'every card is a panel');
    const opStats = d.querySelector('[data-card="operation"] [data-op-stats]');
    ok(opStats && opStats.querySelector('.ui-stat-label').textContent.startsWith('\u0394'), 'the operation card leads with the detuning tile');
    ok(opStats && [...opStats.querySelectorAll('.ui-stat-value')].every(v => v.firstChild.textContent !== '' && v.firstChild.textContent !== '0.0' || f.data.row.detuning_hz === 0),
        'no tile is a fabricated blank or zero');

    const opTiles = () => [...d.querySelectorAll('[data-card="operation"] [data-op-stats] .ui-stat')];
    const duration = opTiles().find(t => t.querySelector('.ui-stat-label').textContent === 'Duration');
    ok(duration && duration.querySelector('.ui-stat-value').firstChild.textContent === String(f.data.row.duration_ns) &&
        duration.querySelector('.ui-stat-unit')?.textContent === 'ns', 'Duration value and ns unit');
    const nearest = opTiles().find(t => t.querySelector('.ui-stat-label').textContent === 'From nearest resonance');
    ok(nearest && nearest.querySelector('.ui-stat-note')?.textContent === f.data.row.nearest.name &&
        nearest.querySelector('.ui-stat-unit')?.textContent === 'MHz', 'nearest resonance note and MHz unit');
    ok([...stats.querySelectorAll('.ui-stat-unit')].every(t => t.textContent === 'mV'), 'amplitude units are mV');

    const redraw = async () => {
        d.getElementById('czp-solve')._op = null;
        row.click();
        await wait(30);
    };
    const original = structuredClone({data: f.data, solve: f.solve});
    const invalid = [null, undefined, NaN, Infinity, -Infinity, '', [], [5000000], {}, 'abc', '5000000', true];
    for (const [i, value] of invalid.entries()) {
        f.data.row.a4 = null;
        f.data.row.duration_ns = value;
        f.data.row.detuning_hz = value;
        f.data.row.nearest.distance_hz = value;
        f.solve.amplitude_v = value;
        f.solve.stored_amplitude_v = value;
        f.solve.difference_v = value;
        await redraw();
        const labels = opTiles().map(t => t.querySelector('.ui-stat-label').textContent);
        ok(!labels.includes('Duration'), `invalid ${i}: Duration absent`);
        ok(!labels.some(t => t.startsWith('\u0394')), `invalid ${i}: detuning absent`);
        ok(!labels.includes('From nearest resonance'), `invalid ${i}: nearest absent`);
        for (const label of ['Required amplitude', 'Stored amplitude', 'Stored minus required']) {
            ok(![...stats.querySelectorAll('.ui-stat-label')].some(t => t.textContent === label), `invalid ${i}: ${label} absent`);
        }
        ok(stats.hidden, `invalid ${i}: empty amplitude grid hidden`);
    }
    // Zero is a real number and must retain its value and unit.
    for (const value of [0, -2500000, 1250000]) {
        f.data = structuredClone(original.data);
        f.solve = structuredClone(original.solve);
        f.data.row.a4 = null;
        f.data.row.detuning_hz = value;
        f.data.row.duration_ns = value;
        f.solve.stored_amplitude_v = value;
        f.solve.difference_v = value * 1e-9;
        if (value === 0) f.data.row.nearest.distance_hz = 0;
        await redraw();
        const tiles = opTiles();
        const delta = tiles.find(t => t.querySelector('.ui-stat-label').textContent.startsWith('\u0394'));
        const time = tiles.find(t => t.querySelector('.ui-stat-label').textContent === 'Duration');
        const stored = [...stats.querySelectorAll('.ui-stat')].find(t => t.querySelector('.ui-stat-label').textContent === 'Stored amplitude');
        ok(delta && delta.querySelector('.ui-stat-value').firstChild.textContent === (value > 0 ? '+' : '') + (value / 1e6).toFixed(1) &&
            delta.querySelector('.ui-stat-unit')?.textContent === 'MHz', `finite ${value}: detuning value and unit`);
        ok(time && time.querySelector('.ui-stat-value').firstChild.textContent === String(value) &&
            time.querySelector('.ui-stat-unit')?.textContent === 'ns', `finite ${value}: Duration value and unit`);
        ok(stored && stored.querySelector('.ui-stat-value').firstChild.textContent === mv(value) &&
            stored.querySelector('.ui-stat-unit')?.textContent === 'mV', `finite ${value}: stored value and unit`);
        const diff = [...stats.querySelectorAll('.ui-stat')].find(t => t.querySelector('.ui-stat-label').textContent === 'Stored minus required');
        ok(diff && diff.querySelector('.ui-stat-value').firstChild.textContent === (value > 0 ? '+' : '') + (value * 1e-6).toFixed(1),
            `finite ${value}: the difference carries a plus sign only above zero`);
        if (value === 0) {
            const near = tiles.find(t => t.querySelector('.ui-stat-label').textContent === 'From nearest resonance');
            ok(near && near.querySelector('.ui-stat-value').firstChild.textContent === '0.0', 'a nearest distance of exactly zero shows 0.0 with no sign');
        }
    }

    // 6. map and detail sit in kit zones
    ok(d.querySelector('[data-czp-zone="map"].ui-zone .czp-map-box'), 'the map is in a zone');
    ok(d.querySelector('[data-czp-zone="detail"].ui-zone .czp-detail'), 'the detail is in a zone');
    d.getElementById('czp-root').remove();
    await wait(0);
    dom.window.close();
    assert(failures.length === 0, failures.join('\n'));
    console.log(`(${n} assertions)`);
})().catch(e => { console.error(e); process.exit(1); });
