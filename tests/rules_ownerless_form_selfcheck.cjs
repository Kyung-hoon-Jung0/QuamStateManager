// C7: the ownerless CZ form (Set-all), EXECUTED under jsdom.
//  O1 no pair id text anywhere in the form; the role rows say how the owner is decided per pair; the button
//  O2 the names line (never a shared pulse name) updates with the macro name
//  O3 a moving-qubit class shows the on_choice select (per pair / control / target)
//  O4 Continue builds a RULE (numbers as typed JSON numbers, structure apart) and opens the dialog; no gate-action call
//  O5 Edit rule mode: prefilled from the rule, the button reads "Save -> review N pairs", Cancel returns focus to the opener
//  O6 a pair-mode form still names the pair and writes through gate-action (the owner path is untouched)
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

function pick(w, el, v) { el.value = v; el.dispatchEvent(new w.Event('change', { bubbles: true })); }

(async () => {
  let m = T.makeAddWorld({ nq: 4 });
  let { w, spec } = m;
  await w.decorate();
  const doc = w.document;
  const btn = doc.querySelector('.gen-setall-gate');
  ok(!!btn && btn.textContent === '+ Gate for all pairs…', 'Set-all button text');
  btn.click();
  const form = () => doc.querySelector('.gen-add-cz-ownerless');
  ok(!!form(), 'O1 the form opens under the Set-all row');
  ok(form().closest('tr').previousElementSibling.classList.contains('gen-pop-setall'), 'O1 directly after the Set-all row');
  pick(w, form().querySelector('.gen-add-cz-class'), 'pkg.G');
  const text = () => form().textContent;
  ok(!/q\d-\d|q\d-q\d/.test(text()), 'O1 no pair id text: ' + text());
  ok(/the higher-f_01 qubit's z, decided per pair/.test(text()) && /the lower-f_01 qubit's z, decided per pair/.test(text()), 'O1 high/low rows');
  ok(form().querySelector('.gen-add-ok').textContent === 'Continue → choose pairs', 'O1 button text');
  ok(/^CZ on all pairs — a gate class, the flux pulse\(s\) it plays, and the gate pointing at them/.test(form().querySelector('.gen-add-cz-head').textContent), 'O1 heading');
  ok(/Pulse names: cz_bm_high_<control>_<target>, e\.g\. cz_bm_high_q1_q2 — one per pair, never shared\./.test(text()), 'O2 names line: ' + form().querySelector('.gen-add-cz-names').textContent);
  const nm = form().querySelector('.gen-add-name');
  nm.value = 'cz_x'; nm.dispatchEvent(new w.Event('input', { bubbles: true }));
  ok(/cz_x_high_<control>_<target>, e\.g\. cz_x_high_q1_q2/.test(form().querySelector('.gen-add-cz-names').textContent), 'O2 names line follows the macro name');
  ok(!form().querySelector('.gen-add-cz-on'), 'O1 high/low have no "plays on" select');

  // O3
  pick(w, form().querySelector('.gen-add-cz-class'), 'pkg.M');
  const oc = form().querySelector('.gen-add-cz-onchoice');
  ok(!!oc && Array.from(oc.options).map(o => o.value).join() === 'auto,control,target' && /the moving qubit's z/.test(text()), 'O3 on_choice select for a moving class');

  // O4: back to the both-movers class, fill, continue
  pick(w, form().querySelector('.gen-add-cz-class'), 'pkg.G');
  form().querySelector('.gen-add-name').value = 'cz_bm';
  const pcs = Array.from(form().querySelectorAll('.gen-add-cz-pclass'));
  pcs.forEach(pc => pick(w, pc, 'pkg.P'));
  const amp = form().querySelector('[data-field="amplitude"]');
  amp.value = '0.12';
  Array.from(form().querySelectorAll('[data-field="length"]')).forEach(i => { i.value = '45'; });
  form().querySelector('.gen-add-ok').click();
  await T.settle();
  ok(!w.gateActions, 'O4 no gate-action call (there is no pair to resolve)');
  const body = m.srv.expands[0];
  ok(!!body && body.rule.source === null && body.rule.gate.name === 'cz_bm' && body.rule.items.map(i => i.role).join() === 'high,low', 'O4 expand got the rule: ' + J(body && body.rule));
  ok(body.rule.numbers['high.length'] === 45 && body.rule.numbers['high.amplitude'] === 0.12 && typeof body.rule.numbers['low.length'] === 'number', 'O4 numbers are JSON numbers: ' + J(body.rule.numbers));
  ok(/Initial values from the form: high: amplitude 0\.12, length 45 ns; low: length 45 ns/.test(w.dlg().textContent), 'O4 the dialog names the initial values');
  ok(!/from q1-2/.test(w.dlg().textContent), 'O4 no "from <pair>" for a rule without a source');
  w.cancelBtn().click();
  ok(doc.activeElement === btn, 'O4 Cancel returns focus to the Set-all button');

  // O5: Edit rule
  const rule = body.rule;
  m = T.makeAddWorld({ nq: 4 });
  w = m.w; spec = m.spec;
  await w.decorate();
  const host = w.document.querySelector('tr.gen-pop-setall');
  let saved = null;
  const opener = w.document.createElement('button'); w.document.body.appendChild(opener);
  // open the ownerless form in edit mode through the same door the Rules card uses
  w.GenAdditions.openRuleForm(host, w.ctx, opener, rule, 'Save → review 3 pairs', (r) => { saved = r; });
  const f2 = w.document.querySelector('.gen-add-cz-ownerless');
  ok(f2.querySelector('.gen-add-ok').textContent === 'Save → review 3 pairs', 'O5 button text');
  ok(f2.querySelector('.gen-add-cz-class').value === 'pkg.G' && f2.querySelector('.gen-add-name').value === 'cz_bm', 'O5 class and name prefilled');
  ok(f2.querySelector('[data-field="amplitude"]').value === '0.12', 'O5 numbers prefilled');
  f2.querySelector('.gen-add-ok').click();
  await T.settle();
  ok(saved && saved.rev === (rule.rev || 0) + 1 && saved.numbers['high.length'] === 45, 'O5 Save hands back the rule with rev+1: ' + J(saved && saved.rev));
  const f3 = (() => { w.GenAdditions.openRuleForm(host, w.ctx, opener, rule, 'Save → review 3 pairs', () => {}); return w.document.querySelector('.gen-add-cz-ownerless'); })();
  f3.querySelector('.gen-add-cancel').click();
  ok(w.document.activeElement === opener, 'O5 Cancel returns focus to the opener');
  // O7 (R5 S4): a rule with a source takes its numbers from that pair's gate, so Edit rule
  // shows them read-only with the reason, and a source-less rule keeps them editable.
  ok(!f2.querySelector('[data-field="amplitude"]').readOnly && !f2.querySelector('.gen-add-source-note'), 'O7 source-less: numbers editable');
  f3.querySelector('.gen-add-cancel') && f3.remove && f3.remove();
  const sourced = Object.assign(JSON.parse(J(rule)), { source: 'q1-2' });
  w.GenAdditions.openRuleForm(host, w.ctx, opener, sourced, 'Save → review 3 pairs', () => {});
  const f4 = Array.from(w.document.querySelectorAll('.gen-add-cz-ownerless')).pop();
  const amp4 = f4.querySelector('[data-field="amplitude"]');
  ok(amp4.readOnly && /from q1-2's gate/.test(f4.textContent), 'O7 sourced: numbers read-only, says where they come from');

  // O6: pair mode untouched
  m = T.makeAddWorld({ nq: 3 });
  w = m.w; spec = m.spec;
  await w.decorate();
  w.document.querySelector('tr[data-rid="q1-q2"] .gen-add-czbtn').click();
  const pf = w.document.querySelector('.gen-add-cz:not(.gen-add-cz-ownerless)');
  ok(!!pf && /CZ on q1-2/.test(pf.textContent) && pf.querySelector('.gen-add-ok').textContent === 'Add CZ', 'O6 pair-mode form unchanged');
  pick(w, pf.querySelector('.gen-add-cz-class'), 'pkg.G');
  Array.from(pf.querySelectorAll('.gen-add-cz-pclass')).forEach(pc => pick(w, pc, 'pkg.P'));
  pf.querySelector('.gen-add-ok').click();
  await T.settle();
  ok((w.gateActions || []).length === 1 && w.gateActions[0].pair === 'q1-q2', 'O6 it still resolves through gate-action');
  T.done('rules_ownerless_form_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
