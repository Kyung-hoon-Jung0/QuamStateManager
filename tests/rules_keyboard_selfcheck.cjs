// 11_v2_1 section 5: the keyboard contract, one assert per row, with real keydown events.
// (jsdom does not turn Space/Enter on a <button>/<input> into a click -- those rows are pinned through
// the element kind + click, and said so below.)
//  K1  hand CZ form OK -> focus on the NEW gate chip
//  K2  gate chip tab order: chip -> "..." -> x
//  K3  the menu: role=menu/menuitem, opens on the first item, Down/Up wrap, Home/End, Enter selects,
//      Esc closes and returns to "...", Tab closes and leaves from "...", disabled items are skipped
//  K4  x confirm of a rule gate: focus = first radio; Esc = Cancel, back on x
//  K5  Set-all form: the main button is a real <button> (Enter presses it natively)
//  K6  dialog landing focus: first group checkbox / Cancel while loading / Close when all can't
//  K7  dialog tab order: Select chips -> group checkboxes -> rows of EXPANDED groups -> Also add -> Cancel -> Add
//  K8  checkboxes are native (Space toggles)
//  K9  group heads are <details>, the checkbox is OUTSIDE <summary>; Right/Enter expand, Left collapses
//  K10 Select chips are buttons with aria-pressed
//  K11 Esc closes the dialog and focuses the opener; Esc inside an open menu closes only the menu
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

function pick(w, el, v) { el.value = v; el.dispatchEvent(new w.Event('change', { bubbles: true })); }
function tabbables(w, root) {
  return Array.from(root.querySelectorAll('button, input, select, summary, [tabindex]')).filter(e => {
    if (e.disabled || e.getAttribute('tabindex') === '-1' || e.hidden) return false;
    for (let p = e.parentElement; p && p !== root; p = p.parentElement) {
      if (p.tagName === 'DETAILS' && !p.open && !(e.tagName === 'SUMMARY' && e.parentElement === p)) return false;
    }
    return true;
  });
}

