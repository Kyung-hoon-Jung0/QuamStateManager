/* Journey (w9/pulsegate): the pair page's "+ Add gate" on a flux pair. A flux
 * CZ gate brings its flux pulses with it, and pulses are added on the Pulses
 * page -- the form says so and links there (it used to write the pulses INLINE
 * in the gate, the layout the Pulses page retired on 2026-09-27).
 *   open the pair -> + Add gate -> the note + link -> the link -> the Pulses
 *   page on that pair's flux rows -> Back -> the pair page whole -> reload
 *
 *   SM_CDP_PORT=<cdp> PORT=<sm> SHOT_DIR=<dir> [PAIR=q1-2] node pair_add_gate_pulses.cjs
 */
'use strict';
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const PORT = +(process.env.PORT || 5365);
const BASE = `http://127.0.0.1:${PORT}`;
const DIR = process.env.SHOT_DIR || '.';
const PAIR = process.env.PAIR || 'q1-2';
fs.mkdirSync(DIR, { recursive: true });
const J = JSON.stringify;
const out = []; let bad = 0;
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; return c; }
async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(120);
  }
  return null;
}
async function clickSel(p, sel) {
  const r = await p.ev(`(function(){var e=document.querySelector(${J(sel)}); if(!e||e.offsetParent===null) return null; e.scrollIntoView({block:'center'}); var b=(e.getClientRects()[0])||e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2]})()`);
  if (!r) return false;
  await p.click(r[0], r[1]);
  return true;
}

(async () => {
  const p = await open(`${BASE}/pair/${PAIR}`);
  await waitFor(p, `document.querySelector('.add-gate-btn')?1:0`, 90000);
  const mark = p.events.length;
  check(await clickSel(p, '.add-gate-btn'), 'the pair page has + Add gate');
  const note = await waitFor(p, `(function(){var n=document.querySelector('.add-gate-pulses-why'); return n? n.innerText : 0})()`, 30000);
  check(!!note && /pulses are added on the Pulses page/.test(note) && /Gaussian CZ/.test(note),
    `the form says where a flux gate is built: "${(note || '').replace(/\s+/g, ' ').slice(0, 160)}"`);
  const offered = await p.ev(`[].slice.call(document.querySelectorAll('#add-gate-type option')).map(function(o){return o.value})`);
  check(Array.isArray(offered) ? offered.every(v => !/^cz_/.test(v)) : true,
    `no flux gate type is offered (${J(offered)})`);
  await p.shot(`${DIR}/P1_add_gate_note.png`);
  check(await clickSel(p, '.add-gate-pulses-link'), 'the link is there');
  const landed = await waitFor(p, `(location.pathname==='/pulses' && document.querySelectorAll('tr[data-pulse-path]').length)?location.search:0`, 90000);
  const q = new URLSearchParams(landed || '');
  check(q.get('owner') === PAIR && q.get('channel') === 'flux', `it lands on ${PAIR}'s flux rows (${landed})`);
  await p.shot(`${DIR}/P2_pulses_flux_rows.png`);
  await p.ev('history.back()');
  const back = await waitFor(p, `(location.pathname.indexOf('/pair')===0 && document.querySelector('.add-gate-btn, .add-gate-pulses-note'))?1:0`, 60000);
  check(!!back, 'Back returns to the pair page, whole');
  const evMark = p.events.length;
  await p.send('Page.reload', {});
  await sleep(1200);
  // edits a rig carries from earlier journeys arm the unsaved-edits guard:
  // answer it like a user who means to reload
  if (p.events.slice(evMark).some(e => e.method === 'Page.javascriptDialogOpening')) {
    await p.send('Page.handleJavaScriptDialog', { accept: true });
  }
  const again = await waitFor(p, `document.readyState==='complete' && document.querySelector('.add-gate-btn')?1:0`, 90000);
  check(!!again, 'after reload the pair page is whole');
  const errs = p.errors(mark).filter(m => !/status of 40[09]/.test(m));
  check(errs.length === 0, 'no page errors: ' + J(errs.slice(0, 5)));
  fs.writeFileSync(`${DIR}/result.json`, J({ bad, out }, null, 1));
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  await p.close();
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
