// 4.11 / 4.12: the Rules card, the review dialog and Edit rule, EXECUTED under jsdom.
//  D1 card text and buttons (needs a look / new pair / edited)
//  D2 Review: focus on the first "Keep as is"; Keep as is = reviewed[key] = instance_sig, ONE undo entry
//  D3 Esc writes nothing; "Apply all N changes" is ONE apply and ONE undo entry
//  D4 Edit rule...: prefilled ownerless form, "Save -> review N pairs", rev+1, one undo entry
//  D5 after Apply in the Apply dialog the focus is the Rules card
//  D6 a deleted source pair, and cross-resonance
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

function build(extra) {
  const m = T.makeAddWorld(Object.assign({ nq: 5, server: { statusOf: (pid) => ({ 'q1-2': 'Already has it', 'q2-3': 'Needs a look', 'q3-4': 'Needs a look', 'q4-5': 'Ready' }[pid]),
    extraRow: (pid) => (pid === 'q2-3' || pid === 'q3-4') ? { existing: true, edited: pid === 'q2-3',
      diff_vs_current: [{ kind: 'pulse', name: 'cz_bm_high_x', field: 'amplitude', old: 0.12, new: 0.2 }] } : {} } }, extra || {}));
  const rule = Object.assign(T.baseRule(), { id: 'r1', rev: 1, source: 'q1-2' });
  rule.items = [{ role: 'high', field: 'flux_pulse_high', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' },
                { role: 'low', field: 'flux_pulse_low', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' }];
  m.spec.addition_rules = [rule];
  m.spec.additions = [].concat(T.gateBundle('q1-2', 'q1', 'q2', 'cz_bm'), T.gateBundle('q2-3', 'q2', 'q3', 'cz_bm', { rule_id: 'r1' }), T.gateBundle('q3-4', 'q3', 'q4', 'cz_bm', { rule_id: 'r1' }));
  return m;
}

(async () => {
  let m = build();
  let { w, spec } = m;
  await w.decorate(); await T.settle();
  const card = () => w.document.querySelector('.gen-rule-card');
  const txt = () => card().textContent;
  ok(/^cz_bm · G · from q1-2 · on 3 of 4 pairs · 1 edited here/.test(card().querySelector('.gen-rule-card-line1').textContent), 'D1 line 1: ' + card().querySelector('.gen-rule-card-line1').textContent);
  ok(card().querySelector('.gen-rule-card-line2').textContent === '◐ 2 need a look (pair facts changed)  ·  1 new pair q4-5', 'D1 line 2: ' + card().querySelector('.gen-rule-card-line2').textContent);
  const btns = Array.from(card().querySelectorAll('.gen-rule-card-btns button')).map(b => b.textContent);
  ok(J(btns) === J(['Review 2…', 'Add to q4-5', 'Apply to more pairs…', 'Edit rule…', '⋯']), 'D1 buttons: ' + J(btns));

  // D2
  const u0 = w.undo.length;
  card().querySelector('.gen-rule-card-review').click();
  await T.settle();
  const rv = () => w.document.querySelector('.gen-rule-review');
  ok(!!rv() && /cz_bm — 2 pairs would change/.test(rv().textContent), 'D2 title: ' + (rv() && rv().querySelector('h3').textContent));
  ok(/amplitude\s+0\.12 → 0\.2/.test(rv().textContent), 'D2 old → new per pair');
  ok(w.document.activeElement.classList.contains('gen-rule-rv-keep') && w.document.activeElement.getAttribute('data-pair') === 'q2-3', 'D2 focus: first Keep as is');
  const keepBtn = rv().querySelector('.gen-rule-rv-keep[data-pair="q2-3"]');
  keepBtn.click();
  await T.settle();
  ok(spec.addition_rules[0].reviewed['2-3'] === 'sig-q2-3' && w.undo.length === u0 + 1 && m.srv.applies.length === 0, 'D2 Keep as is: reviewed[key] = instance_sig, one undo entry, nothing applied: ' + J(spec.addition_rules[0].reviewed));
  w.undo[w.undo.length - 1].fn();
  ok(!spec.addition_rules[0].reviewed || !spec.addition_rules[0].reviewed['2-3'], 'D2 undo takes the review back');
  // D3
  w.key(w.document.activeElement, 'Escape');
  ok(!rv() && m.srv.applies.length === 0 && w.undo.length === u0 + 1, 'D3 Esc closes and writes nothing');
  card().querySelector('.gen-rule-card-review').click();
  await T.settle();
  const uBefore = w.undo.length;
  rv().querySelector('.gen-rule-rv-applyall').click();
  await T.settle();
  ok(m.srv.applies.length === 1 && J(m.srv.applies[0].pairs) === '["q2-3","q3-4"]' && w.undo.length === uBefore + 1, 'D3 Apply all = one apply, one undo entry: ' + J(m.srv.applies.map(a => a.pairs)));

  // D4 Edit rule
  m = build(); ({ w, spec } = m);
  await w.decorate(); await T.settle();
  const uE = w.undo.length;
  w.document.querySelector('.gen-rule-card-edit').click();
  const f = w.document.querySelector('.gen-add-cz-ownerless');
  ok(!!f && f.querySelector('.gen-add-ok').textContent === 'Save → review 4 pairs' && f.querySelector('.gen-add-name').value === 'cz_bm', 'D4 prefilled, button: ' + (f && f.querySelector('.gen-add-ok').textContent));
  f.querySelector('.gen-add-ok').click();
  await T.settle();
  ok(spec.addition_rules[0].rev === 2 && w.undo.length === uE + 1 && spec.addition_rules[0].id === 'r1', 'D4 rev+1, same id, one undo entry: rev=' + spec.addition_rules[0].rev);
  ok(!!w.document.querySelector('.gen-rule-review'), 'D4 then the review opens');

  // D5 apply -> card focus
  m = T.makeAddWorld({ nq: 4 });
  ({ w, spec } = m);
  await w.decorate();
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  w.applyBtn().click();
  await T.settle(); await T.settle();
  const c2 = w.document.querySelector('.gen-rule-card');
  ok(!!c2 && w.document.activeElement === c2, 'D5 after Apply the focus is the Rules card: ' + (w.document.activeElement && w.document.activeElement.className));
  ok(/cz_bm · G · for all pairs \(no source\)/.test(c2.textContent), 'D5 a rule without a source reads "for all pairs (no source)": ' + c2.textContent);

  // D6
  m = build(); ({ w, spec } = m);
  spec.qubit_pairs = spec.qubit_pairs.slice(1);   // the source pair q1-2 is gone
  await w.decorate(); await T.settle();
  ok(/from q1-2 \(pair deleted\)/.test(w.document.querySelector('.gen-rule-card-line1').textContent), 'D6 pair deleted');
  spec.pair_gate = 'cr';
  w.GenRules.renderCards();
  ok(/not applicable to cross-resonance — \d+ gates kept, nothing applies/.test(w.document.querySelector('.gen-rule-card-line1').textContent) &&
     !w.document.querySelector('.gen-rule-card-more'), 'D6 cross-resonance: ' + w.document.querySelector('.gen-rule-card-line1').textContent);
  T.done('rules_card_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
