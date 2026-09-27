/* Pulses page speed on a big chip (docs/217 pulse locations: the shape
 * discovery walk must not make the page slower in a way the user feels).
 * Measures, in real Chrome: first open (cold index build), reload (warm),
 * a search keystroke, the "All" tab after a field commit (the index is
 * rebuilt after every mutation), and a detail open. Prints JSON.
 *
 *   SM_CDP_PORT=9414 SM_PORT=5114 node pulses_big_timing.cjs [ROUNDS]
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5114;
const BASE = `http://127.0.0.1:${PORT}`;
const ROUNDS = +(process.argv[2] || 3);

async function waitFor(p, expr, ms = 120000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(25);
  }
  return null;
}
const ROWS = `document.querySelectorAll('tr[data-pulse-path]').length`;

(async () => {
  const out = { first_open: [], reload: [], keystroke: [], after_commit: [], detail: [] };
  const p = await open(`${BASE}/`);
  for (let r = 0; r < ROUNDS; r++) {
    // a mutation makes the next list build cold: commit a field value
    const first = await p.ev(`fetch('/pulses?rows=1&per_page=1').then(r=>r.text()).then(h=>(h.match(/data-pulse-path="([^"]+)"/)||[])[1]||'')`);
    let t0 = Date.now();
    await p.send('Page.navigate', { url: `${BASE}/pulses` });
    await waitFor(p, `document.readyState==='complete' && ${ROWS} > 0 ? 1 : 0`);
    out.first_open.push(Date.now() - t0);
    t0 = Date.now();
    await p.send('Page.reload');
    await sleep(50);
    await waitFor(p, `document.readyState==='complete' && ${ROWS} > 0 ? 1 : 0`);
    out.reload.push(Date.now() - t0);
    // one keystroke into the search box (server-side filter)
    t0 = Date.now();
    // a real keystroke: the box may hold a persisted query, so the typed text
    // alternates to always be a CHANGE (htmx `keyup changed`)
    const word = r % 2 ? 'x90' : 'x180';
    // (the box's own `search` trigger: a CDP keyUp did not reach htmx's
    // `keyup changed` listener in headless Chrome here)
    await p.ev(`(function(){var q=[...document.querySelectorAll('input[name=q]')].find(e=>(e.getAttribute('hx-get')||'').startsWith('/pulses')); q.value='${word}'; htmx.trigger(q,'search'); return 1})()`);
    await waitFor(p, `(function(){var t=document.querySelector('tr[data-pulse-path]'); return t && t.dataset.pulsePath.indexOf('${word}') >= 0 ? 1 : 0})()`, 30000);
    out.keystroke.push(Date.now() - t0);
    // a field commit, then the whole list rebuilt (cold index)
    if (first) {
      const v = (0.01 + r * 0.001).toFixed(4);
      await p.ev(`(function(){var fd=new FormData(); fd.append('path', ${JSON.stringify(first)}); fd.append('dot_path', ${JSON.stringify(first)} + '.amplitude'); fd.append('mode','value'); fd.append('value','${v}'); return fetch('/pulse/edit',{method:'POST',body:fd,headers:{'HX-Request':'true'}}).then(r=>r.status)})()`);
      t0 = Date.now();
      await p.ev(`fetch('/pulses?rows=1&per_page=50').then(r=>r.text()).then(h=>h.length)`);
      out.after_commit.push(Date.now() - t0);
    }
    t0 = Date.now();
    await p.ev(`htmx.ajax('GET','/pulse/detail?path='+encodeURIComponent(${JSON.stringify(first)}),{target:'#inspector-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.getElementById('pulse-detail-root') ? 1 : 0`, 30000);
    out.detail.push(Date.now() - t0);
  }
  const med = a => a.slice().sort((x, y) => x - y)[Math.floor(a.length / 2)];
  const res = {};
  for (const k of Object.keys(out)) res[k] = { runs: out[k], median: med(out[k]) };
  res.rows_total = await p.ev(`fetch('/pulses?rows=1&per_page=1').then(r=>r.text()).then(h=>(h.match(/\\((\\d+) total\\)/)||[])[1]||'?')`);
  console.log(JSON.stringify(res));
  await p.close();
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
