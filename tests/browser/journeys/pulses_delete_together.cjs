/* Journey (w8, docs/218 open issue): the Pulses page's "Delete together with ..."
 * in real headless Chrome, every click a real mouse event.
 *
 * Per case (a gate's inline pulse, an op the gate plays by name, the gate's
 * REQUIRED control pulse): open the pulse from the table -> Delete -> confirm
 * -> the lab refusal appears IN the delete step with the exact set -> press
 * "Delete together" -> the pane says what went, the rows + count repaint, the
 * server no longer has any of it -> Ctrl+Z -> every path back byte-equal, the
 * pulse re-opened -> reload -> the page equals a cold render.
 * Then (unless NO_APPLY): one batch kept + Apply to live -> the caller runs
 * pulse_lab_check.py on a copy of the live chip.
 * TREE=1: the Json Tree has no ✕ on the op (w9/pulsegate) and its link
 * lands on it here.
 * UNAVAIL=1 adds the lab-worker-unavailable path: the refusal is got with a
 * working env, the env is then made unrunnable (ENV_FILE is the rig's
 * config_generator.json; restored after) and the press must go through with
 * "written UNCHECKED" -- never block.
 *
 *   SM_CDP_PORT=<cdp> PORT=<sm> SHOT_DIR=<dir> node pulses_delete_together.cjs
 *   env: PAIR (q1-2) CTL (q1) TGT (q2) GATE (cz_SNZ) PAIR2 (q2-3) CTL2 (q2) TGT2 (q3)
 */
'use strict';
const fs = require('fs');
const { open, sleep, base, baseFrom, smPath, smUrl } = require('./cdp.cjs');

const PORT = +(process.env.PORT || 5309);
const DIR = process.env.SHOT_DIR || '.';
const SLOW = +(process.env.SLOW || 1);
const E = (k, d) => process.env[k] || d;
const PAIR = E('PAIR', 'q1-2'), CTL = E('CTL', 'q1'), TGT = E('TGT', 'q2'), GATE = E('GATE', 'cz_SNZ');
const PAIR2 = E('PAIR2', 'q2-3'), CTL2 = E('CTL2', 'q2'), TGT2 = E('TGT2', 'q3');
fs.mkdirSync(DIR, { recursive: true });
const J = JSON.stringify;
const out = []; let bad = 0; const timing = {};
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; return c; }

async function waitFor(p, expr, ms = 60000) {
  ms *= SLOW;
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(150);
  }
  return null;
}
async function center(p, sel) {
  return p.ev(`(function(){var e=document.querySelector(${J(sel)}); if(!e||e.offsetParent===null) return null; e.scrollIntoView({block:'center'}); var b=e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2];})()`);
}
async function clickSel(p, sel) {
  let r = await center(p, sel); if (!r) return false;
  for (let i = 0; i < 20; i++) {
    await sleep(120);
    const r2 = await center(p, sel); if (!r2) return false;
    if (Math.abs(r2[0] - r[0]) < 1 && Math.abs(r2[1] - r[1]) < 1) { r = r2; break; }
    r = r2;
  }
  const hit = await p.ev(`(function(){var e=document.querySelector(${J(sel)}); var h=document.elementFromPoint(${r[0]},${r[1]}); return (h&&(h===e||e.contains(h)||h.contains(e))) ? true : (h? h.tagName+'.'+String(h.className).slice(0,60) : 'nothing');})()`);
  if (hit !== true) console.log('  (hit-test miss for ' + sel + ': ' + hit + ')');
  await p.click(r[0], r[1]);
  return hit === true;
}
async function typeInto(p, sel, text) {
  if (!(await center(p, sel))) return false;
  await clickSel(p, sel);
  await p.ev(`(function(){var i=document.querySelector(${J(sel)}); i.focus(); i.select(); return 1})()`);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
  if (text !== '') await p.send('Input.insertText', { text: String(text) });
  // a typed word ends on a key RELEASE (the table search listens on keyup)
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'End', code: 'End', windowsVirtualKeyCode: 35 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'End', code: 'End', windowsVirtualKeyCode: 35 });
  return true;
}
async function pressZ(p) {
  await p.ev(`(document.activeElement&&document.activeElement.blur&&document.activeElement.blur(), 1)`);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
}
const PEEK = (paths) => `fetch('${smUrl(`/field/peek?`)}'+${J(paths)}.map(function(x){return 'dot_path='+encodeURIComponent(x)}).join('&')).then(function(r){return r.json()}).then(function(d){var o={};${J(paths)}.forEach(function(x){o[x]= d.errors&&d.errors[x] ? '<absent>' : JSON.stringify(d.values[x])}); return JSON.stringify(o)})`;
const SRV_PATHS = `fetch('${smUrl(`/api/pulse/paths`)}').then(function(r){return r.json()}).then(function(d){return JSON.stringify(d.options.map(function(o){return o[0]}).sort())})`;
const TOTAL = `(document.getElementById('pulses-total')||{}).textContent||''`;
// the rows wrap's own URL, fetched cold: what a reload would show for the same filter
// (the live search keyword rides every table request -- app.js's configRequest
// rewrite -- so the cold fetch carries it too)
const COLD_TOTAL = `(function(){var w=document.getElementById('pulses-rows-wrap'); var q=(document.querySelector('.table-filter input[name=q]')||{value:''}).value.trim(); var u=w.getAttribute('hx-get').replace(/([?&])q=[^&]*&?/,'$1'); return fetch(u+(u.indexOf('?')<0?'?':'&')+'q='+encodeURIComponent(q),{headers:{'HX-Request':'true'}}).then(function(r){return r.text()}).then(function(t){var m=t.match(/id="pulses-total"[^>]*>([^<]*)</); return m?m[1]:'?'})})()`;
const ROWS = `JSON.stringify([].slice.call(document.querySelectorAll('#pulses-rows-wrap tr[data-pulse-path]')).map(function(r){return r.getAttribute('data-pulse-path')}))`;