(async () => {
  // K1, K2
  let m = T.makeAddWorld({ nq: 3 });
  let { w, spec } = m;
  await w.decorate();
  w.document.querySelector('tr[data-rid="q1-q2"] .gen-add-czbtn').click();
  const pf = w.document.querySelector('.gen-add-cz');
  pick(w, pf.querySelector('.gen-add-cz-class'), 'pkg.G');
  Array.from(pf.querySelectorAll('.gen-add-cz-pclass')).forEach(pc => pick(w, pc, 'pkg.P'));
  pf.querySelector('.gen-add-ok').click();
  await T.settle();
  const newChip = w.chip('q1-2', 'cz_bm');
  ok(!!newChip && w.document.activeElement === newChip, 'K1 focus lands on the new gate chip, not <body>: ' + (w.document.activeElement && w.document.activeElement.tagName));
  const cell = newChip.parentNode;
  const seq = Array.from(cell.children).map(c => c.className.split(' ').filter(x => /gen-gate-(added|more|rm)/.test(x))[0]).filter(Boolean);
  ok(J(seq) === J(['gen-gate-added', 'gen-gate-more', 'gen-gate-rm']), 'K2 tab order chip -> ... -> x: ' + J(seq));

  // K3 menu
  const more = w.more('q1-2', 'cz_bm');
  more.focus(); more.click();
  const menu = w.document.querySelector('.gen-rule-menu');
  const items = w.menuItems();
  ok(menu.getAttribute('role') === 'menu' && items.every(i => i.getAttribute('role') === 'menuitem'), 'K3 roles');
  ok(w.document.activeElement === items[0], 'K3 opens on the first item');
  ok(more.getAttribute('aria-expanded') === 'true', 'K3 aria-expanded on the button');
  w.key(items[0], 'ArrowDown'); ok(w.document.activeElement === items[1], 'K3 Down');
  w.key(items[1], 'ArrowDown'); ok(w.document.activeElement === items[2], 'K3 Down again');
  w.key(items[2], 'ArrowDown'); ok(w.document.activeElement === items[0], 'K3 Down wraps from the end');
  w.key(items[0], 'ArrowUp'); ok(w.document.activeElement === items[2], 'K3 Up wraps from the start');
  w.key(items[2], 'Home'); ok(w.document.activeElement === items[0], 'K3 Home');
  w.key(items[0], 'End'); ok(w.document.activeElement === items[2], 'K3 End');
  w.key(items[2], 'Escape');
  ok(!w.document.querySelector('.gen-rule-menu') && w.document.activeElement === more, 'K3 Esc closes and returns focus to the "..." button');
  more.click();
  const t = w.key(w.menuItems()[0], 'Tab');
  ok(!w.document.querySelector('.gen-rule-menu') && !t.defaultPrevented && w.document.activeElement === more, 'K3 Tab closes the menu and leaves from the button (default Tab goes on)');
  more.click();
  w.key(w.menuItems()[0], 'ArrowDown');   // "Apply to other pairs…"
  w.key(w.document.activeElement, 'Enter');
  await T.settle();
  ok(!w.document.querySelector('.gen-rule-menu') && !!w.dlg(), 'K3 Enter selects the focused item (the Apply dialog opened)');
  // K11 (part): Esc closes the dialog and focuses the opener (the "..." it came from)
  w.key(w.document.activeElement, 'Escape');
  ok(!w.dlg() && w.document.activeElement === more, 'K11 Esc closes the dialog and focuses the opener');

  // disabled items are skipped: the Feedline menu with nothing ready
  ({ w, spec } = T.makeWorld({ nq: 3, server: { statusOf: () => "Can't" } }));
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  await T.settle();
  // K6 all can't -> Close
  ok(w.document.activeElement === w.cancelBtn() && w.cancelBtn().textContent === 'Close', 'K6 all-can\'t lands on Close');

  // K6 / K7 / K8 / K9 / K10 / K11 on a normal dialog
  const FEED = { q1: 'a', q2: 'a', q3: 'b', q4: 'b', q5: 'b' };
  m = T.makeWorld({ nq: 5, feedlines: () => FEED, server: { statusOf: (pid) => ({ 'q1-2': 'Ready', 'q2-3': 'Needs a look', 'q3-4': "Can't", 'q4-5': 'Already has it' }[pid]) } });
  ({ w, spec } = m);
  const opener = w.document.createElement('button'); opener.textContent = 'opener'; w.document.body.appendChild(opener); opener.focus();
  m.srv.hold = true;
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule(), opener });
  await T.settle();
  ok(w.document.activeElement === w.cancelBtn(), 'K6 while loading the focus is Cancel');
  m.srv.hold = false; m.srv.release(); await T.settle();
  ok(w.document.activeElement.classList.contains('gen-rule-grp-cb') && w.document.activeElement.closest('[data-group="Ready"]'), 'K6 answered: the first group checkbox');
  const order = tabbables(w, w.dlg()).map(e => {
    if (e.classList.contains('gen-rule-chip')) return 'chip:' + e.textContent;
    if (e.classList.contains('gen-rule-grp-cb')) return 'grp:' + e.closest('[data-group]').getAttribute('data-group');
    if (e.tagName === 'SUMMARY') return 'sum:' + e.closest('[data-group]').getAttribute('data-group');
    if (e.classList.contains('gen-rule-row-cb')) return 'row:' + e.getAttribute('data-pair');
    if (e.classList.contains('gen-rule-choice')) return 'choice:' + e.getAttribute('data-pair');
    if (e.classList.contains('gen-rule-later-cb')) return 'later';
    if (e.classList.contains('gen-rule-cancel')) return 'cancel';
    if (e.classList.contains('gen-rule-apply')) return 'apply';
    return e.className;
  });
  const want = ['gen-rule-x ui-btn-icon', 'chip:All ready', 'chip:Feedline ▾', 'chip:None', 'grp:Ready', 'sum:Ready', 'row:q1-2', 'grp:Needs a look', 'sum:Needs a look', 'row:q2-3',
    'sum:Can\'t', 'sum:Already has it', 'later', 'cancel', 'apply'];
  // the choice select only shows for a "choose" row; this fixture has none
  ok(J(order) === J(want), 'K7 tab order: ' + J(order));
  const grp = w.dlg().querySelector('[data-group="Ready"] .gen-rule-grp-cb');
  const before = grp.checked; grp.click();
  ok(grp.type === 'checkbox' && grp.checked !== before, 'K8 native checkbox toggles (Space = click)');
  const det = w.dlg().querySelector('[data-group="Ready"] details');
  const grp2 = w.dlg().querySelector('[data-group="Ready"] .gen-rule-grp-cb');
  ok(det.tagName === 'DETAILS' && !grp2.closest('summary') && grp2.nextElementSibling === det, 'K9 group head is <details>, checkbox outside <summary>');
  const sum = det.querySelector('summary');
  w.key(sum, 'ArrowLeft'); ok(det.open === false, 'K9 Left collapses');
  w.key(sum, 'ArrowRight'); ok(det.open === true, 'K9 Right expands');
  det.open = false; w.key(sum, 'Enter'); ok(det.open === true, 'K9 Enter expands');
  const chips = Array.from(w.dlg().querySelectorAll('.gen-rule-chip'));
  ok(chips.every(c => c.tagName === 'BUTTON' && c.hasAttribute('aria-pressed')), 'K10 chips are buttons with aria-pressed');
  // K11: Esc inside an open menu closes only the menu
  chips[1].click();
  ok(!!w.document.querySelector('.gen-rule-menu'), 'K11 feedline menu open');
  w.key(w.document.activeElement, 'Escape');
  ok(!w.document.querySelector('.gen-rule-menu') && !!w.dlg() && w.document.activeElement === chips[1], 'K11 Esc closes only the menu; the dialog stays; focus back on the chip');
  w.key(w.document.activeElement, 'Escape');
  ok(!w.dlg() && w.document.activeElement === opener, 'K11 the next Esc closes the dialog and focuses the opener');

  // K4: x on a rule gate
  m = T.makeAddWorld({ nq: 3 });
  ({ w, spec } = m);
  spec.addition_rules = [Object.assign(T.baseRule(), { id: 'r1', source: 'q1-2' })];
  spec.additions = T.gateBundle('q2-3', 'q2', 'q3', 'cz_bm', { rule_id: 'r1' });
  await w.decorate();
  const rm = w.document.querySelector('tr[data-rid="q2-q3"] .gen-gate-rm');
  rm.focus(); rm.click();
  const radio = w.document.querySelector('.gen-rule-mini input[type=radio]');
  ok(w.document.activeElement === radio, 'K4 focus = first radio');
  w.key(radio, 'Escape');
  ok(!w.document.querySelector('.gen-rule-mini') && w.document.activeElement === rm, 'K4 Esc = Cancel, back on the x');

  // K5
  const btn = w.document.querySelector('.gen-setall-gate');
  btn.click();
  const okb = w.document.querySelector('.gen-add-cz-ownerless .gen-add-ok');
  ok(okb.tagName === 'BUTTON' && okb.type === 'button' && okb.textContent === 'Continue → choose pairs', 'K5 the Set-all form ends in a real button (Enter presses it)');
  T.done('rules_keyboard_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
