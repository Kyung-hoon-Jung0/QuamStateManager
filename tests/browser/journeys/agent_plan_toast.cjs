/* Verifier P4 (w7/agentsqa): a /run line makes a plan card at the bottom of the
 * feed, right above the composer; a "plan card ready" toast in the lifted sink
 * covered that card's Start button. Type the line, send with a real Enter, then
 * measure what sits on top of Start. Cancels the draft (never Starts), reloads.
 *   SM_CDP_PORT=9413 node agent_plan_toast.cjs 5113 OUTDIR WIDTH NQ
 */
'use strict';
const { open, sleep, base, baseFrom, smPath, smUrl } = require('./cdp.cjs');
const [SM, OUT, W, NQ] = [process.argv[2], process.argv[3], +process.argv[4] || 1366, +process.argv[5] || 30];

(async () => {
  const p = await open(`${base(SM)}/agent`, W, 900);
  await sleep(3000);
  const line = '/run 05_power_rabi ' + Array.from({ length: NQ }, (_, i) => 'q' + (i + 1)).join(' ');
  const ta = JSON.parse(await p.ev(`(() => { const t = document.querySelector('#agent-home .ag-input'); const b = t.getBoundingClientRect(); return JSON.stringify([b.x + 20, b.y + b.height/2]); })()`));
  await p.click(ta[0], ta[1]);
  await p.send('Input.insertText', { text: line }); await sleep(300);
  const before = new Set(JSON.parse(await p.ev(`JSON.stringify([...document.querySelectorAll('#agent-home [data-card^="plan:"]')].map(c => c.getAttribute('data-card')))`)));
  await p.key('Enter', 'Enter', 13);
  let card = null;
  for (let i = 0; i < 40 && !card; i++) {
    await sleep(250);
    const all = JSON.parse(await p.ev(`JSON.stringify([...document.querySelectorAll('#agent-home [data-card^="plan:"]')].map(c => c.getAttribute('data-card')))`));
    card = all.find(k => !before.has(k)) || null;
  }
  await sleep(700);
  const m = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card=${JSON.stringify(card || 'none')}]'); if (!c) return 'no card';
    const s = [...c.querySelectorAll('button')].find(b => /^\\s*Start/.test(b.textContent)); if (!s) return 'no start';
    const r = s.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + 3);
    const toasts = [...document.querySelectorAll('#status-bar > *, #ag-toast:not([hidden])')].filter(t => t.getBoundingClientRect().height > 0)
      .map(t => { const b = t.getBoundingClientRect(); return { text: t.textContent.trim().slice(0, 60), y: [Math.round(b.top), Math.round(b.bottom)] }; });
    return JSON.stringify({ card: ${JSON.stringify(card)}, start: [Math.round(r.top), Math.round(r.bottom)], topAtStart: top === s || s.contains(top) ? 'Start' : (top && (top.id || top.className)), toasts }); })()`);
  console.log('measure', m);
  await p.shot(`${OUT}/plan_toast_${W}.png`);
  // cancel the draft with a real mouse (never Start: that spawns the agent)
  const cx = await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card=${JSON.stringify(card || 'none')}]'); const b = c && [...c.querySelectorAll('button')].find(b => /Cancel|Discard/.test(b.textContent));
    if (!b) return 'null'; b.scrollIntoView({ block: 'nearest' }); const r = b.getBoundingClientRect(); return JSON.stringify([r.x + r.width / 2, r.y + r.height / 2]); })()`);
  if (cx !== 'null') { const xy = JSON.parse(cx); await p.click(xy[0], xy[1]); await sleep(1500); }
  console.log('after cancel', await p.ev(`(() => { const c = document.querySelector('#agent-home [data-card=${JSON.stringify(card || 'none')}]'); return c ? c.innerText.replace(/\\s+/g, ' ').slice(0, 120) : 'gone'; })()`));
  await p.send('Page.reload'); await sleep(3500);
  console.log('reload', await p.ev(`JSON.stringify({ path: location.pathname, roots: document.querySelectorAll('#agent-home .ag-root').length, composer: !!document.querySelector('#agent-home .ag-composer') })`));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
