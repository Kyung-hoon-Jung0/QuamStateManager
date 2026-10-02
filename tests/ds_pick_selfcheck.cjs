/* docs/243 -- "Apply selected to chip" on a run's State tab (window.DsPick),
 * against the real app.js module under jsdom.
 *
 * Pins:
 *  P1 model: ticking a container covers everything under it; unticking one
 *     child of a ticked container keeps every sibling; a re-tick of the
 *     container swallows the pieces; partial is "some, not all".
 *  P2 shallowest: search hits under another hit are covered, not repeated.
 *  P3 DOM: every tree row gets a box; a box click ticks (and never bubbles
 *     to the row); a row rendered LATER (a lazy expand) arrives ticked when
 *     its ancestor is; half state shows as indeterminate.
 *  P4 bar: counts; "Select matches" reads container._searchMatches; Clear.
 *  P5 Copy JSON: one pick -> its value; several -> {path: value}.
 *  P6 Apply: preview POSTs {file, paths}; the modal lists change/new rows
 *     and hides "same"; Apply posts ONE /field/edit-batch with group "new",
 *     the chip token, and create only for "new" rows; skip rows are not sent.
 *  P7 a different chip must be acknowledged before Apply is possible.
 *  P8 a failed batch says what failed and writes nothing more.
 *
 * Run: node tests/ds_pick_selfcheck.cjs   (driven by tests/test_ds_pick.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
function grab(head) {
    const i = SRC.indexOf(head);
    const end = /\r?\n\}\)\(\);\r?\n?/.exec(SRC.slice(i));
    return SRC.slice(i, i + end.index + end[0].length);
}
const DATA = {
    qubits: {
        qA1: { T1: 1e-5, f_01: 5e9, z: { exponential_filter: [[-0.01, 100], [-0.004, 1200]] } },
        qA4: { T1: 4e-5, f_01: 5.2e9, z: { exponential_filter: [[-0.02, 90]] } }
    },
    ports: { analog_outputs: { con1: { '5': { '1': { exponential_filter: [[-0.009, 100]], offset: 0 } } } } }
};

function nodeHtml(key, val, p) {
    const kids = (val && typeof val === 'object' && !Array.isArray(val))
        ? Object.keys(val).map(function (k) { return nodeHtml(k, val[k], p + '.' + k); }).join('') : '';
    return '<div class="tree-node" data-path="' + p + '"><div class="tree-row"><span class="tree-toggle"></span>'
         + '<span class="tree-key">' + key + '</span></div>' + kids + '</div>';
}
function world(opts) {
    opts = opts || {};
    const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray" data-change-count="0"></div>'
        + '<input id="ds-state-search"><div id="wrap"><div id="ds-state-tree-state" class="json-tree">'
        + nodeHtml('qubits', { qA1: DATA.qubits.qA1 }, 'qubits')   // qA4 is NOT rendered yet (lazy)
        + nodeHtml('ports', DATA.ports, 'ports')
        + '</div></div></body></html>',
        { url: 'http://localhost/dataset/k:1', pretendToBeVisual: true, runScripts: 'outside-only' });
    const w = dom.window;
    const calls = [];
    w.__previewReply = opts.preview || null;
    w.__batchReply = opts.batch || { ok: true, results: [], tray_html: '<div id="pending-tray" data-change-count="2"></div>' };
    w.fetch = function (u, o) {
        const body = o && o.body ? JSON.parse(o.body) : null;
        calls.push({ u: u, body: body });
        const reply = /apply-selected\/preview$/.test(u) ? w.__previewReply : w.__batchReply;
        return Promise.resolve({ status: reply && reply.ok === false ? 409 : 200,
                                 json: () => Promise.resolve(reply) });
    };
    w.__chipToken = 'tok-1';
    const toasts = [], copies = [], swaps = [];
    w.showToast = (m) => toasts.push(m);
    w.copyWithFeedback = (t) => copies.push(t);
    w._swapPendingTray = (h) => swaps.push(h);
    w.ValueDelta = { chipHtml: function () { return '<span class="val-delta">Δ</span>'; } };
    w.eval(grab('window.DsPick = (function () {'));
    const d = w.document;
    const c = d.getElementById('ds-state-tree-state');
    c._treeData = DATA;
    w.DsPick.attach(c, { uid: 'k:1', file: 'state', searchInput: d.getElementById('ds-state-search') });
    const box = (p) => d.querySelector('.tree-node[data-path="' + p + '"] > .tree-row > input.ds-pick');
    return { w, d, c, box, calls, toasts, copies, swaps };
}
function clickEl(W, el) { el.dispatchEvent(new W.w.MouseEvent('click', { bubbles: true, cancelable: true })); }

(async () => {
    // P1 / P2 the model
    {
        const W = world();
        const M = W.w.DsPick._model;
        const sel = new W.w.Set();
        M.select(sel, 'qubits.qA4');
        ok(M.covered(sel, 'qubits.qA4.z.exponential_filter.0'), 'P1 a ticked qubit covers its deepest fields');
        ok(M.partial(sel, 'qubits') && !M.covered(sel, 'qubits'), 'P1 its parent is partial, not ticked');
        M.unselect(sel, DATA, 'qubits.qA4.T1');
        ok(!M.covered(sel, 'qubits.qA4.T1'), 'P1 unticked T1 is off');
        ok(M.covered(sel, 'qubits.qA4.f_01') && M.covered(sel, 'qubits.qA4.z.exponential_filter'),
           'P1 its siblings stay ticked (' + Array.from(sel).join() + ')');
        ok(M.partial(sel, 'qubits.qA4'), 'P1 qA4 is now partial');
        M.select(sel, 'qubits.qA4');
        ok(Array.from(sel).join() === 'qubits.qA4', 'P1 re-ticking qA4 swallows the pieces');
        M.unselect(sel, DATA, 'qubits.qA4.z.exponential_filter.0');
        ok(M.covered(sel, 'qubits.qA4.T1') && !M.covered(sel, 'qubits.qA4.z.exponential_filter.0'),
           'P1 a deep untick splits every level on the way');
        ok(M.shallowest(['a.b', 'a.b.c', 'a.bd', 'x']).join() === 'a.b,a.bd,x', 'P2 shallowest keeps a.bd (a sibling, not a child)');
    }

    // P3 the tree surface
    {
        const W = world();
        ok(!!W.box('qubits') && !!W.box('qubits.qA1.z.exponential_filter') && !!W.box('ports.analog_outputs.con1.5.1'),
           'P3 every rendered row has a box');
        let rowClicks = 0;
        W.d.querySelector('.tree-node[data-path="qubits"] > .tree-row').addEventListener('click', () => rowClicks++);
        clickEl(W, W.box('qubits'));
        await tick(30);
        ok(W.box('qubits').checked && W.box('qubits.qA1.T1').checked, 'P3 ticking qubits ticks its rendered children');
        ok(rowClicks === 0, 'P3 the tick never reaches the row (no expand/copy)');
        // a lazily rendered branch arrives ticked
        const q4 = W.d.createElement('div');
        q4.innerHTML = nodeHtml('qA4', DATA.qubits.qA4, 'qubits.qA4');
        W.d.querySelector('.tree-node[data-path="qubits"]').appendChild(q4.firstChild);
        await tick(60);
        ok(W.box('qubits.qA4') && W.box('qubits.qA4.T1').checked, 'P3 a branch rendered later arrives ticked');
        clickEl(W, W.box('qubits.qA4.T1'));
        await tick(30);
        ok(W.box('qubits.qA4').indeterminate && W.box('qubits').indeterminate && !W.box('qubits.qA4.T1').checked,
           'P3 one untick shows half state on every ancestor');
        ok(W.box('qubits.qA1').checked, 'P3 the other qubit stays ticked');
    }

    // P4 the bar
    {
        const W = world();
        const bar = W.d.querySelector('.ds-pick-bar');
        ok(bar && bar.querySelector('.ds-pick-apply').disabled, 'P4 Apply is disabled with nothing ticked');
        W.c._searchMatches = new W.w.Set(['qubits.qA1.z.exponential_filter', 'qubits.qA1.z.exponential_filter.0',
                                          'ports.analog_outputs.con1.5.1.exponential_filter']);
        bar._render();
        ok(/\(2\)/.test(bar.querySelector('.ds-pick-matches').textContent), 'P4 Select matches counts the shallowest hits');
        clickEl(W, bar.querySelector('.ds-pick-matches'));
        await tick(10);
        ok(/^2 selected/.test(bar.querySelector('.ds-pick-count').textContent), 'P4 two picked');
        ok(W.box('qubits.qA1.z.exponential_filter').checked, 'P4 the hit is ticked in the tree');
        clickEl(W, bar.querySelector('.ds-pick-clear'));
        await tick(10);
        ok(!W.box('qubits.qA1.z.exponential_filter').checked && bar.querySelector('.ds-pick-apply').disabled, 'P4 Clear clears');
    }

    // P5 Copy JSON
    {
        const W = world();
        const bar = W.d.querySelector('.ds-pick-bar');
        clickEl(W, W.box('qubits.qA1.z.exponential_filter'));
        await tick(10);
        clickEl(W, bar.querySelector('.ds-pick-copy'));
        ok(JSON.stringify(JSON.parse(W.copies[0])) === JSON.stringify(DATA.qubits.qA1.z.exponential_filter), 'P5 one pick copies its value');
        clickEl(W, W.box('ports.analog_outputs.con1.5.1.exponential_filter'));
        await tick(10);
        clickEl(W, bar.querySelector('.ds-pick-copy'));
        const m = JSON.parse(W.copies[1]);
        ok(Object.keys(m).length === 2 && m['ports.analog_outputs.con1.5.1.exponential_filter'], 'P5 several copy a {path: value} map');
    }

    // P6 preview + apply
    {
        const rows = [
            { path: 'qubits.qA1.z.exponential_filter', status: 'change', old: [[-0.01, 100]], new: [[-0.02, 90]] },
            { path: 'qubits.qA1.T1', status: 'same', old: 1e-5, new: 1e-5 },
            { path: 'qubits.qA1.z.extra', status: 'new', old: null, new: 3 },
            { path: 'qubits.qA1.z.ptr', status: 'skip', old: '#/a', new: '#/b', reason: 'a reference (pointer) differs' }
        ];
        const W = world({ preview: { ok: true, rows: rows, counts: { change: 1, same: 1, 'new': 1, skip: 1 }, same_chip: true, chip: 'chipX' } });
        clickEl(W, W.box('qubits.qA1'));
        await tick(10);
        clickEl(W, W.d.querySelector('.ds-pick-apply'));
        await tick(30);
        ok(W.calls[0].u === '/dataset/k%3A1/apply-selected/preview' && W.calls[0].body.file === 'state'
           && W.calls[0].body.paths.join() === 'qubits.qA1', 'P6 the preview names the file and the picks');
        const modal = W.d.getElementById('ds-pick-modal');
        ok(!!modal && modal.classList.contains('modal'), 'P6 the preview opens as a modal (smModalOpen sees .modal)');
        ok(modal.querySelectorAll('tr.ds-pick-r:not([hidden])').length === 3, 'P6 "same" rows are hidden, the rest listed');
        ok(/Apply 2 to working state/.test(modal.querySelector('.ds-pick-go').textContent), 'P6 the button counts what will be written');
        clickEl(W, modal.querySelector('.ds-pick-go'));
        await tick(30);
        const post = W.calls[1];
        ok(post && post.u === '/field/edit-batch', 'P6 Apply posts the shared edit door');
        ok(post.body.group === 'new' && post.body.expect_chip === 'tok-1', 'P6 one Ctrl+Z group, the chip token');
        ok(post.body.updates.length === 2
           && post.body.updates[0].dot_path === 'qubits.qA1.z.exponential_filter' && post.body.updates[0].create === false
           && JSON.stringify(post.body.updates[0].value) === '[[-0.02,90]]'
           && post.body.updates[1].dot_path === 'qubits.qA1.z.extra' && post.body.updates[1].create === true,
           'P6 change + new are sent (the list as one value; create only for new), skip/same are not: ' + JSON.stringify(post.body.updates));
        ok(!W.d.getElementById('ds-pick-modal') && W.swaps.length === 1 && /Staged 2 values/.test(W.toasts[0] || ''),
           'P6 success closes, swaps the tray, says what was staged');
    }

    // P7 a different chip
    {
        const W = world({ preview: { ok: true, rows: [{ path: 'qubits.qA1.T1', status: 'change', old: 1, new: 2 }],
                                    counts: { change: 1 }, same_chip: false, chip: 'other' } });
        clickEl(W, W.box('qubits.qA1.T1'));
        await tick(10);
        clickEl(W, W.d.querySelector('.ds-pick-apply'));
        await tick(30);
        const go = W.d.querySelector('#ds-pick-modal .ds-pick-go');
        ok(go.disabled && /DIFFERENT chip/.test(W.d.querySelector('#ds-pick-modal .ds-pick-warn').textContent),
           'P7 a different chip blocks Apply until acknowledged');
        const ack = W.d.querySelector('#ds-pick-modal .ds-pick-ack');
        ack.checked = true; ack.dispatchEvent(new W.w.Event('change'));
        ok(!go.disabled, 'P7 acknowledging enables it');
    }

    // P8 a failed batch
    {
        const W = world({ preview: { ok: true, rows: [{ path: 'qubits.qA1.T1', status: 'change', old: 1, new: 2 }], counts: { change: 1 }, same_chip: true },
                          batch: { ok: false, results: [{ dot_path: 'qubits.qA1.T1', applied: false, error: 'read-only field' }] } });
        clickEl(W, W.box('qubits.qA1.T1'));
        await tick(10);
        clickEl(W, W.d.querySelector('.ds-pick-apply'));
        await tick(30);
        clickEl(W, W.d.querySelector('#ds-pick-modal .ds-pick-go'));
        await tick(30);
        const err = W.d.querySelector('#ds-pick-modal .ds-pick-err');
        ok(err && !err.hidden && /Nothing was written: qubits\.qA1\.T1 — read-only field/.test(err.textContent),
           'P8 the failure names the field and why; the modal stays');
        ok(!W.toasts.length, 'P8 no success toast');
    }

    console.log(fails ? fails + ' FAILED' : 'all passed');
    process.exit(fails ? 1 : 0);
})();
