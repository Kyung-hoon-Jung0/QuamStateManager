/* Verifier P0/P2 (w7/agentsqa), in real Chrome: "Write to chip" on a big
 * approval while the live state.json is held open from outside (what an
 * experiment reader does on Windows), then Reject it, then approve a small one.
 * The caller holds the file for the first HOLD_S seconds and checks the live
 * values afterwards; this journey presses with a real mouse and reports.
 *   SM_CDP_PORT=9413 node agent_approval_locked.cjs 5113 OUTDIR BIG_ID SMALL_ID HOLD_S
 */
'use strict';
const { open, sleep, base, baseFrom, smPath, smUrl } = require('./cdp.cjs');
const [SM, OUT, BIG, SMALL, HOLD] = [process.argv[2], process.argv[3], process.argv[4], process.argv[5], +process.argv[6] || 30];

(async () => {
  const t00 = Date.now();
  const p = await open(`${base(SM)}/agent`, 1366, 900);
  setInterval(async () => {
    for (const e of p.events.splice(0)) {
      if (e.method === 'Page.javascriptDialogOpening') await p.send('Page.handleJavaScriptDialog', { accept: true, promptText: 'rejected in the locked-file journey' });
      else p.events.push(e);
    }
  }, 100);
  await sleep(3000);
  await p.ev(`localStorage.setItem('quam_actor_name', 'qa'); 1`);
  const btn = async (id, re) => {
    const xy = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card="approval:' + ${JSON.stringify(id)} + '"]'); if (!c) return 'null';
      const b = [...c.querySelectorAll('button')].find(b => ${re}.test(b.textContent)); if (!b) return 'null'; b.scrollIntoView({ block: 'center' });
      const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width / 2, r.y + r.height / 2]); })()`);
    return xy === 'null' ? null : JSON.parse(xy);
  };
  const drift = () => p.ev(`fetch('${smUrl(`/state/drift`)}').then(r => r.json()).then(d => JSON.stringify(d.sync))`);
  console.log('drift at open', await drift());
  let xy = await btn(BIG, /Write to chip/);
  console.log('big card Write to chip', JSON.stringify(xy));
  const t0 = Date.now();
  await p.click(xy[0], xy[1]);
  let err = null;
  for (let i = 0; i < 120 && !err; i++) {
    await sleep(250);
    err = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card="approval:' + ${JSON.stringify(BIG)} + '"]'); const e = c && c.querySelector('.ag-err, .ag-ap-err, .ag-error');
      const t = [...document.querySelectorAll('#status-bar > *')].map(x => x.textContent.trim()).join(' | ');
      return (e && e.textContent.trim()) || (t && /fail|refus|could not|HTTP/i.test(t) ? t : null); })()`);
  }
  console.log(`refused after ${Date.now() - t0} ms:`, JSON.stringify(err));
  await p.shot(`${OUT}/locked_refused.png`);
  console.log('drift after the refused press', await drift());
  const wait = HOLD * 1000 + 1500 - (Date.now() - t00);
  if (wait > 0) await sleep(wait);                       // the outside reader lets go
  xy = await btn(BIG, /Reject/);
  await p.click(xy[0], xy[1]); await sleep(2500);
  console.log('big card after Reject', await p.ev(`document.querySelector('#agent-home [data-card="approval:' + ${JSON.stringify(BIG)} + '"]') ? 'still there' : 'gone'`));
  console.log('drift after Reject', await drift());
  xy = await btn(SMALL, /Write to chip/);
  await p.click(xy[0], xy[1]);
  let gone = false;
  for (let i = 0; i < 120 && !gone; i++) { await sleep(250); gone = await p.ev(`!document.querySelector('#agent-home [data-card="approval:' + ${JSON.stringify(SMALL)} + '"]')`); }
  console.log('small approval written, card gone:', gone);
  await p.send('Page.reload'); await sleep(3500);
  console.log('reload', await p.ev(`JSON.stringify({ path: location.pathname, roots: document.querySelectorAll('#agent-home .ag-root').length, cards: document.querySelectorAll('#agent-home [data-card^="approval:"]').length })`));
  await p.shot(`${OUT}/locked_end.png`);
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
