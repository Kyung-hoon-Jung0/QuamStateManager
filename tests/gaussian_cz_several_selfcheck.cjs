/* jsdom selfcheck: Gaussian CZ "to several pairs" dialog (gcz-several.js).
 * The form HTML and the plan JSON come from the REAL routes (the pytest driver
 * renders them); fetch is a stub that records what the dialog sends.
 *
 * Run by tests/test_gaussian_cz_several.py (needs NODE_PATH to reach jsdom). */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const form = fs.readFileSync(process.env.GCZ_FORM, 'utf8');
const plan = JSON.parse(fs.readFileSync(process.env.GCZ_PLAN, 'utf8'));
const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'gcz-several.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body>' + form + '</body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const sent = [];
window.fetch = (url, opt) => {
    sent.push({ url, body: opt && opt.body ? JSON.parse(opt.body) : null });
    const body = url.endsWith('/plan') ? plan : { ok: true, count: sent[sent.length - 1].body.pairs.length, tray_html: '' };
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
window.eval(src);
const doc = window.document;

let failed = 0;
function ok(cond, label) {
    if (!cond) { failed++; console.error('FAIL: ' + label); }
}
const tick = () => new Promise((r) => setTimeout(r, 20));
const $ = (s) => doc.querySelector(s);
const text = () => $('#gcz-several').textContent.replace(/\s+/g, ' ');

(async () => {
    const opener = $('#gcz-several-open');
    ok(opener && opener.textContent === '…to several pairs', 'opener wording');
    opener.focus();
    opener.click();
    await tick();

    const d = $('#gcz-several');
    ok(d.hasAttribute('open'), 'dialog open');
    ok(sent[0] && sent[0].url === '/api/pulse/gaussian-cz/plan', 'plan asked first');
    const t = text();
    for (const w of [
        'Gaussian CZ — to several pairs',
        'Each pair gets its own pulses: <macro>_pulse_<control>_<target>, e.g. cz_gaussian_unipolar_pulse_q1_q2.',
        'A new macro class on a built chip is not created here in v1; use Re-generate.',
        "Ready means SM's checks and your lab code pass (checked on this chip).",
        'Select:', 'All ready', 'None',
        'Ready — 2 of 3 pairs', 'by chip f_01', 'Can’t — 1',
        'a gate named cz_gaussian_unipolar already exists',
    ]) ok(t.includes(w), 'text: ' + w);
    ok(!/Replace/.test(t), 'no Replace in the dialog');
    ok(!/Feedline ▾/.test(t) && /Feedline selection is not offered/.test(t), 'feedline omitted and said so');

    const rows = [...d.querySelectorAll('input[data-gcz-pair]')];
    ok(rows.length === 2 && rows.every((b) => b.checked), 'Ready rows ticked by default');
    const cells = rows[0].closest('tr') ? [...rows[0].closest('tr').cells].map((c) => c.textContent) : [];
    ok(JSON.stringify(cells.slice(1)) === JSON.stringify(['q1-2', 'Ready', 'q2', 'cz_gaussian_unipolar_pulse_q1_q2']), 'row cells: ' + JSON.stringify(cells));
    const cant = $('.gcz-sev-cant');
    ok(cant && !cant.querySelector('input'), "Can't has no checkbox");
    const gbox = $('input[data-gcz-group]');
    ok(gbox && !gbox.closest('summary'), 'group checkbox sits outside the summary');
    ok($('.gcz-sev-group details > summary') && $('.gcz-sev-group details > summary').textContent.includes('Ready'), 'details/summary head');

    const add = $('#gcz-sev-add');
    const allBtn = $('[data-gcz-select="ready"]');
    const noneBtn = $('[data-gcz-select="none"]');
    ok(add.textContent === 'Add to 2 pairs' && !add.disabled, 'Add count 2');
    ok(allBtn.getAttribute('aria-pressed') === 'true' && noneBtn.getAttribute('aria-pressed') === 'false', 'All ready pressed');

    rows[0].checked = false; rows[0].dispatchEvent(new window.Event('change', { bubbles: true }));
    ok(add.textContent === 'Add to 1 pair', 'Add count follows a tick');
    ok(allBtn.getAttribute('aria-pressed') === 'false', 'All ready released');
    noneBtn.click();
    ok(add.textContent === 'Add to 0 pairs' && add.disabled, 'None: count 0 and disabled');
    ok(noneBtn.getAttribute('aria-pressed') === 'true', 'None pressed');
    allBtn.click();
    ok(add.textContent === 'Add to 2 pairs' && !add.disabled, 'All ready restores');

    rows[1].checked = false; rows[1].dispatchEvent(new window.Event('change', { bubbles: true }));
    add.click();
    await tick();
    const post = sent.find((s) => s.url.endsWith('/apply-several'));
    ok(post && post.body.plan_sig === plan.plan_sig, 'apply echoes the plan signature');
    ok(post && JSON.stringify(post.body.pairs) === JSON.stringify([rows[0].value]), 'apply sends only ticked pairs');

    // Esc closes and focus returns to the opener
    doc.activeElement && doc.activeElement.blur();
    d.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(!d.hasAttribute('open'), 'Esc closes');
    ok(doc.activeElement === opener, 'focus returns to the opener');

    if (failed) { console.error(failed + ' failed'); process.exit(1); }
    console.log('all ok');
})();
