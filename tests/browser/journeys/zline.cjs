/* Z-line distortion journeys in real Chrome.
 *
 *   node tests/browser/journeys/zline.cjs [port] [shotDir]
 * Needs Chrome with --remote-debugging-port (SM_CDP_PORT) and SM on <port>
 * with a chip loaded whose qubits have z lines. Exit 1 on any FAIL.
 * Every scenario ends where a person ends: back/reload, page still intact.
 */
'use strict';
const { open } = require('./cdp.cjs');
const PORT = process.argv[2] || '5111';
const SHOTS = process.argv[3] || '.';
const BASE = `http://127.0.0.1:${PORT}`;
const res = [];
function rec(name, ok, detail) { res.push({ name, ok }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 300) : '')); }

async function center(p, sel) {
  return JSON.parse(await p.ev(`(function(){ var b=Array.from(document.querySelectorAll(${JSON.stringify(sel)})).filter(function(x){var r=x.getBoundingClientRect();return r.width>0&&r.height>0;})[0]; if(!b) return 'null'; b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+Math.min(r.width/2,60)),y:Math.round(r.top+r.height/2)}); })()`));
}
async function waitRendered(p, want, ms = 12000) {
  for (let t = 0; t < ms; t += 250) {
    const r = await p.ev(`(function(){var z=document.getElementById('zline-root'); return z ? (z.getAttribute('data-rendered')||'') : '';})()`);
    if (r && (!want || r.indexOf(want) === 0)) return r;
    await p.sleep(250);
  }
  return null;
}
const plotted = p => p.ev(`(function(){ var s=document.getElementById('zline-step'), q=document.getElementById('zline-pulse');
  function n(g){ return g && g._fullLayout && g.data ? g.data.length : 0; }
  return JSON.stringify({step:n(s), pulse:n(q), stepEmpty:!!(s&&s.querySelector('.zline-empty')), pulseEmpty:!!(q&&q.querySelector('.zline-empty')),
    xlog: s && s._fullLayout ? s._fullLayout.xaxis.type : null}); })()`).then(JSON.parse);

(async () => {
  // ---- Z1: sidebar -> page -> a line -> op -> model -> back -> reload
  {
    const p = await open(`${BASE}/pulses`); await p.sleep(2500);
    const mark = p.events.length;
    const link = await center(p, '#live-edit-subnav a[href="/zline"]');
    rec('Z1a sidebar shows Z-line distortion under Live State Edit', !!link);
    if (link) { await p.click(link.x, link.y); }
    const r0 = await waitRendered(p);
    rec('Z1b clicking it renders the first line', !!r0, r0);
    const pl = await plotted(p);
    rec('Z1c both figures drawn, step on a log axis', pl.step >= 2 && pl.pulse === 2 && pl.xlog === 'log', pl);
    rec('Z1d the address is /zline', (await p.ev('location.pathname')) === '/zline');
    await p.shot(`${SHOTS}/z1_first_line.png`);

    const row = await center(p, '.zline-row[data-line="qubits.q3.z"]');
    if (row) { await p.click(row.x, row.y); }
    const r3 = await waitRendered(p, 'qubits.q3.z|');
    rec('Z1e clicking q3 redraws for q3', !!r3, r3);
    rec('Z1f the row is marked selected + URL carries it',
      (await p.ev(`document.querySelector('.zline-row-selected').getAttribute('data-line')`)) === 'qubits.q3.z'
      && /line=qubits.q3.z/.test(await p.ev('location.search')));
    // pick another pulse
    const ops = JSON.parse(await p.ev(`JSON.stringify(Array.from(document.querySelectorAll('#zline-op option')).map(function(o){return o.value;}))`));
    const other = ops.find(o => /cz_unipolar|cz_flattop_flux|flux/.test(o) && o !== 'const') || ops[1];
    if (other) {
      await p.ev(`(function(){var s=document.getElementById('zline-op'); s.value=${JSON.stringify(other)}; s.dispatchEvent(new Event('change'));})()`);
      const ro = await waitRendered(p, 'qubits.q3.z|' + other + '|sum');
      rec('Z1g choosing another flux pulse redraws it', !!ro, ro);
    }
    await p.ev(`(function(){var s=document.getElementById('zline-model'); s.value='cascade'; s.dispatchEvent(new Event('change'));})()`);
    const rc = await waitRendered(p, 'qubits.q3.z|' + (other || ops[0]) + '|cascade');
    rec('Z1h the QOP<=3.4 model redraws', !!rc, rc);
    await p.shot(`${SHOTS}/z1_q3_other_op_cascade.png`);

    // back -> Pulses intact
    await p.ev('history.back()'); await p.sleep(3000);
    const back = await p.ev(`JSON.stringify({path:location.pathname, pulses:!!document.querySelector('#table-pane table'), zl:!!document.getElementById('zline-root')})`);
    const b = JSON.parse(back);
    rec('Z1i Back returns to Pulses, intact', b.path === '/pulses' && b.pulses && !b.zl, b);
    // forward, then reload onto q3
    await p.ev(`location.href='${BASE}/zline?line=qubits.q3.z'`); await p.sleep(1500);
    const rr = await waitRendered(p, 'qubits.q3.z');
    rec('Z1j a reload of /zline?line=q3 opens q3', !!rr, rr);
    const pl2 = await plotted(p);
    rec('Z1k after reload both figures drawn', pl2.step >= 2 && pl2.pulse === 2, pl2);
    const errs = p.errors(mark);
    rec('Z1l no console errors', errs.length === 0, errs);
    await p.close();
  }
  // ---- Z2: fast clicks -- the last one wins (stale-response guard)
  {
    const p = await open(`${BASE}/zline`); await waitRendered(p);
    const lines = JSON.parse(await p.ev(`JSON.stringify(Array.from(document.querySelectorAll('.zline-row')).map(function(r){return r.getAttribute('data-line');}))`));
    for (const l of lines) { const c = await center(p, `.zline-row[data-line="${l}"]`); if (c) await p.click(c.x, c.y); }
    const last = lines[lines.length - 1];
    const r = await waitRendered(p, last);
    await p.sleep(1500);
    const now = await p.ev(`document.getElementById('zline-root').getAttribute('data-rendered')`);
    rec('Z2 rapid clicks through every line end on the last one', !!r && now.indexOf(last) === 0, { last, now });
    await p.close();
  }
  const bad = res.filter(r => !r.ok);
  console.log(`\n${res.length - bad.length}/${res.length} passed`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
