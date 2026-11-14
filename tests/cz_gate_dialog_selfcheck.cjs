/* jsdom selfcheck: the CZ gate dialog (cz-gate.js) with a mocked fetch.
 *
 * The dialog HTML is the real template: tests/test_cz_gate_routes.py renders it
 * through the route and passes the file in CZG_DIALOG; run alone (npm run
 * selfcheck) the template is read and its few Jinja holes are filled here.
 * Pins: button states across input change / check / create, the busy guard,
 * the two stale rounds, error callouts in the dialog, several pairs, the
 * not-checked wording, the chart guard, and the row buttons (keyboard
 * reachable, never opening the row under them). */
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

function dialogHtml() {
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
const queue = { plan: [], check: [], create: [] };
const OUTCOME_CASES = JSON.parse(require('fs').readFileSync(require('path').join(__dirname, 'fixtures', 'gate_outcome_cases.json'), 'utf8')).cases;
let hold = null;                    // a pending check the test resolves itself
let holdCreate = null;
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
function planOk(body) {
    if (body.gate_class === 'L.TwoFlux') return planCopied(body);
    return { ok: true, plan_sig: 'sig-' + JSON.stringify(body.pulses) + body.gate_name + body.pairs.join(','),
             pairs: body.pairs.map((p) => p === 'q9-9' ? { pair: p, ok: false, refusals: ['No such pair.'] } : {
                 pair: p, ok: /^cz_/.test(body.gate_name), refusals: /^cz_/.test(body.gate_name) ? [] : ['The gate name must start with cz_.'],
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
                            actual: { length_ns: 100, max_v: 0.18, min_v: 0, scale: 1 },
                            differs: { length: false, amplitude: true }, tones: { length: null, amplitude: 'warn' }, why: ['The class shapes the waveform.'] }],
                 paths: ['a', 'b'],
                 verdict: (opts.refuse === p) ? { allowed: false, state: 'not checked', reason: 'A gate named so exists.' }
                     : { allowed: true, state: opts.unchecked ? 'not checked' : 'verified', reason: opts.unchecked ? 'No environment.' : '' } })) };
}
function respond(status, j) { return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(j), text: () => Promise.resolve('') }); }
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
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(dialogHtml()) });
    }
    const body = JSON.parse(opt.body);
    const kind = url.split('/').pop();
    sent.push({ kind, body });
    const q = queue[kind].shift();
    if (kind === 'create' && holdCreate) { const h = holdCreate; holdCreate = null; return h.then(() => respond(200, { ok: true, created: lastAllowed.map((pp) => ({ pair: pp, gate: 'cz_x', state: 'verified', paths: ['a'] })), skipped: [], count: lastAllowed.length, tray_html: '', sync: createSync })); }
    if (kind === 'check' && hold) { const h = hold; hold = null; return h.then(() => respond(200, q ? q(body) : checkOk(body))); }
    if (q) { const r = q(body); return Array.isArray(r) ? respond(r[0], r[1]) : respond(200, r); }
    if (kind === 'plan') return respond(200, planOk(body));
    if (kind === 'check') return respond(200, checkOk(body));
    return respond(200, { ok: true, created: lastAllowed.map((p) => ({ pair: p, gate: 'cz_x', state: 'verified', paths: ['a'] })), skipped: [], count: lastAllowed.length, tray_html: '', sync: createSync });
};
let driftState = 'mine';
let driftSeq = [];
const statusAsked = [];
let createSync = { auto_push: false, state: 'mine', unapplied: 1 };
const charts = [];
window.GateTimeline = { render: (host, data) => { charts.push(data); return { destroy() {} }; } };
const ajax = [];
window.htmx = { ajax: (m, u, o) => ajax.push(u), trigger: () => {} };
window.alert = () => { throw new Error('alert used'); };
window.eval(fs.readFileSync(path.join(ROOT, 'static', 'ui.js'), 'utf8'));
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

