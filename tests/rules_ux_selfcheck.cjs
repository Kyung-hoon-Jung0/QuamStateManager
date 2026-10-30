// UX fixes X3-X10 for the "+ CZ…" form, the Apply dialog and the class selects, EXECUTED under jsdom.
//  X3  a successful Add CZ closes the form (and its row); no stale error text survives
//  X4  the form lives in its own full-width row (colspan) that is scrolled into view; the stylesheet lifts it above sticky cells
//  X5  the dialog's copied-numbers line is grouped by role and lists every field
//  X6  the dialog header names the class once
//  X7  identical option labels get the module path appended
//  X8  the chip badge text is spaced
//  X9  a refusal for missing required fields focuses the first missing input
//  X10 "Read classes" keeps the typed modules and says what was read
'use strict';
const fs = require('fs');
const path = require('path');
const T = require('./rules_world.cjs');
const { ok } = T;

function pick(w, el, v) { el.value = v; el.dispatchEvent(new w.Event('change', { bubbles: true })); }

(async () => {
  // roles: a pulse class with a required field, and one short name from two module homes
  const F = (raw, has_default, kind, d) => ({ raw, has_default: !!has_default, kind: kind || 'plain', default_repr: d });
  T.ROLES.pulses['pkg.R'] = { origin: 'lab', fields: { amplitude: F('float', false, 'plain'), flat_length: F('int', false, 'plain') } };
  T.ROLES.pulses['quam.components.pulses.DragCosinePulse'] = { origin: 'official', fields: {} };
  T.ROLES.pulses['quam_builder.pulses.DragCosinePulse'] = { origin: 'official', fields: {} };

  let m = T.makeAddWorld({ nq: 3 });
  let w = m.w;
  const doc = w.document;
  let scrolled = 0;
  w.Element.prototype.scrollIntoView = function () { scrolled++; };
  await w.decorate();
  const tr = () => doc.querySelector('tr[data-rid="q1-q2"]');
  tr().querySelector('.gen-add-czbtn').click();
  const form = () => doc.querySelector('.gen-add-cz:not(.gen-add-cz-ownerless)');
  // X4: own row, full width, scrolled into view
  const frow = tr().nextElementSibling;
  ok(frow && frow.classList.contains('gen-gates-formrow') && frow.firstChild.getAttribute('colspan') === String(tr().children.length), 'X4 form in its own full-width row');
  ok(scrolled > 0, 'X4 the form is scrolled into view');
  const css = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
  ok(/tr\.gen-gates-formrow\s*>\s*td\s*\{[^}]*z-index:\s*[5-9]/.test(css), 'X4 stylesheet lifts the form row above the sticky header (z-index 3)');
  // X7: duplicate labels disambiguated
  pick(w, form().querySelector('.gen-add-cz-class'), 'pkg.G');
  const pcs = Array.from(form().querySelectorAll('.gen-add-cz-pclass'));
  const labels = Array.from(pcs[0].options).map(o => o.textContent);
  ok(new Set(labels).size === labels.length, 'X7 every option label is unique: ' + labels.join(' | '));
  ok(labels.indexOf('DragCosinePulse · official · quam.components.pulses') >= 0 && labels.indexOf('DragCosinePulse · official · quam_builder.pulses') >= 0, 'X7 module home appended');
  ok(labels.indexOf('P · lab') >= 0, 'X7 a unique label stays plain');
  // X9: refusal focuses the first missing required input
  pcs.forEach(pc => pick(w, pc, 'pkg.R'));
  form().querySelector('.gen-add-ok').click();
  await T.settle();
  const msg = () => form() ? form().querySelector('.gen-add-msg').textContent : '';
  ok(/pulse requires amplitude, flat_length/.test(msg()), 'X9 refused: ' + msg());
  const ae = doc.activeElement;
  ok(ae && ae.getAttribute('data-field') === 'amplitude' && form().contains(ae), 'X9 the first missing input is focused');
  // X3: fix and add -> the form and its row are gone, no error text left
  Array.from(form().querySelectorAll('[data-field="amplitude"]')).forEach(i => { i.value = '0.1'; });
  Array.from(form().querySelectorAll('[data-field="flat_length"]')).forEach(i => { i.value = '20'; });
  form().querySelector('.gen-add-ok').click();
  await T.settle();
  ok((w.gateActions || []).length === 1, 'X3 the gate was added');
  ok(!form() && !doc.querySelector('.gen-gates-formrow') && !/requires amplitude/.test(doc.body.textContent), 'X3 the form is closed and the old error is gone');
  // X8: chip badge spacing
  const chip = w.chip('q1-2', 'cz_bm');
  ok(!chip || !/[^ ]·rule/.test(chip.textContent), 'X8 no cramped "·rule": ' + (chip && chip.textContent));

  // X10: typed modules survive Read classes
  const mods = () => doc.querySelector('.gen-add-modules');
  mods().value = 'my_lab.two_flux_gate';
  mods().dispatchEvent(new w.Event('input', { bubbles: true }));
  doc.querySelector('.gen-add-read').click();
  await T.settle();
  ok(mods().value === 'my_lab.two_flux_gate', 'X10 typed modules kept: "' + mods().value + '"');
  const rf = doc.querySelector('.gen-add-readfrom');
  ok(rf && /read from: my_lab\.two_flux_gate/.test(rf.textContent), 'X10 says what was read');

  // X5 / X6: the dialog
  m = T.makeWorld({ nq: 3 });
  w = m.w;
  const rule = T.baseRule(); rule.source = 'q1-2';
  rule.numbers = { 'control.amplitude': 0.12, 'control.flat_length': 20, 'target.amplitude': 0.08, 'target.flat_length': 20 };
  const opener = w.document.createElement('button'); w.document.body.appendChild(opener);
  w.GenRules.openApply({ ctx: w.ctx, rule, opener });
  await T.settle();
  const nums = w.dlg().querySelector('.gen-rule-nums').textContent;
  ok(/control: amplitude 0\.12, flat_length 20 ns; target: amplitude 0\.08, flat_length 20 ns/.test(nums), 'X5 grouped by role, every field: ' + nums);
  const sub = w.dlg().querySelector('.gen-rule-sub, .gen-rule-class');
  const head = w.dlg().textContent;
  ok(!/\b(\w+) \(\1[,)]/.test(head), 'X6 the class name is not repeated: ' + head.slice(0, 120));
  const sheet = css.match(/\.gen-rule-row input\[type="checkbox"\][^{]*\{[^}]*\}/);
  ok(sheet && /width:\s*1\.\d+rem/.test(sheet[0]) && /height:\s*1\.\d+rem/.test(sheet[0]), 'X6 checkboxes have a real square size');
  // X4 remnant: "table.gen-pop-table tbody td:first-child" (0,2,3, sticky z-index 2) out-ranked the old
  // "tr.gen-gates-formrow > td" (0,1,2), so the sticky header still painted over the form.
  ok(/table\.gen-pop-table\s+tbody\s+tr\.gen-gates-formrow\s*>\s*td[^{]*\{[^}]*z-index:\s*[4-9]/.test(css), 'X4 the form-row rule out-ranks the sticky first-column rule');
  ok(/tr\.gen-gates-formrow input[^{]*\{[^}]*scroll-margin-top:\s*[1-9]/.test(css), 'X4 a focused form field stops below the sticky header');
  // Y3: the review list is the one scroller; the dialog itself does not scroll its head/foot away.
  ok(/dialog\.gen-rule-dlg\s*>\s*\.gen-rule-rv-list[^{]*\{[^}]*flex:\s*1 1 auto[^}]*overflow:\s*auto/.test(css), 'Y3 the review list flexes and scrolls');
  ok(/dialog\.gen-rule-dlg\.gen-rule-review\s*\{[^}]*overflow:\s*hidden/.test(css), 'Y3 the review dialog keeps title and footer in view');
  T.done('rules_ux_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
