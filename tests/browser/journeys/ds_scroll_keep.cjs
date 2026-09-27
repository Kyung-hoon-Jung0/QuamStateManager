/* Queue item 6 journey: the Datasets run detail keeps the reader's place,
 * EXACTLY, across many run switches, on every tab.
 *
 * Per tab: open a run (real click on its table row) -> click the tab (real
 * click) -> wheel the detail pane down (real mouse wheel) -> then open run
 * after run (real clicks, different experiment types) and, after EACH switch,
 * measure where the reader's landmark sits:
 *
 *   ref   = the landmark chain under the READING LINE after the reader's
 *           own wheel (section -> figure / <details> / tree path / ndview
 *           block, and the line's offset inside each). The line is the
 *           bottom edge of the sticky run header (final QA 2026-09-27: the
 *           pane's top edge is hidden under it); a line in the margin
 *           between two blocks is held by the block below it.
 *   hold  = the deepest level of ref this run CAN hold (the landmark exists
 *           and is taller than the offset) -- computed here, independently
 *           of the page's own restore result
 *   PASS  = that landmark's top is exactly `within` above the reading line
 *           (|delta| <= 0.5 px: landmark rects are sub-pixel while Chrome
 *           snaps scrollTop to whole pixels at DPR 1 -- measured: 100.3 reads
 *           back 100 -- so 0.5 is the "== 0" of integer scroll) AND the page is on
 *           the same tab, measured at +60 ms (no paint at the old offset first)
 *           and again once the run's lazy content has settled.
 *
 * Every Nth switch the reader wheels a little (a new, real intent): ref is
 * re-captured, so the journey also proves a moved reader is followed.
 * Close/back/reload at the end: the page must come back whole.
 *
 * usage: SM_CDP_PORT=9409 node tests/browser/journeys/ds_scroll_keep.cjs \
 *          --base http://127.0.0.1:5109 --switches 30 --tabs full,figures,results,state,data,interactive \
 *          --shots <dir>
 * Exit 1 on any miss or JS error.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = arg('base', 'http://127.0.0.1:5099');
const SWITCHES = +arg('switches', 30);
const TABS = arg('tabs', 'full,figures,results,state,data,interactive').split(',');
const SHOTS = arg('shots', '');
const MOVE_EVERY = +arg('move-every', 11);
const GOAL = +arg('goal', 0.4);
const HEIGHT = +arg('height', 950);   // a shorter window gives short tabs (Raw Data) a range to scroll   // how far into the run the reader scrolls (fraction of the range)

const PAGE_HELPERS = `
window.__j = {
  pane: function(){ return document.getElementById('inspector-pane'); },
  uid: function(){ var r=document.querySelector('#inspector-pane #ds-detail-root'); return r ? r.dataset.uid : ''; },
  tab: function(){ var a=document.querySelector('#inspector-pane .dataset-tabs a.active[data-ds-tab]'); return a ? a.getAttribute('data-ds-tab') : null; },
  container: function(){ var p=this.pane(); var t=this.tab();
    if (!t) return null; var root=p.querySelector('#ds-detail-root');
    return ['full','overview','results','figures'].indexOf(t)>=0 ? root.querySelector('#ds-tab-combined') : root.querySelector('#ds-tab-'+t); },
  // the reader's place, as the page itself would capture it
  ref: function(){ var p=this.pane(); var a=DsScrollAnchor.capture(p, this.container()); a.tab=this.tab(); return a; },
  // independent check: walk a chain down THIS run, pick the deepest holdable
  // level, and measure where it sits against the scroller's reading line
  // (the pane: its sticky header's bottom edge on THIS run; an inner
  // scroller: its own top edge)
  one: function(sc, c, a, inner){
    var y=inner ? sc.getBoundingClientRect().top + sc.clientTop : DsScrollAnchor._lineOf(sc); var o={};
    if (!a.chain) { o.level='px'; o.delta=sc.scrollTop - a.scrollTop; o.holdable=true; o.reachable = a.scrollTop <= sc.scrollHeight - sc.clientHeight; return o; }
    var els=[c], el=c;
    for (var i=1;i<a.chain.length;i++){
      var kids=DsScrollAnchor._children(el), seen=0, f=null;
      for (var k=0;k<kids.length;k++){ if (DsScrollAnchor._keyOf(kids[k])!==a.chain[i].key) continue; if (seen===a.chain[i].n){f=kids[k];break;} seen++; }
      if (!f || f.getBoundingClientRect().height<=0) break; els.push(f); el=f; }
    var d=els.length-1;
    while (d>0 && a.chain[d].within >= els[d].getBoundingClientRect().height) d--;
    if (inner && d===0) { o.level='px'; o.delta=sc.scrollTop - a.scrollTop; o.holdable=true;
      o.reachable = a.scrollTop <= sc.scrollHeight - sc.clientHeight; o.depth=0; o.of=a.chain.length-1; return o; }
    var lvl=a.chain[d], r=els[d].getBoundingClientRect();
    o.level=lvl.key; o.depth=d; o.of=a.chain.length-1; o.holdable = lvl.within < r.height;
    var need = sc.scrollTop + (r.top - y) + lvl.within;
    o.reachable = need <= sc.scrollHeight - sc.clientHeight + 0.5 && need >= -0.5;
    o.delta = Math.round(((y - r.top) - lvl.within)*100)/100;
    return o; },
  check: function(ref){
    var p=this.pane(), c=this.container();
    var out={tab:this.tab(), st:p.scrollTop, sh:p.scrollHeight, ch:p.clientHeight};
    if (out.tab !== ref.tab) {
      var has=!!document.querySelector('#inspector-pane .dataset-tabs a[data-ds-tab="'+ref.tab+'"]:not(.disabled)');
      out.notab=!has; out.why='tab '+out.tab+' != '+ref.tab; return out; }
    var m=this.one(p, c, ref, false); for (var k in m) out[k]=m[k];
    out.inner=(ref.inner||[]).map(function(a){ var el=DsScrollAnchor._findInner(c, a.key);
      if (!el) return {key:a.key, missing:true}; var r=__j.one(el, el, a, true); r.key=a.key; return r; });
    return out; },
  settled: function(){ var p=this.pane(); if (!p || p.classList.contains('htmx-request')) return false;
    var imgs=[].slice.call(p.querySelectorAll('img')); if (!imgs.every(function(i){return i.complete;})) return false;
    var t=this.tab(), c=this.container();
    if (t==='state') { var tr=[].slice.call(c.querySelectorAll('.ds-state-tree')).filter(function(e){return e.style.display!=='none';})[0];
      if (tr && !tr.querySelector('.tree-node')) return false; }
    if (t==='interactive') { var ic=c.querySelector('#ds-interactive-container'); if (ic && /Loading/.test(ic.textContent.slice(0,200))) return false;
      var pl=[].slice.call(c.querySelectorAll('.ds-interactive-plot')); if (pl.some(function(e){return !e.querySelector('.plot-container, .js-plotly-plot, .muted, svg');})) return false; }
    if (t==='data') { var h=c.querySelector('#h5-summary-container'); if (h && /Loading/.test(h.textContent.slice(0,120))) return false; }
    return true; },
  rowXY: function(skip){ var cur=this.uid(); var r=null;
    for (var t=0;t<40 && !r;t++){ var rows=[].slice.call(document.querySelectorAll('#datasets-tbody tr.clickable-row'));
      if (skip<0){ r=rows[0]; break; }
      var ix=rows.findIndex(function(x){return x.getAttribute('data-id')===cur;});
      if (ix>=0 && rows[ix+1+skip]) { r=rows[ix+1+skip]; break; }
      if (ix>=0) rows[ix].scrollIntoView({block:'start'}); else { r=rows[0]; break; } }
    if (!r) return null; r.scrollIntoView({block:'center'}); var b=r.querySelector('[data-col-key=id]').getBoundingClientRect();
    return {x:b.left+b.width/2, y:b.top+b.height/2, exp:r.getAttribute('data-exp'), id:r.getAttribute('data-id')}; },
  tabXY: function(t){ var a=document.querySelector('#inspector-pane .dataset-tabs a[data-ds-tab="'+t+'"]:not(.disabled)'); if(!a) return null;
    var b=a.getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2}; },
  // where a wheel scrolls THE PANE: a point over the detail that is not over
  // an inner scroller (a wheel there scrolls that first) -- or, for 'inner',
  // the middle of the visible JSON tree
  wheelXY: function(mode){ var p=this.pane(), b=p.getBoundingClientRect();
    if (mode==='inner') { var c=this.container(); var t=c && [].slice.call(c.querySelectorAll('.json-tree')).filter(function(e){return e.getBoundingClientRect().height>100;})[0];
      if (t) { var r=t.getBoundingClientRect(); var top=Math.max(r.top,b.top), bot=Math.min(r.bottom,b.bottom);
        if (bot-top>40) return {x:r.left+Math.min(r.width/2,200), y:(top+bot)/2}; } }
    for (var y=b.top+120; y<b.bottom-30; y+=40) for (var x=b.left+40; x<b.right-40; x+=90) {
      var e=document.elementFromPoint(x,y); if (!e || !p.contains(e)) continue;
      if (e.closest(DsScrollAnchor.INNER_SEL) || e.closest('.js-plotly-plot')) continue; return {x:x,y:y}; }
    return {x:b.left+20, y:b.top+Math.min(b.height/2,300)}; },
  range: function(mode){ var p=this.pane();
    if (mode==='inner') { var c=this.container(); var t=c && [].slice.call(c.querySelectorAll('.json-tree')).filter(function(e){return e.getBoundingClientRect().height>100;})[0];
      return t ? {st:t.scrollTop, max:t.scrollHeight-t.clientHeight} : {st:0,max:0}; }
    var c=this.container(), cst=c ? c.getBoundingClientRect().top - p.getBoundingClientRect().top + p.scrollTop : 0;
    return {st:p.scrollTop, max:p.scrollHeight-p.clientHeight, cst:cst}; },
};`;

(async () => {
  const p = await open(BASE + '/datasets', 1600, HEIGHT);
  const mark0 = p.events.length;
  for (let i = 0; i < 90; i++) { if (await p.ev("document.querySelectorAll('#datasets-tbody tr.clickable-row').length") > 0) break; await sleep(1000); }
  await p.ev(PAGE_HELPERS);
  const J = async (expr) => { const v = await p.ev(`JSON.stringify(${expr})`); try { return JSON.parse(v); } catch (e) { throw new Error('eval: ' + v); } };
  const wheel = async (dy, mode) => { const o = await J(`__j.wheelXY(${JSON.stringify(mode || 'pane')})`);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: o.x, y: o.y, deltaX: 0, deltaY: dy }); await sleep(250); };
  const waitSettled = async (ms = 15000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await p.ev('__j.settled()')) return true; await sleep(100); } return false; };
  const openRow = async (skip) => {
    const o = await J(`__j.rowXY(${skip})`); if (!o) throw new Error('no row');
    const before = await p.ev('__j.uid()');
    await p.click(o.x, o.y);
    for (let k = 0; k < 150; k++) { const u = await p.ev('__j.uid()'); if (u && u !== before) break; await sleep(20); }
    return o;
  };

  const report = { tabs: {}, errors: [] };
  let fails = 0;
  for (const tab of TABS) {
    await openRow(-1); await sleep(300); await waitSettled();
    if (tab !== 'full') {
      await p.ev('__j.pane().scrollTop=0'); await sleep(100);
      let xy = await J(`__j.tabXY(${JSON.stringify(tab)})`);
      for (let s = 0; !xy && s < 12; s++) { await openRow(0); await waitSettled(); await p.ev('__j.pane().scrollTop=0'); xy = await J(`__j.tabXY(${JSON.stringify(tab)})`); }
      if (!xy) { console.log(`[${tab}] no run with this tab`); continue; }
      await p.click(xy.x, xy.y); await sleep(400); await waitSettled();
      const onTab = await p.ev('__j.tab()');
      if (onTab !== tab) { console.log(`[${tab}] the tab click did not take (on ${onTab})`); fails++; continue; }
      if (tab === 'state') {   // give the tree some depth to scroll in
        const d2 = await J(`(function(){var b=[].slice.call(document.querySelectorAll('#inspector-pane #ds-tab-state .tree-depth-group button')).filter(function(x){return x.textContent.trim()==='2';})[0]; if(!b) return null; var r=b.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
        if (d2) { await p.click(d2.x, d2.y); await sleep(500); }
      }
      if (tab === 'data') {    // plot the first variable: a real reader looks at a plot
        const v = await J(`(function(){var b=document.querySelector('#inspector-pane .ndv-var-card'); if(!b) return null; var r=b.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
        if (v) { await p.click(v.x, v.y); await sleep(1500); }
      }
    }
    // a run with something to scroll through: the reader reads long runs
    const mode = tab === 'state' ? 'inner' : 'pane';
    // (and still on THIS tab: a run without it lands on Full View, and a
    // reference taken there would silently test Full View under this tab's name)
    for (let k = 0; k < 25; k++) { const rg = await J(`__j.range(${JSON.stringify(mode)})`);
      if (rg.max - (rg.cst || 0) >= (tab === 'data' ? 150 : 300) && await p.ev('__j.tab()') === tab) break;
      await openRow(0); await sleep(300); await waitSettled(); }
    // the reader scrolls into the run, about half way (real wheel events)
    // (past the run header when the tab has room: the header is not the reading)
    for (let k = 0; k < 40; k++) { const rg = await J(`__j.range(${JSON.stringify(mode)})`);
      const goal = rg.cst !== undefined && rg.max > rg.cst + 100 ? rg.cst + GOAL * (rg.max - rg.cst) : rg.max * (GOAL + 0.05);
      if (rg.st >= goal) break; await wheel(120, mode); }
    await sleep(400);
    let ref = await J('__j.ref()');
    if (ref.tab !== tab) { console.log(`[${tab}] the reference was taken on ${ref.tab}, not ${tab} -- would prove nothing`); fails++; continue; }
    if (!ref.chain && !ref.scrollTop && !(ref.inner || []).length) {
      console.log(`[${tab}] the reader could not scroll at all -- a trivial reference proves nothing`); fails++; }
    const t = { switches: 0, pass: 0, miss: 0, unholdable: 0, unreachable: 0, notab: 0, flash: 0, moves: 0, innerPass: 0, rows: [] };
    console.log(`[${tab}] ref ${ref.chain ? ref.chain.map(c => c.key + '+' + Math.round(c.within)).join(' > ') : 'px ' + ref.scrollTop}`);
    for (let s = 0; s < SWITCHES; s++) {
      const o = await openRow(s % 3);
      const t0 = Date.now(); while (Date.now() - t0 < 60) await sleep(5);
      // measured as the NEXT PAINTED frame shows it (rAF -> task): a bare eval
      // can land between a layout change and the ResizeObserver delivery that
      // precedes that frame's paint, and would call an unpainted state a flash
      const early = JSON.parse(await p.ev(`new Promise(function(r){ requestAnimationFrame(function(){ setTimeout(function(){ r(JSON.stringify(__j.check(${JSON.stringify(ref)}))); }, 0); }); })`));
      await waitSettled(); await sleep(400);
      const fin = await J(`__j.check(${JSON.stringify(ref)})`);
      t.switches++;
      const EPS = 0.5 + 1e-6;
      const judge = (m) => !m.holdable ? 'SKIP-short' : !m.reachable ? 'SKIP-range' : (Math.abs(m.delta) <= EPS ? 'PASS' : 'MISS');
      let verdict;
      if (fin.why) verdict = fin.notab && fin.tab === 'full' && fin.st === 0 ? 'SKIP-notab' : 'MISS';
      else verdict = judge(fin);
      // every scroller inside the tab is judged the same way
      const innerV = (fin.inner || []).map(m => m.missing ? 'SKIP-noinner' : judge(m));
      if (verdict !== 'MISS' && innerV.includes('MISS')) verdict = 'MISS';
      if (verdict.startsWith('SKIP') && innerV.includes('PASS')) verdict = 'PASS';
      if (verdict === 'PASS') t.pass++; else if (verdict === 'MISS') t.miss++;
      else if (verdict === 'SKIP-short') t.unholdable++; else if (verdict === 'SKIP-notab') t.notab++; else t.unreachable++;
      t.innerPass += innerV.filter(v => v === 'PASS').length;
      // a paint at the old offset before the restore: only judged where the
      // final level was already laid out AND reachable at +60 ms
      if (verdict === 'PASS' && !early.why && early.level === fin.level && early.holdable && early.reachable && Math.abs(early.delta) > EPS) t.flash++;
      const inl = (fin.inner || []).map((m, k) => ` [${m.key} ${innerV[k]} ${m.level || ''} ${m.delta !== undefined ? 'delta=' + m.delta : ''}]`).join('');
      const line = `${String(s).padStart(2)} #${o.id.split(':')[1]} ${o.exp.slice(0, 30).padEnd(30)} ${verdict.padEnd(10)} ${fin.why || (fin.level + ' d' + fin.depth + '/' + fin.of + ' delta=' + fin.delta)} early=${early.why || early.delta}${inl}`;
      t.rows.push(line); console.log(line);
      if (verdict === 'MISS' && SHOTS) await p.shot(path.join(SHOTS, `miss_${tab}_${s}.png`));
      if (verdict === 'PASS' && t.pass === 1 && SHOTS) await p.shot(path.join(SHOTS, `pass_${tab}.png`));   // look at a kept place, not only the end
      if (MOVE_EVERY && s % MOVE_EVERY === MOVE_EVERY - 1 && fin.tab === tab) {   // (only on this tab)   // the reader moves: a new place to keep
        // A move is a scroll that CHANGED something (the page's own contract,
        // verifier P1): a wheel on a run clamped at its end, or at the top
        // wheeling up, scrolls nothing, and the page rightly keeps the old
        // place. Re-taking ref there judged the next tall runs against the
        // clamped landing (final QA 2026-09-27: 4 figures + 9 data "misses",
        // identical on the pre-fix build). Try the other way; if neither
        // scrolls, the reader has not moved.
        const before = await J(`__j.range(${JSON.stringify(mode)})`);
        await wheel(s % 2 ? -150 : 150, mode); await sleep(300);
        let after = await J(`__j.range(${JSON.stringify(mode)})`);
        if (after.st === before.st) { await wheel(s % 2 ? 150 : -150, mode); await sleep(300); after = await J(`__j.range(${JSON.stringify(mode)})`); }
        if (after.st !== before.st) {
          ref = await J('__j.ref()'); t.moves++;
          console.log(`   reader moved -> ${ref.chain ? ref.chain.map(c => c.key + '+' + Math.round(c.within)).join(' > ') : 'px ' + ref.scrollTop}`);
        } else console.log('   reader could not scroll on this (clamped) run -- no move, the place is kept');
      }
    }
    if (SHOTS) await p.shot(path.join(SHOTS, `end_${tab}.png`));
    fails += t.miss + t.flash;
    report.tabs[tab] = { switches: t.switches, pass: t.pass, miss: t.miss, flash: t.flash, unholdable: t.unholdable, unreachable: t.unreachable, notab: t.notab, innerPass: t.innerPass, moves: t.moves };
    // a run WITHOUT this tab (no HDF5 -> no Interactive) lands on Full View by
    // design and has nothing to measure; coverage is judged over the rest
    // (a run whose tab is too short to scroll to the place is SKIP-range: the
    // pane is at its end, the only honest landing -- not a pass, not a miss)
    if (t.pass < Math.max(10, Math.ceil((t.switches - t.notab) / 3))) { console.log(`[${tab}] too few judged switches (${t.pass}) -- the journey proves nothing here`); fails++; }
    console.log(`[${tab}] ${JSON.stringify(report.tabs[tab])}`);
  }

  // close / back / reload: the page comes back whole
  await p.key('Escape', 'Escape', 27); await sleep(300);
  await p.send('Page.reload'); await sleep(1500);
  for (let i = 0; i < 90; i++) { if (await p.ev("document.querySelectorAll('#datasets-tbody tr.clickable-row').length") > 0) break; await sleep(1000); }
  await p.ev(PAGE_HELPERS);
  await openRow(-1); await waitSettled();
  const whole = await J(`({rows: document.querySelectorAll('#datasets-tbody tr.clickable-row').length, detail: !!document.querySelector('#inspector-pane #ds-detail-root'), tab: __j.tab()})`);
  if (SHOTS) await p.shot(path.join(SHOTS, 'after_reload.png'));
  report.after_reload = whole;
  if (!whole.rows || !whole.detail || whole.tab !== 'full') fails++;
  report.errors = p.errors(mark0);
  fails += report.errors.length;
  console.log('REPORT ' + JSON.stringify(report));
  await p.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
