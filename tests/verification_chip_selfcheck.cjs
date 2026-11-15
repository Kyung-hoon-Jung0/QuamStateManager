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
assert.strictEqual(tile.querySelector('.map-tools-chip').title, note + ' ' + w.MapTools.VERIFY_HINT, 'the specific note and verify rule are on hover'); n++;
for (const emptyNote of [undefined, '', '   ']) {
    chips({ clickable: { verification_note: emptyNote, targets: [{ path: 'qubits.q0.f' }] } });
    assert.strictEqual(tile.querySelector('.map-tools-chip').title, w.MapTools.VERIFY_HINT, 'no note means only the general hint'); n++;
}
chips({ clickable: { verification_note: '  ' + note + ' ' + w.MapTools.VERIFY_HINT + '  ' } });
assert.strictEqual(tile.querySelector('.map-tools-chip').title, note + ' ' + w.MapTools.VERIFY_HINT, 'an existing hint is not duplicated'); n++;
const literalNote = '<b title="reason">& missing value</b>';
chips({ clickable: { verification_note: literalNote } });
assert.strictEqual(tile.querySelector('.map-tools-chip').title, literalNote + ' ' + w.MapTools.VERIFY_HINT, 'the note stays literal attribute text'); n++;
assert.strictEqual(tile.querySelector('.map-tools-chip').children.length, 0, 'the note creates no markup'); n++;
chips({ clickable: { n_verified: 1, verification_note: '  ', targets: [{ path: 'qubits.q0.f', evidence_tier: 'applied' }] } });
assert.strictEqual(tile.querySelector('.map-tools-chip').textContent, 'verified on this run', 'whitespace is no note for the verdict too'); n++;
chips({ clickable: { verification_note: note, targets: [{ path: 'qubits.q0.f' }] } });
const hints = () => Array.from(tile.querySelectorAll('[data-verify-hint]')).map(h => h.textContent);
assert.deepStrictEqual(hints(), [w.MapTools.VERIFY_HINT], 'a not-verified chip says, once and in words, what would verify it'); n++;
assert.ok(/node of this run saved a value for each parameter the click sets/.test(hints()[0]), 'the hint states the rule: ' + hints()[0]); n++;
chips({ clickable: { verification_note: note, targets: [{ path: 'qubits.q0.f' }] }});
assert.strictEqual(hints().length, 1, 'a remount leaves one hint, not two'); n++;
t = honest(chips({ clickable: { n_verified: 1, targets: [{ evidence_tier: 'applied' }] } }), 'verified recipe');
assert.deepStrictEqual(t, ['verified on this run'], 'a verified recipe says verified, once'); n++;
assert.strictEqual(hints().length, 0, 'a verified chip carries no what-would-verify hint'); n++;
t = honest(chips({ clickable: { n_verified: 1, verified_against: 'proposals', targets: [{evidence_tier: 'proposed'}] } }), 'proposal-checked recipe');
assert.deepStrictEqual(t, ['proposed, not applied'], 'a recipe checked against a proposal says so'); n++;
t = honest(chips({ status_chip: 'Reproduced with the current analysis code', clickable: { n_verified: 1, evidence_tier: 'proposed', targets: [{evidence_tier: 'proposed'}] } }), 'replot');
assert.deepStrictEqual(t, ['Reproduced with the current analysis code', 'proposed, not applied'], 'a replot keeps its reproduction status'); n++;
t = honest(chips({ status_chip: 'raw data', clickable: null, display_only_reason: 'this run wrote no state: click-to-apply off' }), 'display only');
assert.deepStrictEqual(t, ['raw data', 'not verified on this run'], 'a display-only figure is not verified, reason on hover'); n++;
assert.deepStrictEqual(hints(), ['This run wrote no state: click-to-apply off. No click on this map is verified on this run.'], 'no click contract: the payload own display-only reason, then a sentence true for this run only, never the verify rule'); n++;
// a raw map mounted with no contract and its own reason (the raw data viewer)
const rawReason = 'no node fit to compare; the map shows the stored data';
chips({ status_chip: 'raw data', display_only_reason: rawReason });
assert.deepStrictEqual(hints(), ['No node fit to compare; the map shows the stored data. No click on this map is verified on this run.'], 'raw map without a contract shows its reason'); n++;
assert.ok(!hints().includes(w.MapTools.VERIFY_HINT), 'raw map never promises a verification'); n++;
t = honest(chips({ status_chip: 'raw data' }), 'no contract, no reason');
assert.deepStrictEqual(hints(), ['No click contract is recorded for this kind of map, so nothing can verify a click on it: a click here sets no parameter whose saved value could be checked.'], 'missing contract is named without inventing a cause'); n++;
chips({ clickable: { n_verified: 0, targets: [{ path: 'qubits.q0.f', evidence_tier: 'applied' }] } });
assert.deepStrictEqual(hints(), [w.MapTools.VERIFY_HINT], 'a contract with zero writes states the verify rule'); n++;
chips({ clickable: { n_verified: 1, targets: [{ path: 'qubits.q0.f', evidence_tier: 'applied' }, { path: 'qubits.q0.g', evidence_tier: 'applied' }] } });
assert.deepStrictEqual(hints(), [w.MapTools.PARTIAL_HINT], 'writes that miss a parameter say only some were saved'); n++;
chips({ clickable: { n_verified: 1, targets: [{ path: 'qubits.q0.f', evidence_tier: 'applied' }] } });
assert.deepStrictEqual(hints(), [], 'a remount onto a verified map leaves no hint behind'); n++;
tile._mapTools.destroy(); delete tile._mapTools;
chips({ clickable: { verification_note: note } });
tile._mapTools.destroy(); delete tile._mapTools;
assert.deepStrictEqual(hints(), [], 'destroy removes the hint'); n++;

