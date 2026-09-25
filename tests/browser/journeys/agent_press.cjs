/* Agent home: press one control by its visible text with a real mouse, then
 * report the strip, the cards and any dialog, and reload (a beforeunload
 * dialog is ACCEPTED and logged, never silently hung on).
 *   SM_CDP_PORT=9413 node agent_press.cjs 5113 OUTDIR WIDTH "Stop now" [tag]
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W, LABEL, TAG] = [process.argv[2], process.argv[3], +process.argv[4], process.argv[5], process.argv[6] || 'press'];

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent`, W, 900);
  const dialogs = [];
  const iv = setInterval(async () => {
    for (const e of p.events.splice(0)) {
      if (e.method === 'Page.javascriptDialogOpening') { dialogs.push(e.params.type + ': ' + e.params.message); await p.send('Page.handleJavaScriptDialog', { accept: true }); }
      else p.events.push(e);
    }
  }, 100);
  await sleep(2500);
  const strip = `(() => { const n = document.querySelector('#agent-home .ag-now'); return n ? n.innerText.replace(/\\s+/g, ' ') : null; })()`;
  const cards = `(() => [...document.querySelectorAll('#agent-home .ag-card')].slice(-4).map(c => c.getAttribute('data-card') + ': ' + c.innerText.replace(/\\s+/g, ' ').slice(0, 140)).join(' || '))()`;
  console.log('strip  ', await p.ev(strip));
  console.log('cards  ', await p.ev(cards));
  const at = await p.ev(`(() => { const re = new RegExp('^\\\\s*' + ${JSON.stringify(LABEL)}.replace(/[.*+?^\${}()|[\\]\\\\]/g, '\\\\$&'));
    const e = [...document.querySelectorAll('#agent-home button, #agent-home a, #agent-home summary')].reverse().find(e => re.test(e.textContent) && e.getBoundingClientRect().width > 0);
    if (!e) return null; e.scrollIntoView({block:'nearest'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`);
  console.log('press  ', LABEL, at);
  if (at) { const xy = JSON.parse(at); await p.click(xy[0], xy[1]); }
  await sleep(3000);
  console.log('toast  ', await p.ev(`(() => { const t = document.getElementById('ag-toast'); return t && !t.hidden ? t.textContent : null; })()`));
  console.log('strip  ', await p.ev(strip));
  console.log('cards  ', await p.ev(cards));
  await p.shot(`${OUT}/${TAG}_${W}.png`);
  await p.send('Page.reload'); await sleep(3500);
  console.log('reload ', await p.ev(strip));
  console.log('dialogs', JSON.stringify(dialogs));
  await p.shot(`${OUT}/${TAG}_reload_${W}.png`);
  clearInterval(iv);
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
