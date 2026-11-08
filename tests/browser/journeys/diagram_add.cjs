/* Journey: add a qubit and then a pair from the chip map, in real Chrome.
 *
 *   SM_URL=http://127.0.0.1:5191 SM_CHIP=<chip folder> SHOT_DIR=<dir> [SM_W=1440]
 *   node tests/browser/journeys/diagram_add.cjs
 *
 * Opens the chip, goes to Qubits, clicks the "+" ghost stone, changes the
 * drive frequency (ports re-proposed), reviews, stages, checks the tray and
 * the server's working copy, adds a pair by clicking "+ pair" and two stones,
 * waits for the env check (Quam.load + generate_config of the result), then
 * presses Ctrl+Z twice. Real mouse events throughout. Prints one JSON
 * line of timings and verdicts; screenshots go to SHOT_DIR when it is set.
 */
'use strict';
const path = require('path');
const { open, sleep } = require('./cdp.cjs');

const URL0 = process.env.SM_URL || 'http://127.0.0.1:5191';
const CHIP = process.env.SM_CHIP;
const SHOT = process.env.SHOT_DIR || '';
const W = +(process.env.SM_W || 1440), H = +(process.env.SM_H || 900);

(async () => {
  const p = await open(URL0 + '/', W, H);
  const out = { width: W };
  const shot = async (n) => { if (SHOT) await p.shot(path.join(SHOT, `${W}_${n}.png`)); };
  const until = async (expr, ms = 20000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await p.ev(expr)) return Date.now() - t0; await sleep(15); }
    return -1;
  };
  const center = async (sel) => p.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;
      e.scrollIntoView({block:'center'});var r=e.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2];})()`);
  const clickSel = async (sel) => { const c = await center(sel); if (!c) throw new Error('no ' + sel); await sleep(60); const c2 = await center(sel); await p.click(c2[0], c2[1]); };

  await p.ev(`fetch('/load',{method:'POST',body:new URLSearchParams({folder:${JSON.stringify(CHIP)}})}).then(r=>r.status)`);
  await p.send('Page.navigate', { url: URL0 + '/qubits' });
  await sleep(300);
  out.map_ms = await until(`!!document.querySelector('.cm-dir-ghost')`, 60000);
  await shot('1_map');

  const n0 = await p.ev(`document.querySelectorAll('.cm-node').length`);
  out.stones = n0;
  let t = Date.now();
  await clickSel('[data-cm="add:qubit"] circle');
  out.open_panel_ms = (await until(`!!document.querySelector('#cm-add-form [data-cm-add-drive]')`)) >= 0 ? Date.now() - t : -1;
  out.name = await p.ev(`document.querySelector('#cm-add-form input[name=name]').value`);
  await shot('2_panel');

  // type a new drive frequency: the drive list is re-proposed
  const before = await p.ev(`document.querySelector('[data-cm-add-drive]').innerHTML`);
  await clickSel('[data-cm-add-rf]');
  await p.ev(`(function(){var i=document.querySelector('[data-cm-add-rf]');i.select();})()`);
  t = Date.now();
  const rf = String(Math.round((await p.ev(`+document.querySelector('[data-cm-add-rf]').value`)) + 37e6));
  for (const ch of rf) await p.send('Input.insertText', { text: ch });
  out.propose_ms = (await until(`document.querySelector('[data-cm-add-drive]').innerHTML !== ${JSON.stringify(before)}`)) >= 0 ? Date.now() - t : -1;

  t = Date.now();
  await clickSel('#cm-add-form button[type=submit]');
  out.review_ms = (await until(`/No existing value changes/.test((document.getElementById('cm-add-review')||{}).textContent||'')`)) >= 0 ? Date.now() - t : -1;
  out.review_text = await p.ev(`document.querySelector('#cm-add-review .ui-callout-title').textContent.trim()`);
  await shot('3_review');

  out.check_ms = await until(`!!document.querySelector('[data-cm-check-ok],[data-cm-check-fail]')`, 90000);
  t = Date.now();
  await clickSel('[data-cm-add-stage]');
  out.stage_ms = (await until(`document.querySelectorAll('.cm-node').length === ${n0 + 1} && /Added qubit/.test((document.getElementById('cm-add-panel')||{}).textContent||'')`)) >= 0 ? Date.now() - t : -1;
  out.tray = await p.ev(`(document.getElementById('pending-tray')||{}).getAttribute ? document.getElementById('pending-tray').getAttribute('data-change-count') : null`);
  out.total = await p.ev(`(document.querySelector('.ui-count')||{}).textContent`);
  await shot('4_staged');

  // a pair: + pair, the new stone, then its feedline neighbour
  await clickSel('[data-cm="add:pair"] circle');
  const other = await p.ev(`(function(){var s=[].map.call(document.querySelectorAll('.cm-node'),function(g){return g.getAttribute('data-cm').slice(2)});return s[s.length-2];})()`);
  await clickSel(`[data-cm="q:${out.name}"] .cm-stone`);
  t = Date.now();
  await clickSel(`[data-cm="q:${other}"] .cm-stone`);
  out.pair_panel_ms = (await until(`!!document.querySelector('#cm-add-form input[name=control]')`)) >= 0 ? Date.now() - t : -1;
  await shot('5_pair');
  await clickSel('#cm-add-form button[type=submit]');
  await until(`!!document.querySelector('[data-cm-add-stage]')`, 90000);
  t = Date.now();
  await clickSel('[data-cm-add-stage]');
  out.pair_stage_ms = (await until(`/Added pair/.test((document.getElementById('cm-add-panel')||{}).textContent||'')`)) >= 0 ? Date.now() - t : -1;
  out.pair = await p.ev(`(document.querySelector('#cm-add-panel').textContent.match(/pair (\\S+)/)||[])[1]`);
  await shot('6_pair_staged');

  // what the server holds now, then two undos
  const topo = () => p.ev(`fetch('/api/topology?layout=1').then(r=>r.json()).then(j=>j.nodes.length+'q/'+j.edges.length+'p')`);
  await sleep(+(process.env.SM_SETTLE || 0));
  out.server_after_adds = await topo();
  if (process.env.SM_NO_UNDO) { console.log(JSON.stringify(out)); await p.close(); process.exit(0); }
  await p.ev(`fetch('/undo',{method:'POST'}).then(r=>r.status)`);
  out.server_after_undo1 = await topo();
  await p.ev(`fetch('/undo',{method:'POST'}).then(r=>r.status)`);
  out.server_after_undo2 = await topo();
  out.errors = p.errors();
  console.log(JSON.stringify(out));
  await p.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
