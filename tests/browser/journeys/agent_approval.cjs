/* Agent home: an approval card on a big chip, pressed by hand. Edit one
 * proposed value by typing, press "Write to chip" (timed to the toast), then
 * Reject the other with a typed note. Reload; Back.
 *   SM_CDP_PORT=9413 node agent_approval.cjs 5113 OUTDIR WIDTH
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W] = [process.argv[2], process.argv[3], +process.argv[4] || 1366];

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent`, W, 900);
  const dialogs = [];
  setInterval(async () => {
    for (const e of p.events.splice(0)) {
      if (e.method === 'Page.javascriptDialogOpening') { dialogs.push(e.params.type + ': ' + e.params.message.slice(0, 60)); await p.send('Page.handleJavaScriptDialog', { accept: true, promptText: 'QA: not this one' }); }
      else p.events.push(e);
    }
  }, 50);
  await p.ev(`localStorage.setItem('quam_agent_observer', '0')`);
  await p.send('Page.reload'); await sleep(3000);
  const cards = `JSON.stringify([...document.querySelectorAll('#agent-home [data-card^="approval:"]')].map(c => ({ id: c.getAttribute('data-card'), rows: c.querySelectorAll('.ag-ap-rows tbody tr').length, h: Math.round(c.getBoundingClientRect().height) })))`;
  console.log('approval cards', await p.ev(cards));
  console.log('strip', await p.ev(`(document.querySelector('#agent-home .ag-now-main') || {}).textContent`));
  console.log('pill ', await p.ev(`(document.querySelector('#agent-pill') || {}).textContent.replace(/\\s+/g, ' ').trim()`));
  await p.shot(`${OUT}/approval_cards_${W}.png`);
  // the 60-row card: type a new value into row 8 (q5 amplitude)
  const big = await p.ev(`(() => { const c = [...document.querySelectorAll('#agent-home [data-card^="approval:"]')].find(c => c.querySelectorAll('.ag-ap-rows tbody tr').length > 10); return c ? c.getAttribute('data-card') : null; })()`);
  const inXY = JSON.parse(await p.ev(`(() => { const c = document.querySelector('[data-card="${big}"]'); const i = c.querySelector('.ag-ap-new[data-i="8"]'); i.scrollIntoView({block:'center'}); const b = i.getBoundingClientRect(); return JSON.stringify([b.x + b.width - 8, b.y + b.height/2]); })()`));
  await p.click(inXY[0], inXY[1]);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await p.send('Input.insertText', { text: '0.123456' });
  console.log('row 8', await p.ev(`(() => { const c = document.querySelector('[data-card="${big}"]'); const r = c.querySelectorAll('.ag-ap-rows tbody tr')[8]; return r.cells[0].textContent + ' = ' + r.querySelector('input').value; })()`));
  await sleep(4500);   // a poll or two while the person holds the field: it must survive
  console.log('row 8 after polls', await p.ev(`(() => { const c = document.querySelector('[data-card="${big}"]'); const r = c.querySelectorAll('.ag-ap-rows tbody tr')[8]; return r.querySelector('input').value + ' focus=' + (document.activeElement === r.querySelector('input')); })()`));
  const btn = JSON.parse(await p.ev(`(() => { const b = document.querySelector('[data-card="${big}"] .ag-approve'); b.scrollIntoView({block:'center'}); const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width/2, r.y + r.height/2]); })()`));
  const t0 = Date.now();
  await p.click(btn[0], btn[1]);
  await sleep(800);
  console.log('in flight', await p.ev(`(() => { const c = document.querySelector('[data-card="${big}"]'); return JSON.stringify({ btn: c.querySelector('.ag-approve').textContent, disabled: c.querySelector('.ag-approve').disabled, reject: c.querySelector('.ag-reject') ? !c.querySelector('.ag-reject').hidden : null }); })()`));
  await p.shot(`${OUT}/approval_inflight_${W}.png`);
  let toast = null;
  for (let i = 0; i < 600 && !toast; i++) { await sleep(50); toast = await p.ev(`(() => { const t = document.querySelector('#status-bar .toast') || document.getElementById('ag-toast'); return t && !t.hidden ? t.textContent : null; })()`); }
  console.log('approve ->', JSON.stringify(toast), 'after', Date.now() - t0, 'ms');
  await sleep(2500);
  console.log('approval cards', await p.ev(cards));
  await p.shot(`${OUT}/approval_after_${W}.png`);
  // reject the small one
  const rj = await p.ev(`(() => { const b = [...document.querySelectorAll('#agent-home [data-card^="approval:"] .ag-reject')][0]; if (!b) return null; b.scrollIntoView({block:'center'}); const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width/2, r.y + r.height/2]); })()`);
  if (rj) { const xy = JSON.parse(rj); await p.click(xy[0], xy[1]); await sleep(2500); }
  console.log('after reject', await p.ev(cards), 'dialogs', JSON.stringify(dialogs));
  await p.send('Page.reload'); await sleep(3000);
  console.log('reload', await p.ev(cards), '| pill', await p.ev(`(document.querySelector('#agent-pill') || {}).textContent.replace(/\\s+/g, ' ').trim()`));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
