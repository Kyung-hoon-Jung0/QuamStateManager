/* jsdom selfcheck: the CZ gate dialog (cz-gate.js) with a mocked fetch.
 *
 * The dialog HTML is the real template: tests/test_cz_gate_routes.py renders it
 * through the route and passes the file in CZG_DIALOG; run alone (npm run
 * selfcheck) the template is read and its few Jinja holes are filled here.
 * Pins: button states across input change / check / create, the busy guard,
 * the two stale rounds, error callouts in the dialog, several pairs, the
 * not-checked wording, the chart guard, the row buttons (keyboard
 * reachable, never opening the row under them), and the slot gate class:
 * the new-class row with its two-step save, and the unit method's spectator
 * cells (the allowed range is the server's number, shown as given). */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..', 'quam_state_manager', 'web');
const DATA = {
    pairs: [{ id: 'q1-2', control: 'q1', target: 'q2', moving_qubit: 'control', gates: ['cz_old'] },
            { id: 'q2-3', control: 'q2', target: 'q3', moving_qubit: 'target', gates: [] }],
    selected: 'q1-2', env: 'lab',
    classes: [{ 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [],
                slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: "the moving qubit's flux line", required: true,
                          pulse_classes: [{ 'class': 'P.SquarePulse', label: 'Square', length: 'explicit',
                                            fields: [{ name: 'length', label: 'Length', kind: 'int', unit: 'ns', 'default': 100 },
                                                     { name: 'amplitude', label: 'Amplitude', kind: 'float', unit: 'V', 'default': 0.1 }] }] }] },
              { 'class': 'X', label: 'Other', layout: 'copied', available: false, why: 'SM does not know it.', slots: [], models: [] },
              { 'class': 'L.TwoFlux', label: 'TwoFlux (copies a gate)', layout: 'copied', available: true, why: '', slots: [], models: [['q1-2', 'cz_m']] }],
};

let dataOverride = null;             // a test's own dialog data (which classes can run where)
let openScrollFixture = false;
function dialogHtml() {
    let h = dialogHtmlBase();
    if (openScrollFixture) h = h.replace(/(<[^>]+id="czg-empty"[^>]*>)/,
        '<div data-czg-table-scroll data-czg-open-scroll-fixture><table aria-label="Open table"></table></div>$1');
    if (!dataOverride) return h;
    return h.replace(/(<script type="application\/json" id="czg-data">)[\s\S]*?(<\/script>)/,
        (m, a, b) => a + JSON.stringify(dataOverride) + b);
}
function dialogHtmlBase() {
    if (process.env.CZG_DIALOG) return fs.readFileSync(process.env.CZG_DIALOG, 'utf8');
    let t = fs.readFileSync(path.join(ROOT, 'templates', '_cz_gate_dialog.html'), 'utf8');
    t = t.replace(/\{#[\s\S]*?#\}/g, '');
    t = t.replace('{{ data | tojson }}', JSON.stringify(DATA));
    t = t.replace(/\{%-? for p in data\.pairs[\s\S]*?endfor -?%\}/,
        DATA.pairs.map((p) => '<option value="' + p.id + '"' + (p.id === DATA.selected ? ' selected' : '') + '>' + p.id + '</option>').join(''));
    t = t.replace(/\{%-?[\s\S]*?-?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '');
    return t;
}

const PAGE = '<!doctype html><html><body><div id="inspector-pane"></div>'
    + '<table><tbody><tr id="row" tabindex="0"><td>q1-2'
    + '<span class="czg-row-acts"><button type="button" class="btn-sm outline" id="detail">Detail</button>'
    + '<button type="button" class="btn-sm outline" id="gate" data-czg-open data-czg-pair="q1-2">+ Gate</button></span></td></tr>'
    + '<tr id="qrow"><td>q1<span class="czg-row-acts"><button type="button" class="btn-sm outline" id="qpulse" data-czg-pulse="q1">+ Pulse</button></span></td></tr>'
    + '</tbody></table></body></html>';

const dom = new JSDOM(PAGE, { url: 'http://localhost/pairs', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;
const sent = [];
const queue = { plan: [], check: [], create: [], 'slot-class': [], 'save-config': [] };
let unitReach = null;               // a test's own server range per spectator qubit (the unit method)
const OUTCOME_CASES = JSON.parse(require('fs').readFileSync(require('path').join(__dirname, 'fixtures', 'gate_outcome_cases.json'), 'utf8')).cases;
let hold = null;                    // a pending check the test resolves itself
let holdCreate = null;
let holdPlan = null;                // a pending plan the test resolves itself
let failDialog = false;
const toasts = [];
window.showToast = (m, k) => { toasts.push([m, k]); };              // a pending create the test resolves itself
const LONG = 'copied from the model gate';
function planCopied(body) {
    const given = ((body.pulses.flux_pulse_control || {}).fields) || {};
    const pulse = Object.assign({ __class__: 'L.Step', amplitude: 0.0, t_phi: 2, axis_angle: null, 'a"]b': 1.5 }, given);
    const prov = { amplitude: LONG, t_phi: LONG, axis_angle: LONG, 'a"]b': LONG };
    Object.keys(given).forEach((k) => { prov[k] = 'given'; });
    const mode = (body.macro_fields || {}).mode;
    return { ok: true, plan_sig: 'c' + JSON.stringify(body), pairs: body.pairs.map((pr) => ({
        pair: pr, ok: true, refusals: [], creates: 3, paths: ['a', 'b', 'c'], layout: 'copied',
        operations: { flux_pulse_control: { key: 'op_c_' + pr, channel: 'q1.z' } },
        fields: { flux_pulse_control: prov, macro: { mode: mode ? 'given' : LONG, link: 'kept reference' } },
        pulses: { flux_pulse_control: pulse },
        field_specs: { flux_pulse_control: { amplitude: { kind: 'float', label: 'amplitude', unit: '', optional: false },
                                             t_phi: { kind: 'int', label: 't_phi', unit: '', optional: false },
                                             'a"]b': { kind: 'float', label: 'a"]b', unit: '', optional: false },
                                             axis_angle: { kind: 'float', label: 'Axis angle', unit: 'rad', optional: true } } },
        settings: [{ key: 'link', value: '#./x', kind: 'str', source: 'kept reference', reference: true },
                   { key: 'mode', value: mode || 'fast', kind: 'str', source: mode ? 'given' : LONG, reference: false },
                   { key: 'note', value: null, kind: 'any', source: LONG, reference: false, optional: true }],
        model_gate: ['q1-2', 'cz_m'], reset: ['fidelity', 'extras'] })) };
}
const NO_CLASS = 'The gate class must be given by its class name. Pick a gate class from the list.';
const EMPTY_PART = 'The gate name cz_ has an empty part between underscores. Pick a name like cz_square.';
let bareRefused = false;            // the server's answer for the bare prefix
let planExtra = null;               // a test's own refusals: [reason, aboutClass]
function planOk(body) {
    if (!body.gate_class) return { ok: true, plan_sig: 'none', pairs: body.pairs.map((p) => ({ pair: p, ok: false, refusals: [NO_CLASS], class_refusals: [NO_CLASS] })) };
    if (planExtra) return { ok: true, plan_sig: 'x', pairs: body.pairs.map((p) => ({ pair: p, ok: false,
        refusals: planExtra.map((r) => r[0]), class_refusals: planExtra.filter((r) => r[1]).map((r) => r[0]),
        name_refusals: planExtra.filter((r) => r[2]).map((r) => r[0]) })) };
    if (bareRefused && body.gate_name === 'cz_') return { ok: true, plan_sig: 'bare', pairs: body.pairs.map((p) => ({ pair: p, ok: false,
        refusals: [EMPTY_PART], class_refusals: [], name_refusals: [EMPTY_PART] })) };
    if (body.gate_class === 'L.TwoFlux') return planCopied(body);
    return { ok: true, plan_sig: 'sig-' + JSON.stringify(body.pulses) + body.gate_name + body.pairs.join(','),
             pairs: body.pairs.map((p) => p === 'q9-9' ? { pair: p, ok: false, refusals: ['No such pair.'] } : {
                 pair: p, ok: /^cz_/.test(body.gate_name), refusals: /^cz_/.test(body.gate_name) ? [] : ['The gate name must start with cz_.'],
                 name_refusals: /^cz_/.test(body.gate_name) ? [] : ['The gate name must start with cz_.'],
                 creates: 2, paths: ['qubit_pairs.' + p + '.macros.' + body.gate_name, 'qubits.q1.z.operations.x'],
                 layout: 'known', operations: { flux_pulse_qubit: { key: 'op_' + p, channel: 'q1.z' } },
                 fields: { flux_pulse_qubit: { length: 'default', amplitude: ((body.pulses.flux_pulse_qubit || {}).fields || {}).amplitude !== undefined ? 'given' : 'default' } },
                 pulses: { flux_pulse_qubit: Object.assign({ __class__: 'P.SquarePulse', length: 100, amplitude: 0.1 },
                                                           ((body.pulses.flux_pulse_qubit || {}).fields || {})) } }) };
}
let lastAllowed = ['q1-2'];
function checkOk(body, opts) {
    lastAllowed = body.pairs.filter((p) => !(opts && opts.refuse === p));
    opts = opts || {};
    return { ok: true, status: 'ok', plan_sig: planOk(body).plan_sig, check_token: 'tok' + sent.length,
             pairs: body.pairs.map((p, i) => ({
                 pair: p, gate: body.gate_name, ok: true, refusals: [],
                 checks: [{ name: 'range', status: 'pass', message: 'Inside the output range.' }],
                 timeline: { lanes: [{ key: 'q1.z', segments: [] }], notes: [], cannot_draw: null },
                 tiles: [{ key: 'peak', label: 'Highest level', value: '0.1903', unit: 'V', note: 'on q1.z', tone: 'neutral' }],
                 intent: [{ field: 'flux_pulse_qubit', operation: 'op_' + p, channel: 'q1.z', lane_key: 'q1.z',
                            intended: { length: 100, length_source: 'default', amplitude: 0.2, amplitude_source: 'given' },
                            actual: { length_ns: 100, max_v: 0.18, min_v: 3.55865437559e-7, scale: 1 },
                            differs: { length: false, amplitude: true }, tones: { length: null, amplitude: 'warn' }, why: ['The class shapes the waveform.'] }],
                 paths: ['a', 'b'],
                 verdict: (opts.refuse === p) ? { allowed: false, state: 'not checked', reason: 'A gate named so exists.' }
                     : { allowed: true, state: opts.unchecked ? 'not checked' : 'verified', reason: opts.unchecked ? 'No environment.' : '' } })) };
}

/* Spectators in the answers, only when the body asks for them (the contract's shapes). */
const NOT_CAL = 'The phase shift of this spectator starts at 0 and is not calibrated. Calibrate it before relying on the gate.';
const SPECT_OPS = { q3: 1, q4: 2, q1: 1 };
const RANGE_NOTE = "The level on q4.z was not compared with its port's output range: the output range of port con1/1/4 is not known.";
function spectPlan(body, j) {
    if (!body.spectators) return j;
    (j.pairs || []).forEach((p) => {
        const sp = body.spectators[p.pair] || [];
        if (!sp.length) return;
        const bad = sp.filter((x) => x.level === undefined).map((x) => 'For the spectator ' + x.qubit + ': the level is required. Type a level in V.');
        if (bad.length) { p.ok = false; p.refusals = (p.refusals || []).concat(bad); return; }
        const op = body.gate_name + '_spectator_pulse';
        p.spectators = sp.map((x) => ({ qubit: x.qubit, line: x.qubit + '.z', operation: op, path: 'qubits.' + x.qubit + '.z.operations.' + op,
                                        length: x.length, level: x.level, phase_shift: 0.0, not_calibrated: NOT_CAL,
                                        ...(unitReach && x.unit !== undefined && unitReach[x.qubit] !== undefined ? { unit: x.unit, reachable: unitReach[x.qubit] } : {}),
                                        ...(x.qubit === 'q4' ? { range_not_compared: RANGE_NOTE } : {}) }));
        p.spectator_ops = {};
        sp.forEach((x) => { p.spectator_ops[x.qubit] = SPECT_OPS[x.qubit]; });
        p.paths = (p.paths || []).concat(p.spectators.map((x) => x.path));
    });
    return j;
}
function spectCheck(body, j) {
    if (!body.spectators) return j;
    (j.pairs || []).forEach((p) => {
        const sp = body.spectators[p.pair] || [];
        sp.forEach((x, k) => {
            p.timeline.lanes.push({ key: x.qubit + '.z', group: 'spectator', label: x.qubit + '.z', color_index: k + 1, segments: [] });
            p.intent.push({ field: 'spectator_qubits_control.' + x.qubit, group: 'spectator', lane_key: x.qubit + '.z', operation: body.gate_name + '_spectator_pulse',
                            intended: { length: x.length, length_source: 'given', amplitude: x.level, amplitude_source: 'given' },
                            actual: { length_ns: x.length, max_v: x.level, min_v: 0 }, tones: {}, why: [] });
        });
    });
    return j;
}
let replies = 0;
let holdDialog = null;
function respond(status, j) { return Promise.resolve({ ok: status < 400, status, json: () => { replies++; return Promise.resolve(j); }, text: () => Promise.resolve('') }); }
window.fetch = (url, opt) => {
    if (String(url).indexOf('/pulse/cz-gate/status') === 0) {
        const q = new URLSearchParams(String(url).split('?')[1]);
        statusAsked.push(q.get('pair') + '/' + q.get('gate'));
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('<div class="gate-status" data-row="' + q.get('pair') + '"><div class="gate-status-row"><code class="ui-chip">' + q.get('gate') + '</code></div></div>') });
    }
    if (String(url).indexOf('/state/drift') === 0) {
        const nx = driftSeq.length ? driftSeq.shift() : { state: driftState, unapplied: 0 };
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, sync: nx }) });
    }
    if (!opt || !opt.method) {
        if (failDialog) return Promise.reject(new Error('offline'));
        if (holdDialog) { const held = holdDialog; holdDialog = null; return held(); }
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(dialogHtml()) });
    }
    const body = JSON.parse(opt.body);
    const kind = url.split('/').pop();
    sent.push({ kind, body });
    const q = queue[kind].shift();
    if (kind === 'create' && holdCreate) { const h = holdCreate; holdCreate = null; return h.then(() => respond(200, { ok: true, created: lastAllowed.map((pp) => ({ pair: pp, gate: 'cz_x', state: 'verified', paths: ['a'] })), skipped: [], count: lastAllowed.length, tray_html: '', sync: createSync })); }
    if (kind === 'plan' && holdPlan) { const h = holdPlan; holdPlan = null; const ans = q ? q(body) : spectPlan(body, planOk(body)); return h.then(() => respond(200, ans)); }
    if (kind === 'check' && hold) { const h = hold; hold = null; return h.then(() => respond(200, q ? q(body) : spectCheck(body, checkOk(body)))); }
    if (q) return Promise.resolve(q(body)).then((r) => Array.isArray(r) ? respond(r[0], r[1]) : respond(200, r));
    if (kind === 'plan') return respond(200, spectPlan(body, planOk(body)));
    if (kind === 'slot-class' || kind === 'save-config') return respond(500, {});
    if (kind === 'check') return respond(200, spectCheck(body, checkOk(body)));
    return respond(200, { ok: true, created: lastAllowed.map((p) => ({ pair: p, gate: 'cz_x', state: 'verified', paths: ['a'] })), skipped: [], count: lastAllowed.length, tray_html: '', sync: createSync });
};
let driftState = 'mine';
let driftSeq = [];
const statusAsked = [];
let createSync = { auto_push: false, state: 'mine', unapplied: 1 };
const charts = [];
// the real chart script supplies the shared volt text; only its drawing is mocked
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'plot-theme.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'gate-timeline.js'), 'utf8'));
const realVoltTexts = window.GateTimeline && window.GateTimeline.voltTexts;
window.GateTimeline = { voltTexts: realVoltTexts, render: (host, data) => {
    if (host.closest('[hidden]')) throw new Error('Chart measured inside a hidden section');
    charts.push(data); return { destroy() {} };
} };
const ajax = [];
window.htmx = { ajax: (m, u, o) => ajax.push(u), trigger: () => {} };
window.alert = () => { throw new Error('alert used'); };
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'ui.js'), 'utf8'));
const originalAdd = window.addEventListener;
const originalRemove = window.removeEventListener;
const resizeListeners = new Set();
let resizeAdds = 0, resizeRemoves = 0;
window.addEventListener = function (type, listener, options) {
    if (type === 'resize') { resizeAdds++; resizeListeners.add(listener); }
    return originalAdd.call(this, type, listener, options);
};
window.removeEventListener = function (type, listener, options) {
    if (type === 'resize') { resizeRemoves++; resizeListeners.delete(listener); }
    return originalRemove.call(this, type, listener, options);
};
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'cz-gate.js'), 'utf8'));

