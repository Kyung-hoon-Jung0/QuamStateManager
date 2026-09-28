/* Z-line distortion journeys in real Chrome.
 *
 *   node tests/browser/journeys/zline.cjs [port] [shotDir]
 * Needs Chrome with --remote-debugging-port (SM_CDP_PORT) and SM on <port>
 * with a chip loaded whose qubits have z lines. Exit 1 on any FAIL.
 * Every scenario ends where a person ends: back/reload, page still intact.
 */
'use strict';
const cdp = require('./cdp.cjs');
// The chip may carry unapplied working-copy edits (Z5 makes some), and SM then
// guards a navigation with a beforeunload prompt: a reload would wait on it
// forever in headless Chrome. Accept it (the edits stay staged server-side)
// and count it, so a prompt is seen, never silently hung on.
const dialogs = [];
async function open(url) {
  const p = await cdp.open(url);
  let seen = 0;
  const t = setInterval(() => {
    for (; seen < p.events.length; seen++) {
      const e = p.events[seen];
      if (e.method === 'Page.javascriptDialogOpening') {
        dialogs.push(e.params.type);
        p.send('Page.handleJavaScriptDialog', { accept: true });
      }
    }
  }, 100);
  const close = p.close;
  p.close = async () => { clearInterval(t); return close(); };
  return p;
}
const PORT = process.argv[2] || '5111';
const SHOTS = process.argv[3] || '.';
const BASE = cdp.base(PORT);
const { smPath, smUrl } = cdp;
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
    const link = await center(p, '#live-edit-subnav a[href="' + smUrl('/zline') + '"]');
    rec('Z1a sidebar shows Z-line distortion under Live State Edit', !!link);
    if (link) { await p.click(link.x, link.y); }
    const r0 = await waitRendered(p);
    rec('Z1b clicking it renders the first line', !!r0, r0);
    const pl = await plotted(p);
    rec('Z1c both figures drawn, step on a log axis', pl.step >= 2 && pl.pulse === 2 && pl.xlog === 'log', pl);
    // the four step curves are SHOWN, not parked in the legend (verifier round 3)
    const vis = JSON.parse(await p.ev(`JSON.stringify((document.getElementById('zline-step').data||[]).map(function(t){return [t.name, t.visible === undefined ? true : t.visible];}))`));
    rec('Z1c2 a line with both filters shows all four step curves by default',
      vis.length === 4 && vis.every(v => v[1] === true), vis);
    rec('Z1d the address is /zline', smPath(await p.ev('location.pathname')) === '/zline');
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
    await p.ev('setTimeout(function(){history.back();},0); 1'); await p.sleep(3000);
    const back = await p.ev(`JSON.stringify({path:location.pathname, pulses:!!document.querySelector('#table-pane table'), zl:!!document.getElementById('zline-root')})`);
    const b = JSON.parse(back);
    rec('Z1i Back returns to Pulses, intact', smPath(b.path) === '/pulses' && b.pulses && !b.zl, b);
    // forward, then reload onto q3
    await p.ev(`setTimeout(function(){location.href='${BASE}/zline?line=qubits.q3.z';},0); 1`); await p.sleep(1500);
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
  // ---- Z3: a failed request (a line the chip no longer has) must not leave
  //      the PREVIOUS line's picture under the new selection
  {
    const p = await open(`${BASE}/zline`); const r0 = await waitRendered(p);
    const prev = (r0 || '').split('|')[0];
    const target = await p.ev(`(function(){ var rows=document.querySelectorAll('.zline-row'); var r=rows[rows.length-1];
      if (r.getAttribute('data-line')===${JSON.stringify(prev)}) r=rows[0]; r.setAttribute('data-line','qubits.q999.z'); return 'ok'; })()`);
    const c = await center(p, '.zline-row[data-line="qubits.q999.z"]');
    if (c) await p.click(c.x, c.y);
    let st = null;
    for (let t = 0; t < 8000; t += 250) {
      st = JSON.parse(await p.ev(`(function(){var z=document.getElementById('zline-root'); return JSON.stringify({failed:z.getAttribute('data-failed'),
        rendered:z.getAttribute('data-rendered'), title:document.getElementById('zline-title').textContent,
        note:(document.querySelector('#zline-notes li')||{}).textContent||'', ops:document.querySelectorAll('#zline-op option').length});})()`));
      if (st.failed) break; await p.sleep(250);
    }
    const pl = await plotted(p);
    rec('Z3a a 404 line names itself in the title, not the previous line',
      !!st && st.failed === 'qubits.q999.z' && /q999/.test(st.title) && (!prev || st.title.indexOf(prev.replace(/^qubits\./, '').replace(/^qubit_pairs\./, '')) < 0), st);
    rec('Z3b both figures cleared, data-rendered dropped, no stale operations',
      pl.step === 0 && pl.pulse === 0 && pl.stepEmpty && pl.pulseEmpty && !st.rendered && st.ops === 0, pl);
    rec('Z3c the note says to reload', /reload/.test(st.note), st.note);
    await p.shot(`${SHOTS}/z3_failed_line_cleared.png`);
    await p.ev('setTimeout(function(){location.reload();},0); 1'); await p.sleep(1500);
    const rr = await waitRendered(p);
    const pl2 = await plotted(p);
    rec('Z3d reload: the page is intact and draws again', !!rr && pl2.step >= 2, { rr, pl2 });
    await p.close();
  }
  // ---- Z4: on a long table, clicking a lower row brings its figures into view
  {
    const p = await open(`${BASE}/zline`); await waitRendered(p);
    const n = await p.ev(`document.querySelectorAll('.zline-row').length`);
    const last = await p.ev(`(function(){var r=document.querySelectorAll('.zline-row'); return r[r.length-1].getAttribute('data-line');})()`);
    const c = await center(p, `.zline-row[data-line="${last}"]`);
    if (c) await p.click(c.x, c.y);
    const r = await waitRendered(p, last + '|');
    const pos = JSON.parse(await p.ev(`(function(){var t=document.getElementById('zline-title').getBoundingClientRect(), s=document.getElementById('zline-step').getBoundingClientRect();
      return JSON.stringify({title:Math.round(t.top), step:Math.round(s.top), h:window.innerHeight});})()`));
    rec('Z4 clicking the last row (' + n + ' rows) brings its figures into view',
      !!r && pos.title >= 0 && pos.title <= pos.h * 0.5 && pos.step < pos.h * 0.8, pos);
    await p.shot(`${SHOTS}/z4_last_row_revealed.png`);
    await p.close();
  }
  // ---- Z5: stability is judged PER MODEL (verifier round 3). Edit q3's
  //      exponentials in the working copy, read the table + both models, restore.
  {
    const p = await open(`${BASE}/zline?line=qubits.q3.z`); await waitRendered(p, 'qubits.q3.z');
    const mark = p.events.length;
    const d0 = JSON.parse(await p.ev(`fetch('${smUrl(`/zline/data?line=qubits.q3.z`)}').then(function(r){return r.text();})`));
    const pp = d0.port_path;
    const orig = d0.port ? d0.port.exponential : null;
    const edit = v => p.ev(`(function(){var f=new FormData(); f.append('dot_path', ${JSON.stringify(pp + '.exponential_filter')}); f.append('value', ${JSON.stringify(JSON.stringify(v))});
      return fetch('${smUrl(`/field/edit`)}',{method:'POST',body:f,headers:{'HX-Request':'true'}}).then(function(r){return r.status;});})()`);
    const cell = () => p.ev(`(function(){var r=document.querySelector('.zline-row[data-line="qubits.q3.z"] .zline-models'); return r ? r.textContent.replace(/ +/g,' ').trim() : '';})()`);
    const noteCodes = () => p.ev(`JSON.stringify(Array.from(document.querySelectorAll('#zline-notes li')).map(function(l){return [l.getAttribute('data-code'), l.className, l.textContent];}))`).then(JSON.parse);
    const setModel = async m => { await p.ev(`(function(){var s=document.getElementById('zline-model'); s.value='${m}'; s.dispatchEvent(new Event('change'));})()`); return waitRendered(p, 'qubits.q3.z|'); };
    rec('Z5a q3 has a plain exponential set to restore', !!pp && Array.isArray(orig) && d0.port.dc_gain === 1, { pp, orig });

    const s1 = await edit([[-0.6, 10.0], [-0.6, 1000.0]]);
    await p.ev('setTimeout(function(){location.reload();},0); 1'); await p.sleep(1200); await waitRendered(p, 'qubits.q3.z|');
    const c1 = await cell();
    const n1 = await noteCodes(); const pl1 = await plotted(p);
    rec('Z5b sum-unstable / cascade-stable: the row says UNSTABLE / ok', s1 === 200 && c1 === 'UNSTABLE / ok', { s1, c1 });
    rec('Z5c the sum model draws nothing and points at the cascade',
      pl1.stepEmpty && n1.some(n => n[0] === 'unstable') && n1.some(n => n[0] === 'other_model_draws'), { n1, pl1 });
    await setModel('cascade'); await p.sleep(800);
    const pl2 = await plotted(p); const n2 = await noteCodes();
    rec('Z5d the cascade model DRAWS that set', pl2.step >= 2 && !pl2.stepEmpty && !n2.some(n => n[0] === 'unstable'), { pl2, n2 });
    await p.shot(`${SHOTS}/z5_cascade_draws_sum_unstable.png`);

    const s2 = await edit([[-1.2, 10.0], [0.5, 1000.0]]);
    await p.ev('setTimeout(function(){location.reload();},0); 1'); await p.sleep(1200); await waitRendered(p, 'qubits.q3.z|');
    const c2 = await cell();
    await setModel('cascade'); await p.sleep(800);
    const n3 = await noteCodes(); const pl3 = await plotted(p);
    const blocks = n3.filter(n => /zline-note-block/.test(n[1]));
    rec('Z5e sum-stable / cascade-unstable: the row says ok / UNSTABLE', s2 === 200 && c2 === 'ok / UNSTABLE', { s2, c2 });
    rec('Z5f the cascade shows ONE block note naming stage 1 (A = -1.2), no model_error',
      blocks.length === 1 && blocks[0][0] === 'unstable' && blocks[0][2].indexOf('stage 1 of 2') >= 0 && blocks[0][2].indexOf('A = -1.2') >= 0
      && pl3.stepEmpty && pl3.pulseEmpty, { blocks, pl3 });
    await p.shot(`${SHOTS}/z5_cascade_stage_unstable.png`);

    const s3 = await edit(orig);
    await p.ev('setTimeout(function(){location.reload();},0); 1'); await p.sleep(1200);
    const r3 = await waitRendered(p, 'qubits.q3.z|');
    const pl4 = await plotted(p);
    const d4 = JSON.parse(await p.ev(`fetch('${smUrl(`/zline/data?line=qubits.q3.z`)}').then(function(r){return r.text();})`));
    rec('Z5g restored: q3 draws again with its original set', s3 === 200 && !!r3 && pl4.step >= 2
      && JSON.stringify(d4.port.exponential) === JSON.stringify(orig), { s3, pl4 });
    // Chrome logs its own refusal of SM's unsaved-edits beforeunload guard
    // (the edits Z5 staged): the guard working, not a page error
    const errs = p.errors(mark).filter(m => m.indexOf("Blocked attempt to show a 'beforeunload' confirmation panel") < 0);
    rec('Z5h no console errors', errs.length === 0, errs);
    await p.close();
  }
  console.log('beforeunload/other dialogs accepted: ' + JSON.stringify(dialogs));
  const bad = res.filter(r => !r.ok);
  console.log(`\n${res.length - bad.length}/${res.length} passed`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
