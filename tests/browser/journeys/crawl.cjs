/* The "click everything, then find your way back" crawl (docs/205).
 *
 * Why this exists: the Trends click shipped (docs/190-era, 2026-09-09) with a
 * real-Chrome check that the click OPENED the run, and a selfcheck that pinned
 * where it opened. Nobody pressed the x afterwards. The customer did, and it did
 * nothing (docs/204). A feature check asks "does X happen"; a user asks "and
 * then how do I get back". This crawl asks the user's question of EVERY
 * clickable thing on a page:
 *
 *   fresh load -> scroll the whole pane (lazy sections build) -> one REAL input
 *   (mouse click / slider drag / option pick / typed text) -> what opened?
 *   -> the way back a person would try: the thing's own close control, then
 *   Escape, then a click outside, then browser Back for a navigation
 *   -> is the page still whole? + every JS exception / console error.
 *
 * usage:
 *   node tests/browser/journeys/crawl.cjs --base http://127.0.0.1:5099 \
 *        --path "/topology?view=overview" --out crawl.jsonl [--from N --to M]
 * Needs a Chrome with --remote-debugging-port (SM_CDP_PORT, default 9333) and a
 * running SM with a chip loaded. Exit code 1 if any target has no way back or
 * raised a JS error.
 */
'use strict';
const fs = require('fs');
const { open } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = arg('base', 'http://127.0.0.1:5099');
const PATH = arg('path', '/topology?view=overview');
const OUT = arg('out', 'crawl.jsonl');
const FROM = +arg('from', 0), TO = +arg('to', 1e9);
const URL_ = BASE + PATH;

const PRESCROLL = `(async function(){ var pane=document.getElementById('table-pane'); if(!pane) return 0;
  for(var y=0;y<pane.scrollHeight;y+=600){ pane.scrollTop=y; await new Promise(function(r){setTimeout(r,250);}); }
  await new Promise(function(r){setTimeout(r,2500);}); pane.scrollTop=0; return 1; })()`;

const COLLECT = `(function(){
  var pane=document.getElementById('table-pane'); var list=[];
  function vis(el){ var r=el.getBoundingClientRect(); var cs=getComputedStyle(el); return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none'; }
  function lab(el){ return ((el.getAttribute('aria-label')||el.title||el.textContent||'')+'').trim().replace(/\\s+/g,' ').slice(0,50); }
  var n=0; function push(el,kind){ el.setAttribute('data-crawl',String(n)); list.push({id:n,kind:kind,label:lab(el),cls:(el.getAttribute('class')||'').toString().slice(0,60)}); n++; }
  pane.querySelectorAll('button, a[href], summary, [onclick]:not(button):not(a)').forEach(function(el){ if(vis(el)) push(el, el.tagName.toLowerCase()); });
  pane.querySelectorAll('select').forEach(function(el){ if(vis(el)) push(el,'select'); });
  pane.querySelectorAll('input[type=range]').forEach(function(el){ if(vis(el)) push(el,'range'); });
  pane.querySelectorAll('input[type=search], input[type=text]').forEach(function(el){ if(vis(el)) push(el,'text'); });
  ['.topo-hero-node','.topo-hero-edge-hit','.cm-node','.cm-hit'].forEach(function(sel){ var a=pane.querySelectorAll(sel); for(var i=0;i<Math.min(3,a.length);i++) if(!a[i].hasAttribute('data-crawl')) push(a[i],'map:'+sel); });
  pane.querySelectorAll('.js-plotly-plot').forEach(function(el){ push(el,'plot-point'); });
  return JSON.stringify(list); })()`;

const STATE = `(function(){
  var pane=document.getElementById('table-pane'), insp=document.getElementById('inspector-pane');
  var fixed=[]; document.querySelectorAll('body *').forEach(function(el){ var cs=getComputedStyle(el);
    if(cs.position==='fixed'&&cs.display!=='none'&&cs.visibility!=='hidden'){ var r=el.getBoundingClientRect(); if(r.width>40&&r.height>20) fixed.push(el.id?('#'+el.id):(el.className||el.tagName).toString().split(' ')[0]); } });
  return JSON.stringify({path:location.pathname+location.search, paneLen: pane?pane.textContent.length:0,
    marker: !!(pane && pane.querySelector('[data-crawl]')),
    insp: insp?insp.textContent.trim().length:0,
    fixed: fixed, dialogs: Array.from(document.querySelectorAll('dialog[open]')).map(function(d){return d.id||d.className;}) }); })()`;

// the close control of whatever just opened: searched INSIDE the new overlay(s)
// / the inspector / an open dialog, by what a person reads (x-like glyph,
// "Close") or by accessible name. Returns the first visible one.
const FIND_CLOSE = hosts => `(function(){ var hosts=${JSON.stringify(hosts)}; var cands=[];
  hosts.forEach(function(h){ var root = h.charAt(0)==='#' ? document.getElementById(h.slice(1)) : document.querySelector('.'+h+', '+h);
    if(!root) return; root.querySelectorAll('button, a, [role=button], [onclick]').forEach(function(b){
      var t=(b.textContent||'').trim(), a=((b.getAttribute('aria-label')||'')+' '+(b.title||'')+' '+(b.className||'')).toLowerCase();
      if(/^(\\u00d7|\\u2715|\\u2716|x|X|close|Close)$/.test(t) || /close|dismiss/.test(a)) cands.push(b); }); });
  var b=cands.filter(function(x){var r=x.getBoundingClientRect();return r.width>0&&r.height>0;})[0];
  if(!b) return 'null'; var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),t:(b.textContent||'').trim().slice(0,8)}); })()`;

