/* v1.5.1 (D3 class C): one honest verification status per map tile.
 *
 * A recipe tile has no reproduction status, so the status chip defaulted to "not verified on
 * this run" -- next to a contract chip saying "verified on this run" for a recipe the node
 * never checked (it wrote no state). One chip now carries the verification; a status chip shows
 * only a real reproduction status.
 * Run: node tests/verification_chip_selfcheck.cjs [gated-payloads.json]
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', 'quam_state_manager');
const tpl = fs.readFileSync(path.join(root, 'web', 'templates', '_map_tools.html'), 'utf8');
const dom = new JSDOM('<!doctype html><body>' + tpl + '<div id="tile"><div class="ds-interactive-fig-title">Map</div><div id="p"></div></div></body>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
Object.assign(w, { console, AbortController, structuredClone });
w.fetch = () => new Promise(() => {});
w.Plotly = { react: () => Promise.resolve(), purge: () => {} };
w.PlotTheme = { houseLayout: l => l };
w.eval(fs.readFileSync(path.join(root, 'web', 'static', 'map-tools.js'), 'utf8'));
const tile = w.document.getElementById('tile');
const map = { type: 'heatmap', x: [0, 1], y: [0, 1], z: [[0, 1], [1, 0]] };
let n = 0;
function chips(payload) {
    if (tile._mapTools) tile._mapTools.destroy();
    w.MapTools.mount(tile, w.document.getElementById('p'), Object.assign({ data: [map], layout: {} }, payload), { uid: 'run:1', fig: 'f' });
    return Array.from(tile.querySelectorAll('.map-tools-chip')).map(c => ({ text: c.textContent, title: c.title }));
}
function honest(list, label) {
    const texts = list.map(c => c.text);
    const verdicts = texts.filter(t => /verified on this run|proposed, not applied/.test(t));
    assert.strictEqual(verdicts.length, 1, label + ': exactly one verification chip, got ' + JSON.stringify(texts)); n++;
    assert.ok(!(texts.includes('verified on this run') && texts.includes('not verified on this run')),
        label + ': never verified and not verified together'); n++;
    return texts;
}
const note = 'recipe formula, not verified on this run (the node wrote no state)';
let t = honest(chips({ clickable: { verification_note: note, targets: [{ path: 'qubits.q0.f' }] }}), 'unchecked recipe');
assert.deepStrictEqual(t, ['not verified on this run'], 'an unchecked recipe says not verified, once'); n++;
assert.strictEqual(tile.querySelector('.map-tools-chip').title, note, 'the full reason is on hover'); n++;
t = honest(chips({ clickable: { n_verified: 1, targets: [{ evidence_tier: 'applied' }] } }), 'verified recipe');
assert.deepStrictEqual(t, ['verified on this run'], 'a verified recipe says verified, once'); n++;
t = honest(chips({ clickable: { n_verified: 1, verified_against: 'proposals', targets: [{evidence_tier: 'proposed'}] } }), 'proposal-checked recipe');
assert.deepStrictEqual(t, ['proposed, not applied'], 'a recipe checked against a proposal says so'); n++;
t = honest(chips({ status_chip: 'Reproduced with the current analysis code', clickable: { n_verified: 1, evidence_tier: 'proposed', targets: [{evidence_tier: 'proposed'}] } }), 'replot');
assert.deepStrictEqual(t, ['Reproduced with the current analysis code', 'proposed, not applied'], 'a replot keeps its reproduction status'); n++;
t = honest(chips({ status_chip: 'raw data', clickable: null, display_only_reason: 'this run wrote no state: click-to-apply off' }), 'display only');
assert.deepStrictEqual(t, ['raw data', 'not verified on this run'], 'a display-only figure is not verified, reason on hover'); n++;

// real gated recipe payloads from the server (the Python driver passes them in)
if (process.argv[2]) {
    const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const expected = { no_evidence: 'not verified on this run', applied: 'verified on this run', proposed: 'proposed, not applied' };
    for (const [name, payload] of Object.entries(cases)) {
        t = honest(chips(payload), 'server ' + name);
        assert.deepStrictEqual(t, [expected[name]], 'server ' + name + ' payload: ' + JSON.stringify(t)); n++;
    }
}
console.log(`verification_chip_selfcheck: ${n} assertions passed`);
