/* Chip Status journeys a customer actually walks, in real Chrome (docs/205).
 *
 * Every scenario ends where a person ends: back on the page they started from,
 * with what they were looking at still on screen. usage:
 *   node tests/browser/journeys/chip_status.cjs [port]
 * Needs Chrome with --remote-debugging-port (SM_CDP_PORT) and SM on <port> with
 * a chip that has Trends history and pairs. Exit 1 on any FAIL.
 */
'use strict';
const { open } = require('./cdp.cjs');
const fs = require('fs');
const PORT = process.argv[2] || '5099';
const BASE = `http://127.0.0.1:${PORT}`;
const res = [];
function rec(name, ok, detail) { res.push({ name, ok, detail }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  ' + JSON.stringify(detail).slice(0, 400) : '')); }
const ERRS = p => p.errors(0);

async function trendPoint(p, idx) {
  return JSON.parse(await p.ev(`(function(){ var g=document.getElementById('topo-trend-${idx || 0}'); if(!g||!g._fullLayout) return 'null';
    g.scrollIntoView({block:'center'}); var r=g.getBoundingClientRect(), xa=g._fullLayout.xaxis, ya=g._fullLayout.yaxis;
    for (var ti=0; ti<g.data.length; ti++){ var t=g.data[ti]; if(!t.customdata) continue;
      for (var k=t.x.length-1;k>=0;k--){ if(t.y[k]==null) continue; if(!/click to open/.test(String(t.customdata[k]))) continue;
        return JSON.stringify({x:Math.round(r.left+xa._offset+xa.l2p(xa.d2l(t.x[k]))), y:Math.round(r.top+ya._offset+ya.l2p(ya.d2l(t.y[k])))}); } }
    return 'null'; })()`));
}
const paneTop = p => p.ev(`Math.round(document.getElementById('table-pane').scrollTop)`);
async function center(p, sel) { return JSON.parse(await p.ev(`(function(){ var b=Array.from(document.querySelectorAll(${JSON.stringify(sel)})).filter(function(x){var r=x.getBoundingClientRect();return r.width>0&&r.height>0;})[0]; if(!b) return 'null'; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`)); }

(async () => {
  // ---- S1: Trends point -> run -> its tabs -> x -> Trends where it was
  {
    const p = await open(`${BASE}/topology?view=trends`); await p.sleep(7000);
    const pt = await trendPoint(p, 0); const top0 = await paneTop(p);
    if (!pt) rec('S1 trends has a clickable point', false);
    else {
      await p.click(pt.x, pt.y); await p.sleep(3500);
      const opened = await p.ev(`!!document.querySelector('#inspector-pane #ds-detail-root') && !!document.getElementById('topo-trend-0')`);
      rec('S1a a Trends click opens the run beside the chart', opened);
      const tabs = ['Figures', 'Results', 'State', 'Overview'];
      const seen = [];
      for (const tname of tabs) {
        const c = JSON.parse(await p.ev(`(function(){ var b=Array.from(document.querySelectorAll('#inspector-pane button, #inspector-pane a')).filter(function(x){return (x.textContent||'').trim().indexOf(${JSON.stringify(tname)})===0 && x.getBoundingClientRect().width>0;})[0]; if(!b) return 'null'; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`));
        if (!c) { seen.push(tname + ':missing'); continue; }
        await p.click(c.x, c.y); await p.sleep(1800);
        const still = await p.ev(`!!document.querySelector('#inspector-pane #ds-detail-root') && !!document.getElementById('topo-trend-0')`);
        seen.push(tname + ':' + (still ? 'ok' : 'LOST'));
      }
      rec('S1b the run\'s own tabs work and nothing is lost', seen.every(s => /:ok$/.test(s)), seen);
      const x = await center(p, '#inspector-pane .inspector-close');
      if (x) { await p.click(x.x, x.y); await p.sleep(1500); }
      const back = JSON.parse(await p.ev(`JSON.stringify({run:!!document.querySelector('#ds-detail-root'), trends:!!document.getElementById('topo-trend-0'), top:Math.round(document.getElementById('table-pane').scrollTop), trendVisible:(function(){var g=document.getElementById('topo-trend-0'); if(!g) return false; var r=g.getBoundingClientRect(); var pr=document.getElementById('table-pane').getBoundingClientRect(); return r.bottom>pr.top && r.top<pr.bottom;})()})`));
      rec('S1c x closes the run and Trends is back', !!x && !back.run && back.trends, back);
      rec('S1d ...with the chart still on screen (not scrolled away)', back.trendVisible, { top0, now: back.top });
    }
    rec('S1e no JS exceptions', ERRS(p).length === 0, ERRS(p));
    await p.close();
  }
  // ---- S2: map qubit -> inspector -> x
  {
    const p = await open(`${BASE}/topology?view=topology`); await p.sleep(7000);
    const n = await center(p, '#table-pane .topo-hero-node');
    if (!n) rec('S2 map has a qubit', false);
    else {
      await p.click(n.x, n.y); await p.sleep(2500);
      const st = JSON.parse(await p.ev(`JSON.stringify({insp:(document.getElementById('inspector-pane')||{textContent:''}).textContent.trim().length, map:!!document.querySelector('#table-pane .topo-hero-node')})`));
      rec('S2a a map qubit opens its inspector beside the map', st.insp > 0 && st.map, st);
      const x = await center(p, '#inspector-pane .inspector-close');
      if (x) { await p.click(x.x, x.y); await p.sleep(1200); }
      const st2 = JSON.parse(await p.ev(`JSON.stringify({insp:(document.getElementById('inspector-pane')||{textContent:''}).textContent.trim().length, map:!!document.querySelector('#table-pane .topo-hero-node')})`));
      rec('S2b its x closes it and the map is intact', !!x && st2.insp === 0 && st2.map, st2);
    }
    const e = await center(p, '#table-pane .topo-hero-edge-hit');
    if (e) {
      await p.click(e.x, e.y); await p.sleep(2500);
      const st = JSON.parse(await p.ev(`JSON.stringify({insp:(document.getElementById('inspector-pane')||{textContent:''}).textContent.trim().length})`));
      const x = await center(p, '#inspector-pane .inspector-close');
      if (x) { await p.click(x.x, x.y); await p.sleep(1200); }
      const st2 = JSON.parse(await p.ev(`JSON.stringify({insp:(document.getElementById('inspector-pane')||{textContent:''}).textContent.trim().length, map:!!document.querySelector('#table-pane .topo-hero-node')})`));
      rec('S2c a map pair opens and closes the same way', st.insp > 0 && !!x && st2.insp === 0 && st2.map, { opened: st.insp, after: st2 });
    }
    rec('S2d no JS exceptions', ERRS(p).length === 0, ERRS(p));
    await p.close();
  }
  // ---- S3: every sidebar sub-item brings its section into view and lights its tab
  {
    const SEL = {overview:'[data-topo-section="overview"]',health:'[data-topo-section="health"]',topology:'#sec-topology',fidelity2q:'[data-topo-section="fidelity"]',fidelity1q:'#sec-fidelity-1q',readout:'#sec-readout',coherence:'#topo-metric-panels [data-group="coherence"]',frequencies:'#topo-metric-panels [data-group="frequency"]',calibration:'#topo-metric-panels [data-group="calibration"]',trends:'[data-topo-section="trends"]'};
    const p = await open(`${BASE}/topology?view=overview`); await p.sleep(8000);
    const views = JSON.parse(await p.ev(`JSON.stringify(Array.from(document.querySelectorAll('#chip-status-subnav a[data-view]')).map(function(a){return a.getAttribute('data-view');}))`));
    const out = [];
    for (const v of views) {
      const c = await center(p, `#chip-status-subnav a[data-view="${v}"]`);
      if (!c) { out.push(v + ':no-link'); continue; }
      await p.click(c.x, c.y); await p.sleep(6000);
      const sel = JSON.stringify('#table-pane ' + SEL[v]);
      const r = await p.ev(`(function(){ var els=document.querySelectorAll(${sel}); if(!els.length) return 'none'; var pr=document.getElementById('table-pane').getBoundingClientRect(); var inView=Array.from(els).some(function(s){var r=s.getBoundingClientRect(); return r.bottom>pr.top+80 && r.top<pr.bottom-40;}); return inView?'in-view':'off'; })()`);
      out.push(v + ':' + r);
    }
    rec('S3 every Chip Status sub-item brings its section into view', out.every(s => /:(in-view|none)$/.test(s)) && !out.some(s => /:none$/.test(s) && !/coherence/.test(s)), out);
    rec('S3b no JS exceptions', ERRS(p).length === 0, ERRS(p));
    await p.close();
  }
  // ---- S5: the 2Q Gate Fidelity section is never a bare heading
  {
    const p = await open(`${BASE}/topology?view=fidelity2q`); await p.sleep(9000);
    const st = JSON.parse(await p.ev(`JSON.stringify({panels:document.querySelectorAll('#topo-2q-rb-panels .js-plotly-plot, #topo-2q-rb-panels [class*=panel]').length, empty:!!document.querySelector('#topo-2q-rb-panels .topo-2qrb-empty'), text:(document.getElementById('topo-2q-rb-panels')||{textContent:''}).textContent.trim().length})`));
    rec('S5 the 2Q section shows RB panels or says why it is empty', st.text > 0 && (st.panels > 0 || st.empty), st);
    await p.close();
  }
  // ---- S4: arrive from another page, open a run from Trends, press Back
  {
    const p = await open(`${BASE}/bulk`); await p.sleep(5000);
    await p.send('Page.navigate', { url: `${BASE}/topology?view=trends` }); await p.sleep(8000);
    const pt = await trendPoint(p, 0);
    if (pt) {
      await p.click(pt.x, pt.y); await p.sleep(3000);
      await p.ev('history.back()'); await p.sleep(4000);
      const st = JSON.parse(await p.ev(`JSON.stringify({path:location.pathname, pane:(document.getElementById('table-pane')||{textContent:''}).textContent.trim().length})`));
      rec('S4 Back after opening a run lands on a real page, never a blank', st.pane > 0, st);
    } else rec('S4 point', false);
    await p.close();
  }
  process.exit(res.some(r => !r.ok) ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
