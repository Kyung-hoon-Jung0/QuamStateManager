/* v1.5.1 R3 MINOR 4 + MINOR 5: what "Leave site?" asks about.
 *
 * MINOR 4: the page follows the drift poll's tray_durable (a checkpoint that
 *          started failing after the tray rendered).
 * MINOR 5: a value typed and not committed in the inspector (qubit, pulse
 *          fields) or the Json Tree editor is asked about, durable tray or not.
 *
 * The driver (tests/test_v151_r3_leave_guard_typed.py) renders the REAL full
 * page with a checkpointed tray, the real qubit and pulse inspectors and the
 * real /state/drift answer after the checkpoint writes started failing.
 *
 * Run: node tests/v151_r3_leave_guard_typed_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const SQ = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

function page(extraHtml, drift) {
    const body = FX.page.slice(FX.page.indexOf('<body'));
    const html = body.replace(/<script\b[\s\S]*?<\/script>/gi, '')
        .replace(/<\/body>/i, '<div id="r3-extra">' + (extraHtml || '') + '</div>'
                 + '<div id="r3-tree" class="json-tree"></div></body>');
    const dom = new JSDOM('<!doctype html><html>' + html + '</html>',
        { url: 'http://localhost/help', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    w.fetch = (url) => {
        url = String(url);
        if (url.indexOf('/state/drift') === 0 && url.indexOf('/view') < 0) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve(drift || FX.drift) });
        }
        if (url.indexOf('/field/peek') === 0) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve({ expected: {} }) });
        }
        return new Promise(() => {});
    };
    w.eval(SQ);
    w.eval(APP);
    return w;
}
function asks(w) {
    const ev = new w.Event('beforeunload', { cancelable: true });
    w.dispatchEvent(ev);
    return ev.defaultPrevented;
}

(async function () {
    // --- MINOR 4
    {
        const w = page('');
        const tray = w.document.getElementById('pending-tray');
        ok(tray && tray.getAttribute('data-tray-durable') === '1', 'fixture: the page drew a checkpointed tray');
        ok(!asks(w), 'a checkpointed tray: leaving asks nothing');
        w.document.dispatchEvent(new w.Event('liveDriftChanged'));     // the drift poll
        await tick();
        ok(!tray.hasAttribute('data-tray-durable'), 'the poll says the checkpoint is failing now');
        ok(asks(w), 'leaving asks again');
    }
    // --- MINOR 5: the qubit inspector
    {
        const w = page(FX.inspector);
        const input = w.document.querySelector('#r3-extra .inline-edit input.edit-input');
        ok(!!input, 'the real qubit inspector renders an inline field');
        ok(!asks(w), 'nothing typed: leaving asks nothing (durable tray)');
        input.value = input.value + '9';
        ok(asks(w), 'a value typed in the inspector and not committed: leaving asks');
    }
    // --- MINOR 5: the pulse fields
    {
        const w = page(FX.pulse);
        const input = w.document.querySelector('#r3-extra .pulse-edit-form input.edit-input');
        ok(!!input, 'the real pulse inspector renders a field');
        ok(!asks(w), 'nothing typed: leaving asks nothing');
        input.value = '123';
        ok(asks(w), 'a pulse field typed and not committed: leaving asks');
    }
    // --- MINOR 5: the Json Tree editor
    {
        // the drift poll runs on its own timer here: it says the tray is durable
        const w = page('', Object.assign({}, FX.drift, { tray_durable: true }));
        w.renderJsonTree('r3-tree', { qubits: { q1: { T1: 1.11e-05 } } }, { defaultDepth: 8, crud: true });
        w.jsonTreeSearch('r3-tree', 'q1');
        await new Promise((r) => setTimeout(r, 300));
        const val = w.document.querySelector('#r3-tree .tree-node[data-path="qubits.q1.T1"] .tree-val');
        ok(!!val, 'the tree renders the leaf');
        val.click();
        await new Promise((r) => setTimeout(r, 30));
        const input = val.querySelector('input.tree-edit-input');
        ok(!!input, 'clicking opens the inline editor');
        ok(!asks(w), 'an editor opened and untouched: leaving asks nothing');
        input.value = '2e-05';
        ok(asks(w), 'a value typed in the tree and not committed: leaving asks');
    }
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
