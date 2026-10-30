// C5: the rule dialog's stale-response guards, EXECUTED under jsdom.
//  S1 a response that arrives after the content was replaced (resetRuleRuntime, as applyDraft/undo call it) is ignored
//  S2 Apply is disabled while a request / debounce is pending
//  S3 same seq, different request-time sig: the old answer is not adopted
//  S4 a server 409 is shown as the stale state with the server's words; the apply is never re-sent
//  S5 ticks are kept per pair id across a recheck
//  S6 stale: body is inert and focus that was inside it goes to Cancel
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;

(async () => {
  // S1
  let { w, srv, spec } = T.makeWorld({ nq: 4 });
  srv.hold = true; srv.ignoreAbort = true;   // a late answer even when abort cannot stop it
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  ok(srv.held.length === 1, 'S1 one expand in flight');
  const stale = srv.held[0];
  w.GenRules.resetRuleRuntime();            // the content was replaced
  stale.resolve();                          // the OLD answer arrives late (abort ignored by the mock)
  await T.settle();
  ok(w.rows().length === 0, 'S1 the late answer is not adopted: no rows drawn');
  srv.hold = false; await T.wait(250); await T.settle();
  ok(w.rows().length === 3, 'S1 the recheck answer is adopted: ' + w.rows().length);

  // S2 + S3
  ({ w, srv, spec } = T.makeWorld({ nq: 4 }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  ok(!w.applyBtn().disabled && /Add to 3 pairs/.test(w.applyBtn().textContent), 'S2 ready: Apply enabled ' + w.applyBtn().textContent);
  srv.hold = true;
  spec.populate.qubit.q2 = { RF_freq: 5e9 };
  w.GenRules.specChanged('edit', spec);
  ok(w.applyBtn().disabled, 'S2 Apply disabled the moment the dialog is stale (debounce pending)');
  ok(w.dlg().getAttribute('data-state') === 'stale', 'S2 stale state');
  await T.wait(250);
  ok(srv.held.length === 1 && w.applyBtn().disabled, 'S2 Apply still disabled while the request is in flight');
  // S3: the spec moves AGAIN while the request is in flight -> the in-flight answer has an old sig
  spec.populate.qubit.q3 = { RF_freq: 6e9 };
  const firstHeld = srv.held[0];
  w.GenRules.specChanged('edit', spec);
  firstHeld.resolve();
  await T.settle();
  ok(w.dlg().getAttribute('data-state') === 'stale' && w.applyBtn().disabled, 'S3 an answer for an older sig is not adopted');
  srv.hold = false; await T.wait(250); await T.settle(); srv.release(); await T.settle();
  ok(w.dlg().getAttribute('data-state') === 'ready' && !w.applyBtn().disabled, 'S3 the answer for the current sig is: ' + w.dlg().getAttribute('data-state'));

  // S4 + S5 + S6
  ({ w, srv, spec } = T.makeWorld({ nq: 4, server: {
    applyResult: () => ({ status: 409, body: { ok: false, error: 'q3 RF frequency changed', rule_sig: 'x' } }) } }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  const cbs = () => Array.from(w.document.querySelectorAll('.gen-rule-row-cb'));
  cbs()[0].checked = false; cbs()[0].dispatchEvent(new w.Event('change', { bubbles: true }));
  srv.hold = true;
  w.applyBtn().click();
  await T.settle();
  ok(srv.applies.length === 1, 'S4 apply sent once');
  ok(/q3 RF frequency changed/.test(w.document.querySelector('.gen-rule-status').textContent),
    'S4 the server reason is shown: ' + w.document.querySelector('.gen-rule-status').textContent);
  srv.hold = false; srv.release(); await T.settle();
  ok(srv.applies.length === 1, 'S4 never re-sent');
  ok(srv.expands.length >= 2, 'S4 but the answer was asked for again: ' + srv.expands.length);
  await T.settle();
  ok(cbs().length === 3 && cbs()[0].checked === false && cbs()[1].checked && cbs()[2].checked, 'S5 ticks kept per pair id across the recheck');
  // S6
  cbs()[1].focus();
  w.GenRules.resetRuleRuntime();
  ok(w.document.activeElement === w.cancelBtn(), 'S6 focus inside the dimmed body goes to Cancel');
  ok(w.document.querySelector('.gen-rule-body').hasAttribute('inert'), 'S6 body inert while stale');
  // S7: a choice made in a row asks again; Apply stays off while that request is pending
  ({ w, srv, spec } = T.makeWorld({ nq: 3, server: { statusOf: (pid) => pid === 'q1-2' ? 'Ready' : 'Needs a look',
    extraRow: (pid, st) => st === 'Needs a look' ? { reason: 'high: needs a look: choose; by Populate frequency' } : {} } }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  ok(!w.applyBtn().disabled, 'S7 ready with one ticked pair');
  srv.hold = true;
  const sel = w.document.querySelector('.gen-rule-choice[data-pair="q2-3"]');
  sel.value = 'target'; sel.dispatchEvent(new w.Event('change', { bubbles: true }));
  await T.settle();
  ok(w.dlg().getAttribute('data-state') === 'ready' && w.applyBtn().disabled, 'S7 Apply disabled while the request for the choice is pending');
  srv.hold = false; srv.release(); await T.settle();
  ok(srv.expands[srv.expands.length - 1].choices['q2-3'] === 'target', 'S7 the choice rides the expand');
  ok(!w.applyBtn().disabled, 'S7 Apply back with the answer');
  // S8: apply is asynchronous too; a same-sequence edit must survive its late response.
  ({ w, srv, spec } = T.makeWorld({ nq: 3 }));
  let releaseApply;
  const fetchBefore = w.fetch;
  w.fetch = (url, opts) => String(url).includes('/rules/apply')
    ? new w.Promise(resolve => { releaseApply = () => fetchBefore(url, opts).then(resolve); })
    : fetchBefore(url, opts);
  const rule = T.baseRule();
  const applying = w.GenRules.commitApply({ ctx: w.ctx, rule, answer: { rule_sig: 'preview' }, pids: ['q1-2'], label: 'apply' });
  spec.populate.qubit.q1 = { RF_freq: 8e9 };
  releaseApply();
  const refused = await applying;
  ok(refused.ok === false && refused.stale && spec.populate.qubit.q1.RF_freq === 8e9 && !spec.additions && w.undo.length === 0,
    'S8 a late apply for an older sig writes nothing and adds no undo entry');

  // S9: choosing then unticking a row must retain the question that rule_sig signed.
  ({ w, srv, spec } = T.makeWorld({ nq: 3, server: { statusOf: (pid) => pid === 'q1-2' ? 'Ready' : 'Needs a look',
    extraRow: (pid, st) => st === 'Needs a look' ? { reason: 'high: needs a look: choose' } : {} } }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  const choice = w.document.querySelector('.gen-rule-choice');
  choice.value = 'target'; choice.dispatchEvent(new w.Event('change', { bubbles: true }));
  await T.settle();
  const unchecked = w.document.querySelector('.gen-rule-row-cb[data-pair="q2-3"]');
  unchecked.checked = false; unchecked.dispatchEvent(new w.Event('change', { bubbles: true }));
  w.applyBtn().click(); await T.settle();
  ok(srv.applies.length === 1 && srv.applies[0].choices['q2-3'] === 'target' && srv.applies[0].pairs.join() === 'q1-2',
    'S9 all preview choices ride apply even when only another pair is ticked');

  // S10: a warming manifest cannot be applied even if a row was ticked.
  ({ w, srv, spec } = T.makeWorld({ nq: 3 }));
  const original = w.fetch;
  w.fetch = (url, opts) => original(url, opts).then(res => ({ status: res.status,
    json: () => res.json().then(body => Object.assign(body, { pending: true })) }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  ok(w.applyBtn().disabled, 'S10 Apply stays off while the manifest is warming');

  ({ w, srv, spec } = T.makeWorld({ nq: 3 }));
  const realFetch = w.fetch;
  w.fetch = (url, opts) => String(url).includes('/rules/apply')
    ? new w.Promise(resolve => { releaseApply = () => realFetch(url, opts).then(resolve); })
    : realFetch(url, opts);
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  w.applyBtn().click();
  spec.populate.qubit.q1 = { RF_freq: 8e9 };
  w.GenRules.specChanged('freq', spec);
  srv.hold = true;
  releaseApply(); await T.settle();
  ok(w.dlg().getAttribute('data-state') === 'stale' && w.applyBtn().disabled &&
    w.document.querySelector('.gen-rule-body').hasAttribute('inert'),
    'S11 a chip edit during apply dims the obsolete preview and rechecks');
  T.done('rules_stale_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
