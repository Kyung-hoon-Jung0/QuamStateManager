/* jsdom selfcheck for ndview.js + the REAL app.js render/popup plumbing.
 *
 * CRITICAL harness property: this loads the real app.js so ndview's renders go
 * through the real window._plotlyRender (POSITIONAL divId,data,layout,config).
 * A previous suite passed while the app was 100% broken because it never
 * loaded app.js — ndview called the renderer spec-style and every variable
 * showed "Could not render this view". That hole is closed here for real.
 *
 * Covers: flatten/extract/overlay/heatmap pipeline, entity chips, client-side
 * re-render, theme, classified fallback, positional render convention, click
 * handler attached inside the render promise, run-switch generation guard,
 * /field/peek dot_path= param, esc("'")==="&#39;", data-raw attr escaping,
 * candidate dim-name axis resolution (swapped x/y), default_view null guard,
 * unknown-transform skip, Interactive-tab chip-identity token, and the
 * non-blocking amplitude/offset domain warning in the apply popup.
 *
 * Run: NODE_PATH=<node_modules> node tests/ndview_selfcheck.cjs
 * (driven by tests/test_ndview_client.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="ndv-root" data-uid="k:1" data-which="ds_raw.h5">' +
    '<div id="ndv-controls" hidden></div><div id="ndv-plot"></div>' +
    '<div id="ndv-fallback" hidden></div></div>' +
    '<div id="plot-apply-popup" style="display:none">' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all" type="button">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

/* ── controllable fetch stub (recorded; per-test implementations) ────── */
const fetchCalls = [];
let fetchImpl = function () { return new Promise(function () {}); };  // hang by default
window.fetch = function (url, opts) {
    fetchCalls.push(String(url));
    return fetchImpl(String(url), opts);
};
function jsonResponse(obj) {
    return Promise.resolve({ ok: true, status: 200,
                             json: function () { return Promise.resolve(obj); } });
}

/* ── recording Plotly stub (newPlot/react/purge; binds .on ASYNC like the
 *    real library — only once the draw promise runs) ─────────────────── */
const plots = [];
let purgeCount = 0;
function _record(kind, el, data, layout, config) {
    el = typeof el === 'string' ? document.getElementById(el) : el;
    if (el) {
        el.data = data;
        el.on = function (name, cb) {
            el._handlers = el._handlers || {}; el._handlers[name] = cb;
        };
        el.removeAllListeners = function (name) {
            if (el._handlers) delete el._handlers[name];
        };
    }
    plots.push({ kind: kind, el: el, data: data, layout: layout, config: config });
    return Promise.resolve(el);
}
window.Plotly = {
    newPlot: function (el, data, layout, config) { return _record('newPlot', el, data, layout, config); },
    react:   function (el, data, layout, config) { return _record('react', el, data, layout, config); },
    purge:   function (el) {
        purgeCount++;
        el = typeof el === 'string' ? document.getElementById(el) : el;
        if (el) { delete el.data; el._handlers = {}; }
    },
};
window.htmx = { ajax: function () { return Promise.resolve(); },
                on: function () {}, trigger: function () {} };

/* ── load the REAL scripts: app.js first (base.html order), then theme+ndview ── */
const staticDir = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
for (const f of ['app.js', 'plot-theme.js', 'ndview.js']) {
    window.eval(fs.readFileSync(path.join(staticDir, f), 'utf8'));
}
ok(typeof window._plotlyRender === 'function', 'REAL app.js loaded (_plotlyRender present)');

function feedCube(cube) {
    fetchImpl = function () { return jsonResponse(cube); };
}
function plotEl() { return document.getElementById('ndv-plot'); }
function fallbackEl() { return document.getElementById('ndv-fallback'); }

/* ── cubes ────────────────────────────────────────────────────────────── */
const ramsey = {
    ok: true, var: 'I', dtype: 'float64', units: 'V', long_name: null,
    dims: [
        { name: 'qubit', size: 2, kind: 'entity', coord: ['q0', 'q1'], units: null, decimated: false },
        { name: 'idle_time', size: 5, kind: 'sweep', coord: [0, 10, 20, 30, 40], units: 'ns', decimated: false },
        { name: 'detuning_signs', size: 2, kind: 'sweep', coord: [-1, 1], units: null, decimated: false },
    ],
    data: [
        [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]],
        [[11, 12], [13, 14], [15, 16], [17, 18], [19, 20]],
    ],
    kept: null, aux_axes: [], iq_partner: 'Q',
    default_view: { x: 'idle_time', y: null, entity: 'qubit', overlay: ['detuning_signs'], sliders: {} },
};

