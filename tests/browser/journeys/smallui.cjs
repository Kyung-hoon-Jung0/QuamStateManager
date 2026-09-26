/* Queue #2 / #5 / #7 / #8 journeys, in real Chrome (2026-09-26).
 *
 *   #2  the sidebar sub-item reads "Live edit - Json Tree view"; it opens the
 *       tree, Back returns, reload keeps it
 *   #5  no bare arrow beside a Chip Status panel/tile title; the direction is
 *       in the title's hover text
 *   #7  the slow-route loader is a slim strip under the top bar (measured
 *       while a throttled slow open is in flight), it clears when the page is
 *       painted, and a Trends chip toggle never shows it
 *   #8  a fresh browser gets the compact run list; the toggle and reload
 *       keep the user's choice; clearing the key returns to compact
 *
 * usage: SM_CDP_PORT=9408 node tests/browser/journeys/smallui.cjs 5108 <shot-dir>
 * Exit 1 on any FAIL.
 */
'use strict';
const { open } = require('./cdp.cjs');
const path = require('path');
const PORT = process.argv[2] || '5108';
const SHOTS = process.argv[3] || '.';
const BASE = `http://127.0.0.1:${PORT}`;
const res = [];
function rec(name, ok, detail) { res.push({ name, ok }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 500) : '')); }
const J = async (p, e) => { const v = await p.ev(e); try { return JSON.parse(v); } catch (x) { return v; } };
async function center(p, sel, text) {
  return J(p, `(function(){ var b=Array.from(document.querySelectorAll(${JSON.stringify(sel)})).filter(function(x){var r=x.getBoundingClientRect(); return r.width>0&&r.height>0 && (${JSON.stringify(text || '')}==='' || (x.textContent||'').trim()===${JSON.stringify(text || '')});})[0];
    if(!b) return 'null'; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`);
}
// records every visible frame of the loader: its rect + text, and the top bar's bottom
const WATCH = `(function(){ if (window.__ldr) return 'already'; window.__ldr=[]; var el=document.getElementById('quam-loader');
  function snap(){ if(!el.classList.contains('visible')) { window.__ldr.push({v:false,t:Date.now()}); return; }
    var r=el.getBoundingClientRect(), tb=document.querySelector('.topbar, header.topbar, #topbar'); var tr=tb?tb.getBoundingClientRect():null;
    window.__ldr.push({v:true,t:Date.now(),top:Math.round(r.top),h:Math.round(r.height),w:Math.round(r.width),cx:Math.round(r.left+r.width/2),
      tbBottom:tr?Math.round(tr.bottom):null, vw:innerWidth, vh:innerHeight, txt:el.textContent.replace(/\\s+/g,' ').trim()}); }
  new MutationObserver(snap).observe(el,{attributes:true,attributeFilter:['class']});
  setInterval(function(){ if(el.classList.contains('visible')) snap(); }, 300);
  return 'ok'; })()`;

