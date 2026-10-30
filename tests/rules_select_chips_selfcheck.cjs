// C16 + the Apply dialog's states, EXECUTED under jsdom.
//  C1  chips [All ready] [Feedline v] [None] carry aria-pressed that follows the real selection
//  C2  Feedline ticks ONLY that feedline's Ready pairs (never a Needs-a-look row)
//  C3  there is no Chain chip
//  D1  defaults: Ready ticked, Needs a look unticked, Can't and Already have no checkbox; denominators
//  D2  Apply says how many it will add and sends exactly the ticked pairs
//  D3  a successful apply is ONE undo entry, replaces the spec, toasts the wireframe sentence
//  D4  all-can't: "No other pair can take this gate.", Close, Nothing to add disabled, no "created later"
//  D5  loading: Cancel focused, Apply disabled, skeleton, "Checking N pairs"
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;

const STATUS = { 'q1-2': 'Already has it', 'q2-3': 'Ready', 'q3-4': 'Ready', 'q4-5': 'Ready', 'q5-6': 'Needs a look', 'q6-7': "Can't" };
const FEED = { q1: 'feedline1', q2: 'feedline1', q3: 'feedline1', q4: 'feedline2', q5: 'feedline1', q6: 'feedline2', q7: 'feedline2' };

(async () => {
  let { w, srv, spec } = T.makeWorld({ nq: 7, feedlines: () => FEED, server: { statusOf: (pid) => STATUS[pid] } });
  srv.hold = true;
  const rule = T.baseRule(); rule.source = 'q1-2';
  const opener = w.document.createElement('button'); w.document.body.appendChild(opener);
  w.GenRules.openApply({ ctx: w.ctx, rule, opener });
  await T.settle();
  ok(w.document.activeElement === w.cancelBtn() && w.applyBtn().disabled, 'D5 loading: Cancel focused, Apply disabled');
  ok(/Checking 6 pairs/.test(w.document.querySelector('.gen-rule-status').textContent), 'D5 text: ' + w.document.querySelector('.gen-rule-status').textContent);
  ok(!!w.document.querySelector('.gen-rule-skel'), 'D5 skeleton rows');
  ok(Array.from(w.document.querySelectorAll('.gen-rule-chip')).every(b => b.disabled), 'D5 Select chips disabled while loading');
  srv.hold = false; srv.release(); await T.settle();

  const txt = () => w.dlg().textContent;
  ok(/Ready = SM's checks pass\. Your lab code runs at Build\./.test(txt()), 'header line');
  ok(/Ready — 3 of 6 pairs/.test(txt()), 'D1 Ready denominator: ' + txt());
  ok(/Needs a look — 1/.test(txt()) && /Already has it — 1/.test(txt()), 'D1 other groups counted');
  ok(/Add cz_bm to other pairs/.test(txt()) && /from q1-2/.test(txt()), 'title and source');
  ok(/Numbers copied from q1-2 \(current values\) as initial values: high: amplitude 0\.12, length 40 ns/.test(txt()), 'numbers line: ' + w.dlg().querySelector('.gen-rule-nums').textContent);
  const cb = (pid) => w.dlg().querySelector('.gen-rule-row-cb[data-pair="' + pid + '"]');
  ok(cb('q2-3').checked && cb('q4-5').checked && !cb('q5-6').checked, 'D1 Ready ticked, Needs a look not');
  ok(!w.dlg().querySelector('.gen-rule-row[data-pair="q1-2"] input') && !w.dlg().querySelector('.gen-rule-cant input'), 'D1 Already / Cant have no checkbox');
  ok(/Add to 3 pairs/.test(w.applyBtn().textContent), 'D2 count: ' + w.applyBtn().textContent);
  ok(w.document.activeElement.classList.contains('gen-rule-grp-cb'), 'focus lands on the first group checkbox once answered');

  const chips = () => Array.from(w.dlg().querySelectorAll('.gen-rule-chip'));
  ok(chips().map(c => c.textContent).join('|') === 'All ready|Feedline ▾|None', 'C3 exactly three chips, no Chain: ' + chips().map(c => c.textContent));
  const pressed = () => chips().map(c => c.getAttribute('aria-pressed')).join(',');
  ok(pressed() === 'true,false,false', 'C1 All ready pressed at the default: ' + pressed());
  chips()[2].click();
  ok(pressed() === 'false,false,true' && !cb('q2-3').checked, 'C1 None pressed, nothing ticked');
  // Feedline: menu with the groups
  chips()[1].click();
  const items = Array.from(w.document.querySelectorAll('.gen-rule-menuitem'));
  ok(items.length === 2 && /feedline1/.test(items[0].textContent) && /2 ready pairs/.test(items[0].textContent), 'C2 menu lists feedlines: ' + items.map(i => i.textContent));
  items[0].click();
  ok(cb('q2-3').checked && cb('q3-4').checked && !cb('q4-5').checked && !cb('q5-6').checked, 'C2 only feedline1 Ready pairs ticked');
  ok(pressed() === 'false,true,false', 'C1 Feedline pressed: ' + pressed());
  chips()[0].click();
  ok(pressed() === 'true,false,false' && cb('q4-5').checked && !cb('q5-6').checked, 'C1 All ready re-ticks Ready only');

  // apply: untick one, send exactly the rest
  cb('q3-4').checked = false; cb('q3-4').dispatchEvent(new w.Event('change', { bubbles: true }));
  ok(/Add to 2 pairs/.test(w.applyBtn().textContent), 'D2 count follows ticks');
  w.document.querySelector('.gen-rule-later-cb').checked = true;
  w.applyBtn().click();
  await T.settle();
  ok(srv.applies.length === 1 && srv.applies[0].pairs.join() === 'q2-3,q4-5', 'D2 sends exactly the ticked pairs: ' + JSON.stringify(srv.applies[0] && srv.applies[0].pairs));
  ok(typeof srv.applies[0].rule_sig === 'string', 'rule_sig of the adopted answer rides the apply');
  ok(!w.dlg(), 'dialog closed after apply');
  ok(w.undo.length === 1 && /add cz_bm to 2 pairs/.test(w.undo[0].label), 'D3 one undo entry: ' + JSON.stringify(w.undo.map(u => u.label)));
  ok(Array.isArray(w.spec.addition_rules) && w.spec.addition_rules[0].auto_new === true, 'D3 the spec now carries the rule (auto_new from the checkbox)');
  ok(w.toasts.length === 1 && w.toasts[0].m === 'Added cz_bm to 2 pairs (q2-3, q4-5). Ctrl+Z undoes it.', 'D3 toast: ' + JSON.stringify(w.toasts));
  w.undo[0].fn();
  ok(!w.spec.addition_rules && !(w.spec.additions || []).length, 'D3 undo restores the spec in place');

  // D4 all can't
  ({ w, srv, spec } = T.makeWorld({ nq: 4, server: { statusOf: (pid) => pid === 'q1-2' ? 'Already has it' : "Can't" } }));
  const r2 = T.baseRule(); r2.source = 'q1-2';
  w.GenRules.openApply({ ctx: w.ctx, rule: r2 });
  await T.settle();
  ok(/No other pair can take this gate\./.test(w.dlg().textContent), 'D4 message');
  ok(w.cancelBtn().textContent === 'Close' && w.document.activeElement === w.cancelBtn(), 'D4 Close focused');
  ok(w.applyBtn().textContent === 'Nothing to add' && w.applyBtn().disabled, 'D4 Nothing to add disabled');
  ok(w.document.querySelector('.gen-rule-later').hidden, 'D4 "created later" hidden');
  ok(/Can't — 2 of 3 pairs/.test(w.dlg().textContent) && /2 — no coupler line on the pair/.test(w.dlg().textContent), 'D4 Cant by reason: ' + w.dlg().textContent);
  T.done('rules_select_chips_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