// the full grid: n_verified x number of targets x evidence tier -> chip text + hint. "Only some
// values were saved" appears only when 0 < n_verified < targets; anything else not verified gets
// the general verify rule, never a specific false sentence.
const NV = [['missing', undefined], ['null', null], ['"2"', '2'], ['-1', -1], ['0', 0], ['1', 1], ['2', 2], ['3', 3]];
const TIERS = ['none', 'proposed', 'applied', 'missing'];
const grid = [];
for (const [nvName, nv] of NV) for (const nt of [0, 1, 2]) for (const tier of TIERS) {
    const targets = [];
    for (let i = 0; i < nt; i++) {
        const tg = { path: 'qubits.q0.p' + i };
        if (tier === 'proposed' || tier === 'applied') tg.evidence_tier = tier;
        else if (tier === 'missing') tg.evidence_tier = null;
        targets.push(tg);
    }
    const clickable = { targets };
    if (nvName !== 'missing') clickable.n_verified = nv;
    const label = 'grid n_verified=' + nvName + ' targets=' + nt + ' tier=' + tier;
    const got = honest(chips({ clickable }), label);
    const valid = Number.isInteger(nv) && nv > 0 && nt > 0;
    let chip = 'not verified on this run', hint = [w.MapTools.VERIFY_HINT];
    if (valid && nv < nt) hint = [w.MapTools.PARTIAL_HINT];
    else if (valid && nv === nt && tier === 'applied') { chip = 'verified on this run'; hint = []; }
    else if (valid && nv === nt && tier === 'proposed') { chip = 'proposed, not applied'; hint = []; }
    assert.deepStrictEqual(got, [chip], label + ': chip ' + JSON.stringify(got)); n++;
    assert.deepStrictEqual(hints(), hint, label + ': hint ' + JSON.stringify(hints())); n++;
    grid.push(nvName + ' | ' + nt + ' | ' + tier + ' | ' + chip + ' | ' + (hint.length ? (hint[0] === w.MapTools.PARTIAL_HINT ? 'partial' : 'verify rule') : 'none'));
}
// the over-count boundary named by the review: two writes for one applied target
chips({ clickable: { n_verified: 2, targets: [{ path: 'qubits.q0.f', evidence_tier: 'applied' }] } });
assert.deepStrictEqual(hints(), [w.MapTools.VERIFY_HINT], 'more writes than targets states the verify rule, not only some'); n++;
if (process.env.VERIFY_GRID_OUT) fs.writeFileSync(process.env.VERIFY_GRID_OUT, grid.join('\n') + '\n');

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
