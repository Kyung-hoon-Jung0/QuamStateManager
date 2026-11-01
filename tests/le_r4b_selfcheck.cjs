/* R4b Live State Edit: the shipped version-label.js, undo-trail.js and the
 * extreme-marker helper of bulk-edit.js / pair-edit.js under jsdom.
 * Run: node tests/le_r4b_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const src = f => fs.readFileSync(path.join(STATIC, f), 'utf8');

function world() {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(String(e && (e.detail || e.message || e))));
  const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  dom.window.addEventListener('error', e => errors.push(String(e.message)));
  return { win: dom.window, errors };
}

// LE-16: a keydown / click whose target is not an Element must not throw
{
  const { win, errors } = world();
  win.eval(src('version-label.js'));
  win.document.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  win.document.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  const txt = win.document.createTextNode('x'); win.document.body.appendChild(txt);
  txt.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(errors.length === 0, 'version-label: a non-Element event target raises nothing (' + errors.join(' | ') + ')');
  // and the editor still works for a real element
  win.document.body.insertAdjacentHTML('beforeend',
    '<div class="version-label-edit"><button data-version-label-open>edit</button><form hidden><input name="label"></form></div>');
  win.document.querySelector('[data-version-label-open]').click();
  ok(!win.document.querySelector('.version-label-edit form').hidden, 'version-label: the open button still opens the form');
}

// LE-13: a landed undo's message is a line in the trail; a warning is not
{
  const { win, errors } = world();
  win.eval(src('undo-trail.js'));
  const fire = d => win.document.dispatchEvent(new win.CustomEvent('cellsReverted', { detail: d }));
  fire({ message: 'Undone — in SM only, the chip file is unchanged: qubits.q1.f_01 → 5', entries: [{ dot_path: 'qubits.q1.f_01', old_value_disp: '5' }] });
  const note = win.document.querySelector('.undo-trail-note');
  ok(note && note.textContent === 'Undone — in SM only, the chip file is unchanged', 'trail: the message head is a line in the step (' + (note && note.textContent) + ')');
  fire({ message: 'Not undone — take the live changes: x', level: 'warning', entries: [{ dot_path: 'qubits.q1.f_01', old_value_disp: '6' }] });
  const latest = win.document.querySelector('.undo-trail-step.undo-trail-latest');
  ok(latest && !latest.querySelector('.undo-trail-note'), 'trail: a warning step carries no note (it still toasts)');
  ok(win.document.querySelectorAll('.undo-trail-goto').length === 2, 'trail: go to field stays reachable on every step');
  ok(errors.length === 0, 'trail: no errors');
}

// LE-13 (app.js side): a landed step with a trail does not toast; a warning does
{
  const app = src('app.js');
  ok(/var _inTrail = !!\(window\.UndoTrail && entries\.length && d\.level !== "warning" && d\.level !== "error"\);/.test(app)
     && /if \(d\.message && window\.showToast && !_inTrail\) window\.showToast\(d\.message,/.test(app),
     'app.js: the cellsReverted toast is skipped only when the trail shows it');
}

// LE-1: the extreme marker is a class + a title suffix, toggled cleanly
for (const f of ['bulk-edit.js', 'pair-edit.js']) {
  const s = src(f);
  const m = s.match(/function _markExt\(cell, kind\) \{[\s\S]*?\n    \}/);
  ok(!!m, f + ': _markExt exists');
  if (!m) continue;
  const { win } = world();
  win.eval(m[0] + ';window._markExt=_markExt;');
  const c = win.document.createElement('input'); c.title = 'qubits.q1.f_01';
  win._markExt(c, 'max');
  ok(c.classList.contains('cell-best') && c.title === 'qubits.q1.f_01 — column max', f + ': max -> cell-best + title');
  win._markExt(c, 'min');
  ok(c.classList.contains('cell-worst') && !c.classList.contains('cell-best') && c.title === 'qubits.q1.f_01 — column min', f + ': min replaces max');
  win._markExt(c, null);
  ok(!c.className && c.title === 'qubits.q1.f_01', f + ': cleared -> no class, title restored');
  ok(/_markExt\(cell, 'max'\)/.test(s) && /_markExt\(cell, 'min'\)/.test(s) && /_markExt\(cell, null\)/.test(s), f + ': the stats pass uses it');
}

// LE-15: the f01<->RF mirror says so once per focus session
{
  const s = src('bulk-edit.js');
  ok(/if \(!cell\._freqToasted\) \{\s*cell\._freqToasted = true;\s*if \(window\.showToast\) window\.showToast\('1 edit \+ 1 linked \(' \+ _colLabelOf\(twin\) \+ '\)', 'info'\);/.test(s),
     'bulk-edit: one toast "1 edit + 1 linked (<twin column>)" per focus');
  ok(/cell\._freqCoupled = coupled;\s*cell\._freqToasted = false;/.test(s), 'bulk-edit: the flag resets on focus');
}

console.log(passes + ' passed, ' + fails + ' failed');
process.exit(fails ? 1 : 0);
