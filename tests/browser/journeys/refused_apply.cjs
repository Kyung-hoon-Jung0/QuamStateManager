/* docs/255 -- a refused apply changes nothing; an mtime-only touch is not stale.
 *
 *   SM_CDP_PORT=9437 RA_LIVE=<rig chip folder> node refused_apply.cjs http://127.0.0.1:5117 OUT_DIR
 *
 * Real Chrome, real clicks, the rig's own chip files (never a customer folder).
 * Records every live-file SHA-256 before/after each press in OUT_DIR/refused_apply.json.
 *
 *   0  plain tray Apply (doStateSync('apply')) after an identical-bytes touch
 *   A  Save to working state -> touch live (same bytes) -> the review's
 *      "↑ Apply" (/state/apply-to-live) lands                         (D-03)
 *   B  Auto-Sync push-only: a touch, then a flush lands; an out-of-band
 *      write with DIFFERENT bytes, then the flush is refused -- the edit
 *      stays in the tray, the live files are exactly the outside writer's,
 *      the panel offers the directional trio                     (A-11 / D-04)
 *   C  open -> back -> reload keep the refused state; Pull & apply then
 *      lands both values
 *
 * Exit 0 = every expectation held (expectations are the FIXED behaviour: run
 * against the base commit, phases A/B report the pre-fix failures).
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const BASE = process.argv[2] || 'http://127.0.0.1:5117';
const OUT = process.argv[3] || '.';
const LIVE = process.env.RA_LIVE;
if (!LIVE) { console.error('RA_LIVE=<live chip folder> required'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const res = { steps: [], hashes: [], errors: [] };
let bad = 0;
function note(name, ok, extra) {
  res.steps.push({ name, ok, ...(extra || {}) }); if (!ok) bad++;
  console.log((ok ? 'ok   ' : 'FAIL ') + name + (extra ? ' ' + JSON.stringify(extra).slice(0, 400) : ''));
}
const sha = f => crypto.createHash('sha256').update(fs.readFileSync(path.join(LIVE, f))).digest('hex');
function hashes(label) {
  const h = { label, state: sha('state.json'), wiring: sha('wiring.json'),
              state_mtime_ns: String(fs.statSync(path.join(LIVE, 'state.json'), { bigint: true }).mtimeNs) };
  res.hashes.push(h);
  console.log(`hash ${label}: state ${h.state.slice(0, 12)} wiring ${h.wiring.slice(0, 12)} mtime ${h.state_mtime_ns}`);
  return h;
}
function touch() {          // the same bytes, a new mtime (os.utime)
  for (const f of ['state.json', 'wiring.json']) {
    const p = path.join(LIVE, f); const st = fs.statSync(p);
    const t = new Date(st.mtimeMs + 7000);
    fs.utimesSync(p, t, t);
  }
}
function liveDoc() { return JSON.parse(fs.readFileSync(path.join(LIVE, 'state.json'), 'utf8')); }
function outsideWrite(fn) { // a DIFFERENT content, written the way an outside program would
  const d = liveDoc(); fn(d);
  fs.writeFileSync(path.join(LIVE, 'state.json'), JSON.stringify(d, null, 4), 'utf8');
}
const T1 = 'qubits.qA1.T1';
const CELL = `input.bulk-cell[data-dot-path="${T1}"]`;

(async () => {
  let P = await open(BASE + '/bulk', 1600, 950);
  const mark0 = 0;
  await sleep(2500);
  const tray = async () => JSON.parse(await P.ev(`(function(){var t=document.getElementById('pending-tray'); if(!t) return 'null';
      var sc=t.querySelector('.sync-control'); return JSON.stringify({cc:t.getAttribute('data-change-count'), dirty:t.getAttribute('data-working-dirty'),
      refused:t.getAttribute('data-refused'), conflict:t.classList.contains('pending-tray-conflict'),
      st: sc? sc.getAttribute('data-sync-state'): null, text:(t.textContent||'').replace(/\\s+/g,' ').trim().slice(0,160)});})()`) || 'null');
  const waitTray = async (pred, ms = 15000) => { let t = null; for (let i = 0; i < ms / 150; i++) { t = await tray(); if (t && pred(t)) return t; await sleep(150); } return t; };
  const reveal = async () => {
    await P.ev(`(function(){var s=document.getElementById('bulk-search'); if(!s) return 0; s.focus(); s.value='qA1 T1'; s.dispatchEvent(new Event('input',{bubbles:true})); return 1})()`);
    for (let i = 0; i < 80; i++) {
      const v = await P.ev(`(function(){var c=document.querySelector('${CELL}'); if(!c || !c.getClientRects().length) return null; c.scrollIntoView({block:'center',inline:'center'}); return c.value;})()`);
      if (v != null) return v;
      await sleep(250);
    }
    return null;
  };
  const typeInto = async (value) => {
    const r = JSON.parse(await P.ev(`(function(){var c=document.querySelector('${CELL}'); var b=c.getBoundingClientRect(); return JSON.stringify([b.left+b.width/2,b.top+b.height/2]);})()`));
    await P.click(r[0], r[1]);
    await P.ev(`(function(){var c=document.querySelector('${CELL}'); c.focus(); c.select();})()`);
    await P.send('Input.insertText', { text: value });
    await P.key('Enter', 'Enter', 13);
  };
  const clickSel = async (sel, label) => {
    const xy = await P.ev(`(function(){var b=document.querySelector(${JSON.stringify(sel)}); if(!b) return null; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); var hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,hit:!!hit&&(hit===b||b.contains(hit))});})()`);
    if (!xy) { note('click ' + label + ' (present)', false, { sel }); return false; }
    const o = JSON.parse(xy);
    if (!o.hit) note('click ' + label + ' (not covered)', false, { sel });
    await P.click(o.x, o.y);
    return true;
  };

  const v0 = await reveal();
  note('Live State Edit shows qA1.T1', v0 != null, { value: v0 });

  // ---------------- 0: the plain tray Apply after a touch ----------------
  await typeInto('1.31e-05');
  let t = await waitTray(x => x.cc === '1');
  note('0: the edit is in the tray', t && t.cc === '1', t);
  const h00 = hashes('0 before touch');
  touch();
  const h01 = hashes('0 after identical touch');
  note('0: the touch kept the bytes', h00.state === h01.state && h00.wiring === h01.wiring && h00.state_mtime_ns !== h01.state_mtime_ns);
  await clickSel('#pending-tray .sync-control-act.btn-apply-live', 'tray Apply');
  t = await waitTray(x => x.cc === '0' && x.st === 'synced', 20000);
  const h02 = hashes('0 after tray Apply');
  note('0: the tray Apply landed', t && t.cc === '0' && liveDoc().qubits.qA1.T1 === 1.31e-05, { tray: t, liveT1: liveDoc().qubits.qA1.T1 });
  await P.shot(path.join(OUT, '0_tray_apply_after_touch.png'));

  // ---------------- A: saved edits, the /state/apply-to-live door ----------------
  await reveal();
  await typeInto('1.32e-05');
  await waitTray(x => x.cc === '1');
  await clickSel('#pending-tray .sync-control-main', 'sync control');
  await sleep(900);
  await clickSel('.sp-choice.btn-save', 'Save to working state');
  t = await waitTray(x => x.dirty === '1' && x.cc === '0');
  note('A: saved to the working state (dirty, tray count 0)', t && t.dirty === '1', t);
  await P.key('Escape', 'Escape', 27);
  await sleep(500);
  const hA0 = hashes('A before touch');
  touch();
  const hA1 = hashes('A after identical touch');
  note('A: the touch kept the bytes', hA0.state === hA1.state && hA0.wiring === hA1.wiring);
  await P.shot(path.join(OUT, 'A1_saved_before_apply.png'));
  // the staged "↑ Apply" arms on the first press and fires on the second
  await clickSel('#pending-tray .sync-control-act.btn-apply-live.sync-arm', '↑ Apply (arm)');
  await sleep(400);
  await clickSel('#pending-tray .sync-control-act.btn-apply-live.sync-arm', '↑ Apply (press again)');
  t = await waitTray(x => x.dirty === '0' || x.conflict, 20000);
  await sleep(600);
  t = await tray();
  const hA2 = hashes('A after ↑ Apply');
  await P.shot(path.join(OUT, 'A2_after_apply_touch.png'));
  note('A: ↑ Apply after a same-bytes touch LANDS (D-03)', t && !t.conflict && t.dirty === '0'
       && liveDoc().qubits.qA1.T1 === 1.32e-05 && hA2.state !== hA1.state, { tray: t, liveT1: liveDoc().qubits.qA1.T1 });

  // ---------------- B: Auto-Sync push-only ----------------
  await clickSel('#pending-tray .auto-apply-pill', 'Auto pill');
  await sleep(900);
  const boxes = await P.ev(`(function(){var a=document.getElementById('as-pull'), b=document.getElementById('as-push'); return JSON.stringify({pull:a&&a.checked, push:b&&b.checked});})()`);
  const bx = JSON.parse(boxes || '{}');
  if (bx.pull) await clickSel('#as-pull', 'untick Take live automatically');
  if (!bx.push) await clickSel('#as-push', 'tick Write automatically');
  await clickSel('#auto-sync-pop button[type=submit]', 'Auto-Sync Save');
  await sleep(1200);
  const pill = await P.ev(`(function(){var p=document.querySelector('#pending-tray .auto-apply-pill'); return p? p.className+' | '+p.textContent.replace(/\\s+/g,' ').trim(): null})()`);
  note('B: Auto-Sync armed, push only', /auto-apply-on/.test(pill || '') && /push/.test(pill || ''), { pill });

  touch();
  const hB0 = hashes('B after identical touch');
  await reveal();
  await typeInto('1.33e-05');
  for (let i = 0; i < 80 && liveDoc().qubits.qA1.T1 !== 1.33e-05; i++) await sleep(150);
  await sleep(500);
  t = await tray();
  const hB1 = hashes('B after the auto flush (touched chip)');
  note('B: the auto flush after a same-bytes touch LANDS (D-03)', liveDoc().qubits.qA1.T1 === 1.33e-05 && t && !t.conflict, { tray: t, liveT1: liveDoc().qubits.qA1.T1 });
  await P.shot(path.join(OUT, 'B1_auto_flush_after_touch.png'));

  const OUT_T2 = 4.1e-05;
  outsideWrite(d => { d.qubits.qA2.T2ramsey = OUT_T2; });
  const hB2 = hashes('B after an outside write (different bytes)');
  await reveal();
  await typeInto('1.34e-05');
  t = await waitTray(x => x.conflict, 15000);
  await sleep(800);
  t = await tray();
  const hB3 = hashes('B after the refused flush');
  await P.shot(path.join(OUT, 'B2_refused_flush.png'));
  const changes = await P.ev(`fetch('/changes').then(r=>r.text()).then(t=>JSON.stringify({rows:(t.match(/<tr/g)||[]).length, t1: t.indexOf('${T1}')>=0}))`);
  const agentTray = await P.ev(`fetch('/api/agent/tray').then(r=>r.json()).then(j=>JSON.stringify({count:j.count, live_diverged:j.live_diverged}))`);
  note('B: the refused flush wrote NOTHING (live = the outside writer\'s bytes)', hB3.state === hB2.state && hB3.wiring === hB2.wiring, { before: hB2.state.slice(0, 12), after: hB3.state.slice(0, 12) });
  note('B: the refused edit is still in the tray (A-11)', t && t.conflict && t.cc === '1', { tray: t, changes });
  note('B: the agent view says so too (D-04)', JSON.parse(agentTray || '{}').count === 1 && JSON.parse(agentTray || '{}').live_diverged === true, { agentTray });
  // the panel: the directional trio (docs/86/97)
  await clickSel('#pending-tray .sync-control-main', 'sync control (refused)');
  await sleep(1200);
  const choices = await P.ev(`JSON.stringify(Array.from(document.querySelectorAll('.sp-choice .sp-choice-label')).filter(e=>e.getClientRects().length).map(e=>e.textContent.replace(/\\s+/g,' ').trim()))`);
  await P.shot(path.join(OUT, 'B3_panel_after_refusal.png'));
  const ch = JSON.parse(choices || '[]');
  note('B: the panel offers Pull & apply, Take live and Keep mine', ch.some(c => /Pull & apply/.test(c)) && ch.some(c => /Take live/.test(c)) && ch.some(c => /Keep mine/.test(c)), { choices: ch });
  await P.key('Escape', 'Escape', 27);
  await sleep(400);

  // ---------------- C: open -> back -> reload, then resolve ----------------
  // The refused edit is still in the tray -- i.e. unapplied, in memory -- so
  // leaving the page raises SM's own unsaved-edits guard (app.js
  // beforeunload). Headless Chrome holds that dialog open; accept it and
  // record that it fired (it is the guard working, not a defect).
  const dialogs = [];
  const leave = async (label, fn) => {
    const from = P.events.length;
    const p = fn();
    let done = false;
    p.then(() => { done = true; }, () => { done = true; });
    for (let i = 0; i < 120 && !done; i++) {
      const d = P.events.slice(from).find(e => e.method === 'Page.javascriptDialogOpening');
      if (d && !d._handled) {
        d._handled = true;
        dialogs.push({ at: label, type: d.params.type });
        await P.send('Page.handleJavaScriptDialog', { accept: true });
      }
      await sleep(150);
    }
    await sleep(2500);
  };
  const mark = P.events.length;
  await leave('open /qubits', () => P.send('Page.navigate', { url: BASE + '/qubits' }));
  // a navigation started INSIDE Runtime.evaluate never answers -- schedule it
  await leave('back', () => P.ev('setTimeout(function(){ history.back(); }, 0); 1'));
  let tb = await tray();
  note('C: back -> the tray still holds the edit', tb && tb.cc === '1', { tray: tb, url: await P.ev('location.pathname') });
  await leave('reload', () => P.send('Page.reload', {}));
  tb = await tray();
  await P.shot(path.join(OUT, 'C1_after_reload.png'));
  note('C: reload -> the tray still holds the edit and says live changed', tb && tb.cc === '1' && /live|refused|nothing/i.test(tb.text + ' ' + tb.st), { tray: tb });
  note('C: leaving with an unapplied edit raised the unsaved-edits guard', dialogs.length > 0 && dialogs.every(d => d.type === 'beforeunload'), { dialogs });
  note('C: live untouched by the navigation', sha('state.json') === hB2.state);
  await clickSel('#pending-tray .sync-control-main', 'sync control (after reload)');
  await sleep(1200);
  await clickSel('#sp-merge', '⇄ Pull & apply');
  for (let i = 0; i < 100 && liveDoc().qubits.qA1.T1 !== 1.34e-05; i++) await sleep(150);
  await sleep(800);
  const hC = hashes('C after Pull & apply');
  const d = liveDoc();
  await P.shot(path.join(OUT, 'C2_after_pull_and_apply.png'));
  note('C: Pull & apply lands BOTH values', d.qubits.qA1.T1 === 1.34e-05 && d.qubits.qA2.T2ramsey === OUT_T2, { T1: d.qubits.qA1.T1, T2: d.qubits.qA2.T2ramsey });
  tb = await tray();
  note('C: the tray is clear', tb && tb.cc === '0' && !tb.conflict, { tray: tb });

  // Chrome's own intervention log for the beforeunload guard on a frame with
  // no user gesture (the automated back/reload above) is the BROWSER talking,
  // not a page error -- reported apart, never silently dropped.
  const all = P.errors(mark0);
  const interventions = all.filter(m => /Blocked attempt to show a 'beforeunload' confirmation panel/.test(m));
  res.errors = all.filter(m => !interventions.includes(m));
  res.chrome_interventions = interventions;
  note('console errors (JS exceptions + console.error + log errors): 0', res.errors.length === 0,
       { errors: res.errors.slice(0, 5), chrome_interventions: interventions.length });
  await P.close();
  fs.writeFileSync(path.join(OUT, 'refused_apply.json'), JSON.stringify(res, null, 2));
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(3); });
