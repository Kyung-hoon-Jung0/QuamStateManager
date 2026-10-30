// C12: the Review step's rule lines come from the MODEL, and the Rules card is a polite live region.
//  C1 ruleReviewCounts is pure: numbers right with NO Additions list mounted anywhere in the document
//  C2 "rules not checked: <server words>" only when the settle failed
//  C3 GenAdditions.renderReview (the Review step) carries the rule lines without any Additions DOM
//  C4 the card: aria-live="polite", tabindex=-1, never a toast role
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

(async () => {
  const m = T.makeAddWorld({ nq: 5, server: { statusOf: (pid) => ({ 'q1-2': 'Already has it', 'q2-3': 'Needs a look', 'q3-4': 'Needs a look', 'q4-5': 'Ready' }[pid]),
    extraRow: (pid) => (pid === 'q2-3' || pid === 'q3-4') ? { existing: true, edited: pid === 'q2-3',
      diff_vs_current: [{ kind: 'pulse', name: 'x', field: 'class', old: 'a', new: 'b' }] } : {} } });
  const { w, spec } = m;
  const rule = Object.assign(T.baseRule(), { id: 'r1', rev: 1, source: 'q1-2' });
  spec.addition_rules = [rule];
  spec.additions = [].concat(T.gateBundle('q2-3', 'q2', 'q3', 'cz_bm', { rule_id: 'r1' }), T.gateBundle('q3-4', 'q3', 'q4', 'cz_bm', { rule_id: 'r1' }));
  ok(!w.document.querySelector('.gen-add-list, #gen-pop-additions'), 'no Additions DOM exists');
  const status = { r1: { rows: { 'q2-3': { status: 'Needs a look' }, 'q3-4': { status: 'Needs a look' } } } };
  const lines = w.GenRules.ruleReviewCounts(spec, status, null);
  ok(lines.length === 1 && lines[0] === 'rule cz_bm: 2 gates on 2 pairs · 8 numbers are copied initial values from q1-2 (recalibrate per pair) · 2 pairs have unreviewed changes (Populate › Rule › Review)',
    'C1 counts from the model: ' + J(lines));
  ok(w.GenRules.ruleReviewCounts(spec, {}, 'boom')[0].endsWith(' · rules not checked: boom'), 'C2 the failure is named');
  ok(!/rules not checked/.test(lines[0]), 'C2 and absent otherwise');
  const box = w.document.createElement('div');
  w.document.body.appendChild(box);
  w.GenRules.setSettleError(null);
  w.GenAdditions.renderReview(box, null, spec);
  ok(/rule cz_bm: 2 gates on 2 pairs/.test(box.textContent), 'C3 the Review step shows the rule line with no Additions mounted: ' + box.textContent.slice(0, 200));
  await w.decorate();
  await T.settle();
  const card = w.document.querySelector('.gen-rule-card');
  ok(!!card && card.getAttribute('aria-live') === 'polite' && card.getAttribute('tabindex') === '-1' && !card.hasAttribute('role') === false, 'C4 card is a polite live region, focusable by script');
  ok(card.getAttribute('role') !== 'alert' && card.getAttribute('role') !== 'status', 'C4 not an alert');
  T.done('rules_review_counts_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