async function main() {
    const root = document.getElementById('ndv-root');
    const card = document.createElement('button');
    card.className = 'ndv-var-card'; card.setAttribute('data-var', 'I');
    root.appendChild(card);

    /* ══ 1. line render through the REAL positional _plotlyRender ══════ */
    feedCube(ramsey);
    window.NdView.mount();
    await sleep(30);

    ok(plots.length >= 1, 'a plot was rendered');
    ok(!!plotEl().__plotlyRenderChain,
       'render went through the REAL app.js _plotlyRender (positional convention exercised)');
    ok(fallbackEl().hidden && fallbackEl().innerHTML.indexOf('Could not render') === -1,
       'no "Could not render" card — layout arrived positionally, not as a spec object');
    let p = plots[plots.length - 1];
    ok(p.layout && p.layout.xaxis, 'layout object reached Plotly (positional arg #3)');
    ok(p.data.length === 2, 'overlay dim (detuning_signs=2) → 2 traces, got ' + p.data.length);
    ok(JSON.stringify(p.data[0].y) === JSON.stringify([1, 3, 5, 7, 9]),
       'trace 0 extracts qubit q0, sign -1 slice correctly: ' + JSON.stringify(p.data[0].y));
    ok(JSON.stringify(p.data[1].y) === JSON.stringify([2, 4, 6, 8, 10]),
       'trace 1 extracts the sign +1 slice');
    ok(p.layout.xaxis.title.text.indexOf('idle_time') !== -1 &&
       p.layout.xaxis.title.text.indexOf('[ns]') !== -1,
       'x-axis title carries units: ' + p.layout.xaxis.title.text);
    ok(p.layout.paper_bgcolor === 'rgba(0,0,0,0)', 'house theme applied (transparent paper)');
    ok(p.config && typeof p.config === 'object', 'config reached Plotly (positional arg #4)');
    // P1-1: the click handler must exist AFTER the async draw (attached in .then)
    ok(plotEl()._handlers && typeof plotEl()._handlers['plotly_click'] === 'function',
       'plotly_click handler attached inside the render promise (first render of a fresh div)');

    const ctl = document.getElementById('ndv-controls');
    ok(ctl.innerHTML.indexOf('q0') !== -1 && ctl.innerHTML.indexOf('q1') !== -1,
       'entity chips rendered for qubit');
    ok(ctl.innerHTML.indexOf('component') !== -1, 'I/Q component selector rendered');

    /* ── chip switch → client-side re-render (no fetch) ── */
    plots.length = 0;
    const fetchBefore = fetchCalls.length;
    const chips = ctl.querySelectorAll('.ndv-chip[data-dim]');
    chips[1].click();   // q1
    await sleep(30);
    ok(plots.length === 1, 'chip click re-renders');
    ok(fetchCalls.length === fetchBefore, 'chip click is client-side (no fetch)');
    ok(JSON.stringify(plots[0].data[0].y) === JSON.stringify([11, 13, 15, 17, 19]),
       'q1 slice extracted after chip click');

    /* ══ 2. heatmap + P2-b purge-before-gut ═════════════════════════════ */
    const heat = {
        ok: true, var: 'S', dtype: 'float64', units: null, long_name: null,
        dims: [
            { name: 'flux', size: 3, kind: 'sweep', coord: [0, 1, 2], units: 'V', decimated: false },
            { name: 'freq', size: 4, kind: 'sweep', coord: [10, 20, 30, 40], units: 'Hz', decimated: false },
        ],
        data: [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]],
        kept: null, aux_axes: [], iq_partner: null,
        default_view: { x: 'freq', y: 'flux', entity: null, overlay: [], sliders: {} },
    };
    feedCube(heat);
    plots.length = 0;
    const purgeBefore = purgeCount;
    card.setAttribute('data-var', 'S');
    card.click();
    await sleep(30);
    ok(purgeCount > purgeBefore,
       'openVar purges the live Plotly div before gutting it (no stale el.data → react on a gutted div)');
    ok(plots.length === 1 && plots[0].data[0].type === 'heatmap',
       '2-sweep cube renders a heatmap');
    ok(JSON.stringify(plots[0].data[0].z[0]) === JSON.stringify([1, 2, 3, 4]),
       'heatmap z rows extracted correctly');

    /* ══ 3. classified fallback + esc("'") entity ═══════════════════════ */
    feedCube({ ok: false, error: "synthetic failure — it's broken",
               fallback: { kind: 'table', dims: ['a'], shape: [3], sample: [1, 2, null], total: 3 } });
    plots.length = 0;
    card.setAttribute('data-var', 'bad');
    card.click();
    await sleep(30);
    const fb = fallbackEl();
    ok(!fb.hidden && fb.innerHTML.indexOf('synthetic failure') !== -1,
       'classified fallback renders the error card');
    ok(fb.innerHTML.indexOf('∅') !== -1, 'table sample renders nulls honestly');
    ok(fb.textContent.indexOf("it's broken") !== -1,
       "esc(\"'\") emits &#39; — apostrophes render intact: " + JSON.stringify(fb.textContent.slice(0, 60)));
    ok(fb.innerHTML.indexOf('&//39;') === -1, 'no mangled &//39; entity in output');

    /* ══ 4. value chip: dim-name axis resolution + dot_path= peek ═══════ */
    // Swapped-axes run: power out-sizes freq → power lands on x, freq on y.
    // The candidate deliberately carries axis:"x" (the node's stale decision-
    // axis assumption) but dim:"full_freq" — the DIM binding must win.
    const swapped = {
        ok: true, var: 'S2', dtype: 'float64', units: null, long_name: null,
        dims: [
            { name: 'qubit', size: 1, kind: 'entity', coord: ['q0'], units: null, decimated: false },
            { name: 'full_freq', size: 3, kind: 'sweep', coord: [5.0e9, 5.1e9, 5.2e9], units: 'Hz', decimated: false },
            { name: 'power', size: 4, kind: 'sweep', coord: [-20, -15, -10, -5], units: 'dBm', decimated: false },
        ],
        data: [[[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]],
        kept: null, aux_axes: [], iq_partner: null,
        default_view: { x: 'power', y: 'full_freq', entity: 'qubit', overlay: [], sliders: {} },
        click: {
            candidates: [
                { axis: 'x', dim: 'full_freq', path: 'qubits.{q}.resonator.f_01',
                  label: 'Resonator f_01', tier: 'node' },
                { axis: 'x', dim: 'ghost_dim', path: 'qubits.{q}.f_01',
                  label: 'Ghost (unresolvable dim)', tier: 'coord' },
            ],
            experiment: 'resonator_spectroscopy_vs_power',
            chip: { token: 'run-tok', name: 'run-chip' },
        },
    };
    feedCube(swapped);
    plots.length = 0;
    card.setAttribute('data-var', 'S2');
    card.click();
    await sleep(30);
    ok(plots.length === 1 && plots[0].data[0].type === 'heatmap', 'swapped-axes cube rendered');
    const clickCb = plotEl()._handlers && plotEl()._handlers['plotly_click'];
    ok(typeof clickCb === 'function', 'click handler present on the swapped-axes panel');

    // Route the peek fetch; capture its URL.
    let peekUrl = null;
    fetchImpl = function (url) {
        if (url.indexOf('/field/peek') !== -1) {
            peekUrl = url;
            return jsonResponse({ ok: true,
                values: { 'qubits.q0.resonator.f_01': 5.05e9, 'qubits.q0.f_01': 4.0e9 },
                errors: {} });
        }
        return jsonResponse({});
    };
    clickCb({ points: [{ x: -10, y: 5.2e9, z: 1.5 }], event: { clientX: 8, clientY: 8 } });
    await sleep(30);
    ok(!!peekUrl, 'clicking a point fetches current values via /field/peek');
    ok(peekUrl && peekUrl.indexOf('dot_path=') !== -1,
       'peek uses dot_path= (matches routes.py getlist("dot_path")): ' + peekUrl);
    ok(peekUrl && peekUrl.indexOf('paths=') === -1, 'legacy paths= param is gone');

    const chip = document.getElementById('ndv-value-chip');
    const stageBtns = chip.querySelectorAll('.ndv-cand-stage');
    ok(stageBtns.length === 1,
       'unresolvable-dim candidate SKIPPED (no rows[0] fallback), resolvable one offered: got ' +
       stageBtns.length);
    ok(stageBtns.length && stageBtns[0].getAttribute('data-value') === '5200000000',
       'y-bound candidate takes the Y coordinate (5.2 GHz), not clicked dBm: ' +
       (stageBtns.length ? stageBtns[0].getAttribute('data-value') : 'n/a'));

    // Stage → the audited popup gets path/value + the run's chip identity.
    let staged = null;
    const origPopup = window._openPlotApplyPopup;
    window._openPlotApplyPopup = function (updates, expName, q, ctx, chipExpect) {
        staged = { updates: updates, expName: expName, q: q, chipExpect: chipExpect };
    };
    stageBtns[0].click();
    window._openPlotApplyPopup = origPopup;
    ok(staged && staged.updates[0].dot_path === 'qubits.q0.resonator.f_01' &&
       staged.updates[0].value === '5200000000',
       'Stage hands the resolved path+value to the plot-apply popup');
    ok(staged && staged.chipExpect && staged.chipExpect.token === 'run-tok',
       'Stage carries the run chip identity into the popup');

    // data-raw attribute injection (P1-6): a dataset-derived STRING coordinate
    // must not be able to smuggle attributes into the copy button.
    window.NdViewChip.show({ clientX: 5, clientY: 5 },
        [['category', 'x" onmouseover="window.__pwned=1', null]],
        { var: 'v', dims: [], default_view: { x: 'a', y: null, entity: null }, click: {} },
        { x: 1, y: 2 });
    const copyBtn = document.getElementById('ndv-value-chip').querySelector('.ndv-copy');
    ok(copyBtn && !copyBtn.hasAttribute('onmouseover') &&
       copyBtn.getAttribute('data-raw') === 'x" onmouseover="window.__pwned=1',
       'data-raw is attribute-escaped (no injected onmouseover handler)');

    // P3: default_view null + '{p}' candidate must not deref-crash the chip.
    let threw = false;
    try {
        window.NdViewChip.show({ clientX: 5, clientY: 5 }, [['x', 1, null]],
            { var: 'v', default_view: null,
              dims: [{ name: 'qubit', size: 1, kind: 'entity', coord: ['q0'] }],
              click: { candidates: [{ dim: 'a', path: 'pairs.{p}.x', label: 'L' }] } },
            { x: 1, y: 2 });
    } catch (e) { threw = true; }
    ok(!threw, 'value chip survives default_view=null (entity guard)');
    document.getElementById('ndv-value-chip').hidden = true;

    /* ══ 5. run-switch race: stale fetch discarded, cache uid-scoped ════ */
    const pending = {};   // url → resolve
    fetchImpl = function (url) {
        return new Promise(function (resolve) { pending[url] = resolve; });
    };
    card.setAttribute('data-var', 'I');
    root.setAttribute('data-uid', 'k:1');
    window.NdView.mount();                       // run A → fetch F1 (pending)
    await sleep(10);
    const urlA = Object.keys(pending).find(function (u) { return u.indexOf('k%3A1') !== -1; });
    ok(!!urlA, 'run A fetch in flight: ' + urlA);

    root.setAttribute('data-uid', 'k:2');
    window.NdView.mount();                       // run B before A resolves
    await sleep(10);
    const urlB = Object.keys(pending).find(function (u) { return u.indexOf('k%3A2') !== -1; });
    ok(!!urlB, 'run B fetch in flight: ' + urlB);

    const cubeB = { ok: true, var: 'I', dtype: 'float64', units: 'V', long_name: null,
        dims: [{ name: 'idle_time', size: 3, kind: 'sweep', coord: [0, 1, 2], units: 'ns', decimated: false }],
        data: [7, 8, 9], kept: null, aux_axes: [], iq_partner: null,
        default_view: { x: 'idle_time', y: null, entity: null, overlay: [], sliders: {} } };
    const cubeA = JSON.parse(JSON.stringify(cubeB)); cubeA.data = [666, 666, 666];

    plots.length = 0;
    pending[urlB]({ ok: true, status: 200, json: function () { return Promise.resolve(cubeB); } });
    await sleep(30);
    ok(plots.length >= 1 &&
       JSON.stringify(plots[plots.length - 1].data[0].y) === JSON.stringify([7, 8, 9]),
       'run B cube rendered in run B panel');

    plots.length = 0;
    pending[urlA]({ ok: true, status: 200, json: function () { return Promise.resolve(cubeA); } });
    await sleep(30);
    ok(plots.length === 0, 'STALE run-A fetch discarded (no render with run-A data)');
    const st = window.NdView._state();
    ok(st && st.uid === 'k:2' && st.cube && st.cube.data[0] === 7,
       'state still holds run B cube after the stale resolution');
    // cache not poisoned: re-selecting the var on run B serves B (no A leak)
    plots.length = 0;
    const fetches5 = fetchCalls.length;
    card.click();
    await sleep(30);
    ok(fetchCalls.length === fetches5, 'run B cube served from the (uid-scoped) cache');
    ok(plots.length >= 1 &&
       JSON.stringify(plots[plots.length - 1].data[0].y) === JSON.stringify([7, 8, 9]),
       'cached cube is run B’s, not the poisoned run-A payload');

    /* ══ 6. app.js Interactive-tab: unknown-transform skip + chip token ═ */
    const dsRoot = document.createElement('div');
    dsRoot.id = 'ds-detail-root';
    dsRoot.setAttribute('data-experiment', '20b_cz_calib');
    dsRoot.setAttribute('data-chip-token', 'tok123');
    dsRoot.setAttribute('data-chip-name', 'chipA');
    document.body.appendChild(dsRoot);

    let popCall = null;
    const origPopup2 = window._openPlotApplyPopup;
    window._openPlotApplyPopup = function (updates, expName, q, ctx, chipExpect) {
        popCall = { updates: updates, expName: expName, q: q, ctx: ctx, chipExpect: chipExpect };
    };
    const fakeDiv = { on: function (n, cb) { this._h = this._h || {}; this._h[n] = cb; } };
    window._attachInteractivePlotClickHandler(fakeDiv, {
        axis: 'x', qubit: 'q0',
        targets: [
            { path: 'qubits.{q}.f_01' },
            { path: 'qubits.{q}.grid_gate.length', transform: { type: 'mystery9000' } },
            { path: 'qubits.{q}.resonator.operations.readout.amplitude',
              transform: { type: 'dbm_to_amp', ref_dbm: 0, scale: 1 } },
        ],
    }, 'r1');
    fakeDiv._h['plotly_click']({ points: [{ x: 2, y: 3 }] });
    window._openPlotApplyPopup = origPopup2;

    ok(popCall && popCall.updates.length === 2 &&
       popCall.updates.every(function (u) { return u.dot_path.indexOf('grid_gate') === -1; }),
       'unknown transform.type SKIPS the target (never stages the raw coordinate as identity)');
    ok(popCall && Math.abs(popCall.updates[1].value - Math.pow(10, 2 / 20)) < 1e-12,
       'known transforms (dbm_to_amp) still compute');
    ok(popCall && popCall.chipExpect && popCall.chipExpect.token === 'tok123',
       'Interactive-tab click passes the run chip-identity token to the popup (cross-chip 409 gate)');

    /* ══ 7. apply-popup value-domain warning (non-blocking) ═════════════ */
    window._renderPlotApplyPopup([
        { dot_path: 'qubits.q0.xy.operations.x180.amplitude', value: 1.5 },
        { dot_path: 'qubits.q0.z.joint_offset', value: 0.75 },
        { dot_path: 'qubits.q0.f_01', value: 5.0e9 },
    ], 'exp', 'q0', [], null);
    const rows7 = document.querySelectorAll('#plot-apply-rows .plot-apply-row');
    ok(rows7.length === 3, 'popup rendered 3 rows');
    const warn0 = rows7[0].querySelector('.plot-apply-row-domainwarn');
    const warn1 = rows7[1].querySelector('.plot-apply-row-domainwarn');
    const warn2 = rows7[2].querySelector('.plot-apply-row-domainwarn');
    ok(warn0 && !warn0.hidden && warn0.textContent.indexOf('full scale') !== -1,
       '|amplitude| > 1 row shows the OPX full-scale warning: ' + (warn0 && warn0.textContent));
    ok(warn1 && !warn1.hidden && warn1.textContent.indexOf('flux DC') !== -1,
       '|offset| > 0.5 row shows the flux DC-range warning: ' + (warn1 && warn1.textContent));
    ok(warn2 && warn2.hidden, 'in-range row (f_01) shows no warning');
    ok(!rows7[0].querySelector('.plot-apply-row-btn').disabled,
       'warning is NON-blocking — Apply stays enabled (trust-researcher-input)');
    // re-evaluated on edit
    const inp0 = rows7[0].querySelector('.plot-apply-new-input');
    inp0.value = '0.9';
    inp0.dispatchEvent(new window.Event('input', { bubbles: true }));
    ok(warn0.hidden, 'warning clears when the user edits the value back in range');
    inp0.value = '-1.2';
    inp0.dispatchEvent(new window.Event('input', { bubbles: true }));
    ok(!warn0.hidden, 'warning re-appears on a new out-of-range edit (|-1.2| > 1)');

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
}

main().catch(function (e) {
    console.error('HARNESS ERROR:', e && e.stack || e);
    process.exit(1);
});
