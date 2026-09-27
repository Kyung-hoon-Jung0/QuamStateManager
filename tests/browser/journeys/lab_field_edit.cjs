/* A lab-class pulse edited from the Live Edit pair grid and the Json Tree
 * (2026-09-27): the class's own code is asked before /field/edit(-batch)
 * writes, the cell says "checking with your class's own code...", a refusal
 * writes nothing (no tray entry), a valid value lands.
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

const MACRO = 'qubit_pairs.q2-3.macros.cz_GNZ.flux_pulse_qubit.flat_length';
const TARGET = 'qubit_pairs.q2-3.macros.cz_GNZ.flux_pulse_target.flat_length';
const ZPTR = 'qubits.q2.z.operations.cz_GNZ_flux_pulse_q2_q3.flat_length';
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

  const t1 = Date.now();
  await typeInto(process.env.LFE_VALID || '80');
  let applied = false;
  for (let i = 0; i < 400; i++) {
    const s = await P.ev(`(function(){var c=document.querySelector('${sel}'); return c.classList.contains('bulk-cell-modified') && !c.classList.contains('bulk-cell-bad') ? c.value : '';})()`);
    if (s) { applied = s; break; }
    await sleep(100);
  }
  await sleep(300);
  await P.shot(path.join(OUT, '3_grid_valid_written.png'));
  const after2 = await peek(MACRO, P);
  const tray2 = await changes(P);
  note('grid: a valid value is written, one tray entry', applied && after2.includes(':' + (process.env.LFE_VALID || '80')) && tray2 > tray0, { cell: applied, peek: after2, ms: Date.now() - t1, tray: tray2 });
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
  const tv = Date.now();
  await treeEdit(V2);
  w = { err: null, ms: 0 };
  for (let i = 0; i < 600; i++) {
    if ((await peek(MACRO, P)).includes(':' + V2)) break;
    const e = await P.ev(`(function(){var e=window.__lfeRow.querySelector('.tree-edit-err'); return e?e.textContent:null;})()`);
    if (e) { w.err = e; break; }
    await sleep(100);
  }
  w.ms = Date.now() - tv;
  await sleep(500); await P.shot(path.join(OUT, '8_ptr_valid.png'));
  const zAfter = await peek(MACRO, P);
  note('pointer chain: a valid value lands at the macro leaf', !w.err && zAfter.includes(':' + V2), { zAfter, err: w.err, ms: w.ms });

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
  const unexpected = res.errors.filter((e) => !/status of 400/.test(e));
  note('console clean (the expected refusal 400s aside)', unexpected.length === 0, { errors: unexpected.slice(0, 5), refusals_400: res.errors.length - unexpected.length });
  fs.writeFileSync(path.join(OUT, 'lab_field_edit.json'), JSON.stringify(res, null, 1));
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(3); });
