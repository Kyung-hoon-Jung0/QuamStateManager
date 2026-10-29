'use strict';
/* A4-UX-1..4 on the shipped cz-plan.js under jsdom, fed by the server's own page and data.
 *   node cz_plan_a4_ux_selfcheck.cjs <fixture.json> <item>
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const f = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const item = Number(process.argv[3]);
const root = path.join(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    const dom = new JSDOM(f.html, {url: 'http://localhost/cz-plan?pair=q1-2', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window, d = w.document, renders = [];
    w.HTMLElement.prototype.scrollIntoView = function () {};
    w.requirePlotly = () => Promise.resolve();
    w.PlotTheme = {houseLayout: l => l, houseConfig: c => c};
    w._plotlyRender = (el, traces, layout) => { renders.push({id: el.id, traces, layout}); return Promise.resolve(); };
    w.CzMap = {mount() {}, select() {}, highlight() {}, loss() { return null; }, scale() { return .25; }};
    w.fetch = url => Promise.resolve({json: () => Promise.resolve(structuredClone(
        url.startsWith('/cz-plan/history') ? f.history : f.data))});
    w.eval(source('cz-plan.js')); w.CzPlan.mount(); await wait(60);
    const a4 = f.data.row.a4, match = d.getElementById('czp-match');
    assert(a4 && a4.t_flat_ns != null && a4.stored_phase_pi != null, 'fixture must give a usable ZZ');
    if (item === 1) {
        // the duration mismatch and the phase it gives, in warn colour, from the server's numbers
        const diff = match.querySelector('.czp-a4-diff');
        assert(diff, 'no difference span');
        assert(diff.classList.contains('czp-warn-text'), 'mismatch not in warn colour');
        assert(diff.textContent.includes('difference ' + (a4.difference_ns > 0 ? '+' : '') + a4.difference_ns + ' ns'), diff.textContent);
        assert(diff.textContent.includes('conditional phase ' + a4.stored_phase_pi.toFixed(2) + ' π'), diff.textContent);
        assert.strictEqual(match.getAttribute('data-a4-stored-phase-pi'), String(a4.stored_phase_pi));
        assert.strictEqual(match.getAttribute('data-a4-difference-ns'), String(a4.difference_ns));
    } else if (item === 2) {
        // one duration source: the card shows the same stored lengths as the top line
        const dur = d.querySelector('#czp-card .czp-a4-duration');
        assert(dur, 'no A4 duration cell');
        assert(dur.textContent.includes('flat ' + a4.stored_flat_length_ns + ' ns'), dur.textContent);
        assert(dur.textContent.includes('pulse ' + a4.stored_length_ns + ' ns'), dur.textContent);
        assert(!/unknown/.test(dur.textContent), dur.textContent);
        assert(match.textContent.includes('stored flat ' + a4.stored_flat_length_ns + ' ns'));
    } else if (item === 3) {
        // fixed qubits: "fixed frequency" + idle f_01 only, no flux rows
        const cards = [...d.querySelectorAll('#czp-card .czp-card-fixed')];
        assert.strictEqual(cards.length, 2);
        cards.forEach(c => {
            assert(c.textContent.includes('fixed frequency'));
            assert(c.textContent.includes('Idle f_01'));
            ['Z amp', 'At the operation', 'Validity', 'Direction', 'Scan loss'].forEach(t =>
                assert(!c.textContent.includes(t), t + ' still on a fixed-qubit card'));
        });
    } else if (item === 4) {
        // the idle (residual) ZZ is a reference line on the ZZ plot, at the server's value
        const time = renders.filter(x => x.id === 'czp-time').at(-1);
        assert(time, 'no ZZ plot');
        const iz = a4.idle_zz_hz / 1e6;
        const line = (time.layout.shapes || []).find(s => s.type === 'line' && s.y0 === iz && s.y1 === iz);
        assert(line, 'no idle ZZ line');
        assert((time.layout.annotations || []).some(a => a.y === iz && /idle ZZ/.test(a.text)), 'no idle ZZ label');
        assert.strictEqual(d.getElementById('czp-time').getAttribute('data-idle-zz-hz'), String(a4.idle_zz_hz));
    }
    console.log('a4 ux ' + item + ' ok');
})().catch(e => { console.error(e); process.exit(1); });