(async () => {
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
    ok($('.ui-slot.ui-line-1') && /^Qubit flux pulse/.test($('.ui-slot-head').textContent) && $('.ui-slot-chip').textContent === 'q1.z' && $('.ui-slot-head .ui-text-quiet').textContent === 'flux_pulse_qubit', 'slot: words, this pair\'s channel chip, line colour 1, field name quiet');
    ok(/control q1 \u2192 target q2/.test($('#czg-chips').textContent) && /moving qubit q1/.test($('#czg-chips').textContent), 'the head carries the pair as chips');
    ok(!$('#czg-chips').querySelector('input, select'), 'the mover is not an input');
    { const off = [...doc.querySelectorAll('#czg-class-list .ui-option[aria-disabled="true"]')]; ok(off.length > 0 && /SM does not know/.test(off[0].querySelector('.ui-option-desc').textContent) && $('#czg-class').classList.contains('ui-menu-trigger'), 'gate class is a kit menu; an unavailable class is a disabled row with its reason'); }
    ok(count('plan') === 1 && sent[0].body.pairs.join() === 'q1-2', 'the plan is asked on open');
    ok(prim().textContent === 'Check' && !prim().disabled, 'Check before a check');
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
    prim().click(); prim().click(); window.CzGate._check();
    ok(count('check') === 1, 'clicks while busy are ignored');
    release();
    await settle();
    ok(prim().textContent === 'Create gate' && !prim().disabled, 'after a passing check: Create gate');
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
    queue.check.push((b) => checkOk(b, { refuse: 'q2-3' }));
    prim().click();
    await settle();
    if (process.env.CZG_DEBUG) console.log('DBG', prim().textContent, '|', alertText(), '|', $('#czg-status').textContent, sent.slice(-2).map(s => s.kind));
    ok(prim().textContent === 'Create gate' && /1 of 2 cannot be created: q2-3/.test(alertText()) && /can still be created/.test(alertText()) && /A gate named so exists/.test(alertText()), 'one pair refused: named in the banner, the rest can still be created');
    ok(!$('#czg-pairs').hidden && $('#czg-pairs').querySelectorAll('[data-czg-pair-idx]').length === 2 && $('#czg-pairs .ui-segmented-on').textContent.indexOf('q2-3') === 0 && $('#czg-pairs .ui-badge--danger'), 'pair switch: one option per pair with its mark, the failing pair shown first');
    $('#czg-pairs [data-czg-pair-idx="0"]').click();
    await settle();
    ok($('#czg-pairs .ui-segmented-on').textContent.indexOf('q1-2') === 0 && /op_q1-2/.test($('#czg-intent').textContent), 'choosing a pair shows its own evidence');
    ok(/0.18 V/.test($('#czg-intent').textContent) && !/0.1800/.test($('#czg-intent').textContent), 'numbers in the shortest exact form');

    // not checked wording
    type($('input[data-czg-name="amplitude"]'), '0.25');
    await settle();
    queue.check.push((b) => checkOk(b, { unchecked: true }));
    prim().click();
    await settle();
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
    ok([...doc.querySelectorAll('#czg-status-slot .gate-status-row')].map((r) => r.firstElementChild.getAttribute('data-czg-status-pair')).join() === 'q1-2,q2-3',
       'two created gates: each status row starts with its pair');
    ok(!$('#czg-open-pair').hidden && prim().textContent === 'Close' && !$('#czg-form').contains(doc.activeElement), 'after create: Open the pair and Close; the focus is not left in the hidden form');
    prim().click();
    await settle();
    ok(!$('#czg-dialog'), 'Close closes');

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
    for (const name of ['partly', 'checked']) {
        const cs = OUTCOME_CASES[name];
        if (name !== 'partly') { type($('#czg-name'), 'cz_oc_' + name); await settle(); }
        const scrolled = [];
        window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.id); };
        queue.check.push((b) => { const j = checkOk(b); j.pairs.forEach((x) => { x.outcome = cs.outcome;
            x.checks = Object.entries(cs.result.gates[0].checks).map(([n, c]) => ({ name: n, status: c.status, message: c.message })); }); return j; });
        prim().click();
        await settle();
        if (name === 'partly') {
            ok(/Ready to create/.test(alertText()) && /5 checks passed, 1 could not run: preview comparison \(no preview for this pulse class\)\./.test(alertText())
               && !/every check passed/.test(alertText()) && !/not checked/i.test(alertText()), 'shared case partly: 5 passed, 1 could not run, with the reason; never every check passed');
            ok(/^5 of 6 passed, 1 could not run$/.test($('#czg-checks-sum').textContent), 'shared case partly: the summary counts 5 of 6, 1 could not run');
            ok(prim().textContent === 'Create gate', 'shared case partly: it may be created');
            ok(scrolled.indexOf('czg-alert') >= 0, 'after a check answer the banner is scrolled into view (stacked: it sits above the form)');
        } else {
            ok(/every check passed/.test(alertText()) && /^6 of 6 passed$/.test($('#czg-checks-sum').textContent), 'shared case checked: every check passed, 6 of 6');
        }
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

    // the chart component missing
    delete window.GateTimeline;
    window.CzGate.open({ pair: 'q1-2' });
    await settle();
    prim().click();
    await settle();
    ok(/Chart not loaded/.test($('#czg-chart').textContent), 'no chart component: said, never a throw');

    console.log(failed ? `FAIL cz_gate_dialog_selfcheck (${n} assertions, ${failed} failed)` : `ok cz_gate_dialog_selfcheck (${n} assertions)`);
    process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL crashed: ' + (e && e.stack || e)); process.exit(1); });
