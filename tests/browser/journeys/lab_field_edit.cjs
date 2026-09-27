/* A lab-class pulse edited from the Live Edit pair grid, the Json Tree and
 * the pair inspector (2026-09-27): the class's own code is asked before
 * /field/edit(-batch) writes, the cell says "checking with your class's own
 * code...", a refusal writes nothing (no tray entry). fix3: the lab's GATE
 * (CZGateTwoFlux.apply -> assert_lines_compatible) is asked too, so ONE half
 * of the control/target pair is refused naming the other, and the badge's
 * "Set ... too" press writes both halves in one batch -- the chip is left
 * with the pair consistent (the old journey left 82 vs 74).
 *
 *   SM_CDP_PORT=9481 node lab_field_edit.cjs http://127.0.0.1:5181 OUT_DIR
 *
 * Writes OUT_DIR/lab_field_edit.json + the deciding screenshots. Exit 0 = all ok.
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const fs = require('fs');
const path = require('path');
const BASE = process.argv[2] || 'http://127.0.0.1:5181';
const OUT = process.argv[3] || '.';
fs.mkdirSync(OUT, { recursive: true });
const res = { steps: [], errors: [] };
let bad = 0;
function note(name, ok, extra) { res.steps.push({ name, ok, ...(extra || {}) }); if (!ok) bad++; console.log((ok ? 'ok   ' : 'FAIL ') + name + (extra ? ' ' + JSON.stringify(extra).slice(0, 300) : '')); }

// LFE_PAIR: a pair carrying the lab's cz_GNZ gate (5Q: q2-3, big30x: q1-6)
const PAIR = process.env.LFE_PAIR || 'q2-3';
const QC = 'q' + PAIR.split('-')[0].replace(/^q/, ''), QT = 'q' + PAIR.split('-')[1].replace(/^q/, '');
const MACRO = `qubit_pairs.${PAIR}.macros.cz_GNZ.flux_pulse_qubit.flat_length`;
const TARGET = `qubit_pairs.${PAIR}.macros.cz_GNZ.flux_pulse_target.flat_length`;
const ZPTR = `qubits.${QC}.z.operations.cz_GNZ_flux_pulse_${QC}_${QT}.flat_length`;
// refused values: pass fresh ones per run (a repeated value is a RAM answer
// and never shows the checking state)
const BAD = (process.env.LFE_BAD || '4,4,4').split(',');
const V2 = process.env.LFE_VALID2 || '78';
// LFE_COLD=1: retire the warm lab-env worker before each surface, so each
// shows the COLD first-edit wait (the case the badge exists for)
function coldStart() {
  if (process.env.LFE_COLD !== '1') return;
  try {
    const ps = "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " +
      "Where-Object { $_.CommandLine -like '*run_pulse_waveform.py*--serve*' } | " +
      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }";
    require('child_process').execFileSync('powershell', ['-NoProfile', '-Command', ps], { stdio: 'ignore' });
  } catch (e) { /* none running */ }
}

