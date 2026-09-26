/* Verifier P3 (w7/agentsqa): another window opens a different chip while the
 * Agent home is open. The open page must follow within seconds (heading, cards,
 * strip) and match a cold reload; switching back must follow too.
 *   SM_CDP_PORT=9413 node agent_chip_switch.cjs 5113 OUTDIR FOLDER_OTHER FOLDER_BACK
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, OTHER, BACK] = process.argv.slice(2);
const view = `JSON.stringify({ head: ((document.querySelector('#agent-home .ag-chip') || {}).textContent || '') + ' ' + ((document.querySelector('#agent-home .ag-qubits') || {}).textContent || ''),
  cards: [...document.querySelectorAll('#agent-home [data-card]')].map(c => c.getAttribute('data-card')).sort(),
  writeBtns: [...document.querySelectorAll('#agent-home .ag-approve')].filter(b => !b.disabled).length,
  strip: ((document.querySelector('#agent-home .ag-now-main') || {}).textContent || '').replace(/\\s+/g, ' ').slice(0, 90) })`;
const load = async (p, folder) => p.ev(`(function(){ var fd = new FormData(); fd.append('folder', ${JSON.stringify(folder)});
  return fetch('/load', { method: 'POST', body: fd, headers: { 'HX-Request': 'true' } }).then(function (r) { return r.status; }); })()`);
const norm = (v) => { const o = JSON.parse(v); delete o.strip; return JSON.stringify(o); };

(async () => {
  const a = await open(`http://127.0.0.1:${SM}/agent`, 1366, 900); await sleep(4000);
  console.log('A before     ', await a.ev(view));
  for (const [tag, folder] of [['switch', OTHER], ['back', BACK]]) {
    const b = await open(`http://127.0.0.1:${SM}/`, 1366, 900); await sleep(1500);
    const t0 = Date.now();
    console.log(`load ${tag}`, await load(b, folder));
    await b.close();
    let seen = null, v = null;
    for (let i = 0; i < 40; i++) {                 // up to 20 s, sampled every 0.5 s
      await sleep(500);
      v = await a.ev(view);
      const c0 = JSON.parse(v);
      if (tag === 'switch' ? !/KRS_QA_30Q/.test(c0.head) : /KRS_QA_30Q/.test(c0.head)) { seen = Date.now() - t0; break; }
    }
    await sleep(1500);
    v = await a.ev(view);
    console.log(`A after ${tag}`.padEnd(13), `followed in ${seen === null ? 'NEVER (20 s)' : seen + ' ms'}`, v);
    await a.shot(`${OUT}/switch_${tag}_open.png`);
    const c = await open(`http://127.0.0.1:${SM}/agent`, 1366, 900); await sleep(4000);
    const cv = await c.ev(view);
    console.log(`cold ${tag}`.padEnd(13), cv);
    console.log(`open == cold ${tag}:`, norm(v) === norm(cv));
    const e = c.errors(); if (e.length) console.log('ERRORS cold', e.join(' ## '));
    await c.close();
  }
  await a.send('Page.reload'); await sleep(3500);
  console.log('A +reload    ', await a.ev(view));
  await a.shot(`${OUT}/switch_end.png`);
  const e = a.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await a.close(); process.exit(0);
})();
