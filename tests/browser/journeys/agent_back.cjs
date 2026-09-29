/* Agent home journey: open -> leave via the sidebar -> Back -> Forward -> reload.
 * After every step the home must be LIVE: mounted in AgentPanel's mount list
 * (so its feed polls) and its composer must send.
 *   SM_CDP_PORT=9413 node agent_back.cjs 5113 OUTDIR
 */
'use strict';
const { open, sleep, base, baseFrom, smPath, smUrl } = require('./cdp.cjs');
const SM = process.argv[2] || '5113';
const OUT = process.argv[3] || '.';

const PROBE = `(() => {
  const home = document.getElementById('agent-home');
  const S = window.AgentPanel && AgentPanel._state;
  const live = !!(home && S && S.mounts.some(m => home.contains(m.root)));
  return JSON.stringify({ path: location.pathname, home: !!home, mounts: S ? S.mounts.length : -1, live,
    roots: document.querySelectorAll('#table-pane .ag-root').length,
    cards: home ? home.querySelectorAll('.ag-card').length : 0 });
})()`;

async function clickSel(p, sel) {
  const r = JSON.parse(await p.ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'null';
    e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`));
  if (!r) throw new Error('no ' + sel);
  await p.click(r[0], r[1]);
}

(async () => {
  const p = await open(`${base(SM)}/agent`, 1600, 900);
  await sleep(2000);
  const log = (tag, v) => console.log(tag.padEnd(12), v);
  log('open', await p.ev(PROBE));
  await clickSel(p, '#table-pane a[href="' + smUrl('/journal') + '"]');
  await sleep(2000);
  log('->journal', await p.ev(PROBE));
  await p.ev('history.back()'); await sleep(2500);
  log('back', await p.ev(PROBE));
  await p.shot(`${OUT}/back_agent.png`);
  await p.ev('history.forward()'); await sleep(2500);
  log('forward', await p.ev(PROBE));
  await p.ev('history.back()'); await sleep(2500);
  log('back2', await p.ev(PROBE));
  // does the composer still send from here? count card-poll requests for 3 s after a wake
  await p.ev(`(() => { window.__polls = 0; const f = window.fetch; window.fetch = function (u) { if (String(u).includes('/chat/cards')) window.__polls++; return f.apply(this, arguments); }; })()`);
  await p.ev(`document.dispatchEvent(new CustomEvent('sm:agent-changed', {detail: {agent_seq: -99}}))`);
  await sleep(1500);
  log('wake polls', await p.ev('window.__polls'));
  // fire-and-forget: a Runtime.evaluate of location.reload() can outlive its own
  // execution context and never be answered (hung 900 s behind nginx, docs/226)
  await p.ev('setTimeout(function () { location.reload(); }, 0); 1'); await sleep(3000);
  log('reload', await p.ev(PROBE));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
