/* Chip Status queue items 4 + 10 in real Chrome: panel metadata on hover, the
 * per-panel "Show Meta Info" toggle (persisted), and Overview tiles that jump
 * to their panel. Every scenario ends where a person ends: after a reload /
 * a round trip, the page is still intact.
 *   node tests/browser/journeys/chip_status_meta.cjs [port] [shotdir]
 * Needs Chrome with --remote-debugging-port (SM_CDP_PORT) and SM on <port>
 * with a chip loaded that has history snapshots. Exit 1 on any FAIL.
 */
'use strict';
const { open } = require('./cdp.cjs');
const PORT = process.argv[2] || '5099';
const SHOTS = process.argv[3] || '.';
const BASE = `http://127.0.0.1:${PORT}`;
// a big chip (big30x) needs longer before its page settles and its first
// metadata arrives (docs: the live-diff read stalls the whole server ~9 s)
const SETTLE = Number(process.env.SM_SETTLE || 6000);
const LAND = 67;
// how long the FIRST metadata may take to arrive (big30x: the server is busy
// ~20-30 s after a page open, see docs); polled, so a fast chip pays nothing
const META_MAX = Number(process.env.SM_META_MAX || 2500);
async function until(p, expr, ok, maxMs) {
  let v = null;
  for (let t = 0; ; t += 250) {
    v = await p.ev(expr);
    if (ok(v) || t >= maxMs) return v;
    await p.sleep(250);
  }
}   // px from the pane top a jumped panel lands at (1600x950, measured)
const res = [];
function rec(name, ok, detail) { res.push({ name, ok }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 500) : '')); }

async function center(p, sel, block) {
  // a person SCROLLS to what they point at: the wheel is the intent the jump
  // guard / F5 restore yield to (a bare script scroll is not, and a guard
  // still re-landing its last jump would move the target from under the press)
  return JSON.parse(await p.ev(`(function(){ var b=document.querySelector(${JSON.stringify(sel)}); if(!b) return 'null';
    var pn=document.getElementById('table-pane'); if (pn) pn.dispatchEvent(new WheelEvent('wheel', {bubbles:true, deltaY:1}));
    b.scrollIntoView({block:${JSON.stringify(block || 'center')}}); var r=b.getBoundingClientRect();
    return JSON.stringify({x:Math.round(r.left+Math.min(r.width/2,40)),y:Math.round(r.top+Math.min(r.height/2,12))}); })()`));
}
const hover = (p, x, y) => p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });

(async () => {
  // ---- M1: hover a T1 tile -> card with metadata; hover the title -> summary
  {
    const p = await open(`${BASE}/topology?view=coherence`); await p.sleep(SETTLE);
    await p.ev(`localStorage.removeItem('quam_chip_meta_panels'), 1`);
    const c = await center(p, '.topo-section[data-density-panel="f_01"] .heatmap-cell[data-qubit]');
    rec('M1a an f_01 tile exists', !!c);
    if (c) {
      await p.sleep(400); await hover(p, c.x, c.y); await p.sleep(400);
      const card = await until(p, `(function(){var e=document.getElementById('cs-meta-pop'); return e? e.innerText : null;})()`,
                               v => !!v && !/Loading when/.test(v), META_MAX);
      // S10 C5: old card lines only -> also "First recorded" (a value that appeared then, not a change)
      rec('M1b hovering a tile shows the metadata card', !!card && /(Last measured|Last changed|First recorded|Unchanged since|No change|Run recorded|Not in this chip|Not dated)/.test(card), card);
      const parked = await p.ev(`(function(){var c=document.querySelector('.topo-section[data-density-panel="f_01"] .heatmap-cell[data-meta-title]'); return !!c && !c.hasAttribute('title');})()`);
      rec('M1c the native tooltip is parked while the card is up (no double tooltip)', parked);
      await p.shot(`${SHOTS}/m1_tile_hover.png`);
      await hover(p, 5, 5); await p.sleep(300);
      const gone = await p.ev(`!document.getElementById('cs-meta-pop') && !!document.querySelector('.topo-section[data-density-panel="f_01"] .heatmap-cell[title]')`);
      rec('M1d leaving hides the card and puts the tooltip back', gone);
      const t = await center(p, '.topo-section[data-density-panel="f_01"] .topo-metric-panel-title .metric-label');
      await hover(p, t.x, t.y); await p.sleep(300);
      const sum = await until(p, `(function(){var e=document.getElementById('cs-meta-pop'); return e? e.innerText : null;})()`,
                              v => !!v && /history:|no history/.test(v), META_MAX);
      rec('M1e hovering the panel title shows the panel summary', !!sum && /history:|no history/.test(sum), sum);
      await p.shot(`${SHOTS}/m1_title_hover.png`);
    }
    rec('M1f no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M2: the toggle -> meta in the panel -> reload -> still on; off again
  {
    const p = await open(`${BASE}/topology?view=frequencies`); await p.sleep(SETTLE);
    const cb = await center(p, '.topo-section[data-density-panel="f_01"] .topo-meta-cb');
    rec('M2a the Show Meta Info toggle sits in the f_01 panel title', !!cb);
    const order = await p.ev(`(function(){var h=document.querySelector('.topo-section[data-density-panel="f_01"] .topo-metric-panel-title'); var d=h.querySelector('.topo-density-ctl'), t=h.querySelector('.topo-meta-toggle'); if(!d||!t) return false; return t.getBoundingClientRect().left > d.getBoundingClientRect().right - 1 && /Show Meta Info/.test(t.textContent);})()`);
    rec('M2b ...right of S / M / L, labelled "Show Meta Info"', order);
    if (cb) {
      await p.click(cb.x, cb.y); await p.sleep(300);
      await until(p, `!!document.querySelector('.topo-section[data-density-panel="f_01"] .topo-metric-panel-meta')`, v => v, META_MAX);
      const st = JSON.parse(await p.ev(`JSON.stringify((function(){var s=document.querySelector('.topo-section[data-density-panel="f_01"]'); var m=s.querySelector('.topo-metric-panel-meta'); var cells=[].slice.call(s.querySelectorAll('.heatmap-cell-meta')).filter(function(x){return x.offsetParent && x.textContent;});
        return {on:s.classList.contains('topo-meta-on'), line:m&&m.offsetParent?m.textContent:null, cells:cells.length, sample:cells[0]&&cells[0].textContent, store:localStorage.getItem('quam_chip_meta_panels'), other:document.querySelector('.topo-section[data-density-panel="T1"]').classList.contains('topo-meta-on')};})())`));
      rec('M2c the panel shows its summary line and a meta line per tile', st.on && !!st.line && st.cells > 0, st);
      rec('M2d only THIS panel turned on', st.other === false, st);
      await center(p, '.topo-section[data-density-panel="f_01"]', 'start'); await p.sleep(300);
      await p.shot(`${SHOTS}/m2_meta_on.png`);
      await p.send('Page.reload'); await p.sleep(SETTLE + 1000);
      await p.ev(`window.setChipStatusView && window.setChipStatusView('frequencies')`); await p.sleep(1000);
      await until(p, `[].slice.call(document.querySelectorAll('.topo-section[data-density-panel="f_01"] .heatmap-cell-meta')).some(function(x){return x.offsetParent && x.textContent;})`, v => v, META_MAX);
      const after = JSON.parse(await p.ev(`JSON.stringify((function(){var s=document.querySelector('.topo-section[data-density-panel="f_01"]'); if(!s) return null; var cb=s.querySelector('.topo-meta-cb'); var cells=[].slice.call(s.querySelectorAll('.heatmap-cell-meta')).filter(function(x){return x.offsetParent && x.textContent;});
        return {on:s.classList.contains('topo-meta-on'), checked:cb&&cb.checked, cells:cells.length};})())`));
      rec('M2e after a reload the toggle is still on and the meta loads by itself', after && after.on && after.checked && after.cells > 0, after);
      await p.shot(`${SHOTS}/m2_after_reload.png`);
      const cb2 = await center(p, '.topo-section[data-density-panel="f_01"] .topo-meta-cb');
      await p.click(cb2.x, cb2.y); await p.sleep(500);
      const off = JSON.parse(await p.ev(`JSON.stringify((function(){var s=document.querySelector('.topo-section[data-density-panel="f_01"]'); var m=s.querySelector('.topo-metric-panel-meta'); return {on:s.classList.contains('topo-meta-on'), lineShown:!!(m&&m.offsetParent), store:localStorage.getItem('quam_chip_meta_panels')};})())`));
      rec('M2f switching it off hides the metadata and forgets the choice', !off.on && !off.lineShown && off.store === null, off);
    }
    rec('M2g no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M3: Overview tile -> its panel, still there after the lazy sections land; inert tiles stay put
  {
    const p = await open(`${BASE}/topology?view=overview`); await p.sleep(SETTLE);
    const pane = `document.getElementById('table-pane')`;
    const tiles = JSON.parse(await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#topo-overview-tiles .topo-card[data-tile-id]')).map(function(t){return {id:t.getAttribute('data-tile-id'), jump:t.getAttribute('data-tile-jump'), cursor:getComputedStyle(t).cursor, title:t.getAttribute('title')};}))`));
    rec('M3a tiles with a section carry a jump target + pointer cursor', tiles.filter(t => t.jump).every(t => t.cursor === 'pointer') && tiles.some(t => t.id === 't1' && t.jump), tiles.map(t => t.id + ':' + (t.jump ? 'J' : '-')));
    const inert = tiles.filter(t => t.id === 'cal_age' || t.id === 'gate2q_len');
    rec('M3b Calibration Age / 2Q Gate Length stay inert', inert.every(t => !t.jump), inert);
    for (const [tid, sel] of [['t1', '.topo-section[data-density-panel="T1"]'], ['ro_ge', '.topo-section[data-density-panel="assignment_fidelity"]'], ['irb', '[data-rb-heading="InterleavedRB"]']]) {
      if (!tiles.some(t => t.id === tid && t.jump)) { rec(`M3c ${tid} tile present`, false); continue; }
      await p.ev(`${pane}.scrollTop=0, 1`); await p.sleep(600);
      const c = await center(p, `#topo-overview-tiles .topo-card[data-tile-id="${tid}"]`);
      await hover(p, c.x, c.y); await p.sleep(500);
      const hint = await p.ev(`(function(){var e=document.getElementById('ov-hover-pop'); return e? e.innerText : document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="${tid}"]').getAttribute('title');})()`);
      await p.click(c.x, c.y); await p.sleep(9000);   // the lazy Trends / 2Q panels land meanwhile
      const pos = JSON.parse(await p.ev(`JSON.stringify((function(){var el=document.querySelector(${JSON.stringify(sel)}); var pr=${pane}.getBoundingClientRect(); if(!el) return null; var r=el.getBoundingClientRect(); return {top:Math.round(r.top-pr.top), tab:(document.querySelector('.topo-subnav-btn.active')||{}).getAttribute ? document.querySelector('.topo-subnav-btn.active').getAttribute('data-view') : null};})())`));
      rec(`M3c clicking ${tid} lands its panel at the top of the pane (and it stays)`, !!pos && Math.abs(pos.top - LAND) <= 10, { hint, pos });
      await p.shot(`${SHOTS}/m3_jump_${tid}.png`);
    }
    // inert tile: a click moves nothing
    await p.ev(`${pane}.scrollTop=0, 1`); await p.sleep(500);
    const ca = await center(p, '#topo-overview-tiles .topo-card[data-tile-id="cal_age"]');
    if (ca) {
      const before = await p.ev(`${pane}.scrollTop`);
      await p.click(ca.x, ca.y); await p.sleep(1500);
      const after = await p.ev(`${pane}.scrollTop`);
      rec('M3d an inert tile click moves nothing', before === after, { before, after });
    }
    // keyboard -- on a FRESH page, so the lazy sections still grow under the
    // jump (the case where the jump key used to cancel its own re-anchoring)
    await p.send('Page.reload'); await p.sleep(SETTLE + 1000);
    await p.ev(`${pane}.scrollTop=0, 1`); await p.sleep(400);
    await p.ev(`document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="t2ramsey"]').focus(), 1`);
    await p.key('Enter', 'Enter', 13); await p.sleep(SETTLE);
    const kpos = await p.ev(`(function(){var el=document.querySelector('.topo-section[data-density-panel="T2ramsey"]'); if(!el) return null; return Math.round(el.getBoundingClientRect().top-${pane}.getBoundingClientRect().top);})()`);
    rec('M3e Enter on a focused tile lands where a click does', kpos !== null && Math.abs(kpos - LAND) <= 10, kpos);
    // round trip: F5 -> page intact
    await p.send('Page.reload'); await p.sleep(SETTLE + 1000);
    const intact = await p.ev(`!!document.querySelector('#topo-overview-tiles .topo-card[data-tile-jump]') && !!document.querySelector('.topo-dashboard')`);
    rec('M3f after a reload the page and its jump tiles are intact', intact);
    rec('M3g no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M5 (verifier P1 x2): Readout Fidelity with its meta on. Every tile
  //      equals a cold recompute, an entry is "first" only AT the oldest
  //      snapshot, and a confusion matrix history never held says so.
  {
    const p = await open(`${BASE}/topology?view=readout`); await p.sleep(SETTLE);
    await p.ev(`localStorage.setItem('quam_chip_meta_panels', JSON.stringify({assignment_fidelity:true})), 1`);
    await p.send('Page.reload'); await p.sleep(SETTLE + 1000);
    await p.ev(`window.setChipStatusView && window.setChipStatusView('readout')`); await p.sleep(4000);
    const r = JSON.parse(await p.ev(`(async function(){
      var d = await (await fetch('/topology/metric-meta', {cache:'no-store'})).json();
      var MI = window.ChipStatus.metaInfo, cells = [], badFirst = [];
      ['q','p'].forEach(function(g){ Object.keys(d[g]||{}).forEach(function(k){ Object.keys(d[g][k]).forEach(function(id){
        var e = d[g][k][id]; if (e.first && e.ts !== d.oldest) badFirst.push([k,id,e.ts]); }); }); });
      document.querySelectorAll('.topo-section[data-density-panel="assignment_fidelity"] .heatmap-cell[data-qubit]').forEach(function(c){
        if (c.classList.contains('heatmap-cell-none')) return;
        var q = c.getAttribute('data-qubit'), e = ((d.q||{}).assignment_fidelity||{})[q];
        cells.push({q:q, shown:(c.querySelector('.heatmap-cell-meta')||{}).textContent, mc: e ? e.matches_current : null,
                    first: e ? e.first : null, appeared: e ? !!e.appeared : null});
      });
      return JSON.stringify({oldest:d.oldest, badFirst:badFirst, cells:cells}); })()`));
    rec('M5a no entry is "since history began" unless it sits at the oldest snapshot', !!r.oldest && r.badFirst.length === 0, { oldest: r.oldest, badFirst: r.badFirst.slice(0, 6) });
    rec('M5b a readout tile whose matrix history never held says "not in history"; none borrows a \u2264 date for it',
        r.cells.length > 0 && r.cells.every(c => c.mc !== false || c.shown === 'not in history')
          && r.cells.every(c => !(c.appeared && /^\u2264/.test(c.shown || ''))), r.cells);
    const c = await center(p, '.topo-section[data-density-panel="assignment_fidelity"] .heatmap-cell[data-qubit="q1"]');
    if (c) {
      await p.sleep(300); await hover(p, c.x, c.y); await p.sleep(1500);
      const card = await p.ev(`(function(){var e=document.getElementById('cs-meta-pop'); return e? e.innerText : null;})()`);
      const q1 = r.cells.find(x => x.q === 'q1') || {};
      rec('M5c the q1 hover card agrees with its entry (no "Unchanged since" for an appeared or edited value)',
          !!card && !((q1.appeared || q1.mc === false) && /Unchanged since/.test(card))
            && (q1.mc !== false || /Not in this chip/.test(card)), { card, q1 });
      await p.shot(`${SHOTS}/m5_ro_q1_hover.png`);
    }
    await hover(p, 5, 5);
    await p.send('Page.reload'); await p.sleep(SETTLE);
    const intact = await p.ev(`!!document.querySelector('.topo-dashboard') && !!document.querySelector('.topo-section[data-density-panel="assignment_fidelity"]')`);
    rec('M5d after a reload the page is intact', intact);
    await p.ev(`localStorage.removeItem('quam_chip_meta_panels'), 1`);
    rec('M5e no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M6 (verifier D1, 2026-09-27): Show Meta Info ON for EVERY panel ->
  //      every panel's summary line and every filled tile line is VISIBLE
  //      (laid-out height > 0), nested panels (2Q RB / 1Q fid / readout)
  //      included; then a reload keeps them visible. Text alone proved nothing.
  {
    const p = await open(`${BASE}/topology?view=overview`); await p.sleep(SETTLE);
    // the panels are built lazily: visit every tab that holds them first
    const buildAll = async () => {
      for (const v of ['fidelity2q', 'fidelity1q', 'readout', 'coherence', 'frequencies', 'calibration']) {
        await p.ev(`window.setChipStatusView && window.setChipStatusView(${JSON.stringify(v)}), 1`); await p.sleep(1500);
      }
    };
    await buildAll();
    const measure = `(async function(){
      var secs = [].slice.call(document.querySelectorAll('.topo-section[data-density-panel]'));
      var bad = [], nested = 0, seen = 0;
      for (var i = 0; i < secs.length; i++) {
        var s = secs[i], k = s.getAttribute('data-density-panel');
        s.scrollIntoView({block:'start'}); await new Promise(function(r){setTimeout(r,120);});
        seen++;
        if (s.parentElement && s.parentElement.closest('.topo-section')) nested++;
        var line = s.querySelector('.topo-metric-panel-meta');
        var tags = [].slice.call(s.querySelectorAll('.heatmap-cell-meta')).filter(function(t){ return t.textContent; });
        var hidden = tags.filter(function(t){ return t.getBoundingClientRect().height <= 0; }).length;
        if (!s.classList.contains('topo-meta-on') || !line || line.getBoundingClientRect().height <= 0 || (!tags.length && s.querySelector('.heatmap-cell:not(.heatmap-cell-none)')) || hidden)
          bad.push({k:k, on:s.classList.contains('topo-meta-on'), line: line ? line.getBoundingClientRect().height : null, tags:tags.length, hidden:hidden});
      }
      return JSON.stringify({seen:seen, nested:nested, bad:bad}); })()`;
    await p.ev(`(function(){ var o={}; document.querySelectorAll('.topo-section[data-density-panel]').forEach(function(s){ o[s.getAttribute('data-density-panel')]=true; }); localStorage.setItem('quam_chip_meta_panels', JSON.stringify(o)); return 1; })()`);
    await p.send('Page.reload'); await p.sleep(SETTLE);
    await buildAll(); await p.sleep(Number(process.env.SM_META_WAIT || 4000));
    const m = JSON.parse(await p.ev(measure));
    rec('M6a every panel with Show Meta Info on shows its line and its tile lines (nested panels too)',
        m.seen > 0 && m.nested > 0 && m.bad.length === 0, { seen: m.seen, nested: m.nested, bad: m.bad.slice(0, 8) });
    const ro = await center(p, '.topo-section[data-density-panel="assignment_fidelity"]', 'start');
    if (ro) { await p.sleep(400); await p.shot(`${SHOTS}/m6_readout_meta_on.png`); }
    const rb = await center(p, '#sec-fidelity .topo-section[data-density-panel]', 'start');
    if (rb) { await p.sleep(400); await p.shot(`${SHOTS}/m6_rb_meta_on.png`); }
    await p.ev(`localStorage.removeItem('quam_chip_meta_panels'), 1`);
    await p.send('Page.reload'); await p.sleep(SETTLE);
    await buildAll();
    const off = await p.ev(`[].slice.call(document.querySelectorAll('.topo-section[data-density-panel] .heatmap-cell-meta, .topo-section[data-density-panel] .topo-metric-panel-meta')).filter(function(t){return t.getBoundingClientRect().height>0;}).length`);
    rec('M6b with every panel off again no meta line is laid out', off === 0, off);
    rec('M6c no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M7 (verifier D3, 2026-09-27): after a keyboard jump and an F5 the
  //      page restores the deep place and keeps re-landing it while the lazy
  //      sections grow. A person gets back to the tiles with the WHEEL (which
  //      ends the restore) and CLICKS one: it must land like a key press does.
  //      (The verifier's harness set scrollTop=0 by script, which is no user
  //      intent: the restore re-landed the pane before the click, and the click
  //      hit the 1Q panel under the pointer, not the tile -- traced.)
  {
    const p = await open(`${BASE}/topology?view=overview`); await p.sleep(SETTLE);
    const tileSel = (id) => `#topo-overview-tiles .topo-card[data-tile-id="${id}"]`;
    const pos = async (sel) => JSON.parse(await p.ev(`(function(){var el=document.querySelector(${JSON.stringify(sel)}), pn=document.getElementById('table-pane');
      return JSON.stringify({top: el ? Math.round(el.getBoundingClientRect().top - pn.getBoundingClientRect().top) : null,
        tab: (document.querySelector('.topo-subnav-btn.active')||{getAttribute:function(){return null;}}).getAttribute('data-view')});})()`));
    const RO = '.topo-section[data-density-panel="assignment_fidelity"]';
    for (const how of ['click', 'enter']) {
      await p.ev(`document.getElementById('table-pane').scrollTop = 0, 1`); await p.sleep(300);
      await p.ev(`document.querySelector(${JSON.stringify(tileSel('gate1q'))}).focus(), 1`);
      await p.key('Enter', 'Enter', 13); await p.sleep(Number(process.env.SM_JUMP_WAIT || 9000));
      await p.send('Page.reload'); await p.sleep(SETTLE);
      const deep = await p.ev(`document.getElementById('table-pane').scrollTop`);
      for (let k = 0; k < 60; k++) {
        await p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: 900, y: 500, deltaX: 0, deltaY: -20000 });
        await p.sleep(60);
        if (await p.ev(`document.getElementById('table-pane').scrollTop`) <= 0) break;
      }
      await p.sleep(800);
      if (how === 'click') {
        // where the tile IS after the wheel -- no script scroll before the press
        const c = JSON.parse(await p.ev(`(function(){var r=document.querySelector(${JSON.stringify(tileSel('ro_ge'))}).getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+Math.min(r.width/2,40)), y:Math.round(r.top+Math.min(r.height/2,12))});})()`));
        await p.click(c.x, c.y);
      } else {
        await p.ev(`document.querySelector(${JSON.stringify(tileSel('ro_ge'))}).focus(), 1`);
        await p.key('Enter', 'Enter', 13);
      }
      await p.sleep(Number(process.env.SM_JUMP_WAIT || 9000));
      const at = await pos(RO);
      rec(`M7${how === 'click' ? 'a' : 'b'} after Enter-jump + F5 + wheel back, a ${how} on the Readout (GE) tile lands it at the top on its tab`,
          at.top !== null && Math.abs(at.top - LAND) <= 3 && at.tab === 'readout', { deep, at });
    }
    rec('M7c no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  // ---- M4: an outside program writes q1.T1 while the panel shows its meta ->
  //      what the page shows must equal a COLD recompute from a fresh fetch
  if (process.env.SM_CHIP_DIR) {
    const { execFileSync } = require('child_process');
    const p = await open(`${BASE}/topology?view=coherence`); await p.sleep(SETTLE);
    await p.ev(`localStorage.setItem('quam_chip_meta_panels', JSON.stringify({T1:true})), 1`);
    await p.send('Page.reload'); await p.sleep(SETTLE + 1000);
    await p.ev(`window.setChipStatusView && window.setChipStatusView('coherence')`); await p.sleep(3000);
    const v0 = await p.ev(`(document.querySelector('.topo-section[data-density-panel="T1"] .heatmap-cell[data-qubit="q1"] .heatmap-cell-value')||{}).textContent`);
    const old = parseFloat(execFileSync('python', [process.env.SM_EXT_WRITER, 'get', process.env.SM_CHIP_DIR, 'qubits.q1.T1']).toString());
    // never the value already on screen (the page may hold an earlier take)
    const nv = +(parseFloat(v0) * 1e-6 * (1.2 + Math.random() * 0.3)).toPrecision(8);
    execFileSync('python', [process.env.SM_EXT_WRITER, 'set', process.env.SM_CHIP_DIR, 'qubits.q1.T1', String(nv), '--mode', 'quam']);
    // SM never swaps what you look at (docs/87): the outside write raises the
    // drift pill; the person presses its own "Take live"
    let tl = null;
    for (let i = 0; i < 15 && !tl; i++) {
      await p.sleep(2000);
      tl = JSON.parse(await p.ev(`(function(){var b=[].slice.call(document.querySelectorAll('button,a')).filter(function(x){return x.offsetParent && /Take live/.test(x.textContent);})[0]; if(!b) return 'null'; var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()`));
    }
    rec('M4-0 the drift pill offers Take live', !!tl);
    if (tl) await p.click(tl.x, tl.y);
    let v1 = v0;
    for (let i = 0; i < 30 * SETTLE / 6000 && v1 === v0; i++) {
      await p.sleep(2000);
      v1 = await p.ev(`(document.querySelector('.topo-section[data-density-panel="T1"] .heatmap-cell[data-qubit="q1"] .heatmap-cell-value')||{}).textContent`);
    }
    rec('M4a the outside write reaches the T1 panel', v1 !== v0, { v0, v1, nv });
    await p.ev(`window.setChipStatusView && window.setChipStatusView('coherence')`); await p.sleep(4000);
    // the page's shown tags vs a cold recompute from a fresh /topology/metric-meta
    const cmp = JSON.parse(await p.ev(`(async function(){
      var d = await (await fetch('/topology/metric-meta', {cache:'no-store'})).json();
      var topo = await (await fetch('/api/topology', {cache:'no-store'})).json();
      var MI = window.ChipStatus.metaInfo, out = [];
      document.querySelectorAll('.topo-section[data-density-panel="T1"] .heatmap-cell[data-qubit]').forEach(function(c){
        var q = c.getAttribute('data-qubit'); if (c.classList.contains('heatmap-cell-none')) return;
        var n = (topo.nodes||[]).filter(function(x){return x.id===q;})[0] || {};
        var rec = (n.metrics||{}).T1 || {};
        var cold = MI.describe(((d.q||{}).T1||{})[q], {snaps:d.snaps||{}, cur: typeof rec.raw==='number'?rec.raw:null, stamp: typeof rec.updated_at==='number'?rec.updated_at:null, updating:!!d.updating});
        var shown = (c.querySelector('.heatmap-cell-meta')||{}).textContent;
        out.push({q:q, shown:shown, cold:cold.tag, same: shown === cold.tag});
      });
      return JSON.stringify(out); })()`));
    rec('M4b every T1 tile shows exactly what a cold recompute says', cmp.length > 0 && cmp.every(x => x.same), cmp);
    const t1first = JSON.parse(await p.ev(`(async function(){ var d = await (await fetch('/topology/metric-meta', {cache:'no-store'})).json();
      var t = (d.q||{}).T1 || {}; return JSON.stringify(Object.keys(t).map(function(q){ return {q:q, ts:t[q].ts, first:t[q].first, appeared:!!t[q].appeared, oldest:d.oldest}; })); })()`));
    rec('M4d after Take live no T1 entry claims "since history began" unless at the oldest snapshot',
        t1first.every(x => !x.first || x.ts === x.oldest), t1first);
    await center(p, '.topo-section[data-density-panel="T1"]', 'start'); await p.sleep(300);
    await p.shot(`${SHOTS}/m4_after_outside_write.png`);
    execFileSync('python', [process.env.SM_EXT_WRITER, 'set', process.env.SM_CHIP_DIR, 'qubits.q1.T1', String(old), '--mode', 'quam']);
    await p.ev(`localStorage.removeItem('quam_chip_meta_panels'), 1`);
    rec('M4c no JS exceptions', p.errors(0).length === 0, p.errors(0));
    await p.close();
  }
  const fails = res.filter(r => !r.ok).length;
  console.log(fails ? `FAILED ${fails}/${res.length}` : `ALL PASS ${res.length}`);
  process.exit(fails ? 1 : 0);
})();