const POS = id => `(function(){ var el=document.querySelector('[data-crawl="${id}"]'); if(!el) return 'null';
  el.scrollIntoView({block:'center'}); var r=el.getBoundingClientRect();
  if (el.classList.contains('js-plotly-plot')) { if(!el._fullLayout||!el.data) return 'null'; var xa=el._fullLayout.xaxis, ya=el._fullLayout.yaxis;
    for (var ti=0; ti<el.data.length; ti++){ var d=el.data[ti]; if(!d.x||!d.y) continue;
      for (var k=d.x.length-1;k>=0;k--){ if(d.y[k]==null) continue;
        return JSON.stringify({x:Math.round(r.left+xa._offset+xa.l2p(xa.d2l(d.x[k]))), y:Math.round(r.top+ya._offset+ya.l2p(ya.d2l(d.y[k]))+(d.type==='bar'?4:0))}); } }
    return 'null'; }
  if (el.tagName==='SELECT' || el.tagName==='INPUT') return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),field:el.tagName+':'+el.type});
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`;

(async () => {
  const out = fs.openSync(OUT, 'a');
  const p0 = await open(URL_); await p0.sleep(7000); await p0.ev(PRESCROLL);
  const all = JSON.parse(await p0.ev(COLLECT)); await p0.close();
  console.log('targets', all.length);
  let bad = 0;
  for (const t of all) {
    if (t.id < FROM || t.id > TO) continue;
    const rec = { id: t.id, kind: t.kind, label: t.label, cls: t.cls, errors: [] };
    let pg;
    try {
      pg = await open(URL_); await pg.sleep(6500); await pg.ev(PRESCROLL); await pg.ev(COLLECT);
      const s0 = JSON.parse(await pg.ev(STATE)); const mark = pg.events.length;
      const pos = JSON.parse(await pg.ev(POS(t.id)));
      if (!pos) { rec.opened = 'not-found-on-fresh-load'; fs.writeSync(out, JSON.stringify(rec) + '\n'); await pg.close(); continue; }
      await pg.sleep(400);
      if (pos.field === 'SELECT:select-one') { await pg.click(pos.x, pos.y); await pg.sleep(200); await pg.key('ArrowDown', 'ArrowDown', 40); await pg.key('Enter', 'Enter', 13); rec.did = 'picked next option'; }
      else if (pos.field && /range/.test(pos.field)) {
        await pg.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
        await pg.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pos.x + 30, y: pos.y, button: 'left' });
        await pg.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x + 30, y: pos.y, button: 'left', clickCount: 1 });
        rec.did = 'dragged';
      } else if (pos.field && /search|text/.test(pos.field)) { await pg.click(pos.x, pos.y); await pg.send('Input.insertText', { text: 'f_01' }); await pg.sleep(300); await pg.key('Enter', 'Enter', 13); rec.did = 'typed f_01 + Enter'; }
      else { await pg.click(pos.x, pos.y); rec.did = 'clicked'; }
      await pg.sleep(2200);
      const s1 = JSON.parse(await pg.ev(STATE));
      const newFixed = s1.fixed.filter(f => s0.fixed.indexOf(f) < 0);
      let opened = null;
      if (s1.path.split('?')[0] !== s0.path.split('?')[0]) opened = 'navigated';
      else if (!s1.marker) opened = 'page-replaced';
      else if (s1.dialogs.length) opened = 'dialog';
      else if (newFixed.length) opened = 'overlay';
      else if (s1.insp > 0 && s0.insp === 0) opened = 'inspector';
      rec.opened = opened || 'in-place';
      rec.newFixed = newFixed;
      const whole = s => s.marker && !s.dialogs.length && s.fixed.filter(f => s0.fixed.indexOf(f) < 0).length === 0 && (opened !== 'inspector' || s.insp === 0);
      if (opened === 'navigated') {
        await pg.ev('history.back()'); await pg.sleep(4000);
        const s2 = JSON.parse(await pg.ev(STATE)); rec.tries = ['Back']; rec.back = s2.path.split('?')[0] === s0.path.split('?')[0] && s2.paneLen > 0;
      } else if (opened) {
        rec.tries = [];
        const hosts = opened === 'inspector' ? ['#inspector-pane'] : opened === 'page-replaced' ? ['#table-pane'] : opened === 'dialog' ? ['dialog[open]'] : newFixed;
        const cp = JSON.parse(await pg.ev(FIND_CLOSE(hosts)));
        if (cp) { await pg.click(cp.x, cp.y); rec.tries.push('its close [' + cp.t + ']'); await pg.sleep(1300); }
        let s2 = JSON.parse(await pg.ev(STATE)); let back = whole(s2);
        if (!back) { await pg.key('Escape', 'Escape', 27); rec.tries.push('Escape'); await pg.sleep(1000); s2 = JSON.parse(await pg.ev(STATE)); back = whole(s2); }
        if (!back && opened !== 'page-replaced') { await pg.click(1590, 940); rec.tries.push('click outside'); await pg.sleep(900); s2 = JSON.parse(await pg.ev(STATE)); back = whole(s2); }
        rec.back = back; rec.leftover = s2.fixed.filter(f => s0.fixed.indexOf(f) < 0);
      } else rec.back = true;
      rec.errors = pg.errors(mark);
    } catch (e) { rec.crash = String(e).slice(0, 200); }
    try { if (pg) await pg.close(); } catch (e) {}
    if (rec.back === false || rec.errors.length || rec.crash) bad++;
    fs.writeSync(out, JSON.stringify(rec) + '\n');
    console.log(t.id, t.kind, (t.label || '').slice(0, 26), '->', rec.opened, rec.back === false ? 'NO-WAY-BACK' : '', rec.errors.length ? 'ERR ' + rec.errors[0].slice(0, 80) : '', rec.crash ? 'CRASH' : '');
  }
  console.log('bad', bad);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
