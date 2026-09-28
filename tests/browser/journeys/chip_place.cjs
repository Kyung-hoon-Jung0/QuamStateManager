/* w8 chipplace journeys, in real Chrome (2026-09-28).
 *
 *   place  F5 after a Chip Status jump lands on the EXACT panel it jumped to:
 *          an Overview tile pressed with the mouse, Enter or Space, or a
 *          press on the in-page tab bar; F5 after 0 s, 0.3 s, 2 s and 12 s.
 *          Each trial opens a fresh tab on /topology?view=overview, waits
 *          OPENW ms, jumps, waits the delay, reloads (Page.reload = F5) and
 *          reads where the target sits SETTLE ms later. "Exact" = the
 *          target's top edge within 8 px of where a jump lands it (its own
 *          scroll-margin-top under the pane top), on the tab it jumped to; a
 *          target the page end keeps lower (the last section on a small chip)
 *          counts when the pane is scrolled to its end with the target on screen.
 *          Several servers may be given (label=port,...): the trials are
 *          interleaved across them (A/B on one machine, one Chrome).
 *   ring   Datasets run-list toolbar: a MOUSE press on "Full names" leaves
 *          no focus ring around the "rows" toolbar; keyboard focus (Tab)
 *          still draws it. Back to the saved choice afterwards.
 *
 * usage:
 *   SM_CDP_PORT=9605 node tests/browser/journeys/chip_place.cjs place new=5305,base=5306 <shot-dir>
 *   SM_CDP_PORT=9605 node tests/browser/journeys/chip_place.cjs ring new=5305 <shot-dir>
 * env: REPS (1), OTHER_REPS (REPS: reps for the 2nd+ server), OPENW (7000),
 *      SETTLE (20000), DELAYS (0,300,2000,12000), MODES (mouse,Enter,Space,tab),
 *      OUT (json path), CASES (mode:target:delay,... -- exactly these)
 * Exit 1 on any FAIL of the first server label (the one under test).
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const fs = require('fs');
const path = require('path');

const CMD = process.argv[2];
const SERVERS = (process.argv[3] || 'new=5108').split(',').map((s) => { const a = s.split('='); return { label: a[0], base: 'http://127.0.0.1:' + a[1] }; });
const SHOTS = process.argv[4] || '.';
const REPS = +(process.env.REPS || 1);
const OPENW = +(process.env.OPENW || 7000);
const SETTLE = +(process.env.SETTLE || 20000);
const DELAYS = (process.env.DELAYS || '0,300,2000,12000').split(',').map(Number);
const MODES = (process.env.MODES || 'mouse,Enter,Space,tab').split(',');
const OUT = process.env.OUT || null;
const OTHER_REPS = +(process.env.OTHER_REPS || REPS);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// what each mode presses, rotated across the delays so every target is hit
const TARGETS = {
  mouse: [['tile', 't1'], ['tile', 'irb'], ['tile', 'ro_ge'], ['tile', 't2ramsey']],
  Enter: [['tile', 'irb'], ['tile', 'gate1q'], ['tile', 't1'], ['tile', 'srb_gate']],
  Space: [['tile', 't2ramsey'], ['tile', 'ro_ge'], ['tile', 'irb'], ['tile', 'srb']],
  tab: [['tab', 'coherence'], ['tab', 'frequencies'], ['tab', 'fidelity1q'], ['tab', 'calibration']],
};
const TAB_SEL = { overview: '[data-topo-section="overview"]', health: '[data-topo-section="health"]', topology: '#sec-topology',
  fidelity2q: '[data-topo-section="fidelity"]', fidelity1q: '#sec-fidelity-1q', readout: '#sec-readout',
  coherence: '#topo-metric-panels [data-group="coherence"]', frequencies: '#topo-metric-panels [data-group="frequency"]',
  calibration: '#topo-metric-panels [data-group="calibration"]', trends: '[data-topo-section="trends"]' };

const WHERE = (sel) => `(function(){var pn=document.getElementById('table-pane'); if(!pn) return JSON.stringify(null);
  var el=document.querySelector(${JSON.stringify(sel)}); var a=document.querySelector('.topo-subnav-btn.active');
  var pt=pn.getBoundingClientRect().top; var r=el?el.getBoundingClientRect():null;
  return JSON.stringify({url:location.pathname+location.search, tab:a?a.getAttribute('data-view'):null,
    top:r?Math.round(r.top-pt):null, sm:el?Math.round(parseFloat(getComputedStyle(el).scrollMarginTop)):null,
    st:Math.round(pn.scrollTop), sh:pn.scrollHeight, ch:pn.clientHeight,
    rec:(history.state&&history.state.smChipScroll)||null});})()`;
const J = async (p, e) => { const v = await p.ev(e); try { return JSON.parse(v); } catch (x) { return v; } };

async function tileInfo(p, id) {
  return J(p, `(function(){var t=document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="${id}"]'); if(!t) return JSON.stringify(null);
    var j=t.getAttribute('data-tile-jump')||''; var i=j.indexOf('|'); var r=t.getBoundingClientRect();
    var x=r.left+Math.min(r.width/2,60), y=r.top+Math.min(r.height/2,20); var h=document.elementFromPoint(x,y);
    return JSON.stringify({view:i<0?j:j.slice(0,i), sel:i<0?'':j.slice(i+1), x:Math.round(x), y:Math.round(y),
      hit:!!(h&&(h===t||t.contains(h))), vis:r.top>40&&y<innerHeight-10});})()`);
}
async function tabInfo(p, view) {
  return J(p, `(function(){var b=document.querySelector('.topo-subnav-btn[data-view="${view}"]'); if(!b) return JSON.stringify(null);
    var r=b.getBoundingClientRect(); var x=r.left+r.width/2, y=r.top+r.height/2; var h=document.elementFromPoint(x,y);
    return JSON.stringify({x:Math.round(x), y:Math.round(y), hit:!!(h&&(h===b||b.contains(h))), vis:r.top>=0&&r.bottom<=innerHeight});})()`);
}
async function wheel(p, dy) {
  await p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: 900, y: 600, deltaX: 0, deltaY: dy });
}

async function trial(S, mode, delay, what, tag) {
  const p = await open(S.base + '/topology?view=overview');
  const mark = p.events.length;
  await sleep(OPENW);
  let view, sel, err = null;
  if (what[0] === 'tile') {
    const t = await tileInfo(p, what[1]);
    if (!t || !t.view) { await p.close(); return { skip: 'no jump tile ' + what[1] }; }
    view = t.view; sel = t.sel || TAB_SEL[t.view];
    if (mode === 'mouse') {
      if (!t.vis || !t.hit) err = 'tile not clickable ' + JSON.stringify(t);
      else await p.click(t.x, t.y);
    } else {
      await p.ev(`document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="${what[1]}"]').focus({preventScroll:true}), 1`);
      if (mode === 'Enter') await p.key('Enter', 'Enter', 13); else await p.key(' ', 'Space', 32);
    }
  } else {
    view = what[1]; sel = TAB_SEL[view];
    let b = await tabInfo(p, view);
    // a person scrolls until the tab bar is on screen (it pins once reached)
    for (let i = 0; i < 20 && b && !(b.vis && b.hit); i++) { await wheel(p, 300); await sleep(120); b = await tabInfo(p, view); }
    if (!b || !b.vis || !b.hit) err = 'tab not clickable ' + JSON.stringify(b);
    else await p.click(b.x, b.y);
  }
  const t0 = Date.now();
  const at0 = await J(p, WHERE(sel));
  if (delay > 0) await sleep(Math.max(0, delay - (Date.now() - t0)));
  const pre = await J(p, WHERE(sel));
  await p.send('Page.reload', {});
  const samples = [];
  let last = null;
  const T = [3000, 7000, 12000, SETTLE].filter((x, i, a) => x <= SETTLE && a.indexOf(x) === i);
  let waited = 0;
  for (const at of T) { await sleep(at - waited); waited = at; last = await J(p, WHERE(sel)); samples.push(Object.assign({ t: at }, last || {})); }
  // on it: under the sticky bar where a jump puts it -- or, for a target the
  // page end keeps from reaching the top (Calibration on a 5-qubit chip), on
  // screen with the pane scrolled as far as it goes
  const atEnd = last && last.sh - last.ch - last.st <= 2;
  const exact = !err && last && last.top !== null && last.sm !== null
    && (Math.abs(last.top - last.sm) <= 8 || (atEnd && last.top > last.sm && last.top < last.ch - 40
        // ...and where the jump itself had put it, when it had settled before F5
        && (delay < 2000 || !pre || pre.top === null || Math.abs(last.top - pre.top) <= 8)))
    && last.tab === view && last.url === '/topology?view=' + view;
  const shot = path.join(SHOTS, `f5_${S.label}_${mode}_${what[1]}_${delay}_${tag}.png`);
  await p.shot(shot);
  const errs = p.errors(mark);
  await p.close();
  return { server: S.label, mode, delay, target: what[1], view, sel, exact, err, at0, pre, samples, shot, errs };
}

async function place() {
  const res = [];
  // CASES=mode:target:delay,... runs exactly those (a tab target is a view)
  const only = process.env.CASES ? process.env.CASES.split(',').map((c) => c.split(':')) : null;
  for (let rep = 1; only && rep <= REPS; rep++) {
    for (const c of only) {
      const what = [c[0] === 'tab' ? 'tab' : 'tile', c[1]], delay = +c[2];
      for (const S of SERVERS) {
        if (S !== SERVERS[0] && rep > OTHER_REPS) continue;
        const r = await trial(S, c[0], delay, what, 'c' + rep);
        res.push(r);
        const L = r.samples ? r.samples[r.samples.length - 1] : null;
        log((r.exact ? 'EXACT ' : (r.skip ? 'SKIP  ' : 'OFF   ')) + S.label.padEnd(5) + ' ' + c[0].padEnd(5) + ' ' + String(c[1]).padEnd(12)
            + ' F5@' + String(delay).padEnd(5) + ' top ' + (L && L.top) + ' (sm ' + (L && L.sm) + ') tab ' + (L && L.tab)
            + (r.err ? ' ERR ' + r.err : '') + (r.errs && r.errs.length ? ' JSERR ' + r.errs[0] : ''));
      }
    }
  }
  for (let rep = 1; !only && rep <= REPS; rep++) {
    for (const mode of MODES) {
      for (let i = 0; i < DELAYS.length; i++) {
        const delay = DELAYS[i], what = TARGETS[mode][(i + rep - 1) % TARGETS[mode].length];
        for (const S of SERVERS) {
          if (S !== SERVERS[0] && rep > OTHER_REPS) continue;   // the comparison server: fewer reps
          const r = await trial(S, mode, delay, what, 'r' + rep);
          res.push(r);
          const L = r.samples ? r.samples[r.samples.length - 1] : null;
          log((r.exact ? 'EXACT ' : (r.skip ? 'SKIP  ' : 'OFF   ')) + S.label.padEnd(5) + ' ' + mode.padEnd(5) + ' ' + String(what[1]).padEnd(12)
              + ' F5@' + String(delay).padEnd(5) + ' top ' + (L && L.top) + ' (sm ' + (L && L.sm) + ') tab ' + (L && L.tab)
              + (r.err ? ' ERR ' + r.err : '') + (r.errs && r.errs.length ? ' JSERR ' + r.errs[0] : ''));
          if (OUT) fs.writeFileSync(OUT, JSON.stringify(res, null, 1));
        }
      }
    }
  }
  const sum = {};
  for (const r of res) {
    if (r.skip) continue;
    const s = sum[r.server] || (sum[r.server] = { exact: 0, n: 0, off: [] });
    s.n++; if (r.exact) s.exact++; else s.off.push(r.mode + ' ' + r.target + ' @' + r.delay);
  }
  console.log('SUMMARY ' + JSON.stringify(sum));
  if (OUT) fs.writeFileSync(OUT, JSON.stringify({ trials: res, summary: sum }, null, 1));
  const first = sum[SERVERS[0].label];
  return !!first && first.exact === first.n;
}

async function ring() {
  let allOk = true;
  for (const S of SERVERS) {
    const p = await open(S.base + '/datasets');
    await sleep(3000);
    const Q = `(function(){var tb=document.querySelector('.sidebar-tree-toolbar'); var f=document.getElementById('exp-density-full'), c=document.getElementById('exp-density-compact');
      if(!tb||!f) return JSON.stringify(null); var r=f.getBoundingClientRect(), rc=c.getBoundingClientRect(), rt=tb.getBoundingClientRect();
      var ae=document.activeElement;
      var hf=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      return JSON.stringify({hitFull:!!(hf&&(hf===f||f.contains(hf))), shadow:getComputedStyle(tb).boxShadow, fx:Math.round(r.left+r.width/2), fy:Math.round(r.top+r.height/2),
        cx:Math.round(rc.left+rc.width/2), cy:Math.round(rc.top+rc.height/2), tb:[Math.round(rt.left),Math.round(rt.top),Math.round(rt.width),Math.round(rt.height)],
        vis:r.width>0&&r.height>0, active:ae?(ae.id||ae.tagName):null, compact:document.body.classList.contains('exp-list-compact'),
        fv:!!document.querySelector('.sidebar-tree-toolbar :focus-visible')});})()`;
    // Pico's resting group shadow computes to a transparent 0-spread one; the ring is coloured
    const ringOn = (sh) => !!sh && sh !== 'none' && !/^rgba\(0, 0, 0, 0\)/.test(String(sh).trim());
    // a person scrolls the sidebar until the toolbar is on screen
    await p.ev(`(function(){var tb=document.querySelector('.sidebar-tree-toolbar'); if(tb) tb.scrollIntoView({block:'center', behavior:'instant'}); return 1;})()`);
    await sleep(300);
    const before = await J(p, Q);
    if (!before || !before.vis || !before.hitFull) { console.log('FAIL ring ' + S.label + ' toolbar not visible ' + JSON.stringify(before)); allOk = false; await p.close(); continue; }
    await p.click(before.fx, before.fy);                 // a real mouse press on "Full names"
    await sleep(400);
    const afterMouse = await J(p, Q);
    await p.shot(path.join(SHOTS, `ring_${S.label}_mouse_full.png`));
    await sleep(3000);                                   // "until reload": still there later?
    const later = await J(p, Q);
    // keyboard: Tab from the focused "Full names" moves to "Compact"
    await p.key('Tab', 'Tab', 9);
    await sleep(400);
    const afterKey = await J(p, Q);
    await p.shot(path.join(SHOTS, `ring_${S.label}_keyboard.png`));
    // put the choice back the way a person would: a mouse press on the "rows"
    // label first (focus leaves the keyboard-focused button -- a press on an
    // element that already has keyboard focus keeps :focus-visible, by the
    // browser's own rule), then the mouse on Compact, then reload
    const lab = await J(p, `(function(){var l=document.querySelector('.sidebar-tree-toolbar .toolbar-label'); var r=l.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()`);
    await p.click(lab.x, lab.y);
    await sleep(200);
    await p.click(before.cx, before.cy);
    await sleep(300);
    const backMouse = await J(p, Q);
    await p.send('Page.reload', {});
    await sleep(3000);
    const reloaded = await J(p, Q);
    const okMouse = afterMouse && !afterMouse.compact && afterMouse.active === 'exp-density-full' && !ringOn(afterMouse.shadow) && !ringOn(later.shadow);
    const okKey = afterKey && ringOn(afterKey.shadow) && afterKey.fv;
    const okBack = backMouse && backMouse.compact && !ringOn(backMouse.shadow) && reloaded && reloaded.compact && !ringOn(reloaded.shadow);
    console.log((okMouse ? 'PASS ' : 'FAIL ') + S.label + ' mouse press on Full names: no ring on the rows toolbar  ' + JSON.stringify({ afterMouse, later: later && later.shadow }));
    console.log((okKey ? 'PASS ' : 'FAIL ') + S.label + ' keyboard focus (Tab) draws the ring  ' + JSON.stringify(afterKey));
    console.log((okBack ? 'PASS ' : 'FAIL ') + S.label + ' back to Compact by mouse, then reload: compact, no ring  ' + JSON.stringify({ backMouse: backMouse && backMouse.shadow, reloaded }));
    const errs = p.errors(0);
    if (errs.length) console.log('JSERR ' + S.label + ' ' + errs.slice(0, 3).join(' | '));
    if (S === SERVERS[0]) allOk = allOk && okMouse && okKey && okBack;
    await p.close();
  }
  return allOk;
}

(async () => {
  let ok = false;
  if (CMD === 'place') ok = await place();
  else if (CMD === 'ring') ok = await ring();
  else { console.error('usage: chip_place.cjs place|ring label=port[,label=port] <shot-dir>'); process.exit(2); }
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