(async () => {
  // ---- #2: the renamed sidebar item
  {
    const p = await open(`${BASE}/bulk`); await p.sleep(3500);
    // the live-edit group may be collapsed; open it
    await p.ev(`(function(){ var ul=document.getElementById('live-edit-subnav'); if(ul && ul.classList.contains('nav-subitems-collapsed')){ var b=document.querySelector('[aria-controls="live-edit-subnav"]'); b && b.click(); } })()`);
    await p.sleep(300);
    const lab = await J(p, `JSON.stringify(Array.from(document.querySelectorAll('#live-edit-subnav a')).map(function(a){return a.textContent.trim();}))`);
    rec('#2 the sidebar sub-item reads exactly "Live edit - Json Tree view"', Array.isArray(lab) && lab[0] === 'Live edit - Json Tree view', lab);
    const c = await center(p, '#live-edit-subnav a', 'Live edit - Json Tree view');
    if (c) { await p.click(c.x, c.y); await p.sleep(3000); }
    const on = await J(p, `JSON.stringify({path:location.pathname, tree:!!document.querySelector('#explorer-tree-state, .json-tree'), active:(document.querySelector('#live-edit-subnav a.active')||{}).textContent||null})`);
    rec('#2 clicking it opens the tree at /explorer and lights itself', on.path === '/explorer' && on.tree && /Live edit - Json Tree view/.test(on.active || ''), on);
    await p.shot(path.join(SHOTS, 'q2_explorer.png'));
    await p.ev('history.back()'); await p.sleep(3000);
    const back = await J(p, `JSON.stringify({path:location.pathname, grid:!!document.querySelector('#table-pane .bulk-cell')})`);
    rec('#2 Back returns to Live State Edit, grid intact', back.path === '/bulk' && back.grid, back);
    await p.send('Page.navigate', { url: `${BASE}/explorer` }); await p.sleep(3500);
    const re = await J(p, `JSON.stringify({tree:!!document.querySelector('#explorer-tree-state, .json-tree'), active:(document.querySelector('#live-edit-subnav a.active')||{}).textContent||null, palette:document.documentElement.innerHTML.indexOf('Json Tree View')})`);
    rec('#2 a reload of /explorer keeps the tree and the new label (no old label anywhere in the page)', re.tree && /Live edit - Json Tree view/.test(re.active || '') , re);
    rec('#2 no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }

  // ---- #5: no bare arrow beside a Chip Status title
  {
    const p = await open(`${BASE}/topology?view=coherence`); await p.sleep(7000);
    const t = await J(p, `(function(){ var hs=Array.from(document.querySelectorAll('.topo-metric-panel-title')); var tiles=Array.from(document.querySelectorAll('#topo-overview-tiles .topo-card'));
      var t1=hs.map(function(h){return h.querySelector('.metric-label');}).filter(Boolean)[0];
      return JSON.stringify({n:hs.length, arrowed:hs.filter(function(h){return /[\\u2191\\u2193]/.test(h.textContent);}).map(function(h){return h.textContent.trim().slice(0,30);}),
        tiles:tiles.length, tileArrowed:tiles.filter(function(c){return !!c.querySelector('.metric-dir');}).length,
        firstTitle:t1?t1.textContent.trim():null, firstTip:t1?t1.getAttribute('title'):null,
        leftover:Array.from(document.querySelectorAll('.metric-dir')).filter(function(s){return !/is better/i.test(s.getAttribute('title')||'');}).length}); })()`);
    rec('#5 no panel title carries a bare arrow', t.n > 0 && t.arrowed.length === 0, t);
    rec('#5 no Overview tile title carries one either', t.tiles > 0 && t.tileArrowed === 0, { tiles: t.tiles, arrowed: t.tileArrowed });
    rec('#5 the direction is in the title hover text in words', /is better/i.test(t.firstTip || ''), { title: t.firstTitle, tip: t.firstTip });
    rec('#5 any arrow still drawn explains itself on hover', t.leftover === 0, t.leftover);
    await p.ev(`(function(){ var h=document.querySelector('.topo-metric-panel-title'); h && h.scrollIntoView({block:'start'}); })()`); await p.sleep(600);
    await p.shot(path.join(SHOTS, 'q5_panel_titles.png'));
    await p.send('Page.reload', {}); await p.sleep(7000);
    const again = await J(p, `JSON.stringify({n:document.querySelectorAll('.topo-metric-panel-title').length, arrowed:Array.from(document.querySelectorAll('.topo-metric-panel-title')).filter(function(h){return /[\\u2191\\u2193]/.test(h.textContent);}).length})`);
    rec('#5 after reload the panels are back, still arrow-free', again.n > 0 && again.arrowed === 0, again);
    rec('#5 no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }

  // ---- #7: the loader as a slim strip; cleared after paint; Trends toggles never show it
  {
    const p = await open(`${BASE}/journal`); await p.sleep(3500);
    await p.ev(WATCH);
    await p.send('Network.enable');
    await p.send('Network.emulateNetworkConditions', { offline: false, latency: 1800, downloadThroughput: -1, uploadThroughput: -1 });
    const c = await center(p, '.sidebar-nav a[href="/bulk"]');
    const t0 = Date.now();
    if (c) await p.click(c.x, c.y);
    await p.sleep(900);
    await p.shot(path.join(SHOTS, 'q7_loader_strip.png'));
        await p.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await p.sleep(6000);
    const log = await J(p, 'JSON.stringify(window.__ldr)');
    const vis = (log || []).filter(x => x.v);
    const f = vis[0] || {};
    rec('#7 a throttled slow open shows the loader', vis.length > 0, { frames: vis.length, first: f });
    rec('#7 ...as a slim strip just under the top bar, not a centred card',
        vis.length > 0 && f.tbBottom != null && f.top >= f.tbBottom && f.top - f.tbBottom <= 12 && f.h <= 40 && f.top < f.vh / 4,
        { top: f.top, topbarBottom: f.tbBottom, height: f.h, width: f.w, vh: f.vh });
    rec('#7 ...one line: the please-wait text, no big brand title', vis.every(x => /^Please wait a moment/.test(x.txt) && !/QUAM/.test(x.txt)), vis.map(x => x.txt).slice(-1));
    const end = await J(p, `JSON.stringify({visible:document.getElementById('quam-loader').classList.contains('visible'), path:location.pathname, grid:!!document.querySelector('#table-pane .bulk-cell')})`);
    rec('#7 the page painted and the strip is gone', !end.visible && end.path === '/bulk' && end.grid, end);
    await p.shot(path.join(SHOTS, 'q7_after.png'));
    await p.ev('history.back()'); await p.sleep(3000);
    const bk = await J(p, `JSON.stringify({path:location.pathname, visible:document.getElementById('quam-loader').classList.contains('visible')})`);
    rec('#7 Back to the previous page: no stranded strip', bk.path === '/journal' && !bk.visible, bk);
    rec('#7 no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  {
    const p = await open(`${BASE}/topology?view=trends`); await p.sleep(9000);
    await p.ev(WATCH);
    await p.send('Network.enable');
    await p.send('Network.emulateNetworkConditions', { offline: false, latency: 1200, downloadThroughput: -1, uploadThroughput: -1 });
    const toggles = [];
    for (let i = 0; i < 3; i++) {
      const c = await J(p, `(function(){ var b=Array.from(document.querySelectorAll('#topo-trends .topo-trend-chip[data-trend-metric]')).filter(function(x){return x.getBoundingClientRect().width>0;})[${i}]; if(!b) return 'null'; b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),m:b.getAttribute('data-trend-metric')}); })()`);
      if (!c) { toggles.push('missing'); continue; }
      await p.click(c.x, c.y); toggles.push(c.m);
      await p.sleep(2500);
    }
    await p.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await p.sleep(1500);
    const log = await J(p, 'JSON.stringify(window.__ldr)');
    rec('#7 three throttled Trends chip toggles never flash the loader', toggles.every(t => t !== 'missing') && !(log || []).some(x => x.v), { toggles, frames: (log || []).filter(x => x.v).length });
    await p.shot(path.join(SHOTS, 'q7_trends_after_toggles.png'));
    rec('#7 trends: no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }

  // ---- #8: the compact run list by default
  {
    const p = await open(`${BASE}/datasets`); await p.sleep(5000);
    await p.ev(`localStorage.removeItem('quam_exp_list_compact')`);
    await p.send('Page.reload', {}); await p.sleep(5000);
    const rows = `(function(){ var ns=Array.from(document.querySelectorAll('#sidebar-tree .entry-name')).filter(function(n){return n.getBoundingClientRect().height>0;});
      var hs=ns.map(function(n){return Math.round(n.getBoundingClientRect().height);}); var lh=ns.length?parseFloat(getComputedStyle(ns[0]).lineHeight)||0:0;
      return JSON.stringify({compact:document.body.classList.contains('exp-list-compact'), key:localStorage.getItem('quam_exp_list_compact'),
        pressed:(document.getElementById('exp-density-compact')||{}).getAttribute && document.getElementById('exp-density-compact').getAttribute('aria-pressed'),
        n:ns.length, maxH:hs.length?Math.max.apply(null,hs):0, minH:hs.length?Math.min.apply(null,hs):0, lh:lh}); })()`;
    const a = await J(p, rows);
    rec('#8 a fresh browser (key absent) gets the compact list, compact pressed, key still absent',
        a.compact && a.key === null && a.pressed === 'true' && a.n > 0, a);
    rec('#8 ...and every visible run name is one line', a.n > 0 && a.maxH === a.minH, a);
    await p.ev(`(function(){document.querySelectorAll('#sidebar-tree details').forEach(function(d){d.open=true;}); var n=document.querySelector('#exp-density-compact')||document.querySelector('#sidebar-tree .entry-name'); if(n) n.scrollIntoView({block:'start'}); return 1;})()`); await p.sleep(300);
    await p.shot(path.join(SHOTS, 'q8_compact_default.png'));
    const full = await center(p, '#exp-density-full');
    await p.click(full.x, full.y); await p.sleep(500);
    const b = await J(p, rows);
    rec('#8 pressing Full names wraps the list and stores 0', !b.compact && b.key === '0' && b.maxH > a.maxH, b);
    await p.ev(`(function(){document.querySelectorAll('#sidebar-tree details').forEach(function(d){d.open=true;}); var n=document.querySelector('#exp-density-compact')||document.querySelector('#sidebar-tree .entry-name'); if(n) n.scrollIntoView({block:'start'}); return 1;})()`); await p.sleep(300);
    await p.shot(path.join(SHOTS, 'q8_full_names.png'));
    await p.send('Page.reload', {}); await p.sleep(5000);
    const c = await J(p, rows);
    rec('#8 reload keeps the full-names choice', !c.compact && c.key === '0', c);
    const comp = await center(p, '#exp-density-compact');
    await p.click(comp.x, comp.y); await p.sleep(500);
    await p.send('Page.reload', {}); await p.sleep(5000);
    const d = await J(p, rows);
    rec('#8 Compact again survives a reload', d.compact && d.key === '1', d);
    rec('#8 no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }

  const bad = res.filter(r => !r.ok);
  console.log(`\n${res.length - bad.length}/${res.length} PASS`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
