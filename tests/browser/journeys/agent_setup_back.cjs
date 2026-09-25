/* Agent setup journey: open -> leave -> Back -> press a control -> does the VISIBLE page answer?
 *   SM_CDP_PORT=9413 node agent_setup_back.cjs 5113 OUTDIR
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const SM = process.argv[2] || '5113';
const OUT = process.argv[3] || '.';

async function clickSel(p, sel) {
  const r = JSON.parse(await p.ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'null';
    e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`));
  if (!r) throw new Error('no ' + sel);
  await p.click(r[0], r[1]);
}
const PROBE = `(() => { const S = window.AgentSetup && AgentSetup._state; const vis = document.getElementById('as-body');
  return JSON.stringify({ path: location.pathname, rootLive: !!(S && S.root && document.body.contains(S.root)),
    same: !!(S && vis && (S.root === vis || vis.contains(S.root) || S.root.contains(vis))),
    prev: document.querySelectorAll('#table-pane .as-diff, #table-pane pre.as-diff, #table-pane [class*=diff]').length }); })()`;

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent/setup`, 1600, 900);
  await sleep(2500);
  console.log('open   ', await p.ev(PROBE));
  await clickSel(p, '#nav-agent'); await sleep(2000);
  await p.ev('history.back()'); await sleep(2500);
  console.log('back   ', await p.ev(PROBE));
  const before = await p.ev(`document.querySelector('#table-pane').innerText.length`);
  // press the first "Preview what SM would write"
  const bx = await p.ev(`(() => { const b = [...document.querySelectorAll('#table-pane button')].find(b => /Preview what SM/.test(b.textContent)); if (!b) return null; b.scrollIntoView({block:'center'}); const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width/2, r.y + r.height/2]); })()`);
  const xy = JSON.parse(bx); await p.click(xy[0], xy[1]); await sleep(2500);
  const after = await p.ev(`document.querySelector('#table-pane').innerText.length`);
  console.log('preview pressed after Back: visible text', before, '->', after);
  await p.shot(`${OUT}/setup_back_preview.png`);
  // a WRITE press: "Use this folder" -> the server records the journal root -> load() re-renders.
  const sum = `(() => { const s = [...document.querySelectorAll('#table-pane summary')].find(s => /Journal folder/.test(s.textContent)); return s ? s.textContent.trim().slice(0, 30) : null; })()`;
  console.log('journal section before:', await p.ev(sum));
  const ub = JSON.parse(await p.ev(`(() => { const b = [...document.querySelectorAll('#table-pane button')].find(b => /Use this folder/.test(b.textContent)); b.scrollIntoView({block:'center'}); const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width/2, r.y + r.height/2]); })()`));
  await p.click(ub[0], ub[1]); await sleep(2500);
  console.log('journal section after :', await p.ev(sum));
  await p.shot(`${OUT}/setup_back_usefolder.png`);
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
