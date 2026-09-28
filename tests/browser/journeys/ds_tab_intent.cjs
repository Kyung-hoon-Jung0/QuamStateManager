/* w8/dstab journey: the reader's TAB survives a run that lacks it.
 *
 * The customer's wish (2026-09-28): "with run 2529 open, scrolled or on
 * another tab, clicking run 3002 keeps exactly that scroll position and that
 * tab". docs/221 kept the tab across switches; a run WITHOUT the tab (no
 * HDF5 -> no Interactive) lands on Full View. This journey proves, in real
 * Chrome, with real clicks and keys, that such a run in between does not
 * overwrite the reader's intent -- and that a real reader action on it does:
 *
 *   click   A (Interactive, deep) -> C (no Interactive): Full View at its top
 *           -> D (Interactive): Interactive, the same landmark at the same
 *           offset under the sticky header -> A again: identical place
 *   keys    the same with ] / [ over three adjacent runs (middle one lacks it)
 *   pick    on C the reader PRESSES Full View (the tab it already shows) ->
 *           D lands on Full View (the reader's choice replaces the intent)
 *   close   x on A, then a run opened into the empty pane: Full View at the
 *           top (a fresh open, not a switch; it used to land 194 px down)
 *   reload  the page comes back whole
 *
 * The runs are discovered from the table (each detail's tab strip fetched),
 * so any dataset with Interactive and non-Interactive runs side by side works.
 * The place is measured independently of DsScrollAnchor: landmarks under the
 * reading line (the sticky header's bottom edge), keyed by kind + identity.
 *
 * usage: SM_CDP_PORT=9621 node tests/browser/journeys/ds_tab_intent.cjs \
 *          --base http://127.0.0.1:5321 --shots <dir>
 * Exit 1 on any failure or JS error.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = arg('base', 'http://127.0.0.1:5099');
const SHOTS = arg('shots', '');
const SCAN = +arg('scan', 120);   // how many table rows to inspect for a usable A / C / D
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const HELPERS = `
window.__t = {
  pane: function(){ return document.getElementById('inspector-pane'); },
  uid: function(){ var r=document.querySelector('#inspector-pane #ds-detail-root'); return r ? r.dataset.uid : ''; },
  tab: function(){ var a=document.querySelector('#inspector-pane .dataset-tabs a.active[data-ds-tab]'); return a ? a.getAttribute('data-ds-tab') : null; },
  cont: function(){ var r=this.pane().querySelector('#ds-detail-root'); if(!r) return null; var t=this.tab();
    return ['full','overview','results','figures'].indexOf(t)>=0 ? r.querySelector('#ds-tab-combined') : r.querySelector('#ds-tab-'+t); },
  line: function(){ var p=this.pane(), h=p.querySelector('.inspector-header'), y=p.getBoundingClientRect().top+p.clientTop;
    if (h){ var r=h.getBoundingClientRect(); if (r.height>0 && r.top<=y+0.5 && r.bottom>y+0.5) y=r.bottom; } return y; },
  key: function(e){ var t;
    if (e.hasAttribute('data-fvsec')) return 'sec:'+e.getAttribute('data-fvsec');
    if (e.hasAttribute('data-fig')) return 'ifig:'+e.getAttribute('data-fig');
    if (e.classList.contains('figure-card')) { t=e.querySelector('.figure-label code'); return 'fig:'+(t?t.textContent.trim():''); }
    if (e.tagName==='DETAILS') { t=e.querySelector('summary'); var s=''; if(t) for (var i=0;i<t.childNodes.length;i++) if (t.childNodes[i].nodeType===3) s+=t.childNodes[i].nodeValue; return 'det:'+s.replace(/\\s+/g,' ').trim(); }
    return 'x'; },
  marks: function(){ var c=this.cont(); if(!c) return []; var out=[], seen={};
    [].forEach.call(c.querySelectorAll('[data-fvsec], .ds-interactive-fig[data-fig], .figure-card, details'), function(e){
      if (e.closest('.json-tree, .fig-info-col, .ds-inline-tree')) return; var r=e.getBoundingClientRect(); if (r.height<=0) return;
      var k=__t.key(e); var n=seen[k]||0; seen[k]=n+1; out.push({k:k+'#'+n, top:r.top, h:r.height}); });
    return out; },
  ref: function(){ var L=this.line(); var p=this.pane();
    return {tab:this.tab(), st:p.scrollTop, uid:this.uid(),
            chain:this.marks().filter(function(m){return m.top<=L+0.01 && m.top+m.h>L;}).map(function(m){return {k:m.k, off:L-m.top};})}; },
  meas: function(ref){ var p=this.pane(), L=this.line(), o={uid:this.uid(), tab:this.tab(), st:p.scrollTop, max:p.scrollHeight-p.clientHeight};
    if (o.tab!==ref.tab) { o.why='tab '+o.tab; return o; }
    var map={}; this.marks().forEach(function(m){ map[m.k]=m; });
    for (var d=ref.chain.length-1; d>=0; d--) { var lv=ref.chain[d], m=map[lv.k]; if(!m || m.h<=lv.off) continue;
      var need=o.st+(m.top-L)+lv.off; o.lvl=lv.k; o.depth=d; o.of=ref.chain.length-1; o.reach=need<=o.max+0.5 && need>=-0.5;
      o.delta=Math.round(((L-m.top)-lv.off)*100)/100; return o; }
    o.lvl='none'; return o; },
  settled: function(){ var p=this.pane(); if(!p || p.classList.contains('htmx-request')) return false;
    if (!p.querySelector('#ds-detail-root')) return false;
    var im=p.querySelectorAll('img'); for (var i=0;i<im.length;i++) if (!im[i].complete && im[i].offsetParent) return false;
    var ic=p.querySelector('#ds-interactive-container'); if (this.tab()==='interactive' && ic && /Loading/.test(ic.textContent.slice(0,200))) return false;
    var sh=p.scrollHeight; if (this._sh!==sh){ this._sh=sh; this._t=Date.now(); return false; } return Date.now()-this._t>600; },
  scroller: function(){ return document.getElementById('table-pane'); },
  rowXY: function(id){ return (async function(){ var sc=__t.scroller(); var r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]');
    if (!r) { sc.scrollTop=0; for (var k=0;k<300 && !r;k++){ sc.scrollTop += sc.clientHeight*0.7; await new Promise(function(z){setTimeout(z,80);}); r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]'); } }
    if (!r) return null; r.scrollIntoView({block:'center'}); await new Promise(function(z){setTimeout(z,250);});
    r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]'); if(!r) return null;
    var b=r.querySelector('[data-col-key=id]').getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2}; })(); },
  tabXY: function(t){ var a=document.querySelector('#inspector-pane .dataset-tabs a[data-ds-tab="'+t+'"]:not(.disabled)'); if(!a) return null;
    var b=a.getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2}; },
  wheelXY: function(){ var p=this.pane(), b=p.getBoundingClientRect();
    for (var y=b.top+150; y<b.bottom-30; y+=40) for (var x=b.left+40; x<b.right-40; x+=90) { var e=document.elementFromPoint(x,y);
      if (!e || !p.contains(e) || e.closest('.json-tree, .fig-info-col, .ds-inline-tree')) continue; return {x:x, y:y}; }
    return {x:b.left+40, y:b.top+300}; },
  closeXY: function(){ var b=document.querySelector('#inspector-pane .inspector-close'); if(!b) return null; var r=b.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; },
  // the table order + which runs have which tab (their own detail's tab strip)
  discover: function(n){ return (async function(){ var sc=__t.scroller(); sc.scrollTop=0; var ids=[], seen={};
    for (var k=0;k<400 && ids.length<n;k++){ [].forEach.call(document.querySelectorAll('#datasets-tbody tr.clickable-row'), function(r){ var id=r.getAttribute('data-id'); if(!seen[id]){ seen[id]=1; ids.push({id:id, exp:r.getAttribute('data-exp')}); } });
      sc.scrollTop += sc.clientHeight*0.7; await new Promise(function(z){setTimeout(z,60);}); }
    sc.scrollTop=0; ids=ids.slice(0,n);
    for (var i=0;i<ids.length;i++){ var t=await (await fetch('/dataset/'+ids[i].id, {headers:{'HX-Request':'true'}})).text();
      ids[i].inter = t.indexOf('data-ds-tab="interactive"')>=0; }
    return ids; })(); },
};`;

(async () => {
  const p = await open(BASE + '/datasets', 1600, 950);
  const mark0 = p.events.length;
  const waitRows = async () => { for (let i = 0; i < 120; i++) { if (await p.ev("document.querySelectorAll('#datasets-tbody tr.clickable-row').length") > 0) return; await sleep(500); } };
  await waitRows(); await p.ev(HELPERS);
  const J = async (expr) => { const v = await p.ev(`Promise.resolve(${expr}).then(function(x){return JSON.stringify(x);})`); try { return JSON.parse(v); } catch (e) { throw new Error('eval: ' + String(v).slice(0, 200)); } };
  const settle = async () => { await p.ev('__t._sh=-1'); const t0 = Date.now(); while (Date.now() - t0 < 30000) { if (await p.ev('__t.settled()')) return; await sleep(100); } };
  const waitUid = async (before) => { for (let k = 0; k < 600; k++) { const u = await p.ev('__t.uid()'); if (u && u !== before) return u; await sleep(20); } return null; };
  const clickRun = async (id) => { const before = await p.ev('__t.uid()'); const xy = await J(`__t.rowXY(${JSON.stringify(id)})`); if (!xy) throw new Error('row not found ' + id);
    await p.click(xy.x, xy.y); const u = await waitUid(before); await settle(); await sleep(300); return u; };
  const KEYS = { ']': [']', 'BracketRight', 221], '[': ['[', 'BracketLeft', 219] };
  const key = async (k) => { const before = await p.ev('__t.uid()'); await p.ev('document.activeElement && document.activeElement.blur && document.activeElement.blur()');
    const [kk, code, vk] = KEYS[k]; await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: kk, code, windowsVirtualKeyCode: vk, text: kk });
    await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: kk, code, windowsVirtualKeyCode: vk });
    const u = await waitUid(before); await settle(); await sleep(300); return u; };
  const pressTab = async (t) => { const xy = await J(`__t.tabXY(${JSON.stringify(t)})`); if (!xy) return false; await p.click(xy.x, xy.y); await sleep(500); await settle(); return true; };
  const wheelTo = async (frac) => { for (let k = 0; k < 90; k++) { const g = await J(`({st:__t.pane().scrollTop, max:__t.pane().scrollHeight-__t.pane().clientHeight})`);
    if (g.st >= frac * g.max) break; const xy = await J('__t.wheelXY()');
    await p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: xy.x, y: xy.y, deltaX: 0, deltaY: 97 }); await sleep(110); } await sleep(500); };
  const fresh = async () => { await p.ev('window.__old=1'); await p.send('Page.navigate', { url: BASE + '/datasets' });
    for (let i = 0; i < 200; i++) { if (await p.ev("typeof window.__old==='undefined' && document.readyState==='complete'") === true) break; await sleep(150); }
    await waitRows(); await sleep(400); await p.ev(HELPERS); };
  const shot = async (n) => { if (SHOTS) await p.shot(path.join(SHOTS, n + '.png')); };
  const EPS = 0.5 + 1e-6;
  let fails = 0; const rep = [];
  const check = (name, cond, info) => { rep.push({ name, ok: !!cond, info }); if (!cond) fails++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(info).slice(0, 260)}`); };
  const same = (m, ref) => m.tab === ref.tab && (ref.chain.length ? (m.lvl !== 'none' && Math.abs(m.delta) <= EPS) : m.st === ref.st);

  // ── discover A / C / D ────────────────────────────────────────────────
  const rows = await J(`__t.discover(${SCAN})`);
  let tri = null;   // three adjacent rows, the middle one without Interactive
  for (let i = 1; i + 1 < rows.length && !tri; i++) if (!rows[i].inter && rows[i - 1].inter && rows[i + 1].inter) tri = [rows[i - 1], rows[i], rows[i + 1]];
  let A = null, D = null, C = null;   // A, D: same experiment with Interactive; C: without
  for (let i = 0; i < rows.length && !A; i++) {
    if (!rows[i].inter) continue;
    const d = rows.find((r, j) => j !== i && r.inter && r.exp === rows[i].exp);
    const c = rows.find(r => !r.inter);
    if (d && c) { A = rows[i]; D = d; C = c; }
  }
  if (!tri || !A) { console.log('NO USABLE RUNS (need Interactive and non-Interactive runs side by side)', JSON.stringify({ tri: !!tri, A: !!A })); await p.close(); process.exit(2); }
  console.log(`runs: A ${A.id} (${A.exp})  C ${C.id} (${C.exp})  D ${D.id}  | keys: ${tri.map(r => r.id.split(':').pop()).join(' > ')}`);

  // ── click: A deep on Interactive -> C (lacks it) -> D -> A ─────────────
  await fresh();
  await clickRun(A.id); await pressTab('interactive'); await wheelTo(0.8);
  const ref = await J('__t.ref()'); await shot('click_A');
  check('click: (fixture) A is on Interactive and scrolled', ref.tab === 'interactive' && ref.st > 0, { st: ref.st, chain: ref.chain.map(c => c.k) });
  await clickRun(C.id); const mC = await J(`__t.meas(${JSON.stringify(ref)})`); await shot('click_C');
  check('click: C (no Interactive) shows Full View at its top', mC.uid === C.id && mC.tab === 'full' && mC.st === 0, mC);
  await clickRun(D.id); const mD = await J(`__t.meas(${JSON.stringify(ref)})`); await shot('click_D');
  check('click: D comes back to Interactive at the same place', mD.uid === D.id && same(mD, ref) && (ref.chain.length === 0 || mD.reach), mD);
  await clickRun(A.id); const mA = await J(`__t.meas(${JSON.stringify(ref)})`); await shot('click_A_back');
  check('click: A again: the identical place', mA.uid === A.id && same(mA, ref), Object.assign({ refSt: ref.st }, mA));

  // ── keys: ] / [ across the adjacent triple ────────────────────────────
  await fresh();
  await clickRun(tri[0].id); await pressTab('interactive'); await wheelTo(0.8);
  const kref = await J('__t.ref()'); await shot('keys_A');
  let dir = ']';
  let u = await key(dir);
  if (u !== tri[1].id) { await clickRun(tri[0].id); dir = '['; u = await key(dir); }
  const kC = await J(`__t.meas(${JSON.stringify(kref)})`); await shot('keys_C');
  check('keys: C (no Interactive) shows Full View at its top', u === tri[1].id && kC.tab === 'full' && kC.st === 0, kC);
  u = await key(dir); const kD = await J(`__t.meas(${JSON.stringify(kref)})`); await shot('keys_D');
  check('keys: D is on Interactive again (held or clamped at its end)', u === tri[2].id && kD.tab === 'interactive' &&
        (kD.lvl === 'none' || !kD.reach || Math.abs(kD.delta) <= EPS), kD);
  const back = dir === ']' ? '[' : ']';
  await key(back); u = await key(back); const kA = await J(`__t.meas(${JSON.stringify(kref)})`); await shot('keys_A_back');
  check('keys: A again: the identical place', u === tri[0].id && same(kA, kref), Object.assign({ refSt: kref.st }, kA));

  // ── pick: Full View pressed on C is the reader's choice ───────────────
  await fresh();
  await clickRun(A.id); await pressTab('interactive'); await wheelTo(0.8);
  await clickRun(C.id); await pressTab('full'); await shot('pick_C');
  await clickRun(D.id); const pD = await J('__t.ref()'); await shot('pick_D');
  check('pick: Full View pressed on C -> D lands on Full View', pD.tab === 'full', { tab: pD.tab, st: pD.st });

  // ── close, then a run opened into the empty pane: a fresh open ────────
  await fresh();
  await clickRun(A.id); await pressTab('interactive'); await wheelTo(0.8);
  const cx = await J('__t.closeXY()'); if (cx) await p.click(cx.x, cx.y); await sleep(600);
  const empty = await p.ev("!document.querySelector('#inspector-pane #ds-detail-root')");
  await clickRun(D.id); const fD = await J('__t.ref()'); await shot('close_reopen');
  check('close: a run opened after x lands on Full View at the top', !!cx && empty && fD.tab === 'full' && fD.st === 0, { closed: empty, tab: fD.tab, st: fD.st });

  // ── reload: whole ──────────────────────────────────────────────────────
  await p.send('Page.reload'); await sleep(1500); await waitRows(); await p.ev(HELPERS);
  await clickRun(C.id);
  const whole = await J(`({rows: document.querySelectorAll('#datasets-tbody tr.clickable-row').length, tab: __t.tab(), st: __t.pane().scrollTop})`);
  check('reload: the page comes back whole, a first open on Full View at the top', whole.rows > 0 && whole.tab === 'full' && whole.st === 0, whole);

  const errs = p.errors(mark0);
  if (errs.length) { fails += errs.length; console.log('JS errors:', errs.slice(0, 5)); }
  console.log(`REPORT ${JSON.stringify({ pass: rep.filter(r => r.ok).length, fail: rep.filter(r => !r.ok).length, errors: errs.length })}`);
  await p.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