async function settled(p) {
  // the table's own request has answered and its count held still for 0.8 s
  let last = null, since = Date.now();
  const t0 = Date.now();
  while (Date.now() - t0 < 30000 * SLOW) {
    const s = await p.ev(`(document.querySelector('.htmx-request')?'busy|':'')+(${TOTAL})+'|'+document.querySelectorAll('#pulses-rows-wrap tr[data-pulse-path]').length`);
    if (s !== last) { last = s; since = Date.now(); } else if (!/^busy/.test(s) && Date.now() - since > 800) return s;
    await sleep(100);
  }
  return last;
}
async function openPulse(p, path, q) {
  // a real search + a real row click; the table may page a big chip
  await typeInto(p, '.table-filter input[name="q"]', q);
  await sleep(300);
  await settled(p);
  const row = await waitFor(p, `document.querySelector('tr[data-pulse-path=${J(path)}]')?1:0`, 20000);
  if (row) {
    await clickSel(p, `tr[data-pulse-path=${J(path)}] td:nth-child(2)`);
  } else {
    console.log('  (row not in the searched table -- opened by URL) ' + path);
    await p.ev(`htmx.ajax('GET','/pulse/detail?path=${encodeURIComponent(path)}',{target:'#inspector-pane',swap:'innerHTML'})`);
  }
  return waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path=${J(path)}]')?1:0`, 60000);
}

async function refuse(p, name, path) {
  await clickSel(p, '#pulse-detail-root .pulse-delete-btn');
  if (!check(!!(await waitFor(p, `(document.querySelector('.pulse-delete-confirm')||{}).hidden===false?1:0`, 5000)), `${name}: Delete opens the confirm step`)) return null;
  const t0 = Date.now();
  await clickSel(p, '.pulse-delete-confirm .pulse-confirm-delete');
  const got = await waitFor(p, `document.querySelector('#pulse-delete-result .pulse-delete-refused')?1:0`, 120000);
  timing[name + '_refusal_ms'] = Date.now() - t0;
  if (!check(!!got, `${name}: the lab refusal lands IN the delete step (${timing[name + '_refusal_ms']} ms)`)) {
    console.log('  toasts: ' + await p.ev(`[].slice.call(document.querySelectorAll('.toast')).map(function(x){return x.innerText}).join(' | ').slice(0,400)`));
    return null;
  }
  const info = JSON.parse(await p.ev(`(function(){var b=document.querySelector('.pulse-delete-refused'); var btn=b.querySelector('.pulse-delete-together'); return JSON.stringify({together: JSON.parse(b.getAttribute('data-together')), label: btn? btn.textContent.trim() : '', items: [].slice.call(b.querySelectorAll('.pulse-together-list li')).map(function(li){return li.innerText.replace(/\\s+/g,' ')}), msg: (b.querySelector('.pulse-delete-refused-msg')||{}).innerText||''})})()`));
  await p.shot(`${DIR}/${name}_1_refused.png`);
  return info;
}

