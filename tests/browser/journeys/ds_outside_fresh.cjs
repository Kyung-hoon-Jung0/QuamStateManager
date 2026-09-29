/* w9 uxpolish journey: a run opened from OUTSIDE the Datasets run list is a
 * FRESH open (user decision, 2026-09-28).
 *
 * Switching runs IN the list (the sidebar tree, the Datasets / Collections
 * table, ]/[, j+Enter) keeps the reader's tab and place (docs/221 sections
 * 4/8; tests/browser/journeys/ds_tab_intent.cjs). A run opened from anywhere
 * else lands on Full View at the top, even while the inspector holds a run
 * scrolled deep -- the list marks its own requests (app.js _dsListNav), so
 * every other opener is fresh by default. With real clicks in real Chrome:
 *
 *   chiptrend  A deep (Full View) in the inspector -> Chip Status -> Trends
 *              -> a point that names a run: Full View at the top
 *   list       -> back on Datasets, a table row: the list switch keeps THAT
 *              view (Full View at the top), not A's old place
 *   back       -> browser Back to Chip Status: the page comes back whole
 *   fieldhist  A deep on INTERACTIVE -> the value-history popover (the 🕘 of
 *              a value; FieldHistory.open, the inspectors' own opener) ->
 *              its "Data" button: Full View at the top
 *   colhist    A deep (Full View) -> Live State Edit -> a column's 🕘 ->
 *              "By run" -> a run's #id link: Full View at the top
 *   tree       A deep on Interactive -> a sidebar-tree run -> A through the
 *              tree, and ] then [ over the tree: the list KEEPS the place
 *   reload     the page comes back whole
 *
 * usage: SM_CDP_PORT=9661 node tests/browser/journeys/ds_outside_fresh.cjs \
 *          --base http://127.0.0.1:5361 --shots <dir> [--only chiptrend,fieldhist]
 * Exit 1 on any failure or JS error (run it against a base build to see the
 * pre-fix behaviour: each outside open keeps A's place there).
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep, base, baseFrom } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = baseFrom(arg('base', ''), 5099);   // docs/226: SM_BASE_URL (proxy + prefix) wins
const SHOTS = arg('shots', '');
const ONLY = (arg('only', '') || '').split(',').filter(Boolean);
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const want = (n) => !ONLY.length || ONLY.indexOf(n) >= 0;

const HELPERS = `
window.__t = {
  pane: function(){ return document.getElementById('inspector-pane'); },
  uid: function(){ var r=document.querySelector('#inspector-pane #ds-detail-root'); return r ? r.dataset.uid : ''; },
  tab: function(){ var a=document.querySelector('#inspector-pane .dataset-tabs a.active[data-ds-tab]'); return a ? a.getAttribute('data-ds-tab') : null; },
  at: function(){ var p=this.pane(); return {uid:this.uid(), tab:this.tab(), st:p?Math.round(p.scrollTop):null, sh:p?p.scrollHeight:null}; },
  settled: function(){ var p=this.pane(); if(!p || p.classList.contains('htmx-request')) return false;
    if (!p.querySelector('#ds-detail-root')) return false;
    var im=p.querySelectorAll('img'); for (var i=0;i<im.length;i++) if (!im[i].complete && im[i].offsetParent) return false;
    var sh=p.scrollHeight; if (this._sh!==sh){ this._sh=sh; this._t=Date.now(); return false; } return Date.now()-this._t>600; },
  scroller: function(){ return document.getElementById('table-pane'); },
  xy: function(el){ if(!el) return null; el.scrollIntoView({block:'center', inline:'center'}); var b=el.getBoundingClientRect();
    if (!b.width || !b.height) return null; var x=b.left+b.width/2, y=b.top+b.height/2; var h=document.elementFromPoint(x,y);
    return {x:x, y:y, hit:!!(h && (h===el || el.contains(h)))}; },
  rowXY: function(id){ return (async function(){ var sc=__t.scroller(); var r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]');
    if (!r) { sc.scrollTop=0; for (var k=0;k<300 && !r;k++){ sc.scrollTop += sc.clientHeight*0.7; await new Promise(function(z){setTimeout(z,80);}); r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]'); } }
    if (!r) return null; r.scrollIntoView({block:'center'}); await new Promise(function(z){setTimeout(z,250);});
    r=document.querySelector('#datasets-tbody tr.clickable-row[data-id="'+id+'"]'); if(!r) return null;
    var b=r.querySelector('[data-col-key=id]').getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2}; })(); },
  tabXY: function(t){ var a=document.querySelector('#inspector-pane .dataset-tabs a[data-ds-tab="'+t+'"]:not(.disabled)'); return a ? this.xy(a) : null; },
  wheelXY: function(){ var p=this.pane(), b=p.getBoundingClientRect();
    for (var y=b.top+150; y<b.bottom-30; y+=40) for (var x=b.left+40; x<b.right-40; x+=90) { var e=document.elementFromPoint(x,y);
      if (!e || !p.contains(e) || e.closest('.json-tree, .fig-info-col, .ds-inline-tree, .js-plotly-plot')) continue; return {x:x, y:y}; }
    return {x:b.left+40, y:b.top+300}; },
  discover: function(n){ return (async function(){ var sc=__t.scroller(); sc.scrollTop=0; var ids=[], seen={};
    for (var k=0;k<400 && ids.length<n;k++){ [].forEach.call(document.querySelectorAll('#datasets-tbody tr.clickable-row'), function(r){ var id=r.getAttribute('data-id'); if(!seen[id]){ seen[id]=1; ids.push({id:id}); } });
      sc.scrollTop += sc.clientHeight*0.7; await new Promise(function(z){setTimeout(z,60);}); }
    sc.scrollTop=0; ids=ids.slice(0,n);
    for (var i=0;i<ids.length;i++){ var t=await (await fetch('/dataset/'+ids[i].id, {headers:{'HX-Request':'true'}})).text();
      ids[i].inter = t.indexOf('data-ds-tab="interactive"')>=0; }
    return ids; })(); },
  // pixel positions of trend points (Chip Status Trends), on screen
  trendPoints: function(max){ var out=[]; var charts=document.querySelectorAll('#topo-trends .topo-trend-chart');
    for (var c=0;c<charts.length && out.length<max;c++){ var gd=charts[c]; if(!gd._fullLayout || !gd.calcdata) continue;
      gd.scrollIntoView({block:'center'}); var r=gd.getBoundingClientRect();
      for (var t=0;t<gd.calcdata.length && out.length<max;t++){ var tr=gd._fullData[t]; if(!tr || tr.visible===false || !/scatter/.test(tr.type)) continue;
        var xa=gd._fullLayout[(tr.xaxis||'x').replace('x','xaxis')], ya=gd._fullLayout[(tr.yaxis||'y').replace('y','yaxis')];
        var cd=gd.calcdata[t]; for (var i=cd.length-1;i>=0 && out.length<max;i-=Math.max(1,Math.floor(cd.length/4))){ var pt=cd[i];
          if (typeof pt.x!=='number' || typeof pt.y!=='number') continue;
          var x=r.left+xa._offset+xa.c2p(pt.x), y=r.top+ya._offset+ya.c2p(pt.y);
          if (x<0||y<0||x>innerWidth||y>innerHeight||isNaN(x)||isNaN(y)) continue; out.push({c:c, x:x, y:y}); } } }
    return out; },
};`;

(async () => {
  const p = await open(BASE + '/datasets', 1600, 950);
  const mark0 = p.events.length;
  const waitRows = async () => { for (let i = 0; i < 120; i++) { if (await p.ev("document.querySelectorAll('#datasets-tbody tr.clickable-row').length") > 0) return; await sleep(500); } };
  await waitRows(); await p.ev(HELPERS);
  const J = async (expr) => { const v = await p.ev(`Promise.resolve(${expr}).then(function(x){return JSON.stringify(x);})`); try { return JSON.parse(v); } catch (e) { throw new Error('eval: ' + String(v).slice(0, 200)); } };
  const settle = async () => { await p.ev('__t._sh=-1'); const t0 = Date.now(); while (Date.now() - t0 < 30000) { if (await p.ev('__t.settled()')) return; await sleep(100); } };
  const waitUid = async (before, ms = 12000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const u = await p.ev('__t.uid()'); if (u && u !== before) return u; await sleep(30); } return null; };
  const waitFor = async (expr, ms = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await p.ev(expr) === true) return true; await sleep(150); } return false; };
  const clickRun = async (id) => { const before = await p.ev('__t.uid()'); const xy = await J(`__t.rowXY(${JSON.stringify(id)})`); if (!xy) throw new Error('row not found ' + id);
    await p.click(xy.x, xy.y); const u = await waitUid(before); await settle(); await sleep(300); return u; };
  const pressTab = async (t) => { const xy = await J(`__t.tabXY(${JSON.stringify(t)})`); if (!xy) return false; await p.click(xy.x, xy.y); await sleep(500); await settle(); return true; };
  const wheelTo = async (frac) => { for (let k = 0; k < 120; k++) { const g = await J(`({st:__t.pane().scrollTop, max:__t.pane().scrollHeight-__t.pane().clientHeight})`);
    if (g.st >= frac * g.max) break; const xy = await J('__t.wheelXY()');
    await p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: xy.x, y: xy.y, deltaX: 0, deltaY: 120 }); await sleep(90); } await sleep(600); };
  const nav = async (href, readyExpr) => { await p.ev(`(function(){var a=document.querySelector('.sidebar a[href="${href}"], a[href="${href}"]'); if(a) a.click(); else htmx.ajax('GET','${href}',{target:'#table-pane'});})()`);
    const ok = await waitFor(readyExpr, 30000); await sleep(800); return ok; };
  const shot = async (n) => { if (SHOTS) await p.shot(path.join(SHOTS, n + '.png')); };
  let fails = 0; const rep = [];
  const check = (name, cond, info) => { rep.push({ name, ok: !!cond, info }); if (!cond) fails++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(info).slice(0, 300)}`); };
  const fresh0 = (m, before) => m.uid && m.uid !== before && m.tab === 'full' && m.st === 0;

  const rows = await J('__t.discover(40)');
  const withI = rows.filter(r => r.inter);
  if (withI.length < 2) { console.log('NO USABLE RUNS (need two with Interactive)'); await p.close(); process.exit(2); }
  const A = withI[0], B = withI[1];
  console.log(`runs: A ${A.id}  B ${B.id}`);
  // the reader: A in the inspector, on `tab`, scrolled deep (a real wheel)
  const deep = async (tab) => {
    if (!(await p.ev("!!document.querySelector('#datasets-tbody')"))) await nav('/datasets', "document.querySelectorAll('#datasets-tbody tr.clickable-row').length>0");
    await clickRun(A.id);
    await pressTab(tab);
    await wheelTo(0.6);
    const at = await J('__t.at()');
    check(`(fixture) A on ${tab}, scrolled deep`, at.uid === A.id && at.tab === tab && at.st > 150, at);
    return at;
  };

  // ── chiptrend: a Chip Status Trends point ─────────────────────────────
  if (want('chiptrend')) {
    const d = await deep('full'); await shot('chiptrend_0_A_deep');
    await nav('/topology', "!!document.querySelector('.topo-dashboard')");
    const tb = await J(`__t.xy(document.querySelector('.topo-subnav-btn[data-view="trends"]'))`);
    if (tb) await p.click(tb.x, tb.y);
    const drawn = await waitFor("[].some.call(document.querySelectorAll('#topo-trends .topo-trend-chart'), function(g){return !!(g._fullLayout && g.calcdata);})", 45000);
    await sleep(1500);
    let got = null, tried = 0;
    const pts = drawn ? await J('__t.trendPoints(40)') : [];
    for (const pt of pts) {
      tried++;
      const again = await J('__t.trendPoints(40)');   // the page may have moved
      const q = again.find(z => z.c === pt.c && Math.abs(z.x - pt.x) < 1) || pt;
      await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: q.x, y: q.y });
      await sleep(250);
      await p.click(q.x, q.y);
      got = await waitUid(d.uid, 2500);
      if (got) break;
    }
    await settle(); await sleep(500);
    const m = await J('__t.at()'); await shot('chiptrend_1_opened');
    check('chiptrend: a Trends point opens its run FRESH -- Full View at the top', fresh0(m, A.id), Object.assign({ drawn, tried }, m));
    // list: the next switch IN the list keeps that view
    await nav('/datasets', "document.querySelectorAll('#datasets-tbody tr.clickable-row').length>0");
    await clickRun(B.id);
    const mb = await J('__t.at()'); await shot('chiptrend_2_list_B');
    check('list: the next table switch keeps that view (Full View at the top)', mb.uid === B.id && mb.tab === 'full' && mb.st === 0, mb);
    // back: Back to Chip Status -- the page comes back whole (the inspector is
    // emptied by htmx's history restore, as on base 91c8aae; not this change)
    await p.ev('history.back()');
    const backOk = await waitFor("(window.SM?window.SM.path(location.pathname):location.pathname)==='/topology' && !!document.querySelector('.topo-dashboard')", 30000);
    await sleep(1200); await shot('chiptrend_3_back');
    check('back: Back to Chip Status comes back whole', backOk, { path: await p.ev('location.pathname') });
  }

  // ── fieldhist: the value-history popover's "Data" ─────────────────────
  if (want('fieldhist')) {
    const d = await deep('interactive'); await shot('fieldhist_0_A_deep');
    await p.ev(`window.FieldHistory.open(document.querySelector('#table-pane h2, #table-pane') , 'qubits.q1.f_01', null)`);
    const has = await waitFor("!!document.querySelector('#field-history-panel .fh-data')", 20000);
    let m = null;
    if (has) {
      const xy = await J(`__t.xy(document.querySelector('#field-history-panel .fh-data'))`);
      await p.click(xy.x, xy.y);
      await waitUid(d.uid); await settle(); await sleep(500);
      m = await J('__t.at()');
    }
    await shot('fieldhist_1_opened');
    check('fieldhist: the value-history "Data" opens its run FRESH -- Full View at the top (A was on Interactive)', has && fresh0(m, A.id), Object.assign({ has }, m || {}));
    await p.key('Escape', 'Escape', 27); await sleep(300);
  }

  // ── colhist: Live State Edit -> a column's 🕘 -> By run -> #id ─────────
  if (want('colhist')) {
    const d = await deep('full'); await shot('colhist_0_A_deep');
    await nav('/bulk', "!!document.querySelector('.bulk-col-hist')");
    const hb = await J(`(function(){var bs=[].slice.call(document.querySelectorAll('.bulk-col-hist[data-grid="qubit"]'));
      var b=bs.find(function(x){var th=x.closest('th'); return th && /f_01|frequency/i.test(th.textContent);}) || bs[0]; return __t.xy(b);})()`);
    if (hb) await p.click(hb.x, hb.y);
    const ov = await waitFor("!!document.querySelector('.colhist-overlay .ch-run-open')", 30000);
    let m = null;
    if (ov) {
      const vb = await J(`(function(){var b=[].slice.call(document.querySelectorAll('.colhist-overlay button')).find(function(x){return /by run/i.test(x.textContent);}); return b? __t.xy(b) : null;})()`);
      if (vb) { await p.click(vb.x, vb.y); await sleep(500); }
      // a run OTHER than A (the newest run is often A itself)
      const rx = await J(`(function(){var a=[].slice.call(document.querySelectorAll('.colhist-overlay .ch-run-open')).find(function(x){return x.offsetParent && x.getAttribute('hx-get')!=='/dataset/${A.id}';}); return a? __t.xy(a) : null;})()`);
      if (rx) { await p.click(rx.x, rx.y); await waitUid(d.uid); await settle(); await sleep(500); m = await J('__t.at()'); }
    }
    await shot('colhist_1_opened');
    check('colhist: a Column History run link opens its run FRESH -- Full View at the top', ov && m && fresh0(m, A.id), Object.assign({ ov }, m || {}));
    // the SAME run A, opened from outside while A sits deep: fresh too
    await p.key('Escape', 'Escape', 27); await sleep(300);
    const d2 = await deep('full');
    await p.ev(`window.FieldHistory.open(document.querySelector('#table-pane'), 'qubits.q1.f_01', null)`);
    await waitFor("!!document.querySelector('#field-history-panel .fh-data')", 20000);   // loaded (it replaces its body once)
    // the same run through a link of its own (an hx-get the popover's htmx.process wires)
    const same = await J(`(function(){var pn=document.getElementById('field-history-panel'); var b=document.createElement('button'); b.type='button'; b.className='fh-data'; b.id='__same';
      b.setAttribute('hx-get','/dataset/${A.id}'); b.setAttribute('hx-target','#inspector-pane'); b.setAttribute('hx-swap','innerHTML'); b.textContent='Data (same run)';
      pn.appendChild(b); htmx.process(b); return __t.xy(b);})()`);
    if (same) { await p.click(same.x, same.y); await sleep(2500); await settle(); }
    const ms = await J('__t.at()'); await shot('colhist_2_same_run');
    check('same run: A opened from outside while A sits deep -> Full View at the top', !!same && ms.uid === A.id && ms.tab === 'full' && ms.st === 0 && d2.st > 150, ms);
    await p.key('Escape', 'Escape', 27); await sleep(300);
  }

  // ── tree: the sidebar run tree and ]/[ are the LIST -- they keep ──────
  if (want('tree')) {
    const d = await deep('interactive'); await shot('tree_0_A_deep');
    const treeClick = async (uid) => {
      const before = await p.ev('__t.uid()');
      const xy = await J(`(function(){var e=document.querySelector('.tree-entry-click[data-uid="${uid}"]'); if(!e) return null;
        var dd=e.closest('details:not([open])'); while(dd){ dd.open=true; dd=dd.parentElement?dd.parentElement.closest('details:not([open])'):null; } return __t.xy(e);})()`);
      if (!xy) return null;
      await p.click(xy.x, xy.y); const u = await waitUid(before); await settle(); await sleep(300); return u;
    };
    // the tree's date groups load on open: open the one(s) holding A and B
    const date = await p.ev("(document.querySelector('#inspector-pane #ds-detail-root')||{dataset:{}}).dataset.date || ''");
    await p.ev(`[].forEach.call(document.querySelectorAll('details.tree-date'), function(d){ var s=d.querySelector(':scope > summary');
      if (s && ${JSON.stringify(date)} && s.textContent.trim().indexOf(${JSON.stringify(date)})===0 && !d.open) d.open=true; })`);
    const hasTree = await waitFor(`!!document.querySelector('.tree-entry-click[data-uid="${B.id}"]') && !!document.querySelector('.tree-entry-click[data-uid="${A.id}"]')`, 20000);
    let mB = null, mA = null, mK = null, uK = null;
    if (hasTree) {
      await treeClick(B.id); mB = await J('__t.at()'); await shot('tree_1_B');
      await treeClick(A.id); mA = await J('__t.at()'); await shot('tree_2_A_back');
      // ] then [ walk the TREE (the run was opened from it): back on A, the identical place
      const KEYS = { ']': [']', 'BracketRight', 221], '[': ['[', 'BracketLeft', 219] };
      const key = async (k) => { const before = await p.ev('__t.uid()'); await p.ev('document.activeElement && document.activeElement.blur && document.activeElement.blur()');
        const [kk, code, vk] = KEYS[k]; await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: kk, code, windowsVirtualKeyCode: vk, text: kk });
        await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: kk, code, windowsVirtualKeyCode: vk });
        const u = await waitUid(before); await settle(); await sleep(300); return u; };
      const u1 = await key(']'); uK = u1 ? await key('[') : null;
      if (uK !== A.id && u1) { uK = await key(']'); uK = uK ? await key('[') : uK; }
      mK = await J('__t.at()'); await shot('tree_3_keys_back');
    }
    check('tree: a sidebar-tree switch keeps Interactive (a LIST switch, not a fresh open)', hasTree && mB && mB.uid === B.id && mB.tab === 'interactive', mB || { hasTree });
    check('tree: back on A through the tree -- the identical place', hasTree && mA && mA.uid === A.id && mA.tab === 'interactive' && mA.st === d.st, Object.assign({ ref: d.st }, mA || {}));
    check(']/[ over the tree: back on A -- the identical place', hasTree && mK && mK.uid === A.id && mK.tab === 'interactive' && mK.st === d.st, Object.assign({ ref: d.st, uK }, mK || {}));
  }

  // ── reload: whole ─────────────────────────────────────────────────────
  await p.send('Page.reload'); await sleep(1500);
  await waitFor("document.readyState==='complete' && !!document.getElementById('table-pane')", 30000);
  await sleep(1500);
  const whole = await p.ev("JSON.stringify({pane: !!document.getElementById('table-pane'), insp: !!document.getElementById('inspector-pane'), body: document.body.innerText.length})");
  const w = JSON.parse(whole); await shot('reload');
  check('reload: the page comes back whole', w.pane && w.insp && w.body > 200, w);

  // htmx logs htmx:historyCacheError when the Back step's page snapshot does
  // not fit localStorage (a big Chip Status page) -- identical on base 91c8aae,
  // not a defect of what this journey checks
  const errs = p.errors(mark0).filter(e => e !== 'CON htmx:historyCacheError');
  if (errs.length) { fails += errs.length; console.log('JS errors:', errs.slice(0, 5)); }
  console.log(`REPORT ${JSON.stringify({ pass: rep.filter(r => r.ok).length, fail: rep.filter(r => !r.ok).length, errors: errs.length })}`);
  await p.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
