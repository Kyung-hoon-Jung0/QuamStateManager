/* Exercise the shipped Pulses refusal bridge and the existing batch action. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<body><div id="inspector-pane"><form class="pulse-edit-form"><input name="dot_path" value="qubits.q1.z.operations.cz.flat_length"></form></div></body>',
    { url: 'http://localhost/pulses', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const follow = [{ dot_path: 'qubit_pairs.q1-q2.macros.cz.flux_pulse_qubit.flat_length', value: 40 },
    { dot_path: 'qubit_pairs.q1-q2.macros.cz.flux_pulse_target.flat_length', value: 40 }];
const calls = [], reverted = [];
w.fetch = async (url, opts) => {
    calls.push({ url, body: opts && JSON.parse(opts.body) });
    if (url === '/field/lab-watch') return { json: async () => ({ lab: false }) };
    return { ok: true, status: 200, clone() { return this; }, json: async () => ({
        results: follow.map(u => ({ ...u, applied: true, display: '40' })) }) };
};
w.document.addEventListener('cellsReverted', e => reverted.push(e.detail));
w.eval(fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/lab-check.js'), 'utf8'));
(async () => {
    const form = w.document.querySelector('form');
    w.document.dispatchEvent(new w.CustomEvent('htmx:afterRequest', { detail: { elt: form, xhr: {
        status: 400, getResponseHeader: name => name === 'X-Lab-Refusal' ? JSON.stringify({
            lab_refused: true, error: 'Your gate refused this value: lengths differ', lab_follow: follow }) : null
    } } }));
    await new Promise(r => setTimeout(r, 10));
    const button = w.document.querySelector('.lab-check-follow');
    assert.ok(button, 'a Pulses refusal offers the existing Set too button');
    assert.match(button.textContent, /Set .* too .*one batch/);
    button.click();
    await new Promise(r => setTimeout(r, 10));
    const batches = calls.filter(c => c.url === '/field/edit-batch');
    assert.equal(batches.length, 1, 'one press sends one batch');
    assert.deepEqual(batches[0].body, { updates: follow, group: 'new' });
    assert.equal(reverted.length, 1);
    assert.equal(reverted[0].entries.length, 2, 'both fields repaint through the undo event');
    assert.equal(w.document.querySelector('.lab-check-follow'), null);
    console.log('7 assertions passed');
    w.close();
})().catch(err => { console.error(err); w.close(); process.exitCode = 1; });