async function runCase(p, name, path, q, expectTogether, expectLabel) {
  console.log(`\n== ${name}: ${path}`);
  if (!check(!!(await openPulse(p, path, q)), `${name}: the pulse opens from the table`)) return null;
  await settled(p);
  const srv0 = await p.ev(SRV_PATHS);
  const total0 = await p.ev(TOTAL);
  const rows0 = JSON.parse(await p.ev(ROWS));
  const info = await refuse(p, name, path);
  if (!info) return null;
  check(J(info.together) === J(expectTogether), `${name}: it lists exactly ${J(expectTogether)} -> ${J(info.together)}`);
  check(info.label === expectLabel, `${name}: the button reads "${expectLabel}" -> "${info.label}"`);
  check(info.items.length === expectTogether.length, `${name}: one list line per path (${info.items.length})`);
  const before = await p.ev(PEEK(info.together));
  check(!Object.values(JSON.parse(before)).includes('<absent>'), `${name}: nothing was written by the refusal`);
  // press it
  const t1 = Date.now();
  await clickSel(p, '.pulse-delete-refused .pulse-delete-together');
  const busy = await p.ev(`(document.querySelector('.pulse-together-status')||{}).innerText||''`);
  const done = await waitFor(p, `(function(){var t=document.querySelector('#inspector-pane .toast-success'); if(t) return 'ok'; var s=document.querySelector('.pulse-together-status.pulse-together-error'); return s? 'ERR '+s.innerText : 0})()`, 120000);
  timing[name + '_batch_ms'] = Date.now() - t1;
  console.log(`  status while checking: "${busy}"`);
  if (!check(done === 'ok', `${name}: the batch goes through (${timing[name + '_batch_ms']} ms) -> ${done}`)) { await p.shot(`${DIR}/${name}_2_failed.png`); return null; }
  const paneText = await p.ev(`document.querySelector('#inspector-pane').innerText.replace(/\\s+/g,' ')`);
  check(/^Deleted .* together with \d+ other path/.test(paneText.trim()), `${name}: the pane says what went: "${paneText.slice(0, 160)}"`);
  const gone = await p.ev(PEEK(info.together));
  check(Object.values(JSON.parse(gone)).every(v => v === '<absent>'), `${name}: every listed path is gone on the server`);
  const tr = Date.now();
  const srvAfter = JSON.parse(await p.ev(SRV_PATHS));
  const removedRows = JSON.parse(srv0).filter(x => srvAfter.indexOf(x) < 0);
  // the rows the (filtered) table showed that are gone now: the count must
  // drop by exactly those (the table showed its whole filtered set: checked)
  const shownGone = rows0.filter(x => removedRows.indexOf(x) >= 0).length;
  const n0 = +String(total0).replace(/\D/g, '');
  check(rows0.length === n0, `${name}: the filtered table showed its whole set (${rows0.length} rows, count ${total0})`);
  const total1 = await waitFor(p, `(function(){var t=${TOTAL}; return t===${J('(' + (n0 - shownGone) + ')')}?t:0})()`, 30000);
  timing[name + '_rows_ms'] = Date.now() - tr;
  check(!!total1, `${name}: the row count repaints ${total0} -> ${total1} (${shownGone} of the ${removedRows.length} pulse rows removed on the server were in this table)`);
  await settled(p);
  const rowsNow = JSON.parse(await p.ev(ROWS));
  check(!rowsNow.some(r => info.together.some(x => r === x || r.indexOf(x + '.') === 0)), `${name}: no deleted row is left in the table`);
  const cold1 = await p.ev(COLD_TOTAL);
  check(cold1 === (await p.ev(TOTAL)), `${name}: the count equals a cold render of the same table (${cold1})`);
  await p.shot(`${DIR}/${name}_2_deleted.png`);
  // Ctrl+Z
  const tz = Date.now();
  await pressZ(p);
  const back = await waitFor(p, `(function(){return ${PEEK(info.together)}.then(function(s){return s===${J(before)}?1:0})})()`, 60000);
  timing[name + '_undo_ms'] = Date.now() - tz;
  check(!!back, `${name}: ONE Ctrl+Z restores all ${info.together.length} paths byte-equal (${timing[name + '_undo_ms']} ms)`);
  const reopened = await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path=${J(path)}]')?1:0`, 30000);
  check(!!reopened, `${name}: the pulse is re-opened in the pane`);
  const total2 = await waitFor(p, `(function(){var t=${TOTAL}; return t===${J(total0)}?t:0})()`, 30000);
  check(!!total2, `${name}: the row count is back to ${total0}`);
  check(J(JSON.parse(await p.ev(SRV_PATHS))) === srv0, `${name}: the server's pulse list is back`);
  await p.shot(`${DIR}/${name}_3_undone.png`);
  return info;
}