let n = 0, failed = 0;
function ok(cond, label) { n++; if (cond) console.log('ok - ' + label); else { failed++; console.log('FAIL ' + label); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(5); await tick(300); for (let i = 0; i < 6; i++) await tick(5); };
const $ = (s) => doc.querySelector(s);
const prim = () => $('#czg-primary');
const count = (k) => sent.filter((s) => s.kind === k).length;
const alertText = () => ($('#czg-alert') || { textContent: '' }).textContent;
function type(input, v) { input.value = v; input.dispatchEvent(new window.Event('input', { bubbles: true })); }
function change(sel, v) { sel.value = v; sel.dispatchEvent(new window.Event('change', { bubbles: true })); }
function tableWidths(el, sw, cw) {
    Object.defineProperty(el, 'scrollWidth', { configurable: true, get: () => el.closest('[hidden]') ? 0 : sw });
    Object.defineProperty(el, 'clientWidth', { configurable: true, get: () => el.closest('[hidden]') ? 0 : cw });
}

(async () => {
    // Supply layout before rendering, including the browser's zero size under hidden ancestors.
    const proto = window.HTMLElement.prototype;
    const savedWidths = ['scrollWidth', 'clientWidth'].map((key) => Object.getOwnPropertyDescriptor(proto, key));
    let wide = true;
    ['scrollWidth', 'clientWidth'].forEach((key) => Object.defineProperty(proto, key, {
        configurable: true, get() {
            if (!this.hasAttribute('data-czg-table-scroll') || this.closest('[hidden]')) return 0;
            return key === 'scrollWidth' && wide ? 900 : 300;
        }
    }));
    const named = (w, name) => w && w.getAttribute('tabindex') === '0' && w.getAttribute('role') === 'region'
        && w.getAttribute('aria-label') === 'Scrollable table region: ' + name;
    const plain = (w) => w && !w.hasAttribute('tabindex') && !w.hasAttribute('role') && !w.hasAttribute('aria-label');
    try {
        openScrollFixture = true;
        await window.CzGate.open({ pair: 'q1-2' }); await settle();
        ok(named($('[data-czg-open-scroll-fixture]'), 'Open table'), 'open measures an existing visible scrolling table without a resize');
        openScrollFixture = false;
        ok($('#czg-sec-intent').hidden, 'first check starts with the intent section hidden');
        prim().click(); await settle();
        ok(named($('#czg-intent > [data-czg-table-scroll]'), 'Entered and actual'), 'first check measures the intent table after showing its section without a resize');
        wide = false;
        // A new check redraws the table while the section is already visible.
        type($('#czg-name'), 'cz_width'); await settle(); prim().click(); await settle();
        ok(plain($('#czg-intent > [data-czg-table-scroll]')), 'a fitting redrawn intent table has no region attributes');
        wide = true;
        type($('#czg-name'), 'cz_wider'); await settle(); prim().click(); await settle();
        ok(named($('#czg-intent > [data-czg-table-scroll]'), 'Entered and actual'), 'redraw measures a scrolling intent table without a resize');
        window.CzGate.close();
        await window.CzGate.open({ pair: 'q1-2' }); await settle();
        { const st = window.CzGate.state(); if (!st.data.classes.some((c) => c['class'] === 'L.TwoFlux')) st.data.classes.push(DATA.classes[2]); }
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'L.TwoFlux', kind: 'pick' } }));
        await settle();
        ok(named($('#czg-model-facts > [data-czg-table-scroll]'), 'From the model gate'), 'redraw measures the model table without a resize');
        window.CzGate.close();
        openScrollFixture = true;
        await window.CzGate.open({ pair: 'q1-2' }); await settle();
        ok(named($('[data-czg-open-scroll-fixture]'), 'Open table'), 'reopen measures an existing table without a resize');
        ok(resizeListeners.size === 1 && resizeAdds - resizeRemoves === 1, 'reopening leaves exactly one resize listener counted through add and remove');
        wide = false;
        window.dispatchEvent(new window.Event('resize'));
        ok(plain($('[data-czg-open-scroll-fixture]')), 'the resize listener updates the reopened dialog');
    } finally {
        window.CzGate.close();
        openScrollFixture = false;
        ['scrollWidth', 'clientWidth'].forEach((key, i) => {
            if (savedWidths[i]) Object.defineProperty(proto, key, savedWidths[i]); else delete proto[key];
        });
        window.addEventListener = originalAdd;
        window.removeEventListener = originalRemove;
        sent.length = 0; charts.length = 0;
    }
    const scrollAssertions = n;
    // row buttons: reachable, the row under them never opens
    let rowClicks = 0;
    $('#row').addEventListener('click', () => { rowClicks++; });
    ok($('#gate').tabIndex >= 0 && $('#detail').tabIndex >= 0 && $('#gate').type === 'button', 'row buttons are keyboard reachable buttons');
    $('#detail').click();
    ok(rowClicks === 1, 'Detail reaches the row (opens the inspector as the row does)');
    $('#qpulse').click();
    ok(ajax[0] === '/pulse/new?qubit=q1&channel=xy', '+ Pulse opens the pulse form for that qubit');
    $('#gate').focus();
    $('#gate').click();
    await settle();
    ok(rowClicks === 1, '+ Gate does not open the row');
    const d = $('#czg-dialog');
    ok(d && d.hasAttribute('open') && d.classList.contains('ui-dialog--xl'), 'the kit dialog opens');
    ok($('#czg-pair').value === 'q1-2', 'the pair is preselected');
    ok(doc.activeElement === $('#czg-name'), 'the gate name has the focus on open');
    ok(!$('#czg-empty').hidden && $('#czg-sec-checks').hidden && $('#czg-sec-intent').hidden, 'empty state: one sentence, no empty headings');
    ok(d.querySelectorAll('.ui-zone').length === 3 && !d.querySelector('.ui-zone .ui-zone')
       && [...d.querySelectorAll('.ui-zone-title')].map((t) => t.textContent).join() === 'Gate,Pulses,Result',
       'three zones (Gate, Pulses, Result), never a zone in a zone');
    ok($('#czg-zone-result').classList.contains('ui-zone--quiet') && $('#czg-zone-result').contains($('#czg-empty'))
       && $('#czg-empty').classList.contains('ui-zone-empty') && !$('#czg-zone-result').contains($('#czg-alert'))
       && !$('#czg-zone-result').contains($('#czg-pairs')),
       'before a check the Result zone is quiet with its one line; the banner and pair switch sit above it');
    ok(/^(1 line|\d+ lines)$/.test($('#czg-zone-pulses .ui-zone-sum').textContent),
       'the Pulses line count is the zone summary');
    ok($('.ui-slot.ui-line-1') && /^Qubit flux pulse/.test($('.ui-slot-head').textContent) && $('.ui-slot-chip').textContent === 'q1.z' && $('.ui-slot-head .ui-text-quiet').textContent === 'flux_pulse_qubit', 'slot: words, this pair\'s channel chip, line colour 1, field name quiet');
    ok(/control q1 \u2192 target q2/.test($('#czg-chips').textContent) && /moving qubit q1/.test($('#czg-chips').textContent), 'the head carries the pair as chips');
    ok(!$('#czg-chips').querySelector('input, select'), 'the mover is not an input');
    { const off = [...doc.querySelectorAll('#czg-class-list .ui-option[aria-disabled="true"]')]; ok(off.length > 0 && /SM does not know/.test(off[0].querySelector('.ui-option-desc').textContent) && $('#czg-class').classList.contains('ui-menu-trigger'), 'gate class is a kit menu; an unavailable class is a disabled row with its reason'); }
    ok(count('plan') === 1 && sent[0].body.pairs.join() === 'q1-2', 'the plan is asked on open');
    ok(prim().textContent === 'Check' && !prim().disabled, 'Check before a check');
    const hintShown = () => !!$('#czg-check-hint') && !$('#czg-check-hint').hidden
    && (prim().getAttribute('aria-describedby') || '').split(/\s+/).includes('czg-check-hint');
    ok($('#czg-check-hint') && !hintShown(), 'an enabled Check carries no held-Check hint');
    ok($('#czg-slots .ui-badge') && /default/.test($('#czg-slots').textContent), 'fields marked default');
    ok(!d.querySelector('[style]') && !d.querySelector('[onclick]'), 'no inline style or handler');

    // name validated by the plan
    type($('#czg-name'), 'xx');
    await settle();
    ok(/must start with cz_/.test($('#czg-name-why').textContent) && prim().disabled, 'a bad name is said and Check is off');
    type($('#czg-name'), 'cz_new');
    await settle();
    ok(!prim().disabled, 'a good name enables Check');

    // entered value
    const amp = $('input[data-czg-name="amplitude"]');
    type(amp, '0.2');
    await settle();
    ok(sent[sent.length - 1].body.pulses.flux_pulse_qubit.fields.amplitude === 0.2, 'an entered value is sent as a number');
    ok(Object.keys(sent[sent.length - 1].body.pulses.flux_pulse_qubit.fields).join() === 'amplitude', 'an untouched field is never sent as a given value');
    ok($('input[data-czg-name="amplitude"]').closest('.ui-field').querySelector('.ui-badge').textContent === 'entered', 'the entered field is marked entered');

    // busy guard
    let release;
    hold = new Promise((r) => { release = r; });
    prim().click();
    await tick(5);
    ok(prim().disabled && /Checking with the lab's code, about 10 s/.test($('#czg-status').textContent), 'busy: disabled + status text');
    ok(hintShown() && /valid gate plan and no invalid fields/.test($('#czg-check-hint').textContent),
       'a held Check says why, and the button is described by that hint');
    prim().click(); prim().click(); window.CzGate._check();
    ok(count('check') === 1, 'clicks while busy are ignored');
    release();
    await settle();
    ok(prim().textContent === 'Create gate' && !prim().disabled, 'after a passing check: Create gate');
    ok(!hintShown(), 'the held-Check hint never labels Create');
    ok(charts.length === 1 && charts[0].lanes[0].key === 'q1.z', 'the chart gets the server data');
    { const tile = $('#czg-tiles [data-tile="peak"]'); ok(tile && tile.querySelector('.ui-stat-value').firstChild.textContent === '0.1903'
       && /V/.test(tile.querySelector('.ui-stat-unit').textContent) && tile.querySelector('.ui-text-quiet').textContent === 'on q1.z',
       'a tile is the server\'s value, unit and quiet line, never a split sentence'); }
    ok($('#czg-checks .ui-result[data-check="range"]') && /Output range/.test($('#czg-checks').textContent) && !$('#czg-sec-checks').hidden && /1 of 1 passed/.test($('#czg-checks-sum').textContent), 'checks: chips in words, key as data-check, passes counted');
    ok($('#czg-alert .ui-verdict--strong.ui-verdict--ok') && /Ready to create/.test(alertText()), 'the banner says Ready to create');
    ok($('#czg-intent .ui-cell--warn') && /shapes the waveform/.test($('#czg-intent').textContent) && $('#czg-intent .ui-line-dot.ui-line-1') && /differ/.test($('#czg-intent-sum').textContent), 'a differing cell is marked warn, the row has its line dot, the why is under it');

    // an input change returns to Check and greys the evidence
    type($('input[data-czg-name="amplitude"]'), '0.3');
    await tick(5);
    ok(prim().textContent === 'Check' && $('#czg-evidence').classList.contains('czg-stale')
       && /Inputs changed, check again/.test($('#czg-status').textContent), 'input change: back to Check, evidence greyed');
    await settle();
    prim().click();
    await settle();
    ok(prim().textContent === 'Create gate', 'checked again');

    // two stale rounds, then stop
    for (let i = 0; i < 3; i++) queue.create.push(() => [409, { ok: false, stale: true, error: 'The chip changed.' }]);
    const checks0 = count('check');
    prim().click();
    await settle();
    ok(count('check') === checks0 + 1 && /Checked again/.test(alertText()) && prim().textContent === 'Create gate', 'stale 1: the check re-runs itself');
    prim().click();
    await settle();
    ok(count('check') === checks0 + 2, 'stale 2: the check re-runs itself');
    prim().click();
    await settle();
    ok(count('check') === checks0 + 2 && /The chip keeps changing/.test(alertText()) && prim().textContent === 'Check', 'stale 3: stops and says the chip keeps changing');
    ok(sent.filter((s) => s.kind === 'create').every((s) => Object.keys(s.body).sort().join() === 'check_token,plan_sig'), 'create sends only the token and the signature');

    // an error renders in the dialog
    queue.check.push(() => [500, { ok: false, error: 'Boom.' }]);
    prim().click();
    await settle();
    ok($('#czg-alert .ui-callout--danger') && /The check did not run/.test(alertText()) && /Press Check to try again/.test(alertText()), 'an error is a callout in the dialog saying what to do');

    // several pairs, one refused
    $('#czg-more').open = true;
    const box = $('#czg-more-list input[value="q2-3"]');
    box.checked = true;
    box.dispatchEvent(new window.Event('change', { bubbles: true }));
    await settle();
    ok(sent[sent.length - 1].body.pairs.join() === 'q1-2,q2-3' && /can be created/.test($('#czg-more-list').textContent), 'other pairs join the plan and say so');
    // a second pair's name refusal (marked in name_refusals) shows on its own row, in the server's words
    {
        const TAGGED = 'The pair q2-3 already has a gate named this way. Pick another gate name.';
        const amp = $('input[data-czg-name="amplitude"]');
        const was = amp.value;
        queue.plan.push((b) => { const j = planOk(b); j.pairs[1].ok = false;
            j.pairs[1].refusals = [TAGGED]; j.pairs[1].name_refusals = [TAGGED]; return j; });
        type(amp, was);
        await settle();
        const why = $('#czg-more-list [data-czg-more-row] input[value="q2-3"]').closest('[data-czg-more-row]')
            .querySelector('[data-czg-more-why]');
        ok(why.textContent === TAGGED && $('#czg-name-why').hidden,
           "the second pair name refusal shows in its row in the server's words, not under the first pair's Name");
        type(amp, was);
        await settle();
    }
    queue.check.push((b) => checkOk(b, { refuse: 'q2-3' }));
    prim().click();
    await settle();
    if (process.env.CZG_DEBUG) console.log('DBG', prim().textContent, '|', alertText(), '|', $('#czg-status').textContent, sent.slice(-2).map(s => s.kind));
    ok(prim().textContent === 'Create gate' && /1 of 2 cannot be created: q2-3/.test(alertText()) && /can still be created/.test(alertText()) && /A gate named so exists/.test(alertText()), 'one pair refused: named in the banner, the rest can still be created');
    ok(/A gate named so exists/.test($('#czg-writes').textContent),
       'a refused verdict reason is named in What will be written');
    ok(!$('#czg-pairs').hidden && $('#czg-pairs').querySelectorAll('[data-czg-pair-idx]').length === 2 && $('#czg-pairs .ui-segmented-on').textContent.indexOf('q2-3') === 0 && $('#czg-pairs .ui-badge--danger'), 'pair switch: one option per pair with its mark, the failing pair shown first');
    ok(!$('#czg-zone-result').classList.contains('ui-zone--quiet') && $('#czg-empty').hidden, 'after a check answer the Result zone is not quiet');
    ok([...$('#czg-pairs').querySelectorAll('[data-czg-pair-idx]')].every((b) => ![...b.childNodes].some((n) => n.nodeType === 3 && /^\s*$/.test(n.textContent))),
       'pair switch: label and mark are separated by the kit gap, not a collapsible space');
    $('#czg-pairs [data-czg-pair-idx="0"]').click();
    await settle();
    ok($('#czg-pairs .ui-segmented-on').textContent.indexOf('q1-2') === 0 && /op_q1-2/.test($('#czg-intent').textContent), 'choosing a pair shows its own evidence');
    ok(/0.18 V/.test($('#czg-intent').textContent) && !/0.1800/.test($('#czg-intent').textContent), 'numbers in the shortest exact form');
    ok(typeof realVoltTexts === 'function' && /355\.87 nV/.test($('#czg-intent').textContent)
       && !/\de-\d/.test($('#czg-intent').textContent),
       'a level read off the samples prints as the chart prints it (rounded digits), never as a raw float');

    // not checked wording
    type($('input[data-czg-name="amplitude"]'), '0.25');
    await settle();
    queue.check.push((b) => checkOk(b, { unchecked: true }));
    // the busy form drops the focus to the page (Chrome): the answer gives it back to Create
    if (doc.activeElement && doc.activeElement.blur) doc.activeElement.blur();
    prim().click();
    await settle();
    ok(doc.activeElement === prim() && /^Create/.test(prim().textContent),
       'after Check the focus is back in the dialog, on Create');
    ok(/without the lab check/.test(prim().textContent) && /Create 2 gates, 2 without the lab check/.test(prim().textContent) && $('#czg-alert .ui-verdict--strong.ui-verdict--warn'), 'not checked: warn banner and the button counts what it will create');

    // create
    prim().click();
    await settle();
    ok(/In the working copy only: press Apply to write it to the chip\./.test(alertText()) && !/Not on the chip yet/.test(alertText())
       && /In the working copy only/.test($('#czg-status').textContent) && $('#czg-status-slot'),
       'Auto-Sync off: banner and foot say working copy only; the status slot is there');
    ok($('#czg-status-slot').querySelectorAll('.gate-status').length === 2 && $('#czg-status-slot .gate-status[data-row="q1-2"]') && $('#czg-status-slot .gate-status[data-row="q2-3"]')
       && statusAsked.length === 2,
       'after Create the status slot shows the status slice\'s own row for the created gate');
    ok($('#czg-writes-title').textContent === 'What was written' && !/not written/.test($('#czg-writes').textContent)
       && $('#czg-writes [data-czg-writes-pair="q1-2"]') && $('#czg-writes [data-czg-writes-pair="q2-3"]'),
       'after Create the panel is What was written, from the create answer, both pairs, no re-plan');
    ok(!$('#czg-evidence').classList.contains('czg-stale'), 'after Create the result is not dimmed: it shows what was written');
    ok([...doc.querySelectorAll('#czg-status-slot .gate-status-row')].map((r) => r.firstElementChild.getAttribute('data-czg-status-pair')).join() === 'q1-2,q2-3',
       'two created gates: each status row starts with its pair');
    ok($('#czg-open-pair').textContent === 'Open ' + $('#czg-pairs .ui-segmented-on').firstChild.textContent.trim()
       && /^Open q\d-\d$/.test($('#czg-open-pair').textContent), 'two created pairs: the button names the pair it opens');
    ok(!$('#czg-open-pair').hidden && prim().textContent === 'Close' && !$('#czg-form').contains(doc.activeElement), 'after create: Open the pair and Close; the focus is not left in the hidden form');
    prim().click();
    await settle();
    ok(!$('#czg-dialog'), 'Close closes');

    // the untouched prefill is not an error: neutral on open, the sentence from the first edit or press
    {
        bareRefused = true;
        const calm = () => $('#czg-name-why').hidden && !$('#czg-name').hasAttribute('aria-invalid') && !$('#czg-name-hint').hidden;
        const hasClass = () => !!$('#czg-name').closest('.ui-field').querySelector('.ui-field-error:not([hidden])');
        await window.CzGate.close(); await window.CzGate.open({ pair: 'q1-2' }); await settle();
        ok($('#czg-name').value === 'cz_' && calm() && !hasClass() && prim().disabled,
           'fresh open: the bare prefix shows no error and no red field, Check is still off');
        type($('#czg-name'), 'cz_a'); type($('#czg-name'), 'cz_'); await settle();
        ok($('#czg-name-why').textContent === EMPTY_PART && $('#czg-name').getAttribute('aria-invalid') === 'true'
           && $('#czg-name-hint').hidden && prim().disabled, 'after typing the bare prefix again: the refusal is said');
        $('#czg-name').dispatchEvent(new window.Event('blur', { bubbles: true })); await settle();
        ok($('#czg-name-why').textContent === EMPTY_PART, 'the field stays refused after the person leaves it');
        await window.CzGate.close(); await window.CzGate.open({ pair: 'q1-2' }); await settle();
        ok(calm() && prim().disabled, 'reopened after a refusal: neutral again');
        window.CzGate._check(); await settle();
        ok($('#czg-name-why').textContent === EMPTY_PART && $('#czg-name').getAttribute('aria-invalid') === 'true',
           'a Check press on the bare prefix shows the refusal');
        bareRefused = false;
        await window.CzGate.close();
    }

    // reopened after a create: a clean dialog from fresh server data
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    ok($('#czg-dialog') && !$('#czg-form').hidden && prim().textContent === 'Check' && $('#czg-open-pair').hidden
       && !$('#czg-empty').hidden && $('#czg-sec-chart').hidden && !$('#czg-evidence').classList.contains('czg-stale')
       && !/Created/.test(alertText()), 'reopened after create: form back, Check, no evidence');
    // a failed check is "Cannot be created yet", in the evidence column
    queue.check.push((b) => { const j = checkOk(b, { refuse: 'q1-2' }); return j; });
    prim().click();
    await settle();
    ok(/Cannot be created yet/.test(alertText()) && !/Not created/.test(alertText()) && $('#czg-evidence').contains($('#czg-alert'))
       && prim().textContent === 'Check', 'a refused check: Cannot be created yet, on the right, button Check');
    // every check skipped for one reason: one callout, no table
    queue.check.push((b) => { const j = checkOk(b, { unchecked: true }); j.pairs.forEach((p) => { p.checks = ['load_config', 'ops', 'range'].map((n) => ({ name: n, status: 'skipped', message: 'No environment is selected.' })); }); return j; });
    type($('#czg-name'), 'cz_again');
    await settle();
    prim().click();
    await settle();
    ok(!$('#czg-checks table') && $('#czg-sec-checks').hidden && /No environment/.test(alertText()), 'all skipped for one reason: said once, no table');
    // a late answer for a closed dialog is ignored
    hold = new Promise((r) => { release = r; });
    type($('#czg-name'), 'cz_late');
    await settle();
    prim().click();
    await tick(5);
    window.CzGate.close();
    await window.CzGate.open({ pair: 'q2-3' });
    await settle();
    release();
    await settle();
    ok(prim().textContent === 'Check' && $('#czg-sec-chart').hidden && !/Inputs changed/.test($('#czg-status').textContent), 'an answer for the closed dialog does not land in the new one');
    window.CzGate.close();

    // a copied layout: server kinds, short source tags, optional fields folded, gate settings, model facts
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    { const st = window.CzGate.state(); if (!st.data.classes.some((c) => c['class'] === 'L.TwoFlux')) st.data.classes.push(DATA.classes[2]); }
    const menu = $('#czg-class-host .ui-menu');
    menu.dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'L.TwoFlux', kind: 'pick' } }));
    await settle(); await settle();
    const ampIn = $('input[data-czg-field="flux_pulse_control"][data-czg-name="amplitude"]');
    const badge = ampIn && ampIn.closest('.ui-field').querySelector('.ui-badge');
    ok(badge && badge.textContent === 'copied' && badge.title === 'copied from the model gate' && ampIn.closest('.ui-field--inline'),
       'copied layout: a short "copied" tag with the sentence as its title, inline fields');
    ok($('.ui-disclosure input[data-czg-name="axis_angle"]') && !$('#czg-slots > .ui-slot .ui-field-stack input[data-czg-name="axis_angle"]'),
       'a null optional field sits folded under Optional fields');
    type(ampIn, '0.01');
    await settle();
    ok(!ampIn.hasAttribute('aria-invalid') && sent.filter((x) => x.kind === 'plan').pop().body.pulses.flux_pulse_control.fields.amplitude === 0.01,
       'a float field holding 0.0 accepts 0.01 (the kind comes from the server, never from the digits)');
    const tphi = $('input[data-czg-field="flux_pulse_control"][data-czg-name="t_phi"]');
    type(tphi, '2.5');
    ok(tphi.getAttribute('aria-invalid') === 'true' && /Type a whole number/.test($('#czg-status').textContent), 'an int field refuses 2.5 with the reason');
    type(tphi, '4');
    await settle();
    ok($('#czg-from').options[0].textContent === "The model gate's values" && !/Defaults of the pulse class/.test($('#czg-from').textContent),
       'a copied layout starts from the model gate\'s values');
    ok($('#czg-settings .ui-disclosure input[data-czg-setting="note"]'), 'a null setting sits folded under Optional settings');
    ok(!$('#czg-settings-panel').hidden && $('#czg-settings input[data-czg-setting="mode"]') && /kept reference/.test($('#czg-settings').textContent),
       'gate settings: each key a field, a reference listed read-only');
    type($('#czg-settings input[data-czg-setting="mode"]'), 'slow');
    await settle();
    ok(sent.filter((x) => x.kind === 'plan').pop().body.macro_fields.mode === 'slow', 'a changed setting goes to the plan as macro_fields');
    ok(!$('#czg-model-panel').hidden && /cz_m/.test($('#czg-model-facts').textContent) && /fidelity/.test($('#czg-model-facts').textContent),
       'before the first check the model gate and what is not inherited are shown');
    { const mw = $('#czg-model-facts > [data-czg-table-scroll]');
      ok(mw && mw.querySelector(':scope > table.ui-table') && mw.classList.contains('ui-xscroll-table') && !mw.hasAttribute('tabindex'),
         'the model gate table sits in the scrolling wrapper, plain layout while it fits');
      tableWidths(mw, 900, 300);
      window.dispatchEvent(new window.Event('resize'));
      ok(mw.getAttribute('tabindex') === '0' && /^Scrollable table region: /.test(mw.getAttribute('aria-label')),
         'the model gate table wrapper becomes a named tab stop once it scrolls sideways');
      tableWidths(mw, 300, 300);
      window.dispatchEvent(new window.Event('resize')); }
    queue.check.push((b) => { const j = checkOk(b, { refuse: 'q1-2' }); j.pairs.forEach((p) => {
        p.checks = [{ name: 'ops', status: 'fail', message: 'The gate cz_x does not run with the lab\'s code. The lab\'s error: \u201cValueError: flat lengths differ.\u201d' }]
            .concat(['length', 'preview', 'timeline', 'range'].map((n) => ({ name: n, status: 'skipped', message: 'Not checked: the gate cz_x does not run.' })));
        p.verdict.reason = p.checks[0].message; p.verdict.headline = p.checks[0].message;
        p.intent = [{ field: 'flux_pulse_control', operation: 'op_c', channel: 'q1.z', intended: { length: null, length_source: 'set by the class', amplitude: 0.01, amplitude_source: 'given' },
                      actual: null, differs: { length: false, amplitude: false }, tones: {}, why: ['The check did not place this pulse, so there is nothing to compare.'] }];
        p.timeline = { lanes: [], notes: [], cannot_draw: 'cannot draw: the gate does not run with the lab\'s code, so there is no config to draw' }; }); return j; });
    prim().click();
    await settle();
    ok(/not compared/.test($('#czg-intent-sum').textContent) && $('#czg-intent').querySelectorAll('p').length === 1,
       'nothing compared: the head says so, one line under it');
    ok(/The gate runs with the lab's code/.test($('#czg-checks').textContent) && $('#czg-model-panel').hidden, 'the failing chip is named for the gate running');
    ok($('#czg-checks').querySelectorAll('.ui-fail-row').length === 1 && [...$('#czg-checks').querySelectorAll('p.ui-text-quiet')].some((q) => q.textContent === '4 checks were not run because the gate does not run.'),
       'a lab refusal: one red row, then one quiet line for the four checks not run');
    ok(/\u201d Change the inputs and check again\./.test(alertText()), 'the lab\'s sentence is quoted, then SM\'s sentence');
    // a chip (or class file) that does not load: the inputs are not what to change
    queue.check.push((b) => { const j = checkOk(b, { refuse: 'q1-2' }); j.pairs.forEach((p) => {
        p.verdict.reason = 'The chip as it is does not load or build its config with the lab\'s code,'
            + ' so this gate was not checked. The lab\'s error: SyntaxError: invalid syntax (MyGate.py, line 3)';
        p.verdict.headline = p.verdict.reason; }); return j; });
    prim().click();
    await settle();
    ok(/fix what the lab's error names \(a class file or the chip\), then press Check again\./.test(alertText())
       && !/Change the inputs/.test(alertText()),
       'a chip or class file that does not load: the verdict never asks to change the inputs');
    ok(/fix what the lab's error names/.test($('#czg-status').textContent)
       && !/Change the inputs/.test($('#czg-status').textContent),
       'a chip or class file that does not load: the footer says the same next step as the alert');
    window.CzGate.close();

    // the created banner follows the app's sync state read after the create's entries were counted
    const CASES = [
        ['push: unapplied 3 then synced 0', { auto_push: true, auto_pull: false, state: 'mine', unapplied: 3, readable: true },
         [{ state: 'mine', unapplied: 3 }, { state: 'synced', unapplied: 0 }],
         /Auto-Sync is on: writing it to the chip\./, /Written to the chip by Auto-Sync\./],
        ['push: the create response already synced 0', { auto_push: true, auto_pull: false, state: 'synced', unapplied: 0, readable: true },
         [], /Written to the chip by Auto-Sync\./, null],
        ['push: nothing unapplied but not synced', { auto_push: true, auto_pull: false, state: 'live', unapplied: 0, readable: true },
         [], /Auto-Sync is on: the top bar shows when it reaches the chip./, null],
        ['pull only', { auto_push: false, auto_pull: true, state: 'mine', unapplied: 3, readable: true },
         [], /In the working copy only: press Apply to write it to the chip\./, null],
        ['unreadable', { readable: false, auto_push: false, state: null, unapplied: 0 },
         [], /Auto-Sync is on: the top bar shows when it reaches the chip\./, null],
    ];
    for (const [label, cs, seq, first, later] of CASES) {
        createSync = cs;
        driftSeq = seq.slice();
        driftState = 'synced';
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        type($('#czg-name'), 'cz_s' + label.length);
        await settle();
        queue.check.push((b) => { const j = checkOk(b); j.sync = cs; return j; });
        prim().click();
        await settle();
        const foot = $('#czg-status').textContent;
        ok(cs.auto_push ? /Auto-Sync is on: Create also writes the chip\./.test(foot) : !/Create also writes the chip/.test(foot),
           label + ': the foot note before Create says Create writes the chip only under push');
        prim().click();
        await settle();
        ok(first.test(alertText()) && first.test($('#czg-status').textContent), label + ': banner and foot say ' + first);
        if (label === 'pull only') ok(/pull only, so it does not write this/.test(alertText()), 'pull only: the quiet line says Auto-Sync does not write this');
        if (later) {
            await tick(1700); await settle(); await tick(1700); await settle();
            ok(later.test(alertText()) && !/writing it to the chip/.test(alertText()), label + ': after the state shows the entries then synced, it says ' + later);
        } else {
            ok(!/Written to the chip/.test(alertText()) || /already synced/.test(label), label + ': never "written" without a counted state');
        }
        window.CzGate.close();
    }
    createSync = { auto_push: false, state: 'mine', unapplied: 1 };

    // ---- review fixes (client) ----
    const field = (f, n) => [...doc.querySelectorAll('#czg-dialog input[data-czg-field]')].find((i) => i.dataset.czgField === f && i.dataset.czgName === n);
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    type($('#czg-name'), 'cz_rev');
    await settle();
    const ampR = field('flux_pulse_qubit', 'amplitude');
    type(ampR, '0,1');
    await settle(); await tick(600); await settle();
    const ampNow = field('flux_pulse_qubit', 'amplitude');
    const errLine = ampNow && ampNow.closest('.ui-field').querySelector('.ui-field-error');
    ok(ampNow.getAttribute('aria-invalid') === 'true' && errLine && errLine.textContent === 'Use a dot for decimals.'
       && (ampNow.getAttribute('aria-describedby') || '').indexOf(errLine.id) >= 0 && prim().disabled,
       'invalid "0,1": the error line stays through re-renders, tied by aria-describedby, Check off');
    for (const [txt, why] of [['0x10', 'Type a number.'], ['', 'Type a number.'], ['0.1.2', 'Type a number.'], ['9'.repeat(400), 'Type a number.']]) {
        type(field('flux_pulse_qubit', 'amplitude'), txt);
        await settle();
        ok(field('flux_pulse_qubit', 'amplitude').closest('.ui-field').querySelector('.ui-field-error').textContent === why && prim().disabled,
           'invalid ' + JSON.stringify(txt.slice(0, 8)) + ': ' + why);
    }
    const lenIn = field('flux_pulse_qubit', 'length');
    type(lenIn, '2.5');
    await settle();
    ok(field('flux_pulse_qubit', 'length').closest('.ui-field').querySelector('.ui-field-error').textContent === 'Type a whole number.', '2.5 in an int field: Type a whole number.');
    type(field('flux_pulse_qubit', 'length'), '48');
    type(field('flux_pulse_qubit', 'amplitude'), '-0');
    await settle();
    ok(!prim().disabled && !$('#czg-dialog .ui-field-error:not([hidden])') && $('#czg-status').textContent === '' && sent.filter((x) => x.kind === 'plan').pop().body.pulses.flux_pulse_qubit.fields.amplitude === 0,
       'valid again: errors gone, status cleared, -0 sent as 0, Check on');
    // C3 + C2: a create in flight: the form is inert, X / Escape do not close, a late plan answer is ignored
    prim().click();
    await settle();
    let releaseCreate;
    holdCreate = new Promise((r) => { releaseCreate = r; });
    prim().click();
    await tick(5);
    ok($('#czg-form').inert === true, 'during a create the form is inert');
    ok(window.CzGate.close() === false && $('#czg-dialog'), 'close() refuses while a create is in flight');
    $('#czg-dialog .ui-dialog-head [data-czg-close]').click();
    doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await tick(5);
    ok($('#czg-dialog') && $('#czg-dialog .ui-dialog-head [data-czg-close]').disabled, 'a create in flight: X and Escape do not close, X is disabled');
    releaseCreate();
    await settle();
    ok(/Created/.test(alertText()), 'the create answer lands in the dialog');
    { const nPlan = count('plan');
      window.CzGate._plan();
      await settle();
      ok(/Created/.test(alertText()) && count('plan') === nPlan, 'after Create no plan is asked and the Created banner stays'); }
    window.CzGate.close();
    // C4 + C9 + C7: two pairs, the first refused, one pair without a verdict
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    $('#czg-more').open = true;
    const box2 = $('#czg-more-list input[value="q2-3"]');
    box2.checked = true;
    box2.dispatchEvent(new window.Event('change', { bubbles: true }));
    type($('#czg-name'), 'cz_two');
    await settle();
    queue.check.push((b) => checkOk(b, { refuse: 'q1-2' }));
    prim().click();
    await settle();
    ok(/not written:/.test($('#czg-writes').textContent) && /q2-3/.test($('#czg-writes').textContent) && /2 objects|1 object/.test($('#czg-writes-sum').textContent) === false
       || (/not written:/.test($('#czg-writes').textContent) && $('#czg-writes').querySelectorAll('.ui-path').length === 2),
       'writes: the refused pair is "not written", the other pair\'s objects are listed');
    const opt1 = $('#czg-pairs [data-czg-pair-idx="1"]');
    opt1.focus();
    opt1.click();
    await settle();
    ok($('#czg-pairs').contains(doc.activeElement), 'the pair switch keeps keyboard focus');
    type($('#czg-name'), 'cz_nov');
    await settle();
    queue.check.push((b) => { const j = checkOk(b); delete j.pairs[0].verdict; return j; });
    prim().click();
    await settle();
    ok(/no verdict from the server/.test($('#czg-dialog').textContent) && !/TypeError/.test(alertText()), 'a pair without a verdict is said so, never a TypeError');
    // C5: Escape with a kit menu open closes the menu only; Escape on a worked dialog asks once
    const esc = () => doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    const takeIt = (e) => e.preventDefault();
    doc.addEventListener('keydown', takeIt, { capture: true, once: true });
    esc();
    await tick(5);
    ok($('#czg-dialog') && !/Discard this gate/.test($('#czg-top').textContent), 'an Escape a kit menu took does not close the dialog');
    esc();
    await tick(5);
    ok($('#czg-dialog') && /Discard this gate/.test($('#czg-top').textContent), 'Escape on a worked dialog asks once');
    esc();
    await tick(5);
    ok(!$('#czg-dialog'), 'the second Escape closes');
    // C6: a drag that ends on the backdrop does not close
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    $('#czg-name').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    $('#czg-dialog').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await tick(5);
    ok($('#czg-dialog'), 'a drag from an input onto the backdrop does not close');
    $('#czg-dialog').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    $('#czg-dialog').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await tick(5);
    ok(!$('#czg-dialog'), 'down and up on the backdrop closes an untouched dialog');
    // C8 + A2: a gate setting keeps focus; a field named with a quote and a bracket works
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    { const st = window.CzGate.state(); if (!st.data.classes.some((c) => c['class'] === 'L.TwoFlux')) st.data.classes.push(DATA.classes[2]); }
    $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'L.TwoFlux', kind: 'pick' } }));
    await settle(); await settle();
    const modeIn = $('#czg-settings input[data-czg-setting="mode"]');
    modeIn.focus();
    type(modeIn, 'slowest');
    await settle(); await settle();
    ok(doc.activeElement && doc.activeElement.dataset.czgSetting === 'mode', 'typing in a gate setting keeps its focus after the plan answer');
    const odd = field('flux_pulse_control', 'a"]b');
    odd.focus();
    type(odd, '2.25');
    await settle(); await settle();
    ok(doc.activeElement && doc.activeElement.dataset.czgName === 'a"]b' && sent.filter((x) => x.kind === 'plan').pop().body.pulses.flux_pulse_control.fields['a"]b'] === 2.25,
       'a field named a"]b renders, takes typing and keeps focus');
    window.CzGate.close(); await settle();
    if ($('#czg-dialog')) { $('#czg-top') && $('#czg-top').querySelector('[data-czg-discard]') && $('#czg-top').querySelector('[data-czg-discard]').click(); }
    // A6: evaluated twice, one set of listeners
    const firstApi = window.CzGate;
    window.eval(fs.readFileSync(path.join(ROOT, 'static', 'cz-gate.js'), 'utf8'));
    ok(window.CzGate === firstApi, 'loaded twice: the first instance stays (mutation: drop the guard)');
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    const before = sent.filter((x) => x.kind === 'plan').length;
    type($('#czg-name'), 'cz_once');
    await settle();
    ok(sent.filter((x) => x.kind === 'plan').length - before === 1, 'loaded twice: one plan request per change');
    window.CzGate.close(); await settle();
    if ($('#czg-dialog')) { const dd = $('#czg-top').querySelector('[data-czg-discard]'); if (dd) dd.click(); }
    await settle();

    // ---- round 2 pins (each names the one-line mutation that turns it red) ----
    const fieldOf = (f, n) => [...doc.querySelectorAll('#czg-dialog input[data-czg-field]')].find((i) => i.dataset.czgField === f && i.dataset.czgName === n);
    // R14 (mutation: drop the alertBox line in inputsChanged, or clear every alert in renderPlan)
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    type($('#czg-name'), 'cz_r14');
    await settle();
    prim().click();
    await settle();
    type(fieldOf('flux_pulse_qubit', 'amplitude'), '0.33');
    await tick(5);
    const r14a = $('#czg-alert .ui-callout--note') && /Inputs changed: press Check again\./.test(alertText()) && !/Ready/.test(alertText());
    await settle();
    ok(r14a && $('#czg-alert .ui-callout--note') && !/Ready/.test(alertText()), 'inputs changed after a check: a note banner at once, still there after the plan answer, no "ready"');
    // the note names the Check press only while Check can follow (mutation: changedAlert ignores the marked field)
    const lenWas = fieldOf('flux_pulse_qubit', 'length').value;
    type(fieldOf('flux_pulse_qubit', 'length'), '2.5');
    await settle();
    ok(prim().disabled && /Inputs changed: fix the marked field, then press Check\./.test(alertText())
       && !/press Check again/.test(alertText()),
       'a marked field after a check: the note asks to fix it, never "press Check again"');
    ok(/Fix the marked field, then press Check\./.test($('#czg-status').textContent)
       && !/check again/.test($('#czg-status').textContent),
       'a marked field after a check: the status line under the buttons agrees with the note');
    ok(!hintShown() && doc.querySelectorAll('#czg-check-hint').length === 1,
       'a marked field: its own line explains the held Check, the general hint steps aside');
    const lenBadge = fieldOf('flux_pulse_qubit', 'length').closest('.ui-field')
        .querySelector('.ui-field-head .ui-badge');
    ok(!lenBadge || !/default|entered|copied/.test(lenBadge.textContent),
       'a field holding text that is not used claims no source (never "default" beside it)');
    type(fieldOf('flux_pulse_qubit', 'length'), lenWas);
    await settle();
    ok(!prim().disabled && /Inputs changed: press Check again\./.test(alertText()),
       'the field fixed: the note says press Check again');
    // a check whose output range failed puts the focus on the level field (mutation: drop rng in keepCheckFocus)
    queue.check.push((b) => {
        const j = checkOk(b);
        j.pairs.forEach((p) => {
            p.checks = [{ name: 'range', status: 'fail', message: 'Over the output range.' }];
            p.timeline.lanes = [{ key: 'q1.z', segments: [{ flags: ['over_range'], peak_v: 3, min_v: 0 }] }];
            p.verdict = { allowed: false, state: 'not checked',
                          reason: "q1.z would play 3 V. This port's limit is 2.5 V." };
        });
        return j;
    });
    prim().focus();
    prim().click();
    await settle();
    ok(prim().textContent === 'Check' && doc.activeElement === fieldOf('flux_pulse_qubit', 'amplitude'),
       'an output-range failure: the focus goes to the level field, not left on Check');
    // G1 backdrop while busy (mutation: drop "&& !S.busy" from the backdrop close)
    hold = new Promise((r) => { release = r; });
    prim().click();
    await tick(5);
    ok(fieldOf('flux_pulse_qubit', 'amplitude').disabled && $('#czg-form').getAttribute('aria-disabled') === 'true', 'during a check every form control wears the disabled state');
    $('#czg-dialog').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    $('#czg-dialog').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await tick(5);
    ok($('#czg-dialog') && !$('#czg-dialog [data-czg-discard]'), 'a backdrop press during a check does nothing: no close, no question');
    release();
    await settle();
    ok(!fieldOf('flux_pulse_qubit', 'amplitude').disabled, 'after the check the controls are enabled again');
    // F writes follow the sync truth (mutation: syncLine ignores auto_push)
    type($('#czg-name'), 'cz_r4');
    await settle();
    queue.check.push((b) => { const j = checkOk(b); j.sync = { auto_push: true, auto_pull: false, readable: true }; return j; });
    prim().click();
    await settle();
    ok(/Auto-Sync also writes the chip/.test($('#czg-writes').textContent), 'writes panel: Auto-Sync armed is said there');
    // D partial create (mutation: drop the partial branch)
    queue.create.push(() => [500, { ok: false, partial: true, error: 'The create failed part way and could not be undone; press Ctrl+Z.' }]);
    prim().click();
    await settle();
    ok($('#czg-alert .ui-verdict--danger') && /could not be undone/.test(alertText()) && !/Created/.test(alertText())
       && !$('#czg-status-slot').children.length, 'a partial create: the server sentence in danger, never "Created", no status row');
    window.CzGate.close();
    // R12 mixed verdicts (mutation: label without the count, or the "And N more" tail removed)
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    type($('#czg-name'), 'cz_r12');
    await settle();
    queue.check.push((b) => {
        const j = checkOk(b);
        const base = j.pairs[0];
        j.pairs = ['p1', 'p2', 'p3', 'p4', 'p5'].map((id, k) => Object.assign({}, base, { pair: id,
            verdict: k === 0 ? { allowed: true, state: 'verified', reason: '' } : { allowed: true, state: 'not checked', reason: 'Reason ' + id + '.' } }));
        return j;
    });
    prim().click();
    await settle();
    ok(prim().textContent === 'Create 5 gates, 4 without the lab check' && /p2: Reason p2\./.test(alertText()) && /p4: Reason p4\./.test(alertText())
       && !/Reason p5/.test(alertText()) && /And 1 more\./.test(alertText()), 'mixed: the button counts, the banner names three reasons then "And 1 more."');
    type($('#czg-name'), 'cz_r12b');
    await settle();
    // the shared cases: the banner and the summary read the server's outcome
    for (const name of ['partly', ...Object.keys(OUTCOME_CASES).filter((n) => n !== 'partly')]) {
        const cs = OUTCOME_CASES[name];
        if (name !== 'partly') { type($('#czg-name'), 'cz_oc_' + name); await settle(); }
        const scrolled = [];
        window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.id); };
        queue.check.push((b) => { const j = checkOk(b); j.pairs.forEach((x) => { x.outcome = cs.outcome;
            x.checks = Object.entries(cs.result.gates[0].checks).map(([n, c]) => ({ name: n, status: c.status, message: c.message })); }); return j; });
        prim().click();
        await settle();
        const chips = [...$('#czg-checks').querySelectorAll('.ui-result')].map((c) => ({
            state: c.classList.contains('ui-result--skip') ? 'skip'
                : c.classList.contains('ui-result--fail') ? 'fail' : 'pass',
            text: c.textContent,
        }));
        require('assert').deepStrictEqual(chips, cs.display.chips);
        ok(true, 'shared case chips match the fixture: ' + name);
        ok($('#czg-checks-sum').textContent === cs.display.summary,
            'shared case summary matches the fixture: ' + name);
        const lines = [...$('#czg-checks').querySelectorAll('p.ui-text-quiet')].map((p) => p.textContent);
        require('assert').deepStrictEqual(lines, cs.display.quiet_lines);
        ok(true, 'shared case quiet lines match the fixture: ' + name);
        if (name === 'partly') {
            // a check that could not run: the warn state, never the green tick
            ok(!!$('#czg-alert .ui-verdict--warn') && !$('#czg-alert .ui-verdict--ok') && !/Ready to create/.test(alertText())
               && /not every check ran/.test(alertText()), 'shared case partly: the warn banner, not the ready tick');
            ok(/5 checks passed, 1 could not run: preview comparison \(no preview for this pulse class\)\./.test(alertText())
               && !/every check passed/.test(alertText()) && !/not checked/i.test(alertText()), 'shared case partly: 5 passed, 1 could not run, with the reason; never every check passed');
            ok(prim().textContent === 'Create gate', 'shared case partly: it may be created');
            { const cols = $('#czg-dialog .czg-cols'); const was = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'clientWidth');
              Object.defineProperty(cols, 'clientWidth', { configurable: true, get: () => 900 }); window.dispatchEvent(new window.Event('resize'));
              const narrow = $('#czg-dialog').hasAttribute('data-czg-stacked');
              Object.defineProperty(cols, 'clientWidth', { configurable: true, get: () => 1300 }); window.dispatchEvent(new window.Event('resize'));
              ok(narrow && !$('#czg-dialog').hasAttribute('data-czg-stacked') && was !== null, 'a narrow dialog is marked stacked (banner above the form), a wide one is not'); }
            ok(scrolled.indexOf('czg-alert') >= 0, 'after a check answer the banner is scrolled into view (stacked: it sits above the form)');
        } else if (name === 'checked') {
            ok(/every check passed/.test(alertText()),
               'shared case checked: every check passed, 6 of 6');
            ok(!!$('#czg-alert .ui-verdict--ok') && /Ready to create/.test(alertText()), 'shared case checked: the green ready banner');
        }
    }
    type($('#czg-name'), 'cz_old_outcome');
    await settle();
    queue.check.push((b) => { const j = checkOk(b); j.pairs.forEach((x) => {
        x.outcome = { ...OUTCOME_CASES.failed.outcome }; delete x.outcome.not_run;
    }); return j; });
    prim().click();
    await settle();
    ok($('#czg-checks-sum').textContent === OUTCOME_CASES.failed.display.summary,
        'old failure without not_run still renders');
    // Length and general refusals keep Name reserved for name errors.
    queue.plan.push((b) => { const j = planOk(b); j.pairs[0].ok = false;
        j.pairs[0].refusals = ['flux_pulse_qubit.length: The pulse length 18 ns must be a multiple of 4 ns.',
            'The selected port is unavailable.']; return j; });
    type($('#czg-name'), 'cz_lengths');
    await settle();
    { const i = doc.querySelector('[data-czg-field="flux_pulse_qubit"][data-czg-name="length"]');
      ok(i.getAttribute('aria-invalid') === 'true' && /multiple of 4/.test(i.closest('.ui-field').textContent),
         'length refusal reaches Length');
      ok(prim().disabled, 'a length refusal disables Check');
      ok($('#czg-name-why').hidden && !$('#czg-name').hasAttribute('aria-invalid'),
         'length and general refusals leave Name clean');
      ok(/port is unavailable/.test($('#czg-refusals').textContent) && !$('#czg-refusals').hidden,
         'unrouted refusal has a general line');
      ok($('#czg-refusals .ui-notice.ui-notice--warn .ui-notice-text') && $('#czg-refusals').closest('.ui-dialog-body')
         && !$('#czg-refusals [data-czg-class-page]'),
         'a general refusal is a kit warning notice inside the dialog body, with no class page action'); }
    { const SAVE = 'Save MyGate to quam_config before creating a gate that uses it.';
      queue.plan.push((b) => { const j = planOk(b); j.pairs[0].ok = false; j.pairs[0].refusals = [SAVE];
                               j.pairs[0].save_class = { cls: 'MyGate', refusal: SAVE }; return j; });
      type($('#czg-name'), 'cz_save_first');
      await settle();
      const n = $('#czg-refusals .ui-notice');
      const b = n && n.querySelector('button[data-czg-class-page="MyGate"]');
      ok(n && /^Save MyGate to quam_config before creating a gate that uses it\. Saving is done on the class page of MyGate/.test(n.querySelector('.ui-notice-text').textContent)
         && n.querySelector('.ui-notice-key').textContent === 'Not saved yet'
         && b && b.textContent === 'Open the class page' && b.classList.contains('btn-sync'),
         'a class to save first: the notice says where that is done and offers the class page'); }
    for (const [idx, message] of ['The gate name cz bad is not a valid name. Use letters.',
        'The pair already has a gate named cz_used. Pick another name.',
        'The operation cz_used_pulse already exists on the line. Pick another gate name.'].entries()) {
        queue.plan.push((b) => { const j = planOk(b); j.pairs[0].ok = false;
            j.pairs[0].refusals = [message]; j.pairs[0].name_refusals = [message]; return j; });
        type($('#czg-name'), 'cz_name_refusal_' + idx);
        await settle();
        ok($('#czg-name-why').textContent === message && $('#czg-name').getAttribute('aria-invalid') === 'true',
           'invalid and existing names still show under Name');
    }
    // E a value the server refuses goes on its field (mutation: splitRefusals returns every refusal as rest)
    queue.plan.push((b) => { const j = planOk(b); j.pairs[0].ok = false; j.pairs[0].refusals = ['flux_pulse_qubit.amplitude: Type a number.']; return j; });
    type($('#czg-name'), 'cz_r12c');
    await settle();
    { const a = fieldOf('flux_pulse_qubit', 'amplitude'); const e = a && a.closest('.ui-field').querySelector('.ui-field-error');
      ok(e && e.textContent === 'Type a number.' && a.getAttribute('aria-invalid') === 'true' && !/Type a number/.test($('#czg-name-why').textContent),
         'a server refusal naming a field is shown on that field, not under the name'); }
    // H focus restore (mutation: "if (keep && keep[0]) restoreFocus" -> "if (false)")
    type($('#czg-name'), 'cz_r8');
    await settle();
    { const a = fieldOf('flux_pulse_qubit', 'amplitude'); a.focus(); a.value = '0.123'; a.setSelectionRange(2, 2); a.dispatchEvent(new window.Event('input', { bubbles: true }));
      await settle();
      const b2 = fieldOf('flux_pulse_qubit', 'amplitude');
      ok(b2 !== null && doc.activeElement === b2 && b2.selectionStart === 2, 'a re-rendered field gets the focus and the caret back'); }
    window.CzGate.close();
    // R13 open fails (mutation: drop the catch in open)
    failDialog = true;
    toasts.length = 0;
    let threw = false;
    try { await window.CzGate.open({ pair: 'q1-2' }); } catch (e) { threw = true; }
    await settle();
    failDialog = false;
    ok(!threw && !$('#czg-dialog') && window.CzGate.state() === null && toasts.some((t) => /could not open/.test(t[0]) && t[1] === 'error'),
       'a failed load: the toast says why, no half-open dialog');
    // R10 hooks are data attributes (mutation: query the more rows by class again)
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    ok($('#czg-more-list [data-czg-more-row] [data-czg-more-why]') && !$('#czg-dialog .czg-more-why') && !$('#czg-dialog .czg-head'),
       'script hooks are data attributes; no class without a kit rule');
    window.CzGate.close();

    dataOverride = JSON.parse(JSON.stringify(DATA));
    dataOverride.classes[0].slots.push(Object.assign({}, DATA.classes[0].slots[0],
        { field: 'coupler_flux_pulse', line: 'coupler' }));
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    type($('#czg-name'), 'cz_timing');
    await settle();
    queue.check.push((b) => { const j = checkOk(b);
        j.pairs[0].tiles = [{ key: 'timing', value: '61', unit: 'ns', label: 'Timing',
            note: 'gap, no overlap', tone: 'warn' }];
        return j; });
    prim().click();
    await settle();
    ok(/61 ns gap with no overlap/.test(alertText()) && /port delays/.test(alertText()),
       'server gap appears in verdict warning');
    ok(!prim().disabled && /Create/.test(prim().textContent),
       'the gap warning does not block an intentionally ordered gate');
    ok(/^2 lines$/.test($('#czg-pulses-sum').textContent), 'two-lane gap header does not claim together');
    type($('#czg-name'), 'cz_overlap');
    await settle();
    queue.check.push((b) => { const j = checkOk(b);
        j.pairs[0].tiles = [{ key: 'timing', value: '0', unit: 'ns', label: 'Timing',
            note: 'same start', tone: 'neutral' }];
        return j; });
    prim().click();
    await settle();
    ok(!/gap with no overlap/.test(alertText()), 'overlapping check has no gap warning');
    window.CzGate.close();

    dataOverride = null;
    // the slot takes the server's lane colour (mutation: drop recolourSlots()); no Pico spinner after Create (mutation: set aria-busy again)
    await window.CzGate.open({ pair: 'q1-2' });
    await settle();
    type($('#czg-name'), 'cz_col');
    await settle();
    { const chipKey = $('#czg-slots .ui-slot-chip').textContent;     // the rendered chip's own channel
      queue.check.push((b) => { const j = checkOk(b); j.pairs[0].timeline.lanes[0].key = chipKey; j.pairs[0].timeline.lanes[0].color_index = 2; return j; }); }
    prim().click();
    await settle();
    ok($('#czg-slots .ui-slot').classList.contains('ui-line-3') && !$('#czg-slots .ui-slot').classList.contains('ui-line-1'), 'after a check the form slot wears the lane colour the server gave');
    // a coupler row: the plan says "coupler <element>", the lane key is the element (mutation: laneKeyOf returns r.channel)
    type($('#czg-name'), 'cz_col2');
    await settle();
    queue.check.push((b) => { const j = checkOk(b); const p0 = j.pairs[0];
        p0.timeline.lanes = [{ key: 'q1.z', segments: [], color_index: 0 }, { key: 'c12', segments: [], color_index: 1 }];
        p0.intent = p0.intent.concat([Object.assign({}, p0.intent[0], { field: 'coupler_flux_pulse', operation: 'op_c', channel: 'the coupler of pair q1-2', lane_key: 'c12' })]); return j; });
    window.CzGate.state().plan.pairs[0].operations.coupler_flux_pulse = { key: 'op_c', channel: 'coupler c12' };
    prim().click();
    await settle();
    { const dots = [...doc.querySelectorAll('#czg-intent .ui-line-dot')].map((d) => d.className);
      ok(dots.length === 2 && /ui-line-1/.test(dots[0]) && /ui-line-2/.test(dots[1]), 'the coupler row dot has the coupler lane colour'); }
    prim().click();
    await settle();
    ok(/Created/.test(alertText()) && !$('#czg-dialog[aria-busy="true"], #czg-dialog [aria-busy="true"]'), 'created: nothing left marked busy (Pico draws a spinner for it)');
    window.CzGate.close();


    // ---- which class opens: the first one that can run on the pair (server's data.fit)
    {
        const SQ = DATA.classes[0].slots[0].pulse_classes;
        const CZ = { 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [],
                     slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: 'x', required: true, pulse_classes: SQ }] };
        const CO = { 'class': 'G.CouplerCZ', label: 'Coupler-only CZ gate', layout: 'known', available: true, why: '', models: [],
                     slots: [{ field: 'coupler_flux_pulse', line: 'coupler', line_words: 'x', required: true, pulse_classes: SQ }] };
        const NOZ = 'The qubits of pair q1-2 have no flux line, so a gate that plays on a qubit flux line cannot run here. Pick the coupler-only gate class.';
        const NOC = 'Pair q1-2 has no coupler that can hold operations.';
        const base = { pairs: DATA.pairs, selected: 'q1-2', env: 'lab', classes: [CZ, CO, DATA.classes[1]] };
        const optOf = (v) => [...doc.querySelectorAll('#czg-class-list .ui-option')].filter((o) => o.getAttribute('data-value') === v || (o.textContent || '').indexOf(v) >= 0)[0];
        const st = () => window.CzGate.state();
        const classWhy = () => $('#czg-class-why');
        const nameWhy = () => $('#czg-name-why');

        // 1. no flux line, coupler class offered: the coupler class opens, the name stays clean
        dataOverride = Object.assign({}, base, { fit: { 'q1-2': { 'G.CZGate': NOZ, 'G.CouplerCZ': '', X: '' }, 'q2-3': { 'G.CZGate': '', 'G.CouplerCZ': '', X: '' } } });
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        ok(st().klass === 'G.CouplerCZ', 'no flux line: the coupler-only class is selected on open');
        ok(sent[sent.length - 1].body.gate_class === 'G.CouplerCZ', 'no flux line: the plan is asked for the coupler-only class');
        ok(nameWhy().hidden && !$('#czg-name').hasAttribute('aria-invalid'), 'no flux line: nothing under Name');
        ok(classWhy().hidden && !$('#czg-class').hasAttribute('aria-invalid'), 'no flux line: nothing under Class once a class can run');
        ok(!prim().disabled, 'no flux line: Check is enabled with a valid name');
        { const o = [...doc.querySelectorAll('#czg-class-list .ui-option[aria-disabled="true"]')].filter((x) => /CZ gate/.test(x.textContent) && !/Coupler/.test(x.textContent))[0];
          ok(o && /no flux line/.test(o.querySelector('.ui-option-desc').textContent), 'the class that cannot run is listed, disabled, with its reason'); }
        // 4. hand-picked class survives a pair where it runs; moves with a note where it does not
        window.CzGate.close();
        dataOverride = Object.assign({}, base, { fit: { 'q1-2': { 'G.CZGate': '', 'G.CouplerCZ': '', X: '' }, 'q2-3': { 'G.CZGate': '', 'G.CouplerCZ': 'Pair q2-3 has no coupler that can hold operations.', X: '' } } });
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        ok(st().klass === 'G.CZGate', 'every class runs: the first class opens, as before');
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CouplerCZ', kind: 'pick' } }));
        await settle();
        ok(st().klass === 'G.CouplerCZ', 'a class picked by hand is taken');
        change($('#czg-pair'), 'q2-3');
        await settle();
        ok(st().klass === 'G.CZGate' && sent[sent.length - 1].body.gate_class === 'G.CZGate', 'pair change: the hand-picked class that cannot run there moves to the first runnable class');
        ok(!classWhy().hidden && /class changed/.test(classWhy().textContent) && /no coupler/.test(classWhy().textContent) && nameWhy().hidden, 'pair change: a note under Class says the class changed and why');
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CZGate', kind: 'pick' } }));
        change($('#czg-pair'), 'q1-2');
        await settle();
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CouplerCZ', kind: 'pick' } }));
        await settle();
        change($('#czg-pair'), 'q1-2');
        await settle();
        ok(st().klass === 'G.CouplerCZ' && classWhy().hidden, 'pair change: a hand-picked class that runs on the new pair is kept');
        // 5. a name error and a class error at once: each under its own field
        planExtra = [['The gate name must start with cz_.', false, true], [NOZ, true]];
        type($('#czg-name'), 'cz_both');
        await settle();
        ok(/must start with cz_/.test(nameWhy().textContent) && !/no flux line/.test(nameWhy().textContent), 'name error stays under Name');
        ok(/no flux line/.test(classWhy().textContent) && !/must start/.test(classWhy().textContent) && $('#czg-class').getAttribute('aria-invalid') === 'true', 'class error goes under Class, the class control marked');
        ok($('#czg-name').getAttribute('aria-invalid') === 'true', 'name control marked for the name error');
        planExtra = null;
        window.CzGate.close();
        // 2./4. no class can run (no coupler either): said once under Class, Check disabled
        dataOverride = Object.assign({}, base, { fit: { 'q1-2': { 'G.CZGate': NOZ, 'G.CouplerCZ': NOC, X: '' }, 'q2-3': {} } });
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        ok(st().klass === '', 'no class can run: none is selected');
        ok(classWhy().querySelectorAll('.czg-why-line').length === 1 && /No gate class can run on q1-2/.test(classWhy().textContent) && /no flux line/.test(classWhy().textContent), 'no class can run: said once under Class with the first class\'s reason');
        ok(nameWhy().hidden && !$('#czg-name').hasAttribute('aria-invalid'), 'no class can run: Name stays clean');
        ok(prim().disabled, 'no class can run: Check stays disabled');
        window.CzGate.close();
        dataOverride = null;
    }

    // ---- spectators: other lines that play while the gate plays
    {
        const SQ = DATA.classes[0].slots[0].pulse_classes;
        const WHY_CO = 'The coupler-only gate class plays only the coupler, so SM adds no spectator pulses to it. Pick the CZ gate (quam-builder) class to add spectators.';
        const CZ = { 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [], spectators: { offered: true, why: '' },
                     slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: 'x', required: true, pulse_classes: SQ }] };
        const CO = { 'class': 'G.CouplerCZ', label: 'Coupler-only CZ gate', layout: 'known', available: true, why: '', models: [], spectators: { offered: false, why: WHY_CO },
                     slots: [{ field: 'coupler_flux_pulse', line: 'coupler', line_words: 'x', required: true, pulse_classes: SQ }] };
        const cand = (q, k) => ({ qubit: q, line: q + '.z', port: '#/ports/analog_outputs/con1/1/' + k, range: 0.5 });
        dataOverride = { pairs: [Object.assign({}, DATA.pairs[0], { spectator_candidates: [cand('q3', 3), cand('q4', 4)] }),
                                 Object.assign({}, DATA.pairs[1], { spectator_candidates: [cand('q1', 1), cand('q4', 4)] })],
                         selected: 'q1-2', env: 'lab', classes: [CZ, CO] };
        const st = () => window.CzGate.state();
        const last = () => sent.filter((s) => s.kind === 'plan').slice(-1)[0].body;
        const box = (q, pair) => [...doc.querySelectorAll('#czg-spect-body [data-czg-spect-pair="' + (pair || 'q1-2') + '"] input[data-czg-spect-qubit]')].filter((i) => i.getAttribute('data-czg-spect-qubit') === q)[0];
        const tickBox = (q, on, pair) => { const i = box(q, pair); i.checked = on; i.dispatchEvent(new window.Event('change', { bubbles: true })); };
        const fld = (q, n, pair) => [...doc.querySelectorAll('#czg-spect-body input[data-czg-field]')].filter((i) => i.getAttribute('data-czg-field') === '@sp:' + (pair || 'q1-2') + ':' + q && i.getAttribute('data-czg-name') === n)[0];
        const errOf = (i) => { const e = i && i.closest('.ui-field').querySelector('.ui-field-error'); return e ? e.textContent : ''; };
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        const panel = $('#czg-spect');
        ok(panel && !panel.hidden && !panel.open && $('#czg-zone-pulses').contains(panel) && panel.classList.contains('ui-panel--fold'),
           'spectators: a folded kit panel inside the Pulses zone');
        ok($('#czg-spect-sum').textContent === 'none' && !$('#czg-spect-body input[data-czg-field]') && box('q3') && !box('q3').checked && !box('q4').checked,
           'spectators: none ticked by default, no inputs, summary none');
        ok(!('spectators' in last()), 'spectators: none ticked sends no spectators field');
        ok(/port con1\/1\/3, range ±0\.5 V/.test($('#czg-spect-body [data-czg-spect-row="q3"]').textContent), 'spectators: the port and its range, quietly');
        tickBox('q3', true);
        await settle();
        ok(fld('q3', 'length') && fld('q3', 'length').value === '100' && fld('q3', 'level').value === '',
           'tick: Length starts at the gate length, Level starts empty');
        ok(/SM computes no level/.test(errOf(fld('q3', 'level'))) && prim().disabled, 'tick: an empty level says why under the field and blocks Check');
        ok($('#czg-spect-body [data-czg-spect-row="q3"] .ui-slot-chip.ui-line-2') && $('#czg-spect-sum').textContent.indexOf('1 line') === 0,
           'tick: the line chip wears the next identity colour; summary counts the line');
        type(fld('q3', 'level'), 'x');
        await settle();
        ok(/Type a number/.test(errOf(fld('q3', 'level'))) && prim().disabled, 'an invalid level is said and blocks Check');
        type(fld('q3', 'level'), '0.05');
        type(fld('q3', 'length'), '48');
        tickBox('q4', true);
        await settle();
        type(fld('q4', 'length'), '32');
        type(fld('q4', 'level'), '-0.03');
        await settle();
        ok(!errOf(fld('q3', 'level')) && !prim().disabled, 'valid levels: no error, Check enabled');
        // a spectator operation that already exists is fixed by another gate name: once, under Name, untagged
        {
            const SP_HIT = 'The operation cz_sp_spectator_pulse_q1_q2 already exists on q3.z. Pick another gate name.';
            const before = $('#czg-name').value;
            queue.plan.push((b) => { const j = spectPlan(b, planOk(b)); j.pairs[0].ok = false;
                j.pairs[0].refusals = [SP_HIT]; j.pairs[0].name_refusals = [SP_HIT]; return j; });
            type($('#czg-name'), 'cz_sp');
            await settle();
            const hits = [...doc.querySelectorAll('#czg-form *')]
                .filter((n) => n.children.length === 0 && n.textContent.indexOf(SP_HIT) >= 0);
            ok($('#czg-name-why').textContent === SP_HIT && $('#czg-name').getAttribute('aria-invalid') === 'true'
               && hits.length === 1 && $('#czg-name-why').contains(hits[0])
               && $('#czg-spect-body').textContent.indexOf('already exists') < 0,
               'a spectator operation collision is said once in the form, under Name, not in the spectator panel');
            type($('#czg-name'), before);
            await settle();
        }
        ok(JSON.stringify(last().spectators) === JSON.stringify({ 'q1-2': [{ qubit: 'q3', length: 48, level: 0.05 }, { qubit: 'q4', length: 32, level: -0.03 }] }),
           'one pair: the spectators field equals the server contract\'s example');
        ok(/q3\.z will hold 1 spectator operation, q4\.z will hold 2 spectator operations/.test($('[data-czg-spect-ops="q1-2"]').textContent)
           && $('#czg-spect-sum').textContent === '2 lines ticked',
           'before creating: per line, the spectator operations it will hold; the header only counts ticked lines');
        ok($('[data-czg-spect-range-note="q4"]') && $('[data-czg-spect-range-note="q4"]').textContent === RANGE_NOTE
           && $('[data-czg-spect-row="q4"]').contains($('[data-czg-spect-range-note="q4"]')) && !$('[data-czg-spect-range-note="q3"]'),
           'an unreadable range is said on that line only, in plain words');
        ok(/qubits\.q3\.z\.operations\.cz_\w*spectator_pulse/.test($('#czg-writes').textContent) && /qubits\.q4\.z\.operations/.test($('#czg-writes').textContent),
           'What will be written lists every spectator operation');
        // check: lanes, rows, the not-calibrated note
        prim().click();
        await settle();
        const tl = charts[charts.length - 1];
        ok(tl.lanes.filter((l) => l.group === 'spectator').map((l) => l.key + ':' + l.color).join() === 'q3.z:1,q4.z:2',
           'check: each spectator is its own lane in the spectator group, in its identity colour');
        ok([...doc.querySelectorAll('#czg-intent tbody tr')].map((tr) => tr.children[0].textContent).join('|') === 'op_q1-2|spectator on q3.z|spectator on q4.z'
           && $('#czg-intent tbody tr:nth-child(3) .ui-line-dot.ui-line-3'),
           'check: Entered and actual has one row per spectator, named by its line, with its dot');
        { const w = $('#czg-intent > [data-czg-table-scroll]');
          ok(w && w.classList.contains('ui-xscroll') && w.classList.contains('ui-xscroll-table') && w.querySelector(':scope > table.ui-table')
             && !w.hasAttribute('tabindex') && !w.hasAttribute('role') && !w.hasAttribute('aria-label'),
             'check: a table that fits has no tab stop, role or label of its own');
          const setW = tableWidths;
          const all = [...doc.querySelectorAll('[data-czg-table-scroll]')];
          all.forEach((x) => setW(x, 900, 300));
          window.dispatchEvent(new window.Event('resize'));
          ok(all.length >= 1 && all.every((x) => x.getAttribute('tabindex') === '0' && x.getAttribute('role') === 'region'
               && /^Scrollable table region: /.test(x.getAttribute('aria-label')) && x.getAttribute('aria-label') !== x.querySelector('table').getAttribute('aria-label')),
             'check: a table that scrolls sideways is a focusable, named scrollable region (re-decided on resize)');
          all.forEach((x) => setW(x, 300, 300));
          window.dispatchEvent(new window.Event('resize'));
          ok(all.every((x) => !x.hasAttribute('tabindex') && !x.hasAttribute('role') && !x.hasAttribute('aria-label')),
             'check: after widening, the wrapper is plain layout again'); }
        ok([...doc.querySelectorAll('.czg table')].length > 0 && [...doc.querySelectorAll('.czg table')].every((t) => t.parentElement.hasAttribute('data-czg-table-scroll')),
           'every table the dialog draws sits in the one scrolling wrapper');
        ok(doc.querySelectorAll('[data-czg-spect-uncal]').length === 1 && /not calibrated/.test($('[data-czg-spect-uncal]').textContent)
           && $('[data-czg-spect-uncal]').classList.contains('ui-text-quiet') && !/not calibrated/.test(alertText()),
           'check: the phase shift is not calibrated, said once in the panel, quiet, not in the banner');
        ok($('#czg-spect-body [data-czg-spect-row="q4"] .ui-slot-chip.ui-line-3'), 'check: the form chip takes the lane colour');
        // stale on change; inert while busy
        type(fld('q4', 'level'), '-0.04');
        await tick(5);
        ok($('#czg-evidence').classList.contains('czg-stale') && prim().textContent === 'Check', 'a spectator change marks the result stale');
        await settle();
        let rel;
        hold = new Promise((r) => { rel = r; });
        prim().click();
        await tick(5);
        ok($('#czg-form').inert && box('q3').disabled && fld('q3', 'level').disabled, 'busy: the spectator inputs are inert');
        rel();
        await settle();
        // two pairs: each pair its own choices
        const more = [...doc.querySelectorAll('#czg-more-list input[data-czg-more]')].filter((i) => i.value === 'q2-3')[0];
        more.checked = true;
        more.dispatchEvent(new window.Event('change', { bubbles: true }));
        await settle();
        ok(box('q4', 'q2-3') && !box('q4', 'q2-3').checked && box('q1', 'q2-3'), 'two pairs: the second pair lists its own candidates, none ticked');
        tickBox('q4', true, 'q2-3');
        await settle();
        type(fld('q4', 'level', 'q2-3'), '0.02');
        await settle();
        ok(JSON.stringify(last().spectators) === JSON.stringify({ 'q1-2': [{ qubit: 'q3', length: 48, level: 0.05 }, { qubit: 'q4', length: 32, level: -0.04 }],
                                                              'q2-3': [{ qubit: 'q4', length: 100, level: 0.02 }] }),
           'two pairs: the spectators are given per pair');
        prim().click();
        await settle();
        $('#czg-pairs [data-czg-pair-idx="1"]').click();
        await settle();
        ok(charts[charts.length - 1].lanes.filter((l) => l.group === 'spectator').map((l) => l.key).join() === 'q4.z'
           && /spectator on q4\.z/.test($('#czg-intent').textContent) && !/spectator on q3\.z/.test($('#czg-intent').textContent),
           'the pair switch shows that pair\'s own spectators');
        // pair change in the Gate zone resets the choices and says so
        change($('#czg-pair'), 'q2-3');
        await settle();
        ok(!('spectators' in last()) && !doc.querySelector('#czg-spect-body input[data-czg-spect-qubit]:checked')
           && /spectator choices were cleared/.test($('#czg-spect-body').textContent) && $('#czg-spect-sum').textContent === 'none',
           'pair change: the choices reset, and the panel says so');
        change($('#czg-pair'), 'q1-2');
        await settle();
        ok(!/were cleared/.test($('#czg-spect-body').textContent), 'pair change with nothing ticked: no note');
        // class switched twice with ticks on, a late plan answer arriving after the switch
        tickBox('q3', true);
        await settle();
        type(fld('q3', 'level'), '0.05');
        let relP;
        holdPlan = new Promise((r) => { relP = r; });
        await settle();
        const heldSent = sent.length;
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CouplerCZ', kind: 'pick' } }));
        ok(/spectator choices were cleared/.test($('#czg-spect-body').textContent) && $('#czg-spect-sum').textContent === 'not offered',
           'class switch to one without spectators: the ticks are cleared and the panel says so');
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CZGate', kind: 'pick' } }));
        relP();
        await settle();
        ok(heldSent > 0 && !doc.querySelector('#czg-spect-body input[data-czg-spect-qubit]:checked') && !('spectators' in last())
           && !$('[data-czg-spect-ops]') && $('#czg-spect-sum').textContent === 'none' && !Object.keys(st().spectOn || {}).length,
           'class switched twice: a late plan answer brings no ticks or counts back, and the request carries none');
        // a class that takes no spectators
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'G.CouplerCZ', kind: 'pick' } }));
        await settle();
        ok(!panel.hidden && $('#czg-spect-body').textContent === WHY_CO && !$('#czg-spect-body input') && $('#czg-spect-sum').textContent === 'not offered',
           'not offered: the panel stays, quiet, with the server\'s why, no inputs');
        window.CzGate.close();
        dataOverride = null;
    }

    // the display vectors (tests/test_cz_gate_name_refusals.py reads the same file): every reason
    // shows byte-identical, under Name when the row's name_refusals lists it, else as a general line
    {
        const V = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'refusal_display_vectors.json'), 'utf8'))
            .reasons;
        await window.CzGate.open({ pair: 'q1-2' });
        await settle();
        const bad = [];
        for (const [i, v] of V.entries()) {
            queue.plan.push((b) => { const j = planOk(b); j.pairs[0].ok = false; j.pairs[0].refusals = [v.text];
                                     j.pairs[0].name_refusals = v.name ? [v.text] : []; return j; });
            type($('#czg-name'), 'cz_vec_' + i);
            await settle();
            const shown = v.name ? (!$('#czg-name-why').hidden && $('#czg-name-why').textContent === v.text)
                : ($('#czg-name-why').hidden && [...$('#czg-refusals').children].some((n) => n.querySelector('.ui-notice-text') && n.querySelector('.ui-notice-text').textContent === v.text));
            if (!shown) bad.push(v.case);
        }
        ok(V.length >= 12 && !bad.length,
           'every display vector shows byte-identical in its place' + (bad.length ? ': ' + bad.join(', ') : ''));
        const ECHO = V[0].text;
        const TAKEN = 'The pair already has a gate named cz_x. name: Foo. Pick another name.';
        type($('#czg-name'), 'cz_vecjoin');
        await settle();
        queue.check.push((b) => { const j = checkOk(b, { refuse: 'q1-2' });
            j.pairs[0].verdict.reason = ECHO + ' ' + TAKEN; return j; });
        prim().click();
        await settle();
        ok(alertText().indexOf(ECHO + ' ' + TAKEN) >= 0
           && $('#czg-writes').textContent.indexOf(ECHO + ' ' + TAKEN) >= 0,
           'a joined verdict reason shows byte-identical in the banner and in What will be written');
        type($('#czg-name'), 'cz_vecmid');
        await settle();
        prim().click();
        await settle();
        queue.create.push(() => [400, { ok: false, error: 'No pair can be created: q1-2: ' + TAKEN,
                                        skipped: [{ pair: 'q1-2', reason: TAKEN }] }]);
        prim().click();
        await settle();
        ok(alertText().indexOf('No pair can be created: q1-2: ' + TAKEN) >= 0,
           "a create where every pair is refused shows the server's error byte-identical");
        window.CzGate.close();
    }

    // ---- slot gate classes: the new-class row, its two-step save, the unit method
    {
        const SQ = DATA.classes[0].slots[0].pulse_classes;
        const slot = (n) => ({ field: n + '_flux_pulse', line: n, line_words: 'x', required: true, pulse_classes: SQ });
        const CZ = { 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [], spectators: { offered: true, why: '' },
                     slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: 'x', required: true, pulse_classes: SQ }] };
        const SA = { 'class': 'quam_config.SlotA.SlotA', label: 'SlotA (slot class SM made)', layout: 'slot', available: true, why: '', models: [],
                     slots: [slot('control'), slot('coupler')], slot_spec: { slots: ['control', 'coupler'], spectators: true },
                     spectators: { offered: true, method: 'unit', why: '' } };
        const NEW = { 'class': 'quam_config.MyGate.MyGate', label: 'MyGate (slot class SM made)', layout: 'slot', available: true, why: '', models: [],
                      slots: [slot('coupler')], slot_spec: { slots: ['coupler'], spectators: false },
                      spectators: { offered: false, method: 'unit', why: 'This class was made without spectators.' } };
        // the server's range is NOT twice the unit here on purpose: the screen must show the server's number
        const cand = (q, k, unit, reach) => ({ qubit: q, line: q + '.z', port: '#/ports/analog_outputs/con1/1/' + k, range: 0.5, unit: unit, reachable: reach });
        const PRESETS = [{ key: 'only_coupler', label: 'Coupler only', slots: ['coupler'] },
                         { key: 'both_qubits', label: 'Control and target', slots: ['control', 'target'] }];
        dataOverride = { pairs: [Object.assign({}, DATA.pairs[0], { spectator_candidates: [cand('q3', 3, 0.3, 0.5), cand('q4', 4, null, null)] })],
                         selected: 'q1-2', env: 'lab', classes: [CZ, SA], slot_presets: PRESETS, slot_names: ['control', 'target', 'coupler'] };
        const st = () => window.CzGate.state();
        const until = async (fn) => {
            const end = Date.now() + 3000;
            while (!fn()) { if (Date.now() > end) throw new Error('Condition wait expired: ' + fn); await tick(5); }
        };
        const ready = async () => {
            await until(() => st() && !st().newSlot.busy && !st().busy);
            window.clearTimeout(st().timer);
            await window.CzGate._plan();
            await until(() => [...doc.querySelectorAll('input[data-czg-field]')].every((i) => {
                const why = st().invalid[i.getAttribute('data-czg-field') + '\u0000' + i.getAttribute('data-czg-name')];
                const error = i.closest('.ui-field').querySelector('.ui-field-error');
                return !why || (error && error.textContent === why);
            }));
        };
        const last = () => sent.filter((x) => x.kind === 'plan').slice(-1)[0].body;
        const kinds = (from) => sent.slice(from).map((x) => x.kind).filter((k) => k !== 'plan');
        const pick = (hostSel, v) => $(hostSel + ' .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: v, kind: 'pick' } }));
        const ticks = () => [...doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]')].filter((i) => i.checked).map((i) => i.getAttribute('data-czg-new-slot')).join();
        const spectOn = () => $('#czg-new-body input[data-czg-new-spect]').checked;
        const tickNew = (n, on) => { const i = [...doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]')].filter((x) => x.getAttribute('data-czg-new-slot') === n)[0]; i.focus(); i.checked = on; i.dispatchEvent(new window.Event('change', { bubbles: true })); };
        const box = (q) => [...doc.querySelectorAll('#czg-spect-body input[data-czg-spect-qubit]')].filter((i) => i.getAttribute('data-czg-spect-qubit') === q)[0];
        const tickBox = (q, on) => { const i = box(q); i.checked = on; i.dispatchEvent(new window.Event('change', { bubbles: true })); };
        const fld = (q, n) => [...doc.querySelectorAll('#czg-spect-body input[data-czg-field]')].filter((i) => i.getAttribute('data-czg-field') === '@sp:q1-2:' + q && i.getAttribute('data-czg-name') === n)[0];
        const errOf = (i) => { const e = i && i.closest('.ui-field').querySelector('.ui-field-error'); return e ? e.textContent : ''; };
        const allowed = (q) => $('#czg-spect-body [data-czg-spect-allowed="' + q + '"]');
        const rowOf = (q) => $('#czg-spect-body [data-czg-spect-row="' + q + '"]');
        const lines = (sel) => [...doc.querySelectorAll(sel)].map((x) => x.textContent);
        await window.CzGate.open({ pair: 'q1-2' });
        await ready();
        const panel = $('#czg-new');
        ok(panel && !panel.hidden && !panel.open && panel.classList.contains('ui-panel--fold') && $('#czg-zone-gate').contains(panel)
           && /New slot gate class/.test(panel.querySelector('summary').textContent),
           'new slot class: a folded kit panel in the Gate zone when the server names presets');
        ok($('#czg-new-body input[data-czg-new-name]').value === '' && lines('#czg-new-preset-host [role="option"]').join('|') === 'Coupler only|Control and target'
           && $('#czg-new-preset-host .ui-menu').getAttribute('data-value') === 'only_coupler',
           'new slot class: a blank name and the server\'s presets, the first one picked');
        ok(ticks() === 'coupler' && !spectOn() && doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]').length === 3
           && $('#czg-new-sum').textContent === '1 slot' && $('#czg-new-body [data-czg-new-create]').textContent === 'Create class',
           'new slot class: one check per slot name, ticked from the preset; spectators off; a Create button');
        ok(!doc.querySelector('#czg-spect-body [data-czg-spect-unit]') && !$('#czg-new-body [data-czg-new-confirm]'),
           'a class without slot_spec renders as today: no unit cells');
        // a slot class picked: its slots in the Pulses zone and prefilled checks
        pick('#czg-class-host', SA['class']);
        await ready();
        ok(ticks() === 'control,coupler' && spectOn() && !$('#czg-new-preset-host .ui-menu').getAttribute('data-value') && $('#czg-new-sum').textContent === '2 slots',
           'a slot class: the checks are prefilled from its slot_spec, no preset named');
        ok(Object.keys(last().pulses).join() === 'control_flux_pulse,coupler_flux_pulse' && last().gate_class === SA['class']
           && doc.querySelectorAll('#czg-slots .ui-slot').length === 2,
           'a slot class: its slots are pulse blocks and the request names each slot\'s pulse');
        // the unit method
        tickBox('q3', true);
        tickBox('q4', true);
        await ready();
        ok(fld('q3', 'level').value === '' && fld('q3', 'length').value === '100' && !fld('q3', 'unit'),
           'unit method: Level starts blank, Length at the gate length; a line with a unit operation asks for no unit');
        ok(allowed('q3').textContent === '|level| < 0.5 V' && rowOf('q3').contains(allowed('q3')) && !allowed('q3').matches('input')
           && $('[data-czg-spect-unit-value="q3"]').textContent === '0.3 V',
           'unit method: the allowed range is the server\'s number as given (0.5 for unit 0.3), read-only');
        ok(allowed('q4').textContent === '' && fld('q4', 'unit') && fld('q4', 'unit').value === ''
           && /no unit operation on this line yet/.test(fld('q4', 'unit').closest('.ui-field').textContent) && !/\d/.test(allowed('q4').closest('.ui-field').textContent),
           'unit method: no unit operation: the range is blank, the line says so, the unit amplitude is asked blank');
        type(fld('q3', 'level'), '0.1');
        type(fld('q4', 'level'), '0.1');
        await ready();
        ok(/unit amplitude/.test(errOf(fld('q4', 'unit'))) && prim().disabled, 'unit method: a blank unit amplitude says why and blocks Check');
        unitReach = { q4: 0.35 };
        type(fld('q4', 'unit'), '0.2');
        await ready();
        ok(JSON.stringify(last().spectators) === JSON.stringify({ 'q1-2': [{ qubit: 'q3', length: 100, level: 0.1 }, { qubit: 'q4', length: 100, level: 0.1, unit: 0.2 }] })
           && !prim().disabled,
           'unit method: the request carries unit only for the row without a unit operation');
        ok(allowed('q4').textContent === '|level| < 0.35 V' && allowed('q3').textContent === '|level| < 0.5 V',
           'unit method: a typed unit shows the plan\'s own range (0.35 for unit 0.2), never one computed here');
        // refusals: the server's sentences, next to their row
        const R3 = "Spectator 'q3' level 0.7 must be a finite number with |level / unit| strictly below 2.0.";
        const RD = "Spectators 'q3' and 'q4' name the same qubit.";
        queue.plan.push((b) => ({ ok: true, plan_sig: 'r', pairs: b.pairs.map((p) => ({ pair: p, ok: false, refusals: [R3, RD] })) }));
        type(fld('q3', 'level'), '0.7');
        await ready();
        const levelSaid = (t) =>
            /too large for the line: its size must stay below 2\.0 times the unit amplitude/.test(t);
        ok(fld('q3', 'level').getAttribute('aria-invalid') === 'true' && levelSaid(errOf(fld('q3', 'level')))
           && fld('q4', 'level').getAttribute('aria-invalid') !== 'true'
           && lines('#czg-spect-body .ui-field-error').filter(levelSaid).length === 1
           && lines('#czg-spect-body .ui-field-error').indexOf(R3) < 0,
           'refusal: a spectator level out of range marks that row\'s Level field once, in plain words');
        ok(lines('#czg-spect-body [data-czg-spect-pair="q1-2"] > .ui-field-error').join('|') === RD && prim().disabled,
           'refusal: a sentence about no single row stays under the pair, verbatim; Check is blocked');
        type(fld('q3', 'level'), '0.1');
        await ready();
        // a preset replaces the ticks; the person's own tick names no preset
        st().newSlot.name = '';
        pick('#czg-new-preset-host', 'both_qubits');
        ok(ticks() === 'control,target' && spectOn() && $('#czg-new-preset-host .ui-menu').getAttribute('data-value') === 'both_qubits',
           'preset change: only slot ticks change and spectators survive');
        ok(doc.activeElement === $('#czg-new-preset'), 'preset menu keeps keyboard focus');
        tickNew('coupler', true);
        ok(ticks() === 'control,target,coupler' && !$('#czg-new-preset-host .ui-menu').getAttribute('data-value')
           && doc.activeElement && doc.activeElement.getAttribute('data-czg-new-slot') === 'coupler',
           'an own tick: no preset is named any more, and the ticked box keeps the focus');
        // a refused class: the server's sentences, nothing saved
        let from = sent.length;
        queue['slot-class'].push(() => [400, { ok: false, error: 'The class name must be a Python class name.', problems: ['The class name must be a Python class name.', 'A slot gate class needs at least one slot.'] }]);
        type($('#czg-new-body input[data-czg-new-name]'), '9bad');
        $('#czg-new-body [data-czg-new-create]').click();
        await ready();
        ok(lines('#czg-new-body [data-czg-new-problem]').join('|') === 'The class name must be a Python class name.|A slot gate class needs at least one slot.'
           && kinds(from).join() === 'slot-class' && !$('#czg-new-body [data-czg-new-confirm]') && st().klass === SA['class'],
           'a refused class: the server\'s sentences verbatim, each once; nothing is saved and the picked class stays');
        const nameAfterError = $('#czg-new-name');
        nameAfterError.focus();
        nameAfterError.value = 'MyClass';
        nameAfterError.setSelectionRange(2, 2);
        nameAfterError.dispatchEvent(new window.Event('input', { bubbles: true }));
        ok($('#czg-new-name') === nameAfterError && nameAfterError.selectionStart === 2,
           'clearing a refusal preserves the name node and mouse-placed caret');
        const gateName = $('#czg-name');
        gateName.value = 'cz_'; gateName.focus();
        ok(gateName.selectionStart === 3, 'keyboard focus starts at the end of the gate prefill');
        gateName.blur();
        gateName.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
        gateName.focus(); gateName.setSelectionRange(1, 1);
        gateName.dispatchEvent(new window.Event('pointerup', { bubbles: true }));
        ok(gateName.selectionStart === 1, 'mouse focus preserves the chosen caret');
        gateName.blur(); gateName.setSelectionRange(1, 1);
        gateName.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
        gateName.dispatchEvent(new window.Event('pointerup', { bubbles: true }));
        gateName.focus();
        ok(gateName.selectionStart === 1, 'touch focus (after the pointer is up) keeps the tapped caret');
        const ownErrors = [];
        const originalError = window.console.error;
        window.console.error = (...args) => ownErrors.push(args);
        queue['slot-class'].push(() => [409, { ok: false, confirm_replace: true, error: 'A class named MyClass exists; making it again replaces its steps.' }]);
        $('[data-czg-new-create]').click(); await ready();
        ok($('[data-czg-new-confirm]') && $('[data-czg-new-confirm-save]').textContent === 'Yes', 'replacement refusal opens the kit confirmation');
        let refusedAt = sent.filter(x => x.kind === 'slot-class' || x.kind === 'save-config').length;
        $('[data-czg-new-cancel]').click(); await ready();
        ok(sent.filter(x => x.kind === 'slot-class' || x.kind === 'save-config').length === refusedAt && $('#czg-new-name').value === 'MyClass', 'Cancel writes nothing and keeps typed values');
        queue['slot-class'].push(() => [409, { ok: false, confirm_replace: true, error: 'The class exists; making it again replaces its steps.' }]);
        $('[data-czg-new-create]').click(); await ready();
        queue['slot-class'].push(() => [400, { ok: false, error: 'Check stopped.' }]);
        $('[data-czg-new-confirm-save]').click(); await ready();
        ok(sent.filter(x => x.kind === 'slot-class').slice(-1)[0].body.confirm_replace === true, 'only explicit Yes posts the replacement flag');
        ok(ownErrors.length === 0, 'expected replacement refusals never log our own console error');
        window.console.error = originalError;
        // create, then the two-step save
        pick('#czg-new-preset-host', 'only_coupler');
        const spOff = $('#czg-new-body input[data-czg-new-spect]');
        spOff.checked = false; spOff.dispatchEvent(new window.Event('change', { bubbles: true }));
        type($('#czg-new-body input[data-czg-new-name]'), ' MyGate ');
        ok(!$('#czg-new-body [data-czg-new-problem]'), 'typing a new name clears the old refusal');
        const CONFIRM = { folder: 'C:/lab/quam_config', env_folder: 'C:/env/quam_config', lab_folder: 'C:/lab', importable: false, path: 'C:/lab/quam_config/MyGate.py',
                          notice: 'This file is Python that runs without isolation in the lab environment.',
                          warning: 'The selected environment does not import this file. Gates need a successful load check.',
                          gates: [{ pair: 'q1-2', gate: 'cz_old', live_active: true }] };
        from = sent.length;
        queue['slot-class'].push(() => { dataOverride.classes = [CZ, SA, NEW]; return { ok: true, cls: 'MyGate', qclass: NEW['class'] }; });
        queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
        $('#czg-new-body [data-czg-new-create]').click();
        await ready();
        const mk = sent.slice(from).filter((x) => x.kind === 'slot-class')[0];
        ok(mk && JSON.stringify(mk.body) === JSON.stringify({ class_name: 'MyGate', slots: ['coupler'], spectators: false, pair: 'q1-2' }),
           'Create class posts the name, the ticked slots and the spectators choice and the pair to the slot route');
        const sv1 = sent.slice(from).filter((x) => x.kind === 'save-config');
        ok(sv1.length === 1 && JSON.stringify(sv1[0].body) === JSON.stringify({ cls: 'MyGate' }) && kinds(from).join() === 'slot-class,save-config',
           'then the existing save is asked once, with no folder confirmed yet');
        ok(st().klass === NEW['class'] && $('#czg-class-host .ui-menu').getAttribute('data-value') === NEW['class'] && last().gate_class === NEW['class'],
           'the new class is in the class list and picked');
        ok(/spectator choices were cleared/.test($('#czg-spect-body').textContent) && !('spectators' in last()) && $('#czg-spect-sum').textContent === 'not offered',
           'the class changed: the spectator ticks are cleared with the existing notice');
        const cf = $('#czg-new-body [data-czg-new-confirm]');
        ok(cf && lines('#czg-new-body [data-czg-new-confirm] p').slice(0, 6).join('\n') === ['File: ' + CONFIRM.path,
               'The environment imports quam_config from: ' + CONFIRM.env_folder, CONFIRM.notice, CONFIRM.warning,
               'Gates on this chip using this class:', 'q1-2: cz_old (live and active)'].join('\n'),
           'the save confirm shows the file, the folder the environment imports, the notice, the warning and the gates verbatim');
        ok(cf.querySelector('[data-czg-new-confirm-save]').textContent === 'Confirm folder and save' && cf.querySelector('[data-czg-new-cancel]').textContent === 'Cancel'
           && $('#czg-new-body [data-czg-new-create]').disabled && lines('#czg-new-body [data-czg-new-confirm] p').join(' ').indexOf('then writes ' + CONFIRM.path) > 0,
           'the confirm has the two choices and says what each does; Create class waits');
        ok(doc.activeElement === cf.querySelector('button') && cf.getAttribute('aria-live') === 'polite', 'confirm is announced and its first button receives focus');
        // cancel writes nothing and leaves a way back
        from = sent.length;
        cf.querySelector('[data-czg-new-cancel]').click();
        await ready();
        ok(!$('#czg-new-body [data-czg-new-confirm]') && !kinds(from).length && /Nothing was written/.test($('#czg-new-body [data-czg-new-done]').textContent)
           && $('#czg-new-body [data-czg-new-save]'),
           'Cancel: no request, the row says nothing was written and offers the save again');
        ok(doc.activeElement === $('#czg-new-body [data-czg-new-save]'), 'Cancel focuses Save the class file');
        for (const message of ['Class saving is locked while a run is in progress; try again when it ends. <i>Wait</i>', null]) {
            queue['save-config'].push(() => [409, Object.assign({ ok: false, error: 'scheduler_running' }, message ? { message } : {})]);
            $('#czg-new-body [data-czg-new-save]').click(); await ready();
            const line = $('[data-czg-new-problem]');
            ok(line.textContent === (message || 'scheduler_running') && !line.querySelector('i'),
               'class save lock shows the message as text or the existing error fallback');
        }
        queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
        $('#czg-new-body [data-czg-new-save]').click();
        await ready();
        from = sent.length;
        queue['save-config'].push(() => ({ ok: true, target: CONFIRM.path, dependency: 'This chip now needs quam_config/MyGate.py; every computer that opens this state needs the same file.',
                                           restart: 'A running QUAlibrate imports this new file the first time a state needs it.', warning: CONFIRM.warning,
                                           chip_warning: null, record_error: null, backup: null, proportional: null }));
        const confirmSave = $('#czg-new-body [data-czg-new-confirm-save]');
        confirmSave.click(); confirmSave.click();
        await ready();
        const sv2 = sent.slice(from).filter((x) => x.kind === 'save-config');
        ok(sv2.length === 1 && JSON.stringify(sv2[0].body) === JSON.stringify({ cls: 'MyGate', confirmed_folder: CONFIRM.folder }),
           'Confirm sends the folder the server named back as confirmed');
        const doneLines = lines('#czg-new-body [data-czg-new-done] p');
        ok(doneLines.indexOf('Saved to ' + CONFIRM.path) >= 0 && doneLines.indexOf('This chip now needs quam_config/MyGate.py; every computer that opens this state needs the same file.') >= 0
           && doneLines.indexOf(CONFIRM.warning) >= 0 && !$('#czg-new-body [data-czg-new-confirm]') && !$('#czg-new-body [data-czg-new-save]')
           && !$('#czg-new-body [data-czg-new-create]').disabled,
           'saved: the target, the dependency line and the warning as the server gave them; the confirm is gone');
        ok(kinds(0).filter((k) => k === 'create').length === sent.filter((x) => x.kind === 'create').length && !kinds(from).some((k) => k === 'create' || k === 'check')
           && st().klass === NEW['class'],
           'making and saving a class creates no gate and runs no check');
        const said = panel.textContent + $('#czg-spect-body').textContent;
        ok(!/ -- |->|\bCAS\b|patch/i.test(said), 'the new texts carry no arrow, no double dash and no internal word');
        // the client holds no limit: nothing multiplies or divides a unit or a range
        const SRC = fs.readFileSync(path.join(ROOT, 'static', 'cz-gate.js'), 'utf8');
        const calc = SRC.split('\n').filter((l) => !/^\s*(\*|\/\*|\/\/)/.test(l) && /\b(unit|reachable|reach)\b/.test(l) &&/[^\/*]([*\/])\s*[\d(a-zA-Z]/.test(l.replace(/\/\*.*?\*\/|\/\/.*$/g, '').replace(/'[^']*'|\/[^\/\n]+\/[gimsuy]*/g, '')));
        ok(!calc.length && !/AMP_SCALE|scale_limit/i.test(SRC), 'the client computes no range from a unit and names no scale limit: ' + calc.join(' | '));

        // Contract edge cases and delayed answers use explicit conditions.
        const openSlot = async (classes) => {
            window.CzGate.close();
            dataOverride.classes = classes || [CZ, SA, NEW];
            await window.CzGate.open({ pair: 'q1-2' });
            pick('#czg-class-host', SA['class']);
            await ready();
        };
        SA.slots = [slot('control'), slot('target'), slot('coupler')];
        await openSlot();
        ok(/selection replaced the slots and spectators/.test($('#czg-new-body').textContent), 'gate class refill explains the replaced row choices');
        for (const [field, words] of [['control', 'Control flux pulse'], ['target', 'Target flux pulse'], ['coupler', 'Coupler pulse']]) {
            const select = [...doc.querySelectorAll('#czg-slots select')].filter((i) => i.getAttribute('aria-label') === 'Pulse class of ' + words)[0];
            ok(select && [...doc.querySelectorAll('#czg-slots .ui-slot-head')].some((i) => i.textContent.startsWith(words)), field + ' slot title and pulse class label use words');
        }
        dataOverride.pairs[0].spectator_candidates.push(cand('q5', 5, 0.3, null));
        await openSlot();
        tickBox('q5', true);
        await ready();
        ok(allowed('q5').textContent === '' && rowOf('q5').textContent.includes('0.3 V') && !rowOf('q5').textContent.includes('no unit operation on this line yet'), 'unit with no server range stays blank without an absent operation hint');
        tickBox('q5', false); await ready();
        queue.check.push((b) => { const j = checkOk(b); j.pairs[0].checks = ['note', 'pass', 'fail'].map((status) => ({ name: 'spectator_scale', status, message: 'Server sentence.' })); return j; });
        prim().click();
        await until(() => !st().busy);
        ok([...doc.querySelectorAll('[data-check="spectator_scale"]')].length === 3 && [...doc.querySelectorAll('[data-check="spectator_scale"]')].every((i) => i.textContent.includes('Spectator levels') && !i.textContent.includes('spectator_scale')), 'spectator checks have readable words for every status');
        await openSlot();
        const badName = '<b>x</b> & more', problem = 'Bad <i>name</i> & more';
        pick('#czg-new-preset-host', 'both_qubits');
        type($('#czg-new-name'), badName);
        const refusedTicks = ticks(), refusedPreset = st().newSlot.preset, refusedSpect = spectOn();
        queue['slot-class'].push(() => [400, { ok: false, problems: [problem] }]);
        $('#czg-new-body [data-czg-new-create]').focus();
        $('#czg-new-body [data-czg-new-create]').click();
        await ready();
        ok($('#czg-new-name').value === badName && ticks() === refusedTicks && st().newSlot.preset === refusedPreset && spectOn() === refusedSpect, 'refusal keeps the typed name preset and ticks');
        const refusal = $('[data-czg-new-problem]');
        ok(refusal.textContent === problem && !refusal.querySelector('i') && refusal.getAttribute('role') === 'alert' && doc.activeElement === $('[data-czg-new-create]'), 'refusal is literal text announced with focus on Create');
        let release;
        const delayed = (kind, answer) => { queue[kind].push(() => new Promise((r) => { release = () => r(answer); })); };
        const attemptEdits = () => {
            const before = JSON.stringify(st().newSlot);
            type($('#czg-new-name'), 'Other');
            tickNew('target', false);
            const sp = $('[data-czg-new-spect]'); sp.checked = !sp.checked; sp.dispatchEvent(new window.Event('change', { bubbles: true }));
            pick('#czg-new-preset-host', 'only_coupler');
            pick('#czg-class-host', CZ['class']);
            ok(JSON.stringify(st().newSlot) === before && $('#czg-new-name').disabled && $('#czg-new-preset').disabled, 'busy row rejects name preset slot spectator and gate class refill edits');
        };
        type($('#czg-new-name'), 'MyGate');
        delayed('slot-class', { ok: true, cls: 'MyGate', qclass: NEW['class'] });
        queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
        $('[data-czg-new-create]').click();
        await until(() => !!release);
        attemptEdits();
        release(); await ready();
        ok(st().newSlot.cls === 'MyGate' && st().newSlot.name === 'MyGate' && $('[data-czg-new-confirm]'), 'confirm belongs to the row that asked');
        $('[data-czg-new-cancel]').click();
        release = null;
        delayed('save-config', [409, { ok: false, confirm: CONFIRM }]);
        $('[data-czg-new-save]').click();
        await until(() => !!release);
        attemptEdits(); release(); await ready();
        ok(st().newSlot.cls === 'MyGate' && $('[data-czg-new-confirm-save]'), 'save answer retains a working confirm for the same class');
        // Outside fields keep their focus and caret while row answers arrive.
        for (const kind of ['slot-class', 'save-config']) {
            for (const field of ['name', 'pulse', 'spectator']) {
                await openSlot();
                tickBox('q4', true);
                type(fld('q4', 'level'), '0.1'); type(fld('q4', 'unit'), '0.2'); await ready();
                type($('#czg-new-name'), 'MyGate'); release = null;
                if (kind === 'slot-class') {
                    delayed(kind, { ok: true, cls: 'MyGate', qclass: NEW['class'] });
                    queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
                    $('[data-czg-new-create]').click();
                } else {
                    queue['slot-class'].push(() => ({ ok: true, cls: 'MyGate', qclass: SA['class'] }));
                    queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
                    $('[data-czg-new-create]').click(); await ready();
                    $('[data-czg-new-cancel]').click();
                    delayed(kind, [409, { ok: false, confirm: CONFIRM }]);
                    $('[data-czg-new-save]').click();
                }
                await until(() => !!release);
                const input = field === 'name' ? $('#czg-name') : field === 'pulse' ? doc.querySelector('#czg-slots input[data-czg-name="length"]') : fld('q4', 'level');
                input.focus(); input.setSelectionRange(1, 1);
                const pickedBefore = st().klass, rawBefore = JSON.stringify(st().raw);
                release(); await until(() => !st().newSlot.busy);
                const madeLabel = kind === 'slot-class' ? NEW.label : SA.label;
                const madeSaid = kind === 'slot-class' ? ' is made; pick it from the Class list to use it.' : ' is made and picked.';
                ok(st().klass === pickedBefore && JSON.stringify(st().raw) === rawBefore && $('#czg-new-body').textContent.includes('The class ' + madeLabel + madeSaid) && (kind === 'slot-class' || !/pick it from the Class list/.test($('#czg-new-body').textContent)),
                   kind + ' answer with the focus in outside ' + field + ' lists the class without picking it or touching typed values');
                const focused = doc.activeElement === input && input.selectionStart === 1 && input.selectionEnd === 1;
                if (focused) doc.activeElement.setRangeText('7', 1, 1, 'end');
                ok(focused && input.value[1] === '7', kind + ' answer keeps focus caret and typing in outside ' + field);
            }
        }
        // The save answer must keep the row locked through the list answer.
        await openSlot(); type($('#czg-new-name'), 'MyGate');
        queue['slot-class'].push(() => ({ ok: true, cls: 'MyGate', qclass: SA['class'] }));
        queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
        $('[data-czg-new-create]').click(); await ready();
        release = null; delayed('save-config', { ok: true, target: CONFIRM.path });
        $('[data-czg-new-confirm-save]').click(); await until(() => !!release);
        let listRequested = false, releaseList;
        holdDialog = () => { listRequested = true; return new Promise((resolve) => { releaseList = () => resolve({ ok: true, status: 200, text: () => Promise.resolve(dialogHtml()) }); }); };
        release(); await until(() => listRequested);
        ok(st().newSlot.busy && $('#czg-new-name').disabled, 'save keeps the row locked until the class list answers');
        attemptEdits(); releaseList(); await ready();
        // Each class answer must leave running and completed gate flows alone.
        for (const listed of [true, false]) for (const kind of ['slot-class', 'save-config']) {
            for (const flow of ['check', 'create', 'done']) {
                if (!listed && kind === 'save-config') continue;
                await openSlot([CZ, SA]);
                type($('#czg-new-name'), 'MyGate');
                release = null;
                delayed(kind, kind === 'slot-class' ? { ok: true, cls: 'MyGate', qclass: NEW['class'] } : { ok: true, target: CONFIRM.path });
                if (kind === 'slot-class') { queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]); $('[data-czg-new-create]').click(); }
                else {
                    // Make a draft before holding its second save request.
                    queue['slot-class'].push(() => ({ ok: true, cls: 'MyGate', qclass: SA['class'] }));
                    queue['save-config'].unshift(() => [409, { ok: false, confirm: CONFIRM }]);
                    $('[data-czg-new-create]').click(); await ready();
                    $('[data-czg-new-cancel]').click();
                    $('[data-czg-new-save]').click();
                }
                await until(() => !!release);
                let finish;
                if (flow === 'check') hold = new Promise((r) => { finish = r; });
                prim().click();
                if (flow !== 'check') {
                    await until(() => !st().busy && st().phase === 'create');
                    if (flow === 'create') holdCreate = new Promise((r) => { finish = r; });
                    prim().click();
                    if (flow === 'done') await until(() => st().phase === 'done');
                }
                const seq = st().seq, phase = st().phase, typed = JSON.stringify(st().raw), selected = st().klass, banner = alertText(), plans = count('plan');
                dataOverride.classes = listed ? [CZ, SA, NEW] : [CZ, SA];
                // Put the focus on the body (a disabled field ignores blur): a focused node removed.
                { const spot = doc.createElement('button'); doc.body.appendChild(spot); spot.focus(); spot.remove(); }
                const bodyFocus = doc.activeElement === doc.body || !doc.activeElement;
                release(); await until(() => !st().newSlot.busy);
                await until(() => !!$('[data-czg-new-confirm]') || st().newSlot.saved);
                ok(st().seq === seq && st().phase === phase && JSON.stringify(st().raw) === typed && st().klass === selected && count('plan') === plans && (flow !== 'done' || (prim().textContent === 'Close' && alertText() === banner && /Created/.test(banner))), kind + ' answer preserves ' + flow + ' flow without picking or replanning' + (listed ? '' : ' (class not listed)'));
                const said = $('#czg-new-body').textContent;
                if (listed) {
                    const madeLabel = kind === 'slot-class' ? NEW.label : SA.label;
                    const madeSaid = kind === 'slot-class' ? ' is made; pick it from the Class list to use it.' : ' is made and picked.';
                    ok(bodyFocus && said.includes('The class ' + madeLabel + madeSaid) && (kind === 'slot-class' || !/pick it from the Class list/.test(said)) && !said.includes('quam_config.'), kind + ' says the truth about a class made during ' + flow + ', with the focus on the body');
                } else {
                    ok(bodyFocus && said.includes('The class MyGate is made; the Class list does not offer it for this pair.') && !/pick it from the Class list/.test(said), kind + ' says a class made during ' + flow + ' is not offered when the list lacks it');
                }
                if (finish) { finish(); await until(() => !st().busy); }
                ok($('#czg-class-host').textContent.includes(NEW.label) === listed && st().klass === selected, kind + ' lists the made class after ' + flow + ' without changing the pick' + (listed ? '' : ' (class not listed)'));
            }
        }
        // The row sentence in every branch: free / check / create / done x focus in the row / outside x
        // the made class already picked / listed and not picked before / not listed. One sentence at a time,
        // never the qualified id, after both the create and the save list answers.
        const S_PICKED = (l) => 'The class ' + l + ' is made and picked.';
        const S_PICK = (l) => 'The class ' + l + ' is made; pick it from the Class list to use it.';
        const S_UNLISTED = (l) => 'The class ' + l + ' is made; the Class list does not offer it for this pair.';
        for (const flow of ['free', 'check', 'create', 'done']) for (const where of ['in', 'out']) for (const made of ['picked', 'listed', 'unlisted']) {
            const cell = flow + ' / focus ' + where + ' / ' + made;
            await openSlot(made === 'unlisted' ? [CZ, SA] : [CZ, SA, NEW]);
            type($('#czg-new-name'), 'MyGate');
            const qclass = made === 'picked' ? SA['class'] : NEW['class'];
            release = null;
            delayed('slot-class', { ok: true, cls: 'MyGate', qclass: qclass });
            queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
            $('[data-czg-new-create]').click();
            await until(() => !!release);
            let finish;
            if (flow !== 'free') {
                if (flow === 'check') hold = new Promise((r) => { finish = r; });
                prim().click();
                if (flow !== 'check') {
                    await until(() => !st().busy && st().phase === 'create');
                    if (flow === 'create') holdCreate = new Promise((r) => { finish = r; });
                    prim().click();
                    if (flow === 'done') await until(() => st().phase === 'done');
                }
            }
            const label = made === 'picked' ? SA.label : made === 'listed' ? NEW.label : 'MyGate';
            const before = st().klass;
            const focusSpot = () => {
                if (where === 'out') { const o = !$('#czg-name').disabled ? $('#czg-name') : ($('#czg-zone-gate [data-czg-outside-spot]') || $('#czg-zone-gate').appendChild(Object.assign(doc.createElement('span'), { tabIndex: 0 }))); o.setAttribute('data-czg-outside-spot', ''); o.focus(); return doc.activeElement === o && !$('#czg-new-body').contains(o); }
                const spot = doc.createElement('button'); doc.body.appendChild(spot); spot.focus(); spot.remove();
                return doc.activeElement === doc.body || !doc.activeElement;
            };
            const placed1 = focusSpot();
            release();
            await until(() => !st().newSlot.busy && !!$('[data-czg-new-confirm]'));
            const pickedNow = made !== 'unlisted' && st().klass === qclass;
            const expect = made === 'unlisted' ? S_UNLISTED(label) : pickedNow ? S_PICKED(label) : S_PICK(label);
            const wantPicked = made === 'picked' || (made === 'listed' && flow === 'free' && where === 'in');
            const said1 = $('#czg-new-body').textContent;
            ok(placed1 && pickedNow === wantPicked && (wantPicked || st().klass === before) && said1.includes(expect) && !said1.includes('quam_config.'),
               cell + ': the create answer says "' + expect + '"');
            // a held check or create cannot take a second request: the save answer comes once it is over
            if (finish) { finish(); finish = null; await until(() => !st().busy); }
            queue['save-config'].push(() => ({ ok: true, target: CONFIRM.path }));
            const placed2 = focusSpot();
            $('[data-czg-new-confirm-save]').click();
            await until(() => !st().newSlot.busy && st().newSlot.saved);
            const said2 = $('#czg-new-body').textContent;
            const counts = [S_PICKED(label), S_PICK(label), S_UNLISTED(label)].map((x) => said2.split(x).length - 1);
            const pickedAfter = made !== 'unlisted' && st().klass === qclass;
            const expect2 = made === 'unlisted' ? S_UNLISTED(label) : pickedAfter ? S_PICKED(label) : S_PICK(label);
            ok(placed2 && counts.reduce((a, b) => a + b, 0) === 1 && said2.includes(expect2) && !said2.includes('quam_config.') && (flow === 'check' || flow === 'create' || pickedAfter === wantPicked)
               && st().newSlot.done.length === new Set(st().newSlot.done).size,
               cell + ': after the save answer the row says "' + expect2 + '" once and nothing twice');
            if (finish) { finish(); await until(() => !st().busy); }
        }
        // A class list that cannot be read again leaves the made sentence: the plain class name, never the module path.
        await openSlot([CZ, SA]);
        type($('#czg-new-name'), 'MyGate');
        queue['slot-class'].push(() => ({ ok: true, cls: 'MyGate', qclass: NEW['class'] }));
        holdDialog = () => Promise.reject(new Error('offline'));
        $('[data-czg-new-create]').click();
        await until(() => $('#czg-new-body').textContent.includes('could not be read again'));
        ok($('#czg-new-body').textContent.includes('The class MyGate is made as a draft.') && !$('#czg-new-body').textContent.includes('quam_config.'), 'an unread class list leaves the plain made sentence, never the module path');
        await until(() => !st().newSlot.busy);
        const UNREAD = 'The class list could not be read again.';
        const unreadCount = () => $('#czg-new-body').textContent.split(UNREAD).length - 1;
        const madeFlow = async (classes, readFails) => {
            await openSlot([CZ, SA]);
            type($('#czg-new-name'), 'MyGate');
            queue['slot-class'].push(() => { dataOverride.classes = classes; return { ok: true, cls: 'MyGate', qclass: NEW['class'] }; });
            queue['save-config'].push(() => ({ ok: true, target: CONFIRM.path }));
            readFails();
            $('[data-czg-new-create]').click();
            await until(() => st().newSlot.saved && !st().newSlot.busy);
        };
        // A made class that cannot run on the pair is not offered: the row gives its reason, never "pick it".
        const NEWU = Object.assign({}, NEW, { available: false, why: 'Not for this pair.' });
        await madeFlow([CZ, SA, NEWU], () => {});
        { const said = $('#czg-new-body').textContent;
          ok(said.includes('The class ' + NEW.label + ' is made; it cannot be used on this pair. Not for this pair.') && !/pick it from the Class list/.test(said) && st().klass === SA['class'],
             'a made class that cannot run on the pair says why and is not offered to pick'); }
        // The answer arrives in a free flow with the focus in the new-class row: a made class that cannot run
        // never takes the user's hand pick, its by-hand mark or the entered values.
        {
            await openSlot([CZ, SA]);
            const amp2 = doc.querySelector('#czg-slots input[data-czg-name="length"]');
            type(amp2, '48');
            await ready();
            type($('#czg-new-name'), 'MyGate');
            queue['slot-class'].push(() => { dataOverride.classes = [CZ, SA, NEWU]; return { ok: true, cls: 'MyGate', qclass: NEW['class'] }; });
            queue['save-config'].push(() => ({ ok: true, target: CONFIRM.path }));
            const enteredBefore = JSON.stringify(st().entered);
            const nameBox = $('#czg-new-name'); nameBox.focus();
            const inRow = $('#czg-new-body').contains(doc.activeElement);
            $('[data-czg-new-create]').click();
            await until(() => !st().newSlot.busy && !!st().newSlot.done.length && /is made/.test($('#czg-new-body').textContent));
            const said = $('#czg-new-body').textContent;
            ok(inRow && st().klass === SA['class'] && st().klassByHand === true && JSON.stringify(st().entered) === enteredBefore && enteredBefore.includes('48')
               && !/The class changed to/.test($('#czg-class-why').textContent) && !/The class changed to/.test(doc.body.textContent)
               && said.includes('The class ' + NEW.label + ' is made; it cannot be used on this pair. Not for this pair.'),
               'a made class that cannot run, answered in a free flow with the focus in the new-class row, leaves the hand pick, its mark and the entered values');
        }
        // The latest list answer wins: a failed read on the create answer, then a good read on the save answer.
        await madeFlow([CZ, SA, NEW], () => { holdDialog = () => Promise.reject(new Error('offline')); });
        { const said = $('#czg-new-body').textContent;
          ok(unreadCount() === 0 && said.includes('The class ' + NEW.label + ' is made') && said.includes('Saved to') && st().newSlot.done.length === new Set(st().newSlot.done).size,
             'a later good list read removes the unread line, the row holds the latest answer only'); }
        // Two failing reads show the unread line once.
        await madeFlow([CZ, SA, NEW], () => { failDialog = true; });
        failDialog = false;
        ok(unreadCount() === 1 && $('#czg-new-body').textContent.includes('Saved to'), 'two failed list reads show the unread line exactly once');
        await openSlot();
        release = null;
        delayed('slot-class', { ok: true, cls: 'MyGate', qclass: NEW['class'] });
        $('[data-czg-new-create]').click(); await until(() => !!release);
        await openSlot();
        const next = JSON.stringify(st().newSlot), nextSeq = st().seq, requests = sent.length, previousReplies = replies;
        release(); await until(() => replies > previousReplies);
        ok(JSON.stringify(st().newSlot) === next && st().seq === nextSeq && sent.length === requests && st().klass === SA['class'], 'closed dialog class answer changes nothing in the next dialog');
        queue['slot-class'].push(() => ({ ok: true, cls: badName, qclass: badName }));
        queue['save-config'].push(() => [409, { ok: false, confirm: CONFIRM }]);
        type($('#czg-new-name'), badName); $('[data-czg-new-create]').click(); await ready();
        ok($('[data-czg-new-done]').textContent.includes(badName) && !$('[data-czg-new-done]').querySelector('b'), 'server class name with markup is shown as literal text');
        // One missing unit amplitude is shared across pairs, with ownership transfer.
        dataOverride.pairs.push(Object.assign({}, DATA.pairs[1], { spectator_candidates: [cand('q4', 4, null, null)] }));
        await openSlot();
        const more = $('#czg-more-list input'); more.checked = true; more.dispatchEvent(new window.Event('change', { bubbles: true }));
        tickBox('q4', true); type(fld('q4', 'level'), '0.1'); type(fld('q4', 'unit'), '0.2');
        const second = () => $('#czg-spect-body [data-czg-spect-pair="q2-3"] [data-czg-spect-row="q4"]');
        const secondTick = second().querySelector('input[type="checkbox"]'); secondTick.checked = true; secondTick.dispatchEvent(new window.Event('change', { bubbles: true }));
        const secondField = (name) => second().querySelector('input[data-czg-name="' + name + '"]');
        type(secondField('level'), '0.15'); await ready();
        ok(!secondField('unit') && second().textContent.includes('0.2 V') && second().textContent.includes('Shared with the spectator row of q1-2.') && !prim().disabled && last().spectators['q1-2'][0].unit === 0.2 && !('unit' in last().spectators['q2-3'][0]), 'shared spectator unit is read-only on later row and sent once without blocking Check');
        type(fld('q4', 'unit'), '0.3'); await ready();
        ok(second().textContent.includes('0.3 V') && second().textContent.includes('Shared with the spectator row of q1-2.') && last().spectators['q1-2'][0].unit === 0.3 && !('unit' in last().spectators['q2-3'][0]), 'an owner unit edited after a later row shares it updates the shared note and is sent once');
        type(fld('q4', 'unit'), '0.45');
        ok(second().textContent.includes('0.45 V') && second().textContent.includes('Shared with the spectator row of q1-2.'), 'the shared unit cell follows each keystroke at once, before any answer arrives');
        type(fld('q4', 'unit'), '0.3'); await ready();
        // Typing in a unit field keeps the focus and the caret, also while the unit is shared and rows redraw.
        {
            const keyIn = (ch) => {
                const u = fld('q4', 'unit');
                const at = u.selectionStart;
                u.setRangeText(ch, at, at, 'end');
                u.dispatchEvent(new window.Event('input', { bubbles: true }));
                const now = fld('q4', 'unit');
                ok(now && doc.activeElement === now && now.selectionStart === at + 1 && now.selectionEnd === at + 1, 'shared unit: the unit field keeps the focus and the caret after typing ' + ch);
            };
            type(fld('q4', 'unit'), '0.2'); const u0 = fld('q4', 'unit'); u0.focus(); u0.setSelectionRange(2, 2);
            keyIn('5'); keyIn('1');
            ok(fld('q4', 'unit').value === '0.512', 'two characters typed in the middle land where the caret was: ' + fld('q4', 'unit').value);
            type(fld('q4', 'unit'), '0.2'); await ready();
        }
        type(fld('q4', 'unit'), '0.2'); await ready();
        tickBox('q4', false); await ready();
        ok(secondField('unit').value === '0.2' && last().spectators['q2-3'][0].unit === 0.2 && !prim().disabled, 'unticking the first row transfers the typed unit to the remaining row');
        tickBox('q4', true); type(fld('q4', 'level'), '0.1'); type(fld('q4', 'unit'), '0.4'); await ready();
        ok(fld('q4', 'unit').value === '0.4' && !secondField('unit') && second().textContent.includes('0.4 V') && last().spectators['q1-2'][0].unit === 0.4 && !('unit' in last().spectators['q2-3'][0]), 'owner moving back then an edit sends one unit with the new value');
        // The shown pair stays first; the two extra pairs share one absent unit.
        dataOverride.pairs.push(Object.assign({}, DATA.pairs[1], { id: 'q5-6', spectator_candidates: [cand('q4', 4, null, null)] }));
        const morePair = (pair, on) => { const i = $('#czg-more-list input[value="' + pair + '"]'); i.checked = on; i.dispatchEvent(new window.Event('change', { bubbles: true })); };
        const spectRow = (pair) => $('#czg-spect-body [data-czg-spect-pair="' + pair + '"] [data-czg-spect-row="q4"]');
        const rowField = (pair, name) => spectRow(pair).querySelector('input[data-czg-name="' + name + '"]');
        const rowTick = (pair, on) => { const i = spectRow(pair).querySelector('input[type="checkbox"]'); i.checked = on; i.dispatchEvent(new window.Event('change', { bubbles: true })); };
        for (const door of ['more', 'spectator', 'pair', 'class']) {
            await openSlot(); morePair('q2-3', true); morePair('q5-6', true);
            rowTick('q2-3', true); type(rowField('q2-3', 'level'), '0.1'); type(rowField('q2-3', 'unit'), '0.2');
            rowTick('q5-6', true); type(rowField('q5-6', 'level'), '0.1'); await ready();
            if (door === 'more') morePair('q2-3', false);
            if (door === 'spectator') rowTick('q2-3', false);
            if (door === 'pair') {
                const pair = $('#czg-pair'); pair.value = 'q2-3'; pair.dispatchEvent(new window.Event('change', { bubbles: true }));
                morePair('q5-6', true); rowTick('q5-6', true); type(rowField('q5-6', 'level'), '0.1');
            }
            if (door === 'class') {
                pick('#czg-class-host', CZ['class']); pick('#czg-class-host', SA['class']);
                rowTick('q5-6', true); type(rowField('q5-6', 'level'), '0.1');
            }
            await ready();
            ok(rowField('q5-6', 'unit').value === '0.2' && JSON.stringify(last().spectators) === JSON.stringify({ 'q5-6': [{ qubit: 'q4', length: 100, level: 0.1, unit: 0.2 }] }) && !prim().disabled, door + ' removal preserves the shared unit on the next owner and in the request');
        }
        await openSlot(); morePair('q2-3', true); morePair('q5-6', true);
        rowTick('q2-3', true); type(rowField('q2-3', 'level'), '0.1');
        rowTick('q5-6', true); type(rowField('q5-6', 'level'), '0.1'); morePair('q2-3', false); await ready();
        ok(rowField('q5-6', 'unit').value === '' && errOf(rowField('q5-6', 'unit')) === 'Type the unit amplitude in V: this line has no unit operation yet.' && prim().disabled && !('unit' in last().spectators['q5-6'][0]), 'a blank new owner needs a unit and blocks Check while sending no empty unit');
        const waits = fs.readFileSync(__filename, 'utf8').split('// ---- slot gate classes:')[1].split('// the chart component missing')[0];
        ok(!/await\s+settle\s*\(/.test(waits) && /const until = async/.test(waits), 'the slot tests wait on capped conditions without fixed settles');
        unitReach = null;
        window.CzGate.close();
        dataOverride = null;
    }

    // ---- sequence steps of a new slot class, and the pair's own lines
    {
        const SQ = DATA.classes[0].slots[0].pulse_classes;
        const slot = (n, req) => ({ field: n + '_flux_pulse', line: n, line_words: 'x', required: req, pulse_classes: SQ });
        const CZ = { 'class': 'G.CZGate', label: 'CZ gate', layout: 'known', available: true, why: '', models: [], spectators: { offered: true, why: '' },
                     slots: [{ field: 'flux_pulse_qubit', line: 'moving', line_words: 'x', required: true, pulse_classes: SQ }] };
        const ST = { 'class': 'quam_config.Stepped.Stepped', label: 'Stepped (slot class SM made)', layout: 'slot', available: true, why: '', models: [],
                     slots: [slot('control', true), slot('coupler', false)],
                     slot_spec: { slots: ['control'], spectators: false, before: [{ kind: 'wait', length: 24 }], after: [{ kind: 'xy', qubit: 'target', operation: 'x90' }] },
                     spectators: { offered: false, method: 'unit', why: 'no' } };
        const PRESETS = [{ key: 'only_coupler', label: 'Coupler only', slots: ['coupler'] },
                         { key: 'both_qubits', label: 'Control and target', slots: ['control', 'target'] }];
        const XY = { control: ['x180', 'x90'], target: ['x90'] };
        const p12 = Object.assign({}, DATA.pairs[0], { spectator_candidates: [], lines: { control: true, target: true, coupler: true }, xy_operations: XY });
        const fixed = Object.assign({}, DATA.pairs[1] || DATA.pairs[0], { id: 'q2-3', spectator_candidates: [],
                                    lines: { control: true, target: true, coupler: false }, xy_operations: { control: null, target: [] } });
        const WAIT_RULE = { step: 4, min: 16, max: 1024,
                            empty: 'Sequence step {number} {where} the gate needs a wait length in ns.',
                            problem: 'Sequence step {number} {where} the gate waits {value} ns, a multiple of 4 ns.' };
        dataOverride = { pairs: [p12, fixed], selected: 'q1-2', env: 'lab', classes: [CZ], slot_presets: PRESETS,
                         slot_names: ['control', 'target', 'coupler'], wait_rule: WAIT_RULE };
        const st = () => window.CzGate.state();
        const until = async (fn) => {
            const end = Date.now() + 3000;
            while (!fn()) { if (Date.now() > end) throw new Error('Condition wait expired: ' + fn); await tick(5); }
        };
        const lines = (sel) => [...doc.querySelectorAll(sel)].map((x) => x.textContent);
        const idle = () => until(() => st() && !st().newSlot.busy && !st().busy);
        const rows = (w) => [...doc.querySelectorAll('#czg-new-body [data-czg-step-row="' + w + '"]')];
        const desc = (w) => rows(w).map((r) => {
            const k = r.querySelector('[data-czg-step-kind]').value;
            if (k === 'wait') return 'wait:' + r.querySelector('[data-czg-step-length]').value;
            if (k === 'xy') return 'xy:' + r.querySelector('[data-czg-step-qubit]').value + ':' + r.querySelector('[data-czg-step-op]').value;
            return k;
        }).join(',');
        const btn = (attr, w, k) => $('#czg-new-body [' + attr + '="' + w + '"]' + (k === undefined ? '' : '[data-czg-step-index="' + k + '"]'));
        const add = (w, kind) => $('#czg-new-body [data-czg-step-add="' + w + '"][data-czg-step-add-kind="' + kind + '"]');
        const setVal = (n, v) => { n.focus(); n.value = v; n.dispatchEvent(new window.Event('change', { bubbles: true })); };
        const typeThenClick = (input, command, value, enter = false) => {
            type(input, value);
            ok(st().newSlot.before.concat(st().newSlot.after).some(s => s.kind === 'wait' && s.length === Number(value)),
               'wait input updates the typed value before blur');
            if (enter) input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            command.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
            input.dispatchEvent(new window.Event('change', { bubbles: true }));
            input.blur();
            ok(command.isConnected, 'blur keeps the pressed command attached');
            command.click();
        };
        await window.CzGate.open({ pair: 'q1-2' });
        await idle();
        ok(desc('before') === '' && desc('after') === '' && /No steps\./.test($('#czg-new-body [data-czg-steps="before"]').textContent)
           && $('#czg-new-body [data-czg-steps="before"] [role="list"]') && !add('before', 'xy').disabled,
           'steps: two empty ordered lists, Before and After, with add buttons');
        add('before', 'align').click(); add('before', 'wait').click(); add('before', 'xy').click();
        ok(desc('before') === 'align,wait:16,xy:control:x180' && doc.activeElement === add('before', 'xy'),
           'steps: add align, wait and xy pulse rows in order; the add button keeps the focus');
        ok([...btn('data-czg-step-op', 'before', 2).options].map((o) => o.value).join() === 'x180,x90',
           'steps: the xy operation is picked from the operations that qubit of this pair has');
        setVal(btn('data-czg-step-qubit', 'before', 2), 'target');
        ok(desc('before') === 'align,wait:16,xy:target:x90' && [...btn('data-czg-step-op', 'before', 2).options].map((o) => o.value).join() === 'x90',
           'steps: another qubit picks one of its own operations, and only its own are offered');
        setVal(btn('data-czg-step-length', 'before', 1), '40');
        ok(desc('before') === 'align,wait:40,xy:target:x90' && doc.activeElement === btn('data-czg-step-length', 'before', 1),
           'steps: a typed wait length is kept and the field keeps the focus');
        typeThenClick(btn('data-czg-step-length', 'before', 1), btn('data-czg-step-up', 'before', 2), '40');
        ok(desc('before') === 'align,xy:target:x90,wait:40' && btn('data-czg-step-up', 'before', 0).disabled && btn('data-czg-step-down', 'before', 2).disabled,
           'steps: Up moves a row one place; the first Up and the last Down are disabled');
        typeThenClick(btn('data-czg-step-length', 'before', 2), btn('data-czg-step-down', 'before', 0), '40');
        ok(desc('before') === 'xy:target:x90,align,wait:40', 'steps: Down moves a row one place');
        typeThenClick(btn('data-czg-step-length', 'before', 2), btn('data-czg-step-remove', 'before', 1), '40');
        ok(desc('before') === 'xy:target:x90,wait:40', 'steps: Remove takes that row out');
        typeThenClick(btn('data-czg-step-length', 'before', 1), add('after', 'wait'), '40');
        ok(rows('before').every((r) => r.querySelectorAll('[aria-label]').length >= 4) && rows('after').length === 1
           && rows('before')[0].getAttribute('aria-label') === 'Before the gate pulses, step 1',
           'steps: every row and control has words for a screen reader');
        ok(rows('before').concat(rows('after')).every((r) => r.classList.contains('ui-item') && r.children.length >= 2
           && r.children[0].classList.contains('ui-actions') && !r.children[0].querySelector('[data-czg-step-up]')
           && r.children[1].hasAttribute('data-czg-step-moves') && r.children[1].querySelectorAll('button').length === 3),
           'steps: each row is one kit item, its fields apart from its Up / Down / Remove buttons');
        ok(rows('before').concat(rows('after')).every(r =>
            [...r.querySelectorAll('[data-czg-step-moves] button')].map(b =>
                b.hasAttribute('data-czg-step-up') ? 'up' : b.hasAttribute('data-czg-step-down') ? 'down' : 'remove').join() === 'up,down,remove'),
           'row commands come in reading order: Up, Down, Remove');
        for (const enter of [false, true]) {
            const start = sent.length;
            queue['slot-class'].push(() => [400, { ok: false, error: 'A length is needed.' }]);
            type($('#czg-new-body input[data-czg-new-name]'), 'MyGate');
            typeThenClick(btn('data-czg-step-length', 'before', 1), $('#czg-new-body [data-czg-new-create]'), '48', enter);
            await idle();
            const requests = sent.slice(start).filter(x => x.kind === 'slot-class');
            ok(requests.length === 1 && requests[0].body.before[1].length === 48,
               'one Create click posts the typed length' + (enter ? ' after Enter' : ' directly'));
        }
        setVal(btn('data-czg-step-length', 'before', 1), '40');
        // the post body and the server's sentence at the row it names
        const SENT = 'Sequence step 2 before the gate waits "x"; a wait length is a whole number of ns.';
        let from = sent.length;
        queue['slot-class'].push(() => [400, { ok: false, error: SENT, problems: [SENT, 'Another problem.'], step: { where: 'before', index: 1 } }]);
        type($('#czg-new-body input[data-czg-new-name]'), 'MyGate');
        $('#czg-new-body [data-czg-new-create]').click();
        await idle();
        const mk = sent.slice(from).filter((x) => x.kind === 'slot-class')[0];
        ok(mk && JSON.stringify(mk.body) === JSON.stringify({ class_name: 'MyGate', slots: ['coupler'], spectators: false,
            before: [{ kind: 'xy', qubit: 'target', operation: 'x90' }, { kind: 'wait', length: 40 }], after: [{ kind: 'wait', length: 16 }], pair: 'q1-2' }),
           'steps: Create class posts the two lists in order and the pair the dialog is open on');
        const at = rows('before')[1].querySelector('[data-czg-step-problem]');
        ok(at && at.textContent === SENT && doc.querySelectorAll('#czg-new-body [data-czg-step-problem]').length === 1
           && lines('#czg-new-body [data-czg-new-problem]').join('|') === 'Another problem.',
           'steps: the server\'s sentence shows once, beside the row it names; the others stay below');
        const waitIn = btn('data-czg-step-length', 'before', 1);
        ok(waitIn.getAttribute('aria-invalid') === 'true' && at.id && waitIn.getAttribute('aria-describedby') === at.id,
           'steps: a refused wait marks its length field, tied to the sentence');
        setVal(btn('data-czg-step-length', 'before', 1), '48');
        ok(!doc.querySelector('#czg-new-body [data-czg-step-problem]')
           && btn('data-czg-step-length', 'before', 1).getAttribute('aria-invalid') !== 'true',
           'steps: an edit clears the old sentence and the mark');
        setVal(btn('data-czg-step-length', 'before', 1), '5');
        const local = rows('before')[1].querySelector('[data-czg-step-problem]');
        ok(local && local.textContent === 'Sequence step 2 before the gate waits 5 ns, a multiple of 4 ns.'
           && btn('data-czg-step-length', 'before', 1).getAttribute('aria-invalid') === 'true'
           && $('#czg-new-body [data-czg-new-create]').disabled,
           'steps: a bad wait is marked while typed, by the server rule, and Create class waits for a fix');
        setVal(btn('data-czg-step-length', 'before', 1), 'abc');
        const quoted = rows('before')[1].querySelector('[data-czg-step-problem]');
        ok(quoted && quoted.textContent === "Sequence step 2 before the gate waits 'abc' ns, a multiple of 4 ns.",
           'steps: a typed wait that is not a number is quoted as the server quotes it');
        setVal(btn('data-czg-step-length', 'before', 1), '48');
        ok(!doc.querySelector('#czg-new-body [data-czg-step-problem]')
           && !$('#czg-new-body [data-czg-new-create]').disabled,
           'steps: a fixed wait clears its mark and Create class is offered again');
        // a slot class picked: its stored steps prefill the lists
        dataOverride.classes = [CZ, ST];
        window.CzGate.close();
        await window.CzGate.open({ pair: 'q1-2' });
        await idle();
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: ST['class'], kind: 'pick' } }));
        await idle();
        ok(desc('before') === 'wait:24' && desc('after') === 'xy:target:x90', 'steps: a slot class picked prefills its stored steps');
        // a pair with a fixed coupler and no xy operations
        setVal($('#czg-pair'), 'q2-3');
        await idle();
        const cpl = [...doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]')].filter((i) => i.getAttribute('data-czg-new-slot') === 'coupler')[0];
        ok(cpl && cpl.disabled && !cpl.checked && $('#czg-new-body [data-czg-new-shut]')
           && /no coupler line that can hold a pulse/.test($('#czg-new-body [data-czg-new-shut]').textContent),
           'fixed coupler: the Coupler slot is neither ticked nor enabled, and the panel says why');
        const cplLabel = cpl.closest('label');
        ok(/\(not offered\)/.test(cplLabel.textContent) && cplLabel.classList.contains('ui-check--off'),
           'fixed coupler: the box\'s own label says not offered and wears the kit\'s disabled look');
        ok(!doc.querySelector('#czg-slots [data-czg-slot="coupler_flux_pulse"]') && !st().slotChannels.some((c) => c && /coupler/.test(JSON.stringify(c))),
           'fixed coupler: no coupler pulse line is offered');
        ok(add('before', 'xy').disabled && /neither qubit of this pair has an xy operation/.test($('#czg-new-body [data-czg-steps="before"]').textContent),
           'no xy operations: the xy pulse kind says so in words and cannot be added');
        window.CzGate.close();
        const ALL3 = PRESETS.concat([{ key: 'all_three', label: 'All three', slots: ['control', 'target', 'coupler'] }]);
        dataOverride = { pairs: [p12, fixed], selected: 'q1-2', env: 'lab', classes: [CZ], slot_presets: ALL3, slot_names: ['control', 'target', 'coupler'] };
        await window.CzGate.open({ pair: 'q1-2' });
        await idle();
        ok($('#czg-new-preset-host .ui-menu').getAttribute('data-value') === 'all_three'
           && [...doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]')].filter((i) => i.checked).map((i) => i.getAttribute('data-czg-new-slot')).join() === 'control,target,coupler',
           'tunable qubits and a tunable coupler: the default preset is the one with every line the pair has');
        setVal($('#czg-pair'), 'q2-3');
        await idle();
        ok($('#czg-new-preset-host .ui-menu').getAttribute('data-value') === 'both_qubits'
           && [...doc.querySelectorAll('#czg-new-body input[data-czg-new-slot]')].filter((i) => i.checked).map((i) => i.getAttribute('data-czg-new-slot')).join() === 'control,target',
           'fixed coupler: the default preset is one that fits the pair');
        window.CzGate.close();
        dataOverride = null;
    }

    // ---- start from a gate of a slotted class: the source pulse's class and values
    {
        const SQ = DATA.classes[0].slots[0].pulse_classes[0];
        const FT = { 'class': 'P.FlatTopPulse', label: 'Flat-top', length: 'explicit',
                     fields: [{ name: 'length', label: 'Length', kind: 'int', unit: 'ns', 'default': 60 },
                              { name: 'flat_length', label: 'Flat length', kind: 'int', unit: 'ns', 'default': 100 },
                              { name: 'amplitude', label: 'Amplitude', kind: 'float', unit: 'V', 'default': 0.05 }] };
        dataOverride = JSON.parse(JSON.stringify(DATA));
        dataOverride.classes[0].slots[0].pulse_classes = [SQ, FT];
        const st = () => window.CzGate.state();
        const until = async (fn) => {
            const end = Date.now() + 3000;
            while (!fn()) { if (Date.now() > end) throw new Error('Condition wait expired: ' + fn); await tick(5); }
        };
        const SRC = { __class__: 'P.FlatTopPulse', length: 120, flat_length: 20, amplitude: 0.08 };
        const copiedPlan = (b, refuse) => {
            const given = ((b.pulses.flux_pulse_qubit || {}).fields) || {};
            const prov = { length: 'copied', flat_length: 'copied', amplitude: 'copied' };
            Object.keys(given).forEach((k) => { prov[k] = 'given'; });
            return { ok: true, plan_sig: 'from-' + JSON.stringify(b.pulses), pairs: b.pairs.map((p) => refuse
                ? { pair: p, ok: false, refusals: [refuse], class_refusals: [], name_refusals: [] }
                : { pair: p, ok: true, refusals: [], creates: 2, paths: ['a', 'b'], layout: 'known',
                    operations: { flux_pulse_qubit: { key: 'op_' + p, channel: 'q1.z' } },
                    fields: { flux_pulse_qubit: prov },
                    pulses: { flux_pulse_qubit: Object.assign({}, SRC, given) } }) };
        };
        const fld = (name) => $('#czg-slots input[data-czg-field="flux_pulse_qubit"][data-czg-name="' + name + '"]');
        const badge = (name) => {
            const b = fld(name) && fld(name).closest('.ui-field').querySelector('.ui-field-head .ui-badge');
            return b ? b.textContent : '';
        };
        const cls = () => $('#czg-slots select[data-czg-slot="flux_pulse_qubit"]');
        const val = (name) => (fld(name) ? fld(name).value : null);
        const plans = () => sent.filter((x) => x.kind === 'plan');
        const pick = (node, v) => {
            node.value = v;
            node.dispatchEvent(new window.Event('change', { bubbles: true }));
        };
        await window.CzGate.open({ pair: 'q1-2' });
        await until(() => st() && !st().busy && cls());
        type($('#czg-name'), 'cz_from');
        let from = plans().length;
        for (let k = 0; k < 4; k++) queue.plan.push((b) => copiedPlan(b));
        pick($('#czg-from'), 'cz_old');
        await until(() => plans().length > from && !st().busy && fld('length'));
        const lastPlan = plans()[plans().length - 1].body;
        ok(lastPlan.copy_from === 'cz_old' && !lastPlan.pulses.flux_pulse_qubit,
           'start from a gate: the plan sends no class for the slot, so the source pulse class is copied');
        ok(cls().value === '@copied' && /As the gate cz_old \(Flat-top\)/.test(cls().selectedOptions[0].textContent),
           'start from a gate: the slot shows the source pulse class, not the first class of the list');
        ok(val('length') === '120' && val('flat_length') === '20' && val('amplitude') === '0.08'
           && badge('flat_length') === 'copied' && badge('amplitude') === 'copied',
           'start from a gate: every field of the source pulse is in the form, marked copied');
        // a refused plan: the other fields keep the values the plan uses
        queue.plan.length = 0;
        const TOO_SHORT = 'flux_pulse_qubit.length: The length 16 is too short for a flat length of 20 ns.';
        for (let k = 0; k < 4; k++) queue.plan.push((b) => copiedPlan(b, TOO_SHORT));
        from = plans().length;
        type(fld('length'), '16');
        ok(badge('length') === 'entered', 'a valid typed value is marked entered at once, before any plan');
        await until(() => plans().length > from && !st().busy && fld('length')
                    && fld('length').getAttribute('aria-invalid') === 'true');
        ok(val('amplitude') === '0.08' && val('flat_length') === '20'
           && badge('amplitude') === 'copied' && badge('flat_length') === 'copied' && cls().value === '@copied',
           'a marked field on a copied gate: the other fields show the copied values, not the class defaults');
        ok(prim().disabled && $('#czg-status').textContent === 'Fix the marked field, then press Check.',
           'a marked field holds Check and the footer says what to do');
        // a class picked by hand is sent; going back to class defaults drops the copied choice
        queue.plan.length = 0;
        from = plans().length;
        pick(cls(), 'P.SquarePulse');
        await until(() => plans().length > from && !st().busy);
        ok((plans()[plans().length - 1].body.pulses.flux_pulse_qubit || {})['class'] === 'P.SquarePulse',
           'a class picked by hand while copying is the class sent');
        pick($('#czg-from'), '');
        await until(() => !st().busy && cls() && !cls().querySelector('option[value="@copied"]'));
        ok(cls().value === 'P.SquarePulse',
           'back to class defaults: the hand-picked class stays, the copied choice is gone');
        window.CzGate.close();
        dataOverride = null;
    }
    // ---- start from a gate: no pulse picked by hand, and a source change shows no old plan
    {
        const FT = { 'class': 'P.FlatTopPulse', label: 'Flat-top', length: 'explicit',
                     fields: [{ name: 'length', label: 'Length', kind: 'int', unit: 'ns', 'default': 60 },
                              { name: 'flat_length', label: 'Flat length', kind: 'int', unit: 'ns', 'default': 100 }] };
        const SQ = DATA.classes[0].slots[0].pulse_classes[0];
        dataOverride = JSON.parse(JSON.stringify(DATA));
        dataOverride.pairs[0].gates = ['cz_a', 'cz_b'];
        dataOverride.classes[0].slots[0].pulse_classes = [SQ, FT];
        dataOverride.classes[0].slots.push({ field: 'coupler_flux_pulse', line: 'coupler', line_words: 'the coupler',
                                             required: false, pulse_classes: [SQ] });
        const st = () => window.CzGate.state();
        const until = async (fn) => {
            const end = Date.now() + 3000;
            while (!fn()) { if (Date.now() > end) throw new Error('Condition wait expired: ' + fn); await tick(5); }
        };
        const SRC_A = { flux_pulse_qubit: { __class__: 'P.FlatTopPulse', length: 120, flat_length: 20 },
                        coupler_flux_pulse: { __class__: 'P.SquarePulse', length: 40, amplitude: 0.02 } };
        const answer = (b, refuse) => ({ ok: true, plan_sig: 'two-' + JSON.stringify(b),
                                         pairs: b.pairs.map((p) => refuse
            ?{ pair: p, ok: false, refusals: [refuse], class_refusals: [], name_refusals: [] }
            : { pair: p, ok: true, refusals: [], creates: 2, paths: ['a', 'b'], layout: 'known',
                operations: { flux_pulse_qubit: { key: 'op_' + p, channel: 'q1.z' } },
                fields: { flux_pulse_qubit: { length: 'copied', flat_length: 'copied' } },
                pulses: JSON.parse(JSON.stringify(SRC_A)) }) });
        const sel = (f) => $('#czg-slots select[data-czg-slot="' + f + '"]');
        const fld = (name) => $('#czg-slots input[data-czg-field="flux_pulse_qubit"][data-czg-name="' + name + '"]');
        const plans = () => sent.filter((x) => x.kind === 'plan');
        const pick = (node, v) => {
            node.value = v;
            node.dispatchEvent(new window.Event('change', { bubbles: true }));
        };
        queue.plan.length = 0;
        await window.CzGate.open({ pair: 'q1-2' });
        await until(() => st() && !st().busy && sel('flux_pulse_qubit'));
        type($('#czg-name'), 'cz_two');
        let from = plans().length;
        for (let k = 0; k < 4; k++) queue.plan.push((b) => answer(b));
        pick($('#czg-from'), 'cz_a');
        await until(() => plans().length > from && !st().busy && fld('flat_length'));
        ok(!plans()[plans().length - 1].body.pulses.coupler_flux_pulse && sel('coupler_flux_pulse').value === '@copied',
           'start from a gate: an optional line not picked by hand sends nothing, so its pulse is copied');
        from = plans().length;
        pick(sel('coupler_flux_pulse'), '');
        await until(() => plans().length > from && !st().busy);
        ok(JSON.stringify(plans()[plans().length - 1].body.pulses.coupler_flux_pulse) === '{"none":true}'
           && sel('coupler_flux_pulse').value === '',
           'no pulse picked by hand while copying is sent as no pulse, so the source pulse is not copied');
        // a new source: the old plan's class and values are never shown as copied from it
        queue.plan.length = 0;
        const REFUSED = 'The gate cz_b holds no pulse in flux_pulse_qubit to copy.';
        for (let k = 0; k < 4; k++) queue.plan.push((b) => answer(b, REFUSED));
        from = plans().length;
        pick($('#czg-from'), 'cz_b');
        const shownNow = sel('flux_pulse_qubit').selectedOptions[0].textContent;
        const valueNow = fld('flat_length') ? fld('flat_length').value : null;
        await until(() => plans().length > from && !st().busy);
        const shownLater = sel('flux_pulse_qubit').selectedOptions[0].textContent;
        ok(shownNow === 'As the gate cz_b' && valueNow === null && shownLater === 'As the gate cz_b'
           && !fld('flat_length'),
           'a new source: the slot never shows the old source\'s class or values as copied from the new one');
        window.CzGate.close();
        queue.plan.length = 0;
        dataOverride = null;
    }

    // Older class marks and pair notes use quiet text outside folds.
    {
        const st = () => window.CzGate.state();
        const until = async (fn) => {
            const end = Date.now() + 3000;
            while (!fn()) { if (Date.now() > end) throw new Error('Condition wait expired: ' + fn); await tick(5); }
        };
        const markWords = 'Made by an older version; make it again to update.';
        const OLD_REASON = 'Needs a newer <b>environment</b> & more.';
        dataOverride = JSON.parse(JSON.stringify(DATA));
        dataOverride.classes[0].layout = 'slot';
        dataOverride.classes[0].older_render = true;
        dataOverride.classes[1].older_render = true;
        dataOverride.classes[1].why = OLD_REASON;
        await window.CzGate.open({ pair: 'q1-2' });
        await until(() => st() && st().plan);
        const options = [...doc.querySelectorAll('#czg-class-host [role="option"]')];
        const markOf = (o) => o.querySelectorAll('[data-czg-older-render]');
        const mark = options[0].querySelector('[data-czg-older-render]');
        ok(markOf(options[0]).length === 1, 'older mark: true shows exactly one sign');
        ok(mark && mark.textContent === markWords && mark.classList.contains('ui-option-desc') && !mark.closest('[aria-hidden="true"]')
           && mark.parentNode === options[0] && !options[0].querySelector('.ui-option-desc:not([data-czg-older-render])'),
           'older mark: enabled older class carries the words as its description part, under the title');
        ok(!markOf(options[2]).length, 'older mark: absent flag shows no sign');
        ok(!options[2].querySelector('.ui-option-desc'), 'older mark: a class with no flag and no reason has no description part');
        const dis = [...options[1].querySelectorAll('.ui-option-desc')];
        ok(options[1].getAttribute('aria-disabled') === 'true' && dis.length === 2 && dis[0].textContent === OLD_REASON
           && !dis[0].hasAttribute('data-czg-older-render') && dis[1].textContent === markWords && dis[1].hasAttribute('data-czg-older-render'),
           'older mark: disabled older class shows its reason and the words, each as a description part');
        ok(dis[0].children.length === 0, 'older mark: reason text with markup characters stays text');
        const kit = ['ui-option-check', 'ui-option-title', 'ui-option-desc'];
        ok(options.every((o) => [...o.children].every((ch) => kit.some((k) => ch.classList.contains(k)))),
           'pick options: every direct child is one of the kit option parts, nothing lands in the check column');
        const trig = () => $('#czg-class');
        const trigMarks = () => trig().querySelectorAll('[data-czg-older-render-trigger]');
        const pickClass = (v) => $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: v, kind: 'pick' } }));
        ok(trigMarks().length === 1 && trigMarks()[0].textContent === markWords && trigMarks()[0].className === 'ui-text-quiet ui-menu-note' && trig().getAttribute('aria-labelledby').split(' ').indexOf(trigMarks()[0].id) >= 0,
           'older mark: the closed picker says it for the selected older class and names it for screen readers');
        pickClass('L.TwoFlux');
        ok(trigMarks().length === 0 && trig().getAttribute('aria-labelledby').indexOf('czg-class-older') < 0, 'older mark: picking another class removes the words from the picker');
        pickClass('G.CZGate');
        ok(trigMarks().length === 1, 'older mark: picking the older class again shows the words');
        type($('#czg-name'), 'cz_keep');
        ok(trigMarks().length === 1, 'older mark: typing an input keeps the words, they describe the class');
        const more = $('#czg-more-list input'); more.checked = true; more.dispatchEvent(new window.Event('change', { bubbles: true }));
        window.clearTimeout(st().timer);
        const notes = ['Made with an older version.', 'Keep <b>these words</b> as text.'];
        const withNotes = (j) => { j.pairs.forEach((p) => { p.notes = notes.slice(); }); return j; };
        const shown = () => [...doc.querySelectorAll('#czg-notes [data-czg-note-pair]')];
        const exact = () => ['q1-2', 'q2-3'].every((pair) => {
            const rows = shown().filter((r) => r.getAttribute('data-czg-note-pair') === pair);
            return rows.length === notes.length && rows.every((r, k) => r.textContent === pair + ': ' + notes[k] && r.querySelector('span').textContent === notes[k]);
        });
        queue.plan.push((body) => withNotes(planOk(body)));
        await window.CzGate._plan();
        ok(exact(), 'pair notes: plan shows every sentence once for each named pair');
        ok(!$('#czg-notes').hidden && !$('#czg-notes').closest('details, [hidden]'), 'pair notes: visible outside folds');
        ok(shown().every((r) => r.className === 'ui-text-quiet') && !$('#czg-notes b'), 'pair notes: quiet literal markup');
        ok(/can be created/.test($('#czg-more-list').textContent), 'pair notes: ok plan keeps can be created words');
        type($('#czg-name'), 'cz_notes');
        ok($('#czg-notes').hidden && !$('#czg-notes').childNodes.length, 'pair notes: typing immediately removes stale notes and space');
        window.clearTimeout(st().timer);
        await window.CzGate._plan();
        ok($('#czg-notes').hidden && !$('#czg-notes').childNodes.length, 'pair notes: plan without notes leaves no box');
        queue.check.push((body) => withNotes(checkOk(body)));
        prim().click();
        await until(() => !st().busy && st().phase === 'create');
        ok(exact(), 'pair notes: check shows every sentence once for each named pair');
        ok(!prim().disabled && /^Create 2 gates/.test(prim().textContent) && /can be created/.test($('#czg-more-list').textContent), 'pair notes: ok with notes keeps Create enabled and can be created words');
        // Create currently returns no notes: retain only the notes of matching checked pairs.
        prim().click();
        await until(() => st().phase === 'done');
        ok(exact(), 'pair notes: create without notes retains checked notes once per created pair');
        window.CzGate.close();
        await window.CzGate.open({ pair: 'q1-2' });
        await until(() => st() && st().plan);
        window.clearTimeout(st().timer);
        queue.check.push((body) => withNotes(checkOk(body)));
        prim().click(); await until(() => !st().busy && st().phase === 'create');
        $('#czg-class-host .ui-menu').dispatchEvent(new window.CustomEvent('ui-menu-pick', { bubbles: true, detail: { value: 'L.TwoFlux', kind: 'pick' } }));
        ok($('#czg-notes').hidden && !$('#czg-notes').childNodes.length, 'pair notes: class change removes checked notes');
        window.clearTimeout(st().timer);
        await window.CzGate._plan();
        queue.check.push((body) => withNotes(checkOk(body)));
        prim().click(); await until(() => !st().busy && st().phase === 'create');
        // Recheck without editing, so the check renderer itself must replace the old notes.
        st().phase = 'check';
        prim().click(); await until(() => !st().busy && st().phase === 'create');
        ok($('#czg-notes').hidden && !$('#czg-notes').childNodes.length, 'pair notes: second Check without notes removes them');
        queue.create.push(() => ({ ok: true, created: [{ pair: 'q1-2', gate: 'cz_notes', state: 'verified', paths: ['a'], notes: notes.slice() }], skipped: [], sync: createSync }));
        prim().click(); await until(() => st().phase === 'done');
        ok(shown().length === 2 && shown().every((r, k) => r.querySelector('span').textContent === notes[k]) && !$('#czg-notes b'), 'pair notes: create answer notes override checked notes as literal text');
        // Reopen with another selected class: the picker reflects only the new class.
        window.CzGate.close();
        dataOverride = JSON.parse(JSON.stringify(DATA));
        dataOverride.classes[0].older_render = false;
        dataOverride.classes[2].older_render = true;
        await window.CzGate.open({ pair: 'q2-3' });
        await until(() => st() && st().plan);
        ok(doc.querySelectorAll('#czg-class [data-czg-older-render-trigger]').length === 0, 'older mark: a new open shows the words only for the newly selected class');
        window.CzGate.close();
        dataOverride = null;
    }

    // the chart component missing
    delete window.GateTimeline;
    window.CzGate.open({ pair: 'q1-2' });
    await settle();
    prim().click();
    await settle();
    ok(/Chart not loaded/.test($('#czg-chart').textContent), 'no chart component: said, never a throw');

    console.log(`Scroll checks: ${scrollAssertions}; existing dialog checks (${n - scrollAssertions} assertions)`);
    console.log(failed ? `FAIL cz_gate_dialog_selfcheck (${n} assertions, ${failed} failed)` : `ok cz_gate_dialog_selfcheck (${n} assertions)`);
    process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL crashed: ' + (e && e.stack || e)); process.exit(1); });
