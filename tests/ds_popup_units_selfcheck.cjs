/* DS-1: the plot-click popup is readable.
 *
 * Under jsdom against the REAL app.js:
 *   - the dot path carries a break opportunity (<wbr>) after every dot and
 *     nowhere else, so it wraps at its joints, never mid-word; the full path
 *     stays in the title;
 *   - previous / new / delta carry the unit the value is STORED in, taken
 *     from /field/peek's `units`;
 *   - a field with no known unit shows NO unit (never a guessed one) and says
 *     so when the plot axes carry units;
 *   - a known unit that differs from the plot axes is stated beside them;
 *   - the value Apply posts is exactly the staged value (units are display
 *     only).
 *
 * Run: node tests/ds_popup_units_selfcheck.cjs (driven by tests/test_ds_popup_units.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="plot-apply-popup" style="display:none">' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<div id="plot-apply-point"><div class="pt-copy-rows">' +
    '<div class="pt-copy-row" data-axis="x"><span class="pt-copy-k">x</span><code class="pt-copy-v">7.4566</code><span class="pt-copy-u">GHz</span></div>' +
    '<div class="pt-copy-row" data-axis="y"><span class="pt-copy-k">y</span><code class="pt-copy-v">4.25</code><span class="pt-copy-u">mV</span></div>' +
    '</div></div>' +
    '<div id="plot-apply-verdict" hidden></div>' +
    '<div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

const F = 'qubits.q2.resonator.f_01';
const A = 'qubits.q0.xy.operations.x180_DragGaussian.amplitude';
const V = 'qubits.q0.z.joint_offset';
const posts = [];
window.fetch = function (url, opts) {
    url = String(url);
    if (opts && opts.method === 'POST') {
        posts.push({ url: url, body: String(opts.body || '') });
        return Promise.resolve({ ok: true, status: 200, headers: { get: function () { return null; } },
            json: function () { return Promise.resolve({ ok: true }); },
            text: function () { return Promise.resolve(''); } });
    }
    let body = {};
    if (url.indexOf('/chip/active-token') === 0) body = { loaded: true, token: 't', name: 'c', path: '/c' };
    else if (url.indexOf('/field/peek') === 0) body = {
        ok: true, values: { [F]: 7457600000, [A]: 0.310091286919996, [V]: 0.1 }, errors: {},
        resolved: { [F]: { is_pointer: false, resolvable: true, resolved_value: 7457600000, candidates: [] },
                    [A]: { is_pointer: false, resolvable: true, resolved_value: 0.310091286919996, candidates: [] },
                    [V]: { is_pointer: false, resolvable: true, resolved_value: 0.1, candidates: [] } },
        units: { [F]: 'Hz', [V]: 'mV' } };
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
// the popup fills the point box itself; keep the fixture's axes
window.PointCopy.fill = function () {};

function row(p) { return document.querySelector('#plot-apply-rows .plot-apply-row[data-input-path="' + p + '"]'); }

(async function main() {
    window._openPlotApplyPopup([{ dot_path: F, value: '7456659950.842465' },
                                { dot_path: A, value: '0.2007' },
                                { dot_path: V, value: '0.2' }], 'exp', 'q2', [], null);
    await sleep(80);
    const rf = row(F), ra = row(A);
    ok(rf && ra, 'both rows rendered');

    const code = rf.querySelector('.plot-apply-row-path code');
    const wbrs = code.querySelectorAll('wbr').length;
    ok(wbrs === F.split('.').length - 1, 'one <wbr> per dot joint (' + wbrs + ')');
    ok(/\.<wbr>/.test(code.innerHTML) && !/[^.]<wbr>/.test(code.innerHTML),
       'every break opportunity sits right after a dot: ' + code.innerHTML);
    ok(code.textContent === F && code.getAttribute('title') === F, 'the path text and title are the full path');

    const oldU = rf.querySelector('.plot-apply-old-unit'), newU = rf.querySelector('.plot-apply-new-unit');
    ok(oldU && !oldU.hidden && oldU.textContent === 'Hz', 'previous value states its stored unit');
    ok(!newU, 'the input row carries no unit (it would cut the value)');
    const inF = rf.querySelector('.plot-apply-new-input');
    ok(inF.title === inF.value, 'the input mirrors its whole value in its title');
    const dl = rf.querySelector('.plot-apply-delta');
    ok(dl && !dl.hidden && /^-940,049 Hz \(/.test(dl.textContent), 'delta is in the stored unit: ' + (dl && dl.textContent));
    const nf = rf.querySelector('.plot-apply-row-unitnote');
    ok(nf && !nf.hidden && /Stored in Hz/.test(nf.textContent) && /x in GHz/.test(nf.textContent),
       'a stored unit that differs from the plot axes is stated: ' + (nf && nf.textContent));

    const aU = ra.querySelectorAll('.plot-apply-unit');
    ok(Array.prototype.every.call(aU, function (e) { return e.hidden && !e.textContent; }),
       'a field with no known unit shows NO unit');
    const da = ra.querySelector('.plot-apply-delta');
    ok(da && !/[A-Za-z]/.test(da.textContent.replace(/\(.*\)/, '')), 'its delta carries no unit: ' + (da && da.textContent));
    const na = ra.querySelector('.plot-apply-row-unitnote');
    ok(na && !na.hidden && /no unit SM knows/.test(na.textContent), 'and it says so: ' + (na && na.textContent));

    const oa = ra.querySelector('.plot-apply-old-val');
    ok(oa.innerHTML === '0.<wbr>310091286919996', 'a long previous value breaks after its decimal point: ' + oa.innerHTML);
    const nv = row(V).querySelector('.plot-apply-row-unitnote');
    ok(nv && nv.hidden && !nv.textContent, 'a stored unit equal to a plot axis unit needs no note');
    ok(window.PointCopy.unitOf('I (rotated)') === '' && window.PointCopy.unitOf('Amplitude [mV]') === 'mV'
       && window.PointCopy.unitOf('Frequency (GHz)') === 'GHz',
       'only a unit-shaped trailing token is read as an axis unit');

    window.applyPlotRow(rf);
    await sleep(80);
    const p = posts.filter(function (x) { return x.url.indexOf('/field/edit') === 0; })[0];
    ok(p && /value=7456659950\.842465(&|$)/.test(p.body) && p.body.indexOf('Hz') === -1,
       'Apply posts exactly the staged number, no unit: ' + (p && p.body));

    if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
    console.log('ALL OK');
    process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
