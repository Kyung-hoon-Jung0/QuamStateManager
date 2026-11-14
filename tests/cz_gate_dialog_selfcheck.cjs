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
              { 'class': 'X', label: 'Other', layout: 'copied', available: false, why: 'SM does not know it.', slots: [], models: [] }],
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
let hold = null;                    // a pending check the test resolves itself
function planOk(body) {
    return { ok: true, plan_sig: 'sig-' + JSON.stringify(body.pulses) + body.gate_name + body.pairs.join(','),
             pairs: body.pairs.map((p) => p === 'q9-9' ? { pair: p, ok: false, refusals: ['No such pair.'] } : {
                 pair: p, ok: /^cz_/.test(body.gate_name), refusals: /^cz_/.test(body.gate_name) ? [] : ['The gate name must start with cz_.'],
                 creates: 2, paths: ['qubit_pairs.' + p + '.macros.' + body.gate_name, 'qubits.q1.z.operations.x'],
                 layout: 'known', operations: { flux_pulse_qubit: { key: 'op_' + p, channel: 'q1.z' } },
                 fields: { flux_pulse_qubit: { length: 'default', amplitude: ((body.pulses.flux_pulse_qubit || {}).fields || {}).amplitude !== undefined ? 'given' : 'default' } },
                 pulses: { flux_pulse_qubit: Object.assign({ __class__: 'P.SquarePulse', length: 100, amplitude: 0.1 },
                                                           ((body.pulses.flux_pulse_qubit || {}).fields || {})) } }) };
}
function checkOk(body, opts) {
    opts = opts || {};
    return { ok: true, status: 'ok', plan_sig: planOk(body).plan_sig, check_token: 'tok' + sent.length,
             pairs: body.pairs.map((p, i) => ({
                 pair: p, gate: body.gate_name, ok: true, refusals: [],
                 checks: [{ name: 'range', status: 'pass', message: 'Inside the output range.' }],
                 timeline: { lanes: [{ key: 'q1.z', segments: [] }], notes: [], cannot_draw: null },
                 intent: [{ field: 'flux_pulse_qubit', operation: 'op_' + p, channel: 'q1.z',
                            intended: { length: 100, length_source: 'default', amplitude: 0.2, amplitude_source: 'given' },
                            actual: { length_ns: 100, max_v: 0.18, min_v: 0, scale: 1 },
                            differs: { length: false, amplitude: true }, tones: { length: null, amplitude: 'warn' }, why: ['The class shapes the waveform.'] }],
                 paths: ['a', 'b'],
                 verdict: (opts.refuse === p) ? { allowed: false, state: 'not checked', reason: 'A gate named so exists.' }
                     : { allowed: true, state: opts.unchecked ? 'not checked' : 'verified', reason: opts.unchecked ? 'No environment.' : '' } })) };
}
function respond(status, j) { return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(j), text: () => Promise.resolve('') }); }
window.fetch = (url, opt) => {
    if (!opt || !opt.method) {
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(dialogHtml()) });
    }
    const body = JSON.parse(opt.body);
    const kind = url.split('/').pop();
    sent.push({ kind, body });
    const q = queue[kind].shift();
    if (kind === 'check' && hold) { const h = hold; hold = null; return h.then(() => respond(200, q ? q(body) : checkOk(body))); }
    if (q) { const r = q(body); return Array.isArray(r) ? respond(r[0], r[1]) : respond(200, r); }
    if (kind === 'plan') return respond(200, planOk(body));
    if (kind === 'check') return respond(200, checkOk(body));
    return respond(200, { ok: true, created: [{ pair: 'q1-2', gate: body.gate_name || 'cz_new', state: 'verified', paths: ['a'] }], skipped: [], count: 1, tray_html: '' });
};
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
    ok($('#czg-checks .ui-result[data-check="range"]') && /Output range/.test($('#czg-checks').textContent) && !$('#czg-sec-checks').hidden && /1 of 1 passed/.test($('#czg-checks-sum').textContent), 'checks: chips in words, key as data-check, passes counted');
    ok($('#czg-alert .ui-verdict--strong.ui-verdict--ok') && /Ready to create/.test(alertText()), 'the banner says Ready to create');
    ok($('#czg-intent .ui-text-warn') && /shapes the waveform/.test($('#czg-intent').textContent) && $('#czg-intent .ui-line-dot.ui-line-1') && /differ/.test($('#czg-intent-sum').textContent), 'a differing cell is marked warn, the row has its line dot, the why is under it');

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
    ok(prim().textContent === 'Create without the lab check' && $('#czg-alert .ui-verdict--strong.ui-verdict--warn'), 'not checked: warn banner and the button says so');

    // create
    prim().click();
    await settle();
    ok(/Not on the chip yet: press Apply to write it/.test(alertText()) && $('#czg-status-slot'), 'success says Apply is still needed; the status slot is there');
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