(async () => {
  const peek = async (p, P) => P.ev(`fetch('/field/peek?dot_path=${p}').then(r=>r.json()).then(j=>JSON.stringify(j.values||j))`);
  const changes = async (P) => P.ev(`fetch('/changes').then(r=>r.text()).then(t=>(t.match(/<tr/g)||[]).length)`);

  // ---------------- 1. Live Edit pair grid ----------------
  let P = await open(BASE + '/bulk', 1600, 950);
  await sleep(2500);
  const sel = `input.bulk-cell[data-dot-path="${MACRO}"]`;
  let found = await P.ev(`(function(){var c=document.querySelector('${sel}'); if(!c) return 'none'; c.scrollIntoView({block:'center',inline:'center'}); return c.value;})()`);
  note('pair grid shows cz_GNZ flux_pulse_qubit.flat_length', found !== 'none' && found != null, { value: found });
  const before = await peek(MACRO, P);
  const tray0 = await changes(P);
  const typeInto = async (value) => {
    const r = await P.ev(`(function(){var c=document.querySelector('${sel}'); var b=c.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2];})()`);
    await P.click(r[0], r[1]);
    await P.ev(`(function(){var c=document.querySelector('${sel}'); c.focus(); c.select();})()`);
    await P.send('Input.insertText', { text: value });
    await P.key('Enter', 'Enter', 13);
  };
  coldStart();
  const t0 = Date.now();
  await typeInto(BAD[0]);
  let sawBadge = null, refusedText = null;
  for (let i = 0; i < 400; i++) {
    const s = await P.ev(`(function(){var b=document.querySelector('.lab-check-badge'); var e=document.querySelector('${sel}'); var row=e&&e.closest('tr'); var er=row&&row.querySelector('.bulk-row-error'); return JSON.stringify({b: b? b.textContent: null, ref: b? b.classList.contains('lab-check-refused'):false, err: er && !er.hidden ? er.textContent : null});})()`);
    const j = JSON.parse(s);
    if (j.b && !j.ref && !sawBadge) { sawBadge = { text: j.b, at_ms: Date.now() - t0 }; await P.shot(path.join(OUT, '1_grid_checking.png')); }
    if (j.err) { refusedText = j.err; break; }
    await sleep(100);
  }
  const tRef = Date.now() - t0;
  await sleep(300);
  await P.shot(path.join(OUT, '2_grid_refused.png'));
  note('grid: the checking badge was shown while waiting', !!sawBadge, sawBadge || {});
  note('grid: refusal named in place (row error)', !!refusedText && /refused this value/.test(refusedText) && /nothing was written/.test(refusedText), { refusedText: (refusedText || '').slice(0, 220), ms: tRef });
  const after = await peek(MACRO, P);
  const tray1 = await changes(P);
  note('grid: nothing written, no tray entry', after === before && tray1 === tray0, { before, after, tray0, tray1 });

  // an even value the PULSE class draws, on ONE half: the GATE refuses it
  const VALID = process.env.LFE_VALID || '80';
  await P.ev(`document.querySelectorAll('.lab-check-badge').forEach(function(b){b.remove();})`);
  const t1 = Date.now();
  await typeInto(VALID);
  let offer = null;
  for (let i = 0; i < 600 && !offer; i++) {
    offer = await P.ev(`(function(){var b=document.querySelector('.lab-check-badge.lab-check-refused .lab-check-follow'); if(!b) return null; var r=b.getBoundingClientRect(); return JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,t:b.textContent,badge:b.parentNode.textContent,title:b.parentNode.title});})()`);
    if (!offer) await sleep(100);
  }
  offer = offer ? JSON.parse(offer) : null;
  await sleep(200);
  await P.shot(path.join(OUT, '3_grid_gate_refused_offer.png'));
  note('grid: one half refused by the GATE, naming the other half, with a set-both offer',
       !!offer && /apply\(\)/.test(offer.title) && /flat_length differ/.test(offer.title) && offer.title.includes(TARGET) && /flux_pulse_target\.flat_length too/.test(offer.t),
       { ms: Date.now() - t1, offer: offer && offer.t, why: offer && offer.title.slice(0, 260) });
  note('grid: the refused half was not written', (await peek(MACRO, P)) === before && (await changes(P)) === tray0);
  const t1b = Date.now();
  // the badge re-anchors a frame after it turns into a refusal: read the
  // button's place again right before pressing it
  const pressOffer = async () => {
    await sleep(400);
    const xy = await P.ev(`(function(){var b=document.querySelector('.lab-check-badge.lab-check-refused .lab-check-follow'); if(!b) return null; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); var hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,hit:hit===b});})()`);
    if (!xy) return false;
    const o = JSON.parse(xy);
    await P.click(o.x, o.y);
    return o.hit;
  };
  const hit1 = offer ? await pressOffer() : false;
  note('grid: the offer button is the element under the pointer when pressed', hit1);
  let both = '';
  for (let i = 0; i < 600; i++) {
    const a = await peek(MACRO, P), b = await peek(TARGET, P);
    if (a.includes(':' + VALID) && b.includes(':' + VALID)) { both = a + ' ' + b; break; }
    await sleep(100);
  }
  await sleep(600);
  await P.shot(path.join(OUT, '3b_grid_both_written.png'));
  const cellNow = await P.ev(`(function(){var c=document.querySelector('${sel}'); return c ? c.value + (c.classList.contains('bulk-cell-modified') ? ' modified' : '') : null;})()`);
  const tray2 = await changes(P);
  note('grid: "Set both" writes both halves in one batch (cell repainted, tray +2)', !!both && tray2 >= tray0 + 2 && String(cellNow).indexOf(VALID) === 0,
       { both, cell: cellNow, ms: Date.now() - t1b, tray: tray2 });
  res.errors.push(...P.errors());
  await P.close();

  // ---------------- 2. Json Tree (target leaf, then the pointer chain) ----------------
  P = await open(BASE + '/explorer', 1600, 950);
  await sleep(2500);
  // expand the tree down to a leaf: the tree search reveals it
  const reveal = async (dotted) => {
    const segs = dotted.split('.');
    const leaf = segs[segs.length - 1];
    const q = segs[segs.length - 2] + ' ' + leaf;
    await P.ev(`(function(){var s=document.getElementById('explorer-search'); if(!s) return 'nosearch'; s.focus(); s.value=${JSON.stringify(q)}; s.dispatchEvent(new Event('input',{bubbles:true})); return 'ok';})()`);
    await sleep(1500);
    // find the row whose ancestor keys spell the path
    return P.ev(`(function(){
      var want=${JSON.stringify(segs)};
      var rows=[].slice.call(document.querySelectorAll('.tree-row'));
      for (var i=0;i<rows.length;i++){
        var r=rows[i]; if(!r.getClientRects().length) continue;
        var k=r.querySelector('.tree-key'); if(!k) continue;
        if(k.textContent.replace(/[:\\s"]/g,'')!==want[want.length-1]) continue;
        var keys=[]; var d=r.closest('.tree-node'); d=d&&d.parentElement;
        while(d){ if(d.classList&&d.classList.contains('tree-node')){ var sk=d.querySelector(':scope > .tree-row .tree-key'); if(sk) keys.unshift(sk.textContent.replace(/[:\\s"]/g,'')); } d=d.parentElement; }
        var tail=want.slice(0,-1); var ok=true;
        for (var j=0;j<tail.length;j++){ if(keys[keys.length-tail.length+j]!==tail[j]) { ok=false; break; } }
        if(!ok) continue;
        r.scrollIntoView({block:'center'});
        var v=r.querySelector('.tree-val, [class*=tree-val]'); window.__lfeRow=r; window.__lfeVal=v;
        return v? v.textContent : 'noval';
      }
      return 'notfound';
    })()`);
  };
  const treeEdit = async (value) => {
    const r = await P.ev(`(function(){var b=window.__lfeVal.getBoundingClientRect(); return [b.left+Math.min(20,b.width/2),b.top+b.height/2];})()`);
    await P.click(r[0], r[1]);
    await sleep(200);
    await P.ev(`(function(){var i=window.__lfeRow.querySelector('input.tree-edit-input'); if(i){i.focus(); i.select();} return !!i;})()`);
    await P.send('Input.insertText', { text: value });
    await P.key('Enter', 'Enter', 13);
  };
  const waitTree = async (shotChecking) => {
    const tt = Date.now(); let saw = null, err = null;
    for (let i = 0; i < 400; i++) {
      const s = await P.ev(`(function(){var b=document.querySelector('.lab-check-badge'); var e=window.__lfeRow.querySelector('.tree-edit-err'); return JSON.stringify({b:b?b.textContent:null, ref:b?b.classList.contains('lab-check-refused'):false, err:e?e.textContent:null});})()`);
      const j = JSON.parse(s);
      if (j.b && !j.ref && !saw) { saw = { text: j.b, at_ms: Date.now() - tt }; if (shotChecking) await P.shot(path.join(OUT, shotChecking)); }
      if (j.err) { err = j.err; break; }
      if (!j.b && saw) break;          // answered, no error chip
      await sleep(100);
    }
    return { saw, err, ms: Date.now() - tt };
  };

  let v = await reveal(TARGET);
  note('tree: found cz_GNZ flux_pulse_target.flat_length', v && !/notfound|noval/.test(v), { v });
  if (!v || /notfound|noval/.test(v)) {
    await P.shot(path.join(OUT, 'tree_notfound.png'));
    fs.writeFileSync(path.join(OUT, 'lab_field_edit.json'), JSON.stringify(res, null, 1));
    process.exit(1);
  }
  const tb = await peek(TARGET, P); const tr0 = await changes(P);
  coldStart();
  await treeEdit(BAD[1]);
  let w = await waitTree('4_tree_checking.png');
  await sleep(300); await P.shot(path.join(OUT, '5_tree_refused.png'));
  note('tree: checking badge shown', !!w.saw, w.saw || {});
  note('tree: refusal chip in place', !!w.err && /refused this value/.test(w.err), { err: (w.err || '').slice(0, 200), ms: w.ms });
  note('tree: nothing written, no tray entry', (await peek(TARGET, P)) === tb && (await changes(P)) === tr0);

  // pointer chain: q2's z-channel pulse field POINTS at the macro leaf
  v = await reveal(ZPTR);
  note('tree: found the q2 z-op pointer field', v && !/notfound|noval/.test(v), { v });
  const zb = await peek(MACRO, P); const tr2 = await changes(P);
  coldStart();
  await P.ev(`document.querySelectorAll('.tree-edit-err').forEach(function(e){e.remove();})`);
  await treeEdit(BAD[2]);
  w = await waitTree('6_ptr_checking.png');
  await sleep(300); await P.shot(path.join(OUT, '7_ptr_refused.png'));
  note('pointer chain: refused through the link, nothing written', !!w.err && /refused this value/.test(w.err)
       && (await peek(MACRO, P)) === zb && (await changes(P)) === tr2, { err: (w.err || '').slice(0, 200), ms: w.ms });
  await P.ev(`document.querySelectorAll('.tree-edit-err').forEach(function(e){e.remove();})`);
  await P.ev(`document.querySelectorAll('.lab-check-badge').forEach(function(b){b.remove();})`);
  const tv = Date.now();
  await treeEdit(V2);
  let offer2 = null;
  for (let i = 0; i < 600 && !offer2; i++) {
    offer2 = await P.ev(`(function(){var b=document.querySelector('.lab-check-badge.lab-check-refused .lab-check-follow'); if(!b) return null; var r=b.getBoundingClientRect(); return JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,t:b.textContent});})()`);
    if (!offer2) await sleep(100);
  }
  offer2 = offer2 ? JSON.parse(offer2) : null;
  await sleep(300); await P.shot(path.join(OUT, '8_ptr_gate_refused_offer.png'));
  note('pointer chain: one half through the link is refused by the gate, with the set-both offer', !!offer2, { offer: offer2 && offer2.t, ms: Date.now() - tv });
  if (offer2) await pressOffer();
  let zAfter = '', tAfter = '';
  for (let i = 0; i < 600; i++) {
    zAfter = await peek(MACRO, P); tAfter = await peek(TARGET, P);
    if (zAfter.includes(':' + V2) && tAfter.includes(':' + V2)) break;
    await sleep(100);
  }
  await sleep(500); await P.shot(path.join(OUT, '8b_ptr_both_written.png'));
  note('pointer chain: "Set both" lands the value on both halves', zAfter.includes(':' + V2) && tAfter.includes(':' + V2), { zAfter, tAfter, ms: Date.now() - tv });

  res.errors.push(...P.errors());
  await P.close();

  // ---------------- 2b. the pair inspector (fix3, verifier: an open door) ----------------
  P = await open(BASE + '/pairs', 1600, 950);
  await sleep(2500);
  await P.ev(`htmx.ajax('GET','/pair/${PAIR}',{target:'#inspector-pane',swap:'innerHTML'})`);
  let ibox = null;
  for (let i = 0; i < 100 && !ibox; i++) {
    ibox = await P.ev(`(function(){ var f=[].slice.call(document.querySelectorAll('form.inline-edit')).find(function(f){var d=f.querySelector('input[name=dot_path]'); return d && d.value==='${MACRO}';}); if(!f) return null; var i=f.querySelector('input[name=value]'); i.scrollIntoView({block:'center'}); var r=i.getBoundingClientRect(); return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,v:i.value}); })()`);
    if (!ibox) await sleep(200);
  }
  note('pair inspector shows cz_GNZ flux_pulse_qubit.flat_length as an input', !!ibox, { ibox });
  if (ibox) {
    const bx = JSON.parse(ibox);
    const pv0 = await peek(MACRO, P), pt0 = await changes(P);
    await P.click(bx.x, bx.y);
    await P.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await P.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await P.send('Input.insertText', { text: '9' });
    // a native form: Enter submits only with its text (keypress) -- the
    // bare key() the grids use would leave it to the focusout commit later
    await P.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: String.fromCharCode(13) });
    await P.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
    let toast = '';
    for (let i = 0; i < 300 && !toast; i++) {
      toast = await P.ev(`[].slice.call(document.querySelectorAll('.toast')).map(function(x){return x.innerText}).join(' | ').replace(/\\s+/g,' ')`);
      if (!/refused/.test(toast)) { toast = ''; await sleep(100); }
    }
    await sleep(300); await P.shot(path.join(OUT, '8c_pair_inspector_refused.png'));
    note('pair inspector: an odd flat_length is refused by the class, nothing written',
         /refused this value/.test(toast) && /even/.test(toast) && (await peek(MACRO, P)) === pv0 && (await changes(P)) === pt0,
         { toast: toast.slice(0, 220) });
  }

  // ---------------- 3. Apply to live ----------------
  // the real button (the sync control may sit behind the pill: open it first)
  let btn = null;
  for (let i = 0; i < 30 && !btn; i++) {
    btn = await P.ev(`(function(){var b=[].slice.call(document.querySelectorAll('.btn-apply-live')).filter(function(x){return x.getClientRects().length;})[0]; if(!b){var p=document.querySelector('.sync-pill, #sync-pill, [data-sync-pill]'); if(p) p.click(); return null;} var r=b.getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2, b.textContent.trim()];})()`);
    if (!btn) await sleep(300);
  }
  if (btn) {
    await P.shot(path.join(OUT, '9_before_apply.png'));
    await P.click(btn[0], btn[1]);
    for (let i = 0; i < 100; i++) {
      const n = await changes(P);
      if (n === 0) break;
      await sleep(300);
    }
    await sleep(800);
    await P.shot(path.join(OUT, '10_after_apply.png'));
  }
  note('apply to live pressed (real button)', !!btn, { btn: btn && btn[2], tray_after: await changes(P) });
  res.errors.push(...P.errors());
  await P.close();
  // a refusal IS an HTTP 400 (every edit refusal in the app is): Chrome logs it
  const unexpected = res.errors.filter((e) => !/status of 400|Status Error Code 400 from \/pair\//.test(e));
  note('console clean (the expected refusal 400s aside)', unexpected.length === 0, { errors: unexpected.slice(0, 5), refusals_400: res.errors.length - unexpected.length });
  fs.writeFileSync(path.join(OUT, 'lab_field_edit.json'), JSON.stringify(res, null, 1));
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(3); });
