/* jsdom selfcheck for the FSP compensation notice (D-43/D-44, app.js).
 * Every FSP edit door commits at once; the response header X-SM-Fsp-Changed
 * names the changed ports and the undo group, and ONE hook turns it into a
 * non-blocking notice: Yes writes the plan's amplitudes into that same group,
 * No (or letting it go) keeps the FSP alone and says how the power moved.
 * Run: node tests/fsp_notice_selfcheck.cjs   (driven by tests/test_fsp_notice.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms || 0));

const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray"></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;

const FSP = 'ports.mw_outputs.con1.1.1.full_scale_power_dbm';
const PLAN = { plans: [{ fsp_path: FSP, old_fsp: 0, new_fsp: -6,
    amps: [{ path: 'qubits.q1.xy.operations.x180.amplitude', old: 0.1, new: 0.1995 },
           { path: 'qubits.q1.xy.operations.x90.amplitude', old: 0.05, new: 0.0998 }] }] };
const posts = [];
const hdr = (h) => ({ get: (k) => (h || {})[k] || null });
window.fetch = global.fetch = async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    posts.push({ url, body });
    if (url === '/field/fsp-compensation') return { headers: hdr(), json: async () => ({ ok: true, ...PLAN }) };
    if (url === '/field/edit-batch' && body && body.updates[0].dot_path === FSP)
        return { headers: hdr({ 'X-SM-Fsp-Changed': JSON.stringify({ ports: [{ dot_path: FSP, old: 0 }], gid: 'g7' }) }),
                 json: async () => ({ ok: true, group_id: 'g7', results: [] }) };
    return { headers: hdr(), json: async () => ({ ok: true, results: [] }) };
};
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const d = window.document;
const note = () => d.getElementById('ui-fsp-notice');

(async function () {
    // 1. any door's FSP write -> the notice, no amplitude written yet
    window.FspAfterReplace.ms(150);
    await window.fetch('/field/edit-batch', { method: 'POST', body: JSON.stringify({ updates: [{ dot_path: FSP, value: '-6' }] }) });
    await tick(30);
    ok(!!note(), 'an FSP write through any door brings the compensation notice');
    ok(/changed FSP on 1 port/.test(note().textContent) && /2 related amplitudes/.test(note().textContent), 'the notice names the ports and the amplitudes');
    ok(!posts.some(p => p.url === '/field/edit-batch' && p.body.updates[0].dot_path !== FSP), 'no amplitude written before an answer');
    // 2. Yes -> amplitudes of the plan into the SAME group
    note().querySelector('[name=fsp-yes]').click(); await tick(30);
    const yes = posts.filter(p => p.url === '/field/edit-batch').at(-1);
    ok(yes.body.group === 'g7', 'Yes joins the FSP write\'s undo group (one Ctrl+Z)');
    ok(JSON.stringify(yes.body.updates) === JSON.stringify(PLAN.plans[0].amps.map(a => ({ dot_path: a.path, value: a.new }))), 'Yes writes exactly the plan\'s amplitudes');
    window.FspAfterReplace.close();
    // 3. No -> nothing else written, the power-change line
    let n0 = posts.length;
    await window.fetch('/field/edit-batch', { method: 'POST', body: JSON.stringify({ updates: [{ dot_path: FSP, value: '-6' }] }) });
    await tick(30);
    n0 = posts.length;
    note().querySelector('[name=fsp-no]').click(); await tick(10);
    ok(posts.length === n0, 'No writes nothing');
    ok(/output power changed by -6 dB/.test(note().textContent), 'No says how the output power moved');
    window.FspAfterReplace.close();
    // 4. let it go -> same as No
    await window.fetch('/field/edit-batch', { method: 'POST', body: JSON.stringify({ updates: [{ dot_path: FSP, value: '-6' }] }) });
    await tick(30); n0 = posts.length;
    note().dispatchEvent(new window.MouseEvent('mouseenter')); await tick(250);
    ok(note() && !/output power changed/.test(note().textContent), 'stays open while hovered');
    note().dispatchEvent(new window.MouseEvent('mouseleave')); await tick(250);
    ok(posts.length === n0, 'a dismissed notice writes nothing');
    ok(note() && /output power changed by -6 dB/.test(note().textContent), 'dismissed = No, with the power-change line');
    window.FspAfterReplace.close();
    process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
