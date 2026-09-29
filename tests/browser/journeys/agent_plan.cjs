/* Agent home, a plan pressed by hand on a big chip: type a /run line for every
 * qubit, send with a real Enter, look at the card, press Start / Cancel with a
 * real mouse, then reload and Back. Screens at the given width.
 *   SM_CDP_PORT=9413 node agent_plan.cjs 5113 OUTDIR WIDTH CALFOLDER NQ
 */
'use strict';
const { open, sleep, base, baseFrom, smPath, smUrl } = require('./cdp.cjs');
const [SM, OUT, W, CAL, NQ] = [process.argv[2], process.argv[3], +process.argv[4], process.argv[5], +process.argv[6] || 30];

async function xy(p, js) { const r = await p.ev(js); return r && r !== 'null' ? JSON.parse(r) : null; }
const byText = (sel, re) => `(() => { const e = [...document.querySelectorAll(${JSON.stringify(sel)})].reverse().find(e => ${re}.test(e.textContent) && e.getBoundingClientRect().width > 0);
  if (!e) return 'null'; e.scrollIntoView({block:'nearest'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2, e.disabled ? 1 : 0]); })()`;

(async () => {
  const p = await open(`${base(SM)}/agent`, W, 900);
  await sleep(2500);
  console.log('settings', await p.ev(`fetch('${smUrl(`/scheduler/settings`)}', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({calibrations_folder: ${JSON.stringify(CAL)}})}).then(r => r.status)`));
  const line = '/run 05_power_rabi ' + Array.from({ length: NQ }, (_, i) => 'q' + (i + 1)).join(' ');
  const ta = await xy(p, `(() => { const t = document.querySelector('#agent-home .ag-input'); const b = t.getBoundingClientRect(); return JSON.stringify([b.x + 20, b.y + b.height/2]); })()`);
  await p.click(ta[0], ta[1]);
  await p.send('Input.insertText', { text: line });
  await sleep(300);
  await p.shot(`${OUT}/plan_typed_${W}.png`);
  const t0 = Date.now();
  await p.key('Enter', 'Enter', 13);
  let card = null;
  for (let i = 0; i < 40 && !card; i++) { await sleep(250); card = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card^="plan:"]'); return c ? c.getAttribute('data-card') : null; })()`); }
  console.log('plan card', card, 'after', Date.now() - t0, 'ms; box now', JSON.stringify(await p.ev(`document.querySelector('#agent-home .ag-input').value`)));
  await sleep(800);
  const info = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card^="plan:"]'); if (!c) return null; const r = c.getBoundingClientRect();
    return JSON.stringify({ h: Math.round(r.height), w: Math.round(r.width), text: c.innerText.slice(0, 400), may: c.querySelectorAll('.ag-may li').length,
      overflow: [...c.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > r.right + 1).length }); })()`);
  console.log('card', info);
  await p.shot(`${OUT}/plan_card_${W}.png`);
  // Start with a real mouse
  const st = await xy(p, byText('#agent-home button', '/^\\s*Start/'));
  console.log('start button', JSON.stringify(st));
  if (st) { await p.click(st[0], st[1]); await sleep(3500); }
  console.log('after start', await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card^="plan:"]'); return c ? c.innerText.replace(/\\s+/g, ' ').slice(0, 300) : null; })()`));
  console.log('toast', await p.ev(`(() => { const t = document.getElementById('ag-toast'); return t && !t.hidden ? t.textContent : null; })()`));
  await p.shot(`${OUT}/plan_started_${W}.png`);
  await sleep(4000);
  console.log('+4s', await p.ev(`(() => { const c = [...document.querySelectorAll('#agent-home .ag-card')].map(c => c.getAttribute('data-card') + ': ' + c.innerText.replace(/\\s+/g, ' ').slice(0, 160)); return c.join(' || '); })()`));
  await p.shot(`${OUT}/plan_later_${W}.png`);
  await p.ev('location.reload()'); await sleep(3500);
  console.log('reload', await p.ev(`(() => { const c = [...document.querySelectorAll('#agent-home .ag-card')].map(c => c.getAttribute('data-card') + ': ' + c.innerText.replace(/\\s+/g, ' ').slice(0, 160)); return c.join(' || '); })()`));
  await p.shot(`${OUT}/plan_reload_${W}.png`);
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
