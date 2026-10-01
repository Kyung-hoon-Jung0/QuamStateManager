/* docs/238 -- the Live-Edit dBm sub-line follows BOTH numbers it reads.
 * Against the real window.PhysAmp from app.js. Pinned:
 *  - typing into the port's FSP box recomputes every amplitude on that port
 *    (and only that port), from each amplitude box's own text;
 *  - an FSP box restored without an input event (Escape, a revert) is caught
 *    when focus leaves it;
 *  - a working-copy move (sm:wc-moved) asks POST /bulk/phys for the paths of
 *    the amplitude cells on screen and repaints from the FSP it names, even
 *    when no FSP box is on the page;
 *  - a cell that had no line (amp 0 at render) gets one; a chain that no
 *    longer resolves loses it; a stale answer never overwrites a newer one.
 * Run: node tests/phys_live_selfcheck.cjs   (driven by tests/test_phys_live.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

const FSP_A = 'ports.mw_outputs.con1.1.2.full_scale_power_dbm';
const FSP_B = 'ports.mw_outputs.con1.1.3.full_scale_power_dbm';

function amp(dp, v, fsp, fspPath, line) {
    return '<td><input class="bulk-cell" value="' + v + '" data-orig="' + v + '" data-dot-path="' + dp
        + '" data-resolved="' + dp + '" data-phys-kind="mw" data-phys-fsp="' + fsp + '" data-phys-fsp-path="' + fspPath
        + '" title="' + dp + ' — actual output shown below (P = FSP ' + fsp + ' dBm + 20·log10|amp|; V is rms @ 50 Ω)">'
        + (line === false ? '' : ' <span class="bulk-phys" aria-hidden="true" data-dbm="0">x</span>') + '</td>';
}
function world(withFspBox) {
    const rows = '<tr>'
        + amp('qubits.qA1.xy.operations.x180.amplitude', '0.1', '0', FSP_A)
        + amp('qubits.qA1.xy.operations.x90.amplitude', '0.05', '0', FSP_A)
        + amp('qubits.qA1.resonator.operations.readout.amplitude', '0.1', '0', FSP_B)
        + (withFspBox ? '<td><input class="bulk-cell" value="0" data-orig="0" data-dot-path="qubits.qA1.xy.opx_output.full_scale_power_dbm" data-resolved="' + FSP_A + '"></td>' : '')
        + '<td><input class="bulk-cell" value="0" data-orig="0" data-dot-path="qubits.qA2.xy.operations.x180.amplitude" data-resolved="qubits.qA2.xy.operations.x180.amplitude"></td>'
        + '</tr>';
    const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray" data-edit-seq="1"></div><table><tbody>' + rows + '</tbody></table></body></html>',
        { url: 'http://localhost/bulk', pretendToBeVisual: true, runScripts: 'outside-only' });
    const w = dom.window;
    Object.defineProperty(w, 'localStorage', { configurable: true, value: {
        getItem: () => null, setItem: () => {}, removeItem: () => {} } });
    const posts = [];
    const replies = [];
    w.fetch = function (u, o) {
        posts.push({ u: u, body: JSON.parse(o.body) });
        return new Promise(function (res) { replies.push(res); });
    };
    w.__bulkChipKey = 'chipX';
    const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
    const i = src.indexOf('window.PhysAmp = (function () {');
    const end = /\r?\n\}\)\(\);\r?\n?/.exec(src.slice(i));
    w.eval(src.slice(i, i + end.index + end[0].length));
    const d = w.document;
    const cell = (dp) => d.querySelector('input.bulk-cell[data-dot-path="' + dp + '"]');
    const line = (dp) => { const s = cell(dp).closest('td').querySelector('.bulk-phys'); return s ? s.textContent : null; };
    const answer = (k, phys) => replies[k]({ ok: true, json: () => Promise.resolve({ ok: true, phys: phys }) });
    return { w, d, cell, line, posts, answer };
}
function type(W, el, v) { el.value = v; el.dispatchEvent(new W.w.Event('input', { bubbles: true })); }

(async () => {
    // 1. typing the FSP moves its port's lines, from each box's own text
    let W = world(true);
    const fspBox = W.d.querySelector('input[data-resolved="' + FSP_A + '"]');
    type(W, fspBox, '-10');
    ok(W.line('qubits.qA1.xy.operations.x180.amplitude') === '-30.0 dBm', 'FSP typed -10: x180 (0.1) reads -30.0 dBm (' + W.line('qubits.qA1.xy.operations.x180.amplitude') + ')');
    ok(W.line('qubits.qA1.xy.operations.x90.amplitude') === '-36.0 dBm', 'and x90 (0.05) on the same port reads -36.0 dBm (' + W.line('qubits.qA1.xy.operations.x90.amplitude') + ')');
    ok(W.line('qubits.qA1.resonator.operations.readout.amplitude') === 'x', 'a different port is left alone');
    // the amplitude typed AFTER the FSP still reads the typed FSP
    type(W, W.cell('qubits.qA1.xy.operations.x180.amplitude'), '0.01');
    ok(W.line('qubits.qA1.xy.operations.x180.amplitude') === '-50.0 dBm', 'an amp typed after it reads the typed FSP (' + W.line('qubits.qA1.xy.operations.x180.amplitude') + ')');
    // 2. Escape-like restore without an input event, caught on focusout
    fspBox.value = '0';
    fspBox.dispatchEvent(new W.w.FocusEvent('focusout', { bubbles: true }));
    await tick(5);
    ok(W.line('qubits.qA1.xy.operations.x90.amplitude') === '-26.0 dBm', 'an FSP restored silently is followed on focusout (' + W.line('qubits.qA1.xy.operations.x90.amplitude') + ')');

    // 3. a working-copy move asks the server, no FSP box on the page
    W = world(false);
    W.d.dispatchEvent(new W.w.CustomEvent('sm:wc-moved'));
    await tick(150);
    ok(W.posts.length === 1 && W.posts[0].u === '/bulk/phys', 'sm:wc-moved posts /bulk/phys');
    const sent = W.posts[0] && W.posts[0].body.paths;
    ok(sent && sent.length === 4 && sent.indexOf('qubits.qA2.xy.operations.x180.amplitude') >= 0, 'it names every amplitude cell on screen, the blank one too (' + JSON.stringify(sent) + ')');
    ok(W.posts[0] && W.posts[0].body.chip === 'chipX', 'it names its chip');
    W.answer(0, {
        'qubits.qA1.xy.operations.x180.amplitude': { kind: 'mw', fsp: 3, dbm: -17, text: '-17.0 dBm', fsp_path: FSP_A },
        'qubits.qA1.xy.operations.x90.amplitude': { kind: 'mw', fsp: 3, dbm: -23, text: '-23.0 dBm', fsp_path: FSP_A },
        'qubits.qA1.resonator.operations.readout.amplitude': null,
        'qubits.qA2.xy.operations.x180.amplitude': { kind: 'mw', fsp: -2, dbm: -1, text: 'x', fsp_path: FSP_B },
    });
    await tick(20);
    ok(W.line('qubits.qA1.xy.operations.x180.amplitude') === '-17.0 dBm', 'the server FSP (3) repaints x180 (' + W.line('qubits.qA1.xy.operations.x180.amplitude') + ')');
    ok(W.cell('qubits.qA1.xy.operations.x180.amplitude').getAttribute('data-phys-fsp') === '3', 'and becomes the cell\'s FSP');
    ok(/P = FSP 3 dBm/.test(W.cell('qubits.qA1.xy.operations.x180.amplitude').title), 'the tooltip names the new FSP');
    ok(W.line('qubits.qA1.resonator.operations.readout.amplitude') === null, 'a chain that no longer resolves loses its line');
    ok(!W.cell('qubits.qA1.resonator.operations.readout.amplitude').hasAttribute('data-phys-kind'), 'and its kind');
    // the amp-0 cell has a line only once it is non-zero: value 0 -> blank
    ok(W.line('qubits.qA2.xy.operations.x180.amplitude') === '', 'a zero amplitude gets a blank line, never -inf (' + W.line('qubits.qA2.xy.operations.x180.amplitude') + ')');
    type(W, W.cell('qubits.qA2.xy.operations.x180.amplitude'), '0.1');
    ok(W.line('qubits.qA2.xy.operations.x180.amplitude') === '-22.0 dBm', 'and typing into it now shows the power (' + W.line('qubits.qA2.xy.operations.x180.amplitude') + ')');

    // 4. a stale answer never overwrites a newer one
    W = world(false);
    W.d.dispatchEvent(new W.w.CustomEvent('sm:wc-moved'));
    await tick(150);
    W.d.dispatchEvent(new W.w.CustomEvent('sm:wc-moved'));
    await tick(150);
    ok(W.posts.length === 2, 'two moves, two asks');
    W.answer(1, { 'qubits.qA1.xy.operations.x180.amplitude': { kind: 'mw', fsp: 5, dbm: -15, text: '', fsp_path: FSP_A } });
    await tick(20);
    W.answer(0, { 'qubits.qA1.xy.operations.x180.amplitude': { kind: 'mw', fsp: -40, dbm: -60, text: '', fsp_path: FSP_A } });
    await tick(20);
    ok(W.line('qubits.qA1.xy.operations.x180.amplitude') === '-15.0 dBm', 'the newer answer stands (' + W.line('qubits.qA1.xy.operations.x180.amplitude') + ')');

    console.log(fails ? fails + ' FAILED' : 'all passed');
    process.exit(fails ? 1 : 0);
})();
