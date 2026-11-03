/* docs/235 -- Json tree multi-edit (window.TreeMulti), against the real app.js.
 * Pinned:
 *  1. the entity segment is found from the DATA (outermost level with >= 2
 *     siblings carrying the rest of the path as a leaf) and patterns expand in
 *     natural order (qA2 before qA10);
 *  2. Ctrl+Alt+D on a leaf row selects it, each further Ctrl+Alt+D adds the next
 *     entity's same field, Ctrl+Shift+L selects all; Ctrl+D OUTSIDE a tree is
 *     left to the browser (not prevented);
 *  3. arithmetic (*1.1 /2 +5e6 -1e6 +10%) previews per row; pointers,
 *     read-only fields, the FSP field and (for arithmetic) non-numbers are left
 *     out with a reason;
 *  4. Apply sends ONE /field/edit-batch (group "new" = one Ctrl+Z) holding only
 *     the rows that change, and paints the committed values into the model.
 * Run: node tests/tree_multi_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

const dom = new JSDOM('<!doctype html><html><body><input id="outside"><div class="tree-toolbar"><input id="explorer-search" class="tree-search"></div><div id="t"></div>'
    + '<div id="pending-tray" data-change-count="0"></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
const posts = [];
let reply = null;
window.eval("fetch = window.fetch = function(u, o){ return window.__fetch(u, o); };");
window.__fetch = function (u, o) {
    if (u === '/field/edit-batch') {
        posts.push(JSON.parse(o.body));
        return Promise.resolve({ json: () => Promise.resolve(reply) });
    }
    return new Promise(function () {});
};
global.fetch = window.fetch;
window._treeReadOnly = { membership_tops: ['network'], membership_reason: 'identity', skip_leaves: ['id'], skip_reason: 'read-only' };
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const TM = window.TreeMulti;
const d = window.document;

const data = {
    qubits: {},
    network: { host: '1.2.3.4' },
};
['qA1', 'qA2', 'qA10', 'qB1'].forEach(function (q, i) {
    data.qubits[q] = {
        id: q,
        xy: { operations: { x180: { amplitude: 0.1 * (i + 1), length: 40 } } },
        resonator: { operations: { readout: '#./readout_square', readout_square: { amplitude: 0.02 } } },
        z: { full_scale_power_dbm: 1 },
        note: 'text',
    };
});
data.qubits.qB1.xy.operations.x180.amplitude = '#/qubits/qA1/xy/operations/x180/amplitude';
window.renderJsonTree('t', data, { defaultDepth: 9, crud: true });
const tree = d.getElementById('t');
// jsdom has no layout: say the tree is on screen, as a browser would
Object.defineProperty(tree, 'offsetParent', { get: () => d.body });

(async () => {
    // 1. pattern logic
    ok(TM.generalize(data, 'qubits.qA2.xy.operations.x180.amplitude') === 'qubits.*.xy.operations.x180.amplitude',
       'the entity segment is found from the data');
    ok(TM.generalize(data, 'network.host') === 'network.host', 'a field with no counterpart stays a single path');
    const ex = TM.expand(data, 'qubits.*.xy.operations.x180.amplitude');
    ok(ex.map((p) => p.split('.')[1]).join() === 'qA1,qA2,qA10,qB1', 'expand is in natural order (qA2 before qA10)');
    ok(TM.expand(data, 'qubits.*.xy.operations').length === 0, 'a container is never a match (leaves only)');
    const near = (a, b) => Math.abs(a - b) < 1e-12 * Math.max(1, Math.abs(b));
    ok(near(TM.arithOf('*1.1')(2), 2.2) && near(TM.arithOf('+10%')(50), 55) && TM.arithOf('-1e6')(3e6) === 2e6
       && TM.arithOf('/2')(9) === 4.5 && TM.arithOf('5') === null && TM.arithOf('*10%') === null,
       'arithmetic grammar: * / + - and +/-%, a bare number is absolute');
    ok(TM.numText(0.1 * 3, false) === '0.3' && TM.numText(44.00000000000001, true) === '44',
       'computed numbers lose float noise; an integer field stays an integer');

    // 2. keys
    const leaf = (p) => d.querySelector('.tree-node[data-path="' + p + '"]');
    const kd = (target, key, mods) => {
        const e = new window.KeyboardEvent('keydown', Object.assign({ key: key, bubbles: true, cancelable: true }, mods || {}));
        target.dispatchEvent(e);
        return e;
    };
    d.getElementById('outside').focus();
    let e = kd(d.getElementById('outside'), 'd', { ctrlKey: true });
    ok(!e.defaultPrevented && !TM.state(), 'Ctrl+D outside a tree is left to the browser');
    e = kd(d.getElementById('outside'), 'h', { ctrlKey: true });
    ok(!e.defaultPrevented && !TM.state() && !(d.getElementById('tree-multi') && !d.getElementById('tree-multi').hidden),
       'Ctrl+H outside a tree is left to the browser too (no panel)');
    // the tree builds rows lazily: open the path the way a user does
    const segs = 'qubits.qA1.xy.operations.x180.amplitude'.split('.');
    for (let i = 1; i < segs.length; i++) {
        const nd = leaf(segs.slice(0, i).join('.'));
        const tg = nd && nd.querySelector(':scope > .tree-row .tree-toggle.collapsed');
        if (tg) tg.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    }
    const row = leaf('qubits.qA1.xy.operations.x180.amplitude');
    ok(!!row, 'the leaf row is built once its branch is opened');
    row.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    (row.querySelector('.tree-row') || row).setAttribute('tabindex', '0');
    (row.querySelector('.tree-row') || row).focus();
    e = kd(d.activeElement, 'd', { ctrlKey: true, altKey: true });
    ok(e.defaultPrevented && TM.state() && TM.state().sel.length === 1, 'Ctrl+Alt+D on a leaf selects it');
    kd(d.activeElement, 'd', { ctrlKey: true, altKey: true });
    ok(TM.state().sel.map((p) => p.split('.')[1]).join() === 'qA1,qA2', 'the next Ctrl+Alt+D adds the next entity');
    kd(d.activeElement, 'L', { ctrlKey: true, shiftKey: true });
    ok(TM.state().sel.length === 4, 'Ctrl+Shift+L selects every entity');

    // 3. preview + exclusions
    const vin = d.querySelector('#tree-multi .tree-multi-value');
    vin.value = '*2';
    vin.dispatchEvent(new window.Event('input', { bubbles: true }));
    const why = [...d.querySelectorAll('#tree-multi .tree-multi-row')].map((r) => (r.querySelector('.tree-multi-why') || {}).textContent || '');
    ok(/pointer/.test(why[3]) && !why[0] && !why[2], 'the pointer row is left out with its reason, the numbers are not');
    ok(/Apply to 3/.test(d.querySelector('#tree-multi .tree-multi-apply').textContent), 'Apply names the 3 rows that change');
    TM.setPattern('qubits.*.z.full_scale_power_dbm');
    const whys = () => [...d.querySelectorAll('#tree-multi .tree-multi-why')].map((w) => w.textContent);
    ok(whys().length === 4 && whys().every((w) => /FSP/.test(w)),
       'the FSP field is left to its own editor (the amplitude offer)');
    TM.setPattern('qubits.*.id');
    ok(whys().length === 4 && whys().every((w) => /read-only/.test(w)),
       'read-only fields are left out');
    TM.setPattern('qubits.*.note');
    vin.value = '+1'; vin.dispatchEvent(new window.Event('input', { bubbles: true }));
    ok(whys().length === 4 && whys().every((w) => /not a number/.test(w)),
       'arithmetic on text is left out');
    vin.value = 'renamed'; vin.dispatchEvent(new window.Event('input', { bubbles: true }));
    ok(/Apply to 4/.test(d.querySelector('#tree-multi .tree-multi-apply').textContent), 'an absolute value applies to text');

    // 4. apply = one batch, only changing rows, model painted
    TM.setPattern('qubits.*.xy.operations.x180.amplitude');
    vin.value = '*2'; vin.dispatchEvent(new window.Event('input', { bubbles: true }));
    reply = { ok: true, tray_html: null, results: [
        { dot_path: 'qubits.qA1.xy.operations.x180.amplitude', applied: true, new_value: 0.2 },
        { dot_path: 'qubits.qA2.xy.operations.x180.amplitude', applied: true, new_value: 0.4 },
        { dot_path: 'qubits.qA10.xy.operations.x180.amplitude', applied: true, new_value: 0.6000000000000001 }] };
    TM.apply();
    await tick(20);
    ok(posts.length === 1, 'Apply sends ONE request');
    const body = posts[0];
    ok(body.group === 'new' && body.updates.length === 3
       && body.updates.every((u) => /x180\.amplitude$/.test(u.dot_path) && !/qB1/.test(u.dot_path)),
       'one batch, one undo group, the pointer row not in it');
    ok(body.updates[0].value === '0.2' && body.updates[2].value === '0.6', 'the values sent are the previewed numbers');
    ok(tree._treeData.qubits.qA2.xy.operations.x180.amplitude === 0.4, 'the committed values are painted into the tree model');
    ok(d.getElementById('tree-multi').hidden && !TM.state(), 'the panel closes after a successful apply');

    // a refused batch writes nothing and says why
    TM.setPattern('qubits.*.xy.operations.x180.length');
    vin.value = '+1'; vin.dispatchEvent(new window.Event('input', { bubbles: true }));
    reply = { ok: false, results: [{ dot_path: 'qubits.qA1.xy.operations.x180.length', error: 'must be an int' },
                                   { dot_path: 'qubits.qA2.xy.operations.x180.length', error: 'rolled back' }] };
    TM.apply();
    await tick(20);
    ok(/nothing written — qubits\.qA1\.xy\.operations\.x180\.length: must be an int/.test(
        d.querySelector('#tree-multi .tree-multi-msg').textContent), 'a refused batch names the failing field, nothing painted');
    ok(tree._treeData.qubits.qA1.xy.operations.x180.length === 40, 'and the model is unchanged');

    kd(d.querySelector('#tree-multi .tree-multi-value'), 'Escape');
    ok(!TM.state(), 'Esc clears');
    // Exercise the shipped shared dialog through TreeMulti's actual apply.
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    window.Headers = global.Headers;
    window.FormData = global.FormData;
    const identityPosts = [];
    window.fetch = function (url, options) {
        if (String(url).startsWith("/field/peek")) return Promise.resolve({json: async () => ({expected:{}})});
        const body = JSON.parse(options.body);
        identityPosts.push(body);
        const result = body.port_ack ? {ok: true, results: body.updates.map(u => ({
            dot_path: u.dot_path, applied: true, new_value: 4}))} : {
            ok: false, port_identity: body.updates.map(u => ({message: u.dot_path + ': key disagrees'}))};
        return Promise.resolve({status: body.port_ack ? 200 : 409, ok: !!body.port_ack,
            json: () => Promise.resolve(result),
            clone: function () { return this; }});
    };
    window.eval(fs.readFileSync(path.join(STATIC, 'port-identity.js'), 'utf8'));
    global.fetch = window.fetch;
    tree._treeData.ports = {analog_outputs: {con1: {'2': {
        '1': {port_id: 1}, '2': {port_id: 2}}}}};
    for (const mode of ['keep', 'cancel', 'fix']) {
        // Enter through Find / By path, then select the identity leaves in the panel.
        row.dispatchEvent(new window.MouseEvent('mousedown', {bubbles: true}));
        (row.querySelector('.tree-row') || row).focus();
        kd(d.activeElement, 'h', {ctrlKey: true});
        [...d.querySelectorAll('#tree-find button')].find(b=>b.textContent==='By path').click();
        TM.setPattern('ports.analog_outputs.con1.2.*.port_id');
        vin.value = '4'; vin.dispatchEvent(new window.Event('input', {bubbles: true}));
        const start = identityPosts.length;
        TM.apply();
        await tick(20);
        ok(identityPosts.length === start + 1 && !identityPosts[start].port_ack,
           'TreeMulti waits for explicit identity ack: ' + mode);
        const dialog = d.querySelector('dialog');
        ok(dialog && dialog.textContent.includes('ports.analog_outputs.con1.2.1.port_id')
           && dialog.textContent.includes('ports.analog_outputs.con1.2.2.port_id'),
           'the batch offer lists every identity row');
        const label = mode === 'fix' ? 'Fix other side too' : mode === 'keep' ? 'Keep as typed' : 'Cancel';
        [...dialog.querySelectorAll('button')].find(b => b.textContent === label).click();
        await tick(20);
        ok(identityPosts.length === start + (mode === 'cancel' ? 1 : 2),
           'only an explicit Fix/Keep resends the batch: ' + mode);
        if (mode !== 'cancel') {
            ok(identityPosts[start + 1].port_ack === mode
               && identityPosts[start + 1].group === identityPosts[start].group
               && JSON.stringify(identityPosts[start + 1].updates) === JSON.stringify(identityPosts[start].updates),
               'the ack preserves the whole batch and undo group: ' + mode);
        }
        tree._treeData.ports.analog_outputs.con1['2']['1'].port_id = 1;
        tree._treeData.ports.analog_outputs.con1['2']['2'].port_id = 2;
        kd(vin, 'Escape');
    }

    // Legacy inspectors deliver the same offer through HTMX's XHR transport.
    const xhrPosts = [];
    window.htmx.ajax = function (verb, url, options) {
        xhrPosts.push({verb, url, options});
        return Promise.resolve();
    };
    for (const mode of ['keep', 'cancel']) {
        const detail = {xhr: {status: 409, responseText: JSON.stringify({
            port_identity: [{message: 'ports.analog_outputs.con1.2.1.port_id: key disagrees'}]})},
            requestConfig: {path: '/qubit/q1/edit', elt: row,
                parameters: {dot_path: 'ports.analog_outputs.con1.2.1.port_id', value: '4'},
                headers: {'X-Request-Token': 'preserved'}}, target: tree};
        d.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', {detail}));
        await tick(0);
        ok(detail.shouldSwap === false && detail.isError === false, 'HTMX offer waits for a choice');
        const dialog = d.querySelector('dialog');
        [...dialog.querySelectorAll('button')].find(b => b.textContent === (mode === 'keep' ? 'Keep as typed' : 'Cancel')).click();
        await tick(10);
    }
    ok(xhrPosts.length === 1 && xhrPosts[0].options.values.port_ack === 'keep'
       && xhrPosts[0].options.headers['X-Request-Token'] === 'preserved',
       'HTMX Keep preserves headers and values; Cancel never resends');

    if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
    console.log('tree_multi_selfcheck: all ok');
    process.exit(0);
})();
