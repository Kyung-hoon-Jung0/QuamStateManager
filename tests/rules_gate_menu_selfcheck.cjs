// C9: the gate chip's "..." menu, the x confirm and Reset to rule, EXECUTED under jsdom.
//  G1 a hand gate's menu: Edit / Apply to other pairs / separator / Remove from this pair (gate + 2 pulses)
//  G2 a rule gate that was edited: those + "Reset to rule (1 field: class)" + "Remove from all 3 pairs..."
//  G3 a hand gate never offers "Remove from all"
//  G4 Reset shows old -> new BEFORE it writes, then is ONE undo entry (and writes nothing until pressed)
//  G5 x on a rule gate: two radios; "this pair only" excludes the pair from the rule; one undo restores both
//  G6 "All 3 pairs" removes every instance, keeps the source gate; one undo
//  G7 x on a hand gate keeps the one-branch window.confirm
//  G8 "Apply to other pairs..." on a hand gate opens the dialog with a rule made from it (source, names, classes)
//  G9 the Set-all cell shows "cz_bm · 3 of 3 pairs" and the rule's menu
//  G10 a rule instance's badge, and edit-in-place keeps rule_id
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

function withRule(extraEdited) {
  const spec = T.baseSpec(4);
  const rule = Object.assign(T.baseRule(), { id: 'r1', rev: 1, source: 'q1-2', source_names: { high: 'cz_bm_high', low: 'cz_bm_low' } });
  rule.items = [{ role: 'high', field: 'flux_pulse_high', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' },
                { role: 'low', field: 'flux_pulse_low', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' }];
  spec.addition_rules = [rule];
  // the source: a hand gate (old form names) ; two instances of the rule
  spec.additions = [].concat(
    [T.pulse('q2', 'cz_bm_high'), T.pulse('q1', 'cz_bm_low'),
     { kind: 'macro', target: 'qubit_pairs.q1-2.macros', name: 'cz_bm', 'class': 'pkg.G',
       fields: { flux_pulse_high: T.ptr('q2', 'cz_bm_high'), flux_pulse_low: T.ptr('q1', 'cz_bm_low') } }],
    T.gateBundle('q2-3', 'q2', 'q3', 'cz_bm', { rule_id: 'r1' }),
    T.gateBundle('q3-4', 'q3', 'q4', 'cz_bm', { rule_id: 'r1' }));
  return { spec, rule };
}

(async () => {
  // G1 / G3: a hand gate
  let m = T.makeAddWorld({ nq: 3 });
  let { w, spec } = m;
  spec.additions = T.gateBundle('q1-2', 'q1', 'q2', 'cz_hand');
  await w.decorate();
  ok(!!w.chip('q1-2', 'cz_hand'), 'G1 the hand gate chip is drawn');
  w.more('q1-2', 'cz_hand').click();
  const labels = () => w.menuItems().map(i => i.textContent);
  ok(labels().join('|') === 'Edit…|Apply to other pairs…|Remove from this pair  (gate + 2 pulses)', 'G1 hand menu items: ' + J(labels()));
  ok(w.document.querySelector('.gen-rule-menu').getAttribute('role') === 'menu' && !!w.document.querySelector('.gen-rule-menu-sep'), 'G1 role=menu and a separator');
  ok(!labels().some(l => /Remove from all/.test(l)), 'G3 a hand gate never offers Remove from all');
  w.key(w.document.activeElement, 'Escape');

  // G2, G4..G6, G8..G10: a rule with two instances; q2-3 is edited
  m = T.makeAddWorld({ nq: 4, server: { statusOf: () => 'Already has it',
    extraRow: (pid) => pid === 'q2-3' ? { edited: true, existing: true,
      diff_vs_current: [{ kind: 'pulse', name: 'cz_bm_high_q2_q3', field: 'class', old: 'pkg.Q', new: 'pkg.P' }] } : { existing: true } } });
  w = m.w;
  const made = withRule();
  Object.keys(m.spec).forEach(k => delete m.spec[k]);
  Object.assign(m.spec, made.spec);
  spec = m.spec;
  const rule = spec.addition_rules[0];
  // the last answer said q2-3 was edited in its pulse class
  const rowEdited = T.mkRow('q2-3', 'Already has it', { edited: true, existing: true,
    diff_vs_current: [{ kind: 'pulse', name: 'cz_bm_high_q2_q3', field: 'class', old: 'pkg.Q', new: 'pkg.P' }] });
  const rowPlain = T.mkRow('q3-4', 'Already has it', { existing: true });
  w.GenRules.noteExpand(rule, { pairs: [rowEdited, rowPlain], rule_sig: 'x' }, null);
  await w.decorate();
  ok(/·rule·edited/.test(w.chip('q2-3', 'cz_bm').textContent) && /·rule$/.test(w.chip('q3-4', 'cz_bm').textContent), 'G10 badges: ' + w.chip('q2-3', 'cz_bm').textContent + ' / ' + w.chip('q3-4', 'cz_bm').textContent);
  ok(/·rule·from this pair/.test(w.chip('q1-2', 'cz_bm').textContent), 'G10 the source says from this pair');
  ok(/1 field edited here \(class\)/.test(w.chip('q2-3', 'cz_bm').title) && /was made from this pair/.test(w.chip('q1-2', 'cz_bm').title), 'G10 hover texts');
  w.more('q2-3', 'cz_bm').click();
  ok(labels().join('|') === 'Edit…|Apply to other pairs…|Reset to rule  (1 field: class)|Remove from this pair  (gate + 2 pulses)|Remove from all 3 pairs…',
    'G2 rule gate menu: ' + J(labels()));
  ok(w.document.activeElement === w.menuItems()[0], 'menu opens on its first item');

  // G4 Reset to rule
  const undoBefore = w.undo.length;
  w.menuItems()[2].click();
  await T.settle();
  const rv = w.document.querySelector('.gen-rule-review');
  ok(!!rv && /class\s+pkg\.Q → pkg\.P/.test(rv.textContent), 'G4 old → new shown before writing: ' + (rv && rv.textContent));
  ok(m.srv.applies.length === 0 && w.undo.length === undoBefore, 'G4 nothing written yet');
  rv.querySelector('.gen-rule-rv-apply').click();
  await T.settle();
  ok(m.srv.applies.length === 1 && J(m.srv.applies[0].pairs) === '["q2-3"]' && w.undo.length === undoBefore + 1, 'G4 one apply for that pair, one undo entry: ' + J(m.srv.applies.map(a => a.pairs)));
  w.undo.pop();   // set aside (the mock apply replaced the spec)
  Object.keys(spec).forEach(k => delete spec[k]); Object.assign(spec, withRule().spec);
  await w.decorate();

  // G5: x on the rule gate -> radios, this pair only
  const snap = J(spec);
  const u0 = w.undo.length;
  w.document.querySelector('tr[data-rid="q2-q3"] .gen-gate-rm').click();
  const dlg = w.document.querySelector('.gen-rule-mini');
  ok(!!dlg && /Remove cz_bm from q2-3\?\s+Its 2 pulses go with it\./.test(dlg.textContent), 'G5 confirm text: ' + (dlg && dlg.textContent));
  const radios = Array.from(dlg.querySelectorAll('input[type=radio]'));
  ok(radios.length === 2 && /This pair only — exclude it from the rule/.test(dlg.textContent) && /All 3 pairs/.test(dlg.textContent), 'G5 two radios');
  ok(w.document.activeElement === radios[0] && radios[0].checked, 'G5 focus on the first radio');
  w.key(radios[0], 'Escape');
  ok(!w.document.querySelector('.gen-rule-mini') && spec.additions.length === 9 && w.document.activeElement.classList.contains('gen-gate-rm'), 'G5 Esc = Cancel, focus back on the x');
  w.document.querySelector('tr[data-rid="q2-q3"] .gen-gate-rm').click();
  w.document.querySelector('.gen-rule-rm-ok').click();
  await w.decorate();
  ok(spec.additions.length === 6 && !spec.additions.some(a => /q2_q3/.test(a.name)), 'G5 the gate and its 2 pulses are gone');
  ok(J(spec.addition_rules[0].excluded) === '["2-3"]', 'G5 the pair is excluded from the rule: ' + J(spec.addition_rules[0].excluded));
  ok(w.undo.length === u0 + 1, 'G5 one undo entry');
  w.undo[w.undo.length - 1].fn();
  ok(J(spec) === snap, 'G5 one undo restores the gate AND the rule');
  await w.decorate();

  // G6: all 3 pairs
  w.more('q2-3', 'cz_bm').click();
  w.menuItems()[4].click();
  const dlg2 = w.document.querySelector('.gen-rule-mini');
  ok(dlg2.querySelectorAll('input[type=radio]')[1].checked, 'G6 "All" preselected from the menu');
  w.document.querySelector('.gen-rule-rm-ok').click();
  await w.decorate();
  ok(spec.additions.length === 3 && spec.additions.every(a => !a.rule_id) && w.chip('q1-2', 'cz_bm'), 'G6 every instance gone, the source gate stays');
  w.undo[w.undo.length - 1].fn();
  ok(J(spec) === snap, 'G6 one undo brings them all back');
  await w.decorate();

  // G11 editing an instance in place keeps its rule bookkeeping (rule_id, copied_value)
  w.chip('q3-4', 'cz_bm').click();
  await T.settle();
  const form = w.document.querySelector('.gen-gates-formrow .gen-add-form');
  ok(!!form, 'G11 the edit form opens for a rule instance');
  form.querySelector('.gen-add-ok').click();
  await T.settle();
  const edited = spec.additions.filter(a => a.kind === 'macro' && a.target === 'qubit_pairs.q3-4.macros')[0];
  ok(edited && edited.rule_id === 'r1', 'G11 rule_id survives an edit: ' + J(edited && edited.rule_id));
  w.undo[w.undo.length - 1].fn();
  await w.decorate();

  // G9 the Set-all cell
  const sa = w.document.querySelector('td.gen-gates-setall');
  ok(/cz_bm · 3 of 3 pairs/.test(sa.textContent) && !!sa.querySelector('.gen-setall-gate'), 'G9 Set-all cell: ' + sa.textContent);
  sa.querySelector('.gen-rule-more').click();
  ok(labels()[0] === 'Stop as a rule — keep the 3 gates' && labels()[1] === 'Remove the rule and its 3 gates…' && labels()[2] === 'Also add to pairs created later', 'G9 the rule menu: ' + J(labels()));
  w.key(w.document.activeElement, 'Escape');

  // G8 Apply to other pairs from the source (hand) gate: the rule from the stored one -- then from a plain hand gate
  m = T.makeAddWorld({ nq: 3 });
  w = m.w; spec = m.spec;
  spec.additions = T.gateBundle('q1-2', 'q1', 'q2', 'cz_hand');
  await w.decorate();
  w.more('q1-2', 'cz_hand').click();
  w.menuItems()[1].click();
  await T.settle();
  const body = m.srv.expands[0];
  ok(!!body && body.rule.source === 'q1-2' && body.rule.gate.name === 'cz_hand' && body.rule.gate.macro_class === 'pkg.G', 'G8 expand got a rule from the gate: ' + J(body && body.rule.gate));
  ok(body.rule.items.map(i => i.role + ':' + i.pulse_class).join() === 'high:pkg.P,low:pkg.P' && !('id' in body.rule), 'G8 items by role, no id for a new rule');
  ok(J(body.rule.source_names) === J({ high: 'cz_hand_high_q1_q2', low: 'cz_hand_low_q1_q2' }), 'G8 source_names keep the existing pulse names: ' + J(body.rule.source_names));
  ok(/Add cz_hand to other pairs/.test(w.dlg().textContent), 'G8 the dialog opened');

  // G7 hand gate x -> window.confirm only
  m = T.makeAddWorld({ nq: 3 });
  w = m.w; spec = m.spec;
  spec.additions = T.gateBundle('q1-2', 'q1', 'q2', 'cz_hand');
  const asked = [];
  w.confirm = (t) => { asked.push(t); return true; };
  await w.decorate();
  w.document.querySelector('.gen-gate-rm').click();
  ok(asked.length === 1 && !w.document.querySelector('.gen-rule-mini') && !(spec.additions || []).length, 'G7 hand gate: the one-branch confirm, then it is gone');

  T.done('rules_gate_menu_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
