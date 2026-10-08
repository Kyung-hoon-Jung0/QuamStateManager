/* Agent home: every remaining control pressed with a real mouse / keyboard,
 * each followed by a check that the page is still whole.
 *   SM_CDP_PORT=9413 node agent_controls.cjs 5113 OUTDIR WIDTH
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W] = [process.argv[2], process.argv[3], +process.argv[4] || 1600];

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent`, W, 900);
  const dialogs = [];
  setInterval(async () => {
    for (const e of p.events.splice(0)) {
      if (e.method === 'Page.javascriptDialogOpening') { dialogs.push(e.params.message.slice(0, 80)); await p.send('Page.handleJavaScriptDialog', { accept: true }); }
      else p.events.push(e);
    }
  }, 100);
  await sleep(3000);
  const at = async (sel, re) => {
    const r = await p.ev(`(() => { const re = ${re ? re : 'null'}; const e = [...document.querySelectorAll(${JSON.stringify(sel)})].find(e => e.getBoundingClientRect().width > 0 && (!re || re.test(e.textContent || e.value || '')));
      if (!e) return null; e.scrollIntoView({block:'nearest'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`);
    return r ? JSON.parse(r) : null;
  };
  const press = async (sel, re) => { const xy = await at(sel, re); if (!xy) return false; await p.click(xy[0], xy[1]); return true; };
  const whole = async () => p.ev(`(() => { const h = document.getElementById('agent-home'); const S = AgentPanel._state;
    return JSON.stringify({ path: location.pathname, live: !!h && S.mounts.some(m => h.contains(m.root)), roots: document.querySelectorAll('.ag-root').length,
      doors: [...document.querySelectorAll('#agent-home .ag-now button')].map(b => b.textContent.trim()).join(','), send: (document.querySelector('#agent-home .ag-send') || {}).disabled }); })()`);
  const log = async (tag) => console.log(tag.padEnd(22), await whole());
  await log('open');

  // 1. observer on -> doors go, reload keeps it, off again
  console.log('observer box', await press('#agent-home .ag-now input[type=checkbox]'));
  await sleep(800); await log('observer on');
  await p.send('Page.reload'); await sleep(3500); await log('observer on +reload');
  await p.shot(`${OUT}/ctl_observer_${W}.png`);
  await press('#agent-home .ag-now input[type=checkbox]'); await sleep(800); await log('observer off');

  // 2. a preset fills the box, Send enables; clearing locks it again
  console.log('preset', await press('#agent-home .ag-preset', '/1Q bringup/'));
  await sleep(300);
  console.log('  box', JSON.stringify(await p.ev(`document.querySelector('#agent-home .ag-input').value`)), 'focus', await p.ev('document.activeElement.className'));
  await log('preset filled');
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.key('Backspace', 'Backspace', 8); await sleep(300);
  await log('cleared');

  // 3. actor name typed, survives a reload
  await press('#agent-home .ag-actor');
  await p.send('Input.insertText', { text: 'qa-tester' }); await sleep(300);
  await p.send('Page.reload'); await sleep(3500);
  console.log('actor after reload', JSON.stringify(await p.ev(`document.querySelector('#agent-home .ag-actor').value`)));
  // an actor with Hangul is refused / cleaned (it rides a header)
  await press('#agent-home .ag-actor');
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.send('Input.insertText', { text: '\uac00\uac01\uac02' }); await sleep(300);
  console.log('actor hangul', JSON.stringify(await p.ev(`[document.querySelector('#agent-home .ag-actor').value, localStorage.getItem('quam_actor_name')]`)));
  // verifier P1: with that name in the box, a MOUSE click on Send must still
  // submit (a custom validity on the box used to invalidate the composer form).
  // The real submit is stubbed: this journey must not start a chat.
  await p.ev(`window.__sub = 0; window.__inv = 0; window.__realSubmit = AgentPanel.submit; AgentPanel.submit = function (e) { window.__sub++; if (e && e.preventDefault) e.preventDefault(); return false; };
    document.querySelector('#agent-home .ag-actor').addEventListener('invalid', function () { window.__inv++; }); 1`);
  await press('#agent-home .ag-input');
  await p.send('Input.insertText', { text: 'hello' }); await sleep(300);
  console.log('  note', JSON.stringify(await p.ev(`(document.querySelector('#agent-home .ag-actor-note') || {}).textContent || null`)));
  await p.shot(`${OUT}/ctl_actor_note_${W}.png`);
  await press('#agent-home .ag-send'); await sleep(600);
  console.log('SEND CLICK', JSON.stringify(await p.ev(`JSON.stringify({ submits: window.__sub, invalid: window.__inv, formValid: document.querySelector('#agent-home .ag-composer').checkValidity() })`)));
  await p.ev(`AgentPanel.submit = window.__realSubmit; document.querySelector('#agent-home .ag-input').value = ''; localStorage.setItem('quam_actor_name', 'qa-tester'); 1`);
  await p.send('Page.reload'); await sleep(3500); await log('actor +reload');

  // 4. backend select: what it offers
  console.log('backends', await p.ev(`[...document.querySelectorAll('#agent-home .ag-backend option')].map(o => o.textContent + (o.disabled ? '(dis)' : '')).join(',')`));

  // 5. the wiring strip's "?" opens help; a click elsewhere closes it
  console.log('help ?', await press('.ag-wire-help'));
  await sleep(500);
  console.log('  help open', await p.ev(`(() => { const h = document.querySelector('.ag-wire-pop, .ag-wire-helptext, [class*=wire-help]:not(button)'); return h ? (h.hidden ? 'hidden' : h.textContent.trim().slice(0, 80)) : 'none'; })()`));
  await p.shot(`${OUT}/ctl_help_${W}.png`);
  await p.click(700, 600); await sleep(400);
  console.log('  after outside click', await p.ev(`(() => { const h = document.querySelector('.ag-wire-pop, .ag-wire-helptext, [class*=wire-help]:not(button)'); return h ? (h.hidden || getComputedStyle(h).display === 'none' ? 'closed' : 'open') : 'none'; })()`));

  // 6. every link out of the page, and Back each time
  for (const [sel, re] of [['.ag-wire a', '/Connect/'], ['.ag-wire a', '/setup steps/'], ['#agent-home .ag-now a', '/Calibration log/'], ['#agent-home .ag-now a', '/Setup/'], ['a', '/change in Agent setup/']]) {
    const ok = await press(sel, re);
    await sleep(2200);
    const went = await p.ev('location.pathname');
    await p.ev('history.back()'); await sleep(2500);
    console.log(('link ' + re).padEnd(22), 'pressed', ok, '->', went, '| back:', await whole());
  }
  await p.shot(`${OUT}/ctl_end_${W}.png`);
  console.log('dialogs', JSON.stringify(dialogs));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