(async () => {
  const p = await open(`${base(PORT)}/pulses`);
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 90000);
  const mark = p.events.length;
  const G = `qubit_pairs.${PAIR}.macros.${GATE}`;
  const SLOT_T = `${G}.flux_pulse_target`, SLOT_C = `${G}.flux_pulse_qubit`;
  const OP_T = `qubits.${TGT}.z.operations.${GATE}_flux_pulse_${TGT}_${CTL}`;
  const OP_C = `qubits.${CTL}.z.operations.${GATE}_flux_pulse_${CTL}_${TGT}`;
  // ONLY=A,B,... runs just those cases (a rig whose chip an earlier Apply changed)
  const want = k => !process.env.ONLY || process.env.ONLY.split(',').indexOf(k) >= 0;
  if (want('A')) await runCase(p, 'A_gate_inline_pulse', SLOT_T, `${GATE} ${PAIR}`, [SLOT_T, OP_T], 'Delete together with 1 op');
  if (want('B')) await runCase(p, 'B_by_name_op', OP_T, `${GATE}_flux_pulse_${TGT}_${CTL}`, [OP_T, SLOT_T], 'Delete together with 1 gate field');
  if (want('C')) await runCase(p, 'C_required_control_pulse', SLOT_C, `${GATE} ${PAIR}`, [SLOT_C, G, OP_C, OP_T], 'Delete together with 1 gate and 2 ops');

  // Ctrl+Z pressed WHILE the batch is being checked: held, then it undoes
  // the batch (never the edit before it, never "nothing to undo" + the batch
  // left standing)
  if (want('F')) {
    console.log('\n== F_ctrl_z_during_check');
    const PAIR3 = E('PAIR3', 'q3-4'), CTL3 = E('CTL3', 'q3'), TGT3 = E('TGT3', 'q4');
    const G3 = `qubit_pairs.${PAIR3}.macros.${GATE}`;
    const S3 = `${G3}.flux_pulse_target`, O3 = `qubits.${TGT3}.z.operations.${GATE}_flux_pulse_${TGT3}_${CTL3}`;
    if (check(!!(await openPulse(p, S3, `${GATE} ${PAIR3}`)), 'F: the pulse opens')) {
      const info = await refuse(p, 'F_ctrl_z_during_check', S3);
      if (info && check(J(info.together) === J([S3, O3]), `F: offer ${J(info.together)}`)) {
        const before = await p.ev(PEEK(info.together));
        // what the page shows between the press and the end: the batch's
        // "Deleted" answer, and any refusal of the Ctrl+Z (recorded, the
        // answer and the undo come and go faster than a poll)
        await p.ev(`(function(){window.__qaF={deleted:0,refused:''}; var o=new MutationObserver(function(){var t=document.querySelector('#inspector-pane .toast-success'); if(t&&/^\\s*Deleted /.test(t.innerText)) window.__qaF.deleted=1; [].slice.call(document.querySelectorAll('.toast')).forEach(function(x){ if(/Nothing undone/.test(x.innerText)) window.__qaF.refused=x.innerText.slice(0,200); });}); o.observe(document.body,{childList:true,subtree:true,characterData:true}); window.__qaFobs=o; return 1})()`);
        const t1 = Date.now();
        await clickSel(p, '.pulse-delete-refused .pulse-delete-together');
        await sleep(60);
        const inFlight = await p.ev(`(document.querySelector('.pulse-together-status')||{}).innerText||''`);
        await pressZ(p);
        const back = await waitFor(p, `(function(){if(!window.__qaF.deleted) return 0; return ${PEEK(info.together)}.then(function(s){return s===${J(before)}?1:0})})()`, 120000);
        timing.F_press_during_check_ms = Date.now() - t1;
        check(/Checking the whole batch/.test(inFlight), `F: the press landed while the batch was being checked ("${inFlight}")`);
        check(!!back, `F: the batch landed, then the held Ctrl+Z undid it -- both paths back byte-equal (${timing.F_press_during_check_ms} ms)`);
        const refusedZ = await p.ev(`window.__qaF.refused`);
        check(!refusedZ, `F: the held press was never refused as another window's ("${refusedZ}")`);
        const reop = await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path=${J(S3)}]')?1:0`, 30000);
        check(!!reop, 'F: and the pulse is re-opened');
        await sleep(1500);
        check(J(JSON.parse(await p.ev(PEEK(info.together)))) === J(JSON.parse(before)), 'F: nothing lands after it (still byte-equal 1.5 s later)');
        await p.shot(`${DIR}/F_ctrl_z_during_check.png`);
      }
    }
  }

  // reload: the page equals a cold render
  if (want('R')) {
    const evMark = p.events.length;
    await p.send('Page.reload', {});
    await sleep(1500);
    // every case above ended undone: a reload must not meet the unsaved-edits
    // guard (a dialog would also block every later evaluate -- answer it)
    const dlg = p.events.slice(evMark).some(e => e.method === 'Page.javascriptDialogOpening');
    if (dlg) await p.send('Page.handleJavaScriptDialog', { accept: true });
    check(!dlg, 'the reload met no unsaved-edits guard (every batch above was undone)');
    await waitFor(p, `document.readyState==='complete' && document.querySelectorAll('tr[data-pulse-path]').length`, 90000);
    await typeInto(p, '.table-filter input[name="q"]', `${GATE} ${PAIR}`);
    await sleep(1500);
    await settled(p);
    check((await p.ev(TOTAL)) === (await p.ev(COLD_TOTAL)), 'after reload the count equals a cold render');
  }

  // the unavailable-worker path: refusal with a working env, then the env breaks
  if (want('D') && process.env.UNAVAIL && process.env.ENV_FILE) {
    console.log('\n== D_worker_unavailable');
    const G2 = `qubit_pairs.${PAIR2}.macros.${GATE}`;
    const SLOT_T2 = `${G2}.flux_pulse_target`;
    const OP_T2 = `qubits.${TGT2}.z.operations.${GATE}_flux_pulse_${TGT2}_${CTL2}`;
    if (check(!!(await openPulse(p, SLOT_T2, `${GATE} ${PAIR2}`)), 'D: the pulse opens')) {
      const info = await refuse(p, 'D_worker_unavailable', SLOT_T2);
      if (info && check(J(info.together) === J([SLOT_T2, OP_T2]), `D: offer ${J(info.together)}`)) {
        const envTxt = fs.readFileSync(process.env.ENV_FILE, 'utf8');
        fs.writeFileSync(process.env.ENV_FILE, J({ selected_env_python: 'D:\\no_such_env\\python.exe' }));
        try {
          const t1 = Date.now();
          await clickSel(p, '.pulse-delete-refused .pulse-delete-together');
          const done = await waitFor(p, `(function(){var t=document.querySelector('#inspector-pane .toast-success'); if(t) return 'ok'; var s=document.querySelector('.pulse-together-status.pulse-together-error'); return s? 'ERR '+s.innerText : 0})()`, 120000);
          timing.D_unavailable_batch_ms = Date.now() - t1;
          check(done === 'ok', `D: the batch is never blocked by a worker that cannot run (${timing.D_unavailable_batch_ms} ms) -> ${done}`);
          const warn = await p.ev(`(document.querySelector('#inspector-pane .toast-warning')||{}).innerText||''`);
          check(/NOT checked/.test(warn) && /written unchecked/.test(warn), `D: the pane says it was written UNCHECKED: "${warn.replace(/\s+/g, ' ').slice(0, 220)}"`);
          await p.shot(`${DIR}/D_worker_unavailable_2_unchecked.png`);
        } finally {
          fs.writeFileSync(process.env.ENV_FILE, envTxt);
        }
        await pressZ(p);
        const back = await waitFor(p, `(function(){return ${PEEK([SLOT_T2, OP_T2])}.then(function(s){return s.indexOf('<absent>')<0?1:0})})()`, 60000);
        check(!!back, 'D: Ctrl+Z restores both');
      }
    }
  }

  // the Json Tree (TREE=1). w9/pulsegate: a pulse is deleted on the Pulses
  // page only -- the tree's by-name op has no ✕, it says where, and its link
  // lands on the pulse (the tree's lab offer for a gate: pulse_gate.cjs O)
  if (want('T') && process.env.TREE) {
    console.log('\n== T_json_tree');
    const T = await open(`${base(PORT)}/explorer`);
    await sleep(2500 * SLOW);
    const segs = OP_T.split('.');
    await T.ev(`(function(){var s=document.getElementById('explorer-search'); s.focus(); s.value=${J(segs[segs.length - 2] + ' ' + segs[segs.length - 1])}; s.dispatchEvent(new Event('input',{bubbles:true})); return 1})()`);
    await sleep(2000 * SLOW);
    // the search's suggestion list lies over the rows: Esc closes it, as a user does
    await T.key('Escape', 'Escape', 27);
    await sleep(300);
    const found = await T.ev(`(function(){var n=document.querySelector('.tree-node[data-path=${J(OP_T)}]'); if(!n) return 0; var r=n.querySelector(':scope > .tree-row'); r.scrollIntoView({block:'center'}); window.__qaRow=r; return 1})()`);
    if (check(!!found, 'T: the Json Tree shows the by-name op')) {
      const hov = await T.ev(`(function(){var b=window.__qaRow.getBoundingClientRect(); return [b.left+40,b.top+b.height/2]})()`);
      await T.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hov[0], y: hov[1] });
      await sleep(300);
      const st = await T.ev(`(function(){var s=window.__qaRow.querySelector('.tree-row-actions'); var g=s&&s.querySelector('.tree-pulse-gate'); return {del:!!(s&&s.querySelector('.tree-act-del')), note:g?g.textContent:''}})()`);
      check(!st.del && /Pulses are added, removed and renamed on the Pulses page/.test(st.note),
        `T: no ✕ on the op -- the tree says where instead (${J(st)})`);
      await T.shot(`${DIR}/T_tree_says_where.png`);
      const a = await T.ev(`(function(){var a=window.__qaRow.querySelector('.tree-pulse-gate a'); if(!a) return null; var b=a.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2]})()`);
      if (check(!!a, 'T: the link is there')) {
        await T.click(a[0], a[1]);
        const landed = await waitFor(T, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/pulses' && document.querySelector('#pulse-detail-root[data-pulse-path=${J(OP_T)}]'))?1:0`, 120000);
        check(!!landed, 'T: the link lands on the op on the Pulses page');
        await T.shot(`${DIR}/T_landed.png`);
      }
    }
    const terrs = T.errors(0).filter(m => !/status of 40[09] \((BAD REQUEST|CONFLICT)\)/.test(m));
    check(terrs.length === 0, 'T: no page errors ' + J(terrs.slice(0, 3)));
    await T.close();
  }

  // keep one batch and Apply to live (the caller runs pulse_lab_check.py)
  if (want('E') && !process.env.NO_APPLY) {
    console.log('\n== E_apply');
    const info = await (async () => {
      if (!(await openPulse(p, SLOT_T, `${GATE} ${PAIR}`))) return null;
      return refuse(p, 'E_apply', SLOT_T);
    })();
    if (info) {
      await clickSel(p, '.pulse-delete-refused .pulse-delete-together');
      const done = await waitFor(p, `document.querySelector('#inspector-pane .toast-success')?1:0`, 120000);
      check(!!done, 'E: the batch kept for Apply');
      const ap = await waitFor(p, `document.querySelector('.btn-apply-live')?1:0`, 15000);
      if (check(!!ap, 'E: Apply-to-live button present')) {
        const ta = Date.now();
        await clickSel(p, '.btn-apply-live');
        if (await p.ev(`document.querySelector('.btn-apply-live.sync-arm')?1:0`)) { await sleep(400); await clickSel(p, '.btn-apply-live'); }
        const applied = await waitFor(p, `document.querySelector('.btn-apply-live')?0:1`, 120000);
        timing.E_apply_ms = Date.now() - ta;
        check(!!applied, `E: applied to live (${timing.E_apply_ms} ms)`);
        await p.shot(`${DIR}/E_applied.png`);
      }
    }
  }

  // a lab refusal IS a 400 (as before this change): Chrome logs every 4xx
  // response as a console error with no URL in the text -- counted, not hidden
  const all = p.errors(mark);
  const errs = all.filter(m => !/status of 400 \(BAD REQUEST\)/.test(m));
  console.log(`  (${all.length - errs.length} network-400 log lines: the refusals)`);
  check(errs.length === 0, 'no page errors: ' + J(errs.slice(0, 5)));
  const res = { bad, timing, out };
  fs.writeFileSync(`${DIR}/result.json`, J(res, null, 1));
  console.log(J(timing));
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  await p.close();
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
